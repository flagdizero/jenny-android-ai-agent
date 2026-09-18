"""L'indicatore "sta scrivendo…" su Telegram: chi lo accende e chi lo spegne.

Telegram è l'unico canale utente che non riceve né i progress (``send_progress``
è falso) né un ``turn_end`` (``WebuiTurnCoordinator.handle_turn_end`` esce per i
canali diversi da websocket). L'unico segnale che arriva fin qui è il runtime
event del turno, e il suo ``"idle"`` è emesso in un ``finally``: copre anche gli
errori e i turni abortiti. Questi test provano quel cablaggio e le tre proprietà
del battito — idempotente, silenzioso, mortale.

Le asserzioni sono sugli oggetti (``task.done()``, la lista delle azioni), non
su un ``sleep`` che "dovrebbe bastare": l'unico test che guarda il tempo è
quello del tetto duro, dove il tempo è la cosa sotto esame.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from jenny.bus.queue import MessageBus
from jenny.bus.runtime_events import (
    RuntimeEventBus,
    RuntimeEventContext,
    TurnRunStatusChanged,
)
from jenny.channels.telegram import TelegramChannel, _TypingHeartbeat
from jenny.config.schema import TelegramConfig


class FakeAPI:
    def __init__(self, *, fail_action: bool = False) -> None:
        self.actions: list[str] = []
        self.sent: list[str] = []
        self.fail_action = fail_action
        self.closed = False

    async def send_chat_action(self, chat_id: str, action: str = "typing"):
        if self.fail_action:
            raise RuntimeError("Telegram is having a day")
        self.actions.append(action)

    async def send_message(self, chat_id: str, text: str, *, parse_mode: str | None = None):
        self.sent.append(text)
        return {"message_id": len(self.sent)}

    async def get_updates(self, offset, timeout_s):  # pragma: no cover - non usato
        return []

    async def close(self) -> None:
        self.closed = True


async def _until(predicate, *, tries: int = 500) -> bool:
    """Cede il loop finché ``predicate`` è vera, senza attendere un tempo fisso."""
    for _ in range(tries):
        if predicate():
            return True
        await asyncio.sleep(0)
    return predicate()


async def _spin(tries: int = 50) -> None:
    """Dà al loop qualche giro per far emergere ciò che *non* deve succedere."""
    for _ in range(tries):
        await asyncio.sleep(0)


def _channel(
    api: FakeAPI, events: Any | None = None
) -> tuple[TelegramChannel, MessageBus]:
    config = TelegramConfig(enabled=True, bot_token="TOKEN", paired_chat_id="42")
    bus = MessageBus()
    return (
        TelegramChannel(config, bus, api=api, language="en", runtime_events=events),
        bus,
    )


def _ctx(channel: str = "telegram", chat_id: str = "42") -> RuntimeEventContext:
    return RuntimeEventContext(
        channel=channel, chat_id=chat_id, session_key="unified:default"
    )


# --- il cablaggio sui runtime event --------------------------------------------


async def test_running_turn_shows_typing() -> None:
    api = FakeAPI()
    events = RuntimeEventBus()
    ch, _bus = _channel(api, events)

    await events.publish(TurnRunStatusChanged(context=_ctx(), status="running"))
    assert await _until(lambda: api.actions == ["typing"])
    await ch.stop()


async def test_idle_turn_stops_the_beat() -> None:
    api = FakeAPI()
    events = RuntimeEventBus()
    ch, _bus = _channel(api, events)

    await events.publish(TurnRunStatusChanged(context=_ctx(), status="running"))
    assert await _until(lambda: ch._typing._task is not None)
    await events.publish(TurnRunStatusChanged(context=_ctx(), status="idle"))

    assert ch._typing._task is None
    await ch.stop()


@pytest.mark.parametrize("channel", ["websocket", "internal"])
async def test_other_channels_never_reach_telegram(channel: str) -> None:
    # I turni interni (cron, Dream, heartbeat) sono ``internal``: il filtro sul
    # canale li esclude da solo, senza una lista da tenere aggiornata.
    api = FakeAPI()
    events = RuntimeEventBus()
    ch, _bus = _channel(api, events)

    await events.publish(
        TurnRunStatusChanged(context=_ctx(channel=channel), status="running")
    )
    await _spin(20)

    assert api.actions == []
    assert ch._typing._task is None
    await ch.stop()


async def test_stop_unsubscribes_the_handler() -> None:
    # Il canale viene ricostruito a ogni reload delle impostazioni Telegram: un
    # handler lasciato appeso scriverebbe su una API già chiusa, a ogni turno.
    api = FakeAPI()
    events = RuntimeEventBus()
    ch, _bus = _channel(api, events)
    await ch.stop()

    await events.publish(TurnRunStatusChanged(context=_ctx(), status="running"))
    await _spin(20)

    assert api.actions == []
    assert api.closed is True


async def test_a_published_turn_starts_the_beat_before_the_event() -> None:
    # Fra il publish e il "running" passa la costruzione del contesto, che è
    # tempo vero: senza questo, l'utente vedrebbe il vuoto proprio all'inizio.
    api = FakeAPI()
    ch, bus = _channel(api)
    await ch._handle_update(
        {
            "update_id": 1,
            "message": {"chat": {"id": "42"}, "from": {"id": "42"}, "text": "ciao"},
        }
    )
    await asyncio.wait_for(bus.consume_inbound(), timeout=1)

    assert ch._typing._task is not None
    assert await _until(lambda: api.actions == ["typing"])
    await ch.stop()


# --- le tre proprietà del battito ----------------------------------------------


async def test_start_is_idempotent() -> None:
    api = FakeAPI()
    beat = _TypingHeartbeat(api, interval_s=60)

    await beat.start("42")
    first = beat._task
    await beat.start("42")

    # ``start`` passa da ``stop``, che *attende* la cancellazione: quando la
    # seconda start ritorna, il primo battito è già finito — non c'è una
    # finestra in cui due task mandano l'azione insieme.
    assert first is not None
    assert first.cancelled()
    assert beat._task is not None and beat._task is not first
    await beat.stop()


async def test_a_failing_action_never_escapes() -> None:
    # L'indicatore è cosmetico: se Telegram rifiuta, il turno prosegue e nessuno
    # se ne accorge. Il battito resta vivo, perché il prossimo giro può andare.
    api = FakeAPI(fail_action=True)
    beat = _TypingHeartbeat(api, interval_s=0.01)

    await beat.start("42")
    await _spin(50)

    assert beat._task is not None
    assert not beat._task.done()
    await beat.stop()


async def test_the_beat_dies_on_its_own_cap() -> None:
    # Il caso che nessun evento copre: un "idle" perso lascerebbe il bot a
    # scrivere per sempre. Il tetto è l'unica difesa, e qui il tempo è la cosa
    # sotto esame — quindi si guarda comunque il task, non l'orologio.
    api = FakeAPI()
    beat = _TypingHeartbeat(api, interval_s=0.001, max_s=0.02)

    await beat.start("42")
    task = beat._task
    assert task is not None
    await asyncio.wait_for(task, timeout=2)

    assert task.done() and not task.cancelled()
    assert api.actions  # ha battuto finché ha potuto


async def test_stop_awaits_the_cancellation() -> None:
    api = FakeAPI()
    beat = _TypingHeartbeat(api, interval_s=60)

    await beat.start("42")
    task = beat._task
    await beat.stop()

    assert task is not None and task.done()
    assert beat._task is None
