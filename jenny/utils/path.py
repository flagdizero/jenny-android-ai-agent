"""Path utilities: abbreviation, atomic writes and the state files' safety nets."""

from __future__ import annotations

import contextlib
import logging
import os
import re
import threading
import uuid
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generic, TypeVar
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

    Con *chmod* il temporaneo **nasce** ``0600``, non ci arriva: ``chmod`` dopo
    la scrittura lasciava il contenuto — le chiavi di ``config.json`` — per un
    momento in un file coi permessi di default (``0644`` con umask ``022``). Il
    modo chiesto si applica a contenuto completo, prima della rename. Senza
    *chmod* il file nasce come lo farebbe ``open``, secondo l'umask.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    mode = "wb" if isinstance(content, bytes) else "w"
    encoding = None if isinstance(content, bytes) else "utf-8"

    tmp_path = path.with_suffix(f"{path.suffix}.{uuid.uuid4().hex}.tmp")
    create_mode = 0o600 if chmod is not None else 0o666

    def _opener(file: str, flags: int) -> int:
        # ``O_EXCL``: il nome e' unico (uuid), e un file che c'e' gia' non e' il
        # nostro — meglio un errore che scriverci dentro coi suoi permessi.
        return os.open(file, flags | os.O_EXCL, create_mode)

    try:
        with open(tmp_path, mode, encoding=encoding, opener=_opener) as f:
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

    Un append interrotto (processo ucciso) lascia l'ultima riga senza a capo, e
    la prima riga nuova le si attaccava: la riga unita non si legge più, e
    :func:`iter_jsonl` buttava via tutte e due. Qui la si chiude prima con un a
    capo: resta rotta solo lei, che la lettura salta. Era la guardia di
    ``apps/storage``, l'unico chiamante che l'aveva.
    """
    with open(path, "ab+") as f:
        f.seek(0, os.SEEK_END)
        torn = False
        if f.tell() > 0:
            f.seek(-1, os.SEEK_END)
            torn = f.read(1) != b"\n"
        payload = ("\n" if torn else "") + "".join(line + "\n" for line in lines)
        f.write(payload.encode("utf-8"))
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            if not tolerate_fsync_error:
                raise


def iter_jsonl(
    lines: Iterable[str], *, on_bad: Callable[[str], None] | None = None
) -> Iterator[dict[str, Any]]:
    """Gli oggetti JSON di un JSONL, una riga ciascuno, saltando quel che non lo è.

    Il lettore tollerante dei file a cui :func:`append_lines_durable` accoda:
    un'ultima riga tronca da un processo ucciso, o una riga che non è un oggetto,
    non invalidano le altre. Le righe vuote si saltano in silenzio; le altre
    scartate passano a *on_bad*, per chi le conta o le dice nel log. L'I/O resta
    a chi chiama (un file aperto, ``read_text().splitlines()``).
    """
    import json

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            record = None
        if isinstance(record, dict):
            yield record
        elif on_bad is not None:
            on_bad(line)


def rotate_valid_json_backup(
    path: Path, write: Callable[[Path, str], None] | None = None
) -> None:
    """Copia *path* in ``<nome>.bak``, ma solo se si legge come JSON.

    La rete di sicurezza dei file di stato (config, cron, bacheca), prima di ogni
    salvataggio: un salvataggio partito da un file già rotto distruggerebbe
    l'ultimo backup buono, che è esattamente quello che serve un istante dopo.
    Senza *path* non fa niente. *write* scrive la copia (di serie ``atomic_write``
    senza fsync della cartella; la config la vuole in ``600``); un suo
    ``OSError`` si propaga, e chi chiama decide se il salvataggio vero procede.
    """
    import json

    if not path.exists():
        return
    try:
        content = path.read_text(encoding="utf-8")
        json.loads(content)
    except (OSError, ValueError):
        return
    backup = backup_path(path)
    if write is None:
        atomic_write(backup, content, fsync_dir=False)
    else:
        write(backup, content)


def backup_path(path: Path) -> Path:
    """``<nome>.bak``, la copia che :func:`rotate_valid_json_backup` tiene accanto."""
    return path.with_name(path.name + ".bak")


def quarantine(path: Path) -> Path | None:
    """Sposta di lato un file di stato illeggibile; ``None`` se non ci riesce.

    Non lo cancella: serve a capire cosa è successo. Il nome dice quando,
    ``<nome>.corrupt-<AAAAMMGGThhmmssZ><estensione>`` (``config.corrupt-…json``),
    uguale per config, cron e bacheca; due nello stesso secondo prendono ``_1``,
    ``_2``…, perché ``os.replace`` non chiede il permesso e la seconda
    cancellerebbe in silenzio la prova della prima.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = path.with_name(f"{path.stem}.corrupt-{stamp}{path.suffix}")
    # Un tetto ai tentativi: un filesystem che dice «c'è» a ogni nome non deve
    # diventare un ciclo senza fine.
    for n in range(1, 100):
        if not target.exists():
            break
        target = path.with_name(f"{path.stem}.corrupt-{stamp}_{n}{path.suffix}")
    else:
        return None
    try:
        os.replace(path, target)
    except OSError as exc:
        # Il perché lo sa solo qui: il chiamante vede ``None`` e basta.
        logger.warning("could not set aside %s: %s", path, exc)
        return None
    return target


