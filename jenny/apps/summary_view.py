"""Il riassunto di un'app: quel che il cassetto e la copertina mostrano di lei.

**Lo calcola il codice, mai il modello.** Legge i dati dell'app — le letture
del suo lettore, o una sua collezione — filtrati dai parametri della posa
(quale sensore), e li porta nella **stessa forma** di una micro app
(``jenny/board/micro.py::micro_view``): valore, stato, andamento, chip, righe.
Cosi' il cassetto ha un solo modo di disegnare un dato vivo, chiunque l'abbia
prodotto, e la copertina di una scheda ne prende un pezzo.

Gli stati sono quelli dei dati (``ok``, ``alarm``, ``stale``, ``empty``) finché
il codice va; diventano quelli del codice (``trial``, ``unapproved``,
``broken``, ``paused``…) quando non va, e allora il valore resta l'ultimo buono.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.apps.manifest import AppSection, LoadedApp

# Le righe di una lista: le stesse di una micro app.
MAX_ROWS = 20
# Le letture del lettore stanno in questa collezione dell'app.
READINGS = "readings"
# Le righe intere dell'ultima lettura (liste e tabelle), accanto alle letture.
LATEST_FILE = "latest.json"
# Lo stato del lettore: quando ha girato, se e' rotto.
STATE_FILE = "state.json"


def reader_dir(app_dir: Path) -> Path:
    """Dove il lettore tiene letture, righe e stato: ``data/`` dell'app."""
    return app_dir / "data"


def _filters(section: AppSection, params: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: params.get(val[1:]) if isinstance(val, str) and val.startswith("$") else val
        for key, val in section.where.items()
    }


def matching_records(app: LoadedApp, params: Mapping[str, Any]) -> list[dict[str, Any]]:
    """I record che il riassunto guarda: la sua collezione, filtrata per la posa."""
    from jenny.apps.storage import StorageError, read_collection

    section = app.manifest.section if app.manifest else None
    if section is None:
        return []
    filters = _filters(section, params)
    try:
        return [
            r for r in read_collection(app.dir, section.collection)
            if all(r.get(k) == v for k, v in filters.items())
        ]
    except (StorageError, OSError) as exc:
        logger.warning("apps: summary of {} unreadable: {}", app.slug, exc)
        return []


