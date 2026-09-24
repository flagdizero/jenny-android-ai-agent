"""End-to-end tests for the embedded webui's HTTP routes on the WebSocket channel."""

import asyncio
import functools
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from port_alloc import free_port

from jenny.channels.websocket import WebSocketChannel, WebSocketConfig
from jenny.session.manager import Session, SessionManager
from jenny.webui.gateway_services import GatewayServices, build_gateway_services

_AUTH_SECRET = "test-secret"


def _make_handler(
    cfg: dict[str, Any] | WebSocketConfig,
    bus: Any,
    *,
    session_manager: SessionManager | None = None,
    workspace_path: Path | None = None,
    runtime_model_name: Any | None = None,
) -> GatewayServices:
    config = WebSocketConfig.model_validate(cfg) if isinstance(cfg, dict) else cfg
    workspace = workspace_path or Path.cwd()
    return build_gateway_services(
        config=config,
        bus=bus,
        session_manager=session_manager,
        workspace_path=workspace,
        default_restrict_to_workspace=False,
        runtime_model_name=runtime_model_name,
    )


def _ch(
    bus: Any,
    *,
    session_manager: SessionManager | None = None,
    workspace_path: Path | None = None,
    port: int | None = None,
    runtime_model_name: Any | None = None,
    **extra: Any,
) -> WebSocketChannel:
    cfg: dict[str, Any] = {
        "enabled": True,
        "allowFrom": ["*"],
        "host": "127.0.0.1",
        # Nessun default fisso: senza porta esplicita se ne prende una libera.
        "port": port if port is not None else free_port(),
        "path": "/",
        "websocketRequiresToken": False,
        "tokenIssueSecret": _AUTH_SECRET,
    }
    cfg.update(extra)
    gateway = _make_handler(
        cfg, bus,
        session_manager=session_manager,
        workspace_path=workspace_path,
        runtime_model_name=runtime_model_name,
    )
    return WebSocketChannel(cfg, bus, gateway=gateway)


@pytest.fixture()
def bus() -> MagicMock:
    b = MagicMock()
    b.publish_inbound = AsyncMock()
    return b


async def _http_get(
    url: str, headers: dict[str, str] | None = None
) -> httpx.Response:
    return await asyncio.to_thread(
        functools.partial(httpx.get, url, headers=headers or {}, timeout=5.0)
    )


async def _bootstrap(port: int) -> dict[str, Any]:
    """Call the bootstrap endpoint with the shared test secret."""
    resp = await _http_get(
        f"http://127.0.0.1:{port}/webui/bootstrap",
        headers={"X-Jenny-Auth": _AUTH_SECRET},
    )
    assert resp.status_code == 200, f"bootstrap failed: {resp.status_code}"
    return resp.json()


def _seed_session(workspace: Path, key: str = "websocket:test") -> SessionManager:
    sm = SessionManager(workspace)
    s = Session(key=key)
    s.add_message("user", "hi")
    s.add_message("assistant", "hello back")
    sm.save(s)
    return sm


def _seed_many(workspace: Path, keys: list[str]) -> SessionManager:
    sm = SessionManager(workspace)
    for k in keys:
        s = Session(key=k)
        s.add_message("user", f"hi from {k}")
        sm.save(s)
    return sm


