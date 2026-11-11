"""La sentinella delle zone: legge la posizione ogni tanto e fa scattare gli arrivi.

Il modello non controlla niente. Qui il codice legge un punto, lo confronta con
i cerchi delle zone accese (:mod:`jenny.cron.zones`) e, a un arrivo, rende
dovuto il job (``CronService.fire_zone``): lo esegue il timer del cron come
qualunque altro, ed è solo lì che Jenny si sveglia. Senza zone accese la
posizione non si legge mai.

La cadenza la decide la distanza (``zones.next_check_s``): rara se la zona più
vicina è lontana, fitta quando ci si avvicina. Fra un giro e l'altro si dorme su
una sveglia di sistema (``power.WAKE_REQUEST_CODE_ZONE``), perché
``asyncio.sleep`` non avanza a telefono sospeso (v. ``CronService._sleep_or_wake``).

Lo stato «cieco»: dopo un riavvio o un aggiornamento il servizio gira senza il
tipo ``location`` e ogni lettura a schermo spento fallisce, finché l'app non
torna in primo piano. Con zone accese e letture cieche da
``zones.BLIND_NOTICE_AFTER_S``, un avviso all'utente — uno per episodio.

Tutte le dipendenze verso il telefono sono iniettate, così i test girano senza.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from jenny.cron import zones
from jenny.cron.types import CronJob
from jenny.utils.clock import now_ms as _now_ms

# Quanto aspettare una lettura: la rete risponde in pochi secondi, il GPS a
# freddo ne vuole di più (e al chiuso non arriva mai: si paga tutto il tetto).
NETWORK_TIMEOUT_S = 20.0
GPS_TIMEOUT_S = 40.0
# Il wakelock di un giro copre le due letture, con margine.
_ROUND_LOCK_S = NETWORK_TIMEOUT_S + GPS_TIMEOUT_S + 30
# Il riepilogo dei contatori nel log: una volta l'ora (e allo stop).
_STATS_EVERY_MS = 60 * 60 * 1000

# Gli stati della sentinella: ``ok`` legge; ``no_fix`` non riesce a leggere ma
# il telefono potrebbe (al chiuso, in galleria); ``blind`` e ``no_permission``
# non può (v. ``runtime.location.background_state``); ``off`` è spenta.
STATES = ("ok", "no_fix", "blind", "no_permission", "off")


@dataclass
class ZoneStats:
    """I numeri della sentinella: servono alla misura della batteria sul telefono."""

    since_ms: int = field(default_factory=_now_ms)
    rounds: int = 0
    network_reads: int = 0
    gps_reads: int = 0
    reads_ok: int = 0
    reads_failed: int = 0
    radio_s: float = 0.0
    arrivals: int = 0
    wakes: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "sinceMs": self.since_ms, "rounds": self.rounds,
            "networkReads": self.network_reads, "gpsReads": self.gps_reads,
            "readsOk": self.reads_ok, "readsFailed": self.reads_failed,
            "radioSeconds": round(self.radio_s, 1), "arrivals": self.arrivals,
            "wakes": dict(self.wakes),
        }


# La sentinella del gateway, per chi deve chiederle lo stato senza averla in
# mano (lo snapshot dei job della bacheca e dell'officina). Una sola per processo.
_CURRENT: "ZoneWatcher | None" = None


def current_state() -> str | None:
    """Lo stato della sentinella del gateway, o ``None`` se non ce n'è una."""
    return _CURRENT.state if _CURRENT is not None else None


def status() -> dict[str, Any] | None:
    """Stato e contatori della sentinella del gateway, o ``None``."""
    if _CURRENT is None:
        return None
    return {"state": _CURRENT.state, **_CURRENT.stats.as_dict()}


ReadFix = Callable[..., Awaitable[Any]]


