"""Subagent manager for background task execution."""

import asyncio
import contextlib
import json
import math
import re
import time
import uuid
from collections.abc import Awaitable, Iterable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from loguru import logger

from jenny.agent.agent_types import UnknownAgentTypeError, get_agent_type
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
    SubagentActivityLog,
    SubagentDigestStore,
    classify_tool_result,
    format_tool_end,
    format_tool_start,
)
from jenny.agent.subagent_announce import AnnounceMixin
from jenny.agent.subagent_common import (
    _ANNOUNCE_TASK_CHARS,
    _NO_FINAL_RESPONSE,
    MAX_AUTO_ATTEMPTS,
    MAX_BUDGET_RESUMES,
    SubagentSendError,
    SubagentStatus,
    _append_activity,
    _Lineage,
    _plan_metadata,
    _task_head,
    _tool_classes,
    unavailable_tools,
)
from jenny.agent.subagent_history import (
    KIND_BUDGET,
    KIND_INTERRUPTED,
    SubagentHistoryStore,
)
from jenny.agent.subagent_prompt import PromptMixin
from jenny.agent.subagent_records import (
    CANCEL_REASON_SHUTDOWN,
    DEFAULT_AGENT_TYPE,
    MAX_RESULT_SUMMARY_CHARS,
    SubagentRecord,
    SubagentRecordStore,
    SubagentSpec,
    default_label,
)
from jenny.agent.subagent_recovery import RecoveryMixin
from jenny.agent.subagent_resume import (
    ResumeMixin,
    SubagentRestartError,
    SubagentSendResult,
)
from jenny.agent.subagent_status import (
    _SNAPSHOT_TASK_CHARS,
    _SNAPSHOT_TOOL_EVENTS_LIMIT,
    PROGRESS_SNAPSHOT_EVERY_S,
    StatusMixin,
)
from jenny.agent.subagent_stop import StopMixin
from jenny.agent.subagent_toolbox import (
    ToolboxMixin,
    split_allow_by_scope,
    unavailable_by_scope,
)
from jenny.agent.subagent_undo import (
    SubagentUndoError,
    UndoMixin,
)
from jenny.agent.tools.loader import ToolLoader
from jenny.agent.write_journal import (
    UndoReport,
    WriteJournal,
    bind_journal,
)
from jenny.bus.queue import MessageBus
from jenny.config.schema import AgentDefaults, ToolsConfig
from jenny.providers.base import LLMProvider
from jenny.security.subagent_policy import (
    SubagentRunPolicy,
    current_turn_taint,
    enter_subagent_policy,
)
from jenny.security.workspace_access import (
    WorkspaceScope,
    enter_workspace_scope,
)
from jenny.session.history_meta import INJECTED_EVENT_META
from jenny.utils.helpers import truncate_text
from jenny.utils.runtime import (
    BUDGET_NOTICE_EVENT,
    SUBAGENT_BUDGET_FINALIZATION_PROMPT,
    SUBAGENT_BUDGET_NOTICE_TEMPLATE,
)

# Nomi che stanno nei pezzi (``subagent_*.py``) e che altri moduli e i test
# importano da qui: l'indirizzo pubblico resta questo.
__all__ = [
    "SubagentManager",
    "MAX_AUTO_ATTEMPTS",
    "PROGRESS_SNAPSHOT_EVERY_S",
    "SubagentRestartError",
    "SubagentSendResult",
    "SubagentUndoError",
    "_ANNOUNCE_TASK_CHARS",
    "_SNAPSHOT_TASK_CHARS",
    "_SNAPSHOT_TOOL_EVENTS_LIMIT",
    "split_allow_by_scope",
    "unavailable_by_scope",
]

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




# Profondita della casella di posta di un subagent vivo. Bounded per scelta: se
# l'orchestratore accumula messaggi piu in fretta di quanto il subagent iteri,
# il problema e il flusso, non la coda — meglio un rifiuto leggibile che una
# coda che cresce senza limite su un telefono.
_MAX_PENDING_INJECTIONS = 8



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












