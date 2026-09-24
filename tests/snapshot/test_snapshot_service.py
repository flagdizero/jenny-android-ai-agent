"""Test del servizio di snapshot automatici (timer, debounce, safety)."""

from __future__ import annotations

import asyncio
import functools
import time
from pathlib import Path

from support.aio import wait_until

from jenny.config.schema import SnapshotConfig
from jenny.snapshot.engine import SnapshotEngine
from jenny.snapshot.service import SnapshotService

# Timeout largo: il predicato esce subito quando è vero, ma sotto carico
# esterno (CPU satura) i timer sub-secondo del servizio possono slittare.
# Le scadenze di questo file: 15 s, controllando ogni 20 ms.
_wait_until = functools.partial(wait_until, timeout=15.0, interval=0.02)


async def _wait_for_baseline(engine: SnapshotEngine, service: SnapshotService) -> None:
    """Aspetta la baseline *e* la contabilità che il servizio le fa attorno.

    Lo snapshot diventa visibile su disco dentro ``create_snapshot``, mentre
    ``_last_snapshot_at_ms`` viene aggiornato solo quando ``snapshot_now``
    riprende dopo il thread. Guardare solo il disco fa ripartire il test *in
    mezzo* allo snapshot, e ogni stato che il test tocca lì viene poi
    sovrascritto dal servizio: sotto carico è esattamente come il safety daily
    perdeva il suo riavvolgimento e il test moriva sul timeout.
    """
    await _wait_until(
        lambda: len(engine.list_snapshots()) == 1
        and service.status()["last_snapshot_at_ms"] > 0
    )


def _setup(tmp_path: Path, **cfg_kwargs) -> tuple[SnapshotEngine, SnapshotService, Path]:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "SOUL.md").write_text("anima", encoding="utf-8")
    engine = SnapshotEngine(ws, ws / ".jenny" / "snapshots")
    config = SnapshotConfig(**cfg_kwargs)
    service = SnapshotService(engine, config, scan_interval_s=0.05, quiet_s=0.15)
    return engine, service, ws


async def test_baseline_snapshot_on_first_start(tmp_path: Path) -> None:
    engine, service, _ws = _setup(tmp_path)
    await service.start()
    try:
        await _wait_for_baseline(engine, service)
        assert engine.list_snapshots()[0]["label"] == "baseline"
    finally:
        service.stop()


async def test_debounce_snapshots_after_quiet_window(tmp_path: Path) -> None:
    engine, service, ws = _setup(tmp_path)
    await service.start()
    try:
        await _wait_for_baseline(engine, service)
        (ws / "SOUL.md").write_text("anima modificata", encoding="utf-8")
        # Il tick rileva la modifica, poi serve la finestra di quiete.
        await _wait_until(
            lambda: any(s["trigger"] == "auto" and s["label"] is None
                        for s in engine.list_snapshots())
        )
        assert len(engine.list_snapshots()) == 2
    finally:
        service.stop()


async def test_no_snapshot_without_changes(tmp_path: Path) -> None:
    engine, service, _ws = _setup(tmp_path)
    await service.start()
    try:
        await _wait_for_baseline(engine, service)
        await asyncio.sleep(0.4)  # diverse finestre di quiete senza modifiche
        assert len(engine.list_snapshots()) == 1
    finally:
        service.stop()


async def test_daily_safety_snapshot_fires_when_overdue(tmp_path: Path) -> None:
    engine, service, ws = _setup(tmp_path)
    await service.start()
    try:
        await _wait_for_baseline(engine, service)
        # Simula "ultimo snapshot 25 ore fa" + una modifica mai quietata
        # (il fingerprint cambia a ogni tick non serve: basta una modifica
        # singola, il daily scatta indipendentemente dalla quiete).
        (ws / "SOUL.md").write_text("cambio", encoding="utf-8")
        service._last_snapshot_at_ms = int(time.time() * 1000) - 25 * 3_600_000
        await _wait_until(
            lambda: any(s["trigger"] == "daily" for s in engine.list_snapshots())
        )
    finally:
        service.stop()


