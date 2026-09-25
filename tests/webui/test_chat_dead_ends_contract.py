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
``_closeMini`` non lo tocca, e solo ``turn_end``/``error`` lo chiudono.

Asserzioni sul sorgente, nello stile di ``test_thinking_scroll_contract.py``: la
WebUI non ha un runner JS con DOM.
"""

from __future__ import annotations

import re
from pathlib import Path

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
CHAT_JS = ASSETS / "mobile-chat.js"
JENNY_JS = ASSETS / "mobile-jenny.js"
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
        "il toast deve chiudere il ramo col bridge; window.open è l'ultima row, "
        "raggiunta solo quando il bridge non esiste affatto"
    )
    toast_line = next(line for line in body.splitlines() if "showToast(" in line)
    open_line = next(line for line in body.splitlines() if "window.open(" in line)
    assert len(toast_line) - len(toast_line.lstrip()) > len(open_line) - len(open_line.lstrip()), (
        "window.open è annidato quanto il toast: è ancora il ripiego del bridge fallito, "
        "cioè l'apertura che la WebView blocca in silenzio"
    )


# ── N9 · il turno si chiude anche a minichat chiusa ──────────────────────────


def test_the_pending_turn_flag_is_independent_of_the_minichat_ui() -> None:
    source = JENNY_JS.read_text(encoding="utf-8")
    close = _method(source, "_closeMini")

    # Le due condizioni storiche della guardia: _closeMini le azzera entrambe,
    # ed è questo che rendeva il difetto inevitabile.
    assert "this.awaiting = false;" in close
    assert "classList.remove('thinking', 'mini');" in close
    assert "_pendingTurn" not in close, (
        "se la chiusura della minichat tocca il flag, il flag è di nuovo uno stato della UI "
        "e turn_end torna a essere scartato"
    )

    assert "this._pendingTurn = true;" in _method(source, "_send"), (
        "il flag si alza quando il turno parte, non quando la bolla appare"
    )


def test_turn_end_reaches_the_history_invalidation_with_the_minichat_closed() -> None:
    # La guardia della minichat e' dell'officina: `_handleWsMessage` (condiviso)
    # filtra la conversazione e l'umore, poi passa qui.
    source = JENNY_JS.read_text(encoding="utf-8")
    body = _method(source, "_handleFrame")

    guard = body.split("switch (msg.event)", 1)[0]
    assert "const closing = msg.event === 'turn_end' || msg.event === 'error';" in guard
    assert "this._pendingTurn" in guard, "la guardia non conosce il flag: turn_end resta scartato"
    assert "closing && this._pendingTurn" in guard, (
        "solo gli eventi che chiudono il turno scavalcano la guardia: i delta di una bolla "
        "invisibile non servono a nessuno"
    )

    # Oltre la guardia il frame va alla macchina della madre, che chiude il
    # flag su entrambi gli esiti (turn_end ed error); il fumetto e
    # l'invalidazione stanno nel gancio. L'esecuzione vera di tutto questo e' in
    # `test_minichat_frames_client.py`.
    assert "this._handleChatStream(msg, mine);" in body
    hook = _method(source, "_beforeChatState")
    assert "this._invalidateChatHistory();" in hook
    mother = _method(MASCOT_JS.read_text(encoding="utf-8"), "_handleChatStream")
    closing = mother.split("case 'turn_end':", 1)[1]
    assert "case 'error':" in closing.split("this._pendingTurn = false;", 1)[0], (
        "il flag va chiuso su entrambi gli esiti (turn_end ed error), altrimenti resta alzato"
    )


def test_a_send_that_never_left_does_not_leave_the_flag_up() -> None:
    """Se il WebSocket non si apre (o ``sendToChat`` rifiuta) nessun
    ``turn_end`` arriverà mai: il flag resterebbe alzato per sempre e il primo
    ``turn_end`` di un turno *altrui* verrebbe attribuito a questo."""
    send = _method(JENNY_JS.read_text(encoding="utf-8"), "_send")
    catch = send.split("} catch (err) {", 1)
    assert len(catch) == 2, "il ramo d'errore di _send è sparito"
    assert "this._pendingTurn = false;" in catch[1]


def test_the_flag_is_also_closed_from_the_main_chat_stream() -> None:
    """Un turno partito dalla minichat può concludersi dopo che l'utente è
    passato nella sezione chat: lì gli eventi vengono instradati altrove, e
    senza chiusura il flag resterebbe alzato a tempo indeterminato."""
    body = _method(MASCOT_JS.read_text(encoding="utf-8"), "_handleChatStream")
    assert "this._pendingTurn = false;" in body
