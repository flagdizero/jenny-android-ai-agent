"""I resoconti degli aiutanti di un passo tenuti in attesa, e il risveglio unico.

I risultati degli aiutanti di un passo: si tengono finché arrivano tutti, poi un turno solo.

Un pezzo di :class:`jenny.plan.driver.PlanDriver` (mixin): il testo dei metodi è
quello di prima, spostato qui per responsabilità.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from loguru import logger

from jenny.bus.events import InboundMessage
from jenny.plan.driver_common import (
    _REPORTING,
    _RUNNING,
    _HeldResults,
    _step_now,
    _UnchangedError,
    place_of,
)
from jenny.plan.meta import PLAN_ID_META, PLAN_STEP_META, PLAN_WAKE_META
from jenny.plan.model import Plan, PlanError
from jenny.plan.store import PlanNotFoundError
from jenny.session.history_meta import INJECTED_EVENT_META, PLAN_STEP_EVENT

if TYPE_CHECKING:
    from jenny.plan.driver_common import PlanHost, _Live
    from jenny.plan.store import PlanStore


#: Il testo del risveglio. Inglese, come ogni testo per il modello.
HELD_WAKE_TEXT = (
    "[Plan — helper results waiting]\n\n"
    "{held} helper result(s) of the open step are waiting in the conversation above; "
    "{running} helper(s) of this step still running, {stalled} of them without progress "
    "for a while. Read the results now and decide how to continue the step: wait for the "
    "others, continue or replace a stuck one, or close the step if it is done."
)


class HeldResultsMixin:
    """I risultati degli aiutanti di un passo: si tengono finché arrivano tutti, poi un turno solo.

    Mixin di ``PlanDriver``.
    """

    if TYPE_CHECKING:
        # Contratto host↔mixin (solo per il type-checker; nessun effetto a
        # runtime): gli attributi di ``PlanDriver.__init__`` e i metodi degli
        # altri pezzi che questi metodi usano.
        _held: dict[str, _HeldResults]
        _held_closed: bool
        _index: dict[str, _Live]
        _wakes: set[asyncio.Task[Any]]
        bus: Any
        held_wake_after_s: float
        host: PlanHost
        store: PlanStore

    def hold_helper_result(self, session_key: str, task_id: Any) -> bool:
        """Tiene il resoconto riuscito di *task_id* se un fratello del passo lavora.

        ``True`` = il loop scrive la riga senza turno. Fratello = un aiutante
        vivo della stessa sessione con lo stesso ``(plan_id, plan_step)``, in
        stato ``running`` (uno ``stalled`` non si aspetta). Fuori da un piano in
        corsa, o se il gruppo non si sa, ``False``: il turno parte come sempre.

        Il gruppo vuole **piano e passo entrambi**: un aiutante senza passo
        (lanciato da un turno dell'utente, o prima del piano) non viene tenuto e
        non conta come fratello — senza passo non c'è un «ultimo del passo» da
        aspettare, e due aiutanti estranei si terrebbero a vicenda.
        """
        live = self._index.get(session_key)
        if (
            not isinstance(task_id, str) or not task_id
            or live is None or live.status not in _REPORTING
        ):
            return False
        group = self._helper_group(task_id)
        if group is None or group[0] is None or group[1] is None:
            return False
        held = self._held.get(session_key)
        if held is not None and (held.plan_id, held.plan_step) != group:
            # Un altro gruppo: non si mescolano. Il turno parte, e vede anche
            # quelli tenuti (sono in storia).
            return False
        exclude = {task_id, *(held.task_ids if held is not None else ())}
        running, _stalled = self._step_siblings(session_key, group, exclude)
        if not running:
            return False
        if held is None:
            held = _HeldResults(group[0], group[1], [], time.monotonic())
            self._held[session_key] = held
        if task_id not in held.task_ids:
            held.task_ids.append(task_id)
        # A piano fermo su una domanda si tiene senza timer: la risposta lo
        # riarma (``_rearm_held``). Fino al 26/10 un resoconto arrivato lì non
        # si teneva e apriva un turno senza i tool del passo.
        if held.timer is None and not held.woken and live.status in _RUNNING:
            held.timer = self._arm_held_timer(session_key, held)
        logger.info(
            "Helper result {} held for session {} ({} held, {} sibling(s) running)",
            task_id, session_key, len(held.task_ids), running,
        )
        return True

    def take_held_results(self, session_key: str | None) -> list[str]:
        """Gli id tenuti per *session_key*, tolti: un turno li ha fatti vedere a Jenny.

        Lo chiama il loop alla fine di ogni turno della sessione (il turno
        dell'ultimo resoconto, il risveglio, o un turno qualunque che intanto ha
        letto la storia): il timer si spegne qui.
        """
        if not session_key:
            return []
        held = self._held.pop(session_key, None)
        if held is None:
            return []
        self._cancel_held_timer(held)
        return list(held.task_ids)

    def held_results(self, session_key: str) -> tuple[str, ...]:
        """Gli id tenuti adesso per *session_key* (sola lettura)."""
        held = self._held.get(session_key)
        return tuple(held.task_ids) if held is not None else ()

    def on_helpers_stalled(self, session_key: str, task_ids: Iterable[str]) -> None:
        """Il watchdog ha marcato fermi degli aiutanti di *session_key*.

        Se in quella sessione ci sono resoconti tenuti e nessun fratello lavora
        più (i rimasti sono fermi, o spariti senza resoconto), Jenny si sveglia:
        aspettare chi non va avanti la terrebbe ferma fino al tetto di tempo.
        """
        held = self._held.get(session_key)
        if held is None or held.woken:
            return
        running, _stalled = self._step_siblings(
            session_key, (held.plan_id, held.plan_step), set(held.task_ids)
        )
        if running:
            return
        try:
            task = asyncio.get_running_loop().create_task(self._wake_held(session_key, "stalled"))
        except RuntimeError:
            logger.warning("Held helper results of {}: no loop to wake on", session_key)
            return
        # Tenuto qui: il loop tiene i task solo per riferimento debole, e lo
        # shutdown lo cancella.
        self._wakes.add(task)
        task.add_done_callback(self._wakes.discard)

    def on_helper_journal(self, plan_id: str, step_id: str, key: str) -> None:
        """Un aiutante lavora per *step_id* di *plan_id* nel registro *key*."""

        def _apply(p: Plan) -> None:
            step = next((s for s in p.steps if s.id == step_id), None)
            if step is None or key in step.helpers:
                raise _UnchangedError
            step.helpers.append(key)

        try:
            self.store.update(plan_id, _apply)
        except (_UnchangedError, PlanNotFoundError):
            return
        except (PlanError, OSError):
            logger.opt(exception=True).warning(
                "Plan {}: helper journal {} not noted", plan_id, key)

    def _helper_group(self, task_id: str) -> tuple[str | None, str | None] | None:
        lookup: Any = getattr(self.host, "helper_plan_step", None)
        if lookup is None:
            return None
        group = lookup(task_id)
        if not isinstance(group, tuple) or len(group) != 2:
            return None
        return group[0], group[1]

    def _helpers_of_step(
        self, session_key: str, plan_id: str, *, live_step: str | None
    ) -> frozenset[str] | None:
        """Gli aiutanti vivi che contano per il passo aperto, o ``None`` se non si sa.

        Con il registro per passo (``step_helpers``) contano quelli del passo
        aperto e quelli **senza** passo (un turno dell'utente durante il piano:
        la loro scrittura toccherebbe comunque il quaderno del piano, e si segnala). Un
        aiutante di un altro passo o di un altro piano non tiene fermo questo.
        Senza registro per passo, tutti i vivi della sessione, come prima.
        """
        lookup: Any = getattr(self.host, "step_helpers", None)
        if lookup is None:
            return self.host.running_helpers(session_key)
        try:
            states = dict(lookup(session_key))
        except Exception:  # noqa: BLE001 — il registro non ha risposto: non si sa
            logger.opt(exception=True).warning("Plan {}: helper registry unreadable", plan_id)
            return None
        if not states:
            # Il registro finto dei test (e un manager senza ``live_helper_states``)
            # danno ``{}``: vale la lista dei vivi della sessione.
            return self.host.running_helpers(session_key)
        step_id = live_step
        if step_id is None:
            try:
                now = _step_now(self.store.load(plan_id))
                step_id = now.id if now is not None else None
            except PlanError:
                step_id = None
        mine: set[str] = set()
        for task_id, (state, helper_plan, helper_step) in states.items():
            if state not in ("running", "stalled"):
                continue
            if helper_plan == plan_id and helper_step == step_id:
                mine.add(task_id)
            elif helper_plan is None and helper_step is None:
                logger.warning(
                    "Plan {}: helper {} runs without a plan step, counted for step {}",
                    plan_id, task_id, step_id,
                )
                mine.add(task_id)
        return frozenset(mine)

    def _step_siblings(
        self,
        session_key: str,
        group: tuple[str | None, str | None],
        exclude: set[str],
    ) -> tuple[int, int]:
        """``(running, stalled)`` fra gli aiutanti vivi del gruppo, senza *exclude*."""
        lookup: Any = getattr(self.host, "step_helpers", None)
        if lookup is None:
            return 0, 0
        running = stalled = 0
        for task_id, (state, plan_id, plan_step) in lookup(session_key).items():
            if task_id in exclude or (plan_id, plan_step) != group:
                continue
            if state == "running":
                running += 1
            elif state == "stalled":
                stalled += 1
        return running, stalled

    def _arm_held_timer(self, session_key: str, held: _HeldResults) -> asyncio.Task[Any] | None:
        if self._held_closed:
            return None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return None
        delay = max(0.0, held.first_at + self.held_wake_after_s - time.monotonic())
        return loop.create_task(self._held_timer(session_key, delay))

    async def _held_timer(self, session_key: str, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        held = self._held.get(session_key)
        if held is not None:
            # Il timer ha finito da sé: non va cancellato (sarebbe lui stesso).
            held.timer = None
        await self._wake_held(session_key, "timeout")

    @staticmethod
    def _cancel_held_timer(held: _HeldResults) -> None:
        timer, held.timer = held.timer, None
        if timer is not None and not timer.done() and timer is not asyncio.current_task():
            timer.cancel()

    def _disarm_held(self, session_key: str) -> None:
        """Il piano non è più in corsa (pausa, fermo, finito): il timer si spegne.

        Gli id restano: le righe sono in storia, e il primo turno della sessione
        li marca ``handled`` (``take_held_results``). Se il piano riprende e un
        altro resoconto viene tenuto, il timer si riarma dal primo tenuto — un
        risveglio subito, se il tetto è già passato.
        """
        held = self._held.get(session_key)
        if held is not None:
            self._cancel_held_timer(held)

    def _rearm_held(self, session_key: str) -> None:
        """Riarma il timer spento di resoconti ancora tenuti (piano di nuovo in corsa).

        Il ritardo si conta dal primo tenuto: se il tetto è già passato, il
        risveglio parte subito. Anche dopo un risveglio che è uscito prima di
        pubblicare (passo non aperto in quel momento): lo stato resta riarmabile.
        """
        held = self._held.get(session_key)
        if held is not None and held.timer is None and not held.woken:
            held.timer = self._arm_held_timer(session_key, held)

    async def close_held_timers(self) -> None:
        """Shutdown: spegne i timer del risveglio e i risvegli in volo, e non ne arma altri.

        Lo chiama il loop nel suo drain (``AgentLoop.shutdown``): un timer vivo
        oltre lo shutdown pubblicherebbe su un bus che nessuno legge più.
        """
        self._held_closed = True
        pending: list[asyncio.Task[Any]] = []
        for held in self._held.values():
            timer = held.timer
            self._cancel_held_timer(held)
            if timer is not None and not timer.done():
                pending.append(timer)
        for task in list(self._wakes):
            if not task.done():
                task.cancel()
                pending.append(task)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def _wake_held(self, session_key: str, reason: str) -> None:
        """Pubblica il risveglio dei resoconti tenuti: un turno del passo aperto.

        Uno solo per gruppo tenuto (``woken``). Il messaggio è un turno di passo
        (``PLAN_STEP_EVENT``), così se nel frattempo il passo si è chiuso o il
        piano si è fermato il loop lo scarta da vecchio, e il gancio di fine turno
        lo giudica come un turno del passo, non come il promemoria.
        """
        held = self._held.get(session_key)
        if held is None or held.woken or self._held_closed:
            return
        live = self._index.get(session_key)
        if live is None or live.status not in _RUNNING:
            return
        try:
            plan = await asyncio.to_thread(self.store.load, live.plan_id)
        except PlanError:
            return
        step = _step_now(plan)
        if plan.status not in _RUNNING or step is None:
            return
        # Durante la lettura un turno può aver preso i tenuti, e un resoconto
        # nuovo averne aperto un altro gruppo: quello non è il gruppo per cui
        # il risveglio era partito, e ha il suo timer.
        if self._held.get(session_key) is not held or held.woken:
            return
        present: Any = getattr(self.host, "has_helper_results", None)
        if callable(present) and not present(session_key, list(held.task_ids)):
            # La conversazione è stata svuotata (``/new``): non c'è più niente
            # di tenuto da far vedere, e il turno parlerebbe di righe sparite.
            self._cancel_held_timer(self._held.pop(session_key))
            logger.info("Held helper results of {} are gone; no wake-up", session_key)
            return
        held.woken = True
        self._cancel_held_timer(held)
        running, stalled = self._step_siblings(
            session_key, (held.plan_id, held.plan_step), set(held.task_ids)
        )
        content = HELD_WAKE_TEXT.format(
            held=len(held.task_ids), running=running + stalled, stalled=stalled
        )
        await self.bus.publish_inbound(
            InboundMessage(
                channel="system",
                sender_id="plan",
                chat_id=f"websocket:{place_of(plan).chat_id}",
                content=content,
                session_key_override=plan.session_key,
                metadata={
                    INJECTED_EVENT_META: PLAN_STEP_EVENT,
                    PLAN_ID_META: plan.id,
                    PLAN_STEP_META: step.id,
                    PLAN_WAKE_META: reason,
                },
            )
        )
        logger.info(
            "Plan {}: woke on {} held helper result(s) of step {} ({})",
            plan.id, len(held.task_ids), step.id, reason,
        )
