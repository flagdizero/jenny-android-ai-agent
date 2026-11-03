"""I resti del vecchio annulla: la cartella ``subagents/journal`` nel workspace.

Prima di ``undo.sqlite`` il registro di ogni aiutante stava in
``<workspace>/subagents/journal/<lineage>/`` (``entries.jsonl``, ``blobs/``,
``undo.json``). Non si importa: l'annulla non promette niente sul lavoro fatto
prima dell'aggiornamento (decisione del 02/11/2026). Si cancella, e con lei le
voci che la tenevano fuori dalle foto e dagli aiutanti
(``snapshot.engine.ALWAYS_EXCLUDED_DIRS``, ``subagent_policy._SECRET_DIRS``)
quando questo modulo si cancella.
"""

from __future__ import annotations

import shutil

from jenny.db_migration.registry import DONE, NOTHING, MigrationContext, MigrationResult

OLD_JOURNAL = ("subagents", "journal")


def run(ctx: MigrationContext) -> MigrationResult:
    old = ctx.workspace.joinpath(*OLD_JOURNAL)
    if not old.exists():
        return MigrationResult(NOTHING)
    lineages = sum(1 for p in old.iterdir() if p.is_dir()) if old.is_dir() else 0
    shutil.rmtree(old)
    return MigrationResult(DONE, sources=["/".join(OLD_JOURNAL)],
                           counts={"old_journals_removed": lineages})
