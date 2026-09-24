"""Tool per scaricare file dal web nella cartella ``downloads/`` del workspace.

A differenza di ``web_fetch`` (che estrae testo leggibile da una pagina),
``download_file`` salva il payload binario così com'è: immagini, PDF, archivi,
qualsiasi file. La destinazione è sempre ``downloads/`` — mai la root — e il
file può poi essere presentato in chat come allegato (``message`` con ``media``)
o, per le immagini, embed markdown inline.

**``downloads/`` di chi.** Della radice del *turno*, non dell'installazione:
dentro un progetto è ``<progetto>/downloads/``. Fino al 22/08 era sempre quella
dell'installazione, ed era una scrittura fuori dal progetto che il confine non
vedeva passare — la destinazione non passa da ``resolve_allowed_path``, quindi il
cancello non aveva niente da guardare. Il prompt di un progetto dice «scrivi solo
dentro questa cartella», e questo lo rendeva falso.

Robustezza (stesso pattern di ``webui/media_ingest``): validazione SSRF
pre-fetch e su ogni hop di redirect, cap di dimensione in streaming, nome file
sanificato (parametro esplicito > Content-Disposition > basename dell'URL),
scrittura atomica. Modulo fidato di prima parte: importa ``httpx``
direttamente come i provider.
"""

from __future__ import annotations

import mimetypes
import re
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import httpx
from loguru import logger

from jenny.agent.tools.base import Tool, tool_parameters
from jenny.agent.tools.schema import StringSchema, tool_parameters_schema
from jenny.security.fetch import BROWSER_USER_AGENT, open_validated_stream, read_capped
from jenny.security.network import validate_url_target
from jenny.security.workspace_access import (
    READONLY_TOOL_REFUSAL,
    current_turn_is_readonly,
    current_workspace_scope,
)
from jenny.security.workspace_policy import _safe_expanduser
from jenny.utils.helpers import detect_image_mime, ensure_dir, safe_filename
from jenny.utils.path import atomic_write

# Limiti operativi.
TIMEOUT_S = 60.0
MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024
DOWNLOADS_SUBDIR = "downloads"

_BROWSER_HEADERS = {"User-Agent": BROWSER_USER_AGENT, "Accept": "*/*"}

# filename= o filename*=UTF-8''… nel Content-Disposition (basta il caso comune).
_CONTENT_DISPOSITION_NAME_RE = re.compile(
    r"filename\*?=(?:UTF-8''|\"?)([^\";]+)\"?", re.IGNORECASE
)


def _filename_from_disposition(header: str | None) -> str | None:
    if not header:
        return None
    m = _CONTENT_DISPOSITION_NAME_RE.search(header)
    if not m:
        return None
    name = safe_filename(unquote(m.group(1)))
    return name or None


def _filename_from_url(url: str) -> str | None:
    try:
        name = Path(unquote(urlparse(url).path)).name
    except (ValueError, OSError):
        return None
    name = safe_filename(name)
    return name or None


def _unique_path(directory: Path, name: str) -> Path:
    """Path libero in ``directory``: a collisione appende ``-1``, ``-2``, …"""
    target = directory / name
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    for i in range(1, 1000):
        candidate = directory / f"{stem}-{i}{suffix}"
        if not candidate.exists():
            return candidate
    return directory / f"{stem}-{uuid.uuid4().hex[:8]}{suffix}"


async def _fetch_streaming(
    client: httpx.AsyncClient, url: str
) -> tuple[bytes, str, str | None]:
    """Scarica ``url`` seguendo i redirect a mano, validando ogni hop (SSRF).

    Ritorna ``(data, final_url, content_disposition)``. Solleva ``ValueError``
    con un messaggio leggibile per ogni condizione di errore.
    """
    async with open_validated_stream(
        client, url, validate=validate_url_target, headers=_BROWSER_HEADERS,
    ) as (resp, final_url):
        data = await read_capped(
            resp, MAX_DOWNLOAD_BYTES,
            f"file exceeds the {MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB limit",
        )
        return data, final_url, resp.headers.get("content-disposition")


