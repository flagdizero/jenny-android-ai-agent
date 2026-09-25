"""Le espressioni cron: interpretarle e trovare la prossima esecuzione.

Al posto di ``croniter``, che Jenny usava solo per questo. Appena importata,
``croniter`` chiama ``platform.architecture()`` per sapere se Python è a 32 bit
(``croniter.is_32bit``); su Linux quella funzione lancia il programma ``file``
come sottoprocesso, e sotto Android il figlio creato con ``vfork`` rimette a
predefinito anche il gestore di SIGSEGV di ART, che lo annota nel log con una
traccia di novanta righe (``libsigchain: Setting SIGSEGV to SIG_DFL``) — a ogni
avvio del gateway. Innocuo, ma evitabile solo toccando la libreria; e dopo un
``vfork`` il figlio non dovrebbe fare altro che ``exec``. Qui non si lancia
niente, e con ``croniter`` se ne vanno ``python-dateutil`` e ``six``.

**Stessa lingua di ``croniter``.** L'interpretazione ricalca il suo algoritmo,
comprese le regole meno ovvie, perché i job già salvati continuino a scattare
agli stessi istanti; è verificata contro di lei su decine di migliaia di casi
casuali (``tests/cron/test_cronexpr.py`` ne tiene i risultati come campioni):

- da cinque a sette campi: ``minuto ora giorno mese giorno-della-settimana
  [secondo] [anno]``; le macro ``@hourly``, ``@daily``/``@midnight``,
  ``@weekly``, ``@monthly``, ``@yearly``/``@annually``;
- valori, liste, intervalli ``a-b``, passi ``*/n``, ``a-b/n`` e ``a/n`` (che vale
  ``a-max/n``); nomi inglesi di mesi e giorni; ``?`` come ``*`` nei due campi
  del giorno;
- un intervallo con inizio e fine uguali (``5-5``, ``jan-jan``) vale **tutto il
  ciclo**, quindi anche ``59/4`` (cioè ``59-59/4``) è "ogni quattro minuti"; un
  intervallo che fa il giro (``fri-mon``, ``22-2``) prosegue il passo dopo il
  giro con la regola di croniter;
- nel giorno del mese ``L`` (l'ultimo), ``a-L`` e ``nW``/``Wn`` (il feriale più
  vicino, da solo nel campo); nel giorno della settimana ``x#n`` (l'*n*-esimo
  *x* del mese) e ``Lx`` (l'ultimo), da non mescolare con valori semplici;
- giorno del mese e giorno della settimana, quando nessuno dei due vale "tutti",
  si combinano in **oppure**; un campo che copre tutti i valori vale "tutti"
  solo se l'altro campo contiene un ``*``. Con ``nW`` il giorno della settimana
  di fatto non conta, con ``x#n`` non conta il giorno del mese: così fa croniter.

**L'unica differenza, voluta, è ai cambi d'ora**, dove croniter era incoerente:
un lavoro giornaliero alle 02:30 partiva alle 03:00 nella notte in cui si salta
un'ora mentre uno orario la saltava, e nella notte in cui l'ora si ripete
partiva due volte. Qui la regola è quella del cron classico (Vixie), che
distingue due specie di lavori:

- **a orario fisso** (né il minuto né l'ora sono una ``*``: ``30 2 * * *``)
  seguono il calendario, una volta al giorno: un orario che quel giorno non
  esiste parte una volta sola, appena finito il salto; uno che capita due volte
  parte una volta sola, la prima;
- **a ripetizione** (``*/15 * * * *``, ``30 * * * *``, ``@hourly``) seguono il
  tempo che passa davvero: un orario che non esiste si salta, uno che capita due
  volte parte due volte. Da 01:30 alle 03:30 del salto di primavera passa
  un'ora, come fra 02:30 e 02:30 della notte d'autunno: il ritmo resta quello.
  Farli partire una volta sola, la prima, lasciava un lavoro ogni cinque minuti
  zitto per un'ora e dieci.

Una ``*`` è una ``*`` anche scritta per esteso: ``0-23``, ``0-22/2`` o
``0,15,30,45`` hanno gli stessi valori di un ``*/n``, e valgono come lei
(:func:`_covers_the_cycle`). Qui si allarga Vixie, che guarda solo il testo.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

__all__ = ["CronExpr", "next_after", "parse"]

_MINUTE, _HOUR, _DOM, _MONTH, _DOW, _SECOND, _YEAR = range(7)
_NAMES = ("minute", "hour", "day of month", "month", "day of week", "second", "year")
_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6), (0, 59), (1970, 2099))
_LEN_MEANS_ALL = (60, 24, 31, 12, 7, 60, 130)
_MONTHS = {name: i for i, name in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1
)}
_DAYS = {name: i for i, name in enumerate(["sun", "mon", "tue", "wed", "thu", "fri", "sat"])}

_MACROS = {
    "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
    "@daily": "0 0 * * *",
    "@weekly": "0 0 * * 0",
    "@monthly": "0 0 1 * *",
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
}

_STEP = re.compile(r"^([^-]+)-([^-/]+)(/(\d+))?$")
_INT = re.compile(r"^\d+$")
_WEEKDAYS = "|".join(_DAYS)
_MONTH_NAMES = "|".join(_MONTHS)
_SPECIAL_DOW = re.compile(
    rf"^(?P<pre>((?P<he>(({_WEEKDAYS})(-({_WEEKDAYS}))?)"
    rf"|(({_MONTH_NAMES})(-({_MONTH_NAMES}))?)|\w+)#)|l)(?P<last>\d+)$"
)
_NEAREST_WEEKDAY = re.compile(r"^(?:(\d+)w|w(\d+))$")

# Oltre non si cerca: un'espressione che non scatta mai (``0 0 31 2 *``) deve
# dirlo, non girare per sempre. Come in croniter, cinquant'anni da dove si parte
# (``mon#5`` di febbraio capita ogni ventotto). Con il campo dell'anno il limite
# e' invece il suo ultimo anno, fino al 2099 (v. ``next_after``).
_SEARCH_YEARS = 50


@dataclass(frozen=True)
class CronExpr:
    minutes: tuple[int, ...]
    hours: tuple[int, ...]
    seconds: tuple[int, ...]
    # ``None`` = il campo vale "tutti" (il ``*`` di croniter dopo l'espansione).
    months: frozenset[int] | None
    years: frozenset[int] | None
    # Giorno del mese: i valori (interi, e "l" per l'ultimo); ``nW``.
    dom: frozenset[int | str] | None
    dom_weekday: frozenset[int]
    # Giorno della settimana (0 = domenica); ``x#n`` e ``Lx`` come
    # {giorno: {n o "l"}}.
    dow: frozenset[int] | None
    dow_nth: tuple[tuple[int, frozenset[int | str]], ...]
    # Il minuto o l'ora sono una ``*``, scritta o per esteso: ai cambi d'ora segue
    # il tempo vero e non il calendario (v. il cappello del modulo).
    repeating: bool = False

    def matches_day(self, day: date) -> bool:
        if self.months is not None and day.month not in self.months:
            return False
        if self.years is not None and day.year not in self.years:
            return False
        dom_any = self.dom is None
        dow_any = self.dow is None
        if not dom_any and not dow_any:
            # Come croniter: due ricerche, una col giorno della settimana messo a
            # "tutti" e una col giorno del mese messo a "tutti", e vince la prima.
            # ``nW`` e ``x#n`` restano applicati in entrambe.
            first = self._dom_part(day, ignore=False) and self._dow_part(day, ignore=True)
            second = self._dom_part(day, ignore=True) and self._dow_part(day, ignore=False)
            return first or second
        return self._dom_part(day, ignore=dom_any) and self._dow_part(day, ignore=dow_any)

    def _dom_part(self, day: date, *, ignore: bool) -> bool:
        if self.dom_weekday:
            return any(_nearest_weekday(day.year, day.month, n) == day.day for n in self.dom_weekday)
        if ignore or self.dom is None:
            return True
        last = calendar.monthrange(day.year, day.month)[1]
        return day.day in self.dom or ("l" in self.dom and day.day == last)

    def _dow_part(self, day: date, *, ignore: bool) -> bool:
        if self.dow_nth:
            for weekday, nths in self.dow_nth:
                days = _nth_weekdays(day.year, day.month, weekday)
                for n in nths:
                    if n == "l":
                        if days[-1] == day.day:
                            return True
                    elif isinstance(n, int) and len(days) >= n and days[n - 1] == day.day:
                        return True
            return False
        if ignore or self.dow is None:
            return True
        return (day.weekday() + 1) % 7 in self.dow  # Python: lunedì 0; cron: domenica 0


def _nth_weekdays(year: int, month: int, weekday: int) -> tuple[int, ...]:
    """I giorni del mese che cadono di *weekday* (0 = domenica), in ordine."""
    python_weekday = (weekday + 6) % 7
    return tuple(
        d for d in range(1, calendar.monthrange(year, month)[1] + 1)
        if date(year, month, d).weekday() == python_weekday
    )


def _nearest_weekday(year: int, month: int, n: int) -> int:
    """Il feriale più vicino al giorno *n* senza uscire dal mese (``nW``).

    Un *n* oltre la fine del mese vale l'ultimo giorno (``31W`` a settembre è il
    30), come in croniter.
    """
    last = calendar.monthrange(year, month)[1]
    day = min(n, last)
    weekday = date(year, month, day).weekday()  # 5 sabato, 6 domenica
    if weekday == 5:
        return day - 1 if day > 1 else day + 2
    if weekday == 6:
        return day + 1 if day < last else day - 2
    return day


def _alias(value, field: int, n_fields: int):
    """Gli alias di croniter: 0 per il primo giorno/mese, 7 per domenica — ma
    non in tutte le forme, e le eccezioni sono le sue."""
    lowmap = {_DOM: {0: 1}, _MONTH: {0: 1}, _DOW: {7: 0}}.get(field, {})
    if value in lowmap and not (
        (field in (_DOM, _MONTH) and n_fields == 5)
        or (field in (_MONTH, _DOW) and n_fields == 6)
        or (field in (_DOM, _MONTH, _DOW) and n_fields == 7)
    ):
        return lowmap[value]
    return value


def _alpha(field: int, token: str):
    names = {_DOM: {"l": "l"}, _MONTH: _MONTHS, _DOW: _DAYS}.get(field, {})
    if token not in names:
        raise ValueError(f"cron field {_NAMES[field]}: {token!r} is not acceptable")
    return names[token]


def _bad(field: int, what: str) -> ValueError:
    return ValueError(f"cron field {_NAMES[field]}: {what}")


def _expand(parts: list[str], field: int):
    """Un campo come lo espande croniter: la lista dei valori, o ``["*"]``;
    più ``nW`` (giorno del mese) e ``{giorno: {n}}`` (giorno della settimana)."""
    n_fields = len(parts)
    text = parts[field]
    lo, hi = _RANGES[field]
    nth: dict[int, set[int | str]] = {}
    nearest: set[int] = set()
    if "?" in text:
        if text != "?" or field not in (_DOM, _DOW):
            raise _bad(field, "'?' must be alone, and only in the day fields")
        text = "*"
    pending = text.split(",")
    res: list = []
    seen: set = set()
    while pending:
        item = str(pending.pop())
        nth_value = None
        if field == _DOW:
            special = _SPECIAL_DOW.match(item)
            if special:
                head, last = special.group("he"), special.group("last")
                if head:
                    item = head
                    nth_value = int(last)
                    if not 1 <= nth_value <= 5:
                        raise _bad(field, f"invalid nth value {last!r}")
                else:
                    item = last
                    nth_value = "l"
        if field == _DOM:
            w = _NEAREST_WEEKDAY.match(item)
            if w:
                w_day = int(w.group(1) or w.group(2))
                if not 1 <= w_day <= 31:
                    raise _bad(field, f"nearest weekday {w_day} out of range")
                if pending or res:
                    raise _bad(field, "'W' can only be used with a single day value")
                nearest.add(w_day)
                res.append(w_day)
                continue
        t = re.sub(r"^\*(/.+)$", rf"{lo}-{hi}\1", item)
        m = _STEP.search(t)
        if not m:
            t = re.sub(r"^(.+)/(.+)$", rf"\1-{hi}/\2", item)
            m = _STEP.search(t)
        if m:
            low, high, step = m.group(1), m.group(2), m.group(4) or "1"
            if field == _DOM and high == "l":
                high = "31"
            if not _INT.search(low):
                low = str(_alpha(field, low))
            if not _INT.search(high):
                high = str(_alpha(field, high))
            step = int(step)
            if step == 0:
                raise _bad(field, "step 0 is not acceptable")
            for band in (low, high):
                if not _INT.search(band):
                    raise _bad(field, f"range {low}-{high} is not acceptable")
            low, high = (_alias(int(v), field, n_fields) for v in (low, high))
            if max(low, high) > max(lo, hi):
                raise _bad(field, f"{max(low, high)} is out of range")
            if low > high:
                whole = list(range(lo, hi + 1))
                rng = list(range(low, hi + 1, step))
                to_skip = 0
                if rng:
                    already_skipped = list(reversed(whole)).index(rng[-1])
                    position = whole.index(rng[-1])
                    if position + step > len(whole) and already_skipped < step:
                        to_skip = step - already_skipped
                rng += list(range(lo + to_skip, high + 1, step))
            elif low == high:
                rng = list(range(lo, hi + 1, step))
            else:
                rng = list(range(low, high + 1, step))
            if nth_value is not None:
                rng = [f"{v}#{nth_value}" if nth_value != "l" else f"l{v}" for v in rng]
            pending += [v for v in rng if v not in seen]
            seen.update(rng)
            continue
        if t.startswith("-"):
            raise _bad(field, "negative numbers are not allowed")
        value = t if re.match(r"^(\d+|\*)$", t) else _alpha(field, t)
        if isinstance(value, str) and value.isdigit():
            value = int(value)
        value = _alias(value, field, n_fields)
        if value not in ("*", "l") and not lo <= int(value) <= hi:
            raise _bad(field, f"{value} is out of range {lo}-{hi}")
        res.append(value)
        if field == _DOW and nth_value is not None:
            nth.setdefault(int(value), set()).add(nth_value)

    values = set(res)
    if len(values) == _LEN_MEANS_ALL[field] and not (
        (field == _DOM and "*" not in parts[_DOW])
        or (field == _DOW and "*" not in parts[_DOM])
    ):
        values = {"*"}
    expanded = ["*"] if values == {"*"} else sorted(values, key=lambda v: (isinstance(v, str), v))
    return expanded, nth, nearest


def _ints(expanded: list) -> frozenset[int] | None:
    return None if expanded == ["*"] else frozenset(int(v) for v in expanded)


def parse(expr: str) -> CronExpr:
    """L'espressione interpretata; ``ValueError`` con il motivo se non va."""
    text = str(expr).lower()
    text = _MACROS.get(text, text)
    parts = text.split()
    if len(parts) not in (5, 6, 7):
        raise ValueError(f"cron expression needs 5, 6 or 7 fields, got {len(parts)}: {expr!r}")
    fields = [_expand(parts, i) for i in range(len(parts))]
    dow_expanded, nth, _ = fields[_DOW]
    if nth:
        plain = set(dow_expanded) - set(nth) - {"*"}
        if plain and len(set(dow_expanded)) != _LEN_MEANS_ALL[_DOW]:
            raise _bad(_DOW, "plain days and x#n / Lx cannot be mixed")

    def ints(i: int, default: tuple[int, ...]) -> tuple[int, ...]:
        if i >= len(parts):
            return default
        expanded = fields[i][0]
        lo, hi = _RANGES[i]
        return tuple(range(lo, hi + 1)) if expanded == ["*"] else tuple(sorted(int(v) for v in expanded))

    return CronExpr(
        minutes=ints(_MINUTE, ()),
        hours=ints(_HOUR, ()),
        seconds=ints(_SECOND, (0,)),
        months=_ints(fields[_MONTH][0]),
        years=_ints(fields[_YEAR][0]) if len(parts) > _YEAR else None,
        dom=None if fields[_DOM][0] == ["*"] else frozenset(fields[_DOM][0]),
        dom_weekday=frozenset(fields[_DOM][2]),
        dow=_ints(dow_expanded),
        dow_nth=tuple(sorted((day, frozenset(n)) for day, n in nth.items())),
        repeating=any(
            parts[i].startswith("*") or _covers_the_cycle(ints(i, ()), *_RANGES[i])
            for i in (_MINUTE, _HOUR)
        ),
    )


