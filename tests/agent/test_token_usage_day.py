"""Il giorno dei conti dei token è quello dell'utente, e un fuso sbagliato non rompe."""

from __future__ import annotations

from datetime import datetime, timezone

from jenny.agent.token_usage import _local_day


def test_an_unknown_zone_does_not_raise() -> None:
    # ``ZoneInfo("../x")`` solleva ValueError, non ZoneInfoNotFoundError.
    assert _local_day(datetime(2026, 11, 2, 12, tzinfo=timezone.utc), timezone_name="../x")


def test_without_a_zone_the_day_is_the_phone_s() -> None:
    late = datetime(2026, 11, 2, 23, 30, tzinfo=timezone.utc)
    assert _local_day(late, timezone_name="") == late.astimezone().date().isoformat()


def test_a_named_zone_still_wins() -> None:
    late = datetime(2026, 11, 2, 23, 30, tzinfo=timezone.utc)
    assert _local_day(late, timezone_name="Europe/Rome") == "2026-11-03"
