"""Test per ``jenny/runtime/native_input.py`` (ingresso dalla tendina).

Il chiamante vero è Kotlin da un thread JNI, e questo file lo imita: l'unica
prova che conta davvero è quella che entra da un thread che **non** è il loop,
perché è l'unica in cui un ``publish_inbound`` chiamato male perderebbe il
messaggio invece di consegnarlo.
"""

from __future__ import annotations

import asyncio
import pathlib

import pytest
from support.aio import wait_until

from jenny.bus.events import NOTIFICATION_CHANNEL, InboundMessage
from jenny.runtime import native_input as ni
from jenny.session.keys import UNIFIED_SESSION_KEY


class _FakeBus:
    """Bus minimo: registra gli inbound e segnala l'arrivo."""

    def __init__(self) -> None:
        self.inbound: list[InboundMessage] = []
        self.arrived = asyncio.Event()

    async def publish_inbound(self, msg: InboundMessage) -> None:
        self.inbound.append(msg)
        self.arrived.set()


@pytest.fixture(autouse=True)
def _clean_state():
    """Ogni test parte e finisce slegato: i globali sono di modulo."""
    ni.reset_native_input()
    yield
    ni.reset_native_input()


async def _bound() -> _FakeBus:
    bus = _FakeBus()
    assert ni.bind_native_input(bus) is True
    return bus


class TestRejections:
    async def test_without_bind_does_not_raise_and_says_no(self):
        assert ni.on_native_text("ciao") is False

    async def test_after_reset_does_not_reuse_the_dead_loop(self):
        await _bound()
        ni.reset_native_input()
        assert ni.on_native_text("ciao") is False

    @pytest.mark.parametrize("text", ["", "   ", "\n\t "])
    async def test_empty_text(self, text: str):
        bus = await _bound()
        assert ni.on_native_text(text) is False
        assert bus.inbound == []

    async def test_beyond_the_cap(self):
        bus = await _bound()
        assert ni.on_native_text("x" * (ni.MAX_TEXT_CHARS + 1)) is False
        assert bus.inbound == []

    async def test_the_cap_is_inclusive(self):
        bus = await _bound()
        assert ni.on_native_text("x" * ni.MAX_TEXT_CHARS) is True
        await asyncio.wait_for(bus.arrived.wait(), 2)
        assert len(bus.inbound[0].content) == ni.MAX_TEXT_CHARS

    async def test_unknown_source(self):
        """L'elenco delle sorgenti è chiuso: Kotlin non può inventare un canale."""
        bus = await _bound()
        assert ni.on_native_text("ciao", "overlay-che-non-esiste") is False
        assert bus.inbound == []

    async def test_the_cap_is_measured_on_the_clean_text(self):
        """Spazi in coda non devono far sforare un messaggio che ci sta."""
        bus = await _bound()
        assert ni.on_native_text("x" * ni.MAX_TEXT_CHARS + "   \n") is True
        await asyncio.wait_for(bus.arrived.wait(), 2)
        assert len(bus.inbound[0].content) == ni.MAX_TEXT_CHARS


class TestBind:
    async def test_without_bus_does_not_claim_to_be_attached(self):
        """Un log che dichiara agganciato ciò che non lo è costa una diagnosi."""
        assert ni.bind_native_input(None) is False
        assert ni.on_native_text("ciao") is False

    def test_the_gateway_hooks_it_at_startup(self):
        """Il bind vive in ``GatewayContainer.run`` e nessun test lo esercita
        end-to-end: senza questa guardia, toglierlo lascerebbe la suite verde e
        la tendina muta sul telefono. Stessa forma di
        ``test_the_agent_is_handed_the_generic_hook``.
        """
        import inspect

        from jenny.runtime.container import GatewayContainer

        source = inspect.getsource(GatewayContainer.run)
        assert "bind_native_input(self.bus)" in source

    def test_the_gateway_unbinds_it_at_every_restart(self):
        """``run_gateway`` riparte nello stesso processo: il loop del giro
        precedente è morto e i suoi riferimenti vanno buttati."""
        import inspect

        from jenny import android_entry

        source = inspect.getsource(android_entry)
        assert "reset_native_input()" in source


