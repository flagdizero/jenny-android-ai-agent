"""Registry dei tipi di agente: prompt + allowlist di tool + default.

Un "tipo" e la sola cosa che distingue un subagent da un altro: il prompt di
ruolo, l'insieme di tool che puo vedere e i default di campionamento. Vive in un
modulo foglia (nessun import da ``jenny.agent.*``) perche lo leggono tre livelli
diversi — :class:`~jenny.agent.subagent_records.SubagentSpec` per validare,
:class:`~jenny.agent.subagent.SubagentManager` per costruire il registry dei
tool, e il tool ``spawn`` per pubblicarne l'enum al modello.

La separazione fra i tipi non e decorativa: ``researcher`` legge il web e NON
puo eseguire codice, ``writer`` scrive testo e NON ha rete. Chi ha letto pagine
non fidate non e lo stesso che poi esegue codice — e un confine di sicurezza, e
allargare una di queste due allowlist lo cancella.
"""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger

# Tipo di fallback: usato quando il chiamante non ne indica uno e quando un
# record vecchio su disco porta un tipo che non esiste piu.
DEFAULT_AGENT_TYPE = "operator"


class UnknownAgentTypeError(ValueError):
    """Tipo di agente inesistente, sollevata alla costruzione della spec.

    Il messaggio elenca i tipi validi: il chiamante tipico e un LLM che si e
    inventato il nome, e un errore senza l'elenco lo fa solo indovinare di
    nuovo.
    """

    def __init__(self, name: object) -> None:
        self.requested = name
        super().__init__(
            f"unknown agent_type {name!r}: valid types are "
            + ", ".join(AGENT_TYPE_NAMES)
        )


@dataclass(frozen=True, slots=True)
class AgentType:
    """Definizione di un tipo di agente.

    ``tools`` e l'allowlist per nome passata a ``ToolLoader.load(allow=...)``;
    ``None`` significa "tutto lo scope subagent" e non e la stessa cosa di un
    set vuoto. ``temperature``/``max_iterations``/``model`` sono default: se
    ``None`` vale quello del manager, e un valore esplicito passato allo spawn
    vince sempre su quello del tipo.

    ``requires`` sono i tool senza i quali il tipo non ha piu senso: se
    *nessuno* di questi e disponibile, lo spawn viene rifiutato invece di far
    partire un agente che improvvisera (vedi
    ``SubagentManager._check_capabilities``). Non e un secondo filtro — quel che
    il tipo puo vedere resta ``tools`` — ed e volutamente un sottoinsieme di
    ``tools``: non si puo pretendere cio che non si e nemmeno chiesto.
    ``None`` significa "nessun tool e indispensabile".

    ``scopes`` sono gli scope di ``Tool._scopes`` da cui il tipo puo pescare.
    Quasi tutti si accontentano del solo ``subagent``; un tipo che ne elenca di
    piu li carica tutti (vedi ``SubagentManager._build_tools``). Serve perche
    ``tools=None`` significa "tutto lo scope subagent": un tool tenuto fuori da
    quello scope non puo essere ereditato per distrazione da ``operator``, va
    concesso nominando il suo scope qui.
    """

    name: str
    tools: frozenset[str] | None
    temperature: float | None = None
    max_iterations: int | None = None
    model: str | None = None
    scopes: tuple[str, ...] = ("subagent",)
    requires: frozenset[str] | None = None

    @property
    def prompt_template(self) -> str:
        """Template del prompt di ruolo, incluso nel base ``subagent_system.md``."""
        return f"agent/types/{self.name}.md"


# Insieme dei tool del filesystem "scrivibile" (il modulo filesystem completo).
_FS_ALL = ("read_file", "write_file", "edit_file", "list_dir")
_FS_READ = ("read_file", "list_dir")

