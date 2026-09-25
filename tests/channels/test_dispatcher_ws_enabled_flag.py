"""Tests that ``websocket.enabled`` actually gates channel construction.

Prior to this fix, ``WebSocketDispatcher._init_channel`` built the WebSocket
channel whenever the ``websocket`` config section was non-empty, ignoring the
documented ``enabled`` flag (see docs/websocket.md, docs/configuration.md).
"""

from __future__ import annotations

from jenny.bus.queue import MessageBus
from jenny.channels.dispatcher import WebSocketDispatcher
from jenny.config.schema import Config


def test_websocket_enabled_false_skips_channel_construction():
    config = Config.model_validate({"websocket": {"enabled": False}})

    dispatcher = WebSocketDispatcher(config, MessageBus())

    assert "websocket" not in dispatcher.channels


def test_websocket_enabled_true_builds_channel():
    config = Config.model_validate(
        {"websocket": {"enabled": True, "websocketRequiresToken": False}}
    )

    dispatcher = WebSocketDispatcher(config, MessageBus())

    assert "websocket" in dispatcher.channels


def test_websocket_section_without_enabled_key_defaults_to_disabled():
    """Present-but-unspecified ``enabled`` falls back to the schema default
    (``False``) rather than being silently treated as on."""
    config = Config.model_validate({"websocket": {"websocketRequiresToken": False}})

    dispatcher = WebSocketDispatcher(config, MessageBus())

    assert "websocket" not in dispatcher.channels


def test_the_webui_commands_see_the_turns_in_flight():
    """``project.rename`` rifiuta una sessione con un turno in volo: il getter deve
    arrivare dal dispatcher fino al contesto dei comandi, e senza agente vale
    «nessun turno»."""
    config = Config.model_validate(
        {"websocket": {"enabled": True, "websocketRequiresToken": False}}
    )

    wired = WebSocketDispatcher(
        config, MessageBus(), get_busy_session_keys=lambda: ("project:viaggio",)
    )
    bare = WebSocketDispatcher(config, MessageBus())

    assert wired.channels["websocket"].gateway.commands.busy_session_keys() == (
        "project:viaggio",
    )
    assert tuple(bare.channels["websocket"].gateway.commands.busy_session_keys()) == ()
