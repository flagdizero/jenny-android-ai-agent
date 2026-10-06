"""I dati di una striscia: ogni blocco con quel che disegna, calcolato dal codice.

La striscia (``jenny/apps/slip_spec.py``) dice *quali* blocchi e *quali* campi;
qui si leggono i dati dell'app — le letture del suo lettore o le sue collezioni,
filtrate dai parametri della posa — e ogni blocco porta solo quel che la casa
disegna (``home-board-blocks.js``), piu' gli ``id`` dei record per i controlli.
Lo stato della striscia (ok, ferma, allarme, vuota, rotta) e' quello del
riassunto (``summary_view``): uno solo, per la chat come per la bacheca.

Mai un'eccezione: un blocco che non sa leggere torna vuoto, e la striscia resta.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from typing import Any

from loguru import logger

from jenny.apps.manifest import LoadedApp
from jenny.apps.summary_view import READINGS, summary_view

# Quanti punti al massimo in un grafico: abbastanza per la forma, pochi per il filo.
MAX_CHART_POINTS = 48


class _Data:
    """Le collezioni dell'app lette una volta sola per striscia."""

    def __init__(self, app: LoadedApp, params: Mapping[str, Any], base_where: dict,
                 base: str) -> None:
        self.app = app
        self.params = params
        # Il filtro della posa (quale sensore) vale per la collezione del
        # riassunto; un blocco che ne nomina un'altra porta il suo.
        self.base_where = base_where
        self.base = base
        self._cache: dict[str, list[dict]] = {}

    def _filters(self, collection: str, where: Mapping[str, Any]) -> dict[str, Any]:
        merged = {**(self.base_where if collection == self.base else {}), **where}
        return {k: self.params.get(v[1:]) if isinstance(v, str) and v.startswith("$") else v
                for k, v in merged.items()}

    def records(self, collection: str, where: Mapping[str, Any] | None = None) -> list[dict]:
        from jenny.apps.storage import StorageError, read_collection

        if collection not in self._cache:
            try:
                self._cache[collection] = read_collection(self.app.dir, collection)
            except (StorageError, OSError) as exc:
                logger.warning("apps: strip of {} cannot read {}: {}", self.app.slug, collection, exc)
                self._cache[collection] = []
        filt = self._filters(collection, where or {})
        return [r for r in self._cache[collection] if all(r.get(k) == v for k, v in filt.items())]


def _newest_first(records: list[dict], key: str = "ts") -> list[dict]:
    from jenny.board import model

    return sorted(records, key=lambda r: model.instant_order(r.get(key)), reverse=True)


def _local_day(value: Any) -> date | None:
    from jenny.board import model

    stamp = model.parse_iso(value) if isinstance(value, str) else None
    if stamp is None:
        return None
    if stamp.tzinfo is None:
        return stamp.date()
    return stamp.astimezone().date()


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


# ── i blocchi ──────────────────────────────────────────────────────────────


def _b_value(b, latest, records, section, now):
    from jenny.board.sections import records_view

    trend = []
    if b.get("trend") and records:
        view = records_view(records, field_name=b["field"], trend_days=7, now=now)
        trend = view.get("trend") or []
    out = {"value": latest.get(b["field"]) if latest else None, "unit": b["unit"],
           "label": b["label"], "trend": trend}
    if b.get("delta") and latest:
        out["delta"] = latest.get(b["delta"])
        out["deltaUnit"] = b.get("deltaUnit", "")
    return out


def _b_numbers(b, latest, data):
    items = []
    for it in b["items"]:
        if it.get("count"):
            value = len(data.records(it["collection"], it.get("where")))
        else:
            value = latest.get(it["field"]) if latest else None
        items.append({"label": it["label"], "unit": it["unit"], "value": value})
    return {"items": items}


