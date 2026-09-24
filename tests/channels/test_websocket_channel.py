"""Unit and lightweight integration tests for the WebSocket channel."""

import asyncio
import functools
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import websockets
from port_alloc import free_port
from websockets.exceptions import ConnectionClosed
from websockets.frames import Close

from jenny.bus.events import OUTBOUND_META_AGENT_UI, OutboundMessage
from jenny.bus.queue import MessageBus
from jenny.channels.http_utils import (
    issue_route_secret_matches as _issue_route_secret_matches,
)
from jenny.channels.http_utils import (
    normalize_config_path as _normalize_config_path,
)
from jenny.channels.http_utils import (
    parse_query as _parse_query,
)
from jenny.channels.http_utils import (
    parse_request_path as _parse_request_path,
)
from jenny.channels.websocket import (
    WebSocketChannel,
    WebSocketConfig,
    _parse_envelope,
    _parse_inbound_payload,
)
from jenny.config.loader import load_config, save_config
from jenny.config.schema import Config
from jenny.runtime.context import get_runtime_context
from jenny.session.manager import SessionManager
from jenny.webui.gateway_services import GatewayServices, build_gateway_services
from jenny.webui.settings_api import settings_payload, update_provider
from jenny.webui.transcript import append_transcript_object, read_transcript_lines

# -- Shared helpers (aligned with test_websocket_integration.py) ---------------

def _ch(bus: Any, **kw: Any) -> WebSocketChannel:
    cfg: dict[str, Any] = {
        "enabled": True,
        "allowFrom": ["*"],
        "host": "127.0.0.1",
        # Porta libera per default: le sessioni pytest concorrenti non si pestano.
        "port": free_port(),
        "path": "/ws",
        "websocketRequiresToken": False,
    }
    cfg.update(kw)
    parsed = WebSocketConfig.model_validate(cfg)
    gateway = build_gateway_services(
        config=parsed,
        bus=bus,
        session_manager=None,
        workspace_path=Path.cwd(),
        default_restrict_to_workspace=False,
        runtime_model_name=None,
    )
    return WebSocketChannel(cfg, bus, gateway=gateway)


def _basic_handler(bus: Any, **kw: Any) -> GatewayServices:
    cfg = WebSocketConfig.model_validate({
        "enabled": True, "allowFrom": ["*"],
        "host": "127.0.0.1", "port": free_port(),
        "path": "/ws", "websocketRequiresToken": False,
    })
    return build_gateway_services(
        config=cfg,
        bus=bus,
        session_manager=kw.get("session_manager"),
        workspace_path=kw.get("workspace_path", Path.cwd()),
        default_restrict_to_workspace=kw.get("default_restrict_to_workspace", False),
        runtime_model_name=None,
    )


@pytest.fixture()
def bus() -> MagicMock:
    b = MagicMock()
    b.publish_inbound = AsyncMock()
    return b


@pytest.fixture(autouse=True)
def isolate_webui_workspace_state(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)


async def _http_get(url: str, headers: dict[str, str] | None = None) -> httpx.Response:
    """Run GET in a thread to avoid blocking the asyncio loop shared with websockets."""
    return await asyncio.to_thread(
        functools.partial(httpx.get, url, headers=headers or {}, timeout=5.0)
    )


async def _recv_ws_event(client: Any, event: str) -> dict[str, Any]:
    """Receive until a specific websocket event appears."""
    for _ in range(10):
        payload = json.loads(await client.recv())
        if payload.get("event") == event:
            return payload
    raise AssertionError(f"websocket event {event!r} was not received")


def _sent_ws_payloads(mock_ws: AsyncMock) -> list[dict[str, Any]]:
    return [json.loads(call.args[0]) for call in mock_ws.send.await_args_list]


def test_parse_request_path_strips_trailing_slash_except_root() -> None:
    assert _parse_request_path("/chat/")[0] == "/chat"
    assert _parse_request_path("/chat?x=1")[0] == "/chat"
    assert _parse_request_path("/")[0] == "/"


def test_parse_request_path_matches_parse_query() -> None:
    path, query = _parse_request_path("/ws/?token=secret&client_id=u1")
    assert path == "/ws"
    assert query == _parse_query("/ws/?token=secret&client_id=u1")


def test_normalize_config_path_matches_request() -> None:
    assert _normalize_config_path("/ws/") == "/ws"
    assert _normalize_config_path("/") == "/"


def test_parse_query_extracts_token_and_client_id() -> None:
    query = _parse_query("/?token=secret&client_id=u1")
    assert query.get("token") == ["secret"]
    assert query.get("client_id") == ["u1"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("plain", "plain"),
        ('{"content": "hi"}', "hi"),
        ('{"text": "there"}', "there"),
        ('{"message": "x"}', "x"),
        ("  ", None),
        ("{}", None),
    ],
)
def test_parse_inbound_payload(raw: str, expected: str | None) -> None:
    assert _parse_inbound_payload(raw) == expected


def test_parse_inbound_invalid_json_falls_back_to_raw_string() -> None:
    assert _parse_inbound_payload("{not json") == "{not json"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"content": ""}', None),           # empty string content
        ('{"content": 123}', None),          # non-string content
        ('{"content": "  "}', None),         # whitespace-only content
        ('["hello"]', '["hello"]'),           # JSON array: not a dict, treated as plain text
        ('{"unknown_key": "val"}', None),    # unrecognized key
        ('{"content": null}', None),         # null content
    ],
)
def test_parse_inbound_payload_edge_cases(raw: str, expected: str | None) -> None:
    assert _parse_inbound_payload(raw) == expected


def test_web_socket_config_path_must_start_with_slash() -> None:
    with pytest.raises(ValueError, match='path must start with "/"'):
        WebSocketConfig(path="bad")


def test_ssl_context_requires_both_cert_and_key_files() -> None:
    bus = MagicMock()
    channel = WebSocketChannel(
        {"enabled": True, "allowFrom": ["*"], "sslCertfile": "/tmp/c.pem", "sslKeyfile": ""},
        bus,
        gateway=_basic_handler(bus),
    )
    with pytest.raises(ValueError, match="ssl_certfile and ssl_keyfile"):
        channel._build_ssl_context()


def test_issue_route_secret_matches_bearer_and_header() -> None:
    from websockets.datastructures import Headers

    secret = "my-secret"
    bearer_headers = Headers([("Authorization", "Bearer my-secret")])
    assert _issue_route_secret_matches(bearer_headers, secret) is True
    x_headers = Headers([("X-Jenny-Auth", "my-secret")])
    assert _issue_route_secret_matches(x_headers, secret) is True
    wrong = Headers([("Authorization", "Bearer other")])
    assert _issue_route_secret_matches(wrong, secret) is False


def test_issue_route_secret_matches_empty_secret() -> None:
    from websockets.datastructures import Headers

    # Empty secret always returns True regardless of headers
    assert _issue_route_secret_matches(Headers([]), "") is True
    assert _issue_route_secret_matches(Headers([("Authorization", "Bearer anything")]), "") is True


@pytest.mark.asyncio
async def test_webui_message_envelope_marks_inbound_metadata(bus: MagicMock) -> None:
    from jenny.webui.transcript import read_transcript_lines

    channel = _ch(bus)
    conn = MagicMock()
    conn.remote_address = ("127.0.0.1", 50123)

    await channel._dispatch_envelope(
        conn,
        "webui-client",
        {
            "type": "message",
            "chat_id": "default",
            "content": "hello",
            "webui": True,
            "turn_id": "turn-1",
        },
    )

    msg = bus.publish_inbound.await_args.args[0]
    assert msg.channel == "websocket"
    assert msg.chat_id == "default"
    assert msg.metadata["webui"] is True
    assert msg.metadata["webui_turn_id"] == "turn-1"
    assert msg.metadata["_wants_stream"] is True
    lines = read_transcript_lines("websocket:default")
    assert lines == [{
        "event": "user",
        "chat_id": "default",
        "text": "hello",
        "turn_id": "turn-1",
        "turn_phase": "user",
        "turn_seq": 1,
    }]


