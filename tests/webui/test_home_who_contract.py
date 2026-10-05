"""Il contratto della pagina Quaderni, «con chi parli»: dove sta e come se ne esce.

Grep e struttura, non comportamento: le righe che scrive hanno il loro banco in
`test_home_who_client.py`. Qui stanno le cose che si rompono in silenzio — un
markup che smette di essere un comando, una via d'uscita che sparisce, una
stringa inglese cablata nel JS.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from support.js_harness import member

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "jenny" / "templates" / "ui"
INDEX = UI / "index.html"
WHO_JS = UI / "assets" / "home-who.js"
APP_JS = UI / "assets" / "home-app.js"
CSS = UI / "assets" / "home-style.css"
THEMES_CSS = UI / "assets" / "mobile-style.css"
I18N = UI / "assets" / "i18n"
THEMES = ("chanel", "synthwave", "kyoto", "sticker", "comic", "y2k", "stone")


def _member(source: str, name: str) -> str:
    return member(source, name, prefixes=("async ",), body_only=True)


# ── E' una pagina ───────────────────────────────────────────────────────────


def test_the_notebooks_are_a_page_of_the_home() -> None:
    """Fino al 23/09/2026 era la tendina del titolo; da allora e' la pagina
    Quaderni della pista. Il pannello si
    disegna dentro il suo pannello, e si rilegge quando ci arrivi."""
    html = INDEX.read_text(encoding="utf-8")
    page = html.split('data-page="notebooks"', 1)[1].split('data-page="settings"', 1)[0]
    assert 'id="home-notebooks"' in page, "la pagina Quaderni non ha dove disegnarsi"
    app = APP_JS.read_text(encoding="utf-8")
    assert "new WhoPanel(document.getElementById('home-notebooks')" in app
    assert "this.homePages.register('notebooks', { activate: () => this.who.show() });" in app


def test_a_new_notebook_is_a_round_button_that_does_not_scroll() -> None:
    """Il + tondo sta **nel pannello** della pagina e non nell'elenco che
    scorre: e' tutto il motivo per cui ha preso il posto della riga. Si vede
    anche con zero quaderni — e' li' che serve di piu' — e crea come prima."""
    html = INDEX.read_text(encoding="utf-8")
    page = html.split('data-page="notebooks"', 1)[1].split('data-page="settings"', 1)[0]
    assert 'id="home-notebooks-new"' in page, "la pagina Quaderni non ha il +"
    list = page.split('id="home-notebooks"', 1)[1].split("</div>", 1)[0]
    assert "home-notebooks-new" not in list, "il + e' finito dentro l'elenco che scorre"
    app = APP_JS.read_text(encoding="utf-8")
    assert "getElementById('home-notebooks-new')" in app
    assert "addEventListener('click', () => this.createNotebook())" in app
    css = CSS.read_text(encoding="utf-8")
    rule = css.split("\n.home-notebooks-new {", 1)[1].split("}", 1)[0]
    assert "position: absolute" in rule and "z-index" not in rule
    css_list = css.split("\n.home-notebooks {", 1)[1].split("}", 1)[0]
    assert "max(88px" in css_list, "l'ultimo quaderno finisce sotto il +"


def test_the_old_dropdown_left_nothing_behind() -> None:
    """Un titolo che apre una tendina che non c'e' e' una porta disegnata sul
    muro; un `<dialog>` che nessuno apre e' codice che chi legge crede vivo."""
    html = INDEX.read_text(encoding="utf-8")
    assert 'id="home-who"' not in html, "il titolo e' tornato un comando"
    assert not re.search(r"<h1>\s*<button", html), "c'e' di nuovo un bottone nel titolo"
    src = WHO_JS.read_text(encoding="utf-8")
    for rest in ("showModal", "createElement('dialog')", "::backdrop", "aria-expanded"):
        assert rest not in src, f"{rest}: il pannello e' ancora una tendina"
    assert ".home-who::backdrop" not in CSS.read_text(encoding="utf-8")


# ── Le vie d'uscita ─────────────────────────────────────────────────────────


