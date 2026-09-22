"""Regression tests for /api/workspace/download and /api/audit filtering."""

from __future__ import annotations

import urllib.parse
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from websockets.http11 import Headers
from websockets.http11 import Request as WsRequest

from jenny.webui.ws_http import GatewayHTTPHandler

_AUTH_SECRET = "test-secret"


def _make_request(
    path: str,
    token: str | None = _AUTH_SECRET,
    headers: list[tuple[str, str]] | None = None,
) -> WsRequest:
    """Create a minimal WsRequest for testing."""
    if token is not None and "token=" not in path:
        sep = "&" if "?" in path else "?"
        path = f"{path}{sep}token={urllib.parse.quote(token)}"
    return WsRequest(path=path, headers=Headers(headers or []))


def _make_handler(tmp_path: Path) -> GatewayHTTPHandler:
    """Create a GatewayHTTPHandler with minimal mocked dependencies."""
    media = MagicMock()
    workspaces = MagicMock()
    bus = MagicMock()
    config = SimpleNamespace(
        workspace=SimpleNamespace(enabled=True),
        wiki=SimpleNamespace(enabled=True, wikis_dir="wikis"),
        token_issue_secret=_AUTH_SECRET,
        verbose=False,
    )
    handler = GatewayHTTPHandler(
        config=config,
        session_manager=None,
        runtime_model_name=lambda: "test-model",
        bus=bus,
        media=media,
        workspaces=workspaces,
        skills_workspace_path=tmp_path / "skills",
    )
    return handler


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


# ---------------------------------------------------------------------------
# /api/audit filtering
# ---------------------------------------------------------------------------


class TestAuditFiltering:
    """Regression tests for /api/audit list filtering by target and mode."""

    def _setup_wiki(self, tmp_path: Path) -> Path:
        """Create a wiki directory with audit entries for testing."""
        wiki_dir = tmp_path / "wiki"
        audit_dir = wiki_dir / "audit"
        resolved_dir = audit_dir / "resolved"
        audit_dir.mkdir(parents=True)
        resolved_dir.mkdir(parents=True)

        # Create an open audit targeting page-a.md
        (audit_dir / "20260101-000000-aaaa-page-a.md").write_text(
            "---\nid: 20260101-000000-aaaa\ntarget: page-a.md\ntarget_lines:\n- 1\n- 1\n"
            "anchor_before: ''\nanchor_text: some text\nanchor_after: ''\n"
            "author: test\nsource: web-viewer\n"
            "created: '2026-01-01T00:00:00'\nstatus: open\n---\n\nAudit for page A.",
            encoding="utf-8",
        )
        # Create an open audit targeting page-b.md
        (audit_dir / "20260102-000000-bbbb-page-b.md").write_text(
            "---\nid: 20260102-000000-bbbb\ntarget: page-b.md\ntarget_lines:\n- 1\n- 1\n"
            "anchor_before: ''\nanchor_text: other text\nanchor_after: ''\n"
            "author: test\nsource: web-viewer\n"
            "created: '2026-01-02T00:00:00'\nstatus: open\n---\n\nAudit for page B.",
            encoding="utf-8",
        )
        # Create a resolved audit targeting page-a.md
        (resolved_dir / "20260103-000000-cccc-page-a.md").write_text(
            "---\nid: 20260103-000000-cccc\ntarget: page-a.md\ntarget_lines:\n- 1\n- 1\n"
            "anchor_before: ''\nanchor_text: resolved text\nanchor_after: ''\n"
            "author: test\nsource: web-viewer\n"
            "created: '2026-01-03T00:00:00'\nstatus: resolved\n---\n\nResolved audit.",
            encoding="utf-8",
        )
        return wiki_dir

    def test_list_all_audits(self, tmp_path):
        from jenny.webui.wiki import list_audits

        wiki_dir = self._setup_wiki(tmp_path)
        result = list_audits(wiki_dir)

        assert len(result) == 3

    def test_filter_by_target(self, tmp_path):
        from jenny.webui.wiki import list_audits

        wiki_dir = self._setup_wiki(tmp_path)
        result = list_audits(wiki_dir, target="page-a.md")

        assert len(result) == 2
        assert all(a["target"] == "page-a.md" for a in result)

    def test_filter_by_mode_open(self, tmp_path):
        from jenny.webui.wiki import list_audits

        wiki_dir = self._setup_wiki(tmp_path)
        result = list_audits(wiki_dir, mode="open")

        assert len(result) == 2
        assert all(a["status"] == "open" for a in result)

    def test_filter_by_mode_resolved(self, tmp_path):
        from jenny.webui.wiki import list_audits

        wiki_dir = self._setup_wiki(tmp_path)
        result = list_audits(wiki_dir, mode="resolved")

        assert len(result) == 1
        assert result[0]["status"] == "resolved"

    def test_filter_by_target_and_mode(self, tmp_path):
        from jenny.webui.wiki import list_audits

        wiki_dir = self._setup_wiki(tmp_path)
        result = list_audits(wiki_dir, target="page-a.md", mode="open")

        assert len(result) == 1
        assert result[0]["id"] == "20260101-000000-aaaa"

    def test_no_matches_returns_empty(self, tmp_path):
        from jenny.webui.wiki import list_audits

        wiki_dir = self._setup_wiki(tmp_path)
        result = list_audits(wiki_dir, target="nonexistent.md")

        assert result == []


# ---------------------------------------------------------------------------
# /api/audit/create path traversal (A1)
# ---------------------------------------------------------------------------


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

            # No audit entry must have been created.
            list_req = _make_request("/api/audit?wiki=main&mode=all")
            list_resp = await handler.wiki_routes._audit_list(list_req)

        assert create_resp.status_code in (403, 404)
        # The audit directory must not contain the secret's content.
        assert b"TOPSECRET-EXFIL-MARKER" not in list_resp.body
        import json

        entries = json.loads(list_resp.body.decode("utf-8"))["entries"]
        assert entries == []

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


