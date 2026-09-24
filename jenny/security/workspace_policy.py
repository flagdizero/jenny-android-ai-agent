"""Workspace path boundary helpers.

These helpers are application-level guards.  They make path decisions
consistent across tools, but they are not a replacement for an OS sandbox.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable


def _safe_expanduser(path: str | Path) -> Path:
    """Return an expanded path, gracefully handling platforms without HOME."""
    p = Path(path)
    try:
        return p.expanduser()
    except (RuntimeError, OSError):
        if p.is_absolute():
            return p.resolve()
        first = str(p)
        if first.startswith("~"):
            return Path(first[1:].lstrip("/") if first.startswith("~/") else "")
        return p


WORKSPACE_BOUNDARY_NOTE = (
    " (this is a hard policy boundary, not a transient failure; "
    "do not retry with alternative tools, and ask "
    "the user how to proceed if the resource is genuinely required)"
)


class WorkspaceBoundaryError(PermissionError):
    """Raised when a requested path escapes an allowed workspace boundary."""


class ReadOnlyTurnError(PermissionError):
    """Sollevata quando un turno in sola lettura prova a scrivere.

    Distinta da :class:`WorkspaceBoundaryError` perche' la risposta giusta e'
    diversa: un confine si aggira scrivendo *altrove*, la sola lettura no, e
    dire "outside allowed directory" manderebbe il modello a cercare un percorso
    consentito che non esiste. Qui la strada e' descrivere la modifica invece di
    eseguirla.
    """

    def __init__(self, detail: str = "") -> None:
        super().__init__(
            "This conversation is read-only: nothing on the device can be changed "
            "(files, downloads, app data, memory, scheduled jobs, app updates). "
            "Do not look for another path or another tool — describe the change you "
            "would make instead, and tell the user to turn writing back on if they "
            "want it applied."
            + (f" ({detail})" if detail else "")
        )


class _Unrestricted:
    """Sentinella per l'opt-out ESPLICITO dal confine di workspace.

    ``resolve_allowed_path`` è fail-closed: ``allowed_root=None`` (senza
    allowlist di file) viene rifiutato. Chi vuole davvero accesso illimitato
    deve passare ``allowed_root=UNRESTRICTED`` — così "nessun confine" è una
    scelta deliberata e greppabile, non un default accidentale."""

    _instance: "_Unrestricted | None" = None

    def __new__(cls) -> "_Unrestricted":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "UNRESTRICTED"


UNRESTRICTED = _Unrestricted()


def _resolve_path(path: str | Path, workspace: str | Path | None = None, *, strict: bool = False) -> Path:
    """Resolve *path*, interpreting relative paths against *workspace* when set."""
    candidate = _safe_expanduser(path)
    if not candidate.is_absolute() and workspace is not None:
        candidate = _safe_expanduser(workspace) / candidate
    return candidate.resolve(strict=strict)


def _resolve_logical_path(path: str | Path, workspace: str | Path | None = None) -> Path:
    """Return an absolute normalized path without following symlinks."""
    candidate = _safe_expanduser(path)
    if not candidate.is_absolute() and workspace is not None:
        candidate = _safe_expanduser(workspace) / candidate
    return Path(os.path.abspath(candidate))


def _path_key(path: str | Path) -> str:
    return os.path.normcase(os.fspath(path))


# ---------------------------------------------------------------------------
# Cache della RADICE risolta
# ---------------------------------------------------------------------------
#
# ``resolve_allowed_path`` risolveva la radice consentita a ogni chiamata, e
# ``Path.resolve()`` passa da ``realpath``, cioè una ``lstat`` per OGNI
# componente del percorso (~43 syscall per una radice tipica sul device).
# Dentro un ``python_exec`` guardato ogni singola operazione su file passa di
# qui, quindi la stessa identica radice veniva ricalcolata centinaia di volte
# per esecuzione.
#
# In cache va SOLO la radice, mai il percorso da validare: la radice è una
# directory che l'app possiede e che non cambia mentre un exec gira, il percorso
# è invece l'input non fidato del chiamante e va risolto ogni volta.
#
# Invalidazione — l'unica che serve, e va detta per intero: la voce può
# diventare stantia solo se la radice viene ricreata puntando altrove (p.es.
# sostituita da un symlink) mentre il processo vive. ``invalidate_root_cache()``
# è il gancio, e lo chiama ``python_exec.PythonNamespace._enter_guard``
# all'ingresso di ogni esecuzione guardata: dentro un exec la radice non può
# cambiare, quindi la finestra di staleness è al massimo un exec.
_ROOT_RESOLVE_CACHE: dict[str, Path] = {}
_ROOT_RESOLVE_CACHE_MAX = 256


def invalidate_root_cache() -> None:
    """Svuota la cache delle radici risolte (vedi ``_ROOT_RESOLVE_CACHE``)."""
    _ROOT_RESOLVE_CACHE.clear()


def _resolved_root(root: str | Path) -> Path:
    """Radice risolta, memoizzata. Vedi ``_ROOT_RESOLVE_CACHE``."""
    key = _path_key(root)
    cached = _ROOT_RESOLVE_CACHE.get(key)
    if cached is not None:
        return cached
    resolved = _safe_expanduser(root).resolve(strict=False)
    if len(_ROOT_RESOLVE_CACHE) >= _ROOT_RESOLVE_CACHE_MAX:
        # Limite di crescita: la cache è un'ottimizzazione, non uno stato.
        _ROOT_RESOLVE_CACHE.clear()
    _ROOT_RESOLVE_CACHE[key] = resolved
    return resolved


def _is_path_within(path: str | Path, root: str | Path, *, path_resolved: bool = False) -> bool:
    """Return True when *path* resolves to *root* or a descendant of *root*.

    ``path_resolved=True`` dichiara che *path* è GIÀ il risultato di una
    ``Path.resolve()``: rifarla costerebbe un secondo giro di ``realpath`` sullo
    stesso percorso senza cambiarne il valore (``resolve`` è idempotente).
    """
    try:
        if path_resolved:
            resolved_path = path if isinstance(path, Path) else Path(path)
        else:
            resolved_path = _safe_expanduser(path).resolve(strict=False)
        resolved_root = _resolved_root(root)
        resolved_path.relative_to(resolved_root)
        return True
    except (OSError, RuntimeError, TypeError, ValueError):
        return False


def is_path_within(path: str | Path, root: str | Path, *, path_resolved: bool = False) -> bool:
    """*path*, risolto, e' *root* o sta sotto *root* (anch'essa risolta)?

    La versione pubblica di :func:`_is_path_within`, per chi controlla un
    confine fuori dalla policy dei tool: le rotte della WebUI, la wiki, la
    provenienza. Fino al 24/09/2026 ognuno scriveva a mano
    ``x.resolve().relative_to(root.resolve())``, con eccezioni catturate a
    caso — un ``OSError`` (un loop di symlink) usciva come 500 da una rotta e
    come ``False`` da un'altra.

    A differenza di quella privata **non** usa la cache delle radici: le radici
    di chi la chiama (le cartelle di una wiki, di un progetto) si creano e
    spariscono a runtime, e la cache si invalida solo all'ingresso di
    ``python_exec``. ``path_resolved=True`` evita di risolvere due volte un
    percorso gia' passato da ``resolve()``. Qualunque errore di risoluzione e'
    un no: nel dubbio si sta fuori.
    """
    try:
        resolved_path = (
            (path if isinstance(path, Path) else Path(path))
            if path_resolved
            else _safe_expanduser(path).resolve(strict=False)
        )
        resolved_root = _safe_expanduser(root).resolve(strict=False)
        resolved_path.relative_to(resolved_root)
        return True
    except (OSError, RuntimeError, TypeError, ValueError):
        return False


def _is_path_allowed(
    path: str | Path, roots: Iterable[str | Path], *, path_resolved: bool = False
) -> bool:
    """Return True when *path* is inside any allowed root."""
    return any(_is_path_within(path, root, path_resolved=path_resolved) for root in roots)


def _is_path_exactly_allowed(
    logical_path: Path,
    resolved_path: Path,
    files: Iterable[str | Path],
) -> bool:
    """Return True when *path* resolves exactly to one of the allowed files."""
    logical_key = _path_key(logical_path)
    if _path_key(resolved_path) != logical_key:
        return False
    for file in files:
        try:
            allowed_file = _resolve_logical_path(file)
        except (OSError, RuntimeError, TypeError, ValueError):
            continue
        if _path_key(allowed_file) == logical_key:
            return True
    return False


def resolve_allowed_path(
    path: str | Path,
    *,
    workspace: str | Path | None = None,
    allowed_root: "str | Path | _Unrestricted | None" = None,
    extra_allowed_roots: Iterable[str | Path] | None = None,
    extra_allowed_files: Iterable[str | Path] | None = None,
    strict: bool = False,
) -> Path:
    """Resolve a path and enforce containment in allowed roots.

    Fail-closed: se non è dato né ``allowed_root`` né un'allowlist di file
    esatti (``extra_allowed_files``), il percorso è RIFIUTATO. Per l'accesso
    deliberatamente illimitato passare ``allowed_root=UNRESTRICTED``.
    """
    resolved = _resolve_path(path, workspace, strict=False)
    files = list(extra_allowed_files or [])
    if allowed_root is UNRESTRICTED:
        # Opt-out esplicito: nessun confine.
        return _resolve_path(path, workspace, strict=strict) if strict else resolved
    if allowed_root is None and not files:
        # FAIL-CLOSED: nessun confine e nessuna allowlist → deny.
        raise WorkspaceBoundaryError(
            f"Path {path} rejected: no workspace boundary configured "
            "(pass allowed_root=<dir> or the UNRESTRICTED sentinel to opt out)"
            + WORKSPACE_BOUNDARY_NOTE
        )

    roots = []
    if allowed_root is not None:
        roots.append(allowed_root)
    roots.extend(extra_allowed_roots or [])
    exact_allowed = bool(files) and _is_path_exactly_allowed(
        _resolve_logical_path(path, workspace),
        resolved,
        files,
    )
    # `resolved` viene già da `_resolve_path(..., strict=False)`, cioè da una
    # `Path.resolve()`: dichiararlo evita un secondo `realpath` identico.
    if not _is_path_allowed(resolved, roots, path_resolved=True) and not exact_allowed:
        boundary = _safe_expanduser(allowed_root) if allowed_root is not None else "allowed files"
        raise WorkspaceBoundaryError(
            f"Path {path} is outside allowed directory {boundary}"
            + WORKSPACE_BOUNDARY_NOTE
        )
    if strict:
        return _resolve_path(path, workspace, strict=True)
    return resolved
