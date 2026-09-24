"""Path utilities: abbreviation and atomic writes."""

from __future__ import annotations

import contextlib
import logging
import os
import re
import uuid
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def abbreviate_path(path: str | Path, max_len: int = 40) -> str:
    """Abbreviate a file path or URL, preserving basename and key directories.

    Strategy:
    1. Return as-is if short enough
    2. Replace home directory with ~/
    3. From right, keep basename + parent dirs until budget exhausted
    4. Prefix with …/
    """
    path_str = str(path)
    if not path_str:
        return path_str

    # Handle URLs: preserve scheme://domain + filename
    if re.match(r"https?://", path_str):
        return _abbreviate_url(path_str, max_len)

    # Normalize separators to /
    normalized = path_str.replace("\\", "/")

    # Replace home directory (may raise on Android if no home is available)
    try:
        home = os.path.expanduser("~").replace("\\", "/")
    except (RuntimeError, OSError):
        home = ""
    if home:
        if normalized.startswith(home + "/"):
            normalized = "~" + normalized[len(home):]
        elif normalized == home:
            normalized = "~"

    # Return early only after normalization and home replacement
    if len(normalized) <= max_len:
        return normalized

    # Split into segments
    parts = normalized.rstrip("/").split("/")
    if len(parts) <= 1:
        return normalized[:max_len - 1] + "\u2026"

    # Always keep the basename
    basename = parts[-1]
    # Budget: max_len minus "…/" prefix (2 chars) minus "/" separator minus basename
    budget = max_len - len(basename) - 3  # -3 for "…/" + final "/"

    # Walk backwards from parent, collecting segments
    kept: list[str] = []
    for seg in reversed(parts[:-1]):
        needed = len(seg) + 1  # segment + "/"
        if not kept and needed <= budget:
            kept.append(seg)
            budget -= needed
        elif kept:
            needed_with_sep = len(seg) + 1
            if needed_with_sep <= budget:
                kept.append(seg)
                budget -= needed_with_sep
            else:
                break
        else:
            break

    kept.reverse()
    if kept:
        return "\u2026/" + "/".join(kept) + "/" + basename
    return "\u2026/" + basename


def atomic_write(
    path: Path,
    content: str | bytes,
    *,
    fsync_file: bool = True,
    fsync_dir: bool = True,
    chmod: int | None = None,
) -> None:
    """Atomically write content to path.

    Uses a temp file next to the target, flushes and fsyncs the file (unless
    *fsync_file* is disabled), then atomically replaces the target. On Android,
    fsync on directories may fail; failures are tolerated.

    The temp file carries a per-write unique suffix (uuid) so two concurrent
    writers targeting the same path never clobber each other's temp file; the
    final ``os.replace`` stays atomic and any orphaned temp is cleaned up.

    *chmod* applica i permessi al file temporaneo **prima** della rename, non
    dopo: il rovescio — scrivere, rinominare, poi ``chmod`` — lascia una finestra
    in cui il file definitivo esiste con i permessi di default. È il motivo per
    cui i due scrittori SSH (``known_hosts`` e il sidecar ``.pub``) avevano una
    copia a mano di questa funzione: il gotcha sulle scritture atomiche dice di
    aggiungere un argomento qui invece di scriverne una sesta copia, e questo è
    l'argomento. Quelle copie non facevano ``fsync``, e una usava un nome
    temporaneo fisso — cioè la collisione fra scrittori concorrenti che il
    suffisso uuid qui sopra esiste per evitare.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    mode = "wb" if isinstance(content, bytes) else "w"
    encoding = None if isinstance(content, bytes) else "utf-8"

    tmp_path = path.with_suffix(f"{path.suffix}.{uuid.uuid4().hex}.tmp")
    try:
        with open(tmp_path, mode, encoding=encoding) as f:
            f.write(content)
            if fsync_file:
                f.flush()
                os.fsync(f.fileno())
        if chmod is not None:
            os.chmod(tmp_path, chmod)
        os.replace(tmp_path, path)
        if fsync_dir:
            _fsync_dir(path.parent)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp_path.unlink(missing_ok=True)
        raise


def _fsync_dir(directory: Path) -> None:
    """Best-effort directory fsync (tolerates Android FUSE quirks)."""
    try:
        dir_fd = os.open(str(directory), os.O_RDONLY)
    except OSError as e:
        logger.debug("dir fsync open failed for %s: %s", directory, e)
        return
    try:
        os.fsync(dir_fd)
    except OSError as e:
        logger.debug("dir fsync failed for %s: %s", directory, e)
    finally:
        os.close(dir_fd)


def _abbreviate_url(url: str, max_len: int = 40) -> str:
    """Abbreviate a URL keeping domain and filename."""
    if len(url) <= max_len:
        return url

    parsed = urlparse(url)
    domain = parsed.netloc  # e.g. "example.com"
    path_part = parsed.path  # e.g. "/api/v2/resource.json"

    # Extract filename from path
    segments = path_part.rstrip("/").split("/")
    basename = segments[-1] if segments else ""

    if not basename:
        # No filename, truncate URL
        return url[: max_len - 1] + "\u2026"

    budget = max_len - len(domain) - len(basename) - 4  # "…/" + "/"
    if budget < 0:
        trunc = max_len - len(domain) - 5  # "…/" + "/"
        return domain + "/\u2026/" + (basename[:trunc] if trunc > 0 else "")

    # Build abbreviated path
    kept: list[str] = []
    for seg in reversed(segments[:-1]):
        if len(seg) + 1 <= budget:
            kept.append(seg)
            budget -= len(seg) + 1
        else:
            break

    kept.reverse()
    if kept:
        return domain + "/\u2026/" + "/".join(kept) + "/" + basename
    return domain + "/\u2026/" + basename


def append_lines_durable(
    path: Path, lines: Iterable[str], *, tolerate_fsync_error: bool = False
) -> None:
    """Accoda *lines* a *path* (ognuna col suo a capo) e le porta su disco.

    ``flush`` e ``fsync`` prima di tornare: chi chiama lo fa perché subito dopo
    butta la copia in memoria, o gli originali — il diario della memoria, la
    coda di un progetto compattata, il transcript della WebUI. Era scritto tre
    volte. Con *tolerate_fsync_error* un ``fsync`` fallito non solleva (le righe
    sono comunque nel file, solo non ancora garantite su disco): è la scelta del
    transcript, che non deve far fallire un turno per questo. Gli altri due no,
    perché il loro chiamante cancella qualcosa subito dopo.
    """
    with open(path, "a", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            if not tolerate_fsync_error:
                raise

