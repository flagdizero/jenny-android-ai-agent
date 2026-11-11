"""Le zone: «quando arrivi lì», deciso dal codice. Puro: niente I/O, niente orologio.

Un job ``schedule.kind == "zone"`` è un promemoria il cui «quando» è un cerchio
(centro e raggio) invece di un'ora. Chi guarda la posizione è la sentinella
(:mod:`jenny.cron.zone_watch`); qui ci sono solo le regole con cui decide, così
si provano senza telefono:

* :func:`judge` — dentro, fuori, appena entrato, o non si può dire;
* :func:`needs_precise` — la posizione da rete basta, o serve il GPS;
* :func:`next_check_s` — fra quanto guardare di nuovo.

Il modello non controlla niente: la sentinella confronta a zero token e sveglia
Jenny solo all'arrivo, rendendo dovuto il job (``CronService.fire_zone``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from jenny.runtime.location import distance_m

# ── costanti: un posto solo ─────────────────────────────────────────────────

# Il raggio di una zona. Sotto i 100 m la posizione da rete (e spesso il GPS in
# città) non distingue «dentro» da «dall'altra parte della strada».
MIN_RADIUS_M = 100
DEFAULT_RADIUS_M = 200
MAX_RADIUS_M = 50_000

# Per dire «uscito» non basta un passo oltre il bordo: un fix che balla sul
# confine farebbe entrare e uscire a ogni lettura, e una zona «ogni arrivo»
# scatterebbe a vuoto. Si esce solo oltre il raggio più questo margine.
EXIT_MARGIN_M = 100

# Un fix senza precisione dichiarata vale come uno impreciso.
UNKNOWN_ACCURACY_M = 500

# Un fix più vecchio di così non è una lettura di adesso.
MAX_FIX_AGE_S = 120

# Fra una lettura e l'altra: mai meno di 2 minuti, mai più di 30.
MIN_CHECK_S = 120
MAX_CHECK_S = 1800
# Già dentro (una zona «ogni arrivo», o armata stando lì): si aspetta l'uscita,
# che non ha fretta.
INSIDE_CHECK_S = 900
# La velocità massima con cui si suppone ci si avvicini (108 km/h): a 40 km non
# si arriva prima di 22 minuti, quindi si ricontrolla fra 22 minuti.
SPEED_MPS = 30

# Telefono fermo: due fix di fila a meno di così (o della loro precisione).
STILL_M = 50
# Senza il sensore di movimento il raddoppio a telefono fermo si ferma qui.
STILL_CAP_NO_SENSOR_S = 600

# Un arrivo che non è stato consegnato (niente rete, agente non pronto) si
# riprova per un'ora; dopo non è più «sei appena arrivato».
ZONE_RETRY_WINDOW_MS = 60 * 60 * 1000

# Cieca (servizio senza il tipo ``location``, o permesso tolto) da tanto così con
# delle zone armate: un avviso all'utente, uno per episodio.
BLIND_NOTICE_AFTER_S = 1800

# Quante zone accese al massimo: oltre, la lista diventa illeggibile e le
# letture a cadenza fitta si sommano.
MAX_ZONES = 20


class Fix(Protocol):
    """Quel che serve di una posizione (``runtime.location.LocationFix`` lo è)."""

    @property
    def latitude(self) -> float: ...
    @property
    def longitude(self) -> float: ...
    @property
    def accuracy_m(self) -> float | None: ...
    @property
    def time_ms(self) -> int: ...


@dataclass(frozen=True)
class Zone:
    """Una zona come la vede la sentinella: il job, il cerchio, lo stato."""

    job_id: str
    lat: float
    lng: float
    radius_m: float
    # ``None`` = non si sa ancora (appena armata o ripresa); poi dentro o fuori.
    inside: bool | None
    every_arrival: bool


Verdict = Literal["enter", "inside", "outside", "unsure"]


def accuracy_of(fix: Fix) -> float:
    acc = fix.accuracy_m
    return float(acc) if acc is not None and acc >= 0 else float(UNKNOWN_ACCURACY_M)


def distance_to(zone: Zone, fix: Fix) -> float:
    return distance_m(fix.latitude, fix.longitude, zone.lat, zone.lng)


def is_fresh(fix: Fix, now_ms: int) -> bool:
    """Il fix è di adesso: non più vecchio di ``MAX_FIX_AGE_S``."""
    return now_ms - fix.time_ms <= MAX_FIX_AGE_S * 1000


def _surely_inside(d: float, acc: float, radius: float) -> bool:
    return d <= radius and acc <= radius


def _surely_outside(d: float, acc: float, radius: float) -> bool:
    return d - acc > radius + EXIT_MARGIN_M


def judge(zone: Zone, fix: Fix) -> Verdict:
    """Cosa dice *fix* di *zone*, dato lo stato che la zona aveva.

    * ``enter`` — era fuori ed è dentro: si scatta. Solo da ``inside is False``:
      una zona armata (o ripresa) stando già lì non scatta finché non si esce e
      si rientra, altrimenti «quando arrivo al supermercato» detto *al*
      supermercato suonerebbe subito.
    * ``inside`` / ``outside`` — lo stato nuovo, senza scatto.
    * ``unsure`` — la precisione non basta per dirlo: lo stato non cambia.
    """
    d, acc = distance_to(zone, fix), accuracy_of(fix)
    if _surely_inside(d, acc, zone.radius_m):
        return "enter" if zone.inside is False else "inside"
    if _surely_outside(d, acc, zone.radius_m):
        return "outside"
    return "unsure"


def needs_precise(zones: list[Zone], fix: Fix) -> bool:
    """La lettura da rete non basta: per una zona potrebbe essere dentro, e non si sa.

    Solo per le zone non già dentro (è l'arrivo che conta): il fix tocca il
    cerchio (``d - acc <= raggio``) ma non è abbastanza preciso per dirlo.
    """
    for zone in zones:
        if zone.inside is True:
            continue
        d, acc = distance_to(zone, fix), accuracy_of(fix)
        if d - acc <= zone.radius_m and not _surely_inside(d, acc, zone.radius_m):
            return True
    return False


def next_check_s(zones: list[Zone], fix: Fix | None, *, failures: int = 0) -> float:
    """Fra quanti secondi guardare di nuovo.

    Il tempo minimo per arrivare alla zona più vicina (a ``SPEED_MPS``), fra
    ``MIN_CHECK_S`` e ``MAX_CHECK_S``; ``INSIDE_CHECK_S`` per le zone in cui si
    è già. Senza una lettura (*fix* ``None``) si riprova a ``MIN_CHECK_S``
    raddoppiato a ogni fallimento di fila: al chiuso ogni lettura costa il
    massimo e fallisce lo stesso.
    """
    if fix is None:
        n = max(1, failures)
        return float(min(MAX_CHECK_S, MIN_CHECK_S * 2 ** (n - 1)))
    if not zones:
        return float(MAX_CHECK_S)
    waits: list[float] = []
    for zone in zones:
        if zone.inside is True:
            waits.append(INSIDE_CHECK_S)
            continue
        gap = max(0.0, distance_to(zone, fix) - zone.radius_m - accuracy_of(fix))
        waits.append(gap / SPEED_MPS)
    return float(min(MAX_CHECK_S, max(MIN_CHECK_S, min(waits))))


def is_still(previous: Fix | None, current: Fix | None) -> bool:
    """Due fix di fila nello stesso punto: il telefono non si è mosso."""
    if previous is None or current is None:
        return False
    d = distance_m(previous.latitude, previous.longitude, current.latitude, current.longitude)
    return d <= max(STILL_M, accuracy_of(current))


def fix_record(fix: Fix) -> dict[str, float | int | None]:
    """Il fix come si salva nello stato del job (``zoneFix``) e si dà al turno."""
    return {
        "lat": round(fix.latitude, 6),
        "lng": round(fix.longitude, 6),
        "accuracy": round(fix.accuracy_m, 1) if fix.accuracy_m is not None else None,
        "timeMs": int(fix.time_ms),
    }
