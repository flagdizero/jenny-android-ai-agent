"""L'officina non parla piu' con cio' che se n'e' andato.

La scheda «App» e' uscita il 21/09/2026, e il codice che la chiamava e' rimasto:
``this.controllers.apps`` (sempre ``undefined``) teneva spenti Indietro e Home
sopra una mini-app e i broadcast dei pacchetti; ``switchMode('apps')`` finiva in
un «Unknown mode» a ogni Indietro; lo strumento ``ui_view`` cercava l'app aperta
con ``view === 'apps'``. Nessuno di questi fallisce ad alta voce: l'optional
chaining li rende muti.

Il banco legge il **codice** (commenti esclusi, perche' i commenti possono
raccontare cosa c'era) della WebUI dell'officina e dei moduli condivisi, e
rifiuta i riferimenti ai modi che non esistono piu'. Rifiuta anche i rimandi a
una riga di un altro file (``mobile-style.css:1617``): il file cambia e il
numero resta, a indicare una regola che li' non c'e' piu'.
"""

from __future__ import annotations

import re

import pytest
from support.js_harness import ASSETS

SOURCES = sorted(
    [*ASSETS.glob("mobile-*.js"), *(ASSETS / "shared").glob("*.js"), *(ASSETS / "apps").glob("*.js")]
)


def _code(text: str) -> str:
    """Il sorgente senza commenti. Grezzo ma sufficiente: ``//`` dopo ``:`` o
    dentro una stringa e' un indirizzo, non un commento."""
    return re.sub(r"/\*.*?\*/|(?<![:'\"`\\])//[^\n]*", "", text, flags=re.S)


DEAD = (
    r"controllers\??\.apps\b",
    r"switchMode\(\s*['\"]apps['\"]",
    r"view\s*===\s*['\"]apps['\"]",
    r"_updateSidebarTitles",
    r"_navStateFor\([^)]*,",
)


@pytest.mark.parametrize("source", SOURCES, ids=lambda p: p.name)
def test_no_code_talks_to_a_removed_mode(source) -> None:
    code = _code(source.read_text(encoding="utf-8", errors="replace"))
    found = [m for m in DEAD if re.search(m, code)]
    assert not found, f"{source.name}: {found}"


@pytest.mark.parametrize("source", SOURCES, ids=lambda p: p.name)
def test_no_comment_points_at_a_line_number_of_another_file(source) -> None:
    text = source.read_text(encoding="utf-8", errors="replace")
    rows = re.findall(r"\b[\w-]+\.(?:css|js|html|py):\d+\b", text)
    assert not rows, f"{source.name} rimanda a righe che si spostano: {rows}"


def test_the_guard_sees_what_it_is_for() -> None:
    fake = _code(
        "dismiss: () => this.controllers.apps?.handleBack() ?? false,\n"
        "if (shell.currentMode !== 'apps') shell.switchMode('apps', false);\n"
        "/* qui c'era controllers.apps */ const ok = 1; // switchMode('apps')\n"
        "this.replaceNav(this._navStateFor('chat', null, null));\n"
    )
    assert [m for m in DEAD if re.search(m, fake)] == [
        DEAD[0], DEAD[1], DEAD[4],
    ]


def test_the_stylesheet_styles_only_modes_that_exist() -> None:
    """``:root.mode-x`` la mette ``switchMode`` per i modi di
    ``controllerFactories``, e per nessun altro. Una regola per un modo che non
    esiste piu' non fa errore: resta li' a descrivere una scheda che non c'e'
    (fino al 26/09/2026 ``:root.mode-apps``, la scheda «App» uscita il 21/09)."""
    app = (ASSETS / "mobile-app.js").read_text(encoding="utf-8")
    factories = re.search(r"this\.controllerFactories = \{(.*?)\n    \};", app, re.S)
    assert factories, "controllerFactories non trovato"
    modes = set(re.findall(r"^\s*(\w+):", factories.group(1), re.M))
    assert {"chat", "brain", "hands", "memory"} <= modes, modes
    css = re.sub(r"/\*.*?\*/", "", (ASSETS / "mobile-style.css").read_text(encoding="utf-8"), flags=re.S)
    styled = set(re.findall(r":root\.mode-([\w-]+)", css))
    assert styled, "la grep sulle regole dei modi non morde piu'"
    assert styled <= modes, f"regole per modi che non esistono: {sorted(styled - modes)}"
