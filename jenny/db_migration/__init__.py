"""Lo strumento di migrazione verso SQLite: separato, e da cancellare.

Tutto il codice che legge i formati vecchi sta in questo pacchetto e muore con
lui fra qualche versione (``registry.Migration.remove_after``). Il core lo
conosce per una riga sola: ``GatewayContainer`` chiama ``run_pending`` al boot,
dopo aver aperto i database e prima di chiunque li usi.
``tests/db_migration/test_isolation.py`` fallisce se qualcun altro lo importa.

Le regole di ogni migrazione:

- **ripetibile senza danni**: fonde per chiave e salta quel che c'è già, in una
  transazione per archivio; i file vecchi si rinominano ``*.migrated`` solo dopo
  il commit;
- **se fallisce** lascia i file vecchi come sono, la riga ``failed`` e un ERROR
  nel log (lo legge anche ``get_recent_logs``); il boot dopo ritenta;
- una ``done`` non gira più; 30 giorni dopo toglie i suoi ``*.migrated``.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from loguru import logger

from jenny.db_migration import runs
from jenny.db_migration.registry import (
    CLEANED,
    DONE,
    FAILED,
    NOTHING,
    Migration,
    MigrationContext,
    MigrationResult,
    migrations,
)

# Quanto restano i ``*.migrated`` dopo una migrazione riuscita.
KEEP_MIGRATED_S = 30 * 86_400


def run_pending(workspace: Path, *, now: float | None = None,
                registry: tuple[Migration, ...] | None = None) -> dict[str, str]:
    """Fa le migrazioni che mancano; ritorna nome → esito di questo boot.

    Non solleva mai: una migrazione che fallisce non ferma il gateway.
    """
    from jenny import db
    from jenny.snapshot.locations import runtime_root_for

    runtime_root = runtime_root_for(workspace)
    ctx = MigrationContext(
        workspace=Path(workspace), runtime_root=runtime_root,
        state=db.database(runtime_root, db.STATE), undo=db.database(runtime_root, db.UNDO),
    )
    outcome: dict[str, str] = {}
    for migration in (migrations() if registry is None else registry):
        try:
            outcome[migration.name] = _one(ctx, migration,
                                           time.time if now is None else (lambda: now))
        except Exception:
            logger.opt(exception=True).error("Migration {} could not run", migration.name)
            outcome[migration.name] = FAILED
    return outcome


def _one(ctx: MigrationContext, migration: Migration, clock: Callable[[], float]) -> str:
    now = clock()
    last = runs.last_run(ctx.state, migration.name)
    if last is not None and last["status"] in (DONE, NOTHING, CLEANED):
        if (last["status"] == DONE and migration.cleanup is not None
                and now - float(last["finished_at"] or now) >= KEEP_MIGRATED_S):
            migration.cleanup(ctx)
            runs.mark(ctx.state, migration.name, CLEANED)
            logger.info("Migration {}: old files removed", migration.name)
            return CLEANED
        return "skipped"
    try:
        result = migration.run(ctx)
    except Exception as exc:
        logger.opt(exception=True).error("Migration {} failed; old files left as they are",
                                         migration.name)
        result = MigrationResult(status=FAILED, error=f"{type(exc).__name__}: {exc}")
    runs.record(ctx.state, migration.name, result, started_at=now, finished_at=clock())
    if result.status == FAILED:
        logger.error("Migration {} failed: {}", migration.name, result.error)
    elif result.status == DONE:
        logger.info("Migration {}: {} from {}", migration.name, result.counts, result.sources)
    return result.status


__all__ = ["run_pending"]