class ZoneWatcher:
    """Il giro: scadute via, zone accese, una lettura, i verdetti, la prossima sveglia."""

    def __init__(
        self,
        cron: Any,
        *,
        read_fix: ReadFix,
        background_state: Callable[[], Awaitable[str]],
        enabled: Callable[[], bool],
        schedule_wake: Callable[[int], Awaitable[Any]],
        cancel_wake: Callable[[], Awaitable[Any]],
        notify_blind: Callable[[str], None],
        keep_awake: Callable[..., AbstractAsyncContextManager[Any]] | None = None,
        bind_wake: Callable[[], asyncio.Event | None] | None = None,
        on_state_change: Callable[[str], None] | None = None,
        now_ms: Callable[[], int] = _now_ms,
    ) -> None:
        self._cron = cron
        self._read_fix = read_fix
        self._background_state = background_state
        self._enabled = enabled
        self._schedule_wake = schedule_wake
        self._cancel_wake = cancel_wake
        self._notify_blind = notify_blind
        self._keep_awake = keep_awake
        self._bind_wake = bind_wake
        self._on_state_change = on_state_change
        self._now = now_ms
        self.state = "ok"
        self.stats = ZoneStats(since_ms=now_ms())
        self._failures = 0
        self._blind_since_ms: int | None = None
        self._blind_notified = False
        self._wake_event: asyncio.Event | None = None
        self._poke = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._last_stats_ms = self.stats.since_ms

    # ── vita ────────────────────────────────────────────────────────────────

    async def start(self) -> None:
        global _CURRENT
        _CURRENT = self
        self._wake_event = self._bind_wake() if self._bind_wake is not None else None
        self._task = asyncio.create_task(self._run(), name="zone-watch")

    async def stop(self) -> None:
        global _CURRENT
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 — si sta chiudendo
                pass
            self._task = None
        # La sveglia vive nell'AlarmManager, non nel processo: senza toglierla
        # rialzerebbe il gateway per un giro che nessuno ascolta più.
        try:
            await self._cancel_wake()
        except Exception:  # noqa: BLE001
            logger.opt(exception=True).debug("Zones: wake not cancelled on stop")
        self._log_stats(force=True)
        if _CURRENT is self:
            _CURRENT = None

    def poke(self) -> None:
        """Le zone sono cambiate: un giro adesso. Dal loop del gateway (``CronService``)."""
        self._poke.set()

    # ── il giro ─────────────────────────────────────────────────────────────

    async def _run(self) -> None:
        while True:
            try:
                delay = await self.check_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — un giro rotto non ferma la sentinella
                logger.exception("Zones: watch round failed")
                delay = float(zones.MIN_CHECK_S)
            await self._wait(delay)

    async def check_once(self) -> float | None:
        """Un giro. Ritorna fra quanti secondi rifarlo, o ``None``: solo quando cambia qualcosa."""
        now = self._now()
        await self._cron.drop_expired_zones(now)
        if not self._enabled():
            self._set_state("off")
            return None
        jobs = self._cron.zone_jobs()
        if not jobs:
            # Niente da guardare: niente letture, niente episodio cieco in corso.
            self._failures = 0
            self._end_blind_episode()
            return None
        self.stats.rounds += 1
        armed = [_zone_of(job) for job in jobs]
        fix = await self._read(armed)
        now = self._now()
        if fix is None:
            self._failures += 1
            await self._on_failed_read(now)
            return zones.next_check_s(armed, None, failures=self._failures)

        self._failures = 0
        self._end_blind_episode()
        self._set_state("ok")
        after: list[zones.Zone] = []
        for zone in armed:
            verdict = zones.judge(zone, fix)
            inside = zone.inside
            if verdict == "enter":
                if self._cron.fire_zone(zone.job_id, entered_at_ms=now,
                                        fix=zones.fix_record(fix)):
                    self.stats.arrivals += 1
                inside = True
            elif verdict == "inside" and zone.inside is not True:
                self._cron.set_zone_inside(zone.job_id, True)
                inside = True
            elif verdict == "outside" and zone.inside is not False:
                self._cron.set_zone_inside(zone.job_id, False)
                inside = False
            after.append(zones.Zone(zone.job_id, zone.lat, zone.lng, zone.radius_m,
                                    inside, zone.every_arrival))
        self._log_stats()
        return zones.next_check_s(after, fix)

    async def _read(self, armed: list[zones.Zone]) -> Any:
        """La rete prima; il GPS se la rete non risponde o non basta a decidere."""
        lock = self._keep_awake("zone", timeout_s=_ROUND_LOCK_S) if self._keep_awake else None
        if lock is None:
            return await self._read_both(armed)
        async with lock:
            return await self._read_both(armed)

    async def _read_both(self, armed: list[zones.Zone]) -> Any:
        fix = await self._read_one(low_power=True)
        if fix is None or zones.needs_precise(armed, fix):
            precise = await self._read_one(low_power=False)
            if precise is not None:
                fix = precise
        return fix

    async def _read_one(self, *, low_power: bool) -> Any:
        started = time.monotonic()
        if low_power:
            self.stats.network_reads += 1
        else:
            self.stats.gps_reads += 1
        try:
            fix = await self._read_fix(
                low_power=low_power,
                timeout_s=NETWORK_TIMEOUT_S if low_power else GPS_TIMEOUT_S,
            )
        except Exception:  # noqa: BLE001 — una lettura fallita è un esito, non un guasto
            logger.opt(exception=True).debug("Zones: position read raised")
            fix = None
        self.stats.radio_s += time.monotonic() - started
        if fix is not None and not zones.is_fresh(fix, self._now()):
            fix = None
        if fix is None:
            self.stats.reads_failed += 1
        else:
            self.stats.reads_ok += 1
        return fix

    # ── cieca ───────────────────────────────────────────────────────────────

    async def _on_failed_read(self, now: int) -> None:
        try:
            verdict = await self._background_state()
        except Exception:  # noqa: BLE001
            verdict = "off"
        if verdict not in ("blind", "no_permission"):
            # Il telefono potrebbe leggere: è il posto (al chiuso, in galleria).
            self._end_blind_episode()
            self._set_state("no_fix")
            return
        self._set_state(verdict)
        if self._blind_since_ms is None:
            self._blind_since_ms = now
            logger.warning("Zones: position unreadable in background ({})", verdict)
        if (not self._blind_notified
                and now - self._blind_since_ms >= zones.BLIND_NOTICE_AFTER_S * 1000):
            self._blind_notified = True
            try:
                self._notify_blind(verdict)
            except Exception:  # noqa: BLE001 — l'avviso è una cortesia
                logger.exception("Zones: blind notice not posted")

    def _end_blind_episode(self) -> None:
        self._blind_since_ms = None
        self._blind_notified = False

    def _set_state(self, state: str) -> None:
        if state == self.state:
            return
        logger.info("Zones: watcher {} -> {}", self.state, state)
        self.state = state
        if self._on_state_change is not None:
            try:
                self._on_state_change(state)
            except Exception:  # noqa: BLE001
                logger.exception("Zones: state change not propagated")

    # ── l'attesa ────────────────────────────────────────────────────────────

    async def _wait(self, delay_s: float | None) -> None:
        """Dorme fino al prossimo giro: sveglia di sistema, timer, o un ``poke``."""
        if delay_s is None:
            await self._cancel_wake()
            sleep_s = float(zones.MAX_CHECK_S)
        else:
            await self._schedule_wake(self._now() + int(delay_s * 1000))
            sleep_s = delay_s
        waiters = {
            asyncio.create_task(asyncio.sleep(sleep_s)): "timer",
            asyncio.create_task(self._poke.wait()): "poke",
        }
        if self._wake_event is not None:
            waiters[asyncio.create_task(self._wake_event.wait())] = "alarm"
        try:
            done, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for waiter in waiters:
                waiter.cancel()
        for task in done:
            cause = waiters[task]
            self.stats.wakes[cause] = self.stats.wakes.get(cause, 0) + 1
        # Azzerati prima del giro, non dopo: un poke che arriva durante il giro
        # deve provocarne un altro, non essere mangiato.
        self._poke.clear()
        if self._wake_event is not None:
            self._wake_event.clear()

    def _log_stats(self, *, force: bool = False) -> None:
        now = self._now()
        if not force and now - self._last_stats_ms < _STATS_EVERY_MS:
            return
        self._last_stats_ms = now
        logger.info("Zones: state={} stats={}", self.state, self.stats.as_dict())


