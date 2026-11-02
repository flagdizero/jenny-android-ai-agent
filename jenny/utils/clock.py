"""L'ora: in millisecondi dall'epoch, come la salvano cron, snapshot e WebUI, e
quella dell'utente, nel suo fuso.

``now_ms`` era ``_now_ms`` in quattro moduli. Modulo a sé e senza dipendenze perché lo
importano anche ``cron`` e ``snapshot``, che ``utils.helpers`` lo caricano solo
dentro le funzioni: qui non c'è niente da trascinarsi dietro. Ognuno lo importa
come ``_now_ms``, così i test che lo leggono o lo sostituiscono da quei moduli
trovano ancora quel nome.

``zone``/``local_now`` erano la stessa coppia di righe in sei posti. Il fuso lo
risolve ``utils.helpers.safe_zoneinfo``, importato dentro la funzione per non
trascinarsi dietro ``helpers``.
"""

from __future__ import annotations

import time
from datetime import datetime, tzinfo


def now_ms() -> int:
    return int(time.time() * 1000)


def zone(name: str | None) -> tzinfo | None:
    """Il fuso *name* (mai un'eccezione, v. ``safe_zoneinfo``); ``None`` senza nome."""
    if not name:
        return None
    from jenny.utils.helpers import safe_zoneinfo

    return safe_zoneinfo(name)


def local_now(name: str | None = None) -> datetime:
    """Adesso, consapevole del fuso: in *name*, o in quello del sistema senza nome."""
    tz = zone(name)
    return datetime.now(tz=tz) if tz else datetime.now().astimezone()