@pytest.mark.asyncio
async def test_bootstrap_returns_metadata_for_localhost(
    bus: MagicMock, tmp_path: Path
) -> None:
    port = free_port()
    sm = _seed_session(tmp_path)
    channel = _ch(bus, session_manager=sm, port=port)
    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)
    try:
        resp = await _http_get(
            f"http://127.0.0.1:{port}/webui/bootstrap",
            headers={"X-Jenny-Auth": _AUTH_SECRET},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ws_path"] == "/"
        assert body["ws_url"] == f"ws://127.0.0.1:{port}/"
        assert isinstance(body.get("model_name"), str)
        assert isinstance(body.get("provider"), str)
    finally:
        await channel.stop()
        await server_task



@pytest.mark.asyncio
async def test_webui_skills_route_requires_token_and_hides_paths(
    bus: MagicMock, tmp_path: Path
) -> None:
    port = free_port()
    workspace_skill = tmp_path / "skills" / "workspace-skill"
    workspace_skill.mkdir(parents=True)
    (workspace_skill / "SKILL.md").write_text(
        "---\nname: workspace-skill\ndescription: Workspace skill.\n---\n",
        encoding="utf-8",
    )
    unavailable_skill = tmp_path / "skills" / "zz-unavailable-skill"
    unavailable_skill.mkdir(parents=True)
    (unavailable_skill / "SKILL.md").write_text(
        "\n".join([
            "---",
            "name: zz-unavailable-skill",
            "description: Missing CLI skill.",
            "metadata:",
            "  jenny:",
            "    requires:",
            "      bins:",
            "        - definitely-missing-jenny-skill-cli",
            "      env:",
            "        - DEFINITELY_MISSING_JENNY_SKILL_ENV",
            "---",
            "Use the missing CLI and env var.",
        ]),
        encoding="utf-8",
    )
    channel = _ch(
        bus,
        session_manager=_seed_session(tmp_path),
        workspace_path=tmp_path,
        port=port,
    )
    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)
    try:
        deny = await _http_get(f"http://127.0.0.1:{port}/api/webui/skills")
        assert deny.status_code == 401

        await _bootstrap(port)
        token = _AUTH_SECRET
        resp = await _http_get(
            f"http://127.0.0.1:{port}/api/webui/skills",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 200
        body = resp.json()
        names = [skill["name"] for skill in body["skills"]]
        assert names[0] == "workspace-skill"
        assert all("path" not in skill for skill in body["skills"])
        workspace = body["skills"][0]
        assert workspace == {
            "name": "workspace-skill",
            "description": "Workspace skill.",
            "source": "workspace",
            "available": True,
            "unavailable_reason": "",
            "disabled": False,
            "bundled": False,
            "internal": False,
            "locked": False,
            "user_summary": None,
        }
        unavailable = next(skill for skill in body["skills"] if skill["name"] == "zz-unavailable-skill")
        assert unavailable["available"] is False
        assert unavailable["unavailable_reason"] == (
            "Missing: definitely-missing-jenny-skill-cli, "
            "ENV: DEFINITELY_MISSING_JENNY_SKILL_ENV"
        )
    finally:
        await channel.stop()
        await server_task






@pytest.mark.asyncio
async def test_webui_thread_resigns_assistant_media_urls(
    bus: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = free_port()
    from jenny.webui.transcript import append_transcript_object

    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)
    media_root = tmp_path / "media"
    websocket_media = media_root / "websocket"
    websocket_media.mkdir(parents=True)
    external = tmp_path / "clip.mp4"
    external.write_bytes(b"video")

    def fake_media_dir(channel: str | None = None) -> Path:
        return websocket_media if channel == "websocket" else media_root

    monkeypatch.setattr("jenny.webui.media_gateway.get_media_dir", fake_media_dir)

    append_transcript_object(
        "websocket:video-replay",
        {"event": "user", "chat_id": "video-replay", "text": "make a video"},
    )
    append_transcript_object(
        "websocket:video-replay",
        {
            "event": "message",
            "chat_id": "video-replay",
            "text": "video ready",
            "media": [str(external)],
            "media_urls": [{"url": "/api/media/old-sig/old-payload", "name": "clip.mp4"}],
        },
    )

    channel = _ch(bus, port=port)
    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)
    try:
        await _bootstrap(port)
        token = _AUTH_SECRET
        auth = {"Authorization": f"Bearer {token}"}
        resp = await _http_get(
            f"http://127.0.0.1:{port}/api/sessions/websocket:video-replay/webui-thread",
            headers=auth,
        )
        assert resp.status_code == 200
        assistant = next(m for m in resp.json()["messages"] if m["role"] == "assistant")
        media = assistant["media"]
        assert media[0]["kind"] == "video"
        assert media[0]["name"] == "clip.mp4"
        assert media[0]["url"].startswith("/api/media/")
        assert media[0]["url"] != "/api/media/old-sig/old-payload"

        fetched = await _http_get(f"http://127.0.0.1:{port}{media[0]['url']}")
        assert fetched.status_code == 200
        assert fetched.content == b"video"
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_session_routes_reject_non_websocket_keys(
    bus: MagicMock, tmp_path: Path
) -> None:
    port = free_port()
    sm = _seed_many(
        tmp_path,
        [
            "websocket:kept",
            "internal:direct",
            "other-channel:C123",
        ],
    )
    channel = _ch(bus, session_manager=sm, port=port)
    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)
    try:
        await _bootstrap(port)
        token = _AUTH_SECRET
        auth = {"Authorization": f"Bearer {token}"}

        # The webui list already hides non-websocket sessions; handcrafted URLs
        # should hit the same boundary rather than exposing or deleting them.
        msgs = await _http_get(
            f"http://127.0.0.1:{port}/api/sessions/cli:direct/webui-thread",
            headers=auth,
        )
        assert msgs.status_code == 404

        doomed = sm._get_session_path("other-channel:C123")
        assert doomed.exists()
        deny_delete = await _http_get(
            f"http://127.0.0.1:{port}/api/sessions/other-channel:C123/delete",
            headers=auth,
        )
        assert deny_delete.status_code == 404
        assert doomed.exists()
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_session_routes_reject_invalid_key(
    bus: MagicMock, tmp_path: Path
) -> None:
    port = free_port()
    sm = _seed_session(tmp_path)
    channel = _ch(bus, session_manager=sm, port=port)
    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)
    try:
        await _bootstrap(port)
        token = _AUTH_SECRET
        auth = {"Authorization": f"Bearer {token}"}

        # Invalid characters in the key -> regex match fails -> 404
        # (route doesn't match, falls through to channel 404).
        resp = await _http_get(
            f"http://127.0.0.1:{port}/api/sessions/bad%20key/webui-thread",
            headers=auth,
        )
        assert resp.status_code in {400, 404}
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_static_serves_index_when_dist_present(
    bus: MagicMock, tmp_path: Path
) -> None:
    port = free_port()
    sm = _seed_session(tmp_path / "ws_state")
    channel = _ch(bus, session_manager=sm, port=port)
    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)
    try:
        # Bare ``GET /`` is a browser opening the app: it must return the SPA
        # index.html, not the WS-upgrade handler's 401/426.
        root = await _http_get(f"http://127.0.0.1:{port}/")
        assert root.status_code == 200
        assert "Jenny" in root.text
        # Un asset vero deve arrivare **come asset**, non come la shell.
        #
        # Qui prima si scriveva un ``favicon.svg`` in una ``dist`` che non era
        # mai collegata al canale — ``static_dist_path`` è ``workspace/ui``, non
        # una cartella del test — quindi il file non veniva mai servito: la
        # richiesta cadeva sul fallback SPA e l'asserzione ``"<svg" in text``
        # passava perché la shell dell'officina conteneva ``<svg id="graph-svg">``.
        # Un test verde per una coincidenza. Si sonda un asset che esiste per
        # davvero, e si pretende che NON sia la shell.
        asset = await _http_get(f"http://127.0.0.1:{port}/html-mobile/assets/bootstrap.js")
        assert asset.status_code == 200
        assert "<!DOCTYPE html>" not in asset.text, "è la shell, non l'asset"
        assert "tc-theme" in asset.text
        # Unknown SPA route falls back to index.html.
        spa = await _http_get(f"http://127.0.0.1:{port}/sessions/abc")
        assert spa.status_code == 200
        assert "Jenny" in spa.text
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_static_rejects_path_traversal(
    bus: MagicMock, tmp_path: Path
) -> None:
    port = free_port()
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("ok")
    secret = tmp_path / "secret.txt"
    secret.write_text("classified")
    channel = _ch(bus, port=port)
    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)
    try:
        resp = await _http_get(f"http://127.0.0.1:{port}/../secret.txt")
        # Normalized by httpx into /secret.txt → falls back to index.html, not 'classified'.
        assert "classified" not in resp.text
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_unknown_route_returns_404(bus: MagicMock) -> None:
    port = free_port()
    channel = _ch(bus, port=port)
    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)
    try:
        resp = await _http_get(f"http://127.0.0.1:{port}/api/unknown")
        assert resp.status_code == 404
    finally:
        await channel.stop()
        await server_task


