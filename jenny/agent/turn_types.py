"""Tipi del turno per l'``AgentLoop``: stato FSM + contesto del turno.

Estratti da ``loop.py`` in un modulo leaf così che i mixin del loop
(`turn_states`, ecc.) possano importarli a runtime senza creare un ciclo con
``loop``. ``loop`` li re-esporta per retro-compatibilità.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, Awaitable, Callable

if TYPE_CHECKING:
    from jenny.agent.tools.registry import ToolRegistry
    from jenny.agent.turn_epochs import TurnToken
    from jenny.bus.events import InboundMessage, OutboundMessage
    from jenny.session.manager import Session


class AgentLoopRun(tuple):
    """Il ritorno di ``AgentLoop._run_agent_loop``: la quintupla di sempre, più ``error_kind``.

    ``(final_content, tools_used, messages, stop_reason, had_injections)`` si
    spacchetta come prima — i chiamanti e i test che lo fanno non cambiano — e
    in più porta il tipo d'errore del provider quando il turno è finito in
    errore (``AgentRunResult.error_kind``), che RESPOND mette nei metadata
    dell'outbound. Un attributo e non un sesto elemento proprio per non rompere
    quelle spacchettature; chi lo legge usa ``getattr(..., "error_kind", None)``,
    perché un ``_run_agent_loop`` finto nei test torna una tupla nuda.
    """

    error_kind: str | None

    def __new__(cls, items: tuple[Any, ...], *, error_kind: str | None = None) -> AgentLoopRun:
        run = super().__new__(cls, items)
        run.error_kind = error_kind
        return run


class TurnDisposition(Enum):
    """Come si e' concluso un turno, dal punto di vista di chi lo ha chiesto.

    Prima esisteva solo ``OutboundMessage | None``, e quel ``None`` confondeva
    almeno cinque casi distinti: monitor rimasto in silenzio, monitor che ha
    parlato col tool ``message``, goal continuation soppressa, turno annullato,
    turno di sistema. Distinguerli richiedeva di contrabbandare l'esito fuori dal
    turno mutando il dict ``metadata`` del chiamante; con un tipo di esito il
    valore di ritorno dice da se' cosa e' successo.

    Gli errori NON sono un caso qui: restano eccezioni, che il chiamante cron
    registra nella propria run record. Aggiungere una variante che nessuno
    costruisce sarebbe codice morto.
    """

    DELIVERED = auto()
    """C'e' un messaggio da pubblicare: la risposta finale del turno."""

    SPOKE_VIA_TOOL = auto()
    """Niente risposta finale, ma l'agente ha parlato chiamando ``message``."""

    SILENT = auto()
    """Turno riuscito che non ha nulla da dire. Esito legittimo, non un errore."""