def _covers_the_cycle(values: tuple[int, ...], lo: int, hi: int) -> bool:
    """I valori sono esattamente quelli di un ``*/n``?

    E' la ``*`` scritta in un altro modo: ``0-23``, ``0-22/2``, ``0,15,30,45``.
    Vixie guarda solo il testo, e cosi' ``0 0-23 * * *`` — lo stesso lavoro
    orario di ``0 * * * *`` — al cambio d'ora d'autunno restava zitto un'ora e
    dieci. La regola resta stretta di proposito: si parte dal minimo del ciclo e
    lo si copre, come fa ``*/n``. ``9-17`` (un orario d'ufficio), ``9,21`` e
    ``1-23/2`` (passi sfasati) restano a orario fisso, come in Vixie.
    """
    if len(values) < 2 or values[0] != lo:
        return False
    step = values[1] - values[0]
    return values == tuple(range(lo, hi + 1, step))


def _walls(spec: CronExpr, day: date):
    """Gli orari da parete di *day* che *spec* elenca, in ordine sul quadrante."""
    for hour in spec.hours:
        for minute in spec.minutes:
            for second in spec.seconds:
                yield datetime(day.year, day.month, day.day, hour, minute, second)


def _existing(wall: datetime, tz, fold: int) -> datetime | None:
    """*wall* nel fuso *tz* con *fold*, se quell'orario esiste davvero; se no ``None``.

    Esiste se il giro andata e ritorno per UTC lo restituisce uguale: un orario
    dell'ora saltata torna spostato.
    """
    candidate = wall.replace(tzinfo=tz, fold=fold)
    if candidate.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None) == wall:
        return candidate
    return None


