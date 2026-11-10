"""Il piano a lista dentro il loop (mixin di ``AgentLoop``).

Il driver (``jenny/plan/driver.py``) muove il piano; il loop gli presta quattro
punti del turno, raccolti qui perché ``loop.py`` resti il cuore FSM/scheduling:

- **quali tool si vedono**: ``plan_propose`` nel turno del tasto Piano, i tre
  del passo solo con un passo ``now``;
- **cosa legge il modello**: la lista del piano nel contesto di ogni turno del
  quaderno (e una riga per piano nella chat personale), il blocco di
  pianificazione, le note di un ritorno indietro;
- **prima di un turno dell'utente**: in pausa il turno è in sola lettura, da
  bloccato il messaggio è la risposta, durante una pianificazione
  (``drafting``) il turno è di pianificazione anche senza il tasto;
- **cosa conta come «ha guardato»**: le letture del quaderno di un turno di
  pianificazione (:class:`PlanReadHook`), senza le quali ``plan_propose``
  rifiuta;
- **a fine turno**: il driver decide se il passo è chiuso, da sollecitare o
  bloccato.

**I turni di cron non sono dell'utente né del piano.** Un'automazione del
quaderno gira nella sua conversazione (o in una sessione sua con lo stesso
recinto, ``notebook_cron_session_key``): il suo prompt non è una risposta a un
piano bloccato, la sua fine non giudica il passo, e i tool del passo non li
vede. Finché il piano tiene il quaderno gira in sola lettura: per la stessa
ragione per cui il giardiniere salta i quaderni tenuti (``pick_project``),
quel che scrivesse sopra un file del piano lo terrebbe fuori da «Torna a
prima».

Senza driver (i test che non lo montano, l'agente prima del composition root)
tutto questo tace: i tool del piano restano nascosti e nessun hook gira.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

from loguru import logger

from jenny.agent.hook import AgentHook, ToolResultHookContext
from jenny.agent.tools.result import is_error_text
from jenny.cron.session_turns import is_cron_turn
from jenny.plan.driver import PlanDriver, notebook_of
from jenny.plan.meta import (
    PLAN_ANSWER_META,
    PLAN_FOLLOWUP_META,
    PLAN_ID_META,
    PLAN_MODE_META,
    PLAN_NOTES_META,
    PLAN_PROPOSE_TOOL,
    PLAN_REVISE_META,
    PLAN_STEP_META,
    PLAN_STEP_TOOLS,
    PLAN_TOOL_NAMES,
)
from jenny.security.subagent_policy import UNTRUSTED_TOOLS
from jenny.security.workspace_access import WORKSPACE_READONLY_METADATA_KEY
from jenny.session.history_meta import (
    INJECTED_EVENT_META,
    PLAN_TURN_EVENTS,
    SUBAGENT_RESULT_EVENT,
)
from jenny.session.keys import is_project_session_key, notebook_of_cron_session

if TYPE_CHECKING:
    from jenny.agent.tools.registry import ToolRegistry
    from jenny.bus.events import InboundMessage
    from jenny.session.manager import Session


#: Messo sul messaggio di un passo scartato perché vecchio (un Ferma, una Pausa
#: arrivati mentre era sul bus): quel turno non è girato, e il gancio di fine
#: turno non lo deve contare come un passo lasciato aperto.
PLAN_STALE_META = "_plan_stale"


def is_plan_turn(metadata: Mapping[str, Any] | None) -> bool:
    """Il turno l'ha aperto il driver: un passo o il suo promemoria."""
    return isinstance(metadata, Mapping) and metadata.get(INJECTED_EVENT_META) in PLAN_TURN_EVENTS


def _is_step_work(metadata: Mapping[str, Any] | None) -> bool:
    """Il turno è lavoro di un passo: il passo, il promemoria, o il resoconto di
    un aiutante del passo (che porta il piano nei metadati)."""
    return is_plan_turn(metadata) or (
        isinstance(metadata, Mapping) and bool(metadata.get(PLAN_ID_META))
    )


