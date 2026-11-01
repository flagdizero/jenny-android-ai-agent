"""I controlli di un ``HEARTBEAT.md`` della 2.0.0 non spariscono in silenzio.

L'heartbeat è stato tolto il 31/10/2026 senza migrazione: chi aggiorna ha il
file, e nessuno lo legge più. Al primo job con un agente un turno silenzioso
gli dice quali controlli non girano più, e il file si ritira (``.retired``).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from jenny.cron.retired_heartbeat import (
    HEARTBEAT_FILE,
    RETIRED_SUFFIX,
    active_section,
    announce_retired_heartbeat,
)
from jenny.cron.types import CronJob, CronPayload
from jenny.runtime import cron_dispatch
from jenny.runtime.cron_dispatch import CronDispatcher
from jenny.session.turn_visibility import TurnVisibility

# Il template che la 2.0.0 scriveva al primo avvio: nessun controllo.
_EMPTY_TEMPLATE = """# Heartbeat Tasks

<!--
This file is checked periodically by your Jenny agent.
-->

## Active Tasks

<!-- Add your periodic tasks below this line -->
"""

# La forma di un file vero: un'intestazione ``###`` dà il soggetto alle righe.
_WITH_CHECKS = _EMPTY_TEMPLATE + """
### Rain alert
- Every cycle, read the rain probability for every city.
- Warn the user only above 70%.

- Tell me when the backup server stops answering.

## Archive
- an old check that was already off
"""


class _Agent:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._fail = fail

    async def process_direct(self, prompt: str, **kwargs) -> None:
        self.calls.append((prompt, kwargs))
        if self._fail:
            raise RuntimeError("provider down")


def test_the_empty_template_has_no_checks() -> None:
    assert active_section(_EMPTY_TEMPLATE) == ""


def test_the_section_keeps_its_headings_and_drops_the_rest() -> None:
    section = active_section(_WITH_CHECKS)

    assert section.startswith("### Rain alert")
    assert "backup server" in section
    assert "Add your periodic tasks" not in section
    assert "Archive" not in section and "already off" not in section


async def test_the_checks_are_announced_once_in_a_silent_turn(tmp_path: Path) -> None:
    (tmp_path / HEARTBEAT_FILE).write_text(_WITH_CHECKS, encoding="utf-8")
    agent = _Agent()

    assert await announce_retired_heartbeat(agent, tmp_path) is True

    [(prompt, kwargs)] = agent.calls
    assert "Rain alert" in prompt and "backup server" in prompt
    assert "`message` tool" in prompt
    assert kwargs["visibility"] is TurnVisibility.SILENT
    assert (kwargs["channel"], kwargs["chat_id"]) == ("websocket", "default")
    assert not (tmp_path / HEARTBEAT_FILE).exists()
    assert (tmp_path / (HEARTBEAT_FILE + RETIRED_SUFFIX)).read_text(encoding="utf-8") == _WITH_CHECKS

    assert await announce_retired_heartbeat(agent, tmp_path) is False
    assert len(agent.calls) == 1


async def test_the_empty_template_is_retired_without_a_turn(tmp_path: Path) -> None:
    (tmp_path / HEARTBEAT_FILE).write_text(_EMPTY_TEMPLATE, encoding="utf-8")
    agent = _Agent()

    assert await announce_retired_heartbeat(agent, tmp_path) is False

    assert agent.calls == []
    assert (tmp_path / (HEARTBEAT_FILE + RETIRED_SUFFIX)).exists()


async def test_a_failed_turn_leaves_the_file_for_the_next_start(tmp_path: Path) -> None:
    (tmp_path / HEARTBEAT_FILE).write_text(_WITH_CHECKS, encoding="utf-8")

    with pytest.raises(RuntimeError):
        await announce_retired_heartbeat(_Agent(fail=True), tmp_path)

    assert (tmp_path / HEARTBEAT_FILE).exists()
    assert not (tmp_path / (HEARTBEAT_FILE + RETIRED_SUFFIX)).exists()


def _dispatcher(tmp_path: Path, agent: object, monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []

    async def bound(*_a, **_k):
        calls.append("bound")

    monkeypatch.setattr(cron_dispatch, "run_bound_cron_job", bound)
    config = MagicMock()
    config.workspace_path = tmp_path
    return CronDispatcher(get_agent=lambda: agent, config=config, cron=MagicMock()), calls


def _reminder() -> CronJob:
    return CronJob(
        id="a1b2c3d4",
        name="umbrella",
        payload=CronPayload(
            kind="agent_turn",
            message="remind me of the umbrella",
            session_key="websocket:default",
            origin_channel="websocket",
            origin_chat_id="default",
        ),
    )


async def test_the_first_job_announces_and_still_runs(tmp_path, monkeypatch) -> None:
    (tmp_path / HEARTBEAT_FILE).write_text(_WITH_CHECKS, encoding="utf-8")
    agent = _Agent()
    dispatcher, calls = _dispatcher(tmp_path, agent, monkeypatch)

    await dispatcher._dispatch(_reminder())
    await dispatcher._dispatch(_reminder())

    assert len(agent.calls) == 1
    assert calls == ["bound", "bound"]


async def test_a_failed_notice_does_not_stop_the_job(tmp_path, monkeypatch) -> None:
    (tmp_path / HEARTBEAT_FILE).write_text(_WITH_CHECKS, encoding="utf-8")
    dispatcher, calls = _dispatcher(tmp_path, _Agent(fail=True), monkeypatch)

    await dispatcher._dispatch(_reminder())

    assert calls == ["bound"]
    assert (tmp_path / HEARTBEAT_FILE).exists()


async def test_without_an_agent_nothing_is_announced(tmp_path, monkeypatch) -> None:
    (tmp_path / HEARTBEAT_FILE).write_text(_WITH_CHECKS, encoding="utf-8")
    dispatcher, _calls = _dispatcher(tmp_path, None, monkeypatch)

    with pytest.raises(cron_dispatch.CronJobSkippedError):
        await dispatcher._dispatch(_reminder())

    assert dispatcher._heartbeat_retirement_checked is False
    assert (tmp_path / HEARTBEAT_FILE).exists()


async def test_a_config_without_a_workspace_path_skips_the_notice(monkeypatch) -> None:
    agent = _Agent()
    dispatcher, calls = _dispatcher(Path("unused"), agent, monkeypatch)
    dispatcher._config = SimpleNamespace()

    await dispatcher._dispatch(_reminder())

    assert agent.calls == []
    assert calls == ["bound"]
