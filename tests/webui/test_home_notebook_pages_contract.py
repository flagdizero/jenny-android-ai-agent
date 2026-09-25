"""Il contratto delle tre stanze: come la casa ne mostra una alla volta.

Grep e struttura, non comportamento: quel che le stanze *fanno* ha i suoi
banchi (`test_home_notebook_pages_client.py`, `test_home_switch_client.py`). Qui stanno
le cose che si rompono in silenzio — un attributo che sparisce e lascia tre
stanze impilate, un `import` che diventa statico e fa pagare 280 kB a chi apre
la chat, un file che non arriva sul telefono perche' non e' nel manifest.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from jenny.utils.android_assets import _UI_MANIFEST

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "jenny" / "templates" / "ui"
INDEX = UI / "index.html"
WORKSHOP = UI / "workshop.html"
ASSETS = UI / "assets"
APP_JS = ASSETS / "home-app.js"
MAP_JS = ASSETS / "home-map.js"
CSS = ASSETS / "home-style.css"
I18N = ASSETS / "i18n"


# ── Una stanza alla volta ───────────────────────────────────────────────────


def test_the_shell_says_which_room_is_on_from_the_first_frame() -> None:
    """L'attributo e' scritto anche nell'HTML, non solo dal JS.

    Senza, il primo frame mostra le tre stanze impilate — la conversazione, le
    pagine e il lettore uno sotto l'altro — finche' `home-app.js` non e' stato
    valutato. Non e' un lampo teorico: e' il motivo per cui le sezioni non
    hanno piu' `hidden`, che quel ruolo lo copriva a meta'.
    """
    html = INDEX.read_text(encoding="utf-8")
    # `data-page` si e' aggiunto accanto il 22 settembre 2026, per la stessa
    # ragione: su una pagina di lato la vista e' ancora `chat`, e le regole
    # scritte solo su `data-view` non la distinguono.
    assert re.search(r'<main class="home-shell" data-view="chat"', html), (
        "il guscio non nasce piu' dichiarando la stanza attiva"
    )
    css = CSS.read_text(encoding="utf-8")
    for room in ("pages", "reader"):
        assert f".home-shell[data-view='{room}']" in css, f"la stanza {room} non ha la sua regola"
    # Dal 22 settembre 2026 la chat sta dentro un pannello della pista, e quel
    # che si nasconde e' la pista: una riga invece delle sei che nominavano
    # filo, stato vuoto, riga di lavoro, stato del filo, allegati e composer.
    # Chi ne avesse dimenticata una l'avrebbe lasciata a occupare spazio dentro
    # una stanza. L'invariante e' la stessa — fuori dalla conversazione il
    # composer non c'e' — e adesso ha un posto solo in cui rompersi.
    # Dal 23/09/2026 si nasconde la vetrina, che contiene la pista: i pallini
    # non ci sono piu', e la fila dei nomi ha la sua regola accanto a quella
    # dell'intestazione delle stanze.
    assert (
        ".home-shell:not([data-view='chat']) .home-showcase { display: none; }"
    ) in css, (
        "il composer resta a schermo fuori dalla conversazione"
    )
    html_track = html.split('class="home-track"', 1)[1]
    assert "home-composer" in html_track.split("</main>", 1)[0], (
        "il composer e' uscito dalla pista: la regola sopra non lo copre piu'"
    )


def test_the_rooms_after_the_chat_are_not_in_the_flow_by_default() -> None:
    """`display:none` sulle sezioni e non `hidden`: la regola della vista le
    accende, e due meccanismi per la stessa cosa divergono.

    Il banco non nomina le stanze: le prende da chi le accende, cosi' la
    quinta non puo' essere accesa e dimenticata qui.
    """
    css = CSS.read_text(encoding="utf-8")
    # `[data-view='chat']` non accende una stanza: e' la regola che nella
    # conversazione **spegne** l'intestazione delle stanze.
    active = set(re.findall(r"\.home-shell\[data-view='(?!chat')\w+'\] (\.home-[\w-]+)", css))
    assert active, "nessuna stanza nel foglio: la grep non morde piu'"
    off = set()
    for selectors, body in re.findall(r"([^{}]+)\{([^}]*)\}", css):
        if "display: none" not in body:
            continue
        # L'ultima riga di ogni pezzo: davanti al primo selettore di un blocco
        # c'e' il commento che lo spiega, e quello non e' un selettore.
        off |= {s.strip().splitlines()[-1].strip() for s in selectors.split(",") if s.strip()}
    missing = active - off
    assert not missing, f"stanze che partono dentro il flusso: {missing}"


# ── I 280 kB che si pagano solo aprendo la mappa ────────────────────────────


def test_d3_arrives_with_the_map_and_not_with_the_house() -> None:
    """Il guscio della casa si porta dietro `marked` e `DOMPurify` e basta, e
    il commento in cima a `index.html` dice perche'. D3 pesa 279.706 byte: un
    `import` statico li farebbe pagare a chiunque apra la chat.
    """
    app = APP_JS.read_text(encoding="utf-8")
    assert "import('./home-map.js')" in app, (
        "la mappa non si carica piu' su richiesta"
    )
    assert not re.search(r"(?m)^import .*home-map\.js", app), (
        "home-map.js e' tornato un import statico: D3 lo paga tutta la casa"
    )
    html = INDEX.read_text(encoding="utf-8")
    assert "d3" not in html, "il guscio della casa si e' preso D3 nel <head>"
    assert "d3.min.js" in MAP_JS.read_text(encoding="utf-8")


def test_every_new_asset_is_in_the_manifest() -> None:
    """Un percorso giusto ma fuori manifest non arriva sul telefono: il
    gateway ricade sulla copia su disco, che su Android e' un mirror e non e'
    autoritativa. Il difetto si vede solo sul dispositivo."""
    for name in ("home-notebook-pages.js", "home-reader.js", "home-map.js"):
        rel = f"assets/{name}"
        assert (ASSETS / name).is_file(), f"{name} non esiste"
        assert rel in _UI_MANIFEST, f"{rel} non e' nel manifest"


# ── L'intestazione che cambia stanza ────────────────────────────────────────


def test_the_two_ways_out_of_the_pages_do_the_same_thing() -> None:
    """La tavola disegna l'occhiello in alto a sinistra e la pastiglia in
    basso a destra, e fanno la stessa cosa: si esce da dove stai guardando."""
    html = INDEX.read_text(encoding="utf-8")
    assert 'id="home-back"' in html and 'id="home-talk"' in html
    app = APP_JS.read_text(encoding="utf-8")
    assert "this.backBtn?.addEventListener" in app
    assert "this.talkBtn?.addEventListener" in app


def test_the_rooms_have_a_head_and_the_conversation_has_the_row() -> None:
    """Mai tutte e due. Nella conversazione la fila dice gia' dove sei; nelle
    stanze l'intestazione dice dove sei **e da dove si torna**, e la fila li'
    porterebbe a cambiare pagina da dentro una stanza."""
    css = CSS.read_text(encoding="utf-8")
    assert (
        ".home-shell:not([data-view='chat']) .home-strip,\n"
        ".home-shell[data-view='chat'] .home-head { display: none; }"
    ) in css
    html = INDEX.read_text(encoding="utf-8")
    assert html.index('id="home-strip"') < html.index('class="home-head"') < html.index('class="home-showcase"')


# ── Le parole ───────────────────────────────────────────────────────────────


def test_the_rooms_speak_both_languages() -> None:
    words = {}
    for locale in ("it", "en"):
        data = json.loads((I18N / f"{locale}.json").read_text(encoding="utf-8"))
        section = data["home"]["notebookPages"]
        for key in ("open", "countOne", "countMany", "talk",
                    "tabList", "tabMap", "loading", "none", "noMatch", "failed"):
            assert section.get(key, "").strip(), f"home.notebookPages.{key} manca in {locale}.json"
        assert data["home"]["map"]["noLinks"].strip()
        assert data["home"]["reader"]["failed"].strip()
        assert "{count}" in section["countMany"], "il conteggio non interpola niente"
        assert "{count}" not in section["countOne"], (
            "«1 pagina» non ha bisogno del numero: scriverlo la fa leggere «1 1 pagina»"
        )
        words[locale] = section
    assert words["it"] != words["en"], "una delle due lingue non e' stata tradotta"


def test_the_groups_are_the_three_the_server_actually_sends() -> None:
    """Tre, e la legenda dell'officina aveva ragione.

    `summaries/` sembra un quarto gruppo — ha un colore
    (`.legend-dot.summaries`) e `mobile-graph.js::sanitizeGroup` lo nomina — ma
    da `/api/graph` non esce: `WIKI_PAGES_SKIP_DIRS` lo toglie a tutte e
    quattro le camminate, perche' e' il livello di citazione del pattern di
    ricerca. Misurato sul grafo vero di una wiki che ce l'ha: sei nodi su
    sette, e il settimo era quello.

    Questo banco esiste perche' l'errore l'ho fatto: avevo aggiunto la parola e
    una quarta riga alla legenda, "riparando" un conto che era giusto.
    """
    from jenny.utils.wiki_paths import WIKI_PAGES_SKIP_DIRS

    assert "summaries" in WIKI_PAGES_SKIP_DIRS, (
        "la regola e' cambiata: allora i gruppi diventano quattro e questo "
        "banco va aggiornato insieme a GROUPS in home-notebook-pages.js"
    )
    src = (ASSETS / "home-notebook-pages.js").read_text(encoding="utf-8")
    m = re.search(r"export const GROUPS = \[(.*?)\];", src)
    assert m and "summaries" not in m.group(1), (
        "la casa elenca un gruppo che il server non le manda"
    )
    legend = re.findall(r'data-i18n="graph\.(\w+)"', WORKSHOP.read_text(encoding="utf-8"))
    assert "summaries" not in legend, "la legenda dell'officina ha una riga di troppo"


def test_the_search_box_borrows_the_words_the_workshop_already_has() -> None:
    """«Cerca nelle pagine…» esiste gia' ed e', parola per parola, quel che la
    tavola scrive nel campo."""
    src = (ASSETS / "home-notebook-pages.js").read_text(encoding="utf-8")
    assert "'graph.searchPlaceholder'" in src
    for key in ("graph.entities", "graph.concepts", "graph.other"):
        assert f"'{key}'" in src, f"{key} non e' piu' quella dell'officina"


def test_no_sentence_is_hardcoded_in_the_rooms() -> None:
    """La regola di AGENTS.md non ha eccezioni, e questo e' codice nuovo: un
    `textContent` puo' ricevere solo una traduzione o un dato."""
    for name in ("home-notebook-pages.js", "home-reader.js", "home-map.js"):
        for line in (ASSETS / name).read_text(encoding="utf-8").splitlines():
            m = re.search(r"\.textContent\s*=\s*(.+);", line)
            if not m:
                continue
            assert not re.match(r"^['\"`]", m.group(1)), (
                f"{name}: stringa cablata a schermo: {line.strip()}"
            )


