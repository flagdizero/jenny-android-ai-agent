"""Workspace file management: CRUD operations for shared workspace."""

from __future__ import annotations

import errno
import json
import os
import shutil
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from jenny.utils.path import atomic_write

# Oltre ai dotfile, questi sono stato/implementazione del runtime (non
# contenuto dell'utente): config.json contiene un secret e si edita dalle
# Impostazioni, agent/ e ui/ sono bundle rigenerati ad ogni avvio, cron/ e
# sessions/ sono storage interno dei rispettivi motori.
#
# ``config.json*`` con la stella, non il nome esatto: accanto al file vivo
# possono comparire il backup ``config.json.bak`` e i temporanei di
# ``atomic_write``, che contengono le stesse chiavi API e lo stesso secret.
# Nascondere solo il nome esatto li avrebbe esposti nel browser file.
#
# ``*.tmp``: il temporaneo di ``atomic_write`` è invisibile solo per il tempo
# di una scrittura, ma un processo ucciso in quel momento lo lascia lì per
# sempre. È un residuo del runtime, non un file dell'utente, e mostrarlo
# accanto all'originale invita solo ad aprire quello sbagliato.
#
# ``update_state.json``: lo scrive il controllo aggiornamenti nella radice del
# workspace (``runtime/update_check.py``, ``STATE_FILENAME``). È il diario di
# bordo dell'updater — ultima verifica, versione vista, esito — non qualcosa
# che l'utente abbia creato o debba modificare: editarlo a mano confonde
# soltanto la logica di controllo.
#
# ``__pycache__``: la genera l'interprete Python ovunque l'agente importi un
# modulo del workspace (oggi sotto ``skills/``, domani altrove), quindi non
# basta coprirla nella radice. Il nome secco funziona a **qualsiasi** profondità
# perché ``_is_internal`` prova il glob anche sul solo nome dell'item; le due
# varianti con ``/**`` servono per il contenuto della cartella, che ha nomi
# arbitrari (``*.pyc``, e la sottodirectory che i writer di bytecode possono
# aggiungere) e che si vede solo entrandoci in modalità avanzata. Sono pattern
# ancorati su ``__pycache__/`` e non su ``*__pycache__*``: una cartella
# dell'utente che contenga quella parola nel nome resta visibile.
_DEFAULT_INTERNAL_PATTERNS = [
    ".*",
    "*.tmp",
    "config.json*",
    "config.corrupt-*.json",
    "update_state.json",
    "__pycache__", "__pycache__/**", "*/__pycache__/**",
    "agent", "agent/**",
    "cron", "cron/**",
    "sessions", "sessions/**",
    "ui", "ui/**",
]


def validate_path(workspace_root: Path, requested_path: str) -> Path:
    """Validate and resolve a path within workspace.

    Prevents path traversal attacks. Delega all'UNICO gate di path del core
    (`security.workspace_policy.resolve_allowed_path`, symlink-safe e
    fail-closed) invece di reimplementare il controllo. Mantiene il contratto
    storico di sollevare ``ValueError`` fuori dai confini, atteso dai chiamanti
    delle route WebUI.
    """
    from jenny.security.workspace_policy import (
        WorkspaceBoundaryError,
        resolve_allowed_path,
    )

    try:
        return resolve_allowed_path(
            requested_path,
            workspace=workspace_root,
            allowed_root=workspace_root,
        )
    except WorkspaceBoundaryError as exc:
        raise ValueError("Path traversal detected") from exc


def validate_entry_path(workspace_root: Path, requested_path: str) -> Path:
    """Il percorso della **voce** *requested_path*, senza seguire il suo ultimo link.

    ``validate_path`` risolve tutto, ultimo componente compreso: per un symlink
    restituisce il bersaglio. Per leggere e' quel che serve; per cancellare o
    rinominare no — ``delete`` di un link faceva la ``rmtree`` della cartella
    vera, e un link verso fuori non si poteva toccare affatto, perche' il gate
    rifiutava il bersaglio.

    Qui passa dal gate il **genitore**, risolto; il nome resta com'e'. Il confine
    tiene: il genitore e' dentro, e un solo componente che non sia ``.``/``..``
    non puo' risalire. Se l'ultimo componente e' ``.`` o ``..`` la voce e' una
    cartella nominata per via, non un link: si risolve per intero, come prima.
    Una voce che non e' un link esce identica a quella di ``validate_path``.
    """
    parent_rel, _, name = str(requested_path).rstrip("/").rpartition("/")
    if name in ("", ".", ".."):
        return validate_path(workspace_root, requested_path)
    return validate_path(workspace_root, parent_rel or ".") / name


