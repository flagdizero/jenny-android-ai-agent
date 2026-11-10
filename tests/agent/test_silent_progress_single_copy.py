"""Un solo ``on_progress`` muto per i run interni, e un esito Dream senza doppioni.

Due doppioni: ``_silent`` stava in quattro copie (Dream, il suo
review pass, il giardiniere, i job di sistema del cron); ``DreamTurnResult``
portava ``resp``, che nessuno leggeva, e ``advanced``, da tenere allineato a mano
a ``outcome`` in ogni ``return``.
"""

from __future__ import annotations

import inspect

import pytest

from jenny.agent import dream_cycle, dream_review, gardener, internal_run
from jenny.agent.dream_cycle import DreamOutcome, DreamTurnResult
from jenny.runtime import cron_dispatch
from jenny.session.turn_visibility import silent_progress


@pytest.mark.parametrize("module", [dream_cycle, dream_review, gardener, cron_dispatch])
def test_internal_runs_share_one_silent_progress(module) -> None:
    """Le tre passate la prendono dal loro turno comune (``run_internal_turn``),
    i job di sistema del cron direttamente: in ogni caso la stessa, mai una copia."""
    assert "async def _silent" not in inspect.getsource(module)
    if module is cron_dispatch:
        assert module.silent_progress is silent_progress
    else:
        assert module.run_internal_turn is internal_run.run_internal_turn


async def test_the_internal_turn_is_silent_unless_someone_watches() -> None:
    seen: dict[str, object] = {}

    class _Agent:
        async def process_direct(self, prompt: str, **kwargs: object) -> str:
            seen.update(kwargs, prompt=prompt)
            return "ok"

    assert await internal_run.run_internal_turn(_Agent(), "p", session_key="k", tools="t") == "ok"
    assert seen["on_progress"] is silent_progress and seen["on_retry_wait"] is None
    assert seen["ephemeral"] is True and seen["tools"] == "t" and seen["session_key"] == "k"

    class _Reporter:
        async def on_progress(self, *_a: object, **_k: object) -> None: ...
        async def on_retry_wait(self, *_a: object, **_k: object) -> None: ...

    reporter = _Reporter()
    await internal_run.run_internal_turn(
        _Agent(), "p", session_key="k", tools="t", reporter=reporter
    )
    assert seen["on_progress"] == reporter.on_progress
    assert seen["on_retry_wait"] == reporter.on_retry_wait


async def test_silent_progress_takes_anything_and_says_nothing() -> None:
    assert await silent_progress("testo", tool_hint=True, reasoning=None) is None


@pytest.mark.parametrize(
    ("outcome", "advanced"),
    [
        (DreamOutcome.NO_INPUT, None),
        (DreamOutcome.ADVANCED, True),
        (DreamOutcome.HELD_BATCH, False),
        (DreamOutcome.BLOCKED, False),
        (DreamOutcome.INCOMPLETE, False),
    ],
)
def test_advanced_follows_from_the_outcome(outcome, advanced) -> None:
    assert DreamTurnResult(outcome, refused=0).advanced is advanced


def test_the_turn_result_carries_no_dead_response() -> None:
    assert "resp" not in {f.name for f in DreamTurnResult.__dataclass_fields__.values()}
