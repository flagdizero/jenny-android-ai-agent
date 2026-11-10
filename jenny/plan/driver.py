"""Il driver dei piani a lista: l'unico che muove un piano.

**La lista la tiene il codice, non il modello.** Il driver sa qual è il passo
corrente; apre ogni passo con un turno iniettato nella chat del quaderno
(``agent/plan_step.md``: la lista con gli stati, il dettaglio del passo, le
regole), e il modello lo chiude con un tool (``plan_step_done``,
``plan_step_skip``, ``plan_blocked``). Fra un passo e l'altro c'è il codice:
conta i file del passo dai registri, aggiorna il piano e apre il passo
dopo, o si ferma se l'utente ha chiesto la pausa. Così Pausa e Ferma sono
deterministiche, Jenny non può saltare o riordinare i passi, e «cosa ha fatto
il passo 3» sono le righe dei registri scritte per quel passo.

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

La classe è composta per responsabilità (mixin, come ``AgentLoop``): qui restano
l'indice, i tool del modello, i ganci del loop, i comandi dell'utente e il boot;
la pianificazione sta in ``driver_drafting.py``, i resoconti tenuti degli
aiutanti in ``driver_held.py``, le bozze di un passo in ``driver_drafts.py``, il
controllo a macchina in ``driver_check.py``, il ritorno indietro in
``driver_rollback.py``; costanti e aiuti comuni in ``driver_common.py``.

Le scritture del piano passano dallo store (una transazione ciascuna); qui
sopra un ``asyncio.Lock`` per sessione serializza le transizioni,
perché una transizione è fatta di più passaggi (conti, scrittura, messaggio) che
non devono intrecciarsi con quelli di un'altra — un Ferma toccato mentre il passo
si chiude, per esempio.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.bus.events import InboundMessage, OutboundMessage
from jenny.plan import work
from jenny.plan.audit import (
    Audit,
    render_repair,
    summary_line,
)
from jenny.plan.driver_check import MachineCheckMixin
from jenny.plan.driver_common import (
    _APPROVED_MS,
    _AUDIT_BASELINE,
    _DRAFT_OUTCOMES,
    _EMPTY_AT_START,
    _GAPS_LISTED,
    _KIND_ROLLBACK,
    _KIND_UNDO,
    _MACHINE,
    _MAX_NOTE,
    _NUDGE,
    _PENDING_NOTE,
    _PENDING_OUTCOME,
    _PENDING_QUESTION,
    _PENDING_REASON,
    _REPORTING,
    _RESUME_TO,
    _RUNNING,
    OPEN_GAPS_PATH,
    PENDING_UNDO_KEY,
    PlanCommandError,
    PlanHost,
    _block,
    _cut,
    _DraftsUnreadableError,
    _find,
    _HeldResults,
    _Live,
    _machine_view,
    _step_now,
    place_of,
)
from jenny.plan.driver_drafting import DraftingMixin
from jenny.plan.driver_drafts import StepDraftsMixin
from jenny.plan.driver_held import HELD_WAKE_TEXT, HeldResultsMixin
from jenny.plan.driver_rollback import ROLLBACK_FAILED_KEY, RollbackMixin, card_report
from jenny.plan.facts import LostFacts
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
    PLAN_WAKE_META,
)
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
    names_research_gaps,
    next_pending_step,
    now_iso,
    order_warnings,
    step_counts,
    to_research_items,
    validate_proposal,
)
from jenny.plan.place import PlanPlace, place_from_id, place_of_session
from jenny.plan.store import PlanConflictError, PlanNotFoundError, PlanStore
from jenny.session.history_meta import (
    INJECTED_EVENT_META,
    PLAN_DONE_EVENT,
    PLAN_REMINDER_EVENT,
    PLAN_STEP_EVENT,
    PLAN_TURN_EVENTS,
)
from jenny.session.keys import PROJECT_SESSION_PREFIX, is_project_session_key

_NUDGE_SENT = "sent"
_NUDGE_USED = "used"

# I buchi riportati dagli aiutanti del passo sotto **To research**, nell'``extra``
# del passo: ``{lineage: [voci]}``, e vale l'ultimo resoconto di ogni lineage —
# un editor rimandato indietro con ``subagent_send`` che non ne riporta più li
# azzera. Il cancello di ``plan_step_done`` è uno solo per passo, come il
# promemoria: rifiuta una volta con i buchi aperti, la seconda chiamata passa.
_GAPS = "gaps"
_GAPS_GATE = "gaps_gate"
_GAPS_GATE_USED = "used"
# Il giro di ricerca che il cancello ha aperto in questo passo (1 o 2), per la
# scheda: «ricerca 1 di 2» sotto il passo in corso, invece di «da 47 min».
_RESEARCH_ROUND = "research_round"
# I lineage dei ricercatori che hanno riportato nel passo (per la scheda e i log).
_RESEARCH_LINEAGES = "research_lineages"
# **Il budget di ricerca del piano.** La ricerca dentro un piano è pianificata
# (i passi ``research``); quella reattiva — il giro che il cancello chiede quando
# gli aiutanti riportano buchi — ha un tetto per **piano**, non per passo. Sul
# Titan (26/10) i giri sono stati sei, da 15-18 minuti l'uno (ricercatori più
# pagine rimandate agli editor), su un piano di 8 passi: 70 dei 170 minuti. Due
# è un giro e una riserva. L'unica porta che spende il budget è il rifiuto del
# cancello in ``_close_step``; a budget finito il cancello rifiuta lo stesso una
# volta, ma per far scrivere «non documentato» nelle pagine, non per cercare.
# Il conto sta nell'``extra`` del piano.
_RESEARCH_ROUNDS = "research_rounds"
MAX_RESEARCH_ROUNDS = 2
# Il cancello dei fatti persi di un passo ``delete``: uno per passo, come quello dei buchi.
_LOST_GATE = "lost_gate"




# I passi che non aprono mai un giro di ricerca: chi cancella o controlla non ha
# pagine da riempire, e i «buchi» dei suoi aiutanti sono condizioni da sapere,
# non lacune della wiki (Titan, 26/10: tre ricercatori dopo la cancellazione);
# un passo di ricerca previsto è già la ricerca, e non ne chiede un'altra.
_NO_ROUND_KINDS = frozenset({"delete", "check", "research"})

# Per quanto un ``plan_propose`` dopo un annullamento riceve «fermati»: il turno
# che stava pianificando, non uno di domani.
_CANCELLED_GRACE_S = 600.0



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

#: Il risveglio unico di un passo (resoconti di aiutanti tenuti finché lavora
#: un fratello): dopo quanto dal primo resoconto tenuto il driver sveglia Jenny
#: comunque. È la garanzia contro un aiutante lento ma vivo, che il watchdog
#: non marca ``stalled``.
HELD_WAKE_AFTER_S = 600.0

# La domanda di `plan_blocked` va sulla scheda intera: oltre il tetto si
# rifiuta, mai si taglia. Fino al 26/10 era `[:1000]` muto, e sul Titan una
# seconda domanda era sparita dalla scheda senza che Jenny o l'utente lo
# sapessero. Il tetto non e' una regola di stile (quella sta nel tool): e' la
# guardia contro un testo anomalo, un file incollato nella domanda.
_MAX_QUESTION = 4000
_MAX_ANSWER = 8000











def slot_of(session_key: str | None) -> str | None:
    """L'id del posto dei piani di una sessione (``nb:<nome>``, ``personal``), o ``None``."""
    place = place_of_session(session_key)
    return place.id if place is not None else None




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




def _stamp_step(metadata: dict[str, Any], plan: Plan) -> None:
    """Il piano e il passo aperto nei metadati di un turno che lavora per il passo.

    Il turno della risposta a ``plan_blocked`` ne era senza: gli aiutanti nati
    lì (e dai turni dei loro resoconti) non avevano il passo, il driver scartava
    i loro resoconti e il freno di un giro per passo non scattava (Titan, 26/10).
    Un aiutante eredita queste chiavi dal contesto del turno
    (``subagent._plan_metadata``).
    """
    metadata[PLAN_ID_META] = plan.id
    step = _step_now(plan)
    if step is not None:
        metadata[PLAN_STEP_META] = step.id


def _closing(plan: Plan) -> list[Step]:
    """I passi chiusi dal modello ma non ancora contati.

    Il tool chiude il passo subito (la scheda lo vede fatto), i conti dei file
    li fa il driver a fine turno: il turno può scrivere ancora dopo la chiamata,
    e quelle scritture sono del passo.
    """
    return [
        s for s in plan.steps
        if s.status in ("done", "skipped") and s.started_at and "files" not in s.extra
    ]


def _counts(diff: Mapping[str, list[str]]) -> dict[str, int]:
    return {key: len(diff.get(key, ())) for key in ("added", "changed", "removed")}








