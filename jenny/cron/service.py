"""Cron service for scheduling agent tasks."""

import asyncio
import json
import time
import uuid
from contextlib import suppress
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Coroutine, Literal, NamedTuple

from filelock import SoftFileLock
from loguru import logger

from jenny.cron.session_turns import is_bound_cron_job
from jenny.cron.silence_watchdog import alert_silently_broken_checks
from jenny.cron.types import (
    CronJob,
    CronJobSilencedError,
    CronJobState,
    CronMonitorCouldNotCheckError,
    CronPayload,
    CronRunRecord,
    CronSchedule,
    CronStore,
    CronTaskCheckState,
)

# Modulo, non simboli: i test sostituiscono ``power.schedule_wake`` &co. per
# osservare le sveglie senza un device, e con ``from … import`` la sostituzione
# non arriverebbe qui.
from jenny.runtime import power
from jenny.session.keys import normalize_user_session_key
from jenny.utils.clock import now_ms as _now_ms
from jenny.utils.path import atomic_write

if TYPE_CHECKING:
    from jenny.cron.heartbeat_followup import HeartbeatFollowup

_LockClass = SoftFileLock


class _LoadedStore(NamedTuple):
    """Esito di una lettura dello store, ripiego incluso.

    ``recovered_from`` (``"backup"`` | ``"empty"`` | ``None``) viaggia insieme
    ai job invece di essere registrato subito sul ``RuntimeContext``: chi legge
    il file non sa se in memoria c'è già uno snapshot più fresco, e avvisare
    l'utente di una perdita che non c'è stata è un modo lento di far ignorare
    gli avvisi.
    """

    jobs: list[CronJob]
    version: int
    recovered_from: str | None
    quarantined: Path | None


class CronJobSkippedError(Exception):
    """Raised by cron callbacks when a job was intentionally skipped."""



_CRON_MODES: tuple[str, ...] = ("reminder", "monitor")

# Quanti record di esecuzione tenere in ``runs/``. Fino al 24/08/2026 non c'era
# nessun tetto e nessuna potatura: la directory cresceva per sempre, e i record
# di un job cancellato restavano li' dopo di lui.
#
# **Un conteggio e non un'eta'.** Una scadenza a giorni non protegge da un job
# che gira ogni minuto; un tetto sul numero si', e vale in entrambi i regimi. A
# due esecuzioni al giorno (il ritmo misurato sul telefono) sono circa otto
# mesi; a una ogni cinque minuti, un giorno e mezzo.
#
# Si puo' scegliere un numero senza tante cerimonie perche' **quei record non li
# legge nessuno**: ne' il gateway, ne' le route della WebUI, ne' il client, ne' il
# tool ``cron``. Sono una traccia per il post-mortem, e basta.
_RUN_RECORDS_KEEP = 500


def _load_session_key(raw: Any) -> str | None:
    """Legge ``payload.sessionKey`` **da disco** riportandolo alla forma corrente.

    Gli store scritti prima del 2026-08-21 contengono chiavi come
    ``websocket:default``, che ``CronTool`` fabbricava dal canale: non sono
    sessioni, e ``bound_runner`` le usa come chiave del turno. Lasciarle passare
    farebbe girare per sempre quei job in un file di sessione tutto loro, accanto
    alla conversazione a cui appartengono.

    La migrazione avviene **in memoria**: il file non viene riscritto qui: si
    aggiorna da solo alla prima modifica dello store. Un caricamento non deve
    scrivere — se il processo muore a metà, un job non deve poter cambiare forma.
    """
    if not isinstance(raw, str) or not raw:
        return None
    return normalize_user_session_key(raw)


def _parse_mode(raw: Any) -> Literal["reminder", "monitor"]:
    """Legge ``payload.mode`` **da disco** tollerando qualunque valore.

    Assente (store scritto prima che il campo esistesse) o sconosciuto (store
    scritto da una versione più recente, o modificato a mano): in entrambi i
    casi si ricade su ``"reminder"``. Un modo che non sappiamo eseguire non
    deve impedire l'avvio, e parlare sempre è il ripiego che non perde nulla —
    un job che non dice niente perché non sapevamo interpretarlo sarebbe
    indistinguibile da un job rotto.

    Volutamente **diversa** da ``_validate_mode_for_add``, che invece solleva:
    vedi la spiegazione dell'asimmetria lì sotto.
    """
    return "monitor" if raw == "monitor" else "reminder"


def _validate_mode_for_add(raw: Any) -> Literal["reminder", "monitor"]:
    """Valida il ``mode`` che arriva da un **chiamante vivo**: solleva se ignoto.

    L'asimmetria con ``_parse_mode`` è deliberata, non una svista. Dal disco
    accettiamo tutto perché il file può venire da una versione futura e non
    deve impedire l'avvio; da un chiamante, invece, un modo che non conosciamo
    è un bug del chiamante, e ingoiarlo produce il guasto peggiore che questa
    funzione possa causare: un monitor che diventa reminder in silenzio e
    torna a parlare a ogni ciclo, senza che niente — né stato, né log, né
    store — dica perché. Meglio rumoroso adesso che inspiegabile fra settimane.

    Raises:
        ValueError: se ``raw`` non è uno dei modi ammessi.
    """
    if raw not in _CRON_MODES:
        raise ValueError(
            f"unknown cron mode {raw!r}; expected one of: {', '.join(_CRON_MODES)}"
        )
    return "monitor" if raw == "monitor" else "reminder"


def _compute_next_run(schedule: CronSchedule, now_ms: int) -> int | None:
    """Compute next run time in ms."""
    if schedule.kind == "at":
        return schedule.at_ms if schedule.at_ms and schedule.at_ms > now_ms else None

    if schedule.kind == "every":
        if not schedule.every_ms or schedule.every_ms <= 0:
            return None
        # Next interval from now
        return now_ms + schedule.every_ms

    if schedule.kind == "cron" and schedule.expr:
        try:
            from jenny.cron.cronexpr import next_after
            from jenny.utils.helpers import safe_zoneinfo
            # Use caller-provided reference time for deterministic scheduling
            base_time = now_ms / 1000
            # safe_zoneinfo non solleva mai (fallback: offset locale, poi UTC).
            tz = safe_zoneinfo(schedule.tz) if schedule.tz else datetime.now().astimezone().tzinfo
            base_dt = datetime.fromtimestamp(base_time, tz=tz)
            # Non più croniter: v. il cappello di ``cronexpr`` per il perché.
            return int(next_after(schedule.expr, base_dt).timestamp() * 1000)
        except Exception:
            return None

    return None


def _next_run_with_catch_up(schedule: CronSchedule, now_ms: int) -> int | None:
    """``_compute_next_run``, ma un one-shot già scaduto non si perde.

    ``_compute_next_run`` torna ``None`` per un ``at`` con scadenza passata, ed
    è la risposta giusta a "qual è la PROSSIMA esecuzione" — dopo che il job è
    scattato non ce n'è una. Ma scriverla nello stato di un job ancora abilitato
    produce l'unico stato che nessun altro percorso sa creare: ``enabled`` a
    True con ``next_run_at_ms`` a None. All'occhio è identico a un one-shot già
    eseguito (vedi il blocco one-shot in ``_execute_job``), e significa
    l'opposto — ma nessuno lo ripulisce: ``_get_next_wake_ms`` lo salta, il
    filtro dei job dovuti lo salta, e il promemoria resta lì per sempre senza
    partire né dare errore.

    Tenendo la scadenza passata, ``_arm_timer`` calcola delay 0 e il primo tick
    lo esegue; poi il blocco one-shot di ``_execute_job`` lo disabilita o lo
    cancella come qualunque altro ``at``. È lo stesso "meglio in ritardo che
    mai" già promesso in ``docs/using/scheduling.md`` e già valido per
    ``every``.

    NON va usata dopo l'esecuzione: lì ``None`` è la risposta corretta e
    ``_execute_job`` chiama apposta ``_compute_next_run``.
    """
    computed = _compute_next_run(schedule, now_ms)
    if computed is None and schedule.kind == "at" and schedule.at_ms:
        return schedule.at_ms
    return computed


