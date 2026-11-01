"""Dispatch dei cron job (estratto dalla god-function ``on_cron_job`` in
``gateway_runtime._run_gateway``).

``on_cron_job`` era una closure di ~135 righe con rami inline (dream / bound). Qui diventa una classe con le dipendenze INIETTATE.
L'``agent`` arriva come *getter* (``get_agent``) e non come valore catturato: nel
gateway è un nonlocal riassegnato dall'onboarding (creazione differita
dell'agente quando manca il provider), quindi catturarne il valore romperebbe il
flusso onboarding→cron. Il getter preserva esattamente il late-binding originale.

Nessun ramo consegna più all'utente da fuori il turno: un job schedulato è
lavoro interno e silenzioso (:mod:`jenny.session.turn_visibility`), e l'unico
modo di parlare è il tool ``message`` chiamato dentro il turno.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Protocol

from loguru import logger

from jenny.bus.events import OutboundMessage
from jenny.cron.bound_runner import (
    CRON_WAKELOCK_TIMEOUT_S,
    BoundCronAgent,
    run_bound_cron_job,
)
from jenny.cron.monitor_followup import MonitorFollowup
from jenny.cron.service import CronJobSkippedError
from jenny.cron.session_turns import is_bound_cron_job
from jenny.runtime.power import keep_awake
from jenny.session.turn_visibility import TurnVisibility, silent_progress
from jenny.utils import clock

if TYPE_CHECKING:
    from jenny.agent.context import ContextBuilder
    from jenny.agent.gardener import GardenerOutcome, GardenerStore
    from jenny.agent.tools.registry import ToolRegistry
    from jenny.config.schema import Config
    from jenny.cron.service import CronService
    from jenny.cron.types import CronJob
    from jenny.session.manager import SessionManager


class CronCapableAgent(BoundCronAgent, Protocol):
    """Contratto strutturale dei membri dell'agente usati dal ``CronDispatcher``.

    ``AgentLoop`` lo soddisfa per costruzione. Usare un ``Protocol`` (structural
    typing) evita di importare ``AgentLoop`` a runtime da questo modulo, e con
    esso i cicli di import; i tipi delle annotazioni interne vivono sotto
    ``TYPE_CHECKING``. Estende ``BoundCronAgent`` (``tools`` + ``submit_cron_turn``),
    già usato dal ramo bound in ``run_bound_cron_job``.
    """

    context: "ContextBuilder"  # con ``.memory`` (MemoryStore)
    sessions: "SessionManager"  # con get_or_create / save / sessions_dir

    def active_session_keys(self) -> tuple[str, ...]:
        """Le sessioni con un turno in volo: il giardiniere non entra li'."""
        ...

    async def process_direct(
        self,
        content: str,
        session_key: str = ...,
        channel: str = ...,
        chat_id: str = ...,
        media: list[str] | None = ...,
        on_progress: Callable[..., Awaitable[None]] | None = ...,
        on_stream: Callable[[str], Awaitable[None]] | None = ...,
        on_stream_end: Callable[..., Awaitable[None]] | None = ...,
        ephemeral: bool = ...,
        tools: "ToolRegistry | None" = ...,
        persist_user_message: bool = ...,
        visibility: "TurnVisibility | None" = ...,
        metadata: dict[str, Any] | None = ...,
    ) -> OutboundMessage | None: ...

    def evict_pruned_sessions(self, keys: list[str]) -> None: ...


# Sessione del controllo aggiornamenti. Il prefisso ``cron:`` non è estetico:
# rende la sessione interna per ``is_internal_session_key`` (quindi invisibile
# negli elenchi user-facing) e attribuisce i token del turno alla voce "cron"
# invece che all'utente (``agent/token_usage.py``).
UPDATE_SESSION_KEY = "cron:update_check"

# Una coda cortissima: il turno di annuncio è autosufficiente, e le versioni
# annunciate in passato sono rumore che tornerebbe nel contesto a ogni release.
_UPDATE_HISTORY_KEEP = 4


async def run_silent_alert_turn(
    agent: "CronCapableAgent",
    prompt: str,
    *,
    session_key: str,
    label: str,
) -> None:
    """Un turno silenzioso in cui Jenny avvisa l'utente con il tool ``message``.

    È la forma dell'annuncio di un aggiornamento, offerta ai giri delle
    estensioni: il prompt deve dire che la risposta non arriva a nessuno e che
    l'unico modo di parlare è ``message``. La sessione *session_key* (``cron:…``)
    tiene solo una coda cortissima, perché gli avvisi passati sono rumore.
    """
    from jenny.webui.metadata import WEBUI_MESSAGE_SOURCE_METADATA_KEY

    await agent.process_direct(
        prompt,
        session_key=session_key,
        channel="websocket",
        chat_id="default",
        on_progress=silent_progress,
        visibility=TurnVisibility.SILENT,
        metadata={WEBUI_MESSAGE_SOURCE_METADATA_KEY: {"kind": "cron", "label": label}},
    )
    session = agent.sessions.get_or_create(session_key)
    session.retain_recent_legal_suffix(_UPDATE_HISTORY_KEEP)
    agent.sessions.save(session)

