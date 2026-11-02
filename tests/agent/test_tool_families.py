"""La famiglia di uno strumento arriva col suo evento.

La casa dice cosa sta facendo Jenny con una parola per famiglia («Legge»,
«Cerca»). La tabella stava solo nella WebUI, e diciotto strumenti nati dopo
non c'erano: dicevano «mi do da fare» qualunque cosa facessero. Adesso la
decide il server (`progress_events.TOOL_FAMILIES`), e ogni strumento
registrato ci deve stare — anche quelli che stanno apposta sul generico.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import SimpleNamespace

from jenny.agent.hook import AgentHookContext
from jenny.agent.progress_events import (
    TOOL_FAMILIES,
    build_tool_event_finish_payloads,
    build_tool_event_start_payload,
    tool_family,
)
from jenny.agent.tools import loader
from jenny.agent.tools.loader import declared_tool_name

ROOT = Path(__file__).resolve().parents[2]
I18N = ROOT / "jenny" / "templates" / "ui" / "assets" / "i18n"

# Gli strumenti che non passano dal loader (v. AGENTS.md): `my` e `memory` hanno
# un nome fisso; quello delle azioni di un'app cambia con l'app, e vale `busy`.
_BYPASS = ("my", "memory")


def _registered_names() -> set[str]:
    names: set[str] = set(_BYPASS)
    for module in loader._HARDCODED_TOOL_MODULES:
        mod = importlib.import_module(f"jenny.agent.tools.{module}")
        for cls in getattr(mod, "TOOLS", []):
            names.add(declared_tool_name(cls))
    return names


def test_every_registered_tool_has_a_family() -> None:
    names = _registered_names()
    assert len(names) > 40, names
    missing = sorted(names - TOOL_FAMILIES.keys())
    assert missing == [], f"strumenti senza famiglia: {missing}"


def test_every_family_is_a_word_the_house_has() -> None:
    for locale in ("it", "en"):
        doing = json.loads((I18N / f"{locale}.json").read_text(encoding="utf-8"))["home"]["work"]["doing"]
        assert set(TOOL_FAMILIES.values()) <= doing.keys(), locale


def test_the_events_carry_it() -> None:
    call = SimpleNamespace(id="c1", name="web_search", arguments={"q": "x"})
    assert build_tool_event_start_payload(call)["family"] == "search"
    ctx = AgentHookContext(iteration=0, messages=[])
    ctx.tool_calls = [call]
    ctx.tool_results = ["ok"]
    ctx.tool_events = [{"status": "ok"}]
    assert build_tool_event_finish_payloads(ctx)[0]["family"] == "search"
    assert tool_family("app_todo_add") == "busy"
    assert tool_family(None) == "busy"
