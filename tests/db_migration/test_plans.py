"""``plans``: i piani di ``<runtime_root>/plans/`` nel database dell'annulla.

Le fixture sono la forma vera di prima: un ``plan.json`` per cartella, scritto
da ``Plan.to_dict``, uno di formato 1 con le foto, e ``drafting.json`` con una
chiave ancora nel nome nudo del quaderno.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jenny import db as jdb
from jenny.db_migration import run_pending
from jenny.db_migration.registry import DONE
from jenny.plan.model import validate_proposal
from jenny.plan.store import PlanStore


def _proposal():
    return validate_proposal(
        "Rifare la wiki",
        [{"title": t, "detail": t, "done_when": t, "kind": "check" if t == "Due" else "other"}
         for t in ("Uno", "Due")],
        [{"item": "Acero", "have": "", "missing": "", "strategy": "notebook"}],
    )


@pytest.fixture
def workspace(tmp_path: Path):
    ws = tmp_path / "data" / "workspace"
    ws.mkdir(parents=True)
    yield ws
    jdb.close_all()


def _old_plan(ws: Path, tmp_path: Path, *, status: str = "done", fmt: int = 2) -> dict:
    """Un piano scritto come lo scriveva lo store su file."""
    elsewhere = PlanStore(tmp_path / "elsewhere" / "plans")
    plan = elsewhere.create("piante", _proposal())
    data = plan.to_dict() | {"status": status}
    if fmt == 1:
        data["format"] = 1
        data["photos"] = ["ph1"]
        data.pop("place", None)
    folder = ws.parent / "plans" / plan.id
    folder.mkdir(parents=True)
    (folder / "plan.json").write_text(json.dumps(data), encoding="utf-8")
    if fmt == 1:
        (folder / "photos").mkdir()
    return data


def test_the_plans_and_the_planning_come_in(workspace: Path, tmp_path: Path) -> None:
    done = _old_plan(workspace, tmp_path)
    (workspace.parent / "plans" / "drafting.json").write_text(json.dumps(
        {"format": 2, "notebooks": {"ricette": {"since": "2026-11-01T10:00:00+00:00"}}}))

    assert run_pending(workspace)["plans"] == DONE

    store = PlanStore.for_workspace(workspace)
    assert store.load(done["id"]).to_dict() == done
    assert store.drafting_get("nb:ricette") == {"since": "2026-11-01T10:00:00+00:00"}
    assert not (workspace.parent / "plans").exists()
    assert (workspace.parent / "plans.migrated" / done["id"] / "plan.json").is_file()


def test_an_open_plan_of_format_one_comes_in_stopped(workspace: Path, tmp_path: Path) -> None:
    running = _old_plan(workspace, tmp_path, status="running", fmt=1)
    run_pending(workspace)
    plan = PlanStore.for_workspace(workspace).load(running["id"])
    assert plan.status == "stopped" and plan.place == "nb:piante"
    assert plan.history[-1]["reason"] == "update"


def test_a_second_pass_keeps_what_is_there(workspace: Path, tmp_path: Path) -> None:
    first = _old_plan(workspace, tmp_path)
    run_pending(workspace)
    store = PlanStore.for_workspace(workspace)
    store.update(first["id"], lambda p: setattr(p, "title", "cambiato dopo"))
    # Uno snapshot di prima non riporta i piani (stanno fuori dal workspace), ma
    # una cartella ricomparsa si fonde senza sovrascrivere.
    (workspace.parent / "plans.migrated").rename(workspace.parent / "plans")
    assert run_pending(workspace)["plans"] == DONE
    assert store.load(first["id"]).title == "cambiato dopo"


def test_nothing_to_do_without_the_folder(workspace: Path) -> None:
    assert run_pending(workspace)["plans"] == "nothing"
