"""Le RPC ``plan.*`` e il quaderno chiuso all'utente mentre un piano lo tiene.

Il driver è vero, con l'ospite finto dei suoi test; qui si prova il filo:
il prefisso in ``dispatch_command``, la forma delle risposte che la WebUI legge,
la traduzione degli errori, e il rifiuto ``plan_running`` sulle scritture.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jenny.config.loader import save_config
from jenny.config.schema import Config
from jenny.plan.driver import PlanDriver
from jenny.plan.store import PlanStore
from jenny.runtime.context import get_runtime_context
from jenny.webui.commands import CommandContext, CommandError, dispatch_command
from tests.support.plan import KEY, STEPS, FakeBus, FakeHost, write_file


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    nb = ws / "wikis" / "piante"
    write_file(nb, "AGENTS.md", "id: piante")
    write_file(nb, "wiki/index.md", "# indice")
    return ws


@pytest.fixture()
def config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "config.json"
    save_config(Config(), path)
    monkeypatch.setattr(get_runtime_context(), "config_path", path)
    return path


@pytest.fixture()
def host(workspace: Path) -> FakeHost:
    return FakeHost(workspace)


@pytest.fixture()
def driver(workspace: Path, host: FakeHost) -> PlanDriver:
    return PlanDriver(PlanStore.for_workspace(workspace), FakeBus(), host)


def _ctx(workspace: Path, driver: PlanDriver | None) -> CommandContext:
    return CommandContext(
        get_workspace_root=lambda: workspace,
        invalidate_session=lambda _key: None,
        busy_session_keys=lambda: (),
        get_plan_driver=lambda: driver,
    )


async def _rpc(ctx: CommandContext, method: str, **params: Any) -> dict[str, Any]:
    return await dispatch_command(ctx, method, params)


async def _proposed(driver: PlanDriver) -> Any:
    await driver.propose(KEY, "Rifare la wiki", STEPS)
    plan = driver.store.open_plan_for("piante")
    assert plan is not None
    return plan


async def test_get_and_list_read_the_store(workspace: Path, driver: PlanDriver) -> None:
    ctx = _ctx(workspace, driver)
    assert await _rpc(ctx, "plan.get", notebook="piante") == {"ok": True, "plan": None}
    plan = await _proposed(driver)
    got = await _rpc(ctx, "plan.get", id=plan.id)
    assert got["plan"]["status"] == "proposed"
    assert got["plan"]["total"] == 3 and got["plan"]["done"] == 0
    assert got["plan"]["current_step_id"] is None
    assert got["plan"]["can_rollback"] is False
    by_nb = await _rpc(ctx, "plan.get", notebook="piante")
    assert by_nb["plan"]["id"] == plan.id
    listed = await _rpc(ctx, "plan.list")
    assert listed["plans"] == [{
        "id": plan.id, "notebook": "piante", "status": "proposed",
        "title": "Rifare la wiki", "done": 0, "total": 3,
        "updated_at": listed["plans"][0]["updated_at"],
    }]
    full = await _rpc(ctx, "plan.full", id=plan.id)
    assert full["title"] == "Rifare la wiki"
    assert full["steps"][0] == {
        "id": "s1", "title": STEPS[0]["title"], "detail": STEPS[0]["detail"],
        "kind": "research", "status": "pending", "tag": None,
    }


async def test_reads_work_without_a_driver(workspace: Path, driver: PlanDriver) -> None:
    plan = await _proposed(driver)
    ctx = _ctx(workspace, None)
    assert (await _rpc(ctx, "plan.get", id=plan.id))["plan"]["id"] == plan.id
    with pytest.raises(CommandError) as info:
        await _rpc(ctx, "plan.approve", id=plan.id, version=1, cut=[])
    assert info.value.code == "unavailable"


async def test_the_whole_card_lifecycle_over_rpc(
    workspace: Path, driver: PlanDriver, config_path: Path
) -> None:
    ctx = _ctx(workspace, driver)
    plan = await _proposed(driver)
    with pytest.raises(CommandError) as info:
        await _rpc(ctx, "plan.approve", id=plan.id, version=7, cut=[])
    assert info.value.code == "conflict"
    assert await _rpc(ctx, "plan.approve", id=plan.id, version=1, cut=["s3"]) == {"ok": True}
    assert await _rpc(ctx, "plan.pause", id=plan.id) == {"ok": True}
    assert await _rpc(ctx, "plan.resume", id=plan.id) == {"ok": True}
    with pytest.raises(CommandError) as info:
        await _rpc(ctx, "plan.discard", id=plan.id)
    assert info.value.code == "bad_state"

    write_file(workspace / "wikis" / "piante", "wiki/acero.md", "nuova")
    await driver.step_done(KEY, "scritta")
    await driver.on_turn_end(KEY, {})
    files = await _rpc(ctx, "plan.step_files", id=plan.id, step="s1")
    assert files == {"ok": True, "added": ["wiki/acero.md"], "changed": [], "removed": []}

    await driver.blocked(KEY, "Quale acero?")
    assert await _rpc(ctx, "plan.answer", id=plan.id, text="Il rosso") == {"ok": True}
    stopped = await _rpc(ctx, "plan.stop", id=plan.id, then="keep")
    assert stopped == {"ok": True}

    changes = await _rpc(ctx, "plan.changes", id=plan.id)
    assert changes["added"] == ["wiki/acero.md"]
    assert changes["groups"] == {"wiki/acero.md": "pages"}
    preview = await _rpc(ctx, "plan.rollback_preview", id=plan.id)
    assert preview["report"]["counts"]["removed"] == 1
    with pytest.raises(CommandError) as info:
        await _rpc(ctx, "plan.rollback", id=plan.id, keep=[], expect_end="nope")
    assert info.value.code == "conflict"
    done = await _rpc(ctx, "plan.rollback", id=plan.id, keep=[], expect_end=preview["expect_end"])
    assert done["ok"] and done["report"]["removed"] == ["wiki/acero.md"]
    assert not (workspace / "wikis/piante/wiki/acero.md").exists()
    undone = await _rpc(ctx, "plan.undo_rollback", id=plan.id)
    assert undone["report"]["restored"] == ["wiki/acero.md"]


async def test_bad_requests_are_refused_before_the_driver(
    workspace: Path, driver: PlanDriver
) -> None:
    ctx = _ctx(workspace, driver)
    for method, params in [
        ("plan.get", {}),
        ("plan.get", {"id": "../../etc"}),
        ("plan.approve", {"id": "p_000000000000", "version": 1, "cut": "s1"}),
        ("plan.stop", {"id": "p_000000000000", "then": "maybe"}),
        ("plan.answer", {"id": "p_000000000000", "text": "  "}),
        ("plan.rollback", {"id": "p_000000000000", "keep": []}),
    ]:
        with pytest.raises(CommandError) as info:
            await dispatch_command(ctx, method, params)
        assert info.value.code == "bad_request", method
    with pytest.raises(CommandError) as info:
        await _rpc(ctx, "plan.pause", id="p_000000000000")
    assert info.value.code == "not_found"


async def test_a_held_notebook_refuses_the_users_writes(
    workspace: Path, driver: PlanDriver, config_path: Path
) -> None:
    ctx = _ctx(workspace, driver)
    plan = await _proposed(driver)
    # Una proposta non tiene il quaderno.
    out = await _rpc(ctx, "page.write", wiki="piante", page="index.md",
                     content="# nuovo", base="# indice")
    assert out["bytes"]
    await driver.approve(plan.id, plan.version, [])
    for method, params in [
        ("page.write", {"wiki": "piante", "page": "index.md", "content": "x", "base": "# nuovo"}),
        ("board.card.add", {"wiki": "piante", "title": "Acero"}),
        ("audit.create", {"wiki": "piante", "target": "index.md", "comment": "x",
                          "sel_start": 0, "sel_end": 1}),
        ("project.rename", {"name": "piante", "new_name": "giardino"}),
        ("project.delete", {"name": "piante"}),
        ("workspace.write", {"path": "wikis/piante/wiki/x.md", "content": "x"}),
        ("workspace.delete", {"path": "wikis/piante/wiki/index.md"}),
    ]:
        with pytest.raises(CommandError) as info:
            await dispatch_command(ctx, method, params)
        assert info.value.code == "plan_running", method
    assert (workspace / "wikis/piante/wiki/index.md").read_text() == "# nuovo"
    # Guardare la bacheca resta permesso: non è una scrittura del quaderno.
    await _rpc(ctx, "plan.stop", id=plan.id, then="keep")
    out = await _rpc(ctx, "page.write", wiki="piante", page="index.md",
                     content="# dopo", base="# nuovo")
    assert out["bytes"]


async def test_the_lock_is_read_from_disk_without_a_driver(
    workspace: Path, driver: PlanDriver, config_path: Path
) -> None:
    plan = await _proposed(driver)
    await driver.approve(plan.id, plan.version, [])
    ctx = _ctx(workspace, None)
    with pytest.raises(CommandError) as info:
        await _rpc(ctx, "page.write", wiki="piante", page="index.md", content="x", base="# indice")
    assert info.value.code == "plan_running"
