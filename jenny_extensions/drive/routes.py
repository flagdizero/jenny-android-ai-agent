"""Route ``/api/backup/cloud*``: la copia su Google Drive vista dalla WebUI.

Il core le monta dietro il suo controllo del token (``jenny/extensions.py``,
``webui/ws_http.py``); il payload viaggia nello stesso header delle altre route
backup (``webui/backup_routes.py::decode_backup_data``).
"""

from __future__ import annotations

from functools import partial
from typing import Any

from loguru import logger
from websockets.http11 import Request as WsRequest
from websockets.http11 import Response

from jenny.channels.http_utils import case_insensitive_header, http_error, http_json_response
from jenny.webui.backup_routes import BACKUP_DATA_HEADER, decode_backup_data

PREFIX = "/api/backup/cloud"


class CloudRoutes:
    """La copia esterna: stato, accendi, spegni, un giro, una prova, la password,
    e sul telefono nuovo sblocco, elenco delle copie e ripristino.

    Payload (nell'header come le altre route) per la password e per il
    ripristino; il resto agisce sulla sola copia che esiste, con la chiave che
    il gateway già tiene. La password arriva e riparte solo dentro la
    richiesta: non la si scrive e non la si logga. Le risposte portano sempre
    ``ok`` e, se falso, un ``error`` breve che la WebUI traduce.
    """

    prefix = PREFIX

    def __init__(self, snapshot_service: Any, log: Any = logger) -> None:
        self._snapshot_service = snapshot_service
        self._log = log

    async def dispatch(self, request: WsRequest, path: str) -> Response | None:
        if self._snapshot_service is None:
            return http_error(503, "backup service unavailable")
        from .cloud_service import CloudUnavailableError, cloud_service_for

        cloud = cloud_service_for(self._snapshot_service)
        actions = {
            f"{PREFIX}/disable": cloud.disable,
            f"{PREFIX}/run": cloud.run,
            f"{PREFIX}/verify": cloud.verify,
            f"{PREFIX}/remote": cloud.remote_snapshots,
        }
        with_password = {
            f"{PREFIX}/enable": cloud.enable,
            f"{PREFIX}/password": cloud.set_password,
            f"{PREFIX}/unlock": cloud.unlock,
        }
        if path == PREFIX:
            return http_json_response(cloud.status())
        if path in with_password or path == f"{PREFIX}/restore":
            # «Accendi» senza payload vale «senza password»: riaccendere una
            # copia che ce l'ha già, o sapere se ne serve una.
            if path == f"{PREFIX}/enable" and not case_insensitive_header(
                request.headers, BACKUP_DATA_HEADER
            ):
                data: dict[str, Any] | Response = {}
            else:
                data = decode_backup_data(request)
            if isinstance(data, Response):
                return data
            from jenny.snapshot.types import is_snapshot_id

            if path in with_password:
                password = data.get("password")
                if path == f"{PREFIX}/enable" and password is None:
                    action = cloud.enable
                elif not isinstance(password, str) or not password:
                    return http_error(400, "invalid password")
                else:
                    action = partial(with_password[path], password)
            else:
                snapshot_id = data.get("snapshot_id")
                if not is_snapshot_id(snapshot_id):
                    return http_error(400, "invalid snapshot_id")
                action = partial(cloud.stage_restore, str(snapshot_id))
        else:
            action = actions.get(path)
        if action is None:
            return None
        try:
            return http_json_response(await action())
        except CloudUnavailableError:
            return http_json_response({"ok": False, "error": "unavailable"})
        except Exception:
            self._log.exception("Cloud backup action {} failed", path.rsplit("/", 1)[1])
            return http_error(500, "cloud backup failed")


def make_routers(snapshot_service: Any, log: Any) -> list[Any]:
    return [CloudRoutes(snapshot_service, log)]
