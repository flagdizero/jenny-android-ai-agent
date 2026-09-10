"""Composition root esplicito del gateway (estratto da ``gateway_runtime._run_gateway``).

`GatewayContainer` costruisce l'intero grafo di oggetti del gateway in un unico
posto auditabile e ne possiede lo stato di runtime. Sostituisce le closure e le
variabili ``nonlocal`` (``agent``/``message_tool``) della vecchia god-function con
attributi d'istanza e metodi; i getter late-binding usati da ``CronDispatcher``
diventano ``lambda: self._agent`` — stesso contratto onboarding→cron di prima.

`DeferredAgentActivator` (l'attesa dell'onboarding + creazione differita
dell'agent) vive qui come metodo `_wait_and_create_agent`, così il contratto
nonlocal è incapsulato invece che sparso in closure.

Comportamento invariato rispetto a ``_run_gateway``: stessi oggetti, stesso
ordine di costruzione, stesso drain ordinato allo shutdown.
"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from jenny import __logo__, __version__
from jenny.config.schema import Config

# Id dei job di sistema che una versione precedente registrava e questa non
# esegue piu'. ``build`` li ritira dallo store prima di registrare i vivi (v. il
# commento sul posto e ``CronService.retire_system_job``). Per id e non per
# nome: un promemoria dell'utente puo' chiamarsi come vuole.
_RETIRED_SYSTEM_JOBS: tuple[str, ...] = ("atlas",)


class GatewayContainer:
    """Costruisce e avvia il grafo del gateway; possiede lo stato di runtime."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.port = config.gateway.port

        # Stato di runtime (ex-nonlocal). `agent`/`message_tool` sono riassegnati
        # dall'onboarding tramite set_agent/set_message_tool.
        self._agent: Any = None
        self._message_tool: Any = None
        self.onboarding_event = asyncio.Event()
        # Ultimo errore della sync dei template, se c'è stato (vedi _sync_templates).
        self.template_sync_error: Exception | None = None

        # Collaboratori popolati da build().
        self.bus: Any = None
        self.runtime_events: Any = None
        self.provider: Any = None
        self.session_manager: Any = None
        self.cron: Any = None
        self.snapshot: Any = None
        self.channels: Any = None
        self._deliverer: Any = None
        self._deliver_to_channel: Any = None

    # -- accessor late-binding (usati dai getter di CronDispatcher) ----------

    def set_agent(self, new_agent: Any) -> None:
        self._agent = new_agent

    def set_message_tool(self, mt: Any) -> None:
        self._message_tool = mt

    def _webui_runtime_model_name(self) -> str | None:
        if not self._agent:
            return None
        model = getattr(self._agent, "model", None)
        if isinstance(model, str):
            stripped = model.strip()
            return stripped or None
        return None

    def _on_jobs_changed(self, worker: str) -> None:
        """Ri-arma il job periodico di un lavoratore dopo un cambio in Impostazioni.

        Gancio distinto da :meth:`_on_settings_changed`, che ricostruisce
        provider e modello: qui non si tocca il provider, si fa vedere al cron una
        pianificazione nuova. E serve soprattutto per il caso in cui il job **non
        è registrato** — un gateway partito col lavoratore spento — dove il solo
        ``enabled=True`` nel file non lo farebbe partire fino al riavvio.
        """
        if self.cron is None:
            return
        from jenny.runtime.cron_dispatch import refresh_system_job

        try:
            schedule = refresh_system_job(self.cron, worker)
        except Exception as e:  # noqa: BLE001 — il valore è già scritto: questo è il contorno
            logger.warning("Could not re-arm the {} cron job: {}", worker, e)
            return
        if schedule:
            logger.info("{}: re-armed for {}", worker.capitalize(), schedule)
        else:
            logger.info("{}: disabled", worker.capitalize())

    def _on_settings_changed(self) -> None:
        """Hot-reload model/provider when WebUI settings change."""
        if not self._agent:
            return
        try:
            from jenny.config.loader import load_config as _reload_config
            from jenny.providers.factory import make_provider as _make_provider

            new_config = _reload_config()
            new_provider = _make_provider(new_config)
            new_model = new_config.agents.defaults.model
            new_ctx = new_config.agents.defaults.context_window_tokens
            old_model = getattr(self._agent, "model", None)
            old_provider = getattr(self._agent, "provider", None)
            old_base = getattr(old_provider, "api_base", None)
            new_base = getattr(new_provider, "api_base", None)
            # GenerationSettings è un dataclass frozen, quindi il confronto è per
            # valore: serve perché un cambio di max_tokens / temperature /
            # reasoning_effort lascia model e api_base identici, e senza questo
            # la guardia scartava proprio l'aggiornamento richiesto.
            old_generation = getattr(old_provider, "generation", None)
            new_generation = getattr(new_provider, "generation", None)
            if (
                new_model == old_model
                and new_base == old_base
                and new_generation == old_generation
            ):
                return
            self._agent._apply_provider_switch(
                new_provider, new_model, new_ctx,
                # Un cambio dei soli parametri di generazione non è un cambio di
                # modello: pubblicarlo come tale farebbe annunciare alla UI uno
                # switch verso il modello che era già attivo.
                publish_update=new_model != old_model,
            )
            logger.info(
                "Hot-reloaded after settings change: model={!r} provider={!r}",
                new_model,
                type(new_provider).__name__,
            )
        except Exception:
            logger.exception("Failed to hot-reload after settings change")

    def _telegram_targets(self) -> list[tuple[str, str]]:
        """Target extra per il fan-out proattivo: la chat Telegram accoppiata.

        Legge lo stato vivo del canale (aggiornato al pairing a caldo), non la
        config in memoria che potrebbe essere stantia.
        """
        dispatcher = self.channels
        if dispatcher is None:
            return []
        channel = dispatcher.channels.get("telegram")
        chat_id = getattr(channel, "paired_chat_id", None)
        if isinstance(chat_id, str) and chat_id:
            return [("telegram", chat_id)]
        return []

    def _delivery_record_hook(self) -> Any:
        """Hook di registrazione in sessione per il ``ChannelDeliverer``.

        Late-binding sull'agente vivo (l'onboarding lo crea dopo il gateway e
        ``set_agent`` lo rimpiazza): è lui a possedere il lock di sessione che
        serializza la scrittura con un turno in corso. Prima dell'agente non c'è
        nulla da serializzare e il deliverer scrive per conto proprio.
        """
        agent = self._agent
        return getattr(agent, "record_channel_delivery", None) if agent is not None else None

    async def _snapshot_before_dream(self) -> bool:
        """Checkpoint del workspace prima che Dream riscriva la memoria.

        Ritorna ``True`` solo se il checkpoint è stato **davvero** scattato, e
        il booleano non è una comodità: il prompt del review pass ha due rami
        (``agent/dream_review.md``) e quello "le tue modifiche sono reversibili"
        esiste per far potare di più. Con gli snapshot spenti questo metodo non
        fa nulla, e passare comunque ``snapshotted=True`` attaccherebbe una
        rassicurazione falsa proprio alla frase il cui unico scopo è far
        cancellare. Nel dubbio si mente al ribasso, mai al rialzo.

        Le eccezioni **si propagano**: il fail-open sta nel chiamante
        (``jenny.agent.dream_cycle.take_dream_snapshot``), che le trasforma in
        ``False`` e lascia proseguire il consolidamento. Qui inghiottirle
        vorrebbe dire decidere due cose in un posto solo.
        """
        return await self._take_snapshot("pre_dream")

    async def _take_snapshot(self, trigger: str) -> bool:
        """Checkpoint del workspace con l'etichetta *trigger*.

        Il **meccanismo** dietro i checkpoint pre-lavoro, uno solo. I contratti
        che ci stanno sopra sono invece due e restano distinti, perché dicono
        cose diverse al modello: Dream usa il booleano per scegliere un ramo di
        prompt ("le tue modifiche sono reversibili", che serve a far potare di
        più), il giardiniere no e non deve — aggiungere-non-riscrivere vale
        *anche* con la rete, e prometterla sposterebbe il suo giudizio.

        Ritorna ``False`` con gli snapshot spenti, e le eccezioni si propagano:
        il fail-open sta nei chiamanti, che sono gli unici a sapere se il proprio
        lavoro deve procedere senza rete.
        """
        if not self.snapshot:
            return False
        await self.snapshot.snapshot_now(trigger)
        return True

    # -- costruzione del grafo ----------------------------------------------

    def _sync_templates(self) -> None:
        """Estrae template, prompt di sistema, skill e UI nel workspace.

        L'estrazione dei prompt ``agent/**`` **non è opzionale**: sono codice, e
        riscriverli a ogni avvio è l'unico modo in cui una loro correzione arriva
        su un telefono già installato. Sembrerebbe quindi il posto sbagliato per
        un ``except``. Ma il ramo che si sta scegliendo qui non è "prompt freschi
        contro prompt stantii": è "prompt stantii contro **nessun gateway**", e un
        processo che muore non aggiorna niente. Fallire chiuso non protegge la
        politica, la sospende insieme a tutto il resto — e siccome il servizio
        viene riavviato dal watchdog, la sospende in loop.

        L'altro entry point (``android_entry``) faceva già questa scelta; qui non
        era stata fatta, e la stessa identica rottura aveva due esiti diversi a
        seconda di come Jenny era stata avviata. Un'asimmetria che nessuno aveva
        deciso.

        Il prezzo è che un refresh fallito diventa invisibile, quindi si paga con
        un log a ERROR (non warning: non è un dettaglio) che nomina la conseguenza
        vera — i prompt possono essere quelli della versione precedente — e con
        ``template_sync_error``, così chi vorrà mostrarlo in UI ha da dove
        leggerlo. Il fallimento *noto* di questo passo (la cartella dei risultati
        occupata da un file) è già gestito alla fonte in ``config/paths.py``:
        questo è la rete, non il rimedio.
        """
        from jenny.utils.helpers import sync_workspace_templates

        try:
            sync_workspace_templates(self.config.workspace_path)
            self._migrate_wikis()
            # Stessa promessa e stesso ``except`` della migrazione qui sopra: i
            # file di un lavoratore ritirato vanno via al primo avvio della
            # versione che l'ha tolto, o restano sul telefono per sempre.
            from jenny.runtime.retired_artifacts import sweep_retired_artifacts

            sweep_retired_artifacts(self.config.workspace_path)
            self.template_sync_error = None
        except Exception as exc:
            self.template_sync_error = exc
            logger.opt(exception=True).error(
                "Estrazione degli asset di pacchetto in {} fallita — i prompt di sistema "
                "potrebbero essere quelli della versione precedente e la WebUI potrebbe "
                "essere incompleta; il gateway parte comunque",
                self.config.workspace_path,
            )

    def _migrate_wikis(self) -> None:
        """Porta le wiki esistenti alla forma del passo 7: ``AGENTS.md`` e un id.

        Sta dentro ``_sync_templates`` e non accanto, perche' e' la stessa
        promessa: quel che una versione nuova cambia nel workspace arriva a ogni
        avvio, o non arriva mai su un telefono installato da mesi. Ed eredita
        quindi anche il suo ``except``, che e' la scelta giusta per la stessa
        ragione — il ramo non e' "wiki migrate contro wiki vecchie", e' "wiki
        vecchie contro **nessun gateway**".

        A regime costa zero scritture: la migrazione e' idempotente e a wiki
        gia' a posto non tocca niente. V. ``utils/wiki_migration.py``.
        """
        from jenny.utils.wiki_migration import migrate_wikis

        wiki_cfg = getattr(self.config, "wiki", None)
        wikis_dir = getattr(wiki_cfg, "wikis_dir", "wikis") or "wikis"
        migrate_wikis(self.config.workspace_path / wikis_dir)

    def _repair_pending_renames(self) -> None:
        """Finisce i rinomini di progetto interrotti a metà.

        Sta **dopo** ``SessionManager`` e **prima** che agente e canali possano
        aprire una sessione di progetto, perché è l'unica finestra in cui i file
        di traccia non li sta guardando nessuno.

        Perché all'avvio e non altrove: il caso che il giornale esiste per
        rimediare è il processo ucciso fra due ``rename``, e un processo ucciso
        **riparte**. Su Android quello non è un incidente raro, è il modo normale
        in cui il processo finisce. A regime costa una ``read_text`` che solleva
        ``FileNotFoundError``.

        L'``except`` è largo per la stessa ragione dell'``except`` di
        ``_migrate_wikis``: il ramo non è "rinomini finiti contro rinomini a
        metà", è "un rinomino a metà contro **nessun gateway**". Un rinomino
        lasciato aperto lo riprova l'avvio dopo; un gateway che non parte no.
        """
        from jenny.session.project_rename import repair_pending_project_renames

        try:
            for old_key, new_key in repair_pending_project_renames(
                self.config.workspace_path
            ):
                logger.warning(
                    "Project rename resumed and completed: {} -> {}",
                    old_key, new_key,
                )
        except Exception:
            logger.opt(exception=True).error(
                "Ripresa dei rinomini di progetto in sospeso fallita; il gateway "
                "parte comunque e il prossimo avvio riprova"
            )

    def build(self) -> None:
        """Costruisce l'intero grafo di oggetti (composition point)."""
        from jenny.bus.queue import MessageBus
        from jenny.bus.runtime_events import RuntimeEventBus
        from jenny.channels.dispatcher import WebSocketDispatcher
        from jenny.channels.ui_query import UiQueryCoordinator
        from jenny.cron.service import CronService
        from jenny.cron.types import CronJob, CronPayload, CronSchedule
        from jenny.providers.factory import make_provider
        from jenny.runtime.cron_dispatch import CronDispatcher
        from jenny.runtime.delivery import ChannelDeliverer
        from jenny.session.manager import SessionManager

        config = self.config
        logger.info(
            "{} Starting jenny gateway version {} on port {}...",
            __logo__, __version__, self.port,
        )
        self._sync_templates()

        # Backpressure su dispositivi memory-constrained (Android): code limitate.
        # I delta di streaming/progress usano try_publish_outbound (scartabili),
        # i messaggi finali usano publish_outbound (bloccante).
        self.bus = MessageBus(inbound_maxsize=256, outbound_maxsize=512)
        self.runtime_events = RuntimeEventBus()
        # RPC vista corrente (tool ui_view): condiviso tra agente e canale WS,
        # che girano nello stesso event loop.
        self.ui_query = UiQueryCoordinator()
        try:
            self.provider = make_provider(config)
        except (ValueError, RuntimeError) as exc:
            # Allow gateway to start without provider for onboarding.
            logger.warning("{}", exc)
            logger.info("Gateway starting without provider - complete onboarding to configure.")
            self.provider = None
        self.session_manager = SessionManager(config.workspace_path)
        self._repair_pending_renames()

        cron_store_path = config.workspace_path / "cron" / "jobs.json"
        self.cron = CronService(cron_store_path)

        # Versioning locale del workspace: snapshot automatici di sistema.
        # Lo store vive FUORI dal workspace (sibling), così lo swap atomico
        # del ripristino non porta via la storia insieme al workspace.
        from jenny.config.paths import get_data_dir
        from jenny.snapshot.engine import SnapshotEngine
        from jenny.snapshot.locations import snapshots_dir_for
        from jenny.snapshot.service import SnapshotService

        self.snapshot = SnapshotService(
            SnapshotEngine(
                config.workspace_path,
                snapshots_dir_for(config.workspace_path),
                exclude_globs=config.snapshots.exclude_globs,
                # Esclusione per path reale: vale anche quando il runtime dir
                # ha ancora il nome legacy (.minijenny).
                exclude_dirs=(get_data_dir() / "logs",),
            ),
            config.snapshots,
        )

        # Il ChannelDeliverer va costruito prima dell'agente: fornisce la
        # callback ``_deliver_to_channel`` che ``_instantiate_agent`` collega al
        # tool ``message``. Non dipende dall'agente (``_telegram_targets`` e
        # ``_delivery_record_hook`` sono valutati a posteriori), quindi l'ordine
        # è sicuro.
        self._deliverer = ChannelDeliverer(
            bus=self.bus,
            session_manager=self.session_manager,
            extra_targets=self._telegram_targets,
            record_hook=self._delivery_record_hook,
        )
        self._deliver_to_channel = self._deliverer.deliver

        if self.provider:
            self._agent = self._instantiate_agent(config, self.provider)

        self.channels = WebSocketDispatcher(
            config,
            self.bus,
            session_manager=self.session_manager,
            snapshot_service=self.snapshot,
            ui_query=self.ui_query,
            webui_runtime_model_name=self._webui_runtime_model_name,
            onboarding_event=self.onboarding_event,
            on_settings_changed=self._on_settings_changed,
            on_jobs_changed=self._on_jobs_changed,
            # Late-binding come ``get_agent`` per il cron: l'agente può essere
            # creato dopo il gateway (onboarding) e riassegnato da set_agent.
            get_subagent_manager=lambda: getattr(self._agent, "subagents", None),
            # Il servizio cron esiste gia' quando il gateway parte (lo costruisce
            # ``build`` piu' sopra), ma resta un getter e non l'oggetto: la WebUI
            # e' servita anche prima che ``build`` arrivi in fondo, e ``self.cron``
            # nasce ``None``.
            get_cron_service=lambda: self.cron,
        )

        if self.channels.enabled:
            logger.info("WebSocket channel enabled")
        else:
            logger.warning("WebSocket channel not enabled")

        cron_status = self.cron.status()
        if cron_status["jobs"] > 0:
            logger.info("Cron: {} scheduled jobs", cron_status["jobs"])

        hb_cfg = config.gateway.heartbeat
        if hb_cfg.enabled:
            logger.info("Heartbeat: every {}s", hb_cfg.interval_s)
        else:
            logger.info("Heartbeat: disabled")

        # Cron dispatch: getter late-binding per l'agent (riassegnato
        # dall'onboarding), stesso contratto del vecchio closure on_cron_job.
        # Non riceve più il ``message_tool`` né ``deliver_to_channel``: nessun job
        # consegna più da fuori il turno — chi deve parlare all'utente chiama il
        # tool ``message`` dentro il turno (vedi turn_visibility).
        self.cron.on_job = CronDispatcher(
            get_agent=lambda: self._agent,
            config=config,
            cron=self.cron,
            heartbeat_cfg=hb_cfg,
            snapshot_before_dream=self._snapshot_before_dream,
        ).dispatch

        # I lavoratori periodici che questa versione **non esegue piu'**. Il loro
        # job e' ancora scritto nello store di chi aggiorna — e' cosi' che la
        # registrazione sotto e' idempotente al riavvio — e senza il suo ramo nel
        # dispatcher scatterebbe nel vuoto a ogni scadenza, per sempre. Si
        # ritira per id, prima di registrare i vivi. Elenco chiuso: chi toglie un
        # lavoratore aggiunge il suo id qui, e nessun altro punto lo conosce.
        for retired in _RETIRED_SYSTEM_JOBS:
            self.cron.retire_system_job(retired)

        # Register Dream system job (idempotent on restart).
        dream_cfg = config.agents.defaults.dream
        if dream_cfg.enabled:
            self.cron.register_system_job(CronJob(
                id="dream",
                name="dream",
                schedule=dream_cfg.build_schedule(),
                payload=CronPayload(kind="system_event"),
            ))
            logger.info("Dream: {}", dream_cfg.describe_schedule())
        else:
            logger.info("Dream: disabled")

        # Register the Gardener system job (idempotent on restart). Nessuno
        # snapshot pre-run: il giardiniere **aggiunge e promuove**, non riscrive,
        # e non tocca né il diario (il suo input) né AGENTS.md. Quel che scrive
        # sono pagine nuove sotto wiki/, che l'utente può correggere leggendole —
        # a differenza di Dream, che riscrive memoria irrecuperabile.
        gardener_cfg = config.agents.defaults.gardener
        if gardener_cfg.enabled:
            self.cron.register_system_job(CronJob(
                id="gardener",
                name="gardener",
                schedule=gardener_cfg.build_schedule(),
                payload=CronPayload(kind="system_event"),
            ))
            logger.info("Gardener: {}", gardener_cfg.describe_schedule())
        else:
            logger.info("Gardener: disabled")

        # Register Heartbeat system job (idempotent on restart).
        if hb_cfg.enabled:
            self.cron.register_system_job(CronJob(
                id="heartbeat",
                name="heartbeat",
                schedule=CronSchedule(
                    kind="every",
                    every_ms=hb_cfg.interval_s * 1000,
                    tz=config.agents.defaults.timezone,
                ),
                payload=CronPayload(kind="system_event"),
            ))

        # Register the update-check system job (idempotent on restart). È l'unico
        # percorso periodico che tocca la rete senza che l'utente abbia chiesto
        # niente, quindi con la sezione spenta il job non viene registrato — ma
        # questo da solo non lo spegne: un job già registrato da un avvio
        # precedente resta nello store del cron, perché ``register_system_job``
        # non ha una controparte che deregistri e ``remove_job`` protegge i
        # ``system_event``. A far valere il flag a ogni esecuzione è
        # ``CronDispatcher._run_update_check``, che esce prima della rete.
        updates_cfg = config.updates
        if updates_cfg.enabled:
            self.cron.register_system_job(CronJob(
                id="update_check",
                name="update_check",
                schedule=CronSchedule(
                    kind="every",
                    every_ms=updates_cfg.check_interval_h * 3600 * 1000,
                    tz=config.agents.defaults.timezone,
                ),
                payload=CronPayload(kind="system_event"),
            ))
            logger.info("Update check: every {}h", updates_cfg.check_interval_h)
        else:
            logger.info("Update check: disabled")


    def _instantiate_agent(self, config: Config, provider: Any) -> Any:
        """Costruisce e cabla un ``AgentLoop`` (wiring condiviso build/onboarding).

        Crea l'agente via ``AgentLoop.from_config``, iscrive il
        ``WebuiTurnCoordinator``, collega la callback di consegna al tool
        ``message`` e il checkpoint pre-Dream al loop. Non registra l'agente: i chiamanti restano responsabili di
        pubblicarlo (assegnazione diretta in ``build`` vs ``set_agent`` nel ramo
        onboarding) e di avviarne il run loop. Richiede che
        ``self._deliver_to_channel`` sia già impostato.
        """
        from jenny.agent.loop import AgentLoop
        from jenny.agent.token_usage import TokenUsageHook
        from jenny.agent.tools.message import MessageTool
        from jenny.session.webui_turns import WebuiTurnCoordinator

        agent = AgentLoop.from_config(
            config, self.bus,
            provider=provider,
            cron_service=self.cron,
            session_manager=self.session_manager,
            runtime_events=self.runtime_events,
            ui_query=self.ui_query,
            hooks=[TokenUsageHook(timezone_name=config.agents.defaults.timezone)],
        )
        WebuiTurnCoordinator(
            bus=self.bus,
            sessions=self.session_manager,
            schedule_background=lambda coro: agent._schedule_background(coro),
        ).subscribe(self.runtime_events)
        message_tool = agent.tools.get("message")
        if isinstance(message_tool, MessageTool):
            message_tool.set_send_callback(self._deliver_to_channel)
            self.set_message_tool(message_tool)
        # Lo stesso callback passato al ``CronDispatcher``, non un secondo: i due
        # percorsi di Dream — il job periodico e lo slash command ``/dream`` —
        # devono checkpointare la stessa cosa. Il cablaggio sta qui e non accanto
        # al dispatcher perché l'agente può nascere dopo il gateway (onboarding
        # con provider mancante), e questo è il punto che entrambe le nascite
        # attraversano.
        agent.snapshot_before_dream = self._snapshot_before_dream
        # Il checkpoint generico, per chi non ha il contratto di Dream: il
        # giardiniere lo chiama con il proprio trigger prima di scrivere pagine.
        agent.take_snapshot = self._take_snapshot
        return agent

    # -- onboarding: creazione differita dell'agent (DeferredAgentActivator) --

    async def _wait_and_create_agent(self) -> None:
        """Wait for onboarding to complete, then create and start the agent."""
        logger.info("Waiting for onboarding to complete...")
        await self.onboarding_event.wait()
        logger.info("Onboarding signal received, creating agent...")

        try:
            from jenny.config.loader import load_config as _reload_config
            from jenny.providers.factory import make_provider as _make_provider

            new_config = _reload_config()
            provider = _make_provider(new_config)
        except Exception:
            logger.exception("Failed to create provider after onboarding")
            return

        new_agent = self._instantiate_agent(new_config, provider)
        self.set_agent(new_agent)

        logger.info("Agent created, starting run loop...")
        await new_agent.run()

    # -- orchestrazione dei task + shutdown ordinato -------------------------

    async def run(self) -> None:
        try:
            # Prima cosa a event loop vivo: fissa su disco lo stamp di
            # ``configVersion``. Le migrazioni di schema valgono già in memoria,
            # ma senza questa scrittura ripartirebbero a ogni parse — e il
            # config viene letto più volte per boot.
            from jenny.config.store import persist_schema_migrations
            try:
                await persist_schema_migrations()
            except Exception:
                # Un config non scrivibile non deve impedire l'avvio: la
                # migrazione in memoria è già applicata, si riproverà al
                # prossimo boot.
                logger.opt(exception=True).warning("Could not persist config schema version")
            # Wakelock di servizio (solo con power.keepAwake = "always"): va
            # chiesto qui, a config caricato e prima che cron/heartbeat comincino
            # a contare sui propri timer. Fuori da Android è un no-op silenzioso.
            from jenny.runtime.power import (
                apply_alarm_clock_config,
                apply_service_lock,
                apply_watchdog_config,
            )
            await apply_service_lock()
            # Watchdog: stessa logica e stesso momento del wakelock di servizio
            # (config già caricato, timer non ancora armati). Va spinto anche
            # quando è disattivato, per smontare una catena rimasta armata da un
            # avvio precedente — vedi apply_watchdog_config.
            await apply_watchdog_config()
            # Ultima rete sotto il watchdog (sveglia da 8 ore a priorità
            # massima). Anche questa va spinta a flag spento: solo un False
            # esplicito cancella una sveglia già in coda, che altrimenti
            # continuerebbe a mostrare l'icona nella barra di stato — vedi
            # apply_alarm_clock_config.
            await apply_alarm_clock_config()
            # Buco di attività attraversato prima di questo avvio. Va misurato
            # adesso e non più tardi: la fotografia lasciata da MainActivity è
            # l'unico posto in cui il "prima" sopravvive alla morte del
            # processo, e nessun altro la consuma.
            from jenny.runtime.gap_history import record_startup_gap
            await record_startup_gap()
            await self.cron.start()
            await self.snapshot.start()
            tasks = [self.channels.start()]
            if self._agent:
                tasks.append(self._agent.run())
            else:
                tasks.append(self._wait_and_create_agent())
            await asyncio.gather(*tasks)
        except KeyboardInterrupt:
            logger.info("Shutting down...")
        except Exception:
            logger.opt(exception=True).error("Gateway crashed unexpectedly")
            raise
        finally:
            self.cron.stop()
            self.snapshot.stop()
            if self._agent:
                # Drain ordinato: attende turni/subagent/consolidation in volo
                # PRIMA del flush_all(), così nessun writer di session.messages
                # è attivo durante il flush.
                await self._agent.shutdown()
            await self.channels.stop()
            from jenny.agent.tools.exec_session import DEFAULT_EXEC_SESSION_MANAGER

            DEFAULT_EXEC_SESSION_MANAGER.shutdown()
            # Pool SSH: le sessioni sono socket verso una macchina di qualcun
            # altro, e lasciarle cadere senza disconnettere significa lasciare
            # processi ssh appesi *sul server* fino al suo timeout. Costa nulla
            # quando SSH non è mai stato usato (il pool è vuoto), e non deve mai
            # impedire il resto dello shutdown.
            try:
                from jenny.agent.tools.ssh_transport import get_ssh_backend

                await get_ssh_backend().close_all()
            except Exception:
                logger.opt(exception=True).debug("Could not close ssh connections")
            if self._agent:
                flushed = self._agent.sessions.flush_all()
                if flushed:
                    logger.info("Shutdown: flushed {} session(s) to disk", flushed)
            # Snapshot finale DOPO il flush: cattura le sessioni già scritte.
            try:
                await self.snapshot.snapshot_now("shutdown")
            except Exception:
                logger.exception("Shutdown snapshot failed")