@pytest.mark.asyncio
async def test_webui_message_envelope_persists_user_transcript_for_refresh(
    bus: MagicMock,
    tmp_path,
    monkeypatch,
) -> None:
    from jenny.webui.transcript import build_webui_thread_response, read_transcript_lines

    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)
    channel = _ch(bus)
    conn = AsyncMock()
    conn.remote_address = ("127.0.0.1", 50123)

    async def answer_during_publish(_msg: Any) -> None:
        await channel.send(OutboundMessage(channel="websocket", chat_id="default", content="hi back"))

    bus.publish_inbound.side_effect = answer_during_publish

    await channel._dispatch_envelope(
        conn,
        "webui-client",
        {"type": "message", "chat_id": "default", "content": "hello", "webui": True},
    )

    lines = read_transcript_lines("websocket:default")
    assert [line["event"] for line in lines] == ["user", "message"]

    body = build_webui_thread_response("websocket:default")
    assert body is not None
    assert [message["role"] for message in body["messages"]] == ["user", "assistant"]
    assert [message["content"] for message in body["messages"]] == ["hello", "hi back"]


@pytest.mark.asyncio
async def test_webui_stop_control_message_is_not_persisted_as_user_bubble(
    bus: MagicMock,
    tmp_path,
    monkeypatch,
) -> None:
    from jenny.webui.transcript import read_transcript_lines

    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)
    channel = _ch(bus)
    conn = AsyncMock()
    conn.remote_address = ("127.0.0.1", 50123)

    await channel._dispatch_envelope(
        conn,
        "webui-client",
        {"type": "message", "chat_id": "default", "content": "/stop", "webui": True},
    )

    msg = bus.publish_inbound.await_args.args[0]
    assert msg.content == "/stop"
    assert read_transcript_lines("websocket:default") == []


@pytest.mark.asyncio
async def test_webui_user_transcript_append_failure_does_not_block_inbound(
    bus: MagicMock,
    monkeypatch,
) -> None:
    def fail_append(_session_key: str, _obj: dict[str, Any]) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("jenny.webui.transcript.append_transcript_object", fail_append)
    channel = _ch(bus)
    conn = AsyncMock()
    conn.remote_address = ("127.0.0.1", 50123)

    await channel._dispatch_envelope(
        conn,
        "webui-client",
        {"type": "message", "chat_id": "default", "content": "hello", "webui": True},
    )

    msg = bus.publish_inbound.await_args.args[0]
    assert msg.chat_id == "default"
    assert msg.content == "hello"


@pytest.mark.asyncio
async def test_plain_websocket_message_does_not_mark_webui(bus: MagicMock) -> None:
    channel = _ch(bus)
    conn = MagicMock()

    await channel._dispatch_envelope(
        conn,
        "custom-client",
        {"type": "message", "chat_id": "default", "content": "hello"},
    )

    msg = bus.publish_inbound.await_args.args[0]
    assert "webui" not in msg.metadata


@pytest.mark.asyncio
async def test_webui_message_does_not_inject_or_persist_workspace_scope(
    bus: MagicMock,
    tmp_path,
) -> None:
    """Il path live non risolve/inietta/persiste piu lo scope: default costante.

    La risoluzione dello scope vive interamente nell'AgentLoop (che legge la
    metadata di sessione): il canale non deve aggiungere ``workspace_scope`` alla
    metadata del messaggio ne scriverlo in sessione.
    """
    default_workspace = tmp_path / "default"
    default_workspace.mkdir()
    sessions = SessionManager(tmp_path / "sessions")
    channel = WebSocketChannel(
        {"enabled": True, "allowFrom": ["*"], "host": "127.0.0.1"},
        bus,
        gateway=_basic_handler(
            bus, session_manager=sessions, workspace_path=default_workspace
        ),
    )
    conn = AsyncMock()
    conn.remote_address = ("127.0.0.1", 50123)

    await channel._dispatch_envelope(
        conn,
        "webui-client",
        {"type": "message", "chat_id": "default", "content": "hello", "webui": True},
    )

    msg = bus.publish_inbound.await_args.args[0]
    assert "workspace_scope" not in msg.metadata
    # Il canale non crea/aggiorna piu la sessione per persistere lo scope:
    # o il file non esiste, o comunque non contiene ``workspace_scope``.
    saved = sessions.read_session_file("websocket:default")
    assert "workspace_scope" not in (saved or {}).get("metadata", {})


@pytest.mark.asyncio
async def test_set_workspace_scope_is_unknown_type(bus: MagicMock, tmp_path) -> None:
    """Il tipo di envelope ``set_workspace_scope`` non esiste piu (switching rimosso)."""
    default_workspace = tmp_path / "default"
    project = tmp_path / "project"
    default_workspace.mkdir()
    project.mkdir()
    channel = WebSocketChannel(
        {"enabled": True, "allowFrom": ["*"], "host": "127.0.0.1"},
        bus,
        gateway=_basic_handler(
            bus,
            session_manager=SessionManager(tmp_path / "sessions"),
            workspace_path=default_workspace,
        ),
    )
    conn = AsyncMock()
    conn.remote_address = ("127.0.0.1", 50123)

    await channel._dispatch_envelope(
        conn,
        "webui-client",
        {
            "type": "set_workspace_scope",
            "chat_id": "default",
            "workspace_scope": {
                "project_path": str(project),
                "access_mode": "full",
            },
        },
    )

    payload = json.loads(conn.send.await_args.args[0])
    assert payload["event"] == "error"
    assert payload["detail"].startswith("unknown type")
    bus.publish_inbound.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_delivers_json_message_with_media_and_reply() -> None:
    bus = MagicMock()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus, gateway=_basic_handler(bus))
    mock_ws = AsyncMock()
    channel._attach(mock_ws, "default")

    msg = OutboundMessage(
        channel="websocket",
        chat_id="default",
        content="hello",
        media=["/tmp/a.png"],
        buttons=[["Yes", "No"]],
    )
    await channel.send(msg)

    mock_ws.send.assert_awaited_once()
    payload = json.loads(mock_ws.send.call_args[0][0])
    assert payload["event"] == "message"
    assert payload["chat_id"] == "default"
    assert payload["text"] == "hello"
    assert payload["media"] == ["/tmp/a.png"]


@pytest.mark.asyncio
async def test_send_broadcasts_runtime_model_updates() -> None:
    bus = MessageBus()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus, gateway=_basic_handler(bus))
    mock_ws = AsyncMock()
    channel._attach(mock_ws, "default")

    await channel.send(OutboundMessage(
        channel="websocket",
        chat_id="*",
        content="",
        metadata={
            "_runtime_model_updated": True,
            "model": "openai/gpt-4.1",
            "model_preset": "fast",
            "provider": "openai",
        },
    ))

    payload = json.loads(mock_ws.send.call_args[0][0])
    assert payload["event"] == "runtime_model_updated"
    assert payload["model_name"] == "openai/gpt-4.1"
    assert payload["model_preset"] == "fast"
    assert payload["provider"] == "openai"