# ── I pallini devono dividere, in tutti i temi ──────────────────────────────


def _tokens(block: str) -> dict[str, str]:
    return {k: v.strip() for k, v in re.findall(r"--([a-z-]+):\s*([^;]+);", block)}


def _themes() -> dict[str, dict[str, str]]:
    """I token di colore per tema, **con la base sotto**.

    Un tema ridefinisce solo cio' che cambia: `chanel` non dichiara `--ok` e se
    lo eredita da `:root`. Leggere il solo blocco del tema direbbe «non
    definito» per meta' dei token, che e' il modo in cui un banco di colori
    diventa un banco di niente.
    """
    css = (ASSETS / "mobile-style.css").read_text(encoding="utf-8")
    base: dict[str, str] = {}
    for m in re.finditer(r"(?m)^:root\s*\{(.*?)\n\}", css, re.S):
        base.update(_tokens(m.group(1)))
    out: dict[str, dict[str, str]] = {}
    for m in re.finditer(r'\[data-theme="([a-z-]+)"\]\s*\{(.*?)\n\}', css, re.S):
        out[m.group(1)] = {**base, **_tokens(m.group(2))}
    return out


def _rgb(value: str, over: tuple[int, int, int] = (0, 0, 0)):
    """Il colore che si vede, non quello che c'e' scritto.

    Un token puo' essere `rgba(...)`: `--text-faint` lo e' in meta' dei temi, e
    confrontare la sua tripletta ignorando l'alfa direbbe che un grigio al 32%
    e' bianco. Si compone sullo sfondo del tema, che e' quel che l'occhio fa.
    """
    v = value.strip()
    m = re.fullmatch(r"#([0-9a-fA-F]{6})", v)
    if m:
        h = m.group(1)
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    m = re.fullmatch(r"rgba?\(([^)]+)\)", v)
    if not m:
        return None
    parts = [x.strip() for x in m.group(1).replace("/", " ").split(",")]
    if len(parts) < 3:
        return None
    try:
        r, g, b = (int(float(x)) for x in parts[:3])
        alpha = float(parts[3]) if len(parts) > 3 else 1.0
    except ValueError:
        return None
    return tuple(round(c * alpha + o * (1 - alpha)) for c, o in zip((r, g, b), over, strict=True))


