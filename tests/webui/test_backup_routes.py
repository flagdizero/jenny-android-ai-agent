"""Test delle route /api/backup/* (export/import cifrato, storia snapshot)."""

from __future__ import annotations

import base64
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from support.gateway_http import make_handler, make_request

from jenny.config.schema import SnapshotConfig
from jenny.snapshot.engine import SnapshotEngine
from jenny.snapshot.locations import MARKER_FILE_NAME, STAGED_WORKSPACE_DIR_NAME
from jenny.snapshot.service import SnapshotService

pytest.importorskip("cryptography")

_AUTH_SECRET = "test-secret"
_PASSPHRASE = "passphrase di prova àè"


def _make_request(path: str, payload: dict | None = None, token: str | None = _AUTH_SECRET):
    headers = None
    if payload is not None:
        encoded = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
        headers = [("X-Jenny-Backup-Data", encoded)]
    return make_request(path, token, headers)


@pytest.fixture()
def env(tmp_path: Path, monkeypatch):
    """Workspace + servizio snapshot reali su tmp_path, handler HTTP completo."""
    runtime_root = tmp_path / "data"
    workspace = runtime_root / "workspace"
    (workspace / "memory").mkdir(parents=True)
    (workspace / "SOUL.md").write_text("anima", encoding="utf-8")
    (workspace / "memory" / "MEMORY.md").write_text("# memoria", encoding="utf-8")

    # get_workspace_path è usato dall'handler per gli asset statici.
    from jenny.config import paths as paths_mod

    monkeypatch.setattr(paths_mod, "get_workspace_path", lambda: workspace)

    engine = SnapshotEngine(workspace, runtime_root / "snapshots")
    snap_cfg = SnapshotConfig(pbkdf2_iterations=100_000)
    service = SnapshotService(engine, snap_cfg)

    handler = make_handler(workspace / "skills", snapshot_service=service)
    return SimpleNamespace(
        handler=handler,
        service=service,
        engine=engine,
        workspace=workspace,
        runtime_root=runtime_root,
    )


def _json(response) -> dict:
    return json.loads(response.body.decode("utf-8"))


async def test_unauthorized_without_token(env) -> None:
    response = await env.handler.backup_routes.dispatch(
        _make_request("/api/backup/snapshots", token=None), "/api/backup/snapshots"
    )
    assert response.status_code == 401


async def test_unavailable_without_service(tmp_path: Path, monkeypatch) -> None:
    from jenny.config import paths as paths_mod

    monkeypatch.setattr(paths_mod, "get_workspace_path", lambda: tmp_path)
    handler = make_handler(tmp_path / "skills", runtime_model_name=lambda: None)
    response = await handler.backup_routes.dispatch(
        _make_request("/api/backup/snapshots"), "/api/backup/snapshots"
    )
    assert response.status_code == 503


async def test_snapshot_create_and_list(env) -> None:
    create = await env.handler.backup_routes.dispatch(
        _make_request("/api/backup/snapshots/create", {"label": "manuale"}),
        "/api/backup/snapshots/create",
    )
    assert create.status_code == 200
    created = _json(create)["snapshot"]
    assert created["label"] == "manuale"
    assert created["trigger"] == "manual"

    listing = await env.handler.backup_routes.dispatch(
        _make_request("/api/backup/snapshots"), "/api/backup/snapshots"
    )
    snapshots = _json(listing)["snapshots"]
    assert [s["id"] for s in snapshots] == [created["id"]]