@pytest.mark.asyncio
async def test_send_runtime_model_update_omits_blank_provider() -> None:
    bus = MessageBus()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus, gateway=_basic_handler(bus))
    mock_ws = AsyncMock()
    channel._attach(mock_ws, "default")

    await channel.send(OutboundMessage(
        channel="websocket",
        chat_id="*",
        content="",
        metadata={
            "_runtime_model_updated": True,
            "model": "m1",
            "model_preset": None,
            "provider": "   ",
        },
    ))

    payload = json.loads(mock_ws.send.call_args[0][0])
    assert payload["event"] == "runtime_model_updated"
    assert payload["model_name"] == "m1"
    assert "model_preset" not in payload
    assert "provider" not in payload


@pytest.mark.asyncio
async def test_send_stages_external_media_as_signed_url(monkeypatch, tmp_path) -> None:
    bus = MagicMock()
    media_root = tmp_path / "media"
    ws_media = media_root / "websocket"
    ws_media.mkdir(parents=True)
    external = tmp_path / "clip.mp4"
    external.write_bytes(b"video")

    def fake_media_dir(channel: str | None = None):
        return ws_media if channel == "websocket" else media_root

    monkeypatch.setattr("jenny.webui.media_gateway.get_media_dir", fake_media_dir)
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus, gateway=_basic_handler(bus))
    mock_ws = AsyncMock()
    channel._attach(mock_ws, "default")

    await channel.send(
        OutboundMessage(
            channel="websocket",
            chat_id="default",
            content="video",
            media=[str(external)],
        )
    )

    payload = json.loads(mock_ws.send.call_args[0][0])
    assert payload["media"] == [str(external)]
    assert payload["media_urls"][0]["name"] == "clip.mp4"
    assert payload["media_urls"][0]["url"].startswith("/api/media/")
    assert any(p.name.endswith("-clip.mp4") for p in ws_media.iterdir())


@pytest.mark.asyncio
async def test_send_missing_connection_is_noop_without_error() -> None:
    bus = MagicMock()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus, gateway=_basic_handler(bus))
    msg = OutboundMessage(channel="websocket", chat_id="missing", content="x")
    await channel.send(msg)
    assert channel._subs == {}


@pytest.mark.asyncio
async def test_send_removes_connection_on_connection_closed() -> None:
    bus = MagicMock()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus, gateway=_basic_handler(bus))
    mock_ws = AsyncMock()
    mock_ws.send.side_effect = ConnectionClosed(Close(1006, ""), Close(1006, ""), True)
    channel._attach(mock_ws, "default")

    msg = OutboundMessage(channel="websocket", chat_id="default", content="hello")
    await channel.send(msg)

    assert "default" not in channel._subs
    assert mock_ws not in channel._conn_chats


@pytest.mark.asyncio
async def test_send_progress_includes_structured_tool_events() -> None:
    bus = MagicMock()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus, gateway=_basic_handler(bus))
    mock_ws = AsyncMock()
    channel._attach(mock_ws, "default")

    await channel.send(OutboundMessage(
        channel="websocket",
        chat_id="default",
        content='search "hermes"',
        metadata={
            "_progress": True,
            "_tool_hint": True,
            "webui_turn_id": "turn-1",
            "_tool_events": [
                {
                    "version": 1,
                    "phase": "start",
                    "call_id": "call-1",
                    "name": "web_search",
                    "arguments": {"query": "hermes", "count": 8},
                    "result": None,
                    "error": None,
                    "files": [],
                    "embeds": [],
                }
            ],
        },
    ))

    payload = json.loads(mock_ws.send.await_args.args[0])
    assert payload["event"] == "message"
    assert payload["kind"] == "tool_hint"
    assert payload["turn_id"] == "turn-1"
    assert payload["turn_phase"] == "activity"
    assert payload["turn_seq"] == 1
    assert payload["tool_events"] == [
        {
            "version": 1,
            "phase": "start",
            "call_id": "call-1",
            "name": "web_search",
            "arguments": {"query": "hermes", "count": 8},
            "result": None,
            "error": None,
            "files": [],
            "embeds": [],
        }
    ]


@pytest.mark.asyncio
async def test_send_file_edit_progress_uses_file_edit_event() -> None:
    bus = MagicMock()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus, gateway=_basic_handler(bus))
    mock_ws = AsyncMock()
    channel._attach(mock_ws, "default")

    await channel.send(OutboundMessage(
        channel="websocket",
        chat_id="default",
        content="",
        metadata={
            "_progress": True,
            "_file_edit_events": [
                {
                    "version": 1,
                    "phase": "start",
                    "call_id": "call-1",
                    "tool": "write_file",
                    "path": "src/app.py",
                    "added": 12,
                    "deleted": 2,
                    "approximate": True,
                    "status": "editing",
                }
            ],
        },
    ))

    payload = json.loads(mock_ws.send.await_args.args[0])
    assert payload == {
        "event": "file_edit",
        "chat_id": "default",
        "edits": [
            {
                "version": 1,
                "phase": "start",
                "call_id": "call-1",
                "tool": "write_file",
                "path": "src/app.py",
                "added": 12,
                "deleted": 2,
                "approximate": True,
                "status": "editing",
            }
        ],
    }


@pytest.mark.asyncio
async def test_send_progress_includes_agent_ui_blob() -> None:
    bus = MagicMock()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus, gateway=_basic_handler(bus))
    mock_ws = AsyncMock()
    channel._attach(mock_ws, "default")

    blob = {
        "kind": "panel",
        "data": {"version": 1, "event": "tick", "id": "r1"},
    }
    await channel.send(OutboundMessage(
        channel="websocket",
        chat_id="default",
        content="progress · panel",
        metadata={"_progress": True, OUTBOUND_META_AGENT_UI: blob},
    ))

    payload = json.loads(mock_ws.send.await_args.args[0])
    assert payload["event"] == "message"
    assert payload["kind"] == "progress"
    assert payload["agent_ui"] == blob


@pytest.mark.asyncio
async def test_send_delta_removes_connection_on_connection_closed() -> None:
    bus = MagicMock()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"], "streaming": True}, bus, gateway=_basic_handler(bus))
    mock_ws = AsyncMock()
    mock_ws.send.side_effect = ConnectionClosed(Close(1006, ""), Close(1006, ""), True)
    channel._attach(mock_ws, "default")

    await channel.send_delta("default", "chunk", {"_stream_delta": True, "_stream_id": "s1"})

    assert "default" not in channel._subs
    assert mock_ws not in channel._conn_chats


@pytest.mark.asyncio
async def test_send_delta_emits_delta_and_stream_end() -> None:
    bus = MagicMock()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"], "streaming": True}, bus, gateway=_basic_handler(bus))
    mock_ws = AsyncMock()
    channel._attach(mock_ws, "default")

    await channel.send_delta("default", "part", {"_stream_delta": True, "_stream_id": "sid"})
    await channel.send_delta("default", "", {"_stream_end": True, "_stream_id": "sid"})

    assert mock_ws.send.await_count == 2
    first = json.loads(mock_ws.send.call_args_list[0][0][0])
    second = json.loads(mock_ws.send.call_args_list[1][0][0])
    assert first["event"] == "delta"
    assert first["chat_id"] == "default"
    assert first["text"] == "part"
    assert first["stream_id"] == "sid"
    assert second["event"] == "stream_end"
    assert second["chat_id"] == "default"
    assert second["stream_id"] == "sid"
    assert "text" not in second


@pytest.mark.asyncio
async def test_send_delta_stream_end_includes_inline_final_text() -> None:
    bus = MagicMock()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"], "streaming": True}, bus, gateway=_basic_handler(bus))
    mock_ws = AsyncMock()
    channel._attach(mock_ws, "default")

    await channel.send_delta(
        "default",
        "merged plain text",
        {"_stream_delta": True, "_stream_end": True, "_stream_id": "sid"},
    )

    mock_ws.send.assert_awaited_once()
    final = json.loads(mock_ws.send.await_args.args[0])
    assert final["event"] == "stream_end"
    assert final["chat_id"] == "default"
    assert final["stream_id"] == "sid"
    assert final["text"] == "merged plain text"


