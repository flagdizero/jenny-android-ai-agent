"""Il driver dei piani a lista: l'unico che muove un piano.

**La lista la tiene il codice, non il modello.** Il driver sa qual è il passo
corrente; apre ogni passo con un turno iniettato nella chat del quaderno
(``agent/plan_step.md``: la lista con gli stati, il dettaglio del passo, le
regole), e il modello lo chiude con un tool (``plan_step_done``,
``plan_step_skip``, ``plan_blocked``). Fra un passo e l'altro c'è il codice:
fa le foto, aggiorna ``plan.json`` e apre il passo dopo, o si ferma se l'utente
ha chiesto la pausa. Così Pausa e Ferma sono deterministiche, Jenny non può
saltare o riordinare i passi, e «cosa ha fatto il passo 3» è il confronto fra
due foto con confini netti.

Chi chiama il driver:

- i **tool del modello** (``agent/tools/plan.py``): proporre, chiudere un
  passo, saltarlo, fermarsi su una domanda;
- le **RPC della WebUI** (``webui/plan_commands.py``): Approva, Scarta, Pausa,
  Riprendi, Ferma, la risposta, il ritorno indietro. Nessun tool le raggiunge:
  sono decisioni dell'utente per costruzione;
- il **loop** (``AgentLoop``): prima di un turno dell'utente (pausa = sola
  lettura, blocco = risposta), a fine turno (:meth:`PlanDriver.on_turn_end`), e
  per le righe del contesto di ogni turno;
- il **boot** (:meth:`PlanDriver.recover`): un piano trovato a metà va in
  pausa, un ritorno rimasto a metà si riapplica.

Il driver non conosce ``AgentLoop``: gli parla attraverso :class:`PlanHost`
(``LoopPlanHost`` in produzione, un finto nei test).

Le scritture di ``plan.json`` passano dallo store (lock per piano, scrittura
atomica); qui sopra un ``asyncio.Lock`` per sessione serializza le transizioni,
perché una transizione è fatta di più passaggi (foto, scrittura, messaggio) che
non devono intrecciarsi con quelli di un'altra — un Ferma toccato mentre il passo
si chiude, per esempio.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from loguru import logger

from jenny.bus.events import InboundMessage, OutboundMessage
from jenny.plan.model import (
    HELD_STATUSES,
    OPEN_STATUSES,
    Plan,
    PlanError,
    PlanTransitionError,
    PlanValidationError,
    Step,
    apply_transition,
    current_step,
    next_pending_step,
    now_iso,
    step_counts,
    validate_proposal,
)
from jenny.plan.photos import (
    KEEPABLE_GROUPS,
    PlanPhotos,
    RecoveryResult,
    RollbackConflictError,
    RollbackError,
    RollbackIncompleteError,
    RollbackReport,
    group_of,
)
from jenny.plan.store import PlanConflictError, PlanNotFoundError, PlanStore
from jenny.session.history_meta import (
    INJECTED_EVENT_META,
    PLAN_REMINDER_EVENT,
    PLAN_STEP_EVENT,
    PLAN_TURN_EVENTS,
)
from jenny.session.keys import PROJECT_SESSION_PREFIX, is_project_session_key

#: I quattro tool del modello. Stanno qui perché a decidere quando si vedono è
#: il driver (``AgentLoop`` chiede :meth:`PlanDriver.step_open`).
PLAN_PROPOSE_TOOL = "plan_propose"
PLAN_STEP_TOOLS: frozenset[str] = frozenset({"plan_step_done", "plan_step_skip", "plan_blocked"})
PLAN_TOOL_NAMES: frozenset[str] = frozenset({PLAN_PROPOSE_TOOL, *PLAN_STEP_TOOLS})

#: Metadati del messaggio: il turno di pianificazione (il tasto Piano), la
#: revisione di una proposta, la risposta a un piano bloccato.
PLAN_MODE_META = "plan_mode"
PLAN_REVISE_META = "plan_revise"
PLAN_ANSWER_META = "plan_answer"
PLAN_ID_META = "plan_id"
PLAN_STEP_META = "plan_step"

#: Le note di sistema che il modello legge al turno dopo (un ritorno indietro):
#: stanno nei metadati della sessione, e il loop le consuma nel contesto.
PLAN_NOTES_META = "plan_notes"

# Lo stato del promemoria di un passo, nell'``extra`` del passo: ``sent`` è
# partito e il suo turno non è ancora finito, ``used`` è stato speso. Un solo
# promemoria per tratto senza aiutanti: la lezione dei nove solleciti in 45
# secondi è che uno sprone che si ripete non sblocca niente.
_NUDGE = "nudge"
_NUDGE_SENT = "sent"
_NUDGE_USED = "used"

_RUNNING = frozenset({"running", "pausing"})

# Un ritorno (o il suo annullamento) partito e non finito: nell'``extra`` del
# piano, con l'errore. Il piano resta ``done``/``stopped`` (o ``rolled_back``),
# perché il quaderno è a metà fra i due; la scheda lo legge da qui e Riprova è
# sicuro, perché la regola a tre vie vede le operazioni già fatte come «già
# com'era». Un ritorno riuscito lo toglie.
ROLLBACK_FAILED_KEY = "rollback_failed"

# I due versi di un ritorno, nell'intestazione del journal: il boot li rilegge
# per portare il piano dove il ritorno l'avrebbe portato.
_KIND_ROLLBACK = "rollback"
_KIND_UNDO = "undo"

# I segni della lista nel turno del passo e nel contesto: corti, e uguali ai
# nomi degli stati della scheda.
_MARKS = {
    "done": "✓",
    "skipped": "↷",
    "now": "▶",
    "blocked": "?",
    "pending": "·",
    "interrupted": "✗",
}

_MAX_NOTE = 500
_MAX_QUESTION = 1000
_MAX_ANSWER = 8000


class PlanCommandError(PlanError):
    """Un rifiuto per l'utente, con il codice che la RPC rimanda.

    Codici: ``not_found``, ``conflict`` (il mondo si è mosso: una versione più
    nuova, un quaderno cambiato dopo il conto del ritorno), ``busy`` (un turno
    o un aiutante in volo), ``bad_state`` (non da questo stato),
    ``bad_request``, ``plan_running`` (un piano più nuovo tiene il quaderno),
    ``rollback_failed`` (il ritorno è partito e si è fermato a metà).
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class PlanHost(Protocol):
    """Quel che il driver chiede all'agente. In produzione è ``LoopPlanHost``."""

    def turn_in_flight(self, session_key: str) -> bool: ...

    def running_helpers(self, session_key: str) -> frozenset[str]: ...

    async def cancel(self, session_key: str) -> int: ...

    def goal_active(self, session_key: str) -> bool: ...

    def notebook_root(self, notebook: str) -> Path | None: ...

    def append_note(self, session_key: str, text: str) -> None: ...


@dataclass
class _Live:
    """Una voce dell'indice in memoria: il piano aperto di una sessione."""

    plan_id: str
    notebook: str
    status: str
    step_now: bool


def notebook_of(session_key: str | None) -> str | None:
    """Il quaderno di una chiave ``project:<nome>``, o ``None``."""
    if not session_key or not is_project_session_key(session_key):
        return None
    return session_key[len(PROJECT_SESSION_PREFIX):] or None


def _active_steps(plan: Plan) -> list[Step]:
    """I passi della lista come li vede l'utente: senza quelli tagliati."""
    return [s for s in plan.steps if s.status != "cut"]


def _position(plan: Plan, step_id: str) -> int:
    """Il numero (da 1) del passo fra quelli non tagliati: il «2» di «2/8»."""
    for index, step in enumerate(_active_steps(plan), start=1):
        if step.id == step_id:
            return index
    return 0