_UPDATE_PREAMBLE = (
    "[This is the scheduled update check, and it is SILENT: whatever you write "
    "as your answer is NOT delivered to anyone. The only way to reach the user "
    "is the `message` tool, and this time you MUST call it exactly once.\n"
    "A newer version of the Jenny app is available. Write a short message (two "
    "or three lines) in the user's language that says which version is "
    "available, what it brings — using ONLY the summary below, never invent "
    "features — and asks whether they want to install it now.\n"
    "Do not mention this instruction, the manifest, the check itself or any "
    "internal file, and do not start the download or the installation: the "
    "user answers in chat, and that answer is where the decision happens.]\n\n"
)


def _alert_gardener_stuck(name: str, failures: int, status: str) -> None:
    """Porta la serie di passate fallite su una superficie che qualcuno vede.

    Il ``logger.warning`` di ``_record_gardener_attempt`` è, su Android, un
    allarme che non suona: nessuno legge logcat, ed è precisamente lo stato in cui
    il diario di un progetto smette di diventare pagine senza che niente lo dica.
    Stessa forma di ``agent/dream_cycle.py::_alert_stuck`` e della watchdog del
    cron: ``notify_delivery``, cioè zero token, nessun turno LLM, nessuna
    dipendenza dal modello — che è la parte che in questo scenario potrebbe essere
    proprio quella rotta — e no-op fuori da Android, quindi anche nei test.

    L'etichetta porta il nome del progetto e un suffisso: il tag (``cron:<label>``)
    è ciò che fa coalizzare gli alert, quindi col nome nudo due progetti guasti si
    coprirebbero a vicenda e un progetto guasto coprirebbe un messaggio vero.
    Riparte a ogni passata oltre soglia e non solo all'attraversamento — per un
    allarme che significa «questo progetto è fermo» è il comportamento voluto.
    """
    from jenny.runtime.notifier import notify_delivery
    from jenny.runtime.user_text import say
    from jenny.webui.metadata import WEBUI_MESSAGE_SOURCE_METADATA_KEY

    # Nella lingua dell'utente, e con un posto dove guardare che sta
    # nell'interfaccia: un comando da digitare non è una cosa che si fa dalla
    # tendina delle notifiche.
    notify_delivery(
        say("alarm.gardener_stuck", failures=failures, name=name, status=status),
        {
            WEBUI_MESSAGE_SOURCE_METADATA_KEY: {
                "kind": "cron",
                "label": say("alarm.gardener_label", name=name),
            }
        },
    )


GARDENER_JOB_ID = "gardener"

# I due lavoratori periodici, col nome del loro job e il campo di config che li
# tara. Elenco chiuso e non un getattr sul nome: chi aggiunge un lavoratore lo
# registra qui, e chi legge vede in due righe quali sono.
_SYSTEM_WORKERS: tuple[str, ...] = ("dream", "gardener")


def refresh_system_job(
    cron: "CronService", worker: str, *, config: "Config | None" = None
) -> str | None:
    """Ri-arma il job periodico di *worker* sulla config di adesso, senza riavvio.

    Serve la controparte della rilettura a ogni tick: quella fa vedere i campi
    che il dispatch legge (``enabled``, il fermo del giardiniere, la distanza fra
    due passate), ma **non l'intervallo**. Quel numero non vive nel ``Config``: è
    diventato lo ``schedule`` del ``CronJob`` scritto nello store del cron alla
    registrazione, e da lì in poi nessuna lettura di ``config.json`` lo tocca.

    E c'è il caso peggiore, quello che rende questa funzione necessaria e non
    comoda: ``GatewayContainer.build`` registra ogni job **solo se acceso**. Su un
    gateway partito con un lavoratore spento, riaccenderlo scriverebbe un
    ``enabled=True`` che nessun job va a leggere — l'interruttore che riaccende
    resterebbe l'unico a chiedere un riavvio.

    ``register_system_job`` è idempotente e riparte da zero solo se la
    pianificazione è cambiata (v. il suo commento), quindi ri-registrare a
    intervallo identico non sposta la prossima scadenza.

    Ritorna la descrizione della pianificazione armata, o ``None`` se il
    lavoratore è spento: a spegnere non si deregistra niente — non esiste una
    controparte di ``register_system_job`` — e il cancello è quello di dispatch
    (``CronDispatcher._run_dream`` / ``_run_gardener``).

    **Vale per tutti e due e non solo per il giardiniere** (31/08/2026). Prima
    esisteva solo la versione del giardiniere, perché era l'unico con un
    interruttore raggiungibile; portando le manopole in Impostazioni, anche
    Dream ha guadagnato lo stesso interruttore e aveva bisogno della stessa
    controparte.
    """
    from jenny.config.loader import load_config
    from jenny.cron.types import CronJob, CronPayload

    if worker not in _SYSTEM_WORKERS:
        raise ValueError(f"unknown system worker {worker!r}; known: {_SYSTEM_WORKERS}")
    cfg = getattr((config or load_config()).agents.defaults, worker)
    if not cfg.enabled:
        return None
    cron.register_system_job(CronJob(
        id=worker,
        name=worker,
        schedule=cfg.build_schedule(),
        payload=CronPayload(kind="system_event"),
    ))
    return cfg.describe_schedule()


