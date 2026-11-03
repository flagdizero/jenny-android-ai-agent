"""I database in un ripristino: lo stato torna dal backup, l'annulla si svuota.

Il ripristino scambia il workspace al boot, prima che qualcuno lo apra
(``snapshot.restore_marker``). I database stanno fuori dal workspace e lo
scambio non li porterebbe: li scambia questo modulo, nello stesso momento e con
le stesse regole — a file chiusi, idempotente, il vecchio tenuto da parte.

- ``state.sqlite`` arriva in ``db_staged/`` — dal file di backup
  (``stage_state``), o rifatto dai pezzi che uno snapshot della storia locale o
  di Drive porta con sé (``stage_state_from_tree``, ``jenny/db/shards``) — e
  prende il posto di quello vivo, che resta come ``state.sqlite.pre-restore``.
  Uno snapshot senza stato (fatto prima che lo stato fosse in SQLite) lascia
  quello di adesso: i file vecchi che riporta li fonde la migrazione.
- ``undo.sqlite`` e i suoi oggetti si cancellano a **ogni** ripristino: il
  diario dice come tornare indietro dai file di prima, e dopo un ripristino
  quei file non ci sono più. Un annulla rimasto a metà verrebbe applicato a file
  che non ha mai visto. Con lui vanno i piani (``plan.store``): un piano vive
  del suo diario, e non deve restare a promettere un ritorno che non c'è più.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path

from loguru import logger

from jenny.snapshot.locations import STAGED_DB_DIR_NAME, db_dir_for_runtime_root

STATE_FILE = "state.sqlite"
UNDO_FILE = "undo.sqlite"
UNDO_OBJECTS_DIR_NAME = "undo-objects"
PRE_RESTORE_SUFFIX = ".pre-restore"
# I file accanto a un database in WAL: vanno e vengono col database.
_SIDECARS = ("-wal", "-shm")
_SQLITE_HEADER = b"SQLite format 3\x00"


class StagedStateError(ValueError):
    """Lo ``state.sqlite`` di un backup non è un database sano."""


def staged_db_dir(runtime_root: Path) -> Path:
    return Path(runtime_root) / STAGED_DB_DIR_NAME


def stage_state(runtime_root: Path, source) -> Path:
    """Copia lo ``state.sqlite`` di un backup (un file aperto) in ``db_staged/``.

    Si controlla prima di prometterlo: un file che non è SQLite, o che SQLite
    dice rotto, fa fallire lo staging come un archivio rotto.
    """
    dest_dir = staged_db_dir(runtime_root)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / STATE_FILE
    with open(dest, "wb") as out:
        shutil.copyfileobj(source, out, 1 << 20)
    problem = _state_problem(dest)
    if problem is not None:
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise StagedStateError(problem)
    return dest


def stage_state_from_tree(runtime_root: Path, tree: Path) -> bool:
    """Lo stato che uno snapshot ripristinato in *tree* porta, in ``db_staged/``.

    I pezzi escono comunque dall'albero: non sono file del workspace. Ritorna
    True se ha preparato uno stato; False se lo snapshot non ne aveva, o se lo
    staging ne aveva già uno (il file di backup porta il suo database intero).
    """
    from jenny import db
    from jenny.db.shards import ShardsError, build
    from jenny.snapshot.types import STATE_ENTRY_DIR

    shards_dir = Path(tree) / STATE_ENTRY_DIR
    try:
        if not shards_dir.is_dir():
            return False
        dest = staged_db_dir(runtime_root) / STATE_FILE
        if dest.exists():
            return False
        known = db.database(Path(runtime_root), db.STATE).known_versions()
        try:
            build(shards_dir, dest, known=known)
        except ShardsError as exc:
            raise StagedStateError(str(exc)) from exc
        problem = _state_problem(dest)
        if problem is not None:
            dest.unlink(missing_ok=True)
            raise StagedStateError(problem)
        return True
    finally:
        shutil.rmtree(shards_dir, ignore_errors=True)


def _state_problem(path: Path) -> str | None:
    with open(path, "rb") as fh:
        if fh.read(len(_SQLITE_HEADER)) != _SQLITE_HEADER:
            return "state.sqlite is not a SQLite database"
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            verdict = conn.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return f"state.sqlite cannot be read: {exc}"
    return None if verdict == "ok" else f"state.sqlite is damaged: {verdict}"


def apply_staged(runtime_root: Path) -> bool:
    """Al boot di un ripristino: lo stato del backup al suo posto, l'annulla vuoto.

    Ritorna True se ha messo uno ``state.sqlite`` nuovo. Va chiamata solo
    quando il workspace è stato scambiato (o lo era già, nel giro dopo un
    crash): è idempotente.
    """
    runtime_root = Path(runtime_root)
    live_dir = db_dir_for_runtime_root(runtime_root)
    live_dir.mkdir(parents=True, exist_ok=True)
    swapped = False
    staged = staged_db_dir(runtime_root) / STATE_FILE
    if staged.is_file():
        live = live_dir / STATE_FILE
        if live.exists():
            _move_with_sidecars(live, live_dir / (STATE_FILE + PRE_RESTORE_SUFFIX))
        else:
            _drop_sidecars(live)
        os.replace(staged, live)
        swapped = True
        logger.info("State database restored from the backup")
    shutil.rmtree(staged_db_dir(runtime_root), ignore_errors=True)
    reset_undo(runtime_root)
    return swapped


def reset_undo(runtime_root: Path) -> None:
    """Il diario dell'annulla da zero: il file, i suoi compagni, gli oggetti."""
    live_dir = db_dir_for_runtime_root(Path(runtime_root))
    undo = live_dir / UNDO_FILE
    undo.unlink(missing_ok=True)
    _drop_sidecars(undo)
    shutil.rmtree(live_dir / UNDO_OBJECTS_DIR_NAME, ignore_errors=True)


def _move_with_sidecars(src: Path, dest: Path) -> None:
    for suffix in ("",) + _SIDECARS:
        target = Path(str(dest) + suffix)
        target.unlink(missing_ok=True)
        origin = Path(str(src) + suffix)
        if origin.exists():
            os.replace(origin, target)


def _drop_sidecars(path: Path) -> None:
    for suffix in _SIDECARS:
        Path(str(path) + suffix).unlink(missing_ok=True)
