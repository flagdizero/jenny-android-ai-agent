"""La striscia di un'app nel manifest: blocchi, taglia, errori che si leggono."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jenny.apps.manifest import load_app
from jenny.apps.slip_spec import size_for

SPESA_ACTIONS = [
    {"name": "add_item", "description": "Add", "kind": "storage", "op": "append",
     "collection": "items", "params": {"what": {"type": "string"}, "done": {"type": "boolean"}},
     "required": ["what"]},
    {"name": "set_done", "description": "Tick", "kind": "storage", "op": "update",
     "collection": "items", "params": {"done": {"type": "boolean"}}},
    {"name": "clear", "description": "Clear the ticked", "kind": "storage", "op": "delete",
     "collection": "items"},
]
PORTFOLIO_ACTIONS = [
    {"name": "posizioni", "description": "List", "kind": "storage", "op": "query",
     "collection": "posizioni"},
]


def _app(tmp_path: Path, manifest: dict, run: str | None = None):
    d = tmp_path / "apps" / "x"
    d.mkdir(parents=True)
    (d / "app.json").write_text(json.dumps({"name": "X", "description": "x", **manifest}))
    if run:
        (d / "run.py").write_text(run)
    return load_app(d)


def test_sizes_follow_the_rows() -> None:
    assert [size_for(n) for n in (1, 2, 3, 4, 6, 7, 9, 10)] == \
        ["S", "S", "M", "M", "L", "XL", "XL", None]


def test_a_shopping_list_strip(tmp_path: Path) -> None:
    app = _app(tmp_path, {"actions": SPESA_ACTIONS, "summary": {
        "collection": "items",
        "meta": {"count": True, "where": {"done": False}, "label": "to buy"},
        "blocks": [
            {"kind": "checklist", "text": "what", "done": "done", "toggle": "set_done", "max": 4},
            {"kind": "buttons", "actions": [{"action": "clear", "label": "Clear"}]},
        ]}})
    assert not app.broken, app.error
    s = app.manifest.section
    assert s.size == "L" and [b["kind"] for b in s.blocks] == ["checklist", "buttons"]
    assert s.blocks[0]["toggle"] == "set_done" and s.blocks[0]["rows"] == 4
    assert s.meta == {"count": True, "where": {"done": False}, "collection": "items",
                      "label": "to buy"}
    # Senza un campo da mostrare, il riassunto conta i record.
    assert s.aggregate == "count"


def test_a_reader_strip_checks_the_reader_fields(tmp_path: Path) -> None:
    reader = {"every": 30, "fields": {"totale": {"label": "Totale", "type": "number", "unit": "€"},
                                      "rendimento": {"label": "Dall'inizio", "type": "number",
                                                     "unit": "%"},
                                      "online": {"label": "Online", "type": "bool"}}}
    good = {"reader": reader, "summary": {"blocks": [
        {"kind": "value", "field": "totale", "unit": "€", "delta": "rendimento", "deltaUnit": "%"},
        {"kind": "chart", "field": "totale", "days": 14},
        {"kind": "status", "online": "online"}]}}
    app = _app(tmp_path, good, run="def run(ctx):\n    return {}\n")
    assert not app.broken, app.error
    s = app.manifest.section
    assert (s.size, s.field, s.collection) == ("L", "totale", "readings")  # 2 + 2 + 1 righe
    bad = json.loads(json.dumps(good))
    bad["summary"]["blocks"][1]["field"] = "online"
    app = _app(tmp_path / "b", bad, run="def run(ctx):\n    return {}\n")
    assert app.broken and "'online' is a bool field, needs number" in app.error


@pytest.mark.parametrize(("blocks", "fragment"), [
    ([{"kind": "graph", "field": "x"}], "blocks[0].kind must be one of"),
    ([{"kind": "checklist", "text": "what", "done": "done", "toggle": "add_item"}],
     "must be a storage 'update' action"),
    ([{"kind": "buttons", "actions": ["add_item"]}], "requires what, which a tap cannot fill"),
    ([{"kind": "buttons", "actions": ["nope"]}], "'nope' is not an action of this app"),
    ([{"kind": "table", "columns": {"a": {}, "b": {}, "c": {}, "d": {}}}], "1 to 3 field names"),
    ([{"kind": "checklist", "text": "what", "done": "done", "max": 9}], "max must be between 1 and 5"),
    ([{"kind": "table", "columns": {"what": {}}, "max": 5},
      {"kind": "checklist", "text": "what", "done": "done", "max": 5}], "a strip holds at most 9"),
    ([{"kind": "value", "field": "x"}] * 5, "1 to 4 blocks"),
])
def test_a_bad_block_breaks_the_app_with_a_readable_error(tmp_path: Path, blocks, fragment) -> None:
    app = _app(tmp_path, {"actions": SPESA_ACTIONS,
                          "summary": {"collection": "items", "blocks": blocks}})
    assert app.broken and fragment in app.error, app.error


def test_a_month_is_always_xl(tmp_path: Path) -> None:
    app = _app(tmp_path, {"actions": SPESA_ACTIONS, "summary": {
        "collection": "items", "blocks": [{"kind": "month", "date": "ts"}]}})
    assert not app.broken, app.error
    assert app.manifest.section.size == "XL"


def test_the_old_shapes_become_blocks(tmp_path: Path) -> None:
    lista = _app(tmp_path / "l", {"actions": SPESA_ACTIONS, "summary": {
        "collection": "items", "view": "list", "where": {"done": False}, "label": "to buy",
        "columns": {"what": {"label": "What"}}, "verbs": ["clear"]}})
    s = lista.manifest.section
    assert [b["kind"] for b in s.blocks] == ["table", "buttons"] and s.size == "L"
    assert s.meta["count"] is True and s.meta["label"] == "to buy"
    valore = _app(tmp_path / "v", {"actions": PORTFOLIO_ACTIONS, "summary": {
        "collection": "posizioni", "field": "totale", "unit": "€", "trendDays": 7}})
    s = valore.manifest.section
    assert [b["kind"] for b in s.blocks] == ["value"] and s.size == "S"
    assert s.blocks[0]["trend"] is True
    old = _app(tmp_path / "o", {"actions": SPESA_ACTIONS, "section": {
        "value": {"collection": "items", "field": "what"}}})
    assert [b["kind"] for b in old.manifest.section.blocks] == ["value"]
