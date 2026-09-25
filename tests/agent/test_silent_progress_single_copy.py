"""Un solo ``on_progress`` muto per i run interni, e un esito Dream senza doppioni.

Basse della revisione profonda: ``_silent`` stava in quattro copie (Dream, il suo
review pass, il giardiniere, i job di sistema del cron); ``DreamTurnResult``
portava ``resp``, che nessuno leggeva, e ``advanced``, da tenere allineato a mano
a ``outcome`` in ogni ``return``.
"""

from __future__ import annotations

import inspect

import pytest

from jenny.agent import dream_cycle, dream_review, gardener
from jenny.agent.dream_cycle import DreamOutcome, DreamTurnResult
from jenny.runtime import cron_dispatch
from jenny.session.turn_visibility import silent_progress


@pytest.mark.parametrize("modulo", [dream_cycle, dream_review, gardener, cron_dispatch])
def test_internal_runs_share_one_silent_progress(modulo) -> None:
    assert "async def _silent" not in inspect.getsource(modulo)
    assert modulo.silent_progress is silent_progress


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