@pytest.mark.asyncio
async def test_send_delta_stream_end_rewrites_local_markdown_image(monkeypatch, tmp_path) -> None:
    bus = MagicMock()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "diagram.png").write_bytes(b"\x89PNG\r\n\x1a\nimage")
    media = tmp_path / "media"

    def fake_media_dir(channel: str | None = None):
        path = media / channel if channel else media
        path.mkdir(parents=True, exist_ok=True)
        return path

    monkeypatch.setattr("jenny.webui.media_gateway.get_media_dir", fake_media_dir)
    channel = WebSocketChannel(
        {"enabled": True, "allowFrom": ["*"], "streaming": True},
        bus,
        gateway=_basic_handler(bus, workspace_path=workspace),
    )
    mock_ws = AsyncMock()
    channel._attach(mock_ws, "default")

    await channel.send_delta("default", "![Diagram](", {"_stream_delta": True, "_stream_id": "sid"})
    await channel.send_delta("default", "diagram.png)", {"_stream_delta": True, "_stream_id": "sid"})
    await channel.send_delta("default", "", {"_stream_end": True, "_stream_id": "sid"})

    assert mock_ws.send.await_count == 3
    final = json.loads(mock_ws.send.call_args_list[2][0][0])
    assert final["event"] == "stream_end"
    assert final["text"].startswith("![Diagram](/api/media/")


@pytest.mark.asyncio
async def test_send_delta_stream_end_rewrites_inline_final_text(monkeypatch, tmp_path) -> None:
    bus = MagicMock()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "diagram.png").write_bytes(b"\x89PNG\r\n\x1a\nimage")
    media = tmp_path / "media"

    def fake_media_dir(channel: str | None = None):
        path = media / channel if channel else media
        path.mkdir(parents=True, exist_ok=True)
        return path

    monkeypatch.setattr("jenny.webui.media_gateway.get_media_dir", fake_media_dir)
    channel = WebSocketChannel(
        {"enabled": True, "allowFrom": ["*"], "streaming": True},
        bus,
        gateway=_basic_handler(bus, workspace_path=workspace),
    )
    mock_ws = AsyncMock()
    channel._attach(mock_ws, "default")

    await channel.send_delta(
        "default",
        "![Diagram](diagram.png)",
        {"_stream_delta": True, "_stream_end": True, "_stream_id": "sid"},
    )

    mock_ws.send.assert_awaited_once()
    final = json.loads(mock_ws.send.await_args.args[0])
    assert final["event"] == "stream_end"
    assert final["text"].startswith("![Diagram](/api/media/")


@pytest.mark.asyncio
async def test_send_reasoning_delta_emits_streaming_frame() -> None:
    bus = MagicMock()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus, gateway=_basic_handler(bus))
    mock_ws = AsyncMock()
    channel._attach(mock_ws, "default")

    await channel.send_reasoning_delta(
        "default",
        "step-by-step thinking",
        {"_reasoning_delta": True, "_stream_id": "r1"},
    )

    mock_ws.send.assert_awaited_once()
    payload = json.loads(mock_ws.send.await_args.args[0])
    assert payload["event"] == "reasoning_delta"
    assert payload["chat_id"] == "default"
    assert payload["text"] == "step-by-step thinking"
    assert payload["stream_id"] == "r1"


@pytest.mark.asyncio
async def test_send_reasoning_end_emits_close_frame() -> None:
    bus = MagicMock()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus, gateway=_basic_handler(bus))
    mock_ws = AsyncMock()
    channel._attach(mock_ws, "default")

    await channel.send_reasoning_end("default", {"_reasoning_end": True, "_stream_id": "r1"})

    payload = json.loads(mock_ws.send.await_args.args[0])
    assert payload == {"event": "reasoning_end", "chat_id": "default", "stream_id": "r1"}


@pytest.mark.asyncio
async def test_send_reasoning_delta_drops_empty_chunks() -> None:
    bus = MagicMock()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus, gateway=_basic_handler(bus))
    mock_ws = AsyncMock()
    channel._attach(mock_ws, "default")

    await channel.send_reasoning_delta("default", "", {"_reasoning_delta": True})

    mock_ws.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_reasoning_without_subscribers_is_noop() -> None:
    bus = MagicMock()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus, gateway=_basic_handler(bus))

    await channel.send_reasoning_delta("unattached", "thinking", None)
    await channel.send_reasoning_end("unattached", None)
    assert channel._subs == {}


@pytest.mark.asyncio
async def test_stream_transcript_persists_without_subscribers() -> None:
    from jenny.webui.transcript import build_webui_thread_response, read_transcript_lines

    bus = MagicMock()
    channel = WebSocketChannel(
        {"enabled": True, "allowFrom": ["*"], "streaming": True},
        bus,
        gateway=_basic_handler(bus),
    )

    await channel.send_delta("default", "hello", {"_stream_delta": True, "_stream_id": "s1"})
    await channel.send_delta("default", " world", {"_stream_delta": True, "_stream_id": "s1"})
    await channel.send_delta("default", "", {"_stream_end": True, "_stream_id": "s1"})
    await channel.send(OutboundMessage(
        channel="websocket",
        chat_id="default",
        content="",
        metadata={"_turn_end": True, "latency_ms": 42},
    ))

    assert channel._subs == {}
    lines = read_transcript_lines("websocket:default")
    assert [line["event"] for line in lines] == ["delta", "delta", "stream_end", "turn_end"]
    body = build_webui_thread_response("websocket:default")
    assert body is not None
    assert body["messages"][-1]["role"] == "assistant"
    assert body["messages"][-1]["content"] == "hello world"
    assert body["messages"][-1]["latencyMs"] == 42


@pytest.mark.asyncio
async def test_proactive_delivery_frames_form_one_closed_turn() -> None:
    """I due frame che ``ChannelDeliverer`` pubblica per un avviso proattivo.

    Un turno silenzioso (heartbeat, cron, Dream) non ha vista WebUI, quindi il
    coordinator non emette nessun ``turn_end``: lo emette il deliverer, con lo
    stesso ``webui_turn_id`` del messaggio. È quell'id a far annotare al
    recorder il record di chiusura come ``complete`` dello stesso turno — senza,
    ``_annotate_turn`` esce subito e il turno resta aperto sia sul filo che sul
    disco.
    """
    from jenny.webui.metadata import WEBUI_TURN_METADATA_KEY
    from jenny.webui.transcript import read_transcript_lines

    bus = MagicMock()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus, gateway=_basic_handler(bus))
    mock_ws = AsyncMock()
    channel._attach(mock_ws, "default")
    turn_id = "proactive:deadbeef"

    await channel.send(OutboundMessage(
        channel="websocket",
        chat_id="default",
        content="il monitoraggio non sta girando",
        metadata={WEBUI_TURN_METADATA_KEY: turn_id},
    ))
    await channel.send(OutboundMessage(
        channel="websocket",
        chat_id="default",
        content="",
        metadata={WEBUI_TURN_METADATA_KEY: turn_id, "_turn_end": True},
    ))

    # Sul filo: il client vede il turno chiudersi (è il frame che riporta la
    # mascotte a idle e azzera lo stato di stream), e ogni frame porta l'id del
    # proprio turno — è quello che permette al client di non applicare la
    # chiusura a un turno che non è il suo.
    payloads = _sent_ws_payloads(mock_ws)
    assert [p["event"] for p in payloads] == ["message", "turn_end"]
    assert [p.get("turn_id") for p in payloads[:2]] == [turn_id, turn_id]
    # Sul disco: un turno solo, aperto dalla risposta e chiuso da `complete`.
    lines = read_transcript_lines("websocket:default")
    assert [(line["event"], line.get("turn_id"), line.get("turn_phase")) for line in lines] == [
        ("message", turn_id, "answer"),
        ("turn_end", turn_id, "complete"),
    ]


