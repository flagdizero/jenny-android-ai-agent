"""Retention Tier-1 dei subagent: spec rieseguibile ed esito terminale su disco.

Un subagent non e piu una coroutine effimera. La descrizione del lavoro
(:class:`SubagentSpec`) e il suo esito (:class:`SubagentRecord`) sopravvivono
alla fine del task e alla morte del processo: su Android il gateway viene
ucciso spesso, e senza un record su disco un rilancio dopo il riavvio sarebbe
impossibile.

Il record e volutamente piccolo (~1-2 KB): porta spec, stato finale,
``stop_reason`` e un riassunto del risultato, non la storia dei messaggi.

Dove: tre tabelle di ``state.sqlite`` (lo stato che viaggia con gli snapshot e
il backup), ``subagent_records`` per gli esiti, ``subagent_running`` per i
segnaposti e ``subagent_digests`` per l'attività (``subagent_activity``). Fino
alla 2.x erano file sotto ``<workspace>/subagents/``; li importa una volta
``jenny/db_migration/subagents.py``.

Un tentativo **in corso** lascia un *segnaposto* a parte, scritto alla partenza
e tolto quando il record terminale e salvato. Non sta fra i record perche
quelli per contratto portano solo esiti: chi li legge (``find``,
``mark_handled``) non deve imparare a saltare i vivi. Un segnaposto che
sopravvive al processo e la prova che l'aiutante e morto con lui: al boot
``SubagentManager`` lo trasforma in un record terminale (v.
:meth:`SubagentRecordStore.load_running`).
"""

from __future__ import annotations

import json
import sqlite3
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
from jenny.db.schema import Schema
from jenny.security.workspace_access import (
    WorkspaceScope,
    workspace_sandbox_status,
)
from jenny.utils.helpers import truncate_text

# Stati coarse-grained del ciclo di vita di un subagent. Restano distinti da
# ``SubagentStatus.phase`` (fine-grained, già consumato altrove): il phase dice
# *cosa sta facendo* il subagent, lo state dice *se e ancora in gioco*.
#
# ``budget`` e terminale come ``failed`` ma non e un fallimento: il subagent si e
# fermato al tetto di iterazioni con la conversazione integra, e la sua storia
# Tier-2 resta su disco perche ``subagent_send`` lo faccia continuare da dove era.
# Fino al 24/10/2026 finiva in ``failed`` e ``_cleanup`` cancellava la storia,
# quindi ogni ripresa ripartiva da zero e il lavoro fatto si perdeva.
SUBAGENT_STATES = ("running", "done", "failed", "cancelled", "stalled", "budget")

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


#: Chi può fermare (e annullare) un aiutante: l'utente, o Jenny principale.
#: Un aiutante non ferma un altro aiutante (``subagent_control`` non è nel suo
#: scope).
STOPPED_BY = ("user", "jenny")


def cancellation_stop_reason(reason: str) -> str | None:
    """``stop_reason`` che porta la provenienza, o ``None`` se sconosciuta."""
    entry = _CANCEL_PROVENANCE.get(reason)
    return entry[0] if entry is not None else None


def cancellation_summary(reason: str) -> str:
    """Riassunto user/orchestrator-facing per una provenienza di cancellazione."""
    entry = _CANCEL_PROVENANCE.get(reason)
    return entry[1] if entry is not None else "This subagent was cancelled."


#: Il riassunto di un aiutante interrotto da un riavvio **con** una fotografia
#: della conversazione da cui ripartire. La regola di ``cancelled_at_shutdown``
#: («rilancialo») qui sarebbe sbagliata: rilanciarlo butterebbe il lavoro che
#: la fotografia esiste per salvare.
INTERRUPTED_RESUMABLE_SUMMARY = (
    "The app was closed while this subagent was still working, so its work was "
    "interrupted, not finished or refused. Its conversation up to that point was "
    "saved: continue it with subagent_send (it picks up where it stopped) instead "
    "of relaunching it from scratch."
)