def test_the_three_group_dots_are_telling_apart_in_every_theme() -> None:
    """Un pallino che non divide non e' informazione, e' decorazione.

    Misurato su tutti i temi: ``--accent`` e ``--error`` distano **26** in
    kyoto e 58 in pietra — indistinguibili — e ``--accent`` con ``--ok``
    coincidono (distanza 0) in chanel e in fumetto. La sola coppia che si
    separa ovunque e' ``--error`` / ``--ok``, mai sotto 101. E' per questo che
    la casa non usa i tre token della legenda del grafo.

    Questo banco vale per i temi che ci sono **e per quelli che arriveranno**:
    un tema nuovo con un accento verde non deve poter spegnere questa
    distinzione in silenzio.
    """
    css = (ASSETS / "home-style.css").read_text(encoding="utf-8")
    used = dict(
        re.findall(r"\.home-group-(\w+) \{ background: var\(--([a-z-]+)\); \}", css)
    )
    assert set(used) == {"concepts", "entities", "other"}, used

    themes = _themes()
    assert len(themes) >= 5, f"temi non letti dal foglio: {list(themes)}"
    for name, tokens in themes.items():
        background = _rgb(tokens.get("bg", "#000000")) or (0, 0, 0)
        colors = {}
        for group, token in used.items():
            value = tokens.get(token)
            assert value, f"{name}: il tema non definisce --{token}"
            rgb = _rgb(value, background)
            assert rgb, f"{name}: --{token} non si sa leggere ({value})"
            colors[group] = rgb
        pairs = [("concepts", "entities"), ("concepts", "other"), ("entities", "other")]
        for a, b in pairs:
            dist = sum((x - y) ** 2 for x, y in zip(colors[a], colors[b], strict=True)) ** 0.5
            assert dist >= 60, (
                f"tema {name}: i pallini {a} e {b} distano {dist:.0f} — a occhio "
                f"sono lo stesso colore, e il gruppo smette di dividere"
            )


