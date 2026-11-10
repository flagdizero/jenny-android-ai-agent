"""Tipi condivisi del motore di snapshot."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from jenny.utils.locations import STATE_ENTRY_DIR

_HASH_RE = re.compile(r"\A[0-9a-f]{64}\Z")



def is_state_entry(path: str) -> bool:
    return path.startswith(STATE_ENTRY_DIR + "/")


def is_snapshot_id(value: object) -> bool:
    """*value* ha la forma di un id di snapshot: uno sha256 esadecimale minuscolo.

    L'id finisce in un percorso (``manifests/<id>.json``), che la retention
    cancella: uno arrivato con un ``.jbk`` e' dato non fidato come i ``path``
    delle voci, e ``../../workspace/config`` faceva sparire ``config.json``.
    Un id vero lo calcola ``SnapshotEngine._compute_id``, e ha solo questa forma.
    """
    return isinstance(value, str) and _HASH_RE.match(value) is not None


def unsafe_entry_reason(path: str, hash_hex: str) -> str | None:
    """Perche' una voce di manifest non si puo' materializzare, o ``None``.

    Un manifest scritto da questo motore e' sano per costruzione; uno arrivato
    con un ``.jbk`` e' dato non fidato, e ``restore_snapshot`` scrive ogni
    ``path`` sotto la destinazione: un ``..`` o un assoluto ne uscivano. Il
    ``hash`` finisce in un percorso sotto ``objects/``, quindi anche lui.
    """
    pure = PurePosixPath(path)
    if not path or pure.is_absolute() or path.startswith("/"):
        return f"unsafe path {path!r}"
    if any(part in ("", ".", "..") for part in path.split("/")):
        return f"unsafe path {path!r}"
    if not _HASH_RE.match(hash_hex):
        return f"unsafe blob hash {hash_hex!r} for {path!r}"
    return None

def entry_target(root: Path, path: str) -> Path:
    """Dove la voce di manifest *path* (``a/b.md``, sempre con ``/``) sta sotto *root*."""
    return root / Path(*path.split("/"))


def entry_problem(root: Path, path: str, hash_hex: str) -> str | None:
    """:func:`unsafe_entry_reason`, più il percorso **risolto** sotto *root* (già risolta).

    Il confronto sul percorso risolto copre anche un link simbolico che nessun
    controllo sulla stringa vedrebbe. È la guardia di chi materializza un
    manifest non fidato: il ripristino di uno snapshot e quello dal Drive.
    """
    reason = unsafe_entry_reason(path, hash_hex)
    if reason is None and not entry_target(root, path).resolve().is_relative_to(root):
        reason = f"unsafe path {path!r} leaves the destination"
    return reason


# Trigger riconosciuti per uno snapshot. La stringa finisce nel manifest e
# nella UI (badge tradotto via i18n lato client).


@dataclass
class FileEntry:
    """Un file tracciato dentro uno snapshot.

    ``path`` è sempre relativo alla radice del workspace, in forma POSIX
    (separatore ``/``), così i manifest sono portabili tra device.
    """

    path: str
    hash: str
    size: int
    mtime_ns: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "hash": self.hash,
            "size": self.size,
            "mtime_ns": self.mtime_ns,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FileEntry:
        return cls(
            path=str(data["path"]),
            hash=str(data["hash"]),
            size=int(data["size"]),
            mtime_ns=int(data.get("mtime_ns", 0)),
        )


@dataclass
class SnapshotManifest:
    """Uno snapshot completo del workspace (l'equivalente di un commit)."""

    id: str
    created_at_ms: int
    trigger: str
    label: str | None = None
    parent: str | None = None
    files: list[FileEntry] = field(default_factory=list)

    @property
    def file_count(self) -> int:
        return sum(1 for entry in self.files if not is_state_entry(entry.path))

    @property
    def total_bytes(self) -> int:
        return sum(entry.size for entry in self.files if not is_state_entry(entry.path))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at_ms": self.created_at_ms,
            "trigger": self.trigger,
            "label": self.label,
            "parent": self.parent,
            "files": [entry.to_dict() for entry in self.files],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SnapshotManifest:
        return cls(
            id=str(data["id"]),
            created_at_ms=int(data["created_at_ms"]),
            trigger=str(data.get("trigger", "auto")),
            label=data.get("label"),
            parent=data.get("parent"),
            files=[FileEntry.from_dict(f) for f in data.get("files", [])],
        )

    def summary(self) -> dict[str, Any]:
        """Riga compatta per l'indice e per la lista snapshot della UI."""
        return {
            "id": self.id,
            "created_at_ms": self.created_at_ms,
            "trigger": self.trigger,
            "label": self.label,
            "parent": self.parent,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
        }
