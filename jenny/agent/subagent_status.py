"""Lo stato degli aiutanti come lo vede la casa: istantanee, transizioni, schede lette.

Le istantanee di stato, la loro pubblicazione, e i record di chi ha finito.

Un pezzo di :class:`jenny.agent.subagent.SubagentManager` (mixin): il testo dei
metodi è quello di prima, spostato qui per responsabilità.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from loguru import logger

from jenny.agent.progress_events import tool_family
from jenny.agent.subagent_activity import DigestMeta
from jenny.agent.subagent_common import MAX_AUTO_ATTEMPTS, MAX_BUDGET_RESUMES, SubagentStatus
from jenny.agent.subagent_history import HISTORY_TTL_S
from jenny.agent.subagent_records import INTERRUPTED_RESUMABLE_SUMMARY, SubagentRecord, SubagentSpec
from jenny.bus.events import INTERNAL_CHANNEL, OUTBOUND_META_SUBAGENT_STATUS, OutboundMessage
from jenny.session.turn_visibility import resolve_turn_visibility
from jenny.utils.helpers import truncate_text

if TYPE_CHECKING:
    from jenny.agent.subagent_activity import SubagentActivityLog, SubagentDigestStore
    from jenny.agent.write_journal import UndoReport
    from jenny.bus.queue import MessageBus


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


class StatusMixin:
    """Le istantanee di stato, la loro pubblicazione, e i record di chi ha finito.

    Mixin di ``SubagentManager``.
    """

    if TYPE_CHECKING:
        # Contratto host↔mixin (solo per il type-checker; nessun effetto a
        # runtime): gli attributi di ``SubagentManager`` e i metodi degli
        # altri pezzi che questi metodi usano.
        _MAX_RECORD_KEYS: int
        _handled_pending: dict[str, float]
        _history: Any
        _progress_published_at: dict[str, float]
        _record_keys: dict[str, str]
        _records: Any
        _session_tasks: dict[str, set[str]]
        _task_statuses: dict[str, SubagentStatus]
        activity: SubagentActivityLog
        bus: MessageBus
        digests: SubagentDigestStore

        @staticmethod
        def _interrupted_resumable(record: SubagentRecord) -> bool: ...

        def _journal_view(self, lineage_id: str) -> tuple[int, UndoReport | None]: ...

        @staticmethod
        def _kept_at_shutdown(state: str, status: SubagentStatus | None) -> bool: ...

        def _spec_for_task(self, task_id: str) -> SubagentSpec | None: ...

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
                # Cosa sta facendo, a parole della casa (``progress_events``).
                "last_tool_family": (
                    tool_family(last) if (last := self._last_tool_name(st.tool_events)) else None
                ),
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
                # annulla (``agent.write_journal.UndoReport.summary``).
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

        Un subagent lanciato da un cron monitor e' lavoro
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
