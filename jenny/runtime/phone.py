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
        return {"on": True, "locked": False, "secure": False, "deviceLocked": False}
    return {key: bool(data.get(key)) for key in ("on", "locked", "secure", "deviceLocked")}


async def wake() -> str:
    """Accende lo schermo e toglie un blocco senza credenziali. ``"ready"``,
    ``"woken"``, ``"locked"`` (serve il PIN) o ``"failed"``; senza Android
    ``"ready"``."""
    state = await _call("wake")
    return str(state) if state else "ready"


async def sleep_again() -> bool:
    """Rispegne lo schermo, se a svegliarlo era stata :func:`wake`."""
    return bool(await _call("sleepAgain"))


async def snapshot(filter_text: str | None, max_chars: int) -> dict[str, Any] | None:
    """Legge lo schermo adesso; ``None`` a servizio spento. La lettura diventa
    :func:`last_snapshot`."""
    global _LAST
    data = _json(await _call("snapshot", filter_text or None, int(max_chars)))
    if not isinstance(data, dict):
        return None
    _LAST = data
    return data


async def screenshot(path: str, max_side: int) -> dict[str, Any] | None:
    """Fotografa lo schermo in *path* (JPEG ridotto a *max_side*). Torna
    ``{"width", "height"}``, ``{"secure": True}`` per un'app che vieta gli
    screenshot, o ``None`` se non si è potuto."""
    data = _json(await _call("screenshot", str(path), int(max_side)))
    return data if isinstance(data, dict) else None


async def name_at(x: float, y: float) -> str | None:
    """Il nome di quello che sta nel punto (x, y) dell'ultima foto: ``""`` se
    lì non c'è niente con un nome, ``None`` se non si può sapere (nessuna foto,
    servizio spento)."""
    name = await _call("nameAt", float(x), float(y))
    return None if name is None else str(name)


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


async def pill_ask(cid: str, title: str, body: str, *, always: bool, where: str = "") -> bool:
    """La domanda *cid* sulla pillola sopra l'app. *where* (``ref:…`` o
    ``pt:x,y``) è quello di cui si chiede: la pillola non lo copre. Falso se
    non si può mostrare."""
    return bool(await _call("pillAsk", cid, title, body, bool(always), str(where)))


async def pill_ask_done() -> bool:
    """La domanda ha avuto risposta: la pillola torna alla guida o sparisce."""
    return bool(await _call("pillAskDone"))


async def pill_driving(ident: str, text: str) -> bool:
    """«Jenny sta usando il telefono» sulla pillola, con «Ferma»."""
    return bool(await _call("pillDriving", ident, text))


async def pill_done(text: str | None) -> bool:
    """Fine della guida; con *text* resta l'esito per qualche secondo."""
    return bool(await _call("pillDone", text))


async def stop() -> bool:
    """«Ferma»: i passi non ancora fatti non si fanno."""
    return bool(await _call("stop"))


async def open_chat() -> bool:
    """Riporta davanti la chat di Jenny."""
    return bool(await _call("openChat"))


async def open_phone_settings() -> bool:
    """Apre Impostazioni → Accessibilità, dove il servizio si accende."""
    return bool(await _call("openSettings"))