def _step_now(plan: Plan) -> Step | None:
    for step in plan.steps:
        if step.status == "now":
            return step
    return None


def _closing(plan: Plan) -> list[Step]:
    """I passi chiusi dal modello ma non ancora fotografati dopo.

    Il tool chiude il passo subito (la scheda lo vede fatto), la foto «dopo»
    la fa il driver a fine turno: il turno può scrivere ancora dopo la chiamata,
    e quelle scritture sono del passo.
    """
    return [
        s for s in plan.steps
        if s.status in ("done", "skipped") and s.photo_before and not s.photo_after
    ]


def _counts(diff: Mapping[str, list[str]]) -> dict[str, int]:
    return {key: len(diff.get(key, ())) for key in ("added", "changed", "removed")}


def report_with_groups(report: Mapping[str, Any]) -> dict[str, Any]:
    """Il resoconto di un ritorno con i conti per gruppo, per le caselle della scheda."""
    data = dict(report)
    groups: dict[str, dict[str, int]] = {}
    for outcome in ("restored", "removed", "already", "left", "kept", "excluded"):
        for path in report.get(outcome, ()):
            bucket = groups.setdefault(group_of(path), {})
            bucket[outcome] = bucket.get(outcome, 0) + 1
    data["groups"] = groups
    return data


