"""``undo_cleanup``: la cartella del vecchio annulla se ne va, e una volta sola.

La fixture è la forma vera di prima di ``undo.sqlite``: un lineage con le sue
righe, i suoi contenuti e lo stato dell'annulla.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jenny import db as jdb
from jenny.db_migration import run_pending
from jenny.db_migration.registry import DONE, NOTHING


@pytest.fixture
def workspace(tmp_path: Path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    yield ws
    jdb.close_all()


def _old_journal(ws: Path) -> Path:
    lineage = ws / "subagents" / "journal" / "lin1"
    (lineage / "blobs").mkdir(parents=True)
    (lineage / "entries.jsonl").write_text(json.dumps({
        "path": "wikis/piante/wiki/acero.md", "before": "a" * 64, "after": "b" * 64,
        "ts": 1.0, "task_id": "t1", "attempt": 1, "saved": True}) + "\n", encoding="utf-8")
    (lineage / "blobs" / ("a" * 64)).write_bytes(b"# acero\n")
    (lineage / "undo.json").write_text('{"state": "done"}', encoding="utf-8")
    return lineage.parent


def test_the_old_journal_folder_is_removed_once(workspace: Path) -> None:
    old = _old_journal(workspace)
    assert run_pending(workspace)["undo_cleanup"] == DONE
    assert not old.exists()
    assert (workspace / "subagents").is_dir()  # il resto degli aiutanti resta
    assert run_pending(workspace)["undo_cleanup"] == "skipped"


def test_nothing_to_do_on_a_fresh_install(workspace: Path) -> None:
    assert run_pending(workspace)["undo_cleanup"] == NOTHING


def _old_plan(root: Path, plan_id: str, status: str) -> Path:
    plan_dir = root / "plans" / plan_id
    (plan_dir / "photos" / "objects").mkdir(parents=True)
    (plan_dir / "rollback.journal").write_text("{}", encoding="utf-8")
    (plan_dir / "plan.json").write_text(json.dumps({
        "format": 1, "id": plan_id, "notebook": "piante", "session_key": "project:piante",
        "title": "Rifare", "status": status, "version": 1,
        "photos": {"start": "a", "end": None, "before_rollback": None, "after_rollback": None},
        "steps": [{"id": "s1", "title": "A", "detail": "x", "status": "done",
                   "photo_before": "a", "photo_after": "b"}],
        "history": [], "created_at": "2026-10-30T10:00:00", "updated_at": "2026-10-30T10:00:00",
    }), encoding="utf-8")
    return plan_dir


def test_format_one_plans_become_format_two_without_photos(workspace: Path) -> None:
    from jenny.plan.store import PlanStore

    running = _old_plan(workspace.parent, "p_000000000001", "running")
    done = _old_plan(workspace.parent, "p_000000000002", "done")
    assert run_pending(workspace)["undo_cleanup"] == DONE
    for plan_dir in (running, done):
        assert not (plan_dir / "photos").exists()
        assert not (plan_dir / "rollback.journal").exists()
    store = PlanStore.for_workspace(workspace)
    stopped = store.load("p_000000000001")
    assert stopped.status == "stopped" and stopped.place == "nb:piante"
    assert stopped.history[-1]["reason"] == "update"
    assert store.load("p_000000000002").status == "done"
