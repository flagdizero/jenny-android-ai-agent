"""Due vicoli ciechi della chat: il tap che non produce niente e il turno che
non si chiude mai.

**N6.** Il chip di un allegato non renderizzabile inline provava il bridge
nativo e, se falliva, ripiegava su ``window.open(entry.url)``. Sotto Android
quel ripiego non è un ripiego: la WebView non ha alcun ``DownloadListener`` e
non apre schede nuove, quindi il tap non produceva **niente** — nessun viewer,
nessun errore, nessun segno che fosse successo qualcosa. ``window.open`` ha
senso solo dove il bridge non esiste affatto (debug da browser desktop); dove
esiste, il suo fallimento è un errore da dire.

**N9.** La guardia di ``_handleWsMessage`` scarta gli eventi di un turno che la
mascotte non sta seguendo, e le sue due condizioni — ``awaiting`` e la classe
``thinking`` — sono **esattamente** ciò che ``_closeMini()`` azzera. Chiudendo
la minichat prima della fine del turno, ``turn_end`` veniva scartato; ed è
l'unico punto che invalida lo storico della chat principale, quindi la domanda
posta alla minichat non compariva più e la chat restava con una risposta senza
domanda per tutta la sessione. Un reload "riparava": lo scambio nel file c'era
sempre stato, era la vista viva a mentire.

Il flag che tiene aperto il turno deve quindi essere **indipendente dalla UI**:
``_closeMini`` non lo tocca, e solo ``turn_end``/``error`` lo chiudono. Dal
28/09/2026 la minichat e' dei due gusci (``shared/jenny-minichat.js``) e va oltre:
a minichat chiusa segue ancora la domanda, e la risposta la trovi riaprendola.

Asserzioni sul sorgente, nello stile di ``test_thinking_scroll_contract.py``: la
WebUI non ha un runner JS con DOM.
"""

from __future__ import annotations

import re
from pathlib import Path

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
CHAT_JS = ASSETS / "mobile-chat.js"
MASCOT_JS = ASSETS / "shared" / "jenny-mascot.js"


def _method(source: str, name: str) -> str:
    # I parametri possono avere un default con le sue parentesi
    # (`mine = this._trackedTurnMatches(msg)`): un livello di annidamento basta.
    body = re.search(
        rf"\n  (?:async )?{name}\((?:[^()]|\([^()]*\))*\)\s*\{{(.*?)\n  \}}", source, re.S
    )
    assert body, f"{name} non trovato"
    return body.group(1)


# ── N6 · il tap su un allegato dice sempre qualcosa ──────────────────────────


def test_a_failing_native_bridge_is_reported_not_papered_over() -> None:
    source = CHAT_JS.read_text(encoding="utf-8")
    body = _method(source, "_openMediaFile")

    assert "const bridge = window.JennyNative;" in body
    assert "typeof bridge.openFile === 'function'" in body, (
        "il ramo va scelto sulla *presenza* del bridge, non sull'esito della chiamata"
    )
    # Col bridge presente il ramo si chiude su un toast e su un `return`, e
    # `window.open` resta fuori: è il caso desktop, non il ripiego.
    assert "showToast(" in body, (
        "col bridge presente il fallimento non produceva niente: nessun viewer e nessun errore"
    )
    assert "i18n.t('chat.couldNotOpen'" in body, "il messaggio va localizzato"
    assert "window.open(entry.url, '_blank');" in body, (
        "fuori dalla WebView (debug da browser) la scheda nuova resta l'unica strada"
    )
    assert body.index("showToast(") < body.index("window.open("), (
        "il toast deve chiudere il ramo col bridge; window.open è l'ultima riga, "
        "raggiunta solo quando il bridge non esiste affatto"
    )
    toast_line = next(line for line in body.splitlines() if "showToast(" in line)
    open_line = next(line for line in body.splitlines() if "window.open(" in line)
    assert len(toast_line) - len(toast_line.lstrip()) > len(open_line) - len(open_line.lstrip()), (
        "window.open è annidato quanto il toast: è ancora il ripiego del bridge fallito, "
        "cioè l'apertura che la WebView blocca in silenzio"
    )


# ── N9 · il turno si chiude anche a minichat chiusa ──────────────────────────