@pytest.mark.asyncio
async def test_send_turn_end_emits_turn_end_event() -> None:
    bus = MagicMock()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus, gateway=_basic_handler(bus))
    mock_ws = AsyncMock()
    channel._attach(mock_ws, "default")

    await channel.send(OutboundMessage(
        channel="websocket",
        chat_id="default",
        content="",
        metadata={"_turn_end": True},
    ))

    assert _sent_ws_payloads(mock_ws) == [
        {"event": "turn_end", "chat_id": "default"},
    ]


@pytest.mark.asyncio
async def test_send_turn_end_includes_latency_ms_when_present() -> None:
    bus = MagicMock()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus, gateway=_basic_handler(bus))
    mock_ws = AsyncMock()
    channel._attach(mock_ws, "default")

    await channel.send(OutboundMessage(
        channel="websocket",
        chat_id="default",
        content="",
        metadata={"_turn_end": True, "latency_ms": 1500},
    ))

    assert _sent_ws_payloads(mock_ws) == [
        {"event": "turn_end", "chat_id": "default", "latency_ms": 1500},
    ]


@pytest.mark.asyncio
async def test_send_goal_status_running_emits_event_with_started_at() -> None:
    bus = MagicMock()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus, gateway=_basic_handler(bus))
    mock_ws = AsyncMock()
    channel._attach(mock_ws, "default")

    await channel.send(OutboundMessage(
        channel="websocket",
        chat_id="default",
        content="",
        metadata={
            "_goal_status": True,
            "goal_status": "running",
            "started_at": 1_700_000_000.5,
        },
    ))

    mock_ws.send.assert_awaited_once()
    body = json.loads(mock_ws.send.await_args.args[0])
    assert body == {
        "event": "goal_status",
        "chat_id": "default",
        "status": "running",
        "started_at": 1_700_000_000.5,
    }


@pytest.mark.asyncio
async def test_send_mascot_mood_emits_a_dedicated_frame_with_turn_id() -> None:
    """L'umore della mascotte: un frame suo, mai una bolla, mai nel transcript."""
    bus = MagicMock()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus, gateway=_basic_handler(bus))
    mock_ws = AsyncMock()
    channel._attach(mock_ws, "default")
    channel._transcripts = MagicMock()

    await channel.send(OutboundMessage(
        channel="websocket",
        chat_id="default",
        content="",
        metadata={"_mascot_mood": True, "mascot_mood": "happy", "webui_turn_id": "t-9"},
    ))

    mock_ws.send.assert_awaited_once()
    body = json.loads(mock_ws.send.await_args.args[0])
    assert body == {"event": "mascot_mood", "chat_id": "default", "mood": "happy", "turn_id": "t-9"}
    channel._transcripts.prepare_and_append.assert_not_called()


@pytest.mark.asyncio
async def test_send_mascot_mood_omits_turn_id_and_reaches_only_that_chat() -> None:
    bus = MagicMock()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus, gateway=_basic_handler(bus))
    ws_default = AsyncMock()
    ws_other = AsyncMock()
    channel._attach(ws_default, "default")
    channel._attach(ws_other, "other")

    await channel.send(OutboundMessage(
        channel="websocket",
        chat_id="default",
        content="",
        metadata={"_mascot_mood": True, "mascot_mood": "sad"},
    ))

    assert json.loads(ws_default.send.await_args.args[0]) == {
        "event": "mascot_mood", "chat_id": "default", "mood": "sad",
    }
    ws_other.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_mascot_mood_without_subscribers_sends_nothing() -> None:
    bus = MagicMock()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus, gateway=_basic_handler(bus))
    channel._transcripts = MagicMock()
    pending = await channel.send(OutboundMessage(
        channel="websocket", chat_id="default", content="",
        metadata={"_mascot_mood": True, "mascot_mood": "sad"},
    ))
    assert pending == []
    channel._transcripts.prepare_and_append.assert_not_called()


@pytest.mark.asyncio
async def test_send_goal_status_idle_omits_started_at() -> None:
    bus = MagicMock()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus, gateway=_basic_handler(bus))
    mock_ws = AsyncMock()
    channel._attach(mock_ws, "default")

    await channel.send(OutboundMessage(
        channel="websocket",
        chat_id="default",
        content="",
        metadata={
            "_goal_status": True,
            "goal_status": "idle",
            "goal_started_at": 99.0,
        },
    ))

    mock_ws.send.assert_awaited_once()
    body = json.loads(mock_ws.send.await_args.args[0])
    assert body == {"event": "goal_status", "chat_id": "default", "status": "idle"}


@pytest.mark.asyncio
async def test_maybe_push_turn_run_wall_clock_skips_when_no_active_turn() -> None:
    bus = MagicMock()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus, gateway=_basic_handler(bus))
    mock_ws = AsyncMock()
    channel._attach(mock_ws, "default")
    from jenny.session import webui_turns as wth

    wth._WEBSOCKET_TURN_WALL_STARTED_AT.clear()
    await channel._maybe_push_turn_run_wall_clock("default")
    mock_ws.send.assert_not_called()


@pytest.mark.asyncio
async def test_maybe_push_turn_run_wall_clock_replays_running() -> None:
    bus = MagicMock()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus, gateway=_basic_handler(bus))
    mock_ws = AsyncMock()
    channel._attach(mock_ws, "default")
    from jenny.session import webui_turns as wth

    wth._WEBSOCKET_TURN_WALL_STARTED_AT.clear()
    try:
        wth._WEBSOCKET_TURN_WALL_STARTED_AT["default"] = 1_700_000_000.0
        await channel._maybe_push_turn_run_wall_clock("default")
    finally:
        wth._WEBSOCKET_TURN_WALL_STARTED_AT.pop("default", None)

    mock_ws.send.assert_awaited_once()
    body = json.loads(mock_ws.send.await_args.args[0])
    assert body == {
        "event": "goal_status",
        "chat_id": "default",
        "status": "running",
        "started_at": 1_700_000_000.0,
    }


@pytest.mark.asyncio
async def test_send_non_connection_closed_exception_is_collected_not_raised() -> None:
    """A per-connection send failure must not abort the whole ``send()`` call.

    Regression: ``send()`` used to re-raise on the first non-ConnectionClosed
    error, which the dispatcher's retry then turned into a full re-send
    (re-persisting the transcript row and resending to every connection,
    including ones that already got the message). Now the failing connection
    is reported back via the returned pending list so only it gets retried.
    """
    bus = MagicMock()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus, gateway=_basic_handler(bus))
    mock_ws = AsyncMock()
    mock_ws.send.side_effect = RuntimeError("unexpected")
    channel._attach(mock_ws, "default")

    msg = OutboundMessage(channel="websocket", chat_id="default", content="hello")
    pending = await channel.send(msg)

    assert pending == [mock_ws]
    mock_ws.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_delta_missing_connection_is_noop() -> None:
    bus = MagicMock()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"], "streaming": True}, bus, gateway=_basic_handler(bus))
    # No exception, no error — just a no-op
    await channel.send_delta("nonexistent", "chunk", {"_stream_delta": True, "_stream_id": "s1"})
    assert channel._subs == {}


