"""Il turno incrementale di Dream dà lo stesso esito da ``/dream`` e dal cron.

Le due strade avevano ognuna la propria copia del turno — prompt, snapshot,
tool, ``process_direct``, gate del cursore, batch trattenuto, contatori — e
questo sottosistema è nato proprio da due copie che divergevano una
divergenza alla volta. Qui ogni scenario si lancia da tutte e due le parti
sullo stesso ``MemoryStore`` finto e si confronta ciò che resta: il cursore e
i contatori del review scritti per ultimi. In più si fissa la frase che
``/dream`` dice all'utente, che è l'unica cosa che le due strade fanno
diversamente di proposito (il cron scrive nel log).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from jenny.agent.memory import DREAM_HISTORY_HEADER
from jenny.agent.tools.file_state import FileStates
from jenny.bus.events import InboundMessage
from jenny.command.builtin import cmd_dream
from jenny.command.router import CommandContext
from jenny.config.schema import Config
from jenny.runtime.cron_dispatch import CronDispatcher

_DREAM_JOB = SimpleNamespace(name="dream", id="job-dream")

_BATCH_WITH_FACTS = (
    "template di Dream"
    + DREAM_HISTORY_HEADER
    + "[2026-08-18 11:02] - [permanent] Preferisce le riunioni corte del mattino"
)


class _Memory:
    """``MemoryStore`` finto con i tre file veri: ``budget_report`` li misura."""

    def __init__(self, workspace: Path, scenario: dict[str, Any]) -> None:
        workspace.mkdir(parents=True, exist_ok=True)
        self.memory_file = workspace / "MEMORY.md"
        self.user_file = workspace / "USER.md"
        self.soul_file = workspace / "SOUL.md"
        self.memory_file.write_text("m", encoding="utf-8")
        self.user_file.write_text(scenario.get("user_text", "u"), encoding="utf-8")
        self.soul_file.write_text("s", encoding="utf-8")
        self.scenario = scenario
        self.cursor: int | None = None
        self._review = (3, 0)
        self._nothing_new = 0
        self._forced = 0
        self.review_writes: list[dict[str, Any]] = []
        self.file_states = scenario["file_states"]()

    def get_review_state(self) -> tuple[int, int]:
        return self._review

    def get_nothing_new_runs(self) -> int:
        return self._nothing_new

    def get_review_forced_at_stuck(self) -> int:
        return self._forced

    def set_review_state(self, *, runs_since_review, stuck_runs,
                         forced_at_stuck=None, nothing_new_runs=None) -> None:
        self._review = (runs_since_review, stuck_runs)
        if forced_at_stuck is not None:
            self._forced = forced_at_stuck
        if nothing_new_runs is not None:
            self._nothing_new = nothing_new_runs
        self.review_writes.append({
            "runs": runs_since_review, "stuck": stuck_runs,
            "forced": self._forced, "nothing_new": self._nothing_new,
        })

    def build_dream_prompt(self, **_kwargs):
        if not self.scenario.get("has_work", True):
            return None
        return (self.scenario.get("prompt", "prompt di consolidamento"), 42)

    def build_dream_tools(self, **_kwargs):
        return SimpleNamespace(file_states=self.file_states)

    def set_last_dream_cursor(self, cursor: int) -> None:
        self.cursor = cursor

    def get_last_dream_cursor(self) -> int:
        return 7

    def compact_history(self) -> None:
        pass


def _written(tmp: Path) -> FileStates:
    fs = FileStates()
    fs.record_write_attempt()
    fs.record_write(tmp / "written.md")
    return fs


def _blocked() -> FileStates:
    fs = FileStates()
    fs.record_write_attempt()
    return fs


def _refused(tmp: Path) -> FileStates:
    fs = FileStates()
    fs.record_write_attempt()
    fs.record_write_refused(tmp / "MEMORY.md", "- un fatto che non ci sta")
    return fs


def _scenarios(tmp: Path) -> dict[str, dict[str, Any]]:
    return {
        "no_input": {"has_work": False, "file_states": FileStates,
                     "says": "no conversation history"},
        "advanced": {"file_states": lambda: _written(tmp), "says": "Dream completed in"},
        "nothing_attempted": {"file_states": FileStates, "says": "Dream completed in"},
        "blocked": {"file_states": _blocked, "says": "wrote nothing"},
        "refused": {"file_states": lambda: _refused(tmp), "says": "wrote nothing"},
        "incomplete": {"file_states": lambda: _written(tmp), "stop": "error",
                       "says": "did not complete"},
        # Scrive, accorcia USER.md vicino al tetto, non fa atterrare il fatto.
        "held_batch": {"file_states": lambda: _written(tmp), "prompt": _BATCH_WITH_FACTS,
                       "user_text": "y" * 2990, "shrink_user_to": 2963,
                       "says": "consolidated nothing from its batch"},
        "crash": {"file_states": lambda: _written(tmp), "raise": True, "says": "Dream failed"},
    }


def _process_direct(memory: _Memory):
    async def _run(prompt: str, **_kwargs):
        sc = memory.scenario
        if sc.get("raise"):
            raise RuntimeError("provider giù")
        if "shrink_user_to" in sc:
            memory.user_file.write_text("y" * sc["shrink_user_to"], encoding="utf-8")
        return SimpleNamespace(
            content="done", metadata={"_stop_reason": sc.get("stop", "completed")}, usage={},
        )
    return _run


@pytest.fixture(autouse=True)
def _config_from_memory(monkeypatch: pytest.MonkeyPatch):
    """Le due strade rileggono i knob di Dream da disco: qui da una ``Config``."""
    monkeypatch.setattr("jenny.config.loader.load_config", lambda *a, **k: Config())


async def _drain(timeout: float = 30.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    current = asyncio.current_task()
    while True:
        pending = [t for t in asyncio.all_tasks() if t is not current and not t.done()]
        if not pending:
            return
        remaining = deadline - loop.time()
        assert remaining > 0, f"task ancora in volo dopo {timeout}s: {pending}"
        await asyncio.wait(pending, timeout=remaining)


async def _via_command(tmp: Path, scenario: dict[str, Any]) -> tuple[_Memory, list[str]]:
    memory = _Memory(tmp / "cmd", scenario)
    published: list[str] = []

    async def _publish(message):
        published.append(message.content)

    loop = SimpleNamespace(
        bus=SimpleNamespace(publish_outbound=_publish),
        context=SimpleNamespace(memory=memory, timezone=None),
        sessions=SimpleNamespace(sessions_dir=tmp / "cmd-sessions"),
        process_direct=_process_direct(memory),
        evict_pruned_sessions=lambda _keys: None,
    )
    msg = InboundMessage(channel="websocket", sender_id="u", chat_id="default", content="/dream")
    ack = await cmd_dream(CommandContext(msg=msg, session=None, key="k", raw="/dream", loop=loop))
    assert ack.content == "Dreaming..."
    await _drain()
    return memory, published


async def _via_cron(tmp: Path, scenario: dict[str, Any]) -> _Memory:
    memory = _Memory(tmp / "cron", scenario)
    agent = SimpleNamespace(
        context=SimpleNamespace(memory=memory),
        sessions=SimpleNamespace(sessions_dir=tmp / "cron-sessions"),
        process_direct=_process_direct(memory),
        evict_pruned_sessions=lambda _keys: None,
    )
    dispatcher = CronDispatcher(
        get_agent=lambda: agent, config=Config(), cron=MagicMock(),
        heartbeat_cfg=SimpleNamespace(),
    )
    await dispatcher.dispatch(_DREAM_JOB)
    return memory


@pytest.mark.parametrize(
    "name",
    ["no_input", "advanced", "nothing_attempted", "blocked", "refused",
     "incomplete", "held_batch", "crash"],
)
async def test_both_roads_leave_the_same_state(tmp_path: Path, name: str) -> None:
    scenario_cmd = _scenarios(tmp_path)[name]
    scenario_cron = _scenarios(tmp_path)[name]

    cmd_memory, published = await _via_command(tmp_path, scenario_cmd)
    cron_memory = await _via_cron(tmp_path, scenario_cron)

    assert cmd_memory.cursor == cron_memory.cursor, name
    assert cmd_memory.review_writes, f"{name}: /dream non ha chiuso il ciclo"
    assert cron_memory.review_writes, f"{name}: il cron non ha chiuso il ciclo"
    assert cmd_memory.review_writes[-1] == cron_memory.review_writes[-1], name
    # Ciò che dice all'utente: un messaggio solo, con la frase del suo esito.
    assert len(published) == 1, published
    assert scenario_cmd["says"] in published[0], published[0]


@pytest.mark.parametrize(
    ("name", "cursor"),
    [("no_input", None), ("advanced", 42), ("nothing_attempted", 42), ("blocked", None),
     ("refused", None), ("incomplete", None), ("held_batch", None), ("crash", None)],
)
async def test_the_cursor_moves_only_when_the_batch_landed(
    tmp_path: Path, name: str, cursor: int | None
) -> None:
    """L'esito assoluto, non solo la parità: due strade rotte allo stesso modo
    passerebbero il test di sopra."""
    memory = await _via_cron(tmp_path, _scenarios(tmp_path)[name])
    assert memory.cursor == cursor
    last = memory.review_writes[-1]
    assert last["runs"] == 4, "runs_since_review avanza in ogni caso"
    if name == "refused":
        assert last["stuck"] == 1, "un rifiuto di budget sale su stuck"
    if name in ("advanced", "nothing_attempted", "no_input", "crash"):
        assert last["stuck"] == 0
