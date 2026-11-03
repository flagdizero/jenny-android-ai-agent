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
    # Se ci sono ancora file vecchi da portare. Una migrazione ``done`` rigira
    # quando tornano: il ripristino di uno snapshot di prima riporta i file
    # vecchi nel workspace, e la riga ``done`` non deve lasciarli lì.
    pending: Callable[[MigrationContext], bool] | None = None


def migrations() -> tuple[Migration, ...]:
    """L'elenco, letto a ogni boot: un modulo per archivio, in ordine."""
    from jenny.db_migration import (
        cron,
        plans,
        small_state,
        staged_taint,
        subagents,
        undo_cleanup,
    )

    return (
        Migration("undo_cleanup", remove_after="2.3.0", run=undo_cleanup.run,
                  pending=undo_cleanup.pending),
        Migration("cron", remove_after="2.3.0", run=cron.run,
                  pending=cron.pending, cleanup=cron.cleanup),
        # Dopo ``undo_cleanup``: un piano di formato 1 lo converte comunque da sé.
        Migration("plans", remove_after="2.3.0", run=plans.run,
                  pending=plans.pending, cleanup=plans.cleanup),
        Migration("subagents", remove_after="2.3.0", run=subagents.run,
                  pending=subagents.pending, cleanup=subagents.cleanup),
        Migration("staged_taint", remove_after="2.3.0", run=staged_taint.run,
                  pending=staged_taint.pending, cleanup=staged_taint.cleanup),
        Migration("small_state", remove_after="2.3.0", run=small_state.run,
                  pending=small_state.pending, cleanup=small_state.cleanup),
    )
