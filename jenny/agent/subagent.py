"""Subagent manager for background task execution."""

import asyncio
import contextlib
import json
import re
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from loguru import logger

from jenny.agent.agent_types import AgentType, UnknownAgentTypeError, get_agent_type
from jenny.agent.hook import AgentHook, AgentHookContext, ToolResultHookContext
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
from jenny.agent.subagent_history import SubagentHistoryStore
from jenny.agent.subagent_records import (
    CANCEL_REASON_SHUTDOWN,
    CANCEL_REASON_SUPERSEDED,
    CANCEL_REASON_USER,
    DEFAULT_AGENT_TYPE,
    MAX_RESULT_SUMMARY_CHARS,
    SubagentRecord,
    SubagentRecordStore,
    SubagentSpec,
    cancellation_stop_reason,
    cancellation_summary,
)
from jenny.agent.tools.context import ToolContext
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
from jenny.security.workspace_access import (
    WorkspaceScope,
    current_workspace_scope,
    enter_workspace_scope,
)
from jenny.session.turn_visibility import resolve_turn_visibility
from jenny.utils.helpers import truncate_text
from jenny.utils.prompt_templates import render_template

if TYPE_CHECKING:  # pragma: no cover - solo per i type checker
    from jenny.session.manager import SessionManager

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
}

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
    # Stato coarse-grained: running | done | failed | cancelled | stalled.
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
    ) -> None:
        super().__init__()
        self._task_id = task_id
        self._status = status
        self._activity = activity
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


