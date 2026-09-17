"""Test per ``jenny/channels/floating.py`` (il fumetto della mascotte).

Gemello di ``test_notification_channel.py``, e per la stessa ragione: il bridge
Chaquopy non esiste fuori dal telefono, quindi qui si sostituisce ``show_reply``
e si guarda **con che cosa** viene chiamato, più i due gate che tengono il
fumetto pulito (eventi di coordinamento, messaggi vuoti).

Le due famiglie di test non si accorpano in una parametrizzata sui due canali,
malgrado la forma sia la stessa: sono due contratti che *oggi* coincidono e che
possono divergere alla prima cosa che il fumetto vuole e la tendina no. Un test
condiviso renderebbe quella divergenza una rottura invece di una scelta.
"""

from __future__ import annotations

from typing import Any

import pytest

from jenny.bus.events import FLOATING_CHANNEL, OutboundMessage
from jenny.channels import floating as fc
from jenny.channels.floating import FloatingChannel


class _Spy:
    def __init__(self, result: bool = True) -> None:
        self.calls: list[str] = []
        self.result = result

    async def __call__(self, text: str) -> bool:
        self.calls.append(text)
        return self.result


@pytest.fixture
def spy(monkeypatch) -> _Spy:
    s = _Spy()
    monkeypatch.setattr(fc, "show_reply", s)
    return s


def _msg(content: str = "ecco fatto", **meta: Any) -> OutboundMessage:
    return OutboundMessage(
        channel=FLOATING_CHANNEL, chat_id="shade", content=content, metadata=meta
    )


class TestContratto:
    def test_il_canale_si_chiama_come_la_costante_condivisa(self):
        assert FloatingChannel.name == FLOATING_CHANNEL

    def test_niente_progress_ne_reasoning(self):
        """Il dispatcher legge questi tre attributi prima di instradare."""
        assert FloatingChannel.send_progress is False
        assert FloatingChannel.send_tool_hints is False
        assert FloatingChannel.show_reasoning is False

    def test_un_solo_tentativo(self):
        """``show_reply`` non solleva: un retry ridisegnerebbe lo stesso fumetto."""
        assert FloatingChannel.send_max_retries == 1

    async def test_start_e_stop_non_fanno_niente(self):
        """La finestra la monta e la smonta il service, non il canale."""
        ch = FloatingChannel()
        assert await ch.start() is None
        assert await ch.stop() is None

    async def test_i_send_di_streaming_ritornano_lista_vuota(self):
        ch = FloatingChannel()
        assert await ch.send_delta("x") == []
        assert await ch.send_reasoning_delta("x") == []
        assert await ch.send_reasoning_end("x") == []
        assert await ch.send_file_edit_events("x") == []
        assert ch.discard_stream_buffer("x") is None


class TestSend:
    async def test_disegna_il_messaggio_finale(self, spy: _Spy):
        ch = FloatingChannel()
        assert await ch.send(_msg("ecco fatto")) == []
        assert spy.calls == ["ecco fatto"]

    async def test_il_contenuto_viene_ripulito(self, spy: _Spy):
        ch = FloatingChannel()
        await ch.send(_msg("  con spazi  "))
        assert spy.calls == ["con spazi"]

    @pytest.mark.parametrize("content", ["", "   "])
    async def test_un_messaggio_vuoto_non_apre_il_fumetto(self, spy: _Spy, content: str):
        ch = FloatingChannel()
        assert await ch.send(_msg(content)) == []
        assert spy.calls == []

    async def test_gli_eventi_di_coordinamento_non_aprono_il_fumetto(self, spy: _Spy):
        """Il caso vero è ``_turn_end``: non porta flag di streaming, quindi il
        dispatcher lo fa arrivare fin qui. Senza questo gate ogni fine turno
        stamperebbe un fumetto vuoto sopra l'app di qualcun altro."""
        ch = FloatingChannel()
        await ch.send(_msg("", _turn_end=True))
        await ch.send(_msg("testo di servizio", _progress=True))
        await ch.send(_msg("eco", _user_echo=True))
        await ch.send(_msg("delta", _stream_delta=True))
        assert spy.calls == []

    async def test_un_fumetto_non_mostrato_non_e_un_errore(self, monkeypatch):
        """Mascotte spenta, permesso mancante o app in primo piano: ``show_reply``
        ritorna False e il canale tace. La risposta è comunque in chat."""
        monkeypatch.setattr(fc, "show_reply", _Spy(result=False))
        ch = FloatingChannel()
        assert await ch.send(_msg("ciao")) == []

    async def test_i_media_non_bloccano_il_testo(self, spy: _Spy):
        """Un fumetto è testo: gli allegati restano in chat, la frase si disegna."""
        msg = _msg("guarda qui")
        msg.media = ["/workspace/foto.png"]
        assert await FloatingChannel().send(msg) == []
        assert spy.calls == ["guarda qui"]

    async def test_ogni_send_sovrascrive_il_fumetto_precedente(self, spy: _Spy):
        """«Senza history» è una regola della UI, e qui si vede in negativo: il
        canale non accumula niente fra una risposta e l'altra, quindi non c'è
        nessuno stato che possa divergere da ciò che la finestra mostra."""
        ch = FloatingChannel()
        await ch.send(_msg("prima"))
        await ch.send(_msg("seconda"))
        assert spy.calls == ["prima", "seconda"]