@dataclass(frozen=True)
class TurnOutcome:
    """Esito di un turno: disposizione + eventuale messaggio da consegnare."""

    disposition: TurnDisposition
    message: OutboundMessage | None = None

    final_text: str = ""
    """Risposta finale del modello, **consegnata o no**.

    Da non confondere con :attr:`text`, che e' il testo *consegnato* e resta
    vuoto quando non c'e' consegna. Su un turno silenzioso il modello scrive
    comunque una risposta finale, che oggi non legge nessuno: e' l'unico canale
    gia' esistente in cui puo' dichiarare un fatto su se' stesso senza parlare
    all'utente. I monitor cron lo usano per distinguere "non ho nulla da dire"
    da "non ho potuto controllare" (v. ``jenny/cron/bound_runner.py``).
    """

    stop_reason: str = ""
    """Come è finito il giro del modello (``AgentRunResult.stop_reason``), o vuoto.

    Vuoto per un turno che il modello non l'ha visto (scartato, tenuto, già
    consegnato). Lo legge il driver del piano a fine turno: un passo finito in
    ``provider_refused`` si blocca subito, senza il promemoria che rifarebbe la
    stessa richiesta. Qui e non nei metadata inbound, che finiscono nell'outbound.
    """

    @classmethod
    def delivered(
        cls, message: OutboundMessage, *, final_text: str = "", stop_reason: str = ""
    ) -> "TurnOutcome":
        return cls(
            TurnDisposition.DELIVERED, message, final_text or message.content, stop_reason
        )

    @classmethod
    def spoke_via_tool(cls, *, final_text: str = "", stop_reason: str = "") -> "TurnOutcome":
        return cls(TurnDisposition.SPOKE_VIA_TOOL, None, final_text, stop_reason)

    @classmethod
    def silent(cls, *, final_text: str = "", stop_reason: str = "") -> "TurnOutcome":
        return cls(TurnDisposition.SILENT, None, final_text, stop_reason)

    @classmethod
    def of(
        cls,
        message: OutboundMessage | None,
        *,
        spoke_via_tool: bool,
        final_text: str = "",
        stop_reason: str = "",
    ) -> "TurnOutcome":
        """Costruisce l'esito dai due soli fatti che il turno conosce."""
        if message is not None:
            return cls.delivered(message, final_text=final_text, stop_reason=stop_reason)
        if spoke_via_tool:
            return cls.spoke_via_tool(final_text=final_text, stop_reason=stop_reason)
        return cls.silent(final_text=final_text, stop_reason=stop_reason)

    @property
    def spoke(self) -> bool:
        """True se l'utente ha ricevuto qualcosa da questo turno."""
        return self.disposition in (
            TurnDisposition.DELIVERED,
            TurnDisposition.SPOKE_VIA_TOOL,
        )

    @property
    def text(self) -> str:
        """Testo della risposta finale, o stringa vuota se non c'e'."""
        return self.message.content if self.message is not None else ""


class TurnState(Enum):
    RESTORE = auto()
    COMPACT = auto()
    COMMAND = auto()
    BUILD = auto()
    RUN = auto()
    SAVE = auto()
    RESPOND = auto()
    DONE = auto()


@dataclass
class StateTraceEntry:
    state: TurnState
    started_at: float
    duration_ms: float
    event: str
    error: str | None = None


@dataclass
class TurnContext:
    msg: InboundMessage
    session_key: str
    state: TurnState
    turn_id: str
    session: Session | None = None

    history: list[dict[str, Any]] = field(default_factory=list)
    initial_messages: list[dict[str, Any]] = field(default_factory=list)

    final_content: str | None = None
    tools_used: list[str] = field(default_factory=list)
    all_messages: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str = ""
    had_injections: bool = False
    # Il tipo d'errore del provider, se il turno è finito in errore
    # (``AgentLoopRun.error_kind``): diventa ``error: {kind}`` nell'outbound.
    error_kind: str | None = None

    user_persisted_early: bool = False
    save_skip: int = 0

    outbound: OutboundMessage | None = None
    # Il turno non raggiunge l'utente da se': ne risposta finale, ne progress,
    # ne reasoning, ne marcatori di turno. Risolto una volta al confine del
    # turno (:mod:`jenny.session.turn_visibility`) e mai piu' rimesso in
    # discussione. Distinto da ``suppress_response``, che una goal continuation
    # accende a meta' turno pur restando un turno visibile.
    silent: bool = False
    suppress_response: bool = False
    # Il turno ha parlato chiamando il tool ``message``. Scritto da RESPOND e
    # letto una volta sola, da ``_process_message``, per costruire il
    # ``TurnOutcome``: e' il segnale che prima viaggiava contrabbandato dentro il
    # dict ``metadata`` del chiamante, perche' l'outbound non poteva portarlo.
    spoke_via_tool: bool = False

    on_progress: Callable[..., Awaitable[None]] | None = None
    on_stream: Callable[[str], Awaitable[None]] | None = None
    on_stream_end: Callable[..., Awaitable[None]] | None = None
    on_retry_wait: Callable[[str], Awaitable[None]] | None = None

    pending_queue: asyncio.Queue | None = None
    pending_summary: str | None = None

    ephemeral: bool = False
    tools: ToolRegistry | None = None
    # Token di epoch del turno (None per i chiamanti non governati).
    turn_token: TurnToken | None = None

    turn_wall_started_at: float = field(default_factory=time.time)
    visible_run_started_at: float | None = None
    turn_latency_ms: int | None = None

    trace: list[StateTraceEntry] = field(default_factory=list)
