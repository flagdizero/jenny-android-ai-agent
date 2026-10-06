"""La striscia di un'app: i blocchi del suo ``summary``, convalidati, e la sua taglia.

Sulla bacheca di un quaderno un'app e' una **striscia** di carta: un'intestazione
(icona, nome, un dato a destra) e sotto pochi blocchi presi da un vocabolario
chiuso. L'app la *descrive* nel manifest; la casa la *disegna*
(``home-board-blocks.js``) — niente HTML dell'agente, niente iframe. Questo
modulo e' la descrizione: puro, senza I/O.

Le taglie sono fisse e contate in **righe** (``jenny.board.model.SLIP_ROWS``):
S 2 · M 4 · L 6 · XL 9. Ogni blocco occupa righe intere; una lista conta al suo
``max``, cosi' la taglia viene dal manifest e non dai dati, e la bacheca non
salta quando i dati cambiano. Oltre XL l'app e' rotta, con un errore leggibile.

I controlli (spunta, tasto, interruttore, − / +) chiamano **solo azioni gia'
dichiarate** dall'app: la striscia non sa fare niente che l'app non sappia gia'.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from jenny.board.model import SLIP_ROWS

MAX_BLOCKS = 4
MAX_LIST = 5
MAX_COLUMNS = 3
MAX_BUTTONS = 6
MAX_SWITCHES = 4
MAX_COUNTERS = 2
MAX_NUMBERS = 3
MAX_NOTE_LINES = 3
MAX_CHART_DAYS = 31
KINDS = ("value", "numbers", "chart", "status", "progress", "bars", "table", "events",
         "checklist", "buttons", "switches", "counter", "week", "month", "note")
# I blocchi che leggono un campo dell'ultimo record (e che quindi possono dare
# alla striscia il suo «valore» per la chat e l'orologio).
VALUE_KINDS = ("value", "chart", "progress")
_NAME_RE = re.compile(r"\A[a-zA-Z_][a-zA-Z0-9_]{0,63}\Z")
_COLLECTION_RE = re.compile(r"\A[a-z0-9_][a-z0-9_-]{0,63}\Z")


@dataclass
class SlipSpec:
    """La striscia convalidata: il dato in alto, i blocchi, la taglia."""

    blocks: list[dict] = field(default_factory=list)
    meta: dict | None = None
    size: str = "S"
    rows: int = 0


def size_for(rows: int) -> str | None:
    """La taglia piu' piccola che contiene *rows* righe, o ``None`` oltre XL."""
    for name, cap in SLIP_ROWS.items():
        if rows <= cap:
            return name
    return None


