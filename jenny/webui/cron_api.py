"""Payload della WebUI per la programmazione: cosa e' armato, e cosa fa davvero.

Neutro rispetto al trasporto, come ``skills_api``: costruisce un dizionario e non
sa niente di HTTP. La route che lo serve sta in ``webui/cron_routes.py``.

**Sincrona e con I/O: il chiamante la mette in un thread.** Legge lo store del
cron (sotto il lock del file, lo stesso che prende un ``add_job`` del tool), la
config da disco e ``HEARTBEAT.md``. Sul loop del gateway quel lock bloccherebbe
la chat e la WebSocket.

Il pannello non rende lo store: lo **riconcilia**. Un job di sistema sopravvive
alla configurazione che lo ha creato — ``register_system_job`` non ha una
controparte che deregistri e ``remove_job`` protegge i ``system_event`` — quindi
un lavoratore spento resta armato nello store e il suo gestore esce con
``return None``, cioe' registra ``ok`` senza fare niente. L'heartbeat aggiunge la
sua variante: acceso, armato, e ``HEARTBEAT.md`` senza task attivi. In tutti
questi casi lo store dice ``ok`` e la verita' e' «non sta facendo niente», e
questo e' l'unico posto in cui la differenza puo' diventare visibile.

La config si legge **da disco** e non da un ``Config`` catturato, per la stessa
ragione per cui la leggono da disco i quattro cancelli in
``runtime/cron_dispatch.py``: quello del container e' fotografato alla costruzione
e niente lo aggiorna, quindi un pannello che lo guardasse direbbe «attivo» di un
worker appena spento dall'utente.
"""

from __future__ import annotations

import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.cron.heartbeat_tasks import parse_heartbeat_tasks
from jenny.cron.purposes import system_job_purpose
from jenny.cron.types import CronJob

# Quanto testo di un promemoria entra nell'elenco. Il resto arriva col dettaglio:
# una riga di elenco che porta un promemoria intero smette di essere un elenco.
_MESSAGE_PREVIEW_CHARS = 160

# Dove vive l'interruttore di ciascun lavoratore periodico. **I percorsi non sono
# simmetrici**, e sono copiati da dove i cancelli veri li leggono
# (``cron_dispatch._run_dream`` &co.): inventarne uno regolare — per esempio
# ``gateway.heartbeat`` letto come ``agents.defaults.heartbeat`` — darebbe un
# ``getattr`` a vuoto, cioe' un worker dichiarato spento per sbaglio.
_WORKER_ENABLED_PATHS: dict[str, tuple[str, ...]] = {
    "dream": ("agents", "defaults", "dream", "enabled"),
    "gardener": ("agents", "defaults", "gardener", "enabled"),
    "heartbeat": ("gateway", "heartbeat", "enabled"),
    "update_check": ("updates", "enabled"),
}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _dig(root: Any, path: tuple[str, ...]) -> Any:
    """Segue *path* attributo per attributo. ``None`` se un anello manca.

    ``None`` vuol dire «non lo so», ed e' diverso da ``False``: chi legge non deve
    poter confondere un flag spento con un percorso sbagliato — v. la nota su
    ``_WORKER_ENABLED_PATHS``.
    """
    node = root
    for name in path:
        node = getattr(node, name, None)
        if node is None:
            return None
    return node


def _job_kind(job: CronJob) -> str:
    """``system`` o ``user``, senza far uscire il vocabolario interno.

    Il client non deve conoscere ``system_event``/``agent_turn``: sono i nomi con
    cui il servizio distingue chi esegue il job, non una categoria che l'utente
    riconosca.
    """
    return "system" if job.payload.kind == "system_event" else "user"


def _schedule_payload(job: CronJob) -> dict[str, Any]:
    """La schedulazione **strutturata**, non una frase.

    Il tool ``cron`` la rende in inglese («every 30m») perche' il suo lettore e'
    il modello. Questa esce su una UI bilingue, quindi la frase la compone il
    client: `assets/shared/cron-view.js`, con le sue chiavi i18n.
    """
    schedule = job.schedule
    return {
        "kind": schedule.kind,
        "every_ms": schedule.every_ms,
        "expr": schedule.expr,
        "at_ms": schedule.at_ms,
        "tz": schedule.tz,
    }


