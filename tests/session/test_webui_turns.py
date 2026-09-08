"""Copertura per ``jenny.session.webui_turns``.

``tests/webui/test_webui_turn_helpers.py`` copre solo ``publish_turn_run_status``
(strip di timing). Qui si copre il resto del modulo: marcatura sessione WebUI e
``WebuiTurnCoordinator`` — il fan-out degli eventi runtime verso i messaggi
WebSocket della WebUI, compresa la proiezione dei turni di altri canali.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from jenny.bus.events import InboundMessage
from jenny.bus.queue import MessageBus
from jenny.bus.runtime_events import (
    RuntimeEventBus,
    RuntimeEventContext,
    RuntimeModelChanged,
    SessionTurnStarted,
    TurnCompleted,
    TurnRunStatusChanged,
)
from jenny.config.schema import Config
from jenny.providers.base import LLMResponse
from jenny.session import webui_turns as wt
from jenny.session.keys import HEARTBEAT_SESSION_KEY
from jenny.session.manager import Session, SessionManager
from jenny.session.turn_visibility import silent_turn_metadata
from jenny.utils.llm_runtime import LLMRuntime

# --- mark_webui_session --------------------------------------------------------


def test_mark_webui_session_sets_metadata_when_opted_in():
    session = Session(key="websocket:c1")
    assert wt.mark_webui_session(session, {"webui": True}) is True
    assert session.metadata["webui"] is True


def test_mark_webui_session_noop_when_not_opted_in():
    session = Session(key="websocket:c1")
    assert wt.mark_webui_session(session, {"webui": False}) is False
    assert wt.mark_webui_session(session, {}) is False
    assert "webui" not in session.metadata


# --- WebuiTurnCoordinator ----------------------------------------------------------


def _coordinator(tmp_path) -> tuple[wt.WebuiTurnCoordinator, MessageBus, list]:
    bus = MagicMock()
    bus.publish_outbound = AsyncMock()
    sessions = SessionManager(tmp_path)
    scheduled: list = []

    def schedule_background(coro):
        scheduled.append(coro)

    coordinator = wt.WebuiTurnCoordinator(
        bus=bus,
        sessions=sessions,
        schedule_background=schedule_background,
    )
    return coordinator, bus, scheduled


def test_subscribe_registers_and_unsubscribe_removes_all_handlers(tmp_path):
    coordinator, _bus, _scheduled = _coordinator(tmp_path)
    runtime_events = RuntimeEventBus()

    unsubscribe = coordinator.subscribe(runtime_events)
    assert len(runtime_events._handlers) == 4

    unsubscribe()
    assert runtime_events._handlers == []


async def test_handle_session_turn_started_marks_webui_session(tmp_path):
    coordinator, _bus, _scheduled = _coordinator(tmp_path)
    ctx = RuntimeEventContext(
        channel="websocket",
        chat_id="c1",
        session_key="websocket:c1",
        metadata={"webui": True},
    )
    await coordinator._handle_session_turn_started(SessionTurnStarted(context=ctx))
    session = coordinator.sessions.get_or_create("websocket:c1")
    assert session.metadata["webui"] is True


async def test_handle_session_turn_started_ignores_non_websocket(tmp_path):
    coordinator, _bus, _scheduled = _coordinator(tmp_path)
    ctx = RuntimeEventContext(
        channel="cli",
        chat_id="c1",
        session_key="cli:c1",
        metadata={"webui": True},
    )
    await coordinator._handle_session_turn_started(SessionTurnStarted(context=ctx))
    # Nessuna sessione creata per un canale non websocket.
    assert "cli:c1" not in coordinator.sessions._cache


async def test_handle_run_status_changed_publishes_for_websocket_only(tmp_path):
    coordinator, bus, _scheduled = _coordinator(tmp_path)
    ctx = RuntimeEventContext(channel="websocket", chat_id="c1", session_key="websocket:c1")
    await coordinator._handle_run_status_changed(
        TurnRunStatusChanged(context=ctx, status="running", started_at=10.0),
    )
    bus.publish_outbound.assert_awaited_once()

    bus.publish_outbound.reset_mock()
    ctx_other = RuntimeEventContext(channel="cli", chat_id="c1", session_key="cli:c1")
    await coordinator._handle_run_status_changed(
        TurnRunStatusChanged(context=ctx_other, status="running"),
    )
    bus.publish_outbound.assert_not_awaited()


async def test_handle_runtime_model_changed_broadcasts(tmp_path):
    coordinator, bus, _scheduled = _coordinator(tmp_path)
    await coordinator._handle_runtime_model_changed(
        RuntimeModelChanged(model="m2", model_preset="fast", provider="prov-x"),
    )
    bus.publish_outbound.assert_awaited_once()
    call = bus.publish_outbound.await_args[0][0]
    assert call.chat_id == "*"
    assert call.metadata["model"] == "m2"
    assert call.metadata["model_preset"] == "fast"
    assert call.metadata["provider"] == "prov-x"


async def test_handle_turn_end_publishes_turn_end(tmp_path):
    coordinator, bus, _scheduled = _coordinator(tmp_path)
    msg = InboundMessage(channel="websocket", sender_id="u", chat_id="c1", content="hi")
    await coordinator.handle_turn_end(msg, latency_ms=120)

    bus.publish_outbound.assert_awaited_once()
    call = bus.publish_outbound.await_args[0][0]
    assert call.metadata["_turn_end"] is True
    assert call.metadata["latency_ms"] == 120


async def test_handle_turn_end_noop_for_non_websocket_channel(tmp_path):
    coordinator, bus, _scheduled = _coordinator(tmp_path)
    msg = InboundMessage(channel="cli", sender_id="u", chat_id="c1", content="hi")
    await coordinator.handle_turn_end(msg, latency_ms=None)
    bus.publish_outbound.assert_not_awaited()


async def test_handle_turn_completed_event_publishes_turn_end_and_schedules_nothing(tmp_path):
    """La fine di un turno WebUI è un solo frame, e nessun lavoro in background.

    Fino al 05/09/2026 qui partiva la generazione del titolo di sessione: una
    richiesta al modello dopo ogni turno finché il titolo non c'era, per un campo
    che nessuna vista leggeva. ``TurnCompleted.runtime`` resta sull'evento (è la
    foto del provider/modello del turno) ma il coordinatore non lo consuma più.
    """
    coordinator, bus, scheduled = _coordinator(tmp_path)
    ctx = RuntimeEventContext(
        channel="websocket",
        chat_id="c1",
        session_key="websocket:c1",
        metadata={"webui": True},
    )
    event = TurnCompleted(context=ctx, latency_ms=75, runtime=MagicMock())

    await coordinator._handle_turn_completed_event(event)

    assert scheduled == []
    bus.publish_outbound.assert_awaited_once()
    out = bus.publish_outbound.await_args[0][0]
    assert out.metadata["_turn_end"] is True
    assert out.metadata["latency_ms"] == 75
    assert not out.metadata.get("_session_updated")


async def test_handle_turn_completed_event_ignores_non_websocket(tmp_path):
    coordinator, bus, scheduled = _coordinator(tmp_path)
    ctx = RuntimeEventContext(channel="cli", chat_id="c1", session_key="cli:c1")
    event = TurnCompleted(context=ctx, latency_ms=None, runtime=None)

    await coordinator._handle_turn_completed_event(event)
    bus.publish_outbound.assert_not_awaited()
    assert scheduled == []


# --- il sidecar dell'umore della mascotte -------------------------------------------

_REPLY = "Fatto: ho spostato la riunione alle 16 e avvisato tutti. Spero vada bene!"


def _mood_coordinator(tmp_path, *, config: Config | None = None, letter: str = "A"):
    coordinator, bus, scheduled = _coordinator(tmp_path)
    on = Config.model_validate({"agents": {"defaults": {"mascotMood": True}}})
    coordinator.config_loader = lambda: config if config is not None else on
    session = coordinator.sessions.get_or_create("websocket:c1")
    session.add_message("user", "sposta la riunione")
    session.add_message("assistant", _REPLY)
    provider = MagicMock()
    provider.chat_with_retry = AsyncMock(
        return_value=LLMResponse(content=letter, usage={"total_tokens": 5})
    )
    ctx = RuntimeEventContext(
        channel="websocket",
        chat_id="c1",
        session_key="websocket:c1",
        metadata={"webui": True, "webui_turn_id": "t-1"},
    )
    event = TurnCompleted(
        context=ctx, latency_ms=10, runtime=LLMRuntime(provider=provider, model="turn-model")
    )
    return coordinator, bus, scheduled, provider, event


async def _run_scheduled(scheduled: list) -> None:
    for coro in scheduled:
        await coro


async def test_turn_completed_schedules_the_mood_and_publishes_the_frame(tmp_path, monkeypatch):
    recorded: list = []
    monkeypatch.setattr(
        "jenny.agent.token_usage.record_response_token_usage",
        lambda response, **kw: recorded.append((response, kw)),
    )
    coordinator, bus, scheduled, provider, event = _mood_coordinator(tmp_path, letter="A")

    await coordinator._handle_turn_completed_event(event)

    # Il gestore pubblica solo il ``turn_end``: il resto e' nel background.
    assert bus.publish_outbound.await_count == 1
    assert len(scheduled) == 1
    await _run_scheduled(scheduled)

    provider.chat_with_retry.assert_awaited_once()
    assert provider.chat_with_retry.await_args.kwargs["model"] == "turn-model"
    frame = bus.publish_outbound.await_args_list[-1][0][0]
    assert frame.channel == "websocket" and frame.chat_id == "c1" and frame.content == ""
    assert frame.metadata["_mascot_mood"] is True
    assert frame.metadata["mascot_mood"] == "happy"
    assert frame.metadata["webui_turn_id"] == "t-1"
    # Contato, nel suo bucket: il titolo che questo sostituisce non lo era.
    assert len(recorded) == 1
    assert recorded[0][1]["source"] == "mascot"


async def test_neutral_mood_publishes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "jenny.agent.token_usage.record_response_token_usage", lambda *a, **kw: None
    )
    coordinator, bus, scheduled, provider, event = _mood_coordinator(tmp_path, letter="E")
    await coordinator._handle_turn_completed_event(event)
    await _run_scheduled(scheduled)
    provider.chat_with_retry.assert_awaited_once()
    assert bus.publish_outbound.await_count == 1  # solo il turn_end


async def test_provider_failure_leaves_the_mascot_idle(tmp_path):
    coordinator, bus, scheduled, provider, event = _mood_coordinator(tmp_path)
    provider.chat_with_retry = AsyncMock(side_effect=RuntimeError("boom"))
    await coordinator._handle_turn_completed_event(event)
    await _run_scheduled(scheduled)
    assert bus.publish_outbound.await_count == 1


async def test_default_config_asks_the_question(tmp_path, monkeypatch):
    """Con ``Config()`` nudo il sidecar parte: e' lo stato in cui si spedisce.

    Fino all'08/09/2026 era il contrario (standby, in attesa dell'arte).
    """
    monkeypatch.setattr(
        "jenny.agent.token_usage.record_response_token_usage", lambda *a, **kw: None
    )
    coordinator, bus, scheduled, provider, event = _mood_coordinator(tmp_path, config=Config())
    await coordinator._handle_turn_completed_event(event)
    await _run_scheduled(scheduled)
    provider.chat_with_retry.assert_awaited_once()
    assert bus.publish_outbound.await_args_list[-1][0][0].metadata["mascot_mood"] == "happy"


async def test_mood_disabled_in_config_costs_no_request(tmp_path):
    config = Config.model_validate({"agents": {"defaults": {"mascotMood": False}}})
    coordinator, bus, scheduled, provider, event = _mood_coordinator(tmp_path, config=config)
    await coordinator._handle_turn_completed_event(event)
    await _run_scheduled(scheduled)
    provider.chat_with_retry.assert_not_awaited()
    assert bus.publish_outbound.await_count == 1


async def test_mood_uses_the_configured_preset_model(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "jenny.agent.token_usage.record_response_token_usage", lambda *a, **kw: None
    )
    config = Config.model_validate({
        "agents": {"defaults": {"mascotMood": True, "mascotMoodModelPreset": "cheap"}},
        "modelPresets": {"cheap": {"model": "tiny-1"}},
    })
    coordinator, bus, scheduled, provider, event = _mood_coordinator(tmp_path, config=config)
    await coordinator._handle_turn_completed_event(event)
    await _run_scheduled(scheduled)
    assert provider.chat_with_retry.await_args.kwargs["model"] == "tiny-1"


async def test_command_turn_has_no_runtime_and_no_mood(tmp_path):
    coordinator, bus, scheduled, provider, event = _mood_coordinator(tmp_path)
    event = TurnCompleted(context=event.context, latency_ms=1, runtime=None)
    await coordinator._handle_turn_completed_event(event)
    assert scheduled == []
    assert bus.publish_outbound.await_count == 1


async def test_turn_ended_in_error_costs_no_request(tmp_path):
    coordinator, bus, scheduled, provider, event = _mood_coordinator(tmp_path)
    coordinator.sessions.get_or_create("websocket:c1").add_message("user", "riprova")
    await coordinator._handle_turn_completed_event(event)
    await _run_scheduled(scheduled)
    provider.chat_with_retry.assert_not_awaited()
    assert bus.publish_outbound.await_count == 1


async def test_unreadable_config_skips_quietly(tmp_path):
    coordinator, bus, scheduled, provider, event = _mood_coordinator(tmp_path)

    def _boom():
        raise OSError("no config")

    coordinator.config_loader = _boom
    await coordinator._handle_turn_completed_event(event)
    await _run_scheduled(scheduled)
    provider.chat_with_retry.assert_not_awaited()


async def test_telegram_turn_mood_lands_on_the_webui_view(tmp_path, monkeypatch):
    """Un turno da Telegram e' la stessa conversazione: la mascotte reagisce nella WebUI."""
    monkeypatch.setattr(
        "jenny.agent.token_usage.record_response_token_usage", lambda *a, **kw: None
    )
    coordinator, bus, scheduled = _coordinator(tmp_path)
    coordinator.config_loader = lambda: Config.model_validate(
        {"agents": {"defaults": {"mascotMood": True}}}
    )
    session = coordinator.sessions.get_or_create("unified:default")
    session.add_message("user", "sposta la riunione")
    session.add_message("assistant", _REPLY)
    provider = MagicMock()
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="C"))
    event = TurnCompleted(
        context=_telegram_ctx(), latency_ms=1, runtime=LLMRuntime(provider=provider, model="m")
    )
    await coordinator._handle_turn_completed_event(event)
    await _run_scheduled(scheduled)
    frame = bus.publish_outbound.await_args_list[-1][0][0]
    assert (frame.channel, frame.chat_id) == ("websocket", "default")
    assert frame.metadata["mascot_mood"] == "angry"