async def test_retention_update_persists_and_applies(env, tmp_path, monkeypatch) -> None:
    """La route valida l'input, aggiorna la config viva, persiste su disco e
    applica subito la retention (snapshot oltre l'orizzonte rimossi)."""
    # Config vera su file: la retention passa dal funnel di scrittura, quindi
    # un monkeypatch di load_config/save_config non intercetterebbe più nulla
    # (e non verificherebbe la persistenza reale).
    from jenny.config.loader import load_config, save_config
    from jenny.config.schema import Config
    from jenny.runtime.context import get_runtime_context

    config_path = tmp_path / "retention-config.json"
    save_config(Config(), config_path)
    monkeypatch.setattr(get_runtime_context(), "config_path", config_path)

    # Uno snapshot antico (oltre l'orizzonte) + abbastanza recenti da non
    # farlo rientrare nella protezione keep_recent.
    old = env.engine.create_snapshot(trigger="manual", now_ms=1_000_000)
    assert old is not None
    recent_ids = []
    for i in range(env.service.config.retention_recent):
        (env.workspace / "SOUL.md").write_text(f"v{i}", encoding="utf-8")
        manifest = env.engine.create_snapshot(trigger="manual")
        assert manifest is not None
        recent_ids.append(manifest.id)

    response = await env.handler.backup_routes.dispatch(
        _make_request("/api/backup/snapshots/retention", {"max_age_days": 7}),
        "/api/backup/snapshots/retention",
    )
    assert response.status_code == 200
    body = _json(response)
    assert body["retention_max_age_days"] == 7
    assert body["removed"] == 1
    assert load_config(config_path).snapshots.retention_max_age_days == 7
    assert env.service.config.retention_max_age_days == 7

    listing = await env.handler.backup_routes.dispatch(
        _make_request("/api/backup/snapshots"), "/api/backup/snapshots"
    )
    payload = _json(listing)
    assert payload["retention_max_age_days"] == 7
    kept = {s["id"] for s in payload["snapshots"]}
    assert old.id not in kept
    assert set(recent_ids) <= kept


async def test_retention_update_rejects_bad_values(env) -> None:
    for bad in ("7", -1, 4000, True, None):
        response = await env.handler.backup_routes.dispatch(
            _make_request("/api/backup/snapshots/retention", {"max_age_days": bad}),
            "/api/backup/snapshots/retention",
        )
        assert response.status_code == 400, f"accepted {bad!r}"


async def test_export_then_import_roundtrip(env) -> None:
    export = await env.handler.backup_routes.dispatch(
        _make_request("/api/backup/export", {"passphrase": _PASSPHRASE}),
        "/api/backup/export",
    )
    assert export.status_code == 200
    payload = _json(export)
    staged = Path(payload["staged_path"])
    assert staged.is_file()
    assert payload["suggested_filename"].endswith(".jbk")
    assert payload["size_bytes"] == staged.stat().st_size

    # L'export ha creato lo snapshot pre_export.
    assert any(s["trigger"] == "pre_export" for s in env.engine.list_snapshots())

    # Simula il file scelto dall'utente al posto giusto e importa.
    manager = env.handler._get_backup_manager()
    manager.import_staged_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(staged, manager.import_staged_path)

    imported = await env.handler.backup_routes.dispatch(
        _make_request("/api/backup/import", {"passphrase": _PASSPHRASE}),
        "/api/backup/import",
    )
    assert imported.status_code == 200
    body = _json(imported)
    assert body["ok"] is True
    assert body["requires_restart"] is True
    assert body["metadata"]["format_version"] == 1

    # Staging + marker pronti per lo swap al boot.
    staged_ws = env.runtime_root / STAGED_WORKSPACE_DIR_NAME
    assert (staged_ws / "SOUL.md").read_text("utf-8") == "anima"
    assert (env.runtime_root / MARKER_FILE_NAME).is_file()

    # Lo swap al boot produce il workspace ripristinato.
    from jenny.snapshot.restore_marker import apply_pending_restore

    assert apply_pending_restore(env.runtime_root) is True
    assert (env.runtime_root / "workspace" / "SOUL.md").read_text("utf-8") == "anima"


async def test_import_wrong_passphrase(env) -> None:
    export = await env.handler.backup_routes.dispatch(
        _make_request("/api/backup/export", {"passphrase": _PASSPHRASE}),
        "/api/backup/export",
    )
    manager = env.handler._get_backup_manager()
    manager.import_staged_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(_json(export)["staged_path"]), manager.import_staged_path)

    imported = await env.handler.backup_routes.dispatch(
        _make_request("/api/backup/import", {"passphrase": "sbagliata"}),
        "/api/backup/import",
    )
    assert imported.status_code == 400
    assert b"invalid_passphrase_or_corrupt" in imported.body
    assert not (env.runtime_root / MARKER_FILE_NAME).exists()