# Il tipo di agente e definito in ``jenny/agent/agent_types.py``; qui viene
# ri-esportato perche i consumatori storici della spec importano da questo
# modulo.
__all__ = [
    "ActivityDigestDeleter",
    "CANCEL_REASON_SHUTDOWN",
    "CANCEL_REASON_SUPERSEDED",
    "CANCEL_REASON_USER",
    "DEFAULT_AGENT_TYPE",
    "INTERRUPTED_RESUMABLE_SUMMARY",
    "MAX_RECORDS_PER_SESSION",
    "MAX_RESULT_SUMMARY_CHARS",
    "RECORD_TTL_S",
    "STOPPED_BY",
    "SUBAGENT_CANCEL_REASONS",
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

SCHEMA = Schema(
    "subagents",
    (
        """
        CREATE TABLE subagent_records (
          task_id TEXT PRIMARY KEY,
          session_key TEXT NOT NULL,
          ended_at REAL NOT NULL,
          data TEXT NOT NULL
        );
        CREATE INDEX subagent_records_session ON subagent_records(session_key);
        CREATE TABLE subagent_running (
          task_id TEXT PRIMARY KEY,
          started_at REAL NOT NULL,
          data TEXT NOT NULL
        );
        CREATE TABLE subagent_digests (
          task_id TEXT PRIMARY KEY,
          data TEXT NOT NULL
        );
        """,
    ),
    # Negli snapshot: i record di una conversazione cambiano insieme, un digest
    # si scrive una volta sola.
    shards={"subagent_records": "session_key", "subagent_digests": "task_id"},
)


def subagents_database(workspace: Any):
    """Lo ``state.sqlite`` di *workspace* con le tabelle degli aiutanti, o ``None``.

    ``None`` per un workspace che non è un percorso (un doppio nei test, il
    bootstrap prima che esista): lo store va costruibile anche lì.
    """
    if not isinstance(workspace, (str, Path)):
        return None
    from jenny import db

    return db.store_database(workspace, db.STATE, SCHEMA)


def rename_session_records(workspace: Any, old_key: str, new_key: str) -> int:
    """I record di *old_key* passano a *new_key* (il rinomino di un progetto).

    Anche dentro il record: ``spec.session_key`` dice dove riportare l'esito di
    un rilancio, e un nome vecchio lo manderebbe a una chat che non c'è più.
    """
    database = subagents_database(workspace)
    if database is None or old_key == new_key:
        return 0
    with database.write() as conn:
        rows = conn.execute("SELECT task_id, data FROM subagent_records WHERE session_key = ?",
                            (old_key,)).fetchall()
        for row in rows:
            data = json.loads(row["data"])
            data["session_key"] = new_key
            if isinstance(data.get("spec"), dict):
                data["spec"]["session_key"] = new_key
            conn.execute("UPDATE subagent_records SET session_key = ?, data = ? WHERE task_id = ?",
                         (new_key, json.dumps(data, ensure_ascii=False), row["task_id"]))
    return len(rows)


def delete_session_records(workspace: Any, session_key: str) -> int:
    """Toglie i record di *session_key* e i loro digest (un progetto cancellato)."""
    database = subagents_database(workspace)
    if database is None:
        return 0
    with database.write() as conn:
        ids = [r["task_id"] for r in conn.execute(
            "SELECT task_id FROM subagent_records WHERE session_key = ?", (session_key,))]
        conn.executemany("DELETE FROM subagent_digests WHERE task_id = ?", [(i,) for i in ids])
        conn.execute("DELETE FROM subagent_records WHERE session_key = ?", (session_key,))
    return len(ids)

# Marcatore della nota correttiva iniettata da un rilancio: appesa al task
# originale invece di sostituirlo, cosi il lineage resta lo stesso lavoro.
_CORRECTIVE_HEADER = "## Corrective note from the previous attempt"


def _coerce_str(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) else default


# Lunghezza massima del label ricavato dal task quando il modello non ne da uno.
_DEFAULT_LABEL_CHARS = 40


def default_label(task: str) -> str:
    """Il label di ripiego, dalle prime parole del task.

    La casa lo mostra in «Ho mandato una mini-me: {label}». Fino al 14/10/2026
    era ``task[:30] + "..."``, che tagliava a meta parola («Research the best
    way to co...»); ora si ferma all'ultimo spazio, e taglia secco solo se in
    quei caratteri uno spazio non c'e.
    """
    text = " ".join(task.split())
    if len(text) <= _DEFAULT_LABEL_CHARS:
        return text
    head = text[:_DEFAULT_LABEL_CHARS]
    cut = head.rfind(" ")
    if cut > 0:
        head = head[:cut]
    return head.rstrip(" ,;:.-") + "…"


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
    # Fonti non fidate da cui il lavoro nasce gia' contaminato: un compito
    # scritto da Jenny dopo aver letto un risultato non fidato, o la ripresa di
    # una conversazione che ne conteneva. Non vuoto ⇒ il lavoro parte
    # *untrusted* (v. ``security.subagent_policy``). Sta nella spec perche' un
    # rilancio rigioca lo stesso compito, e quindi lo stesso testo.
    taint_seed: tuple[str, ...] = ()
    # Il piano e il passo del turno che ha lanciato il lavoro
    # (``current_request_context().metadata``), ``None`` fuori da un piano. Nella
    # spec, e non solo nello status, perche un rilancio o una ripresa restano
    # lavoro dello stesso passo, e la casa raggruppa gli aiutanti per passo.
    plan_id: str | None = None
    plan_step: str | None = None
    # I passi chiesti da chi ha lanciato (``spawn steps``); ``None`` = il default
    # del tipo. Nella spec perché un rilancio ha lo stesso budget.
    steps: int | None = None

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
            "taint_seed": list(self.taint_seed),
            "plan_id": self.plan_id,
            "plan_step": self.plan_step,
            "steps": self.steps,
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
            label=_coerce_str(raw.get("label")) or default_label(task),
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
            # Un record scritto prima dell'11/10/2026 non ha la chiave: parte pulito.
            taint_seed=tuple(
                x for x in (raw.get("taint_seed") if isinstance(raw.get("taint_seed"), list) else [])
                if isinstance(x, str)
            )[:8],
            # Chiavi aggiunte il 24/10/2026: un record precedente non le ha.
            plan_id=raw.get("plan_id") if isinstance(raw.get("plan_id"), str) else None,
            plan_step=raw.get("plan_step") if isinstance(raw.get("plan_step"), str) else None,
            # Dal 04/11/2026: un record precedente non la ha, e vale il default del tipo.
            steps=(
                raw["steps"]
                if isinstance(raw.get("steps"), int) and not isinstance(raw.get("steps"), bool)
                and raw["steps"] > 0
                else None
            ),
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
    # vive in una riga sua (``subagent_digests``). Qui stanno solo le due
    # misure, non gli eventi: il record va riscritto a ogni transizione
    # terminale, quindi infilarci il digest renderebbe ogni transizione una
    # riscrittura da centinaia di KB. Con questi due campi la UI
    # decide se offrire o nascondere il blocco "cosa ha fatto davvero" **senza
    # aprire il file**.
    activity_events: int = 0
    activity_bytes: int = 0
    # Quando il resoconto di questo tentativo e arrivato a Jenny e il suo turno
    # e finito (``SubagentManager.mark_handled``), orologio di parete. La casa lo
    # usa per togliere la scheda «si e fermato» quando Jenny ha gia deciso cosa
    # farne: senza, la scheda restava li anche dopo che Jenny aveva ripreso il
    # lavoro con un aiutante nuovo.
    handled_at: float | None = None
    # Fermato al budget con una storia ancora riprendibile (``budget`` e
    # ``SubagentManager.continuable``), misurato una volta alla terminazione:
    # lo snapshot si pubblica ogni pochi secondi e non deve rileggere la storia.
    # Vale anche per un tentativo ``cancelled`` per ``shutdown`` — morto col
    # processo o fermato da uno shutdown ordinato — che ha lasciato una
    # fotografia della conversazione (``SubagentManager.interrupted_helpers``).
    resumable: bool = False
    # Riprese dopo un budget gia spese dal lineage fino a questo tentativo: dopo
    # un riavvio e cio che fa rispettare a ``send`` il tetto delle riprese
    # (``SubagentManager``, ``MAX_BUDGET_RESUMES``).
    budget_resumes: int = 0
    # Chi l'ha fermato, per un ``cancelled`` per ``user``: ``"user"`` (il tasto
    # della casa, l'officina, ``/stop``) o ``"jenny"`` (``subagent_cancel``). La
    # casa lo dice sulla scheda: «fermata da te», «fermata da Jenny».
    stopped_by: str | None = None

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
            "handled_at": self.handled_at,
            "resumable": self.resumable,
            "budget_resumes": self.budget_resumes,
            "stopped_by": self.stopped_by,
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
            handled_at=_coerce_float(raw.get("handled_at")) or None,
            resumable=raw.get("resumable") is True,
            budget_resumes=max(0, _coerce_int(raw.get("budget_resumes"), 0)),
            stopped_by=(
                raw.get("stopped_by") if raw.get("stopped_by") in STOPPED_BY else None
            ),
        )


