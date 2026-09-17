"""Dalla mascotte all'agente e ritorno, senza che il giro cambi conversazione.

Gemello di ``test_notification_thread_roundtrip.py``, che prova la stessa
catena per la tendina. Qui non c'è un tag da trasportare — la finestra è una
sola e non ha fili da tenere distinti — quindi la catena prova l'altra cosa,
quella che l'utente ha chiesto in una riga: **la chat è identica a come sarebbe
dentro l'app.**

I tre punti hanno già i loro test; quello che nessuno di loro prova è che,
messi in fila, una domanda scritta nel fumetto finisca nella conversazione
personale — la stessa di WebUI e Telegram — e che la risposta torni alla
mascotte invece che da qualche altra parte.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from jenny.agent.loop import AgentLoop
from jenny.bus.events import FLOATING_CHANNEL, InboundMessage
from jenny.bus.queue import MessageBus
from jenny.channels import floating as fc
from jenny.channels.floating import FloatingChannel
from jenny.providers.base import LLMResponse
from jenny.runtime.native_input import NATIVE_SOURCE_KEY, SOURCE_FLOATING
from jenny.session.keys import UNIFIED_SESSION_KEY


class _Bubbles:
    def __init__(self) -> None:
        self.shown: list[str] = []

    async def __call__(self, text: str) -> bool:
        self.shown.append(text)
        return True


@pytest.fixture
def bubbles(monkeypatch) -> _Bubbles:
    spy = _Bubbles()
    monkeypatch.setattr(fc, "show_reply", spy)
    return spy


def _loop(tmp_path: Path) -> AgentLoop:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation = SimpleNamespace(max_tokens=4096)
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="done"))
    return AgentLoop(
        bus=MessageBus(), provider=provider, workspace=tmp_path, model="test-model"
    )


def _from_the_mascot(text: str = "che ore sono a Tokyo?") -> InboundMessage:
    """L'inbound esattamente come lo costruisce ``native_input._publish``."""
    return InboundMessage(
        channel=FLOATING_CHANNEL,
        sender_id="user",
        chat_id="shade",
        content=text,
        metadata={NATIVE_SOURCE_KEY: SOURCE_FLOATING},
    )


async def test_la_risposta_torna_alla_mascotte(tmp_path, bubbles: _Bubbles) -> None:
    loop = _loop(tmp_path)
    outbound = loop._assemble_outbound(
        _from_the_mascot(), "le 21:40", [], "stop", False, None
    )

    assert outbound is not None
    assert outbound.channel == FLOATING_CHANNEL

    await FloatingChannel().send(outbound)
    assert bubbles.shown == ["le 21:40"]


def test_la_domanda_entra_nella_conversazione_personale(tmp_path) -> None:
    """La riga che l'utente ha scritto: «la chat è identica a come sarebbe
    dentro l'app». Il ``chat_id`` della superficie non apre una sessione sua —
    ``session_key_for_channel`` manda ogni canale utente su ``unified:default``,
    ed è per questo che Jenny ricorda quello che le si è chiesto dal fumetto."""
    loop = _loop(tmp_path)
    inbound = _from_the_mascot()
    outbound = loop._assemble_outbound(inbound, "ok", [], "stop", False, None)

    assert inbound.session_key == UNIFIED_SESSION_KEY
    assert outbound is not None
    assert outbound.chat_id == inbound.chat_id


def test_la_sorgente_sopravvive_al_turno(tmp_path) -> None:
    """Non serve a instradare — quello lo fa il canale — ma dice *da dove* è
    entrata la domanda, e il trasporto è la stessa riga che porta il tag della
    tendina (``meta = dict(msg.metadata or {})``)."""
    loop = _loop(tmp_path)
    outbound = loop._assemble_outbound(
        _from_the_mascot(), "ok", [], "stop", False, None
    )
    assert outbound is not None
    assert outbound.metadata[NATIVE_SOURCE_KEY] == SOURCE_FLOATING


def test_la_mascotte_e_la_tendina_non_si_scambiano_le_risposte(tmp_path) -> None:
    """Il motivo per cui sono due canali e non uno.

    Due superfici native, due posti diversi in cui la risposta torna: se
    condividessero il canale, chi ha scritto nel fumetto si vedrebbe la risposta
    squillare in tendina — e viceversa.
    """
    from jenny.bus.events import NOTIFICATION_CHANNEL
    from jenny.runtime.native_input import _CHANNEL_BY_SOURCE, SOURCE_NOTIFICATION

    assert _CHANNEL_BY_SOURCE[SOURCE_FLOATING] == FLOATING_CHANNEL
    assert _CHANNEL_BY_SOURCE[SOURCE_NOTIFICATION] == NOTIFICATION_CHANNEL
    assert FLOATING_CHANNEL != NOTIFICATION_CHANNEL