class TestBoundaryWithKotlin:
    """Il punto in cui un rename rompe solo sul telefono.

    Kotlin raggiunge questo modulo per **nome**, attraverso Chaquopy: né il
    compilatore Kotlin né pyright vedono quel legame, e rinominare il modulo, la
    funzione o una stringa di sorgente lascerebbe tutto verde qui e muta la
    superficie là. Queste asserzioni sono l'unico posto in cui quel legame è
    controllato.
    """

    @staticmethod
    def _gateway_service() -> str:
        repo = pathlib.Path(__file__).resolve().parents[2]
        kt = repo / "android/app/src/main/java/com/flagdizero/jenny/GatewayService.kt"
        return kt.read_text(encoding="utf-8")

    def test_kotlin_calls_this_module(self):
        assert 'getModule("jenny.runtime.native_input")' in self._gateway_service()
        assert ni.__name__ == "jenny.runtime.native_input"

    def test_kotlin_calls_this_function(self):
        assert 'callAttr("on_native_text"' in self._gateway_service()
        assert callable(ni.on_native_text)

    def test_kotlin_passes_source_and_thread(self):
        """Tre argomenti, in quest'ordine: testo, sorgente, tag del filo. La
        sorgente è una variabile da quando le superfici native sono due, quindi
        il legame vero sono le costanti — v. il test qui sotto."""
        assert (
            '.callAttr("on_native_text", text, source, sourceTag)'
            in self._gateway_service()
        )

    @pytest.mark.parametrize(
        "kotlin_const, python_source",
        [
            ("NATIVE_SOURCE_NOTIFICATION", "SOURCE_NOTIFICATION"),
            ("NATIVE_SOURCE_FLOATING", "SOURCE_FLOATING"),
        ],
    )
    def test_kotlin_sources_are_recognized(self, kotlin_const, python_source):
        """Una sorgente fuori elenco viene rifiutata, in silenzio: se le due
        stringhe divergono, ogni messaggio di quella superficie viene scartato e
        nulla lo dice a compilazione."""
        value = getattr(ni, python_source)
        assert f'const val {kotlin_const} = "{value}"' in self._gateway_service()
        assert value in ni._CHANNEL_BY_SOURCE

    def test_the_mascot_delivers_from_the_same_boundary(self):
        """La seconda superficie non si è portata un percorso suo: passa dalla
        stessa funzione, con la sua sorgente."""
        src = self._gateway_service()
        assert "fun deliverFloatingText(" in src
        assert "deliverWithRetry(text, NATIVE_SOURCE_FLOATING" in src


