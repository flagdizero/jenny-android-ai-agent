"""Lo stato di Jenny in SQLite; i contenuti restano file.

Sotto ``<runtime_root>/db/`` (accanto al workspace, fuori dalla portata dei
tool del modello):

- ``state.sqlite`` — lo stato che viaggia: va nel backup, e in ogni snapshot a
  pezzi di testo (``shards``), così la storia locale e Drive lo riportano
  insieme ai file. Lo riempiono, un archivio alla volta, i job, i record degli
  aiutanti, le sessioni.
- ``undo.sqlite`` — il diario dell'annulla (``agent.write_journal``) e i piani
  che ci lavorano sopra (``plan.store``). Solo sul telefono: non va nel backup
  e si svuota a un ripristino, piani compresi.

Restano file, per scelta: ``config.json`` (lo legge anche Kotlin), le pagine e
la bacheca dei quaderni, ``MEMORY.md``/``SOUL.md``/``USER.md`` e gli appunti, le
skill, le app, gli upload. Il modello li legge e li scrive coi suoi strumenti, e
un quaderno è una cartella che si sposta intera.

Un ``Database`` per file, aperto la prima volta che serve e tenuto qui: lo
stesso per tutti i thread, e uno diverso per ogni workspace (i test ne aprono
tanti nello stesso processo). Il boot li apre tutti all'inizio
(``open_all``) e lo spegnimento li chiude (``close_all``).
"""

from __future__ import annotations

import threading
from pathlib import Path

from jenny.db.database import Database, SchemaError, SchemaTooNewError
from jenny.db.schema import Schema
from jenny.snapshot.locations import db_dir_for_runtime_root, runtime_root_for

__all__ = [
    "DATABASE_NAMES",
    "Database",
    "Schema",
    "SchemaError",
    "SchemaTooNewError",
    "close_all",
    "database",
    "database_for_workspace",
    "forget",
    "open_all",
]

STATE = "state"
UNDO = "undo"
DATABASE_NAMES = (STATE, UNDO)

_OPEN: dict[Path, Database] = {}
_GUARD = threading.Lock()


def _path(runtime_root: Path, name: str) -> Path:
    if name not in DATABASE_NAMES:
        raise ValueError(f"unknown database {name!r}")
    return db_dir_for_runtime_root(runtime_root) / f"{name}.sqlite"


def database(runtime_root: Path, name: str) -> Database:
    """Il database *name* di *runtime_root*, aperto se non lo è ancora."""
    path = _path(runtime_root, name).resolve()
    with _GUARD:
        db = _OPEN.get(path)
        if db is None or db.closed:
            db = Database(path, name=name)
            _OPEN[path] = db
        return db


def database_for_workspace(workspace: Path, name: str) -> Database:
    return database(runtime_root_for(workspace), name)


def open_all(runtime_root: Path) -> list[Database]:
    """Apre i database di *runtime_root*: al boot, prima di chiunque li usi."""
    return [database(runtime_root, name) for name in DATABASE_NAMES]


def close_all() -> None:
    """Chiude ogni database aperto; il prossimo ``database`` li riapre."""
    with _GUARD:
        dbs = list(_OPEN.values())
        _OPEN.clear()
    for db in dbs:
        db.close()


def forget(runtime_root: Path, name: str) -> None:
    """Chiude e dimentica un database, per chi deve sostituirne il file."""
    path = _path(runtime_root, name).resolve()
    with _GUARD:
        db = _OPEN.pop(path, None)
    if db is not None:
        db.close()
