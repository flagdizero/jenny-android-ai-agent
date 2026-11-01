"""Ciclo di vita dei task del loop (estratto da loop.py).

`LoopTasksMixin` raccoglie la gestione dei task in volo e il drain di shutdown:
cancellazione dei task attivi per sessione, eviction del bookkeeping delle
sessioni pruned, reset del goal su hard-cancel, e il drain ordinato allo
shutdown (turni → subagent → background) che garantisce l'invariante Fase 1
"nessun writer di session.messages attivo durante flush_all". Mixato in
``AgentLoop`` verbatim: `self` risolve via MRO. Il cuore FSM/scheduling
(`run`/`_dispatch`/`_run_agent_loop`) resta in ``loop.py``.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from functools import partial
from typing import TYPE_CHECKING

from loguru import logger

from jenny.session.goal_state import cancel_active_goal

if TYPE_CHECKING:
    from jenny.agent.cron_turns import CronTurnCoordinator
    from jenny.agent.session_locks import SessionLocks
    from jenny.agent.subagent import SubagentManager
    from jenny.agent.tools.file_state import FileStateStore
    from jenny.agent.turn_epochs import TurnEpochs, TurnToken
    from jenny.bus.queue import MessageBus
    from jenny.bus.runtime_events import RuntimeEventPublisher
    from jenny.session.manager import Session, SessionManager

# Grace period concesso a un task cancellato per morire prima di abbandonarlo.
# Bounded di proposito: /stop è dispatchato inline nel loop di intake, quindi
# questo è il costo massimo che uno stop può imporre al consumo dei messaggi.
_CANCEL_GRACE_S = 2.0


class LoopTasksMixin:
    """Gestione task in volo + drain di shutdown (mixin di AgentLoop)."""

    if TYPE_CHECKING:
        # Contratto host↔mixin (solo per il type-checker; nessun effetto a
        # runtime). Attributi forniti da ``AgentLoop.__init__``.
        _active_tasks: dict[str, list[asyncio.Task]]
        _background_tasks: list[asyncio.Task]
        _cron_turns: CronTurnCoordinator
        _file_state_store: FileStateStore
        _pending_queues: dict[str, asyncio.Queue]
        _running: bool
        _session_locks: SessionLocks
        _turn_epochs: TurnEpochs
        _turn_tokens_by_task: dict[asyncio.Task, TurnToken]
        bus: MessageBus
        sessions: SessionManager
        subagents: SubagentManager

        # Metodi forniti da host/altri mixin.
        def _clear_pending_user_turn(self, session: Session) -> None: ...
        def _restore_runtime_checkpoint(self, session: Session) -> bool: ...
        def _runtime_events(self) -> RuntimeEventPublisher: ...

    async def _cancel_active_tasks(
        self,
        key: str,
        *,
        grace_s: float = _CANCEL_GRACE_S,
        spare_subagents: bool = False,
    ) -> int:
        """Cancella i task e i subagent attivi per *key*, senza mai bloccarsi.

        ``spare_subagents=True`` (il ``/stop`` semplice) lascia lavorare i
        subagent della sessione: si fermano dalla loro bolla o con ``/stop all``.
        Il goal sostenuto si cancella comunque, perche' e' il turno stesso che
        continua, non un aiutante.

        Bumpa l'epoch della sessione (ripudiando i turni in volo), cancella,
        attende al massimo ``grace_s`` e ABBANDONA i sopravvissuti: grazie al
        ripudio i loro effetti futuri vengono scartati, e la session lock viene
        ruotata così i turni successivi non si serializzano dietro lo zombie.

        Ritorna il totale di task cancellati/abbandonati + subagent (zero
        subagent quando li risparmia).
        """
        current = asyncio.current_task()
        tasks = [t for t in self._active_tasks.pop(key, []) if not t.done()]
        # Un comando che gira dentro il proprio turno (es. /new via FSM) non
        # deve auto-cancellarsi: il suo task resta registrato e sopravvive.
        own_task = [t for t in tasks if t is current]
        tasks = [t for t in tasks if t is not current]
        if own_task:
            self._active_tasks.setdefault(key, []).extend(own_task)

        # Il bump serve solo se ci sono turni da cancellare: i subagent zombie
        # sono coperti dalla soppressione dell'announce (cancel_by_session).
        if tasks:
            new_epoch = self._turn_epochs.bump(key)
            # Re-adozione: il turno del comando stesso sopravvive al proprio bump.
            own_token = self._turn_tokens_by_task.get(current) if current else None
            if own_token is not None and own_token.key == key:
                own_token.epoch = new_epoch
            # La pending queue registrata appartiene a un turno ripudiato:
            # rimossa e drenata subito, altrimenti i prossimi messaggi
            # verrebbero instradati nella coda dello zombie e stallerebbero.
            stale_queue = self._pending_queues.pop(key, None)
            if stale_queue is not None:
                requeued = 0
                while True:
                    try:
                        item = stale_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    await self.bus.publish_inbound(item)
                    requeued += 1
                if requeued:
                    logger.info(
                        "Re-published {} message(s) from repudiated queue for session {}",
                        requeued, key,
                    )

        cancelled = sum(1 for t in tasks if t.cancel())
        abandoned = 0
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=grace_s)
            abandoned = len(pending)
            if pending:
                logger.warning(
                    "Abandoned {} stuck task(s) for session {} after {}s grace "
                    "(repudiated via turn epoch)",
                    abandoned, key, grace_s,
                )
                self._session_locks.rotate(key)
        sub_cancelled = 0
        if not spare_subagents:
            sub_cancelled = await self.subagents.cancel_by_session(key, grace_s=grace_s)
        await self._cancel_active_goal_if_any(key)
        return cancelled + sub_cancelled

    def _restore_cancelled_turn(self, key: str) -> bool:
        """Materializza il checkpoint del turno fermato nella history.

        Chiamato in modo sincrono da /stop e /new dopo il bump: il path
        CancelledError del turno ripudiato salta il proprio restore, quindi
        questa è l'unica scrittura e non può arrivare "in ritardo".
        Ritorna True se c'era un checkpoint da ripristinare.
        """
        try:
            session = self.sessions.get_or_create(key)
            if self._restore_runtime_checkpoint(session):
                self._clear_pending_user_turn(session)
                self.sessions.save(session)
                logger.info("Restored partial context for stopped session {}", key)
                return True
        except Exception:
            logger.debug(
                "Could not restore checkpoint for stopped session {}", key,
                exc_info=True,
            )
        return False

    async def _emit_stop_turn_end(self, msg, key: str) -> None:
        """Chiude il turno fermato verso l'esterno al posto dello zombie.

        Il turno ripudiato salta turn_completed/idle/deferred-cron nel proprio
        finally (potrebbe eseguirlo con minuti di ritardo, sporcando un turno
        più nuovo); /stop li emette qui, subito e una volta sola — è ciò che
        garantisce alla WebUI il ``turn_end`` che chiude la bolla corrente.
        """
        events = self._runtime_events()
        await events.turn_completed(
            channel=msg.channel,
            chat_id=msg.chat_id,
            session_key=key,
            metadata=msg.metadata,
        )
        await events.run_status_changed(msg, key, "idle")
        events.clear_turn(key)
        await self._cron_turns.publish_next_deferred(key)

    async def _close_if_idle(self, msg, key: str) -> None:
        """Chiude il turno di un messaggio a cui si e' risposto senza farlo girare.

        Un comando prioritario (`/status`, `/stop` a riposo), un comando in linea
        e un rifiuto (cartella del progetto sparita o cambiata, `/init` e `/tidy`
        fuori scope) rispondevano con un solo ``message``: nessun ``turn_end``,
        nessun ``goal_status: idle``. Il client che aveva mandato restava in
        attesa — la mascotte a pensare per sempre, misurato sull'emulatore il
        28/09/2026. Qui si chiude, dopo la risposta.

        **Solo a sessione ferma.** Se un turno gira (c'e' la sua coda), la
        chiusura e' sua, e un ``turn_end`` in piu' qui troncherebbe la risposta
        che sta ancora arrivando.
        """
        if key in self._pending_queues:
            return
        events = self._runtime_events()
        await events.turn_completed(
            channel=msg.channel,
            chat_id=msg.chat_id,
            session_key=key,
            metadata=msg.metadata,
        )
        await events.run_status_changed(msg, key, "idle")

    def evict_pruned_sessions(self, keys: list[str]) -> None:
        """Drop cache/task/lock bookkeeping for session keys pruned from disk.

        Used after e.g. ``MemoryStore.prune_dream_sessions`` deletes old Dream
        session ``.jsonl`` files: without this, ``SessionManager._cache``,
        ``_active_tasks``, and ``_session_locks`` would keep an entry for
        every Dream session key ever created (each cron/Dream run mints a
        fresh timestamped key), growing unboundedly for the life of the
        process.

        A key is skipped (left untouched) if it still has in-flight work —
        an active task that has not finished, or a lock that is currently
        held — so a session that is genuinely still being processed is never
        ripped out from under it. This reuses the "old enough to prune"
        determination the caller already made; it does not add a second,
        independent age check.

        **Quattro registri, non tre** (T2.11). ``_file_state_store`` non stava
        qui, e ``AgentLoop`` una voce per chiave di sessione la crea **sempre**,
        a ogni turno (``bind_file_states`` in ``loop.py``): Dream
        (``dream:<timestamp>``) e il giardiniere (``gardener:<progetto>-<timestamp>``)
        coniano una chiave nuova per esecuzione, quindi era una voce morta per run per la
        vita del processo. Sono byte — 72 per un ``FileStates`` vuoto, e quelle
        voci non vengono nemmeno usate, perche' quei run portano un
        ``FileStates`` esplicito nella loro cassetta — ma illimitati per
        costruzione su un processo pensato per stare su settimane. Passando da
        qui il tetto e' quello che il chiamante ha gia' scelto (``keep=10``).

        Chi conia una chiave per esecuzione **e** non puo' aspettare la potatura
        se la dimentica da se' alla fine del run: v. ``gardener._prune_sessions``. Il cron (``cron:<job_id>``)
        non entra in questo discorso: le sue chiavi sono
        **stabili**, quindi il suo spazio e' finito da se'.
        """
        for key in keys:
            tasks = self._active_tasks.get(key)
            if tasks and any(not t.done() for t in tasks):
                continue
            if self._session_locks.is_locked(key):
                continue
            self._active_tasks.pop(key, None)
            self._session_locks.evict(key)
            self.sessions.invalidate(key)
            self._file_state_store.drop(key)

    async def _cancel_active_goal_if_any(self, key: str) -> None:
        """Reset a hard-cancelled turn's sustained goal so it stops being ``active``.

        A user-initiated ``/stop`` (or ``/new``, which also cancels) must not leave
        ``goal_state`` stuck ``active`` forever — that would permanently disable the
        LLM wall-clock timeout for this session (see
        ``jenny.session.goal_state.runner_wall_llm_timeout_s``), even for later,
        unrelated turns. A goal that is genuinely still running normally is untouched,
        since this only runs from the hard-cancel path.
        """
        session = self.sessions.get_or_create(key)
        updated = cancel_active_goal(session.metadata)
        if updated is None:
            return
        self.sessions.save(session)
        # Detto a voce: la risposta di ``/stop`` conta solo task e subagent, così
        # un goal cancellato qui non lasciava traccia da nessuna parte e non
        # c'era modo di sapere se l'obiettivo fosse ancora vivo o no.
        logger.info(
            "Sustained goal cancelled by hard-stop on session {} ({})",
            key, updated.get("ui_summary") or "no summary",
        )

    async def close_background_tasks(self, *, timeout_s: float | None = None) -> None:
        """Drain pending background archives.

        Se ``timeout_s`` è dato, i task che sforano vengono cancellati (evita che
        lo shutdown si blocchi su una consolidation che non risponde)."""
        if not self._background_tasks:
            return
        tasks = list(self._background_tasks)
        if timeout_s is None:
            await asyncio.gather(*tasks, return_exceptions=True)
        else:
            _, pending = await asyncio.wait(tasks, timeout=timeout_s)
            for t in pending:
                t.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        self._background_tasks.clear()

    def _schedule_background(self, coro) -> None:
        """Schedule a coroutine as a tracked background task (drained on shutdown)."""
        label = getattr(coro, "__qualname__", repr(coro))
        task = asyncio.create_task(coro)
        self._background_tasks.append(task)
        task.add_done_callback(partial(self._on_background_task_done, label=label))

    def _on_background_task_done(self, task: asyncio.Task, label: str) -> None:
        """Drop a finished background task and log it if it failed silently."""
        self._background_tasks.remove(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.opt(exception=exc).error("Background task '{}' failed", label)

    def stop(self) -> None:
        """Stop the agent loop (solo flag; per un teardown pulito usare shutdown())."""
        self._running = False
        logger.info("Agent loop stopping")

    async def shutdown(self, *, timeout_s: float = 10.0) -> None:
        """Drain ordinato del lavoro in volo prima del flush delle sessioni.

        Invariante garantita: quando questo ritorna, nessun writer di
        ``session.messages`` è più attivo, così il chiamante (gateway) può fare
        ``sessions.flush_all()`` senza correre con una consolidation detached.

        Ordine: stop intake → attende i turni attivi (che possono ancora
        schedulare consolidation) → drena i subagent → drena i background task.
        Un timeout hard per fase impedisce che lo shutdown si blocchi: allo
        scadere i residui vengono cancellati (un flush parziale-ma-consistente è
        preferibile a un processo appeso)."""
        self._running = False
        logger.info("Agent loop shutting down (drain, timeout={}s)", timeout_s)

        # 1. Turni attivi (dispatch per-sessione): possono appendere a _background_tasks.
        active = [
            t for tasks in self._active_tasks.values() for t in tasks if not t.done()
        ]
        if active:
            _, pending = await asyncio.wait(active, timeout=timeout_s)
            for t in pending:
                t.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        # 2. Subagent in volo.
        with suppress(Exception):
            await self.subagents.drain(timeout_s=timeout_s)

        # 3. Consolidation detached rimasta (bounded).
        await self.close_background_tasks(timeout_s=timeout_s)
