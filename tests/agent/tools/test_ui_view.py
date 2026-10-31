"""Tests for the ui_view tool."""

from __future__ import annotations

from jenny.agent.tools.context import RequestContext, ToolContext
from jenny.agent.tools.result import ToolResult
from jenny.agent.tools.ui_view import UiViewTool
from jenny.channels.ui_query import UiQueryTimeoutError, UiQueryUnavailableError


class _FakeService:
    def __init__(self, *, payload=None, exc=None):
        self._payload = payload
        self._exc = exc
        self.calls: list[str] = []

    async def query(self, conn_id, *, timeout_s=6.0):
        self.calls.append(conn_id)
        if self._exc is not None:
            raise self._exc
        return self._payload


def _tool(service, *, conn_id="conn-1") -> UiViewTool:
    tool = UiViewTool(service)
    tool.set_context(
        RequestContext(
            channel="websocket",
            chat_id="default",
            session_key="websocket:default",
            metadata={"conn_id": conn_id} if conn_id else {},
        )
    )
    return tool


def test_enabled_gates_on_service():
    assert UiViewTool.enabled(ToolContext(config=None, workspace="/tmp")) is False
    ctx = ToolContext(config=None, workspace="/tmp", ui_query_service=object())
    assert UiViewTool.enabled(ctx) is True


async def test_success_renders_view_and_html():
    svc = _FakeService(payload={"view": "wiki", "drawer": None, "html": "<h1>Recipes</h1>"})
    result = await _tool(svc).execute()
    assert isinstance(result, ToolResult)
    assert result.ok
    assert "view: wiki" in result.content
    assert "<h1>Recipes</h1>" in result.content
    assert svc.calls == ["conn-1"]


async def test_success_renders_open_app():
    svc = _FakeService(payload={
        "view": "apps",
        "html": "<div>apps</div>",
        "app": {"slug": "todo", "name": "Todo", "responded": True, "html": "<ul><li>buy milk</li></ul>"},
    })
    result = await _tool(svc).execute()
    assert result.ok
    assert "app open: Todo" in result.content
    assert "buy milk" in result.content
    assert "<slug>_<action>" in result.content


async def test_app_no_sdk_flags_missing_dom():
    svc = _FakeService(payload={
        "view": "apps",
        "html": "<div>apps</div>",
        "app": {"slug": "todo", "name": "Todo", "responded": False, "html": ""},
    })
    result = await _tool(svc).execute()
    assert result.ok
    assert "did not expose its DOM" in result.content


async def test_missing_conn_id_is_unavailable():
    svc = _FakeService(payload={"view": "wiki"})
    result = await _tool(svc, conn_id=None).execute()
    assert not result.ok
    assert result.error.code == "ui_unavailable"
    assert svc.calls == []  # non ha nemmeno interrogato il client


async def test_timeout_maps_to_ui_timeout():
    svc = _FakeService(exc=UiQueryTimeoutError("nope"))
    result = await _tool(svc).execute()
    assert not result.ok
    assert result.error.code == "ui_timeout"


async def test_unavailable_maps_to_ui_unavailable():
    svc = _FakeService(exc=UiQueryUnavailableError("gone"))
    result = await _tool(svc).execute()
    assert not result.ok
    assert result.error.code == "ui_unavailable"


async def test_the_chat_open_over_the_view_is_said():
    """Dal 05/10/2026 la chat sale sopra la pagina: la vista descritta e'
    quella sotto, e il modello deve saperlo."""
    svc = _FakeService(payload={"view": "home:settings", "over": "chat", "html": "<p>Tema</p>"})
    result = await _tool(svc).execute()
    assert result.ok
    assert "view: home:settings" in result.content
    assert "open full screen over this view" in result.content


async def test_no_chat_over_says_nothing_about_it():
    svc = _FakeService(payload={"view": "home:settings", "html": "<p>Tema</p>"})
    result = await _tool(svc).execute()
    assert "over this view" not in result.content
