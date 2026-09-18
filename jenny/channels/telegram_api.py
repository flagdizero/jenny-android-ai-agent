"""Client minimale della Telegram Bot API basato su httpx.

Nessuna dipendenza nuova: usa lo stesso ``httpx`` dei provider LLM (già nel
lockfile Android). Copre solo i metodi necessari al canale: ``getMe``,
``getUpdates`` (long polling), ``sendMessage``, l'invio di media, il download
degli allegati in ingresso (``getFile`` + file API) e ``sendChatAction``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from loguru import logger

from jenny.utils.media_decode import FileSizeExceeded

TELEGRAM_API_BASE = "https://api.telegram.org"

# Tentativi extra dopo un 429 (l'attesa è dettata da retry_after del server).
_MAX_RATE_LIMIT_RETRIES = 2
# Cap difensivo sull'attesa suggerita dal server per non bloccare il poller.
_MAX_RETRY_AFTER_S = 30.0


class TelegramAPIError(Exception):
    """Errore applicativo della Bot API (ok=false o HTTP non-2xx)."""

    def __init__(self, status_code: int, description: str):
        self.status_code = status_code
        self.description = description
        super().__init__(f"Telegram API error {status_code}: {description}")


class TelegramAPI:
    """Wrapper asincrono e minimale della Bot API.

    Il client httpx è iniettabile per i test (``httpx.MockTransport``).
    """

    def __init__(
        self,
        token: str,
        *,
        client: httpx.AsyncClient | None = None,
        base_url: str = TELEGRAM_API_BASE,
    ):
        self._base = f"{base_url.rstrip('/')}/bot{token}"
        # Il download degli allegati non è un metodo della Bot API: è un'altra
        # radice (``/file/bot<token>/<file_path>``) sullo stesso host. Tenuta
        # qui accanto invece di ricomporla al volo, così il token compare in un
        # punto solo.
        self._file_base = f"{base_url.rstrip('/')}/file/bot{token}"
        self._client = client or httpx.AsyncClient(timeout=30.0)

    async def close(self) -> None:
        await self._client.aclose()

    async def _call(
        self,
        method: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
        files: dict[str, Any] | None = None,
    ) -> Any:
        """Invoca *method* e ritorna il campo ``result`` della risposta.

        Su 429 attende ``retry_after`` (con cap) e riprova; ogni altro errore
        applicativo o HTTP diventa :class:`TelegramAPIError`. Con ``files`` la
        richiesta è multipart/form-data (upload media) e ``payload`` diventa il
        form dei campi testuali; senza ``files`` è JSON come di consueto.
        """
        url = f"{self._base}/{method}"
        attempts = _MAX_RATE_LIMIT_RETRIES + 1
        for attempt in range(attempts):
            if files is not None:
                resp = await self._client.post(
                    url, data=payload or {}, files=files, timeout=timeout
                )
            else:
                resp = await self._client.post(url, json=payload or {}, timeout=timeout)
            if resp.status_code == 429 and attempt < attempts - 1:
                retry_after = _MAX_RETRY_AFTER_S
                try:
                    body = resp.json()
                    retry_after = float(body.get("parameters", {}).get("retry_after", 5))
                except Exception:
                    pass
                retry_after = min(max(retry_after, 0.5), _MAX_RETRY_AFTER_S)
                logger.warning("Telegram rate limit on {}, retrying in {}s", method, retry_after)
                await asyncio.sleep(retry_after)
                continue
            try:
                body = resp.json()
            except Exception:
                raise TelegramAPIError(resp.status_code, resp.text[:200]) from None
            if not body.get("ok"):
                raise TelegramAPIError(
                    resp.status_code, str(body.get("description", "unknown error"))
                )
            return body.get("result")
        raise TelegramAPIError(429, "rate limited")  # pragma: no cover - difensivo

    async def get_me(self) -> dict[str, Any]:
        """Ritorna le info del bot; usato anche per validare il token."""
        return await self._call("getMe")

    async def get_updates(self, offset: int | None, timeout_s: int) -> list[dict[str, Any]]:
        """Long-poll degli update; il timeout HTTP supera quello lato server."""
        payload: dict[str, Any] = {
            "timeout": timeout_s,
            "allowed_updates": ["message"],
        }
        if offset is not None:
            payload["offset"] = offset
        result = await self._call("getUpdates", payload, timeout=timeout_s + 10)
        return result if isinstance(result, list) else []

    async def send_message(
        self,
        chat_id: str,
        text: str,
        *,
        parse_mode: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        return await self._call("sendMessage", payload)

    async def send_media_file(
        self,
        chat_id: str,
        *,
        method: str,
        field: str,
        filename: str,
        data: bytes,
        caption: str | None = None,
        parse_mode: str | None = None,
    ) -> dict[str, Any]:
        """Carica un file locale via multipart (``sendPhoto``/``sendDocument``)."""
        form: dict[str, Any] = {"chat_id": chat_id}
        if caption:
            form["caption"] = caption
            if parse_mode:
                form["parse_mode"] = parse_mode
        return await self._call(
            method, form, files={field: (filename, data)}, timeout=60.0
        )

    async def send_media_url(
        self,
        chat_id: str,
        *,
        method: str,
        field: str,
        url: str,
        caption: str | None = None,
        parse_mode: str | None = None,
    ) -> dict[str, Any]:
        """Invia un media referenziandolo per URL (Telegram lo scarica lato server)."""
        payload: dict[str, Any] = {"chat_id": chat_id, field: url}
        if caption:
            payload["caption"] = caption
            if parse_mode:
                payload["parse_mode"] = parse_mode
        return await self._call(method, payload)

    async def send_chat_action(self, chat_id: str, action: str = "typing") -> Any:
        """Mostra "sta scrivendo…" nella chat. Scade da sola dopo ~5 secondi.

        Chi la usa deve trattarla come cosmetica: un fallimento qui non ha
        nessuna conseguenza sul turno, e il chiamante non deve mai attendere
        oltre il proprio battito (v. ``_TypingHeartbeat`` nel canale).
        """
        return await self._call("sendChatAction", {"chat_id": chat_id, "action": action})

    async def get_file(self, file_id: str) -> dict[str, Any]:
        """``getFile``: ritorna i metadati con ``file_path`` e ``file_size``.

        ``file_size`` è il modo economico di rifiutare un allegato oltre cap:
        si guarda prima di spendere la banda del download.
        """
        result = await self._call("getFile", {"file_id": file_id})
        return result if isinstance(result, dict) else {}

    async def download_file(self, file_path: str, *, max_bytes: int) -> bytes:
        """Scarica un allegato dalla file API, in streaming e con tetto.

        Streaming e non ``resp.content``: su un telefono un file oltre cap non
        deve nemmeno materializzarsi in RAM prima di essere rifiutato. Il
        controllo è incrementale, quindi un server che mente sulla lunghezza
        non aggira il tetto.

        ``file_path`` arriva da ``getFile``, cioè da un host remoto: viene
        rifiutato se prova a risalire la radice. Non è uno scenario realistico
        con ``api.telegram.org``, ma il costo del controllo è una riga e
        l'alternativa è fidarsi di un input remoto per comporre un URL.
        """
        clean = (file_path or "").strip().lstrip("/")
        if not clean or ".." in clean.split("/"):
            raise TelegramAPIError(400, f"invalid file_path: {file_path!r}")
        url = f"{self._file_base}/{clean}"
        chunks: list[bytes] = []
        total = 0
        async with self._client.stream("GET", url, timeout=60.0) as resp:
            if resp.status_code >= 400:
                await resp.aread()
                raise TelegramAPIError(resp.status_code, resp.text[:200])
            async for chunk in resp.aiter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise FileSizeExceeded(
                        f"File exceeds {max_bytes // (1024 * 1024)}MB limit"
                    )
                chunks.append(chunk)
        return b"".join(chunks)

    async def set_my_commands(self, commands: list[dict[str, str]]) -> Any:
        """Registra il menu comandi del bot (chiamata best-effort lato caller)."""
        return await self._call("setMyCommands", {"commands": commands})