class _FakeConn:
    """Minimal connection stub with a configurable remote_address."""

    def __init__(self, remote_address: tuple[str, int]):
        self.remote_address = remote_address

    def respond(self, status: int, body: str) -> Any:
        from websockets.http11 import Response

        return Response(status=status, body=body.encode())


class _FakeReq:
    """Minimal request stub with configurable headers."""

    def __init__(self, headers: dict[str, str] | None = None):
        self.headers = headers or {}


_REMOTE = _FakeConn(("192.168.1.5", 12345))
_LOCAL = _FakeConn(("127.0.0.1", 12345))
_NO_HEADERS = _FakeReq()


def test_wildcard_host_without_auth_raises_on_startup(bus: MagicMock) -> None:
    import pytest

    from jenny.pydantic_compat import ValidationError

    with pytest.raises(ValidationError, match="token"):
        _ch(bus, host="0.0.0.0", tokenIssueSecret="")


def test_wildcard_host_with_secret_is_valid(bus: MagicMock) -> None:
    channel = _ch(bus, host="0.0.0.0", tokenIssueSecret="s3cret")
    assert channel.config.host == "0.0.0.0"


def test_wildcard_ipv6_without_auth_raises(bus: MagicMock) -> None:
    import pytest

    from jenny.pydantic_compat import ValidationError

    with pytest.raises(ValidationError, match="token"):
        _ch(bus, host="::", tokenIssueSecret="")


