"""Il motore dei lettori delle app, nato con le micro app dei cassetti.

Fino al 06/10/2026 una micro app era un ``run.py`` attaccato a un cassetto
(``<quaderno>/board/micro/<id>/``), che l'orologio del quaderno girava dopo un
[Tienilo] dell'utente. Adesso e' un'app generale col suo lettore (``reader`` in
``app.json``, v. ``jenny/apps/reader.py``), e all'avvio
``jenny/apps/migrate_micro.py`` sposta le cartelle rimaste in ``workspace/apps``.

Di questo modulo resta quello che il lettore riusa: la spec
(:func:`normalize_spec`, che valida anche il lettore), la validazione delle
letture, la compattazione, ``ctx.http``, il pool e il sandbox dell'esecuzione,
il passo. In coda, quel poco che la migrazione deve ancora saper leggere: dove
stava una micro app, e la sua approvazione da cancellare.

**La linea di fiducia non e' la sandbox** — ``python_exec`` dice da se' di non
esserlo. Il codice gira nello stesso ``PythonNamespace`` di ``python_exec``, con
lo scope del workspace **in sola lettura**: legge, non scrive niente. Le letture
le scrive ``reader.py``, fuori dal codice guardato.
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from jenny.board import model

MICRO_DIRNAME = "micro"
DRAFTS_DIRNAME = ".drafts"
SPEC_FILE = "micro.json"
CODE_FILE = "run.py"
READINGS_FILE = "readings.jsonl"
STATE_FILE = "state.json"

# Il passo dell'orologio, e quindi l'intervallo minimo di una micro app.
MIN_EVERY_MINUTES = 15
DEFAULT_EVERY_MINUTES = 60
MAX_EVERY_MINUTES = 24 * 60
MAX_CODE_BYTES = 6 * 1024
MAX_FIELDS = 8
MAX_TEXT_VALUE = 80
MAX_SOURCE = 160
MAX_NET_HOSTS = 4
# Quanto puo' durare un giro, e quante richieste puo' fare. Il limite vero e'
# il tempo; il numero ferma un lettore che gira in tondo su un server. Era 3
# fino al 07/10/2026: un portafoglio di quattro ETF chiede un prezzo per ETF, e
# Jenny ripiegava su una fonte che rifiuta chi non e' un browser.
RUN_TIMEOUT_S = 20.0
HTTP_TIMEOUT_S = 15.0
MAX_HTTP_REQUESTS = 8
MAX_HTTP_BYTES = 256 * 1024
# Le letture: le ultime 48, piu' l'ultima di ogni giorno per poco piu' di un
# anno (era un mese fino al 07/10/2026: un andamento a 12 mesi non aveva da
# dove nascere). Si compatta oltre ``_COMPACT_AFTER``, che sta sopra a quel che
# la compattazione tiene, o ogni lettura riscriverebbe il file intero.
KEEP_RECENT = 48
KEEP_DAYS = 400
_COMPACT_AFTER = 600
# Tre errori di codice di fila: rotta.
BROKEN_AFTER = 3
_MAX_ERROR = 2000

FIELD_TYPES = ("number", "bool", "text", "rows")
CELL_TYPES = ("number", "bool", "text")
# Le forme che il cassetto sa disegnare. Niente HTML e niente iframe: la micro
# app dice *quale* forma, il cassetto la disegna sempre allo stesso modo.
#   value     un numero grande, con andamento e allarme (il default)
#   chips     piu' valori alla pari
#   progress  un valore verso un obiettivo (``max`` del campo)
#   list      righe: la prima colonna e' il titolo, le altre la riga sotto
#   table     poche colonne, poche righe
VIEWS = ("value", "chips", "progress", "list", "table")
MAX_ROWS = 20
MAX_COLUMNS = 4
# Le righe dell'ultima lettura: nello storico resta solo quante erano (per
# l'andamento, gli eventi e le attese), le righe intere stanno qui.
LATEST_FILE = "latest.json"

_FIELD_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_HOST_RE = re.compile(r"^[a-z0-9.-]{1,253}$")
_SKILL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
# Il campo riservato della provenienza: «dall'hub, sensore Acerello, alle 14:01».
SOURCE_FIELD = "_source"


class MicroError(ValueError):
    """Una spec o un codice che non rispettano il contratto."""


# ── la spec ─────────────────────────────────────────────────────────────────


def _clean_number(value: Any) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def normalize_spec(raw: Any, *, micro_id: str, strict: bool = False) -> dict[str, Any]:
    """La spec di una micro app, ripulita; :class:`MicroError` se non regge.

    Ripulita vuol dire che alcune cose sbagliate si **correggono in silenzio**:
    un ``type`` sconosciuto diventa ``number``, ``every`` si porta dentro
    15–1440, un host di ``net`` con lo schema o la porta, un ``uses`` o un
    ``alarm`` storti si buttano. Va bene per un'app gia' installata, che deve
    continuare a caricarsi; non va bene mentre la si scrive, perche' l'errore
    salta fuori solo quando il lettore gira («host 'x' is not in this micro
    app's net list») e il modello non sa perche'. Con *strict* (la bozza del
    tool ``app``) le stesse cose diventano errori che dicono come scriverle.
    """
    problems: list[str] = []
    if not isinstance(raw, dict):
        raise MicroError("the spec must be an object")
    title = model.clean_text(raw.get("title"), model.MAX_TITLE)
    if not title:
        raise MicroError("a micro app needs a title")
    fields_raw = raw.get("fields")
    if not isinstance(fields_raw, dict) or not fields_raw:
        raise MicroError("declare the fields run() returns: {name: {label, type, unit?}}")
    if len(fields_raw) > MAX_FIELDS:
        raise MicroError(f"at most {MAX_FIELDS} fields")
    fields: dict[str, dict[str, Any]] = {}
    for name, spec in fields_raw.items():
        if not isinstance(name, str) or not _FIELD_RE.match(name):
            raise MicroError(f"field name {name!r}: lowercase letters, digits and _")
        spec = spec if isinstance(spec, dict) else {}
        if spec.get("type") is not None and spec.get("type") not in FIELD_TYPES:
            problems.append(f"field {name!r}: type {spec.get('type')!r} is not one of "
                            f"{', '.join(FIELD_TYPES)}")
        ftype = spec.get("type") if spec.get("type") in FIELD_TYPES else "number"
        entry: dict[str, Any] = {
            "label": model.clean_text(spec.get("label"), model.MAX_TITLE) or name,
            "type": ftype,
            "unit": model.clean_text(spec.get("unit"), 12),
        }
        for bound in ("min", "max"):
            num = _clean_number(spec.get(bound))
            if num is not None and ftype == "number":
                entry[bound] = num
        if ftype == "rows":
            entry["columns"] = _normalize_columns(name, spec.get("columns"))
        fields[name] = entry
    rows_fields = [n for n, f in fields.items() if f["type"] == "rows"]
    if len(rows_fields) > 1:
        raise MicroError("at most one rows field (a list or a table) per micro app")
    view = raw.get("view") if raw.get("view") in VIEWS else (
        "list" if rows_fields else "value")
    scalars = [n for n, f in fields.items() if f["type"] != "rows"]
    if view in ("list", "table"):
        if not rows_fields:
            raise MicroError(f"view {view!r} needs a field of type rows, with its columns")
        primary = rows_fields[0]
    else:
        if not scalars:
            raise MicroError(f"view {view!r} needs at least one number, bool or text field")
        primary = raw.get("primary") if raw.get("primary") in scalars else scalars[0]
        if view == "progress" and (fields[primary]["type"] != "number"
                                   or "max" not in fields[primary]):
            raise MicroError("view 'progress' needs a number primary field with a max "
                             "(the target)")
    try:
        every = int(raw.get("every") or DEFAULT_EVERY_MINUTES)
    except (TypeError, ValueError):
        problems.append(f"every must be a whole number of minutes, not {raw.get('every')!r}")
        every = DEFAULT_EVERY_MINUTES
    if not MIN_EVERY_MINUTES <= every <= MAX_EVERY_MINUTES:
        problems.append(f"every is in minutes, from {MIN_EVERY_MINUTES} to "
                        f"{MAX_EVERY_MINUTES} (once a day): {every} is out of range")
    every = max(MIN_EVERY_MINUTES, min(MAX_EVERY_MINUTES, every))
    alarm = None
    if raw.get("alarm") is not None:
        a = raw["alarm"] if isinstance(raw.get("alarm"), dict) else {}
        if a.get("field") in fields and a.get("op") in model.CONDITION_OPS and isinstance(
            a.get("value"), (int, float, str, bool)
        ):
            alarm = {"field": a["field"], "op": a["op"], "value": a["value"]}
        else:
            problems.append(f"alarm must be {{field, op, value}} with field one of "
                            f"{', '.join(fields)} and op one of "
                            f"{', '.join(sorted(model.CONDITION_OPS))}")
    try:
        stale = int(raw.get("staleAfter") or every * 3)
    except (TypeError, ValueError):
        stale = every * 3
    net = []
    for host in raw.get("net") or []:
        if isinstance(host, str) and _HOST_RE.match(host.strip().lower()):
            net.append(host.strip().lower())
        else:
            problems.append(f"net takes host names only: {host!r} is not one "
                            "(write api.example.com, not https://api.example.com:443/path)")
    if len(net) > MAX_NET_HOSTS:
        raise MicroError(f"at most {MAX_NET_HOSTS} hosts in net")
    uses = [u for u in (raw.get("uses") or []) if isinstance(u, str) and _SKILL_RE.match(u)]
    if len(uses) != len(raw.get("uses") or []):
        problems.append("uses names one installed skill, by its folder name")
    if len(uses) > 1:
        raise MicroError("a micro app can use one skill")
    if strict and problems:
        raise MicroError("; ".join(problems))
    card = raw.get("card")
    out = {
        "id": micro_id,
        "title": title,
        "card": card if model.is_valid_id(card) else "",
        "every": every,
        "fields": fields,
        "primary": primary,
        "view": view,
        "alarm": alarm,
        "staleAfter": max(every, stale),
        "net": sorted(set(net)),
        "uses": uses,
    }
    for stamp in ("created", "updated"):
        if isinstance(raw.get(stamp), str):
            out[stamp] = raw[stamp]
    return out


def _normalize_columns(name: str, raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict) or not raw:
        raise MicroError(f"`{name}` is rows: declare its columns, {{col: {{label, type}}}}")
    if len(raw) > MAX_COLUMNS:
        raise MicroError(f"`{name}`: at most {MAX_COLUMNS} columns")
    cols: dict[str, dict[str, Any]] = {}
    for col, spec in raw.items():
        if not isinstance(col, str) or not _FIELD_RE.match(col):
            raise MicroError(f"column {col!r}: lowercase letters, digits and _")
        spec = spec if isinstance(spec, dict) else {}
        cols[col] = {
            "label": model.clean_text(spec.get("label"), model.MAX_TITLE) or col,
            "type": spec.get("type") if spec.get("type") in CELL_TYPES else "text",
            "unit": model.clean_text(spec.get("unit"), 12),
        }
    return cols


# ── le micro app di prima, per la migrazione ────────────────────────────────


def micros_dir(root: Path) -> Path:
    return root / model.BOARD_DIR / MICRO_DIRNAME


def micro_dir(root: Path, micro_id: str, *, draft: bool = False) -> Path:
    base = micros_dir(root)
    return (base / DRAFTS_DIRNAME / micro_id) if draft else (base / micro_id)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


@dataclass
class Micro:
    id: str
    dir: Path
    spec: dict[str, Any]
    code: str
    draft: bool = False


def load_micro(root: Path, micro_id: str, *, draft: bool = False) -> Micro | None:
    """La micro app *micro_id* del quaderno, o ``None`` se manca o e' rotta."""
    if not model.is_valid_id(micro_id):
        return None
    d = micro_dir(root, micro_id, draft=draft)
    raw = _read_json(d / SPEC_FILE)
    try:
        code = (d / CODE_FILE).read_text(encoding="utf-8")
        spec = normalize_spec(raw, micro_id=micro_id)
    except (OSError, MicroError):
        return None
    return Micro(id=micro_id, dir=d, spec=spec, code=code, draft=draft)


def list_micros(root: Path) -> list[str]:
    base = micros_dir(root)
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir()
                  if p.is_dir() and not p.name.startswith(".") and model.is_valid_id(p.name))


