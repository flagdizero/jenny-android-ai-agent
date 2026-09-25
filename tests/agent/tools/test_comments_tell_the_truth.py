"""Commenti e docstring che la revisione profonda ha trovato falsi.

Un commento falso costa piu' di nessun commento: chi lo legge ci crede. Qui si
fissano le affermazioni verificabili, perche' non tornino a divergere.
"""

from __future__ import annotations

import re

from jenny.agent.tools import loader
from jenny.agent.tools import self as self_module
from jenny.config.loader import RETIRED_KEY_PATHS
from jenny.webui.wiki_routes import WikiRoutes


def test_the_wiki_routes_docstring_names_the_routes_it_serves() -> None:
    import inspect

    sorgente = inspect.getsource(WikiRoutes.dispatch)
    servite = set(re.findall(r'path == "(/api/[^"]+)"', sorgente))
    doc = WikiRoutes.__doc__ or ""
    for rotta in servite:
        parti = rotta.removeprefix("/api/")
        assert parti in doc, f"{rotta} manca dalla docstring di WikiRoutes"
    assert "config" not in doc and "tree" not in doc


def test_the_retired_mascot_preset_really_is_retired() -> None:
    """Il commento in ``schema.py`` dice che la chiave cade alla prossima
    scrittura: vero solo finche' sta fra le ritirate."""
    assert "agents.defaults.mascotMoodModelPreset" in RETIRED_KEY_PATHS


def test_my_tool_is_registered_by_hand_and_self_py_lists_nothing() -> None:
    assert "self" in loader._HARDCODED_TOOL_MODULES
    assert self_module.TOOLS == []
