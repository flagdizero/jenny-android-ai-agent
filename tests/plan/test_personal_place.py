"""Un piano nella chat personale non parla di «quaderno» e non occupa gli altri canali.

Il piano è nato nei quaderni; nella chat personale — una sessione sola per la
WebUI, Telegram, la mascotte e le notifiche — i testi, i rifiuti e il prompt
ragionavano ancora «un piano = un quaderno». Qui si fissa che il posto venga da
``PlanPlace`` e non da una stringa scritta a mano.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from jenny.bus.events import InboundMessage
from jenny.plan.driver import PLAN_MODE_META, PlanDriver
from jenny.plan.store import PlanStore
from jenny.runtime.user_text import say
from jenny.security.workspace_access import WORKSPACE_READONLY_METADATA_KEY
from tests.support.plan import KEY, FakeBus, FakeHost, propose, start_drafting, write_file

PERSONAL_KEY = "unified:default"
_STEPS = [
    {"title": "Riordina le app", "detail": "Metti in ordine le app.", "kind": "other",
     "done_when": "Le app sono in ordine."},
    {"title": "Rileggi", "detail": "Rileggi il risultato.", "kind": "other",
     "done_when": "Il risultato è riletto."},
]


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    write_file(ws / "wikis" / "piante", "AGENTS.md", "id: piante")
    write_file(ws / "wikis" / "piante", "wiki/index.md", "# indice")
    return ws


@pytest.fixture()
def driver(workspace: Path) -> PlanDriver:
    return PlanDriver(
        PlanStore.for_workspace(workspace), FakeBus(), FakeHost(workspace), personal_plans=True
    )


# ── i canali ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("channel", ["telegram", "floating", "notification"])
async def test_planning_does_not_reach_the_other_channels(
    driver: PlanDriver, channel: str
) -> None:
    """Si preme Piano in casa e ci si allontana: una domanda fatta alla mascotte,
    da Telegram o da una notifica resta una chat, non un turno di pianificazione
    in sola lettura."""
    await start_drafting(driver, PERSONAL_KEY)
    elsewhere: dict[str, object] = {}
    await driver.before_user_turn(PERSONAL_KEY, elsewhere, channel=channel)
    assert PLAN_MODE_META not in elsewhere
    assert WORKSPACE_READONLY_METADATA_KEY not in elsewhere
    assert driver.drafting_for(PERSONAL_KEY) is not None, "la pianificazione resta aperta"

    at_home: dict[str, object] = {}
    await driver.before_user_turn(PERSONAL_KEY, at_home, channel="websocket")
    assert at_home.get(PLAN_MODE_META) is True
    assert at_home.get(WORKSPACE_READONLY_METADATA_KEY) is True


async def test_without_a_channel_the_turn_is_planning_as_before(driver: PlanDriver) -> None:
    """Chi chiama senza dire il canale (i test, una RPC) non cambia comportamento."""
    await start_drafting(driver, PERSONAL_KEY)
    meta: dict[str, object] = {}
    await driver.before_user_turn(PERSONAL_KEY, meta)
    assert meta.get(PLAN_MODE_META) is True


# ── i testi per il modello ──────────────────────────────────────────────────


async def test_refusals_name_the_place_the_plan_is_in(driver: PlanDriver) -> None:
    nowhere = await driver.propose(PERSONAL_KEY, "Riordino", _STEPS)
    assert "in the personal chat" in nowhere and "notebook" not in nowhere

    await start_drafting(driver, PERSONAL_KEY, reads=0)
    unread = await driver.propose(PERSONAL_KEY, "Riordino", _STEPS)
    assert unread.startswith("Not yet: look at the workspace first")

    in_notebook = await driver.propose(KEY, "Riordino", _STEPS)
    assert "in this notebook" in in_notebook


async def test_a_started_personal_plan_refuses_a_second_one_by_its_place(
    driver: PlanDriver,
) -> None:
    await start_drafting(driver, PERSONAL_KEY)
    assert "card" in await propose(driver, "Riordino", _STEPS, key=PERSONAL_KEY)
    plan = driver.store.open_plan_for("personal")
    assert plan is not None
    await driver.approve(plan.id, plan.version, [])
    again = await driver.propose(PERSONAL_KEY, "Un altro", _STEPS)
    assert again.startswith("Not now: the personal chat already has a plan")


def test_the_rollback_notes_name_what_went_back() -> None:
    from jenny.plan.driver import _rollback_note, _undo_note
    from jenny.plan.model import Plan

    def plan(place: str, notebook: str) -> Plan:
        p = MagicMock(spec=Plan)
        p.title, p.place, p.notebook = "Riordino", place, notebook
        return p

    counts = {"restored": 2, "removed": 1, "left": 0}
    personal = plan("personal", "")
    assert "rolled the workspace back" in _rollback_note(personal, counts)
    assert "the workspace is back" in _undo_note(personal, counts)
    notebook = plan("nb:piante", "piante")
    assert "rolled the notebook back" in _rollback_note(notebook, counts)
    assert "the notebook is back" in _undo_note(notebook, counts)


# ── il prompt ───────────────────────────────────────────────────────────────


def test_the_personal_planning_prompt_names_no_tool_it_does_not_have() -> None:
    """``notebook`` è nascosto fuori da un quaderno (``AgentLoop._turn_tool_view``)
    e l'editore è rifiutato (``SpawnTool``): il prompt non li ordina."""
    from jenny.utils.prompt_templates import render_template

    personal = render_template("agent/plan_mode.md", notebook=False, where="x")
    assert "notebook map" not in personal
    assert "notebook read_many" not in personal
    assert "notebook-editor" not in personal
    assert "`coder` helper with a script" in personal
    notebook = render_template("agent/plan_mode.md", notebook=True, where="x")
    assert "notebook map" in notebook and "notebook-editor" in notebook