def _notebook_of_turn(session_key: str) -> str | None:
    """Il quaderno del recinto di un turno: la sua chat, o una sessione di un suo cron."""
    return notebook_of(session_key) or notebook_of_cron_session(session_key)


#: I tool del web, nascosti in un turno di pianificazione (servono solo
#: all'agente in modalità ``core``: l'orchestratore non li ha comunque).
#: **Niente web mentre si pianifica**: una pagina letta adesso contamina la
#: sessione (il taint del turno, ``security.subagent_policy``), e ogni aiutante
#: che i passi lanceranno dopo nascerebbe contaminato, con le scritture su
#: ``AGENTS.md`` o sulle skill del quaderno messe in bozza — il passo che si
#: ferma ad aspettare l'utente, nel mezzo di un lavoro che doveva andare da
#: solo. La pianificazione stabilisce **cosa** va cercato (la copertura, le voci
#: ``web``); la ricerca la fanno i passi ``research``.
#: ``python_exec`` e le sessioni di esecuzione ci sono perché arrivano alla rete
#: per un'altra strada (``http_get`` dentro il codice): stessa contaminazione.
PLANNING_HIDDEN_TOOLS: frozenset[str] = frozenset(
    {*UNTRUSTED_TOOLS, "download_file", "python_exec", "write_stdin", "list_exec_sessions"}
)

#: Le risposte dei tool del piano fuori dal loro turno, e di quelli che la
#: pianificazione chiude: restano nella lista (v. ``_plan_tool_filter``).
NOT_PLANNING_REFUSAL = (
    "Not now: a plan is proposed only while the user is planning (they start it with the Plan "
    "button). Do not propose a plan; answer the user."
)
NO_STEP_REFUSAL = (
    "Not now: no plan step is open in this turn, so there is nothing to close. Answer this "
    "message; the plan's steps go on in their own turns."
)
PLANNING_REFUSAL = (
    "Not while planning: a plan is being prepared, and the web and code do not run now (what "
    "they read would reach every helper of the plan's steps). Put what needs looking up in the "
    "plan, as a step of its own."
)

#: Le letture che contano come «ha guardato il quaderno» (``PlanDriver.propose``).
#: ``board`` solo con ``show``: le altre azioni scrivono, e in sola lettura sono
#: comunque rifiutate. ``notebook`` con ogni azione tranne ``remove_pages``
#: (v. ``_counts_as_read``).
PLANNING_READ_TOOLS: frozenset[str] = frozenset({"read_file", "list_dir", "grep", "find_files"})


def has_plans(session_key: str | None, driver: PlanDriver | None) -> bool:
    """Nella sessione si può pianificare: la chat di un quaderno, o quella
    personale quando il driver la permette (``PlanDriver.place_of``)."""
    if driver is None:
        return bool(session_key) and is_project_session_key(session_key or "")
    return driver.place_of(session_key) is not None


def is_planning_turn(
    metadata: Mapping[str, Any] | None,
    session_key: str,
    driver: PlanDriver | None = None,
) -> bool:
    """Un turno di pianificazione, dove si può pianificare (fuori non vale).

    Lo è il turno del tasto Piano (``plan_mode`` nei metadati) e, con il
    *driver*, ogni turno di una sessione che ha una pianificazione in corso:
    la risposta dell'utente a una domanda di Jenny non porta il tasto, ed è
    pianificazione lo stesso. I turni di un passo e quelli di un cron no.
    """
    if not has_plans(session_key, driver):
        return False
    if isinstance(metadata, Mapping) and metadata.get(PLAN_MODE_META) is True:
        return True
    return (
        driver is not None
        and not is_plan_turn(metadata)
        and not is_cron_turn(metadata)
        and driver.drafting_for(session_key) is not None
    )


