"""La destinazione in memoria dei test del backup su Drive.

Stava in ``jenny_extensions/drive/cloud_sink.py``, e quindi nell'APK, ma la usano
solo i test.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from jenny_extensions.drive.cloud_sink import RemoteNotFoundError, split_key


class MemorySink:
    """Destinazione in memoria, per i test. ``puts`` conta i caricamenti."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.puts: list[str] = []

    async def list(self, folder: str) -> list[str]:
        out = []
        for key in self.files:
            parent, name = split_key(key)
            if parent == folder:
                out.append(name)
        return sorted(out)

    async def put_bytes(self, key: str, data: bytes) -> None:
        split_key(key)
        self.files[key] = bytes(data)
        self.puts.append(key)

    async def put_file(
        self, key: str, source: Path, on_progress: Callable[[int, int], None] | None = None
    ) -> None:
        await self.put_bytes(key, Path(source).read_bytes())

    async def get_bytes(self, key: str) -> bytes:
        try:
            return self.files[key]
        except KeyError:
            raise RemoteNotFoundError(key) from None

    async def get_file(self, key: str, dest: Path) -> None:
        Path(dest).write_bytes(await self.get_bytes(key))

    async def delete(self, key: str) -> None:
        if self.files.pop(key, None) is None:
            raise RemoteNotFoundError(key)