class TestDelivery:
    async def test_happy_path(self):
        bus = await _bound()
        assert ni.on_native_text("ricordamelo domani") is True
        await asyncio.wait_for(bus.arrived.wait(), 2)

        (msg,) = bus.inbound
        assert msg.channel == NOTIFICATION_CHANNEL
        assert msg.chat_id == ni.NATIVE_CHAT_ID
        assert msg.sender_id == "user"
        assert msg.content == "ricordamelo domani"
        assert msg.metadata[ni.NATIVE_SOURCE_KEY] == ni.SOURCE_NOTIFICATION

    async def test_lands_on_the_single_conversation(self):
        """La ragione per cui questa funzionalità vale: stessa conversazione.

        Se questa asserzione cade, il testo scritto dalla tendina apre una
        sessione parallela — invisibile in chat e fuori dalla memoria di Dream.
        """
        bus = await _bound()
        ni.on_native_text("ciao")
        await asyncio.wait_for(bus.arrived.wait(), 2)
        assert bus.inbound[0].session_key == UNIFIED_SESSION_KEY

    async def test_from_a_thread_that_is_not_the_loop(self):
        """Il test che conta: è la condizione vera, Kotlin da un thread JNI.

        Da lì l'unica cosa lecita è ``call_soon_threadsafe``. Una versione che
        toccasse la coda direttamente passerebbe tutti gli altri test di questo
        file e perderebbe messaggi sul telefono.
        """
        bus = await _bound()
        ok = await asyncio.to_thread(ni.on_native_text, "scritto dal thread JNI")
        assert ok is True
        await asyncio.wait_for(bus.arrived.wait(), 2)
        assert bus.inbound[0].content == "scritto dal thread JNI"

    async def test_the_mascot_enters_on_its_own_channel(self):
        """Due superfici native, due canali: la risposta a una domanda scritta
        nel fumetto deve tornare **nel fumetto**, non squillare in tendina."""
        from jenny.bus.events import FLOATING_CHANNEL

        bus = await _bound()
        assert ni.on_native_text("che ore sono?", ni.SOURCE_FLOATING) is True
        await asyncio.wait_for(bus.arrived.wait(), 2)

        (msg,) = bus.inbound
        assert msg.channel == FLOATING_CHANNEL
        assert msg.metadata[ni.NATIVE_SOURCE_KEY] == ni.SOURCE_FLOATING
        assert msg.session_key == UNIFIED_SESSION_KEY

    async def test_the_mascot_carries_no_thread(self):
        """La sua finestra è una sola: non ha schede da tenere distinte, e una
        chiave a vuoto nei metadata la dovrebbe ignorare ogni lettore a valle."""
        bus = await _bound()
        assert ni.on_native_text("ciao", ni.SOURCE_FLOATING) is True
        await asyncio.wait_for(bus.arrived.wait(), 2)
        assert ni.NATIVE_THREAD_KEY not in bus.inbound[0].metadata

    async def test_the_thread_tag_enters_the_metadata(self):
        """È il tag della notifica da cui è partita la domanda: torna a valle e
        ci fa postare la risposta **su quella scheda**."""
        bus = await _bound()
        assert ni.on_native_text("ok", ni.SOURCE_NOTIFICATION, "cron:spesa") is True
        await asyncio.wait_for(bus.arrived.wait(), 2)
        assert bus.inbound[0].metadata[ni.NATIVE_THREAD_KEY] == "cron:spesa"

    @pytest.mark.parametrize("thread", [None, "", "   ", 42])
    async def test_a_missing_thread_leaves_no_empty_key(self, thread):
        """Una chiave a ``None`` è una chiave che ogni lettore a valle deve
        imparare a ignorare: meglio non scriverla."""
        bus = await _bound()
        assert ni.on_native_text("ok", ni.SOURCE_NOTIFICATION, thread) is True
        await asyncio.wait_for(bus.arrived.wait(), 2)
        assert ni.NATIVE_THREAD_KEY not in bus.inbound[0].metadata

    async def test_the_tag_is_cleaned(self):
        bus = await _bound()
        ni.on_native_text("ok", ni.SOURCE_NOTIFICATION, "  heartbeat \n")
        await asyncio.wait_for(bus.arrived.wait(), 2)
        assert bus.inbound[0].metadata[ni.NATIVE_THREAD_KEY] == "heartbeat"

    async def test_two_messages_in_a_row_stay_two(self):
        bus = await _bound()
        assert ni.on_native_text("primo") is True
        assert ni.on_native_text("secondo") is True
        await wait_until(lambda: len(bus.inbound) == 2)
        assert [m.content for m in bus.inbound] == ["primo", "secondo"]

    async def test_the_rebind_moves_the_bus(self):
        """Un gateway che riparte nello stesso processo non deve consegnare al vecchio."""
        old = await _bound()
        fresh = _FakeBus()
        ni.bind_native_input(fresh)
        ni.on_native_text("dopo il riavvio")
        await asyncio.wait_for(fresh.arrived.wait(), 2)
        assert old.inbound == []
        assert fresh.inbound[0].content == "dopo il riavvio"
