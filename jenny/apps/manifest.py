"""Jenny App manifest loading and validation.

An app is a folder in ``<workspace>/apps/<slug>/`` with an ``app.json``
manifest optionally declaring typed actions (see ``docs/contribute/write-a-mini-app.md``
and the ``app-creator`` skill reference). Loading never raises: malformed apps come
back as ``LoadedApp(broken=True, error=...)`` so the grid can show them as
broken without ever crashing the gateway.

``actions`` is optional: an app whose only job is to draw a screen has nothing
agent-facing to declare, and requiring a non-empty list made that app
impossible to write — the observed result was an *invented* action added only to
satisfy the schema. The design always described the agent-facing
side as something an app "can" carry; the code disagreed until Sept 2026.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# I nomi validi di app, azioni e collezioni: una sola copia, qui (il validatore
# della skill che li duplicava e' uno stub dal 07/10/2026).
SLUG_RE = re.compile(r"\A[a-z0-9]+(-[a-z0-9]+)*\Z")
ACTION_NAME_RE = re.compile(r"\A[a-z][a-z0-9_]*\Z")
COLLECTION_RE = re.compile(r"\A[a-z0-9]+(-[a-z0-9]+)*\Z")
PLACEHOLDER_RE = re.compile(r"\{([^}]+)\}")
STORAGE_OPS = {"append", "set", "update", "delete", "query"}
HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
MAX_SLUG_LEN = 32
# Kind ammessi per il blocco ``view``. "external" vuol dire: lo schermo dell'app
# e' la UI del suo ``server.baseUrl``, servita attraverso il proxy su loopback
# (``apps/proxy.py``) invece di ``app/index.html``. Esiste perche' la policy
# cleartext dell'APK rifiuta un iframe verso un ``http://`` non-loopback, quindi
# "incornicia il mio server" non era esprimibile in nessun modo.
VIEW_KINDS = {"external"}
# Gli operatori di un allarme della sezione compatta. Gli stessi delle attese a
# condizione della bacheca (``jenny/board/model.py::CONDITION_OPS``).
SECTION_OPS = {"<", "<=", ">", ">=", "==", "!="}
SECTION_PARAM_RE = re.compile(r"\A[a-z][a-zA-Z0-9_]*\Z")
MAX_SECTION_TREND_DAYS = 31


@dataclass
class AppAction:
    name: str
    description: str
    kind: str  # "storage" | "http"
    params: dict = field(default_factory=dict)
    required: list = field(default_factory=list)
    # storage
    op: str | None = None
    collection: str | None = None
    # http
    method: str | None = None
    path: str | None = None


@dataclass
class AppManifest:
    name: str
    description: str
    icon: str = "ti-apps"
    server_base_url: str | None = None
    server_auth: dict | None = None
    actions: list[AppAction] = field(default_factory=list)
    # "external" quando lo schermo dell'app *e'* il suo server, non
    # ``app/index.html``. None per un'app normale. V. VIEW_KINDS.
    view_kind: str | None = None
    # Il riassunto: la vista compatta con cui l'app sta in un cassetto o sulla
    # copertina di una scheda. Nel manifest si chiama ``summary``; ``section`` e'
    # il suo nome di prima, e si legge ancora. None per un'app senza stato da
    # riassumere (una calcolatrice): si apre e basta.
    section: "AppSection | None" = None
    # Il lettore: ``run.py`` accanto al manifest, che l'orologio gira ogni
    # ``every`` minuti senza modello. La spec normalizzata come quella di una
    # micro app (``jenny/board/micro.py::normalize_spec``), o None.
    reader: dict | None = None
    # Il campo testo che distingue le serie di un lettore che ne legge piu' d'una
    # (un hub con tre sensori: ``sensor``). None per un lettore a serie unica.
    reader_key: str | None = None


@dataclass
class AppSection:
    """La vista compatta di un'app dentro il cassetto di un soggetto.

    Un valore (l'ultimo record di una collezione, filtrato dai parametri della
    sezione), uno stato (vecchio oltre una soglia, o in allarme), un andamento
    sugli ultimi giorni, e al più due azioni. **Solo lettura dei dati**: la
    sezione non chiama l'app, legge quel che il suo cron le ha lasciato.

    *params* sono i parametri che la sezione chiede quando la si aggancia a un
    cassetto (``{"sensor": {"type": "string"}}``): la stessa app sta nel cassetto
    di due piante con due sensori diversi. Nei filtri si scrivono ``$nome``.
    """

    collection: str
    field: str
    params: dict = field(default_factory=dict)
    where: dict = field(default_factory=dict)
    latest: str = "ts"
    unit: str = ""
    label: str = ""
    stale_after_minutes: int | None = None
    alarm: dict | None = None
    trend_days: int = 0
    verbs: list[str] = field(default_factory=list)
    # ── dal 06/10/2026: il riassunto ha una forma, come le micro app ──
    # value | chips | progress | list | table (``jenny/board/micro.py::VIEWS``).
    view: str = "value"
    # last: il campo dell'ultimo record. count: quanti record passano il filtro
    # («7 da comprare»); le righe di una lista sono quei record.
    aggregate: str = "last"
    # I campi da mostrare accanto al principale (chip), in ordine.
    chips: list[str] = field(default_factory=list)
    # Le colonne di una lista o tabella: ``{col: {label, type, unit}}``.
    columns: dict = field(default_factory=dict)
    # La meta di ``progress``.
    max: float | None = None
    # Il tipo del campo principale, quando il lettore lo dichiara.
    type: str = ""
    # Le etichette e le unita' dei campi del lettore, per i chip.
    fields: dict = field(default_factory=dict)
    # ── dal 06/10/2026: la striscia sulla bacheca (``jenny/apps/slip_spec.py``) ──
    # I blocchi convalidati, il dato in alto, la taglia (S/M/L/XL). Le forme di
    # prima arrivano qui tradotte in blocchi: la bacheca ne conosce una sola.
    blocks: list = field(default_factory=list)
    meta: dict | None = None
    size: str = ""


@dataclass
class LoadedApp:
    slug: str
    dir: Path
    manifest: AppManifest | None = None
    broken: bool = False
    error: str = ""


def action_param_schema(action: AppAction) -> dict:
    """Root JSON Schema for an action's parameters (provider/tool compatible).

    Storage ops get their reserved params injected when the manifest doesn't
    declare them, so the LLM and the UI see the full contract: ``id`` for
    set/update/delete (required at runtime), ``limit`` for query (page size,
    not a filter). Undeclared params are rejected at validation time.
    """
    properties = dict(action.params)
    required = list(action.required)
    if action.kind == "storage":
        if action.op in ("set", "update", "delete") and "id" not in properties:
            properties["id"] = {
                "type": "string",
                "description": "Record id (auto-assigned on append)",
            }
            required.append("id")
        elif action.op == "query" and "limit" not in properties:
            properties["limit"] = {
                "type": "integer",
                "description": "Max records returned (default 200); not a filter",
            }
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _parse_action(raw: object, index: int, has_server: bool) -> AppAction:
    """Parse one action entry; raises ValueError with a readable message."""
    where = f"actions[{index}]"
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: must be an object")

    name = raw.get("name")
    if not isinstance(name, str) or not ACTION_NAME_RE.match(name):
        raise ValueError(f"{where}: 'name' must be snake_case (got {name!r})")
    where = f"actions[{index}] ({name})"

    description = raw.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ValueError(f"{where}: 'description' is required")

    params = raw.get("params", {})
    if not isinstance(params, dict):
        raise ValueError(f"{where}: 'params' must be an object mapping name -> JSON Schema")
    for pname, schema in params.items():
        if not isinstance(schema, dict) or "type" not in schema:
            raise ValueError(f"{where}: param '{pname}' must be a JSON Schema object with 'type'")
    required = raw.get("required", [])
    # ``isinstance`` prima dell'``in``: un elemento lista o dict non e' hashable,
    # e il ``TypeError`` che ne usciva non lo raccoglie nessuno fino ad
    # ``AgentLoop`` — il gateway non partiva. Stesso motivo per op/method/kind.
    if not isinstance(required, list) or any(
        not isinstance(r, str) or r not in params for r in required
    ):
        raise ValueError(f"{where}: 'required' must list a subset of params {sorted(params)}")

    kind = raw.get("kind")
    action = AppAction(
        name=name,
        description=description.strip(),
        kind=str(kind),
        params=params,
        required=required,
    )

    if kind == "storage":
        op = raw.get("op")
        if not isinstance(op, str) or op not in STORAGE_OPS:
            raise ValueError(f"{where}: 'op' must be one of {sorted(STORAGE_OPS)}")
        collection = raw.get("collection")
        if not isinstance(collection, str) or not COLLECTION_RE.match(collection):
            raise ValueError(f"{where}: 'collection' must be lowercase alphanumeric/hyphens")
        action.op = op
        action.collection = collection
    elif kind == "http":
        method = raw.get("method")
        if not isinstance(method, str) or method not in HTTP_METHODS:
            raise ValueError(f"{where}: 'method' must be one of {sorted(HTTP_METHODS)}")
        path = raw.get("path")
        if not isinstance(path, str) or not path.startswith("/"):
            raise ValueError(f"{where}: 'path' must start with '/'")
        missing = [p for p in PLACEHOLDER_RE.findall(path) if p not in params]
        if missing:
            raise ValueError(f"{where}: path placeholders not declared in params: {missing}")
        if not has_server:
            raise ValueError(f"{where}: kind 'http' requires a top-level 'server.baseUrl'")
        action.method = method
        action.path = path
    else:
        raise ValueError(f"{where}: 'kind' must be 'storage' or 'http' (got {kind!r})")

    return action


def _parse_manifest(data: object, *, strict: bool = False) -> AppManifest:
    """Parse a decoded app.json; raises ValueError with a readable message."""
    if not isinstance(data, dict):
        raise ValueError("app.json: top level must be an object")

    for fname in ("name", "description"):
        if not isinstance(data.get(fname), str) or not data[fname].strip():
            raise ValueError(f"app.json: '{fname}' is required")

    server = data.get("server")
    base_url: str | None = None
    server_auth: dict | None = None
    if server is not None:
        if not isinstance(server, dict) or not isinstance(server.get("baseUrl"), str):
            raise ValueError("app.json: 'server' must be an object with a 'baseUrl' string")
        base_url = server["baseUrl"]
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("app.json: server.baseUrl must start with http:// or https://")
        if server.get("auth") is not None:
            # Rifiutato in fase di caricamento, non solo di esecuzione. Il
            # credential store non esiste (roadmap) e ``execute_http_action`` e'
            # fail-closed, quindi un manifest con ``auth`` produceva un'app che
            # passava la validazione, si apriva, e aveva *tutte* le azioni http
            # morte con un 501 — visibile solo a chi tentava un'azione. Un
            # contratto irrealizzabile e' un'app rotta, e va detto qui, dove
            # l'errore finisce sotto gli occhi di chi ha scritto il manifest.
            raise ValueError(
                "app.json: server.auth is declared but app-server credentials are not "
                "supported yet — remove the 'auth' block (every http action would be "
                "refused with 501)"
            )

    raw_actions = data.get("actions") or []
    if not isinstance(raw_actions, list):
        raise ValueError("app.json: 'actions' must be an array")
    actions = [
        _parse_action(raw, i, has_server=base_url is not None)
        for i, raw in enumerate(raw_actions)
    ]
    names = [a.name for a in actions]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise ValueError(f"app.json: duplicate action names: {dupes}")

    view = data.get("view")
    view_kind: str | None = None
    if view is not None:
        if not isinstance(view, dict):
            raise ValueError("app.json: 'view' must be an object")
        view_kind = view.get("kind")
        if not isinstance(view_kind, str) or view_kind not in VIEW_KINDS:
            raise ValueError(
                f"app.json: view.kind must be one of {sorted(VIEW_KINDS)} (got {view_kind!r})"
            )
        if view_kind == "external" and base_url is None:
            raise ValueError("app.json: view.kind 'external' requires a 'server.baseUrl'")

    summary = _summary_with_blocks(data.get("summary"))
    reader, reader_key = _parse_reader(data.get("reader"), summary, data["name"], strict=strict)
    if summary is not None:
        section = _parse_summary(summary, actions, reader)
    else:
        section = _parse_section(data.get("section"), [a.name for a in actions])
    if section is not None and not section.blocks:
        from jenny.apps.slip_spec import legacy_blocks

        slip = legacy_blocks(section, actions)
        section.blocks, section.meta, section.size = slip.blocks, slip.meta, slip.size

    icon = data.get("icon")
    return AppManifest(
        name=data["name"].strip(),
        description=data["description"].strip(),
        icon=icon if isinstance(icon, str) and icon else "ti-apps",
        server_base_url=base_url,
        server_auth=server_auth,
        actions=actions,
        view_kind=view_kind,
        section=section,
        reader=reader,
        reader_key=reader_key,
    )


def _parse_section(raw: object, action_names: list[str]) -> AppSection | None:
    """Il blocco ``section`` del manifest; solleva ``ValueError`` leggibile.

    Una sezione dichiarata male rende l'app rotta con un errore che si legge,
    come ogni altro difetto del manifest: non fa cadere il gateway, e non fa
    comparire nel cassetto una sezione che non sa disegnarsi.
    """
    if raw is None:
        return None
    where_ = "app.json: section"
    if not isinstance(raw, dict):
        raise ValueError(f"{where_} must be an object")
    params = raw.get("params") or {}
    if not isinstance(params, dict) or not all(
        isinstance(k, str) and SECTION_PARAM_RE.match(k) and isinstance(v, dict)
        for k, v in params.items()
    ):
        raise ValueError(f"{where_}.params must map parameter names to objects")
    value = raw.get("value")
    if not isinstance(value, dict):
        raise ValueError(f"{where_}.value is required (collection and field)")
    collection = value.get("collection")
    if not isinstance(collection, str) or not COLLECTION_RE.match(collection):
        raise ValueError(f"{where_}.value.collection must be a collection name")
    fld = value.get("field")
    if not isinstance(fld, str) or not fld:
        raise ValueError(f"{where_}.value.field is required")
    filters = value.get("where") or {}
    if not isinstance(filters, dict):
        raise ValueError(f"{where_}.value.where must be an object")
    for key, val in filters.items():
        if isinstance(val, str) and val.startswith("$") and val[1:] not in params:
            raise ValueError(f"{where_}.value.where.{key}: {val} is not a declared param")
    latest = value.get("latest", "ts")
    if not isinstance(latest, str) or not latest:
        raise ValueError(f"{where_}.value.latest must be a field name")
    status = raw.get("status") or {}
    if not isinstance(status, dict):
        raise ValueError(f"{where_}.status must be an object")
    stale = status.get("stale_after_minutes")
    if stale is not None and (isinstance(stale, bool) or not isinstance(stale, int) or stale < 1):
        raise ValueError(f"{where_}.status.stale_after_minutes must be a positive integer")
    alarm = status.get("alarm")
    if alarm is not None:
        if (
            not isinstance(alarm, dict)
            or not isinstance(alarm.get("field"), str)
            or alarm.get("op") not in SECTION_OPS
            or isinstance(alarm.get("value"), bool)
            or not isinstance(alarm.get("value"), (int, float, str))
        ):
            raise ValueError(
                f"{where_}.status.alarm must be {{field, op, value}} with op in {sorted(SECTION_OPS)}"
            )
    trend = raw.get("trend") or {}
    if not isinstance(trend, dict):
        raise ValueError(f"{where_}.trend must be an object")
    days = trend.get("days", 0)
    if isinstance(days, bool) or not isinstance(days, int) or not 0 <= days <= MAX_SECTION_TREND_DAYS:
        raise ValueError(f"{where_}.trend.days must be between 0 and {MAX_SECTION_TREND_DAYS}")
    verbs = raw.get("verbs") or []
    if not isinstance(verbs, list) or len(verbs) > 2:
        raise ValueError(f"{where_}.verbs must be a list of at most two action names")
    for verb in verbs:
        if verb not in action_names:
            raise ValueError(f"{where_}.verbs: {verb!r} is not an action of this app")
    return AppSection(
        collection=collection,
        field=fld,
        params=params,
        where=filters,
        latest=latest,
        unit=value.get("unit") if isinstance(value.get("unit"), str) else "",
        label=value.get("label") if isinstance(value.get("label"), str) else "",
        stale_after_minutes=stale,
        alarm=alarm,
        trend_days=days,
        verbs=list(verbs),
    )


def _int(raw: dict, *names: str) -> object:
    for name in names:
        if name in raw:
            return raw[name]
    return None


def _parse_reader(raw: object, summary: object, name: str, *,
                  strict: bool = False) -> tuple[dict | None, str | None]:
    """Il blocco ``reader``: ``run.py`` che l'orologio gira, senza modello.

    Si valida con le stesse regole di una micro app (campi, forma, principale,
    host, skill): sono la stessa cosa, e una sola regola non diverge. Forma e
    principale stanno nel ``summary``, perche' sono di cio' che si mostra.
    """
    if raw is None:
        return None, None
    from jenny.board.micro import MicroError, normalize_spec

    where_ = "app.json: reader"
    if not isinstance(raw, dict):
        raise ValueError(f"{where_} must be an object")
    summ = summary if isinstance(summary, dict) else {}
    uses = raw.get("uses")
    spec_raw = {
        "title": name,
        "fields": raw.get("fields"),
        "every": raw.get("every"),
        "net": raw.get("net"),
        "uses": [uses] if isinstance(uses, str) else uses,
        "view": summ.get("view"),
        "primary": summ.get("primary") or summ.get("field"),
        "alarm": summ.get("alarm"),
        "staleAfter": _int(summ, "staleAfterMinutes", "stale_after_minutes"),
    }
    try:
        spec = normalize_spec(spec_raw, micro_id="reader", strict=strict)
    except MicroError as exc:
        raise ValueError(f"{where_}: {exc}") from exc
    key = raw.get("key")
    if key is not None:
        if not isinstance(key, str) or spec["fields"].get(key, {}).get("type") != "text":
            raise ValueError(f"{where_}.key must name a text field: the series it reads")
    return spec, key


def _clean_columns(raw: object, where_: str) -> dict:
    if not isinstance(raw, dict) or not raw:
        raise ValueError(f"{where_}.columns must map column names to {{label, type, unit}}")
    if len(raw) > 4:
        raise ValueError(f"{where_}.columns: at most 4 columns")
    out = {}
    for col, spec in raw.items():
        spec = spec if isinstance(spec, dict) else {}
        out[str(col)] = {
            "label": str(spec.get("label") or col),
            "type": spec.get("type") if spec.get("type") in ("number", "bool", "text") else "text",
            "unit": str(spec.get("unit") or ""),
        }
    return out


SUMMARY_VIEWS = ("value", "chips", "progress", "list", "table")


def _summary_with_blocks(raw: object) -> object:
    """Un riassunto a blocchi non deve dichiarare la forma di prima: il campo
    principale (per la chat, l'orologio, le attese) e' quello del primo blocco
    che mostra un valore; senza, il riassunto conta i record."""
    if not isinstance(raw, dict) or "blocks" not in raw:
        return raw
    from jenny.apps.slip_spec import first_value_field

    out = dict(raw)
    fld = out.get("field") or out.get("primary") or first_value_field(raw.get("blocks"))
    if fld:
        out.setdefault("primary", fld)
        out.setdefault("field", fld)
    else:
        out.setdefault("aggregate", "count")
    out.pop("view", None)
    return out


def _parse_summary(raw: object, actions: list[AppAction], reader: dict | None) -> AppSection:
    section = _parse_summary_shape(raw, [a.name for a in actions], reader)
    if isinstance(raw, dict) and "blocks" in raw:
        from jenny.apps.slip_spec import parse_slip

        slip = parse_slip(raw, actions=actions, reader=reader,
                          collection=section.collection, params=section.params)
        section.blocks, section.meta, section.size = slip.blocks, slip.meta, slip.size
    return section


def _parse_summary_shape(raw: object, action_names: list[str], reader: dict | None) -> AppSection:
    """Il blocco ``summary``: come l'app si riassume in un cassetto o in copertina.

    Con un lettore, il riassunto e' quello che il lettore legge: la forma, il
    principale, l'allarme e i campi vengono dalla sua spec. Senza, il riassunto
    legge una collezione: l'ultimo record (``aggregate: last``) o quanti record
    passano il filtro (``count``), e una lista o una tabella ne mostra le righe.
    """
    where_ = "app.json: summary"
    if not isinstance(raw, dict):
        raise ValueError(f"{where_} must be an object")
    params = raw.get("params") or {}
    if not isinstance(params, dict) or not all(
        isinstance(k, str) and SECTION_PARAM_RE.match(k) and isinstance(v, dict)
        for k, v in params.items()
    ):
        raise ValueError(f"{where_}.params must map parameter names to objects")
    filters = raw.get("where") or {}
    if not isinstance(filters, dict):
        raise ValueError(f"{where_}.where must be an object")
    for key, val in filters.items():
        if isinstance(val, str) and val.startswith("$") and val[1:] not in params:
            raise ValueError(f"{where_}.where.{key}: {val} is not a declared param")
    verbs = raw.get("verbs") or []
    if not isinstance(verbs, list) or len(verbs) > 2:
        raise ValueError(f"{where_}.verbs must be a list of at most two action names")
    for verb in verbs:
        if verb not in action_names:
            raise ValueError(f"{where_}.verbs: {verb!r} is not an action of this app")
    trend = _int(raw, "trendDays", "trend_days")
    trend = 7 if trend is None and reader is not None else (trend or 0)
    if isinstance(trend, bool) or not isinstance(trend, int) or not 0 <= trend <= MAX_SECTION_TREND_DAYS:
        raise ValueError(f"{where_}.trendDays must be between 0 and {MAX_SECTION_TREND_DAYS}")
    chips = raw.get("chips") or []
    if not isinstance(chips, list) or not all(isinstance(c, str) for c in chips):
        raise ValueError(f"{where_}.chips must be a list of field names")

    if reader is not None:
        fields = reader["fields"]
        primary = reader["primary"]
        fspec = fields[primary]
        columns = dict(fspec.get("columns") or {})
        return AppSection(
            collection="readings", field=primary, params=params, where=filters,
            latest="ts",
            unit=str(raw.get("unit") or fspec.get("unit", "")),
            label=str(raw.get("label") or fspec.get("label") or primary),
            stale_after_minutes=reader["staleAfter"], alarm=reader["alarm"],
            trend_days=trend, verbs=list(verbs), view=reader["view"], aggregate="last",
            chips=[c for c in chips if c in fields and fields[c]["type"] != "rows"],
            columns=columns, max=fspec.get("max"), type=fspec["type"],
            fields={k: {"label": v["label"], "unit": v.get("unit", ""), "type": v["type"]}
                    for k, v in fields.items()},
        )

    collection = raw.get("collection")
    if not isinstance(collection, str) or not COLLECTION_RE.match(collection):
        raise ValueError(f"{where_}.collection must be a collection name (or declare a reader)")
    view = raw.get("view", "value")
    if view not in SUMMARY_VIEWS:
        raise ValueError(f"{where_}.view must be one of {list(SUMMARY_VIEWS)}")
    aggregate = raw.get("aggregate") or ("count" if view in ("list", "table") else "last")
    if aggregate not in ("last", "count"):
        raise ValueError(f"{where_}.aggregate must be 'last' or 'count'")
    if view in ("list", "table") and aggregate != "count":
        raise ValueError(f"{where_}: a {view} shows the records that match, so aggregate is 'count'")
    fld = raw.get("field") or raw.get("primary") or ""
    if not isinstance(fld, str) or (aggregate == "last" and not fld):
        raise ValueError(f"{where_}.field is required (the value to show)")
    columns = _clean_columns(raw.get("columns"), where_) if view in ("list", "table") else {}
    maximum = raw.get("max")
    if view == "progress" and (isinstance(maximum, bool) or not isinstance(maximum, (int, float))):
        raise ValueError(f"{where_}: view 'progress' needs a numeric 'max' (the target)")
    stale = _int(raw, "staleAfterMinutes", "stale_after_minutes")
    if stale is not None and (isinstance(stale, bool) or not isinstance(stale, int) or stale < 1):
        raise ValueError(f"{where_}.staleAfterMinutes must be a positive integer")
    alarm = raw.get("alarm")
    if alarm is not None and (
        not isinstance(alarm, dict) or not isinstance(alarm.get("field"), str)
        or alarm.get("op") not in SECTION_OPS or isinstance(alarm.get("value"), bool)
        or not isinstance(alarm.get("value"), (int, float, str))
    ):
        raise ValueError(f"{where_}.alarm must be {{field, op, value}} with op in {sorted(SECTION_OPS)}")
    latest = raw.get("latest", "ts")
    if not isinstance(latest, str) or not latest:
        raise ValueError(f"{where_}.latest must be a field name")
    return AppSection(
        collection=collection, field=fld, params=params, where=filters, latest=latest,
        unit=str(raw.get("unit") or ""), label=str(raw.get("label") or ""),
        stale_after_minutes=stale, alarm=alarm, trend_days=trend, verbs=list(verbs),
        view=view, aggregate=aggregate, chips=list(chips), columns=columns,
        max=float(maximum) if isinstance(maximum, (int, float)) and not isinstance(maximum, bool)
        else None,
    )


def load_app(app_dir: Path, *, strict: bool = False) -> LoadedApp:
    """Load one app folder. Never raises: problems come back as broken=True.

    *strict* e' per la bozza che si sta scrivendo: cio' che per un'app gia'
    installata si corregge in silenzio diventa un errore (v.
    ``board.micro.normalize_spec``).
    """
    slug = app_dir.name
    app = LoadedApp(slug=slug, dir=app_dir)

    if not SLUG_RE.match(slug) or len(slug) > MAX_SLUG_LEN:
        app.broken = True
        app.error = f"folder name '{slug}' is not a valid slug (lowercase, hyphens, <=32 chars)"
        return app

    manifest_path = app_dir / "app.json"
    if not manifest_path.is_file():
        app.broken = True
        app.error = "app.json is missing"
        return app

    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        app.broken = True
        app.error = f"app.json: invalid JSON ({exc})"
        return app

    try:
        app.manifest = _parse_manifest(data, strict=strict)
        if app.manifest.reader is not None and not (app_dir / "run.py").is_file():
            app.manifest = None
            raise ValueError("app.json: reader is declared but run.py is missing")
    except ValueError as exc:
        app.broken = True
        app.error = str(exc)
    except Exception as exc:  # noqa: BLE001 - la promessa e' «non solleva mai»
        # Una forma che i controlli sopra non prevedono: resta un'app rotta, non
        # un gateway che non parte.
        app.broken = True
        app.error = f"app.json: unexpected structure ({type(exc).__name__}: {exc})"
    return app


def scan_apps(workspace: Path) -> list[LoadedApp]:
    """Scan ``<workspace>/apps`` for app folders, sorted by slug. Never raises."""
    apps_root = Path(workspace) / "apps"
    if not apps_root.is_dir():
        return []
    try:
        entries = sorted(apps_root.iterdir(), key=lambda p: p.name)
    except OSError:
        return []
    apps: list[LoadedApp] = []
    for entry in entries:
        try:
            if not entry.is_dir() or entry.name.startswith("."):
                continue
        except OSError:
            continue
        apps.append(load_app(entry))
    return apps


def find_app(workspace: Path, slug: str) -> LoadedApp | None:
    """Load a single app by slug, or None when the folder doesn't exist."""
    if not SLUG_RE.match(slug):
        return None
    app_dir = Path(workspace) / "apps" / slug
    if not app_dir.is_dir():
        return None
    return load_app(app_dir)
