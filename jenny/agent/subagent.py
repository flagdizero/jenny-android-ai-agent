"""Subagent manager for background task execution."""

import asyncio
import contextlib
import json
import math
import re
import time
import uuid
from collections.abc import Awaitable, Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from loguru import logger

from jenny.agent.agent_types import AgentType, UnknownAgentTypeError, get_agent_type
from jenny.agent.hook import (
    AgentHook,
    AgentHookContext,
    CompositeHook,
    ToolResultHookContext,
)
from jenny.agent.runner import AgentRunner, AgentRunSpec
from jenny.agent.subagent_activity import (
    KIND_ERROR,
    KIND_ITERATION,
    KIND_MESSAGE_IN,
    KIND_PHASE,
    KIND_RESULT,
    KIND_THINKING,
    KIND_TOOL_END,
    KIND_TOOL_START,
    STATUS_ERROR,
    STATUS_OK,
    DigestMeta,
    SubagentActivityLog,
    SubagentDigestStore,
    classify_tool_result,
    format_tool_end,
    format_tool_start,
)
from jenny.agent.subagent_history import (
    HISTORY_TTL_S,
    KIND_BUDGET,
    KIND_INTERRUPTED,
    SubagentHistoryStore,
    history_chars,
    light_resume_messages,
)
from jenny.agent.subagent_journal import (
    UndoReport,
    WriteJournal,
    finish_interrupted_undos,
    journal_for,
    journal_stats,
    live_journal,
    sweep_orphan_journals,
)
from jenny.agent.subagent_records import (
    CANCEL_REASON_SHUTDOWN,
    CANCEL_REASON_SUPERSEDED,
    CANCEL_REASON_USER,
    DEFAULT_AGENT_TYPE,
    INTERRUPTED_RESUMABLE_SUMMARY,
    MAX_RESULT_SUMMARY_CHARS,
    STOPPED_BY,
    SubagentRecord,
    SubagentRecordStore,
    SubagentSpec,
    cancellation_stop_reason,
    cancellation_summary,
    default_label,
)
from jenny.agent.tools.context import ToolContext, current_request_context
from jenny.agent.tools.file_state import FileStates
from jenny.agent.tools.loader import ToolLoader, ToolLoadError, declared_tool_name
from jenny.agent.tools.registry import ToolRegistry
from jenny.bus.events import (
    INTERNAL_CHANNEL,
    OUTBOUND_META_SUBAGENT_STATUS,
    InboundMessage,
    OutboundMessage,
)
from jenny.bus.queue import MessageBus
from jenny.config.schema import AgentDefaults, ToolsConfig
from jenny.providers.base import LLMProvider
from jenny.runtime.context import get_android_context
from jenny.security.subagent_policy import (
    SubagentRunPolicy,
    current_turn_taint,
    enter_subagent_policy,
)
from jenny.security.workspace_access import (
    WorkspaceScope,
    current_workspace_scope,
    enter_workspace_scope,
)
from jenny.session.history_meta import INJECTED_EVENT_META
from jenny.session.turn_visibility import resolve_turn_visibility
from jenny.utils.helpers import truncate_text
from jenny.utils.prompt_templates import render_template
from jenny.utils.runtime import (
    BUDGET_NOTICE_EVENT,
    SUBAGENT_BUDGET_FINALIZATION_PROMPT,
    SUBAGENT_BUDGET_NOTICE_TEMPLATE,
)

if TYPE_CHECKING:  # pragma: no cover - solo per i type checker
    from jenny.session.manager import SessionManager

# Slot normali per aiutanti di una sessione con un piano che gira (piu uno breve,
# come sempre). Il limite di config (default 3, cioe 2 normali + 1 breve) e
# pensato per la chat; un passo di piano che divide il lavoro per pianta ne
# vuole di piu, e con 2 alla volta la prova del 24/10/2026 ha messo in fila i
# suoi aiutanti per quasi due ore. Il conteggio resta globale: durante un piano
# il quaderno e bloccato, e gli altri usi aspettano. Il testo dei prompt del
# piano («up to 5 helpers at once») e legato a questa costante.
PLAN_HELPER_SLOTS = 5

# Quante volte un lineage fermato al budget si fa continuare (``send``) prima
# che il manager rifiuti. Senza tetto, budget → ripresa → budget poteva girare
# per sempre: ogni ripresa rimanda tutta la storia, che cresce, e un aiutante che
# non ci sta in tre budget ha un compito troppo grande, non poco tempo. Dopo il
# tetto la mossa giusta e dividere il lavoro, e il rifiuto lo dice.
MAX_BUDGET_RESUMES = 2

def _retry_wait_touch(status: "SubagentStatus") -> Callable[[str], Awaitable[None]]:
    """Il callback delle attese del provider: tiene vivo l'aiutante per il watchdog."""

    async def _touch(_notice: str) -> None:
        status.touch()

    return _touch


