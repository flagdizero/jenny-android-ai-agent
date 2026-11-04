"""Dalle due superfici native all'agente e ritorno: la tendina e la mascotte.

Tre punti toccano il giro — chi crea l'inbound (``native_input``), chi lo
trasporta (``AgentLoop._assemble_outbound``) e chi consegna la risposta
(``NotificationChannel``, ``FloatingChannel``) — e ciascuno ha già i suoi test.
Quello che nessuno dei tre prova è la **catena**: il trasporto non è codice
scritto per questo, è una riga che copia i metadata dell'inbound sull'outbound
(``meta = dict(msg.metadata or {})``).

- **La tendina** ha un tag da trasportare: finché la riga regge, la risposta
  torna nella scheda da cui è partita la domanda; il giorno che qualcuno la
  cambia, torna la seconda scheda nella tendina e nessun test se ne accorgerebbe.
- **La mascotte** ha una finestra sola, senza fili da tenere distinti: la catena
  prova quel che l'utente ha chiesto in una riga, **la chat è identica a come
  sarebbe dentro l'app** — la domanda scritta nel fumetto finisce nella
  conversazione personale, la stessa di WebUI e Telegram, e la risposta torna
  alla mascotte invece che da qualche altra parte.

Prima erano due file gemelli, con lo stesso ``AgentLoop`` costruito a mano.
"""

from __future__ import annotations

from typing import Any

import pytest
from support.agent import make_loop

from jenny.bus.events import FLOATING_CHANNEL, NOTIFICATION_CHANNEL, InboundMessage
from jenny.channels import floating as fc
from jenny.channels import notification as nc
from jenny.channels.floating import FloatingChannel
from jenny.channels.notification import REPLY_THREAD_TAG, NotificationChannel
from jenny.runtime.native_input import (
    NATIVE_CHAT_ID,
    NATIVE_SOURCE_KEY,
    NATIVE_THREAD_KEY,
    SOURCE_FLOATING,
    SOURCE_NOTIFICATION,
)
from jenny.session.keys import UNIFIED_SESSION_KEY

# (canale, sorgente, tag): la tendina col tag di un promemoria, la mascotte senza.
SURFACES = [
    pytest.param(NOTIFICATION_CHANNEL, SOURCE_NOTIFICATION, "cron:spesa", id="notification"),
    pytest.param(FLOATING_CHANNEL, SOURCE_FLOATING, None, id="floating"),
]


class _Alerts:
    def __init__(self) -> None:
        self.threads: list[str | None] = []

    async def __call__(self, content, metadata, *, thread=None):
        self.threads.append(thread)
        return True


class _Bubbles:
    def __init__(self) -> None:
        self.shown: list[str] = []

    async def __call__(self, text: str) -> bool:
        self.shown.append(text)
        return True


@pytest.fixture
def alerts(monkeypatch) -> _Alerts:
    spy = _Alerts()
    monkeypatch.setattr(nc, "post_alert", spy)
    return spy


@pytest.fixture
def bubbles(monkeypatch) -> _Bubbles:
    spy = _Bubbles()
    monkeypatch.setattr(fc, "show_reply", spy)
    return spy


def _inbound(
    channel: str, source: str, thread: str | None = None, text: str = "spostalo a lunedì",
) -> InboundMessage:
    """L'inbound esattamente come lo costruisce ``native_input._publish``."""
    metadata: dict[str, Any] = {NATIVE_SOURCE_KEY: source}
    if thread:
        metadata[NATIVE_THREAD_KEY] = thread
    return InboundMessage(
        channel=channel, sender_id="user", chat_id=NATIVE_CHAT_ID, content=text,
        metadata=metadata,
    )


def _from_the_shade(thread: str | None) -> InboundMessage:
    return _inbound(NOTIFICATION_CHANNEL, SOURCE_NOTIFICATION, thread)