def _resolve(wall: datetime, tz) -> datetime:
    """L'istante di un orario da parete nel fuso *tz*.

    Un orario che capita due volte (l'ora che si ripete) vale la prima volta;
    uno che non esiste (l'ora saltata) vale il primo minuto che esiste dopo, cioè
    la fine del salto.
    """
    first = _existing(wall, tz, 0)
    if first is not None:
        return first
    probe = wall.replace(second=0)
    for _ in range(24 * 60):
        probe += timedelta(minutes=1)
        candidate = _existing(probe, tz, 0)
        if candidate is not None:
            return candidate
    raise ValueError(f"no valid local time after {wall} in {tz}")  # pragma: no cover


def _instants(wall: datetime, tz) -> list[datetime]:
    """Gli istanti in cui un orario da parete capita davvero nel fuso *tz*.

    Uno di solito; nessuno se cade nell'ora saltata; due, in ordine, se cade
    nell'ora che si ripete.
    """
    out: list[datetime] = []
    for fold in (0, 1):
        candidate = _existing(wall, tz, fold)
        if candidate is None:
            continue
        back = candidate.astimezone(timezone.utc)
        if not any(o.astimezone(timezone.utc) == back for o in out):
            out.append(candidate)
    return out


def _changes_offset(day: date, tz) -> bool:
    """Il giorno *day* contiene un cambio d'ora nel fuso *tz*?"""
    start = datetime(day.year, day.month, day.day, tzinfo=tz)
    end = start + timedelta(days=1)
    return start.utcoffset() != end.utcoffset()


