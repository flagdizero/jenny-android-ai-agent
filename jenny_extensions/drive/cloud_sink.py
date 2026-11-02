"""Destinazioni remote del backup nel cloud: l'interfaccia e due implementazioni.

Il repository remoto (``cloud.py``) vede solo chiavi della forma ``nome`` o
``cartella/nome``, un livello al massimo: è il minimo comune tra una cartella
su disco e Google Drive, dove ogni cartella è un oggetto da cercare per nome.

Contratto che ogni destinazione deve rispettare:

- ``put_*`` è atomico per chi legge: o il file c'è intero, o non c'è. Su disco
  lo dà il rename, su Drive il caricamento ripartibile, che crea il file solo
  alla fine.
- ``put_*`` su una chiave esistente la sostituisce (Drive ammette due file con
  lo stesso nome: la sua implementazione deve cercare prima di creare).
- ``get_*``/``delete`` su una chiave assente alzano :class:`RemoteNotFoundError`.

``LocalDirSink`` serve a provare tutto sul Mac contro una cartella vera, e a
``scripts/restore_drive_copy.py`` per riaprire una copia scaricata. Quella in
memoria è dei test (``tests/extensions/drive/memory_sink.py``): qui finiva nell'APK.
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from jenny.utils.path import atomic_copy, atomic_write


class RemoteNotFoundError(Exception):
    """La chiave richiesta non esiste sulla destinazione."""


class RemoteSink(Protocol):
    """Una destinazione remota a chiavi ``[cartella/]nome``."""

    async def list(self, folder: str) -> list[str]:
        """Nomi dei file in *folder* (``""`` = radice); vuoto se la cartella manca."""
        ...

    async def put_bytes(self, key: str, data: bytes) -> None: ...

    async def put_file(
        self, key: str, source: Path, on_progress: Callable[[int, int], None] | None = None
    ) -> None:
        """*on_progress(byte mandati, byte totali)*, se la destinazione lo sa dire."""
        ...

    async def get_bytes(self, key: str) -> bytes: ...

    async def get_file(self, key: str, dest: Path) -> None: ...

    async def delete(self, key: str) -> None: ...


def split_key(key: str) -> tuple[str, str]:
    """``"packs/ab.jpk"`` → ``("packs", "ab.jpk")``; ``"repo.json"`` → ``("", "repo.json")``.

    Una chiave con più di un livello, vuota o con ``..`` è un errore del
    chiamante, non un percorso da onorare.
    """
    parts = key.split("/")
    if len(parts) > 2 or any(p in ("", ".", "..") for p in parts):
        raise ValueError(f"invalid remote key {key!r}")
    return ("", parts[0]) if len(parts) == 1 else (parts[0], parts[1])


class LocalDirSink:
    """Destinazione su una cartella del disco locale."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        parent, name = split_key(key)
        return self.root / parent / name if parent else self.root / name

    async def list(self, folder: str) -> list[str]:
        if folder:
            split_key(folder)
        target = self.root / folder if folder else self.root

        def _scan() -> list[str]:
            if not target.is_dir():
                return []
            return sorted(
                p.name for p in target.iterdir() if p.is_file() and not p.name.endswith(".tmp")
            )

        return await asyncio.to_thread(_scan)

    async def put_bytes(self, key: str, data: bytes) -> None:
        await asyncio.to_thread(atomic_write, self._path(key), data)

    async def put_file(
        self, key: str, source: Path, on_progress: Callable[[int, int], None] | None = None
    ) -> None:
        await asyncio.to_thread(atomic_copy, source, self._path(key))

    async def get_bytes(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError:
            raise RemoteNotFoundError(key) from None

    async def get_file(self, key: str, dest: Path) -> None:
        path = self._path(key)
        if not path.is_file():
            raise RemoteNotFoundError(key)
        await asyncio.to_thread(shutil.copyfile, path, dest)

    async def delete(self, key: str) -> None:
        try:
            await asyncio.to_thread(self._path(key).unlink)
        except FileNotFoundError:
            raise RemoteNotFoundError(key) from None