_T = TypeVar("_T")


@dataclass(frozen=True)
class Recovered(Generic[_T]):
    """Quel che :func:`load_with_recovery` ha letto, e da dove.

    *recovered_from* è ``None`` per un file buono o assente (*value* ``None``),
    ``"backup"`` quando *value* viene dal ``.bak``, ``"empty"`` quando non si è
    letto niente. *quarantined* è dove è finito il file rotto. *stuck* vuol dire
    che il file rotto **non** si è potuto spostare: è ancora al suo posto, ed è
    l'unica copia di quel che c'era, quindi niente ci deve scrivere sopra.
    """

    value: _T | None = None
    recovered_from: str | None = None
    quarantined: Path | None = None
    stuck: bool = False


def load_with_recovery(
    path: Path,
    parse: Callable[[Path], _T],
    *,
    errors: tuple[type[BaseException], ...] = (OSError, ValueError),
    write: Callable[[Path, str], None] | None = None,
) -> Recovered[_T]:
    """Legge un file di stato con la sua rete: il file, poi il ``.bak``, poi niente.

    La stessa politica per config, cron e bacheca, che la scrivevano tre volte
    (e divergevano proprio sul caso difficile). *parse* legge un percorso e
    solleva uno di *errors* se non è usabile. Un file rotto non si cancella: va
    di lato con :func:`quarantine`, e se il ``.bak`` si legge lo si rimette al
    posto del vivo (con *write*, di serie ``atomic_write``), altrimenti ogni
    lettura rifarebbe il recupero — e la bacheca, che non lo faceva, alla seconda
    lettura trovava il quaderno vuoto.

    Se lo spostamento fallisce, non si promuove niente e il risultato è
    ``stuck``: il valore del ``.bak`` (se c'è) vale in memoria, ma chi chiama
    deve rifiutare le scritture finché il file rotto è lì. Cosa vuol dire
    rifiutare lo decide lui: il cron non parte, la config parte e non salva.
    """
    if not path.exists():
        return Recovered()
    try:
        return Recovered(parse(path))
    except errors as exc:
        logger.error("%s is unusable: %s", path, exc)

    backup = backup_path(path)
    from_backup = False
    value: _T | None = None
    if backup.exists():
        try:
            value = parse(backup)
            from_backup = True
        except errors as exc:
            logger.error("backup %s is unusable too: %s", backup, exc)
    kind = "backup" if from_backup else "empty"

    target = quarantine(path)
    if target is None:
        logger.error("could not set aside %s: it stays in place, and is not written over", path)
        return Recovered(value, kind, None, stuck=True)
    if from_backup:
        try:
            (write or atomic_write)(path, backup.read_text(encoding="utf-8"))
        except OSError as exc:
            logger.warning("could not put %s back in place of %s: %s", backup.name, path, exc)
        logger.warning("%s recovered from %s; broken file kept as %s", path, backup.name, target)
    else:
        logger.warning("%s could not be recovered; starting empty. Broken file kept as %s", path, target)
    return Recovered(value, kind, target)


_PATH_LOCKS: dict[str, threading.Lock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def path_lock(path: Path) -> threading.Lock:
    """Il ``threading.Lock`` di *path*, lo stesso per ogni nome dello stesso file.

    Per i file di stato scritti da funzioni sincrone, anche da un thread (la
    bacheca): due chiamanti sullo stesso percorso si mettono in fila, su due
    percorsi diversi no. La chiave è il percorso risolto, così due nomi della
    stessa cartella non fanno due lock.
    """
    key = str(path.resolve(strict=False))
    with _PATH_LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(key, threading.Lock())