# Oltre questa distanza sul quadrante due orari sono in ordine anche in UTC.
# Quasi ovunque il cambio d'ora sposta di un'ora (mezz'ora a Lord Howe), ma
# ``Antarctica/Troll`` sposta di **due**: li' il margine e' esatto, non largo, e
# regge perche' i confronti che lo usano sono stretti (``<`` e ``>``) — l'orario
# che sta a due ore esatte resta dentro.
_SHIFT_MARGIN = timedelta(hours=2)


def _next_on_changing_day(
    spec: CronExpr, day: date, tz, start: datetime, base_utc: datetime
) -> datetime | None:
    """Il primo istante vero dopo *base_utc* per un lavoro a ripetizione, in un
    giorno con un cambio d'ora.

    Qui l'ordine sul quadrante non è quello del tempo: le 02:10 della seconda
    passata vengono dopo le 02:50 della prima. Si guardano quindi anche gli
    orari poco prima di *start*, e si tiene il più piccolo in UTC.
    """
    best: datetime | None = None
    best_wall: datetime | None = None
    for wall in _walls(spec, day):
        if wall < start - _SHIFT_MARGIN:
            continue
        if best_wall is not None and wall > best_wall + _SHIFT_MARGIN:
            return best
        for instant in _instants(wall, tz):
            at = instant.astimezone(timezone.utc)
            if at > base_utc and (best is None or at < best.astimezone(timezone.utc)):
                best, best_wall = instant, wall
    return best


