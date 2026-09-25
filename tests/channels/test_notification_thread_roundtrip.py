"""Il tag della notifica fa il giro e torna a casa.

Tre punti lo toccano — chi lo crea (``native_input``), chi lo trasporta
(``AgentLoop._assemble_outbound``) e chi lo consuma (``NotificationChannel``) —
e ciascuno ha già i suoi test. Quello che nessuno dei tre prova è la **catena**:
il trasporto non è codice scritto per questo, è una riga che copia i metadata
dell'inbound sull'outbound (``meta = dict(msg.metadata or {})``). Finché regge,
la risposta torna nella scheda da cui è partita la domanda; il giorno che
qualcuno la cambia, torna la seconda scheda nella tendina e nessun test se ne
accorgerebbe.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from jenny.agent.loop import AgentLoop
from jenny.bus.events import NOTIFICATION_CHANNEL, InboundMessage
from jenny.bus.queue import MessageBus
from jenny.channels import notification as nc
from jenny.channels.notification import REPLY_THREAD_TAG, NotificationChannel
from jenny.providers.base import LLMResponse
from jenny.runtime.native_input import NATIVE_SOURCE_KEY, NATIVE_THREAD_KEY
from jenny.session.keys import UNIFIED_SESSION_KEY


class _Alerts:
    def __init__(self) -> None:
        self.threads: list[str | None] = []

    async def __call__(self, content, metadata, *, thread=None):
        self.threads.append(thread)
        return True


@pytest.fixture
def alerts(monkeypatch) -> _Alerts:
    spy = _Alerts()
    monkeypatch.setattr(nc, "post_alert", spy)
    return spy


def _loop(tmp_path: Path) -> AgentLoop:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation = SimpleNamespace(max_tokens=4096)
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="done"))
    return AgentLoop(
        bus=MessageBus(), provider=provider, workspace=tmp_path, model="test-model"
    )


def _from_the_shade(thread: str | None) -> InboundMessage:
    """L'inbound esattamente come lo costruisce ``native_input._publish``."""
    metadata: dict[str, Any] = {NATIVE_SOURCE_KEY: "notification"}
    if thread:
        metadata[NATIVE_THREAD_KEY] = thread
    return InboundMessage(
        channel=NOTIFICATION_CHANNEL,
        sender_id="user",
        chat_id="shade",
        content="spostalo a lunedì",
        metadata=metadata,
    )


async def test_the_tag_survives_the_turn_and_returns_to_the_same_card(
    tmp_path, alerts: _Alerts
) -> None:
    loop = _loop(tmp_path)
    inbound = _from_the_shade("cron:spesa")

    outbound = loop._assemble_outbound(
        inbound, "fatto, lunedì alle 9", [], "stop", False, None
    )

    assert outbound is not None
    assert outbound.channel == NOTIFICATION_CHANNEL
    assert outbound.metadata[NATIVE_THREAD_KEY] == "cron:spesa"

    await NotificationChannel().send(outbound)
    assert alerts.threads == ["cron:spesa"]


async def test_without_tag_the_roundtrip_ends_on_the_fallback(tmp_path, alerts: _Alerts) -> None:
    loop = _loop(tmp_path)
    outbound = loop._assemble_outbound(
        _from_the_shade(None), "eccomi", [], "stop", False, None
    )

    assert outbound is not None
    assert NATIVE_THREAD_KEY not in outbound.metadata

    await NotificationChannel().send(outbound)
    assert alerts.threads == [REPLY_THREAD_TAG]


def test_question_and_answer_are_in_the_same_conversation(tmp_path) -> None:
    """Il tag sceglie la *scheda*, non la sessione: quella resta una sola."""
    loop = _loop(tmp_path)
    inbound = _from_the_shade("cron:spesa")
    outbound = loop._assemble_outbound(inbound, "ok", [], "stop", False, None)

    assert inbound.session_key == UNIFIED_SESSION_KEY
    assert outbound is not None
    assert outbound.chat_id == inbound.chat_id
