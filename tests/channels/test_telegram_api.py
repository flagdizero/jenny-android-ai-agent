"""Test per ``jenny.channels.telegram_api`` (client Bot API su httpx.MockTransport)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from jenny.channels.telegram_api import TelegramAPI, TelegramAPIError


def _make_api(handler) -> TelegramAPI:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return TelegramAPI("TOKEN", client=client)


async def test_get_me_returns_result() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/botTOKEN/getMe"
        return httpx.Response(200, json={"ok": True, "result": {"username": "jenny_bot"}})

    api = _make_api(handler)
    me = await api.get_me()
    assert me["username"] == "jenny_bot"
    await api.close()


async def test_get_updates_sends_offset_and_timeout() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(
            200, json={"ok": True, "result": [{"update_id": 7, "message": {}}]}
        )

    api = _make_api(handler)
    updates = await api.get_updates(5, 30)
    assert updates[0]["update_id"] == 7
    assert seen["offset"] == 5
    assert seen["timeout"] == 30
    assert seen["allowed_updates"] == ["message", "callback_query"]
    await api.close()


async def test_api_error_raises_with_description() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"ok": False, "description": "Bad Request: bad"})

    api = _make_api(handler)
    with pytest.raises(TelegramAPIError) as exc:
        await api.send_message("1", "ciao")
    assert exc.value.status_code == 400
    assert "bad" in exc.value.description
    await api.close()


async def test_rate_limit_retries_then_succeeds(monkeypatch) -> None:
    sleeps: list[float] = []

    async def fake_sleep(s: float) -> None:
        sleeps.append(s)

    monkeypatch.setattr("jenny.channels.telegram_api.asyncio.sleep", fake_sleep)

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(
                429, json={"ok": False, "parameters": {"retry_after": 3}}
            )
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    api = _make_api(handler)
    result = await api.send_message("1", "ciao")
    assert result["message_id"] == 1
    assert calls["n"] == 2
    assert sleeps == [3.0]
    await api.close()


async def test_non_json_error_body_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="<html>bad gateway</html>")

    api = _make_api(handler)
    with pytest.raises(TelegramAPIError) as exc:
        await api.get_me()
    assert exc.value.status_code == 502
    await api.close()
