"""L'ora in millisecondi dall'epoch, come la salvano cron, snapshot e WebUI.

Era ``_now_ms`` in quattro moduli. Modulo a sé e senza dipendenze perché lo
importano anche ``cron`` e ``snapshot``, che ``utils.helpers`` lo caricano solo
dentro le funzioni: qui non c'è niente da trascinarsi dietro. Ognuno lo importa
come ``_now_ms``, così i test che lo leggono o lo sostituiscono da quei moduli
trovano ancora quel nome.
"""

from __future__ import annotations

import time


def now_ms() -> int:
    return int(time.time() * 1000)