async def test_snapshot_now_works_without_timer(tmp_path: Path) -> None:
    engine, service, _ws = _setup(tmp_path)
    manifest = await service.snapshot_now("pre_dream", label="checkpoint")
    assert manifest is not None
    assert engine.list_snapshots()[0]["trigger"] == "pre_dream"
    # Invariato: no-op.
    assert await service.snapshot_now("shutdown") is None


async def test_snapshot_now_applies_retention(tmp_path: Path) -> None:
    engine, service, ws = _setup(tmp_path, retention_recent=1, retention_thin_after_days=1)
    # Prepara storia vecchia direttamente sull'engine (timestamp sintetici).
    for version in range(3):
        (ws / "SOUL.md").write_text(f"v{version}", encoding="utf-8")
        engine.create_snapshot(trigger="auto", now_ms=1_000_000 + version * 1000)
    (ws / "SOUL.md").write_text("finale", encoding="utf-8")
    manifest = await service.snapshot_now("manual")
    assert manifest is not None
    kept = engine.list_snapshots()
    # Retention: resta il recente + 1/giorno per la giornata vecchia.
    assert len(kept) == 2


async def test_disabled_service_does_not_start(tmp_path: Path) -> None:
    engine, service, ws = _setup(tmp_path, enabled=False)
    await service.start()
    try:
        (ws / "SOUL.md").write_text("cambiata", encoding="utf-8")
        await asyncio.sleep(0.3)
        assert engine.list_snapshots() == []
        assert service.status()["running"] is False
    finally:
        service.stop()


async def test_snapshot_now_serialized_by_lock(tmp_path: Path, monkeypatch) -> None:
    """Trigger concorrenti (timer + shutdown + API) non producono scan sovrapposti."""
    engine, service, _ws = _setup(tmp_path)
    active = 0
    max_active = 0

    def slow_create(**_kwargs):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        time.sleep(0.05)
        active -= 1
        return None

    monkeypatch.setattr(engine, "create_snapshot", slow_create)
    await asyncio.gather(
        service.snapshot_now("manual"),
        service.snapshot_now("shutdown"),
        service.snapshot_now("pre_dream"),
    )
    assert max_active == 1


async def test_start_sweeps_old_safety_copies(tmp_path: Path) -> None:
    """All'avvio il servizio ripulisce le copie di sicurezza scadute dei restore."""
    import os

    from jenny.snapshot.locations import SAFETY_DIR_PREFIX, runtime_root_for

    engine, service, ws = _setup(tmp_path)
    runtime_root = runtime_root_for(ws)
    old = runtime_root / f"{SAFETY_DIR_PREFIX}vecchia"
    old.mkdir()
    ancient = time.time() - 30 * 86_400
    os.utime(old, (ancient, ancient))
    recent = runtime_root / f"{SAFETY_DIR_PREFIX}recente"
    recent.mkdir()

    await service.start()
    try:
        assert not old.exists()
        assert recent.exists()
    finally:
        service.stop()


async def test_status_reports_full_state(tmp_path: Path) -> None:
    engine, service, _ws = _setup(tmp_path)
    assert service.status() == {
        "enabled": True,
        "running": False,
        "pending_changes": False,
        "last_snapshot_at_ms": 0,
    }
    await service.snapshot_now("manual")
    status = service.status()
    assert status["last_snapshot_at_ms"] > 0
    assert status["pending_changes"] is False
    assert status["running"] is False


async def test_stop_cancels_timer(tmp_path: Path) -> None:
    engine, service, ws = _setup(tmp_path)
    await service.start()
    await _wait_until(lambda: len(engine.list_snapshots()) == 1)
    service.stop()
    (ws / "SOUL.md").write_text("dopo lo stop", encoding="utf-8")
    await asyncio.sleep(0.4)
    assert len(engine.list_snapshots()) == 1
