"""Il lettore di un'app: ``run.py``, girato dall'orologio, senza modello.

È la micro app di prima dentro l'app generale: la stessa esecuzione
(``jenny/board/micro.py``: il namespace di ``python_exec``, un pool suo, il
tetto di tempo, la rete solo verso gli host dichiarati), la stessa validazione
dei campi, gli stessi stati (``ok``, ``unreachable`` che non conta come guasto,
``broken`` dopo tre errori, con il passo che si allunga).

Quel che cambia:

- le letture vanno nei **dati dell'app** (``data/readings.jsonl``), cosi' lo
  schermo e le azioni ``query`` le vedono come ogni altra collezione;
- ``run(ctx)`` può tornare **piu' serie** (una lista di dict, uno per sensore)
  quando il lettore dichiara una ``key``: ``ctx.wanted`` dice quali parametri
  servono alle pose sulle bacheche (quali sensori);
- ``ctx.data(collection)`` legge una collezione dell'app: un lettore che
  **aggrega** (il valore del portafoglio dalle operazioni e dai prezzi) gira
  anche dopo ogni scrittura (:func:`schedule_reader`), cosi' il riassunto è
  sempre quello dei dati;
- gira solo il codice **approvato** (``jenny/apps/trust.py``).
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from jenny.apps.manifest import LoadedApp, find_app, load_app, scan_apps
from jenny.apps.summary_view import LATEST_FILE, READINGS, STATE_FILE, reader_dir

# Le serie di un lettore a chiave: al piu' tante per giro.
MAX_SERIES = 20
# Quanti lettori al piu' in un giro dell'orologio.
MAX_PER_TICK = 12
# Il ricalcolo dopo una scrittura aspetta un attimo: dieci acquisti di fila sono
# un ricalcolo, non dieci.
_DEBOUNCE_S = 1.5


@dataclass
class ReaderContext:
    """``ctx``: quel che ``run(ctx)`` riceve."""

    now: str
    prev: dict[str, Any] | None
    http: Any
    params: dict[str, Any] = field(default_factory=dict)
    wanted: list[dict[str, Any]] = field(default_factory=list)
    data: Callable[[str], list[dict[str, Any]]] = field(default=lambda _c: [])


# ── i file ──────────────────────────────────────────────────────────────────


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def read_state(app_dir: Path) -> dict[str, Any]:
    data = _read_json(reader_dir(app_dir) / STATE_FILE)
    return data if isinstance(data, dict) else {}


def write_state(app_dir: Path, state: dict[str, Any]) -> None:
    _write_json(reader_dir(app_dir) / STATE_FILE, state)


def read_readings(app_dir: Path) -> list[dict[str, Any]]:
    from jenny.apps.storage import read_collection

    try:
        return read_collection(app_dir, READINGS)
    except Exception:  # noqa: BLE001 — una collezione illeggibile e' «nessuna lettura»
        return []


def _series_of(record: Mapping[str, Any], key: str | None) -> str:
    return str(record.get(key, "")) if key else ""


# I campi di una lettura che non sono il dato: chi e quando, e da dove.
_NOT_DATA = ("id", "ts", "_source")


def _same_data(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    return ({k: v for k, v in a.items() if k not in _NOT_DATA}
            == {k: v for k, v in b.items() if k not in _NOT_DATA})


def append_readings(app_dir: Path, values: list[dict[str, Any]], *, key: str | None,
                    now: datetime) -> list[dict[str, Any]]:
    """Le letture di un giro: gli scalari nella collezione, le righe a parte.

    Di un campo ``rows`` la collezione tiene **quante** erano (l'andamento, le
    attese e gli eventi vivono di quel numero); le righe intere stanno in
    ``latest.json``, per serie quando il lettore ne ha piu' d'una.

    **Una lettura uguale all'ultima della sua serie non aggiunge una riga**:
    sposta in avanti l'ora di quella. Il dato resta fresco (non diventa
    «fermo») e la collezione non si riempie di copie — un sensore offline
    letto ogni 15 minuti faceva 96 righe identiche al giorno, che il cestino
    poi contava come dati.
    """
    from jenny.board.micro import _COMPACT_AFTER, _compact

    stamp = now.isoformat(timespec="seconds")
    d = reader_dir(app_dir)
    d.mkdir(parents=True, exist_ok=True)
    records = []
    latest = _read_json(d / LATEST_FILE)
    latest = latest if isinstance(latest, dict) else {}
    for item in values:
        rows = {k: v for k, v in item.items() if isinstance(v, list)}
        scalars = {k: (len(v) if isinstance(v, list) else v) for k, v in item.items()}
        records.append({"id": uuid.uuid4().hex[:12], "ts": stamp, **scalars})
        if rows:
            if key:
                latest.setdefault("series", {})[_series_of(item, key)] = {"ts": stamp, "rows": rows}
            else:
                latest = {"ts": stamp, "rows": rows}
    if latest:
        _write_json(d / LATEST_FILE, latest)
    path = d / f"{READINGS}.jsonl"
    readings = read_readings(app_dir)
    last: dict[str, dict[str, Any]] = {}
    for record in readings:
        last[_series_of(record, key)] = record
    fresh: list[dict[str, Any]] = []
    touched = False
    for i, record in enumerate(records):
        prev = last.get(_series_of(record, key))
        if prev is not None and _same_data(prev, record):
            prev["ts"] = stamp
            if "_source" in record:
                prev["_source"] = record["_source"]
            records[i] = prev
            touched = True
        else:
            fresh.append(record)
    if touched:
        readings.extend(fresh)
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in readings),
                       encoding="utf-8")
        os.replace(tmp, path)
    else:
        with path.open("a", encoding="utf-8") as fh:
            for record in fresh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        readings.extend(fresh)
    groups: dict[str, list[dict[str, Any]]] = {}
    for record in readings:
        groups.setdefault(_series_of(record, key), []).append(record)
    if len(readings) > _COMPACT_AFTER * max(1, len(groups)):
        kept: list[dict[str, Any]] = []
        for group in groups.values():
            kept.extend(_compact(group, now))
        kept.sort(key=lambda r: str(r.get("ts", "")))
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in kept),
                       encoding="utf-8")
        os.replace(tmp, path)
    return records


# ── un giro ─────────────────────────────────────────────────────────────────


def validate_values(spec: dict[str, Any], key: str | None, out: Any) -> list[dict[str, Any]]:
    """L'uscita di ``run``: un dict, o (con una ``key``) una lista di dict."""
    from jenny.board.micro import MicroError, validate_output

    if key is None:
        if isinstance(out, list):
            raise MicroError("run() must return one dict: this reader has no `key` for series")
        return [validate_output(spec, out)]
    items = out if isinstance(out, list) else [out]
    if len(items) > MAX_SERIES:
        raise MicroError(f"run() returned {len(items)} series: at most {MAX_SERIES}")
    values = [validate_output(spec, item) for item in items]
    seen: set[str] = set()
    for v in values:
        if v[key] in seen:
            raise MicroError(f"run() returned the series {v[key]!r} twice")
        seen.add(v[key])
    return values