async def test_the_tag_survives_the_turn_and_returns_to_the_same_card(
    tmp_path, alerts: _Alerts
) -> None:
    loop = make_loop(tmp_path)
    inbound = _from_the_shade("cron:spesa")

    outbound = loop._assemble_outbound(
        inbound, "fatto, lunedì alle 9", [], "stop", False, None
    )

    assert outbound is not None
    assert outbound.channel == NOTIFICATION_CHANNEL
    assert outbound.metadata[NATIVE_THREAD_KEY] == "cron:spesa"

    await NotificationChannel().send(outbound)
    assert alerts.threads == ["cron:spesa"]


async def test_without_tag_the_roundtrip_ends_on_the_fallback(tmp_path, alerts: _Alerts) -> None:
    loop = make_loop(tmp_path)
    outbound = loop._assemble_outbound(
        _from_the_shade(None), "eccomi", [], "stop", False, None
    )

    assert outbound is not None
    assert NATIVE_THREAD_KEY not in outbound.metadata

    await NotificationChannel().send(outbound)
    assert alerts.threads == [REPLY_THREAD_TAG]


async def test_the_answer_returns_to_the_mascot(tmp_path, bubbles: _Bubbles) -> None:
    loop = make_loop(tmp_path)
    outbound = loop._assemble_outbound(
        _inbound(FLOATING_CHANNEL, SOURCE_FLOATING, text="che ore sono a Tokyo?"),
        "le 21:40", [], "stop", False, None,
    )

    assert outbound is not None
    assert outbound.channel == FLOATING_CHANNEL

    await FloatingChannel().send(outbound)
    assert bubbles.shown == ["le 21:40"]


@pytest.mark.parametrize(("channel", "source", "thread"), SURFACES)
def test_the_question_enters_the_personal_conversation(
    tmp_path, channel: str, source: str, thread: str | None
) -> None:
    """La riga che l'utente ha scritto: «la chat è identica a come sarebbe
    dentro l'app». Il ``chat_id`` della superficie non apre una sessione sua —
    ``session_key_for_channel`` manda ogni canale utente su ``unified:default``,
    ed è per questo che Jenny ricorda quello che le si è chiesto dal fumetto.
    Il tag della tendina sceglie la *scheda*, non la sessione: quella resta una."""
    loop = make_loop(tmp_path)
    inbound = _inbound(channel, source, thread)
    outbound = loop._assemble_outbound(inbound, "ok", [], "stop", False, None)

    assert inbound.session_key == UNIFIED_SESSION_KEY
    assert outbound is not None
    assert outbound.chat_id == inbound.chat_id


@pytest.mark.parametrize(("channel", "source", "thread"), SURFACES)
def test_the_source_survives_the_turn(
    tmp_path, channel: str, source: str, thread: str | None
) -> None:
    """Non serve a instradare — quello lo fa il canale — ma dice *da dove* è
    entrata la domanda, e il trasporto è la stessa riga che porta il tag della
    tendina (``meta = dict(msg.metadata or {})``)."""
    loop = make_loop(tmp_path)
    outbound = loop._assemble_outbound(
        _inbound(channel, source, thread), "ok", [], "stop", False, None
    )
    assert outbound is not None
    assert outbound.metadata[NATIVE_SOURCE_KEY] == source


def test_the_mascot_and_the_dropdown_do_not_swap_answers() -> None:
    """Il motivo per cui sono due canali e non uno.

    Due superfici native, due posti diversi in cui la risposta torna: se
    condividessero il canale, chi ha scritto nel fumetto si vedrebbe la risposta
    squillare in tendina — e viceversa.
    """
    from jenny.runtime.native_input import _CHANNEL_BY_SOURCE

    assert _CHANNEL_BY_SOURCE[SOURCE_FLOATING] == FLOATING_CHANNEL
    assert _CHANNEL_BY_SOURCE[SOURCE_NOTIFICATION] == NOTIFICATION_CHANNEL
    assert FLOATING_CHANNEL != NOTIFICATION_CHANNEL
