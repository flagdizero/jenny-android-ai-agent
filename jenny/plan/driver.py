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
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from loguru import logger

from jenny.bus.events import InboundMessage, OutboundMessage
from jenny.plan.facts import LostFacts, lost_facts
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
    drafting_expired,
    drafting_view,
    next_pending_step,
    now_iso,
    order_warnings,
    step_counts,
    to_research_items,
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
#: Messo dal server su un turno della pianificazione in corso che il client
#: non ha marcato (la risposta a una domanda di Jenny): il blocco del turno lo
#: dice al modello, che non ricomincia da capo.
PLAN_FOLLOWUP_META = "_plan_drafting_followup"

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

# I buchi riportati dagli aiutanti del passo sotto **To research**, nell'``extra``
# del passo: ``{lineage: [voci]}``, e vale l'ultimo resoconto di ogni lineage —
# un editor rimandato indietro con ``subagent_send`` che non ne riporta più li
# azzera. Il cancello di ``plan_step_done`` è uno solo per passo, come il
# promemoria: rifiuta una volta con i buchi aperti, la seconda chiamata passa
# (i buchi cercati e rimasti vuoti, o fuori dal piano, li spiega la nota).
_GAPS = "gaps"
_GAPS_GATE = "gaps_gate"
_GAPS_GATE_USED = "used"
# Un giro di ricerca c'è già stato nel passo: il resoconto di un ``researcher``
# arrivato mentre il passo aveva buchi aperti. Allora il cancello non rifiuta:
# nella prova A del 24/10 il cancello scattava sui buchi *nuovi* degli editor
# rimandati, dopo il primo giro, e ogni passo pagava un secondo giro in fila
# (12-13 minuti di ricercatore più una ripresa). Quei buchi non si perdono:
# vanno al passo di controllo, sotto ``_CARRIED_GAPS``, e si cercano lì tutti
# insieme.
_GAPS_ROUND = "gaps_round"
_CARRIED_GAPS = "carried_gaps"
# I lineage dei ricercatori che hanno già riportato nel passo: i loro buchi non
# fanno un giro per un altro ricercatore.
_RESEARCH_LINEAGES = "research_lineages"
# Quante voci riportate al controllo, al massimo: un elenco più lungo non è
# più un giro di ricerca ma un altro piano.
_MAX_CARRIED_ITEMS = 40

_RUNNING = frozenset({"running", "pausing"})

# Per quanto un ``plan_propose`` dopo un annullamento riceve «fermati»: il turno
# che stava pianificando, non uno di domani.
_CANCELLED_GRACE_S = 600.0

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

#: Il risveglio unico di un passo (resoconti di aiutanti tenuti finché lavora
#: un fratello): dopo quanto dal primo resoconto tenuto il driver sveglia Jenny
#: comunque. È la garanzia contro un aiutante lento ma vivo, che il watchdog
#: non marca ``stalled``.
HELD_WAKE_AFTER_S = 600.0
#: Messo dal driver sul turno di risveglio: ``stalled`` (i fratelli rimasti sono
#: tutti fermi) o ``timeout`` (il tetto di sopra). Il turno è un turno di passo
#: (``PLAN_STEP_EVENT``): stessa verifica di freschezza, stessi tool.
PLAN_WAKE_META = "plan_wake"
#: Il testo del risveglio. Inglese, come ogni testo per il modello.
HELD_WAKE_TEXT = (
    "[Plan — helper results waiting]\n\n"
    "{held} helper result(s) of the open step are waiting in the conversation above; "
    "{running} helper(s) of this step still running, {stalled} of them without progress "
    "for a while. Read the results now and decide how to continue the step: wait for the "
    "others, continue or replace a stuck one, or close the step if it is done."
)

_MAX_NOTE = 500
_MAX_QUESTION = 1000
_MAX_ANSWER = 8000

# Nell'``extra`` di un passo fermo sulle sue bozze: come il modello l'ha chiuso
# (``done``/``skipped``) e la sua nota, per chiuderlo così quando l'utente ha
# deciso; e cosa ha deciso per ogni bozza, per la nota scritta dal codice.
_PENDING_OUTCOME = "pending_outcome"
_PENDING_NOTE = "pending_note"
_DRAFT_OUTCOMES = "draft_outcomes"
# Una domanda che aspetta dietro le bozze del passo, e il suo motivo.
_PENDING_QUESTION = "pending_question"
_PENDING_REASON = "pending_reason"
# Lo stato a cui tornare quando il blocco si scioglie: ``pausing`` se l'utente
# aveva chiesto la Pausa prima del blocco, o durante.
_RESUME_TO = "resume_to"
# Nell'``extra`` del piano: l'istante dell'Approva in millisecondi.
_APPROVED_MS = "approved_ms"


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

    def notebook_drafts(self, notebook: str) -> list[dict[str, Any]]:
        """Le bozze in attesa (``staged_writes``) con il file da cambiare nel quaderno.

        Ognuna con ``id``, ``target`` (relativo alla radice delle bozze, come lo
        mostra ``staged.list``) e ``created_ms``.
        """
        ...

    def discard_draft(self, draft_id: str) -> bool:
        """Butta una bozza in attesa. ``True`` se c'era."""
        ...


