"""Le bozze lasciate da un passo: il piano le aspetta, e riparte quando sono decise.

Un passo che lascia bozze non si chiude: si ferma su di loro e va avanti da sé dopo.

Un pezzo di :class:`jenny.plan.driver.PlanDriver` (mixin): il testo dei metodi è
quello di prima, spostato qui per responsabilità.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, Any

from loguru import logger

from jenny.plan.driver_common import (
    _APPROVED_MS,
    _DRAFT_OUTCOMES,
    _MAX_NOTE,
    _NUDGE,
    _PENDING_NOTE,
    _PENDING_OUTCOME,
    _PENDING_QUESTION,
    _PENDING_REASON,
    _RESUME_TO,
    _block,
    _cut,
    _DraftsUnreadableError,
    _find,
    _UnchangedError,
)
from jenny.plan.model import Plan, PlanError, apply_transition, now_iso

if TYPE_CHECKING:
    from collections.abc import Callable

    from jenny.plan.driver_common import PlanHost, _Live
    from jenny.plan.store import PlanStore


def _block_on_drafts(plan: Plan, step_id: str, drafts: list[dict[str, str]]) -> None:
    """Il passo aspetta le sue bozze. La domanda è un ripiego in inglese: la
    scheda ha le sue frasi, e mostra le bozze con Installa e Scarta."""
    targets = ", ".join(d["target"] for d in drafts[:3])
    if len(drafts) > 3:
        targets += f" and {len(drafts) - 3} more"
    question = (
        f"Jenny wants to change {targets}: look at the change and install it or discard it."
    )
    _block(plan, step_id, question, "drafts", kind="drafts", drafts=drafts)


def _approved_ms(plan: Plan) -> int | None:
    """L'istante dell'Approva, in millisecondi: annotato all'Approva, o dalla storia."""
    stamp = plan.extra.get(_APPROVED_MS)
    if isinstance(stamp, int) and not isinstance(stamp, bool):
        return stamp
    for entry in reversed(plan.history):
        if entry.get("event") == "approved":
            try:
                return int(datetime.fromisoformat(str(entry.get("at"))).timestamp() * 1000)
            except (TypeError, ValueError):
                return None
    return None


