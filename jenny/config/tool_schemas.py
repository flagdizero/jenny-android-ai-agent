"""Config dataclasses for the built-in tools.

Queste classi vivevano accanto alle implementazioni dei tool, costringendo
``ToolsConfig`` a forward-ref + risoluzione lazy (``_lazy_default`` +
``model_rebuild`` + ``try/except ImportError``) per evitare cicli d'import.

Qui stanno in un modulo LEGGERO che importa solo ``Base`` + ``Field`` (nessuna
dipendenza dai moduli tool, pesanti). Così sia ``config/schema.py`` sia i moduli
tool importano da qui *verso il basso* — niente cicli, niente rebuild a runtime,
e un fallimento d'import è un errore rumoroso allo startup invece di essere
silenziato.

I moduli tool re-esportano la loro classe (``from jenny.config.tool_schemas
import PythonExecConfig``) così gli import storici continuano a funzionare.
"""

from __future__ import annotations

from typing import Literal

from jenny.config_base import Base
from jenny.pydantic_compat import Field


class PythonExecConfig(Base):
    """Python exec tool configuration."""

    enable: bool = True
    timeout: int = Field(default=60, ge=0)  # 0 = no limit
    max_output_chars: int = Field(default=10_000, ge=1000, le=50_000)
    allowed_modules: list[str] = Field(
        default_factory=lambda: [
            "os", "sys", "pathlib", "json", "re", "math", "datetime",
            "collections", "itertools", "functools", "typing",
            "io", "shutil", "glob", "hashlib",
            # Raw URL/HTTP clients (httpx, urllib) intentionally NOT allowlisted:
            # `import httpx` or `from urllib.request import urlopen` would let
            # guarded code hit loopback/link-local/RFC1918 targets (SSRF) or read
            # local files via file:// (LFI), bypassing both the SSRF policy and
            # the workspace policy. Outbound HTTP stays available only via the
            # http_get/http_post helpers (which call validate_url_target).
            # Re-add "httpx"/"urllib" here explicitly to opt back into raw access.
            "base64", "asyncio", "csv",
            "platform", "time", "struct", "textwrap", "unicodedata",
            "html", "xml", "dataclasses", "enum", "uuid",
        ]
    )
    blocked_modules: list[str] = Field(
        default_factory=lambda: [
            "subprocess", "pty", "shlex",
            "multiprocessing", "ctypes", "socket", "signal",
            "termios", "tty", "grp", "pwd", "resource",
            "syslog", "curses", "readline", "_thread", "fcntl",
        ]
    )


class FileToolsConfig(Base):
    """Filesystem tools configuration."""

    enable: bool = True  # built-in file tools on by default
    # Grant read-only access to jenny's own source so the agent can
    # inspect the framework it runs on (never writable).
    expose_package_source: bool = True


class MyToolConfig(Base):
    """Self-inspection tool configuration."""

    enable: bool = True
    allow_set: bool = False


class AndroidWebSearchConfig(Base):
    """Android WebView-backed search configuration.

    I due interi portano i loro limiti qui e non nella rotta che li scrive:
    ``settings_api._parse_int`` li legge da ``model_fields``, quindi il
    messaggio di rifiuto — che nomina il range — non può divergere dal
    validatore. È l'invariante di ``test_settings_bounds_come_from_the_schema``.
    """

    search_engine: str = "bing"
    max_results: int = Field(default=5, ge=1, le=10)
    timeout: int = Field(default=30, ge=1, le=120)


class AndroidWebFetchConfig(Base):
    """Android WebView-backed fetch configuration."""

    max_chars: int = 50000


class AndroidWebBrowserConfig(Base):
    """Sessione di navigazione interattiva (tool ``browser_*``).

    Condivide l'interruttore di ``androidWeb.enable``: e' lo stesso WebView di
    search/fetch come categoria di rischio, quindi non ha senso poterla accendere
    a parte.

    ``max_snapshot_chars`` e' il tetto **autorevole** sullo snapshot, e non e' un
    dettaglio di comodo: nessuno tronca il risultato di un tool a valle
    (``context_governor`` taglia la cronologia, non la singola risposta), quindi
    quel che esce di qui entra intero nel turno. Il default viene da una misura
    del 29/08 su cinque pagine vere: una camminata piatta produce 4.600 caratteri
    su una SERP e 102.510 su una voce di Wikipedia, per cui il tetto da solo non
    basta e la riduzione (viewport prima, resto contato) sta nel motore in
    ``res/raw/browser_agent.js``.
    """

    timeout: int = 30
    max_snapshot_chars: int = 2500
    max_read_chars: int = 4000
    idle_close_s: int = 300


class AndroidWebToolsConfig(Base):
    """Android-only WebView web tools configuration."""

    enable: bool = True
    search: AndroidWebSearchConfig = Field(default_factory=AndroidWebSearchConfig)
    fetch: AndroidWebFetchConfig = Field(default_factory=AndroidWebFetchConfig)
    browser: AndroidWebBrowserConfig = Field(default_factory=AndroidWebBrowserConfig)


