"""Ogni evento che il gateway manda sul WebSocket ha qualcuno che lo ascolta.

Il 21/09/2026 (`98a0230`) la schermata App se n'è andata portandosi via gli
unici gestori di ``app_data_changed`` e ``apps_list_changed``. Il gateway ha
continuato a mandarli a ogni turno che tocca un'app, e nessuno se n'è accorto
per giorni: un frame senza ascoltatore non fa errore, non fa log, non fa
niente — la mini-app aperta smette solo di aggiornarsi da sola. Lo ha trovato
un audit sul codice morto, non un test.

Il banco legge i nomi dal lato Python (le forme in cui ``jenny/channels``
scrive un evento) e chiede che ognuno compaia nel JS della WebUI, commenti
esclusi — perché dopo `98a0230` i nomi sopravvivevano proprio nei commenti, a
descrivere un ascolto che non c'era più — **in una forma d'ascolto**:
``case 'x':`` o ``event === 'x'`` (``!==`` per chi esce presto). Fino al
25/09/2026 bastava la stringa nuda, e per ``'user'`` ed ``'error'`` — parole
che compaiono dappertutto, da ``role === 'user'`` a ``showToast(…, 'error')``
— il gestore poteva sparire lasciando il test verde. Non dimostra che il
gestore funzioni; dimostra che esiste un posto dove guardare.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from jenny.channels.subagent_activity_wire import ACTIVITY_FRAME_EVENT

ROOT = Path(__file__).resolve().parents[2]
CHANNELS = ROOT / "jenny" / "channels"
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"

# Eventi che il client non deve ascoltare, ognuno con il suo perché. Chi ne
# aggiunge uno qui lo fa sapendolo; un evento nuovo senza ascoltatore fa rosso.
WITHOUT_LISTENER = {
    # Conferme di protocollo: la connessione è registrata (``ready``) e la
    # sottoscrizione a una chat è attiva (``attached``). Documentate come API in
    # docs/reference/websocket.md e usate dai test d'integrazione; la WebUI non
    # ha niente da farci.
    "ready": "conferma di protocollo",
    "attached": "conferma di protocollo",
}

def _emitted_events() -> set[str]:
    src = "".join(p.read_text(encoding="utf-8") for p in sorted(CHANNELS.glob("*.py")))
    names = set(re.findall(r'"event"\s*:\s*"([a-z_]+)"', src))
    names |= set(re.findall(r'_send_event\(\s*[\w.]+\s*,\s*"([a-z_]+)"', src, re.S))
    names.add(ACTIVITY_FRAME_EVENT)
    return names


def _ui_js_without_comments() -> str:
    files = sorted(ASSETS.glob("*.js")) + sorted((ASSETS / "shared").glob("*.js"))
    text = "".join(p.read_text(encoding="utf-8", errors="replace") for p in files)
    # Via i blocchi e le righe di commento; ``//`` dopo ``:`` o dentro una
    # stringa è un indirizzo, non un commento.
    return re.sub(r"/\*.*?\*/|(?<![:'\"`])//[^\n]*", "", text, flags=re.S)


EMITTED = _emitted_events()
UI_JS = _ui_js_without_comments()


def test_the_scan_still_finds_the_events() -> None:
    """Se le regex smettessero di trovare i nomi, il banco passerebbe a vuoto."""
    assert {"delta", "turn_end", "message", "error", "rpc_result"} <= EMITTED
    assert len(EMITTED) >= 15, sorted(EMITTED)


def test_the_exceptions_are_still_emitted() -> None:
    """Un'eccezione per un evento che non esiste più è una riga morta."""
    assert set(WITHOUT_LISTENER) <= EMITTED


def _listener(event: str) -> re.Pattern[str]:
    """``case 'x':`` o ``…event === 'x'`` / ``!== 'x'``: dove un frame si smista."""
    q = r"""['"`]"""
    return re.compile(rf"(?:\bcase\s+|\bevent\s*[!=]==\s*){q}{re.escape(event)}{q}")


@pytest.mark.parametrize("event", sorted(EMITTED - set(WITHOUT_LISTENER)))
def test_every_emitted_event_has_a_listener(event: str) -> None:
    assert _listener(event).search(UI_JS), (
        f"il gateway manda `{event}` ma nessun JS della WebUI lo nomina: "
        "o manca il gestore, o l'evento va tolto dal server"
    )


def test_a_bare_mention_is_not_a_listener() -> None:
    """La forma che il banco vecchio accettava, e che non smista niente."""
    fake = "if (role === 'user') x(); showToast(msg, 'error'); const L = ['delta'];"
    assert not _listener("user").search(fake)
    assert not _listener("error").search(fake)
    assert not _listener("delta").search(fake)
    assert _listener("user").search("switch (msg.event) { case 'user': f(); }")
    assert _listener("error").search("if (msg?.event === 'error') g();")
    assert _listener("goal_status").search("if (msg.event !== \"goal_status\") return;")
