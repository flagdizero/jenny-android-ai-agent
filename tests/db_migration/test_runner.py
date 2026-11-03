"""``run_pending``: una migrazione fatta non rigira, una fallita ritenta,
nessuna ferma il boot, e i resti se ne vanno dopo 30 giorni."""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny import db as jdb
from jenny.db_migration import KEEP_MIGRATED_S, run_pending
from jenny.db_migration.registry import DONE, FAILED, Migration, MigrationResult
from jenny.db_migration.runs import last_run


@pytest.fixture
def workspace(tmp_path: Path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    yield ws
    jdb.close_all()


def test_a_done_migration_does_not_run_again(workspace: Path) -> None:
    calls: list[int] = []

    def run(ctx):
        calls.append(1)
        return MigrationResult(DONE, sources=["x.json"], counts={"jobs": 2})

    reg = (Migration("m", "9.9.9", run),)
    assert run_pending(workspace, registry=reg) == {"m": DONE}
    assert run_pending(workspace, registry=reg) == {"m": "skipped"}
    assert calls == [1]
    row = last_run(jdb.database(workspace.parent, jdb.STATE), "m")
    assert row is not None and row["counts"] == '{"jobs": 2}'


def test_a_failure_is_recorded_and_retried_and_never_raises(workspace: Path) -> None:
    attempts: list[int] = []

    def run(ctx):
        attempts.append(1)
        if len(attempts) == 1:
            raise ValueError("file rotto")
        return MigrationResult(DONE)

    reg = (Migration("m", "9.9.9", run),)
    assert run_pending(workspace, registry=reg) == {"m": FAILED}
    row = last_run(jdb.database(workspace.parent, jdb.STATE), "m")
    assert row is not None and "file rotto" in row["error"]
    assert run_pending(workspace, registry=reg) == {"m": DONE}


def test_the_leftovers_go_thirty_days_after_done(workspace: Path) -> None:
    cleaned: list[int] = []
    reg = (Migration("m", "9.9.9", lambda ctx: MigrationResult(DONE),
                     cleanup=lambda ctx: cleaned.append(1)),)
    run_pending(workspace, registry=reg, now=1000.0)
    run_pending(workspace, registry=reg, now=1000.0 + KEEP_MIGRATED_S - 60)
    assert cleaned == []
    later = 1000.0 + KEEP_MIGRATED_S
    assert run_pending(workspace, registry=reg, now=later) == {"m": "cleaned"}
    assert run_pending(workspace, registry=reg, now=later) == {"m": "skipped"}
    assert cleaned == [1]