@dataclass
class _Live:
    """Una voce dell'indice in memoria: il piano aperto di una sessione."""

    plan_id: str
    notebook: str
    status: str
    step_now: bool


@dataclass
class _HeldResults:
    """I resoconti di aiutanti di una sessione scritti in storia senza turno.

    Il gruppo è ``(plan_id, plan_step)`` degli aiutanti: si tengono solo
    resoconti dello stesso gruppo, finché un fratello ``running`` lavora. Gli id
    restano qui fino al turno che li fa vedere a Jenny (``take_held_results``):
    è lì che il loop li marca ``handled``.
    """

    plan_id: str | None
    plan_step: str | None
    task_ids: list[str]
    first_at: float
    timer: asyncio.Task[Any] | None = None
    #: Il risveglio di sicurezza è già partito: non se ne pubblica un secondo.
    woken: bool = False


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
        held_wake_after_s: float = HELD_WAKE_AFTER_S,
    ) -> None:
        self.store = store
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
        # ``drafting.json`` (lo store è la fonte), per le domande sincrone di
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

    # ── pianificazione in corso (``drafting``) ──────────────────────────────
    #
    # Il tasto Piano apre una **pianificazione**, non un turno: Jenny guarda il
    # quaderno, fa le sue domande, l'utente risponde, e solo allora c'è la
    # proposta. Finché dura, ogni turno dell'utente in quella chat è un turno di
    # pianificazione (sola lettura, ``plan_propose`` visibile) anche se il
    # client non lo marca: lo stato è del server. Finisce con la proposta, con
    # l'annullamento (``plan.cancel_drafting``, il tasto spento, ``/stop``) o
    # dopo :data:`~jenny.plan.model.DRAFTING_TTL_HOURS` ore senza turni.

    def drafting_for(self, session_key: str | None) -> dict[str, Any] | None:
        """La pianificazione in corso della sessione, o ``None`` (scaduta = finita)."""
        notebook = notebook_of(session_key)
        if notebook is None:
            return None
        entry = self._drafting.get(notebook)
        if entry is None or drafting_expired(entry):
            return None
        return entry

    def drafting_info(self, notebook: str) -> dict[str, Any] | None:
        """``{since, revise, reads}`` per ``plan.get``, o ``None``."""
        entry = self._drafting.get(notebook)
        if entry is None or drafting_expired(entry):
            return None
        return drafting_view(entry)

    async def start_drafting(
        self, session_key: str, revise: str | None = None, *, create: bool = True
    ) -> None:
        """Apre (o tiene viva) la pianificazione del quaderno della sessione.

        Una pianificazione già aperta tiene il suo inizio e le sue letture: il
        ``plan: true`` che il client rimanda a ogni messaggio non la azzera. Una
        revisione chiesta adesso vince su quella di prima. Con ``create=False``
        tocca solo una pianificazione che c'è: un turno che la continua non la
        fa rinascere se intanto l'utente l'ha annullata.
        """
        notebook = notebook_of(session_key)
        if notebook is None:
            return
        if create:
            # Una pianificazione nuova chiude il conto dell'annullamento di prima.
            self._cancelled_drafting.pop(notebook, None)
        stamp = now_iso()
        created: list[bool] = []

        def _apply(entry: dict[str, Any] | None) -> dict[str, Any] | None:
            if entry is None or drafting_expired(entry):
                if not create:
                    return None
                created.append(True)
                entry = {"since": stamp, "revise": None, "reads": 0, "helpers": []}
            if revise:
                entry["revise"] = revise
            entry["touched"] = stamp
            return entry

        await self._update_drafting(notebook, _apply)
        if created:
            logger.info("Plan drafting started in notebook {}", notebook)
            await self._drafting_changed(notebook)

    async def _update_drafting(
        self, notebook: str, fn: Callable[[dict[str, Any] | None], dict[str, Any] | None]
    ) -> dict[str, Any] | None:
        """Scrive la voce di *notebook* e ne allinea la copia in memoria.

        Sotto un lock solo per tutte le pianificazioni: la scrittura sul file e
        la copia in memoria vanno insieme, o un annullamento che arriva fra le
        due lascerebbe in memoria una pianificazione che sul file non c'è più.
        """
        async with self._drafting_lock:
            entry = await asyncio.to_thread(self.store.drafting_update, notebook, fn)
            if entry is None:
                self._drafting.pop(notebook, None)
            else:
                self._drafting[notebook] = entry
            return entry

    async def note_read(self, session_key: str | None) -> None:
        """Una lettura del quaderno durante la pianificazione (il pavimento di A2)."""
        notebook = notebook_of(session_key)
        if notebook is None or self.drafting_for(session_key) is None:
            return
        stamp = now_iso()

        def _apply(entry: dict[str, Any] | None) -> dict[str, Any] | None:
            if entry is None:
                return None
            reads = entry.get("reads")
            entry["reads"] = (reads if isinstance(reads, int) else 0) + 1
            entry["touched"] = stamp
            return entry

        await self._update_drafting(notebook, _apply)

    async def note_helper(self, session_key: str | None, task_id: str) -> None:
        """Un aiutante lanciato durante la pianificazione: il suo risultato conterà."""
        notebook = notebook_of(session_key)
        if notebook is None or not task_id or self.drafting_for(session_key) is None:
            return

        def _apply(entry: dict[str, Any] | None) -> dict[str, Any] | None:
            if entry is None:
                return None
            helpers = [h for h in entry.get("helpers") or () if isinstance(h, str)]
            if task_id not in helpers:
                helpers.append(task_id)
            entry["helpers"] = helpers[-20:]
            return entry

        await self._update_drafting(notebook, _apply)

    async def note_helper_result(self, session_key: str | None, task_id: Any) -> bool:
        """Il risultato di un aiutante: è una lettura solo se l'ha lanciato la pianificazione.

        Un aiutante partito prima (magari un ``researcher`` di una chat di ieri)
        che rientra adesso non ha guardato il quaderno per questo piano: non
        conta, o basterebbe a superare il pavimento senza aver letto niente.
        """
        entry = self.drafting_for(session_key)
        if entry is None or not isinstance(task_id, str):
            return False
        if task_id not in (entry.get("helpers") or ()):
            return False
        await self.note_read(session_key)
        return True

    async def end_drafting(self, session_key: str | None, reason: str) -> bool:
        """Chiude la pianificazione della sessione. ``True`` se ce n'era una."""
        notebook = notebook_of(session_key)
        if notebook is None:
            return False
        return await self._drop_drafting(notebook, reason)

    async def cancel_drafting(self, notebook: str) -> bool:
        """``plan.cancel_drafting``: l'utente ha spento il tasto o annullato.

        Ferma anche il lavoro della pianificazione: il turno in volo e gli
        aiutanti che ha lanciato. Il 25/10 sul telefono l'annullamento chiudeva
        solo lo stato: il turno andava avanti, ``plan_propose`` lo rifiutava, e
        Jenny («non è partita la modalità piano, così sono andata diretta»)
        lanciava un editor che riscriveva l'indice senza nessuna approvazione.
        """
        entry = self._drafting.get(notebook) or {}
        helpers = [h for h in entry.get("helpers") or () if isinstance(h, str)]
        dropped = await self._drop_drafting(notebook, "cancelled")
        if dropped:
            self._cancelled_drafting[notebook] = time.monotonic()
            await self._stop_drafting_work(notebook, helpers)
        return dropped

    async def _stop_drafting_work(self, notebook: str, helpers: list[str]) -> None:
        """Il turno di pianificazione in volo e gli aiutanti suoi, non gli altri.

        Gli altri aiutanti della chat (una ricerca chiesta prima del tasto Piano)
        non sono della pianificazione e restano al lavoro: per questo non è
        ``host.cancel``, che è ``/stop all``. Metodi facoltativi dell'ospite: un
        ospite che non li ha (i test vecchi) annulla solo lo stato, come prima.
        """
        from jenny.session.keys import project_session_key

        session_key = project_session_key(notebook)
        cancel_turn: Any = getattr(self.host, "cancel_turn", None)
        cancel_helpers: Any = getattr(self.host, "cancel_helpers", None)
        try:
            if callable(cancel_turn) and self.host.turn_in_flight(session_key):
                await cancel_turn(session_key)
            running = self.host.running_helpers(session_key)
            mine = [h for h in helpers if h in running]
            if mine and callable(cancel_helpers):
                await cancel_helpers(mine)
        except Exception:  # noqa: BLE001 — lo stato è già annullato: il resto è un aiuto
            logger.opt(exception=True).warning(
                "Plan drafting in notebook {}: work not stopped", notebook
            )
        logger.info(
            "Plan drafting in notebook {} cancelled: turn stopped, {} helper(s)",
            notebook, len(helpers),
        )

    async def _drop_drafting(
        self, notebook: str, reason: str, *, announce: bool = True
    ) -> bool:
        async with self._drafting_lock:
            self._drafting.pop(notebook, None)
            removed = await asyncio.to_thread(self.store.drafting_remove, notebook)
        if removed is None:
            return False
        # Una voce scaduta è già finita per tutti (``drafting_for`` la ignora):
        # toglierla è pulizia, ma la scheda la vedeva già spenta.
        live = not drafting_expired(removed)
        logger.info("Plan drafting in notebook {} ended: {}", notebook, reason)
        if announce:
            await self._drafting_changed(notebook)
        return live

    async def _settle_drafting(self, session_key: str, metadata: dict[str, Any]) -> None:
        """Prima di un turno dell'utente: apre, tiene viva o chiude la pianificazione.

        Un messaggio col tasto Piano la apre, se non c'è. Ogni messaggio che
        arriva mentre c'è — col tasto o senza: il client lo rimanda finché il
        tasto è acceso — la continua: il turno diventa di pianificazione qui,
        nei metadati del messaggio, così il filtro dei tool, il blocco del
        prompt e la sola lettura dello scope lo vedono tutti allo stesso modo,
        e il blocco dice al modello di andare avanti, non di ricominciare. Un
        turno che la continua non la ricrea: se l'utente l'ha annullata nel
        frattempo, resta annullata.
        """
        notebook = notebook_of(session_key)
        if notebook is None:
            return
        entry = self._drafting.get(notebook)
        if entry is not None and drafting_expired(entry):
            await self._drop_drafting(notebook, "expired")
            entry = None
        if self.held_status(session_key) is not None:
            # Un piano partito tiene il quaderno: non si pianifica sopra di lui
            # (``plan_propose`` lo rifiuterebbe), e una pianificazione rimasta
            # aperta non deve rendere «di pianificazione» la risposta a un blocco.
            if entry is not None:
                await self._drop_drafting(notebook, "plan running")
            return
        asked = metadata.get(PLAN_REVISE_META)
        revise = asked if isinstance(asked, str) and asked else None
        if entry is None:
            if metadata.get(PLAN_MODE_META) is True:
                await self.start_drafting(session_key, revise)
            return
        from jenny.security.workspace_access import WORKSPACE_READONLY_METADATA_KEY

        metadata[PLAN_MODE_META] = True
        metadata[WORKSPACE_READONLY_METADATA_KEY] = True
        metadata[PLAN_FOLLOWUP_META] = True
        kept = entry.get("revise")
        if revise is None and isinstance(kept, str) and kept:
            metadata[PLAN_REVISE_META] = kept
        await self.start_drafting(session_key, revise, create=False)

    async def _drafting_changed(self, notebook: str) -> None:
        """``plan_changed`` per l'inizio o la fine di una pianificazione.

        ``plan_id`` è il piano aperto del quaderno, se c'è (una revisione), o
        ``""``: il client rilegge comunque ``plan.get {notebook}``.
        """
        from jenny.session.keys import project_session_key

        live = self._index.get(project_session_key(notebook))
        await self._publish_changed(notebook, live.plan_id if live is not None else "")

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
                lines.append(f"The plan waits for the user's answer to: {question}".rstrip())
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
        return render_template(
            "agent/plan_mode.md",
            strip=True,
            current=current,
            revise=bool(revise) and current is not None,
            followup=followup,
            reads=reads if isinstance(reads, int) else 0,
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
        notebook = notebook_of(session_key)
        if notebook is None or session_key is None:
            return "Error: a plan can only be proposed inside a notebook chat."
        if self.host.goal_active(session_key):
            return (
                "Not now: this notebook has an active sustained goal, and a plan cannot run "
                "next to it. Finish it or close it with complete_goal first, or tell the user."
            )
        held = self.held_status(session_key)
        if held is not None:
            return (
                f"Not now: this notebook already has a plan that has started ({held}). "
                "A new one can be proposed when that plan is done or stopped."
            )
        drafting = self.drafting_for(session_key)
        if drafting is None:
            cancelled_at = self._cancelled_drafting.get(notebook)
            if cancelled_at is not None and time.monotonic() - cancelled_at < _CANCELLED_GRACE_S:
                return (
                    "Stop: the user cancelled this plan while you were preparing it. Do not "
                    "do the work, do not start helpers and do not write anything: say in one "
                    "short sentence that the plan is cancelled, and wait for the user."
                )
            return (
                "Error: nobody is preparing a plan in this notebook now: the user starts it "
                "with the Plan button. Do not propose a plan; answer the user."
            )
        if not drafting.get("reads"):
            return (
                "Not yet: look at the notebook first: read the pages and sources this job "
                "depends on (list_dir, read_file, grep), so the plan says what is there and "
                "what is missing. Then call plan_propose."
            )
        try:
            proposal = validate_proposal(title, steps, coverage, assumptions)
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
        # La proposta chiude la pianificazione: il prossimo messaggio è una chat
        # normale, o un Rivedi che ne apre un'altra. L'avviso è quello del piano.
        await self._drop_drafting(notebook, "proposed", announce=False)
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
        except Exception:  # noqa: BLE001 — una lettura rotta non deve chiudere un passo a metà
            logger.opt(exception=True).warning("Plan {}: drafts not readable", plan.id)
            return drafts
        for draft in pending:
            created = draft.get("created_ms")
            if isinstance(created, int) and since_ms is not None and created < since_ms:
                continue
            drafts.append({"id": str(draft.get("id")), "target": str(draft.get("target"))})
        return drafts

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
            gaps = _open_gaps(step)
            gate_used = step.extra.get(_GAPS_GATE) == _GAPS_GATE_USED
            if outcome == "done" and gaps and step.extra.get(_GAPS_ROUND):
                # Il giro di ricerca del passo c'è stato: i buchi ancora aperti
                # (riportati dopo, di solito dagli editor rimandati) passano al
                # controllo invece di fermare il passo per un secondo giro. Anche
                # se il cancello aveva già rifiutato una volta: quel rifiuto ha
                # forzato il giro, e i buchi di dopo restano da cercare.
                await self._carry_gaps(plan, step, gaps)
            elif outcome == "done" and gaps and not gate_used:
                # Un aiutante ha riportato buchi e il passo si chiude come se
                # niente fosse: la prova 3 li ha lasciati «non documentati» nelle
                # pagine. Una volta sola, come il promemoria: la seconda chiamata
                # passa, perché un buco cercato e rimasto vuoto è un esito vero.
                def _gate(p: Plan) -> None:
                    _find(p, step_id).extra[_GAPS_GATE] = _GAPS_GATE_USED

                await asyncio.to_thread(self.store.update, plan.id, _gate)
                logger.info("Plan {}: step {} close refused once, gaps open", plan.id, step_id)
                return _gaps_refusal(gaps)
            drafts = await asyncio.to_thread(self._plan_drafts, plan)
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
        # Corto apposta, anche l'ultimo: fra la chiamata e la fine del turno la
        # scheda è «in corso» senza un passo, e il tempo è quello che il modello
        # passa a scrivere. Il riepilogo del piano è la scheda stessa.
        if last:
            return (
                f"Step {n} {verb}. It was the last step: end your turn now with one or two "
                "short sentences for the user; the plan card already lists what was done."
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
            drafts = await asyncio.to_thread(self._plan_drafts, plan)
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
            "The plan now waits for the user; your question is on the plan card. End your "
            "turn: ask it in one sentence, and do nothing else on the plan until they answer."
        )

    # ── risveglio unico per passo ──────────────────────────────────────────
    #
    # Ogni resoconto di aiutante apriva un turno di Jenny: un passo con N
    # aiutanti faceva fino a N turni, e ognuno si portava dietro tutta la
    # conversazione (prova 2: 49 turni). Ora un resoconto riuscito, quando un
    # fratello dello stesso passo lavora ancora, si scrive in storia senza turno
    # (``AgentLoop._hold_subagent_result``); il turno parte con l'ultimo. La rete
    # di sicurezza è qui: fratelli rimasti tutti fermi, o un tetto di tempo.

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
        if not isinstance(task_id, str) or not task_id or not self.plan_running(session_key):
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
        if held.timer is None and not held.woken:
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

    def _helper_group(self, task_id: str) -> tuple[str | None, str | None] | None:
        lookup: Any = getattr(self.host, "helper_plan_step", None)
        if lookup is None:
            return None
        group = lookup(task_id)
        if not isinstance(group, tuple) or len(group) != 2:
            return None
        return group[0], group[1]

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
                chat_id=f"websocket:{plan.session_key}",
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

    # ── hook del loop ──────────────────────────────────────────────────────

    async def _carry_gaps(self, plan: Plan, step: Step, gaps: Mapping[str, list[str]]) -> None:
        """I buchi rimasti di *step* nell'``extra`` del passo di controllo che viene dopo.

        Senza un controllo dopo (il passo è lui, o il piano non ne ha) non si
        riporta niente: il passo si chiude, e la sua nota dice cosa resta.
        """
        target = _carry_target(plan, step)
        if target is None:
            return
        items = [item for lineage_items in gaps.values() for item in lineage_items]
        target_id, origin = target.id, step.title

        def _apply(p: Plan) -> None:
            t = _find(p, target_id)
            carried = [c for c in t.extra.get(_CARRIED_GAPS) or () if isinstance(c, dict)]
            room = _MAX_CARRIED_ITEMS - sum(len(c.get("items") or ()) for c in carried)
            if room > 0:
                carried.append({"step": origin, "items": items[:room]})
                t.extra[_CARRIED_GAPS] = carried

        await asyncio.to_thread(self.store.update, plan.id, _apply)
        logger.info(
            "Plan {}: {} gap(s) of step {} carried to step {}",
            plan.id, len(items), step.id, target_id,
        )

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
    ) -> bool:
        """Il resoconto di un aiutante del passo aperto: registra i suoi buchi **To research**.

        Lo chiama il loop per ogni resoconto che arriva, tenuto o no, e anche a
        metà turno. Conta solo con un piano in corsa e un resoconto del passo
        aperto: il passo lo dicono i metadati del resoconto (``plan_id``,
        ``plan_step``, gli stessi del lancio) o, se mancano, il registro degli
        aiutanti. La chiave è il lineage, che resta lo stesso quando l'aiutante
        è ripreso con ``subagent_send``; senza, il ``task_id``. L'ultimo
        resoconto del lineage sostituisce quello prima: senza buchi, li toglie.
        ``True`` se i buchi del passo sono cambiati.

        Il resoconto di un ``researcher`` (*agent_type*) arrivato con buchi
        d'altri aperti segna il giro di ricerca del passo (``_GAPS_ROUND``).
        """
        if not isinstance(content, str) or not self.plan_running(session_key):
            return False
        key = next((v for v in (lineage, task_id) if isinstance(v, str) and v), None)
        live = self._index.get(session_key)
        if key is None or live is None:
            return False
        if plan_id is None and plan_step is None and isinstance(task_id, str) and task_id:
            group = self._helper_group(task_id)
            if group is not None:
                plan_id, plan_step = group
        if not plan_id or plan_id != live.plan_id or not plan_step:
            return False
        items = to_research_items(content)
        async with self._lock(session_key):
            try:
                plan = await asyncio.to_thread(self.store.load, live.plan_id)
            except PlanError:
                return False
            step = _step_now(plan)
            if plan.status not in _RUNNING or step is None or step.id != plan_step:
                return False
            gaps = _open_gaps(step)
            is_researcher = agent_type == "researcher"
            researchers = [
                r for r in step.extra.get(_RESEARCH_LINEAGES) or () if isinstance(r, str)
            ]
            if is_researcher and key not in researchers:
                researchers.append(key)
            # Un giro è la ricerca dei buchi *di altri*: degli editor, o quelli
            # che i passi prima hanno riportato a questo. I buchi di un altro
            # ricercatore dello stesso lancio no: in un passo di ricerca previsto
            # due ricercatori che arrivano uno dopo l'altro non sono un giro.
            research_round = (
                is_researcher
                and not step.extra.get(_GAPS_ROUND)
                and (
                    any(k != key and k not in researchers for k in gaps)
                    or bool(step.extra.get(_CARRIED_GAPS))
                )
            )
            new_researcher = is_researcher and key not in (
                step.extra.get(_RESEARCH_LINEAGES) or ()
            )
            if items:
                gaps[key] = items
            elif key in gaps:
                del gaps[key]
            elif not research_round and not new_researcher:
                return False
            step_id = step.id

            def _apply(p: Plan) -> None:
                s = _find(p, step_id)
                if gaps:
                    s.extra[_GAPS] = gaps
                else:
                    s.extra.pop(_GAPS, None)
                if research_round:
                    s.extra[_GAPS_ROUND] = True
                if is_researcher:
                    s.extra[_RESEARCH_LINEAGES] = researchers

            await asyncio.to_thread(self.store.update, plan.id, _apply)
        logger.info(
            "Plan {}: step {} has {} gap(s) from helper {}", live.plan_id, step_id, len(items), key
        )
        return True

    async def before_user_turn(self, session_key: str, metadata: dict[str, Any]) -> None:
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
        await self._settle_drafting(session_key, metadata)
        live = self._index.get(session_key)
        if live is not None and live.status == "blocked":
            await self.recheck_drafts(live.plan_id)
            live = self._index.get(session_key)
        if live is None or metadata.get(PLAN_ANSWER_META):
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
            await self._changed(plan)

    async def on_turn_end(
        self,
        session_key: str,
        metadata: Mapping[str, Any] | None,
        *,
        pending: bool = False,
        stop_reason: str = "",
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
        drafts = await asyncio.to_thread(self._plan_drafts, plan)
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
        logger.info("Plan {} blocked: step {} {}", plan.id, step_id, (
            "refused by the provider" if reason == "refused" else "stalled after the reminder"
        ))
        await self._changed(plan)

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
                # Al millisecondo, come ``created_ms`` delle bozze: la storia è al
                # secondo, e una bozza di un attimo prima dell'Approva non è del
                # piano (:meth:`_step_drafts`).
                p.extra[_APPROVED_MS] = int(time.time() * 1000)
                for s in p.steps:
                    if s.id in cut_ids:
                        s.status = "cut"
                p.photos["start"] = start

            plan = await asyncio.to_thread(self.store.update, plan_id, _approve)
            self._remember(plan)
            logger.info("Plan {} approved (v{}, {} cut)", plan_id, plan.version, len(cut_ids))
            # Un Rivedi aperto e poi lasciato lì (Approva sulla versione che c'è):
            # la pianificazione è finita, o i turni dopo resterebbero «di
            # pianificazione» sopra un piano che gira.
            await self._drop_drafting(plan.notebook, "approved", announce=False)
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
            discarded = await asyncio.to_thread(self._discard_plan_drafts, plan)
            self.host.append_note(
                plan.session_key, _rollback_note(plan, report.counts, len(discarded))
            )
            await asyncio.to_thread(self._journal_line, plan, "plan.journal_rolled_back")
            logger.info("Plan {} rolled back: {}", plan_id, report.counts)
        await self._changed(plan)
        return {
            "report": report_with_groups(report.to_dict()),
            "discarded_drafts": discarded,
        }

    def _discard_plan_drafts(self, plan: Plan) -> list[dict[str, str]]:
        """Butta le bozze del piano ancora in attesa (sincrono).

        Dopo un ritorno il quaderno è com'era prima del piano: una bozza del
        piano rimasta lì si potrebbe ancora installare dalla scheda delle bozze,
        e rimetterebbe un pezzo del lavoro appena disfatto. L'annullamento del
        ritorno non le rimette: non erano mai state installate.
        """
        discarded: list[dict[str, str]] = []
        for draft in self._plan_drafts(plan):
            try:
                if self.host.discard_draft(draft["id"]):
                    discarded.append(draft)
            except Exception:  # noqa: BLE001 — una bozza rimasta non disfa il ritorno
                logger.opt(exception=True).warning(
                    "Plan {}: draft {} not discarded", plan.id, draft["id"]
                )
        if discarded:
            logger.info("Plan {}: {} pending draft(s) discarded with the rollback",
                        plan.id, len(discarded))
        return discarded

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
            await asyncio.to_thread(self._journal_line, plan, "plan.journal_rollback_undone")
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
        self._journal_line(
            updated,
            "plan.journal_rollback_undone" if kind == _KIND_UNDO else "plan.journal_rolled_back",
        )
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
            for key in (_NUDGE, _PENDING_OUTCOME, _PENDING_NOTE, _PENDING_QUESTION,
                        _PENDING_REASON, _DRAFT_OUTCOMES, _RESUME_TO, _GAPS, _GAPS_GATE,
                        _GAPS_ROUND, _RESEARCH_LINEAGES):
                s.extra.pop(key, None)

        plan = await asyncio.to_thread(self.store.update, plan.id, _open)
        self._remember(plan)
        step = _find(plan, step_id)
        lost: LostFacts | None = None
        start = plan.photos.get("start")
        if start and _checks_facts(plan, step):
            lost = await asyncio.to_thread(self._lost_facts, plan, photos, start, before)
        await self.bus.publish_inbound(self._step_message(plan, step, lost=lost))
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

    def _lost_facts(
        self, plan: Plan, photos: PlanPhotos, start: str, now: str
    ) -> LostFacts | None:
        """I fatti delle pagine ``wiki/`` della foto di partenza che adesso non ci sono.

        In un thread, all'apertura del passo di verifica. *now* è la foto
        «prima» appena fatta, cioè il quaderno com'è adesso. Un errore qui non
        ferma il passo: si apre senza il blocco, e il log lo dice.
        """
        try:
            return lost_facts(photos.texts(start, "wiki/"), photos.texts(now, "wiki/"))
        except Exception:  # noqa: BLE001 — un aiuto al controllo, non una condizione del passo
            logger.opt(exception=True).warning("Plan {}: lost facts not computed", plan.id)
            return None

    def _step_message(
        self,
        plan: Plan,
        step: Step,
        *,
        reminder: bool = False,
        lost: LostFacts | None = None,
    ) -> InboundMessage:
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
                "done_when": step.done_when,
            },
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
            waiting=[d["target"] for d in self._plan_drafts(plan)] if not reminder else [],
            # I numeri con unità che c'erano prima del piano e adesso non sono
            # in nessuna pagina (``plan/facts.py``): solo nel passo di verifica.
            # La prova 3 ne ha persi 119 su 384 senza che nessuno lo decidesse.
            lost_facts=[
                {"page": f.page, "fact": f.fact, "context": f.context} for f in lost.items
            ] if lost is not None and not reminder else [],
            lost_total=lost.total if lost is not None else 0,
            # I buchi che i passi prima hanno lasciato dopo il loro giro di
            # ricerca (``_carry_gaps``): solo nel passo di controllo.
            carried_gaps=[
                {"step": str(c.get("step") or ""), "lines": [str(i) for i in c.get("items") or ()]}
                for c in step.extra.get(_CARRIED_GAPS) or ()
                if isinstance(c, dict) and c.get("items")
            ] if not reminder else [],
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
        await self._publish_changed(plan.notebook, plan.id)

    async def _publish_changed(self, notebook: str, plan_id: str) -> None:
        try:
            await self.bus.publish_outbound(
                OutboundMessage(
                    channel="websocket",
                    chat_id="webui",
                    content="",
                    metadata={
                        "_plan_changed": True,
                        "notebook": notebook,
                        "plan_id": plan_id,
                    },
                )
            )
        except Exception as exc:  # noqa: BLE001 — un avviso perso non è un guasto
            logger.debug("plan_changed not delivered for {}: {}", plan_id or notebook, exc)

    # ── bozze di un passo ──────────────────────────────────────────────────

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

        *advance* dice se fotografare il passo e aprire il seguente qui: di
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
            s.note = (f"{note} — {suffix}" if note else suffix)[:_MAX_NOTE]
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

        root = self.host.notebook_root(plan.notebook)
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