class SubagentRecordStore:
    """I record terminali degli aiutanti, al più ``max_per_session`` per session key.

    Righe di ``state.sqlite``: ogni scrittura è una transazione, quindi l'ultimo
    esito — proprio quello che serve dopo un kill improvviso — c'è intero o non
    c'è.
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
        # non per import: il modulo dell'attivita importa da qui, quindi
        # l'import inverso sarebbe un ciclo.
        self._digests = digest_store

    @property
    def _db(self):
        return subagents_database(self._workspace)

    # -- tentativi in corso ---------------------------------------------------

    def mark_running(self, record: SubagentRecord) -> None:
        """Scrive il segnaposto di un tentativo che parte (``state="running"``).

        Solleva sugli errori del database: il chiamante decide
        (``SubagentManager`` logga e prosegue).
        """
        database = self._db
        if database is None or not record.task_id:
            return
        with database.write() as conn:
            conn.execute(
                "INSERT INTO subagent_running(task_id, started_at, data) VALUES (?, ?, ?) "
                "ON CONFLICT(task_id) DO UPDATE SET started_at = excluded.started_at, "
                "data = excluded.data",
                (record.task_id, record.started_at,
                 json.dumps(record.to_dict(), ensure_ascii=False)),
            )

    def clear_running(self, task_id: str) -> None:
        """Toglie il segnaposto di *task_id*. Non solleva."""
        database = self._db
        if database is None:
            return
        try:
            with database.write() as conn:
                conn.execute("DELETE FROM subagent_running WHERE task_id = ?", (task_id,))
        except sqlite3.Error as e:
            logger.warning("Could not delete running marker {}: {}", task_id, e)

    def load_running(self) -> list[SubagentRecord]:
        """I segnaposti rimasti, dal piu vecchio al piu nuovo.

        Letto al boot, prima che parta qualunque aiutante: ogni voce e quindi un
        tentativo morto col processo precedente. Una riga illeggibile si toglie
        (non dice piu niente di utile) e non ferma le altre.
        """
        database = self._db
        if database is None:
            return []
        out: list[SubagentRecord] = []
        bad: list[str] = []
        for row in database.read("SELECT task_id, data FROM subagent_running"):
            try:
                out.append(SubagentRecord.from_dict(json.loads(row["data"])))
            except (json.JSONDecodeError, ValueError, TypeError) as e:
                logger.warning("Dropping unreadable running marker {}: {}", row["task_id"], e)
                bad.append(row["task_id"])
        if bad:
            with database.write() as conn:
                conn.executemany("DELETE FROM subagent_running WHERE task_id = ?",
                                 [(i,) for i in bad])
        out.sort(key=lambda r: r.started_at)
        return out

    # -- read ----------------------------------------------------------------

    def _rows(self, where: str = "", params: tuple = ()) -> dict[str, list[SubagentRecord]]:
        """I record per session key; una riga illeggibile si salta."""
        database = self._db
        if database is None:
            return {}
        out: dict[str, list[SubagentRecord]] = {}
        bad = 0
        for row in database.read(f"SELECT session_key, data FROM subagent_records {where}",
                                 params):
            try:
                record = SubagentRecord.from_dict(json.loads(row["data"]))
            except (json.JSONDecodeError, ValueError, TypeError):
                bad += 1
                continue
            out.setdefault(row["session_key"], []).append(record)
        if bad:
            logger.warning("Skipped {} corrupt subagent record(s)", bad)
        return out

    def load(self, session_key: str, *, now: float | None = None) -> list[SubagentRecord]:
        """Record vivi (entro TTL) di una session key, dal piu vecchio al piu nuovo."""
        rows = self._rows("WHERE session_key = ?", (session_key,))
        return self._prune(rows.get(session_key, []), now=now)

    def load_all(self, *, now: float | None = None) -> list[SubagentRecord]:
        """Tutti i record vivi, su tutte le session key."""
        records: list[SubagentRecord] = []
        for _key, group in sorted(self._rows().items()):
            records.extend(self._prune(group, now=now))
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

    # -- write ---------------------------------------------------------------

    def mark_handled(
        self, task_id: str, at: float, *, session_key: str | None = None,
    ) -> SubagentRecord | None:
        """Scrive ``handled_at`` sul record di *task_id*, se c'e e non l'ha gia.

        Ritorna il record (aggiornato o gia marcato), o ``None`` se non esiste.
        Il primo istante vince: un resoconto consegnato due volte non deve
        spostare l'ora in cui Jenny l'ha avuto. *session_key* resta per i
        chiamanti: la riga si trova comunque per id.
        """
        database = self._db
        if database is None or not task_id:
            return None
        with database.write() as conn:
            row = conn.execute("SELECT data FROM subagent_records WHERE task_id = ?",
                               (task_id,)).fetchone()
            if row is None:
                return None
            try:
                found = SubagentRecord.from_dict(json.loads(row["data"]))
            except (json.JSONDecodeError, ValueError, TypeError):
                return None
            if found.handled_at is not None:
                return found
            found.handled_at = at
            conn.execute("UPDATE subagent_records SET data = ? WHERE task_id = ?",
                         (json.dumps(found.to_dict(), ensure_ascii=False), task_id))
            return found

    def append(self, record: SubagentRecord, *, now: float | None = None) -> None:
        """Aggiunge un record e pota la sua session key (TTL + cap).

        La potatura porta con se il digest di attivita dei record che escono.
        La cancellazione avviene **dopo** la scrittura, cosi una scrittura
        fallita non porta via dei digest di record ancora vivi.
        """
        database = self._db
        if database is None:
            return
        with database.write() as conn:
            conn.execute(
                "INSERT INTO subagent_records(task_id, session_key, ended_at, data) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(task_id) DO UPDATE SET "
                "session_key = excluded.session_key, ended_at = excluded.ended_at, "
                "data = excluded.data",
                (record.task_id, record.session_key, record.ended_at,
                 json.dumps(record.to_dict(), ensure_ascii=False)),
            )
            records = []
            for row in conn.execute("SELECT data FROM subagent_records WHERE session_key = ?",
                                    (record.session_key,)):
                try:
                    records.append(SubagentRecord.from_dict(json.loads(row["data"])))
                except (json.JSONDecodeError, ValueError, TypeError):
                    continue
            kept = self._prune(records, now=now)
            alive = {r.task_id for r in kept}
            conn.executemany("DELETE FROM subagent_records WHERE task_id = ?",
                             [(r.task_id,) for r in records if r.task_id not in alive])
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
