"""``cron``: i job di ``cron/jobs.json`` (e del giornale, e i record) nel database.

Le fixture sono la forma vera di prima: un job scritto prima che ``mode`` e gli
stati del terzo esito esistessero, un giornale delle azioni con un ``add`` e un
``del``, un record di esecuzione. Un job vecchio deve continuare a comportarsi
come prima, e niente deve perdersi nel passaggio.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from jenny import db as jdb
from jenny.cron import store as cron_store
from jenny.cron.service import CronService
from jenny.db_migration import KEEP_MIGRATED_S, run_pending
from jenny.db_migration.registry import DONE

_LEGACY_JOB: dict[str, Any] = {
    "id": "legacy-1",
    "name": "Daily Loving Message",
    "enabled": True,
    "schedule": {"kind": "cron", "atMs": None, "everyMs": None,
                 "expr": "0 10 * * *", "tz": "Asia/Kuwait"},
    "payload": {
        "kind": "agent_turn",
        "message": "say something nice",
        "sessionKey": "unified:default",
        "originChannel": "websocket",
        "originChatId": "chat-1",
        "originMetadata": {"webui": True, "thread": {"id": "42"}},
    },
    "state": {
        "nextRunAtMs": 4_102_444_800_000,
        "lastRunAtMs": 1_700_000_000_000,
        "lastStatus": "ok",
        "lastError": None,
        "runHistory": [
            {"runAtMs": 1_700_000_000_000, "status": "ok", "durationMs": 1234, "error": None},
        ],
    },
    "createdAtMs": 1_699_000_000_000,
    "updatedAtMs": 1_700_000_000_000,
    "deleteAfterRun": False,
}

# Quel che il passaggio aggiunge a un job vecchio: i campi nati dopo, ai default.
_NEW_DEFAULTS_STATE: dict[str, Any] = {
    "consecutiveCouldNotCheck": 0,
    "couldNotCheckSinceMs": None,
    "couldNotCheckEscalated": False,
    "couldNotCheckEscalatedAtMs": None,
    "pendingSinceMs": None,
    "conditionNotified": False,
}


def _as_saved(job: dict[str, Any], *, mode: str) -> dict[str, Any]:
    expected = copy.deepcopy(job)
    expected["payload"]["mode"] = mode
    expected["state"] = {**expected["state"], **_NEW_DEFAULTS_STATE}
    expected["pausedAtMs"] = None
    return expected


@pytest.fixture
def workspace(tmp_path: Path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    yield ws
    jdb.close_all()


def _old_store(ws: Path, *jobs: dict[str, Any], actions: list[dict] = ()) -> Path:
    old = ws / "cron"
    old.mkdir(parents=True, exist_ok=True)
    (old / "jobs.json").write_text(
        json.dumps({"version": 1, "jobs": list(jobs)}, ensure_ascii=False), encoding="utf-8")
    if actions:
        (old / "action.jsonl").write_text(
            "".join(json.dumps(a) + "\n" for a in actions), encoding="utf-8")
    (ws / "cron.lock").write_text("")
    return old


def test_the_jobs_come_in_and_the_folder_goes_aside(workspace: Path) -> None:
    old = _old_store(workspace, _LEGACY_JOB)
    runs = old / "runs"
    runs.mkdir()
    (runs / "legacy-1_1700000000000_ab12cd34.json").write_text(json.dumps(
        {"job_id": "legacy-1", "status": "ok", "run_id": "legacy-1:1700000000000:ab12cd34",
         "updated_at_ms": 1_700_000_000_500}), encoding="utf-8")

    assert run_pending(workspace)["cron"] == DONE

    assert not old.exists() and (workspace / "cron.migrated" / "jobs.json").is_file()
    assert not (workspace / "cron.lock").exists()
    database = cron_store.cron_database(workspace)
    assert cron_store.load_rows(database) == [_as_saved(_LEGACY_JOB, mode="reminder")]
    assert [r["status"] for r in cron_store.read_runs(database, "legacy-1")] == ["ok"]
    job = CronService(workspace).get_job("legacy-1")
    assert job is not None and job.payload.mode == "reminder"


def test_the_action_journal_is_applied_on_top(workspace: Path) -> None:
    gone = copy.deepcopy(_LEGACY_JOB) | {"id": "gone-1"}
    _old_store(workspace, _LEGACY_JOB, gone, actions=[
        {"action": "del", "params": {"job_id": "gone-1"}},
        {"action": "add", "params": {
            "id": "weird-1", "name": "watcher", "enabled": True,
            "schedule": {"kind": "every", "at_ms": None, "every_ms": 60_000},
            "payload": {"kind": "agent_turn", "mode": "telepathy", "message": "controlla",
                        "session_key": "unified:default", "origin_channel": "websocket",
                        "origin_chat_id": "chat-1", "origin_metadata": {}},
            "state": {}, "created_at_ms": 1, "updated_at_ms": 1}},
    ])
    run_pending(workspace)
    service = CronService(workspace)
    assert sorted(j.id for j in service.list_jobs(include_disabled=True)) == [
        "legacy-1", "weird-1"]
    # Un modo che questa versione non sa eseguire torna ``reminder``, anche dal giornale.
    assert service.get_job("weird-1").payload.mode == "reminder"


def test_a_monitor_stays_a_monitor(workspace: Path) -> None:
    monitor = copy.deepcopy(_LEGACY_JOB)
    monitor["payload"]["mode"] = "monitor"
    _old_store(workspace, monitor)
    run_pending(workspace)
    service = CronService(workspace)
    service._load_store()
    service._save_store()
    assert cron_store.load_rows(service._db) == [_as_saved(monitor, mode="monitor")]


def test_a_broken_jobs_file_falls_back_to_the_backup(workspace: Path) -> None:
    old = _old_store(workspace, _LEGACY_JOB)
    (old / "jobs.json.bak").write_text((old / "jobs.json").read_text(), encoding="utf-8")
    (old / "jobs.json").write_text("{rotto", encoding="utf-8")
    run_pending(workspace)
    assert [j.id for j in CronService(workspace).list_jobs()] == ["legacy-1"]


def test_a_second_pass_merges_without_touching_what_is_there(workspace: Path) -> None:
    _old_store(workspace, _LEGACY_JOB)
    run_pending(workspace)
    service = CronService(workspace)
    job = service.get_job("legacy-1")
    job.name = "rinominato dopo"
    service._save_store()
    # Il ripristino di uno snapshot di prima riporta la cartella vecchia.
    other = copy.deepcopy(_LEGACY_JOB) | {"id": "other-1"}
    _old_store(workspace, _LEGACY_JOB, other)
    assert run_pending(workspace)["cron"] == DONE
    jobs = {j.id: j.name for j in CronService(workspace).list_jobs()}
    assert jobs == {"legacy-1": "rinominato dopo", "other-1": "Daily Loving Message"}


def test_the_old_folder_leaves_thirty_days_later(workspace: Path) -> None:
    _old_store(workspace, _LEGACY_JOB)
    run_pending(workspace, now=1000.0)
    assert (workspace / "cron.migrated").is_dir()
    run_pending(workspace, now=1000.0 + KEEP_MIGRATED_S)
    assert not (workspace / "cron.migrated").exists()


def test_nothing_to_do_without_the_folder(workspace: Path) -> None:
    assert run_pending(workspace)["cron"] == "nothing"
