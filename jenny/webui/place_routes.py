"""Route HTTP delle schede dei posti in chat (``shared/place-card.js``).

- ``/api/location/last``: l'ultimo fix in cache, per «sei qui» e «1,2 km da
  te». Solo letture della cache, mai il GPS. ``204`` se la posizione è spenta,
  se non c'è un fix, o se la pagina è aperta da un altro dispositivo del
  tailnet: la distanza «da te» sarebbe dal telefono, non da chi guarda.
- ``/api/map/tile/{z}/{x}/{y}``: una tessera della mappa
  (``jenny.runtime.map_tiles``). Il client dà tre numeri, mai un indirizzo.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from loguru import logger
from websockets.http11 import Request as WsRequest
from websockets.http11 import Response

from jenny.channels.http_utils import http_error, http_json_response, http_response

_TILE_PATH = re.compile(r"^/api/map/tile/(\d{1,2})/(\d{1,7})/(\d{1,7})$")


class PlaceRoutes:
    def __init__(
        self,
        *,
        check_api_token: Callable[[WsRequest], bool],
        get_workspace_root: Callable[[], Path],
        log: Any = logger,
    ) -> None:
        self._check_api_token = check_api_token
        self._get_workspace_root = get_workspace_root
        self._log = log

    async def dispatch(self, request: WsRequest, path: str) -> Response | None:
        if path == "/api/location/last":
            if not self._check_api_token(request):
                return http_error(401, "Unauthorized")
            return await self._last_location()
        m = _TILE_PATH.match(path)
        if m:
            if not self._check_api_token(request):
                return http_error(401, "Unauthorized")
            return await self._tile(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return None

    # -- handlers --

    async def _last_location(self) -> Response:
        from jenny.channels.ws_remote import on_remote_listener
        from jenny.config.loader import load_config
        from jenny.runtime.location import cached_location, refresh_current_location

        if on_remote_listener():
            return http_response(b"", status=204)
        cfg = load_config().tools.location
        fix = cached_location(cfg)
        if fix is None and getattr(cfg, "enable", False):
            # Appena acceso il gateway la cache è vuota: un giro di last-known
            # (la cache del sistema, niente GPS) e si risponde con quello.
            await refresh_current_location(cfg)
            fix = cached_location(cfg)
        if fix is None:
            return http_response(b"", status=204)
        return http_json_response(
            {
                "lat": round(fix.latitude, 6),
                "lng": round(fix.longitude, 6),
                "accuracyMeters": round(fix.accuracy_m, 1) if fix.accuracy_m is not None else None,
                "ageSeconds": round(fix.age_seconds()),
            }
        )

    async def _tile(self, z: int, x: int, y: int) -> Response:
        from jenny.runtime.map_tiles import TILE_TTL_S, get_tile, valid_tile
        from jenny.snapshot.locations import map_tiles_dir_for

        if not valid_tile(z, x, y):
            return http_error(404, "Not Found")
        body = await get_tile(map_tiles_dir_for(self._get_workspace_root()), z, x, y)
        if body is None:
            return http_error(502, "Tile unavailable")
        return http_response(
            body,
            content_type="image/png",
            extra_headers=[("Cache-Control", f"private, max-age={TILE_TTL_S}")],
        )
