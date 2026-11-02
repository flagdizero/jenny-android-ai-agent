"""Blob store content-addressed: sha256 come chiave, zlib come compressione.

Il nome del blob È il suo hash: ogni corruzione su disco è auto-rilevabile
alla lettura. La deduplica è implicita (stesso contenuto → stesso path).
"""

from __future__ import annotations

import hashlib
import zlib
from pathlib import Path

from jenny.utils.path import atomic_write


class BlobCorruptError(Exception):
    """Il contenuto di un blob non corrisponde al suo hash dichiarato."""


def hash_content(content: bytes) -> str:
    """Sha256 esadecimale del contenuto raw (non compresso)."""
    return hashlib.sha256(content).hexdigest()


def object_path(objects_dir: Path, hash_hex: str) -> Path:
    """Path del blob per un dato hash (sharding a 2 caratteri, come git)."""
    return objects_dir / hash_hex[:2] / hash_hex


def put_blob(objects_dir: Path, content: bytes) -> str:
    """Scrive il blob se assente e ritorna il suo hash.

    La scrittura è atomica, così un crash a metà non lascia mai un blob
    parziale con il nome definitivo.
    """
    hash_hex = hash_content(content)
    dest = object_path(objects_dir, hash_hex)
    if dest.is_file():
        return hash_hex

    # ``atomic_write`` per il temporaneo con nome unico (due scrittori dello
    # stesso blob non se lo portano via a vicenda), il rename atomico e la
    # pulizia; il suo suffisso ``.tmp`` è già filtrato da ``iter_blob_hashes``.
    #
    # Senza fsync, e non per distrazione: questo è l'unico punto sulla strada
    # calda — ``put_blob`` viene chiamato una volta per file tracciato a ogni
    # snapshot. Misurato sul Titan 2 (f2fs, ``fsync_mode=nobarrier``): ~4 ms per
    # fsync, cioè secondi interi su un workspace di qualche migliaio di file. Il
    # rename resta atomico, quindi un processo ucciso non lascia mai un blob
    # parziale col nome definitivo; a cadere è solo la garanzia contro la
    # perdita di corrente, che è dove stava anche prima di questa modifica.
    atomic_write(dest, zlib.compress(content), fsync_file=False, fsync_dir=False)
    return hash_hex


def read_verified_blob(objects_dir: Path, hash_hex: str) -> tuple[bytes, bytes]:
    """Il blob come sta su disco (zlib) e decompresso, verificato contro l'hash.

    I byte grezzi servono a chi lo spedisce così com'è (la copia su Drive): un
    blob corrotto spedito lo si scoprirebbe al ripristino, quando l'originale
    non c'è più.

    Raises:
        FileNotFoundError: blob assente.
        BlobCorruptError: contenuto che non corrisponde all'hash.
    """
    raw = object_path(objects_dir, hash_hex).read_bytes()
    try:
        content = zlib.decompress(raw)
    except zlib.error as exc:
        raise BlobCorruptError(f"blob {hash_hex} is not valid zlib data") from exc
    if hash_content(content) != hash_hex:
        raise BlobCorruptError(f"blob {hash_hex} content does not match its hash")
    return raw, content


def get_blob(objects_dir: Path, hash_hex: str) -> bytes:
    """Legge e decomprime un blob, verificandone l'integrità (v. :func:`read_verified_blob`)."""
    return read_verified_blob(objects_dir, hash_hex)[1]


def iter_blob_hashes(objects_dir: Path):
    """Itera gli hash di tutti i blob presenti nello store."""
    if not objects_dir.is_dir():
        return
    for shard in objects_dir.iterdir():
        if not shard.is_dir():
            continue
        for blob in shard.iterdir():
            if blob.is_file() and not blob.name.endswith(".tmp"):
                yield blob.name
