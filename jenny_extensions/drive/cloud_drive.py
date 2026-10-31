"""Google Drive come destinazione del backup nel cloud (``RemoteSink`` su httpx).

Vede solo quel che Jenny stessa ha creato: lo scope è ``drive.file``, quindi una
cartella «Jenny» nella radice del Drive dell'utente, con dentro le sottocartelle
del repository (``packs``, ``index``, ``snapshots``). Il resto del Drive per
questa app non esiste — ed è il motivo per cui cercare «Jenny» per nome basta.

Il token arriva da un ``token_provider`` (sul telefono: ``DriveBridge.kt``, che
chiede a Google Play Services un token senza interfaccia). Un 401 vuol dire
token scaduto o revocato: si chiede un token nuovo una volta sola, e se non
c'è — l'utente ha tolto il permesso — :class:`DriveConsentError`.

Drive ammette due file con lo stesso nome nella stessa cartella: una ``put`` su
una chiave esistente **aggiorna** quel file invece di crearne un secondo, e fra
duplicati vince il più vecchio. I caricamenti sono atomici per chi legge: il
multipart e il ripartibile creano il file solo a contenuto completo.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from .cloud_sink import RemoteNotFoundError, split_key

API = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3"
FOLDER_MIME = "application/vnd.google-apps.folder"
DEFAULT_ROOT_NAME = "Jenny"
# Sopra questa soglia il caricamento è ripartibile, a pezzi; sotto, un multipart.
RESUMABLE_THRESHOLD = 5 << 20
# Drive vuole pezzi multipli di 256 KiB (tranne l'ultimo).
DEFAULT_CHUNK = 32 * (256 << 10)
_MAX_ATTEMPTS = 6

TokenProvider = Callable[[bool], Awaitable[str]]
"""``await provider(force_refresh)`` → token d'accesso; alza :class:`DriveConsentError`."""


class DriveError(Exception):
    """Drive ha risposto con un errore che ritentare non risolve."""


class DriveConsentError(DriveError):
    """Manca il permesso: l'utente deve riconsentire (o ha revocato l'accesso)."""


def _q_literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _is_retryable(response: httpx.Response) -> bool:
    if response.status_code == 429 or response.status_code >= 500:
        return True
    if response.status_code == 403:
        # 403 vale anche per «niente permesso»: si ritenta solo il rate limit.
        try:
            reasons = {e.get("reason") for e in response.json()["error"]["errors"]}
        except (ValueError, KeyError, TypeError, AttributeError):
            return False
        return bool(reasons & {"rateLimitExceeded", "userRateLimitExceeded"})
    return False