async def test_import_rejects_path_outside_staging(env, tmp_path: Path) -> None:
    rogue = tmp_path / "rogue.jbk"
    rogue.write_bytes(b"x")
    imported = await env.handler.backup_routes.dispatch(
        _make_request(
            "/api/backup/import", {"passphrase": "x", "staged_path": str(rogue)}
        ),
        "/api/backup/import",
    )
    assert imported.status_code == 400


async def test_import_missing_file(env) -> None:
    imported = await env.handler.backup_routes.dispatch(
        _make_request("/api/backup/import", {"passphrase": "x"}),
        "/api/backup/import",
    )
    assert imported.status_code == 404


async def test_export_requires_passphrase(env) -> None:
    export = await env.handler.backup_routes.dispatch(
        _make_request("/api/backup/export", {"passphrase": ""}),
        "/api/backup/export",
    )
    assert export.status_code == 400


async def test_missing_data_header(env) -> None:
    export = await env.handler.backup_routes.dispatch(
        _make_request("/api/backup/export"), "/api/backup/export"
    )
    assert export.status_code == 400


async def test_snapshot_restore_stages_and_marks(env) -> None:
    manifest = env.engine.create_snapshot(trigger="manual", now_ms=1000)
    (env.workspace / "SOUL.md").write_text("modificata dopo", encoding="utf-8")

    restore = await env.handler.backup_routes.dispatch(
        _make_request("/api/backup/snapshots/restore", {"snapshot_id": manifest.id}),
        "/api/backup/snapshots/restore",
    )
    assert restore.status_code == 200
    body = _json(restore)
    assert body["requires_restart"] is True
    assert body["snapshot_id"] == manifest.id

    # pre_restore fotografato, staging materializzato dallo snapshot.
    assert any(s["trigger"] == "pre_restore" for s in env.engine.list_snapshots())
    staged_ws = env.runtime_root / STAGED_WORKSPACE_DIR_NAME
    assert (staged_ws / "SOUL.md").read_text("utf-8") == "anima"
    assert (env.runtime_root / MARKER_FILE_NAME).is_file()


async def test_snapshot_restore_unknown_id(env) -> None:
    restore = await env.handler.backup_routes.dispatch(
        _make_request("/api/backup/snapshots/restore", {"snapshot_id": "0" * 64}),
        "/api/backup/snapshots/restore",
    )
    assert restore.status_code == 404


# -- backend crypto indisponibile → 503 -------------------------------------------


def _break_crypto_backend(monkeypatch) -> None:
    """Simula l'assenza di un backend crypto utilizzabile."""
    from jenny.snapshot import crypto as crypto_mod
    from jenny.snapshot.crypto_backends.base import CryptoUnavailableError

    def boom():
        raise CryptoUnavailableError("no crypto backend in this environment")

    monkeypatch.setattr(crypto_mod, "get_crypto_backend", boom)


async def test_export_crypto_unavailable_maps_to_503(env, monkeypatch) -> None:
    _break_crypto_backend(monkeypatch)
    response = await env.handler.backup_routes.dispatch(
        _make_request("/api/backup/export", {"passphrase": "x"}), "/api/backup/export"
    )
    assert response.status_code == 503


async def test_import_crypto_unavailable_maps_to_503(env, monkeypatch) -> None:
    manager = env.handler._get_backup_manager()
    manager.import_staged_path.parent.mkdir(parents=True, exist_ok=True)
    manager.import_staged_path.write_bytes(b"contenuto qualunque")
    _break_crypto_backend(monkeypatch)
    response = await env.handler.backup_routes.dispatch(
        _make_request("/api/backup/import", {"passphrase": "x"}), "/api/backup/import"
    )
    assert response.status_code == 503


# -- errori inattesi → 500 (mai un traceback al client) ----------------------------


async def _dispatch_with_broken_manager(env, monkeypatch, method: str, path: str, payload: dict):
    manager = env.handler._get_backup_manager()

    async def boom(*_args, **_kwargs):
        raise RuntimeError("guasto inatteso")

    monkeypatch.setattr(manager, method, boom)
    return await env.handler.backup_routes.dispatch(_make_request(path, payload), path)


