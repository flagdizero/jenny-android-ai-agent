"""Route HTTP ``/api/backup/*`` (export/import cifrato + storia snapshot).

Stesso pattern adapter di ``WorkspaceRoutes``/``WikiRoutes``. Il payload delle
operazioni di scrittura viaggia nell'header ``X-Jenny-Backup-Data`` come JSON
UTF-8 codificato base64: il server WebSocket non legge mai i body HTTP
(``websockets.http11.Request`` non li espone) e il base64 evita i problemi
latin-1 degli header con passphrase non-ASCII. La passphrase non transita MAI
nella query string (finirebbe nei log).
"""

from __future__ import annotations

import base64
import binascii
import json
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from loguru import logger
from websockets.http11 import Request as WsRequest
from websockets.http11 import Response

from jenny.channels.http_utils import (
    case_insensitive_header,
    http_error,
    http_json_response,
    parse_query,
    query_first,
)

if TYPE_CHECKING:
    from jenny.config.schema import Config
    from jenny.snapshot.backup import BackupManager

BACKUP_DATA_HEADER = "X-Jenny-Backup-Data"


class BackupRoutes:
    """Route ``/api/backup/{export,import,snapshots,snapshots/*}``."""

    def __init__(
        self,
        *,
        check_api_token: Callable[[WsRequest], bool],
        get_backup_manager: Callable[[], "BackupManager | None"],
        log: Any = logger,
    ) -> None:
        self._check_api_token = check_api_token
        self._get_backup_manager = get_backup_manager
        self._log = log

    async def dispatch(self, request: WsRequest, path: str) -> Response | None:
        if not path.startswith("/api/backup/"):
            return None
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        manager = self._get_backup_manager()
        if manager is None:
            return http_error(503, "backup service unavailable")

        if path == "/api/backup/snapshots":
            return self._snapshots_list(request, manager)
        if path == "/api/backup/export":
            return await self._export(request, manager)
        if path == "/api/backup/import":
            return await self._import(request, manager)
        if path == "/api/backup/exported":
            return await self._note_exported()
        if path == "/api/backup/snapshots/create":
            return await self._snapshot_create(request, manager)
        if path == "/api/backup/snapshots/restore":
            return await self._snapshot_restore(request, manager)
        if path == "/api/backup/snapshots/retention":
            return await self._retention_update(request, manager)
        return None

    # -- helpers --

    def _payload(self, request: WsRequest) -> dict[str, Any] | Response:
        raw = case_insensitive_header(request.headers, BACKUP_DATA_HEADER)
        if not raw:
            return http_error(400, "missing backup data header")
        try:
            data = json.loads(base64.b64decode(raw, validate=True).decode("utf-8"))
        except (binascii.Error, ValueError, UnicodeDecodeError):
            return http_error(400, "invalid backup data header")
        if not isinstance(data, dict):
            return http_error(400, "invalid backup data header")
        return data

    # -- handlers --

    def _snapshots_list(self, request: WsRequest, manager: "BackupManager") -> Response:
        query = parse_query(request.path)
        raw_limit = query_first(query, "limit")
        try:
            limit = max(1, min(int(raw_limit), 500)) if raw_limit else None
        except ValueError:
            limit = None
        snapshots = manager.list_snapshots(limit)
        return http_json_response({
            "snapshots": snapshots,
            "retention_max_age_days": manager.retention_max_age_days,
        })

    async def _retention_update(self, request: WsRequest, manager: "BackupManager") -> Response:
        data = self._payload(request)
        if isinstance(data, Response):
            return data
        raw = data.get("max_age_days")
        # bool è un int in Python: va escluso esplicitamente.
        if isinstance(raw, bool) or not isinstance(raw, int) or not 0 <= raw <= 3650:
            return http_error(400, "max_age_days must be an integer between 0 and 3650")
        try:
            result = await manager.set_retention_max_age(raw)
        except Exception:
            self._log.exception("Retention update failed")
            return http_error(500, "retention update failed")
        return http_json_response(result)

    async def _export(self, request: WsRequest, manager: "BackupManager") -> Response:
        from jenny.snapshot.backup import BackupError
        from jenny.snapshot.crypto_backends.base import CryptoUnavailableError

        data = self._payload(request)
        if isinstance(data, Response):
            return data
        try:
            result = await manager.export_backup(str(data.get("passphrase") or ""))
        except BackupError as exc:
            return http_error(400, str(exc))
        except CryptoUnavailableError as exc:
            return http_error(503, str(exc))
        except Exception:
            self._log.exception("Backup export failed")
            return http_error(500, "backup export failed")
        return http_json_response(result)

    async def _note_exported(self) -> Response:
        """Il client dice che il file cifrato è stato salvato davvero.

        Il gateway non può saperlo da sé: lui prepara il container in staging,
        e a decidere se quel file finisce su disco è il picker SAF, che
        risponde solo alla WebView. Senza questa riga «ultimo backup» non ha
        nessuna fonte — non esiste da nessuna parte, in nessun file.

        Rotta separata e non un ritorno di ``/export`` per la stessa ragione:
        fra le due cose c'è una schermata di sistema che l'utente può
        annullare, e segnare il backup come fatto mentre il file non è stato
        scritto sarebbe la bugia peggiore di questa pagina.
        """
        from jenny.config import store

        adesso = time.time()

        def _apply(config: Config) -> bool:
            if config.snapshots.last_export_at == adesso:
                return False
            config.snapshots.last_export_at = adesso
            return True

        try:
            await store.mutate(_apply)
        except Exception:
            self._log.exception("Recording the backup export failed")
            return http_error(500, "could not record the export")
        return http_json_response({"ok": True, "last_export_at": adesso})

    async def _import(self, request: WsRequest, manager: "BackupManager") -> Response:
        from jenny.snapshot.backup import BackupError
        from jenny.snapshot.crypto_backends.base import CryptoAuthError, CryptoUnavailableError

        data = self._payload(request)
        if isinstance(data, Response):
            return data
        staged_path = str(data.get("staged_path") or manager.import_staged_path)
        try:
            result = await manager.stage_import(staged_path, str(data.get("passphrase") or ""))
        except CryptoAuthError:
            return http_error(400, "invalid_passphrase_or_corrupt")
        except BackupError as exc:
            return http_error(400, str(exc))
        except FileNotFoundError:
            return http_error(404, "staged backup file not found")
        except CryptoUnavailableError as exc:
            return http_error(503, str(exc))
        except Exception:
            self._log.exception("Backup import failed")
            return http_error(500, "backup import failed")
        return http_json_response(
            {"ok": True, "metadata": result["metadata"], "requires_restart": True}
        )

    async def _snapshot_create(self, request: WsRequest, manager: "BackupManager") -> Response:
        data = self._payload(request)
        if isinstance(data, Response):
            return data
        label = str(data.get("label") or "") or None
        try:
            summary = await manager.create_snapshot(label=label)
        except Exception:
            self._log.exception("Manual snapshot failed")
            return http_error(500, "snapshot failed")
        return http_json_response({"ok": True, "snapshot": summary})

    async def _snapshot_restore(self, request: WsRequest, manager: "BackupManager") -> Response:
        data = self._payload(request)
        if isinstance(data, Response):
            return data
        snapshot_id = str(data.get("snapshot_id") or "")
        if not snapshot_id:
            return http_error(400, "snapshot_id required")
        try:
            result = await manager.stage_snapshot_restore(snapshot_id)
        except FileNotFoundError:
            return http_error(404, "snapshot not found")
        except Exception:
            self._log.exception("Snapshot restore staging failed")
            return http_error(500, "snapshot restore failed")
        return http_json_response({"ok": True, "requires_restart": True, **result})