@tool_parameters(
    tool_parameters_schema(
        url=StringSchema("HTTP(S) URL of the file to download"),
        filename=StringSchema(
            "Optional filename for the saved file (extension included). "
            "Defaults to the server-provided or URL-derived name."
        ),
        required=["url"],
    )
)
class DownloadFileTool(Tool):
    """Scarica un file qualsiasi dal web dentro ``workspace/downloads/``."""

    _scopes = {"core", "subagent"}

    name = "download_file"
    description = (
        "Download ANY file from the web (image, PDF, archive, document, …) and "
        "save it into the workspace `downloads/` folder. "
        "Use this whenever the user wants a file fetched, saved, or sent to them. "
        "Then present it in chat: attach the returned path via the `message` tool "
        "`media` parameter (any file type), or for an image embed it inline as "
        "`![description](downloads/name.jpg)`. "
        "Never fake a requested file by hand-writing SVG/code or decoding base64 "
        "blobs — download the real thing with this tool. "
        f"Max size {MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB."
    )

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(workspace=ctx.workspace)

    def __init__(self, workspace: str | Path, client: httpx.AsyncClient | None = None):
        self._workspace = _safe_expanduser(workspace)
        # Client iniettabile per i test (httpx.MockTransport).
        self._client = client

    def _downloads_root(self) -> Path:
        """La radice sotto cui sta ``downloads/``: quella del turno.

        Chiede a ``WorkspaceScope.write_root()`` invece di leggere
        ``project_path``: dal passo T4.4 «dove posso scrivere» ha una sola
        risposta, e un tool con destinazione fissa non e' un'eccezione — e' solo
        uno che non ha un percorso da validare contro quella radice.

        Senza uno scope legato — sessioni interne, test — resta il workspace del
        costruttore, che è anche la radice del turno in quel caso.
        """
        scope = current_workspace_scope()
        return scope.write_root() if scope is not None else self._workspace

    async def execute(self, url: str, filename: str | None = None, **kwargs: Any) -> str:
        # Prima della rete, non dopo: scaricare 20 MB per poi rifiutare la
        # scrittura sarebbe traffico buttato. La destinazione di questo tool e'
        # fissa (``<installazione>/downloads/``) e non passa da
        # ``resolve_allowed_path``, quindi il cancello non la vede: il controllo
        # va chiesto qui.
        if current_turn_is_readonly():
            return READONLY_TOOL_REFUSAL
        url = url.strip(" \t\r\n`\"'")
        if not url.lower().startswith(("http://", "https://")):
            return "Error: url must be http(s)"

        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=False)
        try:
            data, final_url, disposition = await _fetch_streaming(client, url)
        except ValueError as e:
            return f"Error: download failed: {e}"
        except httpx.HTTPError as e:
            return f"Error: download failed: {e}"
        finally:
            if owns_client:
                await client.aclose()

        if not data:
            return "Error: download failed: empty response body"

        name = (
            (safe_filename(filename) if filename else None)
            or _filename_from_disposition(disposition)
            or _filename_from_url(final_url)
            or f"download-{uuid.uuid4().hex[:8]}"
        )
        # Se manca l'estensione prova a derivarla dai magic byte (immagini)
        # così l'embed markdown e il media serving riconoscono il tipo.
        if not Path(name).suffix:
            sniffed = detect_image_mime(data)
            ext = mimetypes.guess_extension(sniffed) if sniffed else None
            if ext:
                name += ext

        downloads_dir = ensure_dir(self._downloads_root() / DOWNLOADS_SUBDIR)
        target = _unique_path(downloads_dir, name)
        try:
            atomic_write(target, data)
        except OSError as e:
            return f"Error: could not save file: {e}"

        mime = (
            detect_image_mime(data)
            or mimetypes.guess_type(target.name)[0]
            or "application/octet-stream"
        )
        rel = f"{DOWNLOADS_SUBDIR}/{target.name}"
        logger.info("download_file: saved {} ({} bytes, {}) from {}", rel, len(data), mime, url)
        return (
            f"Saved {rel} ({len(data)} bytes, {mime}). "
            "Attach it with the `message` tool `media` parameter to send it in chat"
            + (", or embed it inline as `![description](" + rel + ")`."
               if mime.startswith("image/") else ".")
        )


# Registrazione esplicita dei tool di questo modulo (Fase 5.3): il
# ToolLoader legge questa lista invece della reflection dir(). Un nuovo
# tool va aggiunto qui esplicitamente.
TOOLS = [DownloadFileTool]
