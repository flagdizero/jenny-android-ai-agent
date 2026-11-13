"""Date «a venire» per i test, contate da oggi invece che scritte a mano.

``CronService`` rifiuta un singolo con la data già passata, e la bacheca tratta
in un altro modo un promemoria scaduto: un giorno scritto a mano in un test vale
finché non arriva, poi il test fallisce per il calendario e non per il codice
(``2026-11-12`` in ``tests/cron/test_cron_label.py``, rosso dal 13/11/2026).
Dove una data viene confrontata con oggi, si prende da qui.

Le date che sono solo dati — lette, formattate, mai confrontate con oggi —
restano scritte a mano: fisse si leggono meglio.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# Il fuso in cui i test contano i giorni, lo stesso che passano allo scheduler.
TEST_TZ = "Europe/Rome"


def day_ahead(days: int = 7, tz: str = TEST_TZ) -> str:
    """Un giorno a venire (``YYYY-MM-DD``): oggi + *days* nel fuso *tz*."""
    return (datetime.now(ZoneInfo(tz)) + timedelta(days=days)).strftime("%Y-%m-%d")


def month_ahead(tz: str = TEST_TZ) -> str:
    """Un mese a venire (``YYYY-MM``): il marzo dell'anno prossimo.

    Dista sempre fra due e quattordici mesi: mai passato, e mai così vicino da
    risultare «in arrivo» sulla bacheca.
    """
    return f"{datetime.now(ZoneInfo(tz)).year + 1}-03"


def ms_ahead(days: int = 30) -> int:
    """Un istante a venire, in millisecondi: adesso + *days* giorni."""
    return int(time.time() * 1000) + days * 24 * 60 * 60 * 1000
