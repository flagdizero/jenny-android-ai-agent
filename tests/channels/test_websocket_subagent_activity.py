"""Trasporto dell'attività fine di un subagent sul canale WebSocket.

Cosa viene bloccato qui, in ordine di importanza:

* **il costo.** Con nessun modal aperto non esiste né un frame né un tick, anche
  con tre subagent al lavoro; i delta arrivano *solo* alle connessioni che
  guardano quel task, e un burst diventa un frame per tick invece di uno per
  evento;
* **la contabilità dei watcher.** Ogni via d'uscita di una connessione (unwatch,
  disconnessione senza unwatch, drop per backpressure, shutdown, tetto per
  connessione) lascia il registro pulito: guardare non è un modo per far tenere
  memoria al gateway per sempre;
* **il transcript.** Regressione gemella di quella pinnata in
  ``test_websocket_subagent_status.py``: questi eventi sono ad alta frequenza e
  una sola riga persistita per evento allagherebbe il transcript.

Il log è quello vero (``SubagentActivityLog``, fase 1): un doppio nasconderebbe
proprio ciò che qui conta, cioè che ``seq``, sfratto dal ring e ``gap``
sopravvivano al filo.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from port_alloc import free_port
from support.aio import wait_until
from websockets.exceptions import ConnectionClosed
from websockets.frames import Close

from jenny.agent.subagent_activity import RING_CAPACITY, SubagentActivityLog
from jenny.bus.events import OUTBOUND_META_SUBAGENT_ACTIVITY, OutboundMessage
from jenny.channels import ws_sender
from jenny.channels.subagent_activity_wire import (
    MAX_FRAME_EVENTS,
    MAX_WATCHES_PER_CONNECTION,
)
from jenny.channels.websocket import WebSocketChannel, WebSocketConfig
from jenny.webui.gateway_services import build_gateway_services

_TASK = "d2ee4342"


class _FakeManager:
    """Solo la superficie che il canale usa: ``activity``."""

    def __init__(self, log: Any) -> None:
        self.activity = log


def _channel(*, manager: Any = None) -> WebSocketChannel:
    cfg: dict[str, Any] = {
        "enabled": True,
        "allowFrom": ["*"],
        "host": "127.0.0.1",
        "port": free_port(),
        "path": "/ws",
        "websocketRequiresToken": False,
    }
    parsed = WebSocketConfig.model_validate(cfg)
    bus = MagicMock()
    gateway = build_gateway_services(
        config=parsed,
        bus=bus,
        session_manager=None,
        workspace_path=Path.cwd(),
        default_restrict_to_workspace=False,
        runtime_model_name=None,
        get_subagent_manager=(lambda: manager) if manager is not None else None,
    )
    channel = WebSocketChannel(cfg, bus, gateway=gateway)
    # Il transcript reale scriverebbe su disco: qui interessa solo *se* viene
    # chiamato (la riga persistita per evento sarebbe il bug).
    channel._transcripts = MagicMock()
    return channel


def _conn(channel: WebSocketChannel, chat_id: str = "default") -> MagicMock:
    """Connessione iscritta, registrata come farebbe ``_connection_loop``."""
    connection = MagicMock()
    connection.send = AsyncMock()
    channel._subs.setdefault(chat_id, set()).add(connection)
    channel._conn_chats.setdefault(connection, set()).add(chat_id)
    return connection


def _frames(connection: MagicMock, event: str | None = None) -> list[dict[str, Any]]:
    out = []
    for call in connection.send.await_args_list:
        frame = json.loads(call.args[0])
        if event is None or frame.get("event") == event:
            out.append(frame)
    return out


def _log_with(*summaries: str, task_id: str = _TASK) -> SubagentActivityLog:
    log = SubagentActivityLog()
    for summary in summaries:
        log.append(task_id, "tool_start", summary=summary, name="grep")
    return log


async def _watch(channel: WebSocketChannel, connection: Any, **extra: Any) -> None:
    envelope = {"type": "subagent_watch", "task_id": _TASK}
    envelope.update(extra)
    await channel._dispatch_envelope(connection, "client-1", envelope)


# -- la risposta immediata al watch ------------------------------------------


class TestWatchReply:
    async def test_watch_answers_with_the_current_window_at_once(self) -> None:
        log = _log_with("reading a.py", "grepping for foo")
        channel = _channel(manager=_FakeManager(log))
        connection = _conn(channel)

        await _watch(channel, connection)

        frames = _frames(connection, "subagent_activity")
        assert len(frames) == 1
        frame = frames[0]
        assert frame["task_id"] == _TASK
        assert frame["chat_id"] == "default"
        assert frame["initial"] is True
        assert [e["summary"] for e in frame["events"]] == [
            "reading a.py", "grepping for foo",
        ]
        assert frame["since_seq"] == 0
        assert frame["first_seq"] == 1
        assert frame["last_seq"] == 2
        assert frame["latest_seq"] == 2
        assert frame["gap"] is False
        # Non è un messaggio: niente da renderizzare come bolla.
        assert "text" not in frame and "kind" not in frame

    async def test_watch_honours_a_client_cursor(self) -> None:
        log = _log_with("one", "two", "three")
        channel = _channel(manager=_FakeManager(log))
        connection = _conn(channel)

        await _watch(channel, connection, since=2)

        frame = _frames(connection, "subagent_activity")[0]
        assert [e["seq"] for e in frame["events"]] == [3]
        assert frame["since_seq"] == 2
        assert frame["gap"] is False

    async def test_watching_an_unknown_task_is_an_empty_window_not_an_error(self) -> None:
        channel = _channel(manager=_FakeManager(SubagentActivityLog()))
        connection = _conn(channel)

        await _watch(channel, connection)

        frame = _frames(connection, "subagent_activity")[0]
        assert frame["events"] == []
        # "non è ancora successo niente" ≠ "ti sei perso l'inizio".
        assert frame["latest_seq"] == 0
        assert frame["gap"] is False
        assert _frames(connection, "error") == []
        # Il watch resta: se quel task inizia a produrre, i delta arrivano.
        assert channel._subagent_watches.is_watching(connection, _TASK) is True

    async def test_watching_a_finished_and_reaped_task_behaves_the_same(self) -> None:
        """Il ring viene buttato dopo il digest: la finestra è vuota, non rotta."""
        log = _log_with("did the thing")
        log.drop(_TASK)
        channel = _channel(manager=_FakeManager(log))
        connection = _conn(channel)

        await _watch(channel, connection)

        frame = _frames(connection, "subagent_activity")[0]
        assert frame["events"] == [] and frame["latest_seq"] == 0

    async def test_watch_without_an_agent_yet_still_answers(self) -> None:
        """Onboarding: nessun manager, ma il modal non deve restare appeso."""
        channel = _channel()
        connection = _conn(channel)

        await _watch(channel, connection)

        frame = _frames(connection, "subagent_activity")[0]
        assert frame["events"] == [] and frame["initial"] is True

    async def test_a_ring_that_evicted_reports_the_gap(self) -> None:
        log = SubagentActivityLog(capacity=4)
        for index in range(9):
            log.append(_TASK, "iteration", summary=f"iteration {index}")
        channel = _channel(manager=_FakeManager(log))
        connection = _conn(channel)

        await _watch(channel, connection)

        frame = _frames(connection, "subagent_activity")[0]
        assert frame["gap"] is True
        assert frame["dropped"] == 5
        assert frame["latest_seq"] == 9

    async def test_an_invalid_task_id_is_refused_without_registering_anything(self) -> None:
        channel = _channel(manager=_FakeManager(SubagentActivityLog()))
        connection = _conn(channel)

        await channel._dispatch_envelope(
            connection, "client-1", {"type": "subagent_watch", "task_id": "../etc/passwd"}
        )

        assert _frames(connection, "error")[0]["detail"] == "invalid task_id"
        assert channel._subagent_watches.watch_count() == 0
        assert channel._activity_pump_task is None

    async def test_the_watch_reply_never_touches_the_transcript(self) -> None:
        channel = _channel(manager=_FakeManager(_log_with("one")))
        await _watch(channel, _conn(channel))

        channel._transcripts.prepare_and_append.assert_not_called()

    async def test_a_connection_that_dies_during_the_reply_leaves_no_watch(self) -> None:
        channel = _channel(manager=_FakeManager(_log_with("one")))
        connection = _conn(channel)
        connection.send.side_effect = ConnectionClosed(Close(1006, ""), Close(1006, ""), True)

        await _watch(channel, connection)

        assert channel._subagent_watches.watch_count() == 0
        assert connection not in channel._conn_chats


# -- delta: solo a chi guarda ------------------------------------------------


class TestDeltaRouting:
    async def test_deltas_reach_only_the_watching_connections(self) -> None:
        log = _log_with("one")
        channel = _channel(manager=_FakeManager(log))
        watcher = _conn(channel)
        bystander = _conn(channel)
        await _watch(channel, watcher)
        watcher.send.reset_mock()

        log.append(_TASK, "tool_end", summary="3 matches", name="grep", status="ok")
        await channel._pump_subagent_activity_once()

        assert [e["summary"] for e in _frames(watcher)[0]["events"]] == ["3 matches"]
        # L'altro client è iscritto alla stessa chat e non riceve nulla.
        bystander.send.assert_not_awaited()

    async def test_a_delta_carries_the_cursor_of_the_receiver(self) -> None:
        log = _log_with("one")
        channel = _channel(manager=_FakeManager(log))
        connection = _conn(channel)
        await _watch(channel, connection)
        connection.send.reset_mock()

        log.append(_TASK, "iteration", summary="iteration 2")
        await channel._pump_subagent_activity_once()

        frame = _frames(connection)[0]
        assert frame["since_seq"] == 1
        assert frame["first_seq"] == 2 and frame["last_seq"] == 2
        assert frame["gap"] is False
        assert "initial" not in frame

    async def test_nothing_new_means_no_frame(self) -> None:
        channel = _channel(manager=_FakeManager(_log_with("one")))
        connection = _conn(channel)
        await _watch(channel, connection)
        connection.send.reset_mock()

        await channel._pump_subagent_activity_once()
        await channel._pump_subagent_activity_once()

        connection.send.assert_not_awaited()

    async def test_two_watchers_with_different_cursors_cost_one_ring_read(self) -> None:
        log = _log_with("one", "two")
        reads: list[int] = []
        real_tail = log.tail_window

        def counting_tail(task_id: str, **kwargs: Any) -> Any:
            reads.append(kwargs.get("since_seq", 0))
            return real_tail(task_id, **kwargs)

        channel = _channel(manager=_FakeManager(log))
        early = _conn(channel)
        await _watch(channel, early)          # cursore a 2
        late = _conn(channel)
        await _watch(channel, late, since=1)  # cursore a 2 pure, ma partito da 1
        early.send.reset_mock()
        late.send.reset_mock()
        channel._subagent_watches.watch(late, _TASK, cursor=1)  # riallinea a 1

        log.append(_TASK, "iteration", summary="three")
        log.tail_window = counting_tail  # type: ignore[method-assign]
        await channel._pump_subagent_activity_once()

        assert reads == [1], "una sola lettura, col cursore minimo fra i watcher"
        assert [e["seq"] for e in _frames(early)[0]["events"]] == [3]
        assert [e["seq"] for e in _frames(late)[0]["events"]] == [2, 3]

    async def test_deltas_never_touch_the_transcript(self) -> None:
        log = _log_with("one")
        channel = _channel(manager=_FakeManager(log))
        await _watch(channel, _conn(channel))
        log.append(_TASK, "tool_end", summary="done", name="grep", status="ok")

        await channel._pump_subagent_activity_once()

        channel._transcripts.prepare_and_append.assert_not_called()

    async def test_only_watched_tasks_are_read(self) -> None:
        """Tre subagent al lavoro, uno guardato: gli altri due non costano nulla."""
        log = _log_with("one")
        log.append("other1", "iteration", summary="busy")
        log.append("other2", "iteration", summary="busy")
        channel = _channel(manager=_FakeManager(log))
        connection = _conn(channel)
        await _watch(channel, connection)
        connection.send.reset_mock()

        read_tasks: list[str] = []
        real_tail = log.tail_window

        def counting_tail(task_id: str, **kwargs: Any) -> Any:
            read_tasks.append(task_id)
            return real_tail(task_id, **kwargs)

        log.tail_window = counting_tail  # type: ignore[method-assign]
        log.append(_TASK, "iteration", summary="two")
        log.append("other1", "iteration", summary="still busy")
        await channel._pump_subagent_activity_once()

        assert read_tasks == [_TASK]
        assert len(_frames(connection)) == 1


# -- coalescenza e tetti -----------------------------------------------------


class TestCoalescingAndCaps:
    async def test_a_burst_becomes_one_frame_not_one_per_event(self) -> None:
        log = SubagentActivityLog()
        log.append(_TASK, "phase", summary="starting")
        channel = _channel(manager=_FakeManager(log))
        connection = _conn(channel)
        await _watch(channel, connection)
        connection.send.reset_mock()

        for index in range(12):
            log.append(_TASK, "iteration", summary=f"iteration {index}")
        await channel._pump_subagent_activity_once()

        frames = _frames(connection)
        assert len(frames) == 1, "un frame per tick, non uno per evento"
        assert len(frames[0]["events"]) == 12
        assert connection.send.await_count == 1

    async def test_a_burst_over_the_frame_cap_is_truncated_and_admits_it(self) -> None:
        log = SubagentActivityLog()
        channel = _channel(manager=_FakeManager(log))
        connection = _conn(channel)
        await _watch(channel, connection)
        connection.send.reset_mock()

        for index in range(MAX_FRAME_EVENTS + 30):
            log.append(_TASK, "iteration", summary=f"iteration {index}")
        await channel._pump_subagent_activity_once()

        frame = _frames(connection)[0]
        assert len(frame["events"]) == MAX_FRAME_EVENTS
        # I più recenti, e il buco dichiarato: mai uno stream bucato in silenzio.
        assert frame["last_seq"] == MAX_FRAME_EVENTS + 30
        assert frame["gap"] is True
        assert frame["latest_seq"] == MAX_FRAME_EVENTS + 30

    async def test_the_cursor_walks_the_whole_burst_across_ticks(self) -> None:
        log = SubagentActivityLog()
        channel = _channel(manager=_FakeManager(log))
        connection = _conn(channel)
        await _watch(channel, connection)
        connection.send.reset_mock()

        for index in range(MAX_FRAME_EVENTS + 5):
            log.append(_TASK, "iteration", summary=f"iteration {index}")
        await channel._pump_subagent_activity_once()
        for index in range(3):
            log.append(_TASK, "iteration", summary=f"tail {index}")
        await channel._pump_subagent_activity_once()

        second = _frames(connection)[1]
        assert [e["summary"] for e in second["events"]] == ["tail 0", "tail 1", "tail 2"]
        assert second["gap"] is False, "dopo il recupero il flusso torna integro"

    async def test_the_per_connection_watch_cap_evicts_and_says_so(self) -> None:
        log = SubagentActivityLog()
        for index in range(MAX_WATCHES_PER_CONNECTION + 1):
            log.append(f"task{index}", "phase", summary="working")
        channel = _channel(manager=_FakeManager(log))
        connection = _conn(channel)

        for index in range(MAX_WATCHES_PER_CONNECTION + 1):
            await channel._dispatch_envelope(
                connection, "client-1",
                {"type": "subagent_watch", "task_id": f"task{index}"},
            )

        assert channel._subagent_watches.watch_count() == MAX_WATCHES_PER_CONNECTION
        acks = _frames(connection, "subagent_unwatched")
        assert acks == [{
            "event": "subagent_unwatched", "task_id": "task0", "reason": "watch_limit",
        }]

    async def test_the_http_ring_is_the_larger_bound(self) -> None:
        """Il tetto del frame è più stretto del ring: la risync HTTP recupera."""
        assert MAX_FRAME_EVENTS < RING_CAPACITY


# -- ciclo di vita del watcher ------------------------------------------------


class TestWatcherLifecycle:
    async def test_unwatch_stops_the_deltas_and_is_acked(self) -> None:
        log = _log_with("one")
        channel = _channel(manager=_FakeManager(log))
        connection = _conn(channel)
        await _watch(channel, connection)

        await channel._dispatch_envelope(
            connection, "client-1", {"type": "subagent_unwatch", "task_id": _TASK}
        )
        connection.send.reset_mock()
        log.append(_TASK, "iteration", summary="after unwatch")
        await channel._pump_subagent_activity_once()

        connection.send.assert_not_awaited()
        assert channel._subagent_watches.active is False

    async def test_unwatch_is_idempotent(self) -> None:
        channel = _channel(manager=_FakeManager(SubagentActivityLog()))
        connection = _conn(channel)

        for _ in range(2):
            await channel._dispatch_envelope(
                connection, "client-1", {"type": "subagent_unwatch", "task_id": _TASK}
            )

        acks = _frames(connection, "subagent_unwatched")
        assert len(acks) == 2 and all(a["reason"] == "client" for a in acks)

    async def test_a_dropped_connection_cleans_up_its_watches(self) -> None:
        """Il caso normale su un telefono: la connessione muore senza unwatch."""
        log = _log_with("one")
        channel = _channel(manager=_FakeManager(log))
        gone = _conn(channel)
        alive = _conn(channel)
        await _watch(channel, gone)
        await _watch(channel, alive)

        channel._cleanup_connection(gone)

        assert channel._subagent_watches.is_watching(gone, _TASK) is False
        assert channel._subagent_watches.is_watching(alive, _TASK) is True
        gone.send.reset_mock()
        alive.send.reset_mock()
        log.append(_TASK, "iteration", summary="two")
        await channel._pump_subagent_activity_once()
        gone.send.assert_not_awaited()
        alive.send.assert_awaited_once()

    async def test_a_send_failure_mid_stream_drops_the_watch(self) -> None:
        log = _log_with("one")
        channel = _channel(manager=_FakeManager(log))
        connection = _conn(channel)
        await _watch(channel, connection)
        connection.send.side_effect = ConnectionClosed(Close(1006, ""), Close(1006, ""), True)

        log.append(_TASK, "iteration", summary="two")
        await channel._pump_subagent_activity_once()

        assert channel._subagent_watches.watch_count() == 0
        assert channel._subagent_watches.active is False

    async def test_a_stalled_client_is_dropped_not_buffered(self, monkeypatch) -> None:
        """App in background: il send si blocca, e il watch se ne va col client."""
        monkeypatch.setattr(ws_sender, "_SEND_TIMEOUT_S", 0.01)
        log = _log_with("one")
        channel = _channel(manager=_FakeManager(log))
        connection = _conn(channel)
        await _watch(channel, connection)

        async def never_returns(_raw: str) -> None:
            await asyncio.sleep(10)

        connection.send = AsyncMock(side_effect=never_returns)
        connection.close = AsyncMock()
        log.append(_TASK, "iteration", summary="two")
        await channel._pump_subagent_activity_once()

        assert channel._subagent_watches.watch_count() == 0
        assert connection not in channel._conn_chats

    async def test_two_tabs_on_the_same_subagent_are_independent(self) -> None:
        log = _log_with("one")
        channel = _channel(manager=_FakeManager(log))
        first = _conn(channel)
        second = _conn(channel)
        await _watch(channel, first)
        await _watch(channel, second)

        channel._cleanup_connection(first)

        assert channel._subagent_watches.cursors(_TASK) == [(second, 1)]

    async def test_rewatching_does_not_duplicate_the_watch(self) -> None:
        log = _log_with("one")
        channel = _channel(manager=_FakeManager(log))
        connection = _conn(channel)

        await _watch(channel, connection)
        await _watch(channel, connection)

        assert channel._subagent_watches.watch_count() == 1
        # Due risposte iniziali (una per richiesta), nessun delta duplicato dopo.
        assert len(_frames(connection, "subagent_activity")) == 2


# -- il pump: esiste solo mentre serve ---------------------------------------


class TestPumpLifecycle:
    async def test_no_watch_means_no_pump_task(self) -> None:
        log = _log_with("one")
        channel = _channel(manager=_FakeManager(log))
        _conn(channel)

        log.append(_TASK, "iteration", summary="two")
        await channel._pump_subagent_activity_once()

        assert channel._activity_pump_task is None

    async def test_the_pump_starts_on_the_first_watch_and_delivers(self, monkeypatch) -> None:
        monkeypatch.setattr(ws_sender, "ACTIVITY_PUMP_INTERVAL_S", 0.01)
        log = _log_with("one")
        channel = _channel(manager=_FakeManager(log))
        connection = _conn(channel)

        await _watch(channel, connection)
        assert channel._activity_pump_task is not None
        connection.send.reset_mock()
        log.append(_TASK, "iteration", summary="pumped")
        await wait_until(lambda: connection.send.await_count)

        assert [e["summary"] for e in _frames(connection)[0]["events"]] == ["pumped"]
        channel.stop_subagent_activity_pump()

    async def test_the_pump_stops_itself_when_the_last_watch_goes(self, monkeypatch) -> None:
        monkeypatch.setattr(ws_sender, "ACTIVITY_PUMP_INTERVAL_S", 0.01)
        channel = _channel(manager=_FakeManager(_log_with("one")))
        connection = _conn(channel)
        await _watch(channel, connection)
        task = channel._activity_pump_task
        assert task is not None

        channel._cleanup_connection(connection)
        await wait_until(task.done)
        assert channel._activity_pump_task is None

    async def test_the_pump_is_not_started_twice(self, monkeypatch) -> None:
        monkeypatch.setattr(ws_sender, "ACTIVITY_PUMP_INTERVAL_S", 0.05)
        channel = _channel(manager=_FakeManager(_log_with("one")))
        first = _conn(channel)
        second = _conn(channel)

        await _watch(channel, first)
        task = channel._activity_pump_task
        await _watch(channel, second)

        assert channel._activity_pump_task is task
        channel.stop_subagent_activity_pump()

    async def test_stop_cancels_the_pump_and_clears_the_registry(self, monkeypatch) -> None:
        monkeypatch.setattr(ws_sender, "ACTIVITY_PUMP_INTERVAL_S", 0.05)
        channel = _channel(manager=_FakeManager(_log_with("one")))
        await _watch(channel, _conn(channel))
        task = channel._activity_pump_task
        assert task is not None

        channel.stop_subagent_activity_pump()
        await asyncio.sleep(0)

        assert task.cancelled() or task.done()
        assert channel._subagent_watches.active is False
        assert channel._activity_pump_task is None

    async def test_a_broken_log_does_not_kill_the_pump(self) -> None:
        class _Broken:
            def tail_window(self, *_args: Any, **_kwargs: Any) -> Any:
                raise RuntimeError("ring exploded")

        channel = _channel(manager=_FakeManager(_Broken()))
        connection = _conn(channel)
        await _watch(channel, connection)

        await channel._pump_subagent_activity_once()

        # La finestra iniziale degrada a vuota e il watch resta vivo.
        assert _frames(connection, "subagent_activity")[0]["events"] == []
        assert channel._subagent_watches.is_watching(connection, _TASK) is True


# -- il percorso bus ---------------------------------------------------------


class TestBusCarriedActivity:
    def _msg(self, payload: Any) -> OutboundMessage:
        return OutboundMessage(
            channel="websocket",
            chat_id="default",
            content="",
            metadata={OUTBOUND_META_SUBAGENT_ACTIVITY: payload},
        )

    async def test_a_bus_carried_window_goes_only_to_watchers(self) -> None:
        log = _log_with("one")
        channel = _channel(manager=_FakeManager(log))
        watcher = _conn(channel)
        bystander = _conn(channel)
        await _watch(channel, watcher)
        watcher.send.reset_mock()

        log.append(_TASK, "iteration", summary="two")
        window = log.tail_window(_TASK, since_seq=0).to_dict()
        pending = await channel.send(self._msg({"task_id": _TASK, **window}))

        assert pending == []
        # Consegnato dal cursore del watcher, non da quello del produttore.
        assert [e["seq"] for e in _frames(watcher)[0]["events"]] == [2]
        bystander.send.assert_not_awaited()

    async def test_it_is_never_a_bubble_and_never_persisted(self) -> None:
        log = _log_with("one")
        channel = _channel(manager=_FakeManager(log))
        watcher = _conn(channel)
        await _watch(channel, watcher)
        watcher.send.reset_mock()

        log.append(_TASK, "iteration", summary="two")
        window = log.tail_window(_TASK, since_seq=0).to_dict()
        await channel.send(self._msg({"task_id": _TASK, **window}))

        channel._transcripts.prepare_and_append.assert_not_called()
        frame = _frames(watcher)[0]
        assert frame["event"] == "subagent_activity"
        assert "text" not in frame

    async def test_no_watchers_is_a_silent_no_op(self) -> None:
        log = _log_with("one")
        channel = _channel(manager=_FakeManager(log))
        connection = _conn(channel)

        window = log.tail_window(_TASK, since_seq=0).to_dict()
        pending = await channel.send(self._msg({"task_id": _TASK, **window}))

        assert pending == []
        connection.send.assert_not_awaited()
        channel._transcripts.prepare_and_append.assert_not_called()

    async def test_a_payload_without_a_task_id_is_dropped(self) -> None:
        channel = _channel(manager=_FakeManager(_log_with("one")))
        connection = _conn(channel)
        await _watch(channel, connection)
        connection.send.reset_mock()

        pending = await channel.send(self._msg({"events": []}))

        assert pending == []
        connection.send.assert_not_awaited()
        channel._transcripts.prepare_and_append.assert_not_called()

    async def test_the_flag_is_a_coordination_flag(self) -> None:
        """Telegram e il dispatcher devono ignorarlo: non è un messaggio finale."""
        from jenny.bus.events import COORDINATION_FLAGS

        assert OUTBOUND_META_SUBAGENT_ACTIVITY in COORDINATION_FLAGS
