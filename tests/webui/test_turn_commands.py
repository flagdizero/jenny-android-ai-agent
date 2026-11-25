"""I comandi ``turn.*``: «Ho fatto» sotto una risposta, e tornare a prima.

Un turno scrive nel suo registro (``turn-<id>``), crea un promemoria e cerca sul
web; la casa chiede i conti (``turn.summaries``), l'elenco con l'anteprima
(``turn.work``), torna a prima (``turn.undo``) e ci ripensa (``turn.redo``).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from jenny import db as jdb
from jenny.agent import turn_work
from jenny.agent.write_journal import bind_journal, journal_for
from jenny.cron.service import CronService
from jenny.cron.types import CronSchedule
from jenny.security.subagent_policy import check_write, settle_writes
from jenny.webui.commands import CommandError, dispatch_command

TURN = "t-1700000000-abc"


@pytest.fixture(autouse=True)
def _close_databases():
    yield
    jdb.close_all()


@pytest.fixture
async def world(tmp_path: Path):
    root = tmp_path / "workspace"
    (root / "notes").mkdir(parents=True)
    (root / "notes" / "a.md").write_text("before", encoding="utf-8")
    cron = CronService(root, on_job=lambda _: asyncio.sleep(0))
    cron._running = True
    notes: list[tuple[str, str]] = []
    helpers = SimpleNamespace(busy=False)
    ctx = SimpleNamespace(
        get_workspace_root=lambda: root,
        get_cron_service=lambda: cron,
        get_subagent_manager=lambda: SimpleNamespace(working_for_turn=lambda t: helpers.busy),
        get_plan_driver=lambda: SimpleNamespace(
            host=SimpleNamespace(append_note=lambda s, text: notes.append((s, text)))),
    )
    yield SimpleNamespace(root=root, cron=cron, ctx=ctx, notes=notes, helpers=helpers)
    cron.stop()


def _do_a_turn(w, *, write=True, job=True, search=True) -> str | None:
    ref = turn_work.ref_for(w.root, TURN, session="websocket:default", chat="websocket:default")
    job_id = None
    with turn_work.bound(ref), bind_journal(journal_for(w.root, turn_work.turn_journal_key(TURN))):
        if write:
            path = w.root / "notes" / "a.md"
            check_write(path)
            path.write_text("after", encoding="utf-8")
            settle_writes("write_file")
        if job:
            created = w.cron.add_job(
                name="acqua", schedule=CronSchedule(kind="every", every_ms=3_600_000), message="bevi",
                session_key="websocket:default", origin_channel="websocket", origin_chat_id="default")
            turn_work.note_job(created.id, "acqua")
            job_id = created.id
        if search:
            turn_work.note({"kind": "egress", "what": "web_search", "label": "meteo", "undoable": False})
    return job_id


async def test_a_turn_that_touched_things_gets_its_counts(world) -> None:
    _do_a_turn(world)
    out = await dispatch_command(world.ctx, "turn.summaries", {"turns": [TURN, "t-other"]})
    assert out["turns"] == {TURN: {"files": 1, "jobs": 1, "others": 1, "working": False, "state": "open"}}


async def test_a_turn_that_only_searched_gets_no_row(world) -> None:
    _do_a_turn(world, write=False, job=False)
    out = await dispatch_command(world.ctx, "turn.summaries", {"turns": [TURN]})
    assert out["turns"] == {}


async def test_back_to_before_the_turn_and_undo_of_it(world) -> None:
    job_id = _do_a_turn(world)
    work = await dispatch_command(world.ctx, "turn.work", {"turn": TURN})
    kinds = sorted(i["kind"] for i in work["items"])
    assert kinds == ["egress", "file", "job"]
    assert work["expect_end"]

    out = await dispatch_command(world.ctx, "turn.undo", {"turn": TURN, "expect_end": work["expect_end"]})
    assert out["restored"] == 1 and out["jobs"] == 1
    assert (world.root / "notes" / "a.md").read_text(encoding="utf-8") == "before"
    assert world.cron.get_job(job_id) is None
    assert world.notes and "went back" in world.notes[-1][1]
    with pytest.raises(CommandError) as err:
        await dispatch_command(world.ctx, "turn.undo", {"turn": TURN})
    assert err.value.code == "bad_state"

    again = await dispatch_command(world.ctx, "turn.redo", {"turn": TURN})
    assert again["jobs"] == 1
    assert (world.root / "notes" / "a.md").read_text(encoding="utf-8") == "after"
    assert world.cron.get_job(job_id) is not None
    assert (await dispatch_command(world.ctx, "turn.work", {"turn": TURN}))["state"] == "open"


async def test_a_file_changed_after_the_preview_is_a_conflict(world) -> None:
    _do_a_turn(world, job=False, search=False)
    work = await dispatch_command(world.ctx, "turn.work", {"turn": TURN})
    (world.root / "notes" / "a.md").write_text("someone else", encoding="utf-8")
    with pytest.raises(CommandError) as err:
        await dispatch_command(world.ctx, "turn.undo", {"turn": TURN, "expect_end": work["expect_end"]})
    assert err.value.code == "conflict"
    assert (world.root / "notes" / "a.md").read_text(encoding="utf-8") == "someone else"


async def test_not_while_a_helper_of_the_turn_is_working(world) -> None:
    _do_a_turn(world)
    world.helpers.busy = True
    summaries = await dispatch_command(world.ctx, "turn.summaries", {"turns": [TURN]})
    assert summaries["turns"][TURN]["working"] is True
    assert (await dispatch_command(world.ctx, "turn.work", {"turn": TURN}))["expect_end"] is None
    with pytest.raises(CommandError) as err:
        await dispatch_command(world.ctx, "turn.undo", {"turn": TURN})
    assert err.value.code == "busy"
    assert (world.root / "notes" / "a.md").read_text(encoding="utf-8") == "after"


async def test_bad_ids_are_refused(world) -> None:
    for params in ({"turn": "../x"}, {"turn": ""}, {}):
        with pytest.raises(CommandError) as err:
            await dispatch_command(world.ctx, "turn.work", params)
        assert err.value.code == "bad_request"
    with pytest.raises(CommandError):
        await dispatch_command(world.ctx, "turn.summaries", {"turns": "x"})
    with pytest.raises(CommandError):
        await dispatch_command(world.ctx, "turn.redo", {"turn": TURN})
