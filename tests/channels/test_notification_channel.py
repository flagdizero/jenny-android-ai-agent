"""Test per ``jenny/channels/notification.py`` (la tendina come canale).

Il bridge Chaquopy non esiste fuori dal telefono: qui si sostituisce
``post_alert`` e si guarda **con che cosa** viene chiamato, più i due gate che
tengono la tendina pulita (eventi di coordinamento, messaggi vuoti).
"""

from __future__ import annotations

from typing import Any

import pytest

from jenny.bus.events import NOTIFICATION_CHANNEL, OutboundMessage
from jenny.channels import notification as nc
from jenny.channels.notification import REPLY_THREAD_TAG, NotificationChannel
from jenny.runtime.native_input import NATIVE_THREAD_KEY


class _Spy:
    def __init__(self, result: bool = True) -> None:
        self.calls: list[tuple[str, dict[str, Any] | None, str | None]] = []
        self.result = result

    async def __call__(self, content, metadata, *, thread=None):
        self.calls.append((content, metadata, thread))
        return self.result


@pytest.fixture
def spy(monkeypatch) -> _Spy:
    s = _Spy()
    monkeypatch.setattr(nc, "post_alert", s)
    return s


def _msg(content: str = "ecco fatto", **meta: Any) -> OutboundMessage:
    return OutboundMessage(
        channel=NOTIFICATION_CHANNEL, chat_id="shade", content=content, metadata=meta
    )


class TestContract:
    def test_the_channel_is_named_like_the_shared_constant(self):
        assert NotificationChannel.name == NOTIFICATION_CHANNEL

    def test_neither_progress_nor_reasoning(self):
        """Il dispatcher legge questi tre attributi prima di instradare."""
        assert NotificationChannel.send_progress is False
        assert NotificationChannel.send_tool_hints is False
        assert NotificationChannel.show_reasoning is False

    def test_a_single_attempt(self):
        """``post_alert`` non solleva: un retry ripubblicherebbe l'alert."""
        assert NotificationChannel.send_max_retries == 1

    async def test_start_and_stop_do_nothing(self):
        ch = NotificationChannel()
        assert await ch.start() is None
        assert await ch.stop() is None

    async def test_streaming_sends_return_an_empty_list(self):
        ch = NotificationChannel()
        assert await ch.send_delta("x") == []
        assert await ch.send_reasoning_delta("x") == []
        assert await ch.send_reasoning_end("x") == []
        assert await ch.send_file_edit_events("x") == []
        assert ch.discard_stream_buffer("x") is None


class TestSend:
    async def test_without_thread_posts_on_the_fallback(self, spy: _Spy):
        """Nessun tag d'origine: è il caso di una notifica postata da una
        versione precedente, il cui PendingIntent non porta l'extra."""
        ch = NotificationChannel()
        assert await ch.send(_msg("ecco fatto")) == []

        (content, _meta, thread) = spy.calls[0]
        assert content == "ecco fatto"
        assert thread == REPLY_THREAD_TAG

    async def test_posts_on_the_thread_the_question_came_from(self, spy: _Spy):
        """Il caso normale, e la correzione del difetto: la risposta torna sulla
        notifica a cui l'utente ha risposto, non su una scheda nuova."""
        ch = NotificationChannel()
        await ch.send(_msg("fatto", **{NATIVE_THREAD_KEY: "cron:spesa"}))
        assert spy.calls[0][2] == "cron:spesa"

    @pytest.mark.parametrize("bad", [None, "", "   ", 7, ["cron"]])
    async def test_a_malformed_thread_falls_back_to_the_fallback(self, spy: _Spy, bad):
        """Il valore attraversa il confine con Kotlin: un canale non si fida di
        ciò che gli entra da fuori del processo."""
        ch = NotificationChannel()
        await ch.send(_msg("fatto", **{NATIVE_THREAD_KEY: bad}))
        assert spy.calls[0][2] == REPLY_THREAD_TAG

    async def test_the_metadata_arrive_whole(self, spy: _Spy):
        """``alert_fields`` ne ricava il titolo: non vanno persi per strada."""
        ch = NotificationChannel()
        await ch.send(_msg("ping", webui_turn_id="t1"))
        assert spy.calls[0][1]["webui_turn_id"] == "t1"

    async def test_the_content_is_cleaned(self, spy: _Spy):
        ch = NotificationChannel()
        await ch.send(_msg("  con spazi  "))
        assert spy.calls[0][0] == "con spazi"

    @pytest.mark.parametrize("content", ["", "   "])
    async def test_an_empty_message_does_not_ring(self, spy: _Spy, content: str):
        ch = NotificationChannel()
        assert await ch.send(_msg(content)) == []
        assert spy.calls == []

    async def test_coordination_events_do_not_ring(self, spy: _Spy):
        """Il caso vero è ``_turn_end``: non porta flag di streaming, quindi il
        dispatcher lo fa arrivare fin qui. Senza questo gate ogni fine turno
        farebbe suonare una notifica."""
        ch = NotificationChannel()
        await ch.send(_msg("", _turn_end=True))
        await ch.send(_msg("testo di servizio", _progress=True))
        await ch.send(_msg("eco", _user_echo=True))
        await ch.send(_msg("delta", _stream_delta=True))
        assert spy.calls == []

    async def test_a_suppressed_alert_is_not_an_error(self, monkeypatch):
        """App in primo piano: ``post_alert`` ritorna False e il canale tace."""
        monkeypatch.setattr(nc, "post_alert", _Spy(result=False))
        ch = NotificationChannel()
        assert await ch.send(_msg("ciao")) == []

    async def test_media_do_not_block_the_text(self, spy: _Spy):
        """Un alert è testo: gli allegati restano in chat, il messaggio parte lo stesso."""
        msg = _msg("guarda qui")
        msg.media = ["/workspace/foto.png"]
        assert await NotificationChannel().send(msg) == []
        assert spy.calls[0][0] == "guarda qui"
