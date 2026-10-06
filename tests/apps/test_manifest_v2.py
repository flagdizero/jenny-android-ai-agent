"""Il manifest con ``reader`` e ``summary``: la micro app dentro l'app.

Il vecchio ``section`` si legge ancora, e vuol dire un riassunto di forma
``value``; un ``reader`` si valida con le regole di una micro app; un
``summary`` senza lettore riassume una collezione, anche contandone i record.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from jenny.apps.manifest import load_app
from jenny.apps.summary_view import field_value, summary_view

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)


def _write(root: Path, slug: str, manifest: dict, *, run: str | None = None,
           data: dict[str, list[dict]] | None = None) -> Path:
    d = root / "apps" / slug
    (d / "app").mkdir(parents=True)
    (d / "data").mkdir()
    (d / "app.json").write_text(json.dumps(manifest))
    (d / "app" / "index.html").write_text("<p>x</p>")
    if run is not None:
        (d / "run.py").write_text(run)
    for name, records in (data or {}).items():
        (d / "data" / f"{name}.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in records))
    return d


WATERBOT = {
    "name": "WaterBot", "description": "Moisture from the hub",
    "reader": {
        "every": 30, "net": ["192.168.1.40"], "key": "sensor",
        "fields": {
            "sensor": {"label": "Sensor", "type": "text"},
            "moisture": {"label": "Moisture", "type": "number", "unit": "%", "min": 0, "max": 100},
            "battery": {"label": "Battery", "type": "number", "unit": "%"},
            "online": {"label": "Online", "type": "bool"},
        },
    },
    "summary": {
        "view": "value", "primary": "moisture",
        "params": {"sensor": {"type": "string"}}, "where": {"sensor": "$sensor"},
        "alarm": {"field": "moisture", "op": "<", "value": 20},
    },
}


def test_a_reader_app_reads_like_a_micro_app(tmp_path: Path) -> None:
    d = _write(tmp_path, "waterbot", WATERBOT, run="def run(ctx):\n    return []\n", data={
        "readings": [
            {"id": "a", "ts": "2026-10-06T11:30:00+00:00", "sensor": "acerello",
             "moisture": 38, "battery": 82, "online": True},
            {"id": "b", "ts": "2026-10-06T11:30:00+00:00", "sensor": "monstera",
             "moisture": 17, "battery": 60, "online": True},
        ],
    })
    app = load_app(d)
    assert not app.broken, app.error
    m = app.manifest
    assert m.reader["every"] == 30 and m.reader["net"] == ["192.168.1.40"]
    assert m.reader_key == "sensor"
    section = m.section
    assert (section.collection, section.field, section.view) == ("readings", "moisture", "value")
    assert (section.label, section.unit, section.trend_days) == ("Moisture", "%", 7)

    acero = summary_view(tmp_path, app, {"sensor": "acerello"}, now=NOW)
    assert (acero["value"], acero["state"], acero["unit"]) == (38, "ok", "%")
    # Accanto al principale, i due scalari dopo (la chiave della serie no).
    assert [e["field"] for e in acero["extras"]] == ["battery", "online"]
    assert acero["every"] == 30 and acero["hasScreen"] is True
    monstera = summary_view(tmp_path, app, {"sensor": "monstera"}, now=NOW)
    assert (monstera["value"], monstera["state"]) == (17, "alarm")
    assert field_value(app, {"sensor": "monstera"}, "moisture", now=NOW) == (17, True)


def test_a_reader_without_run_py_is_broken(tmp_path: Path) -> None:
    app = load_app(_write(tmp_path, "waterbot", WATERBOT))
    assert app.broken and "run.py" in app.error


@pytest.mark.parametrize("change, fragment", [
    (lambda m: m["reader"].update(fields={}), "fields"),
    (lambda m: m["reader"].update(key="moisture"), "key"),
    (lambda m: m["summary"].update(view="progress", primary="battery"), "max"),
    (lambda m: m["reader"].update(net=[f"h{i}.lan" for i in range(9)]), "hosts"),
])
def test_a_bad_reader_is_a_readable_error(tmp_path: Path, change, fragment) -> None:
    manifest = json.loads(json.dumps(WATERBOT))
    change(manifest)
    app = load_app(_write(tmp_path, "waterbot", manifest, run="def run(ctx):\n    return {}\n"))
    assert app.broken and "reader" in app.error and fragment in app.error, app.error


SPESA = {
    "name": "Spesa", "description": "Shopping list",
    "actions": [{"name": "add", "description": "Add an item", "kind": "storage",
                 "op": "append", "collection": "items",
                 "params": {"what": {"type": "string"}, "done": {"type": "boolean"}}}],
    "summary": {
        "collection": "items", "view": "list", "where": {"done": False},
        "label": "to buy", "columns": {"what": {"label": "What"}}, "verbs": ["add"],
    },
}


def test_a_list_summary_counts_and_shows_the_records(tmp_path: Path) -> None:
    app = load_app(_write(tmp_path, "spesa", SPESA, data={"items": [
        {"id": "1", "ts": "2026-10-06T09:00:00+00:00", "what": "latte", "done": False},
        {"id": "2", "ts": "2026-10-06T10:00:00+00:00", "what": "uova", "done": False},
        {"id": "3", "ts": "2026-10-06T08:00:00+00:00", "what": "pane", "done": True},
    ]}))
    assert not app.broken, app.error
    view = summary_view(tmp_path, app, {}, now=NOW)
    assert (view["value"], view["view"], view["aggregate"]) == (2, "list", "count")
    assert view["rows"] == [{"what": "uova"}, {"what": "latte"}]
    assert view["verbs"] == ["add"]
    assert field_value(app, {}, "count", now=NOW) == (2, True)


def test_a_list_summary_needs_columns_and_counts(tmp_path: Path) -> None:
    bad = json.loads(json.dumps(SPESA))
    del bad["summary"]["columns"]
    assert "columns" in load_app(_write(tmp_path, "spesa", bad)).error
    bad = json.loads(json.dumps(SPESA))
    bad["summary"]["aggregate"] = "last"
    assert "count" in load_app(_write(tmp_path, "spesa2", bad)).error


def test_the_old_section_is_still_a_value_summary(tmp_path: Path) -> None:
    app = load_app(_write(tmp_path, "orto", {
        "name": "Orto", "description": "x",
        "section": {"value": {"collection": "letture", "field": "umidita", "unit": "%"},
                    "status": {"stale_after_minutes": 60}},
    }, data={"letture": [{"id": "1", "ts": "2026-10-06T08:00:00+00:00", "umidita": 40}]}))
    section = app.manifest.section
    assert (section.view, section.aggregate, section.field) == ("value", "last", "umidita")
    view = summary_view(tmp_path, app, {}, now=NOW)
    assert (view["value"], view["state"], view["unit"]) == (40, "stale", "%")
    assert view["extras"] == []


def test_an_app_without_a_summary_has_none(tmp_path: Path) -> None:
    app = load_app(_write(tmp_path, "calc", {"name": "Calc", "description": "x"}))
    assert summary_view(tmp_path, app, {})["state"] == "no_section"