def next_after(expr: str | CronExpr, base: datetime) -> datetime:
    """La prima esecuzione **dopo** *base* (che deve avere un fuso).

    Il risultato è nel fuso di *base*. ``ValueError`` se l'espressione non va o
    non scatta entro il limite della ricerca (``0 0 31 2 *``): la fine dell'anno
    cinquanta anni dopo *base*, oppure, se c'e' il campo dell'anno, la fine del
    suo ultimo anno.
    """
    if base.tzinfo is None:
        raise ValueError("base must be timezone-aware")
    spec = expr if isinstance(expr, CronExpr) else parse(expr)
    tz = base.tzinfo
    base_utc = base.astimezone(timezone.utc)
    wall = base.replace(tzinfo=None, microsecond=0)
    start = wall + timedelta(seconds=1)
    day = start.date()
    limit = date(start.year + _SEARCH_YEARS, 12, 31)
    if spec.years is not None:
        # Il campo dell'anno dice fin dove cercare: il suo ultimo anno (al massimo
        # il 2099), non i cinquant'anni da oggi, che rifiutavano un ``2080``
        # valido. E si parte dal primo anno elencato che non sia gia' passato,
        # invece di scorrere a vuoto i giorni che lo precedono.
        limit = date(max(spec.years), 12, 31)
        ahead = [y for y in spec.years if y > start.year]
        if start.year not in spec.years and ahead:
            day = date(min(ahead), 1, 1)
    while day <= limit:
        if spec.matches_day(day):
            if spec.repeating and _changes_offset(day, tz):
                found = _next_on_changing_day(spec, day, tz, start, base_utc)
                if found is not None:
                    return found
                day += timedelta(days=1)
                continue
            for candidate in _walls(spec, day):
                if candidate < start:
                    continue
                instant = _resolve(candidate, tz)
                # In UTC, non fra loro: due datetime con lo stesso fuso Python li
                # confronta per orario da parete, ignorando ``fold``, e nella
                # seconda passata dell'ora ripetuta un orario già trascorso
                # sembrerebbe ancora da venire.
                if instant.astimezone(timezone.utc) > base_utc:
                    return instant
        day += timedelta(days=1)
    raise ValueError(f"cron expression {expr!r} has no run before {limit.year}")
