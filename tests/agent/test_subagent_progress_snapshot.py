"""Lo snapshot degli aiutanti anche fra una transizione e l'altra, col freno.

La bolla di lavoro della home dice *cosa* sta facendo un aiutante
(``last_tool``) e da quanto: lo snapshot che partiva solo sulle transizioni
(partito, fermo, finito) le arrivava vecchio di minuti. Ora parte anche a fine
iterazione, ma non piu' di uno ogni ``PROGRESS_SNAPSHOT_EVERY_S`` per task.
"""

from __future__ import annotations

from types import SimpleNamespace

from jenny.agent import subagent as subagent_module
from jenny.agent.subagent import PROGRESS_SNAPSHOT_EVERY_S, SubagentManager, _SubagentHook


def _manager(published: list) -> SimpleNamespace:
    fake = SimpleNamespace(_progress_published_at={})
    fake._publish_status_snapshot = lambda spec: published.append(spec)
    return fake


def test_the_progress_snapshot_is_throttled_per_task(monkeypatch) -> None:
    now = [100.0]
    monkeypatch.setattr(subagent_module.time, "monotonic", lambda: now[0])
    published: list = []
    fake = _manager(published)

    SubagentManager._publish_progress_snapshot(fake, "t1", "spec-1")
    SubagentManager._publish_progress_snapshot(fake, "t1", "spec-1")
    SubagentManager._publish_progress_snapshot(fake, "t2", "spec-2")
    assert published == ["spec-1", "spec-2"]

    now[0] += PROGRESS_SNAPSHOT_EVERY_S + 0.1
    SubagentManager._publish_progress_snapshot(fake, "t1", "spec-1")
    assert published == ["spec-1", "spec-2", "spec-1"]


async def test_the_hook_asks_for_a_snapshot_after_updating_the_status() -> None:
    seen: list = []
    status = SimpleNamespace(touch=lambda: None)

    def _on_iteration() -> None:
        # Lo status e' gia' aggiornato quando lo snapshot parte: e' lui che lo
        # legge.
        seen.append(status.iteration)

    hook = _SubagentHook("t1", status, on_iteration=_on_iteration)
    await hook.after_iteration(SimpleNamespace(
        iteration=4, tool_events=[{"name": "read_file"}], usage={}, error=None,
    ))

    assert seen == [4]


async def test_a_snapshot_that_raises_does_not_stop_the_run() -> None:
    status = SimpleNamespace(touch=lambda: None)

    def _boom() -> None:
        raise RuntimeError("queue on fire")

    hook = _SubagentHook("t1", status, on_iteration=_boom)
    await hook.after_iteration(SimpleNamespace(iteration=1, tool_events=[], usage={}, error=None))