# --- proiezione dei turni esterni sulla vista WebUI --------------------------------


def _telegram_ctx(metadata: dict | None = None) -> RuntimeEventContext:
    return RuntimeEventContext(
        channel="telegram",
        chat_id="42",
        session_key="unified:default",
        metadata=metadata if metadata is not None else {"webui_turn_id": "t1"},
    )


def test_webui_view_target_routing():
    ws = RuntimeEventContext(channel="websocket", chat_id="c1", session_key="unified:default")
    assert wt.webui_view_target(ws) == ("websocket", "c1")
    assert wt.webui_view_target(_telegram_ctx()) == ("websocket", "default")
    internal = RuntimeEventContext(
        channel="internal", chat_id="x", session_key="unified:default"
    )
    assert wt.webui_view_target(internal) is None
    non_unified = RuntimeEventContext(
        channel="telegram", chat_id="42", session_key="cron:job1"
    )
    assert wt.webui_view_target(non_unified) is None


def test_a_silent_turn_has_no_webui_projection():
    """Il canale d'origine non basta a decidere.

    Un heartbeat o un cron monitor gira *su* ``websocket:default`` — è il target a
    cui consegnerà se una condizione scatta — ma spinner e ``_turn_end``
    appartengono alla conversazione dell'utente, non a un controllo che non ha
    chiesto. Senza questo gate ogni ciclo lasciava i propri marcatori in chat.
    """
    heartbeat = RuntimeEventContext(
        channel="websocket", chat_id="default", session_key=HEARTBEAT_SESSION_KEY
    )
    assert wt.webui_view_target(heartbeat) is None

    monitor = RuntimeEventContext(
        channel="websocket", chat_id="chat-1", session_key="cron:job-m"
    )
    assert wt.webui_view_target(monitor) is None

    marked = RuntimeEventContext(
        channel="websocket",
        chat_id="c1",
        session_key="unified:default",
        metadata=silent_turn_metadata(),
    )
    assert wt.webui_view_target(marked) is None


