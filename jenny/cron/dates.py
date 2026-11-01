"""Una data detta a parole («il 12 novembre», «verso marzo») diventa un singolo.

Un singolo è sempre un ``at``: la data senza ora si fissa alla creazione — un
giorno suona alle 9 del mattino, un mese alle 9 del primo giorno («verso marzo»
si ricorda all'inizio di marzo, non alla fine). ``precision`` resta sul job solo
per mostrarla com'è stata detta.
"""

from __future__ import annotations

import re
from datetime import datetime, time

from jenny.cron.types import CronSchedule

# L'ora a cui suona una data senza ora, nel fuso dell'utente.
DUE_AT = time(9, 0)

_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTH = re.compile(r"^\d{4}-\d{2}$")


def date_schedule(value: str, tz_name: str | None) -> CronSchedule:
    """``YYYY-MM-DD`` (giorno), ``YYYY-MM`` (mese) o un ISO con l'ora (esatto).

    Raises:
        ValueError: una data che non si legge.
    """
    from jenny.utils.helpers import safe_zoneinfo

    raw = (value or "").strip()
    tz = safe_zoneinfo(tz_name) if tz_name else datetime.now().astimezone().tzinfo
    if _MONTH.match(raw):
        when = datetime.combine(datetime.strptime(raw + "-01", "%Y-%m-%d").date(), DUE_AT)
        precision = "month"
    elif _DAY.match(raw):
        when = datetime.combine(datetime.strptime(raw, "%Y-%m-%d").date(), DUE_AT)
        precision = "day"
    else:
        try:
            when = datetime.fromisoformat(raw)
        except ValueError:
            raise ValueError(
                f"invalid date {value!r}: use YYYY-MM-DD, YYYY-MM, or YYYY-MM-DDTHH:MM"
            ) from None
        precision = "exact"
    if when.tzinfo is None:
        when = when.replace(tzinfo=tz)
    return CronSchedule(kind="at", at_ms=int(when.timestamp() * 1000), precision=precision)


def schedule_date(schedule: CronSchedule, tz_name: str | None) -> str | None:
    """La data di un singolo com'è stata detta: ``YYYY-MM``, ``YYYY-MM-DD`` o ISO."""
    from jenny.utils.helpers import safe_zoneinfo

    if schedule.kind != "at" or not schedule.at_ms:
        return None
    tz = safe_zoneinfo(tz_name) if tz_name else datetime.now().astimezone().tzinfo
    when = datetime.fromtimestamp(schedule.at_ms / 1000, tz=tz)
    if schedule.precision == "month":
        return when.strftime("%Y-%m")
    if schedule.precision == "day":
        return when.strftime("%Y-%m-%d")
    return when.isoformat(timespec="minutes")