# Cio' che in un ``run.py`` ha l'aria di una credenziale. Una micro app non ne
# porta mai: il quaderno finisce nei backup e negli snapshot, e il codice si
# mostra nel cassetto e in chat. Chi ha un segreto e' una skill installata, che
# la micro app importa con ``uses`` (visto il 05/10/2026: il token Cloudflare
# dell'hub copiato a mano dentro run.py invece di usare la skill che lo aveva).
_SECRET_PATTERNS = (
    re.compile(r"cfast_[A-Za-z0-9]{8,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(r"(?i)[\"']?[a-z0-9_-]*(secret|token|api[_-]?key|password|passwd|client[_-]?id)"
               r"[a-z0-9_-]*[\"']?\s*[:=]\s*[\"'][^\"'\s]{12,}[\"']"),
    re.compile(r"[\"'][A-Za-z0-9+/_=-]{40,}[\"']"),
)


def find_secrets(code: str) -> list[str]:
    """I pezzi di *code* che sembrano credenziali (per il rifiuto e per mascherarli)."""
    hits: list[str] = []
    for pattern in _SECRET_PATTERNS:
        hits += [m.group(0) for m in pattern.finditer(code)]
    return hits


def redact(code: str) -> str:
    for hit in sorted(set(find_secrets(code)), key=len, reverse=True):
        code = code.replace(hit, "<redacted>")
    return code


# ── le letture ──────────────────────────────────────────────────────────────


def _compact(readings: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    recent = readings[-KEEP_RECENT:]
    older = readings[:-KEEP_RECENT]
    since = now - timedelta(days=KEEP_DAYS)
    daily: dict[str, dict[str, Any]] = {}
    for item in older:
        stamp = model.parse_iso(item.get("ts"))
        if stamp is None or stamp.astimezone(timezone.utc) < since:
            continue
        daily[stamp.astimezone(timezone.utc).date().isoformat()] = item
    return [*daily.values(), *recent]


def validate_output(spec: dict[str, Any], out: Any) -> dict[str, Any]:
    """Le letture di un giro, controllate contro i campi dichiarati.

    Un campo mancante, del tipo sbagliato o fuori dai limiti e' un **errore**,
    non un valore: un hub che cambia formato non deve diventare un «0 %»
    plausibile nel cassetto.
    """
    if not isinstance(out, dict):
        raise MicroError(f"run() must return a dict, got {type(out).__name__}")
    unknown = [k for k in out if k not in spec["fields"] and k != SOURCE_FIELD]
    if unknown:
        raise MicroError(f"run() returned undeclared fields: {', '.join(map(str, unknown))}")
    values: dict[str, Any] = {}
    for name, fspec in spec["fields"].items():
        if name not in out or out[name] is None:
            raise MicroError(f"run() did not return `{name}`")
        value = out[name]
        if fspec["type"] == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise MicroError(f"`{name}` must be a number, got {value!r}")
            if "min" in fspec and value < fspec["min"] or "max" in fspec and value > fspec["max"]:
                raise MicroError(f"`{name}` = {value} is outside its range")
        elif fspec["type"] == "bool":
            if not isinstance(value, bool):
                raise MicroError(f"`{name}` must be true or false, got {value!r}")
        elif fspec["type"] == "rows":
            value = _validate_rows(name, fspec["columns"], value)
        else:
            if not isinstance(value, str):
                raise MicroError(f"`{name}` must be text, got {value!r}")
            value = value[:MAX_TEXT_VALUE]
        values[name] = value
    source = out.get(SOURCE_FIELD)
    if isinstance(source, str) and source.strip():
        values[SOURCE_FIELD] = source.strip()[:MAX_SOURCE]
    return values


def _validate_rows(name: str, columns: dict[str, dict[str, Any]], value: Any) -> list[dict]:
    if not isinstance(value, list):
        raise MicroError(f"`{name}` must be a list of rows, got {type(value).__name__}")
    if len(value) > MAX_ROWS:
        raise MicroError(f"`{name}`: at most {MAX_ROWS} rows (return the most useful ones)")
    out = []
    for i, row in enumerate(value):
        if not isinstance(row, dict):
            raise MicroError(f"`{name}` row {i} must be a dict of its columns")
        unknown = [k for k in row if k not in columns]
        if unknown:
            raise MicroError(f"`{name}` row {i} has undeclared columns: {', '.join(unknown)}")
        clean: dict[str, Any] = {}
        for col, cspec in columns.items():
            cell = row.get(col)
            if cell is None:
                raise MicroError(f"`{name}` row {i} is missing `{col}`")
            if cspec["type"] == "number":
                if isinstance(cell, bool) or not isinstance(cell, (int, float)):
                    raise MicroError(f"`{name}` row {i}: `{col}` must be a number")
            elif cspec["type"] == "bool":
                if not isinstance(cell, bool):
                    raise MicroError(f"`{name}` row {i}: `{col}` must be true or false")
            else:
                if not isinstance(cell, str):
                    raise MicroError(f"`{name}` row {i}: `{col}` must be text")
                cell = cell[:MAX_TEXT_VALUE]
            clean[col] = cell
        out.append(clean)
    return out


_approvals_lock = threading.Lock()


def approvals_path(workspace: Path) -> Path:
    """Accanto al workspace, come la chiave SSH (``config.paths.get_ssh_dir``).

    Fuori dalla radice che gli strumenti di Jenny possono leggere o scrivere
    finche' ``restrict_to_workspace`` e' acceso: un'approvazione che stesse nel
    quaderno la potrebbe scrivere chiunque scriva nel quaderno.
    """
    return Path(workspace).resolve().parent / "approvals" / "micro.json"


def _approval_key(root: Path, micro_id: str) -> str:
    return f"{root.name}/{micro_id}"


def drop_approval(workspace: Path, root: Path, micro_id: str) -> None:
    path = approvals_path(workspace)
    with _approvals_lock:
        data = _read_json(path)
        if isinstance(data, dict) and data.pop(_approval_key(root, micro_id), None) is not None:
            _write_json(path, data)


# ── la rete, solo verso gli host dichiarati ─────────────────────────────────


class MicroUnreachableError(Exception):
    """La rete o l'host non rispondono: e' un dato, non un guasto del codice."""


class MicroHttp:
    """``ctx.http``: GET e POST verso gli host della spec, e basta.

    Niente redirect (come ``jenny/apps/http.py``: un 3xx non deve portare la
    richiesta altrove), un tetto alle richieste e alla risposta, e il controllo
    SSRF degli indirizzi che una persona ha nominato
    (``validate_app_server_target``).
    """

    def __init__(self, hosts: list[str]) -> None:
        self._hosts = set(hosts)
        self._count = 0

    def _check(self, url: str) -> None:
        from urllib.parse import urlsplit

        from jenny.security.network import validate_app_server_target

        host = (urlsplit(url).hostname or "").lower()
        if host not in self._hosts:
            raise PermissionError(f"host {host!r} is not in this micro app's net list")
        self._count += 1
        if self._count > MAX_HTTP_REQUESTS:
            raise PermissionError(f"at most {MAX_HTTP_REQUESTS} requests per run")
        ok, error = validate_app_server_target(url)
        if not ok:
            raise PermissionError(f"blocked address: {error}")

    def _send(self, method: str, url: str, **kwargs: Any) -> Any:
        import httpx

        self._check(url)
        try:
            resp = httpx.request(method, url, timeout=HTTP_TIMEOUT_S,
                                 follow_redirects=False, **kwargs)
        except (httpx.TransportError, OSError) as exc:
            raise MicroUnreachableError(f"{type(exc).__name__}: {exc}") from None
        if len(resp.content) > MAX_HTTP_BYTES:
            raise ValueError(f"response over {MAX_HTTP_BYTES // 1024} KB")
        if resp.status_code >= 400:
            raise RuntimeError(f"HTTP {resp.status_code} from {url}")
        return resp

    def get_json(self, url: str, headers: dict[str, str] | None = None) -> Any:
        return self._send("GET", url, headers=headers).json()

    def get_text(self, url: str, headers: dict[str, str] | None = None) -> str:
        return self._send("GET", url, headers=headers).text

    def post_json(self, url: str, data: Any = None,
                  headers: dict[str, str] | None = None) -> Any:
        return self._send("POST", url, json=data, headers=headers).json()


# ── un giro ─────────────────────────────────────────────────────────────────


@dataclass
class RunResult:
    status: str  # ok | unreachable | error | unapproved | quarantined
    values: dict[str, Any] | None = None
    error: str = ""
    at: str = ""


_pool: ThreadPoolExecutor | None = None
_pool_lock = threading.Lock()
# I lettori il cui thread non si e' fermato al timeout: finche' non vengono
# modificati non ripartono (un thread fermo in C non si uccide).
_stuck: set[str] = set()


def _executor() -> ThreadPoolExecutor:
    """Un pool **suo**: una micro incastrata non toglie worker alla chat."""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="micro")
    return _pool


_namespaces: dict[str, Any] = {}


def _namespace(workspace: Path) -> Any:
    from jenny.agent.tools.python_exec import PythonNamespace
    from jenny.config.tool_schemas import PythonExecConfig

    key = str(workspace)
    ns = _namespaces.get(key)
    if ns is None:
        cfg = PythonExecConfig()
        ns = PythonNamespace(
            working_dir=key, allowed_modules=cfg.allowed_modules,
            blocked_modules=cfg.blocked_modules, restrict_to_workspace=True, workspace=key,
        )
        _namespaces[key] = ns
    return ns


def _skill_base(workspace: Path, uses: list[str]) -> Path | None:
    """La cartella da mettere su ``sys.path`` per la skill dichiarata."""
    if not uses:
        return None
    skill = Path(workspace) / "skills" / uses[0]
    scripts = skill / "scripts"
    return scripts if scripts.is_dir() else skill if skill.is_dir() else None


_RUN_SNIPPET = (
    "try:\n"
    "    _micro_out = run(ctx)\n"
    "    _micro_err = None\n"
    "except Exception as _micro_exc:\n"
    "    _micro_out = None\n"
    "    _micro_err = _micro_exc\n"
)


def _is_unreachable(exc: BaseException) -> bool:
    if isinstance(exc, MicroUnreachableError):
        return True
    try:
        import httpx

        if isinstance(exc, httpx.TransportError):
            return True
    except ImportError:  # pragma: no cover
        pass
    return isinstance(exc, (ConnectionError, TimeoutError))


def _format_error(exc: BaseException) -> str:
    import traceback

    from jenny.agent.tools.python_exec import _path_guard_bypass

    # I frame **interni**, dove l'errore nasce (il codice dell'app, la skill,
    # httpx): un ``limit`` positivo teneva i quattro piu' esterni, cioe' la
    # cornice del sandbox, e tagliava proprio la riga che spiega cosa e' successo.
    with _path_guard_bypass():
        text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__, limit=-4))
    return text[-_MAX_ERROR:]


