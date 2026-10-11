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
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

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
    """Stato di un lavoro di subagent, uno per ``_run_subagent``."""

    task_id: str
    install_root: Path

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
    if parts is not None and _is_secret(parts):
        raise SubagentSecretError("/".join(parts))


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
