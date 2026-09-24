"""I confini che nessun test guardava, nei punti che li controllano.

Il 24/09/2026 i controlli di contenimento scritti a mano sono passati per
``is_path_within``; una mutazione che la faceva rispondere sempre sì lasciava
verdi tre dei punti convertiti. Qui c'è un caso per ciascuno: un file statico
della WebUI raggiunto con un symlink, un audit wiki su un bersaglio fuori dalle
pagine, una ``source:`` di provenienza che risale oltre il progetto.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from jenny.agent import wiki_provenance
from jenny.webui.wiki import create_audit
from jenny.webui.ws_http import GatewayHTTPHandler


def _static_handler(tmp_path: Path) -> GatewayHTTPHandler:
    handler = GatewayHTTPHandler(
        config=SimpleNamespace(
            workspace=SimpleNamespace(enabled=True),
            wiki=SimpleNamespace(enabled=True, wikis_dir="wikis"),
            token_issue_secret="test-secret",
            verbose=False,
        ),
        session_manager=None,
        runtime_model_name=lambda: "test-model",
        bus=MagicMock(),
        media=MagicMock(),
        workspaces=MagicMock(),
        skills_workspace_path=tmp_path / "skills",
    )
    ui_dir = (tmp_path / "ui").resolve()
    (ui_dir / "assets").mkdir(parents=True, exist_ok=True)
    (ui_dir / "index.html").write_text("<!DOCTYPE html><html></html>", encoding="utf-8")
    handler.static_dist_path = ui_dir
    return handler


def test_a_static_file_reached_through_a_symlink_out_of_ui_is_refused(tmp_path: Path) -> None:
    handler = _static_handler(tmp_path)
    secret = tmp_path / "config.json"
    secret.write_text('{"apiKey": "sk-segreto"}', encoding="utf-8")
    (handler.static_dist_path / "assets" / "cfg.json").symlink_to(secret)

    resp = handler._serve_static("/html-mobile/assets/cfg.json")

    assert resp is not None and resp.status_code == 403
    assert b"sk-segreto" not in (resp.body or b"")


def test_a_static_file_inside_ui_is_served(tmp_path: Path) -> None:
    handler = _static_handler(tmp_path)
    (handler.static_dist_path / "assets" / "app.js").write_text("ok()", encoding="utf-8")
    resp = handler._serve_static("/html-mobile/assets/app.js")
    assert resp is not None and resp.status_code == 200


def test_an_audit_on_a_target_outside_the_pages_is_refused(tmp_path: Path) -> None:
    wiki_root = tmp_path / "progetto"
    (wiki_root / "wiki").mkdir(parents=True)
    (wiki_root / "raw").mkdir()
    (wiki_root / "raw" / "appunti.md").write_text("privato", encoding="utf-8")

    with pytest.raises(FileNotFoundError):
        create_audit(wiki_root, "../raw/appunti.md", "privato", 0, 3, "nota", "u")
    assert not (wiki_root / "audit").exists()


def test_a_provenance_source_climbing_out_of_the_project_is_unresolved(tmp_path: Path) -> None:
    root = tmp_path / "progetto"
    (root / "raw" / "journal").mkdir(parents=True)
    outside = tmp_path / "fuori.md"
    outside.write_text("- 13:55 — [said] una cosa detta\n", encoding="utf-8")

    verdict = wiki_provenance._journal_line_provenance(root, "../fuori.md#13:55")

    assert verdict == wiki_provenance._UNRESOLVED


def test_a_provenance_source_inside_the_project_is_read(tmp_path: Path) -> None:
    root = tmp_path / "progetto"
    (root / "raw" / "journal").mkdir(parents=True)
    (root / "raw" / "journal" / "2026-09-24.md").write_text(
        "- 13:55 — [said] una cosa detta\n", encoding="utf-8"
    )
    verdict = wiki_provenance._journal_line_provenance(root, "raw/journal/2026-09-24.md#13:55")
    assert verdict != wiki_provenance._UNRESOLVED
