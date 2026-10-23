"""Lo store dei piani: percorsi, lock, la regola del piano aperto, rinomino, potatura."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from jenny.plan.model import Plan, PlanFormatError, apply_transition, validate_proposal
from jenny.plan.store import (
    PlanConflictError,
    PlanNotFoundError,
    PlanStore,
    is_plan_id,
)
from jenny.snapshot.locations import plans_dir_for


def _proposal(*titles: str):
    titles = titles or ("Uno", "Due", "Tre")
    return validate_proposal("Rifare la wiki", [{"title": t, "detail": t} for t in titles])


def _store(tmp_path: Path) -> PlanStore:
    return PlanStore(tmp_path / "plans")


def _close(store: PlanStore, plan: Plan, *statuses: str, at: str | None = None) -> Plan:
    def _go(p: Plan) -> None:
        for status in statuses:
            apply_transition(p, status)

    plan = store.update(plan.id, _go)
    if at is not None:
        # ``update`` timbra ``updated_at`` adesso: per invecchiare un piano si
        # riscrive il file a mano, come lo troverebbe la potatura fra un mese.
        path = store.plan_dir(plan.id) / "plan.json"
        data = json.loads(path.read_text("utf-8"))
        data["updated_at"] = at
        path.write_text(json.dumps(data), "utf-8")
    return plan


def test_plans_live_next_to_the_workspace_not_inside(tmp_path: Path) -> None:
    workspace = tmp_path / "files" / "workspace"
    workspace.mkdir(parents=True)
    store = PlanStore.for_workspace(workspace)
    assert store.plans_dir == (tmp_path / "files" / "plans").resolve()
    assert plans_dir_for(workspace) == store.plans_dir
    assert not store.plans_dir.is_relative_to(workspace.resolve())


def test_create_load_round_trip(tmp_path: Path) -> None:
    store = _store(tmp_path)
    plan = store.create("piante", _proposal(), notes="appunti")
    assert is_plan_id(plan.id)
    assert plan.session_key == "project:piante"
    assert plan.status == "proposed"
    assert plan.version == 1
    assert plan.history[-1]["event"] == "proposed"
    loaded = store.load(plan.id)
    assert loaded.to_dict() == plan.to_dict()
    assert loaded.notes == "appunti"


@pytest.mark.parametrize("bad", ["../etc", "p_123", "p_0123456789AB", "", "p_0123456789ab/x"])
def test_ids_are_validated_before_building_paths(tmp_path: Path, bad: str) -> None:
    store = _store(tmp_path)
    assert not is_plan_id(bad)
    with pytest.raises(PlanNotFoundError):
        store.load(bad)
    with pytest.raises(PlanNotFoundError):
        store.plan_dir(bad)


def test_missing_plan_is_not_found(tmp_path: Path) -> None:
    with pytest.raises(PlanNotFoundError):
        _store(tmp_path).load("p_0123456789ab")


def test_a_file_claiming_another_id_is_refused(tmp_path: Path) -> None:
    store = _store(tmp_path)
    plan = store.create("piante", _proposal())
    path = store.plan_dir(plan.id) / "plan.json"
    data = json.loads(path.read_text("utf-8"))
    data["id"] = "p_ffffffffffff"
    path.write_text(json.dumps(data), "utf-8")
    with pytest.raises(PlanFormatError):
        store.load(plan.id)
    # Gli elenchi lo saltano invece di cadere.
    assert store.list_all() == []


def test_update_reads_inside_the_lock_and_writes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    plan = store.create("piante", _proposal())
    store.update(plan.id, lambda p: apply_transition(p, "running", event="approved"))
    assert store.load(plan.id).status == "running"


def test_update_that_raises_writes_nothing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    plan = store.create("piante", _proposal())
    before = (store.plan_dir(plan.id) / "plan.json").read_text("utf-8")

    def _boom(p: Plan) -> None:
        p.title = "cambiato"
        raise RuntimeError("no")

    with pytest.raises(RuntimeError):
        store.update(plan.id, _boom)
    assert (store.plan_dir(plan.id) / "plan.json").read_text("utf-8") == before


def test_concurrent_updates_do_not_lose_writes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    plan = store.create("piante", _proposal())

    def _append(p: Plan) -> None:
        p.history.append({"at": "x", "event": "tick"})

    threads = [
        threading.Thread(target=lambda: [store.update(plan.id, _append) for _ in range(10)])
        for _ in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(1 for h in store.load(plan.id).history if h["event"] == "tick") == 40


def test_a_second_proposal_supersedes_the_first(tmp_path: Path) -> None:
    store = _store(tmp_path)
    v1 = store.create("piante", _proposal("Uno", "Due", "Tre"))
    v2 = store.create("piante", _proposal("Zero", "Uno", "Tre"))
    assert v2.version == 2
    assert v2.replaces == v1.id
    assert [s.tag for s in v2.steps] == ["new", None, None]
    assert v2.dropped == [{"id": "s2", "title": "Due"}]
    old = store.load(v1.id)
    assert old.status == "superseded"
    assert old.history[-1]["by"] == v2.id
    assert store.open_plan_for("piante") is not None
    assert store.open_plan_for("piante").id == v2.id  # type: ignore[union-attr]


def test_one_open_plan_per_notebook(tmp_path: Path) -> None:
    store = _store(tmp_path)
    plan = store.create("piante", _proposal())
    store.update(plan.id, lambda p: apply_transition(p, "running"))
    with pytest.raises(PlanConflictError) as info:
        store.create("piante", _proposal())
    assert info.value.plan_id == plan.id
    # Un altro quaderno non c'entra.
    assert store.create("ricette", _proposal()).notebook == "ricette"
    # Chiuso il piano, se ne propone un altro (v1: non è una revisione).
    _close(store, plan, "done")
    again = store.create("piante", _proposal())
    assert again.version == 1 and again.replaces is None


def test_list_and_open_plan_for(tmp_path: Path) -> None:
    store = _store(tmp_path)
    a = store.create("piante", _proposal())
    store.update(a.id, lambda p: apply_transition(p, "discarded"))
    b = store.create("piante", _proposal())
    store.create("ricette", _proposal())
    assert {p.id for p in store.list_for_notebook("piante")} == {a.id, b.id}
    assert store.open_plan_for("piante").id == b.id  # type: ignore[union-attr]
    assert store.open_plan_for("vuoto") is None


def test_follow_rename(tmp_path: Path) -> None:
    store = _store(tmp_path)
    a = store.create("piante", _proposal())
    other = store.create("ricette", _proposal())
    assert store.follow_rename("piante", "giardino") == [a.id]
    moved = store.load(a.id)
    assert moved.notebook == "giardino"
    assert moved.session_key == "project:giardino"
    assert store.load(other.id).notebook == "ricette"
    assert store.list_for_notebook("piante") == []


def test_delete_for_notebook_removes_the_whole_dir(tmp_path: Path) -> None:
    store = _store(tmp_path)
    a = store.create("piante", _proposal())
    (store.photos_dir(a.id) / "objects").mkdir(parents=True)
    keep = store.create("ricette", _proposal())
    assert store.delete_for_notebook("piante") == [a.id]
    assert not store.plan_dir(a.id).exists()
    assert store.plan_dir(keep.id).exists()


def test_prune_keeps_the_newest_closed_and_never_an_open_one(tmp_path: Path) -> None:
    store = _store(tmp_path)
    now = datetime.now(timezone.utc)
    ran: list[Plan] = []
    for i in range(7):
        plan = store.create("piante", _proposal())
        stamp = (now - timedelta(days=1, minutes=i)).isoformat(timespec="seconds")
        ran.append(_close(store, plan, "running", "done", at=stamp))
    # Le revisioni di una proposta non spingono fuori i piani che hanno girato.
    for _ in range(6):
        store.create("piante", _proposal())
    live = store.open_plan_for("piante")
    assert live is not None
    other = _close(store, store.create("ricette", _proposal()), "running", "stopped")

    removed = set(store.prune(keep_closed=5, max_age_days=30, now=now))

    assert removed == {ran[5].id, ran[6].id}
    assert store.load(live.id).status == "proposed"
    assert store.load(other.id).status == "stopped"
    superseded = [p for p in store.list_for_notebook("piante") if p.status == "superseded"]
    assert len(superseded) == 5
    for plan in ran[:5]:
        assert store.load(plan.id).status == "done"


def test_prune_drops_closed_plans_older_than_max_age(tmp_path: Path) -> None:
    store = _store(tmp_path)
    now = datetime.now(timezone.utc)
    old = _close(
        store,
        store.create("piante", _proposal()),
        "running",
        "done",
        at=(now - timedelta(days=31)).isoformat(timespec="seconds"),
    )
    fresh = _close(store, store.create("piante", _proposal()), "running", "done")
    assert store.prune(now=now) == [old.id]
    assert not store.plan_dir(old.id).exists()
    assert store.load(fresh.id).status == "done"
