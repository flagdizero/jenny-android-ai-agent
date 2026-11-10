"""Quel che i pezzi di ``SubagentManager`` hanno in comune: costanti, piccoli tipi, errori.

Sta in un modulo a sé perché lo importano sia ``subagent.py`` sia i suoi mixin
(``subagent_*.py``); è testo spostato da ``subagent.py``, non riscritto.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from jenny.agent.subagent_activity import SubagentActivityLog
from jenny.agent.subagent_records import DEFAULT_AGENT_TYPE, SubagentSpec
from jenny.agent.tools.context import current_request_context
from jenny.agent.tools.loader import ToolLoader, declared_tool_name
from jenny.utils.helpers import clip

# Quante volte un lineage fermato al budget si fa continuare (``send``) prima
# che il manager rifiuti. Senza tetto, budget → ripresa → budget poteva girare
# per sempre: ogni ripresa rimanda tutta la storia, che cresce, e un aiutante che
# non ci sta in tre budget ha un compito troppo grande, non poco tempo. Dopo il
# tetto la mossa giusta e dividere il lavoro, e il rifiuto lo dice.
MAX_BUDGET_RESUMES = 2


# Il testo di ripiego quando anche la chiamata di chiusura a budget esaurito non
# produce niente. Costante perche il ramo budget di ``_run_subagent`` lo
# riconosce per non annunciarlo come se fosse un resoconto.
_NO_FINAL_RESPONSE = "Task completed but no final response was generated."


# Tentativi massimi per lineage nei rilanci *automatici* (orchestratore/codice).
# Il rilancio manuale non e mai capped: a un umano che premo "Relaunch" non si
# risponde no.
MAX_AUTO_ATTEMPTS = 3


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


class SubagentSendError(RuntimeError):
    """Sollevata da ``SubagentManager.send`` quando il messaggio non e
    consegnabile ne convertibile in un resume/rilancio (target sconosciuto,
    messaggio vuoto, casella piena). Il testo e user-facing."""


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
    from jenny.plan.meta import PLAN_ID_META, PLAN_STEP_META

    plan_id = meta.get(PLAN_ID_META)
    plan_step = meta.get(PLAN_STEP_META)
    return (
        plan_id if isinstance(plan_id, str) and plan_id else None,
        plan_step if isinstance(plan_step, str) and plan_step else None,
    )


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
    head = clip(head, _ANNOUNCE_TASK_CHARS)
    if head != text:
        head += " (the full brief is in your spawn call)"
    return head
