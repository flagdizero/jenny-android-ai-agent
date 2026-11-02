"""Tool on-demand per la posizione del dispositivo (solo Android).

Si registra solo quando c'è un Android Context e il toggle
``tools.location.enable`` è ON — stesso criterio dei tool ``android_web``. Il
grosso della logica (bridge nativo, cache, reverse-geocoding) vive in
``jenny/runtime/location.py``; qui c'è solo l'affaccio LLM.

Di default ritorna il fix *last-known* (gratis, già iniettato nel contesto);
con ``precise=true`` forza un fix GPS fresco, che accende il radio e costa
batteria — da usare solo quando serve precisione reale.

``find_place`` è l'altra metà: da nome e indirizzo a coordinate vere, col
``Geocoder`` di Android (gratis, senza chiave, nessuna libreria nell'APK). Si
registra quando il telefono ha un Geocoder, anche a posizione spenta: cercare
un indirizzo non legge dov'è il telefono. Tutti e due restituiscono il link
già pronto per la chat (``chatLink``), così le coordinate non le scrive mai
il modello.
"""

from __future__ import annotations

import json
import math
from typing import Any

from jenny.agent.tools.base import Tool, tool_parameters
from jenny.agent.tools.schema import BooleanSchema, StringSchema, tool_parameters_schema

# Il formato con cui Jenny manda un posto in chat: un link a Google Maps da solo
# sulla sua riga, che la WebUI disegna come scheda (``shared/place-card.js``) e
# che altrove resta un link che funziona. Lo strumento lo restituisce già fatto
# (``chatLink``): le coordinate non le deve scrivere il modello.
_MAPS_SEARCH = "https://www.google.com/maps/search/?api=1&query="

_SHARE_HOWTO = (
    "To show a place in the chat, copy its chatLink exactly, alone on its own line: the "
    "app turns it into a map card that opens Google Maps. Several places: one chatLink per "
    "line. Never write coordinates yourself, and never edit the numbers in a chatLink."
)


def _link_text(text: str) -> str:
    """Il testo di un link Markdown: niente parentesi quadre, niente a capo."""
    return " ".join(text.replace("[", "(").replace("]", ")").split())


def chat_link(name: str, address: str, latitude: float, longitude: float) -> str:
    """``[nome — indirizzo](https://www.google.com/maps/search/?api=1&query=lat%2Clng)``."""
    label = " — ".join(part for part in (_link_text(name), _link_text(address)) if part)
    query = f"{latitude:.6f}%2C{longitude:.6f}"
    return f"[{label or f'{latitude:.6f}, {longitude:.6f}'}]({_MAPS_SEARCH}{query})"


def _split_place(place: str | None) -> tuple[str, str]:
    """``"Piazza Santo Stefano, Bologna, Emilia-Romagna, Italia"`` → la via come
    nome, il resto come indirizzo: è quel che fa ``reverseGeocode``."""
    parts = [p.strip() for p in (place or "").split(",") if p.strip()]
    if not parts:
        return "", ""
    return parts[0], ", ".join(parts[1:])


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
        "accurate live GPS fix. " + _SHARE_HOWTO
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
                "chatLink": chat_link(*_split_place(fix.place), fix.latitude, fix.longitude),
            },
            ensure_ascii=False,
        )


@tool_parameters(
    tool_parameters_schema(
        query=StringSchema(
            "What to look up: the place's name together with its street address and city, "
            "as precise as you have it (e.g. 'Trattoria da Bruna, Via del Pratello 48, "
            "Bologna'). A bare business name is often not found: get the address first, "
            "with a web search if needed.",
            min_length=2,
            max_length=200,
        ),
        label=StringSchema(
            "The place's name as it should appear on the card (e.g. 'Trattoria da Bruna'). "
            "Used when the map service returns only an address.",
            max_length=80,
        ),
        required=["query"],
    )
)
class FindPlaceTool(Tool):
    """Look a place up by name and address and return its coordinates."""

    _scopes = {"core", "orchestrator", "subagent"}

    name = "find_place"
    description = (
        "Find where a place is (a shop, a restaurant, a station, an address): returns up to "
        "five matches with name, address, coordinates, distance from the user when the "
        "device location is on, and a ready chatLink. Use it whenever you want to send the "
        "user a place they can open on a map. " + _SHARE_HOWTO
    )

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        from jenny.runtime.location import geocoder_available

        return bool(ctx.android_context) and geocoder_available()

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(config=getattr(ctx.config, "location", None))

    def __init__(self, config: Any = None):
        self.config = config

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, query: str = "", label: str = "", **kwargs: Any) -> str:
        from jenny.runtime.location import cached_location, find_places

        query = str(kwargs.pop("query", query) or "").strip()
        label = _link_text(str(kwargs.pop("label", label) or ""))
        if len(query) < 2:
            return json.dumps({"error": "Give the place's name and address."})
        here = cached_location(self.config)
        matches = await find_places(query, near=here)
        if not matches:
            return json.dumps(
                {
                    "error": (
                        "No place found. Try adding the street address and city, or check "
                        "the name with a web search."
                    )
                },
                ensure_ascii=False,
            )
        results = []
        for m in matches:
            name = m.name or label
            item: dict[str, Any] = {
                "name": name,
                "address": m.address,
                "latitude": round(m.latitude, 6),
                "longitude": round(m.longitude, 6),
            }
            if here is not None:
                item["distanceMeters"] = round(
                    _distance_m(here.latitude, here.longitude, m.latitude, m.longitude)
                )
            item["chatLink"] = chat_link(name, m.address, m.latitude, m.longitude)
            results.append(item)
        return json.dumps({"places": results}, ensure_ascii=False)


def _distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Distanza in metri (haversine): la stessa formula della scheda in chat."""
    rad = math.pi / 180
    d_lat = (lat2 - lat1) * rad
    d_lng = (lng2 - lng1) * rad
    s = (
        math.sin(d_lat / 2) ** 2
        + math.cos(lat1 * rad) * math.cos(lat2 * rad) * math.sin(d_lng / 2) ** 2
    )
    return 2 * 6371008.8 * math.asin(min(1.0, math.sqrt(s)))


# Registrazione esplicita dei tool di questo modulo (letta da loader.py).
TOOLS = [GetLocationTool, FindPlaceTool]
