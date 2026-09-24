"""Ogni evento che il gateway manda sul WebSocket ha qualcuno che lo ascolta.

Il 21/09/2026 (`98a0230`) la schermata App se n'è andata portandosi via gli
unici gestori di ``app_data_changed`` e ``apps_list_changed``. Il gateway ha
continuato a mandarli a ogni turno che tocca un'app, e nessuno se n'è accorto
per giorni: un frame senza ascoltatore non fa errore, non fa log, non fa
niente — la mini-app aperta smette solo di aggiornarsi da sola. Lo ha trovato
un audit sul codice morto, non un test.

Il banco legge i nomi dal lato Python (le forme in cui ``jenny/channels``
scrive un evento) e chiede che ognuno compaia come stringa nel JS della WebUI,
commenti esclusi — perché dopo `98a0230` i nomi sopravvivevano proprio nei
commenti, a descrivere un ascolto che non c'era più. È un controllo largo: un
nome citato in una stringa qualunque lo soddisfa. Non dimostra che il gestore
funzioni; dimostra che esiste un posto dove guardare.
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
SENZA_ASCOLTATORE = {
    # Conferme di protocollo: la connessione è registrata (``ready``) e la
    # sottoscrizione a una chat è attiva (``attached``). Documentate come API in
    # docs/reference/websocket.md e usate dai test d'integrazione; la WebUI non
    # ha niente da farci.
    "ready": "conferma di protocollo",
    "attached": "conferma di protocollo",
    # Nessun client lo legge dal 0.3.0; si toglie col passo D5 di
    # .agent/pulizia-codice-plan.md, e con lui questa riga.
    "session_updated": "da togliere (piano, D5)",
}

# Ascoltatori persi, da rimettere (piano, passo 0.3). ``strict``: quando il
# passo atterra il caso passa, e un xfail che passa è rosso — così questa riga
# se ne va insieme al difetto.
DA_RIPARARE = {"app_data_changed", "apps_list_changed"}


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
    assert set(SENZA_ASCOLTATORE) <= EMITTED
    assert DA_RIPARARE <= EMITTED


def _cases():
    for name in sorted(EMITTED - set(SENZA_ASCOLTATORE)):
        marks = ()
        if name in DA_RIPARARE:
            marks = (pytest.mark.xfail(strict=True, reason="ascoltatore perso in 98a0230"),)
        yield pytest.param(name, marks=marks, id=name)


@pytest.mark.parametrize("event", list(_cases()))
def test_every_emitted_event_has_a_listener(event: str) -> None:
    assert re.search(rf"""['"`]{re.escape(event)}['"`]""", UI_JS), (
        f"il gateway manda `{event}` ma nessun JS della WebUI lo nomina: "
        "o manca il gestore, o l'evento va tolto dal server"
    )
