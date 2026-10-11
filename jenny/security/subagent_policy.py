"""Politica per-lavoro dei subagent: cosa un subagent non tocca mai.

Un subagent riceve un compito scritto da un modello e, spesso, legge contenuto
non fidato (pagine web, download). Il tipo di agente (``agent_types.py``)
decide quali tool vede; questo modulo decide cosa quei tool **non** possono
raggiungere, qualunque sia il tipo. Lo stato vive in una ``ContextVar``
legata da ``SubagentManager._run_subagent`` per la durata del lavoro: Jenny
principale, Dream, il giardiniere e il battito non la legano mai, quindi per
loro ogni controllo di questo modulo è una no-op.

Classi di path, relative alla radice dell'installazione (non a quella di un
progetto: un subagent di quaderno legge anche l'installazione, in sola lettura,
vedi ``filesystem._installation_read_root``):

* SECRET: mai letti né scritti da un subagent. ``config.json`` porta la chiave
  del provider, il segreto dei token della WebUI e il token del bot Telegram, ed
  è anche un pannello di controllo vivo (``ssrfWhitelist``, moduli di
  ``python_exec``) riletto a ogni spawn. Snapshot e staging dei backup ne
  contengono copie. Nessun lavoro delegato ne ha bisogno: a Jenny principale
  resta la lettura.
* SYSTEM_CODE: mai scritti da un subagent. ``agent/`` e ``ui/`` sono riestratti
  dal pacchetto a ogni avvio (``utils/helpers.py``): nessuna funzione li scrive,
  e una scrittura lì cambierebbe il prompt o il JS servito fino al riavvio.
* PRIVATE: leggerli marca il lavoro *private* (memoria, profilo, conversazioni).
* UNTRUSTED_SRC: leggerli marca il lavoro *untrusted* (download, output salvati
  dei tool, storie di altri subagent).

**Il taint del lavoro.** Oltre ai divieti, ogni lavoro tiene due bandierine:
*untrusted* (ha letto contenuto che non viene dall'utente: web, browser,
download, file scritti da un lavoro non fidato) e *private* (ha letto i dati
personali). Da sole non bloccano niente: servono a riconoscere l'incrocio delle
tre gambe della *lethal trifecta* — contenuto non fidato, dati privati, uscita
verso l'esterno — e a dire a Jenny, quando il risultato torna, che va letto come
dato e non come istruzione. Un lavoro nato da un compito già contaminato
(``taint_seed``) parte *untrusted*.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from urllib.parse import urlparse

# I file di configurazione nella radice del workspace: ``config.json``, il suo
# ``.bak``, i temporanei dello store e le copie in quarantena. Sono gli stessi
# nomi che il listing della WebUI nasconde
# (``webui/workspace_files._DEFAULT_INTERNAL_PATTERNS``) e che il
# ``FileProvider`` Kotlin rifiuta di condividere.
CONFIG_FILE_PATTERNS = ("config.json*", "config.corrupt-*.json")

# Cartelle (relative alla radice) che contengono copie di ``config.json``.
_SECRET_DIRS = (
    (".jenny", "snapshots"),
    (".jenny", "backup_staging"),
)

_SYSTEM_CODE_DIRS = ("agent", "ui")

# Leggerli dice qualcosa della persona: memoria, profilo, conversazioni, i
# thread della WebUI, i log dei job SSH, le storie e i digest dei subagent.
_PRIVATE_FILES = ("USER.md", "SOUL.md")
_PRIVATE_DIRS = (
    ("memory",),
    ("sessions",),
    (".jenny", "webui"),
    (".jenny", "ssh_jobs"),
    ("subagents",),
)

# Leggerli porta dentro testo che non ha scritto l'utente. ``subagents/`` è
# anche qui: le storie salvate contengono le pagine lette da altri lavori.
_UNTRUSTED_DIRS = (
    ("downloads",),
    (".jenny", "tool-results"),
    ("subagents",),
)

# Tool il cui risultato riuscito marca il lavoro. ``download_file`` non c'è:
# il contenuto va su disco e non entra nel contesto; leggerlo da
# ``downloads/`` marca.
UNTRUSTED_TOOLS = frozenset({
    "web_search", "web_fetch",
    "browser_open", "browser_snapshot", "browser_do", "browser_read",
})
PRIVATE_TOOLS = frozenset({"get_location", "get_recent_logs"})

_MAX_SOURCES = 8


def is_config_file(path: str | Path, root: str | Path) -> bool:
    """*path* è uno dei file di configurazione nella radice *root*?

    Si guarda il nome **e** la cartella: un ``config.json`` di un progetto
    dell'utente resta un file come un altro.
    """
    try:
        resolved = Path(path).resolve()
        base = Path(root).resolve()
    except (OSError, RuntimeError):
        return False
    return resolved.parent == base and any(
        fnmatch(resolved.name, pattern) for pattern in CONFIG_FILE_PATTERNS
    )


class SubagentSecretError(PermissionError):
    """Un subagent ha provato a leggere o scrivere un file segreto.

    Il testo non contiene le frasi di ``WORKSPACE_VIOLATION_MARKERS``: non è un
    tentativo di uscire dal workspace, e non deve consumare l'unico errore di
    confine che chiude il lavoro (``tool_execution.py``).
    """

    def __init__(self, rel: str) -> None:
        super().__init__(
            f"'{rel}' holds the app's private configuration (provider keys, tokens); "
            "subagents never read or write it. Do not look for another way to it: "
            "say in your report what you needed and the main agent will handle it."
        )


class SubagentSystemCodeError(PermissionError):
    """Un subagent ha provato a scrivere i template o la WebUI di Jenny."""

    def __init__(self, rel: str) -> None:
        super().__init__(
            f"'{rel}' is part of Jenny's own prompts or interface, restored from the "
            "app package at every start; subagents do not write it. Put your output "
            "under your output directory instead."
        )


@dataclass(slots=True)
class SubagentRunPolicy:
    """Stato di un lavoro di subagent, uno per ``_run_subagent``.

    Mutabile e condiviso per riferimento: il contesto copiato sui worker di
    ``python_exec`` porta lo stesso oggetto, quindi una marcatura fatta da
    ``http_get`` dentro il codice arriva al lavoro. Il lock serve per lo stesso
    motivo: le marcature arrivano da un altro thread.
    """

    task_id: str
    install_root: Path
    untrusted: bool = False
    private: bool = False
    sources: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @classmethod
    def for_run(
        cls, task_id: str, install_root: Path, *, seed: tuple[str, ...] = ()
    ) -> SubagentRunPolicy:
        policy = cls(task_id=task_id, install_root=install_root)
        for source in seed:
            policy.mark_untrusted(source)
        return policy

    def _note(self, source: str) -> None:
        if source and source not in self.sources and len(self.sources) < _MAX_SOURCES:
            self.sources.append(source)

    def mark_untrusted(self, source: str) -> None:
        with self.lock:
            self.untrusted = True
            self._note(source)

    def mark_private(self, source: str) -> None:
        with self.lock:
            self.private = True

    @property
    def junction(self) -> bool:
        """Contenuto non fidato e dati privati nello stesso contesto."""
        return self.untrusted and self.private

    def taint_sources(self) -> tuple[str, ...]:
        """Le fonti non fidate, o ``()`` se il lavoro è pulito."""
        with self.lock:
            return tuple(self.sources) if self.untrusted else ()

    def relative(self, path: str | Path) -> tuple[str, ...] | None:
        """Parti di *path* relative alla radice dell'installazione, o None se fuori."""
        try:
            resolved = Path(path).resolve()
            root = self.install_root.resolve()
        except (OSError, RuntimeError):
            return None
        try:
            return resolved.relative_to(root).parts
        except ValueError:
            return None


