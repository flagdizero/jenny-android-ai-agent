"""Le rotte delle schede dei posti: l'ultima posizione e le tessere della mappa.

Si misurano le porte (segreto, numeri fuori scala, telefono contro un altro
dispositivo) e la cache delle tessere: un download solo per tessera, la copia
scaduta quando la rete manca, un host che il client non sceglie.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from jenny.config.tool_schemas import LocationConfig
from jenny.runtime import location, map_tiles
from jenny.webui.place_routes import PlaceRoutes

PNG = map_tiles._PNG_MAGIC + b"tile"


@pytest.fixture(autouse=True)
def _reset_state():
    location.reset_location_state()
    yield
    location.reset_location_state()


def _routes(tmp_path: Path, *, authorized: bool = True) -> PlaceRoutes:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return PlaceRoutes(check_api_token=lambda _r: authorized, get_workspace_root=lambda: workspace)


def _config(enable: bool = True) -> SimpleNamespace:
    return SimpleNamespace(tools=SimpleNamespace(location=LocationConfig(enable=enable)))


class TestLastLocation:
    async def test_needs_the_secret(self, tmp_path):
        r = await _routes(tmp_path, authorized=False).dispatch(object(), "/api/location/last")
        assert r.status_code == 401

    async def test_the_cached_fix(self, tmp_path, monkeypatch):
        monkeypatch.setattr("jenny.config.loader.load_config", lambda: _config())
        location._CURRENT = location.LocationFix(
            latitude=44.4924, longitude=11.3472, accuracy_m=12.0, time_ms=int(time.time() * 1000)
        )
        r = await _routes(tmp_path).dispatch(object(), "/api/location/last")
        assert r.status_code == 200
        body = json.loads(r.body)
        assert body["lat"] == 44.4924 and body["accuracyMeters"] == 12.0
        assert body["ageSeconds"] <= 2

    async def test_off_or_empty_is_204(self, tmp_path, monkeypatch):
        monkeypatch.setattr("jenny.config.loader.load_config", lambda: _config(enable=False))
        location._CURRENT = location.LocationFix(1.0, 2.0, None, int(time.time() * 1000))
        assert (await _routes(tmp_path).dispatch(object(), "/api/location/last")).status_code == 204
        monkeypatch.setattr("jenny.config.loader.load_config", lambda: _config())
        location._CURRENT = None
        assert (await _routes(tmp_path).dispatch(object(), "/api/location/last")).status_code == 204

    async def test_an_empty_cache_asks_the_last_known_once(self, tmp_path, monkeypatch):
        """Appena acceso il gateway la cache è vuota: la prima scheda non deve
        restare senza distanza (visto su jenny_s22)."""
        monkeypatch.setattr("jenny.config.loader.load_config", lambda: _config())
        asked: list[bool] = []

        async def last_known(cfg, *, precise):
            asked.append(precise)
            return location.LocationFix(44.49, 11.34, 8.0, int(time.time() * 1000))

        monkeypatch.setattr(location, "_fetch_fix", last_known)
        r = await _routes(tmp_path).dispatch(object(), "/api/location/last")
        assert r.status_code == 200 and json.loads(r.body)["lat"] == 44.49
        assert asked == [False], "il GPS non si accende per una scheda"

    async def test_another_device_does_not_get_it(self, tmp_path, monkeypatch):
        monkeypatch.setattr("jenny.config.loader.load_config", lambda: _config())
        monkeypatch.setattr("jenny.channels.ws_remote.on_remote_listener", lambda: True)
        location._CURRENT = location.LocationFix(1.0, 2.0, None, int(time.time() * 1000))
        assert (await _routes(tmp_path).dispatch(object(), "/api/location/last")).status_code == 204


class TestTileRoute:
    async def test_needs_the_secret_and_real_numbers(self, tmp_path):
        assert (await _routes(tmp_path, authorized=False).dispatch(object(), "/api/map/tile/16/1/1")).status_code == 401
        routes = _routes(tmp_path)
        assert (await routes.dispatch(object(), "/api/map/tile/16/70000/1")).status_code == 404
        assert (await routes.dispatch(object(), "/api/map/tile/19/1/1")).status_code == 404
        assert await routes.dispatch(object(), "/api/map/tile/16/a/1") is None
        assert await routes.dispatch(object(), "/api/map/tile/16/1/1/../../x") is None

    async def test_a_tile_from_the_network(self, tmp_path, monkeypatch):
        async def fake(cache_dir, z, x, y):
            assert Path(cache_dir) == tmp_path / "map_tiles"
            return PNG

        monkeypatch.setattr(map_tiles, "get_tile", fake)
        r = await _routes(tmp_path).dispatch(object(), "/api/map/tile/16/34601/23824")
        assert r.status_code == 200
        assert r.body == PNG
        assert r.headers["Content-Type"] == "image/png"

    async def test_no_tile_is_502(self, tmp_path, monkeypatch):
        async def none(*_a):
            return None

        monkeypatch.setattr(map_tiles, "get_tile", none)
        assert (await _routes(tmp_path).dispatch(object(), "/api/map/tile/16/1/1")).status_code == 502


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestTileCache:
    async def test_downloads_once_from_the_fixed_host(self, tmp_path):
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, content=PNG)

        async with _client(handler) as client:
            assert await map_tiles.get_tile(tmp_path, 16, 5, 7, client=client) == PNG
            assert await map_tiles.get_tile(tmp_path, 16, 5, 7, client=client) == PNG
        assert len(seen) == 1
        assert str(seen[0].url) == "https://tile.openstreetmap.org/16/5/7.png"
        assert seen[0].headers["User-Agent"].startswith("Jenny/")
        assert (tmp_path / "16" / "5" / "7.png").read_bytes() == PNG

    async def test_offline_serves_the_stale_copy(self, tmp_path):
        path = tmp_path / "16" / "5" / "7.png"
        path.parent.mkdir(parents=True)
        path.write_bytes(PNG)
        old = time.time() - map_tiles.TILE_TTL_S - 60
        os.utime(path, (old, old))

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("offline")

        async with _client(handler) as client:
            assert await map_tiles.get_tile(tmp_path, 16, 5, 7, client=client) == PNG

    async def test_not_a_png_is_not_kept(self, tmp_path):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"<html>blocked</html>")

        async with _client(handler) as client:
            assert await map_tiles.get_tile(tmp_path, 16, 5, 7, client=client) is None
        assert not (tmp_path / "16").exists()

    async def test_out_of_range_never_asks(self, tmp_path):
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("asked")

        async with _client(handler) as client:
            assert await map_tiles.get_tile(tmp_path, 3, 8, 0, client=client) is None
            assert await map_tiles.get_tile(tmp_path, 1, 0, 0, client=client) is None

    def test_prune_drops_the_oldest(self, tmp_path):
        for i in range(4):
            p = tmp_path / "16" / "1" / f"{i}.png"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"x" * 100)
            os.utime(p, (1000 + i, 1000 + i))
        map_tiles._prune(tmp_path, 250)
        assert sorted(p.name for p in (tmp_path / "16" / "1").iterdir()) == ["2.png", "3.png"]
