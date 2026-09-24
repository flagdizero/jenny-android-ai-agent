"""I canali senza streaming rispondono al contratto del dispatcher senza fare niente.

Fumetto, notifiche e Telegram mostrano solo il messaggio finale: i cinque
metodi di streaming devono esistere (il dispatcher li chiama su ogni canale) e
non fare niente. Erano copiati identici nelle tre classi.
"""

from __future__ import annotations

import inspect

import pytest

from jenny.channels.floating import FloatingChannel
from jenny.channels.notification import NotificationChannel
from jenny.channels.telegram import TelegramChannel

CHANNELS = [FloatingChannel, NotificationChannel, TelegramChannel]
ASYNC_NOOPS = ["send_delta", "send_reasoning_delta", "send_reasoning_end", "send_file_edit_events"]


@pytest.mark.parametrize("cls", CHANNELS, ids=lambda c: c.__name__)
@pytest.mark.parametrize("method", ASYNC_NOOPS)
async def test_streaming_methods_are_async_noops(cls, method: str) -> None:
    fn = getattr(cls, method)
    assert inspect.iscoroutinefunction(fn)
    assert await fn(object(), "chat", "x", key="v") == []


@pytest.mark.parametrize("cls", CHANNELS, ids=lambda c: c.__name__)
def test_discard_stream_buffer_is_a_noop(cls) -> None:
    fn = cls.discard_stream_buffer
    assert not inspect.iscoroutinefunction(fn)
    assert fn(object(), "chat") is None
