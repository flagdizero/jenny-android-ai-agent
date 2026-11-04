"""Gli identificatori di ``shared/`` sono in inglese, come dice AGENTS.md.

Dopo la fase 2 ne restavano alcuni in
italiano — le chiavi i18n ``skills.integrataBloccata``, ``skills.tuaBloccata``,
``skills.integrate``, il campo ``_annunciate``, il valore ``'piena'`` dello
stato di una pagina, le variabili ``visto`` e ``su``. Nessuno era persistito
ne' sul filo (verificato: niente ``localStorage``, ``/api/``, ``postMessage``),
quindi si sono rinominati senza migrazione. Qui si tiene che non tornino.
"""

from __future__ import annotations

import re

import pytest
from support.js_harness import ASSETS, locale

_GONE = {
    "shared/skills-view.js": ("integrataBloccata", "tuaBloccata"),
    "mobile-settings.js": ("'skills.integrate'",),
    "shared/apps-source.js": ("_annunciate",),
    "shared/apps-actions.js": ("'piena'",),
    "shared/cron-view.js": ("const visto",),
    "shared/horizontal-swipe.js": ("const su =",),
}


@pytest.mark.parametrize("path", sorted(_GONE))
def test_the_italian_names_are_gone(path: str) -> None:
    src = (ASSETS / path).read_text(encoding="utf-8")
    for name in _GONE[path]:
        assert name not in src, f"{name} e' tornato in {path}"


def test_the_skill_keys_have_their_english_names_in_both_languages() -> None:
    for loc in ("it", "en"):
        skills = locale(loc)["skills"]
        for key in ("builtIn", "builtInLocked", "yoursLocked"):
            assert skills.get(key), (loc, key)
        assert not {k for k in skills if re.search(r"Bloccata|^integrate$", k)}, loc
