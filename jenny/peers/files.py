"""I file fra due Jenny: un file in uscita diventa pezzi in coda, uno in arrivo si ricompone.

Un frame non supera :data:`jenny.peers.model.MAX_FRAME_BYTES`, e un PDF sì: il
file viaggia in pezzi, uno per messaggio ``file_part`` della coda, ognuno con
l'impronta del file intero. La coda li consegna in ordine e uno alla volta,
quindi un pezzo perso è un pezzo che si rimanda, non un file da ricominciare.

**Il contenuto non sta nella coda.** La coda vive in ``state.sqlite``, che
entra negli snapshot: venti MB di base64 lì li porterebbe in ogni copia. Il
file in uscita si copia in ``<runtime_root>/peers/outgoing/<fid>``, fuori dal
workspace, e la riga in coda porta solo dove leggere; i dati si aggiungono al
messaggio al momento di mandarlo (:func:`wire_body`). Lo stesso vale in arrivo:
i pezzi si appoggiano in ``peers/incoming/<fid>/`` e solo il file intero,
con l'impronta giusta, entra nel workspace (``uploads/peers/<nome>/``).
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

from jenny.utils.path import atomic_write

from .model import is_id
from .store import PeerStore, new_id, now_ms, peers_dir

KIND_FILE_PART = "file_part"

# Il tetto di un file. Un documento, non un video: venti MB sono un PDF
# corposo, e a 160 KB per pezzo sono 128 messaggi in coda.
MAX_FILE_BYTES = 20 * 1024 * 1024
# Un pezzo: in base64 cresce di un terzo, e il frame che lo porta resta sotto
# i 256 KB di ``MAX_FRAME_BYTES`` con margine per il resto del messaggio.
PART_BYTES = 160 * 1024
NAME_MAX = 120

_UNSAFE_NAME = re.compile(r"[^\w.\- ()]+", re.UNICODE)


class FileError(ValueError):
    """Un file che non si può mandare o un pezzo che non torna."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code


def safe_name(raw: Any) -> str:
    """Un nome di file che non esce dalla sua cartella e si legge."""
    name = Path(str(raw or "")).name
    name = _UNSAFE_NAME.sub("_", name).strip(" .")
    if not name:
        name = "file"
    if len(name) > NAME_MAX:
        stem, dot, ext = name.rpartition(".")
        name = (stem[: NAME_MAX - len(ext) - 1] + dot + ext) if dot else name[:NAME_MAX]
    return name


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def outgoing_dir(workspace: Path) -> Path:
    return peers_dir(workspace) / "outgoing"


def incoming_dir(workspace: Path) -> Path:
    return peers_dir(workspace) / "incoming"


# -- In uscita ---------------------------------------------------------------


