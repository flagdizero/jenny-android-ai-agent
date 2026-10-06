"""Una scrittura dallo schermo e un lettore che ricalcola avvisano la WebUI.

Sul telefono (06/10/2026) la polaroid del portafoglio restava al valore di
prima finché non si riapriva Jenny: l'avviso partiva solo quando era il tool
di Jenny a scrivere.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jenny.apps import notify
from jenny.apps.executor import execute_action
from jenny.apps.reader import run_reader

LIST = {
    "name": "Spesa", "description": "x",
    "actions": [{"name": "add", "description": "Add", "kind": "storage", "op": "append",
                 "collection": "items", "params": {"what": {"type": "string"}}}],
    "summary": {"collection": "items", "view": "list", "columns": {"what": {"label": "What"}}},
}
TOTAL = {
    "name": "Portafoglio", "description": "x",
    "reader": {"every": 60, "fields": {"totale": {"label": "Totale", "type": "number"}}},
    "summary": {"primary": "totale"},
}


@pytest.fixture
def heard(monkeypatch) -> list[str]:
    out: list[str] = []

    async def note(slug: str) -> None:
        out.append(slug)

    notify.set_notifier(note)
    yield out
    notify.set_notifier(None)


def _app(ws: Path, slug: str, manifest: dict, run: str | None = None) -> None:
    d = ws / "apps" / slug
    d.mkdir(parents=True)
    (d / "app.json").write_text(json.dumps(manifest))
    if run:
        (d / "run.py").write_text(run)


async def test_a_write_from_the_screen_is_announced(tmp_path: Path, heard) -> None:
    _app(tmp_path, "spesa", LIST)
    await execute_action(tmp_path, "spesa", "add", {"what": "latte"})
    assert heard == ["spesa"]
    # Il tool di Jenny avvisa da se': niente doppione.
    await execute_action(tmp_path, "spesa", "add", {"what": "uova"}, announce=False)
    assert heard == ["spesa"]


async def test_a_reader_that_recomputes_is_announced(tmp_path: Path, heard) -> None:
    _app(tmp_path, "portafoglio", TOTAL, run="def run(ctx):\n    return {'totale': 12430}\n")
    state = await run_reader(tmp_path, "portafoglio", force=True)
    assert state and state["status"] == "ok"
    assert heard == ["portafoglio"]