_AGENT_TYPES: tuple[AgentType, ...] = (
    # Raccoglie materiale dal web. Nessuna esecuzione di codice: e il tipo piu
    # esposto a contenuto non fidato, e un web fetch che finisce in python_exec
    # e il percorso piu corto da "pagina ostile" a "codice eseguito".
    AgentType(
        name="researcher",
        tools=frozenset((
            "web_search", "web_fetch",
            # Sessione interattiva: e' qui che serve, perche' e' qui che si
            # incontrano i muri dei cookie e i moduli di ricerca interni. Non
            # sta in ``requires``: fuori da Android non esiste, e un researcher
            # senza deve restare valido.
            "browser_open", "browser_snapshot", "browser_do", "browser_read", "browser_close",
            *_FS_READ, "write_file",
        )),
        temperature=0.2,
        max_iterations=60,
        requires=frozenset(("web_search", "web_fetch")),
    ),
    # Sintesi/wiki/docs da materiale gia raccolto: nessuna rete, ne diretta
    # (web_*) ne indiretta (download_file, python_exec). Chi scrive il
    # documento finale non deve poter andare a ripescare la fonte non fidata.
    AgentType(
        name="writer",
        tools=frozenset((*_FS_READ, "write_file", "apply_patch")),
        temperature=0.5,
        max_iterations=40,
        requires=frozenset(("read_file", "write_file")),
    ),
    # Scrive e modifica codice: filesystem completo, patch, esecuzione, sessioni
    # exec e log. ``find_files``/``grep`` sono inclusi deliberatamente (vedi
    # report di fase): senza ricerca un coder reimplementa grep in python_exec,
    # con output peggiore.
    AgentType(
        name="coder",
        tools=frozenset((
            *_FS_ALL,
            "apply_patch",
            "python_exec",
            "list_exec_sessions",
            "write_stdin",
            "get_recent_logs",
            "find_files",
            "grep",
        )),
        temperature=0.1,
        max_iterations=120,
        requires=frozenset(("read_file", "write_file")),
    ),
    # Calcolo, dati, grafici. Nessun tool del web; la rete c'e' comunque, con
    # ``http_get``/``http_post`` di ``python_exec`` (fino all'11/10/2026 questo
    # commento diceva «nessuna rete», e non era vero). Il confine che regge e'
    # la politica per lavoro di ``security.subagent_policy``, non l'allowlist.
    AgentType(
        name="analyst",
        tools=frozenset(("python_exec", *_FS_READ, "write_file")),
        temperature=0.1,
        max_iterations=60,
        requires=frozenset(("python_exec", "read_file")),
    ),
    # Amministra macchine remote via SSH. E l'unico tipo che esce dal telefono
    # verso una macchina terza, e per questo e anche il piu stretto:
    #
    # * niente ``web_search``/``web_fetch``/``download_file``. Vale la regola
    #   enunciata in cima a questo modulo — chi ha letto pagine non fidate non e
    #   chi poi esegue — ma qui la catena e piu corta e peggiore: da "pagina
    #   ostile" a "shell su un server di produzione" ci sarebbe un passo solo.
    # * niente ``python_exec``: l'esecuzione che questo tipo puo fare e gia
    #   quella remota, e sommarci quella locale rifarebbe l'operator.
    # * ``write_file`` c'e perche serve al lavoro: preparare i file da caricare
    #   con ``ssh_transfer`` e salvare i log scaricati.
    #
    # Gli ``ssh_*`` vivono nello scope ``remote``, non in ``subagent``: e cio che
    # impedisce a ``operator`` (``tools=None``, cioe tutto lo scope subagent) di
    # ereditarli e ritrovarsi web, esecuzione locale e shell remota insieme.
    AgentType(
        name="sysadmin",
        tools=frozenset((
            "ssh_hosts",
            "ssh_exec",
            "ssh_job",
            "ssh_transfer",
            *_FS_READ,
            "write_file",
        )),
        temperature=0.0,
        max_iterations=60,
        scopes=("subagent", "remote"),
        requires=frozenset(("ssh_hosts", "ssh_exec", "ssh_job", "ssh_transfer")),
    ),
    # Scrive e prova le app dell'utente: il tool ``app`` nella sua faccia di
    # costruttore (scope ``apps``), che scrive solo bozze e le prova sui dati veri;
    # a pubblicarle e' l'agente principale. Nato il 07/10/2026, per decisione
    # dell'utente: l'orchestratore scriveva file interi dentro argomenti JSON, e
    # restavano nella conversazione del quaderno.
    #
    # Niente web e niente ``python_exec``: il lettore si prova con ``app run``,
    # nel suo ambiente vero (``ctx.http``), e chi ha letto pagine non fidate non
    # e' chi scrive codice che gira da solo ogni mezz'ora. Lo scope a parte e'
    # cio' che tiene ``app`` fuori da ``operator``, come gli ``ssh_*``.
    AgentType(
        name="app-builder",
        tools=frozenset(("app", *_FS_READ, "grep")),
        temperature=0.1,
        max_iterations=40,
        scopes=("subagent", "apps"),
        requires=frozenset(("app",)),
    ),
    # Cambia un quaderno come l'ha chiesto l'utente: pagine, mappa e bacheca (il
    # tool ``board``, scope ``notebook``), cassetti compresi. Nato il 09/10/2026,
    # per decisione dell'utente: un quaderno si modifica tutto su richiesta, da
    # un editore solo, e il giardiniere resta a fare manutenzione e proposte. Le
    # sue scritture sulla bacheca valgono come dell'utente, quindi il
    # giardiniere non le disfa.
    #
    # Niente rete e niente ``python_exec``, come ``writer``: chi scrive il
    # quaderno non va a prendersi contenuto non fidato. Lo scope a parte tiene
    # ``board`` fuori da ``operator``, come ``app`` e gli ``ssh_*``.
    AgentType(
        name="notebook-editor",
        tools=frozenset(("board", *_FS_ALL, "apply_patch", "grep")),
        temperature=0.2,
        max_iterations=60,
        scopes=("subagent", "notebook"),
        requires=frozenset(("board",)),
    ),
    # Fallback per cio che non rientra negli altri: l'intero scope ``subagent``,
    # cioe esattamente il subagent generico che esisteva prima dei tipi. Resta
    # ``tools=None`` di proposito, ed e il motivo per cui i tool SSH stanno in
    # uno scope a parte: qualunque cosa entri nello scope ``subagent`` finisce
    # qui dentro automaticamente, senza che nessuno debba deciderlo.
    AgentType(name="operator", tools=None),
)