def test_the_personal_step_prompt_does_not_send_to_the_editor() -> None:
    from jenny.utils.prompt_templates import render_template

    step = {"n": 1, "title": "t", "detail": "d", "done_when": "w", "kind": "write"}
    common = dict(reminder=False, title="p", steps=[], total=1, step=step, covers=[], waiting=[])
    personal = render_template("agent/plan_step.md", personal=True, **common)
    assert "remove_pages" not in personal
    notebook = render_template("agent/plan_step.md", personal=False, **common)
    assert "remove_pages" in notebook


# ── il rifiuto degli aiutanti ───────────────────────────────────────────────


async def test_the_planning_refusal_does_not_point_to_a_helper_the_place_refuses() -> None:
    """Nella chat personale l'editore non esiste: il rifiuto di un aiutante
    durante la pianificazione non lo consiglia."""
    from jenny.agent.tools.context import (
        RequestContext,
        bind_request_context,
        reset_request_context,
    )
    from jenny.agent.tools.spawn import SpawnTool

    mgr = MagicMock()
    mgr.spawn = AsyncMock(return_value="started")
    tool = SpawnTool(mgr)

    async def refusal(session_key: str) -> str:
        ctx = RequestContext(
            channel="websocket", chat_id=session_key, session_key=session_key,
            metadata={"plan_mode": True},
        )
        tool.set_context(ctx)
        token = bind_request_context(ctx)
        try:
            return await tool.execute(task="cerca", agent_type="researcher")
        finally:
            reset_request_context(token)

    personal = await refusal(PERSONAL_KEY)
    assert personal.startswith("Cannot spawn a 'researcher' while preparing a plan")
    assert "notebook-editor" not in personal and "`research` step" in personal
    notebook = await refusal(KEY)
    assert "A notebook-editor can take stock" in notebook
    assert not mgr.spawn.await_count


# ── /stop ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("key", "notebook", "wanted"),
    [(PERSONAL_KEY, None, "plan.stopped_personal"), (KEY, "piante", "plan.stopped")],
)
async def test_stop_does_not_call_a_personal_plan_a_notebook(
    key: str, notebook: str | None, wanted: str
) -> None:
    from jenny.command.builtin import cmd_stop
    from jenny.command.router import CommandContext

    place = MagicMock()
    place.notebook = notebook
    plan_driver = MagicMock()
    plan_driver.held_status.return_value = "running"
    plan_driver.stop_for_session = AsyncMock(return_value=True)
    plan_driver.place_of.return_value = place
    loop = MagicMock()
    loop._cancel_active_tasks = AsyncMock(return_value=0)
    loop.plan_driver = plan_driver
    msg = InboundMessage(channel="telegram", sender_id="u", chat_id="c", content="/stop")
    ctx = CommandContext(msg=msg, session=None, key=key, raw="/stop", loop=loop)
    out = await cmd_stop(ctx)
    assert out.content == say(wanted)
    assert ("quaderno" in out.content or "notebook" in out.content) is (notebook is not None)