def stage_outgoing(workspace: Path, store: PeerStore, peer_id: str, source: Path) -> dict[str, Any]:
    """Copia *source* fuori dal workspace e ne mette in coda i pezzi per *peer_id*.

    Ritorna la descrizione del file (``fid``, ``name``, ``size``, ``sha256``) da
    mettere nel messaggio che lo accompagna. Chi chiama controlla prima che
    *source* sia un file che si può mandare (il percorso, i segreti).
    """
    source = Path(source)
    if not source.is_file():
        raise FileError("not_found")
    size = source.stat().st_size
    if size > MAX_FILE_BYTES:
        raise FileError("too_large")
    fid = new_id()
    staged = outgoing_dir(workspace) / fid
    staged.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, staged)
    sha = _sha256(staged)
    if staged.stat().st_size != size:
        staged.unlink(missing_ok=True)
        raise FileError("changed", "the file changed while it was being copied")
    total = max(1, -(-size // PART_BYTES))
    info = {"fid": fid, "name": safe_name(source.name), "size": size, "sha256": sha}
    for index in range(total):
        store.enqueue(peer_id, KIND_FILE_PART, {**info, "i": index, "n": total})
    return info


def wire_body(workspace: Path, kind: str, body: dict[str, Any]) -> dict[str, Any]:
    """Il corpo da mettere sul filo: per un pezzo di file, con i suoi byte letti adesso."""
    if kind != KIND_FILE_PART:
        return body
    fid = body.get("fid")
    index = body.get("i")
    if not is_id(fid) or not isinstance(index, int) or index < 0:
        raise FileError("bad_part")
    staged = outgoing_dir(workspace) / fid
    with staged.open("rb") as fh:
        fh.seek(index * PART_BYTES)
        data = fh.read(PART_BYTES)
    return {**body, "data": base64.b64encode(data).decode("ascii")}


def part_sent(workspace: Path, body: dict[str, Any]) -> None:
    """Dopo l'``ack`` dell'ultimo pezzo, la copia in uscita non serve più."""
    fid = body.get("fid")
    if is_id(fid) and body.get("i") == body.get("n", 0) - 1:
        (outgoing_dir(workspace) / fid).unlink(missing_ok=True)


# -- In arrivo ----------------------------------------------------------------


def receive_part(
    workspace: Path, peer_id: str, peer_name: str, body: dict[str, Any]
) -> dict[str, Any] | None:
    """Appoggia un pezzo; all'ultimo ricompone, controlla e porta il file nel workspace.

    Ritorna la descrizione del file finito (con ``path`` relativo al workspace)
    quando questo pezzo lo completa, altrimenti ``None``. Un file che non torna
    (dimensione, impronta) si scarta e solleva :class:`FileError`.
    """
    fid = body.get("fid")
    index = body.get("i")
    total = body.get("n")
    size = body.get("size")
    sha = body.get("sha256")
    data = body.get("data")
    if (
        not is_id(fid)
        or not isinstance(index, int)
        or not isinstance(total, int)
        or not isinstance(size, int)
        or not isinstance(sha, str)
        or not isinstance(data, str)
        or not 0 <= index < total
        or not 0 <= size <= MAX_FILE_BYTES
        or total != max(1, -(-size // PART_BYTES))
    ):
        raise FileError("bad_part")
    try:
        chunk = base64.b64decode(data, validate=True)
    except ValueError as exc:
        raise FileError("bad_part") from exc
    expected = PART_BYTES if index < total - 1 else size - PART_BYTES * (total - 1)
    if len(chunk) != expected:
        raise FileError("bad_part")
    folder = incoming_dir(workspace) / fid
    folder.mkdir(parents=True, exist_ok=True)
    atomic_write(folder / f"{index:05d}", chunk, fsync_dir=False)
    parts = sorted(folder.glob("[0-9]" * 5))
    if len(parts) < total:
        return None
    assembled = folder / "assembled"
    digest = hashlib.sha256()
    with assembled.open("wb") as out:
        for part in parts:
            block = part.read_bytes()
            digest.update(block)
            out.write(block)
    if assembled.stat().st_size != size or digest.hexdigest() != sha:
        shutil.rmtree(folder, ignore_errors=True)
        raise FileError("bad_file")
    name = safe_name(body.get("name"))
    target_dir = Path(workspace) / "uploads" / "peers" / safe_name(peer_name)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = _free_name(target_dir, name)
    shutil.move(str(assembled), target)
    shutil.rmtree(folder, ignore_errors=True)
    info = {
        "fid": fid,
        "name": name,
        "size": size,
        "sha256": sha,
        "path": target.relative_to(Path(workspace)).as_posix(),
    }
    _remember(workspace, peer_id, info)
    return info


def _free_name(folder: Path, name: str) -> Path:
    candidate = folder / name
    if not candidate.exists():
        return candidate
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    for n in range(2, 1000):
        candidate = folder / (f"{stem} ({n}).{ext}" if dot else f"{stem} ({n})")
        if not candidate.exists():
            return candidate
    return folder / f"{new_id()}-{name}"


def _received_index(workspace: Path) -> Path:
    return incoming_dir(workspace) / "received.json"


def _remember(workspace: Path, peer_id: str, info: dict[str, Any]) -> None:
    """Il file finito, ritrovabile per ``fid`` quando arriva il messaggio che lo cita."""
    path = _received_index(workspace)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    cutoff = now_ms() - 7 * 24 * 3600 * 1000
    data = {k: v for k, v in data.items() if isinstance(v, dict) and v.get("at", 0) >= cutoff}
    data[info["fid"]] = {**info, "peer": peer_id, "at": now_ms()}
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(data, ensure_ascii=False))


def received(workspace: Path, peer_id: str, fid: str) -> dict[str, Any] | None:
    """Il file *fid* arrivato da *peer_id*, se è arrivato intero."""
    try:
        data = json.loads(_received_index(workspace).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    entry = data.get(fid) if isinstance(data, dict) else None
    if not isinstance(entry, dict) or entry.get("peer") != peer_id:
        return None
    if not (Path(workspace) / str(entry.get("path", ""))).is_file():
        return None
    return entry