@pytest.mark.asyncio
async def test_stop_is_idempotent() -> None:
    bus = MagicMock()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus, gateway=_basic_handler(bus))
    # stop() before start() should not raise
    await channel.stop()
    await channel.stop()
    assert channel._subs == {}


@pytest.mark.asyncio
async def test_end_to_end_client_receives_ready_and_agent_sees_inbound(bus: MagicMock) -> None:
    port = free_port()
    channel = _ch(bus, port=port)

    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws?client_id=tester") as client:
            ready_raw = await client.recv()
            ready = json.loads(ready_raw)
            assert ready["event"] == "ready"
            assert ready["client_id"] == "tester"
            chat_id = ready["chat_id"]

            await client.send(json.dumps({"content": "ping from client"}))
            await asyncio.sleep(0.08)

            bus.publish_inbound.assert_awaited()
            inbound = bus.publish_inbound.call_args[0][0]
            assert inbound.channel == "websocket"
            assert inbound.sender_id == "tester"
            assert inbound.chat_id == chat_id
            assert inbound.content == "ping from client"

            await client.send("plain text frame")
            await asyncio.sleep(0.08)
            assert bus.publish_inbound.await_count >= 2
            second = [c[0][0] for c in bus.publish_inbound.call_args_list][-1]
            assert second.content == "plain text frame"
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_token_rejects_handshake_when_mismatch(bus: MagicMock) -> None:
    port = free_port()
    channel = _ch(bus, port=port, path="/", tokenIssueSecret="secret", websocketRequiresToken=True)

    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        with pytest.raises(websockets.exceptions.InvalidStatus) as excinfo:
            async with websockets.connect(f"ws://127.0.0.1:{port}/?token=wrong"):
                pass
        assert excinfo.value.response.status_code == 401
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_wrong_path_returns_webui_html(bus: MagicMock) -> None:
    """Non-WebSocket paths serve the WebUI HTML instead of returning 404."""
    port = free_port()
    channel = _ch(bus, port=port)

    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        with pytest.raises(websockets.exceptions.InvalidStatus) as excinfo:
            async with websockets.connect(f"ws://127.0.0.1:{port}/other"):
                pass
        assert excinfo.value.response.status_code == 200
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_secret_required_for_websocket_handshake(bus: MagicMock) -> None:
    port = free_port()
    secret = "route-secret"
    channel = _ch(
        bus, port=port,
        tokenIssueSecret=secret,
        websocketRequiresToken=True,
    )

    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        with pytest.raises(websockets.exceptions.InvalidStatus) as missing_token:
            async with websockets.connect(f"ws://127.0.0.1:{port}/ws?client_id=x"):
                pass
        assert missing_token.value.response.status_code == 401

        uri = f"ws://127.0.0.1:{port}/ws?token={secret}&client_id=caller"
        async with websockets.connect(uri) as client:
            ready = json.loads(await client.recv())
            assert ready["event"] == "ready"
            assert ready["client_id"] == "caller"

        # Reconnects with the same secret must still succeed.
        async with websockets.connect(uri) as client:
            ready_again = json.loads(await client.recv())
            assert ready_again["event"] == "ready"
            assert ready_again["client_id"] == "caller"

        # Wrong secret is rejected.
        bogus_uri = f"ws://127.0.0.1:{port}/ws?token=wrong-secret&client_id=caller"
        with pytest.raises(websockets.exceptions.InvalidStatus) as bogus_token:
            async with websockets.connect(bogus_uri) as client:
                pass
        assert bogus_token.value.response.status_code == 401
    finally:
        await channel.stop()
        await server_task




def test_settings_payload_normalizes_camel_case_provider(
    bus: MagicMock,
    monkeypatch,
    tmp_path,
) -> None:
    config_path = tmp_path / "config.json"
    config = Config()
    config.providers.default = "minimax_anthropic"
    save_config(config, config_path)
    monkeypatch.setattr(get_runtime_context(), "config_path", config_path)

    body = settings_payload()

    assert body["default_provider"] == "minimax_anthropic"


