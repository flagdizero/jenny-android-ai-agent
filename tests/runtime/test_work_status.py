"""La fonte della bolla di lavoro: ``jenny/runtime/work_status.py``.

Cosa si fissa: la forma del payload (campi, niente testo), il freno sugli
aggiornamenti con l'ultimo che parte comunque in ritardo, le transizioni che
partono subito, il riprova letto dalla riga del provider, lo snapshot per chi si
ricollega e il suo scadere.
"""

from __future__ import annotations

import asyncio

import pytest

from jenny.bus.events import OUTBOUND_META_WORK_STATUS
from jenny.providers import retry_notice
from jenny.runtime import work_status
from jenny.runtime.work_status import WorkReporter, work_snapshot


class _Bus:
    def __init__(self) -> None:
        self.sent: list = []

    def try_publish_outbound(self, msg) -> bool:
        self.sent.append(msg)
        return True

    def payloads(self) -> list[dict]:
        return [m.metadata[OUTBOUND_META_WORK_STATUS] for m in self.sent]


class _Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture(autouse=True)
def _clean_registry():
    work_status.reset_registry()
    yield
    work_status.reset_registry()


def _reporter(bus: _Bus, *, monotonic: _Clock | None = None, **kwargs) -> WorkReporter:
    kwargs.setdefault("kind", "gardener")
    kwargs.setdefault("chat_id", "project:orto")
    kwargs.setdefault("phases", ("pages", "board"))
    return WorkReporter(bus, monotonic=monotonic or _Clock(), **kwargs)


def test_start_publishes_a_dedicated_frame_with_fields_and_no_text() -> None:
    bus = _Bus()
    reporter = _reporter(bus, project="orto")
    reporter.start()

    (msg,) = bus.sent
    assert msg.channel == "websocket"
    assert msg.chat_id == "project:orto"
    assert msg.content == ""
    payload = msg.metadata[OUTBOUND_META_WORK_STATUS]
    assert payload["id"] == reporter.id
    assert payload["kind"] == "gardener"
    assert payload["state"] == "running"
    assert payload["phases"] == ["pages", "board"]
    assert payload["phase"] is None
    assert payload["writes"] == 0
    assert payload["retry"] is None
    assert payload["project"] == "orto"
    assert payload["manual"] is True
    assert "text" not in payload


def test_transitions_go_out_at_once_and_writes_are_throttled() -> None:
    bus = _Bus()
    mono = _Clock()
    reporter = _reporter(bus, monotonic=mono)
    reporter.start()
    reporter.enter("pages")  # una transizione: subito, anche a zero secondi
    reporter.wrote()
    reporter.wrote()
    assert [p["phase"] for p in bus.payloads()] == [None, "pages"]

    mono.t += work_status.PUBLISH_EVERY_S + 0.1
    reporter.wrote()
    assert bus.payloads()[-1]["writes"] == 3


async def test_the_last_throttled_update_still_goes_out(monkeypatch) -> None:
    """Una scrittura seguita da minuti di silenzio non resta fuori dalla bolla."""
    monkeypatch.setattr(work_status, "PUBLISH_EVERY_S", 0.05)
    bus = _Bus()
    reporter = WorkReporter(bus, kind="gardener", chat_id="project:orto", phases=("pages",))
    reporter.enter("pages")
    reporter.wrote()
    assert bus.payloads()[-1]["writes"] == 0

    await asyncio.sleep(0.12)
    assert bus.payloads()[-1]["writes"] == 1


def test_a_provider_retry_line_becomes_the_retry_field_and_progress_clears_it() -> None:
    bus = _Bus()
    reporter = _reporter(bus)
    reporter.start()
    reporter.retry_notice(retry_notice.waiting(2, 2, persistent=False))
    assert bus.payloads()[-1]["retry"] == {"attempt": 2, "of": 3}

    reporter.progress()
    assert bus.payloads()[-1]["retry"] is None


def test_lines_that_are_not_a_retry_wait_change_nothing() -> None:
    bus = _Bus()
    reporter = _reporter(bus)
    reporter.start()
    reporter.retry_notice("something else entirely")
    reporter.retry_notice(retry_notice.gave_up(4))
    assert len(bus.sent) == 1


def test_the_write_observer_counts_and_never_refuses() -> None:
    bus = _Bus()
    reporter = _reporter(bus)
    assert reporter.write_observer("wiki/a.md", "x") is None
    assert reporter.write_observer("wiki/b.md", "y") is None
    assert reporter.writes == 2


def test_finish_keeps_only_the_fields_that_say_something() -> None:
    bus = _Bus()
    reporter = _reporter(bus)
    reporter.finish("done", status="written", writes=7, board="done", absence=None)

    payload = bus.payloads()[-1]
    assert payload["state"] == "done"
    assert payload["outcome"] == {"status": "written", "writes": 7, "board": "done"}
    assert "ended_at" in payload
    with pytest.raises(ValueError):
        reporter.finish("running")


def test_dream_learns_its_phases_once_started() -> None:
    bus = _Bus()
    reporter = _reporter(bus, kind="dream", chat_id="default", phases=())
    reporter.start()
    reporter.set_phases(("review", "memory"))
    reporter.enter("review")
    assert bus.payloads()[-1]["phases"] == ["review", "memory"]
    assert bus.payloads()[-1]["phase"] == "review"


def test_the_snapshot_is_per_chat_and_finished_work_lingers_then_goes() -> None:
    bus = _Bus()
    clock = _Clock(5000.0)
    here = WorkReporter(
        bus, kind="gardener", chat_id="project:orto", phases=("pages",), clock=clock
    )
    WorkReporter(bus, kind="dream", chat_id="default", phases=(), clock=clock)

    assert [w["id"] for w in work_snapshot("project:orto", now=clock.t)] == [here.id]

    here.finish("done", status="written")
    clock.t += work_status.LINGER_S - 1
    assert work_snapshot("project:orto", now=clock.t)
    clock.t += 2
    assert work_snapshot("project:orto", now=clock.t) == []


def test_forget_leaves_no_trace() -> None:
    bus = _Bus()
    reporter = _reporter(bus, kind="dream", chat_id="default", phases=())
    reporter.forget()
    assert work_snapshot("default") == []
    assert bus.sent == []


def test_a_bus_that_raises_does_not_reach_the_work() -> None:
    class _Broken:
        def try_publish_outbound(self, msg):
            raise RuntimeError("queue on fire")

    reporter = WorkReporter(_Broken(), kind="gardener", chat_id="project:orto", phases=())
    reporter.start()
    reporter.finish("done")