class PlanDriver:
    """L'unico che muove i piani. Vedi il docstring del modulo."""

    def __init__(
        self,
        store: PlanStore,
        bus: Any,
        host: PlanHost,
        *,
        keep_closed: int = 5,
        max_age_days: int = 30,
    ) -> None:
        self.store = store
        self.bus = bus
        self.host = host
        self.keep_closed = keep_closed
        self.max_age_days = max_age_days
        self._locks: dict[str, asyncio.Lock] = {}
        # session_key → il piano aperto di quella sessione. È ciò che rende
        # economiche le domande di ogni turno (i tool si vedono? il turno è
        # sostenuto?): il driver è l'unico che scrive i piani, quindi l'indice
        # lo aggiorna lui a ogni scrittura, e al boot lo ricostruisce dal disco.
        self._index: dict[str, _Live] = {}
        # Le cartelle dei quaderni tenuti, per il cancello di scrittura dei tool
        # (``workspace_access.refuse_held_notebook_write``): calcolate alla prima
        # domanda dopo un cambio dell'indice, non a ogni scrittura.
        self._held_roots: dict[Path, str] | None = None

    # ── indice ─────────────────────────────────────────────────────────────

    def _lock(self, session_key: str) -> asyncio.Lock:
        return self._locks.setdefault(session_key, asyncio.Lock())

    def _remember(self, plan: Plan) -> None:
        self._held_roots = None
        if plan.is_open:
            self._index[plan.session_key] = _Live(
                plan_id=plan.id,
                notebook=plan.notebook,
                status=plan.status,
                step_now=_step_now(plan) is not None,
            )
            return
        live = self._index.get(plan.session_key)
        if live is not None and live.plan_id == plan.id:
            self._index.pop(plan.session_key, None)

    def refresh_index(self) -> None:
        """Ricostruisce l'indice dal disco (boot, rinomino, cancellazione di un quaderno)."""
        self._index = {}
        self._held_roots = None
        for plan in reversed(self.store.list_all()):
            if plan.is_open:
                self._remember(plan)

    # ── domande del loop (sincrone, dall'indice) ───────────────────────────

    def plan_open(self, session_key: str) -> bool:
        """La sessione ha un piano aperto (proposto, in corso, fermo)."""
        return session_key in self._index

    def plan_running(self, session_key: str) -> bool:
        """La sessione ha un piano che gira: il turno usa i limiti di un goal sostenuto."""
        live = self._index.get(session_key)
        return live is not None and live.status in _RUNNING

    def step_open(self, session_key: str) -> bool:
        """C'è un passo ``now`` da chiudere: i tool del passo si vedono."""
        live = self._index.get(session_key)
        return live is not None and live.status in _RUNNING and live.step_now

    def held_status(self, session_key: str) -> str | None:
        """Lo stato del piano che tiene il quaderno, o ``None`` se nessuno lo tiene."""
        live = self._index.get(session_key)
        if live is None or live.status not in HELD_STATUSES:
            return None
        return live.status

    def held_notebooks(self) -> set[str]:
        """I quaderni tenuti da un piano partito: l'utente non ci scrive."""
        return {
            live.notebook for live in self._index.values() if live.status in HELD_STATUSES
        }

    def holds_notebook(self, notebook: str) -> bool:
        return any(
            live.notebook == notebook and live.status in HELD_STATUSES
            for live in self._index.values()
        )

    def held_notebook_roots(self) -> dict[Path, str]:
        """``{cartella risolta: nome}`` dei quaderni tenuti, per il cancello dei tool.

        Dall'indice in memoria, con le cartelle chieste all'ospite una volta
        per cambio dell'indice: il cancello la interroga a ogni scrittura di un
        tool, e lì non ci deve essere un giro sul disco.
        """
        roots = self._held_roots
        if roots is None:
            roots = {}
            for name in sorted(self.held_notebooks()):
                root = self.host.notebook_root(name)
                if root is not None:
                    roots[root.resolve(strict=False)] = name
            self._held_roots = roots
        return roots

    def step_message_current(self, metadata: Mapping[str, Any] | None) -> bool:
        """Il turno di passo (o il promemoria) parla del passo che è aperto adesso.

        Un messaggio di passo può arrivare dopo che il passo è stato chiuso da
        un Ferma o da una Pausa (era già sul bus): quel turno non deve partire,
        o farebbe un passo che l'utente ha fermato.
        """
        if not isinstance(metadata, Mapping):
            return False
        plan_id = metadata.get(PLAN_ID_META)
        step_id = metadata.get(PLAN_STEP_META)
        if not isinstance(plan_id, str) or not isinstance(step_id, str):
            return False
        try:
            plan = self.store.load(plan_id)
        except PlanError:
            return False
        if plan.status not in _RUNNING:
            return False
        step = _step_now(plan)
        return step is not None and step.id == step_id

    # ── contesto del turno ─────────────────────────────────────────────────

    def runtime_lines(self, session_key: str | None) -> list[str]:
        """Le righe del contesto di ogni turno: il piano del quaderno, o un riassunto.

        Nella chat del quaderno la lista intera con gli stati, a ogni turno —
        anche quelli aperti dal ritorno di un aiutante, e dopo una
        compattazione: è la lezione di Manus, la lista ripetuta tiene il filo.
        Nella chat personale una riga per piano aperto, così Jenny sa dire «a
        che punto è» senza aprire niente.
        """
        if not session_key:
            return []
        if is_project_session_key(session_key):
            live = self._index.get(session_key)
            if live is None:
                return []
            try:
                plan = self.store.load(live.plan_id)
            except PlanError:
                return []
            return self._notebook_lines(plan)
        lines: list[str] = []
        for live in sorted(self._index.values(), key=lambda v: v.notebook):
            try:
                plan = self.store.load(live.plan_id)
            except PlanError:
                continue
            counts = step_counts(plan)
            lines.append(
                f'Notebook {plan.notebook}: plan "{plan.title}" '
                f"{counts['finished']}/{counts['total']}, {plan.status}."
            )
        return lines

    def _notebook_lines(self, plan: Plan) -> list[str]:
        counts = step_counts(plan)
        current = current_step(plan)
        head = f'Plan "{plan.title}" (v{plan.version}, {plan.status}'
        if current is not None:
            head += f", step {_position(plan, current.id)} of {counts['total']}"
        lines = [head + "):"]
        for index, step in enumerate(_active_steps(plan), start=1):
            lines.append(f"{_MARKS.get(step.status, '·')} {index}. {step.title}")
        if plan.status == "proposed":
            lines.append(
                "The plan is proposed and waits for the user's Approve or Review. "
                "Do not start it."
            )
        elif plan.status in _RUNNING and current is not None:
            lines.append(
                f"Step {_position(plan, current.id)} is open: work on it only, and when it "
                "is finished call plan_step_done (plan_step_skip if there is nothing to do, "
                "plan_blocked if you need the user)."
            )
        elif plan.status == "paused":
            lines.append(
                "The user paused the plan: this conversation is read-only until they tap "
                "Resume. Answer them, but do not work on the plan's steps."
            )
        elif plan.status == "blocked":
            question = (plan.blocked or {}).get("question") or ""
            lines.append(f"The plan waits for the user's answer to: {question}".rstrip())
        return lines

    def planning_block(self, session_key: str, *, revise: str | None = None) -> str:
        """Il blocco del turno di pianificazione (``agent/plan_mode.md``)."""
        from jenny.utils.prompt_templates import render_template

        current: dict[str, Any] | None = None
        live = self._index.get(session_key)
        if live is not None and live.status == "proposed":
            try:
                plan = self.store.load(live.plan_id)
            except PlanError:
                plan = None
            if plan is not None:
                current = {
                    "version": plan.version,
                    "title": plan.title,
                    "steps": [
                        {"id": s.id, "title": s.title, "detail": _shorten(s.detail, 300)}
                        for s in plan.steps
                    ],
                }
        return render_template(
            "agent/plan_mode.md",
            strip=True,
            current=current,
            revise=bool(revise) and current is not None,
        )

    # ── tool del modello ───────────────────────────────────────────────────

    async def propose(
        self,
        session_key: str | None,
        title: Any,
        steps: Any,
        notes: str | None = None,
    ) -> str:
        """``plan_propose``: scrive una proposta (o la versione dopo). Testo per il modello."""
        notebook = notebook_of(session_key)
        if notebook is None or session_key is None:
            return "Error: a plan can only be proposed inside a notebook chat."
        if self.host.goal_active(session_key):
            return (
                "Not now: this notebook has an active sustained goal, and a plan cannot run "
                "next to it. Finish it or close it with complete_goal first, or tell the user."
            )
        try:
            proposal = validate_proposal(title, steps)
        except PlanValidationError as exc:
            return f"Error: {exc} Fix the plan and call plan_propose again."
        clean_notes = notes.strip()[:2000] if isinstance(notes, str) and notes.strip() else None
        async with self._lock(session_key):
            try:
                plan = await asyncio.to_thread(
                    self.store.create, notebook, proposal, notes=clean_notes
                )
            except PlanConflictError as exc:
                return (
                    f"Not now: this notebook already has a plan that has started ({exc.status}). "
                    "A new one can be proposed when that plan is done or stopped."
                )
            self._remember(plan)
        await self._changed(plan)
        await self._prune()
        version = f" (version {plan.version})" if plan.version > 1 else ""
        return (
            f"The plan{version} is now on a card in front of the user, with Approve and "
            "Review. Stop here: do not start any step and do not repeat the list in your "
            "reply. Say in one or two sentences what it does, and wait: the user approves "
            "it, or asks for changes."
        )

    async def step_done(self, session_key: str | None, note: Any) -> str:
        """``plan_step_done``."""
        return await self._close_step(session_key, "done", note)

    async def step_skip(self, session_key: str | None, reason: Any) -> str:
        """``plan_step_skip``."""
        if not isinstance(reason, str) or not reason.strip():
            return "Error: say why the step is skipped (`reason`), honestly and in one line."
        return await self._close_step(session_key, "skipped", reason)

    async def _close_step(self, session_key: str | None, outcome: str, note: Any) -> str:
        if not session_key:
            return "Error: there is no plan step open in this conversation."
        live = self._index.get(session_key)
        if live is None:
            return "Error: there is no plan step open in this conversation."
        helpers = self.host.running_helpers(session_key)
        if helpers:
            # Un passo chiuso con gli aiutanti ancora al lavoro: le loro
            # scritture finirebbero nel passo dopo, e la foto «dopo» di questo
            # mentirebbe. Non è un errore (non «riprova diversamente»): è presto.
            return (
                f"Not yet: {len(helpers)} helper(s) you started for this step are still "
                "working. Wait for their results, then close the step."
            )
        text = note.strip()[:_MAX_NOTE] if isinstance(note, str) else ""
        async with self._lock(session_key):
            try:
                plan = await asyncio.to_thread(self.store.load, live.plan_id)
            except PlanError:
                return "Error: there is no plan step open in this conversation."
            step = _step_now(plan)
            if plan.status not in _RUNNING or step is None:
                return "Error: there is no plan step open in this conversation."
            step_id = step.id

            def _close(p: Plan) -> None:
                s = _find(p, step_id)
                s.status = outcome
                s.ended_at = now_iso()
                s.note = text or None
                s.extra.pop(_NUDGE, None)

            plan = await asyncio.to_thread(self.store.update, plan.id, _close)
            self._remember(plan)
        await self._changed(plan)
        last = next_pending_step(plan) is None
        n = _position(plan, step_id)
        verb = "closed" if outcome == "done" else "skipped"
        if last:
            return (
                f"Step {n} {verb}. It was the last step: end your turn with a short summary "
                "for the user of what the plan did."
            )
        return (
            f"Step {n} {verb}. The code takes it from here: end your turn now, with at most "
            "one short sentence for the user. Do not start the next step: it opens by itself."
        )

    async def blocked(self, session_key: str | None, question: Any) -> str:
        """``plan_blocked``: il piano si ferma su una domanda per l'utente."""
        if not isinstance(question, str) or not question.strip():
            return "Error: `question` must say, in one clear sentence, what you need from the user."
        if not session_key or (live := self._index.get(session_key)) is None:
            return "Error: there is no plan step open in this conversation."
        text = question.strip()[:_MAX_QUESTION]
        async with self._lock(session_key):
            try:
                plan = await asyncio.to_thread(self.store.load, live.plan_id)
            except PlanError:
                return "Error: there is no plan step open in this conversation."
            step = _step_now(plan)
            if plan.status not in _RUNNING or step is None:
                return "Error: there is no plan step open in this conversation."
            step_id = step.id
            plan = await asyncio.to_thread(
                self.store.update, plan.id, lambda p: _block(p, step_id, text, "question")
            )
            self._remember(plan)
        await self._changed(plan)
        return (
            "The plan now waits for the user; your question is on the plan card. End your "
            "turn: ask it in one sentence, and do nothing else on the plan until they answer."
        )

    # ── hook del loop ──────────────────────────────────────────────────────

    async def before_user_turn(self, session_key: str, metadata: dict[str, Any]) -> None:
        """Prima di un turno dell'utente nella chat di un quaderno.

        - piano in **pausa** → il turno è in sola lettura: si parla con Jenny,
          ma né lei né l'utente scrivono nel quaderno fuori da un passo;
        - piano **bloccato** → il messaggio è la risposta: il piano riparte e
          il passo continua in questo stesso turno.
        """
        live = self._index.get(session_key)
        if live is None or metadata.get(PLAN_ANSWER_META):
            return
        if live.status == "paused":
            from jenny.security.workspace_access import WORKSPACE_READONLY_METADATA_KEY

            metadata[WORKSPACE_READONLY_METADATA_KEY] = True
            return
        if live.status == "blocked":
            async with self._lock(session_key):
                try:
                    plan = await asyncio.to_thread(self._unblock, live.plan_id)
                except PlanCommandError:
                    return
                self._remember(plan)
            metadata[PLAN_ANSWER_META] = plan.id
            await self._changed(plan)

    async def on_turn_end(
        self,
        session_key: str,
        metadata: Mapping[str, Any] | None,
        *,
        pending: bool = False,
    ) -> None:
        """A fine turno nella chat di un quaderno: il passo è chiuso? va avanti.

        - passo chiuso → foto «dopo», poi il passo seguente, o la pausa chiesta,
          o la fine;
        - passo aperto con aiutanti in volo → niente: il loro risultato aprirà
          un turno, e quel turno continua lo stesso passo;
        - passo aperto, nessun aiutante, Jenny ha finito senza chiuderlo →
          **un** promemoria; se si ferma di nuovo, il passo è bloccato con un
          motivo scritto dal codice.

        *pending* dice che la sessione ha già un messaggio in coda (un risultato
        arrivato a fine turno): sarà un turno suo, e il giudizio aspetta quello.
        """
        live = self._index.get(session_key)
        if live is None or live.status not in _RUNNING:
            return
        meta = metadata if isinstance(metadata, Mapping) else {}
        async with self._lock(session_key):
            try:
                plan = await asyncio.to_thread(self.store.load, live.plan_id)
            except PlanError:
                return
            if plan.status not in _RUNNING:
                return
            if _closing(plan):
                plan = await self._settle_closed(plan)
                await self._advance(plan)
                return
            step = _step_now(plan)
            if step is None:
                # In corsa senza un passo aperto: un'apertura caduta a metà.
                await self._advance(plan)
                return
            await self._judge_open_step(plan, step, meta, pending=pending)

    async def _judge_open_step(
        self, plan: Plan, step: Step, meta: Mapping[str, Any], *, pending: bool
    ) -> None:
        step_id = step.id
        nudge = step.extra.get(_NUDGE)
        reminder_turn = (
            meta.get(INJECTED_EVENT_META) == PLAN_REMINDER_EVENT
            and meta.get(PLAN_STEP_META) == step_id
        )
        if reminder_turn:
            nudge = _NUDGE_USED
        if self.host.running_helpers(plan.session_key):
            # Lavoro delegato in corso: è progresso, e il promemoria speso torna
            # disponibile per il prossimo tratto senza aiutanti.
            new = _NUDGE_SENT if nudge == _NUDGE_SENT else None
            if new != step.extra.get(_NUDGE):
                await self._set_nudge(plan, step_id, new)
            return
        if pending:
            if nudge != step.extra.get(_NUDGE):
                await self._set_nudge(plan, step_id, nudge)
            return
        if nudge == _NUDGE_SENT:
            # Il promemoria è partito, questo turno non è il suo e in coda non
            # c'è niente: l'ha assorbito questo turno (iniettato a metà, quando
            # un altro messaggio gli è passato davanti), e il turno del
            # promemoria non arriverà più. Aspettarlo lascerebbe il piano in
            # corsa per sempre — e una Pausa ferma in ``pausing``: vale come
            # speso. Se invece è ancora sul bus, girerà da vecchio (il piano
            # non è più in corsa) e verrà scartato: un blocco un attimo in
            # anticipo, mai un piano incantato.
            nudge = _NUDGE_USED
        if nudge == _NUDGE_USED:
            from jenny.runtime.user_text import say

            plan = await asyncio.to_thread(
                self.store.update,
                plan.id,
                lambda p: _block(p, step_id, say("plan.stalled"), "stalled"),
            )
            self._remember(plan)
            logger.info("Plan {} blocked: step {} stalled after the reminder", plan.id, step_id)
            await self._changed(plan)
            return
        await self._set_nudge(plan, step_id, _NUDGE_SENT)
        await self.bus.publish_inbound(self._step_message(plan, step, reminder=True))
        logger.info("Plan {}: one reminder for step {}", plan.id, step_id)

    async def _set_nudge(self, plan: Plan, step_id: str, value: str | None) -> None:
        def _apply(p: Plan) -> None:
            s = _find(p, step_id)
            if value is None:
                s.extra.pop(_NUDGE, None)
            else:
                s.extra[_NUDGE] = value

        await asyncio.to_thread(self.store.update, plan.id, _apply)

    # ── comandi dell'utente (RPC) ──────────────────────────────────────────

    async def approve(self, plan_id: str, version: Any, cut: Iterable[Any] = ()) -> Plan:
        """Approva: foto A, ``running``, primo passo. ``version`` come ``target_sha256``."""
        plan = self._load(plan_id)
        cut_ids = _cut_ids(plan, cut)
        async with self._lock(plan.session_key):
            plan = self._load(plan_id)
            if plan.status == "superseded" or (
                isinstance(version, bool) or not isinstance(version, int) or version != plan.version
            ):
                raise PlanCommandError(
                    "conflict", "the plan changed since it was shown: read it again"
                )
            if plan.status != "proposed":
                raise PlanCommandError("bad_state", f"the plan is {plan.status}, not proposed")
            self._require_idle(plan.session_key)
            if self.host.goal_active(plan.session_key):
                raise PlanCommandError(
                    "bad_state", "this notebook has an active sustained goal"
                )
            photos = self._photos(plan)
            start = await asyncio.to_thread(photos.take, "start")

            def _approve(p: Plan) -> None:
                if p.status != "proposed":
                    raise PlanCommandError("bad_state", f"the plan is {p.status}, not proposed")
                apply_transition(p, "running", event="approved", version=p.version)
                for s in p.steps:
                    if s.id in cut_ids:
                        s.status = "cut"
                p.photos["start"] = start

            plan = await asyncio.to_thread(self.store.update, plan_id, _approve)
            self._remember(plan)
            logger.info("Plan {} approved (v{}, {} cut)", plan_id, plan.version, len(cut_ids))
            await self._advance(plan)
        return self._load(plan_id)

    async def discard(self, plan_id: str) -> Plan:
        plan = self._load(plan_id)
        async with self._lock(plan.session_key):
            plan = await self._transition(plan_id, "discarded", only=("proposed",))
        await self._prune()
        return plan

    async def pause(self, plan_id: str) -> Plan:
        """Pausa: il passo in corso finisce, poi ``paused``."""
        plan = self._load(plan_id)
        async with self._lock(plan.session_key):
            plan = self._load(plan_id)
            if plan.status in ("pausing", "paused"):
                return plan
            if plan.status != "running":
                raise PlanCommandError("bad_state", f"the plan is {plan.status}")
            # Fra due passi non c'è niente da aspettare: in pausa subito.
            wanted = "pausing" if (_step_now(plan) or _closing(plan)) else "paused"
            return await self._transition(plan_id, wanted, only=("running",))

    async def resume(self, plan_id: str) -> Plan:
        """Riprendi: annulla una pausa non ancora arrivata, o apre il passo dopo."""
        plan = self._load(plan_id)
        async with self._lock(plan.session_key):
            plan = self._load(plan_id)
            if plan.status == "pausing":
                return await self._transition(plan_id, "running", only=("pausing",))
            if plan.status != "paused":
                raise PlanCommandError("bad_state", f"the plan is {plan.status}, not paused")
            self._require_idle(plan.session_key)
            plan = await self._transition(plan_id, "running", only=("paused",), event="resumed")
            await self._advance(plan)
        return self._load(plan_id)

    async def stop(self, plan_id: str, then: str = "keep") -> dict[str, Any] | None:
        """Ferma: via il turno e gli aiutanti, passo ``interrupted``, foto B, ``stopped``.

        Con ``then="rollback"`` continua col ritorno a prima, e ne ritorna il
        resoconto.
        """
        if then not in ("keep", "rollback"):
            raise PlanCommandError("bad_request", "then must be keep or rollback")
        plan = self._load(plan_id)
        async with self._lock(plan.session_key):
            plan = self._load(plan_id)
            if plan.status not in HELD_STATUSES:
                raise PlanCommandError("bad_state", f"the plan is {plan.status}")
            cancelled = await self.host.cancel(plan.session_key)
            photos = self._photos(plan)
            end = await asyncio.to_thread(photos.take, "end")

            def _stop(p: Plan) -> None:
                stamp = now_iso()
                for s in p.steps:
                    if s.status in ("now", "blocked"):
                        s.status = "interrupted"
                        s.ended_at = stamp
                        s.extra.pop(_NUDGE, None)
                p.photos["end"] = end
                apply_transition(p, "stopped", at=stamp, then=then, cancelled=cancelled)

            plan = await asyncio.to_thread(self.store.update, plan_id, _stop)
            self._remember(plan)
            logger.info("Plan {} stopped ({}), {} task(s) cancelled", plan_id, then, cancelled)
        await self._changed(plan)
        await self._prune()
        if then == "rollback":
            try:
                return await self.rollback(plan_id, keep=(), expect_end=plan.photos["end"])
            except PlanCommandError as exc:
                # Il piano è fermo comunque: il ritorno si può chiedere dopo dalla
                # scheda (un aiutante che non è morto in tempo, per esempio). La
                # risposta lo dice invece di far credere che il Ferma non sia
                # avvenuto.
                logger.warning("Plan {} stopped, rollback refused: {}", plan_id, exc.message)
                return {"rollback_error": {"code": exc.code, "message": exc.message}}
        return None

    async def stop_for_session(self, session_key: str) -> bool:
        """``/stop`` scritto nella chat di un quaderno con un piano partito = Ferma e tieni."""
        live = self._index.get(session_key)
        if live is None or live.status not in HELD_STATUSES:
            return False
        try:
            await self.stop(live.plan_id, "keep")
        except PlanCommandError:
            return False
        return True

    async def answer(self, plan_id: str, text: Any) -> Plan:
        """La risposta a un piano bloccato: riparte, e il testo è un messaggio vero."""
        if not isinstance(text, str) or not text.strip():
            raise PlanCommandError("bad_request", "text required")
        if len(text) > _MAX_ANSWER:
            raise PlanCommandError("bad_request", "answer too long")
        plan = self._load(plan_id)
        async with self._lock(plan.session_key):
            plan = await asyncio.to_thread(self._unblock, plan_id)
            self._remember(plan)
        await self._changed(plan)
        await self.bus.publish_inbound(
            InboundMessage(
                channel="websocket",
                sender_id="user",
                chat_id=plan.session_key,
                content=text.strip(),
                metadata={PLAN_ANSWER_META: plan.id, "_wants_stream": True},
            )
        )
        return plan

    def _unblock(self, plan_id: str) -> Plan:
        """``blocked → running``, il passo torna ``now`` (sincrono, in un thread)."""

        def _apply(p: Plan) -> None:
            if p.status != "blocked":
                raise PlanCommandError("bad_state", f"the plan is {p.status}, not blocked")
            for s in p.steps:
                if s.status == "blocked":
                    s.status = "now"
                    s.extra.pop(_NUDGE, None)
            apply_transition(p, "running", event="answered")

        try:
            return self.store.update(plan_id, _apply)
        except PlanNotFoundError as exc:
            raise PlanCommandError("not_found", "no such plan") from exc

    # ── ritorno indietro ───────────────────────────────────────────────────

    def _refuse_if_held(self, plan: Plan) -> None:
        """``plan_running`` se un piano più nuovo tiene il quaderno di *plan*.

        Tornare indietro con un piano vecchio mentre uno nuovo gira riscriverebbe
        il quaderno sotto i passi di quest'ultimo, e le sue foto attribuirebbero
        al piano nuovo un ritorno che non ha fatto: è la stessa regola per cui
        l'utente non scrive in un quaderno tenuto.
        """
        if self.holds_notebook(plan.notebook):
            raise PlanCommandError(
                "plan_running",
                f"notebook {plan.notebook!r} is held by a running plan: wait for it, "
                "or stop it first",
            )

    async def rollback_preview(self, plan_id: str) -> dict[str, Any]:
        """Il conto del ritorno, e l'id della foto di «adesso» da cui è stato fatto.

        ``expect_end`` è quella foto: la conferma (:meth:`rollback`) ne rifà una
        e, se è un'altra, il quaderno è cambiato dopo che l'utente ha letto il
        conto — ``conflict``. Il nome è quello dell'RPC; prima era la foto B
        del piano, che dopo il Ferma non cambia più e quindi non vedeva niente.
        """
        plan = self._load(plan_id)
        if plan.status not in ("done", "stopped"):
            raise PlanCommandError("bad_state", f"the plan is {plan.status}")
        start, end = plan.photos.get("start"), plan.photos.get("end")
        if not start or not end:
            raise PlanCommandError("bad_state", "the plan has no photos to go back to")
        self._refuse_if_held(plan)
        photos = self._photos(plan)
        async with self._lock(plan.session_key):
            report = await asyncio.to_thread(
                photos.rollback,
                start,
                end,
                journal_path=self.store.journal_path(plan_id),
                dry_run=True,
            )
        return {"report": report_with_groups(report.to_dict()), "expect_end": report.before}

    async def rollback(
        self, plan_id: str, keep: Iterable[Any] = (), expect_end: Any = None
    ) -> dict[str, Any]:
        """Torna a prima: ``rollback(target=A, expected=B)``, foto C e R, ``rolled_back``.

        *expect_end* è l'id che :meth:`rollback_preview` ha ritornato (la foto
        del quaderno su cui l'utente ha letto il conto); dal Ferma con ritorno è
        la foto B appena fatta.
        """
        keep_groups = _keep_groups(keep)
        plan = self._load(plan_id)
        async with self._lock(plan.session_key):
            plan = self._load(plan_id)
            if plan.status not in ("done", "stopped"):
                raise PlanCommandError("bad_state", f"the plan is {plan.status}")
            start, end = plan.photos.get("start"), plan.photos.get("end")
            if not start or not end:
                raise PlanCommandError("bad_state", "the plan has no photos to go back to")
            self._refuse_if_held(plan)
            self._require_idle(plan.session_key, code="conflict")
            if not isinstance(expect_end, str) or not expect_end:
                raise PlanCommandError(
                    "conflict", "the notebook changed since the summary was shown: read it again"
                )
            photos = self._photos(plan)
            committed: list[Plan] = []

            def _commit(report: RollbackReport) -> None:
                # Nel thread del ritorno, prima di togliere il journal: un crollo
                # fra i file rimessi e ``plan.json`` lascia il journal, e il boot
                # finisce il lavoro (``recover``).
                committed.append(
                    self.store.update(
                        plan_id,
                        lambda p: _mark_rolled_back(
                            p,
                            before=report.before,
                            after=report.after,
                            keep=keep_groups,
                            counts=report.counts,
                        ),
                    )
                )

            report = await self._run_rollback(
                plan,
                _KIND_ROLLBACK,
                photos.rollback,
                start,
                end,
                journal_path=self.store.journal_path(plan_id),
                keep_groups=keep_groups,
                expect_now=expect_end,
                journal_header={"plan_id": plan_id, "kind": _KIND_ROLLBACK},
                commit=_commit,
            )
            plan = committed[-1]
            self._remember(plan)
            self.host.append_note(plan.session_key, _rollback_note(plan, report.counts))
            logger.info("Plan {} rolled back: {}", plan_id, report.counts)
        await self._changed(plan)
        return {"report": report_with_groups(report.to_dict())}

    async def undo_rollback(self, plan_id: str) -> dict[str, Any]:
        """Annulla il ritorno: ``rollback(target=C, expected=R)``, e lo stato di prima."""
        plan = self._load(plan_id)
        async with self._lock(plan.session_key):
            plan = self._load(plan_id)
            if plan.status != "rolled_back":
                raise PlanCommandError("bad_state", f"the plan is {plan.status}")
            before = plan.photos.get("before_rollback")
            after = plan.photos.get("after_rollback")
            if not before or not after:
                raise PlanCommandError("bad_state", "there is no rollback to undo")
            self._refuse_if_held(plan)
            self._require_idle(plan.session_key, code="conflict")
            photos = self._photos(plan)
            committed: list[Plan] = []

            def _commit(report: RollbackReport) -> None:
                committed.append(
                    self.store.update(plan_id, lambda p: _mark_rollback_undone(p, report.counts))
                )

            report = await self._run_rollback(
                plan,
                _KIND_UNDO,
                photos.rollback,
                before,
                after,
                journal_path=self.store.journal_path(plan_id),
                journal_header={"plan_id": plan_id, "kind": _KIND_UNDO},
                commit=_commit,
            )
            plan = committed[-1]
            self._remember(plan)
            self.host.append_note(plan.session_key, _undo_note(plan, report.counts))
        await self._changed(plan)
        return {"report": report_with_groups(report.to_dict())}

    async def _run_rollback(self, plan: Plan, kind: str, fn: Any, *args: Any, **kw: Any) -> Any:
        """Esegue un ritorno in un thread e traduce i suoi errori per la scheda.

        Un ritorno rifiutato prima di scrivere è ``conflict``; uno caduto a metà
        (il disco) resta scritto nel piano (``rollback_failed``) e risponde
        ``rollback_failed``: il quaderno è a metà, e la scheda lo deve dire
        invece di mostrare un piano finito come se niente fosse.
        """
        try:
            return await asyncio.to_thread(fn, *args, **kw)
        except RollbackIncompleteError as exc:
            await asyncio.to_thread(
                self._record_rollback_failure,
                plan.id,
                kind,
                str(exc),
                applied=exc.applied,
                total=exc.total,
            )
            await self._changed(self._load(plan.id))
            raise PlanCommandError(
                "rollback_failed",
                f"the rollback stopped after {exc.applied} of {exc.total} file(s): {exc}. "
                "The notebook is partly rolled back; try again",
            ) from exc
        except (RollbackConflictError, RollbackError) as exc:
            raise PlanCommandError("conflict", str(exc)) from exc

    def _record_rollback_failure(
        self,
        plan_id: str,
        kind: str,
        error: str,
        *,
        applied: int | None = None,
        total: int | None = None,
    ) -> Plan:
        """Scrive nel piano che un ritorno non è finito (sincrono)."""
        stamp = now_iso()
        failure: dict[str, Any] = {"at": stamp, "kind": kind, "error": error[:500]}
        if applied is not None:
            failure["applied"] = applied
        if total is not None:
            failure["total"] = total

        def _apply(p: Plan) -> None:
            p.extra[ROLLBACK_FAILED_KEY] = failure
            p.history.append({"at": stamp, "event": "rollback_failed", **failure})

        plan = self.store.update(plan_id, _apply)
        logger.error("Plan {}: {} did not complete: {}", plan_id, kind, error)
        return plan

    # ── letture per le RPC ─────────────────────────────────────────────────

    def view(self, plan: Plan) -> dict[str, Any]:
        """``plan.json`` più i campi derivati che la scheda usa."""
        return plan_view(plan)

    def step_files(self, plan_id: str, step_id: Any) -> dict[str, list[str]]:
        plan = self._load(plan_id)
        if not isinstance(step_id, str):
            raise PlanCommandError("bad_request", "step required")
        step = next((s for s in plan.steps if s.id == step_id), None)
        if step is None:
            raise PlanCommandError("not_found", "no such step")
        if not step.photo_before or not step.photo_after:
            raise PlanCommandError("bad_state", "the step has not finished")
        return self._photos(plan).diff(step.photo_before, step.photo_after)

    def changes(self, plan_id: str) -> dict[str, Any]:
        plan = self._load(plan_id)
        start = plan.photos.get("start")
        if not start:
            raise PlanCommandError("bad_state", "the plan has not started")
        # La B se c'è; in corsa, l'ultima foto «dopo» di un passo: il confronto
        # di un piano a metà dice quel che è successo fin qui.
        end = plan.photos.get("end") or next(
            (s.photo_after for s in reversed(plan.steps) if s.photo_after), start
        )
        diff = self._photos(plan).diff(start, end)
        paths = [*diff["added"], *diff["changed"], *diff["removed"]]
        return {**diff, "groups": {path: group_of(path) for path in paths}}

    # ── boot ───────────────────────────────────────────────────────────────

    def recover(self) -> list[str]:
        """Al boot: i ritorni a metà si riapplicano, i piani a metà vanno in pausa.

        Riprendere da solo un passo interrotto rischia doppioni anche con passi
        idempotenti, e l'utente non saprebbe che il telefono si è riavviato: il
        piano aspetta Riprendi (deciso il 23/10), con una riga nella storia. Il
        passo interrotto torna ``pending`` e Riprendi lo riapre da capo, con
        una foto «prima» nuova. Sincrono: gira prima del loop.
        """
        from jenny.runtime.user_text import say

        paused: list[str] = []
        for plan in self.store.list_all():
            if self.store.journal_path(plan.id).exists():
                plan = self._recover_journal(plan)
            if plan.status not in ("running", "pausing", "blocked"):
                continue
            closing = _closing(plan)
            after: dict[str, str] = {}
            if closing:
                root = self.host.notebook_root(plan.notebook)
                if root is not None:
                    photo = PlanPhotos(self.store.plan_dir(plan.id), root).take("after")
                    after = {s.id: photo for s in closing}
            interrupted = current_step(plan) or next_pending_step(plan)
            n = _position(plan, interrupted.id) if interrupted is not None else 0

            def _pause(p: Plan, after: dict[str, str] = after, n: int = n) -> None:
                for s in p.steps:
                    if s.id in after:
                        s.photo_after = after[s.id]
                    if s.status in ("now", "blocked"):
                        s.status = "pending"
                        s.started_at = None
                        s.photo_before = None
                        s.extra.pop(_NUDGE, None)
                apply_transition(
                    p, "paused", event="paused", reason="restart", step=n,
                    note=say("plan.restarted", step=n) if n else None,
                )

            try:
                self.store.update(plan.id, _pause)
            except (PlanError, OSError):
                logger.opt(exception=True).error("Plan {}: could not pause at boot", plan.id)
                continue
            paused.append(plan.id)
            logger.warning("Plan {} was {} at boot: paused at step {}", plan.id, plan.status, n)
        self.refresh_index()
        return paused

    def _recover_journal(self, plan: Plan) -> Plan:
        """Finisce al boot un ritorno (o un annullamento) rimasto a metà.

        I file: :meth:`PlanPhotos.recover_pending`, che applica un'operazione
        solo se il file è ancora com'era quando il ritorno è stato deciso. Lo
        stato: se il ritorno è finito, il piano va dove il ritorno l'avrebbe
        portato — ``rolled_back`` con la foto «dopo», o lo stato di prima per un
        annullamento — e Jenny ne riceve la nota, come per un ritorno normale.
        Se il crollo è venuto dopo la scrittura di ``plan.json``, il piano è già
        lì e non si tocca. Un recupero caduto lascia il piano com'è con
        ``rollback_failed``; un journal illeggibile o insicuro si mette da parte
        (``.rejected``) invece di ritentarlo a ogni avvio.
        """
        journal = self.store.journal_path(plan.id)
        root = self.host.notebook_root(plan.notebook)
        if root is None:
            logger.warning(
                "Plan {}: a rollback was interrupted but notebook {!r} is gone",
                plan.id, plan.notebook,
            )
            return plan
        photos = PlanPhotos(self.store.plan_dir(plan.id), root)
        settled: list[tuple[Plan, str, dict[str, int]]] = []

        def _commit(result: RecoveryResult) -> None:
            header = result.header
            kind = header.get("kind")
            counts = _header_counts(header)

            def _settle(p: Plan) -> None:
                if kind == _KIND_ROLLBACK and p.status in ("done", "stopped"):
                    _mark_rolled_back(
                        p,
                        before=str(header.get("before") or "") or None,
                        after=result.after,
                        keep=[str(g) for g in header.get("keep") or ()],
                        counts=counts,
                        recovered=True,
                    )
                elif (
                    kind == _KIND_UNDO
                    and p.status == "rolled_back"
                    and p.photos.get("before_rollback") == header.get("target")
                ):
                    _mark_rollback_undone(p, counts, recovered=True)
                else:
                    raise _UnchangedError

            try:
                settled.append((self.store.update(plan.id, _settle), str(kind), counts))
            except _UnchangedError:
                pass

        try:
            parsed = PlanPhotos.read_journal(journal)
            if parsed is not None and parsed[0].get("plan_id") != plan.id:
                raise RollbackError(
                    f"journal of plan {str(parsed[0].get('plan_id'))[:40]!r}, not {plan.id}"
                )
            result = photos.recover_pending(journal, commit=_commit)
        except RollbackError as exc:
            logger.error("Plan {}: interrupted rollback journal rejected: {}", plan.id, exc)
            try:
                journal.replace(journal.with_name(journal.name + ".rejected"))
            except OSError:
                logger.opt(exception=True).error("Plan {}: journal not set aside", plan.id)
            return self._failed_at_boot(plan, str(exc))
        except (PlanError, OSError) as exc:
            logger.opt(exception=True).error(
                "Plan {}: could not finish an interrupted rollback", plan.id
            )
            return self._failed_at_boot(plan, str(exc))
        if result is not None and result.error is not None:
            return self._failed_at_boot(plan, result.error, kind=result.header.get("kind"))
        if result is not None and result.left:
            logger.warning(
                "Plan {}: recovered rollback left {} file(s) changed since: {}",
                plan.id, len(result.left), result.left[:5],
            )
        if not settled:
            return self._reload(plan)
        updated, kind, counts = settled[-1]
        note = _undo_note(updated, counts) if kind == _KIND_UNDO else _rollback_note(
            updated, counts
        )
        try:
            self.host.append_note(updated.session_key, note)
        except Exception:  # noqa: BLE001 — la nota persa non vale un boot fallito
            logger.opt(exception=True).warning("Plan {}: recovery note not saved", plan.id)
        logger.warning("Plan {}: interrupted {} finished at boot", plan.id, kind)
        return updated

    def _failed_at_boot(self, plan: Plan, error: str, *, kind: Any = None) -> Plan:
        try:
            return self._record_rollback_failure(
                plan.id, str(kind) if kind in (_KIND_ROLLBACK, _KIND_UNDO) else "rollback", error
            )
        except (PlanError, OSError):
            logger.opt(exception=True).error("Plan {}: rollback failure not recorded", plan.id)
            return plan

    def _reload(self, plan: Plan) -> Plan:
        try:
            return self.store.load(plan.id)
        except PlanError:
            return plan

    # ── quaderni ───────────────────────────────────────────────────────────

    def notebook_renamed(self, old: str, new: str) -> None:
        self.store.follow_rename(old, new)
        self.refresh_index()

    def notebook_deleted(self, name: str) -> None:
        self.store.delete_for_notebook(name)
        self.refresh_index()

    # ── interni ────────────────────────────────────────────────────────────

    def _load(self, plan_id: str) -> Plan:
        try:
            return self.store.load(plan_id)
        except PlanNotFoundError as exc:
            raise PlanCommandError("not_found", "no such plan") from exc

    def _photos(self, plan: Plan) -> PlanPhotos:
        root = self.host.notebook_root(plan.notebook)
        if root is None:
            raise PlanCommandError("bad_state", f"notebook {plan.notebook!r} is not there")
        return PlanPhotos(self.store.plan_dir(plan.id), root)

    def _require_idle(self, session_key: str, *, code: str = "busy") -> None:
        if self.host.turn_in_flight(session_key) or self.host.running_helpers(session_key):
            raise PlanCommandError(
                code, "Jenny is working in this notebook: try again when she has finished"
            )

    async def _transition(
        self,
        plan_id: str,
        wanted: str,
        *,
        only: tuple[str, ...],
        event: str | None = None,
    ) -> Plan:
        def _apply(p: Plan) -> None:
            if p.status not in only:
                raise PlanCommandError("bad_state", f"the plan is {p.status}")
            apply_transition(p, wanted, event=event)

        try:
            plan = await asyncio.to_thread(self.store.update, plan_id, _apply)
        except PlanNotFoundError as exc:
            raise PlanCommandError("not_found", "no such plan") from exc
        except PlanTransitionError as exc:
            raise PlanCommandError("bad_state", str(exc)) from exc
        self._remember(plan)
        await self._changed(plan)
        return plan

    async def _settle_closed(self, plan: Plan) -> Plan:
        """La foto «dopo» dei passi appena chiusi, e i loro conti."""
        photos = self._photos(plan)
        photo = await asyncio.to_thread(photos.take, "after")
        counts: dict[str, dict[str, int]] = {}
        for step in _closing(plan):
            if step.photo_before:
                counts[step.id] = _counts(
                    await asyncio.to_thread(photos.diff, step.photo_before, photo)
                )

        def _apply(p: Plan) -> None:
            for s in p.steps:
                if s.id in counts or (
                    s.status in ("done", "skipped") and s.photo_before and not s.photo_after
                ):
                    s.photo_after = photo
                    if s.id in counts:
                        s.extra["files"] = counts[s.id]

        plan = await asyncio.to_thread(self.store.update, plan.id, _apply)
        self._remember(plan)
        return plan

    async def _advance(self, plan: Plan) -> None:
        """Dopo un passo chiuso (o all'avvio): pausa, passo seguente, o fine."""
        if plan.status == "pausing":
            await self._transition(plan.id, "paused", only=("pausing",))
            return
        if plan.status != "running":
            return
        nxt = next_pending_step(plan)
        if nxt is None:
            await self._finish(plan)
            return
        await self._open_step(plan, nxt.id)

    async def _open_step(self, plan: Plan, step_id: str) -> None:
        photos = self._photos(plan)
        before = await asyncio.to_thread(photos.take, f"before {step_id}")

        def _open(p: Plan) -> None:
            s = _find(p, step_id)
            s.status = "now"
            s.started_at = now_iso()
            s.ended_at = None
            s.photo_before = before
            s.photo_after = None
            s.extra.pop(_NUDGE, None)

        plan = await asyncio.to_thread(self.store.update, plan.id, _open)
        self._remember(plan)
        step = _find(plan, step_id)
        await self.bus.publish_inbound(self._step_message(plan, step))
        logger.info("Plan {}: step {} opened", plan.id, step_id)
        await self._changed(plan)

    async def _finish(self, plan: Plan) -> None:
        photos = self._photos(plan)
        end = await asyncio.to_thread(photos.take, "end")

        def _done(p: Plan) -> None:
            p.photos["end"] = end
            apply_transition(p, "done")

        plan = await asyncio.to_thread(self.store.update, plan.id, _done)
        self._remember(plan)
        logger.info("Plan {} done", plan.id)
        await self._changed(plan)
        await self._prune()

    def _step_message(self, plan: Plan, step: Step, *, reminder: bool = False) -> InboundMessage:
        from jenny.utils.prompt_templates import render_template

        steps = [
            {
                "n": index,
                "title": s.title,
                "mark": _MARKS.get(s.status, "·"),
                "note": s.note if s.status in ("done", "skipped") else None,
            }
            for index, s in enumerate(_active_steps(plan), start=1)
        ]
        content = render_template(
            "agent/plan_step.md",
            strip=True,
            reminder=reminder,
            title=plan.title,
            steps=steps,
            total=len(steps),
            step={
                "n": _position(plan, step.id),
                "title": step.title,
                "detail": step.detail,
            },
        )
        return InboundMessage(
            channel="system",
            sender_id="plan",
            # ``websocket:project:<nome>``: il turno di sistema lo divide in
            # canale e chat, e la risposta va alla chat del quaderno.
            chat_id=f"websocket:{plan.session_key}",
            content=content,
            session_key_override=plan.session_key,
            metadata={
                INJECTED_EVENT_META: PLAN_REMINDER_EVENT if reminder else PLAN_STEP_EVENT,
                PLAN_ID_META: plan.id,
                PLAN_STEP_META: step.id,
            },
        )

    async def _changed(self, plan: Plan) -> None:
        """``plan_changed`` alla WebUI: un accenno, il client rilegge ``plan.get``."""
        try:
            await self.bus.publish_outbound(
                OutboundMessage(
                    channel="websocket",
                    chat_id="webui",
                    content="",
                    metadata={
                        "_plan_changed": True,
                        "notebook": plan.notebook,
                        "plan_id": plan.id,
                    },
                )
            )
        except Exception as exc:  # noqa: BLE001 — un avviso perso non è un guasto
            logger.debug("plan_changed not delivered for {}: {}", plan.id, exc)

    async def _prune(self) -> None:
        try:
            removed = await asyncio.to_thread(
                self.store.prune,
                keep_closed=self.keep_closed,
                max_age_days=self.max_age_days,
            )
        except OSError:
            logger.opt(exception=True).warning("Plan pruning failed")
            return
        if removed:
            self.refresh_index()