AGENT_TYPES: dict[str, AgentType] = {t.name: t for t in _AGENT_TYPES}
AGENT_TYPE_NAMES: tuple[str, ...] = tuple(AGENT_TYPES)


def get_agent_type(name: str | None) -> AgentType:
    """Risolve un tipo per nome. Solleva :class:`UnknownAgentTypeError`.

    Usata sui percorsi in cui il tipo arriva da fuori (tool ``spawn``, cron,
    codice): un nome sbagliato deve fallire subito e in modo leggibile.
    """
    key = name or DEFAULT_AGENT_TYPE
    try:
        return AGENT_TYPES[key]
    except KeyError:
        raise UnknownAgentTypeError(name) from None


def validate_agent_type(name: str | None) -> str:
    """Ritorna il nome del tipo se valido, altrimenti solleva."""
    return get_agent_type(name).name


def coerce_agent_type(name: object) -> str:
    """Come :func:`validate_agent_type` ma degrada su ``operator``.

    Solo per i record riletti da disco: un record scritto da una versione che
    aveva un tipo poi rimosso non deve rendere impossibile il rilancio del
    lavoro — meglio un operator generico che un lineage irrilanciabile.
    """
    if isinstance(name, str) and name in AGENT_TYPES:
        return name
    logger.warning(
        "Unknown agent_type {!r} in persisted subagent record; degrading to {}",
        name, DEFAULT_AGENT_TYPE,
    )
    return DEFAULT_AGENT_TYPE