def _zone_of(job: CronJob) -> zones.Zone:
    s = job.schedule
    return zones.Zone(
        job_id=job.id,
        lat=float(s.lat or 0.0),
        lng=float(s.lng or 0.0),
        radius_m=float(s.radius_m or zones.DEFAULT_RADIUS_M),
        inside=job.state.zone_inside,
        every_arrival=not job.delete_after_run,
    )


def build_watcher(
    cron: Any, *, on_state_change: Callable[[str], None] | None = None,
) -> ZoneWatcher:
    """La sentinella vera, col telefono: posizione, sveglie, wakelock, avviso."""
    from jenny.runtime import location, power

    def enabled() -> bool:
        if not power.alarms_available():
            # Fuori da Android non c'è posizione da leggere.
            return False
        try:
            from jenny.config.loader import load_config

            return bool(load_config().tools.location.enable)
        except Exception:  # noqa: BLE001 — un config illeggibile non accende il GPS
            return False

    async def schedule(at_ms: int) -> Any:
        return await power.schedule_wake(at_ms, power.WAKE_REQUEST_CODE_ZONE)

    async def cancel() -> Any:
        return await power.cancel_wake(power.WAKE_REQUEST_CODE_ZONE)

    def bind() -> asyncio.Event | None:
        return power.bind_wake_loop("zone") if power.alarms_available() else None

    return ZoneWatcher(
        cron,
        read_fix=location.read_fresh_fix,
        background_state=location.background_state,
        enabled=enabled,
        schedule_wake=schedule,
        cancel_wake=cancel,
        notify_blind=notify_blind,
        keep_awake=power.keep_awake,
        bind_wake=bind,
        on_state_change=on_state_change,
    )


def notify_blind(state: str) -> None:
    """L'avviso a zero token: le zone sono ferme, e cosa fare."""
    from jenny.runtime.notifier import notify_alarm
    from jenny.runtime.user_text import say

    key = "alarm.zones_no_permission" if state == "no_permission" else "alarm.zones_blind"
    notify_alarm(say(key), say("alarm.zones_label"))