def test_wildcard_ipv6_with_secret_is_valid(bus: MagicMock) -> None:
    channel = _ch(bus, host="::", tokenIssueSecret="s3cret")
    resp = channel.gateway.http._handle_bootstrap(
        _REMOTE, _FakeReq({"X-Jenny-Auth": "s3cret"})
    )
    assert resp.status_code == 200


def test_bootstrap_ws_url_uses_forwarded_https_host(bus: MagicMock) -> None:
    port = free_port()
    channel = _ch(bus, host="127.0.0.1", port=port, tokenIssueSecret="")
    resp = channel.gateway.http._handle_bootstrap(
        _LOCAL,
        _FakeReq({"Host": "jenny.example", "X-Forwarded-Proto": "https"}),
    )
    assert resp.status_code == 200
    body = json.loads(resp.body)
    assert body["ws_url"] == "wss://jenny.example/"


def test_localhost_without_auth_is_valid(bus: MagicMock) -> None:
    channel = _ch(bus, host="127.0.0.1", tokenIssueSecret="")
    resp = channel.gateway.http._handle_bootstrap(_LOCAL, _NO_HEADERS)
    assert resp.status_code == 200


def test_bootstrap_prefers_runtime_model_name(bus: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "jenny.webui.ws_http._default_model_name_from_config",
        lambda: "from-disk",
    )
    channel = _ch(bus, host="127.0.0.1", runtime_model_name=lambda: "  live/model  ", tokenIssueSecret="")
    resp = channel.gateway.http._handle_bootstrap(_LOCAL, _NO_HEADERS)
    assert resp.status_code == 200
    body = json.loads(resp.body)
    assert body["model_name"] == "live/model"


def test_bootstrap_includes_provider_from_config(bus: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "jenny.webui.ws_http._default_provider_name_from_config",
        lambda: "deepseek",
    )
    channel = _ch(bus, host="127.0.0.1", tokenIssueSecret="")
    resp = channel.gateway.http._handle_bootstrap(_LOCAL, _NO_HEADERS)
    assert resp.status_code == 200
    body = json.loads(resp.body)
    assert body["provider"] == "deepseek"


def test_bootstrap_provider_empty_when_config_unreadable(bus: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "jenny.webui.ws_http._default_provider_name_from_config",
        lambda: None,
    )
    channel = _ch(bus, host="127.0.0.1", tokenIssueSecret="")
    resp = channel.gateway.http._handle_bootstrap(_LOCAL, _NO_HEADERS)
    assert resp.status_code == 200
    body = json.loads(resp.body)
    assert body["provider"] == ""


def test_bootstrap_falls_back_when_runtime_returns_empty(bus: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "jenny.webui.ws_http._default_model_name_from_config",
        lambda: "from-disk",
    )
    channel = _ch(bus, host="127.0.0.1", runtime_model_name=lambda: "   ", tokenIssueSecret="")
    resp = channel.gateway.http._handle_bootstrap(_LOCAL, _NO_HEADERS)
    assert resp.status_code == 200
    body = json.loads(resp.body)
    assert body["model_name"] == "from-disk"


def test_bootstrap_falls_back_when_runtime_raises(bus: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "jenny.webui.ws_http._default_model_name_from_config",
        lambda: "from-disk",
    )

    def boom():
        raise RuntimeError("resolver failed")

    channel = _ch(bus, host="127.0.0.1", runtime_model_name=boom, tokenIssueSecret="")
    resp = channel.gateway.http._handle_bootstrap(_LOCAL, _NO_HEADERS)
    assert resp.status_code == 200
    body = json.loads(resp.body)
    assert body["model_name"] == "from-disk"


def test_bootstrap_rejects_wrong_secret(bus: MagicMock) -> None:
    channel = _ch(bus, host="0.0.0.0", tokenIssueSecret="correct")
    resp = channel.gateway.http._handle_bootstrap(
        _REMOTE, _FakeReq({"Authorization": "Bearer wrong"})
    )
    assert resp.status_code == 401


def test_bootstrap_accepts_remote_with_valid_secret(bus: MagicMock) -> None:
    channel = _ch(bus, host="0.0.0.0", tokenIssueSecret="s3cret")
    resp = channel.gateway.http._handle_bootstrap(
        _REMOTE, _FakeReq({"Authorization": "Bearer s3cret"})
    )
    assert resp.status_code == 200