def wanted_params(workspace: Path, slug: str) -> list[dict[str, Any]]:
    """I parametri delle pose di *slug* sulle bacheche (quali sensori servono)."""
    try:
        from jenny.board.placements import all_placements
    except ImportError:  # pragma: no cover
        return []
    seen: list[dict[str, Any]] = []
    for place in all_placements(workspace):
        if place["app"] == slug and place["params"] and place["params"] not in seen:
            seen.append(dict(place["params"]))
    return seen


async def execute_reader(workspace: Path, app: LoadedApp, *,
                         now: datetime | None = None) -> Any:
    """Gira ``run.py`` una volta e valida l'uscita. Non scrive niente.

    Torna un ``RunResult`` di ``jenny/board/micro.py``, con ``values`` che e'
    la **lista** delle letture validate.
    """
    from jenny.agent.tools.python_exec import (
        PythonExecInterrupted,
        _carry_turn_across_thread,
        _interrupt_thread,
    )
    from jenny.apps.storage import read_collection
    from jenny.board import micro as mc
    from jenny.security.workspace_access import build_workspace_scope, enter_workspace_scope

    assert app.manifest is not None and app.manifest.reader is not None
    spec = app.manifest.reader
    key_field = app.manifest.reader_key
    current = now or datetime.now(timezone.utc)
    stamp = current.isoformat(timespec="seconds")
    run_key = f"apps/{app.dir.parent.name}/{app.slug}"
    if run_key in mc._stuck:
        return mc.RunResult("quarantined", error="the last run never stopped; edit it to retry",
                            at=stamp)
    try:
        code = (app.dir / "run.py").read_text(encoding="utf-8")
    except OSError as exc:
        return mc.RunResult("error", error=f"run.py unreadable: {exc}", at=stamp)
    readings = read_readings(app.dir)

    def data(collection: str) -> list[dict[str, Any]]:
        try:
            return read_collection(app.dir, collection)
        except Exception:  # noqa: BLE001 — una collezione che non c'e' e' vuota
            return []

    ctx = ReaderContext(
        now=stamp, prev=readings[-1] if readings else None, http=mc.MicroHttp(spec["net"]),
        wanted=wanted_params(workspace, app.slug), data=data,
    )
    ns = mc._namespace(workspace)
    session = f"reader:{app.slug}:{uuid.uuid4().hex[:6]}"
    base = mc._skill_base(workspace, spec["uses"]) or app.dir
    ident: list[int | None] = [None]
    done = [False]

    def _run() -> tuple[str, str, Any, BaseException | None]:
        ident[0] = threading.get_ident()
        try:
            out, err, _ = ns.execute(code, str(base), session)
            if "Traceback (most recent call last)" in err:
                return out, err, None, None
            ns._globals_for(session)["ctx"] = ctx
            out2, err2, _ = ns.execute(mc._RUN_SNIPPET, str(base), session)
            g = ns._globals_for(session)
            return out + out2, err2, g.get("_micro_out"), g.get("_micro_err")
        except PythonExecInterrupted:
            return "", "interrupted", None, None
        finally:
            done[0] = True
            ns.forget_session(session)

    scope = build_workspace_scope(Path(workspace), "restricted").without_write_access()
    with enter_workspace_scope(scope):
        carried = _carry_turn_across_thread(_run)
    loop = asyncio.get_running_loop()
    try:
        _stdout, stderr, out, exc = await asyncio.wait_for(
            loop.run_in_executor(mc._executor(), carried), timeout=mc.RUN_TIMEOUT_S)
    except asyncio.TimeoutError:
        if not done[0]:
            _interrupt_thread(ident[0])
            await asyncio.sleep(1.0)
            if not done[0]:
                mc._stuck.add(run_key)
                return mc.RunResult("quarantined", error="run() did not stop", at=stamp)
        return mc.RunResult("error", error=f"run() took longer than {mc.RUN_TIMEOUT_S:.0f} s",
                            at=stamp)
    if exc is not None:
        status = "unreachable" if mc._is_unreachable(exc) else "error"
        return mc.RunResult(status, error=mc._format_error(exc), at=stamp)
    if stderr.strip() and out is None:
        return mc.RunResult("error", error=stderr.strip()[-2000:], at=stamp)
    try:
        values = validate_values(spec, key_field, out)
    except mc.MicroError as err:
        return mc.RunResult("error", error=str(err), at=stamp)
    return mc.RunResult("ok", values=values, at=stamp)  # type: ignore[arg-type]