def _b_chart(b, records, section, now):
    since = now - timedelta(days=b["days"])
    points = []
    for r in reversed(records):  # dal piu' vecchio
        from jenny.board import model

        stamp = model.parse_iso(r.get("ts")) if isinstance(r.get("ts"), str) else None
        val = _num(r.get(b["field"]))
        if stamp is None or val is None:
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        if stamp >= since:
            points.append(val)
    if len(points) > MAX_CHART_POINTS:
        step = len(points) / MAX_CHART_POINTS
        points = [points[int(i * step)] for i in range(MAX_CHART_POINTS - 1)] + [points[-1]]
    band = None
    alarm = section.alarm or {}
    if alarm.get("field") == b["field"] and _num(alarm.get("value")) is not None:
        band = {"op": alarm["op"], "value": alarm["value"]}
    return {"points": points, "unit": b["unit"], "band": band, "days": b["days"]}


def _b_status(b, latest, base):
    out = {"state": base.get("state"), "at": base.get("at") or ""}
    if b.get("online") and latest is not None:
        out["online"] = latest.get(b["online"]) is True
    if b.get("text") and latest is not None:
        out["text"] = latest.get(b["text"])
    return out


def _b_progress(b, latest):
    value = _num(latest.get(b["field"])) if latest else None
    maximum = b["max"]
    if isinstance(maximum, str):
        maximum = _num(latest.get(maximum)) if latest else None
    pct = round(min(100.0, max(0.0, value / maximum * 100)), 1) if value is not None and maximum \
        else None
    return {"value": value, "max": maximum, "pct": pct, "unit": b["unit"], "label": b["label"]}


def _rows(records, n):
    return records[:n], max(0, len(records) - n)


def _b_bars(b, data):
    records = data.records(b["collection"], b["where"])
    shown, more = _rows(records, b["max"])
    rows = [{"label": r.get(b["label"]), "value": r.get(b["value"]),
             "weight": _num(r.get(b["weight"])),
             "target": _num(r.get(b["target"])) if b.get("target") else None} for r in shown]
    return {"items": rows, "more": more, "unit": b["unit"]}


def _b_table(b, data):
    records = _newest_first(data.records(b["collection"], b["where"]))
    shown, more = _rows(records, b["max"])
    return {"columns": [{"field": k, **v} for k, v in b["columns"].items()],
            "items": [{k: r.get(k) for k in b["columns"]} for r in shown], "more": more}


def _b_events(b, data, today):
    upcoming = []
    for r in data.records(b["collection"], b["where"]):
        day = _local_day(r.get(b["date"]))
        if day is not None and day >= today:
            upcoming.append((day, r))
    upcoming.sort(key=lambda x: x[0])
    shown, more = _rows(upcoming, b["max"])
    return {"items": [{"date": d.isoformat(), "text": r.get(b["text"]),
                       "days": (d - today).days} for d, r in shown], "more": more}


def _b_checklist(b, data):
    records = data.records(b["collection"], b["where"])
    todo = [r for r in records if r.get(b["done"]) is not True]
    done = [] if b.get("hideDone") else [r for r in records if r.get(b["done"]) is True]
    ordered = todo + done
    shown, more = _rows(ordered, b["max"])
    return {"items": [{"id": r.get("id"), "text": r.get(b["text"]), "done": r.get(b["done"]) is True}
                      for r in shown], "more": more, "toggle": b.get("toggle"),
            "doneField": b["done"], "left": len(todo)}


def _b_buttons(b, app, params):
    acts = {a.name: a for a in app.manifest.actions} if app.manifest else {}
    out = []
    for it in b["actions"]:
        act = acts.get(it["action"])
        label = it["label"] or (act.description if act else it["action"])
        # Solo i parametri della posa che l'azione dichiara: gli altri farebbero
        # rifiutare la chiamata («unknown params»).
        sent = {k: v for k, v in params.items() if act is not None and k in (act.params or {})}
        out.append({"action": it["action"], "label": label[:28], "params": sent})
    return {"actions": out}


def _b_switches(b, data, app):
    records = _newest_first(data.records(b["collection"], b["where"]))
    latest = records[0] if records else {}
    acts = {a.name: a for a in app.manifest.actions} if app.manifest else {}
    return {"id": latest.get("id"), "items": [
        {"field": it["field"], "label": it["label"], "action": it["action"],
         "on": latest.get(it["field"]) is True,
         # L'id del record va solo a un'azione che lo vuole (set/update).
         "withId": getattr(acts.get(it["action"]), "op", None) in ("set", "update")}
        for it in b["items"]]}


