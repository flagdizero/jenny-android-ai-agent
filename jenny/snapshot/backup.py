"""Export/import del backup cifrato e staging dei ripristini.

L'export produce un singolo file ``.jbk``: un container AES-256-GCM (vedi
``crypto.py``) il cui payload in chiaro è uno zip *leggibile* — l'albero del
workspace così com'è (``tree/``) più lo store degli snapshot (``snapshots/``).
Disaster recovery senza Jenny = decifrare e aprire lo zip.

Zip e container passano da file temporanei in staging, mai interi in memoria:
il workspace può pesare centinaia di MB (le foto della chat), e un export che
li teneva in RAM due volte non entrava nell'heap del telefono.

L'import e il ripristino da snapshot non toccano MAI il workspace vivo:
preparano un albero in staging e scrivono il marker; lo swap avviene al boot
successivo (vedi ``restore_marker.py``), dopo il riavvio del processo.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import shutil
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from loguru import logger

from jenny.snapshot.crypto import (
    BACKUP_FILE_EXTENSION,
    decrypt_file,
    encrypt_file,
)
from jenny.snapshot.locations import (
    STAGED_SNAPSHOTS_DIR_NAME,
    STAGED_WORKSPACE_DIR_NAME,
    backup_staging_dir_for,
    runtime_root_for,
)
from jenny.snapshot.types import SnapshotManifest, is_snapshot_id, unsafe_entry_reason
from jenny.utils.clock import now_ms as _now_ms

if TYPE_CHECKING:
    from jenny.snapshot.service import SnapshotService

BACKUP_FORMAT_VERSION = 1
IMPORT_STAGED_FILENAME = f"import{BACKUP_FILE_EXTENSION}"
# I temporanei di export/import: in staging, fuori dal workspace (quindi fuori
# dagli snapshot), e con un suffisso che la rotazione degli export non tocca.
_PART_SUFFIX = ".part"
_EXPORT_ZIP_PART = f"export.zip{_PART_SUFFIX}"
_IMPORT_ZIP_PART = f"import.zip{_PART_SUFFIX}"

# Formati che sono già compressi: deflate non li rimpicciolisce e sul telefono
# costa secondi di CPU per centinaia di MB. Entrano nello zip così come sono.
_STORED_SUFFIXES = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif", ".avif",
    ".mp4", ".m4v", ".mov", ".webm", ".mkv", ".3gp",
    ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".flac",
    ".zip", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".jbk", ".apk",
})


def _compress_type_for(name: str) -> int:
    suffix = PurePosixPath(name).suffix.lower()
    return zipfile.ZIP_STORED if suffix in _STORED_SUFFIXES else zipfile.ZIP_DEFLATED


class BackupError(Exception):
    """Errore utente-visibile delle operazioni di backup (→ HTTP 400)."""


class BackupManager:
    """Orchestra engine, crypto e protocollo di restore."""

    def __init__(self, service: "SnapshotService") -> None:
        self._service = service
        self._engine = service.engine
        self._cfg = service.config
        self._runtime_root = runtime_root_for(self._engine.root)
        self._staging = backup_staging_dir_for(self._engine.root)
        self._lock = asyncio.Lock()

    @property
    def import_staged_path(self) -> Path:
        """Path fisso dove il lato Android deposita il file scelto dall'utente."""
        return self._staging / IMPORT_STAGED_FILENAME

    def list_snapshots(self, limit: int | None = None) -> list[dict[str, Any]]:
        return self._engine.list_snapshots(limit)

    async def create_snapshot(self, label: str | None = None) -> dict[str, Any] | None:
        manifest = await self._service.snapshot_now("manual", label=label)
        return manifest.summary() if manifest else None

    @property
    def retention_max_age_days(self) -> int:
        """Orizzonte di retention corrente in giorni (0 = per sempre)."""
        return self._cfg.retention_max_age_days

    async def set_retention_max_age(self, max_age_days: int) -> dict[str, Any]:
        """Persiste il nuovo orizzonte di retention e lo applica subito."""

        # Passa dal funnel come ogni altra scrittura di config: prima leggeva e
        # riscriveva in un thread separato, dove il lock asincrono non arriva —
        # e una modifica fatta altrove nel frattempo veniva cancellata.
        from jenny.config import store

        def _apply(config: Any) -> None:
            config.snapshots.retention_max_age_days = max_age_days

        await store.mutate(_apply)
        # La config viva del service è lo stesso oggetto costruito al boot:
        # set_retention_max_age la aggiorna e applica retention+gc sotto lock.
        removed = await self._service.set_retention_max_age(max_age_days)
        return {
            "ok": True,
            "retention_max_age_days": max_age_days,
            "removed": removed,
        }

    # -- export -----------------------------------------------------------------

    async def export_backup(self, passphrase: str) -> dict[str, Any]:
        """Crea il file ``.jbk`` cifrato in staging e ne ritorna i riferimenti."""
        if not passphrase:
            raise BackupError("passphrase required")
        async with self._lock:
            # Punto di export fotografato anche nella storia locale.
            await self._service.snapshot_now("pre_export")
            filename = f"jenny-backup-{time.strftime('%Y%m%d-%H%M%S')}{BACKUP_FILE_EXTENSION}"
            path = self._staging / filename
            zip_part = self._staging / _EXPORT_ZIP_PART
            jbk_part = self._staging / f"{filename}{_PART_SUFFIX}"
            await asyncio.to_thread(self._clear_parts)
            try:
                await asyncio.to_thread(self._build_zip, zip_part)
                size = await encrypt_file(
                    passphrase, zip_part, jbk_part, iterations=self._cfg.pbkdf2_iterations
                )
                await asyncio.to_thread(self._replace_staged_exports, jbk_part, path)
            finally:
                await asyncio.to_thread(self._clear_parts)
            logger.info("Backup exported to staging: {} ({} bytes)", filename, size)
            return {
                "staged_path": str(path),
                "suggested_filename": filename,
                "size_bytes": size,
            }

    def _clear_parts(self) -> None:
        """Toglie i temporanei, anche quelli di un processo morto a metà."""
        if not self._staging.is_dir():
            return
        for part in self._staging.glob(f"*{_PART_SUFFIX}"):
            part.unlink(missing_ok=True)

    def _replace_staged_exports(self, part: Path, path: Path) -> None:
        """Promuove il nuovo export e rimuove i precedenti (uno alla volta in staging)."""
        for old in self._staging.glob(f"jenny-backup-*{BACKUP_FILE_EXTENSION}"):
            old.unlink(missing_ok=True)
        os.replace(part, path)

    def _build_zip(self, dest: Path) -> None:
        self._staging.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as archive:
            metadata = {
                "format_version": BACKUP_FORMAT_VERSION,
                "exported_at_ms": _now_ms(),
                "jenny_version": _jenny_version(),
            }
            archive.writestr("metadata.json", json.dumps(metadata, ensure_ascii=False))
            # Il workspace è vivo durante l'export: un file sparito tra lo
            # scan e la write non deve far fallire l'intero backup.
            for rel, full in self._engine.iter_tracked_files():
                try:
                    archive.write(full, f"tree/{rel}", compress_type=_compress_type_for(rel))
                except OSError:
                    logger.warning("Backup export: skipping vanished file {}", rel)
            snapshots_dir = self._engine.snapshots_dir
            if snapshots_dir.is_dir():
                for item in sorted(snapshots_dir.rglob("*")):
                    if item.is_file() and not item.is_symlink():
                        rel = item.relative_to(snapshots_dir).as_posix()
                        try:
                            archive.write(item, f"snapshots/{rel}")
                        except OSError:
                            logger.warning("Backup export: skipping vanished file {}", rel)

    # -- import -----------------------------------------------------------------

    async def stage_import(self, staged_path: str, passphrase: str) -> dict[str, Any]:
        """Decifra e valida un ``.jbk``, prepara staging e marker di restore.

        Raises:
            CryptoAuthError: passphrase errata o file corrotto/non riconosciuto.
            BackupError: contenuto dello zip non valido.
            FileNotFoundError: file staged assente.
        """
        if not passphrase:
            raise BackupError("passphrase required")
        source = self._validated_staged_file(staged_path)
        async with self._lock:
            zip_part = self._staging / _IMPORT_ZIP_PART
            try:
                # Fuori dallo staging: una password sbagliata non svuota l'albero
                # di un ripristino già pronto.
                await decrypt_file(passphrase, source, zip_part)
                metadata = await self._service.stage_restore(
                    lambda _staged: asyncio.to_thread(self._extract_backup, zip_part),
                    source="backup_file",
                )
            finally:
                zip_part.unlink(missing_ok=True)
            logger.info("Backup import staged; restart required to apply")
            return {"metadata": metadata}

    def _validated_staged_file(self, staged_path: str) -> Path:
        from jenny.security.workspace_policy import is_path_within

        candidate = Path(staged_path).resolve()
        if not is_path_within(candidate, self._staging, path_resolved=True):
            raise BackupError("staged file must live inside the backup staging directory")
        if not candidate.is_file():
            raise FileNotFoundError(f"staged backup file not found: {candidate.name}")
        return candidate

    def _extract_backup(self, archive_source: bytes | Path) -> dict[str, Any]:
        """Estrae lo zip decifrato negli staging dir. Ritorna il metadata.

        *archive_source* è il file dello zip; i byte in memoria restano per i
        test, che costruiscono archivi ostili senza passare dal disco.
        """
        staged_ws = self._runtime_root / STAGED_WORKSPACE_DIR_NAME
        staged_snap = self._runtime_root / STAGED_SNAPSHOTS_DIR_NAME
        shutil.rmtree(staged_ws, ignore_errors=True)
        shutil.rmtree(staged_snap, ignore_errors=True)

        try:
            archive = zipfile.ZipFile(
                io.BytesIO(archive_source)
                if isinstance(archive_source, bytes)
                else archive_source
            )
        except zipfile.BadZipFile as exc:
            raise BackupError("decrypted payload is not a valid backup archive") from exc

        with archive:
            names = archive.namelist()
            if "metadata.json" not in names:
                raise BackupError("backup archive is missing metadata.json")
            try:
                metadata = json.loads(archive.read("metadata.json").decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as exc:
                raise BackupError("backup metadata.json is unreadable") from exc

            extracted_tree = 0
            for name in names:
                dest_root: Path | None = None
                if name.startswith("tree/"):
                    dest_root, rel = staged_ws, name[len("tree/"):]
                elif name.startswith("snapshots/"):
                    dest_root, rel = staged_snap, name[len("snapshots/"):]
                else:
                    continue
                if not rel or name.endswith("/"):
                    continue
                # Guardia zip-slip: niente path assoluti né risalite.
                pure = PurePosixPath(rel)
                if pure.is_absolute() or ".." in pure.parts:
                    raise BackupError(f"backup archive contains an unsafe path: {name}")
                target = dest_root / Path(*pure.parts)
                if dest_root is staged_snap and pure.parts[0] == "manifests":
                    data = archive.read(name)
                    problem = _imported_manifest_problem(data, pure)
                    if problem is not None:
                        shutil.rmtree(staged_ws, ignore_errors=True)
                        shutil.rmtree(staged_snap, ignore_errors=True)
                        raise BackupError(f"backup archive {name}: {problem}")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                else:
                    # A flusso: una foto da 20 MB non passa intera in memoria.
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(name) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst, 1 << 20)
                if dest_root is staged_ws:
                    extracted_tree += 1

        if extracted_tree == 0:
            shutil.rmtree(staged_ws, ignore_errors=True)
            shutil.rmtree(staged_snap, ignore_errors=True)
            raise BackupError("backup archive contains no workspace tree")
        return metadata

    # -- restore da storia locale --------------------------------------------------

    async def stage_snapshot_restore(self, snapshot_id: str) -> dict[str, Any]:
        """Prepara il ripristino a uno snapshot della storia locale.

        Raises:
            FileNotFoundError: snapshot inesistente.
        """
        async with self._lock:
            manifest = await asyncio.to_thread(self._engine.load_manifest, snapshot_id)
            await self._service.stage_restore(
                lambda staged: asyncio.to_thread(
                    self._engine.restore_snapshot, snapshot_id, staged
                ),
                source="snapshot",
                snapshot_id=snapshot_id,
            )
            logger.info(
                "Snapshot restore staged ({}); restart required to apply", snapshot_id[:12]
            )
            return {
                "snapshot_id": snapshot_id,
                "created_at_ms": manifest.created_at_ms,
                "file_count": manifest.file_count,
            }