def test_settings_payload_exposes_api_type_only_for_openai(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config = Config()
    from jenny.config.schema import ProviderConfig
    config.providers.providers = [ProviderConfig(name="openai", format="openai_compat", api_key="test-key", api_type="responses")]
    config.providers.default = "openai"
    save_config(config, config_path)
    monkeypatch.setattr(get_runtime_context(), "config_path", config_path)

    body = settings_payload()
    providers = {provider["name"]: provider for provider in body["providers"]}

    assert providers["openai"]["api_type"] == "responses"


def test_settings_payload_reports_workspace_sandbox(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config = Config()
    config.tools.restrict_to_workspace = True
    save_config(config, config_path)
    monkeypatch.setattr(get_runtime_context(), "config_path", config_path)
    monkeypatch.setenv("JENNY_SANDBOX_ENFORCED", "macos_app_sandbox")

    body = settings_payload()
    sandbox = body["advanced"]["workspace_sandbox"]

    assert sandbox["restrict_to_workspace"] is True
    assert sandbox["level"] == "system"
    assert sandbox["enforced"] is True
    assert sandbox["provider"] == "macos_app_sandbox"
    assert sandbox["provider_label"] == "macOS App Sandbox"


@pytest.mark.asyncio
async def test_update_provider_ignores_api_type_for_non_openai(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "config.json"
    save_config(Config(), config_path)
    monkeypatch.setattr(get_runtime_context(), "config_path", config_path)

    body = await update_provider({
        "name": "custom",
        "api_base": "https://example.test/v1",
    })

    assert body["providers"]
    config = load_config(config_path)
    provider_entry = next(p for p in config.providers.providers if p.name == "custom")
    assert provider_entry.api_base == "https://example.test/v1"


@pytest.mark.asyncio
async def test_end_to_end_server_pushes_streaming_deltas_to_client(bus: MagicMock) -> None:
    port = free_port()
    channel = _ch(bus, port=port, streaming=True)

    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws?client_id=stream-tester") as client:
            ready_raw = await client.recv()
            ready = json.loads(ready_raw)
            chat_id = ready["chat_id"]

            # Server pushes deltas directly
            await channel.send_delta(
                chat_id, "Hello ", {"_stream_delta": True, "_stream_id": "s1"}
            )
            await channel.send_delta(
                chat_id, "world", {"_stream_delta": True, "_stream_id": "s1"}
            )
            await channel.send_delta(
                chat_id, "", {"_stream_end": True, "_stream_id": "s1"}
            )

            delta1 = json.loads(await client.recv())
            assert delta1["event"] == "delta"
            assert delta1["text"] == "Hello "
            assert delta1["stream_id"] == "s1"

            delta2 = json.loads(await client.recv())
            assert delta2["event"] == "delta"
            assert delta2["text"] == "world"
            assert delta2["stream_id"] == "s1"

            end = json.loads(await client.recv())
            assert end["event"] == "stream_end"
            assert end["stream_id"] == "s1"

            await channel.send(OutboundMessage(
                channel="websocket",
                chat_id=chat_id,
                content="",
                metadata={"_turn_end": True},
            ))

            turn_end = json.loads(await client.recv())
            assert turn_end == {"event": "turn_end", "chat_id": chat_id}
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_allow_from_rejects_unauthorized_client_id(bus: MagicMock) -> None:
    port = free_port()
    channel = _ch(bus, port=port, allowFrom=["alice", "bob"])

    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        with pytest.raises(websockets.exceptions.InvalidStatus) as exc_info:
            async with websockets.connect(f"ws://127.0.0.1:{port}/ws?client_id=eve"):
                pass
        assert exc_info.value.response.status_code == 403
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_client_id_truncation(bus: MagicMock) -> None:
    port = free_port()
    channel = _ch(bus, port=port)

    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        long_id = "x" * 200
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws?client_id={long_id}") as client:
            ready = json.loads(await client.recv())
            assert ready["client_id"] == "x" * 128
            assert len(ready["client_id"]) == 128
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_non_utf8_binary_frame_ignored(bus: MagicMock) -> None:
    port = free_port()
    channel = _ch(bus, port=port)

    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws?client_id=bin-test") as client:
            await client.recv()  # consume ready
            # Send non-UTF-8 bytes
            await client.send(b"\xff\xfe\xfd")
            await asyncio.sleep(0.05)
            # publish_inbound should NOT have been called
            bus.publish_inbound.assert_not_awaited()
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_allow_from_empty_list_denies_all(bus: MagicMock) -> None:
    port = free_port()
    channel = _ch(bus, port=port, allowFrom=[])

    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        with pytest.raises(websockets.exceptions.InvalidStatus) as exc_info:
            async with websockets.connect(f"ws://127.0.0.1:{port}/ws?client_id=anyone"):
                pass
        assert exc_info.value.response.status_code == 403
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_websocket_requires_token_without_issue_path(bus: MagicMock) -> None:
    """When websocket_requires_token is True but no token or issue path configured, all connections are rejected."""
    port = free_port()
    channel = _ch(bus, port=port, websocketRequiresToken=True)

    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        # No token at all → 401
        with pytest.raises(websockets.exceptions.InvalidStatus) as exc_info:
            async with websockets.connect(f"ws://127.0.0.1:{port}/ws?client_id=u"):
                pass
        assert exc_info.value.response.status_code == 401

        # Wrong token → 401
        with pytest.raises(websockets.exceptions.InvalidStatus) as exc_info:
            async with websockets.connect(f"ws://127.0.0.1:{port}/ws?client_id=u&token=wrong"):
                pass
        assert exc_info.value.response.status_code == 401
    finally:
        await channel.stop()
        await server_task


# -- Multi-chat multiplexing -------------------------------------------------
#
# The multiplex protocol lets one WS connection route N logical chats over
# typed envelopes (`attach` / `message`). Legacy frames must keep
# working on the connection's default chat_id.


@pytest.mark.asyncio
async def test_multiplex_legacy_still_works(bus: MagicMock) -> None:
    port = free_port()
    channel = _ch(bus, port=port)
    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws?client_id=legacy") as client:
            ready = json.loads(await client.recv())
            default_chat = ready["chat_id"]

            # Plain text frame routes to default chat_id
            await client.send("hello from legacy")
            await asyncio.sleep(0.1)
            inbound = bus.publish_inbound.call_args[0][0]
            assert inbound.chat_id == default_chat
            assert inbound.content == "hello from legacy"

            # {"content": ...} frame routes to default chat_id
            await client.send(json.dumps({"content": "structured legacy"}))
            await asyncio.sleep(0.1)
            assert bus.publish_inbound.call_args[0][0].chat_id == default_chat
            assert bus.publish_inbound.call_args[0][0].content == "structured legacy"

            # Outbound still reaches the legacy client, with chat_id annotated
            await channel.send(
                OutboundMessage(channel="websocket", chat_id=default_chat, content="reply")
            )
            reply = json.loads(await client.recv())
            assert reply["event"] == "message"
            assert reply["chat_id"] == default_chat
            assert reply["text"] == "reply"
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_webui_message_envelope_appends_user_transcript(
    bus: MagicMock,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)
    sessions = SessionManager(tmp_path / "sessions")
    channel = WebSocketChannel(
        {"enabled": True, "allowFrom": ["*"], "host": "127.0.0.1"},
        bus,
        gateway=_basic_handler(bus, session_manager=sessions, workspace_path=tmp_path),
    )
    conn = AsyncMock()
    conn.remote_address = ("127.0.0.1", 50123)

    await channel._dispatch_envelope(
        conn,
        "webui-client",
        {
            "type": "message",
            "chat_id": "source",
            "content": "round1",
            "webui": True,
        },
    )

    # Every message routes to the unified default chat regardless of chat_id.
    [line] = read_transcript_lines("websocket:default")
    assert {
        "event": line.get("event"),
        "chat_id": line.get("chat_id"),
        "text": line.get("text"),
    } == {"event": "user", "chat_id": "default", "text": "round1"}
    assert isinstance(line.get("turn_id"), str)
    assert line.get("turn_phase") == "user"
    assert line.get("turn_seq") == 1
    inbound = bus.publish_inbound.await_args.args[0]
    assert inbound.chat_id == "default"
    assert inbound.content == "round1"


@pytest.mark.asyncio
async def test_multiplex_invalid_frames_return_error(bus: MagicMock) -> None:
    port = free_port()
    channel = _ch(bus, port=port)
    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws?client_id=bad") as client:
            await client.recv()  # ready

            # attach ignores chat_id and always lands on the unified chat
            await client.send(json.dumps({"type": "attach", "chat_id": "has space"}))
            attached = json.loads(await client.recv())
            assert attached["event"] == "attached"
            assert attached["chat_id"] == "default"

            # message with missing content
            await client.send(json.dumps({"type": "message", "chat_id": "abc", "content": ""}))
            err2 = json.loads(await client.recv())
            assert err2["event"] == "error"

            # unknown type
            await client.send(json.dumps({"type": "nope"}))
            err3 = json.loads(await client.recv())
            assert err3["event"] == "error"

            # Connection survives: legacy frame still works.
            await client.send("still-alive")
            await asyncio.sleep(0.1)
            bus.publish_inbound.assert_awaited()
            assert bus.publish_inbound.call_args[0][0].content == "still-alive"
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_multiplex_cleanup_on_disconnect(bus: MagicMock) -> None:
    port = free_port()
    channel = _ch(bus, port=port)
    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws?client_id=dc") as client:
            ready = json.loads(await client.recv())
            default_chat = ready["chat_id"]
            assert default_chat in channel._subs
        # Client gone. Server-side tracking must be empty.
        await asyncio.sleep(0.2)
        assert default_chat not in channel._subs
        assert not channel._conn_chats
    finally:
        await channel.stop()
        await server_task


def test_parse_envelope_detects_typed_frames() -> None:
    assert _parse_envelope('{"type":"attach"}') == {"type": "attach"}
    env = _parse_envelope('{"type":"message","chat_id":"abc","content":"hi"}')
    assert env == {"type": "message", "chat_id": "abc", "content": "hi"}


def test_parse_envelope_rejects_legacy_and_garbage() -> None:
    # No `type` field → legacy, caller falls back to _parse_inbound_payload.
    assert _parse_envelope('{"content":"hi"}') is None
    assert _parse_envelope("plain text") is None
    assert _parse_envelope("{broken") is None
    assert _parse_envelope("[1,2,3]") is None
    # Non-string `type` is not a valid envelope.
    assert _parse_envelope('{"type":123}') is None


def test_webui_thread_includes_active_run_started_at(tmp_path, monkeypatch) -> None:
    from urllib.parse import quote

    from websockets.datastructures import Headers
    from websockets.http11 import Request

    from jenny.session import webui_turns as wth

    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)
    key = "websocket:default"
    append_transcript_object(key, {"event": "user", "chat_id": "default", "text": "hi"})
    bus = MagicMock()
    channel = _ch(bus)
    channel.gateway.http.config.token_issue_secret = "tok"
    enc = quote(key, safe="")
    req = Request(f"/api/sessions/{enc}/webui-thread", Headers([("Authorization", "Bearer tok")]))

    wth._WEBSOCKET_TURN_WALL_STARTED_AT.clear()
    try:
        wth._WEBSOCKET_TURN_WALL_STARTED_AT["default"] = 1_700_000_000.0
        resp = channel.gateway.http._handle_webui_thread_get(req, enc)
    finally:
        wth._WEBSOCKET_TURN_WALL_STARTED_AT.clear()

    assert resp.status_code == 200
    body = json.loads(resp.body.decode())
    assert body["run_started_at"] == 1_700_000_000.0


def test_handle_webui_thread_get_returns_json(tmp_path, monkeypatch) -> None:
    from urllib.parse import quote

    from websockets.datastructures import Headers
    from websockets.http11 import Request


    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)
    key = "websocket:c1"
    append_transcript_object(key, {"event": "user", "chat_id": "c1", "text": "hi"})
    bus = MagicMock()
    channel = _ch(bus)
    channel.gateway.http.config.token_issue_secret = "tok"
    enc = quote(key, safe="")
    req = Request(f"/api/sessions/{enc}/webui-thread", Headers([("Authorization", "Bearer tok")]))
    resp = channel.gateway.http._handle_webui_thread_get(req, enc)
    assert resp.status_code == 200
    body = json.loads(resp.body.decode())
    assert body["sessionKey"] == key
    assert len(body["messages"]) == 1
    assert body["messages"][0]["role"] == "user"
    assert body["messages"][0]["content"] == "hi"


def test_handle_webui_thread_get_accepts_pagination_query(tmp_path, monkeypatch) -> None:
    from urllib.parse import quote

    from websockets.datastructures import Headers
    from websockets.http11 import Request


    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)
    key = "websocket:paged-route"
    for idx in range(1, 4):
        append_transcript_object(
            key,
            {"event": "user", "chat_id": "paged-route", "text": f"q{idx}"},
        )
        append_transcript_object(
            key,
            {"event": "message", "chat_id": "paged-route", "text": f"a{idx}"},
        )
        append_transcript_object(key, {"event": "turn_end", "chat_id": "paged-route"})

    bus = MagicMock()
    channel = _ch(bus)
    channel.gateway.http.config.token_issue_secret = "tok"
    enc = quote(key, safe="")
    req = Request(
        f"/api/sessions/{enc}/webui-thread?limit=2",
        Headers([("Authorization", "Bearer tok")]),
    )

    resp = channel.gateway.http._handle_webui_thread_get(req, enc)

    assert resp.status_code == 200
    body = json.loads(resp.body.decode())
    assert [message["content"] for message in body["messages"]] == ["q3", "a3"]
    assert body["page"]["has_more_before"] is True
    assert body["page"]["before_cursor"]


