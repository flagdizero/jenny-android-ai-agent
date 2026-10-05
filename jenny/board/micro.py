"""Le micro app: un piccolo programma Python attaccato a un cassetto.

Una micro app non e' una Jenny App: niente manifest, niente HTML, niente
griglia. E' una sezione del cassetto con un ``run.py`` accanto, che va a
prendere un dato e lo tiene fresco. Vive nel quaderno::

    <quaderno>/board/micro/<id>/
        micro.json      la spec: titolo, ogni quanto, i campi e il principale,
                        la soglia, gli host e la skill che usa
        run.py          ``def run(ctx) -> dict``
        readings.jsonl  le letture, ``{id, ts, ...campi}`` — lo stesso formato dei
                        record di una collezione app, cosi' la vista e' una sola
        state.json      come sta: ultimo giro, ultimo errore, guasti di fila

Le bozze (scritte da Jenny e provate una volta, in attesa del [Tienilo]) stanno
in ``board/micro/.drafts/<id>/`` con la stessa forma.

**Chi la esegue e' l'orologio del quaderno**, non un cron e non il modello: una
lettura costa zero token. Il codice gira nello stesso ``PythonNamespace`` di
``python_exec``, con lo scope del quaderno **in sola lettura**: legge, non
scrive niente. Le letture le scrive questo modulo, fuori dal codice guardato.

**La linea di fiducia non e' la sandbox** — ``python_exec`` dice da se' di non
esserlo. E' chi puo' cambiare il codice: il runner esegue ``run.py`` solo se il
suo hash e' quello approvato, e le approvazioni stanno **fuori dal workspace**
(accanto alla chiave SSH), dove gli strumenti di Jenny non arrivano. La
cartella ``board/`` non la scrive nessuno strumento generico (v.
``jenny.security.workspace_policy.BoardWriteError``): il codice di una micro app
lo cambia solo il tool ``micro_app``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.board import model

MICRO_DIRNAME = "micro"
DRAFTS_DIRNAME = ".drafts"
TRASH_DIRNAME = ".trash"
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
# Quanto puo' durare un giro, e quante richieste puo' fare.
RUN_TIMEOUT_S = 20.0
HTTP_TIMEOUT_S = 15.0
MAX_HTTP_REQUESTS = 3
MAX_HTTP_BYTES = 256 * 1024
# Le letture: le ultime 48, piu' l'ultima di ogni giorno per un mese.
KEEP_RECENT = 48
KEEP_DAYS = 31
_COMPACT_AFTER = 200
# Tre errori di codice di fila: rotta.
BROKEN_AFTER = 3
_MAX_ERROR = 2000

FIELD_TYPES = ("number", "bool", "text")
STATUSES = ("ok", "unreachable", "error", "broken", "quarantined", "unapproved", "paused")

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


def normalize_spec(raw: Any, *, micro_id: str) -> dict[str, Any]:
    """La spec di una micro app, ripulita; :class:`MicroError` se non regge."""
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
        fields[name] = entry
    primary = raw.get("primary") if raw.get("primary") in fields else next(iter(fields))
    try:
        every = int(raw.get("every") or DEFAULT_EVERY_MINUTES)
    except (TypeError, ValueError):
        every = DEFAULT_EVERY_MINUTES
    every = max(MIN_EVERY_MINUTES, min(MAX_EVERY_MINUTES, every))
    alarm = None
    if isinstance(raw.get("alarm"), dict):
        a = raw["alarm"]
        if a.get("field") in fields and a.get("op") in model.CONDITION_OPS and isinstance(
            a.get("value"), (int, float, str, bool)
        ):
            alarm = {"field": a["field"], "op": a["op"], "value": a["value"]}
    try:
        stale = int(raw.get("staleAfter") or every * 3)
    except (TypeError, ValueError):
        stale = every * 3
    net = []
    for host in raw.get("net") or []:
        if isinstance(host, str) and _HOST_RE.match(host.strip().lower()):
            net.append(host.strip().lower())
    if len(net) > MAX_NET_HOSTS:
        raise MicroError(f"at most {MAX_NET_HOSTS} hosts in net")
    uses = [u for u in (raw.get("uses") or []) if isinstance(u, str) and _SKILL_RE.match(u)]
    if len(uses) > 1:
        raise MicroError("a micro app can use one skill")
    card = raw.get("card")
    out = {
        "id": micro_id,
        "title": title,
        "card": card if model.is_valid_id(card) else "",
        "every": every,
        "fields": fields,
        "primary": primary,
        "alarm": alarm,
        "staleAfter": max(every, stale),
        "net": sorted(set(net)),
        "uses": uses,
    }
    for stamp in ("created", "updated"):
        if isinstance(raw.get(stamp), str):
            out[stamp] = raw[stamp]
    return out


def contract_of(spec: dict[str, Any]) -> str:
    """L'impronta di cio' che l'utente ha approvato **vedendolo**.

    I campi e le loro unita', il principale, gli host e la skill: se una
    riparazione li lascia uguali, il codice nuovo gira subito (decisione
    dell'utente del 05/10); se li cambia, serve un nuovo [Tienilo].
    """
    body = {
        "fields": {k: {"type": v["type"], "unit": v.get("unit", "")}
                   for k, v in sorted(spec["fields"].items())},
        "primary": spec["primary"],
        "net": spec["net"],
        "uses": spec["uses"],
    }
    return "sha256:" + hashlib.sha256(
        json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()


def code_hash(code: str) -> str:
    return "sha256:" + hashlib.sha256(code.encode("utf-8")).hexdigest()


# ── dove sta ────────────────────────────────────────────────────────────────


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


def write_micro(root: Path, micro_id: str, spec: dict[str, Any], code: str, *,
                draft: bool) -> Micro:
    """Scrive spec e codice (bozza o viva). Lo chiama solo il tool ``micro_app``."""
    if len(code.encode("utf-8")) > MAX_CODE_BYTES:
        raise MicroError(f"run.py is over {MAX_CODE_BYTES // 1024} KB: a micro app does one thing")
    if not re.search(r"^def run\(", code, re.MULTILINE):
        raise MicroError("run.py must define `def run(ctx):` returning a dict")
    clean = normalize_spec(spec, micro_id=micro_id)
    d = micro_dir(root, micro_id, draft=draft)
    d.mkdir(parents=True, exist_ok=True)
    now = model.now_iso()
    old = _read_json(d / SPEC_FILE) or {}
    clean["created"] = old.get("created") or now
    clean["updated"] = now
    _write_json(d / SPEC_FILE, clean)
    (d / CODE_FILE).write_text(code, encoding="utf-8")
    return Micro(id=micro_id, dir=d, spec=clean, code=code, draft=draft)


def promote_draft(root: Path, micro_id: str) -> Micro | None:
    """La bozza diventa la micro app viva (le letture della prova vengono con lei)."""
    src = micro_dir(root, micro_id, draft=True)
    if not (src / SPEC_FILE).is_file():
        return None
    dst = micro_dir(root, micro_id)
    dst.mkdir(parents=True, exist_ok=True)
    for name in (SPEC_FILE, CODE_FILE):
        os.replace(src / name, dst / name)
    # Le letture vive restano (la storia non si perde per una modifica); se non
    # ce ne sono, quelle della prova diventano le prime.
    if not (dst / READINGS_FILE).exists() and (src / READINGS_FILE).exists():
        os.replace(src / READINGS_FILE, dst / READINGS_FILE)
    _write_json(dst / STATE_FILE, {**(read_state(dst) or {}), "status": "ok", "failures": 0})
    shutil.rmtree(src, ignore_errors=True)
    return load_micro(root, micro_id)


def discard_draft(root: Path, micro_id: str) -> None:
    shutil.rmtree(micro_dir(root, micro_id, draft=True), ignore_errors=True)


def trash_micro(root: Path, micro_id: str) -> None:
    """Toglie una micro app dal quaderno senza cancellarla: va in ``.trash/``."""
    src = micro_dir(root, micro_id)
    discard_draft(root, micro_id)
    if not src.is_dir():
        return
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    dst = micros_dir(root) / TRASH_DIRNAME / f"{micro_id}-{stamp}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src, dst)


# ── stato e letture ─────────────────────────────────────────────────────────


def read_state(d: Path) -> dict[str, Any]:
    data = _read_json(d / STATE_FILE)
    return data if isinstance(data, dict) else {}


def write_state(d: Path, state: dict[str, Any]) -> None:
    _write_json(d / STATE_FILE, state)


def read_readings(d: Path) -> list[dict[str, Any]]:
    path = d / READINGS_FILE
    out: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


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


def append_reading(d: Path, values: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    record = {"id": uuid.uuid4().hex[:12], "ts": now.isoformat(timespec="seconds"), **values}
    d.mkdir(parents=True, exist_ok=True)
    path = d / READINGS_FILE
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    readings = read_readings(d)
    if len(readings) > _COMPACT_AFTER:
        kept = _compact(readings, now)
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in kept),
                       encoding="utf-8")
        os.replace(tmp, path)
    return record


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
        else:
            if not isinstance(value, str):
                raise MicroError(f"`{name}` must be text, got {value!r}")
            value = value[:MAX_TEXT_VALUE]
        values[name] = value
    source = out.get(SOURCE_FIELD)
    if isinstance(source, str) and source.strip():
        values[SOURCE_FIELD] = source.strip()[:MAX_SOURCE]
    return values


# ── le approvazioni, fuori dal workspace ────────────────────────────────────


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


def read_approval(workspace: Path, root: Path, micro_id: str) -> dict[str, Any] | None:
    data = _read_json(approvals_path(workspace))
    entry = (data or {}).get(_approval_key(root, micro_id)) if isinstance(data, dict) else None
    return entry if isinstance(entry, dict) else None


def write_approval(workspace: Path, root: Path, micro: Micro) -> None:
    path = approvals_path(workspace)
    with _approvals_lock:
        data = _read_json(path)
        data = data if isinstance(data, dict) else {}
        data[_approval_key(root, micro.id)] = {
            "codeHash": code_hash(micro.code),
            "contract": contract_of(micro.spec),
            "approvedAt": model.now_iso(),
        }
        _write_json(path, data)


def drop_approval(workspace: Path, root: Path, micro_id: str) -> None:
    path = approvals_path(workspace)
    with _approvals_lock:
        data = _read_json(path)
        if isinstance(data, dict) and data.pop(_approval_key(root, micro_id), None) is not None:
            _write_json(path, data)


def is_approved(workspace: Path, root: Path, micro: Micro) -> bool:
    entry = read_approval(workspace, root, micro.id)
    return bool(entry) and entry.get("codeHash") == code_hash(micro.code)


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


@dataclass
class MicroContext:
    """``ctx``: quel che ``run(ctx)`` riceve."""

    now: str
    prev: dict[str, Any] | None
    http: MicroHttp
    params: dict[str, Any] = field(default_factory=dict)


# ── un giro ─────────────────────────────────────────────────────────────────


@dataclass
class RunResult:
    status: str  # ok | unreachable | error | unapproved | quarantined
    values: dict[str, Any] | None = None
    error: str = ""
    at: str = ""


_pool: ThreadPoolExecutor | None = None
_pool_lock = threading.Lock()
# Le micro app il cui thread non si e' fermato al timeout: finche' non vengono
# modificate non ripartono (un thread fermo in C non si uccide).
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

    with _path_guard_bypass():
        text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__, limit=4))
    return text[-_MAX_ERROR:]


async def execute_micro(workspace: Path, root: Path, micro: Micro, *,
                        now: datetime | None = None) -> RunResult:
    """Esegue ``run.py`` una volta e valida l'uscita. Non scrive niente.

    Il codice gira con lo scope del quaderno **senza permesso di scrittura**:
    legge (la propria skill, le pagine), non cambia niente. I globali sono
    nuovi a ogni giro (una sessione per giro, dimenticata alla fine).
    """
    from jenny.agent.tools.python_exec import (
        PythonExecInterrupted,
        _carry_turn_across_thread,
        _interrupt_thread,
    )
    from jenny.security.workspace_access import build_workspace_scope, enter_workspace_scope

    current = now or datetime.now(timezone.utc)
    key = f"{root.name}/{micro.id}"
    if key in _stuck:
        return RunResult("quarantined", error="the last run never stopped; edit it to retry",
                         at=current.isoformat(timespec="seconds"))
    readings = read_readings(micro_dir(root, micro.id))
    ctx = MicroContext(now=current.isoformat(timespec="seconds"),
                       prev=readings[-1] if readings else None,
                       http=MicroHttp(micro.spec["net"]))
    ns = _namespace(workspace)
    session = f"micro:{key}:{uuid.uuid4().hex[:6]}"
    base = _skill_base(workspace, micro.spec["uses"]) or micro.dir
    ident: list[int | None] = [None]
    done = [False]

    def _run() -> tuple[str, str, Any, BaseException | None]:
        ident[0] = threading.get_ident()
        try:
            out, err, _ = ns.execute(micro.code, str(base), session)
            if "Traceback (most recent call last)" in err:
                return out, err, None, None
            ns._globals_for(session)["ctx"] = ctx
            out2, err2, _ = ns.execute(_RUN_SNIPPET, str(base), session)
            g = ns._globals_for(session)
            return out + out2, err2, g.get("_micro_out"), g.get("_micro_err")
        except PythonExecInterrupted:
            return "", "interrupted", None, None
        finally:
            done[0] = True
            ns.forget_session(session)

    scope = build_workspace_scope(root, "restricted").without_write_access()
    with enter_workspace_scope(scope):
        carried = _carry_turn_across_thread(_run)
    loop = asyncio.get_running_loop()
    stamp = current.isoformat(timespec="seconds")
    try:
        _stdout, stderr, out, exc = await asyncio.wait_for(
            loop.run_in_executor(_executor(), carried), timeout=RUN_TIMEOUT_S)
    except asyncio.TimeoutError:
        if not done[0]:
            _interrupt_thread(ident[0])
            await asyncio.sleep(1.0)
            if not done[0]:
                _stuck.add(key)
                return RunResult("quarantined", error="run() did not stop", at=stamp)
        return RunResult("error", error=f"run() took longer than {RUN_TIMEOUT_S:.0f} s", at=stamp)
    if exc is not None:
        status = "unreachable" if _is_unreachable(exc) else "error"
        return RunResult(status, error=_format_error(exc), at=stamp)
    if stderr.strip() and out is None:
        return RunResult("error", error=stderr.strip()[-_MAX_ERROR:], at=stamp)
    try:
        values = validate_output(micro.spec, out)
    except MicroError as err:
        return RunResult("error", error=str(err), at=stamp)
    return RunResult("ok", values=values, at=stamp)


def unstick(root: Path, micro_id: str) -> None:
    """Una micro app modificata riparte, anche se l'ultimo giro era rimasto appeso."""
    _stuck.discard(f"{root.name}/{micro_id}")


def record_run(micro: Micro, result: RunResult, *, now: datetime) -> dict[str, Any]:
    """Scrive l'esito di un giro: la lettura, se c'e', e lo stato. Torna lo stato."""
    state = read_state(micro.dir)
    state["lastRunAt"] = result.at or now.isoformat(timespec="seconds")
    if result.status == "ok" and result.values is not None:
        append_reading(micro.dir, result.values, now=now)
        state.update({"status": "ok", "failures": 0, "lastError": "",
                      "lastOkAt": state["lastRunAt"]})
    elif result.status == "unreachable":
        # Il mondo, non il codice: non conta verso «rotta».
        state.update({"status": "unreachable", "lastError": result.error[:_MAX_ERROR]})
    elif result.status in ("quarantined", "unapproved"):
        state.update({"status": result.status, "lastError": result.error[:_MAX_ERROR]})
    else:
        failures = int(state.get("failures") or 0) + 1
        state.update({
            "status": "broken" if failures >= BROKEN_AFTER else "error",
            "failures": failures,
            "lastError": result.error[:_MAX_ERROR],
        })
    write_state(micro.dir, state)
    return state


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


async def run_micro(workspace: Path, root: Path, micro_id: str, *,
                    now: datetime | None = None, force: bool = False) -> dict[str, Any] | None:
    """Un giro di una micro app viva, se approvata e (salvo *force*) se tocca.

    Torna il nuovo stato, o ``None`` se non e' girata.
    """
    current = now or datetime.now(timezone.utc)
    micro = load_micro(root, micro_id)
    if micro is None:
        return None
    state = read_state(micro.dir)
    if not force and not is_due(micro.spec, state, current):
        return None
    if not is_approved(workspace, root, micro):
        if state.get("status") != "unapproved":
            state.update({"status": "unapproved",
                          "lastError": "run.py changed outside Jenny: approve it again"})
            write_state(micro.dir, state)
        return None
    result = await execute_micro(workspace, root, micro, now=current)
    return record_run(micro, result, now=current)


async def run_due_micros(workspace: Path, root: Path, *,
                         now: datetime | None = None) -> int:
    """Le micro app del quaderno che toccano, in parallelo. Torna quante sono girate."""
    current = now or datetime.now(timezone.utc)
    ids = list_micros(root)
    if not ids:
        return 0
    results = await asyncio.gather(
        *(run_micro(workspace, root, mid, now=current) for mid in ids),
        return_exceptions=True,
    )
    ran = 0
    for mid, res in zip(ids, results):
        if isinstance(res, BaseException):
            logger.warning("micro {} in {} failed to run: {}", mid, root.name, res)
        elif res is not None:
            ran += 1
    return ran


# ── la vista ────────────────────────────────────────────────────────────────


def micro_view(workspace: Path, root: Path, micro_id: str, *, pending: bool = False,
               now: datetime | None = None) -> dict[str, Any]:
    """La vista di una micro app nel cassetto, nella stessa forma di una mini app.

    ``state`` e' quello dei dati (``ok``, ``alarm``, ``stale``, ``empty``) finche'
    il codice va; diventa quello del codice (``broken``, ``unapproved``,
    ``quarantined``, ``paused``) quando non va: in quel caso il valore resta
    l'ultimo buono, e il cassetto lo mostra in grigio con la sua eta'.
    """
    from jenny.board.sections import records_view

    micro = load_micro(root, micro_id, draft=pending) or (
        load_micro(root, micro_id) if pending else None)
    if micro is None:
        return {"state": "missing", "slug": micro_id, "micro": micro_id}
    spec = micro.spec
    primary = spec["primary"]
    fspec = spec["fields"][primary]
    state = read_state(micro.dir)
    alarm = spec["alarm"] if spec["alarm"] and spec["alarm"]["field"] == primary else None
    view = records_view(
        read_readings(micro.dir), field_name=primary, alarm=alarm,
        stale_after_minutes=spec["staleAfter"], trend_days=7, now=now,
    )
    record = view.pop("record", None) or {}
    extras = [
        {"field": name, "label": f["label"], "unit": f.get("unit", ""), "type": f["type"],
         "value": record.get(name)}
        for name, f in spec["fields"].items() if name != primary
    ][:2]
    code_state = str(state.get("status") or "")
    data_state = view["state"]
    if pending:
        shown = "pending"
    elif code_state in ("broken", "unapproved", "quarantined", "paused"):
        shown = code_state
    elif not is_approved(workspace, root, micro):
        shown = "unapproved"
    else:
        shown = data_state
    due = next_run_at(spec, state)
    return {
        "slug": micro.id,
        "micro": micro.id,
        "name": spec["title"],
        "label": fspec["label"],
        "unit": fspec.get("unit", ""),
        "field": primary,
        "type": fspec["type"],
        "verbs": [],
        "params": {},
        **view,
        "state": shown,
        "dataState": data_state,
        "extras": extras,
        "source": record.get(SOURCE_FIELD, ""),
        "every": spec["every"],
        "net": spec["net"],
        "uses": spec["uses"],
        "codeStatus": code_state or "new",
        "lastRunAt": state.get("lastRunAt", ""),
        "nextRunAt": due.isoformat(timespec="seconds") if due else "",
        "lastError": state.get("lastError", "") if code_state != "ok" else "",
        "failures": int(state.get("failures") or 0),
    }


def micro_field_value(root: Path, micro_id: str, field_name: str, *,
                      now: datetime | None = None) -> tuple[Any, bool]:
    """L'ultimo valore di un campo di una micro app, e se e' fresco (per le attese)."""
    micro = load_micro(root, micro_id)
    if micro is None or field_name not in micro.spec["fields"]:
        return None, False
    readings = read_readings(micro.dir)
    if not readings:
        return None, False
    last = max(readings, key=lambda r: str(r.get("ts", "")))
    at = model.parse_iso(last.get("ts"))
    current = now or datetime.now(timezone.utc)
    fresh = at is not None and current - at <= timedelta(minutes=micro.spec["staleAfter"])
    return last.get(field_name), fresh


# ── la sezione nel cassetto ─────────────────────────────────────────────────


def find_card(board: dict[str, Any], ref: str) -> dict[str, Any] | None:
    """Una scheda per id o per titolo (senza badare alle maiuscole)."""
    ref = (ref or "").strip()
    card = model.card_by_id(board, ref) if model.is_valid_id(ref) else None
    if card is not None:
        return card
    low = ref.casefold()
    matches = [c for c in board["cards"] if c["title"].casefold() == low]
    return matches[0] if len(matches) == 1 else None


def section_of(board: dict[str, Any], micro_id: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    for card in board["cards"]:
        for section in card.get("userSections") or []:
            if section.get("kind") == "micro" and section.get("micro") == micro_id:
                return card, section
    return None


def set_section(root: Path, card_id: str, micro_id: str, *, pending: bool) -> str:
    """Mette (o aggiorna) la sezione della micro app nel cassetto; torna il suo id.

    Scrive **come l'utente**: la chiamano il tool ``micro_app`` (che esiste solo
    nei turni dell'utente, quindi agisce su sua richiesta, e lo segna con
    ``by: jenny-on-request``) e i comandi del cassetto.
    """
    from jenny.board.store import mutate_board

    out: list[str] = []

    def apply(board: dict[str, Any]) -> None:
        found = section_of(board, micro_id)
        if found is not None:
            found[1]["pending"] = pending
            out.append(found[1]["id"])
            return
        card = model.card_by_id(board, card_id)
        if card is None:
            raise MicroError(f"no card `{card_id}` on this board")
        taken = {s["id"] for c in board["cards"] for s in c.get("userSections") or []}
        sid = model.unique_id("s-micro", taken)
        card["userSections"].append({
            "id": sid, "kind": "micro", "micro": micro_id, "pending": pending,
            "by": "jenny-on-request", "created": model.now_iso(),
        })
        out.append(sid)

    mutate_board(root, apply)
    return out[0]


def drop_section(root: Path, micro_id: str) -> None:
    from jenny.board.store import mutate_board

    def apply(board: dict[str, Any]) -> bool | None:
        touched = False
        for card in board["cards"]:
            keep = [s for s in card.get("userSections") or []
                    if not (s.get("kind") == "micro" and s.get("micro") == micro_id)]
            if len(keep) != len(card.get("userSections") or []):
                card["userSections"] = keep
                touched = True
        return None if touched else False

    mutate_board(root, apply)


def keep(workspace: Path, root: Path, micro_id: str) -> Micro | None:
    """[Tienilo]: la bozza diventa viva e approvata; senza bozza, si riapprova la viva."""
    micro = promote_draft(root, micro_id) or load_micro(root, micro_id)
    if micro is None:
        return None
    write_approval(workspace, root, micro)
    unstick(root, micro_id)
    found = None
    from jenny.board.store import read_board

    board = read_board(root)
    if board is not None:
        found = section_of(board, micro_id)
    if found is not None:
        set_section(root, found[0]["id"], micro_id, pending=False)
    state = read_state(micro.dir)
    if state.get("status") in ("unapproved", "quarantined", "broken", "error"):
        state.update({"status": "ok", "failures": 0, "lastError": ""})
        write_state(micro.dir, state)
    return micro


def remove(workspace: Path, root: Path, micro_id: str) -> None:
    """Toglie la micro app: i file in ``.trash/``, l'approvazione cancellata."""
    trash_micro(root, micro_id)
    drop_approval(workspace, root, micro_id)
    unstick(root, micro_id)
