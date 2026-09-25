"""Servizio di snapshot automatici del workspace.

Trigger di sistema, senza alcun coinvolgimento dell'LLM:

- **debounce**: ogni ``scan_interval_minutes`` un fingerprint economico
  (path, size, mtime) rileva i cambiamenti; lo snapshot scatta quando il
  workspace è rimasto quieto per ``quiet_minutes`` dopo una modifica;
- **daily**: rete di sicurezza se non c'è uno snapshot da più di 24h;
- **espliciti**: ``snapshot_now()`` è invocato dal container (shutdown),
  dal checkpoint pre-Dream e dalle API di backup (pre_restore/pre_export).

Lifecycle modellato su ``jenny/cron/service.py::CronService``: timer
``asyncio`` interno, ``start()``/``stop()``, wiring nel ``GatewayContainer``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from loguru import logger

from jenny.snapshot.engine import SnapshotEngine
from jenny.snapshot.types import SnapshotManifest
from jenny.utils.clock import now_ms as _now_ms

if TYPE_CHECKING:
    from jenny.config.schema import SnapshotConfig

_DAY_MS = 86_400_000


class SnapshotService:
    """Timer di snapshot automatici sopra uno :class:`SnapshotEngine`."""

    def __init__(
        self,
        engine: SnapshotEngine,
        config: "SnapshotConfig",
        *,
        scan_interval_s: float | None = None,
        quiet_s: float | None = None,
    ) -> None:
        self._engine = engine
        self._cfg = config
        # Override in secondi usati SOLO dai test (timer sub-secondo).
        self._scan_interval_s = (
            scan_interval_s if scan_interval_s is not None else config.scan_interval_minutes * 60
        )
        self._quiet_ms = int((quiet_s if quiet_s is not None else config.quiet_minutes * 60) * 1000)

        self._running = False
        self._timer_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._last_fingerprint: dict[str, tuple[int, int]] | None = None
        self._last_change_at_ms: int | None = None
        self._pending_changes = False
        self._last_snapshot_at_ms = 0

    @property
    def engine(self) -> SnapshotEngine:
        return self._engine

    @property
    def config(self) -> "SnapshotConfig":
        return self._cfg

    # -- lifecycle ------------------------------------------------------------

    async def start(self) -> None:
        if not self._cfg.enabled:
            logger.info("Snapshot service disabled by config")
            return
        self._running = True
        # L'ora dell'ultimo snapshot si deriva dall'indice: nessun file di
        # stato separato da tenere allineato.
        head = await asyncio.to_thread(self._engine.list_snapshots, 1)
        self._last_snapshot_at_ms = head[0]["created_at_ms"] if head else 0
        # Pulizia best-effort delle copie di sicurezza lasciate dai restore.
        from jenny.snapshot.locations import runtime_root_for
        from jenny.snapshot.restore_marker import sweep_safety_copies

        await asyncio.to_thread(sweep_safety_copies, runtime_root_for(self._engine.root))
        self._arm_timer()
        logger.info(
            "Snapshot service started (scan every {}s, quiet window {}s, {} snapshot(s) on disk)",
            int(self._scan_interval_s),
            self._quiet_ms // 1000,
            len(self._engine.list_snapshots()),
        )

    def stop(self) -> None:
        self._running = False
        if self._timer_task:
            self._timer_task.cancel()
            self._timer_task = None

    def status(self) -> dict:
        return {
            "enabled": self._cfg.enabled,
            "running": self._running,
            "pending_changes": self._pending_changes,
            "last_snapshot_at_ms": self._last_snapshot_at_ms,
        }

    # -- snapshot espliciti -----------------------------------------------------

    async def snapshot_now(self, trigger: str, label: str | None = None) -> SnapshotManifest | None:
        """Crea subito uno snapshot (None se nulla è cambiato) e applica la retention.

        Serializzato da un lock: trigger concorrenti (timer + shutdown + API)
        non producono mai scan sovrapposti.
        """
        async with self._lock:
            # Il fingerprint si legge PRIMA di creare lo snapshot. Leggerlo dopo
            # assorbe come "già noto" qualunque modifica arrivata *durante* la
            # creazione, che quindi non innesca più il debounce e resta fuori
            # dagli snapshot fino al cambiamento successivo o al safety daily.
            # L'errore opposto è innocuo: se una modifica arriva fra la lettura
            # e la creazione, il tick successivo vede un cambiamento già
            # catturato, `create_snapshot` ritorna None e si spreca uno scan.
            fingerprint = await asyncio.to_thread(self._engine.fingerprint)
            manifest = await asyncio.to_thread(
                self._engine.create_snapshot, trigger=trigger, label=label
            )
            # Anche un no-op conta come "verificato ora": evita che il safety
            # giornaliero riprovi a ogni tick su un workspace immutato.
            self._last_snapshot_at_ms = _now_ms()
            self._pending_changes = False
            if manifest is not None:
                self._last_fingerprint = fingerprint
                removed = await asyncio.to_thread(
                    self._engine.apply_retention,
                    keep_recent=self._cfg.retention_recent,
                    thin_after_days=self._cfg.retention_thin_after_days,
                    max_age_days=self._cfg.retention_max_age_days or None,
                )
                if removed:
                    await asyncio.to_thread(self._engine.gc)
            return manifest

    async def set_retention_max_age(self, max_age_days: int) -> int:
        """Aggiorna l'orizzonte di retention (0 = per sempre) e lo applica subito.

        Ritorna il numero di snapshot rimossi. Serializzato dallo stesso lock
        degli snapshot: niente scritture concorrenti sull'indice.
        """
        async with self._lock:
            self._cfg.retention_max_age_days = max_age_days
            removed = await asyncio.to_thread(
                self._engine.apply_retention,
                keep_recent=self._cfg.retention_recent,
                thin_after_days=self._cfg.retention_thin_after_days,
                max_age_days=max_age_days or None,
            )
            if removed:
                await asyncio.to_thread(self._engine.gc)
            return len(removed)

    # -- timer ------------------------------------------------------------------

    def _arm_timer(self) -> None:
        if self._timer_task:
            self._timer_task.cancel()
        if not self._running:
            return

        async def tick() -> None:
            await asyncio.sleep(self._scan_interval_s)
            if not self._running:
                return
            try:
                await self._scan_tick()
            except Exception:
                logger.exception("Snapshot scan tick failed")
            finally:
                self._arm_timer()

        self._timer_task = asyncio.create_task(tick())

    async def _scan_tick(self) -> None:
        now = _now_ms()
        fingerprint = await asyncio.to_thread(self._engine.fingerprint)

        if self._last_fingerprint is None:
            self._last_fingerprint = fingerprint
            # Primissimo avvio: crea la baseline se non esiste alcuna storia.
            if fingerprint and self._engine.head_id() is None:
                await self.snapshot_now("auto", label="baseline")
            return

        if fingerprint != self._last_fingerprint:
            # Workspace in movimento: registra e aspetta la quiete.
            self._last_fingerprint = fingerprint
            self._last_change_at_ms = now
            self._pending_changes = True
        elif (
            self._pending_changes
            and self._last_change_at_ms is not None
            and now - self._last_change_at_ms >= self._quiet_ms
        ):
            await self.snapshot_now("auto")

        if (
            self._cfg.daily_safety_snapshot
            and now - self._last_snapshot_at_ms >= _DAY_MS
        ):
            await self.snapshot_now("daily")
