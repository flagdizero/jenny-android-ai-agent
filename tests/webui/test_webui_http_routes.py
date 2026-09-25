"""Regression tests for /api/workspace/download."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from support.gateway_http import AUTH_SECRET, make_handler, make_request
from websockets.http11 import Request as WsRequest

from jenny.webui.ws_http import GatewayHTTPHandler


def _make_request(
    path: str,
    token: str | None = AUTH_SECRET,
    headers: list[tuple[str, str]] | None = None,
) -> WsRequest:
    """Create a minimal WsRequest for testing."""
    return make_request(path, token, headers)


def _make_handler(tmp_path: Path) -> GatewayHTTPHandler:
    """Create a GatewayHTTPHandler with minimal mocked dependencies."""
    return make_handler(tmp_path / "skills")


# ---------------------------------------------------------------------------
# /api/workspace/download
# ---------------------------------------------------------------------------


class TestWorkspaceDownload:
    """Passano dal ``dispatch``, non dall'handler.

    Auth, gate ``workspace.enabled`` e traduzione degli errori del filesystem
    vivono lì per tutti e sette gli handler, invece di essere ricopiati in
    ognuno. Chiamare l'handler nudo salterebbe esattamente ciò che questi test
    verificano — e prima di questo cambio **nessun test di questo router
    passava dal dispatch**, quindi il percorso di produzione non era coperto.
    """

    """Regression tests for the workspace file download endpoint."""

    @pytest.mark.asyncio
    async def test_download_returns_file_content(self, tmp_path):
        handler = _make_handler(tmp_path)
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        test_file = workspace / "hello.txt"
        test_file.write_text("hello world", encoding="utf-8")

        with patch.object(handler, "_get_workspace_root", return_value=workspace):
            request = _make_request("/api/workspace/download?path=hello.txt")
            response = await handler.workspace_routes.dispatch(request, "/api/workspace/download")

        assert response.status_code == 200
        assert b"hello world" in response.body
        content_disposition = response.headers.get("Content-Disposition", "")
        assert 'filename="hello.txt"' in content_disposition

    @pytest.mark.asyncio
    async def test_download_returns_404_for_missing_file(self, tmp_path):
        handler = _make_handler(tmp_path)
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        with patch.object(handler, "_get_workspace_root", return_value=workspace):
            request = _make_request("/api/workspace/download?path=missing.txt")
            response = await handler.workspace_routes.dispatch(request, "/api/workspace/download")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_download_returns_400_for_directory(self, tmp_path):
        handler = _make_handler(tmp_path)
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "subdir").mkdir()

        with patch.object(handler, "_get_workspace_root", return_value=workspace):
            request = _make_request("/api/workspace/download?path=subdir")
            response = await handler.workspace_routes.dispatch(request, "/api/workspace/download")

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_download_returns_400_for_path_traversal(self, tmp_path):
        handler = _make_handler(tmp_path)
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        with patch.object(handler, "_get_workspace_root", return_value=workspace):
            request = _make_request("/api/workspace/download?path=../etc/passwd")
            response = await handler.workspace_routes.dispatch(request, "/api/workspace/download")

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_download_returns_401_without_token(self, tmp_path):
        handler = _make_handler(tmp_path)
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        request = _make_request("/api/workspace/download?path=test.txt", token=None)
        response = await handler.workspace_routes.dispatch(request, "/api/workspace/download")

        assert response.status_code == 401


# La creazione di un audit non e' piu' una rotta: e' il comando RPC
# ``audit.create``, e il suo banco (traversal compreso) sta in
# ``tests/webui/test_commands.py``.
