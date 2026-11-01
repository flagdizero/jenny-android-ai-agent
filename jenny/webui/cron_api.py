"""Payload della WebUI per la programmazione: cosa e' armato, e cosa fa davvero.

Neutro rispetto al trasporto, come ``skills_api``: costruisce un dizionario e non
sa niente di HTTP. La route che lo serve sta in ``webui/cron_routes.py``.

**Sincrona e con I/O: il chiamante la mette in un thread.** Legge la config da
disco. Lo store del cron invece **no**: lo legge il chiamante
sul loop, con :func:`snapshot_cron`, e qui arriva una copia. ``list_jobs`` in un
thread ricaricava lo store e riassegnava ``CronService._store`` sotto al loop:
un giro del timer in corso salvava poi la copia vecchia, e il job appena eseguito
tornava dovuto e ripartiva.

Il pannello non rende lo store: lo **riconcilia**. Un job di sistema sopravvive
alla configurazione che lo ha creato — ``register_system_job`` non ha una
controparte che deregistri e ``remove_job`` protegge i ``system_event`` — quindi
un lavoratore spento resta armato nello store e il suo gestore esce con
``return None``, cioe' registra ``ok`` senza fare niente. In questo caso lo
store dice ``ok`` e la verita' e' «non sta facendo niente», e
questo e' l'unico posto in cui la differenza puo' diventare visibile.

La config si legge **da disco** e non da un ``Config`` catturato, per la stessa
ragione per cui la leggono da disco i cancelli in
``runtime/cron_dispatch.py``: quello del container e' fotografato alla costruzione
e niente lo aggiorna, quindi un pannello che lo guardasse direbbe «attivo» di un
worker appena spento dall'utente.
"""

from __future__ import annotations

import copy
from dataclasses import asdict
from typing import Any

from jenny.cron.purposes import system_job_purpose
from jenny.cron.service import next_run_on_resume
from jenny.cron.types import CronJob, job_display_name
from jenny.utils.clock import now_ms as _now_ms

# Quanto testo di un promemoria entra nell'elenco. Il resto arriva col dettaglio:
# una riga di elenco che porta un promemoria intero smette di essere un elenco.
_MESSAGE_PREVIEW_CHARS = 160

# Dove vive l'interruttore di ciascun lavoratore periodico. **I percorsi non sono
# simmetrici**, e sono copiati da dove i cancelli veri li leggono
# (``cron_dispatch._run_dream`` &co.): inventarne uno regolare — per esempio
# ``updates.enabled`` letto come ``agents.defaults.update_check.enabled`` — darebbe
# un ``getattr`` a vuoto, cioe' un worker dichiarato spento per sbaglio.
_WORKER_ENABLED_PATHS: dict[str, tuple[str, ...]] = {
    "dream": ("agents", "defaults", "dream", "enabled"),
    "gardener": ("agents", "defaults", "gardener", "enabled"),
    "update_check": ("updates", "enabled"),
}


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


def _place(link: dict[str, Any] | None) -> dict[str, Any] | None:
    """Dove sta un cron del quaderno: ``{notebook, card, cardTitle}``, ``card`` vuoto
    per tutto il quaderno. Il segnaposto del disco (``cron_link``) non esce di qui."""
    if not link:
        return None
    from jenny.board import model
    from jenny.board.cron_link import notebook_root, scope_of
    from jenny.board.store import read_board

    notebook = str(link.get("notebook") or "")
    scope = scope_of(link)
    title = ""
    if scope != model.NOTEBOOK:
        root = notebook_root(notebook)
        card = model.card_by_id(read_board(root) or {}, scope) if root is not None else None
        title = card["title"] if card else ""
    return {"notebook": notebook, "card": scope, "cardTitle": title}


def _schedule_payload(job: CronJob) -> dict[str, Any]:
    """La schedulazione **strutturata**, non una frase.

    Il tool ``cron`` la rende in inglese («every 30m») perche' il suo lettore e'
    il modello. Questa esce su una UI bilingue, quindi la frase la compone il
    client: `assets/shared/cron-view.js`, con le sue chiavi i18n.
    """
    from jenny.cron.cronexpr import shape

    schedule = job.schedule
    return {
        "kind": schedule.kind,
        "every_ms": schedule.every_ms,
        "expr": schedule.expr,
        "at_ms": schedule.at_ms,
        "tz": schedule.tz,
        # La forma di un'espressione comune, per dirla a parole («ogni domenica
        # alle 18:30»); ``None`` quando non ce l'ha, e il client mostra l'espressione.
        "shape": shape(schedule.expr) if schedule.kind == "cron" and schedule.expr else None,
        # Solo su un ``at``: ``day`` (un giorno, senza ora) o ``month`` («marzo
        # 2027»); ``None`` vale l'ora esatta. Il client la dice nella sua lingua.
        "precision": schedule.precision if schedule.kind == "at" else None,
    }