def _imported_manifest_problem(data: bytes, rel: PurePosixPath) -> str | None:
    """Perche' un manifest dello store importato non entra nella storia, o ``None``.

    La guardia zip-slip sopra guarda i nomi dello zip, non i percorsi scritti
    dentro i manifest: quelli entrano nella storia locale al boot
    (``_merge_snapshot_store``) e un ripristino li scriveva sotto lo staging.

    Lo stesso per l'**id**: la retention cancella ``manifests/<id>.json`` con
    l'id letto dentro il manifest, e ``../../workspace/config`` le faceva
    cancellare ``config.json``. Deve avere la forma di un id ed essere il nome
    del file, che sta direttamente in ``manifests/`` (*rel* e' il percorso
    relativo allo store, ``manifests/<id>.json``).
    """
    try:
        manifest = SnapshotManifest.from_dict(json.loads(data.decode("utf-8")))
    except (ValueError, UnicodeDecodeError, KeyError, TypeError, AttributeError):
        return "unsafe or unreadable snapshot manifest"
    if (
        not is_snapshot_id(manifest.id)
        or len(rel.parts) != 2
        or rel.parts[1] != f"{manifest.id}.json"
    ):
        return f"snapshot id {manifest.id[:80]!r} is not an id or not its file name"
    for entry in manifest.files:
        reason = unsafe_entry_reason(entry.path, entry.hash)
        if reason is not None:
            return reason
    return None


def _jenny_version() -> str:
    try:
        from jenny import __version__

        return __version__
    except Exception:
        return "unknown"