# ── funzioni pure ──────────────────────────────────────────────────────────


def plan_view(plan: Plan) -> dict[str, Any]:
    """``plan.json`` più ``done``, ``total``, ``current_step_id``, ``can_rollback``,
    ``can_undo_rollback``: le letture che la scheda e il bollino fanno sempre."""
    counts = step_counts(plan)
    current = current_step(plan)
    photos = plan.photos
    data = plan.to_dict()
    data.update(
        {
            "done": counts["finished"],
            "total": counts["total"],
            "current_step_id": current.id if current is not None else None,
            "can_rollback": plan.status in ("done", "stopped")
            and bool(photos.get("start"))
            and bool(photos.get("end")),
            "can_undo_rollback": plan.status == "rolled_back"
            and bool(photos.get("before_rollback"))
            and bool(photos.get("after_rollback")),
        }
    )
    return data


def _find(plan: Plan, step_id: str) -> Step:
    for step in plan.steps:
        if step.id == step_id:
            return step
    raise PlanError(f"plan {plan.id} has no step {step_id}")


def _block(plan: Plan, step_id: str, question: str, reason: str) -> None:
    step = _find(plan, step_id)
    step.status = "blocked"
    step.extra.pop(_NUDGE, None)
    stamp = now_iso()
    apply_transition(plan, "blocked", at=stamp, step=step_id, reason=reason)
    plan.blocked = {"step": step_id, "question": question, "reason": reason, "at": stamp}


