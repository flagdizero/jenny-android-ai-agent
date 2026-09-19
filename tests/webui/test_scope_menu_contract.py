"""La tendina dello scope sta nello schermo, scorre, e mette in cima l'ultima.

Tre guasti visti sul telefono, tutti e tre invisibili finche' i progetti erano
due:

1. il chip usciva a spigolo vivo sul tema `kyoto`, perche' prendeva
   ``--radius-seg`` — che e' il raggio dei controlli segmentati, e quel tema lo
   porta a 2px. La pillola distingue il chip (che si apre) dalle etichette
   squadrate intorno: non puo' dipendere dal tema;
2. il pannello cresceva con l'elenco e a sette wiki usciva dallo schermo
   dall'alto. Quel che spariva era la testa, cioe' la voce "personale" con cui
   si torna indietro — e non c'era modo di raggiungerla, perche' non scorreva;
3. l'ordine era quello alfabetico della discovery, mentre ogni riga stampa da
   quanto la wiki non si muove. Il progetto su cui si stava lavorando finiva
   dove capitava la sua iniziale.

Asserzioni sul sorgente, come ``test_thinking_scroll_contract.py``: la WebUI non
ha un runner JS con DOM.
"""

from __future__ import annotations

import re
from pathlib import Path

UI_DIR = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui"
CSS = UI_DIR / "assets" / "mobile-style.css"
CHIP_JS = UI_DIR / "assets" / "shared" / "scope-chip.js"
LIST_JS = UI_DIR / "assets" / "shared" / "conversation-list.js"


def _css() -> str:
    return CSS.read_text(encoding="utf-8")


def _rule(selector: str) -> str:
    """Il corpo della prima regola che *include* ``selector``.

    Non solo la regola dove il selettore è solo: da quando la tendina dei
    comandi riusa queste misure (pannello, righe, elenco che scorre) i selettori
    sono elencati insieme — ``.scope-menu,\n.commands-menu { … }`` — proprio per
    non tenerne due copie allineate a mano. È lo stesso corpo, e questi test
    guardano il corpo: pretendere il selettore da solo vorrebbe dire che
    condividere una regola fa fallire il contratto di chi la scriveva prima.
    """
    body = re.search(
        rf"^{re.escape(selector)}(?:,\n[^{{\n]+)* \{{(.*?)^\}}", _css(), re.S | re.M,
    )
    assert body, f"regola {selector} non trovata in mobile-style.css"
    return body.group(1)


def _chip() -> str:
    return CHIP_JS.read_text(encoding="utf-8")


def _method(name: str) -> str:
    body = re.search(rf"\n  (?:async )?{name}\([^)]*\)\s*\{{(.*?)\n  \}}", _chip(), re.S)
    assert body, f"{name} non trovato in scope-chip.js"
    return body.group(1)


# ── 1. Il chip e' una pillola ovunque ─────────────────────────────────────


def test_the_chip_takes_its_radius_from_a_token_no_theme_redefines() -> None:
    # Sulle sole dichiarazioni: il commento accanto nomina --radius-seg per dire
    # perche' non si usa piu'.
    radii = re.findall(r"border-radius:\s*([^;]+);", _rule(".scope-chip"))
    assert radii == ["var(--radius-pill)"], (
        f"il chip deve restare una pillola in ogni tema, non {radii}"
    )


def test_no_theme_squares_the_pill() -> None:
    """La regola sopra tiene solo finche' `--radius-pill` resta uno solo.

    Se un tema lo ridefinisce siamo daccapo, con la differenza che stavolta la
    causa e' scritta a un capo del file e l'effetto all'altro.
    """
    definitions = re.findall(r"--radius-pill:\s*([^;]+);", _css())
    assert definitions == ["999px"], (
        f"--radius-pill non e' piu' un valore unico: {definitions} — un tema puo' squadrare il chip"
    )


# ── 2. Il pannello ha un tetto, e sotto il tetto scorre ───────────────────


def test_the_menu_is_capped_and_lays_out_as_a_column() -> None:
    rule = _rule(".scope-menu")
    assert "max-height:" in rule, "senza tetto il pannello esce dallo schermo dall'alto"
    assert "flex-direction: column" in rule
    assert re.search(r"^\.scope-menu\.open[^{]*\{ display: flex; \}", _css(), re.M), (
        "con `display: block` il figlio che scorre non riceve l'altezza rimasta"
    )


