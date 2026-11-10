"""I registri dell'annulla degli aiutanti, e l'annulla di ciò che uno ha scritto.

Il registro di ogni aiutante e il «Ferma e annulla».

Un pezzo di :class:`jenny.agent.subagent.SubagentManager` (mixin): il testo dei
metodi è quello di prima, spostato qui per responsabilità.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from jenny.agent.subagent_records import STOPPED_BY
from jenny.agent.write_journal import (
    HELPER,
    UndoReport,
    WriteJournal,
    journal_for,
    journal_key,
    journal_stats,
    live_journal,
    sweep_orphan_journals,
)

if TYPE_CHECKING:
    from typing import Any

    from jenny.agent.subagent_records import SubagentSpec


class SubagentUndoError(RuntimeError):
    """Sollevata da ``SubagentManager.undo`` quando l'annulla non si può fare
    adesso (aiutante sconosciuto, o ancora al lavoro). Il testo è user-facing."""


class UndoMixin:
    """Il registro di ogni aiutante e il «Ferma e annulla».

    Mixin di ``SubagentManager``.
    """

    if TYPE_CHECKING:
        # Contratto host↔mixin (solo per il type-checker; nessun effetto a
        # runtime): gli attributi di ``SubagentManager`` e i metodi degli
        # altri pezzi che questi metodi usano.
        UNDO_WAIT_FOR_THREADS_S: float
        _history: Any
        _journal_cache: dict[str, tuple[int, UndoReport | None]]
        _journals: dict[str, WriteJournal]
        _records: Any
        workspace: Path

        def _live_task_for(self, target_id: str) -> str | None: ...

        def _publish_status_snapshot(self, spec: SubagentSpec) -> None: ...

        def _resolve_target(self, target_id: str) -> tuple[str, SubagentSpec, int] | None: ...

        async def cancel_task(self, task_id: str, *, grace_s: float=2.0, by: str='user') -> bool: ...

    def _journal_root(self) -> Path | None:
        """La radice dell'installazione, se il workspace è un path vero.

        Come ``SubagentRecordStore.running_root``: i test costruiscono il manager
        con doppi del workspace, e un registro non deve creare cartelle a caso.
        """
        return Path(self.workspace) if isinstance(self.workspace, (str, Path)) else None

    def recover_journals(self) -> None:
        """Al boot: toglie i registri dei lineage senza più record.

        TTL e potatura: nessuno può più chiederne l'annulla. Gli annulla rimasti
        a metà li ha già finiti il container, prima che l'agente nasca
        (``write_journal.finish_interrupted_undos``). Best-effort: un boot non
        fallisce per questo.
        """
        root = self._journal_root()
        if root is None:
            return
        try:
            keep = {r.lineage_id for r in self._records.load_all()}
            keep |= {m.lineage_id for m in self._records.load_running()}
            # I registri che i piani nominano ancora: il ritorno di un piano
            # toglie anche il lavoro degli aiutanti dei suoi passi.
            from jenny.agent.staged_writes import pending_journal_keys
            from jenny.plan.work import kept_journal_keys

            # E quelli delle bozze in attesa: installata, una bozza è lavoro di
            # chi l'ha scritta, e il suo annulla la deve poter togliere.
            removed = sweep_orphan_journals(
                root, keep, kept_journal_keys(root) | pending_journal_keys(root))
            if removed:
                logger.info("Swept {} orphan subagent journal(s)", removed)
        except Exception as e:  # noqa: BLE001 — vedi docstring
            logger.warning("Subagent journal recovery failed: {}", e)

    def _journal_for(self, lineage_id: str) -> WriteJournal | None:
        """Il registro del lineage, creato alla prima richiesta."""
        root = self._journal_root()
        if root is None or not lineage_id:
            return None
        journal = self._journals.get(lineage_id)
        if journal is None:
            try:
                journal = journal_for(root, journal_key(HELPER, lineage_id))
            except Exception as e:  # noqa: BLE001 — senza registro si lavora lo stesso
                logger.warning("Subagent journal unavailable for {}: {}", lineage_id, e)
                return None
            self._journals[lineage_id] = journal
            self._journal_cache.pop(lineage_id, None)
        return journal

    def _journal_view(self, lineage_id: str) -> tuple[int, UndoReport | None]:
        """File cambiati dal lineage e il suo ultimo annulla, per lo snapshot."""
        journal = self._journals.get(lineage_id)
        if journal is None and (root := self._journal_root()) is not None:
            # Un registro vivo che il manager non ha aperto: la pubblicazione di
            # un'app annota nel registro del suo app-builder (``app_tool``).
            journal = live_journal(root, journal_key(HELPER, lineage_id))
        cached = self._journal_cache.get(lineage_id)
        if cached is None:
            # Dal database una volta per lineage: l'ultimo annulla sta in ``undos``.
            root = self._journal_root()
            try:
                cached = (journal_stats(root, journal_key(HELPER, lineage_id))
                          if root is not None else (0, None))
            except Exception:  # noqa: BLE001 — uno snapshot non fallisce per il registro
                cached = (0, None)
            if len(self._journal_cache) > 256:
                self._journal_cache.clear()
            self._journal_cache[lineage_id] = cached
        if journal is not None:
            return journal.changed_files, cached[1]
        return cached

    async def undo(self, target_id: str, *, by: str = "user") -> UndoReport:
        """Toglie il lavoro di un aiutante: rimette i file che ha cambiato.

        *target_id* è un task id o un lineage id; si annulla tutto il lineage,
        cioè anche i tentativi fatti continuare dopo un budget o un riavvio. Solo
        i file ancora come li ha lasciati lui (``agent.write_journal``): gli
        altri restano, e il resoconto dice quali e perché.

        Rifiuta un aiutante ancora al lavoro: prima si ferma (``cancel_task``),
        o due mani scriverebbero sugli stessi file. Dopo l'annulla la sua
        conversazione non si riprende più (la storia si toglie): continuarla
        ripartirebbe da file che non ci sono.
        """
        resolved = self._resolve_target(target_id)
        lineage_id = resolved[0] if resolved is not None else None
        if lineage_id is None:
            raise SubagentUndoError(f"No helper '{target_id}' to undo.")
        if self._live_task_for(lineage_id) is not None:
            raise SubagentUndoError(
                "This helper is still working: stop it first, then undo its changes."
            )
        journal = self._journal_for(lineage_id)
        if journal is None:
            raise SubagentUndoError("This helper's changes were not recorded: nothing to undo.")
        # Un ``python_exec`` cancellato può girare ancora nel suo thread (i thread
        # non si interrompono in mezzo a codice C): annullare adesso rimetterebbe
        # file che sta ancora scrivendo.
        deadline = time.monotonic() + self.UNDO_WAIT_FOR_THREADS_S
        while journal.busy and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
        if journal.busy:
            raise SubagentUndoError(
                "This helper is still finishing a piece of code: try again in a moment."
            )
        report = await asyncio.to_thread(journal.undo, by=by if by in STOPPED_BY else "user")
        self._journal_cache[lineage_id] = (0, report)
        self._history.drop(lineage_id)
        logger.info(
            "Subagent lineage {} undone by {}: {} restored, {} kept",
            lineage_id, report.by, len(report.restored), len(report.kept),
        )
        spec = resolved[1] if resolved is not None else None
        if spec is not None:
            self._publish_status_snapshot(spec)
        return report

    async def cancel_and_undo(
        self, task_id: str, *, by: str = "user", grace_s: float = 5.0,
    ) -> UndoReport:
        """«Ferma e annulla»: ferma il tentativo vivo (se c'è), poi annulla.

        L'attesa è più lunga di quella di un semplice stop: l'annulla non può
        partire finché lo strumento in corso non è uscito, o rimetterebbe un file
        che l'aiutante sta ancora scrivendo. Il task asyncio muore subito, ma un
        ``python_exec`` può continuare nel suo thread: quello lo aspetta
        :meth:`undo` (``UNDO_WAIT_FOR_THREADS_S``), e se non esce rifiuta.
        """
        # Il tentativo vivo si cerca nel lineage, come fa :meth:`undo`: con l'id
        # di un tentativo vecchio (una bolla rimasta aperta dopo un rilancio)
        # quello vivo non si trovava, niente veniva fermato e l'annulla
        # rifiutava «sta ancora lavorando».
        resolved = self._resolve_target(task_id)
        lineage_id = resolved[0] if resolved is not None else task_id
        live = self._live_task_for(lineage_id) or self._live_task_for(task_id)
        if live is not None:
            await self.cancel_task(live, grace_s=grace_s, by=by)
        return await self.undo(task_id, by=by)