def _b_counter(b, latest):
    return {"items": [{"label": it["label"], "value": latest.get(it["field"]) if latest else None,
                       "dec": it["dec"], "inc": it["inc"]} for it in b["items"]]}


def _b_week(b, data, today):
    monday = today - timedelta(days=today.weekday())
    days = [monday + timedelta(days=i) for i in range(7)]
    have = {_local_day(r.get(b["date"])) for r in data.records(b["collection"], b["where"])}
    return {"days": [{"date": d.isoformat(), "done": d in have} for d in days],
            "today": today.weekday()}


def _b_month(b, data, today):
    first = today.replace(day=1)
    marked, alarm = set(), set()
    alarm_where = b.get("alarmWhere")
    for r in data.records(b["collection"], b["where"]):
        day = _local_day(r.get(b["date"]))
        if day is None or (day.year, day.month) != (today.year, today.month):
            continue
        marked.add(day.day)
        if alarm_where and all(r.get(k) == v for k, v in alarm_where.items()):
            alarm.add(day.day)
    return {"year": first.year, "month": first.month, "firstWeekday": first.weekday(),
            "days": sorted(marked), "alarm": sorted(alarm), "today": today.day}


def _b_note(b, latest):
    text = latest.get(b["field"]) if latest else None
    return {"text": str(text)[:400] if text is not None else "", "lines": b["rows"]}


def _meta(meta, latest, data):
    if not meta:
        return None
    if meta.get("count"):
        return {"value": len(data.records(meta["collection"], meta.get("where"))),
                "label": meta["label"]}
    return {"value": latest.get(meta["field"]) if latest else None, "unit": meta.get("unit", ""),
            "label": meta.get("label", "")}


def slip_view(workspace, app: LoadedApp, params: Mapping[str, Any], *,
              now: datetime | None = None) -> dict[str, Any]:
    """La striscia di *app* per una posa con *params*: taglia, dato in alto, stato, blocchi.

    Senza blocchi (un'app senza dati) torna ``{}``: la bacheca la mostra come una
    polaroid con la sua icona.
    """
    section = app.manifest.section if app.manifest else None
    if section is None or not section.blocks:
        return {}
    current = now or datetime.now(timezone.utc)
    today = current.astimezone().date()
    base = summary_view(workspace, app, params, now=current)
    default = section.collection or READINGS
    data = _Data(app, params, dict(section.where), default)
    records = _newest_first(data.records(default))
    latest = records[0] if records else None
    blocks = []
    for b in section.blocks:
        kind = b["kind"]
        try:
            if kind == "value":
                payload = _b_value(b, latest, records, section, current)
            elif kind == "numbers":
                payload = _b_numbers(b, latest, data)
            elif kind == "chart":
                payload = _b_chart(b, records, section, current)
            elif kind == "status":
                payload = _b_status(b, latest, base)
            elif kind == "progress":
                payload = _b_progress(b, latest)
            elif kind == "bars":
                payload = _b_bars(b, data)
            elif kind == "table":
                payload = _b_table(b, data)
            elif kind == "events":
                payload = _b_events(b, data, today)
            elif kind == "checklist":
                payload = _b_checklist(b, data)
            elif kind == "buttons":
                payload = _b_buttons(b, app, params)
            elif kind == "switches":
                payload = _b_switches(b, data, app)
            elif kind == "counter":
                payload = _b_counter(b, latest)
            elif kind == "week":
                payload = _b_week(b, data, today)
            elif kind == "month":
                payload = _b_month(b, data, today)
            elif kind == "note":
                payload = _b_note(b, latest)
            else:
                payload = {}
        except Exception as exc:  # noqa: BLE001 — un blocco che non legge resta vuoto
            logger.warning("apps: strip block {} of {} failed: {}", kind, app.slug, exc)
            payload = {"failed": True}
        blocks.append({"kind": kind, "rows": b["rows"], **payload})
    return {
        "size": section.size, "name": base.get("name"), "icon": base.get("icon"),
        "hasScreen": base.get("hasScreen"), "state": base.get("state"),
        "at": base.get("at") or "", "meta": _meta(section.meta, latest, data),
        "recordId": latest.get("id") if latest else None, "blocks": blocks,
    }
