"""Bozze in attesa: scritture sensibili di un subagent non fidato.

Un subagent che ha letto contenuto non fidato (``security.subagent_policy``)
può ancora voler cambiare un file che Jenny carica o esegue da sola: una skill,
un promemoria in ``cron/``, ``USER.md``. Rifiutarlo
toglierebbe una funzione («installa questa skill da GitHub»); scriverlo darebbe
a una pagina ostile un posto da cui agire più tardi senza nessuno davanti. La
via di mezzo è questa: la scrittura va in bozza, e solo un tocco dell'utente in
app la mette al suo posto. Nessun tool del modello raggiunge ``install``: lo
chiama soltanto la RPC ``staged.install`` della WebUI.

Forma: il contenuto in ``.jenny/staged/<id>/content`` (un file, come tutto il
contenuto), la descrizione in una riga di ``state.sqlite`` (``staged_drafts``).
La cartella è SECRET per i subagent e il database sta fuori dal workspace,
quindi un lavoro non fidato non può riscrivere una bozza già in attesa né
cambiarne la descrizione. Fino alla 2.x la descrizione era ``meta.json``
accanto al contenuto; la importa ``db_migration/staged_taint.py``.

``install`` verifica che il file di destinazione sia ancora quello visto
quando la bozza è stata mostrata (``expected_sha``): se nel frattempo è
cambiato, la risposta è ``conflict`` e non si scrive niente.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import threading
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.db.schema import Schema
from jenny.utils.clock import now_ms as _now_ms
from jenny.utils.path import atomic_write

STAGED_REL = (".jenny", "staged")
MAX_PENDING = 50

SCHEMA = Schema(
    "staged",
    (
        """
        CREATE TABLE staged_drafts (
          id TEXT PRIMARY KEY,
          target TEXT NOT NULL,
          task_id TEXT,
          created_ms INTEGER NOT NULL,
          data TEXT NOT NULL
        );
        """,
    ),
)

_LOCK = threading.Lock()


@dataclass(frozen=True, slots=True)
class StagedChange:
    """Un cambio dell'elenco delle bozze, per chi ascolta.

    ``action`` è ``staged`` (una bozza nuova o riscritta), ``installed`` o
    ``discarded``; ``count`` quante ne restano in attesa. ``target`` è il
    percorso relativo alla radice in cui la bozza vive.
    """

    action: str
    id: str
    target: str
    count: int
    root: Path


# Chi ascolta: una lista e non un posto solo. Il container ci mette il frame
# ``staged_changed`` per la WebUI, il driver dei piani il suo «le bozze del
# passo sono decise?»; con un posto solo il secondo cancellava il primo.
_listeners: list[Callable[[StagedChange], None]] = []


class StagingFullError(PermissionError):
    """Troppe bozze in attesa: si rifiuta invece di accumulare."""

    def __init__(self) -> None:
        super().__init__(
            f"{MAX_PENDING} changes are already waiting for the user's approval; "
            "this one was not saved. Say in your report what you wanted to change."
        )


@dataclass(frozen=True, slots=True)
class StagedRef:
    """Ciò che il tool risponde al modello dopo aver messo in bozza."""

    id: str
    target_rel: str


def add_change_listener(listener: Callable[[StagedChange], None]) -> Callable[[], None]:
    """Aggiunge chi avvisare quando l'elenco cambia; ritorna come toglierlo.

    Il container ci mette il frame ``staged_changed`` sulla WebSocket (la
    scheda in casa compare senza ricaricare), il driver dei piani il suo
    controllo delle bozze di un passo. Gli avvisi possono arrivare da un thread
    (le RPC fanno il disco fuori dal loop): chi ascolta rientra nel suo loop da
    solo.
    """
    _listeners.append(listener)

    def remove() -> None:
        with suppress(ValueError):
            _listeners.remove(listener)

    return remove


def clear_change_listeners() -> None:
    """Toglie tutti gli ascoltatori (i test, fra un caso e l'altro)."""
    _listeners.clear()


def _notify(root: Path, action: str, staged_id: str, target: str) -> None:
    if not _listeners:
        return
    try:
        change = StagedChange(action, staged_id, target, len(list_staged(root)), Path(root))
    except Exception as exc:  # noqa: BLE001 — l'avviso non deve fermare la scrittura
        logger.warning("Staged-change count failed: {}", exc)
        return
    for listener in list(_listeners):
        try:
            listener(change)
        except Exception as exc:  # noqa: BLE001 — un ascoltatore rotto non zittisce gli altri
            logger.warning("Staged-change listener failed: {}", exc)


def _dir(root: Path) -> Path:
    return Path(root).joinpath(*STAGED_REL)


def sha256_of(path: Path) -> str | None:
    """Lo sha256 di un file, o None se non esiste."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def _db(root: Path):
    from jenny import db

    database = db.database_for_workspace(Path(root), db.STATE)
    database.require(SCHEMA)
    return database


def _meta_of(raw: str) -> dict[str, Any] | None:
    try:
        meta = json.loads(raw)
    except ValueError:
        return None
    return meta if isinstance(meta, dict) and isinstance(meta.get("target"), str) else None


def _read_meta(root: Path, staged_id: str) -> dict[str, Any] | None:
    rows = _db(root).read("SELECT data FROM staged_drafts WHERE id = ?", (staged_id,))
    return _meta_of(rows[0]["data"]) if rows else None


def _forget(root: Path, staged_id: str) -> None:
    """Via la riga e il contenuto di una bozza."""
    with _db(root).write() as conn:
        conn.execute("DELETE FROM staged_drafts WHERE id = ?", (staged_id,))
    shutil.rmtree(_dir(root) / staged_id, ignore_errors=True)


def pending_journal_keys(root: Path) -> set[str]:
    """I registri dell'annulla delle bozze in attesa: chi pulisce i registri li tiene.

    Una bozza installata conta come lavoro di chi l'ha scritta: se il suo
    registro sparisse prima dell'installazione (il record dell'aiutante scade
    in 7 giorni, la bozza no), il suo annulla non la toglierebbe più.
    """
    try:
        rows = _db(root).read("SELECT data FROM staged_drafts")
    except sqlite3.Error:
        return set()
    keys = set()
    for row in rows:
        meta = _meta_of(row["data"]) or {}
        if isinstance(meta.get("journal"), str) and meta["journal"]:
            keys.add(meta["journal"])
    return keys


def stage(
    root: Path,
    target_rel: str,
    content: bytes,
    *,
    sources: tuple[str, ...],
    task_id: str,
) -> StagedRef:
    """Mette in bozza *content* per *target_rel*. Una bozza per file e lavoro.

    Se lo stesso lavoro mette in bozza di nuovo lo stesso file, la bozza si
    sostituisce: è il caso di ``write_file`` che riscrive per intero.
    """
    root = Path(root)
    with _LOCK:
        base = _dir(root)
        database = _db(root)
        pending = database.read("SELECT count(*) FROM staged_drafts")[0][0]
        same = database.read("SELECT id FROM staged_drafts WHERE target = ? AND task_id = ?",
                             (target_rel, task_id))
        existing = same[0]["id"] if same else None
        if existing is None and pending >= MAX_PENDING:
            raise StagingFullError()
        staged_id = existing or uuid.uuid4().hex[:12]
        entry = base / staged_id
        entry.mkdir(parents=True, exist_ok=True)
        atomic_write(entry / "content", content, fsync_dir=False)
        meta = {
            "id": staged_id,
            "target": target_rel,
            "base_sha256": sha256_of(root / target_rel),
            "size": len(content),
            "sources": list(sources),
            "task_id": task_id,
            "created_ms": _now_ms(),
        }
        # Il registro dell'annulla di chi l'ha scritta (``agent.write_journal``):
        # installata, la bozza conta come suo lavoro, e il suo annulla la toglie.
        journal, step = _current_journal_key()
        if journal is not None:
            meta["journal"] = journal
            if step is not None:
                meta["journal_step"] = step
        with database.write() as conn:
            conn.execute(
                "INSERT INTO staged_drafts(id, target, task_id, created_ms, data) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET target = excluded.target, "
                "task_id = excluded.task_id, created_ms = excluded.created_ms, data = excluded.data",
                (staged_id, target_rel, task_id, meta["created_ms"],
                 json.dumps(meta, ensure_ascii=False)),
            )
    _notify(root, "staged", staged_id, target_rel)
    return StagedRef(staged_id, target_rel)


def list_staged(root: Path) -> list[dict[str, Any]]:
    """Le bozze in attesa, dalla più vecchia. Ognuna con lo sha attuale del file."""
    root = Path(root)
    base = _dir(root)
    items: list[dict[str, Any]] = []
    for row in _db(root).read("SELECT id, data FROM staged_drafts ORDER BY created_ms, id"):
        meta = _meta_of(row["data"])
        # Una riga senza contenuto (uno snapshot o una mano che l'ha tolto) non
        # è una bozza che si possa installare.
        if meta is None or not (base / row["id"] / "content").is_file():
            continue
        meta["id"] = row["id"]
        meta["target_sha256"] = sha256_of(root / meta["target"])
        items.append(meta)
    return items


def read_staged(root: Path, staged_id: str) -> tuple[dict[str, Any], bytes] | None:
    """Meta e contenuto di una bozza, o None se non c'è."""
    if not _is_id(staged_id):
        return None
    meta = _read_meta(Path(root), staged_id)
    if meta is None:
        return None
    try:
        return meta, (_dir(Path(root)) / staged_id / "content").read_bytes()
    except OSError:
        return None


