"""Le mani di Jenny: il lato Python di ``PhoneBridge``.

Rovescio di ``PhoneTree``/``PhoneActions`` in Kotlin, con la stessa divisione
dei compiti di ``runtime/screen.py``: **leggere e agire lo fa Kotlin**, qui c'è
il ponte e la memoria dell'ultima lettura. Nessuna decisione sta qui: se
un'azione si può fare lo decide ``jenny/phone/consent.py``, prima.

L'ultima lettura (:func:`last_snapshot`) resta in memoria perché il cancello
del consenso deve sapere **che cosa** sta per toccare un ``ref`` — il nome del
tasto — senza rileggere lo schermo, che nel frattempo potrebbe essere cambiato.

Fuori da Android ogni chiamata al bridge è un no-op che risponde «niente».
"""

from __future__ import annotations

import json
from typing import Any

from jenny.runtime.chaquopy_bridge import BridgeCache
from jenny.runtime.context import get_android_context

_BRIDGE = BridgeCache("com.flagdizero.jenny.PhoneBridge")

# Le chiamate brevi (stato, nome di un'app). Leggere lo schermo costa decine di
# millisecondi; il margine è per un telefono sotto carico.
_BRIDGE_TIMEOUT_S = 10

# Una serie di passi aspetta dopo ognuno che lo schermo si fermi (fino a 3 s) e
# può contenere attese esplicite: il tetto è quello di una serie lunga.
_ACT_TIMEOUT_S = 90

_LAST: dict[str, Any] | None = None


def reset_phone_state() -> None:
    """Butta il bridge in cache e l'ultima lettura a un nuovo start del gateway."""
    global _LAST
    _BRIDGE.reset()
    _LAST = None


def _resolve_bridge_class() -> Any:
    """Seam per i test, come ``screen._resolve_bridge_class``."""
    return _BRIDGE.resolve_class()


async def _call(method: str, *args: Any, timeout: float = _BRIDGE_TIMEOUT_S) -> Any:
    """Chiama un metodo del bridge. Mai solleva: ``None`` se Android non c'è o
    la chiamata fallisce."""
    return await _BRIDGE.call(lambda c: _BRIDGE.get(c, resolve=_resolve_bridge_class),
                              get_android_context(), method, *args, timeout=timeout)


def _json(raw: Any) -> Any:
    if not raw:
        return None
    try:
        return json.loads(str(raw))
    except ValueError:
        return None


async def phone_state() -> str:
    """``"on"``, ``"off"``, ``"unsupported"`` o ``"unavailable"`` (niente Android)."""
    state = await _call("state")
    return str(state) if state else "unavailable"


async def screen_state() -> dict[str, bool]:
    """Schermo acceso, bloccato, con blocco sicuro. Senza Android: acceso e libero."""
    data = _json(await _call("screen"))
    if not isinstance(data, dict):
        return {"on": True, "locked": False, "secure": False}
    return {key: bool(data.get(key)) for key in ("on", "locked", "secure")}


async def snapshot(filter_text: str | None, max_chars: int) -> dict[str, Any] | None:
    """Legge lo schermo adesso; ``None`` a servizio spento. La lettura diventa
    :func:`last_snapshot`."""
    global _LAST
    data = _json(await _call("snapshot", filter_text or None, int(max_chars)))
    if not isinstance(data, dict):
        return None
    _LAST = data
    return data


def last_snapshot() -> dict[str, Any] | None:
    """L'ultima lettura fatta, o ``None``."""
    return _LAST


def ref_name(ref: str) -> tuple[str, str] | None:
    """``(ruolo, nome)`` del *ref* nell'ultima lettura, o ``None`` se non c'è."""
    index = (_LAST or {}).get("index")
    entry = index.get(ref) if isinstance(index, dict) else None
    if isinstance(entry, list) and len(entry) == 2:
        return str(entry[0]), str(entry[1])
    return None


async def act(steps: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """Esegue i passi; una voce per passo eseguito. ``None`` a servizio spento."""
    data = _json(await _call("act", json.dumps(steps, ensure_ascii=False), timeout=_ACT_TIMEOUT_S))
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else None


async def foreground() -> str | None:
    """Il pacchetto dell'app in primo piano, o ``None``."""
    pkg = await _call("foreground")
    return str(pkg) if pkg else None


async def resolve_app(app: str) -> str | None:
    """Il pacchetto dell'app chiamata per nome («WhatsApp»), o ``None``."""
    pkg = await _call("resolveApp", str(app))
    return str(pkg) if pkg else None


async def app_label(package: str) -> str:
    """Il nome che l'utente vede per *package*; il pacchetto stesso se manca."""
    label = await _call("appLabel", str(package))
    return str(label) if label else str(package)


async def open_phone_settings() -> bool:
    """Apre Impostazioni → Accessibilità, dove il servizio si accende."""
    return bool(await _call("openSettings"))
