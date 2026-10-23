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