def _cut_ids(plan: Plan, cut: Iterable[Any]) -> set[str]:
    if cut is None:
        return set()
    if isinstance(cut, (str, bytes)) or not isinstance(cut, Iterable):
        raise PlanCommandError("bad_request", "cut must be a list of step ids")
    ids = set()
    known = {s.id for s in plan.steps}
    for value in cut:
        if not isinstance(value, str) or value not in known:
            raise PlanCommandError("bad_request", f"unknown step in cut: {str(value)[:40]!r}")
        ids.add(value)
    if len(ids) >= len(plan.steps):
        raise PlanCommandError("bad_request", "a plan needs at least one step left")
    return ids


def _keep_groups(keep: Iterable[Any]) -> set[str]:
    if keep is None:
        return set()
    if isinstance(keep, (str, bytes)) or not isinstance(keep, Iterable):
        raise PlanCommandError("bad_request", "keep must be a list of groups")
    groups = set()
    for value in keep:
        if value not in KEEPABLE_GROUPS:
            raise PlanCommandError("bad_request", f"unknown group: {str(value)[:40]!r}")
        groups.add(str(value))
    return groups


class _UnchangedError(Exception):
    """Il piano è già nello stato che il journal recuperato gli darebbe."""


def _header_counts(header: Mapping[str, Any]) -> dict[str, int]:
    raw = header.get("counts")
    counts = {k: 0 for k in ("restored", "removed", "already", "left", "kept", "excluded")}
    if isinstance(raw, Mapping):
        for key in counts:
            value = raw.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                counts[key] = value
    return counts