def _load_internal_patterns(workspace_root: Path) -> list[str]:
    """Legge <workspace_root>/.jenny/internal.json.

    Fallback silenzioso a [".*"] se il manifest manca o è malformato, per
    riprodurre il comportamento storico (i dotfile erano già nascosti nella
    vista tree) senza mai propagare un'eccezione alle route WebUI.
    """
    manifest = workspace_root / ".jenny" / "internal.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        patterns = data.get("patterns")
        if isinstance(patterns, list) and all(isinstance(p, str) for p in patterns):
            return patterns
    except (OSError, ValueError, AttributeError):
        pass
    return _DEFAULT_INTERNAL_PATTERNS


def _is_internal(rel_path: str, name: str, patterns: list[str]) -> bool:
    """Un item è interno se il nome o il path relativo matcha un pattern glob."""
    return any(fnmatch(name, p) or fnmatch(rel_path, p) for p in patterns)


def list_directory(path: Path, *, workspace_root: Path | None = None) -> list[dict[str, Any]]:
    """List directory contents with metadata.

    Raises FileNotFoundError if path does not exist, PermissionError if not accessible.
    """
    # Su Android /data/data/... e /data/user/0/... sono alias simlink dello
    # stesso path: risolvere qui allinea workspace_root alla forma canonica
    # già usata da `path` (arrivato via validate_path, che risolve i symlink),
    # altrimenti relative_to() fallisce per un mismatch puramente testuale.
    if workspace_root is not None:
        workspace_root = workspace_root.resolve()
    patterns = _load_internal_patterns(workspace_root) if workspace_root is not None else None
    items = []
    for item in sorted(path.iterdir()):
        try:
            stat = item.stat()
        except OSError:
            # Un symlink pendente (o un loop): ``stat`` segue il link e non
            # trova niente. E' una voce della cartella come le altre — prima
            # faceva rispondere 404 all'intera cartella.
            # I metadati sono quelli del link; ``is_file``/``is_dir`` qui sotto
            # dicono entrambi di no, quindi figura come file senza dimensione.
            stat = item.lstat()
        internal = False
        if patterns is not None and workspace_root is not None:
            rel = str(item.relative_to(workspace_root))
            internal = _is_internal(rel, item.name, patterns)
        items.append({
            "name": item.name,
            "type": "directory" if item.is_dir() else "file",
            "size": stat.st_size if item.is_file() else None,
            "modified": stat.st_mtime,
            "extension": item.suffix if item.is_file() else None,
            "internal": internal,
        })
    return items


def os_error_text(exc: OSError) -> str:
    """Il perche' di un ``OSError``, senza il dove: il testo per un client.

    ``str(exc)`` e' ``[Errno 21] Is a directory: '/data/user/0/…/workspace/x'``:
    il percorso assoluto della cartella privata dell'app finiva nel corpo dei
    400 e nei toast. Resta ``strerror`` («Is a
    directory»); un ``OSError`` alzato con un messaggio solo, senza file, e'
    gia' scritto per chi legge e passa com'e'.
    """
    if exc.strerror:
        return exc.strerror
    if exc.filename is None and exc.filename2 is None and str(exc):
        return str(exc)
    return "filesystem error"


class WorkspaceBinaryFileError(ValueError):
    """Il file richiesto è binario e non può essere letto come testo."""


# Stessa euristica di webui.file_preview: un byte nullo nei primi 4 KB
# marca il file come binario. La decisione è sul contenuto, mai
# sull'estensione: qualsiasi file di testo resta leggibile.
_BINARY_SNIFF_BYTES = 4096


def read_file(path: Path, max_size: int = 1_000_000) -> str:
    """Read file content with size limit.

    Raises FileNotFoundError if path does not exist, PermissionError if not readable.
    Solleva ``WorkspaceBinaryFileError`` se il contenuto è binario.
    """
    if path.stat().st_size > max_size:
        raise ValueError(f"File too large (max {max_size} bytes)")
    raw = path.read_bytes()
    if b"\0" in raw[:_BINARY_SNIFF_BYTES]:
        raise WorkspaceBinaryFileError("binary file")
    return raw.decode("utf-8", errors="replace")


def read_download(path: Path) -> bytes:
    """I byte di *path* per ``/api/workspace/download``, o l'errore che lo dice.

    ``FileNotFoundError`` se non c'e' (anche un link pendente o un loop:
    ``exists`` dice di no), ``IsADirectoryError`` se e' una cartella. Una sola
    funzione perche' la rotta la chiama in un thread.
    """
    if not path.exists():
        raise FileNotFoundError(errno.ENOENT, "path not found")
    if path.is_dir():
        raise IsADirectoryError(errno.EISDIR, "cannot download a directory")
    return path.read_bytes()