def _carry_target(plan: Plan, step: Step) -> Step | None:
    """Il passo di controllo dopo *step* che riceve i suoi buchi rimasti, se c'è."""
    active = _active_steps(plan)
    ids = [s.id for s in active]
    if step.id not in ids:
        return None
    for later in active[ids.index(step.id) + 1:]:
        if later.status == "pending" and _checks_facts(plan, later):
            return later
    return None


def _checks_facts(plan: Plan, step: Step) -> bool:
    """*step* è quello in cui si controllano i fatti persi.

    Ogni passo ``check`` della lista; se non ce n'è nessuno, l'ultimo passo.
    """
    active = _active_steps(plan)
    if any(s.kind == "check" for s in active):
        return step.kind == "check"
    return bool(active) and active[-1].id == step.id


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


def _gaps_refusal(gaps: Mapping[str, list[str]]) -> str:
    """Il rifiuto di ``plan_step_done`` con i buchi aperti: quali, e cosa fare."""
    lines = [
        "Not closed yet: helpers of this step reported gaps under **To research** that "
        "are still open. They are part of this step:",
    ]
    for lineage, items in gaps.items():
        lines.append(f"- helper `{lineage}`:")
        lines.extend(f"  - {item}" for item in items)
    lines.append(
        "Spawn researchers for them now in this step, a few, grouped by subject; when "
        "they are done, send the same helper back with `subagent_send` to fill the pages "
        "from what they saved. If they were already searched and stay empty, or they are "
        "outside this plan, call `plan_step_done` again and say in the note why they stay "
        "open. This check runs once per step."
    )
    return "\n".join(lines)


