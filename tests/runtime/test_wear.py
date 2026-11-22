"""Test per ``jenny/runtime/wear.py`` (il confine verso ``WearBridge``).

Come per la mascotte, il bridge Chaquopy non esiste fuori dal telefono: si
sostituisce il seam ``_resolve_bridge_class`` e si guarda cosa viene chiamato.
Il Data Layer sta in Kotlin, nel flavor ``play``; qui si prova solo che la
risposta attraversi il confine con il suo indirizzo, e che un bridge assente —
la build ``foss`` — non faccia cadere niente.
"""

from __future__ import annotations

import pytest
from support.kotlin_source import PLAY_SRC, read_code, read_source

from jenny.runtime import wear as we
from jenny.runtime.native_input import SOURCE_WEAR


class _FakeBridge:
    def __init__(self, result: bool = True) -> None:
        self.calls: list[tuple[str, str]] = []
        self.result = result

    def sendReply(self, address, text):  # noqa: N802 — è il nome del metodo Kotlin
        self.calls.append((address, text))
        return self.result


@pytest.fixture(autouse=True)
def _clean_state():
    we.reset_wear_state()
    yield
    we.reset_wear_state()


@pytest.fixture
def bridge(monkeypatch) -> _FakeBridge:
    instance = _FakeBridge()
    monkeypatch.setattr(we, "_resolve_bridge_class", lambda: (lambda _ctx: instance))
    monkeypatch.setattr(we, "get_android_context", lambda: object())
    return instance


async def test_off_the_phone_nothing_happens(monkeypatch):
    monkeypatch.setattr(we, "get_android_context", lambda: None)
    assert await we.send_reply("ciao", "node/q") is False


async def test_the_reply_crosses_with_its_address(bridge: _FakeBridge):
    assert await we.send_reply("  Basilico al 34%  ", "node-1/q-42") is True
    assert bridge.calls == [("node-1/q-42", "Basilico al 34%")]


@pytest.mark.parametrize("text,address", [("", "node/q"), ("   ", "node/q"),
                                          ("ciao", None), ("ciao", "")])
async def test_nothing_to_send_or_nowhere_to_send_it(bridge: _FakeBridge, text, address):
    """Senza testo o senza indirizzo non si chiama Kotlin: non si indovina un
    orologio, e la risposta resta comunque nella conversazione."""
    assert await we.send_reply(text, address) is False
    assert bridge.calls == []


async def test_a_missing_bridge_is_the_foss_build_and_not_an_error(monkeypatch):
    """Nella ``foss`` la classe non c'è: la risoluzione fallisce e la chiamata
    torna ``False`` invece di sollevare dentro un turno."""
    def _missing():
        raise RuntimeError("class not found: com.flagdizero.jenny.WearBridge")

    monkeypatch.setattr(we, "_resolve_bridge_class", _missing)
    monkeypatch.setattr(we, "get_android_context", lambda: object())
    assert await we.send_reply("ciao", "node/q") is False


def test_the_kotlin_side_speaks_the_same_names():
    """Python chiama ``WearBridge.sendReply`` per nome, via Chaquopy: né il
    compilatore Kotlin né pyright vedono quel legame. Se cambia da una parte
    sola, l'orologio smette di ricevere risposte senza un errore. (La sorgente
    ``wear`` la controlla ``test_native_input.TestBoundaryWithKotlin``.)"""
    bridge = read_code(PLAY_SRC / "WearBridge.kt")
    assert "class WearBridge(" in bridge
    assert "fun sendReply(address: String, text: String)" in bridge
    assert we._BRIDGE.java_class == "com.flagdizero.jenny.WearBridge"


def test_the_watch_delivers_from_the_same_boundary():
    """Il polso non si porta un percorso suo verso Python: passa da
    ``deliverWithRetry``, come la tendina e la mascotte, con la sua sorgente."""
    src = read_source("GatewayService")
    assert "fun deliverWearText(" in src
    assert "deliverWithRetry(text, NATIVE_SOURCE_WEAR, address" in src
    assert SOURCE_WEAR == "wear"