def record_reader_run(app: LoadedApp, result: Any, *, now: datetime) -> dict[str, Any]:
    """Scrive l'esito di un giro: le letture, se ci sono, e lo stato."""
    from jenny.board.micro import BROKEN_AFTER

    state = read_state(app.dir)
    state["lastRunAt"] = result.at or now.isoformat(timespec="seconds")
    if result.status == "ok" and result.values is not None:
        assert app.manifest is not None
        append_readings(app.dir, list(result.values), key=app.manifest.reader_key, now=now)
        state.update({"status": "ok", "failures": 0, "lastError": "",
                      "lastOkAt": state["lastRunAt"]})
    elif result.status == "unreachable":
        state.update({"status": "unreachable", "lastError": result.error[:2000]})
    elif result.status in ("quarantined", "unapproved"):
        state.update({"status": result.status, "lastError": result.error[:2000]})
    else:
        failures = int(state.get("failures") or 0) + 1
        state.update({
            "status": "broken" if failures >= BROKEN_AFTER else "error",
            "failures": failures,
            "lastError": result.error[:2000],
        })
    write_state(app.dir, state)
    return state


async def run_reader(workspace: Path, slug: str, *, now: datetime | None = None,
                     force: bool = False, app_dir: Path | None = None) -> dict[str, Any] | None:
    """Un giro del lettore di *slug*, se approvato e (salvo *force*) se tocca.

    Con *app_dir* gira una **bozza** (la prova del tool ``app``): niente
    approvazione richiesta, perche' e' Jenny che la prova davanti all'utente.
    Torna il nuovo stato, o ``None`` se non e' girato.
    """
    from jenny.apps.trust import is_live
    from jenny.board.micro import is_due

    current = now or datetime.now(timezone.utc)
    app = load_app(app_dir) if app_dir is not None else find_app(workspace, slug)
    if app is None or app.broken or app.manifest is None or app.manifest.reader is None:
        return None
    state = read_state(app.dir)
    if app_dir is None:
        if state.get("status") == "paused" and not force:
            return None
        if not force and not is_due(app.manifest.reader, state, current):
            return None
        if not is_live(workspace, slug):
            return None
    result = await execute_reader(workspace, app, now=current)
    return record_reader_run(app, result, now=current)


