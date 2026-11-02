"""Test per jenny/cron/webui_metadata.py.

Copre la derivazione dei metadata WebUI per le consegne cron proattive:
turn id univoco e sorgente strutturata solo sul canale ``websocket``, pulizia
di un turn id residuo su qualunque canale, e non-mutazione dell'input.
"""

from __future__ import annotations

from jenny.cron.bound_runner import cron_proactive_delivery_metadata
from jenny.webui.metadata import WEBUI_MESSAGE_SOURCE_METADATA_KEY, WEBUI_TURN_METADATA_KEY


def test_websocket_channel_adds_turn_id_and_source_with_label() -> None:
    out = cron_proactive_delivery_metadata(
        "websocket", {}, turn_seed="cron:job-1", source_label="spesa"
    )

    assert out[WEBUI_TURN_METADATA_KEY].startswith("cron:job-1:")
    assert out[WEBUI_MESSAGE_SOURCE_METADATA_KEY] == {"kind": "cron", "label": "spesa"}


def test_websocket_channel_without_label_omits_label_key() -> None:
    out = cron_proactive_delivery_metadata("websocket", {}, turn_seed="cron:job-1")

    assert out[WEBUI_MESSAGE_SOURCE_METADATA_KEY] == {"kind": "cron"}


def test_turn_id_is_unique_per_call() -> None:
    out1 = cron_proactive_delivery_metadata("websocket", {}, turn_seed="cron:job-1")
    out2 = cron_proactive_delivery_metadata("websocket", {}, turn_seed="cron:job-1")

    assert out1[WEBUI_TURN_METADATA_KEY] != out2[WEBUI_TURN_METADATA_KEY]


def test_non_websocket_channel_does_not_add_source() -> None:
    out = cron_proactive_delivery_metadata("internal", {}, turn_seed="cron:job-1")

    assert WEBUI_MESSAGE_SOURCE_METADATA_KEY not in out
    assert WEBUI_TURN_METADATA_KEY not in out


def test_non_websocket_channel_strips_existing_turn_id() -> None:
    """Anche fuori da websocket, un vecchio turn id residuo va ripulito."""
    stale = {WEBUI_TURN_METADATA_KEY: "old-turn-id"}
    out = cron_proactive_delivery_metadata("internal", stale, turn_seed="cron:job-1")

    assert WEBUI_TURN_METADATA_KEY not in out


def test_websocket_channel_replaces_existing_turn_id() -> None:
    stale = {WEBUI_TURN_METADATA_KEY: "old-turn-id"}
    out = cron_proactive_delivery_metadata("websocket", stale, turn_seed="cron:job-2")

    assert out[WEBUI_TURN_METADATA_KEY] != "old-turn-id"
    assert out[WEBUI_TURN_METADATA_KEY].startswith("cron:job-2:")


def test_preserves_other_metadata_keys() -> None:
    out = cron_proactive_delivery_metadata(
        "websocket", {"context_chat_id": "1"}, turn_seed="cron:job-1"
    )

    assert out["context_chat_id"] == "1"


def test_none_metadata_treated_as_empty() -> None:
    out = cron_proactive_delivery_metadata("websocket", None, turn_seed="cron:job-1")

    assert out[WEBUI_MESSAGE_SOURCE_METADATA_KEY] == {"kind": "cron"}


def test_does_not_mutate_input_metadata() -> None:
    original = {WEBUI_TURN_METADATA_KEY: "old", "keep": "me"}
    cron_proactive_delivery_metadata("websocket", original, turn_seed="cron:job-1")

    assert original == {WEBUI_TURN_METADATA_KEY: "old", "keep": "me"}
