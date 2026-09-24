"""WebSocket server channel: jenny acts as a WebSocket server and serves connected clients."""

from __future__ import annotations

import asyncio
import hmac
import json
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self

from loguru import logger
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request as WsRequest

from jenny.bus.events import InboundMessage
from jenny.bus.queue import MessageBus
from jenny.config.paths import get_uploads_dir
from jenny.config.schema import Base
from jenny.pydantic_compat import Field, field_validator, model_validator
from jenny.security.workspace_access import WORKSPACE_READONLY_METADATA_KEY
from jenny.session.webui_turns import websocket_turn_wall_started_at

if TYPE_CHECKING:
    import ssl

    # Solo per type-hint: nessun import runtime channels→webui (evita
    # l'inversione di layering e i cicli). Fase 8.
    from jenny.webui.gateway_services import GatewayServices
from jenny.channels.http_utils import (
    normalize_config_path as _normalize_config_path,
)
from jenny.channels.http_utils import (
    parse_request_path as _parse_request_path,
)
from jenny.channels.http_utils import (
    query_first as _query_first,
)
from jenny.channels.subagent_activity_wire import (
    UNWATCH_REASON_CLIENT,
    UNWATCH_REASON_LIMIT,
    SubagentWatchRegistry,
    normalize_since,
    normalize_task_id,
)
from jenny.channels.ws_logging import websockets_server_logger
from jenny.channels.ws_parsing import (
    _MAX_FILE_BYTES,
    _MAX_FILES_PER_MESSAGE,
    _MAX_IMAGE_BYTES,
    _MAX_IMAGES_PER_MESSAGE,
    _MAX_VIDEO_BYTES,
    _MAX_VIDEOS_PER_MESSAGE,
    _extract_data_url_mime,
    _is_websocket_upgrade,
    _parse_envelope,
    _parse_inbound_payload,
    classify_media_item,
)
from jenny.channels.ws_sender import OutboundSenderMixin
from jenny.session.keys import (
    PROJECT_SESSION_PREFIX,
    is_project_session_key,
    is_valid_project_name,
)
from jenny.utils.media_decode import (
    FileSizeExceeded,
    save_base64_data_url,
)
from jenny.webui.metadata import WEBUI_DEFAULT_CHAT_ID


