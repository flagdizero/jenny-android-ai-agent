"""Tests per lineage/attempt, rilancio e ripudio dei tentativi superati."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from jenny.agent.runner import AgentRunResult
from jenny.agent.subagent import (
    MAX_AUTO_ATTEMPTS,
    SubagentManager,
    SubagentRestartError,
)
from jenny.bus.queue import MessageBus
from jenny.providers.base import LLMProvider

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
        # Watchdog spento: lo stallo ha i suoi test, qui non deve interferire.
        stall_threshold_s=0.0,
    )
    defaults.update(kw)
    return SubagentManager(**defaults)


def _completing_runner(sm: SubagentManager, content: str = "done") -> list:
    """Runner che completa subito, registrando le AgentRunSpec ricevute."""
    seen: list = []

    async def _run(spec):
        seen.append(spec)
        return AgentRunResult(final_content=content, messages=[], stop_reason="completed")

    sm.runner.run = _run
    return seen


def _blocking_runner(sm: SubagentManager, block: asyncio.Event) -> None:
    async def _run(spec):
        await block.wait()
        return AgentRunResult(final_content="done", messages=[], stop_reason="completed")

    sm.runner.run = _run


def _capture_bus(sm: SubagentManager) -> list:
    published: list = []
    sm.bus.publish_inbound = AsyncMock(side_effect=lambda msg: published.append(msg))
    return published


async def _settle(sm: SubagentManager) -> None:
    tasks = [t for t in sm._running_tasks.values() if not t.done()]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    await asyncio.sleep(0)
    await sm._cancel_stall_watchdog()


def _only_lineage(sm: SubagentManager) -> str:
    assert len(sm._lineages) == 1
    return next(iter(sm._lineages))


# ---------------------------------------------------------------------------
# lineage / attempt
# ---------------------------------------------------------------------------


class TestLineageIdentity:
    @pytest.mark.asyncio
    async def test_spawn_starts_lineage_at_attempt_one(self, tmp_path: Path):
        sm = _manager(tmp_path)
        block = asyncio.Event()
        _blocking_runner(sm, block)

        await sm.spawn("research X", session_key="s1")
        status = next(iter(sm._task_statuses.values()))
        assert status.attempt == 1
        assert status.lineage_id
        assert status.lineage_id != status.task_id
        assert status.state == "running"
        assert status.agent_type == "operator"

        block.set()
        await _settle(sm)

    @pytest.mark.asyncio
    async def test_restart_keeps_lineage_and_increments_attempt(self, tmp_path: Path):
        sm = _manager(tmp_path)
        block = asyncio.Event()
        _blocking_runner(sm, block)

        await sm.spawn("research X", session_key="s1")
        first = next(iter(sm._task_statuses.values()))
        lineage = first.lineage_id

        await sm.restart(first.task_id, manual=True, grace_s=0.05)

        assert first.task_id not in sm._task_statuses
        second = next(iter(sm._task_statuses.values()))
        assert second.lineage_id == lineage
        assert second.attempt == 2
        assert second.task_id != first.task_id

        block.set()
        await _settle(sm)

    @pytest.mark.asyncio
    async def test_stop_and_undo_from_an_old_attempt_id_stops_the_live_one(self, tmp_path: Path):
        """Una bolla rimasta aperta dopo un rilancio porta l'id del tentativo
        vecchio: «Ferma e annulla» da lì deve fermare quello vivo del lineage,
        non rifiutare «sta ancora lavorando»."""
        sm = _manager(tmp_path)
        block = asyncio.Event()
        _blocking_runner(sm, block)

        await sm.spawn("research X", session_key="s1")
        first_id = next(iter(sm._task_statuses.values())).task_id
        await sm.restart(first_id, manual=True, grace_s=0.05)
        second_id = next(iter(sm._task_statuses.values())).task_id
        assert second_id != first_id and sm._live_task_for(second_id) == second_id

        report = await sm.cancel_and_undo(first_id, by="user", grace_s=0.05)

        assert report.restored == []
        assert sm._live_task_for(second_id) is None
        block.set()
        await _settle(sm)

    @pytest.mark.asyncio
    async def test_announce_metadata_carries_lineage_and_attempt(self, tmp_path: Path):
        sm = _manager(tmp_path)
        _completing_runner(sm)
        published = _capture_bus(sm)

        await sm.spawn("research X", session_key="s1")
        await _settle(sm)
        lineage = _only_lineage(sm)

        assert len(published) == 1
        meta = published[0].metadata
        assert meta["subagent_lineage_id"] == lineage
        assert meta["subagent_attempt"] == 1
        assert meta["subagent_task_id"]

        await sm.restart(lineage, manual=True)
        await _settle(sm)

        assert len(published) == 2
        meta2 = published[1].metadata
        assert meta2["subagent_lineage_id"] == lineage
        assert meta2["subagent_attempt"] == 2
        assert meta2["subagent_task_id"] != meta["subagent_task_id"]


# ---------------------------------------------------------------------------
# ripudio del tentativo superato
# ---------------------------------------------------------------------------


class TestSupersededAttempt:
    @pytest.mark.asyncio
    async def test_late_announce_of_superseded_attempt_is_suppressed(self, tmp_path: Path):
        """Un tentativo che non muore entro il grace è ripudiato: il suo
        risultato tardivo non deve iniettare un turno stale."""
        sm = _manager(tmp_path)
        published = _capture_bus(sm)
        started = asyncio.Event()
        release = asyncio.Event()

        async def _stubborn(spec):
            started.set()
            while True:
                try:
                    await release.wait()
                    break
                except asyncio.CancelledError:
                    # Simula il subagent bloccato in un thread non
                    # interrompibile: ignora la cancellazione e finisce tardi.
                    continue
            return AgentRunResult(final_content="late", messages=[], stop_reason="completed")

        sm.runner.run = _stubborn

        await sm.spawn("research X", session_key="s1")
        first_id = next(iter(sm._task_statuses))
        await asyncio.wait_for(started.wait(), timeout=1.0)

        await sm.restart(first_id, manual=True, grace_s=0.05)
        assert first_id in sm._repudiated_task_ids

        release.set()
        await _settle(sm)

        announced = [m.metadata["subagent_task_id"] for m in published]
        assert first_id not in announced
        # Il nuovo tentativo, invece, annuncia normalmente.
        assert len(announced) == 1

    @pytest.mark.asyncio
    async def test_promptly_cancelled_attempt_leaves_no_repudiation(self, tmp_path: Path):
        sm = _manager(tmp_path)
        block = asyncio.Event()
        _blocking_runner(sm, block)

        await sm.spawn("research X", session_key="s1")
        first_id = next(iter(sm._task_statuses))
        await sm.restart(first_id, manual=True, grace_s=0.5)

        # Il task è morto: nessun announce da sopprimere, nessun residuo.
        assert first_id not in sm._repudiated_task_ids
        assert first_id not in sm._running_tasks

        block.set()
        await _settle(sm)

    @pytest.mark.asyncio
    async def test_cancelled_attempt_is_retained_as_cancelled(self, tmp_path: Path):
        sm = _manager(tmp_path)
        block = asyncio.Event()
        _blocking_runner(sm, block)

        await sm.spawn("research X", session_key="s1")
        first_id = next(iter(sm._task_statuses))
        await sm.restart(first_id, manual=True, grace_s=0.5)

        records = {r.task_id: r for r in sm.list_records("s1")}
        assert records[first_id].state == "cancelled"
        assert records[first_id].attempt == 1

        block.set()
        await _settle(sm)


# ---------------------------------------------------------------------------
# tetto dei tentativi
# ---------------------------------------------------------------------------


class TestAttemptCap:
    @pytest.mark.asyncio
    async def test_automatic_restarts_are_capped(self, tmp_path: Path):
        sm = _manager(tmp_path)
        _completing_runner(sm)

        await sm.spawn("research X", session_key="s1")
        await _settle(sm)
        lineage = _only_lineage(sm)

        for expected in range(2, MAX_AUTO_ATTEMPTS + 1):
            await sm.restart(lineage)
            assert sm._lineages[lineage].attempt == expected
            await _settle(sm)

        with pytest.raises(SubagentRestartError) as exc:
            await sm.restart(lineage)
        assert "automatic restart refused" in str(exc.value)
        assert sm._lineages[lineage].attempt == MAX_AUTO_ATTEMPTS

    @pytest.mark.asyncio
    async def test_manual_restart_is_never_capped(self, tmp_path: Path):
        sm = _manager(tmp_path)
        _completing_runner(sm)

        await sm.spawn("research X", session_key="s1")
        await _settle(sm)
        lineage = _only_lineage(sm)

        for _ in range(MAX_AUTO_ATTEMPTS - 1):
            await sm.restart(lineage)
            await _settle(sm)

        # Il tetto automatico è raggiunto...
        with pytest.raises(SubagentRestartError):
            await sm.restart(lineage)
        # ...ma a un umano che premo "Relaunch" non si risponde no.
        await sm.restart(lineage, manual=True)
        assert sm._lineages[lineage].attempt == MAX_AUTO_ATTEMPTS + 1
        await _settle(sm)
        await sm.restart(lineage, manual=True)
        assert sm._lineages[lineage].attempt == MAX_AUTO_ATTEMPTS + 2
        await _settle(sm)


# ---------------------------------------------------------------------------
# risoluzione del target e replay della spec
# ---------------------------------------------------------------------------


class TestRestartResolution:
    @pytest.mark.asyncio
    async def test_unknown_target_raises(self, tmp_path: Path):
        sm = _manager(tmp_path)
        with pytest.raises(SubagentRestartError):
            await sm.restart("nope", manual=True)
        with pytest.raises(SubagentRestartError):
            await sm.restart("", manual=True)

    @pytest.mark.asyncio
    async def test_old_task_id_does_not_rewind_the_attempt_counter(self, tmp_path: Path):
        sm = _manager(tmp_path)
        _completing_runner(sm)

        await sm.spawn("research X", session_key="s1")
        await _settle(sm)
        lineage = _only_lineage(sm)
        first_task_id = sm.list_records("s1")[0].task_id

        await sm.restart(lineage, manual=True)
        await _settle(sm)
        assert sm._lineages[lineage].attempt == 2

        # Rilancio partendo dall'id del *primo* tentativo: il lineage e lo
        # stesso, la numerazione va avanti, non torna a 2.
        await sm.restart(first_task_id, manual=True)
        await _settle(sm)
        assert sm._lineages[lineage].attempt == 3
        assert sorted(r.attempt for r in sm.list_records("s1")) == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_extra_instructions_are_appended_to_the_replayed_task(self, tmp_path: Path):
        sm = _manager(tmp_path)
        seen = _completing_runner(sm)

        await sm.spawn("count the files", label="counter", session_key="s1", temperature=0.4)
        await _settle(sm)
        lineage = _only_lineage(sm)

        await sm.restart(lineage, extra_instructions="use find_files, not python", manual=True)
        await _settle(sm)

        assert len(seen) == 2
        replayed = seen[1].initial_messages[-1]["content"]
        assert "count the files" in replayed
        assert "use find_files, not python" in replayed
        # Il resto della spec è rigiocato identico.
        assert seen[1].temperature == 0.4

    @pytest.mark.asyncio
    async def test_restart_replays_spec_from_disk_after_process_restart(self, tmp_path: Path):
        """Su Android il gateway viene ucciso: il record su disco è l'unica
        cosa che rende rilanciabile un lavoro dopo il riavvio."""
        first = _manager(tmp_path)
        _completing_runner(first)
        await first.spawn("research X", label="researcher", session_key="s1")
        await _settle(first)
        lineage = _only_lineage(first)
        original_task_id = first.list_records("s1")[0].task_id

        # Nuovo manager sullo stesso workspace: memoria vuota, disco pieno.
        revived = _manager(tmp_path)
        seen = _completing_runner(revived)
        assert revived._lineages == {}

        await revived.restart(original_task_id, manual=True)
        await _settle(revived)

        assert len(seen) == 1
        assert "research X" in seen[0].initial_messages[-1]["content"]
        assert revived._lineages[lineage].attempt == 2
        attempts = sorted(r.attempt for r in revived.list_records("s1"))
        assert attempts == [1, 2]