def _mark_rolled_back(
    plan: Plan,
    *,
    before: str | None,
    after: str | None,
    keep: Iterable[str],
    counts: Mapping[str, int],
    recovered: bool = False,
) -> None:
    """Il piano dopo un ritorno: foto C e R, ``rolled_back``. Lo stesso dal vivo e al boot."""
    plan.photos["before_rollback"] = before
    plan.photos["after_rollback"] = after
    plan.extra.pop(ROLLBACK_FAILED_KEY, None)
    details: dict[str, Any] = {"keep": sorted(keep), "counts": dict(counts)}
    if recovered:
        details["recovered"] = True
    apply_transition(plan, "rolled_back", **details)


def _mark_rollback_undone(
    plan: Plan, counts: Mapping[str, int], *, recovered: bool = False
) -> None:
    """Il piano dopo «Annulla il ritorno»: lo stato di prima, senza le foto C e R."""
    previous = _status_before_rollback(plan)
    plan.photos["before_rollback"] = None
    plan.photos["after_rollback"] = None
    plan.extra.pop(ROLLBACK_FAILED_KEY, None)
    details: dict[str, Any] = {"counts": dict(counts)}
    if recovered:
        details["recovered"] = True
    apply_transition(plan, previous, event="rollback_undone", **details)


def _rollback_note(plan: Plan, counts: Mapping[str, int]) -> str:
    return (
        f'The user rolled the notebook back to before the plan "{plan.title}": '
        f"{counts.get('restored', 0)} file(s) restored, {counts.get('removed', 0)} removed, "
        f"{counts.get('left', 0)} left as they were because they changed afterwards"
        + (f", {counts['kept']} kept on purpose" if counts.get("kept") else "")
        + "."
    )


def _undo_note(plan: Plan, counts: Mapping[str, int]) -> str:
    return (
        f'The user undid the rollback of the plan "{plan.title}": the notebook is '
        f"back as the plan left it ({counts.get('restored', 0)} file(s) restored, "
        f"{counts.get('removed', 0)} removed)."
    )


def _status_before_rollback(plan: Plan) -> str:
    for entry in reversed(plan.history):
        if entry.get("event") == "rolled_back" and entry.get("from") in ("done", "stopped"):
            return str(entry["from"])
    return "done"


def _shorten(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


__all__ = [
    "OPEN_STATUSES",
    "PLAN_ANSWER_META",
    "PLAN_ID_META",
    "PLAN_MODE_META",
    "PLAN_NOTES_META",
    "PLAN_PROPOSE_TOOL",
    "PLAN_REVISE_META",
    "PLAN_STEP_META",
    "PLAN_STEP_TOOLS",
    "PLAN_TOOL_NAMES",
    "PLAN_TURN_EVENTS",
    "ROLLBACK_FAILED_KEY",
    "PlanCommandError",
    "PlanDriver",
    "PlanHost",
    "notebook_of",
    "plan_view",
    "report_with_groups",
]
