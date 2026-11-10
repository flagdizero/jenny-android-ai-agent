"""Gli aiutanti che un riavvio ha interrotto: ritrovarli, dirlo, chiuderli.

Al boot: i lavori rimasti a metà, e le tracce che non sono più di nessuno.

Un pezzo di :class:`jenny.agent.subagent.SubagentManager` (mixin): il testo dei
metodi è quello di prima, spostato qui per responsabilità.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger

from jenny.agent.subagent_activity import KIND_ERROR, STATUS_ERROR
from jenny.agent.subagent_common import SubagentStatus, _append_activity
from jenny.agent.subagent_history import KIND_BUDGET, KIND_INTERRUPTED
from jenny.agent.subagent_records import (
    CANCEL_REASON_SHUTDOWN,
    INTERRUPTED_RESUMABLE_SUMMARY,
    MAX_RESULT_SUMMARY_CHARS,
    SubagentRecord,
    cancellation_stop_reason,
    cancellation_summary,
)
from jenny.utils.helpers import truncate_text

if TYPE_CHECKING:
    from jenny.agent.subagent_activity import SubagentActivityLog, SubagentDigestStore


class RecoveryMixin:
    """Al boot: i lavori rimasti a metà, e le tracce che non sono più di nessuno.

    Mixin di ``SubagentManager``.
    """

    if TYPE_CHECKING:
        # Contratto host↔mixin (solo per il type-checker; nessun effetto a
        # runtime): gli attributi di ``SubagentManager`` e i metodi degli
        # altri pezzi che questi metodi usano.
        _history: Any
        _records: Any
        _task_statuses: dict[str, SubagentStatus]
        activity: SubagentActivityLog
        digests: SubagentDigestStore

        def list_records(self, session_key: str | None=None) -> list[SubagentRecord]: ...

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