async def test_a_silent_turn_publishes_no_run_status(tmp_path):
    coordinator, bus, _scheduled = _coordinator(tmp_path)
    ctx = RuntimeEventContext(
        channel="websocket", chat_id="default", session_key=HEARTBEAT_SESSION_KEY
    )

    await coordinator._handle_run_status_changed(
        TurnRunStatusChanged(context=ctx, status="running", started_at=10.0),
    )
    await coordinator._handle_turn_completed_event(
        TurnCompleted(context=ctx, latency_ms=12, runtime=None),
    )

    bus.publish_outbound.assert_not_awaited()


async def test_external_turn_start_publishes_user_echo(tmp_path):
    coordinator, bus, _scheduled = _coordinator(tmp_path)
    event = SessionTurnStarted(context=_telegram_ctx(), content="ciao dal telefono")

    await coordinator._handle_session_turn_started(event)

    bus.publish_outbound.assert_awaited_once()
    call = bus.publish_outbound.await_args[0][0]
    assert call.channel == "websocket"
    assert call.chat_id == "default"
    assert call.content == "ciao dal telefono"
    assert call.metadata["_user_echo"] is True
    assert call.metadata["origin_channel"] == "telegram"
    assert call.metadata["webui_turn_id"] == "t1"


async def test_external_turn_start_skips_stop_and_empty(tmp_path):
    coordinator, bus, _scheduled = _coordinator(tmp_path)
    await coordinator._handle_session_turn_started(
        SessionTurnStarted(context=_telegram_ctx(), content="/stop")
    )
    await coordinator._handle_session_turn_started(
        SessionTurnStarted(context=_telegram_ctx(), content="   ")
    )
    bus.publish_outbound.assert_not_awaited()


