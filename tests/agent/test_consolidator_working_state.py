"""Lo stato del lavoro: il riassunto di una compattazione dice anche dove si era rimasti.

Il prompt del consolidatore estrae fatti per Dream, e fino al 25/10/2026 era
l'unico prodotto di una compattazione: dopo, il modello sapeva chi e' l'utente ma
non a che punto era il lavoro. Ora le compattazioni di una conversazione che
continua (per lunghezza e per inattivita') chiedono anche lo stato, che torna nel
prompt e non va mai in ``history.jsonl``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jenny.agent.autocompact import AutoCompact
from jenny.agent.consolidator import split_working_state
from jenny.agent.memory import Consolidator, MemoryStore
from jenny.session.keys import UNIFIED_SESSION_KEY
from jenny.session.manager import SessionManager

FACTS = "- [durable] The user keeps the garden notes in serra.md"
STATE = "- Ongoing: a pruning plan for the roses\n- Open items: send the list on Friday"
REPLY = f"{FACTS}\n\n### Working state\n{STATE}"


def _reply(content: str) -> MagicMock:
    return MagicMock(content=content, finish_reason="stop", usage={})


@pytest.fixture
def consolidator(tmp_path):
    provider = MagicMock()
    provider.chat_with_retry = AsyncMock(return_value=_reply(REPLY))
    return Consolidator(
        store=MemoryStore(tmp_path),
        provider=provider,
        model="m",
        sessions=SessionManager(tmp_path),
        context_window_tokens=1000,
        build_messages=MagicMock(return_value=[]),
        get_tool_definitions=MagicMock(return_value=[]),
        max_completion_tokens=100,
    )


def _system_prompts(consolidator) -> list[str]:
    return [
        call.kwargs["messages"][0]["content"]
        for call in consolidator.provider.chat_with_retry.await_args_list
    ]


def _fill(consolidator, key: str = UNIFIED_SESSION_KEY, turns: int = 20):
    session = consolidator.sessions.get_or_create(key)
    for i in range(turns):
        session.add_message("user", f"user msg {i}")
        session.add_message("assistant", f"assistant msg {i}")
    consolidator.sessions.save(session)
    return session


# --- la risposta ----------------------------------------------------------------


@pytest.mark.parametrize("marker", ["### Working state", "## Working State:", "#### working state"])
def test_the_marker_splits_facts_from_the_state(marker):
    facts, state = split_working_state(f"{FACTS}\n\n{marker}\n{STATE}")
    assert facts == FACTS
    assert state == STATE


def test_without_the_marker_everything_is_facts():
    assert split_working_state(FACTS) == (FACTS, None)


def test_a_reply_with_only_the_state_has_no_facts():
    assert split_working_state(f"### Working state\n{STATE}") == ("(nothing)", STATE)


# --- archive --------------------------------------------------------------------


async def test_the_state_never_reaches_the_history(consolidator):
    out: list[str] = []
    summary = await consolidator.archive(
        [{"role": "user", "content": "x"}], session_key=UNIFIED_SESSION_KEY,
        handoff=True, working_state_out=out,
    )

    assert summary == FACTS
    assert out == [STATE]
    entries = consolidator.store.read_unprocessed_history(since_cursor=0)
    assert all("Working state" not in e["content"] for e in entries)
    assert all("pruning plan" not in e["content"] for e in entries)


async def test_the_handoff_prompt_carries_the_earlier_state(consolidator):
    await consolidator.archive(
        [{"role": "user", "content": "x"}], session_key=UNIFIED_SESSION_KEY,
        handoff=True, previous_working_state="- Ongoing: the tomato beds",
    )
    system = _system_prompts(consolidator)[0]
    assert "### Working state" in system
    assert "- Ongoing: the tomato beds" in system


async def test_an_archive_without_handoff_asks_for_facts_only(consolidator):
    """``/new`` e la raccolta del diario: la conversazione non continua."""
    consolidator.provider.chat_with_retry.return_value = _reply(FACTS)
    await consolidator.archive([{"role": "user", "content": "x"}], session_key=UNIFIED_SESSION_KEY)
    assert "### Working state" not in _system_prompts(consolidator)[0]


# --- inattivita' ----------------------------------------------------------------


async def test_an_idle_compaction_keeps_the_state_for_the_prompt(consolidator):
    _fill(consolidator)
    await consolidator.compact_idle_session(UNIFIED_SESSION_KEY, max_suffix=8)

    meta = consolidator.sessions.get_or_create(UNIFIED_SESSION_KEY).metadata["_last_summary"]
    assert meta["text"] == FACTS
    assert meta["working_state"] == STATE
    assert meta["working_state_at"]


async def test_the_prompt_shows_the_facts_then_where_things_stood(consolidator):
    _fill(consolidator)
    await consolidator.compact_idle_session(UNIFIED_SESSION_KEY, max_suffix=8)
    compact = AutoCompact(
        sessions=consolidator.sessions, consolidator=consolidator, session_ttl_minutes=15,
    )
    session = consolidator.sessions.get_or_create(UNIFIED_SESSION_KEY)

    text = compact.summary_for(session, UNIFIED_SESSION_KEY)

    assert text.index(FACTS) < text.index("Where the conversation stood") < text.index(STATE)
    assert "newer and win where they disagree" in text


async def test_the_next_compaction_updates_the_earlier_state(consolidator):
    _fill(consolidator)
    await consolidator.compact_idle_session(UNIFIED_SESSION_KEY, max_suffix=8)
    _fill(consolidator)
    await consolidator.compact_idle_session(UNIFIED_SESSION_KEY, max_suffix=8)

    assert STATE in _system_prompts(consolidator)[-1]


async def test_a_compaction_without_a_state_drops_the_stale_one(consolidator):
    _fill(consolidator)
    await consolidator.compact_idle_session(UNIFIED_SESSION_KEY, max_suffix=8)
    consolidator.provider.chat_with_retry.return_value = _reply(FACTS)
    _fill(consolidator)
    await consolidator.compact_idle_session(UNIFIED_SESSION_KEY, max_suffix=8)

    meta = consolidator.sessions.get_or_create(UNIFIED_SESSION_KEY).metadata["_last_summary"]
    assert "working_state" not in meta


# --- lunghezza ------------------------------------------------------------------


async def test_a_compaction_by_length_keeps_the_state_too(consolidator, monkeypatch):
    session = _fill(consolidator, turns=6)
    estimates = iter([(5_000, "t"), (100, "t")])
    monkeypatch.setattr(
        consolidator, "estimate_session_prompt_tokens", lambda _s: next(estimates, (100, "t")),
    )

    await consolidator.maybe_consolidate_by_tokens(session)

    meta = consolidator.sessions.get_or_create(UNIFIED_SESSION_KEY).metadata["_last_summary"]
    assert meta["working_state"] == STATE
    assert "### Working state" in _system_prompts(consolidator)[0]


async def test_a_project_accumulates_facts_and_keeps_one_state(consolidator):
    key = "project:orto"
    _fill(consolidator, key=key)
    session = consolidator.sessions.get_or_create(key)
    consolidator._persist_summaries(session, ["- [durable] first"], "- Ongoing: one")
    consolidator._persist_summaries(session, ["- [durable] second"], "- Ongoing: two")

    meta = session.metadata["_last_summary"]
    assert "first" in meta["text"] and "second" in meta["text"]
    assert meta["working_state"] == "- Ongoing: two"


# --- una chiamata appesa ----------------------------------------------------------


async def test_a_hanging_summary_call_gives_up_and_leaves_the_session_whole(
    consolidator, monkeypatch,
):
    """Senza tetto, una chiamata appesa teneva la sessione in ``_archiving`` per sempre."""
    import asyncio

    async def hang(**_kwargs):
        await asyncio.sleep(30)

    monkeypatch.setenv("JENNY_LLM_TIMEOUT_S", "0.05")
    consolidator.provider.chat_with_retry = hang
    _fill(consolidator)

    result = await consolidator.compact_idle_session(UNIFIED_SESSION_KEY, max_suffix=8)

    assert result is None
    assert len(consolidator.sessions.get_or_create(UNIFIED_SESSION_KEY).messages) == 40
    assert consolidator.store.read_unprocessed_history(since_cursor=0) == []