def _heartbeat_tasks(workspace: Path) -> dict[str, Any]:
    """Lo stato di ``HEARTBEAT.md``: c'e', si legge, e quanti task attivi ha.

    Il parsing passa da ``parse_heartbeat_tasks``, lo stesso che usa
    ``CronDispatcher._run_heartbeat`` per decidere se il turno parte. Una seconda
    scansione qui vorrebbe dire due idee di «cos'e' un task attivo», e quella del
    pannello si scoprirebbe sbagliata solo il giorno in cui contraddice quella che
    conta.

    ``file_present`` e ``file_readable`` sono due stati distinti: il file mai
    creato e' un caso normale su un telefono nuovo, il file che c'e' e non si
    legge e' un guasto (permessi, o un ``chcon`` perso dopo un ``restorecon``).
    """
    path = workspace / "HEARTBEAT.md"
    if not path.exists():
        return {"file_present": False, "file_readable": True, "tasks": []}
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("HEARTBEAT.md illeggibile: {}", exc)
        return {"file_present": True, "file_readable": False, "tasks": []}
    tasks = parse_heartbeat_tasks(content)
    return {
        "file_present": True,
        "file_readable": True,
        "tasks": [{"id": t.id, "index": t.index, "label": t.label, "text": t.text} for t in tasks],
    }


def _heartbeat_block(job: CronJob, workspace: Path) -> dict[str, Any]:
    """Unisce i task del file con le voci di ``task_checks`` dello store.

    Sono due popolazioni diverse e nessuna delle due basta da sola: il file dice
    **quali controlli esistono**, lo store dice **quali sono rotti** — e le sue
    voci esistono soltanto finche' lo sono (assente = sano). Incrociandole per
    ``id`` si ottiene la sola vista completa, e i tre stati che ne escono sono
    ``ok``, ``broken`` e ``pending`` (affidato a un subagent, verdetto non ancora
    arrivato).

    ``orphan_checks`` e' il residuo dell'incrocio: una voce nello store senza task
    nel file. Capita quando l'utente cancella da ``HEARTBEAT.md`` una riga mentre
    era rotta — niente ripulisce la voce. Non va mostrata fra i controlli rotti
    (accuserebbe l'utente di un controllo che ha tolto), ma non va nascosta: e' la
    spiegazione dei contatori a livello di job che altrimenti non avrebbero causa
    visibile.
    """
    file_state = _heartbeat_tasks(workspace)
    checks = dict(job.state.task_checks)
    tasks: list[dict[str, Any]] = []
    for task in file_state["tasks"]:
        entry = checks.pop(task["id"], None)
        if entry is None:
            state = "ok"
        elif entry.pending_since_ms is not None and not entry.consecutive_could_not_check:
            state = "pending"
        elif entry.consecutive_could_not_check:
            state = "broken"
        else:
            state = "ok"
        tasks.append({
            **task,
            "state": state,
            "consecutive": entry.consecutive_could_not_check if entry else 0,
            "since_ms": entry.since_ms if entry else None,
            "escalated": bool(entry.escalated) if entry else False,
            "escalated_at_ms": entry.escalated_at_ms if entry else None,
        })
    orphans = [
        {
            "id": task_id,
            "label": entry.label or "",
            "consecutive": entry.consecutive_could_not_check,
            "since_ms": entry.since_ms,
        }
        for task_id, entry in checks.items()
    ]
    return {
        "file_present": file_state["file_present"],
        "file_readable": file_state["file_readable"],
        "active_task_count": len(file_state["tasks"]),
        "tasks": tasks,
        "orphan_checks": orphans,
    }


def _effective(job: CronJob, *, config: Any, heartbeat: dict[str, Any] | None) -> str:
    """``active``, ``inert`` o ``disabled``: cosa questo job **fa**, non com'e' scritto.

    - ``disabled``: spento nello store, cioe' l'utente (o il tool) l'ha fermato.
    - ``inert``: armato e in orario, ma qualcosa a valle lo svuota — la config lo
      ha spento, o per l'heartbeat il file non ha task attivi. Registra ``ok`` a
      ogni giro senza fare niente, ed e' il caso che il pannello esiste per dire.
    - ``active``: armato e con qualcosa da fare.

    I promemoria dell'utente non hanno un interruttore in config: per loro
    ``enabled`` e' tutta la storia.
    """
    if not job.enabled:
        return "disabled"
    if _job_kind(job) == "user":
        return "active"
    path = _WORKER_ENABLED_PATHS.get(job.id)
    if path is not None:
        flag = _dig(config, path)
        # ``None`` = percorso non risolto: si tace invece di dichiarare uno stato
        # inventato. Un pannello che dice "inerte" perche' non ha trovato un
        # attributo mente con piu' sicurezza di uno che non dice niente.
        if flag is False:
            return "inert"
    if heartbeat is not None and heartbeat["active_task_count"] == 0:
        return "inert"
    return "active"


