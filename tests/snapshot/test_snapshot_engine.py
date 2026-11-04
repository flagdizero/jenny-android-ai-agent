"""Test del motore di snapshot: scan, dedup, esclusioni, restore, integrità."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from jenny.snapshot.engine import SnapshotEngine
from jenny.snapshot.store import (
    BlobCorruptError,
    get_blob,
    iter_blob_hashes,
    object_path,
    put_blob,
)
from jenny.snapshot.types import SnapshotManifest


def _make_workspace(root: Path) -> None:
    (root / "memory").mkdir(parents=True)
    (root / "SOUL.md").write_text("anima", encoding="utf-8")
    (root / "memory" / "MEMORY.md").write_text("# memoria", encoding="utf-8")
    (root / "memory" / "history.jsonl").write_text('{"a":1}\n', encoding="utf-8")


def _engine(root: Path) -> SnapshotEngine:
    return SnapshotEngine(root, root / ".jenny" / "snapshots")


def test_create_list_and_head(tmp_path: Path) -> None:
    _make_workspace(tmp_path)
    engine = _engine(tmp_path)

    manifest = engine.create_snapshot(trigger="manual", label="primo", now_ms=1000)
    assert manifest is not None
    assert manifest.parent is None
    assert manifest.file_count == 3

    listed = engine.list_snapshots()
    assert len(listed) == 1
    assert listed[0]["id"] == manifest.id
    assert listed[0]["label"] == "primo"
    assert engine.head_id() == manifest.id


def test_noop_when_unchanged(tmp_path: Path) -> None:
    _make_workspace(tmp_path)
    engine = _engine(tmp_path)
    first = engine.create_snapshot(trigger="auto", now_ms=1000)
    assert first is not None

    assert engine.create_snapshot(trigger="auto", now_ms=2000) is None
    assert len(engine.list_snapshots()) == 1

    (tmp_path / "SOUL.md").write_text("anima v2", encoding="utf-8")
    second = engine.create_snapshot(trigger="auto", now_ms=3000)
    assert second is not None
    assert second.parent == first.id
    assert len(engine.list_snapshots()) == 2


def test_blob_dedup(tmp_path: Path) -> None:
    _make_workspace(tmp_path)
    (tmp_path / "copia1.txt").write_text("stesso contenuto", encoding="utf-8")
    (tmp_path / "copia2.txt").write_text("stesso contenuto", encoding="utf-8")
    engine = _engine(tmp_path)
    manifest = engine.create_snapshot(trigger="manual", now_ms=1000)
    assert manifest is not None

    hashes = {e.hash for e in manifest.files if e.path.startswith("copia")}
    assert len(hashes) == 1
    # Snapshot successivo senza modifiche ai contenuti: nessun blob nuovo.
    blob_count = sum(1 for _ in iter_blob_hashes(engine.objects_dir))
    (tmp_path / "copia3.txt").write_text("stesso contenuto", encoding="utf-8")
    engine.create_snapshot(trigger="manual", now_ms=2000)
    assert sum(1 for _ in iter_blob_hashes(engine.objects_dir)) == blob_count


def test_exclusions(tmp_path: Path) -> None:
    _make_workspace(tmp_path)
    (tmp_path / "ui" / "assets").mkdir(parents=True)
    (tmp_path / "ui" / "assets" / "big.webp").write_bytes(b"x" * 100)
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "sessions.log").write_text("log", encoding="utf-8")
    (tmp_path / "scratch.tmp").write_text("tmp", encoding="utf-8")
    (tmp_path / "sub" / "__pycache__").mkdir(parents=True)
    (tmp_path / "sub" / "__pycache__" / "mod.pyc").write_bytes(b"pyc")
    (tmp_path / "sub" / "keep.txt").write_text("keep", encoding="utf-8")

    engine = _engine(tmp_path)
    manifest = engine.create_snapshot(trigger="manual", now_ms=1000)
    assert manifest is not None
    paths = {e.path for e in manifest.files}
    assert "sub/keep.txt" in paths
    assert not any(p.startswith(("ui/", "logs/")) for p in paths)
    assert not any("__pycache__" in p or p.endswith(".tmp") for p in paths)


def test_the_copies_a_migration_leaves_are_not_tracked(tmp_path: Path) -> None:
    """``sessions.migrated/`` e ``.jenny/webui.migrated/`` hanno dati che lo
    stato porta già: fuori dalle foto e dal ``.jbk``. Un file che si chiama così
    resta."""
    _make_workspace(tmp_path)
    (tmp_path / "sessions.migrated").mkdir()
    (tmp_path / "sessions.migrated" / "unified_default.jsonl").write_text("{}", encoding="utf-8")
    (tmp_path / ".jenny" / "webui.migrated").mkdir(parents=True)
    (tmp_path / ".jenny" / "webui.migrated" / "websocket_default.jsonl").write_text(
        "{}", encoding="utf-8")
    (tmp_path / "note.migrated").write_text("mia", encoding="utf-8")

    manifest = _engine(tmp_path).create_snapshot(trigger="manual", now_ms=1000)
    assert manifest is not None
    paths = {e.path for e in manifest.files}
    assert not any(".migrated/" in p for p in paths)
    assert "note.migrated" in paths


def test_output_dir_is_tracked(tmp_path: Path) -> None:
    """``output/`` resta DENTRO gli snapshot: contiene lavoro finito, non scarti.

    La stessa lista di esclusioni governa l'albero del ``.jbk`` esportato
    (``backup.py::_build_zip`` itera ``iter_tracked_files``), quindi escluderla
    toglierebbe i deliverable dell'utente dall'unico artefatto che lascia il
    dispositivo. Vedi il commento esteso sotto ``DEFAULT_EXCLUDE_GLOBS``.
    """
    _make_workspace(tmp_path)
    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "ricerca.md").write_text("# risultato", encoding="utf-8")

    engine = _engine(tmp_path)
    manifest = engine.create_snapshot(trigger="pre_dream", now_ms=1000)
    assert manifest is not None
    assert "output/ricerca.md" in {e.path for e in manifest.files}


def test_output_dedups_across_snapshots(tmp_path: Path) -> None:
    """Un deliverable immutato non ripaga i propri byte a ogni snapshot.

    È la metà quantitativa della decisione di tenere ``output/``: il contenuto
    è content-addressed e quindi pagato una volta, mentre a ripetersi è la sola
    voce di manifest.
    """
    _make_workspace(tmp_path)
    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "grosso.md").write_text("x" * 5000, encoding="utf-8")

    engine = _engine(tmp_path)
    assert engine.create_snapshot(trigger="pre_dream", now_ms=1000) is not None
    blobs_after_first = set(iter_blob_hashes(engine.objects_dir))

    # Un file *diverso* forza un secondo snapshot senza toccare il deliverable.
    (tmp_path / "memory" / "MEMORY.md").write_text("# memoria 2", encoding="utf-8")
    second = engine.create_snapshot(trigger="pre_dream", now_ms=2000)
    assert second is not None
    assert "output/grosso.md" in {e.path for e in second.files}

    added = set(iter_blob_hashes(engine.objects_dir)) - blobs_after_first
    assert len(added) == 1  # solo MEMORY.md: il deliverable è stato riusato


def test_store_self_exclusion_with_legacy_runtime_dir(tmp_path: Path) -> None:
    """Lo store non si auto-include nemmeno col runtime dir legacy .minijenny."""
    _make_workspace(tmp_path)
    engine = SnapshotEngine(tmp_path, tmp_path / ".minijenny" / "snapshots")
    first = engine.create_snapshot(trigger="manual", now_ms=1000)
    assert first is not None
    # Il primo snapshot ha creato blob/manifest dentro .minijenny/snapshots:
    # se non fossero esclusi, il secondo snapshot vedrebbe "modifiche".
    second = engine.create_snapshot(trigger="manual", now_ms=2000)
    assert second is None


def test_symlinks_skipped(tmp_path: Path) -> None:
    _make_workspace(tmp_path)
    outside = tmp_path.parent / "fuori.txt"
    outside.write_text("fuori dal workspace", encoding="utf-8")
    os.symlink(outside, tmp_path / "link.txt")
    os.symlink(tmp_path / "memory", tmp_path / "link-dir")

    engine = _engine(tmp_path)
    manifest = engine.create_snapshot(trigger="manual", now_ms=1000)
    assert manifest is not None
    paths = {e.path for e in manifest.files}
    assert "link.txt" not in paths
    assert not any(p.startswith("link-dir/") for p in paths)


def test_restore_roundtrip(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    _make_workspace(ws)
    engine = _engine(ws)
    manifest = engine.create_snapshot(trigger="manual", now_ms=1000)
    assert manifest is not None

    # Modifica + cancellazione dopo lo snapshot.
    (ws / "SOUL.md").write_text("modificata", encoding="utf-8")
    (ws / "memory" / "history.jsonl").unlink()

    dest = tmp_path / "restored"
    restored = engine.restore_snapshot(manifest.id, dest)
    assert restored.id == manifest.id
    assert (dest / "SOUL.md").read_text("utf-8") == "anima"
    assert (dest / "memory" / "history.jsonl").read_text("utf-8") == '{"a":1}\n'
    assert (dest / "memory" / "MEMORY.md").read_text("utf-8") == "# memoria"


def test_unicode_filenames(tmp_path: Path) -> None:
    _make_workspace(tmp_path)
    (tmp_path / "città è già.md").write_text("ünïcòde", encoding="utf-8")
    engine = _engine(tmp_path)
    manifest = engine.create_snapshot(trigger="manual", now_ms=1000)
    assert manifest is not None
    dest = tmp_path.parent / "restored-unicode"
    engine.restore_snapshot(manifest.id, dest)
    assert (dest / "città è già.md").read_text("utf-8") == "ünïcòde"


def test_corrupt_index_is_rebuilt(tmp_path: Path) -> None:
    _make_workspace(tmp_path)
    engine = _engine(tmp_path)
    manifest = engine.create_snapshot(trigger="manual", now_ms=1000)
    assert manifest is not None

    engine.index_path.write_text("{corrotto!!!", encoding="utf-8")
    listed = engine.list_snapshots()
    assert [s["id"] for s in listed] == [manifest.id]
    assert engine.head_id() == manifest.id


def test_read_path_does_not_rewrite_index(tmp_path: Path) -> None:
    """Le letture (list_snapshots/head_id) non devono scrivere l'indice.

    Regressione F54: un lettore concorrente che ricostruisce l'indice su
    disallineamento e lo riscrive entra in gara col writer sul file temporaneo.
    Il percorso di lettura deve ricostruire solo in memoria.
    """
    _make_workspace(tmp_path)
    engine = _engine(tmp_path)
    manifest = engine.create_snapshot(trigger="manual", now_ms=1000)
    assert manifest is not None

    # Indice corrotto su disco: le letture lo ignorano ma non lo riparano.
    engine.index_path.write_text("{corrotto!!!", encoding="utf-8")
    assert [s["id"] for s in engine.list_snapshots()] == [manifest.id]
    assert engine.head_id() == manifest.id
    # File ancora corrotto: nessuna scrittura è avvenuta dal percorso di lettura.
    assert engine.index_path.read_text(encoding="utf-8") == "{corrotto!!!"
    assert list(engine.snapshots_dir.glob("*.tmp")) == []

    # Un writer (create_snapshot) persiste l'indice ricostruito, sotto lock.
    (tmp_path / "SOUL.md").write_text("anima v2", encoding="utf-8")
    second = engine.create_snapshot(trigger="manual", now_ms=2000)
    assert second is not None
    data = json.loads(engine.index_path.read_text(encoding="utf-8"))
    assert {s["id"] for s in data["snapshots"]} == {manifest.id, second.id}


def test_tampered_blob_detected(tmp_path: Path) -> None:
    _make_workspace(tmp_path)
    engine = _engine(tmp_path)
    manifest = engine.create_snapshot(trigger="manual", now_ms=1000)
    assert manifest is not None

    entry = manifest.files[0]
    blob = object_path(engine.objects_dir, entry.hash)
    blob.write_bytes(b"garbage-not-zlib")
    with pytest.raises(BlobCorruptError):
        get_blob(engine.objects_dir, entry.hash)


def test_manifest_roundtrip_serialization(tmp_path: Path) -> None:
    _make_workspace(tmp_path)
    engine = _engine(tmp_path)
    manifest = engine.create_snapshot(trigger="pre_dream", label="checkpoint", now_ms=1234)
    assert manifest is not None

    raw = json.loads((engine.manifests_dir / f"{manifest.id}.json").read_text("utf-8"))
    loaded = SnapshotManifest.from_dict(raw)
    assert loaded.id == manifest.id
    assert loaded.trigger == "pre_dream"
    assert loaded.label == "checkpoint"
    assert loaded.total_bytes == manifest.total_bytes


def test_concurrent_put_blob_same_content(tmp_path: Path) -> None:
    """Regressione: ``put_blob`` usava un nome tmp deterministico.

    Due scrittori concorrenti dello stesso blob condividevano il temporaneo:
    l'``os.replace`` del primo portava via l'inode mentre il secondo ci stava
    ancora scrivendo, con esito ``FileNotFoundError`` o blob troncato
    (``BlobCorruptError`` alla rilettura). Suffisso uuid per chiamata.
    """
    objects_dir = tmp_path / "objects"
    payload = b"contenuto condiviso " * 500
    errors: list[BaseException] = []
    hashes: list[str] = []
    barrier = threading.Barrier(8)

    def _writer() -> None:
        try:
            barrier.wait()
            for _ in range(20):
                hashes.append(put_blob(objects_dir, payload))
        except BaseException as exc:  # noqa: BLE001 - raccolta per l'asserzione
            errors.append(exc)

    threads = [threading.Thread(target=_writer) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert len(set(hashes)) == 1
    # Il blob è integro e nessun temporaneo resta orfano.
    assert get_blob(objects_dir, hashes[0]) == payload
    assert list(objects_dir.rglob("*.tmp")) == []