async def run_due_readers(workspace: Path, *, now: datetime | None = None) -> int:
    """I lettori che toccano, al piu' :data:`MAX_PER_TICK`. Torna quanti sono girati."""
    slugs = [a.slug for a in scan_apps(workspace)
             if not a.broken and a.manifest is not None and a.manifest.reader is not None]
    if not slugs:
        return 0
    current = now or datetime.now(timezone.utc)
    ran = 0
    for start in range(0, len(slugs), 2):
        if ran >= MAX_PER_TICK:
            break
        batch = slugs[start:start + 2]
        results = await asyncio.gather(
            *(run_reader(workspace, s, now=current) for s in batch), return_exceptions=True)
        for slug, res in zip(batch, results):
            if isinstance(res, BaseException):
                logger.warning("reader of {} failed to run: {}", slug, res)
            elif res is not None:
                ran += 1
    return ran


# ── dopo una scrittura ──────────────────────────────────────────────────────

_pending: dict[str, asyncio.Task] = {}


def schedule_reader(workspace: Path, slug: str) -> None:
    """Dopo una scrittura sui dati dell'app, il lettore ricalcola (se ce n'e' uno).

    Un attimo dopo, e una volta sola per una raffica di scritture: il valore del
    portafoglio si aggiorna dopo l'acquisto, non dieci volte durante.
    """
    app = find_app(workspace, slug)
    if app is None or app.manifest is None or app.manifest.reader is None:
        return
    key = f"{workspace}/{slug}"
    if key in _pending and not _pending[key].done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    async def later() -> None:
        try:
            await asyncio.sleep(_DEBOUNCE_S)
            await run_reader(workspace, slug, force=True)
        except Exception:  # noqa: BLE001 — un ricalcolo mancato non e' un guasto della scrittura
            logger.exception("apps: reader of {} after a write failed", slug)
        finally:
            _pending.pop(key, None)

    _pending[key] = loop.create_task(later())