def _job_payload(job: CronJob, *, config: Any, workspace: Path, default_tz: str) -> dict[str, Any]:
    kind = _job_kind(job)
    heartbeat = _heartbeat_block(job, workspace) if job.id == "heartbeat" else None
    message = job.payload.message or ""
    return {
        "id": job.id,
        "name": job.name,
        "kind": kind,
        "purpose": system_job_purpose(job.id) if kind == "system" else None,
        "protected": kind == "system",
        "enabled": job.enabled,
        "effective": _effective(job, config=config, heartbeat=heartbeat),
        "mode": job.payload.mode,
        "one_shot": job.delete_after_run,
        # Il testo del promemoria e' contenuto dell'utente: l'anteprima
        # nell'elenco, l'intero solo nel dettaglio. Per i job di sistema non c'e'
        # niente da mostrare — il container li registra senza messaggio e il
        # prompt vero lo costruisce ``bound_runner`` a ogni run.
        "message_preview": message[:_MESSAGE_PREVIEW_CHARS] if kind == "user" else None,
        "message": message if kind == "user" else None,
        # ``schedule.tz`` quando c'e', il default altrimenti: la stessa scelta di
        # ``CronTool._display_timezone``, cosi' il pannello dice la stessa ora che
        # Jenny dice in chat.
        "display_timezone": job.schedule.tz or default_tz,
        "schedule": _schedule_payload(job),
        "next_run_at_ms": job.state.next_run_at_ms,
        "last_run_at_ms": job.state.last_run_at_ms,
        "last_status": job.state.last_status,
        "last_error": job.state.last_error,
        "health": {
            # A livello di job **non esiste** un ``escalated_at_ms``: ce l'ha la
            # singola voce di ``task_checks``. Non si aggiunge per simmetria — una
            # simmetria falsa e' una bugia che il client poi renderizza.
            "consecutive_could_not_check": job.state.consecutive_could_not_check,
            "since_ms": job.state.could_not_check_since_ms,
            "escalated": job.state.could_not_check_escalated,
        },
        "runs": [
            asdict(record)
            for record in reversed(job.state.run_history)
        ],
        "heartbeat": heartbeat,
    }


def webui_cron_payload(
    cron: Any | None,
    *,
    config: Any | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Lo stato della programmazione per la WebUI.

    ``cron=None`` non e' un errore: durante l'onboarding il gateway serve gia' la
    WebUI e il servizio non e' ancora costruito. Il payload lo dice con
    ``available: false`` invece di sollevare, cosi' la sezione mostra «non ancora
    avviato» e non un errore di rete.

    ``config=None`` legge da disco. E' l'idioma di
    ``cron_dispatch.refresh_system_job`` (``(config or load_config())``): il
    parametro esiste per i test, il default e' la lettura fresca che la
    riconciliazione richiede.
    """
    from jenny.config.loader import load_config
    from jenny.runtime.context import get_runtime_context

    stamp = now_ms if now_ms is not None else _now_ms()
    if cron is None:
        return {"available": False, "now_ms": stamp, "jobs": []}

    cfg = config if config is not None else load_config()
    workspace = Path(getattr(cfg, "workspace_path", ".") or ".")
    default_tz = _dig(cfg, ("agents", "defaults", "timezone")) or "UTC"

    status = cron.status()
    jobs = [
        _job_payload(job, config=cfg, workspace=workspace, default_tz=default_tz)
        # ``include_disabled=True`` e non il default: un job disabilitato e' la
        # prima cosa che si viene a cercare qui, e senza di lui «l'ho disabilitato
        # o l'ho cancellato?» resta senza risposta.
        for job in cron.list_jobs(include_disabled=True)
    ]

    ctx = get_runtime_context()
    # Lo stesso stato che le impostazioni mostrano in cima alla schermata. Non e'
    # un doppione: il banner sta sopra una pagina lunga, l'elenco sta piu' in
    # basso, e chi lo legge deve sapere **di quell'elenco** che e' stato
    # ricostruito. Da qui non si puo' rispondere "lo store non si legge": se il
    # primo caricamento fallisse il gateway non partirebbe affatto
    # (``CronService._corrupt_store_error``), quindi questa WebUI non esisterebbe.
    recovery = None
    if getattr(ctx, "cron_recovered_from", None):
        recovery = {
            "restored_from": ctx.cron_recovered_from,
            "broken_file": str(ctx.cron_quarantine_path) if ctx.cron_quarantine_path else None,
        }

    return {
        "available": True,
        "now_ms": stamp,
        "service_running": bool(status.get("enabled")),
        "next_wake_at_ms": status.get("next_wake_at_ms"),
        "default_timezone": default_tz,
        "recovery": recovery,
        "counts": {
            "total": len(jobs),
            "enabled": sum(1 for j in jobs if j["enabled"]),
            "system": sum(1 for j in jobs if j["kind"] == "system"),
            "user": sum(1 for j in jobs if j["kind"] == "user"),
            "inert": sum(1 for j in jobs if j["effective"] == "inert"),
            "broken": sum(1 for j in jobs if j["health"]["consecutive_could_not_check"]),
        },
        "jobs": jobs,
    }
