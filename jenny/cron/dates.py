"""Una data detta a parole («il 12 novembre», «verso marzo») diventa un singolo.

Un singolo è sempre un ``at``: la data senza ora si fissa alla creazione — un
giorno suona alle 9 del mattino, un mese alle 9 del primo giorno («verso marzo»
si ricorda all'inizio di marzo, non alla fine). ``precision`` resta sul job solo
per mostrarla com'è stata detta.

L'ora è un pezzo a sé (*time*), non una parte della data: chi non ce l'ha la
omette, invece di doversela inventare per riempire un ISO completo. È ciò che
faceva il modello col vecchio ``at`` del tool ``cron`` (jenny_s22, 01/11/2026:
«il 20 novembre» diventava ``…T09:00:00`` quattro volte su quattro).
"""

from __future__ import annotations

import re
from datetime import datetime, time

from jenny.cron.types import CronSchedule

# L'ora a cui suona una data senza ora, nel fuso dell'utente.
DUE_AT = time(9, 0)

_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTH = re.compile(r"^\d{4}-\d{2}$")
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def date_schedule(value: str, tz_name: str | None, time_of_day: str | None = None) -> CronSchedule:
    """``YYYY-MM-DD`` (giorno), ``YYYY-MM`` (mese) o un ISO con l'ora (esatto).

    *time_of_day* (``HH:MM``) fa di un giorno un'ora esatta; un mese non ne ha.

    Raises:
        ValueError: una data o un'ora che non si leggono.
    """
    from jenny.utils.helpers import safe_zoneinfo

    raw = (value or "").strip()
    clock = (time_of_day or "").strip()
    if clock:
        if not _TIME.match(clock):
            raise ValueError(f"invalid time {time_of_day!r}: use HH:MM, 24-hour")
        if not _DAY.match(raw):
            raise ValueError("a time goes with a day (YYYY-MM-DD), not with a month")
        raw = f"{raw}T{clock}"
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
                f"invalid date {value!r}: use YYYY-MM-DD or YYYY-MM"
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
