"""Rifiniture dopo le nove ondate: sotto-stato di sezione, link relativi, ancore.

Quattro cose che le ondate avevano lasciato indietro perché cadevano fra due
perimetri, e che hanno in comune di essere *incoerenze fra percorsi gemelli* —
la forma di difetto che tutto il lavoro sulla navigazione esiste per togliere:

* Home smontava gli overlay ma lasciava intatto il sotto-stato delle sezioni:
  l'editor del Workspace restava montato e ``activate()`` lo riproponeva al
  rientro (è l'unico intervento del piano che nessuna delle nove ondate aveva
  fatto, rimandato tre volte fra un perimetro e l'altro).
* Il tap sulla notifica componeva ``goHome()`` + ``switchMode('chat')`` dal
  guscio nativo, lasciando la entry di radice a descrivere la vista *home*
  mentre a schermo c'era la chat.
* Un ``[nota](note.md)`` in una wiki restava inerte: il renderer mette la classe
  ``wikilink`` solo per ``[[Target]]``, quindi il modo più naturale di scrivere
  un link a mano finiva nel ramo "non apribile".
* ``_scrollToHash`` cercava l'ancora in tutto il documento mentre il gemello
  della chat era già ristretto al proprio contenitore.

Gli ultimi due punti **non hanno più un banco qui** dal 21/09/2026: guardavano
``mobile-wiki.js``, e la wiki è uscita dall'officina. Le due regole non sono
cadute, hanno cambiato casa insieme alla vista: il lettore della casa le fa
girare in node su un DOM finto (``test_casa_reader_client.py`` — un link
relativo si risolve contro la pagina che lo contiene, un'ancora resta sulla
pagina). Restano qui i primi due punti, che sono del guscio.

Asserzioni sul sorgente, nello stile del resto di ``tests/webui/``.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"


def _src(name: str) -> str:
    return (ASSETS / name).read_text(encoding="utf-8")


def _method(source: str, name: str) -> str:
    body = re.search(rf"\n  (?:async )?{name}\([^)]*\)\s*\{{(.*?)\n  \}}", source, re.S)
    assert body, f"{name} non trovato"
    return body.group(1)


def test_home_collapses_the_workspace_sub_state() -> None:
    """Il Workspace è l'unica sezione con un sotto-stato che sopravvive al
    cambio vista, ed è quella che deve esporre il collasso."""
    body = _method(_src("mobile-workspace.js"), "collapseToRoot")
    assert "this.viewMode === 'editor'" in body
    assert "this._closeEditor(" in body, "l'editor si smonta dal suo punto unico di teardown"
    assert "_resetToExplorerAt" not in body, (
        "scorciatoia che salta il guard delle modifiche non salvate"
    )
    assert "this.navigateTo('')" in body, "anche la sottocartella aperta va collassata"


def test_home_never_discards_unsaved_work() -> None:
    """Home non è una richiesta di buttare via il lavoro, e una conferma che
    spunta *sopra* la schermata home chiederebbe di un file che non è più a
    schermo: col buffer sporco l'editor resta dov'è."""
    body = _method(_src("mobile-workspace.js"), "collapseToRoot")
    dirty = re.search(r"if \(this\._dirty\)\s*return;", body)
    assert dirty, "manca l'uscita anticipata sul buffer sporco"
    assert dirty.start() < body.index("this._closeEditor("), (
        "il guard deve precedere la chiusura, non seguirla"
    )


def test_the_return_mode_is_cleared_before_collapsing() -> None:
    """``_closeEditor`` con ``_returnMode`` valorizzato *naviga* nella history
    per tornare alla sezione d'origine. Da Home non si torna indietro: si va a
    casa. Senza azzerarlo, il tasto Home produrrebbe una navigazione."""
    body = _method(_src("mobile-workspace.js"), "collapseToRoot")
    assert "this._returnMode = null;" in body
    assert body.index("this._returnMode = null;") < body.index("this._closeEditor(")


def test_open_chat_is_one_behaviour_in_one_place() -> None:
    """La chat *diventa* la radice: non è la vista home con sopra un cambio di
    tab."""
    body = _method(_src("mobile-app.js"), "openChat")
    assert "this._dismissAllOverlays()" in body
    assert "collapseToRoot?.()" in body
    assert "this.switchMode('chat', false)" in body, "niente push: si collassa, non si impila"
    assert "this._navPos = 0" in body
    assert "homeView()" not in body, "la vista home è una preferenza, la chat no"
    # Il guscio deve poter distinguere "aperta" da "bloccata dall'onboarding",
    # altrimenti cancella una notifica che l'utente non ha ancora visto.
    assert "return false;" in body and "return true;" in body


def test_the_session_popover_has_no_escape_listener_of_its_own() -> None:
    """Esc passa dalla catena, che chiude già il popover: un secondo handler è
    la divergenza fra copie che la catena esiste per eliminare."""
    chat = _src("mobile-chat.js")
    assert "_sessionInfoEscHandler" not in chat
    # Il click fuori non arriva dalla catena e resta di competenza del popover.
    assert "_sessionInfoOutsideHandler" in chat


def test_the_provider_dialog_cancel_button_respects_the_busy_guard() -> None:
    """Era l'ultima via per perdere la chiave API a metà salvataggio: Esc e il
    tasto Indietro passano dal ``cancel`` annullabile, il bottone no."""
    source = _src("mobile-settings.js")
    handler = re.search(
        r"#dlg-provider-cancel'\)\.addEventListener\('click', \(\) => \{(.*?)\n    \}\);",
        source,
        re.S,
    )
    assert handler, "handler di Annulla non trovato"
    assert "new Event('cancel', { cancelable: true })" in handler.group(1)


def test_the_discard_confirm_drops_the_soft_keyboard_first() -> None:
    """Difetto trovato solo sul dispositivo, invisibile al sorgente.

    Un ``<dialog>`` chiuso ripristina il fuoco all'elemento precedente — l'input
    di CodeMirror — e con quello risale l'IME. La pressione di Indietro
    successiva se la mangia la tastiera per richiudersi: back → conferma → back
    → conferma chiusa → back → **niente**. Sul Titan 2 si legge in ImeTracker
    (``onShown`` subito dopo la chiusura del dialog).
    """
    body = _method(_src("mobile-workspace.js"), "_confirmDiscard")
    blur = body.find("getInputField")
    assert blur != -1, "manca il blur dell'editor prima della modale"
    assert blur < body.index("confirmDialog("), (
        "il blur deve precedere l'apertura della modale, altrimenti il fuoco è già stato preso"
    )
