"""Le letture si ordinano nel tempo, non come testo.

L'orologio della bacheca gira i lettori con l'ora locale (``+02:00``), ``app
run`` e il ricalcolo dopo una scrittura con l'ora UTC. Confrontati come stringhe,
``14:07+02:00`` (le 12:07 UTC) viene dopo ``13:18+00:00``: il cassetto mostrava
il totale di prima di un acquisto, e ``app run`` lo ripeteva, finché l'ora UTC
non superava anche come testo quella locale.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jenny.apps.manifest import load_app
from jenny.apps.reader import append_readings, read_readings
from jenny.apps.summary_view import field_value, summary_view
from jenny.board import model

ROME = timezone(timedelta(hours=2))
NOW = datetime(2026, 10, 6, 13, 20, tzinfo=timezone.utc)

PORTFOLIO = {
    "name": "Portafoglio", "description": "Il valore del portafoglio",
    "reader": {
        "every": 60,
        "fields": {
            "totale": {"label": "Totale", "type": "number", "unit": "€"},
            "primo": {"label": "Sleeve maggiore", "type": "text"},
        },
    },
    "summary": {"view": "value", "primary": "totale"},
}


def _write(root: Path, slug: str, manifest: dict,
           data: dict[str, list[dict]] | None = None) -> Path:
    d = root / "apps" / slug
    (d / "app").mkdir(parents=True)
    (d / "data").mkdir()
    (d / "app.json").write_text(json.dumps(manifest))
    (d / "app" / "index.html").write_text("<p>x</p>")
    (d / "run.py").write_text("def run(ctx):\n    return {}\n")
    for name, records in (data or {}).items():
        (d / "data" / f"{name}.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in records))
    return d


def test_the_newest_reading_wins_across_time_zones(tmp_path: Path) -> None:
    """Il file com'era sul telefono: una lettura dell'orologio, poi una di ``app run``."""
    app = load_app(_write(tmp_path, "portfolio", PORTFOLIO, data={"readings": [
        {"id": "a", "ts": "2026-10-06T14:07:09+02:00", "totale": 34967.0, "primo": "CSSPX 56%"},
        {"id": "b", "ts": "2026-10-06T13:18:40+00:00", "totale": 34934.0, "primo": "CSSPX 60%"},
    ]}))
    assert not app.broken, app.error
    view = summary_view(tmp_path, app, {}, now=NOW)
    assert (view["value"], view["state"]) == (34934.0, "ok")
    assert view["at"] == "2026-10-06T13:18:40+00:00"
    assert field_value(app, {}, "totale", now=NOW) == (34934.0, True)


def test_readings_are_written_in_utc_whoever_runs_the_reader(tmp_path: Path) -> None:
    d = _write(tmp_path, "portfolio", PORTFOLIO)
    clock = datetime(2026, 10, 6, 14, 7, 9, tzinfo=ROME)
    append_readings(d, [{"totale": 34967.0, "primo": "CSSPX 56%"}], key=None, now=clock)
    append_readings(d, [{"totale": 34934.0, "primo": "CSSPX 60%"}], key=None,
                    now=datetime(2026, 10, 6, 13, 18, 40, tzinfo=timezone.utc))
    stamps = [r["ts"] for r in read_readings(d)]
    # Anche chi legge il file come testo (lo schermo dell'app, ``query``) lo
    # trova in ordine.
    assert stamps == ["2026-10-06T12:07:09+00:00", "2026-10-06T13:18:40+00:00"]
    assert stamps == sorted(stamps)
    assert summary_view(tmp_path, load_app(d), {}, now=NOW)["value"] == 34934.0


def test_a_count_summary_lists_the_newest_first_across_time_zones(tmp_path: Path) -> None:
    spesa = {
        "name": "Spesa", "description": "Shopping list",
        "summary": {"collection": "items", "view": "list",
                    "columns": {"what": {"label": "What"}}},
    }
    app = load_app(_write(tmp_path, "spesa", spesa, data={"items": [
        {"id": "1", "ts": "2026-10-06T13:00:00+00:00", "what": "uova"},
        {"id": "2", "ts": "2026-10-06T14:30:00+02:00", "what": "latte"},
    ]}))
    view = summary_view(tmp_path, app, {}, now=NOW)
    assert view["rows"] == [{"what": "uova"}, {"what": "latte"}]
    assert view["at"] == "2026-10-06T13:00:00+00:00"


def test_instant_order_keeps_plain_text_ordered_as_text() -> None:
    """Un campo ``latest`` che non e' un istante si ordina come prima."""
    assert sorted(["b", "a", None], key=model.instant_order) == [None, "a", "b"]
    # Un istante viene dopo un valore che non lo e'.
    assert max(["zzz", "2026-01-01T00:00:00+00:00"], key=model.instant_order) == (
        "2026-01-01T00:00:00+00:00")
    # Una data sola e' un istante (mezzanotte, ora locale).
    assert max(["2026-10-05", "2026-10-06"], key=model.instant_order) == "2026-10-06"