class StepDraftsMixin:
    """Un passo che lascia bozze non si chiude: si ferma su di loro e va avanti da sé dopo.

    Mixin di ``PlanDriver``.
    """

    if TYPE_CHECKING:
        # Contratto host↔mixin (solo per il type-checker; nessun effetto a
        # runtime): gli attributi di ``PlanDriver.__init__`` e i metodi degli
        # altri pezzi che questi metodi usano.
        _index: dict[str, _Live]
        _loop: asyncio.AbstractEventLoop | None
        _unwatch: Callable[[], None] | None
        host: PlanHost
        store: PlanStore

        async def _advance(self, plan: Plan) -> None: ...
        def _bind_loop(self) -> None: ...
        async def _changed(self, plan: Plan) -> None: ...
        def _lock(self, session_key: str) -> asyncio.Lock: ...
        def _reload(self, plan: Plan) -> Plan: ...
        def _remember(self, plan: Plan) -> None: ...
        async def _settle_closed(self, plan: Plan) -> Plan: ...
        def _spawn(self, coro: Any) -> None: ...

    def _plan_drafts(self, plan: Plan) -> list[dict[str, str]]:
        """Le bozze in attesa che appartengono a *plan* (cioè al suo passo aperto).

        **Una bozza è del passo se cambia un file del quaderno ed è nata dopo
        l'Approva.** Non dal ``task_id`` degli aiutanti lanciati nel passo:
        andrebbe registrato a ogni ``spawn`` e si perderebbe col riavvio che
        rimette il passo da capo, mentre la regola sul quaderno regge da sola.
        Il quaderno è tenuto dal piano dall'Approva in poi — l'utente non ci
        scrive, il giardiniere lo salta, un cron ci gira in sola lettura — quindi
        una bozza nuova sul quaderno può venire solo dal piano. E un passo non si
        chiude con le sue bozze in attesa, quindi quelle che restano sono del
        passo aperto (o di una sua corsa di prima, interrotta da un riavvio:
        sono lo stesso lavoro). Una bozza di prima del piano non conta: non è
        lavoro del piano, e terrebbe fermo ogni passo.
        """
        since_ms = _approved_ms(plan)
        drafts: list[dict[str, str]] = []
        try:
            pending = self.host.notebook_drafts(plan.notebook)
        except Exception as exc:  # noqa: BLE001 — chi chiude un passo deve saperlo
            # Fino al 26/10 tornava ``[]``: il passo si chiudeva con le bozze in
            # attesa, cioè il difetto del 23/10 che questo metodo esiste per
            # evitare. Ora chi decide una chiusura rifiuta e riprova.
            logger.opt(exception=True).warning("Plan {}: drafts not readable", plan.id)
            raise _DraftsUnreadableError(str(exc)) from exc
        for draft in pending:
            created = draft.get("created_ms")
            if isinstance(created, int) and since_ms is not None and created < since_ms:
                continue
            drafts.append({"id": str(draft.get("id")), "target": str(draft.get("target"))})
        return drafts

    def _drafts_or_empty(self, plan: Plan) -> list[dict[str, str]]:
        """Le bozze del passo, o ``[]`` se non si leggono: per chi non decide una chiusura."""
        try:
            return self._plan_drafts(plan)
        except _DraftsUnreadableError:
            return []

    def watch_drafts(self) -> None:
        """Ascolta le bozze (``staged_writes``): quelle di un passo fermo si decidono.

        Lo chiama il composition root una volta. L'avviso arriva anche da un
        thread (le RPC ``staged.*`` fanno il disco in ``to_thread``): non si
        decide niente lì — nemmeno se c'è un piano che aspetta, perché l'indice
        è del loop — si rientra nel loop del gateway e si decide da lui, come fa
        il frame ``staged_changed``.
        """
        from jenny.agent.staged_writes import StagedChange, add_change_listener

        self._bind_loop()

        def heard(change: StagedChange) -> None:
            if change.action == "staged":
                return
            try:
                running: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            target = self._loop
            if running is not None and (target is None or running is target):
                self._spawn(self._drafts_changed(change))
            elif target is not None:
                target.call_soon_threadsafe(lambda: self._spawn(self._drafts_changed(change)))
            else:
                logger.warning("Draft change {} heard with no loop to settle it", change.id)

        if self._unwatch is not None:
            self._unwatch()
        self._unwatch = add_change_listener(heard)

    def _blocked_kind(self, plan_id: str) -> str | None:
        try:
            plan = self.store.load(plan_id)
        except PlanError:
            return None
        return (plan.blocked or {}).get("kind") if plan.status == "blocked" else None

    async def _drafts_changed(self, change: Any) -> None:
        """Una bozza installata o scartata: i passi che l'aspettavano ripartono?"""
        for session_key, live in list(self._index.items()):
            if live.status != "blocked":
                continue
            try:
                await self._settle_drafts(session_key, live.plan_id, change)
            except Exception:  # noqa: BLE001 — un piano rotto non ferma gli altri
                logger.opt(exception=True).error(
                    "Plan {}: could not settle its drafts", live.plan_id
                )

    async def recheck_drafts(self, plan_id: str) -> None:
        """La rete di sicurezza: un piano fermo sulle bozze le riguarda adesso.

        Un avviso perso (un riavvio del processo fra l'installazione e
        l'avviso, una bozza tolta a mano) lascerebbe il passo fermo per sempre
        su bozze che non ci sono più. La rilettura costa una lista delle bozze,
        e si fa solo per un piano fermo su di loro: alla lettura della scheda
        (``plan.get``) e prima di un turno dell'utente.
        """
        live = next((v for v in self._index.values() if v.plan_id == plan_id), None)
        if live is None or live.status != "blocked":
            return
        session_key = next(k for k, v in self._index.items() if v is live)
        await self._settle_drafts(session_key, plan_id, None)

    async def _settle_drafts(
        self, session_key: str, plan_id: str, change: Any, *, advance: bool | None = None
    ) -> None:
        async with self._lock(session_key):
            plan = await self._settle_drafts_locked(session_key, plan_id, change, advance=advance)
        if plan is not None:
            await self._changed(plan)

    async def _settle_drafts_locked(
        self,
        session_key: str,
        plan_id: str,
        change: Any,
        *,
        advance: bool | None = None,
    ) -> Plan | None:
        """Se le bozze del passo fermo sono tutte decise, il passo va avanti.

        Si chiude con la nota del modello più l'esito scritto dal codice
        («modifica installata»): il passo ha fatto quel che l'utente ha deciso,
        non quel che il modello aveva chiesto. Se dietro le bozze c'era una
        domanda (``plan_blocked``, o il blocco per un passo lasciato a metà),
        adesso tocca a lei. Ritorna il piano se è cambiato qualcosa da far
        vedere, ``None`` altrimenti. Va chiamato col lock della sessione.

        *advance* dice se fare i conti del passo e aprire il seguente qui: di
        default sì, se nessun turno è in volo in quella sessione. Se ce n'è uno,
        lo fa il suo gancio di fine turno, come per ogni passo chiuso — mai due
        aperture dello stesso passo.
        """
        from jenny.runtime.user_text import say

        try:
            plan = await asyncio.to_thread(self.store.load, plan_id)
        except PlanError:
            return None
        blocked = plan.blocked or {}
        if plan.status != "blocked" or blocked.get("kind") != "drafts":
            return None
        step_id = str(blocked.get("step") or "")
        wanted = {str(d.get("id")) for d in blocked.get("drafts") or ()}
        pending = {
            str(d.get("id"))
            for d in await asyncio.to_thread(self.host.notebook_drafts, plan.notebook)
        }
        heard_id = str(getattr(change, "id", "") or "")
        heard_action = str(getattr(change, "action", "") or "")
        left = wanted & pending
        if left and heard_id not in wanted:
            return None
        outcome_box: dict[str, str] = {}

        def _apply(p: Plan) -> None:
            if p.status != "blocked" or (p.blocked or {}).get("kind") != "drafts":
                raise _UnchangedError
            s = _find(p, step_id)
            outcomes = dict(s.extra.get(_DRAFT_OUTCOMES) or {})
            if heard_id in wanted and heard_action in ("installed", "discarded"):
                outcomes[heard_id] = heard_action
            s.extra[_DRAFT_OUTCOMES] = outcomes
            if left:
                return
            stamp = now_iso()
            question = s.extra.pop(_PENDING_QUESTION, None)
            if isinstance(question, str) and question:
                # Dietro le bozze aspettava una domanda: adesso è lei il blocco.
                reason = str(s.extra.pop(_PENDING_REASON, "question") or "question")
                p.blocked = {
                    "step": step_id, "question": question, "reason": reason, "at": stamp,
                    "kind": "question", "drafts": [],
                }
                p.history.append(
                    {"at": stamp, "event": "drafts_decided", "from": "blocked", "step": step_id}
                )
                p.updated_at = stamp
                outcome_box["result"] = "question"
                return
            kinds = {outcomes.get(i) for i in wanted}
            suffix = say(
                "plan.drafts_installed" if kinds == {"installed"}
                else "plan.drafts_discarded" if kinds == {"discarded"}
                else "plan.drafts_resolved"
            )
            note = str(s.extra.pop(_PENDING_NOTE, "") or "")
            outcome = s.extra.pop(_PENDING_OUTCOME, "done")
            resume_to = s.extra.pop(_RESUME_TO, None)
            s.status = outcome if outcome in ("done", "skipped") else "done"
            s.ended_at = stamp
            s.note = _cut(f"{note} — {suffix}" if note else suffix, _MAX_NOTE, "step note")
            s.extra.pop(_NUDGE, None)
            apply_transition(p, "running", at=stamp, event="drafts_decided", step=step_id)
            if resume_to == "pausing":
                # La Pausa chiesta prima (o durante) il blocco non si perde: il
                # passo è finito, e ``_advance`` porta il piano in pausa.
                apply_transition(p, "pausing", at=stamp)
            outcome_box["result"] = "closed"

        try:
            plan = await asyncio.to_thread(self.store.update, plan_id, _apply)
        except _UnchangedError:
            return None
        self._remember(plan)
        if left:
            return None
        logger.info("Plan {}: the drafts of step {} are decided", plan_id, step_id)
        if outcome_box.get("result") != "closed":
            return plan
        if advance is None:
            advance = not self.host.turn_in_flight(session_key)
        if advance:
            plan = await self._settle_closed(plan)
            await self._advance(plan)
            plan = self._reload(plan)
        return plan

    async def _hold_on_drafts(
        self,
        plan: Plan,
        step_id: str,
        drafts: list[dict[str, str]],
        *,
        outcome: str | None = None,
        note: str = "",
        question: str | None = None,
        reason: str = "question",
    ) -> Plan:
        """Ferma il passo sulle sue bozze (col lock della sessione già preso).

        *outcome*/*note*: come il modello voleva chiudere il passo, per
        chiuderlo così quando l'utente ha deciso. *question*: una domanda che
        aspetta dietro le bozze (``plan_blocked``, o il passo lasciato a metà);
        allora il passo non si chiude, e dopo le bozze tocca alla domanda.
        """
        # Un blocco sulle bozze nasce sempre sul loop del gateway: è il momento
        # sicuro per ricordarlo, se ``watch_drafts`` è stato chiamato fuori.
        self._bind_loop()

        def _apply(p: Plan) -> None:
            s = _find(p, step_id)
            for key in (_PENDING_OUTCOME, _PENDING_NOTE, _PENDING_QUESTION, _PENDING_REASON,
                        _DRAFT_OUTCOMES):
                s.extra.pop(key, None)
            if question:
                s.extra[_PENDING_QUESTION] = question
                s.extra[_PENDING_REASON] = reason
            else:
                s.extra[_PENDING_OUTCOME] = outcome or "done"
                s.extra[_PENDING_NOTE] = note
            s.note = None
            s.ended_at = None
            _block_on_drafts(p, step_id, drafts)

        plan = await asyncio.to_thread(self.store.update, plan.id, _apply)
        self._remember(plan)
        logger.info("Plan {}: step {} waits for {} draft(s)", plan.id, step_id, len(drafts))
        return plan
