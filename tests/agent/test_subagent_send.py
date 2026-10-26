"""Tests per ``subagent_send``: iniezione mid-run, resume, degrado al rilancio."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from jenny.agent.runner import AgentRunResult, AgentRunSpec
from jenny.agent.subagent import (
    MAX_AUTO_ATTEMPTS,
    SubagentManager,
    SubagentSendError,
)
from jenny.agent.subagent_history import SubagentHistoryStore
from jenny.bus.queue import MessageBus
from jenny.providers.base import LLMProvider
from jenny.session.manager import SessionManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _manager(tmp_path: Path, **kw) -> SubagentManager:
    provider = MagicMock(spec=LLMProvider)
    provider.get_default_model.return_value = "test-model"
    defaults = dict(
        provider=provider,
        workspace=tmp_path,
        bus=MessageBus(),
        model="test-model",
        max_tool_result_chars=16_000,
        # Watchdog spento: lo stallo ha i suoi test.
        stall_threshold_s=0.0,
        session_manager=SessionManager(tmp_path),
    )
    defaults.update(kw)
    return SubagentManager(**defaults)


def _completing_runner(sm: SubagentManager, *, content: str = "done") -> list[AgentRunSpec]:
    """Runner che completa subito, restituendo la conversazione accumulata."""
    seen: list[AgentRunSpec] = []

    async def _run(spec: AgentRunSpec) -> AgentRunResult:
        seen.append(spec)
        messages = list(spec.initial_messages) + [{"role": "assistant", "content": content}]
        return AgentRunResult(
            final_content=content, messages=messages, stop_reason="completed"
        )

    sm.runner.run = _run  # type: ignore[method-assign]
    return seen


def _failing_runner(sm: SubagentManager) -> list[AgentRunSpec]:
    seen: list[AgentRunSpec] = []

    async def _run(spec: AgentRunSpec) -> AgentRunResult:
        seen.append(spec)
        return AgentRunResult(
            final_content=None, messages=[], stop_reason="error", error="boom"
        )

    sm.runner.run = _run  # type: ignore[method-assign]
    return seen


async def _spawn_and_settle(sm: SubagentManager, **kw) -> str:
    """Spawna, aspetta la terminazione e ritorna il lineage id."""
    await sm.spawn(task=kw.pop("task", "do it"), session_key="unified:default", **kw)
    task_id = next(iter(sm._task_statuses))
    lineage = sm._task_statuses[task_id].lineage_id
    await asyncio.gather(*[t for t in sm._running_tasks.values()], return_exceptions=True)
    await asyncio.sleep(0)
    return lineage


# ---------------------------------------------------------------------------
# 1. iniezione in un subagent vivo
# ---------------------------------------------------------------------------


class TestInjection:
    async def test_send_reaches_a_live_subagent_without_cancelling_it(
        self, tmp_path: Path
    ) -> None:
        sm = _manager(tmp_path)
        started = asyncio.Event()
        drained: list[list[dict]] = []
        release = asyncio.Event()

        async def _run(spec: AgentRunSpec) -> AgentRunResult:
            started.set()
            await release.wait()
            # Il runner vero chiama la callback fra le iterazioni: qui si simula
            # quel singolo punto di consumo.
            drained.append(await spec.injection_callback(limit=3))
            return AgentRunResult(
                final_content="ok",
                messages=list(spec.initial_messages),
                stop_reason="completed",
            )

        sm.runner.run = _run  # type: ignore[method-assign]
        await sm.spawn(task="long job", session_key="unified:default")
        await started.wait()
        task_id = next(iter(sm._task_statuses))
        bg = sm._running_tasks[task_id]

        outcome = await sm.send(task_id, "also cover 2023")
        assert outcome.mode == "injected"
        assert task_id in outcome.text
        # Il subagent non e stato toccato: sta ancora girando.
        assert not bg.done()
        assert not bg.cancelled()

        release.set()
        await bg
        assert drained == [[{"role": "user", "content": "also cover 2023"}]]

    async def test_injection_works_through_the_lineage_id_too(self, tmp_path: Path) -> None:
        sm = _manager(tmp_path)
        release = asyncio.Event()
        started = asyncio.Event()

        async def _run(spec: AgentRunSpec) -> AgentRunResult:
            started.set()
            await release.wait()
            return AgentRunResult(final_content="ok", messages=[], stop_reason="completed")

        sm.runner.run = _run  # type: ignore[method-assign]
        await sm.spawn(task="long job", session_key="unified:default")
        await started.wait()
        task_id = next(iter(sm._task_statuses))
        lineage = sm._task_statuses[task_id].lineage_id

        assert (await sm.send(lineage, "hello")).mode == "injected"
        release.set()
        await sm._running_tasks[task_id]

    async def test_a_full_mailbox_is_refused_with_a_readable_error(
        self, tmp_path: Path
    ) -> None:
        sm = _manager(tmp_path)
        release = asyncio.Event()
        started = asyncio.Event()

        async def _run(spec: AgentRunSpec) -> AgentRunResult:
            started.set()
            await release.wait()
            return AgentRunResult(final_content="ok", messages=[], stop_reason="completed")

        sm.runner.run = _run  # type: ignore[method-assign]
        await sm.spawn(task="long job", session_key="unified:default")
        await started.wait()
        task_id = next(iter(sm._task_statuses))

        for i in range(8):
            assert (await sm.send(task_id, f"msg {i}")).mode == "injected"
        with pytest.raises(SubagentSendError, match="undelivered messages"):
            await sm.send(task_id, "one too many")

        release.set()
        await sm._running_tasks[task_id]

    async def test_empty_message_is_refused(self, tmp_path: Path) -> None:
        sm = _manager(tmp_path)
        with pytest.raises(SubagentSendError, match="empty"):
            await sm.send("whatever", "   ")

    async def test_unknown_target_is_refused(self, tmp_path: Path) -> None:
        sm = _manager(tmp_path)
        with pytest.raises(SubagentSendError, match="unknown subagent"):
            await sm.send("nope", "hello")


# ---------------------------------------------------------------------------
# 2. resume da storia salvata
# ---------------------------------------------------------------------------


class TestResume:
    async def test_resume_seeds_the_stored_conversation_plus_the_follow_up(
        self, tmp_path: Path
    ) -> None:
        sm = _manager(tmp_path)
        seen = _completing_runner(sm, content="here is the report")
        lineage = await _spawn_and_settle(sm, task="write the report")

        stored = sm._history.load(lineage)
        assert stored is not None and stored[-1]["content"] == "here is the report"

        outcome = await sm.send(lineage, "no, change the title")
        assert outcome.mode == "resumed"
        await asyncio.gather(*list(sm._running_tasks.values()), return_exceptions=True)

        resumed = seen[-1].initial_messages
        # Continuazione coerente: la storia intera piu il solo follow-up, senza
        # ri-specificare il lavoro.
        assert resumed[: len(stored)] == stored
        assert resumed[-1] == {"role": "user", "content": "no, change the title"}
        assert resumed[0]["role"] == "system"

    async def test_resume_does_not_consume_the_automatic_restart_budget(
        self, tmp_path: Path
    ) -> None:
        sm = _manager(tmp_path)
        _completing_runner(sm)
        lineage = await _spawn_and_settle(sm, task="write the report")

        for i in range(MAX_AUTO_ATTEMPTS + 2):
            assert (await sm.send(lineage, f"tweak {i}")).mode == "resumed"
            await asyncio.gather(
                *list(sm._running_tasks.values()), return_exceptions=True
            )
            await asyncio.sleep(0)

        # ``attempt`` non e avanzato, quindi il tetto dei rilanci automatici e
        # ancora intatto e lo snapshot continua a dirlo.
        assert sm._lineages[lineage].attempt == 1
        recent = sm.status_snapshot("unified:default")["recent"]
        assert recent and all(e["can_restart"] for e in recent)
        assert all(e["attempt"] == 1 for e in recent)

        # ...e un rilancio vero resta possibile subito dopo.
        await sm.restart(lineage, extra_instructions="retry")
        assert sm._lineages[lineage].attempt == 2

    async def test_resume_rewrites_the_history_of_the_lineage(
        self, tmp_path: Path
    ) -> None:
        sm = _manager(tmp_path)
        _completing_runner(sm, content="v1")
        lineage = await _spawn_and_settle(sm, task="write the report")
        first = sm._history.load(lineage)
        assert first is not None

        _completing_runner(sm, content="v2")
        await sm.send(lineage, "make it shorter")
        await asyncio.gather(*list(sm._running_tasks.values()), return_exceptions=True)
        await asyncio.sleep(0)

        second = sm._history.load(lineage)
        assert second is not None
        assert len(second) > len(first)
        assert second[-1]["content"] == "v2"


# ---------------------------------------------------------------------------
# 3. degrado al rilancio
# ---------------------------------------------------------------------------


class TestDegradeToRestart:
    async def test_expired_history_of_a_finished_job_refuses_the_relaunch(
        self, tmp_path: Path
    ) -> None:
        """Fino al 26/10 degradava al rilancio: tutto il brief rifatto da zero sopra
        pagine che esistono gia' (Titan: due editor «relaunched from scratch»). Chi
        chiama sa cosa fare: un aiutante nuovo con il brief di cio' che c'e'."""
        sm = _manager(tmp_path)
        _completing_runner(sm)
        lineage = await _spawn_and_settle(sm, task="write the report")
        # Invecchia la storia oltre la TTL.
        sm._history.ttl_s = 0.001
        await asyncio.sleep(0.002)

        with pytest.raises(SubagentSendError, match="no longer saved"):
            await sm.send(lineage, "change the title")
        assert not sm._running_tasks
        assert sm._lineages[lineage].attempt == 1

    async def test_the_lineage_cap_leaves_the_oldest_without_a_resume(
        self, tmp_path: Path
    ) -> None:
        from jenny.agent.subagent_history import MAX_HISTORY_PER_ORIGIN

        sm = _manager(tmp_path, max_concurrent_subagents=8)
        _completing_runner(sm)
        lineages = [
            await _spawn_and_settle(sm, task=f"job {i}", label=f"job {i}")
            for i in range(MAX_HISTORY_PER_ORIGIN + 1)
        ]

        assert sm._history.load(lineages[0]) is None
        assert sm._history.load(lineages[-1]) is not None
        with pytest.raises(SubagentSendError, match="no longer saved"):
            await sm.send(lineages[0], "follow up")
        assert (await sm.send(lineages[-1], "follow up")).mode == "resumed"
        await asyncio.gather(*list(sm._running_tasks.values()), return_exceptions=True)

    async def test_a_failed_subagent_has_no_history_and_is_restarted(
        self, tmp_path: Path
    ) -> None:
        sm = _manager(tmp_path)
        _failing_runner(sm)
        lineage = await _spawn_and_settle(sm, task="write the report")

        assert sm._history.load(lineage) is None
        assert (await sm.send(lineage, "try again with the CSV")).mode == "restarted"
        await asyncio.gather(*list(sm._running_tasks.values()), return_exceptions=True)

    async def test_a_failure_erases_the_history_of_an_earlier_good_attempt(
        self, tmp_path: Path
    ) -> None:
        """La storia riflette solo un lineage il cui ultimo tentativo e riuscito."""
        sm = _manager(tmp_path)
        _completing_runner(sm)
        lineage = await _spawn_and_settle(sm, task="write the report")
        assert sm._history.load(lineage) is not None

        _failing_runner(sm)
        await sm.restart(lineage, extra_instructions="again")
        await asyncio.gather(*list(sm._running_tasks.values()), return_exceptions=True)
        await asyncio.sleep(0)
        assert sm._history.load(lineage) is None

    async def test_corrupt_history_of_a_finished_job_refuses_the_relaunch(
        self, tmp_path: Path
    ) -> None:
        sm = _manager(tmp_path)
        _completing_runner(sm)
        lineage = await _spawn_and_settle(sm, task="write the report")

        path = tmp_path / "sessions" / f"subagent_{lineage}.jsonl"
        assert path.is_file()
        path.write_text("{ truncated", encoding="utf-8")

        with pytest.raises(SubagentSendError, match="no longer saved"):
            await sm.send(lineage, "change the title")
        assert not sm._running_tasks

    async def test_send_without_a_session_manager_still_works(
        self, tmp_path: Path
    ) -> None:
        """``session_manager=None`` (costruzioni preesistenti) degrada, non rompe."""
        sm = _manager(tmp_path, session_manager=None)
        assert sm._history.enabled is False
        _completing_runner(sm)
        lineage = await _spawn_and_settle(sm, task="write the report")
        assert (await sm.send(lineage, "change the title")).mode == "restarted"
        await asyncio.gather(*list(sm._running_tasks.values()), return_exceptions=True)