class DriveSink:
    """Una cartella del Drive dell'utente come :class:`~jenny_extensions.drive.cloud_sink.RemoteSink`."""

    def __init__(
        self,
        token_provider: TokenProvider,
        *,
        root_name: str = DEFAULT_ROOT_NAME,
        client: httpx.AsyncClient | None = None,
        chunk_size: int = DEFAULT_CHUNK,
        resumable_threshold: int = RESUMABLE_THRESHOLD,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if chunk_size % (256 << 10):
            raise ValueError("chunk_size must be a multiple of 256 KiB")
        self._token_provider = token_provider
        self._root_name = root_name
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=20.0))
        self._owns_client = client is None
        self._chunk = chunk_size
        self._threshold = resumable_threshold
        self._sleep = sleep
        self._token: str | None = None
        # cartella ("" = radice del repository) → id Drive
        self._folder_ids: dict[str, str] = {}
        # (cartella, nome) → id Drive, riempita da list() e dalle put
        self._file_ids: dict[tuple[str, str], str] = {}

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # -- HTTP -----------------------------------------------------------------

    async def _bearer(self, force: bool = False) -> str:
        if self._token is None or force:
            self._token = await self._token_provider(force)
        return self._token

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Una richiesta con token, ritentata su rete caduta, 429, 5xx e rate limit."""
        refreshed = False
        attempt = 0
        while True:
            headers = dict(kwargs.pop("headers", None) or {})
            headers["Authorization"] = f"Bearer {await self._bearer()}"
            try:
                response = await self._client.request(method, url, headers=headers, **kwargs)
            except httpx.TransportError as exc:
                attempt += 1
                if attempt >= _MAX_ATTEMPTS:
                    raise
                logger.warning("Drive {} failed ({}), retrying", method, type(exc).__name__)
                await self._sleep(min(2 ** attempt, 30))
                kwargs["headers"] = headers
                continue
            if response.status_code == 401 and not refreshed:
                refreshed = True
                await self._bearer(force=True)
                kwargs["headers"] = headers
                continue
            if response.status_code == 401:
                raise DriveConsentError("Drive rejected a fresh token")
            if _is_retryable(response):
                attempt += 1
                if attempt >= _MAX_ATTEMPTS:
                    raise DriveError(f"Drive {method} gave {response.status_code} repeatedly")
                await self._sleep(min(2 ** attempt, 30))
                kwargs["headers"] = headers
                continue
            return response

    @staticmethod
    def _check(response: httpx.Response, what: str) -> None:
        if response.status_code == 404:
            raise RemoteNotFoundError(what)
        if response.status_code >= 400:
            raise DriveError(f"Drive: {what} → HTTP {response.status_code}: {response.text[:300]}")

    async def _query(self, q: str) -> list[dict[str, Any]]:
        files: list[dict[str, Any]] = []
        page: str | None = None
        while True:
            params = {
                "q": q,
                "fields": "nextPageToken,files(id,name,createdTime)",
                "pageSize": "1000",
                "orderBy": "createdTime",
                "spaces": "drive",
            }
            if page:
                params["pageToken"] = page
            response = await self._request("GET", f"{API}/files", params=params)
            self._check(response, f"query {q!r}")
            data = response.json()
            files.extend(data.get("files", []))
            page = data.get("nextPageToken")
            if not page:
                return files

    # -- cartelle ---------------------------------------------------------------

    async def _find_folder(self, name: str, parent_id: str) -> str | None:
        found = await self._query(
            f"name = {_q_literal(name)} and mimeType = '{FOLDER_MIME}' "
            f"and {_q_literal(parent_id)} in parents and trashed = false"
        )
        return found[0]["id"] if found else None

    async def _create_folder(self, name: str, parent_id: str) -> str:
        response = await self._request(
            "POST",
            f"{API}/files",
            params={"fields": "id"},
            json={"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]},
        )
        self._check(response, f"create folder {name!r}")
        return response.json()["id"]

    async def _folder_id(self, folder: str, *, create: bool) -> str | None:
        """Id Drive di *folder* (``""`` = la cartella «Jenny»), creandola se serve."""
        if folder in self._folder_ids:
            return self._folder_ids[folder]
        if folder:
            parent = await self._folder_id("", create=create)
            if parent is None:
                return None
            name = folder
        else:
            parent, name = "root", self._root_name
        folder_id = await self._find_folder(name, parent)
        if folder_id is None:
            if not create:
                return None
            folder_id = await self._create_folder(name, parent)
            logger.info("Drive folder {!r} created", folder or self._root_name)
        self._folder_ids[folder] = folder_id
        return folder_id

    async def _file_id(self, folder: str, name: str) -> str | None:
        cached = self._file_ids.get((folder, name))
        if cached is not None:
            return cached
        folder_id = await self._folder_id(folder, create=False)
        if folder_id is None:
            return None
        found = await self._query(
            f"name = {_q_literal(name)} and {_q_literal(folder_id)} in parents "
            "and trashed = false"
        )
        if not found:
            return None
        self._file_ids[(folder, name)] = found[0]["id"]
        return found[0]["id"]

    # -- RemoteSink ---------------------------------------------------------------

    async def list(self, folder: str) -> list[str]:
        if folder:
            split_key(folder)
        folder_id = await self._folder_id(folder, create=False)
        if folder_id is None:
            return []
        names: set[str] = set()
        for item in await self._query(
            f"{_q_literal(folder_id)} in parents and trashed = false "
            f"and mimeType != '{FOLDER_MIME}'"
        ):
            # In ordine di creazione: fra duplicati resta il primo.
            self._file_ids.setdefault((folder, item["name"]), item["id"])
            names.add(item["name"])
        return sorted(names)

    async def put_bytes(self, key: str, data: bytes) -> None:
        folder, name = split_key(key)
        folder_id = await self._folder_id(folder, create=True)
        assert folder_id is not None
        existing = await self._file_id(folder, name)
        if len(data) > self._threshold:
            file_id = await self._resumable(name, folder_id, existing, len(data), _BytesSource(data))
        else:
            file_id = await self._multipart(name, folder_id, existing, data)
        self._file_ids[(folder, name)] = file_id

    async def put_file(
        self, key: str, source: Path, on_progress: Callable[[int, int], None] | None = None
    ) -> None:
        size = Path(source).stat().st_size
        if size <= self._threshold:
            await self.put_bytes(key, await asyncio.to_thread(Path(source).read_bytes))
            return
        folder, name = split_key(key)
        folder_id = await self._folder_id(folder, create=True)
        assert folder_id is not None
        existing = await self._file_id(folder, name)
        file_id = await self._resumable(
            name, folder_id, existing, size, _FileSource(Path(source)), on_progress
        )
        self._file_ids[(folder, name)] = file_id

    async def get_bytes(self, key: str) -> bytes:
        response = await self._download(key)
        return response.content

    async def get_file(self, key: str, dest: Path) -> None:
        response = await self._download(key)
        await asyncio.to_thread(Path(dest).write_bytes, response.content)

    async def delete(self, key: str) -> None:
        folder, name = split_key(key)
        file_id = await self._file_id(folder, name)
        if file_id is None:
            raise RemoteNotFoundError(key)
        response = await self._request("DELETE", f"{API}/files/{file_id}")
        self._file_ids.pop((folder, name), None)
        self._check(response, f"delete {key}")

    # -- trasferimenti --------------------------------------------------------------

    async def _download(self, key: str) -> httpx.Response:
        folder, name = split_key(key)
        file_id = await self._file_id(folder, name)
        if file_id is None:
            raise RemoteNotFoundError(key)
        response = await self._request("GET", f"{API}/files/{file_id}", params={"alt": "media"})
        self._check(response, f"download {key}")
        return response

    async def _multipart(
        self, name: str, folder_id: str, existing: str | None, data: bytes
    ) -> str:
        boundary = f"jenny{secrets.token_hex(12)}"
        meta: dict[str, Any] = {"name": name}
        if existing is None:
            meta["parents"] = [folder_id]
        body = (
            f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
            f"{json.dumps(meta)}\r\n--{boundary}\r\n"
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
        headers = {"Content-Type": f"multipart/related; boundary={boundary}"}
        params = {"uploadType": "multipart", "fields": "id"}
        if existing is None:
            response = await self._request(
                "POST", f"{UPLOAD}/files", params=params, content=body, headers=headers
            )
        else:
            response = await self._request(
                "PATCH", f"{UPLOAD}/files/{existing}", params=params, content=body,
                headers=headers,
            )
        self._check(response, f"upload {name}")
        return response.json()["id"]

    async def _resumable(
        self,
        name: str,
        folder_id: str,
        existing: str | None,
        size: int,
        source: _Source,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> str:
        meta: dict[str, Any] = {"name": name}
        if existing is None:
            meta["parents"] = [folder_id]
        headers = {
            "X-Upload-Content-Type": "application/octet-stream",
            "X-Upload-Content-Length": str(size),
        }
        params = {"uploadType": "resumable", "fields": "id"}
        if existing is None:
            start = await self._request(
                "POST", f"{UPLOAD}/files", params=params, json=meta, headers=headers
            )
        else:
            start = await self._request(
                "PATCH", f"{UPLOAD}/files/{existing}", params=params, json=meta, headers=headers
            )
        self._check(start, f"start upload {name}")
        session = start.headers.get("Location")
        if not session:
            raise DriveError(f"Drive gave no upload session for {name}")

        offset = 0
        stalls = 0
        while True:
            length = min(self._chunk, size - offset)
            if length > 0:
                chunk = await asyncio.to_thread(source.read, offset, length)
                content_range = f"bytes {offset}-{offset + length - 1}/{size}"
            else:
                # Tutto mandato ma nessuna conferma: si chiede lo stato.
                chunk, content_range = b"", f"bytes */{size}"
            response = await self._session_put(session, chunk, content_range)
            if response is not None and response.status_code in (200, 201):
                return response.json()["id"]
            if response is not None and response.status_code == 308:
                advanced = _next_offset(response)
                if advanced > offset:
                    offset, stalls = advanced, 0
                    if on_progress is not None:
                        on_progress(offset, size)
                    continue
                # Un 308 che non avanza (Drive non ha preso il pezzo): conta
                # come un intoppo, o un «tutto mandato» girerebbe all'infinito.
            if response is not None and response.status_code == 404:
                raise DriveError(f"upload session for {name} expired")
            if response is not None and not _is_retryable(response):
                self._check(response, f"upload {name}")
            # Rete caduta o errore temporaneo: si aspetta e si chiede a Drive dov'è
            # arrivato, invece di fidarsi di quel che si crede di aver mandato.
            stalls += 1
            if stalls >= _MAX_ATTEMPTS:
                raise DriveError(f"upload of {name} keeps failing")
            await self._sleep(min(2 ** stalls, 30))
            probe = await self._session_put(session, b"", f"bytes */{size}")
            if probe is not None and probe.status_code in (200, 201):
                return probe.json()["id"]
            if probe is not None and probe.status_code == 308:
                offset = _next_offset(probe)
            elif probe is not None and probe.status_code == 404:
                raise DriveError(f"upload session for {name} expired")
            # Probe fallito anch'esso: si riprova dallo stesso offset al giro dopo.

    async def _session_put(
        self, session: str, chunk: bytes, content_range: str
    ) -> httpx.Response | None:
        """Un PUT sulla sessione ripartibile; ``None`` se la rete è caduta."""
        try:
            return await self._client.put(
                session,
                content=chunk,
                headers={
                    "Authorization": f"Bearer {await self._bearer()}",
                    "Content-Range": content_range,
                },
            )
        except httpx.TransportError:
            return None


def _next_offset(response: httpx.Response) -> int:
    """Dal header ``Range: bytes=0-N`` di un 308, il prossimo byte da mandare."""
    value = response.headers.get("Range")
    if not value or "-" not in value:
        return 0
    return int(value.rsplit("-", 1)[1]) + 1


class _Source:
    def read(self, offset: int, length: int) -> bytes:  # pragma: no cover - interfaccia
        raise NotImplementedError


class _BytesSource(_Source):
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self, offset: int, length: int) -> bytes:
        return self._data[offset : offset + length]


class _FileSource(_Source):
    def __init__(self, path: Path) -> None:
        self._path = path

    def read(self, offset: int, length: int) -> bytes:
        with open(self._path, "rb") as handle:
            handle.seek(offset)
            return handle.read(length)