def test_back_leaves_the_notebook_only_after_the_overlays() -> None:
    """Una pressione, una cosa sola. Chiudere la scheda di un quaderno *e*
    uscire dal quaderno con lo stesso tasto farebbe sparire due cose per un
    gesto — e la seconda senza che nessuno l'abbia chiesta.

    Dal 05/10/2026 un quaderno si apre sulla sua bacheca, e Indietro ne esce
    come da ogni stanza, dentro `goBackOneRoom`: qui resta l'ordine, gli
    strati prima di tutto."""
    src = APP_JS.read_text(encoding="utf-8")
    body = _member(src, "handleHardwareBack")
    assert "if (this._closeOverlays()) return;" in body, "gli strati non hanno più la precedenza"
    assert body.index("_closeOverlays") < body.index("goBackOneRoom"), "le stanze prima degli strati"


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
    dato (il nome di un quaderno, la sua data, un numero).

    Fino al 05/10/2026 c'era un'eccezione, il fiore di Jenny sulla riga
    personale: la riga è uscita dalla pagina, e il fiore con lei.
    """
    src = WHO_JS.read_text(encoding="utf-8")
    for line in src.splitlines():
        m = re.search(r"\.textContent\s*=\s*(.+);", line)
        if not m:
            continue
        assert not re.match(r"^['\"`]", m.group(1)), f"stringa cablata a schermo: {line.strip()}"


def test_the_panel_speaks_both_languages() -> None:
    words = {}
    for locale in ("it", "en"):
        data = json.loads((I18N / f"{locale}.json").read_text(encoding="utf-8"))
        section = data["home"]["who"]
        for key in ("none", "loadFailed", "search",
                    "clearSearch", "inUse", "archive", "archiveHint", "inArchive", "sortRecent",
                    "sortAz", "sortLabel", "results", "noResult", "create", "emptyHint",
                    "pages", "pagesShort", "plateKeys", "newNotebook"):
            assert section.get(key, "").strip(), f"home.who.{key} manca in {locale}.json"
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


# ── La vetrina (05/10/2026) ─────────────────────────────────────────────────


def test_the_search_and_the_plate_stay_out_of_the_list_that_scrolls() -> None:
    """Come il +: la ricerca resta in cima e la targhetta in fondo mentre le
    mensole scorrono."""
    html = INDEX.read_text(encoding="utf-8")
    page = html.split('data-page="notebooks"', 1)[1].split('data-page="settings"', 1)[0]
    listed = page.split('id="home-notebooks"', 1)[1].split("</div>", 1)[0]
    for el in ('id="home-notebooks-search"', 'id="home-notebooks-plate"'):
        assert el in page, f"{el} non e' nella pagina Quaderni"
        assert el not in listed, f"{el} e' finito dentro l'elenco che scorre"
    assert page.index('id="home-notebooks-search"') < page.index('id="home-notebooks"')
    app = APP_JS.read_text(encoding="utf-8")
    for el in ("home-notebooks-search", "home-notebooks-search-clear", "home-notebooks-plate"):
        assert f"getElementById('{el}')" in app, f"il guscio non passa {el} al pannello"


def _theme_blocks() -> dict[str, dict[str, str]]:
    """Le tele per tema, come le vede il browser: `:root` (Chanel) sotto, il
    blocco del tema sopra."""
    css = THEMES_CSS.read_text(encoding="utf-8")
    section = css.split("/* ── Le tele dei quaderni ──", 1)[1].split("/* ── Reset ── */", 1)[0]
    blocks = dict(re.findall(r'(:root|\[data-theme="(?:\w+)"\])\s*\{([^}]*)\}', section))
    root = dict(re.findall(r"--(spine-[\w-]+|shelf):\s*([^;]+);", blocks[":root"]))
    out = {}
    for theme in THEMES:
        values = dict(root)
        own = blocks.get(f'[data-theme="{theme}"]', "")
        values.update(re.findall(r"--(spine-[\w-]+|shelf):\s*([^;]+);", own))
        out[theme] = values
    return out


def _luminance(hex_color: str) -> float:
    n = int(hex_color.lstrip("#"), 16)
    channels = []
    for v in ((n >> 16) & 255, (n >> 8) & 255, n & 255):
        c = v / 255
        channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def test_every_theme_has_six_cloths_each_with_its_ink() -> None:
    """Sei tele per tema, ognuna col suo inchiostro: `spineOf` sceglie fra
    sei, e una tela che manca in un tema farebbe una copertina trasparente
    proprio li'."""
    spines = (UI / "assets" / "shared" / "spines.js").read_text(encoding="utf-8")
    assert re.search(r"(?m)^export const SPINES = 6;$", spines), "spineOf non sceglie piu' fra sei"
    for theme, values in _theme_blocks().items():
        for n in range(1, 7):
            assert re.fullmatch(r"#[0-9a-f]{6}", values.get(f"spine-{n}", "")), f"{theme}: --spine-{n}"
            assert values.get(f"spine-{n}-ink") in ("#17141c", "#fbf8f2"), f"{theme}: --spine-{n}-ink"


def test_a_notebooks_name_reads_on_its_cloth_in_every_theme() -> None:
    """Il nome sta a 14 px sulla tela: testo normale, quindi 4,5:1. Il conto
    si rifa' qui, cosi' ritoccare la tinta di un tema lo dice invece di
    lasciarlo scoprire a chi guarda."""
    for theme, values in _theme_blocks().items():
        for n in range(1, 7):
            cloth, ink = _luminance(values[f"spine-{n}"]), _luminance(values[f"spine-{n}-ink"])
            hi, lo = max(cloth, ink), min(cloth, ink)
            ratio = (hi + 0.05) / (lo + 0.05)
            assert ratio >= 4.5, f"{theme} --spine-{n}: {ratio:.2f}:1 sotto 4,5"


def test_the_cover_wears_the_theme_finish_not_its_own() -> None:
    """La copertina non decide filo, ombra, angoli, bagliore, lucido e storta:
    li legge dal tema. Un valore scritto qui varrebbe per sette temi che non
    si somigliano."""
    css = CSS.read_text(encoding="utf-8")
    cover = css.split("\n.home-who-cover {", 1)[1].split("}", 1)[0]
    for token in ("--spine-edge", "--spine-radius", "--spine-shadow", "--spine-glow",
                  "--spine-gloss", "--spine-jitter", "--cover", "--cover-ink"):
        assert f"var({token}" in cover, f"la copertina non legge {token}"
    shelf = css.split("\n.home-who-shelf {", 1)[1].split("}", 1)[0]
    assert "var(--shelf)" in shelf, "il ripiano non e' piu' del tema"