# ---------------------------------------------------------------------------
# 4. concorrenza
# ---------------------------------------------------------------------------


class TestResumeCapacity:
    async def _finished_lineage(self, sm: SubagentManager) -> str:
        _completing_runner(sm)
        return await _spawn_and_settle(sm, task="write the report")

    async def test_resume_is_refused_when_the_pool_is_full(self, tmp_path: Path) -> None:
        from jenny.agent.subagent import SubagentConcurrencyLimitError

        sm = _manager(tmp_path, max_concurrent_subagents=2)
        lineage = await self._finished_lineage(sm)

        release = asyncio.Event()

        async def _blocking(spec: AgentRunSpec) -> AgentRunResult:
            await release.wait()
            return AgentRunResult(final_content="ok", messages=[], stop_reason="completed")

        sm.runner.run = _blocking  # type: ignore[method-assign]
        # Due job vivi su un pool da 2 -> pieno.
        await sm.spawn(task="a", session_key="unified:default", quick=True)
        await sm.spawn(task="b", session_key="unified:default", quick=True)
        await asyncio.sleep(0)

        with pytest.raises(SubagentConcurrencyLimitError):
            await sm.send(lineage, "change the title")

        release.set()
        await asyncio.gather(*list(sm._running_tasks.values()), return_exceptions=True)

    async def test_a_quick_resume_may_use_the_reserved_slot(self, tmp_path: Path) -> None:
        from jenny.agent.subagent import SubagentConcurrencyLimitError

        sm = _manager(tmp_path, max_concurrent_subagents=2)
        lineage = await self._finished_lineage(sm)

        release = asyncio.Event()
        seen: list[AgentRunSpec] = []

        async def _blocking(spec: AgentRunSpec) -> AgentRunResult:
            seen.append(spec)
            await release.wait()
            return AgentRunResult(final_content="ok", messages=[], stop_reason="completed")

        sm.runner.run = _blocking  # type: ignore[method-assign]
        # Un solo job vivo: lo slot rimasto e quello riservato ai quick.
        await sm.spawn(task="a", session_key="unified:default")
        await asyncio.sleep(0)

        with pytest.raises(SubagentConcurrencyLimitError) as normal:
            await sm.send(lineage, "change the title")
        assert normal.value.reserved is True

        assert (await sm.send(lineage, "change the title", quick=True)).mode == "resumed"
        await asyncio.sleep(0)
        assert len(seen) == 2

        release.set()
        await asyncio.gather(*list(sm._running_tasks.values()), return_exceptions=True)

    async def test_a_refused_resume_leaves_the_history_intact(
        self, tmp_path: Path
    ) -> None:
        from jenny.agent.subagent import SubagentConcurrencyLimitError

        sm = _manager(tmp_path, max_concurrent_subagents=1)
        lineage = await self._finished_lineage(sm)

        release = asyncio.Event()

        async def _blocking(spec: AgentRunSpec) -> AgentRunResult:
            await release.wait()
            return AgentRunResult(final_content="ok", messages=[], stop_reason="completed")

        sm.runner.run = _blocking  # type: ignore[method-assign]
        await sm.spawn(task="a", session_key="unified:default")
        await asyncio.sleep(0)

        with pytest.raises(SubagentConcurrencyLimitError):
            await sm.send(lineage, "change the title")
        assert sm._history.load(lineage) is not None

        release.set()
        await asyncio.gather(*list(sm._running_tasks.values()), return_exceptions=True)


# ---------------------------------------------------------------------------
# 5. igiene della RAM
# ---------------------------------------------------------------------------


class TestSessionCacheHygiene:
    async def test_a_subagent_run_leaves_nothing_in_the_session_cache(
        self, tmp_path: Path
    ) -> None:
        sessions = SessionManager(tmp_path)
        sm = _manager(tmp_path, session_manager=sessions, max_concurrent_subagents=8)
        _completing_runner(sm)
        for i in range(3):
            await _spawn_and_settle(sm, task=f"job {i}", label=f"job {i}")
        assert sessions._cache == {}

    async def test_history_store_shares_the_injected_session_manager(
        self, tmp_path: Path
    ) -> None:
        sessions = SessionManager(tmp_path)
        sm = _manager(tmp_path, session_manager=sessions)
        assert isinstance(sm._history, SubagentHistoryStore)
        assert sm._history._sessions is sessions

    async def test_loop_injects_its_own_session_manager(self, tmp_path: Path) -> None:
        from jenny.agent.loop import AgentLoop

        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"
        loop = AgentLoop(
            bus=MessageBus(), provider=provider, workspace=tmp_path, model="test-model"
        )
        assert loop.subagents._history._sessions is loop.sessions
