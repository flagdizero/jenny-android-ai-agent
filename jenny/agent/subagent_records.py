"""Retention Tier-1 dei subagent: spec rieseguibile ed esito terminale su disco.

Un subagent non e piu una coroutine effimera. La descrizione del lavoro
(:class:`SubagentSpec`) e il suo esito (:class:`SubagentRecord`) sopravvivono
alla fine del task e alla morte del processo: su Android il gateway viene
ucciso spesso, e senza un record su disco un rilancio dopo il riavvio sarebbe
impossibile.

Il record e volutamente piccolo (~1-2 KB): porta spec, stato finale,
``stop_reason`` e un riassunto del risultato, non la storia dei messaggi.

Formato: un file JSONL per session key sotto
``<workspace>/subagents/records/<safe_session_key>.jsonl``, riscritto in modo
atomico a ogni transizione terminale (append + prune). Il JSONL e scelto
proprio per la tolleranza: una riga troncata o corrotta viene saltata e il
resto dello storico resta leggibile, perche il gateway deve poter bootare
comunque.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol

from loguru import logger

from jenny.agent.agent_types import (
    DEFAULT_AGENT_TYPE,
    coerce_agent_type,
    validate_agent_type,
)
from jenny.security.workspace_access import (
    WorkspaceScope,
    workspace_sandbox_status,
)
from jenny.utils.helpers import safe_filename, truncate_text
from jenny.utils.path import atomic_write

# Stati coarse-grained del ciclo di vita di un subagent. Restano distinti da
# ``SubagentStatus.phase`` (fine-grained, già consumato altrove): il phase dice
# *cosa sta facendo* il subagent, lo state dice *se e ancora in gioco*.
SUBAGENT_STATES = ("running", "done", "failed", "cancelled", "stalled")

# Provenienza di una cancellazione. ``state="cancelled"`` da solo non dice CHI ha
# fermato il subagent, e le tre risposte portano a decisioni opposte:
#
# * ``user``       — l'utente ha premuto Stop. Lavoro interrotto di proposito:
#                    non va rilanciato se non lo chiede lui.
# * ``superseded`` — un tentativo successivo dello stesso lineage ha preso il suo
#                    posto. Non c'e niente da rilanciare, il rilancio e gia in
#                    volo.
# * ``shutdown``   — il gateway si e fermato mentre lavorava. Interrotto, non
#                    rifiutato: questo si che e legittimamente ripartibile.
#
# Senza questo campo un riavvio del gateway rendeva i tre casi indistinguibili, e
# l'orchestratore leggeva un job fermato dall'utente come lavoro da riprendere.
CANCEL_REASON_USER = "user"
CANCEL_REASON_SUPERSEDED = "superseded"
CANCEL_REASON_SHUTDOWN = "shutdown"
SUBAGENT_CANCEL_REASONS = (
    CANCEL_REASON_USER,
    CANCEL_REASON_SUPERSEDED,
    CANCEL_REASON_SHUTDOWN,
)

# ``stop_reason`` e ``result_summary`` per ogni provenienza. Non sono decorazione:
# sono i due campi che ``subagent_status`` stampa e che il pannello mostra, quindi
# scrivere qui la regola e cio che la fa arrivare a chi decide. Un campo nuovo che
# l'orchestratore non sa leggere avviserebbe solo la WebUI; questi li legge
# comunque.
_CANCEL_PROVENANCE: dict[str, tuple[str, str]] = {
    CANCEL_REASON_USER: (
        "cancelled_by_user",
        "The user stopped this subagent before it finished. Its work was not "
        "rejected by an error: do not restart it, and do not treat it as "
        "unfinished work to resume, unless the user asks for it again.",
    ),
    CANCEL_REASON_SUPERSEDED: (
        "superseded_by_new_attempt",
        "This attempt was replaced by a newer attempt of the same job. Nothing to "
        "restart here: look at the latest attempt of this lineage.",
    ),
    CANCEL_REASON_SHUTDOWN: (
        "cancelled_at_shutdown",
        "The gateway shut down while this subagent was still working, so its work "
        "was interrupted rather than finished or refused. This one is safe to "
        "restart if the result is still wanted.",
    ),
}


def cancellation_stop_reason(reason: str) -> str | None:
    """``stop_reason`` che porta la provenienza, o ``None`` se sconosciuta."""
    entry = _CANCEL_PROVENANCE.get(reason)
    return entry[0] if entry is not None else None


def cancellation_summary(reason: str) -> str:
    """Riassunto user/orchestrator-facing per una provenienza di cancellazione."""
    entry = _CANCEL_PROVENANCE.get(reason)
    return entry[1] if entry is not None else "This subagent was cancelled."


# Il tipo di agente e definito in ``jenny/agent/agent_types.py``; qui viene
# ri-esportato perche i consumatori storici della spec importano da questo
# modulo.
__all__ = [
    "ActivityDigestDeleter",
    "CANCEL_REASON_SHUTDOWN",
    "CANCEL_REASON_SUPERSEDED",
    "CANCEL_REASON_USER",
    "DEFAULT_AGENT_TYPE",
    "MAX_RECORDS_PER_SESSION",
    "MAX_RESULT_SUMMARY_CHARS",
    "RECORD_TTL_S",
    "SUBAGENT_CANCEL_REASONS",
    "SUBAGENTS_DIRNAME",
    "SUBAGENT_STATES",
    "SubagentRecord",
    "SubagentRecordStore",
    "SubagentSpec",
    "cancellation_stop_reason",
    "cancellation_summary",
]


class ActivityDigestDeleter(Protocol):
    """Cio che questo modulo usa di uno store di digest: solo la cancellazione.

    Espresso come protocollo e non come import di
    :class:`~jenny.agent.subagent_activity.SubagentDigestStore` perche la
    dipendenza corre nell'altro senso (l'attivita importa la dir ``subagents/``
    da qui): un import reciproco sarebbe un ciclo, e la retention non ha bisogno
    di sapere altro.
    """

    def delete(self, task_id: str) -> bool: ...


MAX_RECORDS_PER_SESSION = 20
RECORD_TTL_S = 7 * 24 * 60 * 60
MAX_RESULT_SUMMARY_CHARS = 800

# Guardia: un file record oltre questa soglia e patologico (non lo produciamo
# mai), quindi lo ignoriamo invece di caricarlo in memoria.
_MAX_RECORD_FILE_BYTES = 2_000_000

_RECORDS_DIRNAME = "records"
# Radice di tutto cio che i subagent lasciano su disco. Pubblica perche la
# condivide ``jenny/agent/subagent_activity.py``, che ci mette la sua
# sottodirectory ``activity/``: due letterali "subagents" separati sarebbero
# due directory diverse al primo refuso.
SUBAGENTS_DIRNAME = "subagents"

# Marcatore della nota correttiva iniettata da un rilancio: appesa al task
# originale invece di sostituirlo, cosi il lineage resta lo stesso lavoro.
_CORRECTIVE_HEADER = "## Corrective note from the previous attempt"


def _coerce_str(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _coerce_float(value: Any, default: float = 0.0) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def _coerce_int(value: Any, default: int = 0) -> int:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else default


@dataclass(frozen=True, slots=True)
class SubagentSpec:
    """Descrizione persistente e rieseguibile del lavoro di un subagent.

    E l'oggetto che un rilancio *rigioca*: tutto cio che serve a ricreare lo
    stesso subagent deve stare qui, non nei parametri della coroutine.
    Immutabile per costruzione — un rilancio produce una spec nuova via
    :meth:`with_extra_instructions`, non muta quella vecchia.
    """

    task: str
    label: str
    agent_type: str = DEFAULT_AGENT_TYPE
    temperature: float | None = None
    workspace_scope: WorkspaceScope | None = None
    origin_channel: str = "internal"
    origin_chat_id: str = "direct"
    session_key: str | None = None
    origin_message_id: str | None = None
    # Job breve: puo occupare lo slot riservato del pool (vedi
    # ``SubagentManager._check_capacity``).
    quick: bool = False

    def __post_init__(self) -> None:
        # Il tipo si valida qui, alla costruzione della spec, e non piu in la:
        # e l'unico punto attraversato da *tutti* i percorsi di spawn (tool,
        # cron, rilancio, codice interno), quindi un tipo inventato non puo
        # arrivare a far partire un subagent con l'allowlist sbagliata.
        # ``from_dict`` invece degrada (vedi ``coerce_agent_type``): un record su
        # disco non deve diventare irrilanciabile.
        validate_agent_type(self.agent_type)

    @property
    def origin(self) -> dict[str, Any]:
        """Origine nella forma attesa da ``_announce_result``."""
        return {
            "channel": self.origin_channel,
            "chat_id": self.origin_chat_id,
            "session_key": self.session_key,
        }

    @property
    def records_key(self) -> str:
        """Chiave di retention: la session key, o l'origine se manca."""
        return self.session_key or f"{self.origin_channel}:{self.origin_chat_id}"

    def with_extra_instructions(self, extra: str | None) -> SubagentSpec:
        """Spec identica col task arricchito da una nota correttiva."""
        if not extra or not extra.strip():
            return self
        task = f"{self.task}\n\n{_CORRECTIVE_HEADER}\n{extra.strip()}"
        return replace(self, task=task)

    def to_dict(self) -> dict[str, Any]:
        scope: dict[str, Any] | None = None
        if self.workspace_scope is not None:
            scope = {
                "project_path": str(self.workspace_scope.project_path),
                "access_mode": self.workspace_scope.access_mode,
                "restrict_to_workspace": self.workspace_scope.restrict_to_workspace,
            }
        return {
            "task": self.task,
            "label": self.label,
            "agent_type": self.agent_type,
            "temperature": self.temperature,
            "workspace_scope": scope,
            "origin_channel": self.origin_channel,
            "origin_chat_id": self.origin_chat_id,
            "session_key": self.session_key,
            "origin_message_id": self.origin_message_id,
            "quick": self.quick,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> SubagentSpec:
        """Ricostruisce una spec da JSON, tollerando campi mancanti/sbagliati."""
        if not isinstance(raw, dict):
            raise ValueError("subagent spec must be an object")
        task = _coerce_str(raw.get("task"))
        if not task:
            raise ValueError("subagent spec without task")
        temperature = raw.get("temperature")
        return cls(
            task=task,
            label=_coerce_str(raw.get("label")) or task[:30],
            agent_type=coerce_agent_type(raw.get("agent_type")),
            temperature=(
                float(temperature)
                if isinstance(temperature, (int, float)) and not isinstance(temperature, bool)
                else None
            ),
            workspace_scope=_scope_from_dict(raw.get("workspace_scope")),
            origin_channel=_coerce_str(raw.get("origin_channel"), "internal") or "internal",
            origin_chat_id=_coerce_str(raw.get("origin_chat_id"), "direct") or "direct",
            session_key=raw.get("session_key") if isinstance(raw.get("session_key"), str) else None,
            origin_message_id=(
                raw.get("origin_message_id")
                if isinstance(raw.get("origin_message_id"), str)
                else None
            ),
            quick=bool(raw.get("quick", False)),
        )


def _scope_from_dict(raw: Any) -> WorkspaceScope | None:
    """Ricostruisce lo scope dai soli campi persistiti.

    ``sandbox_status`` non e serializzato: dipende dall'host (provider di
    sandbox, env) e va ricalcolato qui, altrimenti un record scritto su un
    device e riletto dopo un aggiornamento porterebbe uno stato falso.
    """
    if not isinstance(raw, dict):
        return None
    path = raw.get("project_path")
    if not isinstance(path, str) or not path:
        return None
    restrict = bool(raw.get("restrict_to_workspace", False))
    mode = raw.get("access_mode")
    access_mode = "restricted" if mode == "restricted" else "full"
    project_path = Path(path)
    return WorkspaceScope(
        project_path=project_path,
        access_mode=access_mode,
        restrict_to_workspace=restrict,
        sandbox_status=workspace_sandbox_status(
            restrict_to_workspace=restrict,
            workspace=project_path,
        ),
    )


@dataclass(slots=True)
class SubagentRecord:
    """Esito terminale di un tentativo, abbastanza per ispezionarlo o rilanciarlo."""

    task_id: str
    lineage_id: str
    attempt: int
    spec: SubagentSpec
    state: str
    phase: str = "done"
    stop_reason: str | None = None
    error: str | None = None
    result_summary: str = ""
    iteration: int = 0
    # Perche il subagent e stato cancellato, quando lo e stato. Vedi
    # ``SUBAGENT_CANCEL_REASONS``: e il campo che sopravvive al riavvio del
    # gateway e distingue "fermato dall'utente" da "interrotto dallo shutdown".
    cancel_reason: str | None = None
    # Orologio di parete (``time.time``): ``time.monotonic`` non sopravvive al
    # processo, e la TTL dei record deve funzionare tra un riavvio e l'altro.
    started_at: float = field(default_factory=time.time)
    ended_at: float = field(default_factory=time.time)
    # Puntatore al digest di attivita (``jenny/agent/subagent_activity.py``), che
    # vive in un file suo sotto ``<workspace>/subagents/activity/``. Qui stanno
    # solo le due misure, non gli eventi: il record va riscritto per intero a
    # ogni transizione terminale, quindi infilarci il digest renderebbe ogni
    # transizione una riscrittura da centinaia di KB. Con questi due campi la UI
    # decide se offrire o nascondere il blocco "cosa ha fatto davvero" **senza
    # aprire il file**.
    activity_events: int = 0
    activity_bytes: int = 0

    @property
    def session_key(self) -> str:
        return self.spec.records_key

    @property
    def has_activity_digest(self) -> bool:
        """True se esiste un digest da offrire per questo tentativo."""
        return self.activity_events > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "lineage_id": self.lineage_id,
            "attempt": self.attempt,
            "spec": self.spec.to_dict(),
            "state": self.state,
            "phase": self.phase,
            "stop_reason": self.stop_reason,
            "error": self.error,
            "result_summary": truncate_text(self.result_summary, MAX_RESULT_SUMMARY_CHARS),
            "iteration": self.iteration,
            "cancel_reason": self.cancel_reason,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "activity_events": self.activity_events,
            "activity_bytes": self.activity_bytes,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> SubagentRecord:
        if not isinstance(raw, dict):
            raise ValueError("subagent record must be an object")
        task_id = _coerce_str(raw.get("task_id"))
        if not task_id:
            raise ValueError("subagent record without task_id")
        state = _coerce_str(raw.get("state"), "done")
        # Campo aggiunto dopo: un record scritto da una versione precedente non
        # lo ha, e non poterlo leggere renderebbe irrilanciabile tutto lo storico
        # (contratto del modulo: un campo mancante non solleva). Un valore non
        # riconosciuto degrada a ``None`` invece di propagarsi.
        cancel_reason = raw.get("cancel_reason")
        return cls(
            task_id=task_id,
            lineage_id=_coerce_str(raw.get("lineage_id")) or task_id,
            attempt=max(1, _coerce_int(raw.get("attempt"), 1)),
            spec=SubagentSpec.from_dict(raw.get("spec")),
            state=state if state in SUBAGENT_STATES else "done",
            phase=_coerce_str(raw.get("phase"), "done") or "done",
            stop_reason=raw.get("stop_reason") if isinstance(raw.get("stop_reason"), str) else None,
            error=raw.get("error") if isinstance(raw.get("error"), str) else None,
            result_summary=_coerce_str(raw.get("result_summary")),
            iteration=_coerce_int(raw.get("iteration"), 0),
            cancel_reason=(
                cancel_reason if cancel_reason in SUBAGENT_CANCEL_REASONS else None
            ),
            started_at=_coerce_float(raw.get("started_at")),
            ended_at=_coerce_float(raw.get("ended_at")),
            # Campi aggiunti dopo, come ``cancel_reason``: un record scritto da
            # una versione precedente non li ha, e degradano a 0 — cioe "nessun
            # digest", che e proprio la verita per quei record.
            activity_events=max(0, _coerce_int(raw.get("activity_events"), 0)),
            activity_bytes=max(0, _coerce_int(raw.get("activity_bytes"), 0)),
        )


class SubagentRecordStore:
    """Store JSONL bounded dei record terminali, uno per session key.

    Scritture atomiche con fsync (come ``agent/memory.py`` e
    ``session/manager.py``): l'ultimo esito e proprio quello che serve dopo un
    kill improvviso, quindi non puo restare in page cache.
    """

    def __init__(
        self,
        workspace: Any,
        *,
        max_per_session: int = MAX_RECORDS_PER_SESSION,
        ttl_s: float = RECORD_TTL_S,
        digest_store: ActivityDigestDeleter | None = None,
    ) -> None:
        # ``workspace`` resta grezzo e viene risolto lazy: il manager va
        # costruibile anche quando il workspace non e un path utilizzabile
        # (test con doppi, bootstrap prima che la dir esista).
        self._workspace = workspace
        self.max_per_session = max(1, max_per_session)
        self.ttl_s = ttl_s
        # Chi cancella il digest di un record potato. Tipizzato per protocollo e
        # non per import: il modulo dell'attivita importa da qui (per la dir
        # ``subagents/``), quindi l'import inverso sarebbe un ciclo.
        self._digests = digest_store

    # -- paths ---------------------------------------------------------------

    @property
    def root(self) -> Path | None:
        try:
            return Path(self._workspace) / SUBAGENTS_DIRNAME / _RECORDS_DIRNAME
        except TypeError:
            return None

    def _path_for(self, session_key: str) -> Path | None:
        root = self.root
        if root is None:
            return None
        stem = safe_filename(session_key.replace(":", "_")) or "unknown"
        return root / f"{stem}.jsonl"

    # -- read ----------------------------------------------------------------

    def load(self, session_key: str, *, now: float | None = None) -> list[SubagentRecord]:
        """Record vivi (entro TTL) di una session key, dal piu vecchio al piu nuovo."""
        path = self._path_for(session_key)
        if path is None:
            return []
        return self._prune(self._read_file(path), now=now)

    def load_all(self, *, now: float | None = None) -> list[SubagentRecord]:
        """Tutti i record vivi, su tutte le session key."""
        root = self.root
        if root is None:
            return []
        records: list[SubagentRecord] = []
        try:
            paths = sorted(root.glob("*.jsonl"))
        except OSError as e:
            logger.warning("Subagent record dir unreadable {}: {}", root, e)
            return []
        for path in paths:
            records.extend(self._prune(self._read_file(path), now=now))
        return records

    def find(self, target_id: str) -> SubagentRecord | None:
        """Record che corrisponde a un task id o a un lineage id.

        Su un lineage con piu tentativi vince l'attempt piu alto: e quello che
        un rilancio deve continuare a numerare.
        """
        matches = [
            r for r in self.load_all()
            if r.task_id == target_id or r.lineage_id == target_id
        ]
        if not matches:
            return None
        return max(matches, key=lambda r: (r.attempt, r.ended_at))

    def _read_file(self, path: Path) -> list[SubagentRecord]:
        try:
            if not path.is_file():
                return []
            if path.stat().st_size > _MAX_RECORD_FILE_BYTES:
                logger.warning("Subagent record file too large, ignoring: {}", path)
                return []
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            logger.warning("Subagent record file unreadable {}: {}", path, e)
            return []

        records: list[SubagentRecord] = []
        skipped = 0
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(SubagentRecord.from_dict(json.loads(line)))
            except (json.JSONDecodeError, ValueError, TypeError):
                # Riga troncata da un kill a meta scrittura o record di una
                # versione incompatibile: la si salta, il resto dello storico
                # resta valido e il gateway boota comunque.
                skipped += 1
        if skipped:
            logger.warning("Skipped {} corrupt subagent record(s) in {}", skipped, path)
        return records

    # -- write ---------------------------------------------------------------

    def append(self, record: SubagentRecord, *, now: float | None = None) -> None:
        """Aggiunge un record e riscrive il file potato (TTL + cap).

        La potatura porta con se il digest di attivita dei record che escono: un
        file orfano su un telefono e una perdita lenta che nessuno va a guardare.
        La cancellazione avviene **dopo** la scrittura, cosi una scrittura
        fallita non porta via dei digest che il file su disco considera ancora
        vivi.
        """
        path = self._path_for(record.session_key)
        if path is None:
            return
        records = self._read_file(path)
        records.append(record)
        kept = self._prune(records, now=now)
        payload = "".join(
            json.dumps(r.to_dict(), ensure_ascii=False) + "\n" for r in kept
        )
        atomic_write(path, payload)
        self._drop_pruned_digests(records, kept)

    def _drop_pruned_digests(
        self,
        seen: list[SubagentRecord],
        kept: list[SubagentRecord],
    ) -> None:
        """Cancella i digest dei record appena potati. Best-effort, non solleva."""
        if self._digests is None:
            return
        alive = {r.task_id for r in kept}
        for record in seen:
            if record.task_id in alive or not record.has_activity_digest:
                continue
            try:
                self._digests.delete(record.task_id)
            except Exception as e:  # noqa: BLE001 — la pulizia non uccide la retention
                logger.warning(
                    "Could not delete activity digest for pruned record {}: {}",
                    record.task_id, e,
                )

    def _prune(
        self,
        records: list[SubagentRecord],
        *,
        now: float | None = None,
    ) -> list[SubagentRecord]:
        """Scarta i record oltre TTL e tiene solo gli ultimi ``max_per_session``."""
        cutoff = (time.time() if now is None else now) - self.ttl_s
        alive = [r for r in records if self.ttl_s <= 0 or r.ended_at >= cutoff]
        alive.sort(key=lambda r: r.ended_at)
        if len(alive) > self.max_per_session:
            alive = alive[-self.max_per_session:]
        return alive
