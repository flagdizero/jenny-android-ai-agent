"""Il contratto della pagina Quaderni, «con chi parli»: dove sta e come se ne esce.

Grep e struttura, non comportamento: le righe che scrive hanno il loro banco in
`test_casa_who_client.py`. Qui stanno le cose che si rompono in silenzio — un
markup che smette di essere un comando, una via d'uscita che sparisce, una
stringa inglese cablata nel JS.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "jenny" / "templates" / "ui"
INDEX = UI / "index.html"
WHO_JS = UI / "assets" / "casa-who.js"
APP_JS = UI / "assets" / "casa-app.js"
CSS = UI / "assets" / "casa-style.css"
I18N = UI / "assets" / "i18n"


def _member(source: str, name: str) -> str:
    m = re.search(rf"\n  (?:async )?{re.escape(name)}\([^)]*\)\s*\{{(.*?)\n  \}}", source, re.S)
    assert m, f"{name} non trovato"
    return m.group(1)


# ── E' una pagina ───────────────────────────────────────────────────────────


def test_the_notebooks_are_a_page_of_the_home() -> None:
    """Fino al 23/09/2026 era la tendina del titolo; da allora e' la pagina
    Quaderni della pista (`.agent/pagine-in-alto-plan.md`). Il pannello si
    disegna dentro il suo pannello, e si rilegge quando ci arrivi."""
    html = INDEX.read_text(encoding="utf-8")
    pagina = html.split('data-pagina="quaderni"', 1)[1].split('data-pagina="impostazioni"', 1)[0]
    assert 'id="casa-quaderni"' in pagina, "la pagina Quaderni non ha dove disegnarsi"
    app = APP_JS.read_text(encoding="utf-8")
    assert "new WhoPanel(document.getElementById('casa-quaderni')" in app
    assert "this.pagine.registra('quaderni', { accendi: () => this.who.mostra() });" in app


def test_the_old_dropdown_left_nothing_behind() -> None:
    """Un titolo che apre una tendina che non c'e' e' una porta disegnata sul
    muro; un `<dialog>` che nessuno apre e' codice che chi legge crede vivo."""
    html = INDEX.read_text(encoding="utf-8")
    assert 'id="casa-who"' not in html, "il titolo e' tornato un comando"
    assert not re.search(r"<h1>\s*<button", html), "c'e' di nuovo un bottone nel titolo"
    src = WHO_JS.read_text(encoding="utf-8")
    for resto in ("showModal", "createElement('dialog')", "::backdrop", "aria-expanded"):
        assert resto not in src, f"{resto}: il pannello e' ancora una tendina"
    assert ".casa-who::backdrop" not in CSS.read_text(encoding="utf-8")


# ── Le vie d'uscita ─────────────────────────────────────────────────────────


def test_back_leaves_the_notebook_only_after_the_overlays() -> None:
    """Una pressione, una cosa sola. Chiudere la scheda di un quaderno *e*
    uscire dal quaderno con lo stesso tasto farebbe sparire due cose per un
    gesto — e la seconda senza che nessuno l'abbia chiesta."""
    body = _member(APP_JS.read_text(encoding="utf-8"), "handleHardwareBack")
    assert "if (this._closeOverlays()) return;" in body, "gli strati non hanno più la precedenza"
    assert "projectNameOf" in body, "Indietro non riporta più a casa da un quaderno"


def test_a_switch_releases_the_turn_that_was_running() -> None:
    """Il legame che il banco dello scambio non può esercitare: lo fa `init()`,
    e senza, tutto quel che aspetta un `turn_end` — la faccia di Jenny, la riga
    di lavoro, il bottone Ferma — resta ad aspettarne uno che è già stato
    scartato."""
    body = _member(APP_JS.read_text(encoding="utf-8"), "init")
    assert "sessionManager.addEventListener('chat:switch'" in body
    assert "_releaseTurn()" in body


def test_the_house_has_the_dialogs_it_needs_to_make_a_notebook() -> None:
    """«Nuovo quaderno» fa due domande, e i tre modali vivevano nel markup
    dell'officina: in casa `document.getElementById('oc-prompt-dialog')` sarebbe
    stato `null`, e `promptDialog` torna `false` quando non trova il suo nodo —
    cioè il giro si sarebbe annullato da sé, in silenzio, per sempre.

    Ora il markup se lo porta il modulo e lo monta all'import. Se quella
    chiamata sparisce, in casa non si crea più niente e nessun test di
    comportamento se ne accorge: girano tutti su un DOM finto.
    """
    dialog = (UI / "assets" / "shared" / "dialog.js").read_text(encoding="utf-8")
    assert re.search(r"(?m)^mountDialogs\(\);$", dialog), (
        "il markup dei modali non viene più montato: in casa i dialoghi non esistono"
    )
    assert 'id="oc-prompt-dialog"' in dialog and 'id="oc-confirm-dialog"' in dialog


def test_a_tapped_alert_lands_in_the_personal_conversation() -> None:
    """La copia websocket di un avviso proattivo va **sempre** alla chat
    personale (il fan-out di `runtime/delivery.py` ce la mette d'ufficio):
    dentro un quaderno quell'avviso non c'è, e aprire "la chat" senza tornare a
    casa aprirebbe la stanza sbagliata per una notifica appena toccata."""
    body = _member(APP_JS.read_text(encoding="utf-8"), "openChat")
    assert "this.switchConversation(null)" in body


# ── Le parole ───────────────────────────────────────────────────────────────


def test_every_word_on_screen_comes_from_the_translations() -> None:
    """Nessun testo cablato: la regola di AGENTS.md non ha eccezioni, e questo
    è codice nuovo. Un `textContent` può ricevere solo una traduzione o un
    dato (il nome di un quaderno, la sua data).

    L'unica eccezione è un **segno**, non una parola: il fiore di Jenny, che non
    si traduce e che l'officina disegna identico (`chat-identity-flower`,
    `dock-flower`). Tradurlo non vorrebbe dire niente; metterlo nei file di
    lingua vorrebbe dire due posti da cui può divergere.
    """
    src = WHO_JS.read_text(encoding="utf-8")
    for line in src.splitlines():
        m = re.search(r"\.textContent\s*=\s*(.+);", line)
        if not m:
            continue
        value = m.group(1)
        if value == "'✿'":
            continue
        assert not re.match(r"^['\"`]", value), f"stringa cablata a schermo: {line.strip()}"
    assert src.count("'✿'") == 1, "il fiore è un segno solo, in un punto solo"


def test_the_panel_speaks_both_languages() -> None:
    words = {}
    for locale in ("it", "en"):
        data = json.loads((I18N / f"{locale}.json").read_text(encoding="utf-8"))
        section = data["casa"]["who"]
        for key in ("title", "personal", "notebooks", "none", "loadFailed"):
            assert section.get(key, "").strip(), f"casa.who.{key} manca in {locale}.json"
        words[locale] = section
    assert words["it"] != words["en"], "una delle due lingue non è stata tradotta"


def test_the_notebooks_borrow_the_words_the_workshop_already_has() -> None:
    """Le date relative e le note delle cartelle non apribili esistono già e
    sono già tradotte: una seconda copia sarebbe una seconda cosa da tenere
    allineata per dire la stessa identica frase."""
    src = WHO_JS.read_text(encoding="utf-8")
    for key in ("scope.loading", "scope.unopenableSection", "scope.invalidName"):
        assert f"'{key}'" in src, f"{key} non è più quella dell'officina"
    assert "UNOPENABLE_HINT_KEYS" in src, "la mappa dei motivi non è più quella condivisa"