class _Ctx:
    """Quel che serve per convalidare: le azioni, il lettore, la collezione di base."""

    def __init__(self, where_: str, actions: list[Any], reader: dict | None,
                 collection: str | None, params: dict) -> None:
        self.where_ = where_
        self.actions = {a.name: a for a in actions}
        self.reader = reader
        self.collection = collection
        self.params = params

    def fail(self, msg: str) -> None:
        raise ValueError(f"{self.where_}{msg}")

    def name(self, raw: Any, what: str) -> str:
        if not isinstance(raw, str) or not _NAME_RE.match(raw):
            self.fail(f".{what} must be a field name")
        return raw

    def reader_field(self, name: str, what: str, types: tuple[str, ...] | None = None) -> None:
        """Con un lettore che legge ``readings`` i campi sono i suoi: si controllano."""
        if self.reader is None:
            return
        spec = self.reader["fields"].get(name)
        if spec is None:
            self.fail(f".{what}: {name!r} is not a field of the reader "
                      f"({', '.join(self.reader['fields'])})")
        if types and spec.get("type") not in types:
            self.fail(f".{what}: {name!r} is a {spec.get('type')} field, needs {'/'.join(types)}")

    def source(self, raw: dict) -> str:
        """La collezione che il blocco legge: la sua, o quella del riassunto."""
        coll = raw.get("collection")
        if coll is None:
            if self.collection is None:
                self.fail(".collection is required (the summary has none)")
            return self.collection  # type: ignore[return-value]
        if not isinstance(coll, str) or not _COLLECTION_RE.match(coll):
            self.fail(".collection must be a collection name")
        return coll

    def where(self, raw: dict) -> dict:
        filt = raw.get("where") or {}
        if not isinstance(filt, dict):
            self.fail(".where must be an object")
        for key, val in filt.items():
            if isinstance(val, str) and val.startswith("$") and val[1:] not in self.params:
                self.fail(f".where.{key}: {val} is not a declared param")
        return dict(filt)

    def action(self, raw: Any, what: str, *, needs: tuple[str, ...] = (),
               op: str | None = None, collection: str | None = None) -> str:
        if not isinstance(raw, str) or raw not in self.actions:
            self.fail(f".{what}: {raw!r} is not an action of this app")
        act = self.actions[raw]
        if op is not None and (act.kind != "storage" or act.op != op):
            self.fail(f".{what}: {raw!r} must be a storage '{op}' action")
        if collection is not None and act.collection != collection:
            self.fail(f".{what}: {raw!r} writes {act.collection!r}, not {collection!r}")
        for name in needs:
            if name not in (act.params or {}):
                self.fail(f".{what}: {raw!r} must declare the param {name!r}")
        # Un tasto non sa chiedere niente: i parametri obbligatori sono solo quelli
        # che la striscia puo' riempire (la posa, e quelli che il blocco manda).
        reserved = {"id"} if act.op in ("set", "update", "delete") else set()
        missing = [p for p in act.required if p not in self.params and p not in needs
                   and p not in reserved]
        if missing:
            self.fail(f".{what}: {raw!r} requires {', '.join(missing)}, which a tap cannot "
                      "fill — open the app for it, or make the param optional")
        return raw

    def max_rows(self, raw: dict, default: int = MAX_LIST) -> int:
        n = raw.get("max", default)
        if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= MAX_LIST:
            self.fail(f".max must be between 1 and {MAX_LIST}")
        return n

    def text(self, raw: dict, key: str) -> str:
        val = raw.get(key, "")
        if not isinstance(val, str):
            self.fail(f".{key} must be text")
        return val.strip()[:40]


# ── i blocchi ──────────────────────────────────────────────────────────────


def _value(c: _Ctx, raw: dict) -> dict:
    fld = c.name(raw.get("field"), "field")
    c.reader_field(fld, "field", ("number", "text", "bool"))
    out = {"field": fld, "unit": c.text(raw, "unit"), "label": c.text(raw, "label"),
           "trend": bool(raw.get("trend", True))}
    if raw.get("delta") is not None:
        out["delta"] = c.name(raw.get("delta"), "delta")
        c.reader_field(out["delta"], "delta", ("number", "text"))
        out["deltaUnit"] = c.text(raw, "deltaUnit")
    return {**out, "rows": 2}


def _numbers(c: _Ctx, raw: dict) -> dict:
    items = raw.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_NUMBERS:
        c.fail(f".items must list 1 to {MAX_NUMBERS} numbers")
    out = []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            c.fail(f".items[{i}] must be an object")
        entry = {"label": c.text(it, "label"), "unit": c.text(it, "unit")}
        if it.get("count"):
            entry["count"] = True
            entry["where"] = c.where(it)
            entry["collection"] = c.source(it)
        else:
            entry["field"] = c.name(it.get("field"), f"items[{i}].field")
            c.reader_field(entry["field"], f"items[{i}].field")
        out.append(entry)
    return {"items": out, "rows": 2}


def _chart(c: _Ctx, raw: dict) -> dict:
    fld = c.name(raw.get("field"), "field")
    c.reader_field(fld, "field", ("number",))
    days = raw.get("days", 7)
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= MAX_CHART_DAYS:
        c.fail(f".days must be between 1 and {MAX_CHART_DAYS}")
    return {"field": fld, "days": days, "unit": c.text(raw, "unit"), "rows": 2}