def _validate_schedule_for_add(schedule: CronSchedule) -> None:
    """Validate schedule fields that would otherwise create non-runnable jobs."""
    if schedule.tz and schedule.kind != "cron":
        raise ValueError("tz can only be used with cron schedules")

    if schedule.kind == "cron" and schedule.tz:
        from jenny.utils.helpers import validate_timezone_name

        # Degrada (accetta) quando il database tzdata manca del tutto.
        if msg := validate_timezone_name(schedule.tz):
            raise ValueError(msg)

    if schedule.kind == "cron":
        _validate_cron_expr(schedule)


def _validate_cron_expr(schedule: CronSchedule) -> None:
    """Un'espressione che non si legge, o che non scatta mai, si rifiuta qui.

    ``_compute_next_run`` inghiotte ogni errore e torna ``None``, ed e' giusto:
    lo chiamano anche il boot e il timer, su job gia' salvati, e li' un'eccezione
    non ha nessuno a cui arrivare. Ma all'aggiunta quel ``None`` diventava un job
    abilitato che non parte mai e non lo dice — ``0 25 * * *`` accettato in
    silenzio. Qui c'e' ancora qualcuno a cui dirlo.
    """
    from jenny.cron.cronexpr import next_after
    from jenny.utils.helpers import safe_zoneinfo

    if not schedule.expr:
        raise ValueError("a cron schedule needs an expression")
    tz = safe_zoneinfo(schedule.tz) if schedule.tz else datetime.now().astimezone().tzinfo
    try:
        next_after(schedule.expr, datetime.now(tz))
    except ValueError as exc:
        raise ValueError(f"invalid cron expression {schedule.expr!r}: {exc}") from None


