"""Regression tests for /api/workspace/download and /api/audit filtering."""

from __future__ import annotations

import urllib.parse
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


class TestAuditCreateTraversal:
    """A1: /api/audit/create must not read files outside the wiki pages dir."""

    def _setup_workspace(self, tmp_path: Path) -> Path:
        """Create a workspace with wikis/main/wiki/index.md and an external secret."""
        workspace = tmp_path / "workspace"
        pages_dir = workspace / "wikis" / "main" / "wiki"
        pages_dir.mkdir(parents=True)
        (pages_dir / "index.md").write_text("# Home\ncontent here", encoding="utf-8")
        # A secret file outside any wiki, target of the traversal attempt.
        (workspace / "secret.txt").write_text("TOPSECRET-EXFIL-MARKER", encoding="utf-8")
        return workspace

    @pytest.mark.asyncio
    async def test_traversal_target_is_forbidden_and_not_exfiltrated(self, tmp_path):
        handler = _make_handler(tmp_path)
        workspace = self._setup_workspace(tmp_path)

        traversal = urllib.parse.quote("../../../../secret.txt")
        with patch.object(handler, "_get_workspace_root", return_value=workspace):
            create_req = _make_request(
                f"/api/audit/create?wiki=main&target={traversal}"
                "&selStart=0&selEnd=5&comment=x&author=t"
            )
            create_resp = await handler.wiki_routes._audit_create(create_req)

        assert create_resp.status_code in (403, 404)

        # Nessun audit deve essere nato, e **si guarda il disco**: la rotta che
        # li elencava e' uscita il 22/09/2026 insieme alle altre senza clienti,
        # e il disco e' comunque la misura piu' forte delle due — e' quel che
        # Jenny legge, non quel che una risposta racconta.
        audit_dir = workspace / "wikis" / "main" / "audit"
        scritti = list(audit_dir.glob("**/*.md")) if audit_dir.exists() else []
        assert scritti == []
        # E il segreto non deve essere finito da nessuna parte dentro la wiki.
        for f in (workspace / "wikis").rglob("*"):
            if f.is_file():
                assert "TOPSECRET-EXFIL-MARKER" not in f.read_text(
                    encoding="utf-8", errors="replace"
                ), f

    @pytest.mark.asyncio
    async def test_audit_on_real_page_succeeds(self, tmp_path):
        handler = _make_handler(tmp_path)
        workspace = self._setup_workspace(tmp_path)

        with patch.object(handler, "_get_workspace_root", return_value=workspace):
            create_req = _make_request(
                "/api/audit/create?wiki=main&target=index.md"
                "&selStart=8&selEnd=15&comment=typo&author=t"
            )
            create_resp = await handler.wiki_routes._audit_create(create_req)

        assert create_resp.status_code == 200
        import json

        payload = json.loads(create_resp.body.decode("utf-8"))
        assert "id" in payload
        assert payload["filename"]


# ---------------------------------------------------------------------------
# /api/audit/{id}/resolve
# ---------------------------------------------------------------------------


