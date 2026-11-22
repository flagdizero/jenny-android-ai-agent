"""Test per ``jenny/channels/wear.py`` (la risposta che torna al polso).

Gemello di ``test_floating_channel.py`` e tenuto separato per la stessa
ragione scritta là: due contratti che oggi coincidono e domani no. Qui in più
c'è l'indirizzo — il *thread* con cui la domanda è entrata — che il canale deve
riportare a Kotlin così com'è.
"""

from __future__ import annotations

from typing import Any

import pytest

from jenny.bus.events import WEAR_CHANNEL, OutboundMessage
from jenny.channels import wear as wc
from jenny.channels.wear import WearChannel
from jenny.runtime.native_input import NATIVE_THREAD_KEY

ADDRESS = "node-1/q-42"


class _Spy:
    def __init__(self, result: bool = True) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self.result = result

    async def __call__(self, text: str, address: str | None) -> bool:
        self.calls.append((text, address))
        return self.result


@pytest.fixture
def spy(monkeypatch) -> _Spy:
    s = _Spy()
    monkeypatch.setattr(wc, "send_reply", s)
    return s


def _msg(content: str = "ecco fatto", **meta: Any) -> OutboundMessage:
    meta.setdefault(NATIVE_THREAD_KEY, ADDRESS)
    return OutboundMessage(channel=WEAR_CHANNEL, chat_id="shade", content=content, metadata=meta)


class TestContract:
    def test_the_channel_is_named_like_the_shared_constant(self):
        assert WearChannel.name == WEAR_CHANNEL

    def test_only_the_final_text(self):
        """Sul polso niente pezzi di risposta, niente tool hint, niente ragionamento."""
        assert WearChannel.send_progress is False
        assert WearChannel.send_tool_hints is False
        assert WearChannel.show_reasoning is False

    def test_a_single_attempt(self):
        """``send_reply`` non solleva: un retry manderebbe due volte la risposta."""
        assert WearChannel.send_max_retries == 1


class TestSend:
    async def test_the_reply_goes_back_to_the_address_it_came_from(self, spy: _Spy):
        assert await WearChannel().send(_msg("ecco fatto")) == []
        assert spy.calls == [("ecco fatto", ADDRESS)]

    async def test_without_an_address_the_reply_is_still_attempted_as_none(self, spy: _Spy):
        """Senza indirizzo è ``send_reply`` a rinunciare (lo prova il test del
        runtime): il canale non inventa un destinatario."""
        await WearChannel().send(_msg("ciao", **{NATIVE_THREAD_KEY: "   "}))
        assert spy.calls == [("ciao", "")]

    async def test_coordination_events_are_not_sent_to_the_wrist(self, spy: _Spy):
        """``_turn_end`` arriva fin qui come per la mascotte: sul polso sarebbe
        un messaggio vuoto a ogni fine turno."""
        ch = WearChannel()
        await ch.send(_msg("", _turn_end=True))
        await ch.send(_msg("testo di servizio", _progress=True))
        await ch.send(_msg("delta", _stream_delta=True))
        assert spy.calls == []

    async def test_an_undelivered_reply_is_not_an_error(self, monkeypatch):
        """Orologio lontano o app chiusa: la risposta è comunque in chat, e
        l'orologio se ne accorge col suo tempo massimo."""
        monkeypatch.setattr(wc, "send_reply", _Spy(result=False))
        assert await WearChannel().send(_msg("ciao")) == []
