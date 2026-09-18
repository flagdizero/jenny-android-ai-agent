"""Test per la gestione delle posizioni condivise via Telegram.

Una posizione (o venue) dall'owner viene registrata come override per-canale
(``runtime.location``) e innesca un turno LLM col marker sintetico; col toggle
posizione off si ricade sulla risposta di servizio "media_unsupported".
"""

from __future__ import annotations

import asyncio
import pathlib
import tempfile
from typing import Any

import pytest

from jenny.bus.queue import MessageBus
from jenny.channels.telegram import _LOCATION_TURN_MARKER, TelegramChannel
from jenny.config.paths import set_workspace_dir
from jenny.config.schema import Config, TelegramConfig
from jenny.runtime import location
from jenny.runtime.context import get_runtime_context


class FakeAPI:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str | None]] = []

    async def send_message(self, chat_id: str, text: str, *, parse_mode: str | None = None):
        self.sent.append((chat_id, text, parse_mode))
        return {"message_id": len(self.sent)}

    async def get_updates(self, offset, timeout_s):  # pragma: no cover
        return []

    async def close(self) -> None:  # pragma: no cover
        pass


@pytest.fixture(autouse=True)
def _clean_state():
    # Salva il workspace corrente e lo ripristina in teardown: senza restore
    # il path temporaneo inquinerebbe i test successivi della suite.
    previous = get_runtime_context().workspace_dir
    set_workspace_dir(pathlib.Path(tempfile.mkdtemp()))
    location.reset_location_state()
    yield
    location.reset_location_state()
    set_workspace_dir(previous if previous is not None else "")


def _channel(paired: str = "42") -> tuple[TelegramChannel, FakeAPI, MessageBus]:
    config = TelegramConfig(enabled=True, bot_token="TOKEN", paired_chat_id=paired)
    bus = MessageBus()
    api = FakeAPI()
    return TelegramChannel(config, bus, api=api, language="en"), api, bus


def _update(chat_id: str, **extra: Any) -> dict[str, Any]:
    return {
        "update_id": 1,
        "message": {"chat": {"id": chat_id}, "from": {"id": chat_id}, **extra},
    }


async def test_location_recorded_and_triggers_turn() -> None:
    ch, api, bus = _channel()
    await ch._handle_update(
        _update("42", location={"latitude": 45.4642, "longitude": 9.19})
    )
    msg = await asyncio.wait_for(bus.consume_inbound(), timeout=1)
    assert msg.content == _LOCATION_TURN_MARKER
    assert msg.channel == "telegram"
    # Registrata come override del canale.
    assert "42" in location._TELEGRAM
    fix = location._TELEGRAM["42"]
    assert fix.latitude == pytest.approx(45.4642)
    assert fix.source == "telegram"
    assert api.sent == []  # nessun "coming soon"


async def test_venue_uses_title_and_address_as_place() -> None:
    ch, _api, bus = _channel()
    await ch._handle_update(
        _update(
            "42",
            venue={
                "location": {"latitude": 44.49, "longitude": 11.34},
                "title": "Piazza Maggiore",
                "address": "Bologna",
            },
        )
    )
    await asyncio.wait_for(bus.consume_inbound(), timeout=1)
    assert location._TELEGRAM["42"].place == "Piazza Maggiore, Bologna"


async def test_toggle_off_falls_back_to_service_reply(monkeypatch) -> None:
    import jenny.config.loader as loader

    cfg = Config()
    cfg.tools.location.enable = False
    monkeypatch.setattr(loader, "load_config", lambda *a, **k: cfg)

    ch, api, bus = _channel()
    await ch._handle_update(
        _update("42", location={"latitude": 45.0, "longitude": 9.0})
    )
    # Toggle off: niente registrazione, niente turno, solo la risposta di
    # servizio. È il ramo ``_LOCATION_KEYS`` del canale: senza, una posizione
    # con il toggle spento sparirebbe in silenzio.
    assert "42" not in location._TELEGRAM
    assert bus.inbound.empty()
    assert len(api.sent) == 1
    assert "can't handle" in api.sent[0][1].lower()


async def test_malformed_location_is_not_a_turn() -> None:
    ch, api, bus = _channel()
    # location senza coordinate valide → non gestita come posizione, ricade
    # sulla risposta di servizio (è comunque tra le _LOCATION_KEYS).
    await ch._handle_update(_update("42", location={"latitude": "nope"}))
    assert bus.inbound.empty()
    assert "42" not in location._TELEGRAM
    assert len(api.sent) == 1