def _find(plan: Plan, step_id: str) -> Step:
    for step in plan.steps:
        if step.id == step_id:
            return step
    raise PlanError(f"plan {plan.id} has no step {step_id}")


def _block(
    plan: Plan,
    step_id: str,
    question: str,
    reason: str,
    *,
    kind: str = "question",
    drafts: list[dict[str, str]] | None = None,
) -> None:
    step = _find(plan, step_id)
    step.status = "blocked"
    step.extra.pop(_NUDGE, None)
    if plan.status == "pausing":
        # La Pausa chiesta prima del blocco si ricorda: sciolto il blocco, il
        # passo finisce e il piano va in pausa, invece di ripartire.
        step.extra[_RESUME_TO] = "pausing"
    stamp = now_iso()
    apply_transition(plan, "blocked", at=stamp, step=step_id, reason=reason)
    plan.blocked = {
        "step": step_id,
        "question": question,
        "reason": reason,
        "at": stamp,
        "kind": kind,
        "drafts": [dict(d) for d in drafts or ()],
    }


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


def _rollback_note(plan: Plan, counts: Mapping[str, int], drafts: int = 0) -> str:
    return (
        f'The user rolled the notebook back to before the plan "{plan.title}": '
        f"{counts.get('restored', 0)} file(s) restored, {counts.get('removed', 0)} removed, "
        f"{counts.get('left', 0)} left as they were because they changed afterwards"
        + (f", {counts['kept']} kept on purpose" if counts.get("kept") else "")
        + (f", {drafts} pending draft(s) of the plan discarded" if drafts else "")
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
    "plan_view",
    "report_with_groups",
]
