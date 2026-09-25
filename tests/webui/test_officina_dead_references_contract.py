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

SORGENTI = sorted(
    [*ASSETS.glob("mobile-*.js"), *(ASSETS / "shared").glob("*.js"), *(ASSETS / "apps").glob("*.js")]
)


def _codice(testo: str) -> str:
    """Il sorgente senza commenti. Grezzo ma sufficiente: ``//`` dopo ``:`` o
    dentro una stringa e' un indirizzo, non un commento."""
    return re.sub(r"/\*.*?\*/|(?<![:'\"`\\])//[^\n]*", "", testo, flags=re.S)


MORTI = (
    r"controllers\??\.apps\b",
    r"switchMode\(\s*['\"]apps['\"]",
    r"view\s*===\s*['\"]apps['\"]",
    r"_updateSidebarTitles",
    r"_navStateFor\([^)]*,",
)


@pytest.mark.parametrize("sorgente", SORGENTI, ids=lambda p: p.name)
def test_no_code_talks_to_a_removed_mode(sorgente) -> None:
    codice = _codice(sorgente.read_text(encoding="utf-8", errors="replace"))
    trovati = [m for m in MORTI if re.search(m, codice)]
    assert not trovati, f"{sorgente.name}: {trovati}"


@pytest.mark.parametrize("sorgente", SORGENTI, ids=lambda p: p.name)
def test_no_comment_points_at_a_line_number_of_another_file(sorgente) -> None:
    testo = sorgente.read_text(encoding="utf-8", errors="replace")
    righe = re.findall(r"\b[\w-]+\.(?:css|js|html|py):\d+\b", testo)
    assert not righe, f"{sorgente.name} rimanda a righe che si spostano: {righe}"


def test_the_guard_sees_what_it_is_for() -> None:
    finto = _codice(
        "dismiss: () => this.controllers.apps?.handleBack() ?? false,\n"
        "if (guscio.currentMode !== 'apps') guscio.switchMode('apps', false);\n"
        "/* qui c'era controllers.apps */ const ok = 1; // switchMode('apps')\n"
        "this.replaceNav(this._navStateFor('chat', null, null));\n"
    )
    assert [m for m in MORTI if re.search(m, finto)] == [
        MORTI[0], MORTI[1], MORTI[4],
    ]
