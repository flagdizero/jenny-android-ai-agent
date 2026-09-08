"""Configuration schema using Pydantic."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from loguru import logger

from jenny.config.tool_schemas import (
    AndroidWebToolsConfig,
    DiagnosticsToolConfig,
    FileToolsConfig,
    IntrospectToolConfig,
    LocationConfig,
    MyToolConfig,
    PythonExecConfig,
    SshConfig,
)
from jenny.config_base import Base
from jenny.cron.types import CronSchedule
from jenny.pydantic_compat import (
    AliasChoices,
    BaseSettings,
    Field,
    model_validator,
)
from jenny.runtime.update_manifest import DEFAULT_MANIFEST_URL
from jenny.snapshot.engine import DEFAULT_EXCLUDE_GLOBS


class DreamConfig(Base):
    """Dream memory consolidation configuration.

    Qui vivono anche i budget dei file di memoria lunga, perché Dream è l'unico
    processo che li scrive: ``memory/MEMORY.md``, ``USER.md`` e ``SOUL.md``.
    Nessuno, oggi, chiede mai a quei file se sono diventati troppo grandi, ed è
    per questo che le regole di pruning già scritte nel prompt non scattano mai.
    """

    _HOUR_MS = 3_600_000

    enabled: bool = True  # Register the periodic Dream consolidation job on startup
    interval_h: int = Field(default=2, ge=1)  # Every 2 hours by default

    # I due budget qui sotto valgono 3.000 caratteri, ed è un numero misurato,
    # non stimato — ma la misura giusta non è quella con cui erano nati. La prima
    # stesura li metteva a 2.000 leggendo il device *dopo due passaggi di review*
    # (``memory/MEMORY.md`` 3.019 caratteri, ``USER.md`` 1.626): un tetto tarato
    # sul pavimento post-compressione, non sulla dimensione a cui il file lavora.
    # Senza review gli stessi due file misurano 3.943 e 3.524 (Titan 2,
    # 2026-08-16, le stesse misure citate sotto per ``SOUL.md``), e un tetto sotto
    # quella soglia non è un soffitto contro la ricrescita: è uno stato di
    # saturazione permanente. Il 2026-08-18 ``USER.md`` sul device stava a
    # 1.999/2.000 — 99%, un carattere — e in quel giro Dream ha letto tutti e tre
    # i file e non ha scritto niente: la conversazione da consolidare è passata
    # per un file che non aveva più spazio.
    #
    # Da lì il criterio dei 3.000: il massimo delle due dimensioni non potate
    # (3.943 e 3.524) non ci sta comunque, e non deve — il tetto serve a mordere.
    # Ma deve mordere lasciando margine di manovra a chi pota, non chiudere la
    # porta a chi aggiunge: 3.000 tiene ``USER.md`` sotto soglia con ~500
    # caratteri di respiro sopra il suo stato attuale, e resta vincolante su
    # entrambi i file quando ricrescono. Per riferimento, Hermes tiene gli
    # equivalenti a 2.200 e 1.375.
    #
    # Un'installazione nuova nasce ben sotto: i template di ``MEMORY.md`` e
    # ``USER.md`` sono vuoti, il tetto non morde finché non c'è dentro qualcosa
    # da potare.
    #
    # Sono rimasti a 0 — "misurato ma non applicato" — per tutto il tempo in cui
    # servivano le misure, e soprattutto finché un rifiuto di budget poteva far
    # avanzare comunque il cursore di Dream: il fatto rifiutato non era su disco
    # e non sarebbe tornato in nessun batch. Ora ``internal_run_should_commit``
    # (``agent/memory.py``) non registra il progresso di un run che si è visto
    # rifiutare una scrittura, quindi l'input torna al run seguente. È quella la
    # precondizione che questi due numeri aspettavano.
    #
    # **Cambiare questo default non raggiunge un'installazione esistente.**
    # ``config/loader.py`` serializza con ``by_alias=True`` e senza
    # ``exclude_defaults``, quindi ogni ``config.json`` già scritto porta dentro
    # lo zero di prima e continua a vincere su questa riga. Là il tetto si alza
    # con ``/dream budget memory 3000``, che è esattamente il motivo per cui quel
    # comando esiste.
    #
    # Lo 0 resta legale, e da lì il vincolo ``ge=0`` e non ``gt=0``. La
    # distinzione è la stessa di ``_positive_float_env`` in
    # ``config/runtime_env.py``, ma con il segno opposto: lì zero non
    # disabilitava niente (un ``wait_for(0)`` fa fallire ogni send) e quindi il
    # valore andava rifiutato; qui zero disabilita davvero l'enforcement, ed è
    # sia il default di SOUL.md sia la via d'uscita se un tetto si rivela
    # sbagliato. Non "correggerlo" in ``gt=0``.
    memory_budget_chars: int = Field(
        default=3000,
        ge=0,
        validation_alias=AliasChoices("memoryBudgetChars", "memory_budget_chars"),
        serialization_alias="memoryBudgetChars",
    )
    user_budget_chars: int = Field(
        default=3000,
        ge=0,
        validation_alias=AliasChoices("userBudgetChars", "user_budget_chars"),
        serialization_alias="userBudgetChars",
    )
    # ``SOUL.md`` resta a 0 anche dopo la taratura degli altri due, ed è una
    # decisione, non una dimenticanza — ma non per la ragione che stava scritta
    # qui. La prima stesura diceva "è sano, 45 righe": misurato sul device il
    # 2026-08-16 è 6.342 caratteri su 55 righe, cioè **il più grande dei tre**
    # (MEMORY.md 3.943, USER.md 3.524). La roadmap lo dava per sano e nessuno
    # aveva più guardato.
    #
    # La decisione regge lo stesso, con l'argomento vero: il file mescola due
    # popolazioni che un tetto di dimensione non sa distinguere. C'è l'identità
    # dell'agente, che il system prompt non riscrive da nessun'altra parte e che
    # non va potata mai; e c'è un blocco ``## Execution Rules`` di vincoli di
    # piattaforma che è esattamente la stessa deriva per cui questo budget
    # esiste. Un rifiuto di scrittura non sa su quale delle due sta premendo, e
    # premerebbe su entrambe. Lo strumento giusto lì è il review pass, che legge
    # e sceglie; il guard no. Gauge sì, rifiuto no.
    soul_budget_chars: int = Field(
        default=0,
        ge=0,
        validation_alias=AliasChoices("soulBudgetChars", "soul_budget_chars"),
        serialization_alias="soulBudgetChars",
    )
    # Ogni quanti run di Dream gira il review pass, quello il cui unico compito è
    # rimpicciolire il file invece di aggiungerci roba. Con ``interval_h`` al
    # default (2) dodici run vogliono dire al massimo un review pass al giorno.
    # ``ge=1`` e non ``ge=0``: un review pass ogni zero run non è una
    # configurazione, è una divisione per zero scritta a parole.
    #
    # E resta ``ge=1``, non ``ge=12``, pur essendo dodici il pavimento operativo
    # (v. ``command/builtin.py::_REVIEW_CADENCE_FLOOR``). Due ragioni. Un restore
    # deve poter riscrivere qualunque valore storico, e un ``le=``/``ge=`` nuovo su
    # un campo già spedito non è una restrizione innocua: un `config.json` con un
    # valore fuori range diventa illeggibile, ``loader._load_with_recovery`` prova
    # il `.bak` — stesso valore — e poi mette in quarantena il file ripartendo dai
    # default, provider e chiave API inclusi (v. ``GardenerConfig.clamp_raw``).
    # Alzare qui il minimo costerebbe la config di chi ha già `reviewEveryRuns: 1`
    # sul telefono, per una cadenza che al massimo spende token. Il pavimento vive
    # nel comando, dove c'è una persona a cui spiegarlo e una frase con cui
    # scavalcarlo.
    review_every_runs: int = Field(
        default=12,
        ge=1,
        validation_alias=AliasChoices("reviewEveryRuns", "review_every_runs"),
        serialization_alias="reviewEveryRuns",
    )

    def build_schedule(self) -> CronSchedule:
        """Build the runtime schedule from the configured interval."""
        return CronSchedule(kind="every", every_ms=self.interval_h * self._HOUR_MS)

    def describe_schedule(self) -> str:
        """Return a human-readable summary for logs and startup output."""
        hours = self.interval_h
        return f"every {hours}h"


# Tetti dei tre numeri del giardiniere. Stanno in costanti, e non solo nel ``le=``
# dei campi, perché il comando ``/gardener`` li nomina nei suoi rifiuti: un range
# scritto due volte diventa due range appena uno dei due cambia.
#
# Un giorno per i due in minuti. Il tetto non serve a proteggere da un numero
# "grande" ma da un numero *senza significato*: ``interval_min`` diventa un
# ``every_ms`` che arma una sveglia RTC, quindi 10**9 pianifica diciannove secoli
# in avanti — un job che non scatterà mai, e nessun errore da nessuna parte. Oltre
# le 24 ore la battuta non è più periodica, e "non guardare più" ha già il suo
# interruttore (``enabled``), che è anche l'unica forma reversibile di dirlo.
GARDENER_INTERVAL_MIN_MAX = 1440
# Stesso tetto e ragione diversa: ``idle_min`` è il silenzio richiesto *prima* di
# entrare. Oltre un giorno nessun progetto vivo lo raggiunge mai, quindi il valore
# non è una cadenza lenta — è uno spegnimento travestito, di quelli che si
# diagnosticano leggendo il codice.
GARDENER_IDLE_MIN_MAX = 1440
# Un anno per la distanza fra due passate sulla stessa materia. Più larga delle
# altre due perché qui un valore grande è una scelta legittima (un progetto lo si
# vuole rivedere a stagioni, non a ore); oltre l'anno però "distanza" ha smesso di
# voler dire distanza e vuol dire "mai", che di nuovo è ``enabled=False``.
GARDENER_DISTANCE_HOURS_MAX = 8760


class GardenerConfig(Base):
    """Il giardiniere: promuove il diario dei progetti in pagine, a mente fredda.

    Secondo lavoro periodico interno dopo Dream, e come lui **acceso di
    default**. La ragione è la stessa di Dream, e vale la pena scriverla
    perché questo è il primo che scrive dentro le cartelle *dell'utente* e non in
    un file derivato: senza righe di diario nuove il tick esce prima di qualunque
    chiamata al provider, quindi su un'installazione che non usa i progetti costa
    zero. E se si spegne, ``/gardener`` resta la strada a mano.
    """

    _MINUTE_MS = 60_000

    enabled: bool = True

    # Ogni quanto si *guarda*, non ogni quanto si lavora: un tick che non trova
    # nulla non spende niente. Mezz'ora è la scala dei tre orologi qui sotto —
    # guardare più spesso non anticipa niente, perché a decidere è il fermo.
    interval_min: int = Field(
        default=30,
        ge=1,
        le=GARDENER_INTERVAL_MIN_MAX,
        validation_alias=AliasChoices("intervalMin", "interval_min"),
        serialization_alias="intervalMin",
    )

    # Da quanto la conversazione di quel progetto deve essere zitta. Il
    # giardiniere lavora **a mente fredda**: entrare mentre si sta parlando
    # significa promuovere metà di un discorso, e riscrivere la mappa sotto le
    # mani di chi la sta leggendo.
    idle_min: int = Field(
        default=30,
        ge=0,
        le=GARDENER_IDLE_MIN_MAX,
        validation_alias=AliasChoices("idleMin", "idle_min"),
        serialization_alias="idleMin",
    )

    # Distanza minima fra due passate **sulla stessa materia**. È la lezione del
    # degrado del Dream scritta come numero: un secondo giro ravvicinato sullo
    # stesso argomento è quello che rimpasta invece di aggiungere. Per wiki e non
    # globale, perché il degrado è per materia.
    min_hours_between_passes: int = Field(
        default=6,
        ge=0,
        le=GARDENER_DISTANCE_HOURS_MAX,
        validation_alias=AliasChoices("minHoursBetweenPasses", "min_hours_between_passes"),
        serialization_alias="minHoursBetweenPasses",
    )

    # I tre campi che ``clamp_raw`` riporta dentro i tetti. Elencati per nome e non
    # dedotti da ``model_fields``: ``enabled`` non è un numero, e un domani un
    # quarto campo numerico potrebbe volere il rifiuto e non la clemenza.
    _CLAMPED_FIELDS = ("interval_min", "idle_min", "min_hours_between_passes")

    @classmethod
    def clamp_raw(cls, data: Any) -> Any:
        """Riporta dentro i tetti i numeri di un ``config.json`` scritto prima di loro.

        Serve perché aggiungere un ``le=`` a un campo già spedito non è una
        restrizione innocua: ``loader._load_with_recovery`` reagisce a un
        ``ValidationError`` provando il ``.bak`` — che ha lo stesso valore fuori
        range — e poi **mettendo in quarantena il file e ripartendo dai default**.
        Un tetto nuovo su ``intervalMin`` diventerebbe così la cancellazione del
        provider e della sua chiave, per un numero che al massimo pianificava una
        sveglia troppo in là. E soprattutto renderebbe irraggiungibile la via
        d'uscita: ``/gardener off`` passa da ``store.mutate``, che rilegge il file
        — quindi scriverebbe *sopra* una config appena azzerata.

        Si limita, non si ripiega sul default: il valore fuori range dice in che
        *direzione* andava la scelta di chi l'ha scritto (o l'accidente che l'ha
        prodotto), e riportarlo a 30 minuti farebbe lavorare il giardiniere più di
        quanto chiunque avesse chiesto. Il tetto è il minimo cambiamento che rende
        il file valido, e il log dice entrambi i numeri.

        Chiamata da ``AgentDefaults._clamp_gardener_clocks``, cioè **solo** sul
        dizionario grezzo che arriva dal file. Costruire ``GardenerConfig`` in
        codice con un valore fuori range resta un errore e alza: se la clemenza
        stesse su questa classe, il ``le=`` non potrebbe più rifiutare niente.
        """
        if not isinstance(data, dict):
            return data
        out = data
        for name in cls._CLAMPED_FIELDS:
            finfo = cls.model_fields[name]
            low, high = int(finfo.ge or 0), int(finfo.le or 0)
            aliases = getattr(finfo.validation_alias, "aliases", ())
            for key in (*aliases, name):
                if key not in out:
                    continue
                raw = out[key]
                if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                    # Non un numero: lo boccia la validazione del campo, e non è
                    # questo il posto dove indovinare cosa volesse dire.
                    break
                clamped = min(max(raw, low), high)
                if clamped != raw:
                    logger.warning(
                        "Config: gardener.{} was {}, outside {}..{}; clamped to {}",
                        key, raw, low, high, clamped,
                    )
                    out = {**out, key: clamped}
                break
        return out

    def build_schedule(self) -> CronSchedule:
        return CronSchedule(kind="every", every_ms=self.interval_min * self._MINUTE_MS)

    def describe_schedule(self) -> str:
        return (
            f"every {self.interval_min}min, on projects idle {self.idle_min}min "
            f"and not gardened for {self.min_hours_between_passes}h"
        )


class AgentDefaults(Base):
    """Default agent configuration."""

    model: str = ""
    # Tetto per singola risposta. Sui reasoning model il thinking pesa su questo
    # stesso budget: con 8192 un turno che pianifica a lungo lo consumava tutto
    # prima di dire qualcosa. 16384 lascia margine restando dentro la finestra
    # anche coi prompt più grossi osservati (~38k su 65536).
    max_tokens: int = 16384
    context_window_tokens: int = 65536
    context_block_limit: int | None = None
    temperature: float = 0.1
    # Esplicito invece di lasciare il default del provider: un reasoning model a
    # briglia sciolta consuma tutto il budget di output in ragionamento su un
    # compito aperto. "medium" limita il thinking senza appiattirlo.
    reasoning_effort: str | None = "medium"
    max_tool_iterations: int = 200
    # L'agente principale delega: tre slot reggono due lavori lunghi piu il
    # lavoro breve. Uno slot resta sempre riservato ai job quick (vedi
    # ``SubagentManager._check_capacity``), altrimenti i long-running saturano il
    # pool e non c'e piu modo di rispondere all'utente. Tre e non cinque perche
    # ogni slot e una richiesta LLM in volo da un telefono: oltre non e la CPU a
    # cedere ma il rate limit del provider e la batteria.
    #
    # Alzare questo default NON basta per chi aggiorna: ``loader.py`` serializza
    # il config *includendo i default*, quindi ogni installazione esistente porta
    # il vecchio valore scritto nel file. Se lo cambi, aggiungi una migrazione in
    # ``Config._migrate_by_version`` e alza ``CURRENT_CONFIG_VERSION``.
    max_concurrent_subagents: int = Field(default=3, ge=1)
    # Modalita orchestratore: l'agente principale carica il registry con lo scope
    # "orchestrator" invece di "core" — delega il lavoro pesante ai subagent e
    # perde i tool che gonfiano la sessione dell'utente (python_exec, scrittura,
    # patch, download, web, exec_session, search), tenendo solo lettura e
    # controllo. Acceso di default perche e il comportamento voluto; resta un
    # flag perche cambia in modo sostanziale cio che Jenny puo fare da sola e
    # l'utente gira su un solo telefono, senza altro modo per tornare indietro.
    orchestrator_mode: bool = Field(
        default=True,
        validation_alias=AliasChoices("orchestratorMode", "orchestrator_mode"),
        serialization_alias="orchestratorMode",
    )
    # Watchdog di stallo: oltre questa soglia senza progresso il subagent viene
    # marcato ``stalled``. Marcatura sola, mai cancellazione: rilanciare e una
    # decisione dell'utente o dell'orchestratore.
    subagent_stall_threshold_seconds: int = Field(default=180, ge=10)
    # Errori tool recuperabili che un subagent puo commettere prima di arrendersi.
    # Zero = il vecchio comportamento, in cui il primo risultato che iniziava per
    # "Error" uccideva il subagent: un ``offset`` indovinato male buttava via un
    # lavoro finito. La contabilita (consecutivi, totali, boundary di sicurezza)
    # sta in ``jenny/agent/tool_execution.py::ToolErrorBudget``.
    subagent_tool_error_budget: int = Field(default=3, ge=0)
    max_tool_result_chars: int = 16000
    provider_retry_mode: Literal["standard", "persistent"] = "standard"
    tool_hint_max_length: int = Field(default=40, ge=20, le=500)
    # Stringa vuota = auto: timezone del dispositivo su Android, altrimenti
    # UTC. Risolta una volta per load in ``loader._resolve_default_timezone``.
    timezone: str = ""
    bot_name: str = "Jenny"
    bot_icon: str = "✿"
    language: str = "it"
    tool_choice: Literal["auto", "any", "none", "required"] = Field(
        default="auto",
        validation_alias=AliasChoices("toolChoice", "tool_choice"),
    )
    disabled_skills: list[str] = Field(default_factory=list)
    session_ttl_minutes: int = Field(
        default=15,
        ge=0,
        validation_alias=AliasChoices("idleCompactAfterMinutes"),
        serialization_alias="idleCompactAfterMinutes",
    )
    # **L'interruttore di P4.** Con ``False`` (il default) la cronologia di un
    # progetto non viene mai archiviata per inattività: un progetto può stare
    # fermo tre settimane e riprendere dove era, ed è il suo mestiere. Con
    # ``True`` la conversazione di un progetto si compatta come quella personale,
    # perché la verità non sta più lì — sta nelle pagine.
    #
    # È una **manopola e non una rimozione del recinto**, e la differenza è
    # tutta nella prova: accendere P4 diventa reversibile con un'impostazione, e
    # la suite che pinna il recinto resta verde cambiando significato — da muro a
    # descrizione della manopola spenta.
    #
    # Cosa si perde accendendolo: l'agente non ha più in contesto quel che è
    # stato *detto*, solo quel che è stato *scritto*. Il transcript visibile
    # (``.jenny/webui/``) non viene toccato, quindi una persona può ancora
    # rileggere — l'amnesia è dell'agente, non del registro.
    compact_projects_when_idle: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "compactProjectsWhenIdle", "compact_projects_when_idle"
        ),
        serialization_alias="compactProjectsWhenIdle",
    )
    max_messages: int = Field(default=120, ge=0)
    consolidation_ratio: float = Field(default=0.5, ge=0.1, le=0.95)
    dream: DreamConfig = Field(default_factory=DreamConfig)
    gardener: GardenerConfig = Field(default_factory=GardenerConfig)
    model_preset: str | None = Field(
        default=None,
        validation_alias=AliasChoices("modelPreset", "model_preset"),
    )
    # L'umore della mascotte: una richiesta piccola al modello dopo ogni turno
    # WebUI (``jenny/session/mascot_mood.py``). Letti al momento della chiamata,
    # quindi un cambio vale dal turno dopo senza riavvio. Il preset presta solo
    # il ``model``: il provider resta quello del turno. Acceso di default: era
    # in standby finche' le espressioni non erano disegnate (08/09/2026), e
    # adesso lo sono.
    mascot_mood: bool = Field(
        default=True,
        validation_alias=AliasChoices("mascotMood", "mascot_mood"),
        serialization_alias="mascotMood",
    )
    mascot_mood_model_preset: str | None = Field(
        default=None,
        validation_alias=AliasChoices("mascotMoodModelPreset", "mascot_mood_model_preset"),
        serialization_alias="mascotMoodModelPreset",
    )

    @model_validator(mode="before")
    @classmethod
    def _clamp_gardener_clocks(cls, data: Any) -> Any:
        """Fa entrare i tre numeri del giardiniere nei tetti aggiunti dopo di loro.

        Il punto di chiamata è qui e non su ``GardenerConfig`` di proposito: qui il
        valore è ancora il dizionario grezzo del file, mentre una clemenza montata
        sulla classe si applicherebbe anche a ``GardenerConfig(interval_min=10**9)``
        e il ``le=`` non potrebbe più rifiutare nulla. Il perché il file vada
        salvato invece che bocciato sta in :meth:`GardenerConfig.clamp_raw`.
        """
        if not isinstance(data, dict):
            return data
        raw = data.get("gardener")
        if not isinstance(raw, dict):
            return data
        clamped = GardenerConfig.clamp_raw(raw)
        if clamped is not raw:
            data = {**data, "gardener": clamped}
        return data


class AgentsConfig(Base):
    """Agent configuration."""

    defaults: AgentDefaults = Field(default_factory=AgentDefaults)


class ProviderConfig(Base):
    """LLM provider configured by the user."""

    name: str
    format: Literal["openai_compat", "anthropic"]
    api_key: str | None = Field(default=None, repr=False)
    api_base: str | None = None
    # Percorso di un PEM di cui fidarsi *in piu'* del bundle di default, per chi
    # ha un server con certificato firmato da una CA propria (lo store dei
    # certificati di Android non c'entra: il runtime Python nell'APK ha il suo).
    # Relativo = dentro il workspace. Non e' un segreto, a differenza di
    # ``api_key``, quindi niente ``repr=False``: e' un percorso a un certificato
    # pubblico. Semantica e casi d'errore: ``jenny/providers/tls.py``.
    ca_bundle: str | None = None
    extra_headers: dict[str, str] | None = None
    extra_body: dict[str, Any] | None = None
    extra_query: dict[str, str] | None = None
    api_type: Literal["auto", "chat_completions", "responses"] = "auto"


class ProvidersConfig(Base):
    """User-defined LLM providers."""

    providers: list[ProviderConfig] = Field(default_factory=list)
    default: str | None = None


class HeartbeatConfig(Base):
    """Heartbeat service configuration (now backed by cron)."""

    enabled: bool = True
    interval_s: int = Field(default=30 * 60, ge=1)  # 30 minutes
    keep_recent_messages: int = 8


class GatewayConfig(Base):
    """Gateway/server configuration."""

    host: str = "127.0.0.1"  # Safer default: local-only bind.
    port: int = 18790
    heartbeat: HeartbeatConfig = Field(default_factory=HeartbeatConfig)


class ToolsConfig(Base):
    """Tools configuration.

    I tipi dei sub-config dei tool sono importati direttamente da
    ``config.tool_schemas`` (modulo leggero, nessun ciclo): niente più
    forward-ref / ``model_rebuild`` / risoluzione lazy.
    """

    android_web: AndroidWebToolsConfig = Field(
        default_factory=AndroidWebToolsConfig,
        validation_alias=AliasChoices("androidWeb", "android_web"),
    )
    python_exec: PythonExecConfig = Field(
        default_factory=PythonExecConfig,
        validation_alias=AliasChoices("pythonExec", "python_exec"),
    )
    file: FileToolsConfig = Field(default_factory=FileToolsConfig)
    location: LocationConfig = Field(default_factory=LocationConfig)
    my: MyToolConfig = Field(default_factory=MyToolConfig)
    introspect: IntrospectToolConfig = Field(default_factory=IntrospectToolConfig)
    diagnostics: DiagnosticsToolConfig = Field(default_factory=DiagnosticsToolConfig)
    ssh: SshConfig = Field(default_factory=SshConfig)
    # NB: canonical home = ``Config.security`` (SecurityConfig). Questo campo
    # resta su ToolsConfig come **mirror** sincronizzato (il tool-layer lo legge
    # via ``ctx.config.restrict_to_workspace``); il validator di ``Config`` lo
    # tiene allineato a ``security``. Non impostarlo a mano: usare ``security``.
    restrict_to_workspace: bool = True


class SecurityConfig(Base):
    """Policy di sicurezza a livello top-level (fonte canonica).

    ``restrict_to_workspace``: mantiene l'accesso dei tool dentro il workspace.
    ``ssrf_whitelist``: CIDR esentati dal blocco SSRF (es. ``["100.64.0.0/10"]``
    per Tailscale). Retro-compat: se un vecchio ``config.json`` porta questi campi
    sotto ``tools`` e non c'è ``security``, il validator di ``Config`` li migra qui.
    """

    restrict_to_workspace: bool = True
    ssrf_whitelist: list[str] = Field(default_factory=list)


# Modalità ammesse per ``PowerConfig.keep_awake``. Fuori da queste tre si
# ricade su ``DEFAULT_KEEP_AWAKE``: un valore scritto male non deve impedire
# l'avvio del gateway.
KEEP_AWAKE_MODES = ("off", "turns", "always")
DEFAULT_KEEP_AWAKE = "turns"


class PowerConfig(Base):
    """Gestione dell'alimentazione: wakelock e risvegli programmati (anti-doze).

    Perché esiste: un foreground service **non** tiene un wakelock sulla CPU.
    Tiene vivo il processo, non il processore. A schermo spento il device entra
    in suspend e i timer asyncio non scattano: il loop del gateway resta fermo
    ovunque si trovi, i cron slittano di minuti o ore e da fuori sembra che
    Jenny si sia piantata. Solo un ``PARTIAL_WAKE_LOCK`` impedisce la sospensione
    della CPU, e i risvegli puntuali richiedono un alarm dell'OS.

    ``keep_awake`` sceglie quanto in là spingersi:

    * ``"turns"`` (default) — il wakelock viene preso **solo** attorno al lavoro
      vero (un turno dell'agente, un job cron, una sessione SSH) e rilasciato
      subito dopo. È il compromesso: la CPU resta sveglia quando serve, il
      telefono dorme il resto del tempo.
    * ``"always"`` — wakelock tenuto per tutta la vita del servizio. Da usare a
      telefono in carica: la batteria non regge un lock permanente.
    * ``"off"`` — comportamento pre-0.6.6, nessun wakelock. Resta disponibile
      come via di fuga se il lock dovesse creare problemi su un device.

    ``wakelock_rotate_min`` ruota il lock (release + acquire) per non farlo
    invecchiare indefinitamente; 0 disattiva la rotazione. Il watchdog misura il
    ritardo reale del loop e ``gap_warning_min`` è la soglia oltre la quale un
    buco di attività va segnalato invece di passare inosservato.
    """

    keep_awake: str = Field(
        default=DEFAULT_KEEP_AWAKE,
        validation_alias=AliasChoices("keepAwake", "keep_awake"),
        serialization_alias="keepAwake",
    )
    # 0 = nessuna rotazione. Il tetto a 4 ore evita che una config assurda
    # trasformi la "rotazione" in "mai".
    wakelock_rotate_min: int = Field(
        default=50,
        ge=0,
        le=240,
        validation_alias=AliasChoices("wakelockRotateMin", "wakelock_rotate_min"),
        serialization_alias="wakelockRotateMin",
    )
    watchdog_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("watchdogEnabled", "watchdog_enabled"),
        serialization_alias="watchdogEnabled",
    )
    watchdog_interval_min: int = Field(
        default=15,
        ge=5,
        le=120,
        validation_alias=AliasChoices("watchdogIntervalMin", "watchdog_interval_min"),
        serialization_alias="watchdogIntervalMin",
    )
    alarm_driven_cron: bool = Field(
        default=True,
        validation_alias=AliasChoices("alarmDrivenCron", "alarm_driven_cron"),
        serialization_alias="alarmDrivenCron",
    )
    alarm_clock_fallback: bool = Field(
        default=True,
        validation_alias=AliasChoices("alarmClockFallback", "alarm_clock_fallback"),
        serialization_alias="alarmClockFallback",
    )
    gap_warning_min: int = Field(
        default=60,
        ge=5,
        validation_alias=AliasChoices("gapWarningMin", "gap_warning_min"),
        serialization_alias="gapWarningMin",
    )

    @model_validator(mode="before")
    @classmethod
    def _coerce_keep_awake(cls, data: Any) -> Any:
        """Normalizza ``keep_awake`` e ricade su ``"turns"`` se non riconosciuto.

        Deliberatamente in ``mode="before"`` e non un ``field_validator``: qui il
        valore è ancora quello grezzo del file, quindi si intercetta anche un
        tipo sbagliato (``true``, ``null``, un numero) che la validazione del
        campo boccerebbe con un'eccezione. Un ``keep_awake`` scritto male è un
        refuso, non un motivo per non far partire il gateway.
        """
        if not isinstance(data, dict):
            return data
        for key in ("keepAwake", "keep_awake"):
            if key not in data:
                continue
            raw = data[key]
            mode = raw.strip().lower() if isinstance(raw, str) else ""
            if mode not in KEEP_AWAKE_MODES:
                logger.warning(
                    "Invalid power.keepAwake value {!r}; falling back to {!r}",
                    raw,
                    DEFAULT_KEEP_AWAKE,
                )
                mode = DEFAULT_KEEP_AWAKE
            if mode != raw:
                data = {**data, key: mode}
            break
        return data


class WikiConfig(Base):
    """Wiki configuration."""

    enabled: bool = True
    wikis_dir: str = "wikis"  # Relativo a workspace
    extensions: list[str] = Field(default_factory=lambda: [
        "fenced_code",
        "tables",
        "toc",
        "wikilinks",
        "mermaid",
    ])


class WorkspaceConfig(Base):
    """Workspace file management configuration."""

    enabled: bool = True
    max_file_size: int = 1_000_000  # 1MB
    allow_delete: bool = True
    allow_write: bool = True


class AppsConfig(Base):
    """Jenny Apps configuration (workspace apps with typed actions)."""

    enabled: bool = True
    http_timeout_s: float = Field(default=20.0, ge=1.0, le=120.0)
    max_collection_bytes: int = 5_000_000


class SnapshotConfig(Base):
    """Configurazione del versioning locale del workspace (snapshot + backup).

    Gli snapshot sono creati automaticamente dal runtime (debounce su quiete,
    checkpoint pre-Dream, shutdown, safety giornaliero) senza coinvolgere
    l'LLM. ``pbkdf2_iterations`` governa la derivazione chiave del backup
    cifrato esportato.
    """

    enabled: bool = True
    scan_interval_minutes: int = Field(default=5, ge=1)
    quiet_minutes: int = Field(default=10, ge=1)
    daily_safety_snapshot: bool = True
    retention_recent: int = Field(default=20, ge=1)
    retention_thin_after_days: int = Field(default=30, ge=1)
    # Orizzonte massimo della storia in giorni (0 = per sempre). Gli ultimi
    # ``retention_recent`` snapshot restano comunque protetti dall'orizzonte.
    retention_max_age_days: int = Field(default=0, ge=0)
    # Il tetto rispecchia MAX_KDF_ITERATIONS del formato container (crypto.py).
    pbkdf2_iterations: int = Field(default=600_000, ge=100_000, le=10_000_000)
    # Unica fonte di verità: la costante del motore di snapshot (engine.py).
    exclude_globs: list[str] = Field(
        default_factory=lambda: list(DEFAULT_EXCLUDE_GLOBS)
    )


class UpdatesConfig(Base):
    """Controllo degli aggiornamenti in-app (manifest remoto + notifica in chat).

    ``enabled`` decide se il job periodico ``update_check`` viene registrato
    all'avvio **e** se ogni sua esecuzione fa qualcosa: il job registrato da un
    avvio precedente resta nello store del cron, quindi a spegnere davvero la
    rete è il controllo in ``CronDispatcher._run_update_check``.
    ``notify_in_chat`` decide invece se una versione nuova apre un messaggio in
    chat oppure resta solo visibile dove l'utente va a cercarla.

    Le ventiquattro ore di default non sono un compromesso di rete: sono la
    cadenza con cui ha senso *disturbare*. Il controllo costa una richiesta HTTP
    da qualche centinaio di byte, ma ogni suo esito positivo è un'interruzione.
    """

    enabled: bool = True
    # Unica fonte di verità: la costante di ``runtime/update_manifest.py``, un
    # modulo senza dipendenze proprio perché questo schema viene caricato da
    # ``config/bootstrap.py`` prima dell'event loop.
    manifest_url: str = DEFAULT_MANIFEST_URL
    check_interval_h: int = Field(default=24, ge=1, le=168)
    notify_in_chat: bool = True


class TelegramConfig(Base):
    """Configurazione del canale Telegram (bot personale).

    Stato derivato, nessuna enum persistita:
    disabled → token presente ma unpaired (``pairing_code`` attivo) → paired.
    Il ``pairing_code`` è persistito così il pairing sopravvive ai riavvii del
    processo (frequenti su Android) e viene azzerato al pairing riuscito.
    """

    enabled: bool = False
    bot_token: str | None = Field(default=None, repr=False)
    bot_username: str | None = None
    paired_chat_id: str | None = None
    paired_username: str | None = None
    pairing_code: str | None = Field(default=None, repr=False)
    poll_timeout_s: int = Field(default=50, ge=1, le=300)


class ModelPresetConfig(Base):
    """Named model preset configuration."""
    label: str | None = None
    provider: str | None = Field(default=None, validation_alias=AliasChoices("provider"))
    model: str | None = None
    max_tokens: int | None = None
    context_window_tokens: int | None = None
    temperature: float | None = None
    reasoning_effort: str | None = None


# Versione corrente dello schema del config. Alzala di uno ogni volta che
# aggiungi un ramo a ``Config._migrate_by_version``, mai altrimenti.
CURRENT_CONFIG_VERSION = 2

# Migrazioni gia annunciate in questo processo. Solo per il log: la migrazione
# resta idempotente e rigira a ogni parse finche il file non viene riscritto (lo
# fa ``store.persist_schema_migrations`` all'avvio), ma il config viene letto piu
# volte per boot e una riga per lettura e rumore, non informazione.
_ANNOUNCED_MIGRATIONS: set[int] = set()


class Config(BaseSettings):
    """Root configuration for jenny."""

    # Versione dello *schema* del file, non della app. Serve a una sola cosa:
    # distinguere "questo valore e una scelta dell'utente" da "questo valore e un
    # vecchio default rimasto scritto nel file". Senza il contatore la differenza
    # e indecidibile, perche ``loader.py`` serializza includendo i default: un
    # config scritto quando il default era X porta X per sempre, e alzare il
    # default nello schema non raggiunge nessuno di quelli che aggiornano.
    #
    # Assente (installazioni pre-versioning) => 0, cosi le migrazioni girano.
    # Dopo il parse viene sempre riportata a ``CURRENT_CONFIG_VERSION``, e la
    # prima scrittura ordinaria del config la persiste: da quel momento i valori
    # nel file *sono* scelte, e nessuna migrazione li tocca piu.
    config_version: int = Field(
        default=0,
        ge=0,
        validation_alias=AliasChoices("configVersion", "config_version"),
        serialization_alias="configVersion",
    )
    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    # Allegati non-immagine: di default vengono solo referenziati per path
    # (salvati in ``uploads/``) e letti on-demand dall'agente coi suoi tool,
    # senza iniettarne il testo nel contesto a ogni turno. Impostare a ``True``
    # per estrarre e inlinare subito il testo di PDF/documenti.
    extract_document_text: bool = False
    websocket: dict[str, Any] = Field(default_factory=dict)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    power: PowerConfig = Field(default_factory=PowerConfig)
    wiki: WikiConfig = Field(default_factory=WikiConfig)
    workspace: WorkspaceConfig = Field(default_factory=WorkspaceConfig)
    apps: AppsConfig = Field(default_factory=AppsConfig)
    snapshots: SnapshotConfig = Field(default_factory=SnapshotConfig)
    updates: UpdatesConfig = Field(default_factory=UpdatesConfig)
    model_presets: dict[str, ModelPresetConfig] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("modelPresets", "model_presets"),
    )

    @model_validator(mode="before")
    @classmethod
    def _migrate_by_version(cls, data: Any) -> Any:
        """Migrazioni una-tantum sui valori, guidate da ``config_version``.

        Ogni migrazione e condizionata *anche* sul valore vecchio esatto: chi ha
        gia il valore nuovo non viene toccato, e la migrazione resta idempotente
        se il file non fa in tempo a essere riscritto prima del boot successivo.

        Costo accettato consapevolmente: un utente che avesse scelto a mano
        esattamente il vecchio default viene comunque spostato, una volta sola e
        con un log a WARNING. Il contrario — lasciare spenta la concorrenza a
        tutti quelli che aggiornano — e peggio e silenzioso.
        """
        if not isinstance(data, dict):
            return data
        raw_version = data.get("configVersion", data.get("config_version", 0))
        try:
            version = int(raw_version)
        except (TypeError, ValueError):
            # Versione illeggibile (file toccato a mano): la trattiamo come 0 e la
            # riscriviamo sanificata, invece di far fallire la validazione del
            # campo e mandare in quarantena un config per il resto valido.
            version = 0
            data = {k: v for k, v in data.items() if k != "config_version"}
            data["configVersion"] = 0
        if version >= CURRENT_CONFIG_VERSION:
            return data

        # v1: ``maxConcurrentSubagents`` passa da 1 a 3. L'1 era il default di
        # quando i subagent erano fire-and-forget uno alla volta; con
        # l'orchestratore che delega tutto, un solo slot serializza il fan-out e
        # un job lungo blocca ogni altra richiesta dell'utente.
        if version < 1:
            agents = data.get("agents")
            if isinstance(agents, dict):
                defaults = agents.get("defaults")
                if isinstance(defaults, dict):
                    for key in ("maxConcurrentSubagents", "max_concurrent_subagents"):
                        if defaults.get(key) == 1:
                            new_value = AgentDefaults.model_fields[
                                "max_concurrent_subagents"
                            ].default
                            if 1 not in _ANNOUNCED_MIGRATIONS:
                                _ANNOUNCED_MIGRATIONS.add(1)
                                logger.warning(
                                    "Config migration v1: maxConcurrentSubagents 1 -> {} "
                                    "(the old default blocked subagent fan-out; set it back "
                                    "explicitly if you really want one at a time)",
                                    new_value,
                                )
                            defaults = {**defaults, key: new_value}
                            agents = {**agents, "defaults": defaults}
                            data = {**data, "agents": agents}
                            break

        # v2: nessun valore cambia. Il passo esiste perche' ``agents.defaults.atlas``
        # e ``wiki.defaultWiki`` sono **chiavi ritirate** (``loader.RETIRED_KEY_PATHS``):
        # il loader smette di conservarle, e alzare la versione fa riscrivere il
        # file una volta all'avvio (``store.persist_schema_migrations``), cosi'
        # cadono al primo boot e non alla prima impostazione che l'utente cambia.
        if version < 2 and 2 not in _ANNOUNCED_MIGRATIONS:
            _ANNOUNCED_MIGRATIONS.add(2)
            logger.info(
                "Config migration v2: retired keys are dropped on the next write "
                "(agents.defaults.atlas, wiki.defaultWiki)"
            )
        return data

    @model_validator(mode="after")
    def _stamp_config_version(self) -> "Config":
        """Porta la versione a quella corrente: le migrazioni sono state applicate.

        Non scrive nulla — la prima ``store.mutate()`` ordinaria persiste lo
        stamp insieme al resto, perche il dump include tutti i campi.
        """
        if self.config_version != CURRENT_CONFIG_VERSION:
            self.config_version = CURRENT_CONFIG_VERSION
        return self

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_security_fields(cls, data: Any) -> Any:
        """Retro-compat: sposta ``tools.{restrict_to_workspace,ssrf_whitelist}``
        legacy sotto ``security`` quando ``security`` non è dato esplicitamente."""
        if not isinstance(data, dict):
            return data
        tools = data.get("tools")
        if not isinstance(tools, dict):
            return data
        if "security" not in data:
            # Accetta sia snake_case sia l'alias camelCase (Base) presenti nei
            # config legacy: es. ``ssrf_whitelist`` o ``ssrfWhitelist``.
            aliases = {
                "restrict_to_workspace": ("restrict_to_workspace", "restrictToWorkspace"),
                "ssrf_whitelist": ("ssrf_whitelist", "ssrfWhitelist"),
            }
            migrated: dict[str, Any] = {}
            for field, keys in aliases.items():
                for key in keys:
                    if key in tools:
                        migrated[field] = tools[key]
                        break
            if migrated:
                data = {**data, "security": migrated}
        return data

    @model_validator(mode="after")
    def _sync_security_mirror(self) -> Config:
        """``security`` è canonico; ``tools`` ne è il mirror letto dal tool-layer."""
        self.tools.restrict_to_workspace = self.security.restrict_to_workspace
        return self

    @property
    def workspace_path(self) -> Path:
        """Get the fixed workspace path."""
        from jenny.config.paths import get_workspace_path
        return get_workspace_path()

    def get_active_provider(self) -> ProviderConfig:
        """Return the active provider config.

        Uses ``providers.default`` if set, otherwise the first provider in
        the list.  Raises ValueError if no provider is configured.
        """
        if self.providers.default:
            for p in self.providers.providers:
                if p.name == self.providers.default:
                    return p
        if self.providers.providers:
            return self.providers.providers[0]
        raise ValueError("No provider configured. Add one in settings or config.json.")
