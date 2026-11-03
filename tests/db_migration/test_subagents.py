"""``subagents``: record, segnaposti e attività degli aiutanti nel database.

Le fixture sono la forma vera di prima: un JSONL di record per session key (con
una riga troncata da un kill), un segnaposto, un digest.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from jenny import db as jdb
from jenny.agent.subagent_activity import SubagentDigestStore
from jenny.agent.subagent_records import SubagentRecord, SubagentRecordStore, SubagentSpec
from jenny.db_migration import KEEP_MIGRATED_S, run_pending
from jenny.db_migration.registry import DONE


def _record(task_id: str, key: str = "project:palestra") -> SubagentRecord:
    return SubagentRecord(task_id=task_id, lineage_id=task_id, attempt=1, state="done",
                          ended_at=time.time(),
                          spec=SubagentSpec(task="x", label="x", session_key=key))


@pytest.fixture
def workspace(tmp_path: Path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    yield ws
    jdb.close_all()


def _old_files(ws: Path) -> None:
    base = ws / "subagents"
    (base / "records").mkdir(parents=True)
    good = json.dumps(_record("t1").to_dict())
    (base / "records" / "project_palestra.jsonl").write_text(f"{good}\n{good[:30]}")
    (base / "running").mkdir()
    running = _record("t2")
    running.state = "running"
    (base / "running" / "t2.json").write_text(json.dumps(running.to_dict()))
    (base / "activity").mkdir()
    (base / "activity" / "t1.json").write_text(json.dumps(
        {"version": 1, "task_id": "t1", "written_at": 1.0,
         "events": [{"kind": "result", "summary": "fatto"}]}))


def test_records_running_markers_and_digests_come_in(workspace: Path) -> None:
    _old_files(workspace)
    assert run_pending(workspace)["subagents"] == DONE
    store = SubagentRecordStore(workspace)
    [record] = store.load("project:palestra")
    assert record.task_id == "t1"
    assert [r.task_id for r in store.load_running()] == ["t2"]
    assert SubagentDigestStore(workspace).load("t1")[0]["summary"] == "fatto"
    base = workspace / "subagents"
    assert sorted(p.name for p in base.iterdir()) == [
        "activity.migrated", "records.migrated", "running.migrated"]


def test_a_second_pass_keeps_what_is_there_and_the_leftovers_go_later(workspace: Path) -> None:
    _old_files(workspace)
    run_pending(workspace, now=1000.0)
    store = SubagentRecordStore(workspace)
    store.mark_handled("t1", 5.0)
    _old_files_again = workspace / "subagents" / "records"
    _old_files_again.mkdir()
    (_old_files_again / "x.jsonl").write_text(json.dumps(_record("t1").to_dict()) + "\n")
    assert run_pending(workspace, now=2000.0)["subagents"] == DONE
    assert store.load("project:palestra")[0].handled_at == 5.0
    run_pending(workspace, now=2000.0 + KEEP_MIGRATED_S)
    assert not (workspace / "subagents" / "records.migrated").exists()
