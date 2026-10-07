"""Il blocco ``section`` del manifest: la vista compatta di un'app in un cassetto.

Una sezione dichiarata male rende l'app rotta con un errore che si legge — come
ogni altro difetto del manifest — e il validatore della skill ``app-creator``
dice la stessa cosa mentre il manifest si scrive.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jenny.apps.manifest import load_app

ROOT = Path(__file__).resolve().parents[2]

SECTION = {
    "params": {"sensor": {"type": "string"}},
    "value": {"collection": "readings", "where": {"sensor": "$sensor"},
              "field": "moisture", "unit": "%"},
    "status": {"stale_after_minutes": 120,
               "alarm": {"field": "moisture", "op": "<", "value": 20}},
    "trend": {"days": 7},
    "verbs": ["refresh"],
}
BASE = {
    "name": "WaterBot", "description": "Sensori",
    "actions": [{"name": "refresh", "description": "Rilegge", "kind": "storage",
                 "op": "query", "collection": "readings"}],
}


def _app(tmp_path: Path, section) -> Path:
    app = tmp_path / "apps" / "waterbot"
    (app / "app").mkdir(parents=True)
    (app / "app" / "index.html").write_text("<p>x</p>")
    (app / "app.json").write_text(json.dumps({**BASE, "section": section}))
    return app


def test_a_good_section_loads(tmp_path) -> None:
    app = load_app(_app(tmp_path, SECTION))
    assert not app.broken, app.error
    section = app.manifest.section
    assert (section.collection, section.field, section.unit) == ("readings", "moisture", "%")
    assert section.stale_after_minutes == 120 and section.trend_days == 7
    assert section.verbs == ["refresh"]


@pytest.mark.parametrize(("change", "message"), [
    ({"value": None}, "section.value is required"),
    ({"value": {"collection": "Bad Name", "field": "x"}}, "collection must be"),
    ({"value": {"collection": "readings", "field": "x", "where": {"sensor": "$nope"}}},
     "not a declared param"),
    ({"status": {"stale_after_minutes": 0}}, "positive integer"),
    ({"status": {"alarm": {"field": "x", "op": "~", "value": 1}}}, "alarm must be"),
    ({"verbs": ["launch"]}, "not an action of this app"),
    ({"verbs": ["refresh", "refresh", "refresh"]}, "at most two"),
])
def test_a_bad_section_breaks_the_app_with_a_readable_error(tmp_path, change, message) -> None:
    section = {**SECTION, **change}
    if change.get("value") is None and "value" in change:
        section.pop("value")
    app = load_app(_app(tmp_path, section))
    assert app.broken
    assert message in app.error


def test_an_app_without_a_section_is_unchanged(tmp_path) -> None:
    app = tmp_path / "apps" / "todo"
    (app / "app").mkdir(parents=True)
    (app / "app.json").write_text(json.dumps(BASE))
    loaded = load_app(app)
    assert not loaded.broken and loaded.manifest.section is None
