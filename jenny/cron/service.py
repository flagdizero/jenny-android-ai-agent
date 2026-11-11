"""Cron service for scheduling agent tasks."""

import asyncio
import uuid
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Coroutine, Literal

from loguru import logger

from jenny.board.model import CONDITION_OPS
from jenny.cron import store as cron_store
from jenny.cron import zones as zone_rules
from jenny.cron.session_turns import is_bound_cron_job
from jenny.cron.silence_watchdog import alert_silently_broken_checks
from jenny.cron.types import (
    CronJob,
    CronJobSilencedError,
    CronJobState,
    CronMonitorCouldNotCheckError,
    CronMonitorDelegatedError,
    CronPayload,
    CronRunRecord,
    CronSchedule,
    CronStore,
    job_display_name,
)

# Modulo, non simboli: i test sostituiscono ``power.schedule_wake`` &co. per
# osservare le sveglie senza un device, e con ``from … import`` la sostituzione
# non arriverebbe qui.
from jenny.runtime import power
from jenny.session.keys import normalize_user_session_key
from jenny.utils.clock import now_ms as _now_ms

if TYPE_CHECKING:
    from jenny.cron.monitor_followup import MonitorFollowup

class CronJobSkippedError(Exception):
    """Raised by cron callbacks when a job was intentionally skipped."""


_CRON_MODES: tuple[str, ...] = ("reminder", "monitor")



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


def _load_board_link(raw: Any) -> dict[str, str] | None:
    """Il legame di un cron del quaderno, o ``None``: tre stringhe, o niente."""
    if not isinstance(raw, dict):
        return None
    link = {k: raw.get(k) for k in ("notebook", "card", "section")}
    if not all(isinstance(v, str) and v for v in link.values()):
        return None
    return link  # type: ignore[return-value]


def _load_precision(raw: Any) -> Any:
    """``exact``/``day``/``month``, o ``None`` per qualunque altro valore."""
    return raw if raw in ("exact", "day", "month") else None


def _load_condition(raw: Any) -> dict[str, Any] | None:
    """Il filtro di un ricorrente, o ``None``: ``{app, params, field, op, value}``."""
    if not isinstance(raw, dict):
        return None
    app, field, op = raw.get("app"), raw.get("field"), raw.get("op")
    if not (isinstance(app, str) and app and isinstance(field, str) and field
            and op in CONDITION_OPS and "value" in raw):
        return None
    params = raw.get("params")
    return {"app": app, "params": params if isinstance(params, dict) else {},
            "field": field, "op": op, "value": raw.get("value")}


