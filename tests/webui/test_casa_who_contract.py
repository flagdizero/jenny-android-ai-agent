"""Il contratto della tendina «con chi parli»: come si apre e come si chiude.

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


# ── Il titolo è il comando ──────────────────────────────────────────────────


def test_the_title_is_what_opens_the_panel() -> None:
    """Il chevron senza la tendina era la bugia che questo lavoro toglie."""
    html = INDEX.read_text(encoding="utf-8")
    m = re.search(r"<h1>\s*(<button[^>]*id=\"casa-who\".*?</button>)\s*</h1>", html, re.S)
    assert m, "il comando non è più il titolo (o non è più dentro l'h1)"
    button = m.group(1)
    assert 'aria-haspopup="dialog"' in button
    assert 'aria-expanded="false"' in button, "lo stato iniziale deve dire che è chiusa"
    assert "ti-chevron-down" in button, "senza chevron niente dice che si apre"
    assert 'class="casa-who-name"' in button, (
        "il nome sta in uno span suo: è da lì che il pannello lo legge, "
        "e senza si porterebbe dentro anche il testo dell'icona"
    )


def test_the_button_is_inside_the_heading_and_not_the_other_way_round() -> None:
    """Un `<h1>` dentro un `<button>` non è markup valido: il bottone accetta
    solo contenuto di frase. La tavola usa un `<a>`, che è trasparente; qui
    serve un comando, e l'unico annidamento valido è questo."""
    html = INDEX.read_text(encoding="utf-8")
    assert not re.search(r"<button[^>]*>\s*<h1", html), "h1 dentro button: markup non valido"


# ── Le vie d'uscita ─────────────────────────────────────────────────────────


def test_hardware_back_closes_the_panel_first() -> None:
    """Il guscio nativo intercetta Indietro prima della WebView, quindi il
    `cancel` del `<dialog>` non arriva mai: se non lo chiude questo metodo, sul
    telefono il tasto non lo chiude nessuno.

    E per primo: `showModal()` mette il pannello nel top layer, cioè sopra
    tutto — compresa l'immagine ingrandita.
    """
    body = _member(APP_JS.read_text(encoding="utf-8"), "handleHardwareBack")
    chiusura = body.index("this.who.close()")
    lightbox = body.index("image-lightbox")
    assert chiusura < lightbox, "il pannello va chiuso prima dello strato che gli sta sotto"


def test_the_veil_and_escape_are_both_wired() -> None:
    src = WHO_JS.read_text(encoding="utf-8")
    assert "if (e.target === dialog) this.close();" in src, "il velo non chiude più"
    assert "if (e.key === 'Escape') this.close();" in src, "Esc non chiude più"


def test_the_panel_is_a_modal_dialog() -> None:
    """Il top layer non è un vezzo: in casa la mascotte è disegnata sopra il
    resto, e senza `showModal()` il pannello dovrebbe contenderle uno
    `z-index`."""
    assert "showModal()" in WHO_JS.read_text(encoding="utf-8")


def test_the_veil_has_a_rule_of_its_own() -> None:
    css = CSS.read_text(encoding="utf-8")
    assert re.search(r"\.casa-who::backdrop\s*\{[^}]*background:", css), (
        "senza velo il pannello galleggia su uno schermo che sembra ancora vivo"
    )


# ── Le parole ───────────────────────────────────────────────────────────────


def test_every_word_on_screen_comes_from_the_translations() -> None:
    """Nessun testo cablato: la regola di AGENTS.md non ha eccezioni, e questo
    è codice nuovo. Un `textContent` può ricevere solo una traduzione o un
    dato (il nome di un quaderno, la sua data)."""
    src = WHO_JS.read_text(encoding="utf-8")
    for line in src.splitlines():
        m = re.search(r"\.textContent\s*=\s*(.+);", line)
        if not m:
            continue
        value = m.group(1)
        assert not re.match(r"^['\"`]", value), f"stringa cablata a schermo: {line.strip()}"


def test_the_panel_speaks_both_languages() -> None:
    words = {}
    for locale in ("it", "en"):
        data = json.loads((I18N / f"{locale}.json").read_text(encoding="utf-8"))
        section = data["casa"]["who"]
        for key in ("open", "title", "personal", "notebooks", "none", "loadFailed"):
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