def _counts_as_read(context: ToolResultHookContext) -> bool:
    if context.error is not None:
        return False
    if is_error_text(context.result):
        return False
    if context.name in PLANNING_READ_TOOLS:
        return True
    # ``notebook``: ogni azione legge, tranne ``remove_pages`` (che in un turno
    # di pianificazione è comunque rifiutata).
    if context.name == "notebook":
        return context.arguments.get("action") != "remove_pages"
    return context.name == "board" and context.arguments.get("action") == "show"


_SPAWNED_ID = re.compile(r"\(id: ([\w.-]+)\)")


class PlanReadHook(AgentHook):
    """Conta le letture del quaderno di un turno di pianificazione (il pavimento di A2).

    Un hook e non una lettura della storia a ``plan_propose``: le chiamate del
    turno in corso non sono ancora nella sessione quando il modello propone,
    e sono proprio quelle che contano. Annota anche gli aiutanti lanciati qui:
    il loro risultato, quando torna, è una lettura (``note_helper_result``).
    """

    def __init__(self, driver: PlanDriver, session_key: str) -> None:
        super().__init__()
        self._driver = driver
        self._session_key = session_key

    async def after_execute_tool(self, context: ToolResultHookContext) -> None:
        if _counts_as_read(context):
            await self._driver.note_read(self._session_key)
        elif context.name == "spawn" and context.error is None and isinstance(
            context.result, str
        ):
            if (found := _SPAWNED_ID.search(context.result)) is not None:
                await self._driver.note_helper(self._session_key, found.group(1))