def test_the_map_paints_its_nodes_with_the_same_three() -> None:
    """Elenco e mappa sono due rese della stessa risposta: un pallino verde
    deve voler dire la stessa cosa in tutte e due."""
    css = (ASSETS / "home-style.css").read_text(encoding="utf-8")
    list = dict(
        re.findall(r"\.home-group-(\w+) \{ background: var\(--([a-z-]+)\); \}", css)
    )
    map = dict(
        re.findall(r"\.home-map-nodes \.home-group-(\w+) \{ fill: var\(--([a-z-]+)\); \}", css)
    )
    assert list == map, f"elenco {list} contro mappa {map}"


def test_a_node_of_an_unforeseen_group_is_still_painted() -> None:
    """Un gruppo che i tre colori non prevedono prende il grigio di `other`.

    La mappa non passa da `sanitizeGroup` come l'elenco: il nodo porta
    `home-group-<quel che arriva>`, e senza un `fill` di ripiego SVG lo
    dipinge nero — invisibile nel tema scuro (revisione del 25/09/2026, M20).
    """
    css = (ASSETS / "home-style.css").read_text(encoding="utf-8")
    fallback = re.search(r"\n\.home-map-node \{([^}]*)\}", css)
    assert fallback, "regola .home-map-node non trovata"
    others = re.search(r"\.home-map-nodes \.home-group-other \{ fill: (var\(--[a-z-]+\)); \}", css)
    assert others, "il colore di `other` non si trova piu'"
    assert f"fill: {others.group(1)}" in fallback.group(1), (
        "un nodo di un gruppo non previsto resta col nero di default di SVG"
    )


