"""Session turn helpers for WebUI-capable WebSocket sessions."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from loguru import logger

from jenny.bus.events import INTERNAL_CHANNEL, InboundMessage, OutboundMessage
from jenny.bus.queue import MessageBus
from jenny.bus.runtime_events import (
    RuntimeEventBus,
    RuntimeEventContext,
    RuntimeModelChanged,
    SessionTurnStarted,
    TurnCompleted,
    TurnRunStatusChanged,
)
from jenny.config.loader import load_config
from jenny.session.keys import UNIFIED_SESSION_KEY
from jenny.session.manager import Session, SessionManager
from jenny.session.mascot_mood import (
    MOOD_TOKEN_USAGE_SOURCE,
    NEUTRAL_MOOD,
    classify_mood,
    mood_inputs,
    resolve_mood_model,
)
from jenny.session.turn_visibility import resolve_turn_visibility
from jenny.utils.llm_runtime import LLMRuntime
from jenny.webui.metadata import WEBUI_DEFAULT_CHAT_ID

if TYPE_CHECKING:
    from jenny.config.schema import Config

WEBUI_SESSION_METADATA_KEY = "webui"

# Wall-clock turn start per ``chat_id`` (websocket only). Survives browser refresh while the
# gateway process stays up; cleared on idle/stop and implicitly dropped on restart.
_WEBSOCKET_TURN_WALL_STARTED_AT: dict[str, float] = {}


def mark_webui_session(session: Session, metadata: dict[str, Any]) -> bool:
    """Persist a WebUI marker only when the inbound websocket frame opted in."""
    if metadata.get(WEBUI_SESSION_METADATA_KEY) is not True:
        return False
    session.metadata[WEBUI_SESSION_METADATA_KEY] = True
    return True


def webui_view_target(ctx: RuntimeEventContext) -> tuple[str, str] | None:
    """Ritorna il target (channel, chat_id) della vista WebUI per un turno.

    La WebUI è la vista canonica della conversazione unificata: i turni
    websocket la aggiornano direttamente, quelli di altri canali utente
    (es. Telegram) vengono proiettati sul thread ``default``. I turni
    interni (cron, dream, heartbeat) e le sessioni non unificate non hanno
    proiezione.
    """
    # Il canale d'origine non basta a decidere: un heartbeat o un cron monitor
    # gira *su* ``websocket:default`` — è il target a cui potrà consegnare se la
    # condizione scatta — ma nessuno dei suoi marcatori di turno (spinner,
    # ``_turn_end``) appartiene alla conversazione dell'utente. Il discrimine è
    # la visibilità del turno, non il canale.
    if resolve_turn_visibility(
        ctx.metadata, channel=ctx.channel, session_key=ctx.session_key
    ).silent:
        return None
    if ctx.channel == "websocket":
        return (ctx.channel, ctx.chat_id)
    if ctx.channel == INTERNAL_CHANNEL or ctx.session_key != UNIFIED_SESSION_KEY:
        return None
    return ("websocket", WEBUI_DEFAULT_CHAT_ID)


def websocket_turn_wall_started_at(chat_id: str) -> float | None:
    """Return ``time.time()`` when the active user turn began, if still running."""
    return _WEBSOCKET_TURN_WALL_STARTED_AT.get(chat_id)


async def publish_turn_run_status(
    bus: MessageBus,
    msg: InboundMessage,
    status: str,
    *,
    started_at: float | None = None,
) -> None:
    """Notify WebSocket clients while a user turn is executing (timing strip)."""
    if msg.channel != "websocket":
        return
    cid = str(msg.chat_id)
    meta: dict[str, Any] = {
        **dict(msg.metadata or {}),
        "_goal_status": True,
        "goal_status": status,
    }
    if status == "running":
        if isinstance(started_at, int | float) and started_at > 0:
            t0 = float(started_at)
        else:
            t0 = time.time()
        meta["started_at"] = t0
        _WEBSOCKET_TURN_WALL_STARTED_AT[cid] = t0
    else:
        _WEBSOCKET_TURN_WALL_STARTED_AT.pop(cid, None)
    await bus.publish_outbound(
        OutboundMessage(
            channel=msg.channel,
            chat_id=cid,
            content="",
            metadata=meta,
        ),
    )

@dataclass
class WebuiTurnCoordinator:
    """Translate generic runtime events into WebUI/WebSocket wire messages."""

    bus: MessageBus
    sessions: SessionManager
    schedule_background: Callable[[Awaitable[None]], None]
    # Il config si legge **al momento della chiamata**, non alla costruzione:
    # ``mascotMood`` e il suo preset valgono dal turno dopo senza riavvio. In
    # test si inietta un lettore che non tocca il disco.
    config_loader: Callable[[], Config] = load_config

    def subscribe(self, runtime_events: RuntimeEventBus) -> Callable[[], None]:
        """Subscribe this coordinator to runtime events."""
        unsubscribe = [
            runtime_events.subscribe(
                self._handle_session_turn_started,
                SessionTurnStarted,
            ),
            runtime_events.subscribe(
                self._handle_run_status_changed,
                TurnRunStatusChanged,
            ),
            runtime_events.subscribe(
                self._handle_turn_completed_event,
                TurnCompleted,
            ),
            runtime_events.subscribe(
                self._handle_runtime_model_changed,
                RuntimeModelChanged,
            ),
        ]

        def _unsubscribe() -> None:
            for fn in reversed(unsubscribe):
                fn()

        return _unsubscribe

    @staticmethod
    def _view_msg(ctx: RuntimeEventContext) -> InboundMessage | None:
        """Messaggio sintetico indirizzato alla vista WebUI del turno (o None)."""
        target = webui_view_target(ctx)
        if target is None:
            return None
        channel, chat_id = target
        return InboundMessage(
            channel=channel,
            sender_id="runtime",
            chat_id=chat_id,
            content="",
            metadata=dict(ctx.metadata or {}),
            session_key_override=ctx.session_key,
        )

    async def _handle_session_turn_started(self, event: SessionTurnStarted) -> None:
        ctx = event.context
        if ctx.channel == "websocket":
            session = self.sessions.get_or_create(ctx.session_key)
            mark_webui_session(session, ctx.metadata)
            return
        # Turno partito da un altro canale utente: eco del messaggio utente
        # sulla vista WebUI, così la chat aperta lo mostra in tempo reale e
        # il transcript resta la storia completa della conversazione.
        target = webui_view_target(ctx)
        if target is None:
            return
        metadata = dict(ctx.metadata or {})
        # Le continuation interne mantengono il canale d'origine ma il loro
        # contenuto è un prompt sintetico, non un messaggio dell'utente.
        if metadata.get("_internal_continuation") or metadata.get("_skip_user_persist"):
            return
        text = (event.content or "").strip()
        # Un allegato senza didascalia e' un messaggio: la bolla e' l'immagine.
        # Col solo controllo sul testo, una foto muta non arrivava in chat.
        if (not text and not event.media) or text == "/stop":
            return
        channel, chat_id = target
        await self.bus.publish_outbound(
            OutboundMessage(
                channel=channel,
                chat_id=chat_id,
                content=event.content,
                media=list(event.media),
                metadata={
                    **metadata,
                    "_user_echo": True,
                    "origin_channel": ctx.channel,
                },
            )
        )

    async def _handle_run_status_changed(self, event: TurnRunStatusChanged) -> None:
        msg = self._view_msg(event.context)
        if msg is None:
            return
        await publish_turn_run_status(
            self.bus,
            msg,
            event.status,
            started_at=event.started_at,
        )

    async def _handle_turn_completed_event(self, event: TurnCompleted) -> None:
        msg = self._view_msg(event.context)
        if msg is None:
            return
        await self.handle_turn_end(msg, latency_ms=event.latency_ms)
        self._schedule_mood_from_event(event, msg)

    def _schedule_mood_from_event(self, event: TurnCompleted, msg: InboundMessage) -> None:
        """Il sidecar dell'umore della mascotte, in background, dopo il ``turn_end``.

        ``runtime`` e' la foto di provider/modello scattata da ``_state_build``: un
        turno-comando non la scatta (``None``) e non ha un umore. Tutto il resto —
        il flag di config, l'ultimo scambio, la richiesta, il frame — sta nel task
        in background, cosi' il gestore dell'evento resta a costo zero e un
        errore qualunque lascia la mascotte ``idle``, com'era prima.
        """
        runtime = event.runtime
        if not isinstance(runtime, LLMRuntime):
            return

        async def _classify_and_notify(turtime: LLMRuntime = runtime) -> None:
            try:
                config = self.config_loader()
            except Exception:
                logger.debug("mascot mood: config unreadable, skipping", exc_info=True)
                return
            defaults = config.agents.defaults
            if not defaults.mascot_mood:
                return
            session = self.sessions.get_or_create(event.context.session_key)
            inputs = mood_inputs(session)
            if inputs is None:
                return
            model = resolve_mood_model(config, turtime.model)
            mood, response = await classify_mood(
                turtime.provider, model, inputs, bot_name=defaults.bot_name,
                session_key=session.key,
            )
            if response is not None:
                # Import qui e non in testa: ``jenny.agent`` carica il loop intero
                # e ``jenny.session`` deve reggere da primo import
                # (``tests/session/test_cold_imports.py``).
                from jenny.agent.token_usage import record_response_token_usage

                record_response_token_usage(
                    response,
                    source=MOOD_TOKEN_USAGE_SOURCE,
                    timezone_name=defaults.timezone or None,
                )
            if mood == NEUTRAL_MOOD:
                return
            await self.bus.publish_outbound(OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="",
                metadata={
                    **dict(event.context.metadata or {}),
                    "_mascot_mood": True,
                    "mascot_mood": mood,
                },
            ))

        self.schedule_background(_classify_and_notify())

    async def _handle_runtime_model_changed(self, event: RuntimeModelChanged) -> None:
        await self.bus.publish_outbound(
            OutboundMessage(
                channel="websocket",
                chat_id="*",
                content="",
                metadata={
                    "_runtime_model_updated": True,
                    "model": event.model,
                    "model_preset": event.model_preset,
                    "provider": event.provider,
                },
            )
        )

    async def handle_turn_end(
        self,
        msg: InboundMessage,
        *,
        latency_ms: int | None,
    ) -> None:
        if msg.channel != "websocket":
            return

        turn_metadata: dict[str, Any] = {**msg.metadata, "_turn_end": True}
        if latency_ms is not None:
            turn_metadata["latency_ms"] = int(latency_ms)
        await self.bus.publish_outbound(OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content="",
            metadata=turn_metadata,
        ))