class CronDispatcher:
    """Instrada un ``CronJob`` al gestore giusto (dream / gardener / reminders /
    update_check / bound)."""

    def __init__(
        self,
        *,
        get_agent: Callable[[], "CronCapableAgent | None"],
        config: "Config",
        cron: "CronService",
        # Ritorna ``True`` solo se il checkpoint è stato davvero eseguito. Non è
        # ``None`` perché il prompt del review pass ne ha bisogno per scegliere
        # fra "pota, è reversibile" e "è definitivo, nel dubbio tieni": un
        # callback che con gli snapshot spenti non fa nulla e tace verrebbe
        # letto come uno che ha fatto il suo lavoro.
        snapshot_before_dream: Callable[[], Awaitable[bool]] | None = None,
        # L'orologio, iniettabile perché il seguito dei monitor delegati
        # (``MonitorFollowup``) confronta istanti: un test che non può muoverlo
        # non può provare niente su quel confronto.
        now_ms: Callable[[], int] | None = None,
        # I giri delle estensioni (``jenny/extensions.py``): ``id → giro``.
        # Instradati per id prima del controllo sull'agente, perché un giro di
        # un'estensione può non aver bisogno del modello.
        extension_jobs: dict[str, Callable[[], Awaitable[str | None]]] | None = None,
    ) -> None:
        self._get_agent = get_agent
        self._extension_jobs = dict(extension_jobs or {})
        self._config = config
        self._cron = cron
        self._snapshot_before_dream = snapshot_before_dream
        self._now_ms = now_ms or clock.now_ms
        # Un monitor che delega con ``spawn`` non ha un esito dentro il turno che
        # lo delega, e il turno che quell'esito ce l'ha — l'annuncio del subagent —
        # arriva dal bus e non passa mai di qui. Il servizio cron è l'aggancio
        # che i due condividono: v. ``jenny/cron/monitor_followup.py``.
        cron.monitor_followup = MonitorFollowup(cron=cron, now_ms=self._now_ms)
        # Un ``HEARTBEAT.md`` rimasto dalla 2.0.0 si guarda al primo job con un
        # agente, una volta per processo: v. ``jenny/cron/retired_heartbeat.py``.
        self._heartbeat_retirement_checked = False

    async def dispatch(self, job: "CronJob") -> str | None:
        """Execute a cron job through the agent.

        Il wakelock sta **qui** e non solo in ``run_bound_cron_job`` perché Dream
        e il controllo aggiornamenti non passano affatto da quel modulo: entrano da
        ``process_direct``, che non è il percorso di turno coperto da
        ``AgentLoop._dispatch``. Questo è l'unico punto attraversato da tutti
        i tipi di job. Sul ramo bound i due blocchi si annidano sullo
        stesso tag, che per costruzione acquisisce una volta sola.
        """
        async with keep_awake("cron", timeout_s=CRON_WAKELOCK_TIMEOUT_S):
            return await self._dispatch(job)

    async def _dispatch(self, job: "CronJob") -> str | None:
        """Instrada il job al suo gestore.

        **Chi aggiunge un lavoratore periodico qui sotto gli deve anche un
        cancello su ``enabled`` dentro il gestore, letto da disco.**
        ``GatewayContainer.build`` registra ogni job **solo se acceso**, ma
        ``register_system_job`` non ha una controparte che deregistri e
        ``remove_job`` protegge i ``system_event``: il job scritto da un avvio in
        cui il lavoratore era acceso resta nello store del cron e scatta per
        sempre, riavvii compresi. È il difetto che Dream ha avuto fino al
        09/09/2026 — un interruttore girato per **fermare** riscritture di
        memoria, e che non le fermava.

        Da disco e non da ``self._config``, perché quel ``Config`` è catturato
        quando il container si costruisce e niente lo aggiorna. Per i giri delle
        estensioni il cancello sta nel giro stesso.
        """
        if job.payload.kind == "system_event" and job.id in self._extension_jobs:
            return await self._extension_jobs[job.id]()

        agent = self._get_agent()
        if not agent:
            logger.warning("Cron: skipped job '{}' - no provider configured", job.name)
            raise CronJobSkippedError("no provider configured")

        if not self._heartbeat_retirement_checked:
            self._heartbeat_retirement_checked = True
            await self._announce_retired_heartbeat(agent)

        # Per id e solo sui ``system_event``, mai per nome: il nome di un job
        # dell'utente lo sceglie lui (o il modello), e un promemoria chiamato
        # «dream» faceva partire un ciclo di Dream al posto del promemoria. I job
        # di sistema nascono con l'id del lavoratore (``GatewayContainer.build``,
        # ``refresh_system_job``); quelli dell'utente hanno un uuid troncato e
        # sono ``agent_turn``.
        if job.payload.kind == "system_event":
            if job.id == "dream":
                return await self._run_dream(agent)
            if job.id == "gardener":
                return await self._run_gardener(agent)
            if job.id == "update_check":
                return await self._run_update_check(agent)
            if job.id == "reminders":
                return await self._run_reminders(agent)
        elif is_bound_cron_job(job):
            return await run_bound_cron_job(job, agent=agent, cron=self._cron)

        reason = "unbound agent cron job must be recreated from a chat session"
        logger.warning(
            "Cron: skipped unbound agent job '{}' ({}): {}", job.name, job.id, reason
        )
        raise CronJobSkippedError(reason)

    async def _announce_retired_heartbeat(self, agent: "CronCapableAgent") -> None:
        """L'avviso una tantum sui controlli di ``HEARTBEAT.md`` che non girano più.

        Prima del job e non al suo posto: il job che l'ha fatto partire gira lo
        stesso. Un guasto qui non deve fermarlo, quindi si logga e si va avanti;
        il file resta e si riprova al prossimo avvio.
        """
        from pathlib import Path

        from jenny.cron.retired_heartbeat import announce_retired_heartbeat

        workspace = getattr(self._config, "workspace_path", None)
        if not isinstance(workspace, Path):
            return
        try:
            await announce_retired_heartbeat(agent, workspace)
        except Exception:
            logger.exception("heartbeat: retirement notice failed, retried at the next start")

    async def _run_gardener(self, agent: "CronCapableAgent") -> str | None:
        """Il giardiniere: una passata su un progetto, se uno è pronto.

        Come per Dream, qui resta solo l'instradamento: i tre orologi stanno in
        ``agent/gardener_schedule.py`` e la passata in ``agent/gardener.py``,
        condivisa con lo slash command ``/gardener``.

        Il controllo su ``enabled`` è **qui e non solo alla registrazione**:
        ``register_system_job`` non ha una controparte che deregistri, quindi un
        job registrato da un avvio precedente resta nello store del cron anche
        dopo che la sezione è stata spenta. È la stessa ragione per cui
        ``_run_update_check`` esce prima della rete.

        Quel che **non** è solo instradamento è la coda: una passata che non
        registra niente va segnata come tentata, e la serie di insuccessi va
        detta fuori dal log. V. ``_record_gardener_attempt``.

        La mutua esclusione fra due passate sullo stesso progetto **non** è qui:
        sta in ``run_gardener``, perché i chiamanti sono due e la guardia deve
        essere una. Conseguenza da conoscere: ``pick_project`` non sa niente delle
        passate in volo, quindi un tick che incontra un ``/gardener`` a mano paga
        la selezione e poi si ritira con ``already_running``. Una lettura di file
        piccoli, e in cambio nessuna copia della guardia in due posti.
        """
        from jenny.agent.gardener import report_gardener_outcome, run_gardener
        from jenny.agent.gardener_schedule import pick_project
        from jenny.config.loader import load_config

        # I tre orologi si rileggono **da disco a ogni tick**, esattamente come i
        # knob di Dream in ``_run_dream`` e per la stessa ragione: il ``Config``
        # che questo dispatcher tiene in mano è quello catturato quando il
        # container si è costruito, e nessuno lo aggiorna —
        # ``container._on_settings_changed`` ricarica modello e provider, non
        # questo.
        #
        # Senza la rilettura ``enabled=False`` non è raggiungibile: è il valore
        # che l'utente cambia per **fermare** una cosa, quindi è precisamente
        # quello che non può chiedere un riavvio del gateway. Vale lo stesso per
        # ``idle_min`` e ``min_hours_between_passes``, che decidono se entrare in
        # un progetto e sono l'altra metà del freno.
        #
        # Solo i knob: ``workspace_path`` e ``wiki.wikis_dir`` restano da
        # ``self._config``, perché cambiarli è un trasloco del workspace e non una
        # taratura (v. la nota gemella in ``_run_dream``).
        cfg = load_config().agents.defaults.gardener
        if not cfg.enabled:
            logger.debug("Gardener: disabled")
            return None

        pick = pick_project(
            self._config.workspace_path,
            idle_min=cfg.idle_min,
            min_hours_between_passes=cfg.min_hours_between_passes,
            sessions=agent.sessions,
            active_session_keys=agent.active_session_keys(),
            wikis_dir_name=getattr(self._config.wiki, "wikis_dir", "wikis") or "wikis",
        )
        if pick is None:
            logger.debug("Gardener: no project ready")
            return None

        # Il delta lo porta il ``pick``: la selezione ha già aperto i diari per
        # decidere, e prima di T2.5 ``run_gardener`` li riapriva da zero un istante
        # dopo. Così il prompt e il commit parlano della **stessa** lettura, e le
        # righe arrivate nel frattempo restano non lette invece di finire sotto un
        # cursore che le dichiara digerite.
        #
        # **E chi e' dentro quel quaderno la vede lavorare.** La bolla di lavoro
        # va alla chat del progetto: se nessuno ci e' collegato il frame non
        # arriva a nessuno, ed e' giusto — nessuno sta aspettando. Se qualcuno
        # c'e', la bolla gli dice anche perche' un suo messaggio la fermera'
        # (``manual=False``: una passata automatica cede il passo all'utente).
        reporter = self._gardener_reporter(agent, pick.store.name)
        outcome = await run_gardener(agent, pick.store, delta=pick.delta, reporter=reporter)
        if reporter is not None:
            try:
                report_gardener_outcome(reporter, outcome, pick.store.root)
            except Exception as exc:  # noqa: BLE001 — la bolla non tocca l'esito
                logger.debug("Gardener: work bubble not closed: {}", exc)
        return self._record_gardener_attempt(pick.store, outcome)

    @staticmethod
    def _gardener_reporter(agent: Any, name: str) -> Any:
        """La bolla di lavoro di una passata automatica, o ``None`` senza bus."""
        bus = getattr(agent, "bus", None)
        if bus is None:
            return None
        from jenny.runtime.work_status import WorkReporter
        from jenny.session.keys import project_session_key

        reporter = WorkReporter(
            bus, kind="gardener", chat_id=project_session_key(name),
            phases=("pages", "board"), project=name, manual=False,
        )
        reporter.start()
        return reporter

    def _record_gardener_attempt(
        self, store: "GardenerStore", outcome: "GardenerOutcome"
    ) -> str | None:
        """Timbra il tentativo, allarma se la serie si allunga, e **dice l'esito**.

        Tre cose che prima non succedevano, e tutte e tre per lo stesso motivo:
        una passata che non registra niente era invisibile.

        1. **Il timbro.** Il cursore lo tengono fermo di proposito
           ``partial_write`` e ``commit_failed`` — ci sono righe non promosse che
           devono tornare — ma senza timbro «tenere il cursore» diventava «rifare
           la passata ogni mezz'ora», perché la distanza si misurava sul cursore.
           Il timbro lo mette ``run_gardener`` (v. il suo ``_stamped``), così la
           strada a mano e questa contano la stessa cosa; qui si legge
           ``outcome.failures`` e si decide se la serie vale una notifica.
        2. **L'esito nel record del cron.** Ritornando sempre ``None`` il servizio
           cron segnava ``last_status="ok"`` su una passata fallita, e lo stato del
           job — che è dove si va a guardare — diceva che tutto funzionava.
        3. **L'allarme.** Il log su Android non lo legge nessuno: è la stessa
           ragione per cui Dream ha ``_alert_stuck``, ed è la stessa primitiva
           (zero token, e no-op fuori da Android, quindi anche nei test).
        """
        from jenny.agent.gardener_state import (
            COMMITTED_STATUSES,
            GARDENER_FAILURES_ARE_ALARMING,
        )

        if outcome.status == "already_running":
            # Una passata su quel progetto è già in volo — quasi sempre un
            # ``/gardener`` lanciato a mano un attimo prima, perché il cancello del
            # fermo tiene fuori due tick di fila. Non è un insuccesso e non si
            # timbra (l'ha già fatto, o lo farà, la passata che sta lavorando), ma a
            # INFO e non a DEBUG: è l'unico posto da cui si vede che il tick non ha
            # lavorato *per un motivo*, e su Android i DEBUG non arrivano.
            logger.info(
                "Gardener cron job: a pass on {} is already in flight; this tick stands down",
                store.name,
            )
            return None
        if not outcome.ran:
            # Nessuna chiamata al provider: niente da timbrare e niente da
            # contare. ``skipped_no_delta`` è il caso *normale* di un tick, non un
            # insuccesso, e timbrarlo sposterebbe la distanza in avanti per una
            # passata che non è mai partita.
            logger.debug("Gardener cron job: {} on {}", outcome.status, store.name)
            return None
        if outcome.status in COMMITTED_STATUSES:
            # Il timbro del tentativo l'ha già messo ``GardenerState.advanced``,
            # insieme al cursore e all'azzeramento della serie.
            logger.info(
                "Gardener cron job: {} on {} ({} lines, {} writes)",
                outcome.status, store.name, outcome.lines, outcome.writes,
            )
            return None

        # Il timbro e il conto li ha già messi ``run_gardener``, perché la strada
        # a mano deve contare la stessa cosa di questa. Qui resta la sola parte
        # che è davvero del cron: decidere che la serie è abbastanza lunga da
        # valere una notifica.
        failures = outcome.failures
        logger.warning(
            "Gardener cron job: {} on {} ({} consecutive passes with nothing recorded); "
            "the next pass on it is not due for another min_hours_between_passes",
            outcome.status, store.name, failures,
        )
        if failures >= GARDENER_FAILURES_ARE_ALARMING:
            _alert_gardener_stuck(store.name, failures, outcome.status)
        return f"gardener: {store.name} {outcome.status}"

    async def _run_dream(self, agent: "CronCapableAgent") -> str | None:
        """Il job Dream, sotto la presa che tiene un solo ciclo per volta.

        La presa è **la stessa** di ``/dream`` (``agent/dream_cycle.py``), e il caso
        raggiungibile è proprio l'incrocio fra i due: i job cron sono serializzati
        fra loro, un ``/dream`` battuto a mano non lo è con niente. Il rifiuto è un
        log e un ritorno, non un'eccezione: un tick che non parte perché il lavoro è
        già in corso non è un job fallito.

        Il controllo su ``enabled`` è **qui e non solo alla registrazione**, come
        per il giardiniere e per la stessa ragione: v. la regola in ``_dispatch``.
        """
        from jenny.agent.dream_cycle import claim_dream_cycle, release_dream_cycle
        from jenny.config.loader import load_config

        # Da disco e non da ``self._config``: quel ``Config`` è catturato quando il
        # container si costruisce e **niente lo aggiorna** — è la stessa ragione,
        # con le stesse parole, per cui rileggono ``_run_gardener`` e i knob di
        # ``_dream_cycle``. Senza la rilettura ``enabled=False`` non è
        # raggiungibile, ed è precisamente il valore che l'utente gira per
        # **fermare** una cosa: non può pretendere un riavvio del gateway.
        #
        # Prima della presa e non dentro ``_dream_cycle``: la presa è condivisa con
        # il ``/dream`` battuto a mano, e un tick spento non deve nemmeno
        # contendersela.
        #
        # Il comando resta possibile a interruttore spento, come ``/gardener``:
        # l'interruttore governa il ciclo automatico, non la richiesta esplicita.
        if not load_config().agents.defaults.dream.enabled:
            logger.debug("Dream: disabled")
            return None

        if not claim_dream_cycle():
            logger.warning(
                "Dream cron job skipped: a Dream cycle is already running"
            )
            return "dream: already running"
        try:
            return await self._dream_cycle(agent)
        finally:
            # Un livello fuori dal lavoro, come nel percorso del comando: nessun
            # cammino dentro ``_dream_cycle`` può saltarlo, eccezioni e
            # cancellazione comprese. La presa la si rende sempre.
            release_dream_cycle()

    async def _dream_cycle(self, agent: "CronCapableAgent") -> str | None:
        # Dream is an internal job — run directly, not through the agent loop.
        #
        # Il prologo e l'epilogo del ciclo stanno in ``jenny/agent/dream_cycle.py``,
        # condivisi con lo slash command ``/dream``: erano due copie della stessa
        # sequenza e divergevano una divergenza alla volta. Qui resta il mestiere
        # di questo percorso — il turno incrementale, i log, la pulizia.
        from jenny.agent.dream_cycle import begin_dream_cycle, finish_dream_cycle
        from jenny.agent.memory import MemoryStore

        prune_dream_sessions = MemoryStore.prune_dream_sessions

        store = agent.context.memory
        # I knob di Dream si rileggono da disco a ogni run, e non si prendono da
        # ``self._config``. Quel Config è catturato quando il container si
        # costruisce, e **niente lo aggiorna**: ``_on_settings_changed``
        # (``runtime/container.py``) ricarica modello e provider, non questo.
        #
        # Senza la rilettura la superficie di taratura non funziona.
        # ``/dream budget memory 6000`` scrive ``config.json`` e il run manuale
        # lo applica subito (fa un ``load_config()`` fresco), mentre *questo*
        # run — quello che gira ogni due ore ed è il consumatore vero —
        # continuerebbe a usare il numero vecchio fino al riavvio del gateway.
        # L'utente vedrebbe il budget confermato in chat e nessun effetto per
        # ore, che è il modo peggiore in cui una manopola può non funzionare.
        #
        # Vale solo per i knob di Dream: il resto di ``self._config`` resta com'è
        # (v. il chip aperto sulla staleness generale del Config del cron), e il
        # costo è una lettura di un file piccolo ogni due ore.
        from jenny.config.loader import load_config

        cfg = load_config().agents.defaults.dream
        try:
            prologue = await begin_dream_cycle(
                agent,
                store=store,
                cfg=cfg,
                take_snapshot=self._snapshot_before_dream,
            )
            # Il turno incrementale ha un ``try`` suo, e il ciclo si chiude nel
            # suo ``finally``: correzione del 2026-08-17. Con
            # ``finish_dream_cycle`` come ultima istruzione del try esterno, un
            # ``process_direct`` che solleva finiva nell'``except`` e i contatori
            # non venivano scritti — quindi ``runs_since_review`` non avanzava, e
            # un Dream che crasha a ogni run non arrivava **mai** a un review
            # pass, senza che niente lo dicesse. È lo stesso guasto che la
            # docstring di ``finish_dream_cycle`` racconta di aver chiuso per il
            # caso "niente storia da consolidare"; mancava questo.
            #
            # ``advanced=None`` significa "il turno non ha mancato niente": è il
            # valore del ramo senza storia, e anche quello di un turno crashato.
            # Su un crash **non** si usa ``False``: quello incrementa ``stuck``,
            # il cui allarme dice "le scritture continuano a essere rifiutate dal
            # budget" — una diagnosi sbagliata per un'eccezione, e su una
            # superficie che l'utente vede. Il crash ha già il suo
            # ``logger.exception``.
            advanced: bool | None = None
            # Zero finché il turno non dice altro: un turno che crasha prima di
            # scrivere non ha avuto nessun rifiuto, e attribuirgliene uno lo
            # manderebbe nel ramo "manca spazio" con la diagnosi sbagliata.
            refused = 0
            try:
                advanced, refused = await self._dream_turn(agent, store, prologue)
            finally:
                finish_dream_cycle(
                    store,
                    advanced=advanced,
                    runs_since_review=prologue.runs_since_review,
                    stuck=prologue.stuck,
                    nothing_new=prologue.nothing_new,
                    # La causa, che è ciò che decide quale dei due contatori sale:
                    # un rifiuto rimasto aperto significa "manca spazio" e un
                    # review può liberarlo; senza rifiuti non c'è niente da
                    # liberare, e forzarlo poterebbe file che non hanno da dare.
                    refused=refused,
                )
        except Exception:
            logger.exception("Dream cron job failed")
        # La contabilita' dei token **non passa da qui**: la fa
        # ``TokenUsageHook.after_iteration`` sul turno stesso, che e' l'unico punto
        # in cui l'``usage`` del provider esiste davvero. Qui c'era un ``finally``
        # con ``record_response_token_usage(resp, source="dream")``: ``resp`` e' un
        # ``OutboundMessage``, che un campo ``usage`` non ce l'ha e non l'ha mai
        # avuto, quindi quella riga non ha mai registrato niente — in nessuno dei
        # cinque punti in cui era stata scritta. Toglierla non perde una misura, ne
        # toglie una finta.
        # compact_history now acquires a threading.Lock and rewrites the whole
        # file; run it off the event loop so a concurrent append holding the
        # lock (on another thread) can't stall the loop on the blocking wait.
        await asyncio.to_thread(store.compact_history)
        pruned_keys = prune_dream_sessions(agent.sessions.sessions_dir)
        if pruned_keys:
            agent.evict_pruned_sessions(pruned_keys)
        return None

    async def _dream_turn(
        self, agent: "CronCapableAgent", store: Any, prologue: Any
    ) -> tuple[bool | None, int]:
        """Il turno incrementale, e la sua riga di log. Ritorna ``(avanzato, rifiuti)``.

        Il turno è :func:`jenny.agent.dream_cycle.run_dream_turn`, lo stesso di
        ``/dream`` dal 24/09/2026; qui resta ciò che è del cron, cioè dirlo nel
        log. I rifiuti viaggiano fin qui perché sono la **causa**: decidono quale
        dei due contatori del livelock sale. ``None`` come primo valore vuol dire
        "non c'era niente da consolidare", diverso da ``False`` (ha provato e non
        ce l'ha fatta).

        Estratto in un metodo perché la chiusura del ciclo va in un ``finally``
        nel chiamante, e un ``return`` di mezzo inline costringeva a ripeterla.
        """
        from jenny.agent.dream_cycle import DreamOutcome, run_dream_turn

        turn = await run_dream_turn(
            agent, store, prologue, take_snapshot=self._snapshot_before_dream,
        )
        if turn.outcome is DreamOutcome.NO_INPUT:
            logger.info("Dream: nothing to process")
        elif turn.outcome is DreamOutcome.ADVANCED:
            logger.info("Dream cron job completed, cursor advanced to {}", turn.last_cursor)
        elif turn.outcome is DreamOutcome.HELD_BATCH:
            # Niente "wrote to disk" nel testo: quel run poteva non aver scritto
            # nulla. Il fatto che conta è che il batch non è atterrato, non se
            # qualcosa è stato scritto — ed è per questo che la guardia esiste.
            logger.warning(
                "Dream cron job consolidated nothing from its batch "
                "(no memory file grew while a memory file was near its budget); "
                "cursor held at {} so the entries come back",
                store.get_last_dream_cursor(),
            )
        elif turn.outcome is DreamOutcome.BLOCKED:
            # Completato pulito ma senza scritture riuscite pur avendole tentate:
            # blocco/rifiuto. Le voci vanno riprocessate al prossimo run.
            logger.warning(
                "Dream cron job completed without writing (attempts blocked/refused); "
                "cursor remains at {}",
                store.get_last_dream_cursor(),
            )
        else:
            logger.warning(
                "Dream cron job did not complete; cursor remains at {}",
                store.get_last_dream_cursor(),
            )
        return turn.advanced, turn.refused

    async def _run_reminders(self, agent: "CronCapableAgent") -> str | None:
        """L'orologio dei quaderni: le attese delle bacheche, e gli eventi delle app.

        Il cancello e' ``wiki.enabled``, letto da disco come gli altri: il job
        registrato da un avvio con la wiki accesa resta nello store del cron
        anche dopo che e' stata spenta. Il lavoro sta in ``jenny/board/clock.py``.
        """
        from jenny.board.clock import run_reminders
        from jenny.config.loader import load_config

        config = load_config()
        if not config.wiki.enabled:
            logger.debug("Reminders: wiki disabled")
            return None
        return await run_reminders(
            agent,
            self._config.workspace_path,
            wikis_dir_name=getattr(self._config.wiki, "wikis_dir", "wikis") or "wikis",
            timezone_name=config.agents.defaults.timezone or None,
            cron=self._cron,
        )

    async def _run_update_check(self, agent: "CronCapableAgent") -> str | None:
        """Update check: annuncia una versione nuova UNA volta sola, poi tace.

        La regola centrale è la seconda esecuzione: un utente che ha già visto
        (e magari rimandato) l'annuncio di 0.7.0 non deve ritrovarselo ogni
        giorno. Chi decide è ``notified_code`` nello stato dell'updater, non il
        modello.

        Il turno segue il contratto dei monitor: silenzioso, con la chat
        WebUI come indirizzo, e l'unica consegna possibile è il tool ``message``
        chiamato dentro il turno.
        """
        from jenny.config.loader import load_config

        # Da disco come gli altri tre lavoratori periodici, e non da
        # ``self._config``: quel ``Config`` è catturato quando il container si
        # costruisce e niente lo aggiorna. Oggi ``updates.enabled`` non ha una
        # superficie di scrittura a runtime, quindi la lettura stantia non si
        # vedrebbe — ma il giorno che nasce quell'interruttore il difetto sarebbe
        # identico a quello che Dream ha avuto fino al 09/09/2026, con
        # l'aggravante di una guardia che *sembra* già esserci.
        #
        # La stessa lettura serve tutto il ramo — il cancello, il manifest e la
        # notifica — altrimenti sarebbero due sorgenti di verità nella stessa
        # funzione.
        config = load_config()

        if not config.updates.enabled:
            # Lo spegnimento va fatto valere **qui**, non solo alla
            # registrazione. Il job sopravvive alla configurazione che lo ha
            # creato: ``register_system_job`` non ha una controparte che
            # deregistri e ``remove_job`` protegge i ``system_event``, quindi il
            # job registrato al primo avvio (il default è acceso) resta nello
            # store del cron anche dopo che l'utente ha spento la sezione.
            # Senza questa uscita l'unico percorso periodico che tocca la rete
            # senza che nessuno l'abbia chiesto continuerebbe a girare — e con
            # esso il turno LLM e i token che costa.
            logger.debug("Update check: disabled in config, nothing to do")
            return None

        from jenny.runtime.update_check import (
            check_for_update,
            mark_notified,
            notified_version_code,
        )

        info = await check_for_update(config)
        if info is None:
            logger.debug("Update check: nothing to propose")
            return None
        if not config.updates.notify_in_chat:
            # Niente ``mark_notified``: l'annuncio non è avvenuto, e se l'utente
            # riaccende la notifica deve ancora poterlo ricevere.
            logger.info(
                "Update check: {} available, chat notification disabled", info.version_name
            )
            return None
        if notified_version_code() == info.version_code:
            logger.debug("Update check: {} was already announced", info.version_name)
            return None

        from jenny.webui.metadata import WEBUI_MESSAGE_SOURCE_METADATA_KEY

        source_metadata = {WEBUI_MESSAGE_SOURCE_METADATA_KEY: {"kind": "update"}}
        size_mb = info.size / (1024 * 1024)
        prompt = (
            _UPDATE_PREAMBLE
            + f"New version: {info.version_name}\n"
            + f"Summary: {info.summary or '(no summary provided)'}\n"
            + f"Download size: {size_mb:.1f} MB\n"
            + (f"Release notes: {info.notes_url}\n" if info.notes_url else "")
            + (
                "This is a critical security update: say so plainly.\n"
                if info.critical
                else ""
            )
        )

        await agent.process_direct(
            prompt,
            session_key=UPDATE_SESSION_KEY,
            channel="websocket",
            chat_id="default",
            on_progress=silent_progress,
            visibility=TurnVisibility.SILENT,
            metadata=source_metadata,
        )

        # Marcato **incondizionatamente**, anche se il modello non avesse
        # chiamato ``message``. È un compromesso, non una svista, e va detto per
        # intero perché il costo cade su un utente che non vedrà mai il log.
        #
        # Il fatto esiste: il turno lo calcola come ``TurnOutcome.spoke``
        # (``agent/turn_types.py``, da ``ctx.spoke_via_tool``). Non arriva qui
        # perché ``process_direct`` restituisce per contratto il *payload* e non
        # l'esito — scelta deliberata, documentata nella sua docstring — e
        # cambiarla vorrebbe dire toccare la firma condivisa da Dream
        # e dai comandi. La strada alternativa (un deliverer iniettato
        # nel dispatcher) è chiusa apposta: v. la docstring di questo modulo e
        # ``runtime/container.py``.
        #
        # Legandolo a ``spoke`` si scambierebbe comunque un difetto con un
        # altro: un modello che sistematicamente non chiama ``message`` farebbe
        # ripartire un turno LLM a ogni controllo, per sempre. Così invece si
        # perde al più *una* spinta in chat — e la versione resta visibile
        # altrove, nel badge delle impostazioni (``webui/settings_api.py``) e nel
        # tool ``update_status``, che leggono lo stesso ``cached_update()``.
        mark_notified(info.version_code)

        session = agent.sessions.get_or_create(UPDATE_SESSION_KEY)
        session.retain_recent_legal_suffix(_UPDATE_HISTORY_KEEP)
        agent.sessions.save(session)

        if info.critical:
            # Una fix di sicurezza deve squillare anche se l'utente non aveva la
            # chat aperta: l'alert implicito della consegna parte solo se il
            # turno ha davvero prodotto un messaggio. Stesso tag, quindi le due
            # notifiche si sostituiscono invece di sommarsi.
            from jenny.runtime.notifier import post_alert
            from jenny.runtime.user_text import say

            await post_alert(
                say("alarm.update_critical", version=info.version_name),
                source_metadata,
            )

        logger.info(
            "Update check: announced {} (versionCode {})",
            info.version_name, info.version_code,
        )
        return None
