import pytest

from jenny.cron.bound_runner import origin_delivery_context
from jenny.cron.types import CronJob, CronPayload


def test_origin_delivery_context_uses_explicit_origin_fields() -> None:
    metadata = {
        "context_chat_id": "456",
        "parent_channel_id": "456",
        "thread_id": "777",
    }
    job = CronJob(
        id="thread-check",
        name="Thread check",
        payload=CronPayload(
            message="check",
            session_key="websocket:456:thread:777",
            origin_channel="websocket",
            origin_chat_id="777",
            origin_metadata=metadata,
        ),
    )

    channel, chat_id, returned_metadata = origin_delivery_context(job)

    assert channel == "websocket"
    assert chat_id == "777"
    assert returned_metadata == metadata
    assert returned_metadata is not metadata


def test_origin_delivery_context_rejects_missing_origin_fields() -> None:
    job = CronJob(
        id="old-bound",
        name="Old bound job",
        payload=CronPayload(
            message="check",
            session_key="websocket:chat-1",
        ),
    )

    with pytest.raises(ValueError, match="missing origin delivery context"):
        origin_delivery_context(job)


def test_a_reminder_asked_from_the_bubble_is_delivered_to_the_personal_chat() -> None:
    """Il fumetto si disegna solo a finestra aperta: quando il job scatta è
    chiusa, e la consegna a quel canale non arrivava a nessuno."""
    from jenny.cron.bound_runner import _bound_session_delivery_context

    job = CronJob(
        id="from-bubble",
        name="Chiama Anna",
        payload=CronPayload(
            message="chiama Anna",
            session_key="unified:default",
            origin_channel="floating",
            origin_chat_id="floating",
            origin_metadata={"anything": "of the bubble's turn"},
        ),
    )

    assert origin_delivery_context(job) == ("websocket", "default", {})
    channel, chat_id, metadata = _bound_session_delivery_context(
        job, turn_seed="cron:from-bubble", source_label="Chiama Anna"
    )
    assert (channel, chat_id) == ("websocket", "default")
    # Le stesse chiavi di consegna proattiva di un promemoria chiesto dall'app.
    twin = CronJob(
        id="from-bubble", name="Chiama Anna",
        payload=CronPayload(
            message="chiama Anna", session_key="unified:default",
            origin_channel="websocket", origin_chat_id="default",
        ),
    )
    _, _, expected = _bound_session_delivery_context(
        twin, turn_seed="cron:from-bubble", source_label="Chiama Anna"
    )
    assert metadata.keys() == expected.keys() and metadata
