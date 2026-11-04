"""WebUI-facing access to installed Android apps, via the Kotlin bridge.

Mirrors the lazy bridge resolution/caching pattern used by
``jenny.agent.tools.android_web`` for ``AgenticSearchBridge``, but for
``InstalledAppsBridge``. This is UI-only plumbing (not an agent tool): the
user picks an app from the WebUI, the agent is not involved.
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from jenny.android_entry import get_android_context
from jenny.runtime.chaquopy_bridge import BridgeCache

_BRIDGE = BridgeCache("com.flagdizero.jenny.InstalledAppsBridge")

_LIST_TIMEOUT_S = 15
_LAUNCH_TIMEOUT_S = 10
_UNINSTALL_TIMEOUT_S = 10
_APP_INFO_TIMEOUT_S = 10


def reset_installed_apps_state() -> None:
    """Drop the cached InstalledAppsBridge so a fresh gateway start can't inherit
    a stale bridge from a previous crashed loop.

    Simmetrico a ``android_web.reset_android_web_state``: prima questo bridge
    non veniva mai resettato (asimmetria → possibile bridge stale dopo un
    restart del gateway). Chiamato da ``android_entry.run_gateway``.
    """
    _BRIDGE.reset()


def _resolve_bridge_class() -> Any:
    """Resolve the Kotlin InstalledAppsBridge class via Chaquopy.

    Stays a module-level function rather than a direct call to
    ``_BRIDGE.resolve_class``: it is the seam the tests replace to stand
    in for the bridge off-device.
    """
    return _BRIDGE.resolve_class()


async def _get_bridge(context: Any) -> Any:
    """Build or return a cached InstalledAppsBridge instance (thread-safe)."""
    return await _BRIDGE.get(context, resolve=_resolve_bridge_class)


async def webui_android_apps_payload() -> dict[str, Any]:
    """Return installed launchable Android apps as ``{"apps": [...]}``.

    Returns an empty list (rather than raising) when there is no Android
    context (desktop/test runs) or the bridge call fails, so the WebUI tab
    degrades gracefully instead of erroring the whole apps view.

    **Un guasto del bridge però si dichiara** (``"error": "unavailable"``, passo
    6.2 del piano del cassetto). Senza quel campo la risposta di un ponte rotto
    è indistinguibile da quella di un telefono senza app: la UI stampa "nessuna
    app" in tutti e due i casi, e non c'è modo di sapere quale dei due sia — è
    il limite che ``docs/using/app-launcher.md`` denunciava. Assenza di contesto
    Android **non** è un errore: lì la lista è davvero vuota, e dirlo un guasto
    accenderebbe un avviso su ogni sessione desktop.
    """
    context = get_android_context()
    if context is None:
        return {"apps": []}
    raw = await _BRIDGE.call(_get_bridge, context, "listInstalledApps", timeout=_LIST_TIMEOUT_S)
    try:
        return {"apps": json.loads(raw)}
    except (TypeError, ValueError):
        # ``None`` è un guasto del bridge, già loggato con la sua traccia.
        if raw is not None:
            logger.warning("Installed Android apps unreadable: {!r}", str(raw)[:80])
        return {"apps": [], "error": "unavailable"}


async def launch_android_app(package_name: str) -> bool:
    """Launch an installed app by package name. Returns False on any failure."""
    return bool(await _BRIDGE.call(_get_bridge, get_android_context(), "launchApp",
                                   package_name, timeout=_LAUNCH_TIMEOUT_S, default=False))


async def uninstall_android_app(package_name: str) -> bool:
    """Open the system uninstall dialog for an app. Returns False on any failure."""
    return bool(await _BRIDGE.call(_get_bridge, get_android_context(), "uninstallApp",
                                   package_name, timeout=_UNINSTALL_TIMEOUT_S, default=False))


async def open_android_app_info(package_name: str) -> bool:
    """Open the system "App info" screen for an app. Returns False on any failure."""
    return bool(await _BRIDGE.call(_get_bridge, get_android_context(), "openAppInfo",
                                   package_name, timeout=_APP_INFO_TIMEOUT_S, default=False))
