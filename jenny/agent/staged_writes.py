"""Bozze in attesa: scritture sensibili di un subagent non fidato.

Un subagent che ha letto contenuto non fidato (``security.subagent_policy``)
può ancora voler cambiare un file che Jenny carica o esegue da sola: una skill,
un promemoria in ``cron/``, ``USER.md``. Rifiutarlo
toglierebbe una funzione («installa questa skill da GitHub»); scriverlo darebbe
a una pagina ostile un posto da cui agire più tardi senza nessuno davanti. La
via di mezzo è questa: la scrittura va in bozza, e solo un tocco dell'utente in
app la mette al suo posto. Nessun tool del modello raggiunge ``install``: lo
chiama soltanto la RPC ``staged.install`` della WebUI.

Forma su disco: ``.jenny/staged/<id>/meta.json`` + ``content``. La cartella è
SECRET per i subagent, quindi un lavoro non fidato non può riscrivere una bozza
già in attesa né cambiarne la descrizione.

``install`` verifica che il file di destinazione sia ancora quello visto
quando la bozza è stata mostrata (``expected_sha``): se nel frattempo è
cambiato, la risposta è ``conflict`` e non si scrive niente.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import threading
import time
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.utils.path import atomic_write

STAGED_REL = (".jenny", "staged")
MAX_PENDING = 50

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


def _read_meta(entry: Path) -> dict[str, Any] | None:
    try:
        meta = json.loads((entry / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return meta if isinstance(meta, dict) and isinstance(meta.get("target"), str) else None


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
        existing = None
        pending = 0
        if base.is_dir():
            for entry in base.iterdir():
                meta = _read_meta(entry)
                if meta is None:
                    continue
                pending += 1
                if meta.get("target") == target_rel and meta.get("task_id") == task_id:
                    existing = entry.name
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
            "created_ms": int(time.time() * 1000),
        }
        atomic_write(entry / "meta.json", json.dumps(meta, ensure_ascii=False), fsync_dir=False)
    _notify(root, "staged", staged_id, target_rel)
    return StagedRef(staged_id, target_rel)


def list_staged(root: Path) -> list[dict[str, Any]]:
    """Le bozze in attesa, dalla più vecchia. Ognuna con lo sha attuale del file."""
    root = Path(root)
    base = _dir(root)
    if not base.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for entry in base.iterdir():
        meta = _read_meta(entry)
        if meta is None or not (entry / "content").is_file():
            continue
        meta["id"] = entry.name
        meta["target_sha256"] = sha256_of(root / meta["target"])
        items.append(meta)
    items.sort(key=lambda m: m.get("created_ms") or 0)
    return items


def read_staged(root: Path, staged_id: str) -> tuple[dict[str, Any], bytes] | None:
    """Meta e contenuto di una bozza, o None se non c'è."""
    entry = _entry(root, staged_id)
    if entry is None:
        return None
    meta = _read_meta(entry)
    if meta is None:
        return None
    try:
        return meta, (entry / "content").read_bytes()
    except OSError:
        return None


def _entry(root: Path, staged_id: str) -> Path | None:
    if not staged_id or not staged_id.isalnum():
        return None
    entry = _dir(Path(root)) / staged_id
    return entry if entry.is_dir() else None


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
            shutil.rmtree(_dir(root) / staged_id, ignore_errors=True)
            return "missing"
        if sha256_of(target) != expected_sha:
            return "conflict"
        atomic_write(target, _keep_wiki_id(target, content))
        # L'utente l'ha guardata e confermata: da qui in poi non è più contenuto
        # non fidato per chi la legge.
        ledger_for(root).clear(meta["target"])
        shutil.rmtree(_dir(root) / staged_id, ignore_errors=True)
    logger.info("Installed staged change {} into {}", staged_id, meta["target"])
    _notify(root, "installed", staged_id, meta["target"])
    return "installed"


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
        entry = _entry(root, staged_id)
        if entry is None:
            return False
        meta = _read_meta(entry)
        shutil.rmtree(entry, ignore_errors=True)
    _notify(root, "discarded", staged_id, str((meta or {}).get("target") or ""))
    return True