def _is_id(staged_id: str) -> bool:
    return bool(staged_id) and staged_id.isalnum()


def install(root: Path, staged_id: str, expected_sha: str | None) -> str:
    """Scrive la bozza al suo posto. Ritorna ``installed``, ``conflict`` o ``missing``.

    *expected_sha* è lo sha del file che l'utente aveva davanti quando ha
    toccato «Installa» (``None`` = il file non esisteva): se ora è diverso, non
    si scrive niente.
    """
    from jenny.security.taint_ledger import ledger_for

    root = Path(root)
    with _LOCK:
        found = read_staged(root, staged_id)
        if found is None:
            return "missing"
        meta, content = found
        target = (root / meta["target"]).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError:
            logger.warning("Staged change {} points outside the workspace; discarded", staged_id)
            _forget(root, staged_id)
            return "missing"
        if sha256_of(target) != expected_sha:
            return "conflict"
        journal = _journal_of(root, meta)
        with _bound(journal, meta.get("journal_step")):
            if journal is not None:
                journal.touch(target)
            try:
                atomic_write(target, _keep_wiki_id(target, content))
            finally:
                if journal is not None:
                    journal.settle("install draft")
        # L'utente l'ha guardata e confermata: da qui in poi non è più contenuto
        # non fidato per chi la legge.
        ledger_for(root).clear(meta["target"])
        _forget(root, staged_id)
    logger.info("Installed staged change {} into {}", staged_id, meta["target"])
    _notify(root, "installed", staged_id, meta["target"])
    return "installed"