class SubagentManager:
    """Manages background subagent execution."""

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
    ):
        defaults = AgentDefaults()
        self.provider = provider
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
        # Subagent abbandonati da cancel_by_session (task che non muore entro
        # il grace) o superati da un rilancio: il loro announce tardivo va
        # soppresso, non deve iniettare un turno stale nella sessione.
        self._repudiated_task_ids: set[str] = set()
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
        self.sweep_orphan_digests()

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
        ctx = self._tool_context(root, cfg)
        loader = ToolLoader()
        if agent_type is None:
            loader.load(ctx, registry, scope="subagent", allow=None)
            return registry
        for scope, allow in split_allow_by_scope(loader, agent_type).items():
            loader.load(ctx, registry, scope=scope, allow=allow)
        self._log_unavailable(loader, agent_type, ctx)
        return registry

    def _tool_context(self, root: Path, cfg: ToolsConfig) -> ToolContext:
        """Il ctx che i tool ricevono. Estratto perche lo usa anche il pre-volo."""
        return ToolContext(
            config=cfg,
            workspace=str(root.resolve()),
            file_states=FileStates(),
            android_context=get_android_context(),
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
            label=label or task[:30] + ("..." if len(task) > 30 else ""),
            agent_type=agent_type,
            temperature=temperature,
            workspace_scope=workspace_scope,
            origin_channel=origin_channel,
            origin_chat_id=origin_chat_id,
            session_key=session_key,
            origin_message_id=origin_message_id,
            quick=quick,
        )
        return await self._spawn_spec(spec)

    def _check_capacity(self, *, quick: bool) -> None:
        """Invariante di concorrenza, con uno slot sempre libero.

        Uno spawn normale puo occupare al massimo ``max_concurrent - 1`` slot:
        senza riserva cinque ricercatori lunghi saturano il pool e
        l'orchestratore non ha piu modo di far partire un job breve per
        rispondere all'utente. Con limite 1 la riserva si annulla, altrimenti
        una configurazione a slot singolo non spawnerebbe piu nulla.
        """
        # Conta solo i task ancora vivi: un rilancio arriva subito dopo aver
        # cancellato il tentativo precedente, che puo essere ancora nel dict in
        # attesa della propria done-callback.
        running = sum(1 for t in self._running_tasks.values() if not t.done())
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
    ) -> tuple[str, str]:
        """Avvia un tentativo per ``spec`` su un lineage nuovo o esistente.

        Ritorna ``(task_id, lineage_id)``. Con ``resume_messages`` il subagent
        riparte dalla conversazione salvata invece che dal solo task: e un loop
        LLM completo come uno spawn, quindi passa dallo stesso
        :meth:`_check_capacity` (slot riservato incluso).
        """
        self._check_capacity(quick=spec.quick)
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
        )
        self._task_statuses[task_id] = status
        self._track_lineage(_Lineage(lineage, spec, attempt, task_id))
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
            self._repudiated_task_ids.discard(task_id)
            self._discard_injections(task_id)
            if session_key and (ids := self._session_tasks.get(session_key)):
                ids.discard(task_id)
                if not ids:
                    del self._session_tasks[session_key]
            state = self._terminal_state(finished, final)
            self._retain(finished, final, spec, lineage, attempt)
            if state != "done":
                # Invariante: la storia Tier-2 riflette solo un lineage il cui
                # ultimo tentativo e andato a buon fine. Un fallimento, uno stop
                # o un tentativo superato la eliminano — riprendere da un
                # transcript che il lineage ha poi abbandonato darebbe una
                # continuazione incoerente. Il drop invalida anche il cache del
                # SessionManager, quindi la RAM non cresce.
                self._history.drop(lineage)
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
        hook = _SubagentHook(task_id, status, activity=self.activity)
        # Primo evento del task, prima di qualunque I/O: la modale che si apre su
        # un subagent appena nato deve mostrare qualcosa, non una lista vuota.
        hook.note_phase(status.phase)

        async def _on_checkpoint(payload: dict) -> None:
            phase = payload.get("phase", status.phase)
            if phase != status.phase:
                hook.note_phase(phase)
            status.phase = phase
            status.iteration = payload.get("iteration", status.iteration)
            # Secondo punto di stamp del progresso: un subagent che fa un solo
            # tool call lunghissimo passa dai checkpoint, non dalle iterazioni.
            status.touch()

        try:
            root = workspace_scope.project_path if workspace_scope is not None else self.workspace
            cfg = None
            if workspace_scope is not None:
                cfg = self._subagent_tools_config()
                cfg.restrict_to_workspace = workspace_scope.restrict_to_workspace
            atype = get_agent_type(spec.agent_type)
            tools = self._build_tools(workspace=root, tools_config=cfg, agent_type=atype)
            system_prompt = self._build_subagent_prompt(
                workspace=root,
                agent_type=atype,
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
            with enter_workspace_scope(workspace_scope):
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
                    max_iterations=self._type_max_iterations(atype),
                    max_tool_result_chars=self.max_tool_result_chars,
                    hook=hook,
                    max_iterations_message="Task completed but no final response was generated.",
                    finalize_on_max_iterations=False,
                    error_message=None,
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
                await self._announce_result(
                    task_id, label, task,
                    error_text,
                    origin, "error", origin_message_id,
                )
            else:
                final_result = result.final_content or "Task completed but no final response was generated."
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
                    getattr(result, "messages", None),
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
        announce_content = render_template(
            "agent/subagent_announce.md",
            label=label,
            status_text=status_text,
            task=task,
            result=result,
            silent=silent,
        )
        live = self._task_statuses.get(task_id)
        metadata: dict[str, Any] = {
            "injected_event": "subagent_result",
            "subagent_task_id": task_id,
            "subagent_lineage_id": (
                lineage_id
                if lineage_id is not None
                else (live.lineage_id if live is not None else task_id)
            ),
            "subagent_attempt": (
                attempt
                if attempt is not None
                else (live.attempt if live is not None else 1)
            ),
        }
        if origin_message_id:
            metadata["origin_message_id"] = origin_message_id
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
    ) -> str:
        """Build a focused system prompt for the subagent.

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
            skills_summary=skills_summary or "",
            role_section=self._render_role_section(agent_type),
        )

    @staticmethod
    def _render_role_section(agent_type: AgentType | None) -> str:
        """Rende il prompt di ruolo del tipo, o stringa vuota se non c'e.

        Un template mancante non deve uccidere il subagent: un workspace
        sincronizzato da una versione precedente puo non avere ancora il file, e
        girare senza la sezione di ruolo e infinitamente meglio che non girare.
        """
        if agent_type is None:
            return ""
        try:
            return render_template(agent_type.prompt_template, strip=True)
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
        record = SubagentRecord(
            task_id=task_id,
            lineage_id=lineage_id,
            attempt=attempt,
            spec=spec,
            state=self._terminal_state(finished, status),
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
        )
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
        for task_id, st in list(self._task_statuses.items()):
            if ids is not None and task_id not in ids:
                continue
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
            })

        recent: list[dict[str, Any]] = []
        for record in reversed(self.list_records(session_key)):
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

    def list_statuses(self) -> dict[str, SubagentStatus]:
        """Status dei subagent attualmente vivi (copia difensiva)."""
        return dict(self._task_statuses)

    def list_records(self, session_key: str | None = None) -> list[SubagentRecord]:
        """Record Tier-1 dei subagent terminati, dal piu vecchio al piu nuovo."""
        if session_key is None:
            return self._records.load_all()
        return self._records.load(session_key)

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

        new_spec = spec.with_extra_instructions(extra_instructions)
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
    ) -> SubagentSendResult:
        """Parla a un subagent, qualunque sia il suo stato.

        Il chiamante NON deve sapere se il subagent e vivo: dice "parla con
        questo subagent" e la scelta la fa il manager, in quest'ordine:

        1. tentativo **vivo e in corso** -> iniezione mid-run, senza fermarlo;
        2. **terminato bene** con storia entro la finestra di retention ->
           *resume*: un run nuovo seminato con i messaggi salvati piu il
           follow-up;
        3. tutto il resto (stallato, fallito, storia scaduta) -> *rilancio*
           dalla spec, col messaggio come nota correttiva.

        Un resume occupa uno slot di concorrenza come uno spawn, ma **non**
        consuma il budget dei rilanci automatici: ``attempt`` non avanza, perche
        una continuazione diretta non e il ritentativo di un fallimento e
        ``MAX_AUTO_ATTEMPTS`` esiste per quello.
        """
        text = (message or "").strip()
        if not text:
            raise SubagentSendError("the message is empty: there is nothing to send")

        live_id = self._live_task_for(target_id)
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
                self._enqueue_injection(live_id, text)
                return SubagentSendResult(
                    "injected",
                    f"Delivered to running subagent [{live_id}] ({live.label}); it will "
                    "pick the message up at its next step and keep working. Its result "
                    "will be announced to you as usual — do not poll for it.",
                )

        resolved = self._resolve_target(target_id)
        if resolved is None:
            raise SubagentSendError(f"unknown subagent or lineage: {target_id}")
        lineage_id, spec, attempt = resolved

        # La storia si consulta solo se non c'e un tentativo vivo: un lineage
        # stallato puo avere il transcript di un tentativo precedente, e
        # riprenderlo mentre un altro tentativo e in volo sarebbe incoerente.
        history = self._history.load(lineage_id) if live_id is None else None
        if history:
            return await self._resume_lineage(
                lineage_id, spec, attempt, history, text, quick=quick,
            )

        detail = await self.restart(
            target_id,
            extra_instructions=text,
            manual=False,
            quick=quick,
            grace_s=grace_s,
        )
        return SubagentSendResult(
            "restarted",
            "No resumable conversation for this subagent (it failed, stalled, or its "
            f"history aged out), so the job was relaunched from scratch with your "
            f"message as a corrective note. {detail}",
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
    ) -> SubagentSendResult:
        """Rilancia un lineage terminato seminandolo con la storia salvata."""
        resume_spec = spec if quick is None or quick == spec.quick else replace(spec, quick=quick)
        messages = list(history) + [{"role": "user", "content": message}]
        # ``attempt`` invariato: vedi il contratto in ``send``. Il record del run
        # ripreso resta sullo stesso attempt, cosi ``can_restart`` nello snapshot
        # continua a significare "quanti rilanci automatici sono stati spesi".
        task_id, _ = await self._launch(
            resume_spec,
            lineage_id=lineage_id,
            attempt=attempt,
            resume_messages=messages,
        )
        logger.info(
            "Resumed lineage {} as [{}] from {} stored message(s)",
            lineage_id, task_id, len(history),
        )
        return SubagentSendResult(
            "resumed",
            f"Resumed subagent [{spec.label}] from its saved conversation "
            f"(new id: {task_id}, lineage {lineage_id}, {len(history)} messages of "
            "context): it already knows what it did, you only sent the change. "
            "Its result will be announced to you as usual.",
        )

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
                for task_id in self.check_stalls():
                    if (spec := self._spec_for_task(task_id)) is not None:
                        status = self._task_statuses.get(task_id)
                        self._publish_transition(
                            spec, "stalled", status.label if status else spec.label
                        )
            except Exception:  # noqa: BLE001 — il watchdog non deve mai morire
                logger.exception("Subagent stall check failed")

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
            self._mark_cancelled(by_task[t], CANCEL_REASON_USER)
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

    async def cancel_task(self, task_id: str, *, grace_s: float = 2.0) -> bool:
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

    def get_running_count_by_session(self, session_key: str) -> int:
        """Return the number of currently running subagents for a session."""
        tids = self._session_tasks.get(session_key, set())
        return sum(
            1 for tid in tids
            if tid in self._running_tasks and not self._running_tasks[tid].done()
        )
