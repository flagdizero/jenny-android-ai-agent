"""Le migrazioni, in ordine, e cosa porta ciascuna.

Ogni migrazione legge da sola i file di un formato vecchio (con
``utils.path.read_json``/``iter_jsonl``, mai con le classi che li scrivevano:
quelle non ci sono più) e scrive attraverso l'interfaccia pubblica del nuovo
archivio. Così cancellarne il modulo non tocca niente del core.

``remove_after`` è la versione di Jenny da cui il modulo va cancellato:
``tests/db_migration/test_expiry.py`` diventa rosso quando ``pyproject.toml`` la
raggiunge. Di partenza, tre versioni minori dopo quella che la introduce.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jenny.db.database import Database

DONE = "done"
NOTHING = "nothing"
FAILED = "failed"
CLEANED = "cleaned"


@dataclass(frozen=True)
class MigrationContext:
    workspace: Path
    runtime_root: Path
    state: Database
    undo: Database


@dataclass
class MigrationResult:
    status: str  # DONE | NOTHING | FAILED
    sources: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    error: str | None = None


@dataclass(frozen=True)
class Migration:
    name: str
    remove_after: str
    run: Callable[[MigrationContext], MigrationResult]
    # I resti da togliere 30 giorni dopo il ``done`` (i ``*.migrated``): fino ad
    # allora tornare a un APK vecchio ritrova i dati.
    cleanup: Callable[[MigrationContext], None] | None = None


def migrations() -> tuple[Migration, ...]:
    """L'elenco, letto a ogni boot: un modulo per archivio, in ordine."""
    from jenny.db_migration import undo_cleanup

    return (
        Migration("undo_cleanup", remove_after="2.3.0", run=undo_cleanup.run),
    )