def _load_number(raw: Any) -> float | None:
    """Un numero finito dal disco, o ``None`` (un bool non è un numero)."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    value = float(raw)
    return value if value == value and abs(value) != float("inf") else None


def _parse_schedule(raw: dict[str, Any]) -> CronSchedule:
    """La schedulazione di un job dello store. Solleva se non è usabile.

    Una ``zone`` senza centro o raggio solleva: un Jenny più vecchio, che non
    conosce le zone, riscrive lo store perdendo i campi che non sa leggere, e
    una zona senza cerchio non si può né guardare né raccontare. Il job resta
    nel database, non letto, con un ERROR nel log (``_load_store``).
    """
    kind = raw["kind"]
    schedule = CronSchedule(
        kind=kind,
        at_ms=raw.get("atMs"),
        every_ms=raw.get("everyMs"),
        expr=raw.get("expr"),
        tz=raw.get("tz"),
        precision=_load_precision(raw.get("precision")),
    )
    # La scadenza vale per una zona e per un ricorrente («fino a»).
    until = raw.get("untilMs")
    schedule.until_ms = until if isinstance(until, int) and not isinstance(until, bool) else None
    if kind != "zone":
        return schedule
    lat, lng, radius = (_load_number(raw.get(k)) for k in ("lat", "lng", "radiusM"))
    if lat is None or lng is None or radius is None:
        raise ValueError("zone schedule without its circle")
    name = raw.get("zoneName")
    schedule.lat, schedule.lng, schedule.radius_m = lat, lng, radius
    schedule.zone_name = name if isinstance(name, str) and name.strip() else None
    return schedule


def _validate_label_for_add(
    schedule: CronSchedule, *, why: Any, condition: Any,
) -> tuple[str | None, dict[str, Any] | None]:
    """Il perché e la condizione che arrivano da un chiamante vivo: solleva se storti."""
    if why is not None and not (isinstance(why, str) and why.strip()):
        raise ValueError("why must be the path of a wiki page")
    cond = _load_condition(condition)
    if condition is not None and cond is None:
        raise ValueError(f"invalid condition: {condition!r}")
    if cond is not None and schedule.kind == "zone":
        # La zona scatta all'arrivo: è già lei la condizione.
        raise ValueError("a condition cannot go on a zone")
    if cond is not None and schedule.kind == "at":
        # Un singolo scatta una volta: con un filtro falso in quel momento non
        # direbbe mai niente. La condizione si guarda a una cadenza.
        raise ValueError("a condition needs a recurring schedule (every or cron), not at")
    if schedule.precision is not None and schedule.kind != "at":
        raise ValueError("precision is only for a one-time date")
    return (why.strip() if isinstance(why, str) else None), cond


def _load_inside(raw: Any) -> bool | None:
    """``zoneInside``: vero, falso, o ``None`` per «non si sa» (e per qualunque altro valore)."""
    return raw if isinstance(raw, bool) else None


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


def _schedule_tzinfo(schedule: CronSchedule):
    """Il fuso in cui leggere l'espressione: quello del job, o l'ora locale.

    Una sola risoluzione per chi calcola la prossima esecuzione e per chi valida
    all'aggiunta: due copie potevano divergere, e un job validato in un fuso
    sarebbe poi scattato in un altro. ``safe_zoneinfo`` non solleva mai
    (ripiego: l'offset locale, poi UTC).
    """
    from jenny.utils.helpers import safe_zoneinfo

    return safe_zoneinfo(schedule.tz) if schedule.tz else datetime.now().astimezone().tzinfo


def _same_wall_clock(ms: int, old_tz: str, new_tz: str) -> int:
    """L'istante che nel fuso *new_tz* segna la stessa ora che *ms* segna in *old_tz*.

    «Le 9 del 14 aprile» scritte a Roma restano le 9 del 14 aprile a Tokyo.
    """
    from jenny.utils.helpers import safe_zoneinfo

    wall = datetime.fromtimestamp(ms / 1000, tz=safe_zoneinfo(old_tz)).replace(tzinfo=None)
    return int(wall.replace(tzinfo=safe_zoneinfo(new_tz)).timestamp() * 1000)


def _move_to_timezone(schedule: CronSchedule, old_tz: str, new_tz: str) -> bool:
    """Porta le ore di *schedule* da *old_tz* a *new_tz*; ``True`` se qualcosa è cambiato.

    Tre cose portano un'ora d'orologio: il fuso di un'espressione cron, l'istante
    di un singolo e il «fino a» di un ricorrente o di una zona. Un «ogni N minuti»
    è un intervallo e non ne ha.
    """
    changed = False
    if schedule.kind == "cron" and schedule.tz != new_tz:
        schedule.tz = new_tz
        changed = True
    if schedule.kind == "at" and schedule.at_ms:
        moved = _same_wall_clock(schedule.at_ms, old_tz, new_tz)
        changed = changed or moved != schedule.at_ms
        schedule.at_ms = moved
    if schedule.until_ms is not None:
        moved = _same_wall_clock(schedule.until_ms, old_tz, new_tz)
        changed = changed or moved != schedule.until_ms
        schedule.until_ms = moved
    return changed


def _compute_next_run(schedule: CronSchedule, now_ms: int) -> int | None:
    """Compute next run time in ms.

    Un ricorrente con un «fino a» (``until_ms``) non ha una prossima esecuzione
    oltre la sua scadenza: torna ``None``, e chi chiama lo toglie
    (``_execute_job``, ``drop_expired``).
    """
    nxt = _next_run_unbounded(schedule, now_ms)
    if (nxt is not None and schedule.kind in ("every", "cron")
            and schedule.until_ms is not None and nxt > schedule.until_ms):
        return None
    return nxt


def _next_run_unbounded(schedule: CronSchedule, now_ms: int) -> int | None:
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

            # Use caller-provided reference time for deterministic scheduling
            base_time = now_ms / 1000
            base_dt = datetime.fromtimestamp(base_time, tz=_schedule_tzinfo(schedule))
            # Non più croniter: v. il cappello di ``cronexpr`` per il perché.
            return int(next_after(schedule.expr, base_dt).timestamp() * 1000)
        except Exception:
            return None

    return None


def next_run_on_resume(job: CronJob, now_ms: int) -> int | None:
    """Quando ripartirebbe *job* se lo riprendessi adesso; ``None`` = non puo'.

    Da adesso e senza recupero: e' ``_compute_next_run``, non
    ``_next_run_with_catch_up``. Un ``at`` scaduto durante la pausa torna
    ``None``. Pubblica perche' la stessa domanda la fa il pannello
    (``webui/cron_api.py``) per decidere se offrire «Riprendi»: una regola sola,
    cosi' il bottone non compare dove il server poi rifiuterebbe.
    """
    return _compute_next_run(job.schedule, now_ms)


def can_resume(job: CronJob, now_ms: int) -> bool:
    """*job* in pausa si può riprendere adesso.

    Un job a orario se ha ancora una prossima esecuzione
    (:func:`next_run_on_resume`); una zona, che un'ora non ce l'ha, se non è
    scaduta. Una regola sola per ``CronService.set_paused`` e per il pannello
    (``webui/cron_api.py``), così «Riprendi» non compare dove il server rifiuterebbe.
    """
    if job.schedule.kind == "zone":
        until = job.schedule.until_ms
        return until is None or until > now_ms
    return next_run_on_resume(job, now_ms) is not None


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

    # Un intervallo nullo o negativo: ``_compute_next_run`` torna ``None`` e il
    # job nasceva abilitato ma muto, senza mai partire ne' dirlo.
    if schedule.kind == "every" and (not schedule.every_ms or schedule.every_ms <= 0):
        raise ValueError("an interval schedule needs a positive interval")

    if schedule.kind == "zone":
        _validate_zone(schedule)
    elif schedule.until_ms is not None:
        if schedule.kind == "at":
            raise ValueError("an end (until) is for a recurring job or a zone, not a one-time one")
        if _compute_next_run(schedule, _now_ms()) is None:
            raise ValueError("the end (until) comes before the first run: nothing would ever fire")


def _validate_zone(schedule: CronSchedule) -> None:
    """Il cerchio di una zona nuova: centro sulla Terra, raggio fra i limiti, un nome."""
    lat, lng, radius = schedule.lat, schedule.lng, schedule.radius_m
    if lat is None or lng is None or not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise ValueError("a zone needs a latitude in [-90, 90] and a longitude in [-180, 180]")
    if radius is None or not (zone_rules.MIN_RADIUS_M <= radius <= zone_rules.MAX_RADIUS_M):
        raise ValueError(
            f"a zone radius goes from {zone_rules.MIN_RADIUS_M} to "
            f"{zone_rules.MAX_RADIUS_M} metres"
        )
    if not (schedule.zone_name or "").strip():
        raise ValueError("a zone needs the name of the place")


# Quanto nel passato puo' cadere un singolo appena creato. Un giorno: un'ora
# calcolata e poi arrivata tardi (un turno lungo) suona comunque, e cosi' un
# giorno o un mese di adesso («il 1° novembre» detto il 1° alle 12, che e' gia'
# oltre le 9); un mese o un giorno di prima no — quelli sono un anno sbagliato.
_PAST_GRACE_MS = 24 * 60 * 60 * 1000

# Fra un tentativo e l'altro di un singolo che non ha suonato (``_settle_one_shot``).
_ONE_SHOT_RETRY_MS = 5 * 60 * 1000


def _refuse_past_one_time(schedule: CronSchedule, now_ms: int) -> None:
    """Un singolo nuovo con la data gia' passata si rifiuta, invece di suonare subito.

    Il 01/11/2026, su jenny_s22, la passata della bacheca ha scritto «verso
    marzo» come ``2026-03``: il marzo gia' passato. Il job e' nato scaduto, e un
    ``at`` scaduto suona al primo giro (e' il recupero di chi era spento): il
    promemoria del rinvaso e' arrivato otto mesi in anticipo, ed e' sparito. Lo
    dice l'errore, a chiunque scriva — tool, bacheca, giardiniere — cosi' il
    modello riprova con l'anno giusto. Solo alla creazione: i job gia' salvati e
    scaduti a telefono spento devono ancora recuperare.
    """
    if schedule.kind == "zone":
        if schedule.until_ms is not None and schedule.until_ms <= now_ms:
            raise ValueError("that end date is already past: a zone needs a future one")
        return
    if schedule.kind != "at" or not schedule.at_ms or schedule.at_ms >= now_ms - _PAST_GRACE_MS:
        return
    today = datetime.fromtimestamp(now_ms / 1000).date().isoformat()
    raise ValueError(
        f"that date is already past (today is {today}): a one-time job needs a future "
        "date, and a month or a day said without a year is the next one to come"
    )


def _with_turn_taint(origin_metadata: dict | None) -> dict:
    """I metadati d'origine di un job, col taint del turno che lo scrive.

    Un job scritto con contenuto non fidato in vista (una pagina ostile letta da
    un aiutante, il risultato di un tool) può portarne il testo, e girerà da
    solo: i lavori che i suoi turni avvieranno nascono contaminati
    (``security.subagent_policy``). Sta qui, e non in chi chiama, perché le porte
    sono tante — il tool ``cron``, la bacheca, il tool ``board``, il giardiniere —
    e fino al 01/11/2026 solo la prima lo faceva: un promemoria scritto dalla
    bacheca girava pulito. Fuori da un turno il taint è vuoto e non si tocca
    niente; un ``_taint`` già scritto resta, e il nuovo gli si somma.
    """
    from jenny.security.subagent_policy import current_turn_taint

    out = dict(origin_metadata or {})
    turn_taint = current_turn_taint()
    if turn_taint:
        known = [str(x) for x in out.get("_taint") or () if isinstance(x, str)]
        out["_taint"] = known + [x for x in turn_taint if x not in known]
    return out


def _validate_cron_expr(schedule: CronSchedule) -> None:
    """Un'espressione che non si legge, o che non scatta mai, si rifiuta qui.

    ``_compute_next_run`` inghiotte ogni errore e torna ``None``, ed e' giusto:
    lo chiamano anche il boot e il timer, su job gia' salvati, e li' un'eccezione
    non ha nessuno a cui arrivare. Ma all'aggiunta quel ``None`` diventava un job
    abilitato che non parte mai e non lo dice — ``0 25 * * *`` accettato in
    silenzio. Qui c'e' ancora qualcuno a cui dirlo.
    """
    from jenny.cron.cronexpr import next_after

    if not schedule.expr:
        raise ValueError("a cron schedule needs an expression")
    try:
        next_after(schedule.expr, datetime.now(_schedule_tzinfo(schedule)))
    except ValueError as exc:
        raise ValueError(f"invalid cron expression {schedule.expr!r}: {exc}") from None


def read_jobs(workspace: Path) -> list[CronJob]:
    """I job del workspace letti col parser dello scheduler, senza avviarlo né toccarlo.

    Per chi non ha lo scheduler in mano (un prompt, una scrittura della bacheca
    in un thread): una lettura del database, sicura da qualunque thread. Un job
    che non si legge si salta.
    """
    jobs: list[CronJob] = []
    for raw in cron_store.load_rows(cron_store.cron_database(workspace)):
        try:
            jobs.append(CronService._parse_job(raw))
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    return jobs


class CronService:
    """Service for managing and executing scheduled jobs."""

    _MAX_RUN_HISTORY = 20
    _UNBOUND_AGENT_JOB_REASON = (
        "agent cron payload is missing bound session delivery context; "
        "recreate it from a chat session"
    )

    def __init__(
        self,
        workspace: Path,
        on_job: Callable[[CronJob], Coroutine[Any, Any, str | None]] | None = None,
        max_sleep_ms: int = 300_000,  # 5 minutes
    ):
        # I job stanno nello ``state.sqlite`` di *workspace* (``cron/store.py``).
        self.workspace = Path(workspace)
        self._db = cron_store.cron_database(self.workspace)
        self.on_job = on_job
        self._store: CronStore | None = None
        # Gli id dei job all'ultima lettura (o salvataggio): chi manca da lì in
        # poi è stato tolto da questa istanza (v. ``_save_store``).
        self._saved_ids: set[str] = set()
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
        # Registrato dal ``CronDispatcher`` e letto dall'``AgentLoop`` sul turno
        # di ritorno di un subagent. Sta qui perché il servizio cron è l'unico
        # oggetto che entrambi hanno già in mano: il turno d'annuncio non passa
        # dal dispatcher — arriva dal bus — e senza questo aggancio l'esito di un
        # monitor delegato non avrebbe nessuna strada per tornare nel suo job.
        self.monitor_followup: "MonitorFollowup | None" = None
        # Chiamata quando le zone cambiano (aggiunta, tolta, pausa, ripresa,
        # scaduta): la sentinella (``zone_watch.ZoneWatcher.poke``) rilegge
        # subito invece di aspettare il suo giro. ``None`` fuori dal gateway.
        self.on_zones_changed: Callable[[], None] | None = None
        # Il fuso dell'utente adesso (``agents.defaults.timezone``): lo mette il
        # gateway alla costruzione e a ogni cambio di fuso del telefono
        # (:meth:`follow_timezone`); il tool ``cron`` lo legge da qui, così non
        # ne tiene una copia ferma all'avvio. ``None`` fuori dal gateway.
        self.timezone: str | None = None

    def _zones_changed(self) -> None:
        if self.on_zones_changed is None:
            return
        try:
            self.on_zones_changed()
        except Exception:  # noqa: BLE001 — un aggancio rotto non rompe lo scheduler
            logger.exception("Cron: zone watcher not notified")

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

    @staticmethod
    def _parse_job(j: dict[str, Any]) -> CronJob:
        """Un job dello store. Solleva se non è usabile."""
        return CronJob(
            id=j["id"],
            name=j["name"],
            enabled=j.get("enabled", True),
            schedule=_parse_schedule(j["schedule"]),
            payload=CronPayload(
                kind=j["payload"].get("kind", "agent_turn"),
                mode=_parse_mode(j["payload"].get("mode")),
                message=j["payload"].get("message", ""),
                session_key=_load_session_key(j["payload"].get("sessionKey")),
                origin_channel=j["payload"].get("originChannel"),
                origin_chat_id=j["payload"].get("originChatId"),
                origin_metadata=j["payload"].get("originMetadata") or {},
                board=_load_board_link(j["payload"].get("board")),
                why=j["payload"].get("why") or None,
                condition=_load_condition(j["payload"].get("condition")),
                autonomous=j["payload"].get("autonomous") is True,
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
                could_not_check_escalated_at_ms=j.get("state", {}).get(
                    "couldNotCheckEscalatedAtMs"
                ),
                could_not_check_escalated=bool(
                    j.get("state", {}).get("couldNotCheckEscalated")
                ),
                pending_since_ms=j.get("state", {}).get("pendingSinceMs"),
                condition_notified=bool(j.get("state", {}).get("conditionNotified")),
                zone_inside=_load_inside(j.get("state", {}).get("zoneInside")),
                zone_entered_at_ms=j.get("state", {}).get("zoneEnteredAtMs"),
                zone_fix=j.get("state", {}).get("zoneFix") or None,
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
            paused_at_ms=j.get("pausedAtMs"),
        )

    def _load_store(self) -> CronStore:
        """I job dal database.

        Si rilegge a ogni chiamata, così quel che un'altra istanza ha salvato si
        vede; durante ``_on_timer`` invece si tiene lo store in memoria, perché
        una lettura nel mezzo (il polling di ``list_jobs``) non lo sostituisca
        a metà esecuzione.
        """
        if self._timer_active and self._store:
            return self._store
        jobs: list[CronJob] = []
        for raw in cron_store.load_rows(self._db):
            try:
                jobs.append(self._parse_job(raw))
            except (KeyError, TypeError, ValueError, AttributeError):
                logger.error("Cron: job {} unreadable, skipped", raw.get("id"))
        self._store = CronStore(version=1, jobs=jobs)
        self._saved_ids = {j.id for j in jobs}
        if self._enforce_store_agent_bindings() and self._running:
            self._save_store()

        return self._store

    def _save_store(self, meta: dict[str, str] | None = None) -> None:
        """I job in memoria nel database, in una transazione.

        Si tolgono solo i job che c'erano all'ultima lettura e non ci sono più:
        uno aggiunto nel frattempo da un'altra istanza resta.
        """
        if not self._store:
            return
        current = {j.id for j in self._store.jobs}
        cron_store.save_rows(self._db, [self._job_to_dict(j) for j in self._store.jobs],
                             removed=self._saved_ids - current, meta=meta)
        self._saved_ids = current

    def follow_timezone(self, tz_name: str | None) -> int:
        """Porta le ore dei job nel fuso *tz_name*; torna quanti ne ha spostati.

        Ogni ora scritta su un job è un'ora dell'orologio dell'utente: «ogni
        giorno alle 7» e «il 14 aprile alle 9» valgono dove l'utente si trova,
        non dove li ha detti. Quando il telefono cambia fuso, tutti lo seguono,
        senza eccezioni: l'espressione cron prende il fuso nuovo, il singolo e
        i «fino a» tengono la stessa ora d'orologio. Un singolo che così finisce
        nel passato scatta subito, in ritardo (``_recompute_next_runs``).

        Il fuso in cui le ore sono scritte sta nel database
        (``cron_store.META_TIMEZONE``), non in memoria: il confronto vale anche
        dopo un riavvio, e una fotografia ripristinata riporta i job insieme al
        fuso in cui erano scritti. La prima volta non c'è niente da confrontare:
        si prende nota del fuso di ora e non si sposta nulla.
        """
        name = (tz_name or "").strip()
        if not name:
            return 0
        self.timezone = name
        written_in = cron_store.read_meta(self._db, cron_store.META_TIMEZONE)
        if written_in == name:
            return 0
        store = self._load_store()
        moved = 0
        if written_in:
            now = _now_ms()
            for job in store.jobs:
                if _move_to_timezone(job.schedule, written_in, name):
                    job.updated_at_ms = now
                    moved += 1
            self._recompute_next_runs()
        self._save_store(meta={cron_store.META_TIMEZONE: name})
        if self._running:
            self._arm_timer()
        if moved:
            self._zones_changed()
        logger.info("Cron: timezone {} -> {}, {} job(s) moved", written_in or "unset", name, moved)
        return moved

    @staticmethod
    def _job_to_dict(j: CronJob) -> dict[str, Any]:
        """Un job come riga dello store (lo legge ``_parse_job``)."""
        return {
            "id": j.id,
            "name": j.name,
            "enabled": j.enabled,
            "schedule": {
                "kind": j.schedule.kind,
                "atMs": j.schedule.at_ms,
                "everyMs": j.schedule.every_ms,
                "expr": j.schedule.expr,
                "tz": j.schedule.tz,
                **({"precision": j.schedule.precision} if j.schedule.precision else {}),
                # Solo per una zona: gli store senza zone restano identici.
                **({
                    "lat": j.schedule.lat,
                    "lng": j.schedule.lng,
                    "radiusM": j.schedule.radius_m,
                    "zoneName": j.schedule.zone_name,
                    "untilMs": j.schedule.until_ms,
                } if j.schedule.kind == "zone" else {}),
                # Il «fino a» di un ricorrente, solo se c'è.
                **({"untilMs": j.schedule.until_ms}
                   if j.schedule.kind != "zone" and j.schedule.until_ms is not None else {}),
            },
            "payload": {
                "kind": j.payload.kind,
                "mode": j.payload.mode,
                "message": j.payload.message,
                "sessionKey": j.payload.session_key,
                "originChannel": j.payload.origin_channel,
                "originChatId": j.payload.origin_chat_id,
                "originMetadata": j.payload.origin_metadata,
                # Solo per i cron del quaderno: uno store senza quaderni
                # resta identico a prima, byte per byte.
                **({"board": j.payload.board} if j.payload.board else {}),
                **({"why": j.payload.why} if j.payload.why else {}),
                **({"condition": j.payload.condition} if j.payload.condition else {}),
                **({"autonomous": True} if j.payload.autonomous else {}),
            },
            "state": {
                "nextRunAtMs": j.state.next_run_at_ms,
                "lastRunAtMs": j.state.last_run_at_ms,
                "lastStatus": j.state.last_status,
                "lastError": j.state.last_error,
                "consecutiveCouldNotCheck": j.state.consecutive_could_not_check,
                "couldNotCheckSinceMs": j.state.could_not_check_since_ms,
                "couldNotCheckEscalated": j.state.could_not_check_escalated,
                "couldNotCheckEscalatedAtMs": j.state.could_not_check_escalated_at_ms,
                "pendingSinceMs": j.state.pending_since_ms,
                "conditionNotified": j.state.condition_notified,
                **({
                    "zoneInside": j.state.zone_inside,
                    "zoneEnteredAtMs": j.state.zone_entered_at_ms,
                    "zoneFix": j.state.zone_fix,
                } if j.schedule.kind == "zone" else {}),
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
            "pausedAtMs": j.paused_at_ms,
        }

    def _prune_run_records(self) -> int:
        """Tiene i ``RUNS_KEEP`` record più recenti. Ritorna quanti ne toglie."""
        removed = cron_store.prune_runs(self._db)
        if removed:
            logger.info("Cron: potati {} record di esecuzione", removed)
        return removed

    def _remove_run_records(self, job_id: str) -> int:
        """Toglie i record del job *job_id*. Ritorna quanti ne toglie."""
        return cron_store.remove_runs(self._db, job_id)

    def write_run_record(self, run_id: str, record: dict[str, Any]) -> None:
        """Write an internal audit record for one cron execution."""
        now = _now_ms()
        payload = {**record, "run_id": run_id, "updated_at_ms": now}
        cron_store.write_run(self._db, run_id, str(record.get("job_id") or ""), now, payload)

    # ── i risvegli ──────────────────────────────────────────────────────────

    def note_wake(self, job: CronJob) -> None:
        """Segna che *job* sta per svegliare il modello (un turno d'agente).

        Lo chiama il runner un attimo prima del turno (``cron/bound_runner``): i
        giri che non arrivano al modello — una condizione falsa, niente rete —
        non contano. È il conto che la casa mostra quando i risvegli sono
        troppi (``wakes``). Non può far fallire il job.
        """
        with suppress(Exception):
            cron_store.add_wake(self._db, _now_ms(), job.id, job_display_name(job),
                                job.payload.autonomous)

    def wakes(self, since_ms: int) -> list[dict[str, Any]]:
        """I risvegli da *since_ms*, per job, dal più frequente.

        ``[{job_id, name, autonomous, count, last_ms}]``: nome e firma sono
        quelli dell'ultimo risveglio, così un singolo già scattato (e tolto)
        si legge ancora.
        """
        return cron_store.read_wakes(self._db, since_ms)

    async def start(self) -> None:
        """Start the cron service."""
        self._running = True
        # Prima di qualunque cosa possa armare un timer: da qui in poi una
        # sveglia dell'OS che scatta trova un destinatario. L'aggancio va fatto
        # sul loop del gateway, e ``start`` è l'unico punto di questa classe che
        # gira sicuramente là dentro (vedi ``power.bind_wake_loop``).
        self._wake_event = power.bind_wake_loop() if power.alarm_driven_cron_enabled() else None
        self._load_store()
        # Il telefono può aver cambiato fuso a gateway spento (un volo a
        # telefono spento): prima di ricalcolare, le ore seguono quello di ora.
        try:
            self.follow_timezone(self.timezone)
        except Exception:  # noqa: BLE001 — non può fallire l'avvio dello scheduler
            logger.opt(exception=True).error("Cron: could not follow the timezone")
        self._recompute_next_runs()
        self._save_store()
        # Un ricorrente scaduto a telefono spento non ha più una prossima
        # esecuzione: va tolto, o resterebbe acceso e muto per sempre.
        with suppress(Exception):
            await self.drop_expired_recurring(_now_ms())
        self._arm_timer()
        # Una volta per avvio, che su Android vuol dire spesso: non puo' fallire il boot.
        with suppress(Exception):
            self._prune_run_records()
            cron_store.prune_wakes(self._db, _now_ms())
        logger.info("Cron service started with {} jobs", len(self._store.jobs if self._store else []))

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
            if job.schedule.kind == "zone":
                # Una zona non ha un'ora: il ``next_run_at_ms`` salvato è un
                # arrivo da consegnare (o da riprovare), e va consegnato anche
                # se l'app è stata giù nel mezzo. Lo mette e lo toglie solo
                # ``fire_zone`` / ``_settle_zone``.
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
                # ``due_jobs`` e' la foto d'inizio giro, e ogni job prima di
                # questo e' stato un turno d'agente: nel frattempo l'officina
                # puo' averlo messo in pausa, eliminato o ripreso con una
                # scadenza nuova. Durante il giro lo store non si ricarica
                # (``_load_store`` con ``_timer_active``), quindi quei gesti
                # hanno mutato proprio questi oggetti: basta riguardarli.
                if not self._still_due(job, now):
                    continue
                await self._execute_job(job)
                # Dopo **ogni** job, non a fine giro: un giro dura quanto la somma
                # dei suoi turni d'agente, e un kill (o lo spegnimento, che
                # cancella il job in corso) a meta' lasciava su disco i job gia'
                # eseguiti ancora dovuti. Al riavvio ripartivano, compreso un
                # promemoria ``at`` gia' consegnato.
                self._save_store()

            # I ricorrenti il cui «fino a» è passato senza un ultimo giro (in
            # pausa, o a telefono spento). Qui e non con ``drop_expired_recurring``:
            # dentro il giro lo store non si ricarica.
            expired = self._expired_jobs(self._store, _now_ms(), zones=False)
            for job in expired:
                logger.info("Cron: job '{}' ({}) expired", job.name, job.id)
                await self._drop_job(job)
            if expired or not due_jobs:
                self._save_store()
        finally:
            self._timer_active = False
        self._arm_timer()

    def _still_due(self, job: CronJob, now: int) -> bool:
        """Il job e' ancora nello store, acceso e dovuto a *now*."""
        if self._store is None or not any(j is job for j in self._store.jobs):
            return False
        next_run = job.state.next_run_at_ms
        return bool(job.enabled and next_run and now >= next_run)

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
        state.could_not_check_escalated_at_ms = None

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
                job.state.could_not_check_escalated_at_ms = start_ms
            logger.warning(
                "Cron: job '{}' could not run its check ({} in a row): {}",
                job.name,
                job.state.consecutive_could_not_check,
                e.reason or "no reason given",
            )
            # Il presupposto di ogni altro avviso è che il modello faccia la sua
            # parte; questa riga è ciò che accade quando non la fa. V.
            # ``jenny/cron/silence_watchdog.py``.
            alert_silently_broken_checks(job.name, job.state, now_ms=start_ms)
        except CronMonitorDelegatedError:
            # PRIMA di ``CronJobSilencedError``: il controllo è nelle mani di un
            # aiutante e non è ancora una prova di niente. La sequenza resta
            # com'è; il verdetto lo scrive il turno d'annuncio
            # (``monitor_followup``), o il run dopo se non arriva.
            job.state.last_status = "delegated"
            job.state.last_error = None
            job.state.pending_since_ms = start_ms
            logger.info("Cron: job '{}' handed its check to a helper", job.name)
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

        if job.schedule.kind == "at":
            await self._settle_one_shot(job, now_ms=end_ms)
        elif job.schedule.kind == "zone":
            await self._settle_zone(job, now_ms=end_ms)
        elif job.paused_at_ms is None:
            # Compute next run
            job.state.next_run_at_ms = _compute_next_run(job.schedule, _now_ms())
            if job.state.next_run_at_ms is None and job.schedule.until_ms is not None:
                # Era l'ultimo giro prima del suo «fino a»: il job se ne va, come
                # un singolo che ha suonato.
                logger.info("Cron: job '{}' ({}) reached its end", job.name, job.id)
                await self._drop_job(job)
        # Altrimenti la pausa e' arrivata mentre il job stesso girava:
        # ``set_paused`` ha gia' tolto la prossima esecuzione, e ricalcolarla qui
        # lasciava un job «in pausa» con una scadenza armata nello store.

    async def _settle_one_shot(self, job: CronJob, *, now_ms: int) -> None:
        """Cosa resta di un singolo dopo un giro: niente, se ha suonato.

        Un singolo che ha suonato sparisce sempre (decisione del 31/10/2026):
        niente job spenti che restano nello store, niente esito orfano sulla
        bacheca. Ma ``skipped`` ed ``error`` non sono un giro: il primo è l'agente
        che ancora non c'è (il boot, l'onboarding), il secondo un provider che non
        ha risposto — in entrambi il promemoria non è arrivato a nessuno, e
        cancellarlo lo perdeva con un WARNING nel log per tutta traccia
        (review del 01/11/2026). Resta nello store e riprova fra
        ``_ONE_SHOT_RETRY_MS``, finché non suona o finché la sua ora non è
        passata da più di ``_PAST_GRACE_MS``: dopo, suonerebbe a un giorno di
        distanza, e se ne va con un ERROR che dice cosa si è perso. È lo stesso
        «in ritardo ma suona» del recupero a telefono spento.
        """
        at_ms = job.schedule.at_ms or 0
        rang = job.state.last_status not in ("skipped", "error")
        if not rang and now_ms - at_ms <= _PAST_GRACE_MS:
            # Messo in pausa mentre girava: ``set_paused`` ha già tolto la
            # scadenza, e la ripresa decide se è ancora in tempo.
            if job.paused_at_ms is None:
                job.state.next_run_at_ms = now_ms + _ONE_SHOT_RETRY_MS
            logger.warning(
                "Cron: one-shot job '{}' ({}) did not ring ({}): retrying in {}s",
                job.name, job.id, job.state.last_status, _ONE_SHOT_RETRY_MS // 1000,
            )
            return
        if not rang:
            logger.error(
                "Cron: one-shot job '{}' ({}) never rang: its time passed more than a day "
                "ago ({}), giving up", job.name, job.id, job.state.last_error or "",
            )
        await self._drop_job(job)

    async def _drop_job(self, job: CronJob) -> None:
        """Toglie *job* dallo store in memoria e, se era di un quaderno, il suo esito.

        Il salvataggio lo fa chi chiama (``_on_timer`` dopo ogni job,
        ``drop_expired_zones`` alla fine).
        """
        if self._store is not None:
            self._store.jobs = [j for j in self._store.jobs if j.id != job.id]
        if job.payload.board:
            from jenny.board.cron_link import forget_job

            try:
                await forget_job(dict(job.payload.board), job.id)
            except Exception:  # noqa: BLE001 — il job è già andato, la bacheca è contorno
                logger.exception("Cron: board not cleaned after job {}", job.id)

    async def _settle_zone(self, job: CronJob, *, now_ms: int) -> None:
        """Cosa resta di una zona dopo che il suo arrivo è stato consegnato (o no).

        * Ha suonato: «una volta» (``delete_after_run``) se ne va; «ogni
          arrivo» resta, senza scadenza in agenda, e aspetta il prossimo arrivo
          (che per la sentinella vuol dire: prima uscire, poi rientrare).
        * Non ha suonato (``skipped``/``error``: niente rete, agente non pronto):
          riprova fra ``_ONE_SHOT_RETRY_MS`` finché l'arrivo ha meno di
          ``ZONE_RETRY_WINDOW_MS``. Dopo, non è più «sei appena arrivato»: niente
          più tentativi, un WARNING, e la zona **resta armata** — ``zone_inside``
          è vero, quindi riscatta al prossimo arrivo. Niente si perde in silenzio.
        """
        rang = job.state.last_status not in ("skipped", "error")
        if rang:
            if job.delete_after_run:
                await self._drop_job(job)
                self._zones_changed()
            else:
                job.state.next_run_at_ms = None
            return
        entered = job.state.zone_entered_at_ms or now_ms
        if now_ms - entered <= zone_rules.ZONE_RETRY_WINDOW_MS:
            if job.paused_at_ms is None:
                job.state.next_run_at_ms = now_ms + _ONE_SHOT_RETRY_MS
            logger.warning(
                "Cron: zone job '{}' ({}) did not ring ({}): retrying in {}s",
                job.name, job.id, job.state.last_status, _ONE_SHOT_RETRY_MS // 1000,
            )
            return
        job.state.next_run_at_ms = None
        logger.warning(
            "Cron: zone job '{}' ({}) arrival not delivered within {} min ({}): "
            "it stays armed for the next arrival",
            job.name, job.id, zone_rules.ZONE_RETRY_WINDOW_MS // 60_000,
            job.state.last_error or "",
        )

    # ========== Public API ==========

    def list_jobs(self, include_disabled: bool = False) -> list[CronJob]:
        """List all jobs."""
        store = self._load_store()
        jobs = store.jobs if include_disabled else [j for j in store.jobs if j.enabled]
        return sorted(jobs, key=lambda j: j.state.next_run_at_ms or float('inf'))

    def jobs_of_notebook(self, notebook: str) -> list[CronJob]:
        """I job legati al quaderno *notebook*, spenti e in pausa compresi."""
        return [j for j in self.list_jobs(include_disabled=True) if j.notebook == notebook]

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
        board: dict | None = None,
        why: str | None = None,
        condition: dict | None = None,
        autonomous: bool = False,
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
        _refuse_past_one_time(schedule, _now_ms())
        mode = _validate_mode_for_add(mode)
        why, condition = _validate_label_for_add(schedule, why=why, condition=condition)
        is_zone = schedule.kind == "zone"
        if is_zone and mode != "reminder":
            # All'arrivo si parla sempre: un monitor che tace all'arrivo è un
            # arrivo perso.
            raise ValueError("a zone is a reminder: it always speaks on arrival")
        if is_zone and len(self.zone_jobs()) >= zone_rules.MAX_ZONES:
            raise ValueError(
                f"too many zones: at most {zone_rules.MAX_ZONES} can be armed; "
                "remove or pause one first"
            )
        # Un legame storto, qui, non torna ``None`` in silenzio come al caricamento:
        # un cron del quaderno diventerebbe personale, e un monitor perderebbe il
        # recinto del quaderno. Chi chiama sbaglia, e lo deve sapere.
        link = _load_board_link(board)
        if board is not None and link is None:
            raise ValueError(f"invalid board link: {board!r}")
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
                origin_metadata=_with_turn_taint(origin_metadata),
                board=link,
                why=why,
                condition=condition,
                autonomous=bool(autonomous),
            ),
            state=CronJobState(next_run_at_ms=_next_run_with_catch_up(schedule, now)),
            created_at_ms=now,
            updated_at_ms=now,
            delete_after_run=delete_after_run or schedule.kind == "at",
        )
        self._enforce_agent_binding(job)
        store = self._load_store()
        store.jobs.append(job)
        self._save_store()
        self._arm_timer()
        if is_zone:
            self._zones_changed()

        logger.info("Cron: added job '{}' ({})", name, job.id)
        return job

    _UNSET: Any = object()

    def update_job(
        self,
        job_id: str,
        *,
        name: str | None = None,
        message: str | None = None,
        schedule: CronSchedule | None = None,
        why: Any = _UNSET,
        condition: Any = _UNSET,
    ) -> CronJob | None:
        """Cambia testo, quando, perché o condizione di un job dell'utente.

        ``None`` se il job non c'è o è di sistema. Un nuovo ``schedule`` riarma
        il job da adesso (con recupero, come all'aggiunta); un job in pausa resta
        in pausa e riparte col nuovo quando lo si riprende.

        Raises:
            ValueError: schedule, perché o condizione non validi.
        """
        store = self._load_store()
        job = next((j for j in store.jobs if j.id == job_id), None) if store else None
        if job is None or job.payload.kind == "system_event":
            return None
        new_schedule = schedule or job.schedule
        if schedule is not None:
            _validate_schedule_for_add(schedule)
            _refuse_past_one_time(schedule, _now_ms())
        new_why = job.payload.why if why is self._UNSET else why
        new_cond = job.payload.condition if condition is self._UNSET else condition
        new_why, new_cond = _validate_label_for_add(new_schedule, why=new_why, condition=new_cond)
        now = _now_ms()
        if name is not None:
            job.name = name.strip()
        if message is not None:
            job.payload.message = message
        job.payload.origin_metadata = _with_turn_taint(job.payload.origin_metadata)
        job.payload.why = new_why
        if new_cond != job.payload.condition:
            job.payload.condition = new_cond
            job.state.condition_notified = False
        zones_touched = job.schedule.kind == "zone" or (
            schedule is not None and schedule.kind == "zone"
        )
        if schedule is not None:
            job.schedule = schedule
            job.delete_after_run = job.delete_after_run or schedule.kind == "at"
            # Un cerchio nuovo (o un'ora al posto di un cerchio): dove si era
            # non vale più, e un arrivo in sospeso non è di questo posto.
            job.state.zone_inside = None
            job.state.zone_entered_at_ms = None
            job.state.zone_fix = None
            if job.paused_at_ms is None:
                job.state.next_run_at_ms = _next_run_with_catch_up(schedule, now)
        job.updated_at_ms = now
        self._save_store()
        self._arm_timer()
        if zones_touched:
            self._zones_changed()
        logger.info("Cron: updated job '{}' ({})", job.name, job.id)
        return job

    def register_system_job(self, job: CronJob) -> CronJob:
        """Register an internal system job (idempotent on restart)."""
        store = self._load_store()
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
            self._save_store()
            self._arm_timer()
            if job.schedule.kind == "zone":
                self._zones_changed()
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

    def set_paused(
        self, job_id: str, paused: bool
    ) -> Literal["paused", "resumed", "unchanged", "expired", "protected", "not_found"]:
        """Mette in pausa un job dell'utente, o lo riprende.

        La chiama la route dell'officina (``webui/cron_routes.py``), sullo stesso
        servizio che usa il tool ``cron``: e' lo stesso imbuto di scrittura, non
        un secondo scrittore. Stessa protezione di :meth:`remove_job` — un
        ``system_event`` non e' dell'utente.

        - Pausa: spento, senza prossima esecuzione, con ``paused_at_ms``. Un job
          gia' spento per un'altra ragione (``at`` eseguito, job senza sessione)
          non si mette in pausa: ``unchanged``.
        - Ripresa: la prossima esecuzione si conta **da adesso**, senza recupero
          (``_compute_next_run``, non ``_next_run_with_catch_up``). Un «ogni ora»
          ripreso dopo tre ore riparte fra un'ora; un ``at`` scaduto durante la
          pausa torna ``expired`` e resta in pausa, perche' riprenderlo lo farebbe
          scattare subito, in ritardo.
        """
        store = self._load_store()
        job = next((j for j in store.jobs if j.id == job_id), None) if store else None
        if job is None:
            return "not_found"
        if job.payload.kind == "system_event":
            logger.info("Cron: refused to pause/resume protected system job {}", job_id)
            return "protected"
        now = _now_ms()
        if paused:
            if job.paused_at_ms is not None or not job.enabled:
                return "unchanged"
            job.enabled = False
            job.state.next_run_at_ms = None
            job.paused_at_ms = now
        else:
            if job.paused_at_ms is None:
                return "unchanged"
            if not can_resume(job, now):
                return "expired"
            job.enabled = True
            job.paused_at_ms = None
            if job.schedule.kind == "zone":
                # Ripresa: come appena armata. Dove si è adesso lo dice la
                # prossima lettura, e se si è già dentro non scatta.
                job.state.zone_inside = None
                job.state.next_run_at_ms = None
            else:
                job.state.next_run_at_ms = next_run_on_resume(job, now)
        job.updated_at_ms = now
        self._save_store()
        self._arm_timer()
        if job.schedule.kind == "zone":
            self._zones_changed()
        logger.info("Cron: {} job '{}' ({})", "paused" if paused else "resumed", job.name, job.id)
        return "paused" if paused else "resumed"

    # ========== Zone (v. jenny/cron/zones.py, jenny/cron/zone_watch.py) ==========

    def zone_jobs(self) -> list[CronJob]:
        """Le zone accese (non in pausa), lette adesso dallo store.

        Chi le guarda (la sentinella) non tiene questi oggetti attraverso un
        ``await``: lo store si può rileggere nel mezzo, e un oggetto vecchio
        mutato andrebbe perso. Torna sempre per id.
        """
        store = self._load_store()
        return [j for j in store.jobs if j.enabled and j.schedule.kind == "zone"]

    def _zone(self, job_id: str) -> CronJob | None:
        store = self._load_store()
        return next(
            (j for j in store.jobs
             if j.id == job_id and j.enabled and j.schedule.kind == "zone"),
            None,
        )

    def set_zone_inside(self, job_id: str, inside: bool | None) -> bool:
        """Dove si è rispetto alla zona *job_id*, senza scattare. ``False`` se non c'è."""
        job = self._zone(job_id)
        if job is None:
            return False
        if job.state.zone_inside is inside:
            return True
        job.state.zone_inside = inside
        self._save_store()
        return True

    def fire_zone(
        self, job_id: str, *, entered_at_ms: int, fix: dict[str, Any] | None = None,
    ) -> bool:
        """Si è appena entrati nella zona *job_id*: il job diventa dovuto adesso.

        La sentinella non esegue niente: rende il job dovuto, e lo esegue il
        timer come qualunque altro (stesso runner, stesso esito, stessa
        consegna). ``run_job`` non andrebbe bene: non alza ``_timer_active``, e
        uno store riletto a metà turno perderebbe lo stato del job.
        """
        job = self._zone(job_id)
        if job is None:
            return False
        job.state.zone_inside = True
        job.state.zone_entered_at_ms = entered_at_ms
        job.state.zone_fix = dict(fix) if fix else None
        job.state.next_run_at_ms = _now_ms()
        job.updated_at_ms = job.state.next_run_at_ms
        self._save_store()
        self._arm_timer()
        logger.info("Cron: arrived in zone '{}' ({})", job.name, job.id)
        return True

    async def drop_expired_zones(self, now_ms: int) -> list[str]:
        """Toglie le zone (accese o in pausa) la cui scadenza è passata; ritorna gli id."""
        return await self._drop_expired(now_ms, zones=True)

    async def drop_expired_recurring(self, now_ms: int) -> list[str]:
        """Toglie i ricorrenti il cui «fino a» è passato (a telefono spento, o in pausa).

        Quello che scade mentre gira lo toglie già ``_execute_job``; questo è per
        chi è scaduto senza un ultimo giro: senza, resterebbe acceso con la
        prossima esecuzione a ``None``, per sempre.
        """
        return await self._drop_expired(now_ms, zones=False)

    @staticmethod
    def _expired_jobs(store: CronStore, now_ms: int, *, zones: bool) -> list[CronJob]:
        return [
            j for j in store.jobs
            if (j.schedule.kind == "zone") == zones and j.schedule.kind != "at"
            and j.schedule.until_ms is not None and j.schedule.until_ms <= now_ms
            # Un arrivo (o un giro) ancora da consegnare si consegna: scade al giro dopo.
            and not (j.enabled and j.state.next_run_at_ms is not None)
        ]

    async def _drop_expired(self, now_ms: int, *, zones: bool) -> list[str]:
        store = self._load_store()
        expired = self._expired_jobs(store, now_ms, zones=zones)
        if not expired:
            return []
        for job in expired:
            logger.info("Cron: job '{}' ({}) expired", job.name, job.id)
            await self._drop_job(job)
        self._save_store()
        self._arm_timer()
        return [j.id for j in expired]

    def move_notebook(
        self, old: str, new: str, *, old_session: str, new_session: str
    ) -> list[str]:
        """Porta dietro a un quaderno rinominato i job che erano suoi; ritorna gli id.

        Un job resta legato a un quaderno in due modi: il link della bacheca
        (``payload.board.notebook``), che decide recinto e posto sulla bacheca, e
        la chat in cui consegna (``session_key`` / ``origin_chat_id``, cioè
        ``project:<nome>``). Rinominato il quaderno, il primo faceva fallire ogni
        giro con «notebook gone» e il secondo consegnava a una chat che non c'è
        più. Si riscrivono tutti e due, nello stesso salvataggio, e solo dove
        valgono esattamente il nome vecchio. La sessione interna di un monitor
        (``cron:nb:<nome>/<id>``) si ricava dal link a ogni giro: segue da sola.
        """
        store = self._load_store()
        if old == new:
            return []
        now = _now_ms()
        moved: list[CronJob] = []
        for job in store.jobs:
            if job.payload.kind == "system_event":
                continue
            payload = job.payload
            changed = False
            if payload.board is not None and job.notebook == old:
                payload.board = {**payload.board, "notebook": new}
                changed = True
            if payload.session_key == old_session:
                payload.session_key = new_session
                changed = True
            if payload.origin_chat_id == old_session:
                payload.origin_chat_id = new_session
                changed = True
            if changed:
                job.updated_at_ms = now
                moved.append(job)
        if not moved:
            return []
        self._save_store()
        logger.info("Cron: {} job(s) moved from notebook {} to {}", len(moved), old, new)
        return [job.id for job in moved]

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
        before = len(store.jobs)
        store.jobs = [j for j in store.jobs if j.id != job_id]
        if len(store.jobs) == before:
            return False
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
        return next((j for j in store.jobs if j.id == job_id), None)

    def persist_job_state(self) -> None:
        """Salva lo stato dei job mutato da un chiamante esterno.

        Serve al solo caso in cui uno stato cambia **fuori** da un run: il
        verdetto di un monitor delegato arriva col turno d'annuncio del
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
        return {
            "enabled": self._running,
            "jobs": len(store.jobs),
            "next_wake_at_ms": self._get_next_wake_ms(),
        }
