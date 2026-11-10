"""Fermare gli aiutanti: il guardiano di chi è fermo, la cancellazione, lo spegnimento.

Chi è fermo da troppo, chi va fermato, e come si chiude tutto allo shutdown.

Un pezzo di :class:`jenny.agent.subagent.SubagentManager` (mixin): il testo dei
metodi è quello di prima, spostato qui per responsabilità.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import TYPE_CHECKING

from loguru import logger

from jenny.agent.subagent_records import (
    CANCEL_REASON_SHUTDOWN,
    CANCEL_REASON_USER,
    STOPPED_BY,
    SubagentSpec,
    cancellation_summary,
)

if TYPE_CHECKING:
    from typing import Any, Callable

    from jenny.agent.subagent_common import SubagentStatus, _Lineage


class StopMixin:
    """Chi è fermo da troppo, chi va fermato, e come si chiude tutto allo shutdown.

    Mixin di ``SubagentManager``.
    """

    if TYPE_CHECKING:
        # Contratto host↔mixin (solo per il type-checker; nessun effetto a
        # runtime): gli attributi di ``SubagentManager`` e i metodi degli
        # altri pezzi che questi metodi usano.
        _lineages: dict[str, _Lineage]
        _repudiated_task_ids: set[str]
        _running_tasks: dict[str, asyncio.Task[None]]
        _session_tasks: dict[str, set[str]]
        _stall_check_interval_s: Any
        _stall_listener: Callable[[str, list[str]], None] | None
        _task_statuses: dict[str, SubagentStatus]
        _watchdog_task: asyncio.Task[None] | None
        stall_threshold_s: Any

        async def _announce_result(self, task_id: str, label: str, task: str, result: str, origin: dict[str, str], status: str, origin_message_id: str | None=None, *, lineage_id: str | None=None, attempt: int | None=None, force: bool=False, resumable: bool=False, budget_stops: int=1, interrupted: bool=False) -> None: ...

        def _mark_cancelled(self, task_id: str, reason: str) -> SubagentStatus | None: ...

        def _publish_status_snapshot(self, spec: SubagentSpec) -> None: ...

        def _publish_transition(self, spec: SubagentSpec, state: str, label: str) -> None: ...

    def _ensure_stall_watchdog(self) -> None:
        """Crea lazy l'unico task di vigilanza per tutti i subagent.

        Uno solo, non uno per subagent: su Android ogni task in piu e memoria e
        wakeup. Il loop esce da solo quando non resta nulla da vigilare, e uno
        spawn successivo lo ricrea.
        """
        if self.stall_threshold_s <= 0:
            return
        if self._watchdog_task is not None and not self._watchdog_task.done():
            return
        self._watchdog_task = asyncio.create_task(self._stall_watchdog_loop())

    async def _stall_watchdog_loop(self) -> None:
        while self._running_tasks:
            await asyncio.sleep(self._stall_check_interval_s)
            try:
                by_session: dict[str, list[str]] = {}
                for task_id in self.check_stalls():
                    if (spec := self._spec_for_task(task_id)) is not None:
                        status = self._task_statuses.get(task_id)
                        self._publish_transition(
                            spec, "stalled", status.label if status else spec.label
                        )
                        if spec.session_key:
                            by_session.setdefault(spec.session_key, []).append(task_id)
                self._notify_stalls(by_session)
            except Exception:  # noqa: BLE001 — il watchdog non deve mai morire
                logger.exception("Subagent stall check failed")

    def _notify_stalls(self, by_session: dict[str, list[str]]) -> None:
        """Passa le marcature di questo giro all'ascoltatore, se c'e.

        Isolato per sessione: un ascoltatore che solleva non deve togliere la
        notizia alle altre sessioni, ne fermare il watchdog.
        """
        listener = self._stall_listener
        if listener is None:
            return
        for session_key, task_ids in by_session.items():
            try:
                listener(session_key, task_ids)
            except Exception:  # noqa: BLE001 — vedi docstring
                logger.exception("Subagent stall listener failed for {}", session_key)

    def _spec_for_task(self, task_id: str) -> SubagentSpec | None:
        """Spec del tentativo vivo ``task_id``, se ancora tracciato in RAM."""
        status = self._task_statuses.get(task_id)
        if status is not None and (lin := self._lineages.get(status.lineage_id)) is not None:
            return lin.spec
        for lin in self._lineages.values():
            if lin.task_id == task_id:
                return lin.spec
        return None

    def check_stalls(self, *, now: float | None = None) -> list[str]:
        """Marca ``stalled`` i subagent senza progresso da oltre la soglia.

        MARCA E BASTA: non cancella nulla. La decisione di rilanciare e
        dell'utente o dell'orchestratore, e un subagent lento non e un subagent
        rotto. Simmetricamente, uno ``stalled`` che riprende a progredire torna
        ``running``.

        Ritorna i task id marcati in questo giro.
        """
        moment = time.monotonic() if now is None else now
        marked: list[str] = []
        for task_id, status in list(self._task_statuses.items()):
            if status.state not in ("running", "stalled"):
                continue
            idle = moment - status.last_progress_at
            if idle >= self.stall_threshold_s:
                if status.state != "stalled":
                    status.state = "stalled"
                    marked.append(task_id)
                    logger.warning(
                        "Subagent [{}] marked stalled after {:.0f}s without progress",
                        task_id, idle,
                    )
            elif status.state == "stalled":
                status.state = "running"
        return marked

    async def _cancel_stall_watchdog(self) -> None:
        """Ferma il watchdog senza lasciare task orfani allo shutdown."""
        task = self._watchdog_task
        self._watchdog_task = None
        if task is None or task.done():
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def cancel_by_session(self, session_key: str, *, grace_s: float = 2.0) -> int:
        """Cancel all subagents for the given session. Returns count cancelled.

        Bounded: attende al massimo ``grace_s`` e abbandona i task che non
        muoiono (es. bloccati in un thread non interrompibile). Gli abbandonati
        vengono marcati ripudiati così il loro announce tardivo è soppresso.
        """
        by_task: dict[asyncio.Task, str] = {
            self._running_tasks[tid]: tid
            for tid in self._session_tasks.get(session_key, [])
            if tid in self._running_tasks and not self._running_tasks[tid].done()
        }
        tasks = list(by_task)
        for t in tasks:
            # Provenienza scritta PRIMA della cancellazione: dopo il cancel la
            # done-callback puo girare in qualunque momento e il record verrebbe
            # persistito senza. Questo path e /stop, quindi la provenienza e
            # l'utente (vedi ``AgentLoop`` in loop_tasks.py).
            if (st := self._mark_cancelled(by_task[t], CANCEL_REASON_USER)) is not None:
                st.stopped_by = "user"
            t.cancel()
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=grace_s)
            if pending:
                abandoned_ids = [by_task[t] for t in pending]
                self._repudiated_task_ids.update(abandoned_ids)
                logger.warning(
                    "Abandoned {} stuck subagent(s) for session {} after {}s grace: {}",
                    len(pending), session_key, grace_s, abandoned_ids,
                )
        return len(tasks)

    async def cancel_task(
        self, task_id: str, *, grace_s: float = 2.0, by: str = "user",
    ) -> bool:
        """Cancella un singolo tentativo vivo. Ritorna ``True`` se c'era da fermare.

        Pensata per lo stop puntuale (bottone in UI, orchestratore): a differenza
        di ``cancel_by_session`` non tocca gli altri subagent della sessione.

        Il tentativo viene ripudiato (l'announce naturale, se riesce a partire,
        è stale) **ma al suo posto ne viene iniettato uno esplicito di
        cancellazione**. Non è rumore: l'orchestratore è tipicamente fermo
        mid-turn in attesa proprio di quell'injection, e senza nulla il turno
        resta appeso fino al timeout della pending queue (300s) — l'utente
        preme Stop e vede "Agent running" per cinque minuti.
        """
        task = self._running_tasks.get(task_id)
        if task is None or task.done():
            return False
        lineage_id: str | None = None
        attempt: int | None = None
        if (status := self._mark_cancelled(task_id, CANCEL_REASON_USER)) is not None:
            lineage_id = status.lineage_id or None
            attempt = status.attempt
            status.stopped_by = by if by in STOPPED_BY else "user"
        self._repudiated_task_ids.add(task_id)
        # Snapshot pubblicato subito, non solo dalla done-callback: un task che
        # non muore entro il grace verrebbe abbandonato e il pannello lo
        # mostrerebbe "running" per sempre.
        spec = self._spec_for_task(task_id)
        if spec is not None:
            self._publish_status_snapshot(spec)
        task.cancel()
        _, pending = await asyncio.wait([task], timeout=grace_s)
        if pending:
            logger.warning(
                "Abandoned stuck subagent [{}] after {}s grace", task_id, grace_s
            )
        if spec is not None:
            await self._announce_result(
                task_id,
                spec.label,
                spec.task,
                # Stesso testo che finisce nel record e in ``subagent_status``:
                # l'orchestratore deve leggere la stessa regola sia sull'annuncio
                # immediato sia rileggendo lo storico dopo un riavvio.
                cancellation_summary(CANCEL_REASON_USER),
                {
                    "channel": spec.origin_channel,
                    "chat_id": spec.origin_chat_id,
                    "session_key": spec.session_key,
                },
                "cancelled",
                spec.origin_message_id,
                lineage_id=lineage_id,
                attempt=attempt,
                force=True,
            )
        return True

    async def drain(self, *, timeout_s: float = 10.0) -> int:
        """Attende la fine di TUTTI i subagent in volo; cancella quelli che
        sforano ``timeout_s``. Usato dallo shutdown ordinato del gateway.

        Ritorna il numero di subagent che erano ancora attivi all'inizio."""
        # Il watchdog va fermato per primo: e un task periodico del manager,
        # non un subagent, e nessuno lo aspetterebbe.
        await self._cancel_stall_watchdog()
        by_task: dict[asyncio.Task[None], str] = {
            task: task_id
            for task_id, task in self._running_tasks.items()
            if not task.done()
        }
        tasks = list(by_task)
        if not tasks:
            return 0
        _, pending = await asyncio.wait(tasks, timeout=timeout_s)
        for t in pending:
            # Solo i pending: chi ha finito da se ha gia il proprio esito, e
            # sovrascriverlo con "cancellato allo shutdown" sarebbe una bugia.
            self._mark_cancelled(by_task[t], CANCEL_REASON_SHUTDOWN)
            t.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        return len(tasks)