def test_handle_file_preview_returns_workspace_file(tmp_path) -> None:
    from urllib.parse import quote

    from websockets.datastructures import Headers
    from websockets.http11 import Request

    workspace = tmp_path / "workspace"
    source = workspace / "jenny" / "agent" / "hook.py"
    source.parent.mkdir(parents=True)
    source.write_text("print('hello')\n", encoding="utf-8")

    gateway = _basic_handler(MagicMock(), workspace_path=workspace)
    gateway.http.config.token_issue_secret = "tok"
    key = "websocket:file-preview"
    enc = quote(key, safe="")
    path = quote("jenny/agent/hook.py:12", safe="")
    req = Request(
        f"/api/sessions/{enc}/file-preview?path={path}",
        Headers([("Authorization", "Bearer tok")]),
    )

    resp = gateway.http._handle_file_preview(req, enc)

    assert resp.status_code == 200
    body = json.loads(resp.body.decode())
    assert body["display_path"] == "jenny/agent/hook.py"
    assert body["language"] == "python"
    assert body["content"].splitlines() == ["print('hello')"]
    assert body["truncated"] is False


def test_file_preview_normalizes_windows_file_url() -> None:
    from jenny.webui.file_preview import _clean_preview_path

    assert _clean_preview_path("file:///C:/Users/me/project/app.py") == (
        "C:/Users/me/project/app.py"
    )
    assert _clean_preview_path("file:///tmp/project/app.py") == "/tmp/project/app.py"


def test_handle_file_preview_rejects_paths_outside_workspace(tmp_path) -> None:
    from urllib.parse import quote

    from websockets.datastructures import Headers
    from websockets.http11 import Request

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "secret.py"
    outside.write_text("secret = True\n", encoding="utf-8")

    gateway = _basic_handler(MagicMock(), workspace_path=workspace)
    gateway.http.config.token_issue_secret = "tok"
    key = "websocket:file-preview"
    enc = quote(key, safe="")
    req = Request(
        f"/api/sessions/{enc}/file-preview?path={quote(str(outside), safe='')}",
        Headers([("Authorization", "Bearer tok")]),
    )

    resp = gateway.http._handle_file_preview(req, enc)

    assert resp.status_code == 403


def test_handle_webui_thread_get_backfills_legacy_missing_user_rows(
    tmp_path,
    monkeypatch,
) -> None:
    from urllib.parse import quote

    from websockets.datastructures import Headers
    from websockets.http11 import Request


    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)
    workspace = tmp_path / "workspace"
    sessions = SessionManager(workspace)
    key = "websocket:c-legacy"
    session = sessions.get_or_create(key)
    session.add_message("user", "legacy question")
    session.add_message("assistant", "legacy answer")
    sessions.save(session)
    append_transcript_object(
        key,
        {"event": "message", "chat_id": "c-legacy", "text": "legacy answer"},
    )

    bus = MagicMock()
    channel = WebSocketChannel(
        {"enabled": True, "allowFrom": ["*"]},
        bus,
        gateway=_basic_handler(bus, session_manager=sessions, workspace_path=workspace),
    )
    channel.gateway.http.config.token_issue_secret = "tok"
    enc = quote(key, safe="")
    req = Request(f"/api/sessions/{enc}/webui-thread", Headers([("Authorization", "Bearer tok")]))
    resp = channel.gateway.http._handle_webui_thread_get(req, enc)

    assert resp.status_code == 200
    body = json.loads(resp.body.decode())
    assert [message["role"] for message in body["messages"]] == ["user", "assistant"]
    assert [message["content"] for message in body["messages"]] == [
        "legacy question",
        "legacy answer",
    ]


def test_handle_webui_thread_get_does_not_backfill_cron_internal_prompt(
    tmp_path,
    monkeypatch,
) -> None:
    from urllib.parse import quote

    from websockets.datastructures import Headers
    from websockets.http11 import Request

    from jenny.cron.session_turns import CRON_HISTORY_META

    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)
    workspace = tmp_path / "workspace"
    sessions = SessionManager(workspace)
    key = "websocket:c-cron"
    session = sessions.get_or_create(key)
    session.add_message(
        "user",
        "Scheduled cron job triggered: 30s-test\n\nInternal reminder prompt",
        **{CRON_HISTORY_META: True},
    )
    session.add_message("assistant", "提醒已经到期。")
    sessions.save(session)
    append_transcript_object(
        key,
        {"event": "message", "chat_id": "c-cron", "text": "提醒已经到期。"},
    )

    bus = MagicMock()
    channel = WebSocketChannel(
        {"enabled": True, "allowFrom": ["*"]},
        bus,
        gateway=_basic_handler(bus, session_manager=sessions, workspace_path=workspace),
    )
    channel.gateway.http.config.token_issue_secret = "tok"
    enc = quote(key, safe="")
    req = Request(f"/api/sessions/{enc}/webui-thread", Headers([("Authorization", "Bearer tok")]))
    resp = channel.gateway.http._handle_webui_thread_get(req, enc)

    assert resp.status_code == 200
    body = json.loads(resp.body.decode())
    assert [message["role"] for message in body["messages"]] == ["assistant"]
    assert [message["content"] for message in body["messages"]] == ["提醒已经到期。"]
