"""``staged_taint``: le descrizioni delle bozze e il registro del taint nel database.

Le fixture sono la forma vera di prima: ``meta.json`` accanto al contenuto di
una bozza, e ``.jenny/taint_ledger.json``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jenny import db as jdb
from jenny.agent import staged_writes
from jenny.db_migration import KEEP_MIGRATED_S, run_pending
from jenny.db_migration.registry import DONE
from jenny.security import taint_ledger
from jenny.security.taint_ledger import TaintLedger


@pytest.fixture
def workspace(tmp_path: Path):
    ws = tmp_path / "workspace"
    (ws / "out").mkdir(parents=True)
    taint_ledger._LEDGERS.clear()
    yield ws
    jdb.close_all()


def _old_files(ws: Path) -> None:
    draft = ws / ".jenny" / "staged" / "abc123def456"
    draft.mkdir(parents=True)
    (draft / "content").write_bytes(b"nuovo")
    (draft / "meta.json").write_text(json.dumps({
        "id": "abc123def456", "target": "USER.md", "base_sha256": None, "size": 5,
        "sources": ["web_search"], "task_id": "t1", "created_ms": 10,
        "journal": "helper-L1"}))
    (ws / "out" / "pagina.md").write_text("x")
    (ws / ".jenny" / "taint_ledger.json").write_text(json.dumps(
        {"v": 1, "entries": {"out/pagina.md": {"t": 5, "src": "web_fetch:evil.example"}}}))


def test_the_drafts_and_the_taint_come_in(workspace: Path) -> None:
    _old_files(workspace)
    assert run_pending(workspace)["staged_taint"] == DONE
    [draft] = staged_writes.list_staged(workspace)
    assert draft["id"] == "abc123def456" and draft["target"] == "USER.md"
    assert staged_writes.pending_journal_keys(workspace) == {"helper-L1"}
    assert TaintLedger(workspace).source_of("out/pagina.md") == "web_fetch:evil.example"
    assert not (workspace / ".jenny" / "taint_ledger.json").exists()
    assert (workspace / ".jenny" / "staged" / "abc123def456" / "meta.json.migrated").is_file()


def test_the_leftovers_go_thirty_days_later(workspace: Path) -> None:
    _old_files(workspace)
    run_pending(workspace, now=1000.0)
    run_pending(workspace, now=1000.0 + KEEP_MIGRATED_S)
    assert not (workspace / ".jenny" / "taint_ledger.json.migrated").exists()
    assert not (workspace / ".jenny" / "staged" / "abc123def456" / "meta.json.migrated").exists()
    assert (workspace / ".jenny" / "staged" / "abc123def456" / "content").is_file()


def test_the_token_usage_comes_in_by_day(workspace: Path) -> None:
    from jenny.agent.token_usage import read_token_usage_state

    path = workspace / ".jenny" / "webui" / "token-usage.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"schema_version": 1, "updated_at": "2026-11-01T10:00:00+00:00",
                                "days": {"2026-11-01": {"requests": 3, "total_tokens": 900}}}))
    assert run_pending(workspace)["small_state"] == DONE
    from unittest.mock import patch

    with patch("jenny.agent.token_usage._workspace", lambda: workspace):
        state = read_token_usage_state()
    assert state["days"]["2026-11-01"]["total_tokens"] == 900
    assert not path.exists() and path.with_name("token-usage.json.migrated").is_file()