class WebSocketConfig(Base):
    """WebSocket server channel configuration.

    Clients connect with URLs like ``ws://{host}:{port}{path}?client_id=...&token=...``.
    - ``client_id``: Used for ``allow_from`` authorization; if omitted, a value is generated and logged.
    - ``token_issue_secret``: If non-empty, the ``token`` query param must match this secret.
      The same secret is used for HTTP API authentication via ``Authorization: Bearer <secret>``
      or ``X-Jenny-Auth: <secret>``.
    - ``websocket_requires_token``: If True, the handshake must include the secret as ``?token=...``.
    - Each connection has its own session: a unique ``chat_id`` maps to the agent session internally.
    - ``media`` field in outbound messages contains local filesystem paths; remote clients need a
      shared filesystem or an HTTP file server to access these files.
    """

    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8765
    path: str = "/"
    token_issue_secret: str = ""
    websocket_requires_token: bool = True
    allow_from: list[str] = Field(default_factory=lambda: ["*"])
    streaming: bool = True
    send_progress: bool = True
    send_tool_hints: bool = False
    show_reasoning: bool = True
    send_max_retries: int = Field(default=3, ge=0, le=10)
    # Default 36 MB, upper 40 MB: supports up to 4 images at ~6 MB each after
    # client-side Worker normalization (see webui Composer). 4 × 6 MB × 1.37
    # (base64 overhead) + envelope framing stays under 36 MB; the 40 MB ceiling
    # leaves a small margin for sender slop without opening a DoS avenue.
    max_message_bytes: int = Field(default=37_748_736, ge=1024, le=41_943_040)
    ping_interval_s: float = Field(default=20.0, ge=5.0, le=300.0)
    ping_timeout_s: float = Field(default=20.0, ge=5.0, le=300.0)
    ssl_certfile: str = ""
    ssl_keyfile: str = ""

    @field_validator("path")
    @classmethod
    def path_must_start_with_slash(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError('path must start with "/"')
        return _normalize_config_path(value)

    @model_validator(mode="after")
    def wildcard_host_requires_auth(self) -> Self:
        if self.host not in ("0.0.0.0", "::"):
            return self
        if self.token_issue_secret.strip():
            return self
        raise ValueError(
            "host is 0.0.0.0 (all interfaces) but token_issue_secret is not set "
            "— set it to prevent unauthenticated access"
        )



class WebSocketChannel(OutboundSenderMixin):
    """Run a local WebSocket server; forward text/JSON messages to the message bus."""

    name = "websocket"

    def __init__(
        self,
        config: Any,
        bus: MessageBus,
        *,
        gateway: GatewayServices,
        ui_query: Any | None = None,
    ):
        if isinstance(config, dict):
            config = WebSocketConfig.model_validate(config)
        self.config: WebSocketConfig = config
        self.logger = logger.bind(channel=self.name)
        self.bus = bus
        self._running = False
        self.send_progress = self.config.send_progress
        self.send_tool_hints = self.config.send_tool_hints
        self.show_reasoning = self.config.show_reasoning
        self.send_max_retries = self.config.send_max_retries
        # chat_id -> connections subscribed to it (fan-out target).
        self._subs: dict[str, set[Any]] = {}
        # connection -> chat_ids it is subscribed to (O(1) cleanup on disconnect).
        self._conn_chats: dict[Any, set[str]] = {}
        # Stable per-connection id ↔ connection: every client shares chat_id
        # "default", so ui_query targeting must key on a dedicated conn_id
        # rather than the chat_id.
        self._conn_ids: dict[Any, str] = {}
        self._conns_by_id: dict[str, Any] = {}
        # Verdetto dell'handshake per connessione: l'RPC client→server pretende
        # un token valido quando un secret è configurato, e il solo posto dove
        # quel controllo è già stato fatto è l'handshake.
        self._conn_authed: dict[Any, bool] = {}
        self._stop_event: asyncio.Event | None = None
        self._server_task: asyncio.Task[None] | None = None
        # Attività fine dei subagent: chi guarda cosa, e l'unico task che la
        # spinge. Il registro nasce vuoto e il task non esiste finché nessuno
        # guarda — il costo aggiunto con il pannello chiuso è zero.
        self._subagent_watches = SubagentWatchRegistry()
        self._activity_pump_task: asyncio.Task[None] | None = None

        # RPC domanda→risposta verso un singolo client (tool ui_view). Opzionale.
        self._ui_query = ui_query
        if ui_query is not None:
            ui_query.set_channel(self)

        self.gateway = gateway
        self._http_router = gateway.http
        self._media = gateway.media
        self._transcripts = gateway.transcripts

        self._stream_text_buffers: dict[tuple[str, str], list[str]] = {}
        # Il reasoning si persiste una volta per segmento, non per chunk: qui si
        # accumula il testo fra ``reasoning_delta`` e ``reasoning_end``. Vedi
        # ``ws_sender.send_reasoning_delta``.
        self._reasoning_text_buffers: dict[tuple[str, str], list[str]] = {}

    # -- BaseChannel methods inlined ----------------------------------------

    @property
    def supports_streaming(self) -> bool:
        return bool(getattr(self.config, "streaming", True))

    def is_allowed(self, sender_id: str) -> bool:
        allow_list = getattr(self.config, "allow_from", None) or []
        if "*" in allow_list:
            return True
        if str(sender_id) in allow_list:
            return True
        return False

    async def _handle_message(
        self,
        sender_id: str,
        chat_id: str,
        content: str,
        media: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not self.is_allowed(sender_id):
            self.logger.warning(
                "Access denied for sender {}. "
                "Add them to allowFrom list in config to grant access.",
                sender_id,
            )
            return
        meta = metadata or {}
        if self.supports_streaming:
            meta = {**meta, "_wants_stream": True}
        msg = InboundMessage(
            channel=self.name,
            sender_id=str(sender_id),
            chat_id=str(chat_id),
            content=content,
            media=media or [],
            metadata=meta,
        )
        await self.bus.publish_inbound(msg)

    # -- Subscription bookkeeping -------------------------------------------

    def _attach(self, connection: Any, chat_id: str) -> None:
        """Idempotently subscribe *connection* to *chat_id*."""
        self._subs.setdefault(chat_id, set()).add(connection)
        self._conn_chats.setdefault(connection, set()).add(chat_id)

    def _cleanup_connection(self, connection: Any) -> None:
        """Remove *connection* from every subscription set; safe to call multiple times."""
        # Punto di uscita unico: ci passano la disconnessione pulita (il
        # ``finally`` di ``_connection_loop``), il ``ConnectionClosed`` a metà
        # invio e il drop per backpressure (app in background su Android). Quindi
        # è qui, e solo qui, che i watch di attività vengono dimenticati: un
        # client che sparisce senza ``subagent_unwatch`` non lascia niente dietro.
        self._subagent_watches.forget(connection)
        chat_ids = self._conn_chats.pop(connection, set())
        for cid in chat_ids:
            subs = self._subs.get(cid)
            if subs is None:
                continue
            subs.discard(connection)
            if not subs:
                self._subs.pop(cid, None)
        self._conn_authed.pop(connection, None)
        conn_id = self._conn_ids.pop(connection, None)
        if conn_id is not None:
            self._conns_by_id.pop(conn_id, None)
            if self._ui_query is not None:
                self._ui_query.cancel_for_conn(conn_id)

    async def _maybe_push_turn_run_wall_clock(self, chat_id: str) -> None:
        """Replay ``goal_status: running`` when a turn is still active (same-process refresh)."""
        t0 = websocket_turn_wall_started_at(chat_id)
        if t0 is None:
            return
        await self.send_goal_status(chat_id, "running", started_at=t0)

    async def _hydrate_after_subscribe(self, chat_id: str) -> None:
        """Replay goal/run strip state after subscribe (same-process refresh)."""
        await self._maybe_push_turn_run_wall_clock(chat_id)

    async def _send_event(self, connection: Any, event: str, **fields: Any) -> None:
        """Send a control event (attached, error, ...) to a single connection."""
        payload: dict[str, Any] = {"event": event}
        payload.update(fields)
        raw = json.dumps(payload, ensure_ascii=False)
        try:
            await connection.send(raw)
        except ConnectionClosed:
            self._cleanup_connection(connection)
        except Exception as e:
            self.logger.warning("failed to send {} event: {}", event, e)

    def _expected_path(self) -> str:
        return _normalize_config_path(self.config.path)

    def _build_ssl_context(self) -> ssl.SSLContext | None:
        import ssl

        cert = self.config.ssl_certfile.strip()
        key = self.config.ssl_keyfile.strip()
        if not cert and not key:
            return None
        if not cert or not key:
            raise ValueError(
                "ssl_certfile and ssl_keyfile must both be set for WSS, or both left empty"
            )
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(certfile=cert, keyfile=key)
        return ctx

    # -- HTTP dispatch ------------------------------------------------------

    async def _dispatch_http(self, connection: Any, request: WsRequest) -> Any:
        """Route an inbound HTTP request to the HTTP handler or WS upgrade."""
        got, query = _parse_request_path(request.path)

        # WebSocket upgrade — channel handles this itself
        expected_ws = self._expected_path()
        if got == expected_ws and _is_websocket_upgrade(request):
            client_id = _query_first(query, "client_id") or ""
            if len(client_id) > 128:
                client_id = client_id[:128]
            if not self.is_allowed(client_id):
                return connection.respond(403, "Forbidden")
            return self._authorize_websocket_handshake(connection, query)

        # Everything else goes to the HTTP handler
        return await self._http_router.dispatch(connection, request)

    def _authorize_websocket_handshake(self, connection: Any, query: dict[str, list[str]]) -> Any:
        """Autorizza l'handshake e **registra il verdetto** per la connessione.

        Il verdetto serve oltre l'handshake: l'RPC client→server
        (``_handle_rpc``) muta lo stato — scrive file — e con un secret
        configurato deve accettare solo una connessione che quel secret l'ha
        presentato. Con ``websocket_requires_token = false`` la chat resta
        aperta senza token, ma la scrittura no: altrimenti sposterebbe una
        mutazione su un canale più debole di ``/api/``, che senza secret
        fallisce chiuso.
        """
        supplied = _query_first(query, "token")
        secret = self.config.token_issue_secret.strip()
        token_matches = bool(secret and supplied and hmac.compare_digest(supplied, secret))

        if self.config.websocket_requires_token:
            if not token_matches:
                return connection.respond(401, "Unauthorized")
            self._conn_authed[connection] = True
            return None

        if supplied and secret and not token_matches:
            return connection.respond(401, "Unauthorized")
        # Registrato solo sulle connessioni accettate: un handshake respinto non
        # arriva mai a ``_cleanup_connection`` e lascerebbe la voce nel dizionario.
        self._conn_authed[connection] = token_matches
        return None

    # -- Server lifecycle and connection ingress ---------------------------

    async def start(self) -> None:
        from jenny.utils.logging_bridge import redirect_lib_logging

        redirect_lib_logging("websockets", level="WARNING")
        ws_logger = websockets_server_logger()

        self._running = True
        self._stop_event = asyncio.Event()

        ssl_context = self._build_ssl_context()
        scheme = "wss" if ssl_context else "ws"

        async def process_request(
            connection: ServerConnection,
            request: WsRequest,
        ) -> Any:
            return await self._dispatch_http(connection, request)

        async def handler(connection: ServerConnection) -> None:
            await self._connection_loop(connection)

        self.logger.info(
            "WebSocket server listening on {}",
            f"{scheme}://{self.config.host}:{self.config.port}{self.config.path}",
        )

        async def runner() -> None:
            server = await serve(
                handler,
                self.config.host,
                self.config.port,
                process_request=process_request,
                max_size=self.config.max_message_bytes,
                ping_interval=self.config.ping_interval_s,
                ping_timeout=self.config.ping_timeout_s,
                ssl=ssl_context,
                logger=ws_logger,
            )
            try:
                assert self._stop_event is not None
                await self._stop_event.wait()
            finally:
                server.close()
                await server.wait_closed()

        self._server_task = asyncio.create_task(runner())
        await self._server_task

    async def _connection_loop(self, connection: Any) -> None:
        request = connection.request
        path_part = request.path if request else "/"
        got, query = _parse_request_path(path_part)

        client_id_raw = _query_first(query, "client_id")
        client_id = client_id_raw.strip() if client_id_raw else ""
        if not client_id:
            client_id = f"anon-{uuid.uuid4().hex[:12]}"
        elif len(client_id) > 128:
            self.logger.warning("client_id too long ({} chars), truncating", len(client_id))
            client_id = client_id[:128]

        default_chat_id = WEBUI_DEFAULT_CHAT_ID
        conn_id = uuid.uuid4().hex

        try:
            await connection.send(
                json.dumps(
                    {
                        "event": "ready",
                        "chat_id": default_chat_id,
                        "client_id": client_id,
                        "conn_id": conn_id,
                    },
                    ensure_ascii=False,
                )
            )
            # Register only after ready is successfully sent to avoid out-of-order sends
            self._conn_ids[connection] = conn_id
            self._conns_by_id[conn_id] = connection
            self._attach(connection, default_chat_id)
            await self._hydrate_after_subscribe(default_chat_id)

            async for raw in connection:
                if isinstance(raw, bytes):
                    try:
                        raw = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        self.logger.warning("ignoring non-utf8 binary frame")
                        continue

                envelope = _parse_envelope(raw)
                if envelope is not None:
                    await self._dispatch_envelope(connection, client_id, envelope)
                    continue

                content = _parse_inbound_payload(raw)
                if content is None:
                    continue
                await self._handle_message(
                    sender_id=client_id,
                    chat_id=default_chat_id,
                    content=content,
                    metadata={
                        "remote": getattr(connection, "remote_address", None),
                        "conn_id": conn_id,
                    },
                )
        except Exception as e:
            self.logger.warning("connection loop error (client={}): {}", client_id, e, exc_info=True)
        finally:
            self._cleanup_connection(connection)

    # -- Inbound WebSocket envelopes ---------------------------------------

    def _save_envelope_media(
        self,
        media: list[Any],
    ) -> tuple[list[str], str | None]:
        """Decode and persist ``media`` items from a ``message`` envelope.

        Returns ``(paths, None)`` on success or ``([], reason)`` on the first
        failure — the caller is expected to surface ``reason`` to the client
        and skip publishing so no half-formed message ever reaches the agent.
        On failure, any files already written to disk earlier in the same
        call are unlinked so partial ingress doesn't leak orphan files.
        ``reason`` is a short, stable token suitable for UI localization.

        Shape: ``list[{"data_url": str, "name"?: str | None}]``.
        """
        image_count = 0
        video_count = 0
        file_count = 0
        for item in media:
            kind = classify_media_item(item)
            if kind == "video":
                video_count += 1
            elif kind == "image":
                image_count += 1
            else:
                # Qualsiasi altro tipo è un allegato generico (documento,
                # archivio, …): salvato e referenziato per path.
                file_count += 1
        if image_count > _MAX_IMAGES_PER_MESSAGE:
            return [], "too_many_images"
        if video_count > _MAX_VIDEOS_PER_MESSAGE:
            return [], "too_many_videos"
        if file_count > _MAX_FILES_PER_MESSAGE:
            return [], "too_many_files"

        media_dir = get_uploads_dir()
        paths: list[str] = []

        def _abort(reason: str) -> tuple[list[str], str]:
            for p in paths:
                try:
                    Path(p).unlink(missing_ok=True)
                except OSError as exc:
                    self.logger.warning(
                        "failed to unlink partial media {}: {}", p, exc
                    )
            return [], reason

        for item in media:
            if not isinstance(item, dict):
                return _abort("malformed")
            data_url = item.get("data_url")
            if not isinstance(data_url, str) or not data_url:
                return _abort("malformed")
            name = item.get("name")
            original_name = name if isinstance(name, str) and name else None
            mime = _extract_data_url_mime(data_url)
            if mime is None:
                return _abort("decode")
            kind = classify_media_item(item)
            if kind == "video":
                max_bytes = _MAX_VIDEO_BYTES
            elif kind == "image":
                max_bytes = _MAX_IMAGE_BYTES
            else:
                max_bytes = _MAX_FILE_BYTES
            try:
                saved = save_base64_data_url(
                    data_url, media_dir, max_bytes=max_bytes,
                    original_name=original_name,
                )
            except FileSizeExceeded:
                return _abort("size")
            except Exception as exc:
                self.logger.warning("media decode failed: {}", exc)
                return _abort("decode")
            if saved is None:
                return _abort("decode")
            paths.append(saved)
        return paths, None

    # Il rifiuto di un ``chat_id`` ``project:`` il cui nome non puo' essere un
    # progetto. Testo e motivo stanno **in un punto solo** perche' li mandano due
    # frame diversi (``attach`` e ``message``) e devono dire la stessa cosa.
    #
    # Inglese e non una chiave i18n, come le risposte sul progetto sparito in
    # ``agent/loop.py``: il client mostra ``detail`` cosi' com'e'
    # (``mobile-jenny.js``, ``case 'error'``), e una chiave nuova nei file i18n
    # e' un file di un altro proprietario. ``reason`` e' la parte per la
    # macchina, sulla forma di ``image_rejected``.
    _INVALID_PROJECT_REASON = "invalid_project_name"
    _INVALID_PROJECT_DETAIL = (
        "That project cannot be opened: its folder name is not a valid project name "
        "(letters, numbers, dot, dash and underscore only, no spaces or accents). "
        "Rename the folder — you can ask me to do it from the personal chat — then "
        "pick it again from the chip above the message box."
    )

    @staticmethod
    def _envelope_chat_id(envelope: dict[str, Any]) -> str | None:
        """Il ``chat_id`` di un frame: la chat personale, quella di un progetto, o un no.

        **Qui il ``chat_id`` del frame era ignorato** e sostituito dalla costante
        ``WEBUI_DEFAULT_CHAT_ID``: era il modo in cui la collassata "una sola
        sessione" era stata implementata, e va bene finche' di conversazioni ce
        n'e' una. Con le sessioni-progetto diventa il punto in cui un messaggio
        mandato a ``project:patreon`` finiva nella chat personale — e non lo
        diceva nessuno, perche' dal lato client sembrava partito.

        L'elenco delle forme accettate resta chiuso, e la verifica del nome e'
        quella di ``jenny.session.keys``: un ``chat_id`` che non riconosciamo
        cade sulla chat personale invece di aprire una conversazione inventata.
        Il ``chat_id`` e' anche la chiave del *thread* su disco, quindi lasciar
        passare una stringa arbitraria vorrebbe dire lasciar creare file.

        **Ma per un ``chat_id`` che *e'* nella forma ``project:`` e sbaglia solo
        il nome, la caduta sulla chat personale era la risposta sbagliata**, e
        ritorna ``None`` — cioe' il frame va rifiutato. Un nome come
        ``Ricerca ETF`` o ``citta``-con-l'accento non passa
        ``is_valid_project_name``, e il frame che lo portava finiva sulla
        conversazione personale: scope ``default()`` (l'installazione intera
        scrivibile), ``session_kind`` ``personal`` (quindi il contenuto alimenta
        ``MEMORY.md`` via Dream — la cosa precisa che le sessioni-progetto
        esistono per impedire) e la trascrizione personale servita nella schermata
        di un progetto. Tre guasti insieme, e nessuno dei tre visibile: l'eco
        ``attached`` non la guarda nessuno.

        La differenza fra i due esiti non e' capricciosa: davanti a spazzatura
        (``chat_id`` assente, ``"pippo"``, un numero) la chat personale e'
        l'unica risposta sensata — e' la conversazione, e non c'e' niente da
        rifiutare. Davanti a ``project:<qualcosa>`` il client ha detto
        esplicitamente *quale* conversazione vuole: dargliene un'altra in
        silenzio e' peggio di dire no.
        """
        raw = envelope.get("chat_id")
        if not isinstance(raw, str) or not is_project_session_key(raw):
            return WEBUI_DEFAULT_CHAT_ID
        if not is_valid_project_name(raw[len(PROJECT_SESSION_PREFIX):]):
            return None
        return raw

    async def _refuse_invalid_project(self, connection: Any, envelope: dict[str, Any]) -> None:
        """Dice no, ad alta voce, a un frame che nomina un progetto impossibile.

        Il nome ricevuto **non** viene rimandato indietro nel frame di errore:
        arriva da un client, quindi la sua lunghezza e il suo contenuto non sono
        nostri, e il chip sa gia' quale progetto ha chiesto. Nel log invece ci
        sta, troncato — e' il solo posto in cui si scopre *quale* cartella.
        """
        raw = envelope.get("chat_id")
        shown = raw[:80] if isinstance(raw, str) else raw
        self.logger.warning(
            "frame refused: {!r} is project-shaped but not a valid project name", shown
        )
        await self._send_event(
            connection,
            "error",
            detail=self._INVALID_PROJECT_DETAIL,
            reason=self._INVALID_PROJECT_REASON,
        )

    async def _dispatch_envelope(
        self,
        connection: Any,
        client_id: str,
        envelope: dict[str, Any],
    ) -> None:
        """Route one typed inbound envelope (``attach`` / ``message`` / ...)."""
        t = envelope.get("type")
        if t == "attach":
            cid = self._envelope_chat_id(envelope)
            if cid is None:
                # Niente ``_attach``: agganciare la connessione a una chiave che
                # nessun turno potra' mai usare la iscriverebbe a un canale morto.
                await self._refuse_invalid_project(connection, envelope)
                return
            self._attach(connection, cid)
            await self._send_event(connection, "attached", chat_id=cid)
            await self._hydrate_after_subscribe(cid)
            return
        if t == "message":
            cid = self._envelope_chat_id(envelope)
            if cid is None:
                # Prima di leggere il contenuto e **prima** di decodificare i
                # media: un frame rifiutato non deve scrivere niente su disco.
                await self._refuse_invalid_project(connection, envelope)
                return
            content = envelope.get("content")
            if not isinstance(content, str):
                await self._send_event(
                    connection, "error",
                    detail="missing content", reason="missing_content",
                )
                return

            raw_media = envelope.get("media")
            media_paths: list[str] = []
            if raw_media is not None:
                if not isinstance(raw_media, list):
                    await self._send_event(
                        connection, "error",
                        detail="image_rejected", reason="malformed",
                    )
                    return
                # Decode/persist off the event loop: base64 decode + disk
                # write for videos up to 20 MB would otherwise freeze the
                # entire gateway for hundreds of ms on Android CPUs.
                media_paths, reason = await asyncio.to_thread(
                    self._save_envelope_media, raw_media
                )
                if reason is not None:
                    await self._send_event(
                        connection, "error",
                        detail="image_rejected", reason=reason,
                    )
                    return

            # Allow image-only turns (content may be empty when media is attached).
            if not content.strip() and not media_paths:
                await self._send_event(
                    connection, "error",
                    detail="missing content", reason="missing_content",
                )
                return

            # Auto-attach on first use so clients can one-shot without a separate attach.
            self._attach(connection, cid)
            await self._hydrate_after_subscribe(cid)
            metadata: dict[str, Any] = {"remote": getattr(connection, "remote_address", None)}
            # Sola lettura: viaggia **nel messaggio**, come lo scope. Il server
            # non la tiene da nessuna parte — se la tenesse potrebbe raccontare
            # al client uno stato diverso da quello con cui il messaggio e'
            # partito, che e' il solo guasto che conta qui. Solo ``True`` accende
            # (v. ``readonly_from_metadata``).
            if envelope.get(WORKSPACE_READONLY_METADATA_KEY) is True:
                metadata[WORKSPACE_READONLY_METADATA_KEY] = True
            if envelope.get("webui") is True:
                metadata["webui"] = True
                metadata.update(self._transcripts.client_turn_metadata(envelope.get("turn_id")))
            # Which connection sent this turn — the ui_view tool queries it back.
            conn_id = self._conn_ids.get(connection)
            if conn_id is not None:
                metadata["conn_id"] = conn_id
            if self.is_allowed(client_id):
                self._transcripts.append_user_message(
                    cid,
                    content,
                    metadata=metadata,
                    media_paths=media_paths or None,
                )
            await self._handle_message(
                sender_id=client_id,
                chat_id=cid,
                content=content,
                media=media_paths or None,
                metadata=metadata,
            )
            return
        if t == "subagent_watch":
            await self._handle_subagent_watch(connection, envelope)
            return
        if t == "subagent_unwatch":
            await self._handle_subagent_unwatch(connection, envelope)
            return
        if t == "ui_result":
            # Risposta del client a una ui_query: risolvi la Future in attesa.
            # Tutta la validazione/rifiuto vive nel coordinator (modulo leaf).
            cid = self._conn_ids.get(connection)
            if self._ui_query is not None and cid is not None:
                self._ui_query.handle_ui_result(cid, envelope)
            return
        if t == "rpc":
            await self._handle_rpc(connection, envelope)
            return
        await self._send_event(
            connection, "error",
            detail=f"unknown type: {t!r}", reason="unknown_type",
        )

    # -- RPC client→server -------------------------------------------------

    async def _handle_rpc(self, connection: Any, envelope: dict[str, Any]) -> None:
        """Esegue un comando richiesto dalla WebUI e risponde con ``rpc_result``.

        È la via per le operazioni che portano contenuto (il salvataggio di un
        file dall'editor): gli header HTTP del gateway non possono trasportarlo,
        un frame WebSocket sì. Validazione, autorizzazione e traduzione degli
        errori stanno in ``channels.ws_rpc``; la logica in ``webui.commands``.
        """
        from jenny.channels import ws_rpc
        from jenny.webui.commands import CommandError

        conn_id = self._conn_ids.get(connection) or "unknown"
        try:
            rpc_id, method, params = ws_rpc.parse_rpc_frame(envelope)
        except ws_rpc.RpcFrameError as exc:
            ws_rpc.log_dropped_frame(exc, conn_id)
            return
        except CommandError as exc:
            # C'è un id valido ma il resto del frame no: l'errore è recapitabile.
            raw_id = envelope.get("id")
            await self._send_event(
                connection, "rpc_result", **ws_rpc.error_frame(str(raw_id), exc),
            )
            return

        try:
            result = await ws_rpc.run_rpc(
                self.gateway.commands,
                method=method,
                params=params,
                secret=self.config.token_issue_secret,
                connection_authenticated=self._conn_authed.get(connection, False),
            )
        except CommandError as exc:
            self.logger.info("rpc {} failed for {}: {}", method, conn_id, exc.code)
            await self._send_event(
                connection, "rpc_result", **ws_rpc.error_frame(rpc_id, exc),
            )
            return
        await self._send_event(
            connection, "rpc_result", **ws_rpc.result_frame(rpc_id, result),
        )

    # -- Osservazione dell'attività di un subagent -------------------------

    async def _handle_subagent_watch(
        self,
        connection: Any,
        envelope: dict[str, Any],
    ) -> None:
        """``{"type": "subagent_watch", "task_id": ..., "since"?: int}``.

        Ordine deliberato: **prima** parte la finestra corrente, **poi** si
        registra il watch con il cursore che quella finestra ha consegnato. Al
        contrario il pump potrebbe infilare un delta davanti alla risposta
        iniziale e portare avanti il cursore, e il client vedrebbe arrivare la
        coda prima della testa.

        Un task ignoto, mai esistito o già finito e ripulito non è un errore: la
        risposta è una finestra vuota con ``latest_seq == 0``, e il watch resta
        registrato — se quel task inizia a produrre (o è appena stato lanciato)
        i suoi eventi arrivano dal tick successivo.
        """
        task_id = normalize_task_id(envelope.get("task_id"))
        if task_id is None:
            await self._send_event(
                connection, "error",
                detail="invalid task_id", reason="invalid_task_id",
            )
            return
        since = normalize_since(envelope.get("since"))
        cursor = await self.send_subagent_activity_window(connection, task_id, since=since)
        if connection not in self._conn_chats:
            # La connessione è morta durante l'invio (``_fanout`` l'ha già
            # ripulita): registrare il watch ora creerebbe un watcher fantasma
            # che nessun cleanup verrebbe più a raccogliere.
            return
        evicted = self._subagent_watches.watch(connection, task_id, cursor=cursor)
        for gone in evicted:
            # Il client deve sapere quale vista è ferma: senza l'ack resterebbe
            # in attesa di delta che non arriveranno più.
            await self._send_event(
                connection, "subagent_unwatched", task_id=gone, reason=UNWATCH_REASON_LIMIT
            )
        self._ensure_subagent_activity_pump()

    async def _handle_subagent_unwatch(
        self,
        connection: Any,
        envelope: dict[str, Any],
    ) -> None:
        """``{"type": "subagent_unwatch", "task_id": ...}``. Idempotente."""
        task_id = normalize_task_id(envelope.get("task_id"))
        if task_id is None:
            await self._send_event(
                connection, "error",
                detail="invalid task_id", reason="invalid_task_id",
            )
            return
        self._subagent_watches.unwatch(connection, task_id)
        await self._send_event(
            connection, "subagent_unwatched", task_id=task_id, reason=UNWATCH_REASON_CLIENT
        )

    # -- Outbound WebSocket events -----------------------------------------

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        # Prima del teardown del server: il pump è l'unico task che il canale
        # possiede oltre a quello del server, e lasciarlo vivo lo farebbe girare
        # su un registro che stiamo per svuotare.
        self.stop_subagent_activity_pump()
        if self._stop_event:
            self._stop_event.set()
        if self._server_task:
            try:
                await self._server_task
            except Exception as e:
                self.logger.warning("server task error during shutdown: {}", e)
            self._server_task = None
        self._subs.clear()
        self._conn_chats.clear()

