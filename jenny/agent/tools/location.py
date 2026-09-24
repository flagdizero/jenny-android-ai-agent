"""Tool on-demand per la posizione del dispositivo (solo Android).

Si registra solo quando c'è un Android Context e il toggle
``tools.location.enable`` è ON — stesso criterio dei tool ``android_web``. Il
grosso della logica (bridge nativo, cache, reverse-geocoding) vive in
``jenny/runtime/location.py``; qui c'è solo l'affaccio LLM.

Di default ritorna il fix *last-known* (gratis, già iniettato nel contesto);
con ``precise=true`` forza un fix GPS fresco, che accende il radio e costa
batteria — da usare solo quando serve precisione reale.
"""

from __future__ import annotations

import json
from typing import Any

from jenny.agent.tools.base import Tool, tool_parameters
from jenny.agent.tools.schema import BooleanSchema, tool_parameters_schema


@tool_parameters(
    tool_parameters_schema(
        precise=BooleanSchema(
            description=(
                "Force a fresh GPS fix (turns the radio on, costs battery and a "
                "few seconds). Leave false to use the cheap last-known position, "
                "which is enough for most questions."
            ),
            default=False,
        ),
        required=[],
    )
)
class GetLocationTool(Tool):
    """Return the device's current geographic location."""

    _scopes = {"core", "orchestrator", "subagent"}

    name = "get_location"
    description = (
        "Get the user's current location (reverse-geocoded place plus "
        "latitude/longitude). The device position is already injected into "
        "context each turn; call this to refresh it, to get coordinates for a "
        "maps link or distance calc, or with precise=true when you need an "
        "accurate live GPS fix."
    )

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return (
            bool(ctx.android_context)
            and getattr(ctx.config, "location", None) is not None
            and ctx.config.location.enable
        )

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(config=ctx.config.location)

    def __init__(self, config: Any = None):
        self.config = config

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, precise: bool = False, **kwargs: Any) -> str:
        precise = bool(kwargs.pop("precise", precise))
        from jenny.runtime.location import get_location

        fix = await get_location(self.config, precise=precise)
        if fix is None:
            return json.dumps(
                {
                    "error": (
                        "Location unavailable — the toggle may be off, the "
                        "Android location permission not granted, or no GPS fix "
                        "is currently known."
                    )
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "place": fix.place,
                "latitude": round(fix.latitude, 6),
                "longitude": round(fix.longitude, 6),
                "accuracyMeters": round(fix.accuracy_m, 1) if fix.accuracy_m is not None else None,
                "ageSeconds": round(fix.age_seconds()),
                "source": fix.source,
                "mapsUrl": f"https://www.google.com/maps?q={fix.latitude:.6f},{fix.longitude:.6f}",
            },
            ensure_ascii=False,
        )


# Registrazione esplicita dei tool di questo modulo (letta da loader.py).
TOOLS = [GetLocationTool]