_CURRENT: ContextVar[SubagentRunPolicy | None] = ContextVar(
    "jenny_subagent_policy", default=None
)


def current_subagent_policy() -> SubagentRunPolicy | None:
    return _CURRENT.get()


@contextmanager
def enter_subagent_policy(policy: SubagentRunPolicy | None) -> Iterator[None]:
    """Lega *policy* per la durata del blocco (no-op se ``None``)."""
    if policy is None:
        yield
        return
    token = _CURRENT.set(policy)
    try:
        yield
    finally:
        _CURRENT.reset(token)


def _is_secret(parts: tuple[str, ...]) -> bool:
    if not parts:
        return False
    if len(parts) == 1 and any(fnmatch(parts[0], p) for p in CONFIG_FILE_PATTERNS):
        return True
    return any(parts[: len(prefix)] == prefix for prefix in _SECRET_DIRS)


def _is_system_code(parts: tuple[str, ...]) -> bool:
    return bool(parts) and parts[0] in _SYSTEM_CODE_DIRS


def _under(parts: tuple[str, ...], prefixes: tuple[tuple[str, ...], ...]) -> bool:
    return any(parts[: len(prefix)] == prefix for prefix in prefixes)


def _project_relative(parts: tuple[str, ...]) -> tuple[str, ...]:
    """Le parti dentro un quaderno, se il path è in uno (``wikis/<q>/...``).

    Un quaderno ha la sua ``memory/`` e i suoi ``downloads/``: valgono come
    quelli dell'installazione. Si riconosce dalla posizione, senza I/O.
    """
    if len(parts) >= 3 and parts[0] == "wikis":
        return parts[2:]
    return ()


