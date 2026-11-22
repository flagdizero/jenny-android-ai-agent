"""I tasti in linea di Telegram: una domanda di Jenny con Ok / Sempre / No.

Un tasto premuto è un ordine quanto un messaggio: vale solo dalla persona
abbinata, nella sua chat.
"""

from __future__ import annotations

from typing import Any

from jenny.bus.queue import MessageBus
from jenny.channels.telegram import TelegramChannel
from jenny.config.schema import TelegramConfig


class _API:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.answered: list[tuple[str, str | None]] = []
        self.cleared: list[tuple[str, int]] = []

    async def send_message(self, chat_id: str, text: str, *, parse_mode: str | None = None,
                           buttons: list[tuple[str, str]] | None = None) -> dict[str, Any]:
        self.sent.append({"chat_id": chat_id, "text": text, "buttons": buttons})
        return {"message_id": 77}

    async def answer_callback(self, callback_id: str, text: str | None = None) -> None:
        self.answered.append((callback_id, text))

    async def clear_buttons(self, chat_id: str, message_id: int) -> None:
        self.cleared.append((chat_id, message_id))

    async def close(self) -> None:
        return None


def _channel(paired: str | None = "42") -> tuple[TelegramChannel, _API]:
    channel = TelegramChannel(
        TelegramConfig(enabled=True, bot_token="x", paired_chat_id=paired), MessageBus(),
    )
    api = _API()
    channel.api = api  # type: ignore[assignment]
    return channel, api


def _press(data: str, *, sender: int = 42, chat: int = 42) -> dict[str, Any]:
    return {"update_id": 1, "callback_query": {
        "id": "cb1", "from": {"id": sender}, "data": data,
        "message": {"message_id": 77, "chat": {"id": chat}},
    }}


async def test_a_question_goes_to_the_paired_chat_with_its_buttons() -> None:
    channel, api = _channel()
    message_id = await channel.send_buttons("Jenny sta per usare Gmail", [("No", "ph:c:no"), ("Ok", "ph:c:ok")])
    assert message_id == 77
    assert api.sent == [{"chat_id": "42", "text": "Jenny sta per usare Gmail",
                         "buttons": [("No", "ph:c:no"), ("Ok", "ph:c:ok")]}]
    await channel.clear_buttons(77)
    assert api.cleared == [("42", 77)]


async def test_no_paired_chat_no_question() -> None:
    channel, api = _channel(paired=None)
    assert await channel.send_buttons("x", [("Ok", "d")]) is None and api.sent == []


async def test_a_press_from_the_paired_person_reaches_the_handler() -> None:
    channel, api = _channel()
    got: list[str] = []

    async def handler(data: str) -> str | None:
        got.append(data)
        return "closed"

    channel.on_button = handler
    await channel._handle_update(_press("ph:abc:ok"))
    assert got == ["ph:abc:ok"] and api.answered == [("cb1", "closed")]


async def test_a_press_from_anyone_else_does_nothing() -> None:
    channel, api = _channel()
    got: list[str] = []

    async def handler(data: str) -> str | None:
        got.append(data)
        return None

    channel.on_button = handler
    await channel._handle_update(_press("ph:abc:ok", sender=7))
    await channel._handle_update(_press("ph:abc:ok", chat=-100))
    assert got == [] and api.answered == []


def test_the_api_asks_telegram_for_button_presses_too() -> None:
    import inspect

    from jenny.channels import telegram_api

    assert '"callback_query"' in inspect.getsource(telegram_api.TelegramAPI.get_updates)