class LocationConfig(Base):
    """Configurazione della posizione del dispositivo (solo Android).

    Sorgente primaria: GPS del telefono via ``LocationBridge`` nativo (fix
    last-known, gratis, iniettato nel contesto a ogni turno). Il tool
    ``get_location`` on-demand forza un fix fresco solo quando l'agente passa
    ``precise=true``. La posizione condivisa via Telegram fa da override
    per-canale con validità ``telegram_ttl_s`` (poi anche Telegram ricade sul
    GPS live).

    ``enable`` è il toggle utente (default ON), comunque gattato dal permesso
    runtime Android ``ACCESS_FINE_LOCATION``: se il permesso manca il bridge
    ritorna sempre ``None`` e non viene iniettato nulla.
    """

    enable: bool = True
    # Validità di una posizione condivisa via Telegram prima del fallback a GPS.
    telegram_ttl_s: int = Field(default=3600, ge=60)  # 1 h
    # Timeout del fix fresco on-demand (precise=true).
    fresh_timeout_s: int = Field(default=15, ge=1, le=60)


class IntrospectToolConfig(Base):
    """Source introspection tool configuration."""

    enable: bool = True


class DiagnosticsToolConfig(Base):
    """Diagnostics tool configuration."""

    enable: bool = True


class SshHostConfig(Base):
    """Un host SSH registrato a mano dall'utente in Settings.

    ``alias`` è **l'unica cosa che il modello passa** ai tool SSH, e da lì viene
    la garanzia che conta: l'agente non può raggiungere un indirizzo arbitrario
    della rete, può solo nominare un alias che un umano ha già dichiarato qui.
    Nessuna credenziale entra mai negli argomenti o nei risultati dei tool.

    Host e username invece il modello li *vede*, elencati da ``ssh_hosts``:
    senza non potrebbe scegliere fra due alias né dire all'utente su quale
    macchina ha agito. Non sono segreti — i segreti sono la chiave privata, che
    vive fuori dal workspace, e la ``password`` qui sotto.

    Su quella password serve una precisazione, perché questo commento diceva il
    falso: nessun tool *SSH* la legge e nessun risultato di tool la contiene, ma
    sta in chiaro in ``config.json``, che è *dentro* il workspace. Qualunque tipo
    di agente con ``read_file`` può quindi leggerla — ``researcher`` compreso,
    che è l'unico che ingerisce pagine non fidate e ha anche ``web_fetch``. È la
    stessa esposizione delle chiavi API dei provider e del token Telegram, ed è
    esattamente ciò che la chiave privata evita stando fuori dal workspace.

    ``host_key_fingerprint`` è **solo per display** nella UI. L'enforcement vero
    è il file ``known_hosts`` accanto alla chiave (vedi
    ``jenny.config.paths.get_ssh_dir``): è quello che il backend legge, e senza
    una riga corrispondente la connessione viene rifiutata. Vale per entrambi i
    modi di autenticazione, e con ``auth="password"`` conta di più: senza
    impronta verificata la password andrebbe a chiunque risponda a quell'indirizzo.
    """

    alias: str
    host: str
    port: int = Field(default=22, ge=1, le=65535)
    username: str
    # Mostrata al modello da ``ssh_hosts``: serve a fargli scegliere l'alias
    # giusto quando ce n'è più di uno ("il NAS di casa", "il VPS del sito").
    description: str = ""
    host_key_fingerprint: str | None = None
    # Come si autentica questo host. Default ``key``: è il modo che non lascia
    # un segreto riutilizzabile nella config, quindi resta quello di partenza
    # anche ora che la password esiste.
    auth: Literal["key", "password"] = "key"
    # ``repr=False`` è la convenzione con cui questo repo tiene i segreti fuori
    # dai log (come ``api_key`` e ``bot_token`` in ``config/schema.py``): un
    # ``repr`` di questo oggetto finisce facilmente in una riga di log o in un
    # messaggio d'errore, e la password non deve poterci arrivare.
    password: str | None = Field(default=None, repr=False)
    # Dove vivono i log dei job lunghi lato server (vedi il tool ``ssh_job``).
    job_log_dir: str = "/tmp/jenny-jobs"


class SshConfig(Base):
    """Accesso SSH a macchine remote.

    Spento di default e senza host: sono due gate distinti e volutamente
    entrambi necessari, perché questa è la sola capacità di Jenny che agisce su
    una macchina che non è il telefono.

    ``command_timeout_s`` è basso di proposito, e il wakelock introdotto in
    0.6.6 (``jenny/runtime/power.py``, tag ``ssh``) non è un motivo per alzarlo:
    tiene accesa la CPU, non la connessione. Un comando lungo atteso su un canale
    SSH aperto muore comunque al primo passaggio wifi→dati mobili, o se il
    gateway viene riavviato. I comandi lunghi vanno passati a ``ssh_job``, che li
    stacca dalla connessione e li segue a delta.
    """

    enable: bool = False
    hosts: list[SshHostConfig] = Field(default_factory=list)
    connect_timeout_s: float = Field(default=15.0, ge=1.0, le=60.0)
    command_timeout_s: int = Field(default=60, ge=1, le=300)
    max_output_chars: int = Field(default=10_000, ge=1_000, le=50_000)
    # 0 = keepalive disattivato.
    keepalive_interval_s: int = Field(default=30, ge=0, le=300)
    idle_close_s: int = Field(default=300, ge=30)
    max_transfer_bytes: int = Field(default=50 * 1024 * 1024, ge=1024)