class PlanDriver(
    DraftingMixin,
    HeldResultsMixin,
    RollbackMixin,
    StepDraftsMixin,
    MachineCheckMixin,
):
    """L'unico che muove i piani. Vedi il docstring del modulo."""

    def __init__(
        self,
        store: PlanStore,
        bus: Any,
        host: PlanHost,
        *,
        keep_closed: int = 5,
        max_age_days: int = 30,
        held_wake_after_s: float = HELD_WAKE_AFTER_S,
        personal_plans: bool = False,
    ) -> None:
        self.store = store
        # I piani nella chat personale: solo quando i file li scrivono gli
        # aiutanti (``orchestrator_mode``), che hanno ciascuno il suo registro.
        self.personal_plans = personal_plans
        self.bus = bus
        self.host = host
        self.keep_closed = keep_closed
        self.max_age_days = max_age_days
        # Il risveglio unico per passo: session_key → i resoconti tenuti. In
        # memoria e basta: dopo un riavvio gli aiutanti non ci sono più, e le
        # righe tenute le vede il primo turno della sessione.
        self.held_wake_after_s = held_wake_after_s
        self._held: dict[str, _HeldResults] = {}
        # Allo shutdown (``close_held_timers``) nessun timer nuovo si arma più.
        self._held_closed = False
        # I risvegli partiti dal watchdog (``on_helpers_stalled``): lo shutdown
        # li cancella insieme ai timer.
        self._wakes: set[asyncio.Task[Any]] = set()
        # Il watchdog degli aiutanti avvisa qui quando ne marca di fermi
        # (``LoopPlanHost.watch_stalls``); un ospite finto può non saperlo fare.
        watch: Any = getattr(host, "watch_stalls", None)
        if callable(watch):
            watch(self.on_helpers_stalled)
        # E quando un aiutante parte per un passo: il suo registro è lavoro del
        # passo (``Step.helpers``), e il ritorno del piano lo toglie.
        watch_journals: Any = getattr(host, "watch_helper_journals", None)
        if callable(watch_journals):
            watch_journals(self.on_helper_journal)
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
        # notebook → la sua pianificazione in corso: la copia in memoria di
        # quella dello store (che è la fonte), per le domande sincrone di
        # ogni turno — il filtro dei tool la legge a ogni lettura del registro.
        self._drafting: dict[str, dict[str, Any]] = {}
        # Il loop su cui rientrano gli avvisi delle bozze, che arrivano anche
        # da un thread (:meth:`watch_drafts`).
        self._loop: asyncio.AbstractEventLoop | None = None
        self._unwatch: Callable[[], None] | None = None
        # I task nati dagli avvisi delle bozze: tenuti qui, o il loop li tiene
        # solo per riferimento debole e un task può sparire a metà.
        self._tasks: set[asyncio.Task[Any]] = set()
        self._drafting_lock = asyncio.Lock()
        # notebook → quando l'utente ha annullato la sua pianificazione
        # (``time.monotonic``): un ``plan_propose`` che arriva poco dopo, dal
        # turno che stava pianificando, riceve «fermati», non «nessuno pianifica».
        self._cancelled_drafting: dict[str, float] = {}

    # ── posti ──────────────────────────────────────────────────────────────

    def place_of(self, session_key: str | None) -> PlanPlace | None:
        """Il posto dei piani di una sessione, se lì si può pianificare."""
        place = place_of_session(session_key)
        if place is None or (place.notebook is None and not self.personal_plans):
            return None
        return place

    def _slot(self, session_key: str | None) -> str | None:
        place = self.place_of(session_key)
        return place.id if place is not None else None

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
            if plan.status not in _RUNNING:
                self._disarm_held(plan.session_key)
            else:
                # Tornato in corsa (la risposta a un blocco, una ripresa) con
                # resoconti ancora tenuti: il timer spento riparte dal primo.
                self._rearm_held(plan.session_key)
            return
        live = self._index.get(plan.session_key)
        if live is not None and live.plan_id == plan.id:
            self._index.pop(plan.session_key, None)
            self._disarm_held(plan.session_key)

    def refresh_index(self) -> None:
        """Ricostruisce l'indice dal disco (boot, rinomino, cancellazione di un quaderno)."""
        self._index = {}
        self._held_roots = None
        for plan in reversed(self.store.list_all()):
            if plan.is_open:
                self._remember(plan)
        # Un rinomino o una cancellazione cambiano le chiavi: un risveglio
        # tenuto per una sessione che non ha più un piano in corsa non parte, e
        # quello di una chiave senza più piano si dimentica (la sua chat è
        # altrove, o non c'è più).
        for session_key in list(self._held):
            if session_key not in self._index:
                self._cancel_held_timer(self._held.pop(session_key))
            elif not self.plan_running(session_key):
                self._disarm_held(session_key)
        try:
            self._drafting = self.store.drafting_all()
        except OSError:
            logger.opt(exception=True).warning("Plan drafting state not readable")
            self._drafting = {}

    # ── domande del loop (sincrone, dall'indice) ───────────────────────────

    def plan_open(self, session_key: str) -> bool:
        """La sessione ha un piano aperto (proposto, in corso, fermo)."""
        return session_key in self._index

    def plan_running(self, session_key: str) -> bool:
        """La sessione ha un piano che gira: il turno non ha tetto sulla chiamata al modello."""
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
            live.notebook for live in self._index.values()
            if live.notebook and live.status in HELD_STATUSES
        }

    def holds_notebook(self, notebook: str) -> bool:
        if not notebook:
            return False
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

    # ── pianificazione in corso (``drafting``) ──────────────────────────────
    #
    # Il tasto Piano apre una **pianificazione**, non un turno: Jenny guarda il
    # quaderno, fa le sue domande, l'utente risponde, e solo allora c'è la
    # proposta. Finché dura, ogni turno dell'utente in quella chat è un turno di
    # pianificazione (sola lettura, ``plan_propose`` visibile) anche se il
    # client non lo marca: lo stato è del server. Finisce con la proposta, con
    # l'annullamento (``plan.cancel_drafting``, il tasto spento, ``/stop``) o
    # dopo :data:`~jenny.plan.model.DRAFTING_TTL_HOURS` ore senza turni.

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
        lines: list[str] = []
        place = self.place_of(session_key)
        if place is not None:
            live = self._index.get(session_key)
            if live is not None:
                try:
                    lines = self._notebook_lines(self.store.load(live.plan_id))
                except PlanError:
                    lines = []
            if place.notebook:
                return lines
        elif is_project_session_key(session_key):
            return []
        # Nella chat personale: oltre al suo piano, una riga per ogni piano di
        # quaderno aperto.
        for live in sorted(self._index.values(), key=lambda v: v.notebook):
            if not live.notebook:
                continue
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
        elif plan.status == "paused" and not plan.notebook:
            lines.append(
                "The user paused the plan until they tap Resume. Answer them, but do not "
                "work on the plan's steps."
            )
        elif plan.status == "paused":
            lines.append(
                "The user paused the plan: this conversation is read-only until they tap "
                "Resume. Answer them, but do not work on the plan's steps."
            )
        elif plan.status == "blocked":
            blocked = plan.blocked or {}
            if blocked.get("kind") == "drafts":
                targets = ", ".join(str(d.get("target")) for d in blocked.get("drafts") or ())
                lines.append(
                    "The plan waits for the user to install or discard the change(s) the step "
                    f"left as drafts ({targets}). The step closes by itself when they decide: "
                    "do not redo it."
                )
            else:
                question = blocked.get("question") or ""
                where = "" if plan.notebook else " (they answer on the plan card)"
                lines.append(
                    f"The plan waits for the user's answer{where} to: {question}".rstrip())
        return lines

    def planning_block(
        self, session_key: str, *, revise: str | None = None, followup: bool = False
    ) -> str:
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
                        {
                            "id": s.id,
                            "title": s.title,
                            "detail": _shorten(s.detail, 300),
                            "covers": list(s.covers),
                        }
                        for s in plan.steps
                    ],
                    # La copertura com'era, con gli id: una revisione deve
                    # rifare i ``covers`` contro gli id nuovi (per posizione),
                    # e senza vederla il modello li ricopierebbe alla cieca.
                    "coverage": [
                        {
                            "id": c.get("id", ""),
                            "item": _shorten(c.get("item", ""), 80),
                            "strategy": c.get("strategy", ""),
                        }
                        for c in plan.coverage
                    ],
                }
        drafting = self.drafting_for(session_key)
        reads = drafting.get("reads") if drafting is not None else 0
        place = self.place_of(session_key)
        notebook = place.notebook if place is not None else None
        return render_template(
            "agent/plan_mode.md",
            strip=True,
            current=current,
            revise=bool(revise) and current is not None,
            followup=followup,
            reads=reads if isinstance(reads, int) else 0,
            notebook=notebook,
            where=f"in {place.here}" if place is not None else "in this notebook",
        )

    # ── tool del modello ───────────────────────────────────────────────────

    async def propose(
        self,
        session_key: str | None,
        title: Any,
        steps: Any,
        notes: str | None = None,
        *,
        coverage: Any = None,
        assumptions: Any = None,
    ) -> str:
        """``plan_propose``: scrive una proposta (o la versione dopo). Testo per il modello.

        Si propone **solo dentro una pianificazione** (il tasto Piano), e solo
        dopo aver guardato il quaderno: una pianificazione che non ha letto
        niente si rifiuta. È un pavimento e non una garanzia — una lettura sola
        lo supera — ma è il pavimento che la prova del 23/10 non aveva: zero
        letture prima della proposta, e «raw/ has all the material» scritto
        come un fatto. Il resto lo fa la copertura, obbligatoria.
        """
        slot = self._slot(session_key)
        if slot is None or session_key is None:
            return "Error: a plan can only be proposed inside a notebook chat or the personal chat."
        place = self.place_of(session_key)
        here = place.here if place is not None else "this notebook"
        subject = place.subject if place is not None else "the notebook"
        held = self.held_status(session_key)
        if held is not None:
            return (
                f"Not now: {here} already has a plan that has started ({held}). "
                "A new one can be proposed when that plan is done or stopped."
            )
        drafting = self.drafting_for(session_key)
        if drafting is None:
            cancelled_at = self._cancelled_drafting.get(slot)
            if cancelled_at is not None and time.monotonic() - cancelled_at < _CANCELLED_GRACE_S:
                return (
                    "Stop: the user cancelled this plan while you were preparing it. Do not "
                    "do the work, do not start helpers and do not write anything: say in one "
                    "short sentence that the plan is cancelled, and wait for the user."
                )
            return (
                f"Error: nobody is preparing a plan in {here} now: the user starts it "
                "with the Plan button. Do not propose a plan; answer the user."
            )
        if not drafting.get("reads") and not self._revises_a_plan_of(slot, drafting):
            return (
                f"Not yet: look at {subject} first: read the pages and sources this job "
                "depends on (list_dir, read_file, grep), so the plan says what is there and "
                "what is missing. Then call plan_propose."
            )
        try:
            proposal = validate_proposal(
                title, steps, coverage, assumptions,
                require_check=place is None or place.traits.machine_check,
            )
        except PlanValidationError as exc:
            return f"Error: {exc} Fix the plan and call plan_propose again."
        clean_notes = (
            _cut(notes.strip(), 2000, "plan notes")
            if isinstance(notes, str) and notes.strip() else None
        )
        async with self._lock(session_key):
            try:
                plan = await asyncio.to_thread(
                    self.store.create, slot, proposal, notes=clean_notes
                )
            except PlanConflictError as exc:
                return (
                    f"Not now: {here} already has a plan that has started ({exc.status}). "
                    "A new one can be proposed when that plan is done or stopped."
                )
            self._remember(plan)
        # La proposta chiude la pianificazione: il prossimo messaggio è una chat
        # normale, o un Rivedi che ne apre un'altra. L'avviso è quello del piano.
        await self._drop_drafting(slot, "proposed", announce=False)
        await self._changed(plan)
        await self._prune()
        version = f" (version {plan.version})" if plan.version > 1 else ""
        text = (
            f"The plan{version} is now on a card in front of the user, with Approve and "
            "Review. Stop here: do not start any step and do not repeat the list in your "
            "reply. Say in one or two sentences what it does, and wait: the user approves "
            "it, or asks for changes."
        )
        # Un avviso, non un rifiuto: il piano è sulla scheda comunque, e se
        # l'ordine è sbagliato il modello lo ripropone (``order_warnings``).
        warnings = order_warnings(proposal.steps)
        if warnings:
            text += "\n\nNote on the order: " + " ".join(warnings)
        return text

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
        helpers = self._helpers_of_step(session_key, live.plan_id, live_step=None)
        if helpers is None:
            # Il registro degli aiutanti non ha risposto: chiudere «al buio»
            # lascerebbe le loro scritture fuori dai conti del passo.
            return (
                "Not yet: I cannot tell whether helpers of this step are still working (the "
                "helper registry did not answer). Wait a moment, then call `plan_step_done` again."
            )
        if helpers:
            # Un passo chiuso con gli aiutanti ancora al lavoro: le loro
            # scritture arriverebbero dopo i conti di questo, che
            # mentirebbero. Non è un errore (non «riprova diversamente»): è presto.
            return (
                f"Not yet: {len(helpers)} helper(s) you started for this step are still "
                "working. Wait for their results, then close the step."
            )
        text = _cut(note.strip(), _MAX_NOTE, "step note") if isinstance(note, str) else ""
        async with self._lock(session_key):
            try:
                plan = await asyncio.to_thread(self.store.load, live.plan_id)
            except PlanError:
                return "Error: there is no plan step open in this conversation."
            step = _step_now(plan)
            if plan.status not in _RUNNING or step is None:
                return "Error: there is no plan step open in this conversation."
            step_id = step.id
            if (
                outcome == "done" and step.kind == "delete"
                and step.extra.get(_LOST_GATE) != _GAPS_GATE_USED and step.started_at
            ):
                # Chi cancella risponde dei fatti: quelli che erano in una pagina
                # all'apertura del passo e adesso in nessuna. Una volta sola,
                # come il cancello dei buchi: sul Titan (26/10) questo controllo
                # l'hanno fatto quattro aiutanti in 17 minuti.
                lost = await asyncio.to_thread(self._lost_since, plan, step)
                if lost is not None and lost.total:

                    def _lost_gate(p: Plan) -> None:
                        _find(p, step_id).extra[_LOST_GATE] = _GAPS_GATE_USED

                    await asyncio.to_thread(self.store.update, plan.id, _lost_gate)
                    logger.info(
                        "Plan {}: step {} close refused once, {} fact(s) lost",
                        plan.id, step_id, lost.total,
                    )
                    return _lost_refusal(lost)
            gaps = _open_gaps(step)
            gate_used = step.extra.get(_GAPS_GATE) == _GAPS_GATE_USED
            listed = 0
            if outcome == "done" and gaps:
                if gate_used or step.kind in _NO_ROUND_KINDS:
                    # Il cancello ha già rifiutato una volta (il giro c'è stato,
                    # o il budget era finito), o il passo non apre giri: quel
                    # che resta va nella lista del quaderno, non al passo dopo.
                    listed = await self._list_gaps(plan, step, gaps)
                else:
                    # Un aiutante ha riportato buchi e il passo si chiude come se
                    # niente fosse: la prova 3 li ha lasciati «non documentati»
                    # nelle pagine. Una volta sola, come il promemoria: la
                    # seconda chiamata passa. Cosa chiede il rifiuto lo dice il
                    # budget del piano: un giro di ricerca finché ce n'è, poi
                    # «scrivi non documentato».
                    rounds = _research_rounds(plan)
                    spend = rounds < MAX_RESEARCH_ROUNDS

                    def _gate(p: Plan) -> None:
                        s = _find(p, step_id)
                        s.extra[_GAPS_GATE] = _GAPS_GATE_USED
                        if spend:
                            p.extra[_RESEARCH_ROUNDS] = rounds + 1
                            s.extra[_RESEARCH_ROUND] = rounds + 1

                    await asyncio.to_thread(self.store.update, plan.id, _gate)
                    logger.info(
                        "Plan {}: step {} close refused once, gaps open ({})",
                        plan.id, step_id,
                        f"research round {rounds + 1} of {MAX_RESEARCH_ROUNDS}"
                        if spend else "research budget used",
                    )
                    # «ricerca 1 di 2» sulla scheda: il giro è appena aperto.
                    await self._changed(plan)
                    return (
                        _gaps_refusal(gaps, round_=rounds + 1)
                        if spend else _gaps_budget_used(gaps)
                    )
            refusal = await self._recheck(plan, step, outcome)
            if refusal is not None:
                return refusal
            plan = await asyncio.to_thread(self.store.load, plan.id)
            try:
                drafts = await asyncio.to_thread(self._plan_drafts, plan)
            except _DraftsUnreadableError as exc:
                return (
                    f"Not yet: the pending drafts could not be read ({exc}), and a step does "
                    "not close with drafts waiting. Try `plan_step_done` again in a moment."
                )
            if drafts:
                # Un passo che lascia bozze non è fatto: il file non è scritto, e
                # i passi dopo lavorerebbero sul vecchio (la prova del 23/10:
                # «Nuovo AGENTS.md» con AGENTS.md in bozza). Il passo aspetta
                # l'utente, e si chiude da solo quando ha deciso, con questo
                # esito e questa nota. Poi si riguarda subito: una bozza decisa
                # fra la lista e il blocco non deve lasciare il passo fermo.
                plan = await self._hold_on_drafts(plan, step_id, drafts, outcome=outcome, note=text)
                settled = await self._settle_drafts_locked(
                    session_key, plan.id, None, advance=False
                )
                plan = settled or plan
            else:

                def _close(p: Plan) -> None:
                    s = _find(p, step_id)
                    s.status = outcome
                    s.ended_at = now_iso()
                    s.note = text or None
                    s.extra.pop(_NUDGE, None)

                plan = await asyncio.to_thread(self.store.update, plan.id, _close)
                self._remember(plan)
        await self._changed(plan)
        if plan.status == "blocked":
            return (
                "Not closed yet: the change is waiting for the user's approval; the step will "
                "close when they decide. It is on the plan card: end your turn now, with at "
                "most one short sentence, and do not redo the change."
            )
        last = next_pending_step(plan) is None
        n = _position(plan, step_id)
        verb = "closed" if outcome == "done" else "skipped"
        tail = (
            f" {listed} gap(s) of this step went to `{OPEN_GAPS_PATH}`: tell the user in one "
            "sentence what stays missing."
        ) if listed else ""
        # Corto apposta, anche l'ultimo: fra la chiamata e la fine del turno la
        # scheda è «in corso» senza un passo, e il tempo è quello che il modello
        # passa a scrivere. Il riepilogo del piano è la scheda stessa.
        if last:
            left = _left_after_check(plan, step_id)
            return (
                f"Step {n} {verb}. It was the last step: end your turn now with one or two "
                "short sentences for the user; the plan card already lists what was done."
                + (f" The code's check still finds {left} thing(s) to fix: say so plainly."
                   if left else "")
                + tail
            )
        return (
            f"Step {n} {verb}. The code takes it from here: end your turn now, with at most "
            "one short sentence for the user. Do not start the next step: it opens by itself."
            + tail
        )

    async def blocked(self, session_key: str | None, question: Any) -> str:
        """``plan_blocked``: il piano si ferma su una domanda per l'utente."""
        if not isinstance(question, str) or not question.strip():
            return "Error: `question` must say, in one clear sentence, what you need from the user."
        if not session_key or (live := self._index.get(session_key)) is None:
            return "Error: there is no plan step open in this conversation."
        text = question.strip()
        if len(text) > _MAX_QUESTION:
            return (
                f"Error: the question is {len(text)} characters, the limit is {_MAX_QUESTION}. "
                "Nothing was stored and the plan is still on this step. Put the report in your "
                "reply to the user and keep in `question` only what they must decide, numbered "
                "if more than one."
            )
        async with self._lock(session_key):
            try:
                plan = await asyncio.to_thread(self.store.load, live.plan_id)
            except PlanError:
                return "Error: there is no plan step open in this conversation."
            step = _step_now(plan)
            if plan.status not in _RUNNING or step is None:
                return "Error: there is no plan step open in this conversation."
            step_id = step.id
            try:
                drafts = await asyncio.to_thread(self._plan_drafts, plan)
            except _DraftsUnreadableError as exc:
                return (
                    f"Not yet: the pending drafts could not be read ({exc}). Try "
                    "`plan_blocked` again in a moment."
                )
            if drafts:
                # Prima le bozze del passo, poi la domanda: una domanda sulla
                # scheda con le bozze fuori dal piano è il difetto di partenza.
                plan = await self._hold_on_drafts(plan, step_id, drafts, question=text)
                plan = (
                    await self._settle_drafts_locked(session_key, plan.id, None, advance=False)
                    or plan
                )
            else:
                plan = await asyncio.to_thread(
                    self.store.update, plan.id, lambda p: _block(p, step_id, text, "question")
                )
                self._remember(plan)
        await self._changed(plan)
        return (
            "The plan now waits for the user; your question is on the plan card, whole. End "
            "your turn with a short reply: what you found, and that the plan waits for their "
            "answer. Do nothing else on the plan until they answer."
        )

    # ── risveglio unico per passo ──────────────────────────────────────────
    #
    # Ogni resoconto di aiutante apriva un turno di Jenny: un passo con N
    # aiutanti faceva fino a N turni, e ognuno si portava dietro tutta la
    # conversazione (prova 2: 49 turni). Ora un resoconto riuscito, quando un
    # fratello dello stesso passo lavora ancora, si scrive in storia senza turno
    # (``AgentLoop._hold_subagent_result``); il turno parte con l'ultimo. La rete
    # di sicurezza è qui: fratelli rimasti tutti fermi, o un tetto di tempo.

    # ── hook del loop ──────────────────────────────────────────────────────

    def _revises_a_plan_of(self, slot: str, drafting: Mapping[str, Any]) -> bool:
        """La pianificazione rivede un piano di questo quaderno, che ha già guardato.

        Il cancello delle letture esiste perché una proposta nasca guardando il
        quaderno; una revisione riparte da zero letture, ma la versione che rivede
        quel quaderno l'ha già letto. Nella prova 3 il primo ``plan_propose`` di
        ogni revisione veniva rifiutato per «non hai guardato».
        """
        revise = drafting.get("revise")
        if not isinstance(revise, str) or not revise:
            return False
        try:
            return self.store.load(revise).place == slot
        except PlanError:
            return False

    async def note_step_report(
        self,
        session_key: str,
        task_id: Any,
        lineage: Any,
        content: Any,
        *,
        plan_id: Any = None,
        plan_step: Any = None,
        agent_type: Any = None,
        items: Any = None,
    ) -> bool:
        """Il resoconto di un aiutante del passo aperto: registra i suoi buchi **To research**.

        *items* sono le voci già lette dal manager sul resoconto nudo
        (``subagent_to_research``); senza, si legge *content*, che è l'annuncio
        intero e nomina la lista anche nel testo del template.

        Lo chiama il loop per ogni resoconto che arriva, tenuto o no, e anche a
        metà turno. Conta solo con un piano in corsa e un resoconto del passo
        aperto: il passo lo dicono i metadati del resoconto (``plan_id``,
        ``plan_step``, gli stessi del lancio) o, se mancano, il registro degli
        aiutanti. La chiave è il lineage, che resta lo stesso quando l'aiutante
        è ripreso con ``subagent_send``; senza, il ``task_id``. L'ultimo
        resoconto del lineage sostituisce quello prima: senza buchi, li toglie.
        ``True`` se i buchi del passo sono cambiati.

        Il resoconto di un ``researcher`` (*agent_type*) non porta buchi: i suoi
        **To research** sono il suo esito. Il suo lineage si registra
        (``_RESEARCH_LINEAGES``) per la scheda e i log.
        """
        live = self._index.get(session_key)
        if not isinstance(content, str) or live is None or live.status not in _REPORTING:
            return False
        key = next((v for v in (lineage, task_id) if isinstance(v, str) and v), None)
        if key is None:
            return False
        if plan_id is None and plan_step is None and isinstance(task_id, str) and task_id:
            group = self._helper_group(task_id)
            if group is not None:
                plan_id, plan_step = group
        if not plan_id or plan_id != live.plan_id or not plan_step:
            # La spia: un resoconto senza il passo in un piano vivo è un buco
            # nei metadati (sul Titan si è letto solo nei record, a mano).
            logger.warning(
                "Plan {}: report of helper {} ignored, it carries plan={} step={}",
                live.plan_id, key, plan_id, plan_step,
            )
            return False
        is_researcher = agent_type == "researcher"
        # I **To research** di un ricercatore sono il suo esito (cercato, non
        # trovato), non buchi del passo: contarli faceva un secondo giro dentro
        # ogni passo di ricerca.
        if is_researcher:
            items = []
        elif isinstance(items, list):
            items = [i for i in items if isinstance(i, str) and i]
        else:
            items = to_research_items(content)
        if not is_researcher and not items and names_research_gaps(content):
            # La spia che mancava: un resoconto con una lista **To research**
            # scritta e nessuna voce letta usciva di qui in silenzio, e le
            # lacune sparivano senza traccia (s22, 26/10 sera: 4 su 5).
            logger.warning(
                "Plan {}: report of helper {} names To research but no item was read: "
                "the list's shape is not one the parser knows",
                live.plan_id, key,
            )
        async with self._lock(session_key):
            try:
                plan = await asyncio.to_thread(self.store.load, live.plan_id)
            except PlanError:
                return False
            step = next(
                (s for s in plan.steps if s.id == plan_step and s.status in ("now", "blocked")),
                None,
            )
            if plan.status not in _REPORTING or step is None:
                now = _step_now(plan)
                logger.warning(
                    "Plan {}: report of helper {} for step {} ignored, the open step is {}",
                    live.plan_id, key, plan_step, now.id if now is not None else None,
                )
                return False
            gaps = _open_gaps(step)
            researchers = [
                r for r in step.extra.get(_RESEARCH_LINEAGES) or () if isinstance(r, str)
            ]
            if is_researcher and key not in researchers:
                researchers.append(key)
            new_researcher = is_researcher and key not in (
                step.extra.get(_RESEARCH_LINEAGES) or ()
            )
            if items:
                gaps[key] = items
            elif key in gaps:
                del gaps[key]
            elif not new_researcher:
                return False
            step_id = step.id

            def _apply(p: Plan) -> None:
                s = _find(p, step_id)
                if gaps:
                    s.extra[_GAPS] = gaps
                else:
                    s.extra.pop(_GAPS, None)
                if is_researcher:
                    s.extra[_RESEARCH_LINEAGES] = researchers

            await asyncio.to_thread(self.store.update, plan.id, _apply)
        logger.info(
            "Plan {}: step {} has {} gap(s) from helper {}", live.plan_id, step_id, len(items), key
        )
        # La scheda legge `gaps_open` da `plan.get`, e rilegge solo a un
        # `plan_changed`: senza questo, «4 lacune aperte» non si è mai visto
        # (s22, 26/10 sera: la riga diceva «appena cominciato» per tutto il passo).
        await self._changed(plan)
        return True

    async def before_user_turn(
        self, session_key: str, metadata: dict[str, Any], *, channel: str | None = None,
    ) -> None:
        """Prima di un turno dell'utente nella chat di un quaderno.

        - piano in **pausa** → il turno è in sola lettura: si parla con Jenny,
          ma né lei né l'utente scrivono nel quaderno fuori da un passo;
        - piano **bloccato** → il messaggio è la risposta: il piano riparte e
          il passo continua in questo stesso turno. Non se aspetta le bozze del
          passo: quelle si decidono sulla scheda, e il messaggio è una chat in
          sola lettura come in pausa;
        - **pianificazione** in corso → il turno è di pianificazione
          (:meth:`_settle_drafting`).
        """
        self._bind_loop()
        await self._settle_drafting(session_key, metadata, channel=channel)
        live = self._index.get(session_key)
        if live is not None and live.status == "blocked":
            await self.recheck_drafts(live.plan_id)
            live = self._index.get(session_key)
        if live is None or metadata.get(PLAN_ANSWER_META):
            return
        place = self.place_of(session_key)
        if place is None or not place.traits.answer_by_chat:
            # Nella chat personale un messaggio non è la risposta (si risponde
            # dalla scheda) e non c'è niente da tenere in sola lettura: è chat.
            return
        if live.status == "paused" or (
            live.status == "blocked" and self._blocked_kind(live.plan_id) == "drafts"
        ):
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
            _stamp_step(metadata, plan)
            await self._changed(plan)

    def awaits_answer(self, session_key: str) -> bool:
        """Il piano della sessione è fermo su una domanda a cui si risponde in chat."""
        live = self._index.get(session_key)
        place = self.place_of(session_key)
        return (
            live is not None
            and place is not None and place.traits.answer_by_chat
            and live.status == "blocked"
            and self._blocked_kind(live.plan_id) == "question"
        )

    async def on_turn_end(
        self,
        session_key: str,
        metadata: Mapping[str, Any] | None,
        *,
        pending: bool = False,
        stop_reason: str = "",
    ) -> None:
        """A fine turno nella chat di un quaderno: il passo è chiuso? va avanti.

        - passo chiuso → i suoi conti dai registri, poi il passo seguente, o la
          pausa chiesta, o la fine;
        - passo aperto con aiutanti in volo → niente: il loro risultato aprirà
          un turno, e quel turno continua lo stesso passo;
        - passo aperto, nessun aiutante, Jenny ha finito senza chiuderlo →
          **un** promemoria; se si ferma di nuovo, il passo è bloccato con un
          motivo scritto dal codice.

        *pending* dice che la sessione ha già un messaggio in coda (un risultato
        arrivato a fine turno): sarà un turno suo, e il giudizio aspetta quello.

        *stop_reason* è come è finito il giro del modello: con
        ``provider_refused`` il passo aperto si blocca subito, senza promemoria
        (:meth:`_judge_open_step`).
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
            closing = _closing(plan)
            if closing:
                drafts = await asyncio.to_thread(self._plan_drafts, plan)
                if drafts:
                    # Chiuso col tool, ma il turno (o un aiutante lanciato dopo
                    # la chiamata) ha lasciato bozze: stessa regola di
                    # ``plan_step_done``, il passo aspetta l'utente.
                    step = closing[-1]
                    plan = await self._hold_on_drafts(
                        plan, step.id, drafts, outcome=step.status, note=step.note or ""
                    )
                    plan = (
                        await self._settle_drafts_locked(session_key, plan.id, None, advance=True)
                        or plan
                    )
                    await self._changed(plan)
                    return
                plan = await self._settle_closed(plan)
                await self._advance(plan)
                return
            step = _step_now(plan)
            if step is None:
                # In corsa senza un passo aperto: un'apertura caduta a metà.
                await self._advance(plan)
                return
            await self._judge_open_step(
                plan, step, meta, pending=pending, refused=stop_reason == "provider_refused"
            )

    async def _judge_open_step(
        self,
        plan: Plan,
        step: Step,
        meta: Mapping[str, Any],
        *,
        pending: bool,
        refused: bool = False,
    ) -> None:
        step_id = step.id
        nudge = step.extra.get(_NUDGE)
        reminder_turn = (
            meta.get(INJECTED_EVENT_META) == PLAN_REMINDER_EVENT
            and meta.get(PLAN_STEP_META) == step_id
        )
        if reminder_turn:
            nudge = _NUDGE_USED
        helpers = self._helpers_of_step(plan.session_key, plan.id, live_step=step_id)
        if helpers is None:
            # Il registro non ha risposto: né promemoria né blocco al buio. Il
            # prossimo turno giudicherà di nuovo.
            logger.warning("Plan {}: helpers of step {} unknown, not judged", plan.id, step_id)
            return
        if helpers:
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
        if refused:
            # Il provider ha rifiutato di rispondere (``content_filter``), e il
            # runner ha già speso l'unico tentativo che cambiava il contesto
            # (``_recover_provider_refusal``). Il promemoria rifarebbe la stessa
            # richiesta e prenderebbe lo stesso no — sul
            # telefono è andata così, e il blocco arrivava come «stallo», con
            # una domanda che non diceva cosa era successo. Dopo i due controlli
            # di sopra: un aiutante in volo o un messaggio in coda portano un
            # contesto nuovo, e il loro turno giudicherà di nuovo.
            await self._block_open_step(plan, step_id, "refused")
            return
        if nudge == _NUDGE_USED:
            await self._block_open_step(plan, step_id, "stalled")
            return
        await self._set_nudge(plan, step_id, _NUDGE_SENT)
        await self.bus.publish_inbound(self._step_message(plan, step, reminder=True))
        logger.info("Plan {}: one reminder for step {}", plan.id, step_id)

    async def _block_open_step(self, plan: Plan, step_id: str, reason: str) -> None:
        """Blocca il passo aperto con la domanda ``plan.<reason>`` (col lock preso).

        *reason* è ``stalled`` (fermo dopo il promemoria) o ``refused`` (il
        provider non ha risposto). Una domanda come quella di ``plan_blocked``:
        la risposta dell'utente fa ripartire il passo (:meth:`answer`). Se il
        passo ha lasciato bozze, la domanda aspetta dietro di loro.
        """
        from jenny.runtime.user_text import say

        question = say(f"plan.{reason}")
        drafts = await asyncio.to_thread(self._drafts_or_empty, plan)
        if drafts:
            plan = await self._hold_on_drafts(
                plan, step_id, drafts, question=question, reason=reason
            )
            plan = await self._settle_drafts_locked(plan.session_key, plan.id, None) or plan
        else:
            plan = await asyncio.to_thread(
                self.store.update,
                plan.id,
                lambda p: _block(p, step_id, question, reason),
            )
            self._remember(plan)
        logger.info("Plan {} blocked: step {} {}", plan.id, step_id, {
            "refused": "refused by the provider",
            "advance_failed": "the code could not move the plan on",
        }.get(reason, "stalled after the reminder"))
        await self._changed(plan)

    async def block_after_failure(self, session_key: str, error: str) -> bool:
        """Il gancio di fine turno è saltato: il passo aperto si ferma con una domanda.

        Fino al 26/10 l'eccezione finiva nel log e basta: piano «in corsa»,
        passo chiuso o senza turno che lo riprenda, composer spento — l'utente
        poteva solo fermare. Ora la scheda dice che il codice non è riuscito ad
        andare avanti, e la risposta dell'utente rifà il passo (idempotente).
        ``True`` se c'era un passo da fermare.
        """
        live = self._index.get(session_key)
        if live is None or live.status not in _RUNNING:
            return False
        async with self._lock(session_key):
            try:
                plan = await asyncio.to_thread(self.store.load, live.plan_id)
            except PlanError:
                return False
            step = _step_now(plan) or next(iter(_closing(plan)), None)
            if plan.status not in _RUNNING or step is None:
                return False
            if step.status != "now":
                # Il passo era già chiuso dal modello: lo si riapre perché la
                # domanda stia su un passo vivo, e la risposta lo rifaccia.
                reopen_id = step.id

                def _reopen(p: Plan) -> None:
                    s = _find(p, reopen_id)
                    s.status = "now"
                    s.ended_at = None

                plan = await asyncio.to_thread(self.store.update, plan.id, _reopen)
            logger.error("Plan {}: could not move on after step {}: {}", plan.id, step.id, error)
            await self._block_open_step(plan, step.id, "advance_failed")
        return True

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
        """Approva: ``running``, primo passo. ``version`` come ``target_sha256``."""
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
            self._require_place(plan)
            baseline, empty = await asyncio.to_thread(self._audit_baseline, plan)

            def _approve(p: Plan) -> None:
                if p.status != "proposed":
                    raise PlanCommandError("bad_state", f"the plan is {p.status}, not proposed")
                apply_transition(p, "running", event="approved", version=p.version)
                # Al millisecondo, come ``created_ms`` delle bozze: la storia è al
                # secondo, e una bozza di un attimo prima dell'Approva non è del
                # piano (:meth:`_step_drafts`).
                p.extra[_APPROVED_MS] = int(time.time() * 1000)
                for s in p.steps:
                    if s.id in cut_ids:
                        s.status = "cut"
                p.extra[_AUDIT_BASELINE] = baseline
                p.extra[_EMPTY_AT_START] = empty

            plan = await asyncio.to_thread(self.store.update, plan_id, _approve)
            self._remember(plan)
            logger.info("Plan {} approved (v{}, {} cut)", plan_id, plan.version, len(cut_ids))
            # Un Rivedi aperto e poi lasciato lì (Approva sulla versione che c'è):
            # la pianificazione è finita, o i turni dopo resterebbero «di
            # pianificazione» sopra un piano che gira.
            await self._drop_drafting(plan.place, "approved", announce=False)
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
            if plan.status == "blocked" and (plan.blocked or {}).get("kind") == "drafts":
                # Fermo sulle bozze: la Pausa si ricorda, e arriva quando le bozze
                # sono decise e il passo si chiude (``_settle_drafts_locked``).
                return await self._remember_pause(plan, "pausing")
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
            if plan.status == "blocked" and _resume_to(plan) == "pausing":
                return await self._remember_pause(plan, None)
            if plan.status != "paused":
                raise PlanCommandError("bad_state", f"the plan is {plan.status}, not paused")
            self._require_idle(plan.session_key)
            plan = await self._transition(plan_id, "running", only=("paused",), event="resumed")
            await self._advance(plan)
        return self._load(plan_id)

    async def _remember_pause(self, plan: Plan, value: str | None) -> Plan:
        """Annota (o toglie) la Pausa chiesta mentre il passo è fermo; col lock preso."""
        step_id = str((plan.blocked or {}).get("step") or "")

        def _apply(p: Plan) -> None:
            if p.status != "blocked":
                raise PlanCommandError("bad_state", f"the plan is {p.status}")
            s = _find(p, step_id)
            if value is None:
                s.extra.pop(_RESUME_TO, None)
            else:
                s.extra[_RESUME_TO] = value
            p.history.append(
                {"at": now_iso(), "event": "pause_requested" if value else "pause_cancelled"}
            )

        plan = await asyncio.to_thread(self.store.update, plan.id, _apply)
        self._remember(plan)
        await self._changed(plan)
        return plan

    async def stop(self, plan_id: str, then: str = "keep") -> dict[str, Any] | None:
        """Ferma: via il turno e gli aiutanti, passo ``interrupted``, ``stopped``.

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

            def _stop(p: Plan) -> None:
                stamp = now_iso()
                for s in p.steps:
                    if s.status in ("now", "blocked"):
                        s.status = "interrupted"
                        s.ended_at = stamp
                        s.extra.pop(_NUDGE, None)
                apply_transition(p, "stopped", at=stamp, then=then, cancelled=cancelled)

            plan = await asyncio.to_thread(self.store.update, plan_id, _stop)
            self._remember(plan)
            logger.info("Plan {} stopped ({}), {} task(s) cancelled", plan_id, then, cancelled)
        await self._changed(plan)
        await self._prune()
        if then == "rollback":
            try:
                preview = await self.rollback_preview(plan_id)
                return await self.rollback(plan_id, keep=(), expect_end=preview["expect_end"])
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
        metadata: dict[str, Any] = {PLAN_ANSWER_META: plan.id, "_wants_stream": True}
        _stamp_step(metadata, plan)
        await self.bus.publish_inbound(
            InboundMessage(
                channel="websocket",
                sender_id="user",
                chat_id=place_of(plan).chat_id,
                content=text.strip(),
                metadata=metadata,
            )
        )
        return plan

    def _unblock(self, plan_id: str) -> Plan:
        """``blocked → running``, il passo torna ``now`` (sincrono, in un thread)."""

        def _apply(p: Plan) -> None:
            if p.status != "blocked":
                raise PlanCommandError("bad_state", f"the plan is {p.status}, not blocked")
            if (p.blocked or {}).get("kind") == "drafts":
                # Le bozze del passo si decidono sulla scheda (``staged.*``):
                # una risposta a parole non installa niente, e far ripartire il
                # passo lascerebbe la bozza fuori dal piano — il difetto di
                # partenza.
                raise PlanCommandError(
                    "bad_state", "the plan waits for its drafts: install or discard them"
                )
            resume_to = None
            for s in p.steps:
                if s.status == "blocked":
                    s.status = "now"
                    s.extra.pop(_NUDGE, None)
                    resume_to = s.extra.pop(_RESUME_TO, None)
            apply_transition(p, "running", event="answered")
            if resume_to == "pausing":
                # La Pausa chiesta prima della domanda: il passo finisce, poi pausa.
                apply_transition(p, "pausing")

        try:
            return self.store.update(plan_id, _apply)
        except PlanNotFoundError as exc:
            raise PlanCommandError("not_found", "no such plan") from exc

    # ── letture per le RPC ─────────────────────────────────────────────────

    def view(self, plan: Plan) -> dict[str, Any]:
        """``plan.json`` più i campi derivati che la scheda usa."""
        return plan_view(plan)

    def step_files(self, plan_id: str, step_id: Any) -> dict[str, Any]:
        plan = self._load(plan_id)
        if not isinstance(step_id, str):
            raise PlanCommandError("bad_request", "step required")
        step = next((s for s in plan.steps if s.id == step_id), None)
        if step is None:
            raise PlanCommandError("not_found", "no such step")
        if not step.started_at or step.status in ("now", "blocked"):
            raise PlanCommandError("bad_state", "the step has not finished")
        root = self.store.workspace
        return self._shown_files(plan, work.files(work.step_journals(root, plan, step),
                                                  step=step.id))

    def changes(self, plan_id: str) -> dict[str, Any]:
        plan = self._load(plan_id)
        if plan.status == "proposed" or not any(s.started_at for s in plan.steps):
            raise PlanCommandError("bad_state", "the plan has not started")
        return self._shown_files(plan, work.files(work.plan_journals(self.store.workspace, plan)))

    @staticmethod
    def _shown_files(plan: Plan, diff: Mapping[str, list[str]]) -> dict[str, Any]:
        place = place_of(plan)
        out: dict[str, Any] = {k: [place.shown(p) for p in v] for k, v in diff.items()}
        out["groups"] = {place.shown(p): place.group_of(p)
                         for paths in diff.values() for p in paths}
        return out

    # ── boot ───────────────────────────────────────────────────────────────

    def recover(self) -> list[str]:
        """Al boot: i ritorni a metà si riapplicano, i piani a metà vanno in pausa.

        Riprendere da solo un passo interrotto rischia doppioni anche con passi
        idempotenti, e l'utente non saprebbe che il telefono si è riavviato: il
        piano aspetta Riprendi (deciso il 23/10), con una riga nella storia. Il
        passo interrotto torna ``pending`` e Riprendi lo riapre da capo.
        Sincrono: gira prima del loop.

        Un piano fermo (``blocked``, su una domanda o sulle bozze) resta fermo:
        aspettava già l'utente, e non c'è lavoro a metà da proteggere. Fino al
        26/10 andava in pausa anche lui, e la domanda spariva: Riprendi rifaceva
        il passo da capo, aiutanti compresi, per richiedere la stessa cosa — e
        un aggiornamento dell'app basta a riavviare il gateway. Le bozze le
        rilegge ``recheck_drafts`` alla prima lettura della scheda.
        """
        from jenny.runtime.user_text import say

        paused: list[str] = []
        for plan in self.store.list_all():
            if plan.extra.get(PENDING_UNDO_KEY):
                plan = self._recover_undo(plan)
            if plan.status not in ("running", "pausing"):
                if plan.status == "blocked":
                    logger.info("Plan {} was blocked at boot: still waits for the user", plan.id)
                continue
            counts = {s.id: self._step_counts(plan, s) for s in _closing(plan)}
            interrupted = current_step(plan) or next_pending_step(plan)
            n = _position(plan, interrupted.id) if interrupted is not None else 0

            def _pause(p: Plan, counts: dict[str, dict[str, int]] = counts, n: int = n) -> None:
                for s in p.steps:
                    if s.id in counts:
                        s.extra["files"] = counts[s.id]
                    if s.status in ("now", "blocked"):
                        s.status = "pending"
                        s.started_at = None
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

    @contextmanager
    def _journaled(self, plan: Plan, step_id: str) -> Iterator[Any]:
        """Le scritture del driver per un passo vanno nel registro del piano.

        Scrivono con ``atomic_write`` o ``rmdir``, che non passano da nessun
        recinto: chi scrive chiama ``touch`` a mano, e il «dopo» si annota qui.
        """
        from jenny.agent.write_journal import bind_journal

        journal = work.plan_journal(self.store.workspace, plan)
        with bind_journal(journal, step=step_id):
            try:
                yield journal
            finally:
                journal.settle("plan")

    def _require_place(self, plan: Plan) -> None:
        """Il quaderno di un piano di quaderno c'è ancora."""
        if plan.notebook and self.host.notebook_root(plan.notebook) is None:
            raise PlanCommandError("bad_state", f"notebook {plan.notebook!r} is not there")

    def _step_counts(self, plan: Plan, step: Step) -> dict[str, int]:
        journals = work.step_journals(self.store.workspace, plan, step)
        return _counts(work.files(journals, step=step.id))

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
        """I conti dei file dei passi appena chiusi, dai registri."""
        counts: dict[str, dict[str, int]] = {}
        for step in _closing(plan):
            counts[step.id] = await asyncio.to_thread(self._step_counts, plan, step)

        def _apply(p: Plan) -> None:
            for s in p.steps:
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
        self._require_place(plan)

        def _open(p: Plan) -> None:
            s = _find(p, step_id)
            s.status = "now"
            s.started_at = now_iso()
            s.ended_at = None
            s.extra.pop("files", None)
            for key in (_NUDGE, _PENDING_OUTCOME, _PENDING_NOTE, _PENDING_QUESTION,
                        _PENDING_REASON, _DRAFT_OUTCOMES, _RESUME_TO, _GAPS, _GAPS_GATE,
                        _GAPS_LISTED, _RESEARCH_ROUND, _RESEARCH_LINEAGES, _MACHINE):
                s.extra.pop(key, None)

        plan = await asyncio.to_thread(self.store.update, plan.id, _open)
        self._remember(plan)
        step = _find(plan, step_id)
        lost: LostFacts | None = None
        if _checks_facts(plan, step):
            lost = await asyncio.to_thread(self._lost_facts, plan)
        audit: Audit | None = None
        if step.kind == "check":
            audit = await asyncio.to_thread(self._run_audit, plan, step)
            if audit is not None:
                # Un controllo che non ha visto pagine non ha controllato niente.
                if not audit.findings and not (lost and lost.total) and audit.pages > 0:
                    await self._close_by_machine(plan, step_id, audit)
                    return
                plan = await self._note_machine(plan, step_id, _machine_view(audit, round_=1))
                step = _find(plan, step_id)
        # Sul loop e non in un thread: il manager degli aiutanti vive lì, e il suo
        # elenco dei vivi cambia mentre lo si legge. Legge un file di record piccolo.
        interrupted = self._interrupted_of(plan, step_id)
        await self.bus.publish_inbound(
            self._step_message(plan, step, lost=lost, audit=audit, interrupted=interrupted)
        )
        logger.info("Plan {}: step {} opened", plan.id, step_id)
        await self._changed(plan)

    def _interrupted_of(self, plan: Plan, step_id: str) -> list[dict[str, Any]]:
        """Gli aiutanti di questo passo che un riavvio ha interrotto, con la storia salvata.

        Il passo riparte da capo dopo Riprendi (:meth:`recover`), ma i suoi
        aiutanti non devono: ognuno ha la conversazione salvata fino al riavvio
        (``SubagentManager.interrupted_helpers``) e ``subagent_send`` la
        continua. Il messaggio del passo li elenca; senza, il modello ne
        lanciava di nuovi e rifaceva da zero ricerche già a metà.
        """
        lookup: Any = getattr(self.host, "interrupted_helpers", None)
        if lookup is None:
            return []
        try:
            helpers = lookup(plan.session_key)
        except Exception:  # noqa: BLE001 — un aiuto, non una condizione del passo
            logger.opt(exception=True).warning("Plan {}: interrupted helpers unknown", plan.id)
            return []
        return [
            h for h in helpers
            if isinstance(h, dict) and h.get("plan_id") == plan.id and h.get("plan_step") == step_id
        ]

    async def _finish(self, plan: Plan) -> None:
        def _done(p: Plan) -> None:
            apply_transition(p, "done")

        plan = await asyncio.to_thread(self.store.update, plan.id, _done)
        self._remember(plan)
        logger.info("Plan {} done", plan.id)
        await self._changed(plan)
        await self._prune()
        # Un piano finiva in silenzio: il 04/11/2026 l'ultimo passo si è chiuso
        # da solo e la risposta alla domanda l'utente ha dovuto chiederla.
        try:
            await self.bus.publish_inbound(self._done_message(plan))
        except Exception:  # noqa: BLE001 — il piano è finito comunque
            logger.opt(exception=True).warning("Plan {}: closing turn not sent", plan.id)

    def _done_message(self, plan: Plan) -> InboundMessage:
        """Il turno che chiude un piano finito: la risposta all'utente, in chat."""
        from jenny.utils.prompt_templates import render_template

        steps = [
            {"n": index, "title": s.title, "mark": _MARKS.get(s.status, "·"),
             "note": s.note if s.status in ("done", "skipped") else None}
            for index, s in enumerate(_active_steps(plan), start=1)
        ]
        return InboundMessage(
            channel="system",
            sender_id="plan",
            chat_id=f"websocket:{place_of(plan).chat_id}",
            content=render_template("agent/plan_done.md", strip=True, title=plan.title,
                                    steps=steps),
            session_key_override=plan.session_key,
            metadata={INJECTED_EVENT_META: PLAN_DONE_EVENT, PLAN_ID_META: plan.id},
        )

    def _step_message(
        self,
        plan: Plan,
        step: Step,
        *,
        reminder: bool = False,
        lost: LostFacts | None = None,
        audit: Audit | None = None,
        interrupted: list[dict[str, Any]] | None = None,
    ) -> InboundMessage:
        from jenny.utils.prompt_templates import render_template

        repair = render_repair(audit) if audit is not None and not reminder else None
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
                "done_when": step.done_when,
                "kind": step.kind,
            },
            # Senza quaderno il passo di verifica non ha una macchina dietro
            # (:meth:`_notebook_root`): la verifica la fa il turno.
            personal=not plan.notebook,
            # Le voci di copertura che il passo di ricerca deve cercare, con
            # cosa manca: il ``detail`` le può parafrasare, e questa è la lista
            # esatta che la validazione ha legato al passo (prova 2: voci web
            # che nessuno ha cercato, poi «non documentato»).
            covers=_covered_entries(plan, step),
            # Le bozze del piano già in attesa all'apertura del passo: ci sono
            # solo se il passo riparte dopo un riavvio (un passo non si chiude
            # con le sue bozze aperte). Le si dice al modello invece di
            # buttarle: sono lavoro fatto, e l'utente può ancora deciderle;
            # rifarle ne metterebbe una seconda copia davanti a lui.
            waiting=[d["target"] for d in self._drafts_or_empty(plan)] if not reminder else [],
            # I numeri con unità che c'erano prima del piano e adesso non sono
            # in nessuna pagina (``plan/facts.py``): solo nel passo di verifica.
            # La prova 3 ne ha persi 119 su 384 senza che nessuno lo decidesse.
            lost_facts=[
                {"page": f.page, "fact": f.fact, "context": f.context} for f in lost.items
            ] if lost is not None and not reminder else [],
            lost_total=lost.total if lost is not None else 0,
            # Il controllo a macchina del passo ``check`` (:meth:`_run_audit`):
            # la lista da riparare al posto della verifica libera.
            repair=repair,
            repair_more=max(0, len(audit.findings) - sum(len(g["items"]) for g in repair))
            if audit is not None and repair is not None else 0,
            machine_summary=summary_line(audit) if audit is not None else "",
            # Gli aiutanti del passo interrotti da un riavvio (:meth:`_interrupted_of`).
            interrupted=[
                {"task_id": str(h.get("task_id") or ""), "label": str(h.get("label") or ""),
                 "agent_type": str(h.get("agent_type") or "")}
                for h in interrupted or ()
            ] if not reminder else [],
        )
        return InboundMessage(
            channel="system",
            sender_id="plan",
            # ``websocket:<chat>``: il turno di sistema lo divide in canale e
            # chat, e la risposta va alla chat del posto (:attr:`PlanPlace.chat_id`).
            chat_id=f"websocket:{place_of(plan).chat_id}",
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
        await self._publish_changed(plan.place, plan.id)

    async def _publish_changed(self, slot: str, plan_id: str) -> None:
        place = place_from_id(slot)
        try:
            await self.bus.publish_outbound(
                OutboundMessage(
                    channel="websocket",
                    chat_id="webui",
                    content="",
                    metadata={
                        "_plan_changed": True,
                        "place": slot,
                        "notebook": place.notebook if place is not None else None,
                        "plan_id": plan_id,
                    },
                )
            )
        except Exception as exc:  # noqa: BLE001 — un avviso perso non è un guasto
            logger.debug("plan_changed not delivered for {}: {}", plan_id or slot, exc)

    # ── bozze di un passo ──────────────────────────────────────────────────

    def _bind_loop(self) -> None:
        """Il loop del gateway, per gli avvisi che arrivano da un thread."""
        if self._loop is None:
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                pass

    def _spawn(self, coro: Any) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ── diario ─────────────────────────────────────────────────────────────

    def _journal_line(self, plan: Plan, key: str) -> None:
        """Una riga di sistema in coda al diario di oggi del quaderno (sincrono).

        Il diario è append-only e il ritorno non lo tocca mai: quel che Jenny ha
        annotato durante il piano («riordino completato») resta. Senza una riga
        dopo, il diario racconterebbe un lavoro che il quaderno non ha più; con
        questa, chi lo rilegge — l'utente, il giardiniere — sa che è stato
        disfatto. In coda, come ogni riga del diario.
        """
        from jenny.runtime.user_text import say

        root = self._notebook_root(plan)
        if root is None:
            return
        try:
            from jenny.agent.tools.journal import append_journal_entry
            from jenny.utils.wiki_paths import journal_page_name, wiki_journal_dir

            stamp = _journal_now()
            page = wiki_journal_dir(root) / journal_page_name(stamp.date())
            line = say(key, title=plan.title)
            append_journal_entry(page, stamp.date(), f"- {stamp.strftime('%H:%M')} — {line}\n")
        except Exception:  # noqa: BLE001 — una riga persa non disfa il ritorno
            logger.opt(exception=True).warning("Plan {}: journal line not written", plan.id)

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


def _covered_entries(plan: Plan, step: Step) -> list[dict[str, str]]:
    """Le voci di copertura nei ``covers`` di *step*, nell'ordine della copertura.

    Solo per un passo ``research``: è l'unico che ne ha (lo vuole la
    validazione); un id che non trova la sua voce si salta in silenzio. E solo
    le voci ``web``: un passo può coprire anche una voce ``notebook`` (la
    validazione non lo rifiuta, per non far riprovare il modello all'infinito),
    ma da cercare sul web restano le ``web``.
    """
    if step.kind != "research" or not step.covers:
        return []
    wanted = set(step.covers)
    return [
        {"id": c.get("id", ""), "item": c.get("item", ""), "missing": c.get("missing", "")}
        for c in plan.coverage
        if c.get("id") in wanted and c.get("strategy") == "web"
    ]


def plan_view(plan: Plan) -> dict[str, Any]:
    """``plan.json`` più ``done``, ``total``, ``current_step_id``, ``can_rollback``,
    ``can_undo_rollback``: le letture che la scheda e il bollino fanno sempre."""
    counts = step_counts(plan)
    current = current_step(plan)
    data = plan.to_dict()
    # Cosa fa il passo, per la scheda: i buchi aperti del passo in corso, quelli
    # messi in lista, il giro di ricerca che il passo ha aperto, e quanto budget
    # di ricerca resta al piano (26/10: «aperto da 47 minuti» senza dire che
    # era un giro di ricerca). Gli aiutanti al lavoro per il passo la scheda li
    # conta da sola, dallo snapshot ``subagent_status`` che riceve comunque.
    for raw_step, step in zip(data["steps"], plan.steps):
        raw_step["gaps_open"] = (
            sum(len(v) for v in _open_gaps(step).values())
            if step.status in ("now", "blocked") else 0
        )
        listed = step.extra.get(_GAPS_LISTED)
        raw_step["gaps_listed"] = len(listed) if isinstance(listed, list) else 0
        round_ = step.extra.get(_RESEARCH_ROUND)
        raw_step["research_round"] = round_ if isinstance(round_, int) and round_ > 0 else None
    rounds = _research_rounds(plan)
    data.update(
        {
            "research_rounds": rounds,
            "rounds_max": MAX_RESEARCH_ROUNDS,
            "rounds_left": max(0, MAX_RESEARCH_ROUNDS - rounds),
            "done": counts["finished"],
            "total": counts["total"],
            "current_step_id": current.id if current is not None else None,
            "can_rollback": plan.status in ("done", "stopped")
            and any(s.started_at for s in plan.steps),
            "can_undo_rollback": plan.status == "rolled_back" and bool(plan.rollback_journal),
        }
    )
    return data


def _checks_facts(plan: Plan, step: Step) -> bool:
    """*step* è quello in cui si controllano i fatti persi: il passo ``check``.

    Fino al 26/10, senza un ``check``, era l'ultimo passo qualunque fosse: sul
    Titan «Riscrivi i drawer» ha ricevuto i fatti persi e 7 temi di ricerca.
    Ora una proposta senza ``check`` in fondo è rifiutata (``validate_proposal``).
    """
    return step.kind == "check"


def _research_rounds(plan: Plan) -> int:
    """Quanti giri di ricerca il piano ha già speso (``_RESEARCH_ROUNDS``)."""
    raw = plan.extra.get(_RESEARCH_ROUNDS)
    return raw if isinstance(raw, int) and raw >= 0 else 0




def _lost_refusal(lost: LostFacts) -> str:
    """Il rifiuto di un passo ``delete`` con fatti persi: quali, e cosa fare."""
    lines = [
        f"Not closed yet: {lost.total} fact(s) that were in a page when this step opened are "
        "in no page now. The code compared numbers with units, so it can be wrong: check each "
        "one. Put back what is still true in the right page; what went on purpose (wrong, "
        "superseded, removed at the user's request) you say in the step note. Then call "
        "`plan_step_done` again: this check runs once per step.",
    ]
    lines.extend(f'- {f.page}: {f.fact} — "{f.context}"' for f in lost.items)
    if lost.total > len(lost.items):
        lines.append(f"(and {lost.total - len(lost.items)} more)")
    return "\n".join(lines)




def _left_after_check(plan: Plan, step_id: str) -> int:
    """Quanti difetti il controllo a macchina ha lasciato al passo *step_id*, o 0."""
    machine = _find(plan, step_id).extra.get(_MACHINE)
    return int(machine.get("left") or 0) if isinstance(machine, dict) else 0






def _open_gaps(step: Step) -> dict[str, list[str]]:
    """I buchi aperti del passo, ``{lineage: [voci]}``, senza le voci vuote."""
    raw = step.extra.get(_GAPS)
    if not isinstance(raw, dict):
        return {}
    gaps: dict[str, list[str]] = {}
    for lineage, items in raw.items():
        if isinstance(items, list):
            clean = [str(item) for item in items if isinstance(item, str) and item]
            if clean:
                gaps[str(lineage)] = clean
    return gaps


def _gaps_lines(gaps: Mapping[str, list[str]]) -> list[str]:
    lines = [
        "Not closed yet: helpers of this step reported gaps under **To research** that "
        "are still open. They are part of this step:",
    ]
    for lineage, items in gaps.items():
        lines.append(f"- helper `{lineage}`:")
        lines.extend(f"  - {item}" for item in items)
    return lines


def _gaps_refusal(gaps: Mapping[str, list[str]], *, round_: int) -> str:
    """Il rifiuto di ``plan_step_done`` con i buchi aperti e un giro nel budget: cercali."""
    lines = _gaps_lines(gaps)
    lines.append(
        f"Research round {round_} of {MAX_RESEARCH_ROUNDS} for this plan: spawn researchers "
        "for them now in this step, a few, grouped by subject; when they are done, send the "
        "same helper back with `subagent_send` to fill the pages from what they saved. If they "
        "were already searched and stay empty, or they are outside this plan, call "
        "`plan_step_done` again and say in the note why they stay open. This check runs once "
        "per step; what stays open after it goes to the notebook's open list."
    )
    return "\n".join(lines)


def _gaps_budget_used(gaps: Mapping[str, list[str]]) -> str:
    """Il rifiuto a budget finito: niente ricerca, le pagine dicono «non documentato»."""
    lines = _gaps_lines(gaps)
    lines.append(
        f"The plan's research budget ({MAX_RESEARCH_ROUNDS} rounds) is used: do not research "
        "them. Make sure each page says \"not documented\" with the queries tried, then call "
        f"`plan_step_done` again: the gaps go to the notebook's open list (`{OPEN_GAPS_PATH}`), "
        "which the next plan reads."
    )
    return "\n".join(lines)








def _journal_now() -> datetime:
    """Adesso, nel fuso configurato (``agents.defaults.timezone``), come il diario.

    Lo stesso orologio di ``journal_append`` (``JournalAppendTool._stamp``): il
    fuso in cui il modello legge l'ora, non quello di sistema, così la riga
    finisce nella pagina del giorno giusto e con l'ora che l'utente si aspetta.
    ``safe_zoneinfo``: su Android il database dei fusi può mancare.
    """
    try:
        from jenny.config.loader import load_config

        name = (load_config().agents.defaults.timezone or "").strip()
    except Exception:  # noqa: BLE001 — senza config, il fuso del telefono
        name = ""
    if name:
        from jenny.utils.helpers import safe_zoneinfo

        return datetime.now(tz=safe_zoneinfo(name))
    return datetime.now().astimezone()


def _resume_to(plan: Plan) -> str | None:
    step_id = (plan.blocked or {}).get("step")
    for step in plan.steps:
        if step.id == step_id:
            value = step.extra.get(_RESUME_TO)
            return value if isinstance(value, str) else None
    return None




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


















def _shorten(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


__all__ = [
    "HELD_WAKE_AFTER_S",
    "HELD_WAKE_TEXT",
    "OPEN_STATUSES",
    "PLAN_ANSWER_META",
    "PLAN_FOLLOWUP_META",
    "PLAN_ID_META",
    "PLAN_MODE_META",
    "PLAN_NOTES_META",
    "PLAN_PROPOSE_TOOL",
    "PLAN_REVISE_META",
    "PLAN_STEP_META",
    "PLAN_STEP_TOOLS",
    "PLAN_TOOL_NAMES",
    "PLAN_TURN_EVENTS",
    "PLAN_WAKE_META",
    "ROLLBACK_FAILED_KEY",
    "PlanCommandError",
    "PlanDriver",
    "PlanHost",
    "notebook_of",
    "card_report",
    "place_of",
    "plan_view",
]