def _status(c: _Ctx, raw: dict) -> dict:
    out: dict = {"rows": 1}
    for key in ("online", "text"):
        if raw.get(key) is not None:
            out[key] = c.name(raw.get(key), key)
            c.reader_field(out[key], key, ("bool",) if key == "online" else ("text", "number"))
    return out


def _progress(c: _Ctx, raw: dict) -> dict:
    fld = c.name(raw.get("field"), "field")
    c.reader_field(fld, "field", ("number",))
    maximum = raw.get("max")
    if isinstance(maximum, str):
        c.name(maximum, "max")
    elif isinstance(maximum, bool) or not isinstance(maximum, (int, float)) or maximum <= 0:
        c.fail(".max must be a positive number or the field that holds the target")
    return {"field": fld, "max": maximum, "unit": c.text(raw, "unit"),
            "label": c.text(raw, "label"), "rows": 1}


def _bars(c: _Ctx, raw: dict) -> dict:
    n = c.max_rows(raw, 3)
    out = {"collection": c.source(raw), "where": c.where(raw), "max": n,
           "label": c.name(raw.get("label"), "label"), "value": c.name(raw.get("value"), "value"),
           "weight": c.name(raw.get("weight"), "weight"), "unit": c.text(raw, "unit")}
    if raw.get("target") is not None:
        out["target"] = c.name(raw.get("target"), "target")
    return {**out, "rows": n}


def _table(c: _Ctx, raw: dict) -> dict:
    n = c.max_rows(raw)
    cols = raw.get("columns")
    if not isinstance(cols, dict) or not 1 <= len(cols) <= MAX_COLUMNS:
        c.fail(f".columns must map 1 to {MAX_COLUMNS} field names to {{label, type, unit}}")
    columns = {}
    for name, spec in cols.items():
        c.name(name, f"columns.{name}")
        spec = spec if isinstance(spec, dict) else {}
        columns[name] = {"label": str(spec.get("label") or name)[:24],
                         "type": spec.get("type") if spec.get("type") in ("number", "bool", "text")
                         else "text",
                         "unit": str(spec.get("unit") or "")[:8]}
    return {"collection": c.source(raw), "where": c.where(raw), "max": n, "columns": columns,
            "rows": n}


def _events(c: _Ctx, raw: dict) -> dict:
    n = c.max_rows(raw, 3)
    return {"collection": c.source(raw), "where": c.where(raw), "max": n,
            "date": c.name(raw.get("date"), "date"), "text": c.name(raw.get("text"), "text"),
            "rows": n}


def _checklist(c: _Ctx, raw: dict) -> dict:
    n = c.max_rows(raw)
    coll = c.source(raw)
    done = c.name(raw.get("done"), "done")
    out = {"collection": coll, "where": c.where(raw), "max": n,
           "text": c.name(raw.get("text"), "text"), "done": done,
           "hideDone": bool(raw.get("hideDone", False))}
    if raw.get("toggle") is not None:
        out["toggle"] = c.action(raw.get("toggle"), "toggle", needs=(done,), op="update",
                                 collection=coll)
    return {**out, "rows": n}


