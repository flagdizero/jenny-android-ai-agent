"""Un surrogato isolato nel messaggio non cancella la riga utente dal transcript.

``json.loads`` produce un surrogato isolato da un frame tagliato a metà di
un'emoji (``"\\ud83d"``), e in UTF-8 non esiste: la riga utente si scriveva nel
transcript *prima* della pulizia (che avviene più avanti, nel loop), la sua
codifica falliva, e il messaggio spariva dalla cronologia della WebUI mentre
l'agente gli rispondeva.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from support.ws_channel import make_ws_channel

from jenny.channels.websocket import WebSocketChannel
from jenny.webui.transcript import read_transcript_lines


@pytest.fixture(autouse=True)
def isolate_webui_workspace_state(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)


def _make_channel() -> WebSocketChannel:
    channel = make_ws_channel()
    channel._handle_message = AsyncMock()  # type: ignore[method-assign]
    return channel


async def test_a_lone_surrogate_keeps_the_user_row_in_the_transcript() -> None:
    channel = _make_channel()
    envelope = {"type": "message", "chat_id": "default", "content": "ciao \ud83d a te"}

    await channel._dispatch_envelope(AsyncMock(), "client-1", envelope)

    rows = read_transcript_lines("websocket:default")
    users = [row for row in rows if row.get("event") == "user"]
    assert [row.get("text") for row in users] == ["ciao � a te"]
    assert channel._handle_message.call_args.kwargs["content"] == "ciao � a te"
