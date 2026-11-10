"""Il rientro di un aiutante: il suo risultato annunciato alla chat che l'ha lanciato.

Il risultato di un aiutante, com'è detto a chi l'ha lanciato.

Un pezzo di :class:`jenny.agent.subagent.SubagentManager` (mixin): il testo dei
metodi è quello di prima, spostato qui per responsabilità.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger

from jenny.agent.subagent_common import _NO_FINAL_RESPONSE, MAX_BUDGET_RESUMES, _task_head
from jenny.bus.events import INTERNAL_CHANNEL, InboundMessage
from jenny.session.turn_visibility import resolve_turn_visibility
from jenny.utils.prompt_templates import render_template

if TYPE_CHECKING:
    from jenny.agent.subagent_common import SubagentStatus
    from jenny.agent.subagent_records import SubagentSpec
    from jenny.bus.queue import MessageBus
    from jenny.security.subagent_policy import SubagentRunPolicy


class AnnounceMixin:
    """Il risultato di un aiutante, com'è detto a chi l'ha lanciato.

    Mixin di ``SubagentManager``.
    """

    if TYPE_CHECKING:
        # Contratto host↔mixin (solo per il type-checker; nessun effetto a
        # runtime): gli attributi di ``SubagentManager`` e i metodi degli
        # altri pezzi che questi metodi usano.
        _repudiated_task_ids: set[str]
        _run_policies: dict[str, SubagentRunPolicy]
        _task_statuses: dict[str, SubagentStatus]
        bus: MessageBus

        def _spec_for_task(self, task_id: str) -> SubagentSpec | None: ...

    async def _announce_result(
        self,
        task_id: str,
        label: str,
        task: str,
        result: str,
        origin: dict[str, str],
        status: str,
        origin_message_id: str | None = None,
        *,
        lineage_id: str | None = None,
        attempt: int | None = None,
        force: bool = False,
        resumable: bool = False,
        budget_stops: int = 1,
        interrupted: bool = False,
    ) -> None:
        """Announce the subagent result to the main agent via the message bus.

        ``force`` scavalca il ripudio: serve all'annuncio *esplicito* di
        cancellazione, che deve arrivare proprio perché quello naturale è stato
        soppresso. Vedi :meth:`cancel_task`.
        """
        if not force and task_id in self._repudiated_task_ids:
            # Subagent abbandonato da /stop, o superato da un rilancio, che è
            # riuscito a finire più tardi: il risultato è stale, non deve
            # iniettare un turno nella sessione.
            self._repudiated_task_ids.discard(task_id)
            logger.info("Suppressed stale announce from repudiated subagent [{}]", task_id)
            return
        status_text = {
            "ok": "completed successfully",
            "budget": "stopped at its iteration budget before finishing",
            "cancelled": "was stopped by the user",
        }.get(status, "failed")

        # Inject as system message to trigger main agent.
        # Use session_key_override to align with the main agent's effective
        # session key (which accounts for unified sessions) so the result is
        # routed to the correct pending queue (mid-turn injection) instead of
        # being dispatched as a competing independent task.
        #
        # Questo override porta anche la VISIBILITA del turno d'annuncio: la
        # session key d'origine e' cio da cui
        # ``jenny.session.turn_visibility.resolve_turn_visibility`` deduce se il
        # turno appartiene a lavoro interno. Un subagent lanciato dentro
        # un cron monitor (``cron:<job_id>``) termina molto dopo la fine del turno che lo ha
        # lanciato, e prima il suo annuncio apriva un turno nuovo che consegnava
        # in chat senza passare da alcun gate. Cambiare questo override in un
        # ``f"{channel}:{chat_id}"` "piu semplice" rimetterebbe quel messaggio in
        # chat: la provenienza si perderebbe.
        override = origin.get("session_key") or f"{origin['channel']}:{origin['chat_id']}"
        # Il prompt d'annuncio dipende dalla visibilita del turno che sta per
        # aprirsi, e va deciso QUI perche' e' qui che si conosce l'origine. Su un
        # turno silenzioso il vecchio testo unico ("Summarize this naturally for
        # the user") e' un'istruzione a parlare rivolta a un turno che non puo'
        # consegnare: produceva testo che non arrivava a nessuno mentre il dato
        # vero — quello che il subagent e' andato a prendere — restava inedito.
        silent = resolve_turn_visibility(
            None, channel=origin.get("channel") or INTERNAL_CHANNEL, session_key=override
        ).silent
        # Un lavoro che ha letto contenuto non fidato consegna un risultato che
        # può portarne dentro le istruzioni: arriva incorniciato come dato, con
        # le fonti, e i metadati lo dicono a chi lo salva nella sessione.
        policy = self._run_policies.get(task_id)
        taint = policy.taint_sources() if policy is not None else ()
        staged_ids = list(policy.staged.values()) if policy is not None else []
        live = self._task_statuses.get(task_id)
        resolved_lineage = (
            lineage_id
            if lineage_id is not None
            else (live.lineage_id if live is not None else task_id)
        )
        # Un aiutante fermato al budget si fa continuare, non si rilancia:
        # ``resumable`` lo misura il ramo budget (storia salvata, abbastanza
        # corta, tetto delle riprese non raggiunto). Se non lo e, ``send`` lo
        # rifiuterebbe, e il template allora non lo promette: dice di dividere.
        # Lo stesso per uno fermato da un errore passeggero del provider.
        interrupted = interrupted and resumable
        resumable = (status == "budget" or interrupted) and resumable
        # Il passo del piano per cui lavorava, e la sua lista **To research**
        # letta come la legge il driver (``plan.model.to_research_items``): il
        # template la cercava come sottostringa, e su una lista in linea che il
        # driver contava zero mandava Jenny a cercare lo stesso (Titan, 26/10).
        spec_for_plan = self._spec_for_task(task_id)
        from jenny.plan.model import to_research_items

        research_items = to_research_items(result) if isinstance(result, str) else []
        announce_content = render_template(
            "agent/subagent_announce.md",
            label=label,
            status_text=status_text,
            task=_task_head(task),
            result=result,
            silent=silent,
            untrusted=bool(taint),
            sources=", ".join(taint),
            staged_count=len(staged_ids),
            budget=status == "budget",
            resumable=resumable,
            interrupted=interrupted,
            budget_capped=status == "budget" and budget_stops > MAX_BUDGET_RESUMES,
            budget_stops=budget_stops,
            task_id=task_id,
            research_items=len(research_items),
            in_plan=bool(spec_for_plan is not None and spec_for_plan.plan_step),
        )
        metadata: dict[str, Any] = {
            "injected_event": "subagent_result",
            # Le voci **To research** del resoconto, lette qui una volta sola: il
            # driver le prende da qui invece di rileggere l'annuncio intero, in
            # cui il testo del template nomina la lista e si faceva contare
            # (prova su s22, 26/10: una riga del template in open-gaps.md).
            "subagent_to_research": research_items,
            "subagent_status": status,
            "subagent_task_id": task_id,
            "subagent_lineage_id": resolved_lineage,
            "subagent_attempt": (
                attempt
                if attempt is not None
                else (live.attempt if live is not None else 1)
            ),
        }
        if status == "budget":
            metadata["subagent_resumable"] = resumable
        if interrupted:
            # Lo legge il loop: come al budget, un ``subagent_send`` riuscito a
            # questo aiutante zittisce il turno d'annuncio (l'annuncio lo promette).
            metadata["subagent_interrupted"] = True
        # Il passo del piano per cui lavorava: il turno d'annuncio lega questi
        # metadati come contesto della richiesta, e un aiutante che Jenny lancia
        # da li eredita il passo (``_plan_metadata``). Sicuro: un turno di passo si
        # riconosce da ``injected_event``, non da queste chiavi.
        if spec_for_plan is not None and (spec_for_plan.plan_id or spec_for_plan.plan_step):
            from jenny.plan.meta import PLAN_ID_META, PLAN_STEP_META

            if spec_for_plan.plan_id:
                metadata[PLAN_ID_META] = spec_for_plan.plan_id
            if spec_for_plan.plan_step:
                metadata[PLAN_STEP_META] = spec_for_plan.plan_step
            # Il tipo serve al cancello dei buchi: il resoconto di un
            # ``researcher`` nel passo vuol dire che un giro di ricerca c'è stato.
            if spec_for_plan.agent_type:
                metadata["subagent_agent_type"] = spec_for_plan.agent_type
        if origin_message_id:
            metadata["origin_message_id"] = origin_message_id
        if taint:
            metadata["subagent_tainted"] = True
            metadata["subagent_taint_sources"] = list(taint)
        if staged_ids:
            metadata["subagent_staged_ids"] = staged_ids
        msg = InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id=f"{origin['channel']}:{origin['chat_id']}",
            content=announce_content,
            session_key_override=override,
            metadata=metadata,
        )

        await self.bus.publish_inbound(msg)
        logger.debug("Subagent [{}] announced result to {}:{}", task_id, origin['channel'], origin['chat_id'])

    @classmethod
    def _budget_report(cls, result) -> str:
        """Il resoconto di un subagent fermato al budget: riassunto + parziale.

        Il riassunto e la risposta della chiamata di chiusura
        (``SUBAGENT_BUDGET_FINALIZATION_PROMPT``); se quella non ha prodotto
        niente il runner mette il ripiego, che qui si riconosce e si scarta —
        annunciarlo come resoconto direbbe «completato» di un lavoro a meta. I
        passi fatti restano sotto in ogni caso: sono misurati, il riassunto no.
        """
        partial = cls._format_partial_progress(result)
        summary = (result.final_content or "").strip()
        if not summary or summary == _NO_FINAL_RESPONSE:
            return partial
        return f"{summary}\n\nSteps before the stop:\n{partial}"

    @staticmethod
    def _format_partial_progress(result) -> str:
        completed = [e for e in result.tool_events if e["status"] == "ok"]
        failure = next((e for e in reversed(result.tool_events) if e["status"] == "error"), None)
        lines: list[str] = []
        if completed:
            lines.append("Completed steps:")
            for event in completed[-3:]:
                lines.append(f"- {event['name']}: {event['detail']}")
        if failure:
            if lines:
                lines.append("")
            lines.append("Failure:")
            lines.append(f"- {failure['name']}: {failure['detail']}")
        if result.error and not failure:
            if lines:
                lines.append("")
            lines.append("Failure:")
            lines.append(f"- {result.error}")
        return "\n".join(lines) or (result.error or "Error: subagent execution failed.")
