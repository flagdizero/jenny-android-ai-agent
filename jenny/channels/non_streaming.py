"""Il contratto di streaming del dispatcher, per i canali che non fanno streaming.

Il dispatcher chiama ``send_delta``, ``send_reasoning_*``, ``send_file_edit_events``
e ``discard_stream_buffer`` su ogni canale. Il fumetto della mascotte, la
tendina delle notifiche e Telegram mostrano solo il messaggio finale: per loro
quei metodi esistono e non fanno niente. Erano copiati identici in tutte e tre.
"""

from __future__ import annotations

from typing import Any


class NonStreamingChannelMixin:
    async def send_delta(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []

    async def send_reasoning_delta(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []

    async def send_reasoning_end(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []

    async def send_file_edit_events(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []

    def discard_stream_buffer(self, *args: Any, **kwargs: Any) -> None:
        return None