def test_bootstrap_accepts_x_jenny_auth_header(bus: MagicMock) -> None:
    channel = _ch(bus, host="0.0.0.0", tokenIssueSecret="s3cret")
    resp = channel.gateway.http._handle_bootstrap(
        _REMOTE, _FakeReq({"X-Jenny-Auth": "s3cret"})
    )
    assert resp.status_code == 200


def test_bootstrap_secret_also_enforced_on_localhost(bus: MagicMock) -> None:
    """When secret is set, even localhost must provide it (reverse-proxy safety)."""
    channel = _ch(bus, host="0.0.0.0", tokenIssueSecret="s3cret")
    resp = channel.gateway.http._handle_bootstrap(_LOCAL, _NO_HEADERS)
    assert resp.status_code == 401


# -- Shipped-default auth gap (any-app-can-bootstrap over loopback) ---------
#
# Reproduces the exact scenario from the security audit: the gateway starts
# with whatever config.bootstrap.ensure_minimal_config produces for a brand
# new install, and a third party is only able to reach the gateway over the
# loopback TCP socket (which Android does not isolate between apps) — it has
# no filesystem access to the app's private workspace directory.


def test_shipped_default_no_longer_grants_bootstrap_without_secret(
    bus: MagicMock, tmp_path: Path
) -> None:
    """A caller with only network (loopback) access — no secret — must be rejected.

    Before the fix, ``ensure_minimal_config`` shipped ``token_issue_secret=""``,
    so ``_handle_bootstrap`` fell back to an is-localhost-only check and handed
    out the bootstrap secret to anyone who could open a TCP connection
    to 127.0.0.1 — including another installed app.
    """
    from jenny.config.bootstrap import ensure_minimal_config

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ensure_minimal_config(workspace)  # exactly what run_gateway() does on start

    data = json.loads((workspace / "config.json").read_text(encoding="utf-8"))
    secret = data["websocket"]["token_issue_secret"]
    assert secret  # auto-generated; the old empty-string default is gone

    # Gateway is otherwise at its shipped defaults: host 127.0.0.1, no static token.
    channel = _ch(bus, host="127.0.0.1", tokenIssueSecret=secret)

    # Attacker: reaches the loopback socket, has no way to read the secret.
    resp = channel.gateway.http._handle_bootstrap(_LOCAL, _NO_HEADERS)
    assert resp.status_code == 401


def test_shipped_default_bootstrap_succeeds_with_persisted_secret(
    bus: MagicMock, tmp_path: Path
) -> None:
    """The legitimate WebUI — which can read the secret from the same private
    workspace config the app's own process already has filesystem access to —
    can still bootstrap successfully."""
    from jenny.config.bootstrap import ensure_minimal_config

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ensure_minimal_config(workspace)

    data = json.loads((workspace / "config.json").read_text(encoding="utf-8"))
    secret = data["websocket"]["token_issue_secret"]

    channel = _ch(bus, host="127.0.0.1", tokenIssueSecret=secret)

    resp = channel.gateway.http._handle_bootstrap(
        _LOCAL, _FakeReq({"X-Jenny-Auth": secret})
    )
    assert resp.status_code == 200


def test_ensure_minimal_config_backfills_secret_for_legacy_config(tmp_path: Path) -> None:
    """Configs written before this fix (empty/missing token_issue_secret) get
    a secret backfilled in place on the next gateway start."""
    from jenny.config.bootstrap import ensure_minimal_config

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    legacy = {"gateway": {"host": "127.0.0.1"}, "websocket": {"enabled": True}}
    config_path = workspace / "config.json"
    config_path.write_text(json.dumps(legacy), encoding="utf-8")

    ensure_minimal_config(workspace)

    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert data["websocket"]["token_issue_secret"]
    assert data["websocket"]["enabled"] is True


def test_ensure_minimal_config_does_not_overwrite_explicit_secret(tmp_path: Path) -> None:
    """An operator-configured secret (or static token) is never clobbered."""
    from jenny.config.bootstrap import ensure_minimal_config

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    explicit = {
        "gateway": {"host": "127.0.0.1"},
        "websocket": {"enabled": True, "token_issue_secret": "operator-chosen"},
    }
    config_path = workspace / "config.json"
    config_path.write_text(json.dumps(explicit), encoding="utf-8")

    ensure_minimal_config(workspace)

    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert data["websocket"]["token_issue_secret"] == "operator-chosen"

    explicit_token = {
        "gateway": {"host": "127.0.0.1"},
        "websocket": {"enabled": True, "token": "static-tok"},
    }
    config_path.write_text(json.dumps(explicit_token), encoding="utf-8")
    ensure_minimal_config(workspace)
    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert "token_issue_secret" not in data["websocket"]
    assert data["websocket"]["token"] == "static-tok"
