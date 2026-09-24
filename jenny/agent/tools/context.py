"""Runtime context for tool construction."""
from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from jenny.agent.tools.file_state import FileStates

_CURRENT_REQUEST_CONTEXT: ContextVar["RequestContext | None"] = ContextVar(
    "jenny_tool_request_context",
    default=None,
)

# Identita del turno in corso, legata dall'``AgentLoop`` (vedi
# :func:`bind_turn_id`). ContextVar e non parametro perche il contesto dei tool
# viene ricostruito in piu punti del turno — a inizio turno e a ogni iterazione
# dal progress hook — e il turno e informazione *ambiente* al task che lo esegue.
_CURRENT_TURN_ID: ContextVar[str | None] = ContextVar(
    "jenny_tool_turn_id",
    default=None,
)


@dataclass(frozen=True)
class RequestContext:
    """Per-request context injected into tools at message-processing time."""
    channel: str
    chat_id: str
    message_id: str | None = None
    session_key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    # Identita del turno che sta eseguendo il tool. Distinta da ``message_id``:
    # quello e un id di *routing* scelto dal canale (reply/announce) e i
    # messaggi della WebUI non ne hanno alcuno, mentre questa c'e per ogni
    # turno. E cio su cui si delimitano le guardie per-turno dei tool.
    turn_id: str | None = None


@runtime_checkable
class ContextAware(Protocol):
    def set_context(self, ctx: RequestContext) -> None:
        ...


def bind_request_context(ctx: RequestContext) -> Token[RequestContext | None]:
    return _CURRENT_REQUEST_CONTEXT.set(ctx)


def reset_request_context(token: Token[RequestContext | None]) -> None:
    _CURRENT_REQUEST_CONTEXT.reset(token)


def current_request_context() -> RequestContext | None:
    return _CURRENT_REQUEST_CONTEXT.get()


def current_request_session_key() -> str | None:
    ctx = current_request_context()
    return ctx.session_key if ctx else None


def bind_turn_id(turn_id: str) -> Token[str | None]:
    """Lega l'identita del turno in corso al task che lo esegue.

    Un *turno* e una dispatch dell'``AgentLoop``: il messaggio in ingresso piu
    tutte le iterazioni LLM, le tool call e le injection che ne discendono. E
    l'unita su cui ragionano le guardie per-turno dei tool (vedi
    ``tools/subagent_control.py``), e la stessa stringa che i log mostrano come
    ``[turn unified:default:<ns>]``.

    Perche non ``RequestContext.message_id``: quello e un id di routing che il
    canale e libero di non mandare, e nessun messaggio della WebUI ne porta uno
    — cioe nessun messaggio reale dell'utente. Una guardia che vi si appoggia
    esiste solo nei test.

    Ogni turno gira nel proprio task, quindi il valore legato qui non puo
    contaminare turni concorrenti su altre sessioni.
    """
    return _CURRENT_TURN_ID.set(turn_id)


def reset_turn_id(token: Token[str | None]) -> None:
    _CURRENT_TURN_ID.reset(token)


def current_turn_id() -> str | None:
    """Identita del turno in corso, o ``None`` fuori da un turno."""
    return _CURRENT_TURN_ID.get()


@dataclass
class ToolContext:
    config: Any
    workspace: str
    bus: Any | None = None
    subagent_manager: Any | None = None
    cron_service: Any | None = None
    sessions: Any | None = None
    # Lo stato file di *una* sessione, non il ``FileStateStore`` che le contiene
    # tutte: chi lo legge (``_FsTool``) chiama ``record_write``/``check_read``,
    # che il contenitore non ha. Lo valorizzano solo i runner isolati (subagent);
    # l'agente principale lo lascia a ``None`` e i tool risolvono la sessione
    # corrente dal ContextVar legato in ``AgentLoop`` — vedi
    # ``file_state.current_file_states``.
    file_states: FileStates | None = field(default=None)
    timezone: str = "UTC"
    runtime_events: Any | None = None
    android_context: Any | None = None
    ui_query_service: Any | None = None
    # Vero quando il registry e quello dell'agente principale in modalita
    # orchestratore. Serve ai tool che esistono in entrambi i mondi ma devono
    # restare economici in questo: tutto cio che l'orchestratore produce resta
    # nella conversazione per sempre, mentre l'output di un subagent no.
    orchestrator: bool = False
    # Registry in costruzione, valorizzato da ``ToolLoader.load``. Lo legge solo
    # chi deve osservare l'attivita degli *altri* tool dello stesso agente (la
    # guardia anti-polling di ``subagent_status``), non per chiamarli.
    registry: Any | None = None