async def _await_thread(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """``asyncio.to_thread`` che, se cancellato, aspetta comunque il thread.

    Un thread non si interrompe: con un ``await asyncio.to_thread`` semplice, un
    aiutante cancellato a meta scrittura usciva subito, la done-callback
    cancellava la sua storia e il thread, finendo dopo, la riscriveva. Cosi la
    cancellazione arriva quando la scrittura e finita (pochi ms), e cio che
    segue la fine del run vede il disco gia fermo. Una seconda cancellazione
    durante l'attesa passa.
    """
    future = asyncio.ensure_future(asyncio.to_thread(fn, *args, **kwargs))
    try:
        return await asyncio.shield(future)
    except asyncio.CancelledError:
        if not future.done():
            await asyncio.wait([future])
        raise


# Errori del provider passeggeri (``retry_policy.classify_user_error``): un
# aiutante fermato da uno di questi dopo i tentativi si riprende, non si rifà.
_TRANSIENT_PROVIDER_ERRORS = frozenset({"rate_limit", "server", "timeout", "connection"})

# A che frazione del budget di iterazioni un subagent riceve l'avviso «ti
# restano N chiamate» (``AgentRunSpec.iteration_notice``). L'80% lascia un
# quinto delle chiamate per salvare il lavoro e scrivere il resoconto: meno e
# l'avviso arriva quando non c'e piu tempo di fare niente, prima lo distrae dal
# lavoro quando di tempo ce n'e ancora.
BUDGET_NOTICE_FRACTION = 0.8

# Il testo di ripiego quando anche la chiamata di chiusura a budget esaurito non
# produce niente. Costante perche il ramo budget di ``_run_subagent`` lo
# riconosce per non annunciarlo come se fosse un resoconto.
_NO_FINAL_RESPONSE = "Task completed but no final response was generated."

# Tentativi massimi per lineage nei rilanci *automatici* (orchestratore/codice).
# Il rilancio manuale non e mai capped: a un umano che premo "Relaunch" non si
# risponde no.
MAX_AUTO_ATTEMPTS = 3

# Errori tool recuperabili che un subagent puo commettere prima di arrendersi.
#
# NON zero, che era il comportamento precedente: un subagent gira con
# ``fail_on_tool_error=True`` e qualunque risultato che inizi per "Error" lo
# uccideva, ``stop_reason="tool_error"``. Osservato sul device: un researcher con
# due ``web_search`` e tre ``web_fetch`` andati a segno ha letto un file di output
# spillato con ``read_file(offset=40)`` su un file di meno di 40 righe, ha preso
# "Error: offset 40 is beyond end of file" e ha buttato via un lavoro finito.
# L'agente principale, nella stessa situazione, riceve il retry hint e prosegue.
#
# Tre e il numero concordato: abbastanza per assorbire gli errori di mira che un
# LLM fa su parametri che deve indovinare, troppo pochi per lasciar girare a
# vuoto un subagent che non ha capito il proprio compito. La contabilita
# (consecutivi vs totali vs boundary) sta in ``ToolErrorBudget``.
DEFAULT_TOOL_ERROR_BUDGET = 3

# Quanti lineage terminati restano tracciati in RAM. Oltre questo tetto il
# rilancio si appoggia ai record su disco, che sono la fonte durevole.
_MAX_TRACKED_LINEAGES = 64

# Quanti subagent terminati entrano in ``status_snapshot()["recent"]``. Il
# pannello mostra la coda recente, non lo storico: quello sta nei record.
_SNAPSHOT_RECENT_LIMIT = 10

# Quanto testo del task entra nello snapshot. La card del pannello mostra solo
# l'etichetta, ma la modale di dettaglio mostra il task per intero: senza questo
# campo l'unico modo di sapere *cosa* si e chiesto al subagent e leggere i log.
# Il tetto e generoso e comunque un tetto: lo snapshot viaggia su WebSocket a
# ogni transizione e con cinque subagent in parallelo un task da 50 KB lo
# renderebbe un frame da spedire cinque volte.
_SNAPSHOT_TASK_CHARS = 2000

# Quanti tool event recenti accompagnano un subagent vivo. ``last_tool`` da solo
# dice cosa sta facendo adesso, non come ci e arrivato: la modale mostra la coda,
# ma corta, perche la lista intera cresce per tutta la vita del subagent.
_SNAPSHOT_TOOL_EVENTS_LIMIT = 6

# Profondita della casella di posta di un subagent vivo. Bounded per scelta: se
# l'orchestratore accumula messaggi piu in fretta di quanto il subagent iteri,
# il problema e il flusso, non la coda — meglio un rifiuto leggibile che una
# coda che cresce senza limite su un telefono.
_MAX_PENDING_INJECTIONS = 8

# Riga di progress per *transizione di stato*, non per tool call: con cinque
# subagent in parallelo una riga per tool call rende la chat illeggibile, e il
# dettaglio vivo e proprio cio che il pannello mostra meglio della chat.
_TRANSITION_HINTS = {
    "started": "subagent started",
    "stalled": "subagent stalled (no progress)",
    "done": "subagent done",
    "failed": "subagent failed",
    "budget": "subagent stopped at its budget",
}

# Al massimo uno snapshot di progresso ogni tanti secondi per task (v.
# ``SubagentManager._publish_progress_snapshot``): le transizioni restano
# immediate, il resto e' per la bolla di lavoro della home, a cui basta sapere
# cosa sta facendo l'aiutante con qualche secondo di ritardo.
PROGRESS_SNAPSHOT_EVERY_S = 5.0

# Fase interna -> riga leggibile nel pannello. La fase e un identificatore del
# runner ("awaiting_tools"), il pannello e testo per una persona; la mappa sta qui
# perche e anche cio che rende **stabile** il summary, e il digest tiene solo le
# transizioni confrontando proprio quello (vedi ``build_digest``).
_PHASE_LABELS = {
    "initializing": "starting up",
    "awaiting_tools": "running tools",
    "tools_completed": "tools finished",
    "final_response": "writing the answer",
    "done": "finished",
    "error": "failed",
}

# Finestra di coalescing del segnale di pensiero. 400 ms e scelto, non ereditato:
# sotto i ~250 ms gli aggiornamenti sono piu veloci di quanto un occhio segua e
# consumano il ring per niente; sopra il secondo l'attesa torna a sembrare un
# blocco, che e esattamente cio che il segnale esiste per smentire.
_THINKING_THROTTLE_S = 0.4

# Estratto mostrato e finestra minima perche resti una frase e non un mozzicone.
# 140 sta sotto ``MAX_SUMMARY_CHARS`` (160): il taglio a frase e nostro, e non
# deve essere ritagliato una seconda volta dal cap del log — con l'aggiunta
# dell'etichetta ("thinking: ") il totale resta comunque dentro.
_THINKING_EXCERPT_CHARS = 140
_THINKING_EXCERPT_MIN_CHARS = 40

# Quanto testo di ragionamento resta in RAM per task. Solo la coda serve
# all'estratto: tenere tutto il ragionamento di un run sarebbe megabyte su un
# telefono per mostrarne 140 caratteri.
_THINKING_BUFFER_CHARS = 512

_THINKING_WS_RE = re.compile(r"\s+")
# Fine di frase seguita da spazio. Include ``;`` e ``:`` perche il ragionamento di
# un modello e fatto di elenchi e di clausole, non di prosa.
_THINKING_SENTENCE_RE = re.compile(r"[.!?;:]\s+")


@dataclass(slots=True)
class SubagentStatus:
    """Real-time status of a running subagent."""

    task_id: str
    label: str
    task_description: str
    started_at: float          # time.monotonic()
    phase: str = "initializing"  # initializing | awaiting_tools | tools_completed | final_response | done | error
    iteration: int = 0
    tool_events: list = field(default_factory=list)   # [{name, status, detail}, ...]
    usage: dict = field(default_factory=dict)          # token usage
    stop_reason: str | None = None
    error: str | None = None
    # Identita del lavoro: ``lineage_id`` e stabile tra i rilanci, ``attempt``
    # cresce di uno per rilancio. ``task_id`` resta l'id del singolo tentativo.
    lineage_id: str = ""
    attempt: int = 1
    agent_type: str = DEFAULT_AGENT_TYPE
    # Stato coarse-grained: running | done | failed | cancelled | stalled |
    # budget (fermato al tetto di iterazioni, riprendibile: v. ``SUBAGENT_STATES``).
    # Non sostituisce ``phase``, che risponde a un'altra domanda (cosa sta
    # facendo il subagent adesso) e ha gia consumatori.
    state: str = "running"
    # Provenienza della cancellazione (``SUBAGENT_CANCEL_REASONS``), valorizzata
    # solo quando ``state == "cancelled"``. Viaggia nel record Tier-1, quindi
    # sopravvive al riavvio del gateway: e la sola differenza fra "l'utente ha
    # premuto Stop" e "lo shutdown ha interrotto il lavoro".
    cancel_reason: str | None = None
    # Ultimo segno di vita (monotonic). Senza questo non si distingue "bloccato
    # da 4 minuti" da "al lavoro da 4 minuti": ``started_at`` da solo non basta.
    last_progress_at: float = 0.0
    started_at_wall: float = field(default_factory=time.time)
    result_summary: str = ""
    # Riprese dopo un budget gia spese dal lineage, fino a questo tentativo
    # (v. ``MAX_BUDGET_RESUMES``): passa al record.
    budget_resumes: int = 0
    # Fermato al budget con una ripresa ancora possibile: misurato una volta, nel
    # ramo budget di ``_run_subagent`` e fuori dal loop, e letto da annuncio e
    # record.
    resumable: bool = False
    # Fermato da un errore passeggero del provider (429, 5xx, timeout,
    # connessione) dopo i tentativi: la storia resta salvata, come al budget, e
    # ``subagent_send`` lo riprende invece di rifarlo da zero.
    provider_interrupted: bool = False
    # Questo tentativo ha una fotografia ``interrupted`` della propria
    # conversazione su disco (``SubagentManager._checkpoint_history``): uno
    # shutdown ordinato che lo ferma la tiene invece di cancellarla.
    checkpointed: bool = False
    # Chi l'ha fermato (``subagent_records.STOPPED_BY``): passa al record.
    stopped_by: str | None = None

    def __post_init__(self) -> None:
        if not self.last_progress_at:
            self.last_progress_at = self.started_at

    def touch(self, *, now: float | None = None) -> None:
        """Registra un segno di vita del subagent.

        Un subagent marcato ``stalled`` che riprende a produrre progresso torna
        ``running``: il watchdog marca, non condanna, quindi la marcatura deve
        essere reversibile.
        """
        self.last_progress_at = time.monotonic() if now is None else now
        if self.state == "stalled":
            self.state = "running"


class SubagentConcurrencyLimitError(RuntimeError):
    """Sollevata da ``SubagentManager.spawn`` quando lo spawn supererebbe
    ``max_concurrent_subagents``. Porta i contatori per formattare l'errore
    user-facing al layer chiamante.

    ``reserved`` distingue il rifiuto dovuto allo slot tenuto libero per i job
    quick da quello dovuto al pool davvero pieno."""

    def __init__(self, running: int, limit: int, *, reserved: bool = False) -> None:
        self.running = running
        self.limit = limit
        self.reserved = reserved
        if reserved:
            super().__init__(
                f"concurrency limit reached ({running}/{limit} running, "
                "one slot is reserved for quick tasks)"
            )
        else:
            super().__init__(f"concurrency limit reached ({running}/{limit} running)")


class SubagentCapabilityError(RuntimeError):
    """Il tipo richiesto ha perso tutti i tool dichiarati in ``requires``.

    ``sysadmin`` senza SSH non e un sysadmin con qualche tool in meno: e un
    agente che sa solo leggere file a cui e stato chiesto di amministrare una
    macchina remota, e improvvisera. Meglio rifiutare e dire quale interruttore
    e giu — la frase arriva nello stesso turno, mentre un subagent monco costa
    un giro completo per produrre un non-risultato.

    Sollevata **solo** quando esiste una ``reason``, cioe quando la causa e
    qualcosa che l'utente puo cambiare. Se i tool mancano perche il runtime non
    li ha (i tool web fuori da Android), rifiutare darebbe un consiglio
    impossibile da seguire: in quel caso restano i log e il subagent parte.

    Perdita *parziale* non passa di qui: e un log, non un rifiuto.
    """

    def __init__(self, agent_type: str, tools: tuple[str, ...], reason: str) -> None:
        self.agent_type = agent_type
        self.tools = tools
        self.reason = reason
        super().__init__(
            f"agent type '{agent_type}' cannot run without {', '.join(tools)}: {reason}"
        )


class SubagentRestartError(RuntimeError):
    """Sollevata da ``SubagentManager.restart`` quando il rilancio non e
    possibile (target sconosciuto, o tetto dei tentativi automatici raggiunto).
    Il messaggio e pensato per essere mostrato al chiamante."""


class SubagentUndoError(RuntimeError):
    """Sollevata da ``SubagentManager.undo`` quando l'annulla non si può fare
    adesso (aiutante sconosciuto, o ancora al lavoro). Il testo è user-facing."""


class SubagentSendError(RuntimeError):
    """Sollevata da ``SubagentManager.send`` quando il messaggio non e
    consegnabile ne convertibile in un resume/rilancio (target sconosciuto,
    messaggio vuoto, casella piena). Il testo e user-facing."""


@dataclass(slots=True)
class SubagentSendResult:
    """Esito di ``SubagentManager.send``.

    ``mode`` e la sola informazione che cambia la mossa successiva
    dell'orchestratore — "iniettato in un subagent vivo" non e "ripartito da
    zero" — per questo viaggia strutturato e non solo dentro ``text``.
    """

    mode: str  # injected | resumed | restarted
    text: str
    # Il tentativo che portera la risposta: quello vivo per un'iniezione, quello
    # nuovo per una ripresa o un rilancio. La rotta «continua» della casa lo
    # rimanda al client, che segue il lavoro su quell'id.
    task_id: str | None = None


def _budget_notice(max_iterations: int) -> Callable[[int], str | None]:
    """L'``iteration_notice`` di un run da *max_iterations* chiamate.

    Scatta **una volta**, quando le chiamate fatte arrivano a
    ``floor(BUDGET_NOTICE_FRACTION * max)`` — con ``>=`` e non ``==``: il runner
    lo chiama solo dopo un giro di tool, e un'iterazione chiusa in un altro modo
    (un'iniezione dopo una risposta) non deve far saltare l'avviso. Tace se non
    resta nessuna chiamata: a quel punto la chiusura a budget esaurito chiede gia
    il resoconto, e un avviso in piu non avrebbe dove essere letto.
    """
    threshold = math.floor(BUDGET_NOTICE_FRACTION * max_iterations)
    fired = False

    def _notice(iteration: int) -> str | None:
        nonlocal fired
        done = iteration + 1
        left = max_iterations - done
        if fired or done < threshold or left <= 0:
            return None
        fired = True
        return SUBAGENT_BUDGET_NOTICE_TEMPLATE.format(left=left)

    return _notice


def _without_budget_notice(messages: Any) -> Any:
    """La conversazione da salvare senza l'avviso di budget del run.

    L'avviso («ti restano N chiamate») vale per il run che lo riceve: tornato
    nella storia di una ripresa, che ha un budget nuovo, direbbe una cosa falsa.
    """
    if not isinstance(messages, list):
        return messages
    return [
        m for m in messages
        if not (isinstance(m, dict) and m.get(INJECTED_EVENT_META) == BUDGET_NOTICE_EVENT)
    ]


def _plan_metadata() -> tuple[str | None, str | None]:
    """Piano e passo del turno che sta lanciando un lavoro, dal suo contesto.

    Il task di spawn gira dentro il turno, quindi eredita il
    ``RequestContext`` legato da ``AgentLoop``: nei turni di passo i metadati
    portano le chiavi del driver. Import locale: il driver si porta dietro le
    foto e lo snapshot, che il manager non deve caricare per esistere.
    """
    ctx = current_request_context()
    meta = ctx.metadata if ctx is not None and isinstance(ctx.metadata, dict) else {}
    if not meta:
        return None, None
    from jenny.plan.driver import PLAN_ID_META, PLAN_STEP_META

    plan_id = meta.get(PLAN_ID_META)
    plan_step = meta.get(PLAN_STEP_META)
    return (
        plan_id if isinstance(plan_id, str) and plan_id else None,
        plan_step if isinstance(plan_step, str) and plan_step else None,
    )


def _restamped(spec: SubagentSpec) -> SubagentSpec:
    """La spec di un rimando o di un rilancio con il passo del turno **corrente**.

    Un ``subagent_send`` nel passo 7 a un editor nato nel passo 6 e' lavoro del
    passo 7: col passo di nascita il driver scartava il suo resoconto
    (``note_step_report``: «un altro passo»), e il resoconto apriva un turno suo
    invece di aspettare i fratelli. Senza chiavi nel turno la spec resta com'e'.
    """
    plan_id, plan_step = _plan_metadata()
    if not (plan_id or plan_step):
        return spec
    if (plan_id, plan_step) == (spec.plan_id, spec.plan_step):
        return spec
    return replace(spec, plan_id=plan_id, plan_step=plan_step)


def _append_activity(
    log: SubagentActivityLog | None,
    task_id: str,
    kind: str,
    **fields: Any,
) -> None:
    """Registra un evento di attivita. **Non solleva mai, per contratto.**

    INVARIANTE applicata qui e nient'altrove: un difetto della telemetria non
    puo uccidere un subagent ne fargli perdere una tool call. ``append`` e gia
    scritta per non sollevare per ragioni ordinarie (vedi
    ``SubagentActivityLog.append``), quindi questa guardia copre solo l'imprevisto
    — ed e la stessa linea che il manager tiene sul bus con
    ``try_publish_outbound``: la telemetria e best-effort, il lavoro no.
    """
    if log is None:
        return
    try:
        log.append(task_id, kind, **fields)
    except Exception:  # noqa: BLE001 — vedi docstring
        logger.exception("Subagent activity event dropped for [{}] ({})", task_id, kind)


class _ThinkingSignal:
    """Estratto rotolante del ragionamento, coalescato in una finestra di tempo.

    Perche non uno stream: un provider emette il ragionamento a token, e un
    evento per token riempirebbe il ring (:data:`RING_CAPACITY` = 200) in pochi
    secondi buttando fuori tutto il resto — il pannello mostrerebbe *solo*
    ragionamento, cioe l'unica cosa che il digest poi collassa. Perche non un
    flag "sta pensando": non dice nulla che l'assenza di eventi non dica gia.

    Perche l'estratto e la **coda** del testo e non la testa: la coda e cio a cui
    il modello sta pensando adesso; la testa e cio a cui pensava dieci secondi
    fa, e resta identica per tutta la durata del segmento.

    Un evento viene emesso anche quando l'estratto non e cambiato: cio che cambia
    e ``duration_ms``, ed e quello che permette alla UI di scrivere
    "thinking - 12s" senza tenere un orologio proprio.
    """

    __slots__ = ("_label", "_last_emit", "_pending", "_started", "_text")

    def __init__(self, label: str) -> None:
        self._label = label
        self._text = ""
        self._started = 0.0
        self._last_emit = 0.0
        self._pending = False

    def feed(self, chunk: Any, *, now: float) -> tuple[str, int] | None:
        """Aggiunge testo e ritorna ``(summary, elapsed_ms)`` se e ora di emettere."""
        if not isinstance(chunk, str) or not chunk:
            return None
        self._text = (self._text + chunk)[-_THINKING_BUFFER_CHARS:]
        self._pending = True
        if not self._started:
            self._started = now
        if self._last_emit and now - self._last_emit < _THINKING_THROTTLE_S:
            return None
        return self._take(now)

    def flush(self, *, now: float) -> tuple[str, int] | None:
        """Emette l'aggiornamento coalescato non ancora uscito, se c'e.

        Chiamata alla fine di un segmento: senza di lei la coda del ragionamento
        — la parte piu vicina a cio che il modello ha deciso — sarebbe proprio
        quella che la finestra di throttle fa sparire.
        """
        return self._take(now) if self._pending else None

    def reset(self) -> None:
        """Chiude il segmento: il prossimo riparte con elapsed da zero."""
        self._text = ""
        self._started = 0.0
        self._last_emit = 0.0
        self._pending = False

    def _take(self, now: float) -> tuple[str, int]:
        self._pending = False
        self._last_emit = now
        elapsed_ms = int(max(0.0, now - self._started) * 1000)
        return f"{self._label}: {_thinking_excerpt(self._text)}", elapsed_ms


def _thinking_excerpt(text: str) -> str:
    """Coda del ragionamento tagliata perche si legga come una frase.

    Un troncamento secco a N caratteri produce "...ttribuire il calo di traffi",
    che chiede al lettore di indovinare. Quindi il taglio non e alla lunghezza ma
    a un **confine**: dentro la finestra si riparte dalla *prima* fine di frase —
    quindi dall'estratto piu lungo che inizi comunque all'inizio di un pensiero —
    e in mancanza di una fine di frase dal primo spazio. La soglia
    :data:`_THINKING_EXCERPT_MIN_CHARS` scarta i confini troppo a destra, che
    lascerebbero due parole al posto di una frase. Il risultato inizia dove inizia
    un pensiero e finisce dove il modello e arrivato adesso.
    """
    flat = _THINKING_WS_RE.sub(" ", text).strip()
    if len(flat) <= _THINKING_EXCERPT_CHARS:
        return flat
    window = flat[-_THINKING_EXCERPT_CHARS:]
    for match in _THINKING_SENTENCE_RE.finditer(window):
        if len(window) - match.end() >= _THINKING_EXCERPT_MIN_CHARS:
            return window[match.end():]
    _, _, rest = window.partition(" ")
    return rest or window


class _SubagentHook(AgentHook):
    """Hook di un subagent: aggiorna lo status e produce la sua attivita viva.

    I produttori stanno qui e non nel manager perche l'hook e l'unico oggetto che
    il runner chiama nei momenti giusti — inizio di una tool call, **fine di una
    singola** tool call, fine iterazione, chunk di ragionamento. Prima esisteva
    solo ``after_iteration``, che copia gli eventi una volta per iterazione: un
    ``web_fetch`` da 8 secondi non produceva un aggiornamento finche non finiva
    tutto il batch.

    Le note che il runner non conosce (fase, messaggio dall'orchestratore, esito
    terminale) arrivano dal manager attraverso i metodi ``note_*``: cosi l'intera
    attivita di un task ha un solo produttore, con un solo punto di guardia.
    """

    def __init__(
        self,
        task_id: str,
        status: SubagentStatus | None = None,
        *,
        activity: SubagentActivityLog | None = None,
        on_iteration: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self._task_id = task_id
        self._status = status
        self._activity = activity
        # Chiamato a fine iterazione, dopo l'aggiornamento dello status: e' cosi'
        # che la bolla di lavoro della home sa cosa sta facendo l'aiutante adesso
        # e non solo quando cambia stato (v. ``_publish_progress_snapshot``).
        self._on_iteration = on_iteration
        self._reasoning = _ThinkingSignal("thinking")
        # Il testo della risposta e un secondo segnale vivo, distinto dal
        # ragionamento: e il risultato che si sta formando. Stesso ``kind``
        # (l'enum non ne ha uno per "sta scrivendo", e il digest li collassa
        # entrambi), etichetta diversa nel summary.
        self._writing = _ThinkingSignal("writing")

    # -- produzione ----------------------------------------------------------

    def _emit(self, kind: str, **fields: Any) -> None:
        _append_activity(self._activity, self._task_id, kind, **fields)

    def _emit_thinking(self, signal: _ThinkingSignal, update: tuple[str, int] | None) -> None:
        if update is None:
            return
        summary, elapsed_ms = update
        self._emit(KIND_THINKING, summary=summary, duration_ms=elapsed_ms)
        # Un ragionamento lungo E progresso: senza questo il watchdog vedrebbe
        # fermo un subagent che sta pensando da tre minuti.
        if self._status is not None:
            self._status.touch()

    # -- hook del runner -----------------------------------------------------

    def wants_streaming(self) -> bool:
        """Chiede al runner la risposta in streaming. Serve a rendere l'attivita
        *viva*, non solo dettagliata.

        Senza questo il subagent riceve un segnale per iterazione: mentre l'LLM
        ragiona per venti secondi la modale resta immobile, che era esattamente la
        lamentela. L'altro path (``progress_callback`` +
        ``supports_progress_deltas``) non e utilizzabile: nessun provider dichiara
        quel flag.

        Costo dichiarato: con lo streaming il runner **non** applica il wall
        timeout esterno (``request_execution``, ~riga 192). Non e una protezione
        persa ma spostata — lo streaming ha il proprio idle timeout
        (``JENNY_STREAM_IDLE_TIMEOUT_S``, default 90s), che e la forma giusta del
        vincolo: si mette un tetto al *silenzio*, non alla durata di un
        ragionamento sano. Un provider che smette di produrre delta viene comunque
        interrotto, e il watchdog di stallo resta la seconda rete.
        """
        return True

    async def on_stream(self, context: AgentHookContext, delta: str) -> None:
        """Delta del testo di risposta: alimenta il segnale ``writing``.

        Throttlato e ridotto a un estratto dal segnale stesso — qui non arriva
        mai testo grezzo alla UI.
        """
        self._emit_thinking(self._writing, self._writing.feed(delta, now=time.monotonic()))

    async def on_stream_end(self, context: AgentHookContext, *, resuming: bool) -> None:
        """Chiude il segmento: la coda va emessa, non scartata dalla finestra.

        Su ``resuming`` il testo continua nella stessa risposta, quindi il segnale
        non viene resettato: azzerarlo farebbe ripartire l'elapsed a zero in mezzo
        a una singola generazione.
        """
        self._emit_thinking(self._writing, self._writing.flush(now=time.monotonic()))
        if not resuming:
            self._writing.reset()

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        for tool_call in context.tool_calls:
            args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
            logger.debug(
                "Subagent [{}] executing: {} with arguments: {}",
                self._task_id, tool_call.name, args_str,
            )
            self._emit(
                KIND_TOOL_START,
                summary=format_tool_start(tool_call.name, tool_call.arguments),
                name=tool_call.name,
                # Id di chiamata del provider: e cio che fa accoppiare questo
                # start con il proprio end anche quando tre chiamate dello stesso
                # tool sono in volo insieme.
                call_id=tool_call.id,
            )

    async def after_execute_tool(self, context: ToolResultHookContext) -> None:
        self._emit(
            KIND_TOOL_END,
            summary=format_tool_end(
                context.name,
                context.arguments,
                context.result,
                error=context.error,
            ),
            name=context.name,
            call_id=context.call_id,
            status=classify_tool_result(context.name, context.result, context.error),
            duration_ms=context.duration_ms,
        )
        # Una tool call finita e un segno di vita: con un solo tool lentissimo per
        # iterazione era l'unico che esistesse, e non veniva registrato.
        if self._status is not None:
            self._status.touch()

    async def emit_reasoning(self, reasoning_content: str | None) -> None:
        self._emit_thinking(
            self._reasoning, self._reasoning.feed(reasoning_content, now=time.monotonic())
        )

    async def emit_reasoning_end(self) -> None:
        self._emit_thinking(self._reasoning, self._reasoning.flush(now=time.monotonic()))
        self._reasoning.reset()

    async def after_iteration(self, context: AgentHookContext) -> None:
        # La coda del testo di risposta prima dell'evento di iterazione: e l'ultima
        # cosa accaduta dentro l'iterazione, quindi e dove va letta.
        self._emit_thinking(self._writing, self._writing.flush(now=time.monotonic()))
        self._writing.reset()
        self._emit(KIND_ITERATION, summary=f"iteration {context.iteration}")
        if self._status is None:
            return
        self._status.iteration = context.iteration
        self._status.tool_events = list(context.tool_events)
        self._status.usage = dict(context.usage)
        if context.error:
            self._status.error = str(context.error)
        # Fine iterazione = progresso osservabile: e uno dei due punti in cui il
        # watchdog di stallo viene disarmato (l'altro e il checkpoint).
        self._status.touch()
        if self._on_iteration is not None:
            try:
                self._on_iteration()
            except Exception:  # noqa: BLE001 — la telemetria non ferma il run
                logger.debug("Subagent [{}] progress snapshot failed", self._task_id)

    # -- note dal manager ----------------------------------------------------

    def note_output(self, content: Any) -> None:
        """Delta di testo della risposta (dal ``progress_callback`` del run)."""
        self._emit_thinking(self._writing, self._writing.feed(content, now=time.monotonic()))

    def note_phase(self, phase: Any) -> None:
        """Transizione di fase (dal ``checkpoint_callback`` del run)."""
        if not isinstance(phase, str) or not phase:
            return
        self._emit(KIND_PHASE, summary=_PHASE_LABELS.get(phase, phase.replace("_", " ")))

    def note_message_in(self, count: int) -> None:
        """Messaggi dell'orchestratore appena consumati da questo subagent.

        Solo il conteggio, non il testo: il messaggio arriva dall'agente
        principale, che a sua volta cita risultati di subagent — cioe contenuto
        non fidato — e da qui finirebbe in una UI.
        """
        if count <= 0:
            return
        plural = "message" if count == 1 else "messages"
        self._emit(
            KIND_MESSAGE_IN,
            summary=f"received {count} {plural} from the orchestrator",
        )

    def note_result(self, chars: int) -> None:
        """Esito terminale buono: la misura del risultato, non il risultato."""
        self._emit(
            KIND_RESULT,
            summary=f"task completed, {chars} characters of result",
            status=STATUS_OK,
        )

    def note_error(self, summary: str) -> None:
        """Esito terminale cattivo. ``summary`` deve essere testo **nostro**."""
        self._emit(KIND_ERROR, summary=summary, status=STATUS_ERROR)


def split_allow_by_scope(
    loader: ToolLoader, agent_type: AgentType
) -> dict[str, frozenset[str] | None]:
    """Ripartisce l'allowlist di un tipo fra i suoi ``scopes``, validandola una volta.

    ``ToolLoader.load`` valida l'``allow`` che riceve contro i soli tool dello
    scope che sta caricando, e una voce che li non trova nessuno e un
    ``ToolLoadError`` **fatale** che aborta lo startup del gateway. La guardia e
    giusta — un typo in un agent type deve esplodere, non far girare un subagent
    con meno tool del previsto — ma e per-scope, e un tipo multi-scope come
    ``sysadmin`` la farebbe scattare a vuoto: ``ssh_exec`` non esiste nello scope
    ``subagent``, e passargli l'allowlist intera impedirebbe il boot.

    Quindi l'allowlist si valida qui **una volta contro l'unione** degli scope
    del tipo, e a ogni ``load`` si passa solo la fetta di sua competenza. Il
    risultato e quello voluto in entrambi i versi: un nome che non esiste in
    NESSUNO degli scope resta un errore fatale, un nome che esiste in uno solo
    non lo e piu.

    ``tools=None`` (``operator``) non e un'allowlist vuota: significa "tutto lo
    scope", e resta ``None`` per ogni scope.
    """
    if agent_type.tools is None:
        return {scope: None for scope in agent_type.scopes}

    discovered = loader.discover()
    by_scope: dict[str, frozenset[str] | None] = {}
    known: set[str] = set()
    for scope in agent_type.scopes:
        names = {
            name
            for cls in discovered
            if scope in getattr(cls, "_scopes", {"core"}) and (name := declared_tool_name(cls))
        }
        known |= names
        by_scope[scope] = frozenset(agent_type.tools & names)

    unknown = sorted(set(agent_type.tools) - known)
    if unknown:
        raise ToolLoadError(
            f"Unknown tool name(s) in allow list for agent type '{agent_type.name}': "
            f"{', '.join(unknown)}. Known tools in scope(s) "
            f"{', '.join(agent_type.scopes)}: {', '.join(sorted(known))}."
        )
    return by_scope


def _tool_classes(loader: ToolLoader) -> dict[str, type]:
    return {name: cls for cls in loader.discover() if (name := declared_tool_name(cls))}


def unavailable_tools(
    loader: ToolLoader, names: Iterable[str], ctx: Any
) -> tuple[str, ...]:
    """Quali fra ``names`` esistono come classe ma ``enabled()`` nega adesso.

    Non istanzia niente: ``enabled()`` e un classmethod che legge la sola
    config, quindi il controllo costa una lettura di attributi e si puo fare
    *prima* di avviare qualcosa.
    """
    by_name = _tool_classes(loader)
    missing: list[str] = []
    for name in sorted(names):
        cls = by_name.get(name)
        if cls is None:
            continue
        try:
            available = cls.enabled(ctx)
        except Exception:
            # Un ``enabled()`` che solleva e gia trattato come "spento" dal
            # loader (con log ERROR): qui si concorda, senza duplicare.
            available = False
        if not available:
            missing.append(name)
    return tuple(missing)


def first_disabled_reason(loader: ToolLoader, names: Iterable[str], ctx: Any) -> str | None:
    """La prima spiegazione *azionabile* fra i tool indicati, se ce n'e una.

    ``None`` non significa "non so perche": significa che nessuno di quei tool
    sa indicare qualcosa che l'utente possa cambiare. E la distinzione che
    separa "l'hai spento tu" da "questo runtime non ce l'ha", e su cui si regge
    la decisione di rifiutare o no uno spawn.
    """
    by_name = _tool_classes(loader)
    for name in sorted(names):
        cls = by_name.get(name)
        if cls is None:
            continue
        with contextlib.suppress(Exception):
            if reason := cls.disabled_reason(ctx):
                return reason
    return None


def unavailable_by_scope(
    loader: ToolLoader, agent_type: AgentType, ctx: Any
) -> dict[str, tuple[str, ...]]:
    """Per ogni scope del tipo, i tool che l'allowlist chiede e ``enabled()`` nega.

    ``split_allow_by_scope`` valida l'allowlist contro i tool che esistono *come
    classe*; ``ToolLoader.load`` poi salta quelli spenti dalla config. Fra i due
    c'e un buco per cui un tipo puo perdere la propria ragione d'essere senza
    che nessuno lo dica: ``sysadmin`` partiva con i soli tool sul filesystem e
    rispondeva "il tool SSH non era disponibile", che si legge come una scusa
    inventata dal modello e invece era la verita.

    Qui non si istanzia niente: ``enabled()`` e un classmethod e legge solo la
    config, quindi il controllo costa quanto una lettura di attributi e si puo
    fare *prima* di avviare il subagent.

    ``tools=None`` (``operator``) significa "tutto lo scope": non c'e nessuna
    promessa esplicita da tradire, quindi lo scope risulta sempre vuoto qui.
    """
    return {
        scope: () if allow is None else unavailable_tools(loader, allow, ctx)
        for scope, allow in split_allow_by_scope(loader, agent_type).items()
    }


@dataclass(slots=True)
class _Lineage:
    """Stato in RAM di un lineage: la spec da rigiocare e l'ultimo tentativo."""

    lineage_id: str
    spec: SubagentSpec
    attempt: int
    task_id: str
    # Riprese dopo un budget spese dal lineage (``MAX_BUDGET_RESUMES``), e lo
    # stato terminale dell'ultimo tentativo (``None`` finche e vivo): bastano a
    # ``send`` per decidere senza rileggere i record da disco.
    budget_resumes: int = 0
    last_state: str | None = None


# Quanto del compito torna nell'annuncio. Il testo intero e' gia' nella
# cronologia, negli argomenti della ``spawn``: ripeterlo raddoppiava ogni
# delega nel contesto (un compito da 10 k caratteri tornava tale e quale).
_ANNOUNCE_TASK_CHARS = 240


def _task_head(task: str) -> str:
    """La prima riga del compito, quanto basta a riconoscerlo."""
    text = (task or "").strip()
    head = text.splitlines()[0] if text else ""
    if len(head) > _ANNOUNCE_TASK_CHARS:
        head = head[:_ANNOUNCE_TASK_CHARS].rstrip() + "…"
    if head != text:
        head += " (the full brief is in your spawn call)"
    return head



def _merge_taint(*groups: tuple[str, ...]) -> tuple[str, ...]:
    """Unisce fonti di taint senza doppioni, nel tetto di ``SubagentSpec``."""
    merged: list[str] = []
    for group in groups:
        for source in group:
            if source not in merged:
                merged.append(source)
    return tuple(merged[:8])

class SubagentManager:
    """Manages background subagent execution."""

    # Ogni quanto, al piu, la conversazione di un aiutante vivo si fotografa su
    # disco (``KIND_INTERRUPTED``), per riprenderlo se Android uccide il
    # processo: e il lavoro che un kill puo far perdere. Oltre
    # ``HISTORY_CHECKPOINT_LARGE_BYTES`` di file l'intervallo raddoppia: misurato
    # il 25/10/2026 sul Mac, una storia da 300 messaggi (risultati da 1-10 kB)
    # pesa 0,6-1,3 MB e costa 2-4 ms; il tempo e trascurabile (e in un thread), i byte scritti no —
    # cinque aiutanti grandi ogni 30 s sono ~10 MB al minuto sulla flash.
    HISTORY_CHECKPOINT_INTERVAL_S = 30.0
    HISTORY_CHECKPOINT_LARGE_INTERVAL_S = 60.0
    HISTORY_CHECKPOINT_LARGE_BYTES = 512 * 1024
    # Quanto l'annulla aspetta che il ``python_exec`` di un aiutante fermato
    # esca dal suo thread, prima di rifiutare.
    UNDO_WAIT_FOR_THREADS_S = 10.0

    def __init__(
        self,
        provider: LLMProvider,
        workspace: Path,
        bus: MessageBus,
        max_tool_result_chars: int,
        model: str | None = None,
        tools_config: ToolsConfig | None = None,
        tools_config_provider: Callable[[], ToolsConfig] | None = None,
        disabled_skills: list[str] | None = None,
        max_iterations: int | None = None,
        max_concurrent_subagents: int | None = None,
        llm_wall_timeout_for_session: Callable[[str | None], float | None] | None = None,
        stall_threshold_s: float | None = None,
        stall_check_interval_s: float = 15.0,
        tool_error_budget: int | None = None,
        record_store: SubagentRecordStore | None = None,
        *,
        session_manager: "SessionManager | None" = None,
        history_store: SubagentHistoryStore | None = None,
        usage_hooks: Iterable[AgentHook] = (),
        cron_service: Any | None = None,
        timezone: str = "UTC",
    ):
        defaults = AgentDefaults()
        self.provider = provider
        # Il servizio cron e il fuso dell'utente, per i tool che li chiedono al
        # ctx. Li vuole il tool ``board`` dell'editore dei quaderni: togliere una
        # scheda mette in pausa i cron del suo cassetto, e una nota diventa una
        # riga di diario con l'ora locale. Fino al 09/10/2026 il ctx dei subagent
        # non li aveva: sull'emulatore l'editore toglieva Prunus e il suo
        # promemoria restava armato. Il tool ``cron`` resta fuori lo stesso: il
        # suo scope non e' ``subagent``.
        self.cron_service = cron_service
        self.timezone = timezone
        # Gli hook di **misura** del loop (oggi ``TokenUsageHook``), montati su
        # ogni run accanto a ``_SubagentHook``. Senza, un subagent girava nel suo
        # ``AgentRunner`` e i suoi token non arrivavano mai in
        # ``token-usage.json``. La spesa si registra
        # sotto la chiave della sessione che l'ha lanciato: il runner del
        # subagent porta quella, non una ``subagent:``.
        self.usage_hooks: list[AgentHook] = list(usage_hooks)
        self.workspace = workspace
        self.bus = bus
        self.model = model or provider.get_default_model()
        self.tools_config = tools_config or ToolsConfig()
        # Sorgente della config *corrente*, consultata a ogni costruzione di
        # registry. Senza, resta la copia presa all'avvio: vedi
        # :meth:`_live_tools_config`.
        self._tools_config_provider = tools_config_provider
        self.max_tool_result_chars = max_tool_result_chars
        self.disabled_skills = set(disabled_skills or [])
        self.max_iterations = (
            max_iterations
            if max_iterations is not None
            else defaults.max_tool_iterations
        )
        self.max_concurrent_subagents = (
            max_concurrent_subagents
            if max_concurrent_subagents is not None
            else defaults.max_concurrent_subagents
        )
        self.stall_threshold_s = (
            float(stall_threshold_s)
            if stall_threshold_s is not None
            else float(defaults.subagent_stall_threshold_seconds)
        )
        # Knob al pari di ``stall_threshold_s``/``max_iterations``: iniettato dal
        # chiamante, con il default del modulo quando non lo specifica. Zero e
        # legittimo e significa "il primo errore chiude il subagent" (il vecchio
        # comportamento), negativo viene normalizzato a zero dal budget.
        self.tool_error_budget = (
            int(tool_error_budget)
            if tool_error_budget is not None
            else DEFAULT_TOOL_ERROR_BUDGET
        )
        self.runner = AgentRunner(provider)
        self._llm_wall_timeout_for_session = llm_wall_timeout_for_session
        self._running_tasks: dict[str, asyncio.Task[None]] = {}
        self._task_statuses: dict[str, SubagentStatus] = {}
        self._session_tasks: dict[str, set[str]] = {}  # session_key -> {task_id, ...}
        # task_id -> ultimo snapshot di progresso (monotonic): il freno di
        # ``_publish_progress_snapshot``.
        self._progress_published_at: dict[str, float] = {}
        # Subagent abbandonati da cancel_by_session (task che non muore entro
        # il grace) o superati da un rilancio: il loro announce tardivo va
        # soppresso, non deve iniettare un turno stale nella sessione.
        self._repudiated_task_ids: set[str] = set()
        # Politica di ogni lavoro vivo (``security.subagent_policy``): l'annuncio
        # la legge per sapere se il risultato va incorniciato come non fidato.
        self._run_policies: dict[str, SubagentRunPolicy] = {}
        self._lineages: dict[str, _Lineage] = {}
        # Casella di posta per subagent vivo, consumata dal runner via
        # ``injection_callback``. Creata allo spawn, rimossa alla terminazione.
        self._pending_injections: dict[str, asyncio.Queue[str]] = {}
        # Telemetria viva (RAM, a perdere) e sua condensa durevole (un file per
        # task). Nomi PUBBLICI di proposito: sono il contratto verso il transport,
        # che legge ``manager.activity`` e ``manager.digests`` e non deve conoscere
        # nient'altro del manager.
        self.activity = SubagentActivityLog()
        self.digests = SubagentDigestStore(workspace)
        # Lo store dei record riceve i digest: cosi la potatura di un record porta
        # via il proprio digest invece di lasciarlo su disco per sempre.
        self._records = record_store or SubagentRecordStore(
            workspace, digest_store=self.digests
        )
        # SessionManager INIETTATO, mai costruito qui: il suo ``_cache`` e
        # per-istanza, quindi due manager sulla stessa directory sarebbero due
        # cache divergenti sugli stessi file. Senza sessioni la storia Tier-2 e
        # semplicemente disabilitata e ``send`` degrada al rilancio.
        self._history = history_store or SubagentHistoryStore(session_manager)
        self._stall_check_interval_s = max(0.01, stall_check_interval_s)
        self._watchdog_task: asyncio.Task[None] | None = None
        # Limite di slot normali per sessione, iniettato dal container (il driver
        # dei piani): ``None`` dal provider = il valore di config. V.
        # :meth:`set_limit_provider`.
        self._limit_provider: Callable[[str | None], int | None] | None = None
        # ``mark_handled`` arrivato mentre il tentativo e ancora vivo (il
        # resoconto parte prima che la done-callback scriva il record):
        # applicato da :meth:`_retain`.
        self._handled_pending: dict[str, float] = {}
        # task id -> chiave del file dei record, per ``mark_handled``: senza,
        # ogni fine turno leggeva tutti i file dei record per trovarne uno.
        # Riempito da ``_retain`` e dalle letture dei record; potato al tetto.
        self._record_keys: dict[str, str] = {}
        # Chi vuole sapere quando il watchdog marca ``stalled`` degli aiutanti
        # (il driver dei piani: un risveglio tenuto non deve aspettare chi non
        # va piu avanti). V. :meth:`set_stall_listener`.
        self._stall_listener: Callable[[str, list[str]], None] | None = None
        # Orologio del freno dei checkpoint; iniettabile dai test.
        self._clock: Callable[[], float] = time.monotonic
        # I registri delle scritture per l'annulla (``agent.subagent_journal``),
        # uno per lineage, condivisi fra i tentativi di questo processo.
        self._journals: dict[str, WriteJournal] = {}
        # File cambiati e ultimo annulla dei lineage dei processi precedenti,
        # letti dal disco una volta: lo snapshot si pubblica ogni pochi secondi.
        self._journal_cache: dict[str, tuple[int, UndoReport | None]] = {}
        # Prima la ripresa dei tentativi morti col processo precedente, poi la
        # pulizia dei digest: guarda i record, e quelli nuovi devono esserci.
        self.recover_interrupted()
        self.sweep_orphan_digests()
        self.recover_journals()

    def recover_interrupted(self) -> int:
        """Chiude i tentativi morti col processo precedente. Ritorna quanti.

        Ogni tentativo lascia un segnaposto su disco finche e vivo
        (:meth:`SubagentRecordStore.mark_running`); al boot, prima che ne parta
        uno nuovo, un segnaposto rimasto vuol dire che il processo e morto mentre
        l'aiutante lavorava (Android lo uccide: force-stop, memoria, update).
        Diventa un record terminale ``cancelled`` con provenienza ``shutdown`` —
        la stessa di uno shutdown ordinato, e la casa e l'orchestratore la sanno
        gia leggere — e, se l'aiutante aveva una storia da cui ripartire,
        ``resumable``: :meth:`interrupted_helpers` lo offre al driver dei piani e
        ``send`` lo riprende da li.

        Riprendibile vuol dire una storia del lineage entro TTL che sia lavoro
        non finito: la fotografia ``interrupted`` di un checkpoint, o una
        ``budget`` (la ripresa dopo un budget morta prima del suo primo
        checkpoint). Una ``done`` di un tentativo precedente no: riprenderla
        ripartirebbe dal resoconto, non dal lavoro interrotto — resta su disco e
        ``send`` la usa come per ogni lavoro finito.

        Un segnaposto con gia il suo record terminale (kill fra la scrittura del
        record e la cancellazione del segnaposto) si toglie e basta.
        Best-effort: un boot non fallisce per questo.
        """
        try:
            markers = self._records.load_running()
        except Exception as e:  # noqa: BLE001 — vedi docstring
            logger.warning("Subagent running markers unreadable: {}", e)
            return 0
        if not markers:
            return 0
        try:
            closed = {r.task_id for r in self._records.load_all()}
        except Exception:  # noqa: BLE001
            closed = set()
        recovered = 0
        for marker in markers:
            if marker.task_id in closed:
                self._records.clear_running(marker.task_id)
                continue
            entry = self._history.entry_of(marker.lineage_id)
            resumable = entry is not None and entry[0] in (KIND_INTERRUPTED, KIND_BUDGET)
            # L'ultimo segno di vita noto: la fotografia da cui si riparte.
            last_alive = marker.started_at
            if resumable and entry is not None:
                last_alive = max(last_alive, entry[1])
            record = SubagentRecord(
                task_id=marker.task_id,
                lineage_id=marker.lineage_id,
                attempt=marker.attempt,
                spec=marker.spec,
                state="cancelled",
                phase=marker.phase,
                stop_reason=cancellation_stop_reason(CANCEL_REASON_SHUTDOWN),
                result_summary=(
                    INTERRUPTED_RESUMABLE_SUMMARY if resumable
                    else cancellation_summary(CANCEL_REASON_SHUTDOWN)
                ),
                iteration=marker.iteration,
                cancel_reason=CANCEL_REASON_SHUTDOWN,
                started_at=marker.started_at,
                ended_at=last_alive,
                resumable=resumable,
                budget_resumes=marker.budget_resumes,
            )
            try:
                self._records.append(record)
            except Exception as e:  # noqa: BLE001 — il segnaposto resta: si riprova al boot dopo
                logger.warning(
                    "Could not close interrupted subagent [{}]: {}", marker.task_id, e,
                )
                continue
            self._records.clear_running(marker.task_id)
            recovered += 1
            logger.info(
                "Subagent [{}] lineage={} was running when the process died (resumable={})",
                marker.task_id, marker.lineage_id, resumable,
            )
        return recovered

    def interrupted_helpers(self, session_key: str) -> list[dict[str, Any]]:
        """Gli aiutanti di *session_key* interrotti da un riavvio e riprendibili.

        Per il driver dei piani, dopo un boot: invece di rifare un passo da zero,
        riprende i suoi aiutanti con ``send(lineage_id, ...)``. Una voce per
        lineage, dal tentativo piu vecchio al piu nuovo::

            {"task_id": str, "lineage_id": str, "label": str, "agent_type": str,
             "plan_id": str | None, "plan_step": str | None}

        Entra l'ultimo tentativo di un lineage se e ``cancelled`` per
        ``shutdown`` (morto col processo, o fermato da uno shutdown ordinato dopo
        almeno un checkpoint), e ``resumable`` e la sua storia c'e ancora (TTL,
        potatura). Esce appena il lineage ha un tentativo piu nuovo (vivo, o
        finito: la ripresa e partita) o il record e marcato gestito
        (:meth:`mark_handled`, il modo di scartarlo senza riprenderlo).
        Legge il file dei record della sessione: da chiamare a un evento (il
        boot, un risveglio), non in un ciclo stretto.
        """
        newer = {st.lineage_id for st in self._task_statuses.values()}
        out: list[dict[str, Any]] = []
        for record in reversed(self.list_records(session_key)):
            if record.lineage_id in newer:
                continue
            newer.add(record.lineage_id)
            if not self._interrupted_resumable(record):
                continue
            if record.handled_at is not None or not self._history.has(record.lineage_id):
                continue
            out.append({
                "task_id": record.task_id,
                "lineage_id": record.lineage_id,
                "label": record.spec.label,
                "agent_type": record.spec.agent_type,
                "plan_id": record.spec.plan_id,
                "plan_step": record.spec.plan_step,
            })
        out.reverse()
        return out

    @staticmethod
    def _interrupted_resumable(record: SubagentRecord) -> bool:
        """Il record e un tentativo interrotto da un riavvio con una storia da riprendere."""
        return (
            record.state == "cancelled"
            and record.cancel_reason == CANCEL_REASON_SHUTDOWN
            and record.resumable
        )

    def _journal_root(self) -> Path | None:
        """La radice dell'installazione, se il workspace è un path vero.

        Come ``SubagentRecordStore.running_root``: i test costruiscono il manager
        con doppi del workspace, e un registro non deve creare cartelle a caso.
        """
        return Path(self.workspace) if isinstance(self.workspace, (str, Path)) else None

    def recover_journals(self) -> None:
        """Al boot: finisce gli annulla interrotti, poi toglie i registri orfani.

        Prima gli annulla: un kill a metà annulla ha lasciato file mezzi rimessi,
        e finirli viene prima di qualunque lavoro nuovo. Poi i registri dei
        lineage senza più record (TTL e potatura): nessuno può più chiederne
        l'annulla. Best-effort: un boot non fallisce per questo.
        """
        root = self._journal_root()
        if root is None:
            return
        try:
            finished = finish_interrupted_undos(root)
            if finished:
                logger.info("Finished {} interrupted subagent undo(s)", finished)
            keep = {r.lineage_id for r in self._records.load_all()}
            keep |= {m.lineage_id for m in self._records.load_running()}
            removed = sweep_orphan_journals(root, keep)
            if removed:
                logger.info("Swept {} orphan subagent journal(s)", removed)
        except Exception as e:  # noqa: BLE001 — vedi docstring
            logger.warning("Subagent journal recovery failed: {}", e)

    def _journal_for(self, lineage_id: str) -> WriteJournal | None:
        """Il registro del lineage, creato alla prima richiesta."""
        root = self._journal_root()
        if root is None or not lineage_id:
            return None
        journal = self._journals.get(lineage_id)
        if journal is None:
            try:
                journal = journal_for(root, lineage_id)
            except Exception as e:  # noqa: BLE001 — senza registro si lavora lo stesso
                logger.warning("Subagent journal unavailable for {}: {}", lineage_id, e)
                return None
            self._journals[lineage_id] = journal
            self._journal_cache.pop(lineage_id, None)
        return journal

    def _journal_view(self, lineage_id: str) -> tuple[int, UndoReport | None]:
        """File cambiati dal lineage e il suo ultimo annulla, per lo snapshot."""
        journal = self._journals.get(lineage_id)
        if journal is None and (root := self._journal_root()) is not None:
            # Un registro vivo che il manager non ha aperto: la pubblicazione di
            # un'app annota nel registro del suo app-builder (``app_tool``).
            journal = live_journal(root, lineage_id)
        cached = self._journal_cache.get(lineage_id)
        if cached is None:
            # Dal disco una volta per lineage: l'ultimo annulla sta in ``undo.json``.
            root = self._journal_root()
            try:
                cached = journal_stats(root, lineage_id) if root is not None else (0, None)
            except Exception:  # noqa: BLE001 — uno snapshot non fallisce per il registro
                cached = (0, None)
            if len(self._journal_cache) > 256:
                self._journal_cache.clear()
            self._journal_cache[lineage_id] = cached
        if journal is not None:
            return journal.changed_files, cached[1]
        return cached

    def sweep_orphan_digests(self) -> int:
        """Cancella i digest di attivita senza record. Chiamata una volta all'avvio.

        Copre l'unica finestra che la retention non puo coprire: il digest viene
        scritto **prima** del record (vedi :meth:`_retain`), quindi un processo
        ucciso tra le due lascia un file di cui nessun record parla — e la
        potatura cancella i digest guardando i record che escono, quindi di
        quell'orfano non si accorgerebbe mai. Su un telefono e una perdita lenta.

        Best-effort: un boot non fallisce per una pulizia.
        """
        try:
            removed = self.digests.keep_only({r.task_id for r in self._records.load_all()})
        except Exception as e:  # noqa: BLE001 — vedi docstring
            logger.warning("Subagent activity digest sweep failed: {}", e)
            return 0
        if removed:
            logger.info("Swept {} orphan subagent activity digest(s)", removed)
        return removed

    def _mark_cancelled(self, task_id: str, reason: str) -> SubagentStatus | None:
        """Marca il tentativo vivo ``task_id`` come cancellato, con provenienza.

        UNICO punto in cui uno status passa a ``cancelled``: la provenienza deve
        essere scritta insieme allo stato, non dopo, perche il record Tier-1 la
        legge dallo status appena la done-callback lo estrae dal dict — e quel
        momento non e sotto il controllo di chi cancella.

        Oltre al campo strutturato scrive ``stop_reason`` e ``result_summary``:
        sono i due campi che ``subagent_status`` mostra all'orchestratore, e una
        regola che i dati portano vale piu di una scritta nel prompt.
        """
        status = self._task_statuses.get(task_id)
        if status is None:
            return None
        status.state = "cancelled"
        status.cancel_reason = reason
        if (stop_reason := cancellation_stop_reason(reason)) is not None:
            status.stop_reason = stop_reason
        status.result_summary = truncate_text(
            cancellation_summary(reason), MAX_RESULT_SUMMARY_CHARS
        )
        # Registrato qui e non nei rami di ``_run_subagent``: una cancellazione non
        # ne attraversa nessuno (il task muore su ``CancelledError``), quindi senza
        # questo evento il digest finirebbe senza dire *come* il lavoro e finito.
        _append_activity(
            self.activity, task_id, KIND_ERROR,
            summary=f"cancelled ({reason})", status=STATUS_ERROR,
        )
        return status

    def _live_tools_config(self) -> ToolsConfig:
        """La ``ToolsConfig`` corrente, non quella catturata all'avvio.

        Quale tool *esiste* lo decide ``enabled()``, che legge da qui; cosa fa
        una volta chiamato lo decide il suo corpo, che rilegge la config a ogni
        chiamata (``ssh_transport.resolve_target`` carica da disco). Leggere una
        copia vecchia solo nel primo dei due punti produceva un'asimmetria
        assurda: togliere un host aveva effetto subito, aggiungerne uno no —
        finche non si riavviava l'app.

        Il provider e iniettato invece che letto qui dentro perche questo modulo
        non deve sapere che esiste un file: i test passano una config e basta, e
        il percorso vero resta uno solo.
        """
        if self._tools_config_provider is None:
            return self.tools_config
        try:
            return self._tools_config_provider()
        except Exception as exc:
            # Config illeggibile o corrotta: si degrada alla copia nota. Perdere
            # il subagent sarebbe peggio, ma in silenzio sarebbe peggio ancora.
            logger.warning(
                "Could not read the current tools config, using the startup copy: {}", exc
            )
            return self.tools_config

    def _subagent_tools_config(self) -> ToolsConfig:
        """Build a ToolsConfig scoped for subagent use."""
        live = self._live_tools_config()
        return ToolsConfig(
            python_exec=live.python_exec,
            android_web=live.android_web,
            file=live.file,
            # Senza questo il tipo ``sysadmin`` non vedrebbe MAI i tool SSH:
            # ``enabled()`` legge il toggle e gli host da qui, e una ToolsConfig
            # ricostruita senza ``ssh`` porta i default (spento, zero host). Il
            # gate vero resta l'allowlist del tipo, non l'assenza della config.
            ssh=live.ssh,
            # ATTENZIONE: questo NON viene da ``live``. Sull'oggetto vivo
            # ``restrict_to_workspace`` e il valore *risolto* da ``AgentLoop``
            # all'avvio, non quello scritto nel file; ripescarlo dal disco
            # insieme al resto cancellerebbe la risoluzione, e con essa il
            # confine dello workspace.
            restrict_to_workspace=self.tools_config.restrict_to_workspace,
        )

    def _build_tools(
        self,
        workspace: Path | None = None,
        tools_config: ToolsConfig | None = None,
        agent_type: AgentType | None = None,
        origin_session_key: str | None = None,
        lineage_id: str | None = None,
    ) -> ToolRegistry:
        """Build an isolated subagent tool registry via ToolLoader.

        L'allowlist del tipo e il *secondo* filtro dopo lo scope: ``_scopes`` e
        per-classe e globale, quindi non sa distinguere un researcher da un
        coder (vedi ``agent_types.py``). ``agent_type=None`` o un tipo con
        ``tools=None`` lascia l'intero scope subagent, come prima dei tipi.

        Gli scope del tipo si caricano uno alla volta sullo *stesso* registry:
        un tipo come ``sysadmin`` prende ``subagent`` + ``remote``. La
        ripartizione dell'allowlist fra gli scope, e la sua validazione,
        stanno in :func:`split_allow_by_scope` — che va letta prima di
        toccare questo metodo.
        """
        root = self.workspace if workspace is None else workspace
        registry = ToolRegistry()
        cfg = tools_config if tools_config is not None else self._subagent_tools_config()
        ctx = self._tool_context(root, cfg, origin_session_key, lineage_id)
        loader = ToolLoader()
        if agent_type is None:
            loader.load(ctx, registry, scope="subagent", allow=None)
            return registry
        for scope, allow in split_allow_by_scope(loader, agent_type).items():
            loader.load(ctx, registry, scope=scope, allow=allow)
        self._log_unavailable(loader, agent_type, ctx)
        return registry

    def _tool_context(self, root: Path, cfg: ToolsConfig,
                      origin_session_key: str | None = None,
                      lineage_id: str | None = None) -> ToolContext:
        """Il ctx che i tool ricevono. Estratto perche lo usa anche il pre-volo."""
        return ToolContext(
            config=cfg,
            workspace=str(root.resolve()),
            file_states=FileStates(),
            android_context=get_android_context(),
            cron_service=self.cron_service,
            timezone=self.timezone,
            origin_session_key=origin_session_key,
            subagent_lineage_id=lineage_id,
        )

    @staticmethod
    def _log_unavailable(loader: ToolLoader, agent_type: AgentType, ctx: ToolContext) -> None:
        """Traccia i tool *chiesti per nome* e non caricati.

        Solo quelli: l'insieme dei tool spenti in generale e grande e noioso,
        quello dei tool che un tipo aveva dichiarato di volere e piccolo ed e
        sempre interessante. Era la riga di log che mancava quando ``sysadmin``
        e partito senza SSH.
        """
        for scope, missing in unavailable_by_scope(loader, agent_type, ctx).items():
            if missing:
                logger.info(
                    "Agent type '{}' declared {} tool(s) in scope '{}' that are "
                    "switched off and were not loaded: {}",
                    agent_type.name, len(missing), scope, ", ".join(missing),
                )

    def set_limit_provider(self, fn: Callable[[str | None], int | None] | None) -> None:
        """Inietta il limite di slot *normali* per sessione.

        ``fn(session_key)`` ritorna quanti aiutanti normali quella sessione puo
        avere insieme (piu uno breve), o ``None`` per il valore di config. Il
        container ci passa il driver dei piani (:data:`PLAN_HELPER_SLOTS` per la
        sessione con un piano che gira): il manager non sa cosa sia un piano, e
        non deve saperlo.
        """
        self._limit_provider = fn

    def set_stall_listener(self, fn: Callable[[str, list[str]], None] | None) -> None:
        """Inietta chi ascolta le marcature ``stalled`` del watchdog.

        ``fn(session_key, task_ids)`` riceve, per sessione d'origine, gli
        aiutanti appena marcati. Lo cabla il driver dei piani attraverso il suo
        ospite (``LoopPlanHost.watch_stalls``): i resoconti di un passo tenuti in
        attesa dei fratelli si svegliano se i fratelli rimasti sono tutti fermi.
        Come per :meth:`set_limit_provider`, il manager non sa cosa sia un piano.
        """
        self._stall_listener = fn

    def helper_plan_step(self, task_id: str) -> tuple[str | None, str | None] | None:
        """Piano e passo per cui lavora (o ha lavorato) *task_id*, se lo si sa ancora.

        Dalla spec del lineage in RAM: vale anche per un tentativo appena finito
        (il resoconto si legge quando la done-callback puo essere gia passata).
        ``None`` se il task non e piu tracciato. Sola lettura.
        """
        spec = self._spec_for_task(task_id)
        if spec is None:
            return None
        return spec.plan_id, spec.plan_step

    def live_helper_states(
        self, session_key: str,
    ) -> dict[str, tuple[str, str | None, str | None]]:
        """``task_id -> (state, plan_id, plan_step)`` dei tentativi vivi della sessione.

        Solo RAM, nessun disco (a differenza di :meth:`status_snapshot`, che
        legge i record): la chiede il risveglio unico dei piani a ogni resoconto
        di un aiutante. ``state`` e ``running`` o ``stalled``.
        """
        out: dict[str, tuple[str, str | None, str | None]] = {}
        for task_id in self.get_running_ids_by_session(session_key):
            status = self._task_statuses.get(task_id)
            if status is None:
                continue
            spec = self._spec_for_task(task_id)
            out[task_id] = (
                status.state,
                spec.plan_id if spec is not None else None,
                spec.plan_step if spec is not None else None,
            )
        return out

    def set_provider(self, provider: LLMProvider, model: str) -> None:
        self.provider = provider
        self.model = model
        self.runner.provider = provider

    # ------------------------------------------------------------------
    # spawn / capacity
    # ------------------------------------------------------------------

    async def spawn(
        self,
        task: str,
        label: str | None = None,
        origin_channel: str = "internal",
        origin_chat_id: str = "direct",
        session_key: str | None = None,
        origin_message_id: str | None = None,
        temperature: float | None = None,
        workspace_scope: WorkspaceScope | None = None,
        *,
        agent_type: str = DEFAULT_AGENT_TYPE,
        quick: bool = False,
    ) -> str:
        """Spawn a subagent to execute a task in the background.

        Solleva ``SubagentConcurrencyLimitError`` se lo spawn violerebbe
        l'invariante di concorrenza: e applicata qui, dove il manager possiede
        lo stato, cosi ogni chiamante (tool, cron, path interni) ne e vincolato.
        """
        spec = SubagentSpec(
            task=task,
            label=label or default_label(task),
            agent_type=agent_type,
            temperature=temperature,
            workspace_scope=workspace_scope,
            origin_channel=origin_channel,
            origin_chat_id=origin_chat_id,
            session_key=session_key,
            origin_message_id=origin_message_id,
            quick=quick,
            # Un compito scritto mentre un risultato non fidato è in vista nasce
            # contaminato (``security.subagent_policy``, taint del turno).
            taint_seed=current_turn_taint(),
        )
        plan_id, plan_step = _plan_metadata()
        if plan_id or plan_step:
            spec = replace(spec, plan_id=plan_id, plan_step=plan_step)
        else:
            # La spia, non una regola: un aiutante nato in una sessione col
            # piano in corsa da un turno senza il passo non contera' per il
            # passo. Sul Titan (26/10) lo si e' letto solo nei record, a mano.
            # Non si assegna il passo da qui: durante un piano girano anche
            # turni che non sono suoi, e un aiutante attribuito per sbaglio
            # terrebbe fermo ``plan_step_done`` e finirebbe nella sua foto.
            from jenny.session.goal_state import plan_running_in_session

            if plan_running_in_session(session_key):
                logger.warning(
                    "Subagent spawned in {} while its plan runs, from a turn without a plan "
                    "step: its report will not count for the step ({})",
                    session_key, _task_head(task)[:80],
                )
        return await self._spawn_spec(spec)

    def _session_limit(self, session_key: str | None) -> int | None:
        """Slot normali concessi a *session_key* dal provider iniettato, o ``None``."""
        if self._limit_provider is None:
            return None
        try:
            value = self._limit_provider(session_key)
        except Exception:  # noqa: BLE001 — un provider rotto non blocca gli spawn
            logger.exception("Subagent limit provider failed; using the config limit")
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            return None
        return value

    def _check_capacity(self, *, quick: bool, session_key: str | None = None) -> None:
        """Invariante di concorrenza, con uno slot sempre libero.

        Uno spawn normale puo occupare al massimo ``max_concurrent - 1`` slot:
        senza riserva cinque ricercatori lunghi saturano il pool e
        l'orchestratore non ha piu modo di far partire un job breve per
        rispondere all'utente. Con limite 1 la riserva si annulla, altrimenti
        una configurazione a slot singolo non spawnerebbe piu nulla.

        Con un limite per sessione (:meth:`set_limit_provider`) i numeri sono
        quelli: ``n`` normali piu uno breve, contati sullo stesso totale globale.
        """
        # Conta solo i task ancora vivi: un rilancio arriva subito dopo aver
        # cancellato il tentativo precedente, che puo essere ancora nel dict in
        # attesa della propria done-callback.
        running = sum(1 for t in self._running_tasks.values() if not t.done())
        normal = self._session_limit(session_key)
        if normal is not None:
            # Mai meno della chat: un utente che ha alzato il limite di config
            # oltre i posti di un piano non li perde dentro un piano.
            normal = max(normal, self.max_concurrent_subagents - 1)
            limit = normal + 1
            if running >= limit:
                raise SubagentConcurrencyLimitError(running, limit)
            if not quick and running >= normal:
                raise SubagentConcurrencyLimitError(running, limit, reserved=True)
            return
        limit = self.max_concurrent_subagents
        if running >= limit:
            raise SubagentConcurrencyLimitError(running, limit)
        if not quick:
            normal_limit = max(1, limit - 1)
            if running >= normal_limit:
                raise SubagentConcurrencyLimitError(running, limit, reserved=True)

    def _check_capabilities(self, spec: SubagentSpec) -> None:
        """Rifiuta un tipo rimasto senza *nessuno* dei tool che lo definiscono.

        Due condizioni, entrambe necessarie. Che manchi l'intero ``requires``:
        finche ne resta uno il tipo puo ancora fare qualcosa di sensato, e il
        resto e materia da log. E che qualcuno sappia dire **perche** in termini
        che l'utente possa agire — senza quella frase il rifiuto sarebbe solo un
        secondo modo di non fare il lavoro.
        """
        try:
            agent_type = get_agent_type(spec.agent_type)
        except UnknownAgentTypeError:
            return  # Gestito da SubagentSpec, non e affare di questo controllo.
        if not agent_type.requires:
            return

        scope = spec.workspace_scope
        root = scope.project_path if scope is not None else self.workspace
        cfg = self._subagent_tools_config()
        if scope is not None:
            cfg.restrict_to_workspace = scope.restrict_to_workspace
        loader = ToolLoader()
        ctx = self._tool_context(root, cfg)

        missing = unavailable_tools(loader, agent_type.requires, ctx)
        if set(missing) != set(agent_type.requires):
            return
        reason = first_disabled_reason(loader, missing, ctx)
        if reason is None:
            logger.warning(
                "Agent type '{}' has none of its required tools ({}), but nothing "
                "can explain why in user terms — spawning anyway",
                agent_type.name, ", ".join(missing),
            )
            return
        raise SubagentCapabilityError(agent_type.name, missing, reason)

    async def _spawn_spec(
        self,
        spec: SubagentSpec,
        *,
        lineage_id: str | None = None,
        attempt: int = 1,
    ) -> str:
        """Avvia un tentativo per ``spec`` e ritorna il testo per il chiamante."""
        task_id, _ = await self._launch(spec, lineage_id=lineage_id, attempt=attempt)
        suffix = "" if attempt <= 1 else f" (attempt {attempt})"
        return (
            f"Subagent [{spec.label}] started{suffix} (id: {task_id}). "
            "I'll notify you when it completes."
        )

    async def _launch(
        self,
        spec: SubagentSpec,
        *,
        lineage_id: str | None = None,
        attempt: int = 1,
        resume_messages: list[dict[str, Any]] | None = None,
        budget_resumes: int = 0,
    ) -> tuple[str, str]:
        """Avvia un tentativo per ``spec`` su un lineage nuovo o esistente.

        Ritorna ``(task_id, lineage_id)``. Con ``resume_messages`` il subagent
        riparte dalla conversazione salvata invece che dal solo task: e un loop
        LLM completo come uno spawn, quindi passa dallo stesso
        :meth:`_check_capacity` (slot riservato incluso).
        """
        self._check_capacity(quick=spec.quick, session_key=spec.session_key)
        self._check_capabilities(spec)

        task_id = str(uuid.uuid4())[:8]
        lineage = lineage_id or str(uuid.uuid4())[:8]
        status = SubagentStatus(
            task_id=task_id,
            label=spec.label,
            task_description=spec.task,
            started_at=time.monotonic(),
            lineage_id=lineage,
            attempt=attempt,
            agent_type=spec.agent_type,
            state="running",
            budget_resumes=budget_resumes,
        )
        self._task_statuses[task_id] = status
        self._track_lineage(
            _Lineage(lineage, spec, attempt, task_id, budget_resumes=budget_resumes)
        )
        self._pending_injections[task_id] = asyncio.Queue(maxsize=_MAX_PENDING_INJECTIONS)

        bg_task = asyncio.create_task(
            self._run_subagent(task_id, spec, status, resume_messages=resume_messages)
        )
        self._running_tasks[task_id] = bg_task
        session_key = spec.session_key
        if session_key:
            self._session_tasks.setdefault(session_key, set()).add(task_id)

        def _cleanup(finished: asyncio.Task) -> None:
            self._running_tasks.pop(task_id, None)
            # Lo status non viene piu buttato via: la terminazione e una
            # *transizione* verso un record Tier-1 su disco. Col pop distruttivo
            # tutto cio che serve a ispezionare o rilanciare il subagent
            # svaniva nell'istante in cui finiva.
            final = self._task_statuses.pop(task_id, None)
            self._progress_published_at.pop(task_id, None)
            self._repudiated_task_ids.discard(task_id)
            self._discard_injections(task_id)
            if session_key and (ids := self._session_tasks.get(session_key)):
                ids.discard(task_id)
                if not ids:
                    del self._session_tasks[session_key]
            state = self._terminal_state(finished, final)
            tracked = self._lineages.get(lineage)
            if tracked is not None and tracked.task_id == task_id:
                tracked.last_state = state
            self._retain(finished, final, spec, lineage, attempt)
            interrupted = (
                state == "failed" and final is not None
                and getattr(final, "provider_interrupted", False)
            ) or self._kept_at_shutdown(state, final)
            if state not in ("done", "budget") and not interrupted:
                # Invariante: la storia Tier-2 riflette solo un lineage il cui
                # ultimo tentativo e andato a buon fine, o si e fermato al budget
                # con la conversazione integra. Un fallimento, uno stop o un
                # tentativo superato la eliminano — riprendere da un transcript
                # che il lineage ha poi abbandonato darebbe una continuazione
                # incoerente. Il budget no: fino al 24/10/2026 cadeva qui come
                # ``failed`` e ``subagent_send`` ripartiva sempre da zero. Il drop
                # invalida anche il cache del SessionManager, quindi la RAM non
                # cresce.
                self._history.drop(lineage)
            # Il segnaposto «in corso» si toglie solo ora, a record scritto: un
            # kill fra i due lascia entrambi, e il boot sa riconoscerlo.
            self._records.clear_running(task_id)
            # Snapshot pubblicato DOPO il retain e dopo il pop dello status: solo
            # qui il subagent e uscito da ``running`` ed e entrato in ``recent``,
            # quindi lo snapshot non mostra lo stesso task in entrambe le liste.
            self._publish_transition(spec, state, spec.label)

        bg_task.add_done_callback(_cleanup)
        self._ensure_stall_watchdog()
        self._publish_transition(spec, "started", spec.label)

        logger.info(
            "Spawned subagent [{}] lineage={} attempt={} resumed={}: {}",
            task_id, lineage, attempt, bool(resume_messages), spec.label,
        )
        return task_id, lineage

    @staticmethod
    def _kept_at_shutdown(state: str, status: SubagentStatus | None) -> bool:
        """Fermato da uno shutdown ordinato con una fotografia della conversazione.

        Lo stesso caso di un kill, solo con la done-callback che gira: la storia
        ``interrupted`` resta e il record nasce riprendibile, come quello che
        :meth:`recover_interrupted` scrive al boot dopo un kill.
        """
        return (
            state == "cancelled" and status is not None
            and status.cancel_reason == CANCEL_REASON_SHUTDOWN
            and status.checkpointed
        )

    def _track_lineage(self, lineage: _Lineage) -> None:
        """Registra il lineage in RAM, potando i piu vecchi non attivi."""
        self._lineages[lineage.lineage_id] = lineage
        if len(self._lineages) <= _MAX_TRACKED_LINEAGES:
            return
        for key, lin in list(self._lineages.items()):
            if len(self._lineages) <= _MAX_TRACKED_LINEAGES:
                break
            task = self._running_tasks.get(lin.task_id)
            if task is None or task.done():
                del self._lineages[key]

    # ------------------------------------------------------------------
    # execution
    # ------------------------------------------------------------------

    async def _run_subagent(
        self,
        task_id: str,
        spec: SubagentSpec,
        status: SubagentStatus,
        *,
        resume_messages: list[dict[str, Any]] | None = None,
    ) -> None:
        """Execute the subagent task and announce the result."""
        label = spec.label
        task = spec.task
        origin = spec.origin
        origin_message_id = spec.origin_message_id
        workspace_scope = spec.workspace_scope
        logger.info("Subagent [{}] starting task: {}", task_id, label)
        hook = _SubagentHook(
            task_id, status, activity=self.activity,
            on_iteration=lambda: self._publish_progress_snapshot(task_id, spec),
        )
        # Primo evento del task, prima di qualunque I/O: la modale che si apre su
        # un subagent appena nato deve mostrare qualcosa, non una lista vuota.
        hook.note_phase(status.phase)

        # La fotografia della conversazione durante il run (``_checkpoint_history``):
        # la prima non prima di un intervallo dalla partenza, poi al piu una per
        # intervallo. Non l'app-builder: lo si esclude gia dalla ripresa leggera,
        # perche il suo seguito vive del codice letto e dei risultati di ``app
        # run``; un costruttore ripreso a meta da una fotografia ripartirebbe da
        # un ``draft`` che nel frattempo puo essere cambiato.
        checkpoints = self._history.enabled and spec.agent_type != "app-builder"
        next_checkpoint = [self._clock() + self.HISTORY_CHECKPOINT_INTERVAL_S]
        # I messaggi con cui il run parte, riempita sotto: il runner aggiunge in
        # coda a una sua copia, e il checkpoint ne porta solo la parte nuova.
        initial: list[dict[str, Any]] = []
        run_policy: list[SubagentRunPolicy] = []

        async def _on_checkpoint(payload: dict) -> None:
            phase = payload.get("phase", status.phase)
            if phase != status.phase:
                hook.note_phase(phase)
            status.phase = phase
            status.iteration = payload.get("iteration", status.iteration)
            # Secondo punto di stamp del progresso: un subagent che fa un solo
            # tool call lunghissimo passa dai checkpoint, non dalle iterazioni.
            status.touch()
            # Solo a giro di tool chiuso: ogni tool call ha il suo risultato, e
            # la storia si riprende cosi com'e. Il freno si guarda prima di
            # costruire qualunque lista.
            if not checkpoints or phase != "tools_completed" or self._clock() < next_checkpoint[0]:
                return
            size = await self._checkpoint_history(
                status, spec, initial, payload,
                run_policy[0].taint_sources() if run_policy else (),
            )
            interval = (
                self.HISTORY_CHECKPOINT_LARGE_INTERVAL_S
                if size is not None and size >= self.HISTORY_CHECKPOINT_LARGE_BYTES
                else self.HISTORY_CHECKPOINT_INTERVAL_S
            )
            next_checkpoint[0] = self._clock() + interval

        # Il segnaposto «in corso» (``SubagentRecordStore.mark_running``): cio che,
        # dopo un kill, dice al boot che questo lavoro c'era. Scritto in un thread
        # mentre il run parte, e aspettato nel ``finally``: la done-callback che
        # lo toglie non puo arrivare prima che sia scritto.
        marker = asyncio.ensure_future(
            asyncio.to_thread(self._mark_running, status, spec)
        )
        try:
            root = workspace_scope.project_path if workspace_scope is not None else self.workspace
            cfg = None
            if workspace_scope is not None:
                cfg = self._subagent_tools_config()
                cfg.restrict_to_workspace = workspace_scope.restrict_to_workspace
            atype = get_agent_type(spec.agent_type)
            tools = self._build_tools(workspace=root, tools_config=cfg, agent_type=atype,
                                      origin_session_key=spec.session_key,
                                      lineage_id=status.lineage_id or None)
            system_prompt = self._build_subagent_prompt(
                workspace=root,
                agent_type=atype,
                tool_names=tools.tool_names,
                # Lo scope della spec, non l'ambiente: il prompt si costruisce
                # qui fuori, il ``with`` che lega e' piu' sotto.
                workspace_scope=workspace_scope,
            )
            messages: list[dict[str, Any]]
            if resume_messages:
                messages = list(resume_messages)
                if messages[0].get("role") != "system":
                    # Una storia potata dalla testa (``enforce_file_cap``) puo
                    # aver perso il proprio system prompt: rimetterne uno fresco
                    # e sempre meglio che far girare un subagent senza ruolo.
                    messages.insert(0, {"role": "system", "content": system_prompt})
            else:
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": task},
                ]

            sess_key = origin.get("session_key")
            llm_timeout = (
                self._llm_wall_timeout_for_session(sess_key)
                if self._llm_wall_timeout_for_session
                else None
            )
            # La politica del lavoro (``security.subagent_policy``) si misura
            # sempre dalla radice dell'installazione, anche per un subagent di
            # quaderno: è lì che stanno config.json e le sue copie.
            policy = SubagentRunPolicy.for_run(
                task_id, Path(self.workspace), seed=spec.taint_seed, task=spec.task,
            )
            # Il registro delle scritture del lineage, per l'annulla: i recinti
            # di scrittura ci annotano il «prima», il runner il «dopo».
            policy.journal = self._journal_for(status.lineage_id or task_id)
            if policy.journal is not None:
                policy.journal.begin(task_id, status.attempt)
                # Un lineage che riparte dopo un annulla non è più «annullato».
                self._journal_cache.pop(status.lineage_id, None)
            self._run_policies[task_id] = policy
            initial.extend(messages)
            run_policy.append(policy)
            max_iterations = self._type_max_iterations(atype)
            with enter_workspace_scope(workspace_scope), enter_subagent_policy(policy):
                result = await self.runner.run(AgentRunSpec(
                    initial_messages=messages,
                    tools=tools,
                    model=atype.model or self.model,
                    # Una temperatura passata allo spawn e una scelta esplicita
                    # del chiamante e vince sul default del tipo.
                    temperature=(
                        spec.temperature if spec.temperature is not None
                        else atype.temperature
                    ),
                    max_iterations=max_iterations,
                    max_tool_result_chars=self.max_tool_result_chars,
                    hook=(
                        CompositeHook([hook, *self.usage_hooks]) if self.usage_hooks else hook
                    ),
                    max_iterations_message=_NO_FINAL_RESPONSE,
                    # A budget finito una chiamata in piu, senza tool, per il
                    # resoconto all'orchestratore: fatto, file, cosa resta. Fino
                    # al 24/10/2026 era spenta, e l'annuncio portava solo gli
                    # ultimi tre tool — Jenny non sapeva cosa far continuare.
                    finalize_on_max_iterations=True,
                    max_iterations_finalization_prompt=SUBAGENT_BUDGET_FINALIZATION_PROMPT,
                    # «Ti restano N chiamate» una volta, verso la fine: senza,
                    # un aiutante non sapeva di avere un budget e si fermava a
                    # meta di una lettura, senza aver scritto niente.
                    iteration_notice=_budget_notice(max_iterations),
                    # Le letture nella stessa risposta girano insieme (solo i
                    # tool ``concurrency_safe``; le scritture restano in fila).
                    # Fino al 24/10/2026 un aiutante le faceva una per
                    # iterazione: 39 ``read_file`` in 39 chiamate al modello.
                    concurrent_tools=True,
                    # Un errore tool resta fatale, ma non il primo: il budget
                    # lascia sbagliare qualche colpo (col retry hint, come
                    # l'agente principale) e chiude solo quando l'agente non sta
                    # piu recuperando. Vedi ``DEFAULT_TOOL_ERROR_BUDGET``.
                    fail_on_tool_error=True,
                    tool_error_budget=self.tool_error_budget,
                    checkpoint_callback=_on_checkpoint,
                    # Il ``progress_callback`` del subagent non scrive in chat: e
                    # cio che apre lo streaming incrementale del runner (vedi
                    # ``wants_progress_streaming`` in ``request_execution.py``),
                    # quindi e la differenza fra "ragionamento consegnato in blocco
                    # a fine iterazione" e "ragionamento visibile mentre accade".
                    # La firma e deliberatamente stretta: senza ``file_edit_events``
                    # ne ``tool_events`` il runner non attiva i tracker di
                    # file-edit, che sono UI della chat principale e non di qui.
                    progress_callback=self._make_progress_callback(hook),
                    # Iniezione mid-run: la stessa macchina che ``AgentLoop`` usa
                    # per i messaggi di follow-up dell'utente. Un ``subagent_send``
                    # su un subagent vivo NON lo ferma, entra alla sua prossima
                    # iterazione.
                    injection_callback=self._make_injection_callback(task_id, hook),
                    session_key=sess_key,
                    workspace=root,
                    llm_timeout_s=llm_timeout,
                    # Nessuno aspetta la risposta di un aiutante: su un 429 o un
                    # 5xx aspetta minuti, non secondi (``_BACKGROUND_RETRY_DELAYS``).
                    provider_retry_mode="background",
                    # Un'attesa del provider è un segno di vita: quasi tre minuti
                    # di tentativi passerebbero la soglia del watchdog (180 s), e
                    # il piano conterebbe fermo un aiutante che sta solo aspettando.
                    retry_wait_callback=_retry_wait_touch(status),
                ))
            status.phase = "done"
            status.stop_reason = result.stop_reason

            if result.stop_reason == "tool_error":
                hook.note_error("stopped: too many failed tool calls")
                status.state = "failed"
                status.tool_events = list(result.tool_events)
                partial = self._format_partial_progress(result)
                status.result_summary = truncate_text(partial, MAX_RESULT_SUMMARY_CHARS)
                await self._announce_result(
                    task_id, label, task,
                    partial,
                    origin, "error", origin_message_id,
                )
            elif result.stop_reason == "error":
                # Frase nostra, non ``result.error``: quel testo puo portare un
                # messaggio di provider, una URL o un frammento di pagina, e da
                # qui finirebbe in una UI e nel digest su disco.
                hook.note_error("stopped: the run failed")
                status.state = "failed"
                error_text = result.error or "Error: subagent execution failed."
                status.result_summary = truncate_text(error_text, MAX_RESULT_SUMMARY_CHARS)
                if getattr(result, "error_kind", None) in _TRANSIENT_PROVIDER_ERRORS:
                    # Il provider, non il lavoro: nella riprova del 25/10 otto
                    # aiutanti chiusi da un 429 sono stati rifatti da zero. La
                    # conversazione è integra fino all'errore: si salva come al
                    # budget, e la done-callback non la cancella.
                    status.provider_interrupted = True
                    self._history.save(
                        status.lineage_id,
                        spec.records_key,
                        _without_budget_notice(getattr(result, "messages", None)),
                        taint=policy.taint_sources(),
                        kind=KIND_BUDGET,
                    )
                    status.resumable = await asyncio.to_thread(
                        self._history_continuable, status.lineage_id
                    )
                await self._announce_result(
                    task_id, label, task,
                    error_text,
                    origin, "error", origin_message_id,
                    resumable=status.resumable,
                    interrupted=status.provider_interrupted,
                )
            elif result.stop_reason == "max_iterations":
                # Il budget di iterazioni finito **non** e' un successo:
                # cadeva nel ramo buono, e l'annuncio diceva
                # «completed successfully» con il testo di ripiego del runner come
                # risultato. Si annuncia per quel che e': il resoconto della
                # chiamata di chiusura, se c'e', e i passi fatti. Stato
                # ``budget``, non ``failed``: la conversazione e' integra, la
                # storia Tier-2 si salva come sull'esito buono e la done-callback
                # non la cancella — continuarla con ``subagent_send`` e' il rimedio.
                hook.note_error("stopped: iteration budget exhausted")
                status.state = "budget"
                status.tool_events = list(result.tool_events)
                report = self._budget_report(result)
                status.result_summary = truncate_text(report, MAX_RESULT_SUMMARY_CHARS)
                logger.info("Subagent [{}] stopped at its iteration budget", task_id)
                self._history.save(
                    status.lineage_id,
                    spec.records_key,
                    _without_budget_notice(getattr(result, "messages", None)),
                    taint=policy.taint_sources(),
                    kind=KIND_BUDGET,
                )
                # Una volta per stop e fuori dal loop: la misura rilegge e
                # serializza la storia appena salvata. Oltre il tetto delle
                # riprese non si offre piu di continuare.
                status.resumable = (
                    status.budget_resumes < MAX_BUDGET_RESUMES
                    and await asyncio.to_thread(self._history_continuable, status.lineage_id)
                )
                await self._announce_result(
                    task_id, label, task, report, origin, "budget", origin_message_id,
                    resumable=status.resumable,
                    budget_stops=status.budget_resumes + 1,
                )
            else:
                final_result = result.final_content or _NO_FINAL_RESPONSE
                hook.note_result(len(final_result))
                status.state = "done"
                status.result_summary = truncate_text(final_result, MAX_RESULT_SUMMARY_CHARS)
                logger.info("Subagent [{}] completed successfully", task_id)
                # Storia Tier-2 salvata solo sull'esito buono, e PRIMA
                # dell'announce: l'orchestratore puo rispondere al risultato con
                # un ``subagent_send`` nello stesso turno, e a quel punto la
                # storia deve essere gia su disco.
                self._history.save(
                    status.lineage_id,
                    spec.records_key,
                    _without_budget_notice(getattr(result, "messages", None)),
                    taint=policy.taint_sources(),
                )
                await self._announce_result(task_id, label, task, final_result, origin, "ok", origin_message_id)

        except Exception as e:
            # Solo il nome di classe dell'eccezione, per la stessa ragione del
            # ramo ``error``: il messaggio e testo di provenienza ignota.
            hook.note_error(f"crashed ({type(e).__name__})")
            status.phase = "error"
            status.state = "failed"
            status.error = str(e)
            status.result_summary = truncate_text(f"Error: {e}", MAX_RESULT_SUMMARY_CHARS)
            logger.exception("Subagent [{}] failed", task_id)
            await self._announce_result(task_id, label, task, f"Error: {e}", origin, "error", origin_message_id)
        finally:
            # Le scritture dello strumento interrotto (una cancellazione arriva
            # a metà di un tool): il runner non è arrivato al suo «dopo».
            if run_policy and run_policy[0].journal is not None:
                run_policy[0].journal.settle()
            self._run_policies.pop(task_id, None)
            self._close_browser_session(status.lineage_id)
            if not marker.done():
                await asyncio.wait([marker])

    def _mark_running(self, status: SubagentStatus, spec: SubagentSpec) -> None:
        """Scrive il segnaposto del tentativo (gira in un thread). Non solleva."""
        record = SubagentRecord(
            task_id=status.task_id,
            lineage_id=status.lineage_id,
            attempt=status.attempt,
            spec=spec,
            state="running",
            phase=status.phase,
            started_at=status.started_at_wall,
            ended_at=status.started_at_wall,
            budget_resumes=status.budget_resumes,
        )
        try:
            self._records.mark_running(record)
        except Exception as e:  # noqa: BLE001 — senza segnaposto si perde solo la ripresa
            logger.warning("Subagent [{}] running marker failed: {}", status.task_id, e)

    async def _checkpoint_history(
        self,
        status: SubagentStatus,
        spec: SubagentSpec,
        initial: list[dict[str, Any]],
        payload: dict[str, Any],
        taint: tuple[str, ...],
    ) -> int | None:
        """Fotografa la conversazione del run come storia ``interrupted``.

        Dal checkpoint ``tools_completed`` del runner, che porta tutto il turno:
        ``prior_messages`` (le iterazioni gia chiuse e le iniezioni), il messaggio
        dell'assistente con le tool call e i loro risultati. Uniti ai messaggi
        iniziali sono la conversazione intera in un punto coerente — nessuna
        tool call senza risultato — ed e cio che il runner stesso avrebbe in
        ``result.messages`` se il run finisse qui. Nessun hook o campo nuovo nel
        runner: il ``checkpoint_callback`` c'era gia, e porta proprio questo.

        La scrittura gira in un thread (:func:`_await_thread`) mentre il run
        aspetta: niente tocca i messaggi mentre si serializzano. L'indice dello
        store si aggiorna dopo, sul loop. Ritorna i byte del file scritto, o
        ``None`` se non e andata; non solleva mai: una fotografia persa costa
        meno di un aiutante ucciso dal suo salvataggio.
        """
        try:
            prior = payload.get("prior_messages")
            assistant = payload.get("assistant_message")
            results = payload.get("completed_tool_results")
            if (
                not isinstance(prior, list) or not isinstance(assistant, dict)
                or not isinstance(results, list)
            ):
                return None
            snapshot = _without_budget_notice([*initial, *prior, assistant, *results])
            saved_at = time.time()
            size = await _await_thread(
                self._write_checkpoint, status.lineage_id, spec.records_key, snapshot,
                saved_at, taint,
            )
            if size is None:
                return None
            self._history.register(
                status.lineage_id, spec.records_key, saved_at=saved_at, kind=KIND_INTERRUPTED,
            )
            status.checkpointed = True
            return size
        except Exception as e:  # noqa: BLE001 — vedi docstring
            logger.warning("Subagent [{}] history checkpoint failed: {}", status.task_id, e)
            return None

    def _write_checkpoint(
        self,
        lineage_id: str,
        origin_key: str,
        messages: list[dict[str, Any]],
        saved_at: float,
        taint: tuple[str, ...],
    ) -> int | None:
        """La parte in thread del checkpoint: scrive e misura il file."""
        if not self._history.write(
            lineage_id, origin_key, messages,
            saved_at=saved_at, taint=taint, kind=KIND_INTERRUPTED,
        ):
            return None
        return self._history.file_size(lineage_id) or 0

    @staticmethod
    def _close_browser_session(lineage_id: str) -> None:
        """Chiude la sessione del browser dell'aiutante, se ne ha aperta una.

        Senza, la sua WebView restava aperta fino al guardiano d'inattivita'
        (``idleCloseS``), tenendo uno slot che un altro aiutante aspetta in
        ``browser_open`` e ~100 MB del telefono. Il proprietario e' il lineage,
        come in ``browser.owner_for``. La chiusura parte e **non si aspetta**:
        ``close`` in Kotlin dura fino a 10 s, e finche' questo task non finisce
        il piano conta l'aiutante fra quelli in corsa e non giudica il passo.
        Import pigro, perche' questo modulo non dipenda dai tool al caricamento.
        Un errore qui non deve cambiare l'esito del lavoro, gia' annunciato.
        """
        if not lineage_id:
            return
        try:
            from jenny.agent.tools.browser import close_owner_sessions_later

            close_owner_sessions_later(lineage_id)
        except Exception:
            logger.opt(exception=True).warning(
                "Closing the browser session of lineage {} failed", lineage_id
            )

    # ------------------------------------------------------------------
    # iniezione mid-run
    # ------------------------------------------------------------------

    @staticmethod
    def _make_progress_callback(hook: _SubagentHook) -> Callable[..., Any]:
        """Callback di progress di un subagent: telemetria, non output in chat.

        L'agente principale ne costruisce uno che pubblica sul bus
        (``build_bus_progress_callback``); un subagent non ha una bolla in chat da
        aggiornare, quindi il suo va nel log di attivita — che e proprio cio che la
        modale mostra.

        La firma e stretta di proposito, e il runner la ispeziona: dichiarare
        ``file_edit_events`` o ``tool_events`` accenderebbe i tracker di file-edit
        streaming del runner, che alimentano la UI della chat principale e qui
        sarebbero lavoro e allocazioni per nessun consumatore.
        """

        async def _on_progress(
            content: str,
            *,
            tool_hint: bool = False,
            reasoning: bool = False,
            reasoning_end: bool = False,
        ) -> None:
            if reasoning_end:
                await hook.emit_reasoning_end()
                return
            if reasoning:
                await hook.emit_reasoning(content)
                return
            # ``tool_hint`` e la riga "sto per chiamare X": qui e ridondante, gli
            # eventi ``tool_start`` la portano gia con gli argomenti.
            if not tool_hint:
                hook.note_output(content)

        return _on_progress

    def _make_injection_callback(
        self,
        task_id: str,
        hook: _SubagentHook | None = None,
    ) -> Callable[..., Any]:
        """Callback nella forma attesa da ``AgentRunSpec.injection_callback``.

        Non blocca mai: se la casella e vuota ritorna una lista vuota e il
        subagent continua per la sua strada. Il ``limit`` lo passa il runner
        (``_MAX_INJECTIONS_PER_TURN``); la firma keyword-only e cio che gli fa
        capire, per introspezione, che il parametro e supportato.
        """

        async def _drain(*, limit: int = 3) -> list[dict[str, Any]]:
            queue = self._pending_injections.get(task_id)
            if queue is None:
                return []
            items: list[dict[str, Any]] = []
            while len(items) < max(1, limit):
                try:
                    items.append({"role": "user", "content": queue.get_nowait()})
                except asyncio.QueueEmpty:
                    break
            if items:
                logger.info(
                    "Delivered {} orchestrator message(s) to live subagent [{}]",
                    len(items), task_id,
                )
                # Un messaggio consumato spiega perche il subagent cambia
                # comportamento a metà lavoro: senza l'evento, nel post-mortem
                # quella svolta sembrerebbe arbitraria.
                if hook is not None:
                    hook.note_message_in(len(items))
            return items

        return _drain

    def _enqueue_injection(self, task_id: str, message: str) -> None:
        """Accoda un messaggio per un subagent vivo. Solleva se non consegnabile."""
        queue = self._pending_injections.get(task_id)
        if queue is None:
            raise SubagentSendError(
                f"subagent [{task_id}] is not accepting messages (it just terminated)"
            )
        try:
            queue.put_nowait(message)
        except asyncio.QueueFull:
            raise SubagentSendError(
                f"subagent [{task_id}] already has {_MAX_PENDING_INJECTIONS} undelivered "
                "messages: let it work through them before sending more"
            ) from None

    def _discard_injections(self, task_id: str) -> None:
        """Butta la casella di un subagent terminato, segnalando cio che resta."""
        queue = self._pending_injections.pop(task_id, None)
        if queue is not None and not queue.empty():
            logger.warning(
                "Subagent [{}] terminated with {} undelivered message(s)",
                task_id, queue.qsize(),
            )

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
        # l'heartbeat (sessione ``heartbeat``) o dentro un cron monitor
        # (``cron:<job_id>``) termina molto dopo la fine del turno che lo ha
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
            from jenny.plan.driver import PLAN_ID_META, PLAN_STEP_META

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

    def _type_max_iterations(self, agent_type: AgentType) -> int:
        """Tetto di iterazioni effettivo per un tipo.

        Il valore del tipo puo solo STRINGERE quello configurato, mai allargarlo:
        ``max_tool_iterations`` e una decisione dell'utente sul proprio device, e
        un default di tipo piu generoso la scavalcherebbe in silenzio.
        """
        if agent_type.max_iterations is None:
            return self.max_iterations
        return min(agent_type.max_iterations, self.max_iterations)

    def _build_subagent_prompt(
        self,
        workspace: Path | None = None,
        agent_type: AgentType | None = None,
        *,
        workspace_scope: WorkspaceScope | None = None,
        tool_names: list[str] | None = None,
    ) -> str:
        """Build a focused system prompt for the subagent.

        *tool_names* sono gli strumenti del subagent: i template dicono solo
        cio' che vale per chi li ha (``has``), come nel prompt principale.

        Il prompt di ruolo del tipo viene *composto* dentro il template base, non
        duplicato: la parte condivisa (contenuto non fidato, workspace, skills)
        resta in un solo file.

        ``workspace_scope`` e' lo scope della **spec**, cioe' quello che il run
        legherà davvero (v. ``_run_subagent``). Va passato, non dedotto
        dall'ambiente: questo metodo viene chiamato FUORI dal blocco
        ``enter_workspace_scope``, e su ``restart`` / ``send`` /
        ``_resume_lineage`` l'ambiente e' il turno del chiamante, non la spec —
        cosi' rilanciare una spec scrivibile da dentro un turno in sola lettura
        dava un prompt che diceva "non puoi scrivere" a un subagent che poteva.
        """
        from jenny.agent.context import ContextBuilder
        from jenny.agent.skills import SkillsLoader
        from jenny.config.paths import get_output_path
        from jenny.utils.wiki_paths import is_wiki_root

        time_ctx = ContextBuilder._build_runtime_context(None, None)
        root = workspace or self.workspace
        # Lo scope che il run legherà. Vedi la docstring per il perche' il
        # fallback all'ambiente e' corretto e non un ripiego.
        bound_scope = (
            workspace_scope if workspace_scope is not None else current_workspace_scope()
        )
        skills_summary = SkillsLoader(
            root,
            disabled_skills=self.disabled_skills,
        ).build_skills_summary()
        return render_template(
            "agent/subagent_system.md",
            has=ContextBuilder._tool_predicate(tool_names),
            time_ctx=time_ctx,
            workspace=str(root),
            # Assoluto come ``workspace`` qui accanto: il subagent scrive con
            # ``write_file``, e un path relativo lo lascerebbe a indovinare
            # rispetto a cosa — la radice che il prompt gli vieta di sporcare.
            # ``create=False``: qui il prompt la nomina soltanto, la directory
            # la crea ``sync_workspace_templates`` una volta per avvio.
            output_dir=str(get_output_path(root)),
            # Un subagent lanciato dentro un progetto riceve la radice del
            # progetto, e il paragrafo qui sopra gli dava le regole del
            # workspace applicate a una cartella che non e' il workspace: e'
            # cosi' che il 21/08 il file di prova e' finito in
            # ``wikis/<nome>/output/``, cartella che nello scaffold non esiste
            # nemmeno. La domanda si fa sulla cartella perche' qui la chiave di
            # sessione non arriva; ``agent/project.md`` e' lo stesso file che
            # legge l'agente principale, incluso e non ricopiato.
            project=is_wiki_root(root),
            project_path=str(root.resolve()),
            # La pianta della cartella gli serve — e' dove scrive. La politica di
            # cattura no: non ha un utente che gli dica fatti stabili, e quel che
            # scoprisse va in ``raw/research/`` e in una pagina, non nel diario.
            capture=False,
            # Il paragrafo sulla ricerca durante un piano e' per chi lancia
            # aiutanti: un subagent non ne lancia (``has('spawn')`` e' falso), e
            # qui la chiave di sessione non arriva. Falso, e dichiarato: il
            # test «ogni variabile e' alimentata» lo pretende.
            plan_running=False,
            # **T3.9 — la mappa e le pagine, o niente sezione.** Fino a qui le
            # due sezioni di ``agent/project.md`` si rendevano vuote, e in
            # silenzio: Jinja valuta falso un ``{% if %}`` su una variabile
            # assente. Il costo non era teorico — il subagent e' l'attore che
            # scrive le pagine e cura la mappa, e lo faceva senza averle davanti.
            #
            # **I lettori sono quelli del prompt principale, non una copia.** Un
            # ``ContextBuilder`` costruito qui e' l'unica strada: questa classe
            # non ne riceve uno (``AgentLoop`` tiene il suo e non lo passa al
            # manager), e i due lettori sono metodi suoi. Costruirlo costa un
            # ``MemoryStore`` e uno ``SkillsLoader`` — nessuna lettura, un
            # ``mkdir(exist_ok=True)`` su ``memory/`` che esiste gia' — cioe'
            # meno di quel che costa lo ``SkillsLoader`` che questo metodo
            # istanzia qualche riga piu' su. La radice passata e' quella
            # dell'installazione, come in produzione: la cartella del progetto e'
            # l'**argomento** dei lettori, non lo stato del builder.
            #
            # Senza guardia su ``project``: fuori da una wiki i due lettori non
            # trovano ne' ``wiki/index.md`` ne' pagine e tornano vuoti, e il
            # template la sezione non la rende comunque. Una guardia in piu' qui
            # sarebbe un secondo posto in cui sbagliare la stessa domanda.
            **ContextBuilder(self.workspace)._project_block_vars(root),
            # **L'esecuzione eredita lo scope, la conoscenza no.** Visto sul
            # telefono il 22/08: in sola lettura l'agente principale sapeva di
            # non poter scrivere, ha delegato, e il subagent ha pianificato e
            # scritto sei file — tutti rifiutati dal cancello. Il confine ha
            # tenuto, il lavoro e' stato buttato. Lo stesso file che legge
            # l'agente principale, incluso e non ricopiato.
            #
            # Lo scope della spec vince sull'ambiente perche' e' quello che il
            # run lega (``enter_workspace_scope(spec.workspace_scope)``). Il
            # fallback all'ambiente non e' un ripiego: con ``workspace_scope``
            # a ``None`` quel ``with`` e' un no-op, quindi lo scope legato *e'*
            # l'ambiente — e allora e' l'ambiente a dover parlare.
            readonly=not (bound_scope is None or bound_scope.writable),
            # Il budget di chiamate del tipo, quello che il run usera davvero: un
            # aiutante che non sa di averne uno si ferma a meta di una lettura.
            max_iterations=(
                self._type_max_iterations(agent_type)
                if agent_type is not None else self.max_iterations
            ),
            skills_summary=skills_summary or "",
            role_section=self._render_role_section(agent_type, install_path=self.workspace),
        )

    @staticmethod
    def _render_role_section(agent_type: AgentType | None, *,
                             install_path: Path | None = None) -> str:
        """Rende il prompt di ruolo del tipo, o stringa vuota se non c'e.

        Un template mancante non deve uccidere il subagent: un workspace
        sincronizzato da una versione precedente puo non avere ancora il file, e
        girare senza la sezione di ruolo e infinitamente meglio che non girare.
        """
        if agent_type is None:
            return ""
        try:
            # ``install_path``: la radice dell'installazione, perche' un subagent
            # in un quaderno lavora nella cartella del progetto e un percorso
            # relativo (``skills/…``) non ci arriva.
            return render_template(agent_type.prompt_template, strip=True,
                                   install_path=str(install_path or ""))
        except Exception as e:  # noqa: BLE001 — vedi docstring
            logger.warning(
                "Agent type prompt {} unavailable ({}); running without role section",
                agent_type.prompt_template, e,
            )
            return ""

    # ------------------------------------------------------------------
    # retention Tier-1
    # ------------------------------------------------------------------

    def _retain(
        self,
        finished: asyncio.Task,
        status: SubagentStatus | None,
        spec: SubagentSpec,
        lineage_id: str,
        attempt: int,
    ) -> None:
        """Persiste il record terminale del tentativo appena concluso.

        Sequenza, in quest'ordine per una ragione: **digest -> record -> drop**.
        Il digest va su disco prima del record perche il record e cio che lo
        rende raggiungibile (porta ``activity_events``/``activity_bytes``): con
        l'ordine inverso un kill in mezzo lascerebbe un record che promette un
        digest inesistente, cioe un blocco "cosa ha fatto davvero" che si apre
        vuoto. Con questo ordine il rischio simmetrico e un digest senza record —
        invisibile, e raccolto da :meth:`sweep_orphan_digests` al boot. Il ring in
        RAM si butta per ultimo: e la sorgente del digest, e va liberato solo
        quando la copia durevole e stata tentata.
        """
        task_id = status.task_id if status is not None else lineage_id
        digest = self._write_activity_digest(task_id)
        state = self._terminal_state(finished, status)
        kept = self._kept_at_shutdown(state, status)
        if kept and status is not None:
            status.result_summary = INTERRUPTED_RESUMABLE_SUMMARY
        record = SubagentRecord(
            task_id=task_id,
            lineage_id=lineage_id,
            attempt=attempt,
            spec=spec,
            state=state,
            phase=status.phase if status is not None else "done",
            stop_reason=status.stop_reason if status is not None else None,
            error=status.error if status is not None else None,
            result_summary=status.result_summary if status is not None else "",
            iteration=status.iteration if status is not None else 0,
            cancel_reason=status.cancel_reason if status is not None else None,
            started_at=status.started_at_wall if status is not None else time.time(),
            ended_at=time.time(),
            activity_events=digest.events,
            activity_bytes=digest.bytes,
            # Un resoconto consegnato mentre il tentativo era ancora vivo.
            handled_at=self._handled_pending.pop(task_id, None),
            # Misurato nel ramo budget, fuori dal loop: qui siamo nella
            # done-callback, che non deve rileggere la storia da disco.
            # Uno shutdown con una fotografia su disco: riprendibile come dopo
            # un kill (v. ``_kept_at_shutdown``).
            resumable=(
                (state == "budget" and status is not None and status.resumable)
                # Fermato dal provider con la storia salvata: senza, la casa e
                # ``subagent_status`` lo davano per perso e offrivano solo il
                # rilancio da zero.
                or (state == "failed" and status is not None
                    and status.provider_interrupted and status.resumable)
                or kept
            ),
            budget_resumes=status.budget_resumes if status is not None else 0,
            stopped_by=status.stopped_by if status is not None else None,
        )
        self._remember_record_key(record)
        try:
            self._records.append(record)
        except Exception as e:  # noqa: BLE001 — la retention non puo uccidere il subagent
            logger.warning(
                "Subagent record persistence failed for [{}]: {}", record.task_id, e
            )
        self.activity.drop(task_id)

    def _write_activity_digest(self, task_id: str) -> DigestMeta:
        """Condensa il ring del task e la scrive su disco. Non solleva."""
        try:
            return self.digests.write(task_id, self.activity.digest(task_id))
        except Exception as e:  # noqa: BLE001 — la telemetria non uccide la retention
            logger.warning("Subagent activity digest failed for [{}]: {}", task_id, e)
            return DigestMeta()

    @staticmethod
    def _terminal_state(finished: asyncio.Task, status: SubagentStatus | None) -> str:
        """Stato finale osservato dal task, non solo quello dichiarato.

        Una cancellazione (``/stop``, rilancio, drain) non passa dai rami di
        ``_run_subagent``: va letta dal task, altrimenti il record resterebbe
        ``running`` per sempre.
        """
        if finished.cancelled():
            return "cancelled"
        if finished.exception() is not None:
            return "failed"
        if status is None:
            return "done"
        return status.state if status.state != "running" else "done"

    # ------------------------------------------------------------------
    # snapshot + pubblicazione sul bus
    # ------------------------------------------------------------------

    def status_snapshot(self, session_key: str | None = None) -> dict:
        """Snapshot JSON-serializzabile di subagent vivi e terminati di recente.

        Contratto stabile: lo consuma il pannello della WebUI (che non importa
        nulla da ``jenny/agent``) e il tool ``subagent_status``. Solo tipi JSON:
        i timestamp monotonic non escono da qui, ``elapsed_s``/``idle_s`` sono
        derivate e ``ended_at`` e orologio di parete.

        ``recent`` e ordinato dal piu recente al meno recente e limitato a
        ``_SNAPSHOT_RECENT_LIMIT``.

        Ogni voce porta ``task`` troncato a ``_SNAPSHOT_TASK_CHARS``, e le voci
        vive anche la coda dei ``tool_events``: sono i due campi che la modale di
        dettaglio del pannello mostra e che la card, larga una riga, non puo.
        """
        now = time.monotonic()
        # Default a ``set()``, non a ``None``: una chiave di sessione sconosciuta
        # deve filtrare tutto, non disattivare il filtro. Con ``.get(key)`` un
        # caller con una chiave stale otteneva i subagent di *tutte* le sessioni
        # in ``running`` e solo i propri in ``recent``.
        ids = self._session_tasks.get(session_key, set()) if session_key else None
        running: list[dict[str, Any]] = []
        live_lineages: set[str] = set()
        for task_id, st in list(self._task_statuses.items()):
            live_lineages.add(st.lineage_id)
            if ids is not None and task_id not in ids:
                continue
            spec = self._spec_for_task(task_id)
            running.append({
                "task_id": st.task_id,
                "lineage_id": st.lineage_id,
                "attempt": st.attempt,
                "label": st.label,
                "task": truncate_text(st.task_description, _SNAPSHOT_TASK_CHARS),
                "agent_type": st.agent_type,
                "state": st.state,
                "phase": st.phase,
                "iteration": st.iteration,
                "elapsed_s": round(max(0.0, now - st.started_at), 1),
                "idle_s": round(max(0.0, now - st.last_progress_at), 1),
                "last_tool": self._last_tool_name(st.tool_events),
                "tool_events": self._snapshot_tool_events(st.tool_events),
                # Il passo del piano per cui lavora, se lavora per un piano: la
                # casa raggruppa gli aiutanti per passo.
                "plan_id": spec.plan_id if spec is not None else None,
                "plan_step": spec.plan_step if spec is not None else None,
                # Quanti file ha cambiato finora: la casa offre «Ferma e annulla
                # le sue modifiche (N file)» solo se ce n'è almeno uno.
                "changes": self._journal_view(st.lineage_id)[0],
            })

        recent: list[dict[str, Any]] = []
        # Lineage gia visti scendendo dal piu recente: un tentativo che ne ha uno
        # piu nuovo (una ripresa, un rilancio) non e piu da riprendere — lo e, se
        # mai, l'ultimo. Insieme a quelli vivi: un lineage che sta continuando
        # adesso non offre un secondo «continua».
        newer: set[str] = set(live_lineages)
        wall = time.time()
        for record in reversed(self.list_records(session_key)):
            resumable = (
                (
                    (record.state == "budget" and record.budget_resumes < MAX_BUDGET_RESUMES)
                    # Fermato da un errore passeggero del provider: un ``failed``
                    # con ``resumable`` nel record e solo lui (``_retain``).
                    or record.state == "failed"
                    # Interrotto da un riavvio con una fotografia della
                    # conversazione (``interrupted_helpers``).
                    or self._interrupted_resumable(record)
                )
                and record.resumable
                and record.lineage_id not in newer
                # La storia scade (``HISTORY_TTL_S``) e la potatura per origine
                # la puo togliere prima: l'indice in RAM lo sa senza leggere file.
                and wall - record.ended_at < HISTORY_TTL_S
                and self._history.has(record.lineage_id)
            )
            newer.add(record.lineage_id)
            changes, undone = self._journal_view(record.lineage_id)
            recent.append({
                "task_id": record.task_id,
                "lineage_id": record.lineage_id,
                "attempt": record.attempt,
                "label": record.spec.label,
                "task": truncate_text(record.spec.task, _SNAPSHOT_TASK_CHARS),
                "agent_type": record.spec.agent_type,
                "state": record.state,
                "stop_reason": record.stop_reason,
                # Provenienza della cancellazione: ``state="cancelled"`` da solo e
                # ambiguo, e dopo un riavvio l'ambiguita si risolveva sempre nel
                # modo peggiore (rilanciare cio che l'utente aveva fermato).
                # ``None`` per tutti gli altri stati.
                "cancel_reason": record.cancel_reason,
                "result_summary": record.result_summary,
                "ended_at": record.ended_at,
                # Riflette il tetto dei rilanci *automatici*: un rilancio
                # manuale resta sempre possibile (vedi ``restart``).
                "can_restart": record.attempt < MAX_AUTO_ATTEMPTS,
                # Fermato al budget, o dal provider, con una storia da riprendere:
                # la casa offre «fai continuare» (``/api/subagents/{id}/continue``)
                # invece del rilancio da zero.
                "resumable": resumable,
                # Quando Jenny ha avuto il resoconto e ha finito il turno; ``None``
                # finche non e successo (v. ``mark_handled``).
                "handled_at": record.handled_at,
                "plan_id": record.spec.plan_id,
                "plan_step": record.spec.plan_step,
                "stopped_by": record.stopped_by,
                # File cambiati dal lineage e ancora da annullare, e l'ultimo
                # annulla (``agent.subagent_journal.UndoReport.summary``).
                "changes": changes,
                "undo": undone.summary() if undone is not None else None,
            })
            if len(recent) >= _SNAPSHOT_RECENT_LIMIT:
                break
        return {"running": running, "recent": recent}

    @staticmethod
    def _last_tool_name(tool_events: list) -> str | None:
        for event in reversed(tool_events or []):
            if isinstance(event, dict) and isinstance(event.get("name"), str):
                return event["name"]
        return None

    @staticmethod
    def _snapshot_tool_events(tool_events: list) -> list[dict[str, str]]:
        """Coda corta e JSON-only dei tool event, dal piu vecchio al piu nuovo.

        Ricostruisce i dizionari invece di passarli: ``tool_events`` arriva dal
        runner e lo snapshot e un contratto verso la WebUI, quindi qui non deve
        poter transitare una chiave nuova ne un valore non serializzabile.
        """
        recent = [e for e in (tool_events or []) if isinstance(e, dict)]
        return [
            {
                "name": str(e.get("name") or ""),
                "status": str(e.get("status") or ""),
                "detail": str(e.get("detail") or ""),
            }
            for e in recent[-_SNAPSHOT_TOOL_EVENTS_LIMIT:]
        ]

    def _origin_is_silent(self, spec: SubagentSpec) -> bool:
        """True se questo subagent lavora per un turno che non parla all'utente.

        Un subagent lanciato da un heartbeat o da un cron monitor e' lavoro
        interno tanto quanto il turno che lo ha lanciato: il pannello e il digest
        in chat lo annuncerebbero all'utente, che non ha chiesto quel controllo.
        La provenienza si legge dalla session key d'origine, la stessa da cui
        l'annuncio del risultato eredita la propria visibilita.
        """
        return resolve_turn_visibility(
            None,
            channel=spec.origin_channel or INTERNAL_CHANNEL,
            session_key=spec.session_key,
        ).silent

    def _publish_status_snapshot(self, spec: SubagentSpec) -> None:
        """Pubblica lo snapshot sul canale d'origine a ogni transizione.

        Best-effort (``try_publish_outbound``): e uno stato ricalcolabile, e
        bloccare una transizione di stato su una coda outbound piena sarebbe un
        prezzo peggiore di uno snapshot perso — il successivo lo rimpiazza.

        Silenzioso per un'origine silenziosa: lo snapshot e' cio che alimenta il
        pannello e il blocco "cosa ha fatto davvero" in chat, quindi cade sotto
        la stessa regola della risposta finale.
        """
        if self._origin_is_silent(spec):
            return
        try:
            payload = self.status_snapshot(spec.session_key)
            self.bus.try_publish_outbound(OutboundMessage(
                channel=spec.origin_channel,
                chat_id=spec.origin_chat_id,
                content="",
                metadata={OUTBOUND_META_SUBAGENT_STATUS: payload},
            ))
        except Exception:  # noqa: BLE001 — la telemetria non uccide il subagent
            logger.exception("Failed to publish subagent status snapshot")

    def _publish_progress_snapshot(self, task_id: str, spec: SubagentSpec) -> None:
        """Lo snapshot anche fra una transizione e l'altra, ma non piu' di uno ogni tanto.

        Fino al 05/10/2026 partiva solo sulle transizioni (partito, fermo,
        finito): bastava al chip «al lavoro», non alla bolla di lavoro della home,
        che dice *cosa* sta facendo l'aiutante (``last_tool``) e da quanto. Al
        massimo uno ogni :data:`PROGRESS_SNAPSHOT_EVERY_S` per task: un aiutante
        che fa venti chiamate al minuto non deve mandarne venti.
        """
        now = time.monotonic()
        last = self._progress_published_at.get(task_id)
        if last is not None and now - last < PROGRESS_SNAPSHOT_EVERY_S:
            return
        self._progress_published_at[task_id] = now
        self._publish_status_snapshot(spec)

    def _publish_transition(self, spec: SubagentSpec, state: str, label: str) -> None:
        """Riga di progress + snapshot per una transizione di stato."""
        hint = _TRANSITION_HINTS.get(state)
        if hint and not self._origin_is_silent(spec):
            try:
                self.bus.try_publish_outbound(OutboundMessage(
                    channel=spec.origin_channel,
                    chat_id=spec.origin_chat_id,
                    content=f"{hint}: {label}",
                    metadata={"_progress": True, "_tool_hint": True},
                ))
            except Exception:  # noqa: BLE001 — vedi _publish_status_snapshot
                logger.exception("Failed to publish subagent transition hint")
        self._publish_status_snapshot(spec)

    def mark_handled(self, task_id: str) -> bool:
        """Il resoconto di *task_id* e arrivato a Jenny e il suo turno e finito.

        Scrive ``handled_at`` nel record e ripubblica lo snapshot, cosi la casa
        toglie la scheda di un aiutante fermato quando Jenny ha gia deciso cosa
        farne. Lo chiama il loop (``AgentLoop``): a fine del turno che ha
        presentato il resoconto, o di quello che l'ha ricevuto a meta, e quando
        un resoconto doppio trova la risposta gia data. Se il tentativo e ancora
        vivo (il resoconto parte prima che la done-callback scriva il record) la
        marca resta in RAM e la scrive :meth:`_retain`. Ritorna ``True`` se ha
        marcato qualcosa. Non solleva: e contabilita della UI.
        """
        if not isinstance(task_id, str) or not task_id:
            return False
        now = time.time()
        if task_id in self._task_statuses:
            self._handled_pending.setdefault(task_id, now)
            return True
        key = self._record_keys.get(task_id)
        if key is None:
            # Record di un processo precedente: la ricerca su tutti i file sta
            # fuori dal loop, e lo snapshot parte quando ha finito.
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return self._mark_handled_on_disk(task_id, now, None)
            loop.create_task(self._mark_handled_off_loop(task_id, now))
            return True
        return self._mark_handled_on_disk(task_id, now, key)

    async def _mark_handled_off_loop(self, task_id: str, at: float) -> None:
        try:
            record = await asyncio.to_thread(self._records.mark_handled, task_id, at)
        except Exception as e:  # noqa: BLE001 — contabilita della UI
            logger.warning("Subagent handled mark failed for [{}]: {}", task_id, e)
            return
        if record is not None:
            self._remember_record_key(record)
            self._publish_status_snapshot(record.spec)

    def _mark_handled_on_disk(self, task_id: str, at: float, key: str | None) -> bool:
        """Scrive ``handled_at``: un solo file se la chiave e nota (pochi KB)."""
        try:
            record = self._records.mark_handled(task_id, at, session_key=key)
        except Exception as e:  # noqa: BLE001 — vedi ``mark_handled``
            logger.warning("Subagent handled mark failed for [{}]: {}", task_id, e)
            return False
        if record is None:
            return False
        self._publish_status_snapshot(record.spec)
        return True

    _MAX_RECORD_KEYS = 512

    def _remember_record_key(self, record: SubagentRecord) -> None:
        self._record_keys[record.task_id] = record.session_key
        while len(self._record_keys) > self._MAX_RECORD_KEYS:
            self._record_keys.pop(next(iter(self._record_keys)))

    def list_records(self, session_key: str | None = None) -> list[SubagentRecord]:
        """Record Tier-1 dei subagent terminati, dal piu vecchio al piu nuovo."""
        records = (
            self._records.load_all() if session_key is None else self._records.load(session_key)
        )
        for record in records[-_SNAPSHOT_RECENT_LIMIT:]:
            self._remember_record_key(record)
        return records

    # ------------------------------------------------------------------
    # restart
    # ------------------------------------------------------------------

    async def restart(
        self,
        target_id: str,
        *,
        extra_instructions: str | None = None,
        manual: bool = False,
        quick: bool | None = None,
        grace_s: float = 2.0,
    ) -> str:
        """Rilancia il lavoro identificato da ``target_id`` come attempt N+1.

        ``target_id`` puo essere un ``task_id`` (di un tentativo vivo o
        terminato) o un ``lineage_id``. Il tentativo precedente, se ancora vivo,
        viene cancellato e ripudiato prima di far partire il successivo.

        ``manual=True`` e il bottone premuto da un umano e non ha tetto;
        ``manual=False`` (orchestratore/codice) si fermano a
        ``MAX_AUTO_ATTEMPTS`` tentativi per lineage.
        """
        resolved = self._resolve_target(target_id)
        if resolved is None:
            raise SubagentRestartError(f"unknown subagent or lineage: {target_id}")
        lineage_id, spec, attempt = resolved
        next_attempt = attempt + 1
        if not manual and next_attempt > MAX_AUTO_ATTEMPTS:
            raise SubagentRestartError(
                f"automatic restart refused for lineage {lineage_id}: "
                f"{attempt}/{MAX_AUTO_ATTEMPTS} attempts already used "
                "(a manual relaunch is still possible)"
            )

        await self._supersede(lineage_id, grace_s=grace_s)

        new_spec = _restamped(spec.with_extra_instructions(extra_instructions))
        if extra_instructions and (turn := current_turn_taint()):
            # La nota correttiva l'ha scritta un turno con contenuto non fidato in
            # vista: il rilancio la porta, e con lei il taint.
            new_spec = replace(new_spec, taint_seed=_merge_taint(new_spec.taint_seed, turn))
        if quick is not None and quick != new_spec.quick:
            new_spec = replace(new_spec, quick=quick)
        logger.info(
            "Restarting lineage {} as attempt {} (manual={})",
            lineage_id, next_attempt, manual,
        )
        return await self._spawn_spec(new_spec, lineage_id=lineage_id, attempt=next_attempt)

    # ------------------------------------------------------------------
    # send: iniezione / resume / rilancio
    # ------------------------------------------------------------------

    async def send(
        self,
        target_id: str,
        message: str,
        *,
        quick: bool | None = None,
        grace_s: float = 2.0,
        allow_restart: bool = True,
    ) -> SubagentSendResult:
        """Parla a un subagent, qualunque sia il suo stato.

        Il chiamante NON deve sapere se il subagent e vivo: dice "parla con
        questo subagent" e la scelta la fa il manager, in quest'ordine:

        1. tentativo **vivo e in corso** -> iniezione mid-run, senza fermarlo;
        2. **terminato bene**, o **fermato al budget**, con storia entro la
           finestra di retention ->
           *resume*: un run nuovo seminato con i messaggi salvati piu il
           follow-up — o, per un lavoro finito bene con una storia lunga, con
           la sola ossatura (brief, resoconto, file scritti: vedi
           ``_resume_lineage``);
        3. tutto il resto (stallato, fallito, storia scaduta) -> *rilancio*
           dalla spec, col messaggio come nota correttiva.

        Un resume occupa uno slot di concorrenza come uno spawn, ma **non**
        consuma il budget dei rilanci automatici: ``attempt`` non avanza, perche
        una continuazione diretta non e il ritentativo di un fallimento e
        ``MAX_AUTO_ATTEMPTS`` esiste per quello.

        Un lineage il cui ultimo tentativo si e fermato al budget non cade mai
        nel rilancio: si riprende, oppure ``SubagentSendError`` dice perche no
        (tetto ``MAX_BUDGET_RESUMES``, storia non piu riprendibile).
        ``allow_restart=False`` (il tasto «continua» della casa) rifiuta anche
        il terzo ramo: chi ha chiesto di continuare non ha chiesto di rifare.
        """
        text = (message or "").strip()
        if not text:
            raise SubagentSendError("the message is empty: there is nothing to send")

        # Prima il lineage, poi il tentativo vivo **del lineage**: chiesto col
        # task id di un tentativo finito, ``_live_task_for`` non vedeva la
        # ripresa gia in volo (il suo id e un altro), e il tasto «continua» piu
        # un ``subagent_send`` di Jenny facevano due tentativi vivi sullo stesso
        # lavoro.
        resolved = self._resolve_target(target_id)
        live_id = self._live_task_for(resolved[0] if resolved is not None else target_id)
        if live_id is not None:
            live = self._task_statuses.get(live_id)
            # La casella deve esistere: un tentativo che ha appena perso la
            # propria (terminazione in corso) non e piu iniettabile e deve
            # cadere sui rami resume/rilancio, non fallire.
            if (
                live is not None
                and live.state == "running"
                and live_id in self._pending_injections
            ):
                # Un messaggio scritto con contenuto non fidato in vista porta il
                # taint del turno dentro il lavoro vivo.
                if (turn := current_turn_taint()) and (policy := self._run_policies.get(live_id)):
                    for source in turn:
                        policy.mark_untrusted(source)
                self._enqueue_injection(live_id, text)
                return SubagentSendResult(
                    "injected",
                    f"Delivered to running subagent [{live_id}] ({live.label}); it will "
                    "pick the message up at its next step and keep working. Its result "
                    "will be announced to you as usual — do not poll for it.",
                    task_id=live_id,
                )

        if resolved is None:
            raise SubagentSendError(f"unknown subagent or lineage: {target_id}")
        lineage_id, spec, attempt = resolved
        last_state, budget_resumes = self._lineage_tail(lineage_id)
        if live_id is None and last_state == "budget":
            # Un aiutante fermato al budget si continua o non si tocca: un
            # rilancio da zero, in silenzio, butterebbe proprio il lavoro che la
            # ripresa esiste per salvare.
            if budget_resumes >= MAX_BUDGET_RESUMES:
                raise SubagentSendError(
                    f"this helper stopped at its budget {budget_resumes + 1} times: do not "
                    "continue it again. Split the remaining work into smaller helpers, "
                    "or ask the user how to proceed."
                )
            if not await asyncio.to_thread(self._history_continuable, lineage_id):
                raise SubagentSendError(
                    "this helper stopped at its budget, and its conversation can no longer "
                    "be resumed (too long, or expired). Start new, smaller helpers for what "
                    "remains, with a brief of what is already done."
                )

        # La storia si consulta solo se non c'e un tentativo vivo: un lineage
        # stallato puo avere il transcript di un tentativo precedente, e
        # riprenderlo mentre un altro tentativo e in volo sarebbe incoerente.
        history = (
            await asyncio.to_thread(self._history.load, lineage_id) if live_id is None else None
        )
        if history:
            return await self._resume_lineage(
                lineage_id, spec, attempt, history, text, quick=quick,
                budget_resumes=budget_resumes + (1 if last_state == "budget" else 0),
                finished=last_state == "done",
            )

        if not allow_restart:
            raise SubagentSendError(
                "no resumable conversation for this subagent, and continuing it would "
                "mean relaunching it from scratch"
            )
        detail = await self.restart(
            target_id,
            extra_instructions=text,
            manual=False,
            quick=quick,
            grace_s=grace_s,
        )
        relaunched = self._lineages.get(lineage_id)
        return SubagentSendResult(
            "restarted",
            "No resumable conversation for this subagent (it failed, stalled, or its "
            f"history aged out), so the job was relaunched from scratch with your "
            f"message as a corrective note. {detail}",
            task_id=relaunched.task_id if relaunched is not None else None,
        )

    async def _resume_lineage(
        self,
        lineage_id: str,
        spec: SubagentSpec,
        attempt: int,
        history: list[dict[str, Any]],
        message: str,
        *,
        quick: bool | None,
        budget_resumes: int = 0,
        finished: bool = False,
    ) -> SubagentSendResult:
        """Rilancia un lineage terminato seminandolo con la storia salvata.

        ``finished`` dice che l'ultimo tentativo e' finito bene: solo allora, e
        solo oltre ``LIGHT_RESUME_MIN_HISTORY_CHARS``, la ripresa e' *leggera*
        (:func:`~jenny.agent.subagent_history.light_resume_messages`). Un aiutante
        fermato al budget riprende sempre con la storia intera: e' a meta' di un
        lavoro, e cio' che aveva letto gli serve per finirlo.
        """
        resume_spec = spec if quick is None or quick == spec.quick else replace(spec, quick=quick)
        resume_spec = _restamped(resume_spec)
        # La storia rimette in contesto ciò che il lavoro aveva letto: se era non
        # fidato, lo è anche la ripresa. Vale anche per quella leggera: il
        # resoconto e la nota vengono dalle stesse letture non fidate.
        taint = _merge_taint(self._history.taint_of(lineage_id), current_turn_taint())
        if taint:
            resume_spec = replace(resume_spec, taint_seed=taint)
        light: list[dict[str, Any]] | None = None
        # L'app-builder no: scrive con ``app draft/edit/set``, che la nota dei
        # file non vede, e il suo seguito vive del codice letto e dei risultati
        # di ``app run`` (l'instradamento di ``spawn`` gli promette proprio
        # questo: «conosce il codice e continua da lì»).
        light_ok = finished and spec.agent_type != "app-builder"
        size = history_chars(history) if light_ok else 0
        if size > self.LIGHT_RESUME_MIN_HISTORY_CHARS:
            light = light_resume_messages(history, message)
        if light is not None:
            messages = light
            logger.info(
                "Light resume of lineage {}: {} message(s), {} chars -> {} message(s), {} chars",
                lineage_id, len(history), size, len(light), history_chars(light),
            )
        else:
            messages = list(history) + [{"role": "user", "content": message}]
        # ``attempt`` invariato: vedi il contratto in ``send``. Il record del run
        # ripreso resta sullo stesso attempt, cosi ``can_restart`` nello snapshot
        # continua a significare "quanti rilanci automatici sono stati spesi".
        task_id, _ = await self._launch(
            resume_spec,
            lineage_id=lineage_id,
            attempt=attempt,
            resume_messages=messages,
            budget_resumes=budget_resumes,
        )
        logger.info(
            "Resumed lineage {} as [{}] from {} stored message(s)",
            lineage_id, task_id, len(history),
        )
        if light is not None:
            return SubagentSendResult(
                "resumed",
                f"Resumed subagent [{spec.label}] with a light context (new id: {task_id}, "
                f"lineage {lineage_id}): its saved conversation was long ({len(history)} "
                "messages), so it continues from its brief, its report and the list of "
                "files it wrote, not from everything it read. It will re-read the pages "
                "it changes. Its result will be announced to you as usual.",
                task_id=task_id,
            )
        return SubagentSendResult(
            "resumed",
            f"Resumed subagent [{spec.label}] from its saved conversation "
            f"(new id: {task_id}, lineage {lineage_id}, {len(history)} messages of "
            "context): it already knows what it did, you only sent the change. "
            "Its result will be announced to you as usual.",
            task_id=task_id,
        )

    # Oltre questa conversazione un ritocco riparte da un costruttore nuovo:
    # riprendere rimanda al provider tutta la storia del subagent, e a ogni
    # ritocco cresce.
    CONTINUE_MAX_HISTORY_CHARS = 160_000

    # Oltre questa storia un aiutante finito bene riprende *leggero*: brief,
    # resoconto, i file che ha scritto e il messaggio nuovo (vedi
    # ``_resume_lineage``). Misurato sul piano del 24/10/2026: 19 notebook-editor
    # ripresi per aggiungere due o tre fatti a pagine gia' loro partivano con 113-623
    # mila caratteri di contesto (mediana ~190 mila) e sono costati 23,5 M token di
    # prompt, un quinto del piano. Sotto, la storia intera costa poco e un
    # aiutante breve resta fedele a se stesso: 60 mila caratteri sono ~15 mila
    # token, un lavoro di poche letture.
    LIGHT_RESUME_MIN_HISTORY_CHARS = 60_000

    def continuable(self, target_id: str) -> bool:
        """Vero se ``send`` a *target_id* continuerebbe il suo lavoro.

        Cioe' se c'e' un tentativo vivo che accetta un messaggio, o una storia
        da riprendere non troppo lunga. Il terzo ramo di ``send`` — il rilancio
        dalla spec, col messaggio come nota — qui non conta: rifarebbe il lavoro
        vecchio, non quello nuovo.
        """
        resolved = self._resolve_target(target_id)
        if resolved is None:
            return False
        # Il tentativo vivo si cerca sul lineage, come in ``send``.
        live_id = self._live_task_for(resolved[0])
        if live_id is not None:
            live = self._task_statuses.get(live_id)
            return (live is not None and live.state == "running"
                    and live_id in self._pending_injections)
        last_state, budget_resumes = self._lineage_tail(resolved[0])
        if last_state == "budget" and budget_resumes >= MAX_BUDGET_RESUMES:
            return False
        return self._history_continuable(resolved[0])

    def _lineage_tail(self, lineage_id: str) -> tuple[str | None, int]:
        """Stato terminale dell'ultimo tentativo e riprese dopo un budget del lineage.

        Dalla RAM se il lineage e tracciato e il suo ultimo tentativo e finito;
        altrimenti dal record piu recente su disco (dopo un riavvio).
        """
        tracked = self._lineages.get(lineage_id)
        if tracked is not None and tracked.last_state is not None:
            return tracked.last_state, tracked.budget_resumes
        if tracked is not None and tracked.task_id in self._task_statuses:
            return None, tracked.budget_resumes
        try:
            record = self._records.find(lineage_id)
        except Exception:  # noqa: BLE001 — record illeggibili: nessuno stato noto
            record = None
        if record is None or record.lineage_id != lineage_id:
            return None, tracked.budget_resumes if tracked is not None else 0
        return record.state, record.budget_resumes

    def _history_continuable(self, lineage_id: str) -> bool:
        """La storia salvata di *lineage_id* c'e ed e abbastanza corta da riprendere.

        La meta «storia» di :meth:`continuable`, senza la domanda sul tentativo
        vivo: l'annuncio di un budget e il record si chiedono questo mentre il
        tentativo sta ancora uscendo, e per loro vivo non vuol dire niente.
        """
        try:
            history = self._history.load(lineage_id)
        except Exception as e:  # noqa: BLE001 — una storia illeggibile non e riprendibile
            logger.warning("Subagent history unreadable for lineage {}: {}", lineage_id, e)
            return False
        if not history:
            return False
        return history_chars(history) <= self.CONTINUE_MAX_HISTORY_CHARS

    def _live_task_for(self, target_id: str) -> str | None:
        """Task id del tentativo VIVO che corrisponde a un task id o lineage id."""
        if not target_id:
            return None
        candidates: list[str] = []
        if target_id in self._task_statuses:
            candidates.append(target_id)
        candidates.extend(
            tid for tid, st in self._task_statuses.items()
            if tid != target_id and st.lineage_id == target_id
        )
        for tid in candidates:
            task = self._running_tasks.get(tid)
            if task is not None and not task.done():
                return tid
        return None

    def _resolve_target(self, target_id: str) -> tuple[str, SubagentSpec, int] | None:
        """Risolve un task id o lineage id in (lineage_id, spec, attempt corrente).

        L'attempt restituito e sempre il piu alto noto per il lineage: rilanciare
        partendo dall'id di un tentativo vecchio non deve far regredire la
        numerazione (ne rimettere in gioco il tetto automatico).
        """
        if not target_id:
            return None
        lineage_id: str | None = None
        spec: SubagentSpec | None = None
        attempt = 0

        if (live := self._task_statuses.get(target_id)) is not None:
            lineage_id = live.lineage_id
        elif target_id in self._lineages:
            lineage_id = target_id
        else:
            for lin in self._lineages.values():
                if lin.task_id == target_id:
                    lineage_id = lin.lineage_id
                    break
        if lineage_id is None:
            # Fallback durevole: dopo un riavvio del processo la memoria e vuota
            # ma i record Tier-1 su disco portano ancora spec, lineage e attempt.
            record = self._records.find(target_id)
            if record is None:
                return None
            lineage_id, spec, attempt = record.lineage_id, record.spec, record.attempt

        tracked = self._lineages.get(lineage_id)
        if tracked is not None and tracked.attempt >= attempt:
            spec, attempt = tracked.spec, tracked.attempt
        if spec is None:
            return None
        return lineage_id, spec, attempt

    async def _supersede(self, lineage_id: str, *, grace_s: float) -> None:
        """Cancella e ripudia il tentativo vivo di un lineage.

        Riusa ``_repudiated_task_ids``, lo stesso meccanismo di ``/stop``: un
        tentativo superato che riesce a finire piu tardi non deve iniettare il
        proprio risultato stale nella sessione.
        """
        lin = self._lineages.get(lineage_id)
        if lin is None:
            return
        task = self._running_tasks.get(lin.task_id)
        if task is None or task.done():
            return
        self._mark_cancelled(lin.task_id, CANCEL_REASON_SUPERSEDED)
        self._repudiated_task_ids.add(lin.task_id)
        task.cancel()
        await asyncio.wait([task], timeout=grace_s)

    # ------------------------------------------------------------------
    # stall watchdog
    # ------------------------------------------------------------------

    def _ensure_stall_watchdog(self) -> None:
        """Crea lazy l'unico task di vigilanza per tutti i subagent.

        Uno solo, non uno per subagent: su Android ogni task in piu e memoria e
        wakeup. Il loop esce da solo quando non resta nulla da vigilare, e uno
        spawn successivo lo ricrea.
        """
        if self.stall_threshold_s <= 0:
            return
        if self._watchdog_task is not None and not self._watchdog_task.done():
            return
        self._watchdog_task = asyncio.create_task(self._stall_watchdog_loop())

    async def _stall_watchdog_loop(self) -> None:
        while self._running_tasks:
            await asyncio.sleep(self._stall_check_interval_s)
            try:
                by_session: dict[str, list[str]] = {}
                for task_id in self.check_stalls():
                    if (spec := self._spec_for_task(task_id)) is not None:
                        status = self._task_statuses.get(task_id)
                        self._publish_transition(
                            spec, "stalled", status.label if status else spec.label
                        )
                        if spec.session_key:
                            by_session.setdefault(spec.session_key, []).append(task_id)
                self._notify_stalls(by_session)
            except Exception:  # noqa: BLE001 — il watchdog non deve mai morire
                logger.exception("Subagent stall check failed")

    def _notify_stalls(self, by_session: dict[str, list[str]]) -> None:
        """Passa le marcature di questo giro all'ascoltatore, se c'e.

        Isolato per sessione: un ascoltatore che solleva non deve togliere la
        notizia alle altre sessioni, ne fermare il watchdog.
        """
        listener = self._stall_listener
        if listener is None:
            return
        for session_key, task_ids in by_session.items():
            try:
                listener(session_key, task_ids)
            except Exception:  # noqa: BLE001 — vedi docstring
                logger.exception("Subagent stall listener failed for {}", session_key)

    def _spec_for_task(self, task_id: str) -> SubagentSpec | None:
        """Spec del tentativo vivo ``task_id``, se ancora tracciato in RAM."""
        status = self._task_statuses.get(task_id)
        if status is not None and (lin := self._lineages.get(status.lineage_id)) is not None:
            return lin.spec
        for lin in self._lineages.values():
            if lin.task_id == task_id:
                return lin.spec
        return None

    def check_stalls(self, *, now: float | None = None) -> list[str]:
        """Marca ``stalled`` i subagent senza progresso da oltre la soglia.

        MARCA E BASTA: non cancella nulla. La decisione di rilanciare e
        dell'utente o dell'orchestratore, e un subagent lento non e un subagent
        rotto. Simmetricamente, uno ``stalled`` che riprende a progredire torna
        ``running``.

        Ritorna i task id marcati in questo giro.
        """
        moment = time.monotonic() if now is None else now
        marked: list[str] = []
        for task_id, status in list(self._task_statuses.items()):
            if status.state not in ("running", "stalled"):
                continue
            idle = moment - status.last_progress_at
            if idle >= self.stall_threshold_s:
                if status.state != "stalled":
                    status.state = "stalled"
                    marked.append(task_id)
                    logger.warning(
                        "Subagent [{}] marked stalled after {:.0f}s without progress",
                        task_id, idle,
                    )
            elif status.state == "stalled":
                status.state = "running"
        return marked

    async def _cancel_stall_watchdog(self) -> None:
        """Ferma il watchdog senza lasciare task orfani allo shutdown."""
        task = self._watchdog_task
        self._watchdog_task = None
        if task is None or task.done():
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    # ------------------------------------------------------------------
    # cancellation / shutdown
    # ------------------------------------------------------------------

    async def cancel_by_session(self, session_key: str, *, grace_s: float = 2.0) -> int:
        """Cancel all subagents for the given session. Returns count cancelled.

        Bounded: attende al massimo ``grace_s`` e abbandona i task che non
        muoiono (es. bloccati in un thread non interrompibile). Gli abbandonati
        vengono marcati ripudiati così il loro announce tardivo è soppresso.
        """
        by_task: dict[asyncio.Task, str] = {
            self._running_tasks[tid]: tid
            for tid in self._session_tasks.get(session_key, [])
            if tid in self._running_tasks and not self._running_tasks[tid].done()
        }
        tasks = list(by_task)
        for t in tasks:
            # Provenienza scritta PRIMA della cancellazione: dopo il cancel la
            # done-callback puo girare in qualunque momento e il record verrebbe
            # persistito senza. Questo path e /stop, quindi la provenienza e
            # l'utente (vedi ``AgentLoop`` in loop_tasks.py).
            if (st := self._mark_cancelled(by_task[t], CANCEL_REASON_USER)) is not None:
                st.stopped_by = "user"
            t.cancel()
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=grace_s)
            if pending:
                abandoned_ids = [by_task[t] for t in pending]
                self._repudiated_task_ids.update(abandoned_ids)
                logger.warning(
                    "Abandoned {} stuck subagent(s) for session {} after {}s grace: {}",
                    len(pending), session_key, grace_s, abandoned_ids,
                )
        return len(tasks)

    async def cancel_task(
        self, task_id: str, *, grace_s: float = 2.0, by: str = "user",
    ) -> bool:
        """Cancella un singolo tentativo vivo. Ritorna ``True`` se c'era da fermare.

        Pensata per lo stop puntuale (bottone in UI, orchestratore): a differenza
        di ``cancel_by_session`` non tocca gli altri subagent della sessione.

        Il tentativo viene ripudiato (l'announce naturale, se riesce a partire,
        è stale) **ma al suo posto ne viene iniettato uno esplicito di
        cancellazione**. Non è rumore: l'orchestratore è tipicamente fermo
        mid-turn in attesa proprio di quell'injection, e senza nulla il turno
        resta appeso fino al timeout della pending queue (300s) — l'utente
        preme Stop e vede "Agent running" per cinque minuti.
        """
        task = self._running_tasks.get(task_id)
        if task is None or task.done():
            return False
        lineage_id: str | None = None
        attempt: int | None = None
        if (status := self._mark_cancelled(task_id, CANCEL_REASON_USER)) is not None:
            lineage_id = status.lineage_id or None
            attempt = status.attempt
            status.stopped_by = by if by in STOPPED_BY else "user"
        self._repudiated_task_ids.add(task_id)
        # Snapshot pubblicato subito, non solo dalla done-callback: un task che
        # non muore entro il grace verrebbe abbandonato e il pannello lo
        # mostrerebbe "running" per sempre.
        spec = self._spec_for_task(task_id)
        if spec is not None:
            self._publish_status_snapshot(spec)
        task.cancel()
        _, pending = await asyncio.wait([task], timeout=grace_s)
        if pending:
            logger.warning(
                "Abandoned stuck subagent [{}] after {}s grace", task_id, grace_s
            )
        if spec is not None:
            await self._announce_result(
                task_id,
                spec.label,
                spec.task,
                # Stesso testo che finisce nel record e in ``subagent_status``:
                # l'orchestratore deve leggere la stessa regola sia sull'annuncio
                # immediato sia rileggendo lo storico dopo un riavvio.
                cancellation_summary(CANCEL_REASON_USER),
                {
                    "channel": spec.origin_channel,
                    "chat_id": spec.origin_chat_id,
                    "session_key": spec.session_key,
                },
                "cancelled",
                spec.origin_message_id,
                lineage_id=lineage_id,
                attempt=attempt,
                force=True,
            )
        return True

    async def undo(self, target_id: str, *, by: str = "user") -> UndoReport:
        """Toglie il lavoro di un aiutante: rimette i file che ha cambiato.

        *target_id* è un task id o un lineage id; si annulla tutto il lineage,
        cioè anche i tentativi fatti continuare dopo un budget o un riavvio. Solo
        i file ancora come li ha lasciati lui (``agent.subagent_journal``): gli
        altri restano, e il resoconto dice quali e perché.

        Rifiuta un aiutante ancora al lavoro: prima si ferma (``cancel_task``),
        o due mani scriverebbero sugli stessi file. Dopo l'annulla la sua
        conversazione non si riprende più (la storia si toglie): continuarla
        ripartirebbe da file che non ci sono.
        """
        resolved = self._resolve_target(target_id)
        lineage_id = resolved[0] if resolved is not None else None
        if lineage_id is None:
            raise SubagentUndoError(f"No helper '{target_id}' to undo.")
        if self._live_task_for(lineage_id) is not None:
            raise SubagentUndoError(
                "This helper is still working: stop it first, then undo its changes."
            )
        journal = self._journal_for(lineage_id)
        if journal is None:
            raise SubagentUndoError("This helper's changes were not recorded: nothing to undo.")
        # Un ``python_exec`` cancellato può girare ancora nel suo thread (i thread
        # non si interrompono in mezzo a codice C): annullare adesso rimetterebbe
        # file che sta ancora scrivendo.
        deadline = time.monotonic() + self.UNDO_WAIT_FOR_THREADS_S
        while journal.busy and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
        if journal.busy:
            raise SubagentUndoError(
                "This helper is still finishing a piece of code: try again in a moment."
            )
        report = await asyncio.to_thread(journal.undo, by=by if by in STOPPED_BY else "user")
        self._journal_cache[lineage_id] = (0, report)
        self._history.drop(lineage_id)
        logger.info(
            "Subagent lineage {} undone by {}: {} restored, {} kept",
            lineage_id, report.by, len(report.restored), len(report.kept),
        )
        spec = resolved[1] if resolved is not None else None
        if spec is not None:
            self._publish_status_snapshot(spec)
        return report

    async def cancel_and_undo(
        self, task_id: str, *, by: str = "user", grace_s: float = 5.0,
    ) -> UndoReport:
        """«Ferma e annulla»: ferma il tentativo vivo (se c'è), poi annulla.

        L'attesa è più lunga di quella di un semplice stop: l'annulla non può
        partire finché lo strumento in corso non è uscito, o rimetterebbe un file
        che l'aiutante sta ancora scrivendo. Il task asyncio muore subito, ma un
        ``python_exec`` può continuare nel suo thread: quello lo aspetta
        :meth:`undo` (``UNDO_WAIT_FOR_THREADS_S``), e se non esce rifiuta.
        """
        live = self._live_task_for(task_id)
        if live is not None:
            await self.cancel_task(live, grace_s=grace_s, by=by)
        return await self.undo(task_id, by=by)

    async def drain(self, *, timeout_s: float = 10.0) -> int:
        """Attende la fine di TUTTI i subagent in volo; cancella quelli che
        sforano ``timeout_s``. Usato dallo shutdown ordinato del gateway.

        Ritorna il numero di subagent che erano ancora attivi all'inizio."""
        # Il watchdog va fermato per primo: e un task periodico del manager,
        # non un subagent, e nessuno lo aspetterebbe.
        await self._cancel_stall_watchdog()
        by_task: dict[asyncio.Task[None], str] = {
            task: task_id
            for task_id, task in self._running_tasks.items()
            if not task.done()
        }
        tasks = list(by_task)
        if not tasks:
            return 0
        _, pending = await asyncio.wait(tasks, timeout=timeout_s)
        for t in pending:
            # Solo i pending: chi ha finito da se ha gia il proprio esito, e
            # sovrascriverlo con "cancellato allo shutdown" sarebbe una bugia.
            self._mark_cancelled(by_task[t], CANCEL_REASON_SHUTDOWN)
            t.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        return len(tasks)

    def get_running_count(self) -> int:
        """Return the number of currently running subagents."""
        return len(self._running_tasks)

    def active_origin_session_keys(self) -> tuple[str, ...]:
        """Le sessioni d'origine con almeno un subagent vivo adesso.

        Un subagent sopravvive al turno che l'ha lanciato, e quando finisce scrive
        i suoi record e annuncia il risultato **sotto la chiave d'origine**: chi
        vuole spostare quella sessione (il rinomino di un quaderno) deve saperlo.
        """
        return tuple(
            key for key in self._session_tasks
            if self.get_running_count_by_session(key) > 0
        )

    def get_running_ids_by_session(self, session_key: str) -> frozenset[str]:
        """Gli id dei subagent vivi di una sessione, adesso.

        Serve a chi deve distinguere i subagent nati **dopo** un certo momento da
        quelli che c'erano gia': ``AgentLoop`` se li fotografa all'inizio del
        turno, e aspetta solo quelli nuovi.
        """
        tids = self._session_tasks.get(session_key, set())
        return frozenset(
            tid for tid in tids
            if tid in self._running_tasks and not self._running_tasks[tid].done()
        )

    def get_running_count_by_session(self, session_key: str) -> int:
        """Return the number of currently running subagents for a session."""
        tids = self._session_tasks.get(session_key, set())
        return sum(
            1 for tid in tids
            if tid in self._running_tasks and not self._running_tasks[tid].done()
        )
