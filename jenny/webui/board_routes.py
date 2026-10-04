"""Le letture della bacheca: ``/api/board`` e ``/api/board/card``.

Solo letture, come ogni rotta ``/api/``: questo trasporto non porta un corpo (v.
la docstring di ``webui/commands.py``), e le scritture della bacheca sono
comandi RPC (``board.*``) che passano tutti dall'imbuto
``jenny/board/store.py``.

Lo stesso cancello delle rotte della wiki: token, ``config.wiki.enabled``
fail-closed, e un quaderno che esiste. Una bacheca sta dentro la cartella del
quaderno, quindi il nome del quaderno è il solo indirizzo che serve.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

from websockets.http11 import Request as WsRequest
from websockets.http11 import Response

from jenny.channels.http_utils import (
    http_error,
    http_json_response,
    http_response,
    parse_query,
    query_first,
)

# Le foto delle sezioni: solo immagini, solo da ``raw/photos/``.
_PHOTO_MIMES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                ".webp": "image/webp"}


class BoardRoutes:
    def __init__(
        self,
        *,
        check_api_token: Callable[[WsRequest], bool],
        get_workspace_root: Callable[[], Path],
        check_wiki_enabled: Callable[[], Response | None],
        get_wikis_dir: Callable[[], Path],
    ) -> None:
        self._check_api_token = check_api_token
        self._get_workspace_root = get_workspace_root
        self._check_wiki_enabled = check_wiki_enabled
        self._get_wikis_dir = get_wikis_dir

    async def dispatch(self, request: WsRequest, path: str) -> Response | None:
        if path == "/api/board":
            return await self._board(request)
        if path == "/api/board/card":
            return await self._card(request)
        if path == "/api/board/photo":
            return await self._photo(request)
        return None

    def _project_root(self, request: WsRequest) -> tuple[Path | None, Response | None]:
        if not self._check_api_token(request):
            return None, http_error(401, "Unauthorized")
        err = self._check_wiki_enabled()
        if err:
            return None, err
        from jenny.utils.wiki_paths import discover_wiki_roots

        name = query_first(parse_query(request.path), "wiki") or ""
        if not name:
            return None, http_error(400, "wiki required")
        roots = discover_wiki_roots(self._get_wikis_dir())
        if name not in roots:
            return None, http_error(404, "wiki not found")
        return roots[name], None

    async def _board(self, request: WsRequest) -> Response:
        root, err = self._project_root(request)
        if err:
            return err
        assert root is not None
        from jenny.board.sections import alert_probe
        from jenny.board.view import build_board_view

        workspace = self._get_workspace_root()
        payload: dict[str, Any] = await asyncio.to_thread(
            build_board_view, root, alert_probe=alert_probe(workspace)
        )
        return http_json_response(payload)

    async def _card(self, request: WsRequest) -> Response:
        root, err = self._project_root(request)
        if err:
            return err
        assert root is not None
        from jenny.board.corrections import list_corrections
        from jenny.board.model import is_valid_id
        from jenny.board.sections import section_resolver
        from jenny.board.view import build_card_view

        card_id = query_first(parse_query(request.path), "card") or ""
        if not is_valid_id(card_id):
            return http_error(400, "invalid card")
        workspace = self._get_workspace_root()

        def _build() -> dict[str, Any] | None:
            return build_card_view(
                root,
                card_id,
                sections_resolver=section_resolver(workspace, root),
                corrections=list_corrections(root, card_id),
            )

        payload = await asyncio.to_thread(_build)
        if payload is None:
            return http_error(404, "card not found")
        return http_json_response(payload)

    async def _photo(self, request: WsRequest) -> Response:
        """Una foto di una sezione. La chiede il client con il suo token e ne fa
        un ``blob:`` — un ``<img src>`` non porterebbe l'intestazione."""
        root, err = self._project_root(request)
        if err:
            return err
        assert root is not None
        from jenny.board.sections import PHOTOS_DIR
        from jenny.security.workspace_policy import is_path_within

        rel = query_first(parse_query(request.path), "path") or ""
        photos = root / PHOTOS_DIR
        candidate = root / rel
        mime = _PHOTO_MIMES.get(candidate.suffix.lower())
        if not rel.startswith(PHOTOS_DIR + "/") or mime is None:
            return http_error(400, "invalid photo path")
        if not is_path_within(candidate, photos) or not candidate.is_file():
            return http_error(404, "not found")
        body = await asyncio.to_thread(candidate.read_bytes)
        return http_response(
            body,
            content_type=mime,
            extra_headers=[("Cache-Control", "private, max-age=86400"),
                           ("X-Content-Type-Options", "nosniff")],
        )