def _current_journal_key() -> tuple[str | None, str | None]:
    from jenny.agent.write_journal import current_journal, current_step

    journal = current_journal()
    return (journal.key, current_step()) if journal is not None else (None, None)


def _bound(journal: Any, step: Any):
    """Il registro della bozza legato mentre la si installa, col passo per cui
    era stata scritta: le righe sono di quel passo."""
    from contextlib import nullcontext

    from jenny.agent.write_journal import bind_journal

    if journal is None:
        return nullcontext()
    return bind_journal(journal, step=step if isinstance(step, str) else None)


def _journal_of(root: Path, meta: dict[str, Any]):
    """Il registro in cui va l'installazione: quello di chi ha scritto la bozza.

    L'installazione la fa l'utente dalla casa, fuori dal lavoro che l'ha
    scritta: il registro si ritrova dalla chiave nella bozza, e si annota a mano
    perché ``atomic_write`` non passa da nessun recinto.
    """
    key = meta.get("journal")
    if not isinstance(key, str) or not key:
        return None
    from jenny.agent.write_journal import journal_for

    try:
        return journal_for(root, key)
    except Exception:  # noqa: BLE001 — senza registro si installa lo stesso
        logger.opt(exception=True).warning("No undo journal for staged change {}", meta["id"])
        return None


def _keep_wiki_id(target: Path, content: bytes) -> bytes:
    """*content* con l'id del quaderno rimesso, se la bozza e' il suo ``AGENTS.md``.

    Gli strumenti dell'aiutante rifiutano gia' una scrittura che toglie l'id,
    bozza compresa: qui arriva una bozza nata **prima** che quel recinto ci
    fosse, o prima che la wiki avesse l'id che ha adesso. L'utente l'ha
    approvata, quindi si installa — con l'id. Una bozza di Piante, il 26/10,
    l'ha tolto senza che la scheda lo dicesse.
    """
    from jenny.utils.wiki_paths import keep_wiki_id

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return content
    kept = keep_wiki_id(target, text)
    return content if kept == text else kept.encode("utf-8")


def discard(root: Path, staged_id: str) -> bool:
    """Butta una bozza. True se c'era."""
    root = Path(root)
    with _LOCK:
        if not _is_id(staged_id):
            return False
        meta = _read_meta(root, staged_id)
        if meta is None and not (_dir(root) / staged_id).is_dir():
            return False
        _forget(root, staged_id)
    _notify(root, "discarded", staged_id, str((meta or {}).get("target") or ""))
    return True
