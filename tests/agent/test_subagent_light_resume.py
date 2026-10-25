"""La ripresa leggera di ``subagent_send``: brief, resoconto, file scritti, messaggio.

Misurato sul piano del 24/10/2026: un notebook-editor finito, rimandato al lavoro
per due o tre fatti, ripartiva con tutta la sua conversazione (113-623 mila
caratteri) e costava milioni di token di prompt. Qui si fissa quando la ripresa
condensa, cosa porta, e che il budget e le storie corte restano come prima.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from jenny.agent.runner import AgentRunResult, AgentRunSpec
from jenny.agent.subagent import SubagentManager, SubagentSpec
from jenny.agent.subagent_history import (
    KIND_BUDGET,
    history_chars,
    light_resume_messages,
)
from jenny.bus.queue import MessageBus
from jenny.providers.base import LLMProvider
from jenny.session.manager import SessionManager
from jenny.utils.runtime import build_budget_notice_message

BRIEF = "Update the seven plant pages from the research files."
REPORT = "Done: seven pages written, sources cited."


def _manager(tmp_path: Path) -> SubagentManager:
    provider = MagicMock(spec=LLMProvider)
    provider.get_default_model.return_value = "test-model"
    return SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=MessageBus(),
        model="test-model",
        max_tool_result_chars=16_000,
        stall_threshold_s=0.0,
        session_manager=SessionManager(tmp_path),
    )


def _completing_runner(sm: SubagentManager) -> list[AgentRunSpec]:
    seen: list[AgentRunSpec] = []

    async def _run(spec: AgentRunSpec) -> AgentRunResult:
        seen.append(spec)
        messages = list(spec.initial_messages) + [{"role": "assistant", "content": "ok"}]
        return AgentRunResult(final_content="ok", messages=messages, stop_reason="completed")

    sm.runner.run = _run  # type: ignore[method-assign]
    return seen


def _call(call_id: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)},
        }],
    }


def _result(call_id: str, name: str, content: str) -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": call_id, "name": name, "content": content}


def _big_history(*, reads: int = 20) -> list[dict[str, Any]]:
    """Una conversazione da notebook-editor: molte letture, poche scritture, un resoconto."""
    msgs: list[dict[str, Any]] = [
        {"role": "system", "content": "# Subagent\nYou are a notebook-editor."},
        {"role": "user", "content": BRIEF},
    ]
    for i in range(reads):
        msgs.append(_call(f"r{i}", "read_file", {"path": f"wiki/p{i}.md"}))
        msgs.append(_result(f"r{i}", "read_file", "x" * 5_000))
    writes = [
        ("w1", "write_file", {"path": "wiki/entities/Acero.md", "content": "..."}, "Wrote"),
        ("w2", "edit_file", {"path": "wiki/entities/Betulla.md", "old_text": "a",
                             "new_text": "b"}, "Edited"),
        ("w3", "apply_patch", {"edits": [
            {"path": "wiki/entities/Cedro.md", "action": "add", "new_text": "c"},
            {"path": "wiki/entities/Acero.md", "action": "replace", "old_text": "a",
             "new_text": "b"},
        ]}, "Patched"),
        ("w4", "notebook", {"action": "remove_pages", "pages": ["wiki/old.md"],
                            "reason": "dup"}, "Removed"),
        ("w5", "board", {"action": "write_drawer", "card": "Acero", "brief": "b"}, "OK"),
        ("w6", "write_file", {"path": "wiki/fail.md", "content": "x"},
         "Error: path outside the notebook"),
        ("w7", "apply_patch", {"dry_run": True, "edits": [
            {"path": "wiki/dry.md", "action": "add", "new_text": "d"}]}, "dry-run ok"),
    ]
    for call_id, name, args, out in writes:
        msgs.append(_call(call_id, name, args))
        msgs.append(_result(call_id, name, out))
    msgs.append(build_budget_notice_message("You have 3 calls left."))
    msgs.append({"role": "assistant", "content": REPORT})
    return msgs


async def _settle(sm: SubagentManager) -> None:
    await asyncio.gather(*list(sm._running_tasks.values()), return_exceptions=True)
    await asyncio.sleep(0)


async def _finished_lineage(sm: SubagentManager, history: list[dict[str, Any]],
                            **save_kw: Any) -> str:
    """Un lineage finito bene, con *history* come storia salvata."""
    await sm.spawn(task=BRIEF, session_key="unified:default")
    lineage = next(iter(sm._task_statuses.values())).lineage_id
    await _settle(sm)
    assert sm._history.save(lineage, "unified:default", history, **save_kw)
    return lineage


def _assert_well_formed(seed: list[dict[str, Any]]) -> None:
    """Nessun tool, nessuna tool call, ruoli alterni dopo il system, si apre con user."""
    assert not any(m["role"] == "tool" for m in seed)
    assert not any(m.get("tool_calls") for m in seed)
    body = seed[1:] if seed[0]["role"] == "system" else seed
    assert body[0]["role"] == "user" and body[-1]["role"] == "user"
    assert all(a["role"] != b["role"] for a, b in zip(body, body[1:]))


# ---------------------------------------------------------------------------
# send: quale ripresa
# ---------------------------------------------------------------------------


async def test_a_finished_helper_with_a_long_history_resumes_light(tmp_path: Path) -> None:
    sm = _manager(tmp_path)
    seen = _completing_runner(sm)
    history = _big_history()
    assert history_chars(history) > sm.LIGHT_RESUME_MIN_HISTORY_CHARS
    lineage = await _finished_lineage(sm, history)

    outcome = await sm.send(lineage, "Add the new frost dates to your pages.")
    await _settle(sm)

    assert outcome.mode == "resumed"
    assert "light context" in outcome.text
    assert "brief, its report and the list of files it wrote" in outcome.text
    assert "re-read the pages it changes" in outcome.text
    seed = seen[-1].initial_messages
    _assert_well_formed(seed)
    assert [m["role"] for m in seed] == ["system", "user", "assistant", "user"]
    assert seed[0]["content"].startswith("# Subagent")
    assert seed[1]["content"] == BRIEF
    assert seed[2] == {"role": "assistant", "content": REPORT}
    last = seed[3]["content"]
    assert last.endswith("Add the new frost dates to your pages.")
    assert ("Files you wrote or edited: wiki/entities/Acero.md, wiki/entities/Betulla.md, "
            "wiki/entities/Cedro.md.") in last
    assert "Pages you removed: wiki/old.md." in last
    assert "Drawers you rewrote: Acero." in last
    # Una scrittura fallita e una prova a secco non hanno scritto niente.
    assert "wiki/fail.md" not in last and "wiki/dry.md" not in last
    # Le letture se ne sono andate, l'avviso di budget del run vecchio pure.
    assert "wiki/p0.md" not in json.dumps(seed)
    assert "calls left" not in json.dumps(seed)
    assert history_chars(seed) < 3_000


async def test_a_finished_helper_with_a_short_history_resumes_whole(tmp_path: Path) -> None:
    sm = _manager(tmp_path)
    seen = _completing_runner(sm)
    history = _big_history(reads=2)
    assert history_chars(history) <= sm.LIGHT_RESUME_MIN_HISTORY_CHARS
    lineage = await _finished_lineage(sm, history)
    stored = sm._history.load(lineage)

    outcome = await sm.send(lineage, "one more fact")
    await _settle(sm)

    assert "from its saved conversation" in outcome.text
    assert "light context" not in outcome.text
    seed = seen[-1].initial_messages
    assert stored is not None and seed[: len(stored)] == stored
    assert seed[-1] == {"role": "user", "content": "one more fact"}


async def test_a_budget_stop_with_a_long_history_resumes_whole(tmp_path: Path) -> None:
    sm = _manager(tmp_path)
    seen = _completing_runner(sm)
    history = _big_history()
    lineage = await _finished_lineage(sm, history, kind=KIND_BUDGET)
    sm._lineages[lineage].last_state = "budget"

    outcome = await sm.send(lineage, "finish the remaining pages")
    await _settle(sm)

    assert "from its saved conversation" in outcome.text
    seed = seen[-1].initial_messages
    assert any(m["role"] == "tool" for m in seed), "a budget stop keeps what it read"
    assert seed[-1] == {"role": "user", "content": "finish the remaining pages"}


async def test_a_budget_stop_over_the_cap_is_still_refused(tmp_path: Path) -> None:
    import pytest

    from jenny.agent.subagent import SubagentSendError

    sm = _manager(tmp_path)
    _completing_runner(sm)
    history = _big_history(reads=40)
    assert history_chars(history) > sm.CONTINUE_MAX_HISTORY_CHARS
    lineage = await _finished_lineage(sm, history, kind=KIND_BUDGET)
    sm._lineages[lineage].last_state = "budget"

    with pytest.raises(SubagentSendError, match="can no longer be resumed"):
        await sm.send(lineage, "finish")


async def test_a_light_resume_keeps_the_taint(tmp_path: Path) -> None:
    sm = _manager(tmp_path)
    history = _big_history()
    sm._history.save("lin-1", "unified:default", history, taint=("web_fetch:a.org",))
    launched: list[tuple[SubagentSpec, list[dict[str, Any]]]] = []

    async def _fake_launch(spec, **kw):
        launched.append((spec, kw["resume_messages"]))
        return "t2", "lin-1"

    sm._launch = _fake_launch  # type: ignore[method-assign]
    outcome = await sm._resume_lineage(
        "lin-1", SubagentSpec(task=BRIEF, label="l"), 1, history, "continue",
        quick=None, finished=True,
    )
    assert "light context" in outcome.text
    spec, seed = launched[0]
    assert spec.taint_seed == ("web_fetch:a.org",)
    assert not any(m["role"] == "tool" for m in seed)


# ---------------------------------------------------------------------------
# light_resume_messages
# ---------------------------------------------------------------------------


def test_a_second_light_resume_keeps_the_first_exchange() -> None:
    first = light_resume_messages(_big_history(), "Add the frost dates.")
    assert first is not None
    # Il secondo run: ha letto e scritto ancora, poi ha chiuso con un resoconto.
    second_run = first + [
        _call("r9", "read_file", {"path": "wiki/entities/Acero.md"}),
        _result("r9", "read_file", "y" * 5_000),
        _call("w9", "edit_file", {"path": "wiki/entities/Dalia.md", "old_text": "a",
                                  "new_text": "b"}),
        _result("w9", "edit_file", "Edited"),
        {"role": "assistant", "content": "Frost dates added."},
    ]
    seed = light_resume_messages(second_run, "Now the soil.")
    assert seed is not None
    _assert_well_formed(seed)
    texts = [m["content"] for m in seed]
    assert texts[1] == BRIEF
    assert texts[2] == REPORT
    assert "Add the frost dates." in texts[3] and "wiki/entities/Cedro.md" in texts[3]
    assert texts[4] == "Frost dates added."
    assert "wiki/entities/Dalia.md" in texts[5] and texts[5].endswith("Now the soil.")


def test_a_history_without_a_report_is_not_condensed() -> None:
    history = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": BRIEF},
        _call("r1", "read_file", {"path": "a"}),
        _result("r1", "read_file", "x"),
    ]
    assert light_resume_messages(history, "go on") is None


def test_a_history_without_writes_says_so() -> None:
    history = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": BRIEF},
        _call("r1", "read_file", {"path": "a"}),
        _result("r1", "read_file", "x"),
        {"role": "assistant", "content": REPORT},
    ]
    seed = light_resume_messages(history, "go on")
    assert seed is not None
    assert "No file writes were found" in seed[-1]["content"]
