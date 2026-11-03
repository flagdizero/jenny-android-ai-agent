"""Test per jenny/runtime/location.py (posizione del dispositivo, solo Android).

Il bridge Chaquopy non esiste nei test desktop: si verificano la logica pura
(parsing del fix, formattazione della riga, età leggibile), la selezione
channel-scoped con TTL Telegram, il gating sul toggle e i percorsi async con un
bridge finto.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from jenny.config.tool_schemas import LocationConfig
from jenny.runtime import location


@pytest.fixture(autouse=True)
def _reset_state():
    """Ogni test parte da stato di modulo pulito."""
    location.reset_location_state()
    yield
    location.reset_location_state()


def _fix(place: str | None = "Milano, Piazza del Duomo", age_s: float = 0.0) -> location.LocationFix:
    return location.LocationFix(
        latitude=45.4642,
        longitude=9.1900,
        accuracy_m=12.0,
        time_ms=int((time.time() - age_s) * 1000),
        place=place,
        source="last-known",
    )


class _FakeBridge:
    def __init__(self, last: str | None = None, fresh: str | None = None, place: str | None = None):
        self._last = last
        self._fresh = fresh
        self._place = place
        self.geocode_calls = 0

    def getLastKnown(self) -> str | None:  # noqa: N802
        return self._last

    def getFresh(self, timeout_ms: int) -> str | None:  # noqa: N802
        return self._fresh

    def reverseGeocode(self, lat: float, lng: float) -> str | None:  # noqa: N802
        self.geocode_calls += 1
        return self._place


class TestParseFix:
    def test_parses_full_string(self):
        fix = location._parse_fix("45.4642;9.19;12.5;1700000000000;gps", source="x")
        assert fix is not None
        assert fix.latitude == pytest.approx(45.4642)
        assert fix.longitude == pytest.approx(9.19)
        assert fix.accuracy_m == pytest.approx(12.5)
        assert fix.time_ms == 1700000000000
        assert fix.source == "gps"

    def test_accuracy_optional(self):
        fix = location._parse_fix("45.0;9.0;;1700000000000;", source="last-known")
        assert fix is not None
        assert fix.accuracy_m is None
        assert fix.source == "last-known"  # provider vuoto → fallback al source

    def test_none_and_empty(self):
        assert location._parse_fix(None, source="x") is None
        assert location._parse_fix("", source="x") is None

    def test_malformed_is_none(self):
        assert location._parse_fix("nope", source="x") is None
        assert location._parse_fix("45.0;9.0", source="x") is None  # troppo corta
        assert location._parse_fix("a;b;c;d;e", source="x") is None  # non numerica


class TestFormatLine:
    def test_device_line_uses_place_and_age(self):
        line = location._format_line(_fix(age_s=0))
        assert line.startswith("Device location (just now):")
        assert "Milano, Piazza del Duomo" in line
        assert "(45.46420, 9.19000)" in line

    def test_shared_line_label(self):
        line = location._format_line(_fix(), shared=True)
        assert line.startswith("User location (shared via Telegram):")

    def test_falls_back_to_coords_without_place(self):
        line = location._format_line(_fix(place=None))
        assert "45.46420, 9.19000" in line


class TestHumanizeAge:
    def test_buckets(self):
        assert location._humanize_age(10) == "just now"
        assert location._humanize_age(300) == "~5 min ago"
        assert location._humanize_age(7200) == "~2 h ago"
        assert location._humanize_age(3 * 86400) == "~3 d ago"


class TestRuntimeLine:
    def test_none_when_disabled(self):
        cfg = LocationConfig(enable=False)
        location._CURRENT = _fix()
        assert location.location_runtime_line("webui", None, cfg) is None

    def test_none_when_no_fix(self, monkeypatch):
        monkeypatch.setattr(location, "get_android_context", lambda: None)
        assert location.location_runtime_line("webui", None, LocationConfig()) is None

    def test_uses_current_gps(self, monkeypatch):
        monkeypatch.setattr(location, "get_android_context", lambda: None)
        location._CURRENT = _fix()
        line = location.location_runtime_line("webui", None, LocationConfig())
        assert line is not None and "Device location" in line

    def test_telegram_override_within_ttl(self, monkeypatch):
        monkeypatch.setattr(location, "get_android_context", lambda: None)
        location._CURRENT = _fix(place="Roma")  # GPS dice Roma
        location.record_telegram_location("chat1", _fix(place="Napoli", age_s=10))
        line = location.location_runtime_line("telegram", "chat1", LocationConfig())
        assert "shared via Telegram" in line
        assert "Napoli" in line

    def test_telegram_override_expires(self, monkeypatch):
        monkeypatch.setattr(location, "get_android_context", lambda: None)
        location._CURRENT = _fix(place="Roma")
        location.record_telegram_location("chat1", _fix(place="Napoli", age_s=7200))
        cfg = LocationConfig(telegram_ttl_s=3600)
        line = location.location_runtime_line("telegram", "chat1", cfg)
        assert "shared via Telegram" not in line
        assert "Roma" in line  # ricaduto sul GPS
        assert "chat1" not in location._TELEGRAM  # scaduto → rimosso

    def test_telegram_override_only_for_telegram_channel(self, monkeypatch):
        monkeypatch.setattr(location, "get_android_context", lambda: None)
        location._CURRENT = _fix(place="Roma")
        location.record_telegram_location("chat1", _fix(place="Napoli", age_s=10))
        # Stesso chat_id ma canale webui → niente override Telegram.
        line = location.location_runtime_line("webui", "chat1", LocationConfig())
        assert "Roma" in line and "shared via Telegram" not in line


class TestGetLocation:
    async def test_disabled_returns_none(self):
        assert await location.get_location(LocationConfig(enable=False), precise=True) is None

    async def test_last_known_from_cache(self):
        location._CURRENT = _fix(place="Torino")
        fix = await location.get_location(LocationConfig(), precise=False)
        assert fix is not None and fix.place == "Torino"

    async def test_precise_fetches_fresh(self, monkeypatch):
        bridge = _FakeBridge(fresh="45.07;7.68;5.0;1700000000000;gps", place="Torino, Via Roma")
        monkeypatch.setattr(location, "get_android_context", lambda: object())

        async def fake_get_bridge(ctx: Any) -> Any:
            return bridge

        monkeypatch.setattr(location, "_get_bridge", fake_get_bridge)
        fix = await location.get_location(LocationConfig(), precise=True)
        assert fix is not None
        assert fix.latitude == pytest.approx(45.07)
        assert fix.place == "Torino, Via Roma"

    async def test_geocode_cache_avoids_recall(self, monkeypatch):
        bridge = _FakeBridge(place="Milano")

        async def fake_get_bridge(ctx: Any) -> Any:
            return bridge

        monkeypatch.setattr(location, "_get_bridge", fake_get_bridge)
        fix1 = await location._reverse_geocode(object(), 45.4642, 9.1900)
        fix2 = await location._reverse_geocode(object(), 45.4642, 9.1900)
        assert fix1 == fix2 == "Milano"
        assert bridge.geocode_calls == 1  # seconda volta dalla cache


class TestBuildTelegramFix:
    async def test_stamps_now_and_geocodes(self, monkeypatch):
        bridge = _FakeBridge(place="Bologna, Piazza Maggiore")
        monkeypatch.setattr(location, "get_android_context", lambda: object())

        async def fake_get_bridge(ctx: Any) -> Any:
            return bridge

        monkeypatch.setattr(location, "_get_bridge", fake_get_bridge)
        fix = await location.build_telegram_fix(LocationConfig(), 44.49, 11.34)
        assert fix.source == "telegram"
        assert fix.place == "Bologna, Piazza Maggiore"
        assert fix.age_seconds() < 5

    async def test_no_geocode_without_android(self, monkeypatch):
        monkeypatch.setattr(location, "get_android_context", lambda: None)
        fix = await location.build_telegram_fix(LocationConfig(), 44.49, 11.34)
        assert fix.place is None
        assert fix.latitude == pytest.approx(44.49)