class LoopPlanMixin:
    """I punti del turno che il piano a lista usa. Mixato in ``AgentLoop``."""

    if TYPE_CHECKING:
        tools: ToolRegistry
        _pending_queues: dict[str, Any]
        bus: Any

    plan_driver: PlanDriver | None = None

    def attach_plan_driver(self, driver: PlanDriver | None) -> None:
        """Lega il driver al loop e ai suoi tool (``GatewayContainer``)."""
        from jenny.agent.tools.plan import PlanTool

        self.plan_driver = driver
        for name in PLAN_TOOL_NAMES:
            tool = self.tools.get(name)
            if isinstance(tool, PlanTool):
                tool.bind_driver(driver)

    # ── il registro dell'annulla ──────────────────────────────────────────────

    def _plan_journal_of(self, metadata: Mapping[str, Any] | None) -> tuple[Any, str | None]:
        """Il registro del piano e il passo, per un turno di un passo; ``(None, None)``.

        Solo un turno con un passo aperto: lì Jenny lavora per il piano, e quel
        che scrive (le scritture dirette, le bozze installate dopo) è del passo.
        """
        driver = self.plan_driver
        meta = metadata or {}
        plan_id, step_id = meta.get(PLAN_ID_META), meta.get(PLAN_STEP_META)
        if driver is None or not isinstance(plan_id, str) or not isinstance(step_id, str):
            return None, None
        from jenny.agent.write_journal import journal_for
        from jenny.plan.work import plan_journal_key

        try:
            return journal_for(driver.store.workspace, plan_journal_key(plan_id)), step_id
        except Exception:  # noqa: BLE001 — senza registro il turno lavora lo stesso
            logger.opt(exception=True).warning("Plan {}: no undo journal for the turn", plan_id)
            return None, None

    # ── tool ────────────────────────────────────────────────────────────────

    def _plan_tool_filter(
        self, msg: InboundMessage, session_key: str
    ) -> Callable[[str], bool | str | None]:
        """Il pezzo del piano nel filtro dei tool del turno.

        Ritorna una funzione nome → ``True`` (nascosto), ``False`` (visibile),
        una frase (nella lista, ma la chiamata risponde con quella), ``None``
        (non è un tool del piano: decide il resto del filtro). Lo stato del piano
        si legge **una volta**, qui: il filtro gira a ogni lettura del registro,
        e un passo chiuso a metà turno lascia i tool visibili fino alla fine del
        turno — sono loro stessi a rispondere che non c'è più niente.

        **Nascosti solo dove non si pianifica mai** (dal 03/11/2026): dove si
        può pianificare i tool del piano restano nella lista a ogni turno, e
        fuori dal loro turno rispondono col motivo. Lo stesso per quelli che la
        pianificazione chiude. Nasconderli per turno cambiava la lista a metà
        chat, e il provider ripagava tutta la conversazione (v.
        ``AgentLoop._turn_tool_view``). Il rifiuto sta qui e non nei tool:
        ``plan_step_done`` da un turno di chat chiuderebbe il passo, e il web
        in pianificazione contaminerebbe la sessione.
        """
        driver = self.plan_driver
        here = has_plans(session_key, driver)
        planning = driver is not None and is_planning_turn(msg.metadata, session_key, driver)
        # Un cron non apre né chiude passi: i tool del passo non li vede.
        step_open = (
            driver is not None
            and not is_cron_turn(msg.metadata)
            and driver.step_open(session_key)
        )
        if step_open and driver is not None:
            # Nella chat personale un turno di chat (la mascotte, una notifica)
            # non chiude il passo: solo un turno che parla del passo aperto (il
            # suo messaggio, il promemoria, il resoconto di un suo aiutante, che
            # porta piano e passo) o la risposta data dalla scheda.
            place = driver.place_of(session_key)
            meta = msg.metadata if isinstance(msg.metadata, Mapping) else {}
            if place is not None and not place.traits.answer_by_chat:
                step_open = (driver.step_message_current(meta)
                             or bool(meta.get(PLAN_ANSWER_META)))

        def plan_verdict(name: str) -> bool | str | None:
            if name == PLAN_PROPOSE_TOOL:
                if not here:
                    return True
                return False if planning else NOT_PLANNING_REFUSAL
            if name in PLAN_STEP_TOOLS:
                if not here:
                    return True
                return False if step_open else NO_STEP_REFUSAL
            if planning and name in PLANNING_HIDDEN_TOOLS:
                return PLANNING_REFUSAL
            return None

        return plan_verdict

    # ── contesto ────────────────────────────────────────────────────────────

    def _plan_runtime_lines(
        self, msg: InboundMessage, session: Session | None, session_key: str
    ) -> list[str]:
        """Le righe del piano per il contesto di questo turno.

        Le note di sistema (un ritorno indietro) si consumano qui: le legge
        questo turno, e da qui in poi stanno nella storia col messaggio che le ha
        portate. Il salvataggio di fine turno le toglie dal disco.
        """
        driver = self.plan_driver
        if driver is None:
            return []
        lines: list[str] = []
        try:
            lines.extend(driver.runtime_lines(session_key))
            if is_planning_turn(msg.metadata, session_key, driver):
                meta = msg.metadata if isinstance(msg.metadata, Mapping) else {}
                revise = meta.get(PLAN_REVISE_META)
                lines.append(
                    driver.planning_block(
                        session_key,
                        revise=revise if isinstance(revise, str) else None,
                        followup=meta.get(PLAN_FOLLOWUP_META) is True,
                    )
                )
        except Exception:  # noqa: BLE001 — il contesto del piano non deve far cadere il turno
            logger.opt(exception=True).warning("Plan runtime lines failed for {}", session_key)
        if session is not None:
            notes = session.metadata.pop(PLAN_NOTES_META, None)
            if isinstance(notes, list):
                lines.extend(f"Note: {n}" for n in notes if isinstance(n, str))
        return lines

    def _plan_injection(
        self,
        pending_msg: InboundMessage,
        turn_metadata: Mapping[str, Any] | None,
        session_key: str | None = None,
    ) -> str:
        """Cosa fare di un messaggio in coda che arriva a turno in volo.

        ``"inject"`` per tutto quello che non è un turno del piano, e per un
        passo (o il promemoria) ancora attuale: il modello lo legge a metà
        turno, e il gancio di fine turno lo conta come letto. ``"drop"`` per un
        passo vecchio — lo stesso controllo del turno di sistema
        (``step_message_current``), che a metà turno mancava: un passo fermato
        da un Ferma non deve arrivare al modello da questa porta. ``"defer"``
        se il turno in volo è un cron: lì il passo non ha i suoi tool e la fine
        del turno non lo giudica, quindi il messaggio torna in coda e diventa
        un turno suo quando il cron finisce.

        ``"defer"`` anche per un messaggio che è pianificazione mentre il turno
        in volo non lo è, o viceversa: un ``plan: true`` iniettato in un turno
        normale lo scriverebbe senza la sola lettura e senza ``plan_propose``,
        e una risposta normale iniettata in una pianificazione resterebbe in
        sola lettura. Diventa un turno suo, che passa dal gancio di inizio.
        """
        if not is_plan_turn(pending_msg.metadata):
            driver = self.plan_driver
            if driver is None or not session_key or not has_plans(session_key, driver):
                return "inject"
            meta = pending_msg.metadata if isinstance(pending_msg.metadata, Mapping) else {}
            place = driver.place_of(session_key)
            if (
                place is not None and not place.traits.answer_by_chat
                and _is_step_work(turn_metadata)
            ):
                # Nella chat personale arriva di tutto (la mascotte, le notifiche,
                # Telegram): un messaggio a metà passo non entra nel turno del
                # passo, aspetta e diventa un turno suo. Per tutto il turno, non
                # solo finché il passo è aperto: il messaggio rimandato si
                # ricontrolla a ogni giro, e chiuso il passo sarebbe entrato
                # nella coda del turno — con la risposta in chat invece che nel
                # fumetto (trovato sul telefono il 03/11/2026).
                return "defer"
            if (
                pending_msg.channel != "system"
                and not meta.get(INJECTED_EVENT_META)
                and driver.awaits_answer(session_key)
            ):
                # La risposta dell'utente a un piano fermo, arrivata mentre gira
                # il turno di un resoconto: iniettata sarebbe chat, e il piano
                # resterebbe fermo. Diventa un turno suo, che passa dal gancio
                # di inizio (``before_user_turn``) e riapre il passo.
                return "defer"
            running = isinstance(turn_metadata, Mapping) and (
                turn_metadata.get(PLAN_MODE_META) is True
            )
            if is_planning_turn(pending_msg.metadata, session_key, driver) != running:
                return "defer"
            return "inject"
        driver = self.plan_driver
        if driver is None or not driver.step_message_current(pending_msg.metadata):
            return "drop"
        if is_cron_turn(turn_metadata):
            return "defer"
        place = driver.place_of(session_key) if session_key else None
        if place is not None and not place.traits.answer_by_chat and not _is_step_work(
            turn_metadata
        ):
            # Il rovescio della regola sopra: un passo che si apre mentre gira un
            # turno di chat (la mascotte passata davanti, v. ``_plan_turn_ended``)
            # aspetta la fine di quel turno. Dentro non avrebbe i suoi tool, e la
            # risposta andrebbe a chi ha scritto, non al piano.
            return "defer"
        return "inject"

    @staticmethod
    def _persist_plan_turn(session: Session, msg: InboundMessage) -> dict[str, Any]:
        """Scrive in storia il turno di passo, marcato come riga sintetica."""
        from jenny.plan.meta import PLAN_ID_META, PLAN_STEP_META

        meta = msg.metadata if isinstance(msg.metadata, dict) else {}
        session.add_message(
            "user",
            msg.content,
            sender_id=msg.sender_id,
            injected_event=meta.get(INJECTED_EVENT_META),
            plan_id=meta.get(PLAN_ID_META),
            plan_step=meta.get(PLAN_STEP_META),
        )
        return session.messages[-1]

    # ── hook del turno ──────────────────────────────────────────────────────

    def _plan_read_hook(
        self, metadata: Mapping[str, Any] | None, session_key: str | None
    ) -> AgentHook | None:
        """L'hook che conta le letture, per un turno di pianificazione; o ``None``."""
        driver = self.plan_driver
        if driver is None or not session_key:
            return None
        if not is_planning_turn(metadata, session_key, driver):
            return None
        return PlanReadHook(driver, session_key)

    async def _plan_system_turn(self, msg: InboundMessage, session_key: str) -> None:
        """Un turno di sistema durante una pianificazione: il rientro di un aiutante.

        È pianificazione come il turno che l'ha lanciato — sola lettura, e
        ``plan_propose`` visibile, perché è qui che il modello ha i risultati
        per proporre. Anche il rientro di un aiutante lanciato prima: in sola
        lettura, perché la pianificazione non scrive; ma come lettura conta
        solo quello lanciato da lei (:meth:`_plan_count_helper_result`, che il
        loop chiama dopo aver scartato i rientri già consegnati o vuoti).
        """
        driver = self.plan_driver
        if driver is None or not isinstance(msg.metadata, dict):
            return
        if is_plan_turn(msg.metadata) or is_cron_turn(msg.metadata):
            return
        if not has_plans(session_key, driver) or driver.drafting_for(session_key) is None:
            return
        msg.metadata[PLAN_MODE_META] = True
        msg.metadata[WORKSPACE_READONLY_METADATA_KEY] = True
        msg.metadata[PLAN_FOLLOWUP_META] = True

    async def _plan_count_helper_result(self, msg: InboundMessage, session_key: str) -> None:
        """Il risultato di un aiutante della pianificazione è una lettura del quaderno.

        È il modo di guardarlo quando è grande. Vale per un rientro che apre un
        turno suo e per uno iniettato a metà turno; non per un aiutante nato
        prima della pianificazione (``PlanDriver.note_helper_result``).
        """
        driver = self.plan_driver
        meta = msg.metadata if isinstance(msg.metadata, Mapping) else {}
        if driver is None or meta.get(INJECTED_EVENT_META) != SUBAGENT_RESULT_EVENT:
            return
        if not has_plans(session_key, driver):
            return
        try:
            await driver.note_helper_result(session_key, meta.get("subagent_task_id"))
        except Exception:  # noqa: BLE001 — il conto perso non vale il turno
            logger.opt(exception=True).warning("Plan read not counted for {}", session_key)

    async def _plan_note_step_report(self, msg: InboundMessage, session_key: str) -> None:
        """Il resoconto di un aiutante durante un piano: i suoi buchi **To research**.

        Il driver li tiene sul passo aperto e ne fa il cancello di
        ``plan_step_done`` (``PlanDriver.note_step_report``). Vale per un
        rientro che apre un turno, per uno tenuto senza turno e per uno
        iniettato a metà turno: il loop lo chiama in tutti e tre i punti.
        """
        driver = self.plan_driver
        meta = msg.metadata if isinstance(msg.metadata, Mapping) else {}
        if driver is None or meta.get(INJECTED_EVENT_META) != SUBAGENT_RESULT_EVENT:
            return
        if not has_plans(session_key, driver) or not driver.plan_running(session_key):
            return
        try:
            await driver.note_step_report(
                session_key,
                meta.get("subagent_task_id"),
                meta.get("subagent_lineage_id"),
                msg.content,
                plan_id=meta.get(PLAN_ID_META),
                plan_step=meta.get(PLAN_STEP_META),
                agent_type=meta.get("subagent_agent_type"),
                items=meta.get("subagent_to_research"),
            )
        except Exception:  # noqa: BLE001 — un buco non registrato non vale il turno
            logger.opt(exception=True).warning("Plan step report not noted for {}", session_key)

    async def _plan_before_turn(self, msg: InboundMessage, session_key: str) -> None:
        driver = self.plan_driver
        if driver is None or msg.channel == "system" or not isinstance(msg.metadata, dict):
            return
        if is_cron_turn(msg.metadata):
            # Mai una risposta a un piano bloccato; in sola lettura finché il
            # piano tiene il quaderno (v. il docstring del modulo).
            notebook = _notebook_of_turn(session_key)
            if notebook is not None and driver.holds_notebook(notebook):
                msg.metadata[WORKSPACE_READONLY_METADATA_KEY] = True
                logger.info("Cron turn in held notebook {} runs read-only", notebook)
            return
        if not has_plans(session_key, driver):
            return
        try:
            await driver.before_user_turn(session_key, msg.metadata, channel=msg.channel)
        except Exception:  # noqa: BLE001
            logger.opt(exception=True).warning("Plan pre-turn hook failed for {}", session_key)

    async def _let_waiting_messages_go_first(self, session_key: str, queue: Any) -> None:
        """I messaggi rimasti in coda nel turno di un passo vanno sul bus adesso.

        Nella chat personale un messaggio a metà passo aspetta la fine del turno
        (``_plan_injection``). Ripubblicato nel ``finally`` del turno, arrivava
        dopo il passo seguente, che il gancio di fine turno apre prima: e
        aspettava di nuovo, passo dopo passo, fino alla fine del piano (la
        mascotte senza risposta per tutto il piano, provato sul telefono il
        03/11/2026). Qui il turno lascia la sua coda — chi arriva da ora apre un
        task che aspetta il lock della sessione — e i messaggi passano davanti.
        """
        if self._pending_queues.get(session_key) is queue:
            self._pending_queues.pop(session_key, None)
        while True:
            try:
                item = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            await self.bus.publish_inbound(item)

    async def _plan_turn_ended(
        self, msg: InboundMessage, session_key: str, *, stop_reason: str = ""
    ) -> None:
        """*stop_reason*: come è finito il giro del modello (``TurnOutcome``), o vuoto."""
        driver = self.plan_driver
        if driver is None or not has_plans(session_key, driver):
            return
        if isinstance(msg.metadata, dict) and msg.metadata.get(PLAN_STALE_META):
            return
        if is_cron_turn(msg.metadata):
            # Un cron non è il lavoro del passo: la sua fine non lo giudica.
            return
        queue = self._pending_queues.get(session_key)
        pending = queue is not None and not queue.empty()
        meta = msg.metadata if isinstance(msg.metadata, Mapping) else {}
        if pending and _is_step_work(meta):
            place = driver.place_of(session_key)
            if place is not None and not place.traits.answer_by_chat:
                await self._let_waiting_messages_go_first(session_key, queue)
        # Il rifiuto del provider blocca il passo solo se il turno era il lavoro
        # del passo: il suo messaggio, il promemoria o il resoconto di un suo
        # aiutante. Un turno di chat dell'utente rifiutato durante il piano non
        # dice niente del passo, e il promemoria (una richiesta diversa) resta.
        if stop_reason == "provider_refused" and not (
            is_plan_turn(meta) or meta.get(INJECTED_EVENT_META) == SUBAGENT_RESULT_EVENT
        ):
            stop_reason = ""
        try:
            await driver.on_turn_end(
                session_key, msg.metadata, pending=pending, stop_reason=stop_reason
            )
        except Exception as exc:  # noqa: BLE001 — il turno è comunque finito
            logger.opt(exception=True).error("Plan end-of-turn hook failed for {}", session_key)
            # Il piano non resta «in corsa» senza niente che lo muova: il passo
            # aperto si ferma con una domanda, e la risposta dell'utente lo rifà.
            try:
                await driver.block_after_failure(session_key, repr(exc))
            except Exception:  # noqa: BLE001 — l'ultima rete: nel log
                logger.opt(exception=True).error("Plan {} could not be blocked", session_key)
