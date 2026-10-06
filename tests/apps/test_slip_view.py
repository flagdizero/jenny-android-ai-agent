"""I dati di una striscia: ogni blocco porta quel che la casa disegna."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jenny.apps.manifest import load_app
from jenny.apps.slip_view import slip_view

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)  # un giovedi'


def _app(tmp_path: Path, manifest: dict, data: dict[str, list[dict]], run: str | None = None):
    d = tmp_path / "apps" / "x"
    (d / "data").mkdir(parents=True)
    (d / "app.json").write_text(json.dumps({"name": "X", "description": "x", **manifest}))
    if run:
        (d / "run.py").write_text(run)
    for name, rows in data.items():
        (d / "data" / f"{name}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    app = load_app(d)
    assert not app.broken, app.error
    return app


def _ts(days=0, hours=0):
    return (NOW - timedelta(days=days, hours=hours)).isoformat(timespec="seconds")


SPESA = {
    "actions": [
        {"name": "set_done", "description": "Tick", "kind": "storage", "op": "update",
         "collection": "items", "params": {"done": {"type": "boolean"}}},
        {"name": "clear", "description": "Clear the ticked", "kind": "storage", "op": "delete",
         "collection": "items"},
    ],
    "summary": {"collection": "items",
                "meta": {"count": True, "where": {"done": False}, "label": "da comprare"},
                "blocks": [
                    {"kind": "checklist", "text": "what", "done": "done", "toggle": "set_done",
                     "max": 3},
                    {"kind": "buttons", "actions": ["clear"]}]},
}


def test_a_checklist_shows_what_is_left_first_and_how_many_more(tmp_path: Path) -> None:
    items = [{"id": f"i{n}", "ts": _ts(hours=n), "what": w, "done": d}
             for n, (w, d) in enumerate([("Caffè", True), ("Latte", False), ("Uova", False),
                                         ("Pane", False), ("Sale", False)])]
    view = slip_view(tmp_path, _app(tmp_path, SPESA, {"items": items}), {}, now=NOW)
    # 3 righe di lista + 1 fila di tasti: M.
    assert view["size"] == "M" and view["meta"] == {"value": 4, "label": "da comprare"}
    check, buttons = view["blocks"]
    assert [i["text"] for i in check["items"]] == ["Latte", "Uova", "Pane"]
    assert check["more"] == 2 and check["left"] == 4 and check["toggle"] == "set_done"
    assert check["items"][0]["id"] == "i1"
    assert buttons["actions"] == [{"action": "clear", "label": "Clear the ticked", "params": {}}]


def test_a_reader_strip_follows_its_series(tmp_path: Path) -> None:
    manifest = {
        "reader": {"every": 30, "key": "sensor", "fields": {
            "sensor": {"label": "Sensore", "type": "text"},
            "umidita": {"label": "Umidità", "type": "number", "unit": "%"},
            "online": {"label": "Online", "type": "bool"}}},
        "summary": {"params": {"sensor": {"type": "string"}}, "where": {"sensor": "$sensor"},
                    "alarm": {"field": "umidita", "op": "<", "value": 20},
                    "blocks": [{"kind": "value", "field": "umidita", "unit": "%"},
                               {"kind": "chart", "field": "umidita", "days": 7},
                               {"kind": "status", "online": "online"}]},
    }
    # Le letture fresche (ogni 30 minuti: ferma dopo 90), e due giorni di storia.
    readings = [{"id": f"r{n}", "ts": _ts(days=d, hours=h), "sensor": s, "umidita": v,
                 "online": n == 0}
                for n, (s, v, d, h) in enumerate([("acero", 38, 0, 0), ("monstera", 17, 0, 1),
                                                   ("acero", 41, 1, 0), ("acero", 44, 2, 0)])]
    app = _app(tmp_path, manifest, {"readings": readings}, run="def run(ctx):\n    return []\n")
    view = slip_view(tmp_path, app, {"sensor": "acero"}, now=NOW)
    value, chart, status = view["blocks"]
    assert value["value"] == 38 and value["unit"] == "%"
    assert chart["points"] == [44, 41, 38] and chart["band"] == {"op": "<", "value": 20}
    assert status["online"] is True and view["state"] == "ok"
    # Nessun dato in alto dichiarato: la casa ci mette lo stato.
    assert view["meta"] is None and view["at"]
    other = slip_view(tmp_path, app, {"sensor": "monstera"}, now=NOW)
    assert other["blocks"][0]["value"] == 17 and other["state"] == "alarm"


def test_portfolio_bars_and_numbers(tmp_path: Path) -> None:
    manifest = {"summary": {"collection": "totali", "blocks": [
        {"kind": "value", "field": "totale", "unit": "€", "delta": "rendimento", "deltaUnit": "%"},
        {"kind": "bars", "collection": "posizioni", "label": "ticker", "value": "valore",
         "weight": "peso", "target": "target", "unit": "€", "max": 2}]}}
    data = {"totali": [{"id": "t", "ts": _ts(), "totale": 12430, "rendimento": 14.9}],
            "posizioni": [{"id": t, "ts": _ts(), "ticker": t, "valore": v, "peso": p, "target": g}
                          for t, v, p, g in [("VWCE", 5827, 46.9, 50), ("SWDA", 4074, 32.8, 30),
                                             ("AGGH", 2529, 20.3, 20)]]}
    view = slip_view(tmp_path, _app(tmp_path, manifest, data), {}, now=NOW)
    value, bars = view["blocks"]
    assert (value["value"], value["delta"], value["deltaUnit"]) == (12430, 14.9, "%")
    assert bars["rows"] == 2
    assert [r["label"] for r in bars["items"]] == ["VWCE", "SWDA"] and bars["more"] == 1
    assert bars["items"][0]["weight"] == 46.9 and bars["items"][0]["target"] == 50
    assert view["size"] == "M"


def test_time_blocks_week_month_events(tmp_path: Path) -> None:
    manifest = {"summary": {"collection": "fatti", "blocks": [
        {"kind": "week", "date": "ts"},
        {"kind": "events", "collection": "scadenze", "date": "quando", "text": "cosa", "max": 1},
        {"kind": "month", "collection": "scadenze", "date": "quando",
         "alarmWhere": {"urgente": True}}]}}
    data = {"fatti": [{"id": "a", "ts": _ts(days=1)}, {"id": "b", "ts": _ts(days=3)}],
            "scadenze": [{"id": "s1", "ts": _ts(), "quando": "2026-10-16", "cosa": "F24",
                          "urgente": True},
                         {"id": "s2", "ts": _ts(), "quando": "2026-10-30", "cosa": "730"},
                         {"id": "s3", "ts": _ts(), "quando": "2026-10-31", "cosa": "IMU"},
                         {"id": "s0", "ts": _ts(), "quando": "2026-10-01", "cosa": "passata"}]}
    view = slip_view(tmp_path, _app(tmp_path, manifest, data), {}, now=NOW)
    week, events, month = view["blocks"]
    assert view["size"] == "XL"
    assert [d["done"] for d in week["days"]] == [True, False, True, False, False, False, False]
    assert week["today"] == 3
    assert [r["text"] for r in events["items"]] == ["F24"] and events["more"] == 2
    assert events["items"][0]["days"] == 8
    assert month["days"] == [1, 16, 30, 31] and month["alarm"] == [16] and month["today"] == 8


def test_controls_carry_what_they_need(tmp_path: Path) -> None:
    manifest = {
        "actions": [
            {"name": "set_luce", "description": "Luce", "kind": "storage", "op": "update",
             "collection": "stato", "params": {"luce": {"type": "boolean"}}},
            {"name": "piu", "description": "+1", "kind": "storage", "op": "append",
             "collection": "vite"},
            {"name": "meno", "description": "-1", "kind": "storage", "op": "append",
             "collection": "vite"}],
        "summary": {"collection": "vite", "blocks": [
            {"kind": "switches", "collection": "stato",
             "items": [{"field": "luce", "label": "Luce balcone", "action": "set_luce"}]},
            {"kind": "counter", "items": [{"field": "tu", "label": "Tu", "dec": "meno",
                                           "inc": "piu"}]},
            {"kind": "note", "field": "nota", "lines": 1}]},
    }
    data = {"stato": [{"id": "st", "ts": _ts(), "luce": True}],
            "vite": [{"id": "v", "ts": _ts(), "tu": 20, "nota": "partita 3"}]}
    view = slip_view(tmp_path, _app(tmp_path, manifest, data), {}, now=NOW)
    switches, counter, note = view["blocks"]
    assert switches == {"kind": "switches", "rows": 1, "id": "st", "items": [
        {"field": "luce", "label": "Luce balcone", "action": "set_luce", "on": True,
         "withId": True}]}
    assert counter["items"] == [{"label": "Tu", "value": 20, "dec": "meno", "inc": "piu"}]
    assert note["text"] == "partita 3"


def test_an_app_without_blocks_has_no_strip(tmp_path: Path) -> None:
    app = _app(tmp_path, {}, {})
    assert slip_view(tmp_path, app, {}, now=NOW) == {}
