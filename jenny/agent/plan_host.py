"""``LoopPlanHost``: quel che il driver dei piani chiede all'``AgentLoop``.

Il driver (``jenny/plan/driver.py``) non importa il loop: gli parla attraverso
il protocollo ``PlanHost``. Questo è l'adattatore di produzione, ed è l'unico
posto in cui il piano tocca gli interni del loop — le code dei turni in volo, la
cancellazione di ``/stop``, gli aiutanti della sessione. Tenuti qui, un cambio di
quegli interni rompe un file solo, e i test del driver girano con un finto.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from jenny.bus.events import InboundMessage
from jenny.plan.driver import PLAN_NOTES_META
from jenny.session.goal_state import sustained_goal_active
from jenny.session.keys import project_session_key

if TYPE_CHECKING:
    from jenny.agent.loop import AgentLoop

# Le note di sistema che una sessione accumula prima di un turno: un tetto, per
# non far crescere i metadati se l'utente fa e disfa il ritorno dieci volte
# senza mai scrivere.
_MAX_NOTES = 5


class LoopPlanHost:
    """Adattatore ``PlanHost`` su un ``AgentLoop`` vivo."""

    def __init__(self, loop: AgentLoop) -> None:
        self._loop = loop

    def turn_in_flight(self, session_key: str) -> bool:
        """Un turno della sessione è in volo, compreso uno abbandonato da ``/stop``.

        ``_cancel_active_tasks`` toglie da ``_active_tasks`` anche i turni che
        non muoiono entro la grazia (li abbandona, ripudiati): per il loop non
        esistono più, ma possono ancora scrivere — uno ``python_exec`` dentro un
        thread non si ferma col ``cancel``. Un ritorno indietro sotto un turno
        così riscriverebbe il quaderno mentre lui ci scrive, quindi qui contano
        anche loro: ``_turn_tokens_by_task`` tiene ogni turno fino all'uscita
        vera dal suo ``_dispatch``.
        """
        loop = self._loop
        if session_key in loop._pending_queues:
            return True
        if any(not task.done() for task in loop._active_tasks.get(session_key, ())):
            return True
        current = asyncio.current_task()
        return any(
            token.key == session_key and task is not current and not task.done()
            for task, token in list(loop._turn_tokens_by_task.items())
        )

    def running_helpers(self, session_key: str) -> frozenset[str]:
        try:
            return frozenset(self._loop.subagents.get_running_ids_by_session(session_key))
        except Exception:  # noqa: BLE001 — un manager finto nei test non deve rompere il piano
            return frozenset()

    def helper_plan_step(self, task_id: str) -> tuple[str | None, str | None] | None:
        """Piano e passo di un aiutante (anche appena finito), per il risveglio unico."""
        lookup: Any = getattr(self._loop.subagents, "helper_plan_step", None)
        if lookup is None:
            return None
        try:
            return lookup(task_id)
        except Exception:  # noqa: BLE001 — un manager finto nei test non deve rompere il piano
            return None

    def step_helpers(self, session_key: str) -> dict[str, tuple[str, str | None, str | None]]:
        """``task_id -> (state, plan_id, plan_step)`` degli aiutanti vivi della sessione."""
        lookup: Any = getattr(self._loop.subagents, "live_helper_states", None)
        if lookup is None:
            return {}
        try:
            return dict(lookup(session_key))
        except Exception:  # noqa: BLE001 — come sopra
            return {}

    def has_helper_results(self, session_key: str, task_ids: Any) -> bool:
        """Almeno uno dei resoconti *task_ids* è ancora nella storia della sessione.

        Il risveglio di sicurezza lo chiede prima di partire: un ``/new`` nel
        frattempo ha svuotato la conversazione, e un turno che annuncia
        «resoconti in attesa qui sopra» parlerebbe di righe che non ci sono più.
        """
        from jenny.session.history_meta import INJECTED_EVENT_META, SUBAGENT_RESULT_EVENT

        wanted = set(task_ids or ())
        if not wanted:
            return False
        session = self._loop.sessions.get_or_create(session_key)
        return any(
            row.get(INJECTED_EVENT_META) == SUBAGENT_RESULT_EVENT
            and row.get("subagent_task_id") in wanted
            for row in session.messages
        )

    def watch_stalls(self, fn: Any) -> None:
        """Lega *fn* alle marcature ``stalled`` del watchdog degli aiutanti.

        Lo chiama il driver quando nasce: è il solo modo in cui un aiutante
        fermo raggiunge il driver, che non conosce il manager.
        """
        register = getattr(self._loop.subagents, "set_stall_listener", None)
        if callable(register):
            register(fn)

    async def cancel_turn(self, session_key: str) -> bool:
        """Solo il turno in volo della sessione (``/stop`` semplice), non gli aiutanti.

        Per l'annullamento di una pianificazione: il turno che stava pianificando
        si ferma, gli aiutanti della chat che non sono suoi restano al lavoro.
        Come in :meth:`cancel`, il ``turn_end`` lo emette chi ferma.
        """
        loop = self._loop
        if not self.turn_in_flight(session_key):
            return False
        await loop._cancel_active_tasks(session_key, spare_subagents=True)
        loop._restore_cancelled_turn(session_key)
        origin = InboundMessage(
            channel="websocket", sender_id="plan", chat_id=session_key, content="",
        )
        try:
            await loop._emit_stop_turn_end(origin, session_key)
        except Exception:  # noqa: BLE001 — il turno è fermo comunque
            logger.opt(exception=True).warning("Plan drafting cancel: turn end not emitted")
        return True

    async def cancel_helpers(self, task_ids: list[str]) -> int:
        """Ferma questi aiutanti, uno per uno (``SubagentManager.cancel_task``)."""
        stopped = 0
        for task_id in task_ids:
            try:
                if await self._loop.subagents.cancel_task(task_id):
                    stopped += 1
            except Exception:  # noqa: BLE001 — uno che non si ferma non blocca gli altri
                logger.opt(exception=True).warning("Helper {} not cancelled", task_id)
        return stopped

    async def cancel(self, session_key: str) -> int:
        """Il percorso di ``/stop all``: il turno in volo e gli aiutanti della sessione.

        Come ``/stop``, il turno ripudiato non chiude se stesso: il contesto
        parziale si materializza qui e il ``turn_end`` lo emette chi ferma, così
        la bolla della chat si chiude anche se il Ferma arriva dalla scheda.
        """
        loop = self._loop
        # Il ``turn_end`` solo se c'era un turno da chiudere: da ``/stop`` il
        # turno l'ha gia' fermato (e chiuso) il comando, e qui restano gli aiutanti.
        had_turn = self.turn_in_flight(session_key)
        total = await loop._cancel_active_tasks(session_key, spare_subagents=False)
        if had_turn:
            loop._restore_cancelled_turn(session_key)
            origin = InboundMessage(
                channel="websocket", sender_id="plan", chat_id=session_key, content="",
            )
            try:
                await loop._emit_stop_turn_end(origin, session_key)
            except Exception:  # noqa: BLE001 — il piano si ferma comunque
                logger.opt(exception=True).warning("Plan stop: turn end not emitted")
        return total

    def goal_active(self, session_key: str) -> bool:
        return sustained_goal_active(self._loop.sessions.get_or_create(session_key).metadata)

    def notebook_root(self, notebook: str) -> Path | None:
        try:
            root = self._loop.workspace_scopes.for_project(
                project_session_key(notebook)
            ).project_path
        except Exception:  # noqa: BLE001 — uno scope irrisolvibile è un quaderno che non c'è
            return None
        return root if root is not None and root.is_dir() else None

    def notebook_drafts(self, notebook: str) -> list[dict[str, Any]]:
        """Le bozze in attesa che cambiano un file del quaderno *notebook*.

        Le bozze stanno sotto la radice dell'installazione (la politica dei
        subagent si misura da lì, anche per un aiutante di quaderno), con il
        percorso relativo a quella radice: un file è del quaderno se il suo
        percorso risolto sta nella cartella del quaderno.
        """
        from jenny.agent.staged_writes import list_staged

        root = self.notebook_root(notebook)
        if root is None:
            return []
        workspace = Path(self._loop.workspace)
        base = root.resolve(strict=False)
        drafts: list[dict[str, Any]] = []
        for meta in list_staged(workspace):
            target = str(meta.get("target") or "")
            try:
                inside = (workspace / target).resolve(strict=False).is_relative_to(base)
            except (OSError, ValueError):
                inside = False
            if inside:
                drafts.append(
                    {"id": meta["id"], "target": target, "created_ms": meta.get("created_ms")}
                )
        return drafts

    def discard_draft(self, draft_id: str) -> bool:
        from jenny.agent.staged_writes import discard

        return discard(Path(self._loop.workspace), draft_id)

    def append_note(self, session_key: str, text: str) -> None:
        sessions = self._loop.sessions
        session = sessions.get_or_create(session_key)
        notes: Any = session.metadata.get(PLAN_NOTES_META)
        notes = [n for n in notes if isinstance(n, str)] if isinstance(notes, list) else []
        notes.append(text)
        session.metadata[PLAN_NOTES_META] = notes[-_MAX_NOTES:]
        sessions.save(session)