class CronService:
    """Service for managing and executing scheduled jobs."""

    _MAX_RUN_HISTORY = 20
    _UNBOUND_AGENT_JOB_REASON = (
        "agent cron payload is missing bound session delivery context; "
        "recreate it from a chat session"
    )

    def __init__(
        self,
        store_path: Path,
        on_job: Callable[[CronJob], Coroutine[Any, Any, str | None]] | None = None,
        max_sleep_ms: int = 300_000,  # 5 minutes
    ):
        self.store_path = store_path
        self._action_path = store_path.parent / "action.jsonl"
        self._run_records_dir = store_path.parent / "runs"
        self._lock = _LockClass(str(self._action_path.parent) + ".lock")
        self.on_job = on_job
        self._store: CronStore | None = None
        self._timer_task: asyncio.Task | None = None
        self._running = False
        self._timer_active = False
        self.max_sleep_ms = max_sleep_ms
        # ── sveglia di sistema (anti-doze) ──
        # Evento acceso da ``power.on_wake_tick`` quando l'alarm dell'OS ci
        # risveglia; ``None`` quando le sveglie non sono in gioco (desktop/CI o
        # ``alarm_driven_cron`` spento), e allora il timer resta il solo padrone.
        self._wake_event: asyncio.Event | None = None
        # Ultima scadenza *dichiarata* e ultima *applicata* al bridge. Sono due
        # perché il bridge è async e i chiamanti no: fra la dichiarazione e
        # l'applicazione passa un giro di loop, e nel mezzo la scadenza può
        # cambiare di nuovo.
        self._alarm_target_ms: int | None = None
        self._alarm_armed_ms: int | None = None
        self._alarm_lock = asyncio.Lock()
        self._alarm_tasks: set[asyncio.Task] = set()
        # Registrato dal ``CronDispatcher`` (che è chi conosce l'heartbeat) e
        # letto dall'``AgentLoop`` sul turno di ritorno di un subagent. Sta qui
        # perché il servizio cron è l'unico oggetto che entrambi hanno già in
        # mano: il turno d'annuncio non passa dal dispatcher — arriva dal bus —
        # e senza questo aggancio l'esito di un controllo delegato non avrebbe
        # nessuna strada per tornare nello stato del job.
        self.heartbeat_followup: "HeartbeatFollowup | None" = None

    def _is_unbound_agent_job(self, job: CronJob) -> bool:
        return job.payload.kind == "agent_turn" and not is_bound_cron_job(job)

    def _enforce_agent_binding(self, job: CronJob) -> bool:
        """Disable user cron jobs that cannot be routed to a concrete session."""
        if not self._is_unbound_agent_job(job):
            return False
        if (
            not job.enabled
            and job.state.next_run_at_ms is None
            and job.state.last_status == "error"
            and job.state.last_error
        ):
            return False

        job.enabled = False
        job.state.next_run_at_ms = None
        job.state.last_status = "error"
        job.state.last_error = self._UNBOUND_AGENT_JOB_REASON
        job.updated_at_ms = max(job.updated_at_ms, _now_ms())
        logger.warning(
            "Cron: disabled unbound agent job '{}' ({}): {}",
            job.name,
            job.id,
            self._UNBOUND_AGENT_JOB_REASON,
        )
        return True

    def _enforce_store_agent_bindings(self) -> bool:
        if not self._store:
            return False
        changed = False
        for job in self._store.jobs:
            changed = self._enforce_agent_binding(job) or changed
        return changed

    @property
    def _backup_path(self) -> Path:
        return self.store_path.with_name(self.store_path.name + ".bak")

    def _rotate_backup(self) -> None:
        """Conserva l'ultimo contenuto *valido* come ``jobs.json.bak``.

        Solo se quello attuale si rilegge come JSON: un salvataggio partito da
        uno store già rotto distruggerebbe l'ultimo backup buono, che è
        esattamente quello che serve un istante dopo.
        """
        if not self.store_path.exists():
            return
        try:
            content = self.store_path.read_text(encoding="utf-8")
            json.loads(content)
        except (OSError, json.JSONDecodeError):
            return
        try:
            atomic_write(self._backup_path, content, fsync_dir=False)
        except OSError as e:
            # Rete di sicurezza, non requisito: il salvataggio vero procede.
            logger.warning("Could not refresh the cron store backup: {}", e)

    def _parse_jobs(self, path: Path) -> tuple[list[CronJob], int]:
        """Parse *path* into ``(jobs, version)``. Solleva se il file non è usabile."""
        data = json.loads(path.read_text(encoding="utf-8"))
        jobs: list[CronJob] = []
        version = data.get("version", 1)
        for j in data.get("jobs", []):
            job = CronJob(
                id=j["id"],
                name=j["name"],
                enabled=j.get("enabled", True),
                schedule=CronSchedule(
                    kind=j["schedule"]["kind"],
                    at_ms=j["schedule"].get("atMs"),
                    every_ms=j["schedule"].get("everyMs"),
                    expr=j["schedule"].get("expr"),
                    tz=j["schedule"].get("tz"),
                ),
                payload=CronPayload(
                    kind=j["payload"].get("kind", "agent_turn"),
                    mode=_parse_mode(j["payload"].get("mode")),
                    message=j["payload"].get("message", ""),
                    session_key=_load_session_key(j["payload"].get("sessionKey")),
                    origin_channel=j["payload"].get("originChannel"),
                    origin_chat_id=j["payload"].get("originChatId"),
                    origin_metadata=j["payload"].get("originMetadata") or {},
                ),
                state=CronJobState(
                    next_run_at_ms=j.get("state", {}).get("nextRunAtMs"),
                    last_run_at_ms=j.get("state", {}).get("lastRunAtMs"),
                    last_status=j.get("state", {}).get("lastStatus"),
                    last_error=j.get("state", {}).get("lastError"),
                    # Assenti negli store scritti prima del terzo stato: i
                    # default del dataclass valgono "monitor sano", che è il
                    # ripiego giusto — un contatore inventato farebbe partire
                    # un avviso per un guasto che non c'è.
                    consecutive_could_not_check=(
                        j.get("state", {}).get("consecutiveCouldNotCheck") or 0
                    ),
                    could_not_check_since_ms=j.get("state", {}).get("couldNotCheckSinceMs"),
                    could_not_check_escalated=bool(
                        j.get("state", {}).get("couldNotCheckEscalated")
                    ),
                    task_checks={
                        task_id: CronTaskCheckState(
                            consecutive_could_not_check=entry.get(
                                "consecutiveCouldNotCheck"
                            ) or 0,
                            since_ms=entry.get("sinceMs"),
                            escalated=bool(entry.get("escalated")),
                            # Assente nelle voci scritte prima di questo campo:
                            # ``None`` significa "gli si è parlato, non si sa
                            # quando", cioè non riarmabile. V. ``CronTaskCheckState``.
                            escalated_at_ms=entry.get("escalatedAtMs"),
                            label=entry.get("label") or "",
                            pending_since_ms=entry.get("pendingSinceMs"),
                        )
                        for task_id, entry in (
                            j.get("state", {}).get("taskChecks") or {}
                        ).items()
                    },
                    run_history=[
                        CronRunRecord(
                            run_at_ms=r["runAtMs"],
                            status=r["status"],
                            duration_ms=r.get("durationMs", 0),
                            error=r.get("error"),
                        )
                        for r in j.get("state", {}).get("runHistory", [])
                    ],
                ),
                created_at_ms=j.get("createdAtMs", 0),
                updated_at_ms=j.get("updatedAtMs", 0),
                delete_after_run=j.get("deleteAfterRun", False),
            )
            jobs.append(job)
        return jobs, version

    def _quarantine_store(self) -> Path | None:
        """Sposta di lato lo store illeggibile. ``None`` se non ci è riuscita.

        La distinzione è tutto: finché il file rotto è ancora al suo posto, il
        primo ``_save_store`` lo sovrascrive e i job dell'utente non esistono
        più da nessuna parte. Una volta spostato, invece, ripartire da una
        lista vuota è recuperabile — ed è la scelta che facciamo.
        """
        stamp = int(time.time())
        target = self.store_path.with_suffix(self.store_path.suffix + f".corrupt-{stamp}")
        # Due corruzioni nello stesso secondo non devono sovrascriversi a
        # vicenda: ``rename`` non chiede il permesso, e la seconda cancellerebbe
        # in silenzio la prova della prima.
        suffix = 0
        while target.exists():
            suffix += 1
            target = self.store_path.with_suffix(
                self.store_path.suffix + f".corrupt-{stamp}_{suffix}"
            )
        try:
            self.store_path.rename(target)
        except OSError as e:
            logger.error("Could not set aside the broken cron store at {}: {}", self.store_path, e)
            return None
        return target

    def _load_jobs(self) -> _LoadedStore | None:
        """Load jobs from disk, recovering from ``jobs.json.bak`` when needed.

        Stessa politica di ``config.json`` (vedi ``config/loader.py``): un file
        illeggibile non deve impedire l'avvio, ma non deve nemmeno sparire in
        silenzio. Nell'ordine: il file vivo, poi il backup dell'ultimo
        salvataggio buono, poi una lista vuota.

        Il ripiego lo *riporta* soltanto: decidere se accettarlo — e quindi se
        avvisare l'utente — tocca a ``_load_store``, che è l'unico a sapere se
        esiste già uno snapshot in memoria più fresco del file su disco.

        Returns:
            ``None`` solo quando il file rotto **non** si è potuto spostare di
            lato, perché salvarci sopra distruggerebbe l'unica copia rimasta.
        """
        if not self.store_path.exists():
            return _LoadedStore([], 1, None, None)

        try:
            jobs, version = self._parse_jobs(self.store_path)
        except Exception:
            logger.exception("Failed to load cron store at {}", self.store_path)
        else:
            return _LoadedStore(jobs, version, None, None)

        backup = self._backup_path
        if backup.exists():
            try:
                jobs, version = self._parse_jobs(backup)
            except Exception:
                logger.exception("Cron store backup at {} is unusable too", backup)
            else:
                quarantined = self._quarantine_store()
                if quarantined is None:
                    return None
                # Il backup diventa il file vivo, altrimenti ogni avvio
                # rifarebbe il recupero e il primo salvataggio ripartirebbe da
                # un grezzo rotto.
                with suppress(OSError):
                    atomic_write(self.store_path, backup.read_text(encoding="utf-8"))
                logger.warning(
                    "Cron store recovered from {}; broken file kept at {}", backup, quarantined
                )
                return _LoadedStore(jobs, version, "backup", quarantined)

        quarantined = self._quarantine_store()
        if quarantined is None:
            return None
        logger.warning(
            "Cron store could not be recovered; starting with no jobs. Broken file kept at {}",
            quarantined,
        )
        return _LoadedStore([], 1, "empty", quarantined)

    @staticmethod
    def _record_recovery(kind: str, quarantined: Path) -> None:
        """Segna sul ``RuntimeContext`` che lo store cron è stato recuperato.

        Import locale come in ``config/loader.py``: ``cron.service`` non deve
        dipendere dal runtime per caricare dei job.
        """
        from jenny.runtime.context import get_runtime_context

        ctx = get_runtime_context()
        ctx.cron_recovered_from = kind
        ctx.cron_quarantine_path = quarantined

    def _merge_action(self):
        if not self._action_path.exists():
            return

        jobs_map = {j.id: j for j in self._store.jobs}
        def _update(params: dict):
            j = CronJob.from_dict(params)
            # Stessa tolleranza che ``_parse_jobs`` applica a jobs.json: il
            # journal è l'altra porta d'ingresso dallo storage, e senza questa
            # riga un ``mode`` ignoto entrerebbe verbatim nel modello, violando
            # il Literal dichiarato in ``CronPayload.mode``.
            j.payload.mode = _parse_mode(j.payload.mode)
            jobs_map[j.id] = j

        def _del(params: dict):
            # Cancellare un job che non c'e' piu' e' un no-op, non un errore: il
            # giornale si rilegge a **ogni** caricamento finche' il servizio non
            # parte, e nel frattempo ``register_system_job`` salva lo store senza
            # quel job. Con ``pop(job_id)`` secco la riga sollevava a ogni giro,
            # il ``continue`` sotto saltava ``changed = True``, e il giornale non
            # veniva mai svuotato: un traceback per ogni ``_load_store``, per
            # sempre (misurato sul telefono il 03/09/2026, primo avvio della
            # 0.10.0, con la riga «del atlas» del ritiro).
            if job_id := params.get("job_id"):
                jobs_map.pop(job_id, None)

        with self._lock:
            with open(self._action_path, "r", encoding="utf-8") as f:
                changed = False
                for line in f:
                    try:
                        line = line.strip()
                        action = json.loads(line)
                        if "action" not in action:
                            continue
                        if action["action"] == "del":
                            _del(action.get("params", {}))
                        else:
                            _update(action.get("params", {}))
                        changed = True
                    except Exception:
                        logger.exception("load action line error")
                        continue
            self._store.jobs = list(jobs_map.values())
            if self._running and changed:
                self._action_path.write_text("", encoding="utf-8")
                self._save_store()
        return

    def _load_store(self) -> CronStore | None:
        """Load jobs from disk. Reloads automatically if file was modified externally.
        - Reload every time because it needs to merge operations on the jobs object from other instances.
        - During _on_timer execution, return the existing store to prevent concurrent
          _load_store calls (e.g. from list_jobs polling) from replacing it mid-execution.
        - When the on-disk store exists but is unreadable: keep using the
          previous in-memory ``self._store`` if we already have one (so a
          transient corruption does not drop live jobs); only the very first
          load can return ``None`` to signal an unrecoverable state to the
          caller. Quel primo caricamento avviene in ``register_system_job``
          (``GatewayContainer.build``), non in ``start``: entrambi devono
          quindi controllare il ``None``.
        """
        if self._timer_active and self._store:
            return self._store
        loaded = self._load_jobs()
        if loaded is None:
            # Corrupt store on disk.  Prefer the last good in-memory snapshot
            # over wiping live jobs; ``_load_jobs`` has already moved the
            # corrupt file aside with a ``.corrupt-<ts>`` suffix.
            if self._store is not None:
                return self._store
            return None
        if loaded.recovered_from is not None and self._store is not None:
            # Corruzione arrivata *dopo* un avvio riuscito (su Android: una
            # lettura parziale, un processo ucciso a metà). Lo snapshot in
            # memoria è almeno fresco quanto il ``.bak`` e più fresco di una
            # lista vuota: si tiene quello, e il prossimo salvataggio lo
            # riscrive su disco. Nessun avviso all'utente, perché qui non ha
            # perso niente — è il caso in cui il ripiego non serve.
            return self._store
        if loaded.recovered_from is not None and loaded.quarantined is not None:
            self._record_recovery(loaded.recovered_from, loaded.quarantined)
        self._store = CronStore(version=loaded.version, jobs=loaded.jobs)
        self._merge_action()
        if self._enforce_store_agent_bindings() and self._running:
            self._save_store()

        return self._store

    def _save_store(self) -> None:
        """Save jobs to disk."""
        if not self._store:
            return

        self.store_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "version": self._store.version,
            "jobs": [
                {
                    "id": j.id,
                    "name": j.name,
                    "enabled": j.enabled,
                    "schedule": {
                        "kind": j.schedule.kind,
                        "atMs": j.schedule.at_ms,
                        "everyMs": j.schedule.every_ms,
                        "expr": j.schedule.expr,
                        "tz": j.schedule.tz,
                    },
                    "payload": {
                        "kind": j.payload.kind,
                        "mode": j.payload.mode,
                        "message": j.payload.message,
                        "sessionKey": j.payload.session_key,
                        "originChannel": j.payload.origin_channel,
                        "originChatId": j.payload.origin_chat_id,
                        "originMetadata": j.payload.origin_metadata,
                    },
                    "state": {
                        "nextRunAtMs": j.state.next_run_at_ms,
                        "lastRunAtMs": j.state.last_run_at_ms,
                        "lastStatus": j.state.last_status,
                        "lastError": j.state.last_error,
                        "consecutiveCouldNotCheck": j.state.consecutive_could_not_check,
                        "couldNotCheckSinceMs": j.state.could_not_check_since_ms,
                        "couldNotCheckEscalated": j.state.could_not_check_escalated,
                        "taskChecks": {
                            task_id: {
                                "consecutiveCouldNotCheck": e.consecutive_could_not_check,
                                "sinceMs": e.since_ms,
                                "escalated": e.escalated,
                                "escalatedAtMs": e.escalated_at_ms,
                                "label": e.label,
                                "pendingSinceMs": e.pending_since_ms,
                            }
                            for task_id, e in j.state.task_checks.items()
                        },
                        "runHistory": [
                            {
                                "runAtMs": r.run_at_ms,
                                "status": r.status,
                                "durationMs": r.duration_ms,
                                "error": r.error,
                            }
                            for r in j.state.run_history
                        ],
                    },
                    "createdAtMs": j.created_at_ms,
                    "updatedAtMs": j.updated_at_ms,
                    "deleteAfterRun": j.delete_after_run,
                }
                for j in self._store.jobs
            ]
        }

        self._rotate_backup()
        atomic_write(self.store_path, json.dumps(data, indent=2, ensure_ascii=False))

    @staticmethod
    def _safe_run_record_name(run_id: str) -> str:
        return "".join(c if c.isalnum() or c in "._-" else "_" for c in run_id)

    def _run_record_paths(self) -> list[Path]:
        """I file in ``runs/``, o lista vuota se la directory non c'e'.

        Non solleva: la potatura e' igiene, e un problema nell'igiene non deve
        poter impedire al cron di partire.
        """
        try:
            return [p for p in self._run_records_dir.iterdir() if p.is_file()]
        except OSError:
            return []

    @staticmethod
    def _run_record_sort_key(path: Path) -> int | None:
        """Il timestamp dentro il nome, o ``None`` se il nome non ha quella forma.

        Il nome e' ``<job_id>_<ms>_<rand>.json`` (v. ``bound_runner``). Si legge
        **dal nome e non dall'mtime**: e' deterministico, sopravvive a una copia
        del workspace, e si puo' provare senza toccare l'orologio — che in questo
        repo e' una regola e non un gusto.

        ``None`` vuol dire «non lo capisco», e quel che non si capisce non si
        cancella (v. :meth:`_prune_run_records`).
        """
        parts = path.stem.split("_")
        if len(parts) < 2:
            return None
        try:
            return int(parts[1])
        except ValueError:
            return None

    def _prune_run_records(self) -> int:
        """Tiene i :data:`_RUN_RECORDS_KEEP` record piu' recenti. Ritorna quanti ne toglie.

        **I file che non hanno la forma attesa restano.** Sono l'unico caso in
        cui questa funzione non sa che sta guardando, e cancellare per non aver
        capito e' il modo di trasformare un'igiene in una perdita di dati.
        """
        dated = []
        for path in self._run_record_paths():
            stamp = self._run_record_sort_key(path)
            if stamp is not None:
                dated.append((stamp, path))
        if len(dated) <= _RUN_RECORDS_KEEP:
            return 0
        dated.sort(key=lambda item: item[0], reverse=True)
        removed = 0
        for _, path in dated[_RUN_RECORDS_KEEP:]:
            try:
                path.unlink()
                removed += 1
            except OSError:
                logger.opt(exception=True).warning(
                    "Cron: record di esecuzione non rimosso: {}", path.name
                )
        if removed:
            logger.info("Cron: potati {} record di esecuzione", removed)
        return removed

    def _remove_run_records(self, job_id: str) -> int:
        """Toglie i record del job *job_id*. Ritorna quanti ne toglie.

        Il confronto e' sul **segmento** dell'id e non su una sottostringa:
        ``<job_id>_`` col separatore, cosi' un id che sia prefisso di un altro
        non si porta via i record del vicino.
        """
        prefix = f"{job_id}_"
        removed = 0
        for path in self._run_record_paths():
            if not path.name.startswith(prefix):
                continue
            try:
                path.unlink()
                removed += 1
            except OSError:
                logger.opt(exception=True).warning(
                    "Cron: record di {} non rimosso: {}", job_id, path.name
                )
        return removed

    def write_run_record(self, run_id: str, record: dict[str, Any]) -> None:
        """Write an internal audit record for one cron execution."""
        name = self._safe_run_record_name(run_id)
        if not name:
            name = str(uuid.uuid4())
        path = self._run_records_dir / f"{name}.json"
        payload = {
            **record,
            "run_id": run_id,
            "updated_at_ms": _now_ms(),
        }
        atomic_write(path, json.dumps(payload, indent=2, ensure_ascii=False))

    async def start(self) -> None:
        """Start the cron service."""
        self._running = True
        # Prima di qualunque cosa possa armare un timer: da qui in poi una
        # sveglia dell'OS che scatta trova un destinatario. L'aggancio va fatto
        # sul loop del gateway, e ``start`` è l'unico punto di questa classe che
        # gira sicuramente là dentro (vedi ``power.bind_wake_loop``).
        self._wake_event = power.bind_wake_loop() if power.alarm_driven_cron_enabled() else None
        loaded = self._load_store()
        if loaded is None:
            self._running = False
            raise self._corrupt_store_error()
        self._recompute_next_runs()
        self._save_store()
        self._arm_timer()
        # Una volta per avvio, che su Android vuol dire spesso. Costa una
        # ``iterdir`` di qualche centinaio di voci e non puo' fallire il boot.
        with suppress(Exception):
            self._prune_run_records()
        logger.info("Cron service started with {} jobs", len(self._store.jobs if self._store else []))

    def _corrupt_store_error(self) -> RuntimeError:
        """Store illeggibile **e** non spostabile: l'unico caso che rifiuta.

        Da 0.6.6 un file rotto non impedisce più l'avvio: ``_load_jobs`` prova
        il ``.bak``, altrimenti mette il file da parte come ``.corrupt-<ts>`` e
        riparte senza job, registrandolo perché la WebUI lo dica. Quella scelta
        regge su una condizione precisa — che la copia forense esista davvero.

        Se il rename fallisce, il file rotto è ancora al suo posto e il primo
        ``_save_store`` ci scriverebbe sopra: i job dell'utente non esisterebbero
        più da nessuna parte. Lì, e solo lì, non partire è la risposta giusta.

        Vive qui, e non inline in ``start``, perché il primo caricamento non
        avviene in ``start``: ``GatewayContainer.build`` registra i job di
        sistema prima, quindi è ``register_system_job`` a vedere per primo il
        ``None``.
        """
        return RuntimeError(
            f"cron store at {self.store_path} is corrupt and could not be set aside; "
            "refusing to start, because saving would overwrite the only copy left. "
            "Move the file out of the way manually and start again."
        )

    def stop(self) -> None:
        """Stop the cron service."""
        self._running = False
        if self._timer_task:
            self._timer_task.cancel()
            self._timer_task = None
        # In lockstep con il timer: la sveglia vive nell'AlarmManager di
        # sistema, non nel nostro processo, quindi sopravvive allo spegnimento
        # del servizio e — peggio — farebbe ripartire il gateway per una
        # scadenza che nessuno sta più ascoltando.
        self._set_wake_alarm(None)
        self._wake_event = None

    def _recompute_next_runs(self) -> None:
        """Recompute next run times for all enabled jobs."""
        if not self._store:
            return
        now = _now_ms()
        for job in self._store.jobs:
            if self._enforce_agent_binding(job):
                continue
            if not job.enabled:
                continue
            computed = _compute_next_run(job.schedule, now)
            # Solo ``every`` è relativo, quindi solo ``every`` poteva perdersi
            # qui: ricalcolarlo a ogni avvio significava "N ore di uptime
            # ininterrotto", non "ogni N ore". Su Android, dove il processo
            # viene ucciso e rilanciato, una scadenza lunga (sei o dodici ore) non
            # arrivava mai. Conservare quella salvata fa sì che il conto non
            # arretri mai e che una scadenza mancata a app spenta venga
            # recuperata al primo tick.
            # ``cron`` resta ricalcolato: è già ancorato all'orologio, non
            # deriva dal momento dell'avvio (l'occorrenza mancata a app spenta
            # si perde, ma la successiva arriva da sé).
            #
            # ``at`` invece si recupera — vedi ``_next_run_with_catch_up``. È il
            # caso "il telefono era spento alle 15", cioè proprio quello per cui
            # un promemoria esiste. Il WARNING sta qui e non nell'helper: questo
            # è l'unico punto in cui il ritardo racconta qualcosa (l'app è stata
            # giù), mentre negli altri chiamanti sarebbe solo rumore.
            if computed is None and job.schedule.kind == "at" and job.schedule.at_ms:
                logger.warning(
                    "Cron: one-shot job '{}' ({}) is {}s overdue, running it late",
                    job.name, job.id, (now - job.schedule.at_ms) // 1000,
                )
                job.state.next_run_at_ms = job.schedule.at_ms
                continue
            if job.schedule.kind == "every" and job.state.next_run_at_ms is not None:
                # Il tetto copre un orologio che è saltato in avanti e un
                # intervallo accorciato a mano: senza, una scadenza assurda
                # salvata una volta resterebbe tale per sempre.
                job.state.next_run_at_ms = (
                    min(job.state.next_run_at_ms, computed)
                    if computed is not None
                    else job.state.next_run_at_ms
                )
                continue
            job.state.next_run_at_ms = computed

    def _get_next_wake_ms(self) -> int | None:
        """Get the earliest next run time across all jobs."""
        if not self._store:
            return None
        times = [j.state.next_run_at_ms for j in self._store.jobs
                 if j.enabled and j.state.next_run_at_ms]
        return min(times) if times else None

    def _arm_timer(self) -> None:
        """Schedule the next timer tick.

        No-op while a due job is in-flight (``self._timer_active``): that
        execution is running *inside* ``self._timer_task`` itself (mid-await
        in ``_on_timer``'s ``due_jobs`` loop -> ``_execute_job``). Cancelling
        and replacing the task here -- as would happen from an unrelated
        ``add_job``/``remove_job`` call made
        while that job's agent turn is still running -- would abort the
        in-flight job mid-execution, lose its result, and leave its
        ``next_run_at_ms`` stale, causing a silent double-fire on the next
        tick. ``_on_timer`` already calls ``_arm_timer`` again right after its
        ``due_jobs`` loop completes; by then ``self._store`` already reflects
        whatever change triggered this call, so deferring to that later call
        is safe and loses no scheduling update.

        Il no-op vale **anche per la sveglia di sistema**, e per lo stesso
        motivo: disarmarla qui significherebbe togliere al job in corso la
        sveglia che copre la sua prossima scadenza, per poi riprogrammarla
        subito dopo dal ``_arm_timer`` finale di ``_on_timer``. Nel mezzo,
        però, il device potrebbe sospendersi — e resterebbe sospeso.
        """
        if self._timer_active:
            return

        if self._timer_task:
            self._timer_task.cancel()

        if not self._running:
            self._set_wake_alarm(None)
            return

        next_wake = self._get_next_wake_ms()
        if next_wake is None:
            delay_ms = self.max_sleep_ms
        else:
            delay_ms = min(self.max_sleep_ms, max(0, next_wake - _now_ms()))
        delay_s = delay_ms / 1000

        # La sveglia punta alla scadenza VERA, non al risveglio accorciato da
        # ``max_sleep_ms``: quel tetto esiste per rileggere lo store e
        # raccogliere le modifiche fatte da altre istanze mentre il device è
        # sveglio, non per svegliare il telefono ogni cinque minuti a vuoto —
        # che è esattamente il comportamento che i gestori energetici OEM
        # marcano come "l'app sveglia il sistema troppo spesso". Senza scadenze
        # in agenda (``None``) non si arma proprio niente.
        self._set_wake_alarm(next_wake)

        # Catturato adesso: se il servizio viene fermato e riavviato mentre
        # questo tick dorme, l'evento nuovo appartiene a un altro giro e questo
        # task è già stato cancellato.
        wake_event = self._wake_event

        async def tick():
            if wake_event is None:
                # Nessuna sveglia in gioco: percorso storico, invariato.
                await asyncio.sleep(delay_s)
            else:
                await self._sleep_or_wake(delay_s, wake_event)
            if self._running:
                await self._on_timer()

        self._timer_task = asyncio.create_task(tick())

    async def _sleep_or_wake(self, delay_s: float, wake_event: asyncio.Event) -> None:
        """Attende il timer asyncio **o** un tick di sveglia: il primo che arriva.

        Perché non basta ``asyncio.sleep``: dorme su un orologio monotono che
        **non avanza mentre il SoC è sospeso**. A schermo spento un job da 30
        minuti scattava dopo 83 (misurato): il timer non era in ritardo, era
        semplicemente fermo. La sveglia RTC dell'OS arriva anche da sospeso ed è
        l'unica che rispetti l'orario vero. Il timer resta perché copre l'altro
        caso — modifiche allo store fatte da fuori a device sveglio — che la
        sveglia non vede.

        Che scattino entrambi non è un problema: ``asyncio.wait`` ritorna e si
        chiama ``_on_timer`` **una volta sola**, e ``_on_timer`` rifiltra
        comunque i job per ``next_run_at_ms``, che ``_execute_job`` ha già
        spostato avanti.
        """
        waiters = [
            asyncio.create_task(asyncio.sleep(delay_s)),
            asyncio.create_task(wake_event.wait()),
        ]
        try:
            await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        finally:
            # ``asyncio.wait`` non cancella i task rimasti — nemmeno quando è il
            # chiamante a essere cancellato, che qui succede a ogni
            # riprogrammazione (``_arm_timer`` cancella ``self._timer_task``).
            # Senza questo giro resterebbe appeso un task per ogni tick.
            for waiter in waiters:
                waiter.cancel()
        if wake_event.is_set():
            # Azzerato PRIMA di eseguire i job, non dopo: un tick che arrivasse
            # mentre ``_on_timer`` gira deve lasciare l'evento acceso e
            # provocare un altro giro (a vuoto, ma non perso). Azzerarlo dopo lo
            # mangerebbe.
            wake_event.clear()
            # La sveglia dell'OS è one-shot: quella appena scattata non esiste
            # più. Senza questo azzeramento un riarmo alla stessa scadenza
            # verrebbe scambiato per "già armata" e non riprogrammerebbe nulla.
            self._alarm_armed_ms = None

    def _set_wake_alarm(self, at_ms: int | None) -> None:
        """Dichiara quando il sistema deve svegliarci (``None`` = mai).

        Sincrona perché i chiamanti lo sono (``_arm_timer``, ``stop``), mentre il
        bridge verso Kotlin è async: qui si registra solo l'obiettivo e si
        delega a un task l'applicazione. L'obiettivo è quindi sempre aggiornato
        all'istante, anche quando l'effetto arriva un giro di loop più tardi.
        """
        if not power.alarm_driven_cron_enabled():
            return
        self._alarm_target_ms = at_ms
        try:
            task = asyncio.create_task(self._apply_wake_alarm())
        except RuntimeError:
            # Nessun loop in esecuzione (spegnimento fuori dal gateway): non
            # c'è nulla da applicare e non è un errore.
            return
        # Riferimento forte finché il task non finisce: asyncio tiene solo un
        # riferimento debole, e un task raccolto dal GC a metà lascerebbe la
        # sveglia non programmata senza dire niente.
        self._alarm_tasks.add(task)
        task.add_done_callback(self._alarm_tasks.discard)

    async def _apply_wake_alarm(self) -> None:
        """Allinea la sveglia di sistema all'ultimo obiettivo dichiarato.

        L'obiettivo si rilegge **dentro** il lock. Due ``_arm_timer`` ravvicinati
        creano due task, e asyncio non garantisce in che ordine finiranno: senza
        la rilettura, a decidere quale scadenza resta programmata sarebbe
        l'ordine di risveglio, con la concreta possibilità che vinca la più
        vecchia. Rileggendo, vince sempre l'ultimo che ha scritto e il task
        rimasto indietro trova l'obiettivo già applicato e non fa nulla.
        """
        async with self._alarm_lock:
            target = self._alarm_target_ms
            if target == self._alarm_armed_ms:
                return
            if target is None:
                await power.cancel_wake(power.WAKE_REQUEST_CODE_CRON)
            else:
                await power.schedule_wake(target, power.WAKE_REQUEST_CODE_CRON)
            self._alarm_armed_ms = target

    async def _on_timer(self) -> None:
        """Handle timer tick - run due jobs."""
        self._load_store()
        # If a hot reload found a corrupt store on disk, ``self._store`` may
        # still hold the previous, known-good in-memory snapshot.  Keep using
        # it rather than crashing the timer or wiping live jobs.
        if not self._store:
            self._arm_timer()
            return

        self._timer_active = True
        try:
            now = _now_ms()
            due_jobs = [
                j for j in self._store.jobs
                if j.enabled and j.state.next_run_at_ms and now >= j.state.next_run_at_ms
            ]

            for job in due_jobs:
                await self._execute_job(job)

            self._save_store()
        finally:
            self._timer_active = False
        self._arm_timer()

    @staticmethod
    def _reset_could_not_check(state: CronJobState) -> None:
        """Chiude la sequenza di controlli mancati: il controllo è avvenuto.

        Chiamata solo da ``ok`` e ``silenced``, che sono le due prove che il
        controllo è stato eseguito. ``error`` e ``skipped`` la lasciano stare
        apposta: non dimostrano né che il monitor sia sano né che sia rotto, e
        azzerare su un errore trasformerebbe un guasto intermittente in un
        guasto invisibile — esattamente ciò che questo stato esiste per evitare.
        """
        state.consecutive_could_not_check = 0
        state.could_not_check_since_ms = None
        state.could_not_check_escalated = False

    async def _execute_job(self, job: CronJob) -> None:
        """Execute a single job."""
        start_ms = _now_ms()
        logger.info("Cron: executing job '{}' ({})", job.name, job.id)

        try:
            if self.on_job:
                await self.on_job(job)

            job.state.last_status = "ok"
            job.state.last_error = None
            self._reset_could_not_check(job.state)
            logger.info("Cron: job '{}' completed", job.name)

        except CronMonitorCouldNotCheckError as e:
            # PRIMA di ``CronJobSilencedError``, di cui è sottoclasse: qui si
            # separa "ho guardato e non c'era niente" da "non sono riuscito a
            # guardare", che senza questo ramo producevano lo stesso identico
            # nulla. Non è un errore del job — il turno è andato a termine —
            # quindi il job resta armato; è una sequenza che va contata.
            job.state.last_status = "could_not_check"
            job.state.last_error = e.reason
            job.state.consecutive_could_not_check += 1
            if job.state.could_not_check_since_ms is None:
                job.state.could_not_check_since_ms = start_ms
            if e.escalated:
                job.state.could_not_check_escalated = True
            logger.warning(
                "Cron: job '{}' could not run its check ({} in a row): {}",
                job.name,
                job.state.consecutive_could_not_check,
                e.reason or "no reason given",
            )
            # Ultimo, e dopo che lo stato è completo: è l'unico punto del sistema
            # che vede sia i contatori del job aggiornati sia la mappa per-task
            # che il dispatcher ha appena riscritto, ed è quindi l'unico da cui
            # una sola chiamata copre monitor e heartbeat insieme.
            #
            # Il presupposto di ogni altro avviso è che il modello faccia la sua
            # parte; questa riga è ciò che accade quando non la fa. V.
            # ``jenny/cron/silence_watchdog.py``.
            alert_silently_broken_checks(job.name, job.state, now_ms=start_ms)
        except CronJobSilencedError:
            # Esito riuscito, non mancato: il job monitor ha girato fino in
            # fondo e ha deciso che non c'era niente da dire. Nessun
            # ``last_error`` — un errore residuo del giro precedente qui
            # resterebbe appeso e farebbe sembrare guasto un job sano.
            job.state.last_status = "silenced"
            job.state.last_error = None
            self._reset_could_not_check(job.state)
            logger.info("Cron: job '{}' completed silently", job.name)
        except CronJobSkippedError as e:
            job.state.last_status = "skipped"
            job.state.last_error = str(e) or None
            logger.warning("Cron: job '{}' skipped: {}", job.name, job.state.last_error or "")
        except asyncio.CancelledError as e:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise
            job.state.last_status = "error"
            job.state.last_error = str(e) or e.__class__.__name__
            logger.exception("Cron: job '{}' was cancelled", job.name)
        except Exception as e:
            job.state.last_status = "error"
            job.state.last_error = str(e)
            logger.exception("Cron: job '{}' failed", job.name)

        end_ms = _now_ms()
        job.state.last_run_at_ms = start_ms
        job.updated_at_ms = end_ms

        job.state.run_history.append(CronRunRecord(
            run_at_ms=start_ms,
            status=job.state.last_status,
            duration_ms=end_ms - start_ms,
            error=job.state.last_error,
        ))
        job.state.run_history = job.state.run_history[-self._MAX_RUN_HISTORY:]

        # Handle one-shot jobs
        if job.schedule.kind == "at":
            if job.delete_after_run:
                self._store.jobs = [j for j in self._store.jobs if j.id != job.id]
            else:
                job.enabled = False
                job.state.next_run_at_ms = None
        else:
            # Compute next run
            job.state.next_run_at_ms = _compute_next_run(job.schedule, _now_ms())

    def _append_action(self, action: Literal["add", "del"], params: dict):
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with open(self._action_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"action": action, "params": params}, ensure_ascii=False) + "\n")


    # ========== Public API ==========

    def list_jobs(self, include_disabled: bool = False) -> list[CronJob]:
        """List all jobs."""
        store = self._load_store()
        if store is None:
            return []
        jobs = store.jobs if include_disabled else [j for j in store.jobs if j.enabled]
        return sorted(jobs, key=lambda j: j.state.next_run_at_ms or float('inf'))

    def add_job(
        self,
        name: str,
        schedule: CronSchedule,
        message: str,
        delete_after_run: bool = False,
        session_key: str | None = None,
        origin_channel: str | None = None,
        origin_chat_id: str | None = None,
        origin_metadata: dict | None = None,
        *,
        mode: Literal["reminder", "monitor"] = "reminder",
    ) -> CronJob:
        """Add a new job.

        La firma dichiara i due literal, ma a runtime nessuno li impone: il
        controllo vero è ``_validate_mode_for_add``, che **solleva** invece di
        ripiegare, perché qui il valore sporco viene da chi chiama e non da un
        file scritto da un'altra versione.

        Raises:
            ValueError: schedule non valido, o ``mode`` non riconosciuto.
        """
        _validate_schedule_for_add(schedule)
        mode = _validate_mode_for_add(mode)
        now = _now_ms()

        job = CronJob(
            id=str(uuid.uuid4())[:8],
            name=name,
            enabled=True,
            schedule=schedule,
            payload=CronPayload(
                kind="agent_turn",
                mode=mode,
                message=message,
                session_key=session_key,
                origin_channel=origin_channel,
                origin_chat_id=origin_chat_id,
                origin_metadata=origin_metadata or {},
            ),
            state=CronJobState(next_run_at_ms=_next_run_with_catch_up(schedule, now)),
            created_at_ms=now,
            updated_at_ms=now,
            delete_after_run=delete_after_run,
        )
        self._enforce_agent_binding(job)
        if self._running:
            store = self._load_store()
            store.jobs.append(job)
            self._save_store()
            self._arm_timer()
        else:
            self._append_action("add", asdict(job))

        logger.info("Cron: added job '{}' ({})", name, job.id)
        return job

    # -- i job che seguono una conversazione di progetto -------------------------

    _DELETED_NOTEBOOK_REASON = "the notebook this job belonged to was deleted"

    def retarget_session(self, old_key: str, new_key: str) -> int:
        """I job legati a *old_key* passano a *new_key*. Torna quanti.

        Un job creato dentro un quaderno salva la sua chiave (``session_key``) e
        la chat d'origine (``origin_chat_id``), entrambe ``project:<nome>``. Il
        rinomino del quaderno sposta cartella e chat ma non il job, che al primo
        scatto ricreerebbe sessione e trascrizione sotto il nome vecchio, senza
        cartella. Lo chiama ``project.rename`` a rinomino riuscito.
        """

        def _sposta(job: CronJob) -> None:
            if job.payload.session_key == old_key:
                job.payload.session_key = new_key
            if job.payload.origin_chat_id == old_key:
                job.payload.origin_chat_id = new_key

        return self._rewrite_session_jobs(old_key, _sposta, "retargeted to " + new_key)

    def disable_session_jobs(self, session_key: str) -> int:
        """Spegne i job legati a *session_key*, dicendo perche'. Torna quanti.

        Per un quaderno cancellato: al primo scatto il suo job ricreerebbe una
        sessione sotto un nome ormai libero, che il prossimo quaderno con quel
        nome si riprenderebbe. Spento e non cancellato (deciso il 25/09/2026): il
        job resta nell'elenco con il motivo, e il testo che l'utente gli aveva
        affidato non sparisce senza che l'abbia chiesto.
        """

        def _spegni(job: CronJob) -> None:
            job.enabled = False
            job.state.next_run_at_ms = None
            job.state.last_status = "error"
            job.state.last_error = self._DELETED_NOTEBOOK_REASON

        return self._rewrite_session_jobs(session_key, _spegni, "disabled")

    def _rewrite_session_jobs(
        self, session_key: str, change: Callable[[CronJob], None], what: str
    ) -> int:
        """Applica *change* ai job di agente legati a *session_key*, e li salva.

        Salva come ``add_job``: a servizio avviato ``jobs.json``, a servizio fermo
        il giornale, dove ogni riga che non e' ``del`` e' un upsert del job intero.
        """
        store = self._load_store()
        if store is None:
            raise self._corrupt_store_error()
        toccati = [
            job for job in store.jobs
            if job.payload.kind == "agent_turn"
            and session_key in (job.payload.session_key, job.payload.origin_chat_id)
        ]
        if not toccati:
            return 0
        now = _now_ms()
        for job in toccati:
            change(job)
            job.updated_at_ms = max(job.updated_at_ms, now)
        if self._running:
            self._save_store()
            self._arm_timer()
        else:
            for job in toccati:
                self._append_action("add", asdict(job))
        logger.info(
            "Cron: {} job(s) of {} {}: {}",
            len(toccati), session_key, what, ", ".join(j.id for j in toccati),
        )
        return len(toccati)

    def register_system_job(self, job: CronJob) -> CronJob:
        """Register an internal system job (idempotent on restart)."""
        store = self._load_store()
        if store is None:
            raise self._corrupt_store_error()
        now = _now_ms()
        previous = next((j for j in store.jobs if j.id == job.id), None)
        # Il conto alla rovescia sopravvive al riavvio. Ricalcolarlo qui a ogni
        # avvio rendeva "ogni N ore" un sinonimo di "dopo N ore di uptime
        # ininterrotto": su un telefono, dove il servizio viene ucciso e
        # rilanciato, un job lungo dodici ore poteva non scattare mai,
        # perché ogni ripartenza spostava la scadenza di altre 12 ore.
        # Conservando lo stato la scadenza non arretra mai, quindi il job
        # arriva a scattare anche a colpi di sessioni brevi; se è gia passata
        # mentre l'app era spenta, ci pensa il primo tick — è il recupero che
        # "ogni N ore" promette.
        # Si riparte da zero solo se la pianificazione è cambiata: un nuovo
        # intervallo deve valere subito, non dopo la scadenza del vecchio.
        if (
            previous is not None
            and previous.schedule == job.schedule
            and previous.state.next_run_at_ms is not None
        ):
            job.state = previous.state
            job.created_at_ms = previous.created_at_ms
        else:
            # No-op per i job di sistema, che sono tutti ``every``: sta qui solo
            # perché OGNI punto che programma usi la stessa funzione, e nessuno
            # reintroduca l'orfano scegliendo quella sbagliata.
            job.state = CronJobState(next_run_at_ms=_next_run_with_catch_up(job.schedule, now))
            job.created_at_ms = now
        job.updated_at_ms = now
        store.jobs = [j for j in store.jobs if j.id != job.id]
        store.jobs.append(job)
        self._save_store()
        self._arm_timer()
        logger.info("Cron: registered system job '{}' ({})", job.name, job.id)
        return job

    def remove_job(self, job_id: str) -> Literal["removed", "protected", "not_found"]:
        """Remove a job by ID, unless it is a protected system job."""
        store = self._load_store()
        job = next((j for j in store.jobs if j.id == job_id), None)
        if job is None:
            return "not_found"
        if job.payload.kind == "system_event":
            logger.info("Cron: refused to remove protected system job {}", job_id)
            return "protected"

        before = len(store.jobs)
        store.jobs = [j for j in store.jobs if j.id != job_id]
        removed = len(store.jobs) < before

        if removed:
            if self._running:
                self._save_store()
                self._arm_timer()
            else:
                self._append_action("del", {"job_id": job_id})
            # I record di esecuzione sono indicizzati per id del job, e nessuno
            # li potava: restavano dietro al job per sempre. Non e' la
            # reincarnazione che colpiva i progetti (gli id dei job sono opachi,
            # quindi un job nuovo non eredita mai), e' una perdita lenta — ma e'
            # la stessa mancanza, e si chiude qui perche' questo e' il punto in
            # cui quel job smette di esistere.
            dropped = self._remove_run_records(job_id)
            logger.info(
                "Cron: removed job {} ({} run records)", job_id, dropped
            )
            return "removed"

        return "not_found"

    def retire_system_job(self, job_id: str) -> bool:
        """Toglie un job di sistema che **questa versione non sa piu' eseguire**.

        E' l'unica strada che passa sopra la protezione di :meth:`remove_job`, e
        la protezione resta giusta per tutto il resto: un ``system_event`` non
        e' dell'utente, e l'utente non deve poterlo cancellare da un tool. Ma un
        lavoratore periodico ritirato dal codice lascia il suo job scritto nello
        store — e' cosi' che ``register_system_job`` lo rende idempotente al
        riavvio — e senza il suo ramo in ``_dispatch`` quel job cadrebbe ogni
        volta su «unbound agent job», un warning e una ``CronJobSkippedError`` a
        ogni scadenza, per sempre. Il chiamante e' uno solo,
        ``GatewayContainer.build``, su un elenco chiuso di id; qui si ritira
        **per id**, mai per nome, cosi' un promemoria dell'utente battezzato come
        il vecchio lavoratore non c'entra.

        Ritorna ``True`` se c'era qualcosa da togliere. I record di esecuzione
        vanno via con lui, per la ragione scritta in :meth:`remove_job`.
        """
        store = self._load_store()
        if store is None:
            return False
        before = len(store.jobs)
        store.jobs = [j for j in store.jobs if j.id != job_id]
        if len(store.jobs) == before:
            return False
        # Salva direttamente, come ``register_system_job`` che gira nella stessa
        # fase (``GatewayContainer.build``, prima di ``start``): passare dal
        # giornale delle azioni lascerebbe una riga «del» che il primo
        # ``_save_store`` della registrazione rende gia' vecchia.
        self._save_store()
        if self._running:
            self._arm_timer()
        dropped = self._remove_run_records(job_id)
        logger.info(
            "Cron: retired system job {} — this version no longer runs it ({} run records)",
            job_id, dropped,
        )
        return True

    async def run_job(self, job_id: str, force: bool = False) -> bool:
        """Manually run a job without disturbing the service's running state."""
        was_running = self._running
        self._running = True
        try:
            store = self._load_store()
            for job in store.jobs:
                if job.id == job_id:
                    if self._is_unbound_agent_job(job):
                        self._enforce_agent_binding(job)
                        self._save_store()
                        return False
                    if not force and not job.enabled:
                        return False
                    await self._execute_job(job)
                    self._save_store()
                    return True
            return False
        finally:
            self._running = was_running
            if was_running:
                self._arm_timer()

    def get_job(self, job_id: str) -> CronJob | None:
        """Get a job by ID."""
        store = self._load_store()
        if store is None:
            return None
        return next((j for j in store.jobs if j.id == job_id), None)

    def persist_job_state(self) -> None:
        """Scrive su disco lo stato dei job mutato da un chiamante esterno.

        Serve al solo caso in cui uno stato cambia **fuori** da un run: il
        verdetto di un controllo delegato arriva col turno d'annuncio del
        subagent, che non passa da ``_execute_job`` e quindi non incontra il
        salvataggio di fine ciclo. Il chiamante muta il job restituito da
        ``get_job`` — che è l'oggetto vivo dello store, non una copia — e poi
        chiama questo. Nessun ``_load_store`` prima: rileggere qui butterebbe
        via proprio la mutazione appena fatta.
        """
        if self._store is not None:
            self._save_store()

    def status(self) -> dict:
        """Get service status."""
        store = self._load_store()
        # I lettori **degradano**, non abortiscono. Una query di stato è la
        # prima cosa che ``GatewayContainer.build`` chiama, prima ancora di
        # registrare i job: se decidesse lei di sollevare, il rifiuto
        # arriverebbe da "quanti job ci sono" invece che da chi quel rifiuto
        # lo sa spiegare. La decisione di non partire resta di
        # ``register_system_job`` e ``start``, che dicono anche cosa fare col
        # file ``.corrupt-<ts>``.
        return {
            "enabled": self._running,
            "jobs": len(store.jobs) if store is not None else 0,
            "next_wake_at_ms": self._get_next_wake_ms(),
        }