def test_everything_the_shell_hides_by_attribute_can_actually_be_hidden() -> None:
    """`[hidden]` e' una regola del foglio del browser, e ogni regola
    dell'autore la batte a qualunque specificita'.

    Un `display:` messo su una classe la scavalca, e l'elemento resta a schermo
    con `hidden` vero: nessun errore, nessun avviso, solo una riga che non se
    ne va. E' successo con «torna alla chat», che compariva **dentro la chat**,
    e questo foglio documenta la stessa trappola per `.home-empty` da
    settembre.

    Il banco la cerca da solo: ogni classe che il guscio nasconde con
    l'attributo deve avere la sua regola — o stare dentro `.home-actions`, che
    ne ha una per i figli, con una specificita' in piu'.
    """
    app = APP_JS.read_text(encoding="utf-8")
    html = INDEX.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")

    assert ".home-actions [hidden]" in css, "la fila dei comandi ha perso la sua regola"
    strip = re.search(r'<div class="home-actions">(.*?)</div>', html, re.S)
    assert strip, "la fila dei comandi non esiste piu'"

    fields = dict(re.findall(r"this\.(\w+) = document\.getElementById\('([\w-]+)'\)", app))
    hidden = {fields[c] for c in re.findall(r"this\.(\w+)\.hidden = ", app) if c in fields}
    assert hidden, "nessun elemento nascosto per attributo: la grep non morde piu'"

    missing = []
    for el_id in sorted(hidden):
        if f'id="{el_id}"' in strip.group(1):
            continue  # coperto dalla regola della fila
        m = re.search(rf'<[^>]*id="{re.escape(el_id)}"[^>]*>', html)
        if not m:
            continue
        classes = re.search(r'class="([^"]+)"', m.group(0))
        if not classes:
            continue
        classes = classes.group(1).split()
        ha_display = any(
            re.search(rf"\.{re.escape(c)}[^{{]*\{{[^}}]*display:", css) for c in classes
        )
        has_rule = any(re.search(rf"\.{re.escape(c)}\[hidden\]", css) for c in classes)
        if ha_display and not has_rule:
            missing.append((el_id, classes))

    assert not missing, (
        "questi si nascondono con `hidden` ma hanno un `display` che lo "
        f"scavalca, e resteranno a schermo: {missing}"
    )


def test_the_note_sits_above_the_list_and_not_under_it() -> None:
    """L'elenco e' `flex: 1`: da vuoto si prende tutta l'altezza.

    Con la nota dopo, «nessuna pagina con queste parole» finiva appiccicata al
    bordo inferiore — visto sul telefono — dove sembra un piede di pagina e non
    la risposta alla ricerca appena fatta.
    """
    html = INDEX.read_text(encoding="utf-8")
    note = html.index('id="home-notebook-pages-note"')
    list = html.index('id="home-notebook-page-list"')
    assert note < list, "la nota e' tornata sotto l'elenco"


def test_the_names_are_placed_once_the_physics_stops() -> None:
    """A ogni tick vorrebbe dire far lampeggiare i nomi mentre la nuvola si
    assesta, e una misura di testo per etichetta per frame. Lo zoom non lo rifa
    perche' non serve: ingrandire e' una trasformazione del gruppo, e due
    riquadri che non si toccavano non cominciano a toccarsi."""
    src = (ASSETS / "home-map.js").read_text(encoding="utf-8")
    fine = re.search(r"this\._sim\.on\('end', \(\) => \{(.*?)\n    \}\);", src, re.S)
    assert fine, "la simulazione non ha piu' un gestore di fine"
    assert "_placeLabels" in fine.group(1), (
        "i nomi non si collocano piu' a fisica ferma"
    )
    tick = re.search(r"\.on\('tick', \(\) => \{(.*?)\n      \}\)", src, re.S)
    assert tick and "_placeLabels" not in tick.group(1), (
        "i nomi si ricollocano a ogni tick: lampeggiano, e costano una misura "
        "di testo per etichetta per frame"
    )


def test_the_physics_stops_in_a_few_seconds_and_not_in_ten() -> None:
    """I nomi si collocano a fisica ferma, quindi quanto ci mette a fermarsi e'
    quanto restano dove capita.

    Il default di D3 sono ~300 tick: cinque secondi a 60 fps, e dieci sul Titan
    con 31 nodi. Misurato con due scatti — a 5 s le etichette erano accavallate,
    a 16 s a posto.
    """
    src = (ASSETS / "home-map.js").read_text(encoding="utf-8")
    m = re.search(r"\.alphaDecay\(([\d.]+)\)", src)
    assert m, "la simulazione e' tornata al tempo di assestamento di serie"
    assert float(m.group(1)) >= 0.04, (
        f"alphaDecay {m.group(1)}: la nuvola ci mette troppo a fermarsi, e fino "
        "ad allora i nomi stanno dove capita"
    )