def _is_private(parts: tuple[str, ...]) -> bool:
    if len(parts) == 1 and parts[0] in _PRIVATE_FILES:
        return True
    inner = _project_relative(parts)
    return _under(parts, _PRIVATE_DIRS) or (bool(inner) and inner[0] == "memory")


def _is_untrusted_src(parts: tuple[str, ...]) -> bool:
    inner = _project_relative(parts)
    if _under(parts, _UNTRUSTED_DIRS) or (bool(inner) and inner[0] == "downloads"):
        return True
    # Gli output salvati dei tool stanno in ``.jenny/tool-results`` a qualunque
    # profondità (anche dentro un quaderno).
    return any(
        parts[i] == ".jenny" and parts[i + 1] == "tool-results" for i in range(len(parts) - 1)
    )


def _host(url: object) -> str:
    try:
        return urlparse(str(url)).hostname or ""
    except ValueError:
        return ""


def policy_after_tool(name: str, params: object) -> None:
    """Marca il lavoro dopo un tool riuscito; no-op fuori da un subagent."""
    policy = current_subagent_policy()
    if policy is None:
        return
    args = params if isinstance(params, dict) else {}
    if name in UNTRUSTED_TOOLS:
        host = _host(args.get("url")) if "url" in args else ""
        policy.mark_untrusted(f"{name}:{host}" if host else name)
    elif name in PRIVATE_TOOLS:
        policy.mark_private(name)


def mark_untrusted(source: str) -> None:
    """Marca il lavoro corrente; no-op fuori da un subagent."""
    policy = current_subagent_policy()
    if policy is not None:
        policy.mark_untrusted(source)


def is_secret_path(path: str | Path) -> bool:
    """True se un subagent è legato e *path* è SECRET per lui."""
    policy = current_subagent_policy()
    if policy is None:
        return False
    parts = policy.relative(path)
    return parts is not None and _is_secret(parts)


def check_read(path: str | Path) -> None:
    """Controlla una lettura di un subagent; no-op fuori da un subagent."""
    policy = current_subagent_policy()
    if policy is None:
        return
    parts = policy.relative(path)
    if parts is None:
        return
    if _is_secret(parts):
        raise SubagentSecretError("/".join(parts))
    rel = "/".join(parts)
    if _is_private(parts):
        policy.mark_private(f"file:{rel}")
    if _is_untrusted_src(parts):
        policy.mark_untrusted(f"file:{rel}")


def check_write(path: str | Path) -> None:
    """Controlla una scrittura di un subagent; no-op fuori da un subagent."""
    policy = current_subagent_policy()
    if policy is None:
        return
    parts = policy.relative(path)
    if parts is None:
        return
    if _is_secret(parts):
        raise SubagentSecretError("/".join(parts))
    if _is_system_code(parts):
        raise SubagentSystemCodeError("/".join(parts))