async def test_external_turn_start_skips_internal_continuation(tmp_path):
    coordinator, bus, _scheduled = _coordinator(tmp_path)
    await coordinator._handle_session_turn_started(
        SessionTurnStarted(
            context=_telegram_ctx({"_internal_continuation": True}),
            content="continua il lavoro",
        )
    )
    bus.publish_outbound.assert_not_awaited()


async def test_external_turn_start_skips_internal_and_non_unified(tmp_path):
    coordinator, bus, _scheduled = _coordinator(tmp_path)
    internal = RuntimeEventContext(
        channel="internal", chat_id="x", session_key="unified:default"
    )
    await coordinator._handle_session_turn_started(
        SessionTurnStarted(context=internal, content="lavoro interno")
    )
    non_unified = RuntimeEventContext(
        channel="telegram", chat_id="42", session_key="cron:job1"
    )
    await coordinator._handle_session_turn_started(
        SessionTurnStarted(context=non_unified, content="x")
    )
    bus.publish_outbound.assert_not_awaited()


async def test_external_turn_completed_publishes_turn_end_on_view(tmp_path):
    coordinator, bus, _scheduled = _coordinator(tmp_path)
    event = TurnCompleted(context=_telegram_ctx(), latency_ms=99, runtime=None)

    await coordinator._handle_turn_completed_event(event)

    bus.publish_outbound.assert_awaited_once()
    call = bus.publish_outbound.await_args[0][0]
    assert call.channel == "websocket"
    assert call.chat_id == "default"
    assert call.metadata["_turn_end"] is True
    assert call.metadata["latency_ms"] == 99
    assert call.metadata["webui_turn_id"] == "t1"


async def test_external_run_status_projected_on_view(tmp_path):
    coordinator, bus, _scheduled = _coordinator(tmp_path)
    await coordinator._handle_run_status_changed(
        TurnRunStatusChanged(context=_telegram_ctx(), status="running", started_at=10.0),
    )
    bus.publish_outbound.assert_awaited_once()
    call = bus.publish_outbound.await_args[0][0]
    assert call.channel == "websocket"
    assert call.chat_id == "default"
    assert call.metadata["_goal_status"] is True
    assert call.metadata["goal_status"] == "running"