def _condition_payload(job: CronJob) -> dict[str, Any] | None:
    """Il filtro di un ricorrente su un dato di un'app, o ``None``.

    Solo i campi che il pannello dice («umidità < 15»): ``app`` e ``params``
    servono a chi legge il dato, non a chi guarda l'elenco, ma escono lo stesso
    perche' il dettaglio puo' nominare l'app.
    """
    condition = job.payload.condition
    if not isinstance(condition, dict):
        return None
    return {
        "app": condition.get("app"),
        "params": condition.get("params") or {},
        "field": condition.get("field"),
        "op": condition.get("op"),
        "value": condition.get("value"),
    }


def _effective(job: CronJob, *, config: Any) -> str:
    """``active``, ``inert`` o ``disabled``: cosa questo job **fa**, non com'e' scritto.

    - ``disabled``: spento nello store, cioe' l'utente (o il tool) l'ha fermato.
    - ``inert``: armato e in orario, ma la config lo ha spento. Registra ``ok`` a
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
    return "active"


def _job_actions(job: CronJob, now_ms: int) -> list[str]:
    """Cosa l'officina puo' fare di questo job: le route di ``cron_routes``.

    Solo i job dell'utente: un job di sistema ha il suo interruttore in config,
    e il servizio lo rifiuterebbe comunque (``protected``). «Riprendi» solo se
    ``set_paused`` lo accetterebbe; un job spento senza pausa — un ``at`` gia'
    eseguito, un job senza sessione — si puo' solo togliere.
    """
    if _job_kind(job) != "user":
        return []
    if job.paused_at_ms is not None:
        return ["resume", "remove"] if next_run_on_resume(job, now_ms) is not None else ["remove"]
    if job.enabled:
        return ["pause", "remove"]
    return ["remove"]


def _job_payload(
    job: CronJob, *, config: Any, default_tz: str, now_ms: int
) -> dict[str, Any]:
    kind = _job_kind(job)
    message = job.payload.message or ""
    return {
        "id": job.id,
        # Mai vuoto per il pannello: un job senza nome mostra l'inizio del
        # messaggio, come quando quel ripiego stava nel nome stesso.
        "name": job_display_name(job),
        "kind": kind,
        "purpose": system_job_purpose(job.id) if kind == "system" else None,
        "protected": kind == "system",
        "enabled": job.enabled,
        "effective": _effective(job, config=config),
        "paused_at_ms": job.paused_at_ms,
        "actions": _job_actions(job, now_ms),
        "mode": job.payload.mode,
        # Un singolo e' il suo ``schedule``: ``delete_after_run`` lo portano anche i
        # job di uno store vecchio che non lo avevano, e l'officina mostrava loro
        # una «prossima volta» che non c'e'.
        "one_shot": job.schedule.kind == "at",
        # Un cron del quaderno: dove sta, cosi' l'elenco dell'officina lo dice
        # «Piante › Acero», o «Piante» per tutto il quaderno. ``None`` per gli altri.
        "place": _place(job.payload.board),
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
        # Il filtro di un ricorrente su un'app: il turno parte solo quando e' vero.
        "condition": _condition_payload(job),
        "next_run_at_ms": job.state.next_run_at_ms,
        "last_run_at_ms": job.state.last_run_at_ms,
        "last_status": job.state.last_status,
        "last_error": job.state.last_error,
        "health": {
            "consecutive_could_not_check": job.state.consecutive_could_not_check,
            "since_ms": job.state.could_not_check_since_ms,
            "escalated": job.state.could_not_check_escalated,
        },
        "runs": [
            asdict(record)
            for record in reversed(job.state.run_history)
        ],
    }


class CronSnapshot:
    """Una copia di cio' che il payload legge dal servizio cron: stato e job.

    Stessa forma delle due chiamate che :func:`webui_cron_payload` fa al servizio,
    cosi' il payload non sa se ha in mano l'uno o l'altra.
    """

    def __init__(self, status: dict[str, Any], jobs: list[CronJob]) -> None:
        self._status = status
        self._jobs = jobs

    def status(self) -> dict[str, Any]:
        return self._status

    def list_jobs(self, include_disabled: bool = False) -> list[CronJob]:
        return self._jobs if include_disabled else [j for j in self._jobs if j.enabled]


def snapshot_cron(cron: Any | None) -> CronSnapshot | None:
    """Fotografa il servizio **sul loop**, dove girano anche il timer e i suoi gesti.

    Una lettura dello store, sotto il lock del file: la stessa che fa il tool
    ``cron`` a ogni ``list``. La copia e' profonda perche' il payload si
    costruisce poi in un thread, mentre il loop continua a mutare i job veri.
    """
    if cron is None:
        return None
    return CronSnapshot(
        status=dict(cron.status()),
        jobs=copy.deepcopy(cron.list_jobs(include_disabled=True)),
    )


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
    default_tz = _dig(cfg, ("agents", "defaults", "timezone")) or "UTC"

    status = cron.status()
    jobs = [
        _job_payload(job, config=cfg, default_tz=default_tz, now_ms=stamp)
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
