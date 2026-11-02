"""Tests for the gateway entry point."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from jenny.android_entry import MAX_RETRIES, run_gateway
from jenny.config.bootstrap import ensure_minimal_config
from jenny.runtime.context import get_runtime_context


def test_run_gateway_prepares_workspace_and_passes_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """run_gateway should create workspace, sync templates, ensure config,
    and forward host/port/ws_port=port to _run_gateway."""
    mock_run = AsyncMock()

    # Il workspace vive nel RuntimeContext; monkeypatch ripristina la sessione.
    monkeypatch.setattr(get_runtime_context(), "workspace_dir", None)

    with patch("jenny.gateway_runtime._run_gateway", new=mock_run):
        run_gateway(
            str(tmp_path),
            host="127.0.0.1",
            port=18000,
        )

    workspace = tmp_path / "workspace"
    assert workspace.exists()
    assert (workspace / "config.json").exists()
    assert (workspace / "SOUL.md").exists()

    mock_run.assert_awaited_once_with(
        config=None,
        host="127.0.0.1",
        port=18000,
        ws_port=18000,
    )


def test_run_gateway_retries_after_a_system_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """B2: SystemExit non è una Exception. Con il vecchio `except Exception`
    i tre tentativi venivano saltati e run_gateway tornava a Kotlin lasciando
    il servizio in piedi senza agente dietro."""
    monkeypatch.setattr(get_runtime_context(), "workspace_dir", None)
    monkeypatch.setattr("jenny.android_entry.RETRY_DELAY_S", 0)

    calls: list[int] = []

    async def _fake_run(**_kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise SystemExit(2)

    with patch("jenny.gateway_runtime._run_gateway", new=_fake_run):
        run_gateway(str(tmp_path), host="127.0.0.1", port=18001)

    assert len(calls) == 2


def test_run_gateway_reraises_after_max_retries_of_base_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Esaurititi i tentativi la BaseException risale, come per Exception."""
    monkeypatch.setattr(get_runtime_context(), "workspace_dir", None)
    monkeypatch.setattr("jenny.android_entry.RETRY_DELAY_S", 0)

    calls: list[int] = []

    async def _always_exit(**_kwargs):
        calls.append(1)
        raise SystemExit(9)

    with patch("jenny.gateway_runtime._run_gateway", new=_always_exit):
        with pytest.raises(SystemExit):
            run_gateway(str(tmp_path), host="127.0.0.1", port=18002)

    assert len(calls) == MAX_RETRIES


def test_run_gateway_does_not_retry_a_keyboard_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Ctrl-C è volontario: un solo tentativo, poi risale."""
    monkeypatch.setattr(get_runtime_context(), "workspace_dir", None)
    monkeypatch.setattr("jenny.android_entry.RETRY_DELAY_S", 0)

    calls: list[int] = []

    async def _interrupted(**_kwargs):
        calls.append(1)
        raise KeyboardInterrupt

    with patch("jenny.gateway_runtime._run_gateway", new=_interrupted):
        with pytest.raises(KeyboardInterrupt):
            run_gateway(str(tmp_path), host="127.0.0.1", port=18003)

    assert len(calls) == 1


def test_run_gateway_resets_loop_bound_state_before_every_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """I ``reset_*`` girano a ogni tentativo, non una volta prima del ciclo.

    Il primo tentativo lega il lock di ``config.store`` al suo loop (basta un
    secondo scrittore in coda) e muore; il secondo scrive la config. Con il
    reset fuori dal ciclo il secondo ereditava il lock del loop morto e moriva
    con ``bound to a different event loop``, e così il terzo: gateway perso.
    """
    import asyncio

    from jenny.config import store

    monkeypatch.setattr(get_runtime_context(), "workspace_dir", None)
    monkeypatch.setattr("jenny.android_entry.RETRY_DELAY_S", 0)
    config_path = tmp_path / "workspace" / "config.json"

    async def _mutate_with_a_queued_writer() -> None:
        # L'accodamento è l'unica cosa che lega una ``asyncio.Lock`` al loop.
        async with store._LOCK:
            queued = asyncio.create_task(
                store.mutate(lambda _cfg: None, config_path=config_path)
            )
            # Finché lo scrittore non è in coda; se muore subito (lock di un
            # loop morto) non si accoderà mai, e il suo errore esce da ``await``.
            while not getattr(store._LOCK, "_waiters", None) and not queued.done():
                await asyncio.sleep(0)
        await queued

    outcomes: list[str] = []

    async def _fake_run(**_kwargs):
        try:
            await _mutate_with_a_queued_writer()
        except RuntimeError as exc:
            outcomes.append(f"error: {exc}")
            raise
        outcomes.append("ok")
        if len(outcomes) == 1:
            raise RuntimeError("first attempt crashes after binding the lock")

    try:
        with patch("jenny.gateway_runtime._run_gateway", new=_fake_run):
            run_gateway(str(tmp_path), host="127.0.0.1", port=18004)
    finally:
        store.reset_config_store_state()

    assert outcomes == ["ok", "ok"]


def test_ensure_minimal_config_writes_minimal_json(tmp_path: Path):
    """ensure_minimal_config should write a minimal config when missing."""
    ensure_minimal_config(tmp_path)

    path = tmp_path / "config.json"
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["gateway"]["host"] == "127.0.0.1"
    ws = data["websocket"]
    assert ws["enabled"] is True
    assert "channels" not in data


def test_ensure_minimal_config_uses_existing_workspace(tmp_path: Path):
    """The config should land inside the provided workspace directory."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ensure_minimal_config(workspace)
    path = workspace / "config.json"
    assert path.exists()


def test_ensure_minimal_config_leaves_a_config_that_is_not_utf8_to_the_loader(tmp_path: Path):
    """Un byte non UTF-8 non deve far fallire l'avvio qui: il file rotto lo
    recupera (o lo mette da parte) ``config.loader``, che viene dopo."""
    path = tmp_path / "config.json"
    path.write_bytes(b'{"websocket": "caf\xe9"}')

    ensure_minimal_config(tmp_path)

    assert path.read_bytes() == b'{"websocket": "caf\xe9"}'


def test_ensure_minimal_config_is_idempotent(tmp_path: Path):
    """ensure_minimal_config should not overwrite an existing config."""
    ensure_minimal_config(tmp_path)
    path = tmp_path / "config.json"
    original = path.read_text(encoding="utf-8")

    ensure_minimal_config(tmp_path)
    assert path.read_text(encoding="utf-8") == original




def test_loop_bound_reset_includes_the_app_storage_locks(monkeypatch: pytest.MonkeyPatch):
    """Anche i lock per collezione delle Jenny App si rimettono a nuovo.

    Sono ``asyncio.Lock`` di modulo come quello di ``config.store``: un
    tentativo morto li lascerebbe legati al suo loop.
    """
    from jenny.android_entry import _reset_loop_bound_state
    from jenny.apps import storage

    calls: list[str] = []
    monkeypatch.setattr(storage, "reset_storage_locks", lambda: calls.append("apps"))

    _reset_loop_bound_state()

    assert calls == ["apps"]