async def test_unexpected_export_error_maps_to_500(env, monkeypatch) -> None:
    response = await _dispatch_with_broken_manager(
        env, monkeypatch, "export_backup", "/api/backup/export", {"passphrase": "x"}
    )
    assert response.status_code == 500
    assert b"guasto inatteso" not in response.body


async def test_unexpected_import_error_maps_to_500(env, monkeypatch) -> None:
    response = await _dispatch_with_broken_manager(
        env, monkeypatch, "stage_import", "/api/backup/import", {"passphrase": "x"}
    )
    assert response.status_code == 500


async def test_unexpected_snapshot_create_error_maps_to_500(env, monkeypatch) -> None:
    response = await _dispatch_with_broken_manager(
        env, monkeypatch, "create_snapshot", "/api/backup/snapshots/create", {}
    )
    assert response.status_code == 500


async def test_unexpected_snapshot_restore_error_maps_to_500(env, monkeypatch) -> None:
    response = await _dispatch_with_broken_manager(
        env,
        monkeypatch,
        "stage_snapshot_restore",
        "/api/backup/snapshots/restore",
        {"snapshot_id": "abc"},
    )
    assert response.status_code == 500


# -- clamp del parametro ?limit -----------------------------------------------------


async def test_snapshots_list_limit_clamped(env) -> None:
    for version in range(3):
        (env.workspace / "SOUL.md").write_text(f"v{version}", encoding="utf-8")
        env.engine.create_snapshot(trigger="manual", now_ms=1000 + version)

    async def _list(path: str) -> list:
        # dispatch riceve il path già privato della query (come fa l'handler).
        response = await env.handler.backup_routes.dispatch(
            _make_request(path), "/api/backup/snapshots"
        )
        assert response.status_code == 200
        return _json(response)["snapshots"]

    assert len(await _list("/api/backup/snapshots?limit=2")) == 2
    # 0 e negativi vengono clampati a 1, non passati all'engine.
    assert len(await _list("/api/backup/snapshots?limit=0")) == 1
    assert len(await _list("/api/backup/snapshots?limit=-5")) == 1
    # Un limit non numerico viene ignorato (nessun 500): lista completa.
    assert len(await _list("/api/backup/snapshots?limit=abc")) == 3
    assert len(await _list("/api/backup/snapshots?limit=9999")) == 3


# ── «Quando l'hai esportato l'ultima volta» ─────────────────────────────────


async def test_the_export_record_is_written_by_the_client_and_not_by_the_export(
    env,
) -> None:
    """«Ultimo backup: ieri alle 23:10» non aveva nessuna fonte.

    E non poteva averla dal lato che prepara il file: il gateway cifra il
    container e lo lascia in staging, poi si apre il picker SAF di sistema — e
    se quel file finisca su disco lo sa solo il client, che riceve la risposta
    del picker. Fra le due cose c'è uno schermo annullabile, quindi segnare il
    backup come fatto quando il container è pronto sarebbe falso proprio nel
    caso in cui l'utente ha detto di no.
    """
    from jenny.config.loader import load_config

    # La fixture ha gia' puntato il workspace su tmp_path: `config.json` sta
    # li' dentro, ed e' quello che il funnel di `store.mutate` scrive.
    config_path = env.workspace / "config.json"

    assert load_config(config_path).snapshots.last_export_at == 0, "parte da «mai»"

    # Preparare il container non scrive niente: l'utente non ha ancora visto
    # il picker.
    export = await env.handler.backup_routes.dispatch(
        _make_request("/api/backup/export", {"passphrase": _PASSPHRASE}),
        "/api/backup/export",
    )
    assert export.status_code == 200
    assert load_config(config_path).snapshots.last_export_at == 0, (
        "il record è stato scritto prima che il file fosse salvato"
    )

    # È il client a dire che il file c'è.
    noted = await env.handler.backup_routes.dispatch(
        _make_request("/api/backup/exported"), "/api/backup/exported"
    )
    assert noted.status_code == 200
    quando = load_config(config_path).snapshots.last_export_at
    assert quando > 0
    assert _json(noted)["last_export_at"] == pytest.approx(quando)


async def test_the_export_record_needs_a_token(env) -> None:
    response = await env.handler.backup_routes.dispatch(
        _make_request("/api/backup/exported", token=None), "/api/backup/exported"
    )
    assert response.status_code == 401
