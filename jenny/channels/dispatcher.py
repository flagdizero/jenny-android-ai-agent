"""Outbound dispatcher: possiede i canali (WebSocket + Telegram) e smista
i messaggi outbound del bus al canale indicato da ``msg.channel``."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from jenny.bus.events import COORDINATION_FLAGS, OutboundMessage
from jenny.bus.queue import MessageBus
from jenny.config.schema import Config
from jenny.runtime.notifier import notify_delivery
from jenny.webui.metadata import WEBUI_DEFAULT_CHAT_ID

if TYPE_CHECKING:
    from jenny.session.manager import SessionManager


_SEND_RETRY_DELAYS = (1, 2, 4)

# Duplicate suppression only needs recent replies; older entries are evicted
# FIFO so the fingerprint map stays bounded on a long-lived gateway process.
_MAX_REPLY_FINGERPRINTS = 512

CHANNEL_NAME = "websocket"

# Flag che marcano un outbound come coordinamento/streaming: tutto ciò che NON
# è un messaggio finale user-visible, e quindi non va proiettato sulla vista
# WebUI quando è indirizzato a un altro canale utente. Deriva dalla sorgente
# unica ``COORDINATION_FLAGS`` più ``_mirror`` (copie del fan-out proattivo di
# delivery.py, escluse solo qui e non nel gating webui-only di Telegram).
_NON_FINAL_METADATA_FLAGS = (*COORDINATION_FLAGS, "_mirror")


class WebSocketDispatcher:
    """Owns the WebSocket channel and dispatches outbound bus messages to it."""

    def __init__(
        self,
        config: Config,
        bus: MessageBus,
        *,
        session_manager: SessionManager | None = None,
        snapshot_service: Any | None = None,
        webui_runtime_model_name: Callable[[], str | None] | None = None,
        onboarding_event: asyncio.Event | None = None,
        on_settings_changed: Callable[[], None] | None = None,
        on_jobs_changed: Callable[[str], None] | None = None,
        ui_query: Any | None = None,
        get_subagent_manager: Callable[[], Any | None] | None = None,
        get_cron_service: Callable[[], Any | None] | None = None,
    ):
        self.config = config
        self.bus = bus
        self._session_manager = session_manager
        self._snapshot_service = snapshot_service
        self._get_subagent_manager = get_subagent_manager
        self._get_cron_service = get_cron_service
        self._webui_runtime_model_name = webui_runtime_model_name
        self._onboarding_event = onboarding_event
        self._on_settings_changed = on_settings_changed
        self._on_jobs_changed = on_jobs_changed
        self._ui_query = ui_query
        self.channels: dict[str, Any] = {}
        self._dispatch_task: asyncio.Task | None = None
        self._hot_tasks: list[asyncio.Task] = []
        self._origin_reply_fingerprints: dict[tuple[str, str, str], str] = {}

        self._init_channel()
        self._init_telegram()

    def _init_channel(self) -> None:
        """Initialize the WebSocket channel from the top-level websocket config."""
        from jenny.channels.websocket import WebSocketChannel, WebSocketConfig
        from jenny.webui.gateway_services import build_gateway_services

        section = self.config.websocket
        if not section:
            return
        parsed = WebSocketConfig.model_validate(section)
        if not parsed.enabled:
            logger.info("WebSocket channel disabled via config (websocket.enabled=false)")
            return
        workspace = Path(self.config.workspace_path)
        gateway = build_gateway_services(
            config=parsed,
            bus=self.bus,
            session_manager=self._session_manager,
            workspace_path=workspace,
            default_restrict_to_workspace=self.config.security.restrict_to_workspace,
            disabled_skills=set(self.config.agents.defaults.disabled_skills),
            runtime_model_name=self._webui_runtime_model_name,
            snapshot_service=self._snapshot_service,
            get_subagent_manager=self._get_subagent_manager,
            get_cron_service=self._get_cron_service,
            logger=logger,
            onboarding_event=self._onboarding_event,
            on_settings_changed=self._on_settings_changed,
            on_jobs_changed=self._on_jobs_changed,
            on_telegram_changed=self._schedule_telegram_reload,
        )
        self.channels[CHANNEL_NAME] = WebSocketChannel(
            section, self.bus, gateway=gateway, ui_query=self._ui_query
        )
        logger.info("WebSocket channel enabled")

    def _init_telegram(self) -> None:
        """Crea il canale Telegram se abilitato in config con un token.

        I due casi di uscita si loggano, e separatamente, come fa
        ``_init_channel`` tre righe sopra per il websocket. Prima si usciva in
        silenzio, e il costo si e' visto il 02/09/2026: un canale spento e un
        bot che tace sono indistinguibili dall'esterno, e nei log non c'era una
        riga che dicesse quale dei due fosse — la diagnosi e' finita a datare
        l'``enabled`` dagli snapshot del workspace. "Spento" e "non
        configurato" restano distinti perche' portano a due rimedi diversi: il
        toggle nelle impostazioni, oppure il token da BotFather.
        """
        section = self.config.telegram
        if not section.bot_token:
            logger.info("Telegram channel not configured (no bot token)")
            return
        if not section.enabled:
            logger.info("Telegram channel disabled via config (telegram.enabled=false)")
            return
        from jenny.channels.telegram import TelegramChannel
        from jenny.webui.telegram_api import record_paired

        self.channels["telegram"] = TelegramChannel(
            section,
            self.bus,
            on_paired=record_paired,
            language=self.config.agents.defaults.language,
        )
        logger.info("Telegram channel enabled")

    @property
    def enabled(self) -> bool:
        return bool(self.channels)

    @staticmethod
    def _channel_allows_progress(channel: Any, *, tool_hint: bool = False) -> bool:
        if channel is None:
            return False
        return channel.send_tool_hints if tool_hint else channel.send_progress

    async def _start_channel(self, name: str, channel: Any) -> None:
        logger.info("Starting {} channel...", name)
        try:
            await channel.start()
        except Exception:
            logger.exception("Failed to start {} channel", name)

    async def start(self) -> None:
        if not self.channels:
            logger.warning("No channels enabled")
            return
        self._dispatch_task = asyncio.create_task(self._dispatch_outbound())
        tasks = [
            asyncio.create_task(self._start_channel(name, ch))
            for name, ch in self.channels.items()
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def stop(self) -> None:
        logger.info("Stopping all channels...")
        if self._dispatch_task:
            self._dispatch_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._dispatch_task
        for task in self._hot_tasks:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        self._hot_tasks.clear()
        for name, channel in self.channels.items():
            try:
                await channel.stop()
                logger.info("Stopped {} channel", name)
            except Exception:
                logger.exception("Error stopping {} channel", name)

    def _schedule_telegram_reload(self) -> None:
        """Callback sync per il layer settings: applica la config a caldo."""
        self._hot_tasks.append(asyncio.create_task(self.reload_telegram()))
        self._hot_tasks = [t for t in self._hot_tasks if not t.done()]

    async def reload_telegram(self) -> None:
        """Applica a caldo la config Telegram (token salvato/unpair/disable).

        Ferma il canale esistente, rilegge la config da disco e, se ancora
        abilitato, ricrea e avvia il canale — così il pairing funziona subito
        dopo il salvataggio del token, senza riavvio del gateway.
        """
        from jenny.config.loader import load_config

        old = self.channels.pop("telegram", None)
        if old is not None:
            try:
                await old.stop()
            except Exception:
                logger.exception("Error stopping telegram channel during reload")
        try:
            self.config = load_config()
        except Exception:
            logger.exception("reload_telegram: config reload failed")
            return
        self._init_telegram()
        new = self.channels.get("telegram")
        if new is not None:
            self._hot_tasks.append(asyncio.create_task(self._start_channel("telegram", new)))
        self._hot_tasks = [t for t in self._hot_tasks if not t.done()]

    @staticmethod
    def _fingerprint_content(content: str) -> str:
        normalized = " ".join(content.split())
        return hashlib.sha1(normalized.encode("utf-8")).hexdigest() if normalized else ""

    def _remember_fingerprint(self, key: tuple[str, str, str], fingerprint: str) -> None:
        self._origin_reply_fingerprints[key] = fingerprint
        while len(self._origin_reply_fingerprints) > _MAX_REPLY_FINGERPRINTS:
            self._origin_reply_fingerprints.pop(next(iter(self._origin_reply_fingerprints)))

    def _should_suppress_outbound(self, msg: OutboundMessage) -> bool:
        metadata = msg.metadata or {}
        if metadata.get("_progress"):
            return False
        fingerprint = self._fingerprint_content(msg.content)
        if not fingerprint:
            return False
        origin_message_id = metadata.get("origin_message_id")
        if isinstance(origin_message_id, str) and origin_message_id:
            key = (msg.channel, msg.chat_id, origin_message_id)
            if self._origin_reply_fingerprints.get(key) == fingerprint:
                return True
            self._remember_fingerprint(key, fingerprint)
        message_id = metadata.get("message_id")
        if isinstance(message_id, str) and message_id:
            key = (msg.channel, msg.chat_id, message_id)
            self._remember_fingerprint(key, fingerprint)
        return False

    def _route_channel(self, msg: OutboundMessage) -> Any | None:
        return self.channels.get(msg.channel)

    async def _dispatch_outbound(self) -> None:
        logger.info("Outbound dispatcher started")
        pending: list[OutboundMessage] = []
        consecutive_errors = 0
        while True:
            try:
                if pending:
                    msg = pending.pop(0)
                else:
                    msg = await self.bus.consume_outbound()

                if (
                    msg.metadata.get("_reasoning_delta")
                    or msg.metadata.get("_reasoning_end")
                ):
                    channel = self._route_channel(msg)
                    if channel is not None and channel.show_reasoning:
                        await self._send_with_retry(channel, msg)
                    continue

                if msg.metadata.get("_progress"):
                    # Gating per-canale: il canale di destinazione decide se
                    # ricevere progress/tool-hint (Telegram: mai).
                    target = self._route_channel(msg)
                    if not self._channel_allows_progress(
                        target, tool_hint=bool(msg.metadata.get("_tool_hint"))
                    ):
                        continue

                if msg.metadata.get("_retry_wait"):
                    continue

                if msg.metadata.get("_stream_delta") and not msg.metadata.get("_stream_end"):
                    msg, extra_pending = self._coalesce_stream_deltas(msg)
                    pending.extend(extra_pending)

                channel = self._route_channel(msg)
                if channel is not None:
                    if (
                        not msg.metadata.get("_stream_delta")
                        and not msg.metadata.get("_stream_end")
                        and not msg.metadata.get("_streamed")
                    ):
                        if self._should_suppress_outbound(msg):
                            logger.info("Suppressing duplicate outbound message to {}:{}", msg.channel, msg.chat_id)
                            continue
                    await self._send_with_retry(channel, msg)
                    # Proiezione sulla vista canonica: la consegna al canale
                    # d'origine è avvenuta, ora la WebUI (transcript + client
                    # live). Inline — non ri-accodata sul bus — così la riga
                    # arriva prima del turn_end già in coda.
                    await self._mirror_final_to_webui_view(msg)
                elif not msg.metadata.get("_runtime_model_updated"):
                    logger.warning("Unknown channel: {}", msg.channel)

                consecutive_errors = 0

            except asyncio.CancelledError:
                break
            except Exception:
                # Un singolo messaggio "veleno" (metadata malformato, bug in
                # _send_with_retry, ecc.) non deve abbattere l'unico pump di
                # consegna. Il messaggio è già stato estratto da pending/bus
                # sopra, quindi viene scartato — nessun re-enqueue.
                logger.exception("Outbound dispatcher: dropping message after unexpected error")
                # Backstop anti-spin: se anche la consume/loop stessa fallisce in
                # modo persistente, cediamo il controllo con un piccolo backoff
                # invece di occupare la CPU al 100%.
                consecutive_errors += 1
                if consecutive_errors >= 5:
                    await asyncio.sleep(min(0.1 * consecutive_errors, 1.0))
                continue

    async def _mirror_final_to_webui_view(self, msg: OutboundMessage) -> None:
        """Consegna alla vista WebUI il finale di un turno di un altro canale utente.

        La WebUI è la vista canonica della conversazione unificata: un finale
        consegnato a Telegram (o a un futuro canale utente) viene proiettato
        sul thread ``default`` con ``origin_channel`` nei metadata; il canale
        websocket persiste la riga transcript e la trasmette ai client live.
        Le copie ``_mirror`` del fan-out proattivo sono escluse (la primaria
        websocket è già stata pubblicata dal ChannelDeliverer).
        """
        if msg.channel == CHANNEL_NAME:
            return
        if not msg.content or not msg.content.strip():
            return
        if any(msg.metadata.get(flag) for flag in _NON_FINAL_METADATA_FLAGS):
            return
        ws_channel = self.channels.get(CHANNEL_NAME)
        if ws_channel is None:
            return
        copy = OutboundMessage(
            channel=CHANNEL_NAME,
            chat_id=WEBUI_DEFAULT_CHAT_ID,
            content=msg.content,
            media=msg.media,
            metadata={**msg.metadata, "origin_channel": msg.channel},
            buttons=msg.buttons,
        )
        await self._send_with_retry(ws_channel, copy)

    @staticmethod
    async def _send_once(
        channel: Any,
        msg: OutboundMessage,
        *,
        only_conns: list[Any] | None = None,
        skip_persist: bool = False,
    ) -> list[Any] | None:
        """Perform (or retry) one delivery attempt.

        Returns the list of connections still needing delivery, or ``None``/``[]``
        when the send type doesn't support partial-fan-out retry tracking or has
        fully succeeded. On a retry (``only_conns`` set), *skip_persist* must be
        True so the transcript row isn't written a second time and, for
        ``send_delta``, the stream buffer isn't re-mutated.
        """
        if msg.metadata.get("_reasoning_end"):
            return await channel.send_reasoning_end(
                msg.chat_id, msg.metadata, only_conns=only_conns, skip_persist=skip_persist
            )
        elif msg.metadata.get("_reasoning_delta"):
            return await channel.send_reasoning_delta(
                msg.chat_id, msg.content, msg.metadata,
                only_conns=only_conns, skip_persist=skip_persist,
            )
        elif msg.metadata.get("_file_edit_events"):
            edits = msg.metadata.get("_file_edit_events")
            return await channel.send_file_edit_events(
                msg.chat_id,
                edits if isinstance(edits, list) else [],
                msg.metadata,
                only_conns=only_conns,
                skip_persist=skip_persist,
            )
        elif msg.metadata.get("_stream_delta") or msg.metadata.get("_stream_end"):
            return await channel.send_delta(
                msg.chat_id, msg.content, msg.metadata,
                only_conns=only_conns, skip_persist=skip_persist,
            )
        elif not msg.metadata.get("_streamed"):
            return await channel.send(msg, only_conns=only_conns, skip_persist=skip_persist)
        # Messaggio finale già streammato ai client via delta: qui non si
        # rispedisce nulla, ma è l'unico punto che vede il testo completo di
        # un turno streammato (es. cron con WebView connessa-ma-in-pausa a
        # schermo spento) — resta solo l'alert di sistema Android. Il gate
        # foreground sta nel bridge Kotlin; `skip_persist` esclude i retry.
        if not skip_persist and msg.content.strip():
            notify_delivery(msg.content, msg.metadata)
        return None

    def _coalesce_stream_deltas(
        self, first_msg: OutboundMessage
    ) -> tuple[OutboundMessage, list[OutboundMessage]]:
        target_key = (first_msg.channel, first_msg.chat_id)
        combined_content = first_msg.content
        final_metadata = dict(first_msg.metadata or {})
        non_matching: list[OutboundMessage] = []
        while True:
            try:
                next_msg = self.bus.outbound.get_nowait()
            except asyncio.QueueEmpty:
                break
            same_target = (next_msg.channel, next_msg.chat_id) == target_key
            is_delta = next_msg.metadata and next_msg.metadata.get("_stream_delta")
            is_end = next_msg.metadata and next_msg.metadata.get("_stream_end")
            if same_target and is_delta and not final_metadata.get("_stream_end"):
                combined_content += next_msg.content
                if is_end:
                    final_metadata["_stream_end"] = True
                    break
            else:
                non_matching.append(next_msg)
                break
        merged = OutboundMessage(
            channel=first_msg.channel,
            chat_id=first_msg.chat_id,
            content=combined_content,
            metadata=final_metadata,
        )
        return merged, non_matching

    @staticmethod
    def _discard_abandoned_stream(channel: Any, msg: OutboundMessage) -> None:
        """Best-effort cleanup when a stream delta/end is never fully delivered.

        Without this, an undeliverable ``stream_end`` would leave its buffered
        text in ``_stream_text_buffers`` forever (send_delta only pops it on
        full success) since retries are now exhausted.
        """
        if not (msg.metadata.get("_stream_delta") or msg.metadata.get("_stream_end")):
            return
        discard = getattr(channel, "discard_stream_buffer", None)
        if discard is None:
            return
        with suppress(Exception):
            discard(msg.chat_id, msg.metadata.get("_stream_id"))

    async def _send_with_retry(self, channel: Any, msg: OutboundMessage) -> None:
        """Deliver *msg*, retrying only what actually failed.

        A retry must not re-run the whole operation: ``_send_once`` persists to
        the transcript and fans out to every subscribed connection, so blindly
        calling it again would duplicate the persisted row and resend to
        connections that already received the message on a prior attempt. Once
        the first attempt has run, ``only_conns``/``skip_persist`` narrow every
        subsequent attempt to just the connections that still need delivery,
        with persistence (and, for send_delta, the stream buffer mutation)
        skipped entirely.
        """
        max_attempts = max(getattr(channel, "send_max_retries", 3), 1)
        only_conns: list[Any] | None = None
        skip_persist = False
        for attempt in range(max_attempts):
            is_last = attempt == max_attempts - 1
            try:
                pending = await self._send_once(
                    channel, msg, only_conns=only_conns, skip_persist=skip_persist,
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if is_last:
                    logger.exception(
                        "Failed to send to {} after {} attempts",
                        msg.channel, max_attempts
                    )
                    self._discard_abandoned_stream(channel, msg)
                    return
                delay = _SEND_RETRY_DELAYS[min(attempt, len(_SEND_RETRY_DELAYS) - 1)]
                logger.warning(
                    "Send to {} failed (attempt {}/{}): {}, retrying in {}s",
                    msg.channel, attempt + 1, max_attempts, type(e).__name__, delay
                )
                # The failure point is unknown (it may predate persistence or
                # fan-out entirely) — fall back to a full resend since we
                # can't be sure anything was delivered or persisted yet.
                only_conns = None
                skip_persist = False
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    raise
                continue

            if not pending:
                return  # fully delivered (or a send type with no retry tracking)

            if is_last:
                logger.warning(
                    "Send to {} partially failed after {} attempts: {} connection(s) never delivered",
                    msg.channel, max_attempts, len(pending)
                )
                self._discard_abandoned_stream(channel, msg)
                return

            delay = _SEND_RETRY_DELAYS[min(attempt, len(_SEND_RETRY_DELAYS) - 1)]
            logger.warning(
                "Send to {} partially delivered (attempt {}/{}): retrying {} connection(s) in {}s",
                msg.channel, attempt + 1, max_attempts, len(pending), delay
            )
            only_conns = pending
            skip_persist = True
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                raise