class SubagentManager(
    RecoveryMixin,
    ToolboxMixin,
    PromptMixin,
    AnnounceMixin,
    StatusMixin,
    ResumeMixin,
    StopMixin,
    UndoMixin,
):
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
        # Chi vuole sapere in quale registro dell'annulla lavora un aiutante di un
        # passo (il driver dei piani). V. :meth:`set_journal_listener`.
        self._journal_listener: Callable[[str, str, str], None] | None = None
        # Orologio del freno dei checkpoint; iniettabile dai test.
        self._clock: Callable[[], float] = time.monotonic
        # I registri delle scritture per l'annulla (``agent.write_journal``),
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















    def set_limit_provider(self, fn: Callable[[str | None], int | None] | None) -> None:
        """Inietta il limite di slot *normali* per sessione.

        ``fn(session_key)`` ritorna quanti aiutanti normali quella sessione puo
        avere insieme (piu uno breve), o ``None`` per il valore di config. Il
        container ci passa il driver dei piani (:data:`PLAN_HELPER_SLOTS` per la
        sessione con un piano che gira): il manager non sa cosa sia un piano, e
        non deve saperlo.
        """
        self._limit_provider = fn
        # Le storie seguono gli slot: con N aiutanti alla volta un passo ne fa
        # girare 2N fra editor e ricercatori prima di rimandare i primi, e ogni
        # storia caduta e' una ripartenza da zero (~5 min sul Titan, 26/10).
        self._history.set_limit_provider(
            None if fn is None else (lambda key: (
                (lim * 2 + 2) if isinstance(lim := self._session_limit(key), int) else None
            ))
        )

    def set_stall_listener(self, fn: Callable[[str, list[str]], None] | None) -> None:
        """Inietta chi ascolta le marcature ``stalled`` del watchdog.

        ``fn(session_key, task_ids)`` riceve, per sessione d'origine, gli
        aiutanti appena marcati. Lo cabla il driver dei piani attraverso il suo
        ospite (``LoopPlanHost.watch_stalls``): i resoconti di un passo tenuti in
        attesa dei fratelli si svegliano se i fratelli rimasti sono tutti fermi.
        Come per :meth:`set_limit_provider`, il manager non sa cosa sia un piano.
        """
        self._stall_listener = fn

    def set_journal_listener(self, fn: Callable[[str, str, str], None] | None) -> None:
        """Inietta chi ascolta i registri degli aiutanti dei piani.

        ``fn(plan_id, step_id, journal_key)`` a ogni tentativo che parte per un
        passo — nuovo, rimandato con ``subagent_send``, ripreso: è il lavoro del
        passo, e il ritorno del piano lo toglie. Lo cabla il driver attraverso il
        suo ospite (``LoopPlanHost.watch_helper_journals``).
        """
        self._journal_listener = fn

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
        steps: int | None = None,
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
            steps=steps if steps and steps > 0 else None,
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
            from jenny.session.plan_probe import plan_running_in_session

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
        # Il registro del tentativo, tenuto qui e non solo nel contesto: il
        # ``finally`` sotto sta fuori dal blocco che lo lega.
        run_journal: list[WriteJournal] = []

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
            max_iterations = self._run_max_iterations(atype, spec.steps)
            system_prompt = self._build_subagent_prompt(
                workspace=root,
                agent_type=atype,
                tool_names=tools.tool_names,
                # Lo scope della spec, non l'ambiente: il prompt si costruisce
                # qui fuori, il ``with`` che lega e' piu' sotto.
                workspace_scope=workspace_scope,
                max_iterations=max_iterations,
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
            # di scrittura ci annotano il «prima», il runner il «dopo». Col passo
            # del piano, se lavora per uno: il ritorno di un passo toglie il suo.
            journal = self._journal_for(status.lineage_id or task_id)
            if journal is not None:
                journal.begin(task_id, status.attempt)
                # Un lineage che riparte dopo un annulla non è più «annullato».
                self._journal_cache.pop(status.lineage_id, None)
                run_journal.append(journal)
                if spec.plan_id and spec.plan_step and self._journal_listener is not None:
                    try:
                        self._journal_listener(spec.plan_id, spec.plan_step, journal.key)
                    except Exception:  # noqa: BLE001 — il lavoro parte comunque
                        logger.opt(exception=True).warning(
                            "Subagent [{}]: plan {} not told about its journal",
                            task_id, spec.plan_id)
            self._run_policies[task_id] = policy
            initial.extend(messages)
            run_policy.append(policy)
            with (enter_workspace_scope(workspace_scope), enter_subagent_policy(policy),
                  bind_journal(journal, step=spec.plan_step)):
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
            if run_journal:
                run_journal[0].settle()
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








    # ------------------------------------------------------------------
    # retention Tier-1
    # ------------------------------------------------------------------




    # ------------------------------------------------------------------
    # snapshot + pubblicazione sul bus
    # ------------------------------------------------------------------











    _MAX_RECORD_KEYS = 512



    # ------------------------------------------------------------------
    # restart
    # ------------------------------------------------------------------


    # ------------------------------------------------------------------
    # send: iniezione / resume / rilancio
    # ------------------------------------------------------------------



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







    # ------------------------------------------------------------------
    # stall watchdog
    # ------------------------------------------------------------------







    # ------------------------------------------------------------------
    # cancellation / shutdown
    # ------------------------------------------------------------------






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
