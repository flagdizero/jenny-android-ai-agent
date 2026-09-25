"""Il dispatch di cron legge l'ora dall'orologio condiviso.

``utils/clock.now_ms`` era nato per togliere quattro copie di
``int(time.time() * 1000)`` (1f38c06); ``CronDispatcher`` ne teneva ancora una
sua, come default del parametro ``now_ms``. Visto dalla revisione finale della
pulizia (25/09).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from jenny.config.schema import Config
from jenny.runtime.cron_dispatch import CronDispatcher
from jenny.utils import clock


def _dispatcher(**kwargs) -> CronDispatcher:
    return CronDispatcher(
        get_agent=lambda: None, config=Config(), cron=MagicMock(),
        heartbeat_cfg=SimpleNamespace(), **kwargs,
    )


def test_the_default_clock_is_the_shared_one() -> None:
    assert _dispatcher()._now_ms is clock.now_ms


def test_an_injected_clock_still_wins() -> None:
    fake = lambda: 42  # noqa: E731
    assert _dispatcher(now_ms=fake)._now_ms is fake