def _latest_rows(app: LoadedApp, field: str, series: str | None) -> list[dict[str, Any]]:
    try:
        data = json.loads((reader_dir(app.dir) / LATEST_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    if series is not None and isinstance(data.get("series"), dict):
        data = data["series"].get(series) or {}
    rows = (data.get("rows") or {}).get(field) if isinstance(data, dict) else None
    return rows if isinstance(rows, list) else []


def read_reader_state(app_dir: Path) -> dict[str, Any]:
    try:
        data = json.loads((reader_dir(app_dir) / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _record_time(record: Mapping[str, Any]) -> datetime | None:
    from jenny.board import model

    stamp = model.parse_iso(record.get("ts"))
    return stamp.astimezone(timezone.utc) if stamp else None


def _count_view(section: AppSection, records: list[dict[str, Any]],
                now: datetime) -> dict[str, Any]:
    """``aggregate: count``: quanti record passano il filtro, e i piu' recenti come righe."""
    from jenny.board import model

    ordered = sorted(records, key=lambda r: str(r.get(section.latest, "")), reverse=True)
    value = len(records)
    newest = _record_time(ordered[0]) if ordered else None
    state = "ok"
    if section.alarm and model.compare(value, section.alarm["op"], section.alarm["value"]):
        state = "alarm"
    if records and section.stale_after_minutes and (
        newest is None or now - newest > timedelta(minutes=section.stale_after_minutes)
    ):
        state = "stale"
    rows = [
        {col: r.get(col) for col in section.columns} for r in ordered[:MAX_ROWS]
    ] if section.columns else []
    return {
        "state": state, "value": value, "at": newest.isoformat(timespec="seconds") if newest
        else "", "trend": [], "record": ordered[0] if ordered else None, "rows": rows,
    }


def summary_view(
    workspace: Path,
    app: LoadedApp,
    params: Mapping[str, Any],
    *,
    now: datetime | None = None,
    code_state: str | None = None,
) -> dict[str, Any]:
    """Il riassunto di *app* per una posa con *params*. Mai un'eccezione.

    *code_state*, se c'e', vince sullo stato dei dati: un'app in prova o non
    approvata lo dice anche se ha un valore (che resta, l'ultimo buono).
    """
    from jenny.board.sections import records_view

    manifest = app.manifest
    base: dict[str, Any] = {
        "slug": app.slug, "app": app.slug,
        "name": manifest.name if manifest else app.slug,
        "icon": manifest.icon if manifest else "ti-apps",
        "hasScreen": bool(manifest) and (
            manifest.view_kind == "external" or (app.dir / "app" / "index.html").is_file()),
        "params": dict(params),
    }
    if manifest is None or manifest.section is None:
        return {**base, "state": "no_section"}
    section = manifest.section
    current = now or datetime.now(timezone.utc)
    records = matching_records(app, params)
    if section.aggregate == "count":
        view = _count_view(section, records, current)
    else:
        view = records_view(
            records, field_name=section.field, latest=section.latest, alarm=section.alarm,
            stale_after_minutes=section.stale_after_minutes, trend_days=section.trend_days,
            now=current,
        )
    record = view.pop("record", None) or {}
    rows = view.pop("rows", None)
    columns = [{"field": c, **cv} for c, cv in section.columns.items()]
    if section.type == "rows":
        series = None
        key = manifest.reader_key
        if key and isinstance(section.where.get(key), str):
            series = _filters(section, params).get(key)
        rows = _latest_rows(app, section.field, series)
    # I chip: quelli dichiarati, o (con un lettore) tutti gli scalari per la
    # forma ``chips`` e al piu' due oltre al principale per le altre.
    fields = section.fields
    chip_names = list(section.chips)
    if not chip_names and fields:
        scalars = [n for n, f in fields.items() if f["type"] != "rows"
                   and n != manifest.reader_key]
        chip_names = scalars if section.view == "chips" else [
            n for n in scalars if n != section.field][:2]
    extras = [
        {"field": name, "label": fields.get(name, {}).get("label", name),
         "unit": fields.get(name, {}).get("unit", ""),
         "type": fields.get(name, {}).get("type", ""), "value": record.get(name)}
        for name in chip_names
    ]
    reader = manifest.reader
    state = read_reader_state(app.dir) if reader else {}
    reader_status = str(state.get("status") or "")
    data_state = view["state"]
    shown = data_state
    if code_state:
        shown = code_state
    elif reader_status in ("broken", "quarantined", "paused"):
        shown = reader_status
    out = {
        **base,
        "label": section.label or section.field or "",
        "unit": section.unit,
        "field": section.field,
        "type": section.type or ("number" if isinstance(view.get("value"), (int, float))
                                  and not isinstance(view.get("value"), bool) else ""),
        "view": section.view,
        "aggregate": section.aggregate,
        "rows": rows or [],
        "columns": columns,
        "min": 0,
        "max": section.max,
        "verbs": list(section.verbs),
        **view,
        "state": shown,
        "dataState": data_state,
        "extras": extras,
        "source": record.get("_source", "") if isinstance(record.get("_source"), str) else "",
        "codeStatus": code_state or reader_status or "ok",
        "lastError": state.get("lastError", "") if reader_status not in ("", "ok") else "",
        "failures": int(state.get("failures") or 0),
    }
    if reader:
        from jenny.board.micro import next_run_at

        due = next_run_at(reader, state)
        out.update({
            "every": reader["every"], "net": reader["net"], "uses": reader["uses"],
            "lastRunAt": state.get("lastRunAt", ""),
            "nextRunAt": due.isoformat(timespec="seconds") if due else "",
        })
    return out


def field_value(app: LoadedApp, params: Mapping[str, Any], field_name: str, *,
                now: datetime | None = None) -> tuple[Any, bool]:
    """L'ultimo valore di *field_name* per una posa, e se è fresco.

    Per un riassunto a conteggio, il suo campo (o ``count``) è il conteggio.
    """
    manifest = app.manifest
    if manifest is None or manifest.section is None:
        return None, False
    section = manifest.section
    records = matching_records(app, params)
    current = now or datetime.now(timezone.utc)
    if section.aggregate == "count" and field_name in ("count", section.field, ""):
        view = _count_view(section, records, current)
        return view["value"], view["state"] != "stale"
    if not records:
        return None, False
    latest = max(records, key=lambda r: str(r.get(section.latest, "")))
    at = _record_time(latest)
    fresh = at is not None and (
        not section.stale_after_minutes
        or current - at <= timedelta(minutes=section.stale_after_minutes)
    )
    return latest.get(field_name), fresh
