"""Test del checkpoint snapshot pre-Dream nel ``CronDispatcher``.

Dream può riscrivere MEMORY/SOUL/USER e le skill: il contratto è che il
checkpoint venga scattato PRIMA della consolidazione, solo quando c'è
davvero lavoro da fare, e in modalità fail-open (un checkpoint guasto non
blocca il Dream).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from jenny.config.schema import Config
from jenny.runtime.cron_dispatch import CronDispatcher

_DREAM_JOB = SimpleNamespace(
    name="dream", id="dream", payload=SimpleNamespace(kind="system_event")
)


class _FakeMemory:
    # ``budget_report`` misura questi tre file: non esistono, e va bene —
    # ``count_chars`` legge 0 su file assente, e con i budget a 0 del default
    # nessuno puo' risultare oltre soglia.
    memory_file = Path("no-such-MEMORY.md")
    user_file = Path("no-such-USER.md")
    soul_file = Path("no-such-SOUL.md")

    def get_review_state(self) -> tuple[int, int]:
        return (0, 0)

    _nothing_new = 0

    def get_nothing_new_runs(self) -> int:
        return self._nothing_new

    def get_review_forced_at_stuck(self) -> int:
        return 0

    def set_review_state(self, **_kwargs) -> None:
        pass

    def __init__(self, *, has_work: bool = True) -> None:
        self._has_work = has_work
        self.cursor: int | None = None

    def build_dream_prompt(self, **_kwargs):
        return ("prompt di consolidamento", 42) if self._has_work else None

    def build_dream_tools(self, **_kwargs):
        return []

    def set_last_dream_cursor(self, cursor: int) -> None:
        self.cursor = cursor

    def get_last_dream_cursor(self) -> int:
        return 0

    def compact_history(self) -> None:
        pass


class _FakeAgent:
    def __init__(self, events: list[str], sessions_dir: Path, *, has_work: bool = True) -> None:
        self._events = events
        self.context = SimpleNamespace(memory=_FakeMemory(has_work=has_work))
        self.sessions = SimpleNamespace(sessions_dir=sessions_dir)

    async def process_direct(self, prompt: str, **_kwargs):
        self._events.append("process_direct")
        return None

    def evict_pruned_sessions(self, keys) -> None:
        pass


def _make_dispatcher(agent, snapshot_cb) -> CronDispatcher:
    return CronDispatcher(
        get_agent=lambda: agent,
        config=Config(),
        cron=MagicMock(),
        snapshot_before_dream=snapshot_cb,
    )


async def test_checkpoint_runs_before_dream(tmp_path: Path) -> None:
    events: list[str] = []
    agent = _FakeAgent(events, tmp_path)

    async def checkpoint() -> None:
        events.append("snapshot")

    await _make_dispatcher(agent, checkpoint).dispatch(_DREAM_JOB)
    assert events == ["snapshot", "process_direct"]


async def test_checkpoint_failure_does_not_block_dream(tmp_path: Path) -> None:
    """Fail-open: un checkpoint guasto logga ma la consolidazione procede."""
    events: list[str] = []
    agent = _FakeAgent(events, tmp_path)

    async def broken_checkpoint() -> None:
        events.append("snapshot")
        raise RuntimeError("checkpoint guasto")

    await _make_dispatcher(agent, broken_checkpoint).dispatch(_DREAM_JOB)
    assert events == ["snapshot", "process_direct"]


async def test_dream_runs_without_checkpoint_callback(tmp_path: Path) -> None:
    events: list[str] = []
    agent = _FakeAgent(events, tmp_path)
    await _make_dispatcher(agent, None).dispatch(_DREAM_JOB)
    assert events == ["process_direct"]


async def test_no_checkpoint_when_nothing_to_consolidate(tmp_path: Path) -> None:
    """Se Dream non ha lavoro, non si crea nemmeno lo snapshot di checkpoint."""
    events: list[str] = []
    agent = _FakeAgent(events, tmp_path, has_work=False)

    async def checkpoint() -> None:
        events.append("snapshot")

    await _make_dispatcher(agent, checkpoint).dispatch(_DREAM_JOB)
    assert events == []
