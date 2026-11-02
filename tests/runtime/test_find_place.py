"""Un posto per nome (``find_place``) e il link pronto per la chat (``chatLink``).

Il patto che si misura: le coordinate che finiscono in chat vengono sempre da
uno strumento, nel formato che la scheda della WebUI riconosce
(``shared/place-card.js``), e mai dal modello. Il ``Geocoder`` nativo non c'è
fuori dal telefono: qui è un ponte finto.
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from typing import Any

import pytest

from jenny.agent.tools import location as tools
from jenny.config.tool_schemas import LocationConfig
from jenny.runtime import location


@pytest.fixture(autouse=True)
def _reset_state():
    location.reset_location_state()
    yield
    location.reset_location_state()


class _Geocoder:
    """Il ponte finto: risponde nel riquadro solo se ``boxed`` ha qualcosa."""

    def __init__(self, boxed: list[dict] | None = None, anywhere: list[dict] | None = None):
        self.boxed = boxed
        self.anywhere = anywhere
        self.calls: list[tuple] = []

    def findPlaces(self, query, max_results, south, west, north, east):  # noqa: N802
        self.calls.append((query, max_results, south, west, north, east))
        box = (south, west, north, east) != (0.0, 0.0, 0.0, 0.0)
        found = self.boxed if box else self.anywhere
        return json.dumps(found) if found is not None else None


def _on_phone(monkeypatch, bridge: Any) -> None:
    monkeypatch.setattr(location, "get_android_context", lambda: object())

    async def fake_get_bridge(ctx: Any) -> Any:
        return bridge

    monkeypatch.setattr(location, "_get_bridge", fake_get_bridge)


def _here() -> location.LocationFix:
    return location.LocationFix(
        latitude=44.4924, longitude=11.3472, accuracy_m=12.0,
        time_ms=int(time.time() * 1000), place="Piazza Santo Stefano, Bologna, Italia",
    )


BRUNA = {"name": "", "address": "Via del Pratello 48, 40122 Bologna", "lat": 44.4957, "lng": 11.333}


class TestChatLink:
    def test_the_format_the_card_reads(self):
        link = tools.chat_link("Trattoria da Bruna", "Via del Pratello 48", 44.4957, 11.333)
        assert link == (
            "[Trattoria da Bruna — Via del Pratello 48]"
            "(https://www.google.com/maps/search/?api=1&query=44.495700%2C11.333000)"
        )

    def test_brackets_in_a_name_do_not_break_the_link(self):
        link = tools.chat_link("Bar [Centrale]", "", 45.0, 9.0)
        assert link.startswith("[Bar (Centrale)](")

    def test_no_name_falls_back_to_the_numbers(self):
        assert tools.chat_link("", "", 45.0, 9.0).startswith("[45.000000, 9.000000](")


class TestGetLocationLink:
    async def test_the_fix_comes_with_its_link(self, monkeypatch):
        location._CURRENT = _here()
        out = json.loads(await tools.GetLocationTool(LocationConfig()).execute())
        assert out["chatLink"] == (
            "[Piazza Santo Stefano — Bologna, Italia]"
            "(https://www.google.com/maps/search/?api=1&query=44.492400%2C11.347200)"
        )


class TestFindPlaces:
    async def test_near_first_then_anywhere(self, monkeypatch):
        bridge = _Geocoder(boxed=[], anywhere=[BRUNA])
        _on_phone(monkeypatch, bridge)
        found = await location.find_places("Trattoria da Bruna, Bologna", near=_here())
        assert [m.address for m in found] == [BRUNA["address"]]
        assert len(bridge.calls) == 2
        south, west, north, east = bridge.calls[0][2:]
        assert south < 44.4924 < north and west < 11.3472 < east

    async def test_without_a_fix_searches_everywhere(self, monkeypatch):
        bridge = _Geocoder(anywhere=[BRUNA])
        _on_phone(monkeypatch, bridge)
        await location.find_places("Trattoria da Bruna", near=None)
        assert [c[2:] for c in bridge.calls] == [(0.0, 0.0, 0.0, 0.0)]

    async def test_garbage_is_no_place(self, monkeypatch):
        bridge = _Geocoder(anywhere=[{"lat": 200, "lng": 9}, {"name": "x"}, "nope"])
        _on_phone(monkeypatch, bridge)
        assert await location.find_places("x y", near=None) == []

    async def test_off_the_phone_nothing(self, monkeypatch):
        monkeypatch.setattr(location, "get_android_context", lambda: None)
        assert await location.find_places("Bologna", near=None) == []


class TestFindPlaceTool:
    async def test_matches_with_distance_and_link(self, monkeypatch):
        _on_phone(monkeypatch, _Geocoder(boxed=[BRUNA]))
        location._CURRENT = _here()
        tool = tools.FindPlaceTool(LocationConfig())
        out = json.loads(await tool.execute(query="Trattoria da Bruna, Via del Pratello 48, Bologna", label="Trattoria da Bruna"))
        (place,) = out["places"]
        assert place["name"] == "Trattoria da Bruna"  # il Geocoder dava solo l'indirizzo
        assert 1000 < place["distanceMeters"] < 1400
        assert place["chatLink"].startswith("[Trattoria da Bruna — Via del Pratello 48, 40122 Bologna](")

    async def test_location_off_no_distance(self, monkeypatch):
        _on_phone(monkeypatch, _Geocoder(anywhere=[BRUNA]))
        location._CURRENT = _here()
        out = json.loads(await tools.FindPlaceTool(LocationConfig(enable=False)).execute(query="Via del Pratello 48"))
        assert "distanceMeters" not in out["places"][0]

    async def test_nothing_found_says_what_to_do(self, monkeypatch):
        _on_phone(monkeypatch, _Geocoder(boxed=[], anywhere=[]))
        out = json.loads(await tools.FindPlaceTool(LocationConfig()).execute(query="Nowhere at all"))
        assert "address" in out["error"]

    def test_registered_only_where_a_geocoder_exists(self, monkeypatch):
        ctx = SimpleNamespace(android_context=object(), config=SimpleNamespace(location=LocationConfig()))
        monkeypatch.setattr(location, "get_android_context", lambda: object())
        monkeypatch.setattr(location, "_resolve_bridge_class", lambda: SimpleNamespace(isGeocoderPresent=lambda: True))
        assert tools.FindPlaceTool.enabled(ctx)
        monkeypatch.setattr(location, "_resolve_bridge_class", lambda: SimpleNamespace(isGeocoderPresent=lambda: False))
        assert not tools.FindPlaceTool.enabled(ctx)
        ctx.android_context = None
        assert not tools.FindPlaceTool.enabled(ctx)


class TestPreciseFixIsRemembered:
    async def test_a_fresh_fix_replaces_an_old_cache(self, monkeypatch):
        class Fresh:
            def getFresh(self, timeout_ms):  # noqa: N802
                return f"44.4924;11.3472;5.0;{int(time.time() * 1000)};gps"

            def reverseGeocode(self, lat, lng):  # noqa: N802
                return "Via Castiglione, Bologna"

        _on_phone(monkeypatch, Fresh())
        location._CURRENT = location.LocationFix(37.42, -122.08, 5.0, int((time.time() - 3600) * 1000))
        await location.get_location(LocationConfig(), precise=True)
        cached = location.cached_location(LocationConfig())
        assert cached is not None and cached.latitude == 44.4924


class TestCachedLocation:
    def test_off_means_none(self):
        location._CURRENT = _here()
        assert location.cached_location(LocationConfig(enable=False)) is None
        assert location.cached_location(LocationConfig()) is location._CURRENT