def test_the_cap_is_written_with_the_tokens_it_depends_on() -> None:
    """Il tetto sottraeva un `172px` calcolato a mano.

    Quel numero era `--dock-height + 58 + --scope-row + 25` di margine, cioe' la
    stessa somma che la geometria della mascotte fa con i token
    (`bottom: calc(var(--dock-height) + 58px + var(--scope-row))`). Con la somma
    ricalcolata, ritoccare il padding del chip — cioe' `--scope-row` — muoveva la
    mascotte e lasciava il menu dov'era: il token esiste per non avere due
    misure della stessa cosa.
    """
    cap = re.search(r"max-height:\s*([^;]+);", _rule(".scope-menu"))
    assert cap, "il tetto del pannello e' sparito"
    value = cap.group(1)
    assert "var(--dock-height)" in value and "var(--scope-row)" in value, (
        f"il tetto torna a ricalcolare a mano lo spazio dei suoi vicini: {value}"
    )
    assert not re.search(r"\b172px\b", value)
    # E la somma non e' cambiata: 56 + 58 + 33 + 25 = 172.
    subtracted = re.search(r"100vh -(.*)", value)
    assert subtracted, f"lo spazio sottratto non parte piu' da 100vh: {value}"
    numbers = [int(n) for n in re.findall(r"(\d+)px", subtracted.group(1))]
    tokens = {
        name: int(re.search(rf"--{name}:\s*(\d+)px", _css()).group(1))
        for name in ("dock-height", "scope-row")
    }
    assert sum(numbers) + tokens["dock-height"] + tokens["scope-row"] == 172


def test_only_the_project_list_scrolls() -> None:
    rule = _rule(".scope-menu-scroll")
    assert "overflow-y: auto" in rule
    # Senza `min-height: 0` un figlio flex non scende sotto il suo contenuto:
    # il riquadro non scorre e il pannello sfonda il tetto.
    assert "min-height: 0" in rule, "un figlio flex senza min-height:0 non cede: niente scroll"
    assert "flex: 1 1 auto" in rule, "e' la lista che deve assorbire l'eccedenza, non il pannello"


def test_the_two_anchors_stay_out_of_the_scroller() -> None:
    """"Personale" e "nuovo" sono le due voci che si cercano senza guardare."""
    body = _method("_renderMenu")
    scroller = re.search(r"list\.className = 'scope-menu-scroll'", body)
    assert scroller, "l'elenco dei progetti non ha un contenitore suo"
    personal = body.index("scope.personalSection")
    listed = body.index("list.className")
    new = body.index("scope.newProject")
    assert personal < listed < new, "la lista che scorre deve stare in mezzo alle due voci fisse"
    for pinned in ("this._item({\n      name: this.personalLabel", "add.classList.add"):
        assert pinned in body
    # La riga di un progetto e' un contenitore (`_projectRow`: la scelta piu' il
    # tasto elimina), non piu' il bottone nudo — ma deve continuare a finire
    # dentro il riquadro che scorre, che e' quel che questo test difende.
    assert re.search(r"list\.appendChild\(this\._projectRow\(", body), (
        "i progetti vanno dentro il riquadro che scorre"
    )


def test_the_open_menu_shows_where_you_are() -> None:
    body = _method("_renderMenu")
    assert "activeEl?.scrollIntoView({ block: 'nearest' })" in body, (
        "con l'elenco piu' lungo del riquadro il progetto attivo puo' restare sotto la piega"
    )


# ── 3. Dal piu' recente ───────────────────────────────────────────────────


def test_projects_are_ordered_by_the_same_field_the_rows_print() -> None:
    """L'ordine non sta piu' nel chip: sta nel modulo che i due gusci dividono.

    `_loadProjects` ora delega a `conversation-list.js`, che fa quell'ordine una
    volta per la tendina dell'officina e per il pannello della casa. Il
    contratto e' rimasto lo stesso — *dal piu' recente, con lo spareggio sul
    nome* — e ha solo cambiato indirizzo.
    """
    src = LIST_JS.read_text(encoding="utf-8")
    body = re.search(r"function byRecent\([^)]*\)\s*\{(.*?)\n\}", src, re.S)
    assert body, "byRecent non trovato in conversation-list.js"
    assert ".sort((a, b) => (b.modified || 0) - (a.modified || 0)" in body.group(1), (
        "l'elenco deve scendere dal piu' recente, con lo stesso `modified` che ogni riga stampa"
    )
    assert "a.name.localeCompare(b.name)" in body.group(1), (
        "senza spareggio due mtime uguali danno un ordine diverso a ogni apertura"
    )
    assert "this._list.load()" in _method("_loadProjects"), (
        "il chip deve passare da li', o l'ordine verificato sopra non e' quello che usa"
    )