def write_file(path: Path, content: str) -> None:
    """Write content to file."""
    from jenny.utils.wiki_paths import keep_wiki_id

    # Salvataggio dall'editor della WebUI: riscrive il file intero, quindi un
    # processo ucciso a metà lo troncherebbe. Il contenuto vecchio resta valido
    # fino al rename finale.
    #
    # L'``AGENTS.md`` di un quaderno si salva sempre con il suo ``id:``: e' la riga
    # che lega il quaderno alla sua chat, e chi la toglie dall'editor non sa di
    # farlo. Rimessa in silenzio e non rifiutata — il file e' dell'utente, e un
    # rifiuto sarebbe un cancello che non protegge niente che non si possa
    # rimettere.
    atomic_write(path, keep_wiki_id(path, content))


def create_directory(path: Path) -> None:
    """Create a directory."""
    path.mkdir(parents=True, exist_ok=True)


def _refuse_taken(src: Path, dest: Path) -> None:
    """``FileExistsError`` se *dest* c'e' gia' e non e' *src* stesso.

    ``Path.rename`` su POSIX sostituisce in silenzio un file (e una cartella
    vuota), ``shutil.copy2`` pure: dal file manager un nome gia' preso cancellava
    quel che c'era sotto senza chiedere. Il controllo e la
    mossa restano due passi, con la finestra di un ``lstat``. «E' lo stesso file»
    serve al rinomino che cambia solo le maiuscole su un disco che non le
    distingue: li' la destinazione «esiste», ed e' l'origine.
    """
    try:
        dest_stat = dest.lstat()
    except FileNotFoundError:
        return
    try:
        if os.path.samestat(src.lstat(), dest_stat):
            return
    except OSError:
        pass
    raise FileExistsError(errno.EEXIST, "destination already exists")


def rename_path(old_path: Path, new_path: Path) -> None:
    """Rename a file or directory, never over an existing one."""
    _refuse_taken(old_path, new_path)
    old_path.rename(new_path)


def delete_path(path: Path) -> None:
    """Delete a file or directory; a symlink is removed itself, never followed."""
    # ``is_dir`` segue il link: la ``rmtree`` finiva sulla cartella vera.
    # ``rmtree`` dentro l'albero i link non li segue.
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def free_copy_name(src: Path) -> Path:
    """Un nome libero accanto a *src* per la sua copia: ``nota (copy).md``, poi
    ``nota (copy 2).md`` e cosi' via. Per una cartella il suffisso resta sul nome
    intero (``foto.2024`` → ``foto.2024 (copy)``): li' un punto non e' un'estensione.

    Il nome e' inglese come gli altri che il runtime scrive su disco; non passa
    dall'i18n perche' non e' un testo dell'interfaccia, e' un file.
    """
    if src.is_dir():
        stem, suffix = src.name, ""
    else:
        stem, suffix = src.stem, src.suffix
    for n in range(1, 1000):
        label = "copy" if n == 1 else f"copy {n}"
        candidate = src.with_name(f"{stem} ({label}){suffix}")
        if not os.path.lexists(candidate):
            return candidate
    raise FileExistsError("no free name for the copy")


def copy_path(src: Path, dest: Path) -> None:
    """Copy a file or directory, never over an existing one.

    Niente ``copy2``/``copytree``: finiscono entrambi con ``copystat``, che copia
    anche gli attributi estesi, e su Android ``security.selinux`` non si
    riscrive da un'app (EACCES). La copia era gia' sul disco, ma l'errore
    risaliva al file manager come «permission denied». Si copiano contenuto,
    permessi e ora di modifica; gli xattr no.
    """
    _refuse_taken(src, dest)
    if src.is_dir():
        _copy_tree(src, dest)
    else:
        _copy_file(src, dest)


def _copy_file(src: Path, dest: Path) -> None:
    shutil.copyfile(src, dest)
    shutil.copymode(src, dest)
    _copy_times(src, dest)


def _copy_tree(src: Path, dest: Path) -> None:
    os.mkdir(dest)
    for entry in os.scandir(src):
        child_src, child_dest = Path(entry.path), dest / entry.name
        if entry.is_symlink():
            # I link dentro la cartella si copiano come link. Seguirli portava
            # dentro il workspace una copia di quel che c'era fuori, dove il
            # file manager poi la mostrava.
            os.symlink(os.readlink(child_src), child_dest)
        elif entry.is_dir():
            _copy_tree(child_src, child_dest)
        else:
            _copy_file(child_src, child_dest)
    shutil.copymode(src, dest)
    _copy_times(src, dest)


def _copy_times(src: Path, dest: Path) -> None:
    """L'ora di modifica dell'originale sulla copia, se il filesystem la accetta."""
    try:
        st = os.stat(src)
        os.utime(dest, ns=(st.st_atime_ns, st.st_mtime_ns))
    except OSError:
        pass