def _buttons(c: _Ctx, raw: dict) -> dict:
    items = raw.get("actions")
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_BUTTONS:
        c.fail(f".actions must list 1 to {MAX_BUTTONS} actions")
    out = []
    for i, it in enumerate(items):
        name = it.get("action") if isinstance(it, dict) else it
        act = c.action(name, f"actions[{i}]")
        label = c.text(it, "label") if isinstance(it, dict) else ""
        out.append({"action": act, "label": label})
    return {"actions": out, "rows": (len(out) + 2) // 3}


def _switches(c: _Ctx, raw: dict) -> dict:
    items = raw.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_SWITCHES:
        c.fail(f".items must list 1 to {MAX_SWITCHES} switches")
    coll = c.source(raw)
    out = []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            c.fail(f".items[{i}] must be an object")
        fld = c.name(it.get("field"), f"items[{i}].field")
        out.append({"field": fld, "label": c.text(it, "label"),
                    "action": c.action(it.get("action"), f"items[{i}].action", needs=(fld,))})
    return {"collection": coll, "where": c.where(raw), "items": out, "rows": len(out)}


def _counter(c: _Ctx, raw: dict) -> dict:
    items = raw.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_COUNTERS:
        c.fail(f".items must list 1 or {MAX_COUNTERS} counters")
    out = []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            c.fail(f".items[{i}] must be an object")
        fld = c.name(it.get("field"), f"items[{i}].field")
        c.reader_field(fld, f"items[{i}].field", ("number",))
        out.append({"field": fld, "label": c.text(it, "label"),
                    "dec": c.action(it.get("dec"), f"items[{i}].dec"),
                    "inc": c.action(it.get("inc"), f"items[{i}].inc")})
    return {"items": out, "rows": 2}


def _week(c: _Ctx, raw: dict) -> dict:
    return {"collection": c.source(raw), "where": c.where(raw),
            "date": c.name(raw.get("date", "ts"), "date"), "rows": 2}


def _month(c: _Ctx, raw: dict) -> dict:
    out = {"collection": c.source(raw), "where": c.where(raw),
           "date": c.name(raw.get("date", "ts"), "date")}
    if raw.get("alarmWhere") is not None:
        if not isinstance(raw["alarmWhere"], dict):
            c.fail(".alarmWhere must be an object")
        out["alarmWhere"] = dict(raw["alarmWhere"])
    return {**out, "rows": 6}


def _note(c: _Ctx, raw: dict) -> dict:
    fld = c.name(raw.get("field"), "field")
    c.reader_field(fld, "field", ("text",))
    lines = raw.get("lines", 2)
    if isinstance(lines, bool) or not isinstance(lines, int) or not 1 <= lines <= MAX_NOTE_LINES:
        c.fail(f".lines must be between 1 and {MAX_NOTE_LINES}")
    return {"field": fld, "rows": lines}


_PARSERS = {
    "value": _value, "numbers": _numbers, "chart": _chart, "status": _status,
    "progress": _progress, "bars": _bars, "table": _table, "events": _events,
    "checklist": _checklist, "buttons": _buttons, "switches": _switches, "counter": _counter,
    "week": _week, "month": _month, "note": _note,
}


def _meta(c: _Ctx, raw: Any) -> dict | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        c.fail(".meta must be an object: {field, unit, label} or {count, where, label}")
    if raw.get("count"):
        return {"count": True, "where": c.where(raw), "collection": c.source(raw),
                "label": c.text(raw, "label")}
    fld = c.name(raw.get("field"), "meta.field")
    c.reader_field(fld, "meta.field")
    return {"field": fld, "unit": c.text(raw, "unit"), "label": c.text(raw, "label")}


def parse_slip(raw: dict, *, actions: list[Any], reader: dict | None, collection: str | None,
               params: dict) -> SlipSpec:
    """``summary.blocks`` e ``summary.meta`` convalidati, con la taglia.

    *collection* e' quella del riassunto (``readings`` con un lettore): un blocco
    che non ne nomina una legge quella. ``ValueError`` con un messaggio che si
    legge, nello stile del resto del manifest.
    """
    base = "app.json: summary"
    blocks_raw = raw.get("blocks")
    if not isinstance(blocks_raw, list) or not 1 <= len(blocks_raw) <= MAX_BLOCKS:
        raise ValueError(f"{base}.blocks must list 1 to {MAX_BLOCKS} blocks")
    blocks = []
    for i, b in enumerate(blocks_raw):
        kind = b.get("kind") if isinstance(b, dict) else None
        if kind not in _PARSERS:
            raise ValueError(f"{base}.blocks[{i}].kind must be one of {list(KINDS)}")
        c = _Ctx(f"{base}.blocks[{i}] ({kind})", actions, reader if
                 (b.get("collection") in (None, "readings")) else None, collection, params)
        blocks.append({"kind": kind, **_PARSERS[kind](c, b)})
    months = [b for b in blocks if b["kind"] == "month"]
    rows = sum(b["rows"] for b in blocks)
    size = size_for(rows)
    if size is None:
        raise ValueError(f"{base}.blocks take {rows} rows: a strip holds at most "
                         f"{SLIP_ROWS['XL']} (XL). Show less on the board; the rest stays in "
                         "the app")
    if months:
        size = "XL"
    meta = _meta(_Ctx(f"{base}", actions, reader, collection, params), raw.get("meta"))
    return SlipSpec(blocks=blocks, meta=meta, size=size, rows=rows)


def legacy_blocks(section: Any) -> SlipSpec:
    """Le forme di prima (``view``) come blocchi, senza riscrivere il manifest.

    value → valore (+ i chip come numeri); chips → numeri; progress → valore e
    avanzamento; list / table → tabella (al piu' 3 colonne, 5 righe); i verbi
    → tasti. Il dato in alto, per una lista, e' il conteggio.
    """
    blocks: list[dict] = []
    meta = None
    view = section.view
    fld = section.field
    if view in ("list", "table"):
        cols = dict(list((section.columns or {}).items())[:MAX_COLUMNS])
        if cols:
            blocks.append({"kind": "table", "collection": section.collection,
                           "where": dict(section.where), "max": MAX_LIST, "columns": cols,
                           "rows": MAX_LIST})
        meta = {"count": True, "where": dict(section.where), "collection": section.collection,
                "label": section.label}
    elif view == "chips":
        names = [fld, *[c for c in section.chips if c != fld]] if fld else list(section.chips)
        if not names and section.fields:
            names = [k for k, v in section.fields.items() if v.get("type") != "rows"]
        items = [{"field": n, "label": (section.fields.get(n) or {}).get("label", n),
                  "unit": (section.fields.get(n) or {}).get("unit", "")}
                 for n in names[:MAX_NUMBERS]]
        if items:
            blocks.append({"kind": "numbers", "items": items, "rows": 2})
    elif fld:
        blocks.append({"kind": "value", "field": fld, "unit": section.unit,
                       "label": section.label, "trend": section.trend_days > 0, "rows": 2})
        if view == "progress" and section.max:
            blocks.append({"kind": "progress", "field": fld, "max": section.max,
                           "unit": section.unit, "label": "", "rows": 1})
        extras = [c for c in section.chips if c != fld]
        if not extras and section.fields:
            extras = [k for k, v in section.fields.items()
                      if k != fld and v.get("type") not in ("rows",)][:2]
        if extras:
            blocks.append({"kind": "numbers", "rows": 2, "items": [
                {"field": n, "label": (section.fields.get(n) or {}).get("label", n),
                 "unit": (section.fields.get(n) or {}).get("unit", "")}
                for n in extras[:MAX_NUMBERS]]})
    if section.verbs:
        blocks.append({"kind": "buttons", "rows": 1,
                       "actions": [{"action": v, "label": ""} for v in section.verbs]})
    rows = sum(b["rows"] for b in blocks)
    size = size_for(rows) or "XL"
    return SlipSpec(blocks=blocks, meta=meta, size=size, rows=rows)


def first_value_field(raw_blocks: Any) -> str | None:
    """Il campo del primo blocco che mostra un valore: il «valore» dell'app per la
    chat, l'orologio e le attese a condizione."""
    for b in raw_blocks if isinstance(raw_blocks, list) else []:
        if isinstance(b, dict) and b.get("kind") in VALUE_KINDS and isinstance(b.get("field"), str):
            return b["field"]
        if isinstance(b, dict) and b.get("kind") == "numbers":
            for it in b.get("items") or []:
                if isinstance(it, dict) and isinstance(it.get("field"), str):
                    return it["field"]
    return None