def next_run_at(spec: dict[str, Any], state: dict[str, Any]) -> datetime | None:
    """Quando tocca di nuovo; ``None`` = adesso. Un errore allunga il passo."""
    last = model.parse_iso(state.get("lastRunAt"))
    if last is None:
        return None
    every = int(spec["every"])
    if state.get("status") in ("error", "broken"):
        failures = int(state.get("failures") or 1)
        every = min(MAX_EVERY_MINUTES, every * (2 ** min(failures, 4)))
    return last + timedelta(minutes=every)


# Il margine sul passo: l'orologio non batte al secondo, e una micro «ogni 30
# minuti» che al giro trova 29:58 aspetterebbe il giro dopo, cioe' 45.
_DUE_SLACK = timedelta(minutes=2)


def is_due(spec: dict[str, Any], state: dict[str, Any], now: datetime) -> bool:
    if state.get("status") in ("paused", "quarantined"):
        return False
    due = next_run_at(spec, state)
    return due is None or now >= due - _DUE_SLACK


# ── una scheda per nome ─────────────────────────────────────────────────────


def find_card(board: dict[str, Any], ref: str) -> dict[str, Any] | None:
    """Una scheda per id o per titolo (senza badare alle maiuscole)."""
    ref = (ref or "").strip()
    card = model.card_by_id(board, ref) if model.is_valid_id(ref) else None
    if card is not None:
        return card
    low = ref.casefold()
    matches = [c for c in board["cards"] if c["title"].casefold() == low]
    return matches[0] if len(matches) == 1 else None


