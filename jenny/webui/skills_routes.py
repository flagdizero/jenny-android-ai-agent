"""Adapter di route HTTP per le API Skills della WebUI (estratto da ws_http).

Segue lo stesso pattern di ``WebUISettingsRouter``: gli helper condivisi
(auth/parse/response) arrivano iniettati dal costruttore; ``dispatch`` ritorna
``None`` se il path non è di sua competenza, così l'handler principale può
proseguire con le altre famiglie di route.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any
from urllib.parse import unquote

from loguru import logger
from websockets.http11 import Request as WsRequest
from websockets.http11 import Response

from jenny.channels.http_utils import parse_flag
from jenny.webui.skills_api import (
    update_workspace_skill,
    webui_skills_payload,
)

QueryParams = dict[str, list[str]]


class SkillsRoutes:
    """Route ``/api/webui/skills*`` dietro un confine trasporto-neutro."""

    def __init__(
        self,
        *,
        check_api_token: Callable[[WsRequest], bool],
        json_response: Callable[..., Response],
        error_response: Callable[[int, str | None], Response],
        parse_query: Callable[[str], QueryParams],
        query_first: Callable[[QueryParams, str], str | None],
        skills_workspace_path: Any,
        disabled_skills: set[str],
        log: Any = logger,
    ) -> None:
        self._check_api_token = check_api_token
        self._json = json_response
        self._error = error_response
        self._parse_query = parse_query
        self._query_first = query_first
        self._skills_workspace_path = skills_workspace_path
        self._disabled_skills = disabled_skills
        self._log = log

    def dispatch(self, request: WsRequest, path: str) -> Response | None:
        if path == "/api/webui/skills":
            return self._list(request)
        m = re.match(r"^/api/webui/skills/([^/]+)/update$", path)
        if m:
            return self._update(request, m.group(1))
        return None

    def _list(self, request: WsRequest) -> Response:
        if not self._check_api_token(request):
            return self._error(401, "Unauthorized")
        try:
            return self._json(
                webui_skills_payload(
                    self._skills_workspace_path,
                    disabled_skills=self._disabled_skills,
                )
            )
        except Exception:
            self._log.exception("Skills list failed")
            return self._error(500, "skills list failed")

    def _update(self, request: WsRequest, raw_name: str) -> Response:
        if not self._check_api_token(request):
            return self._error(401, "Unauthorized")
        name = unquote(raw_name)
        if not name or "/" in name or "\\" in name:
            return self._error(400, "invalid skill name")
        query = self._parse_query(request.path)
        description = self._query_first(query, "description")
        content = self._query_first(query, "content")
        disabled_raw = self._query_first(query, "disabled")
        kwargs: dict = {}
        if description is not None:
            kwargs["description"] = unquote(description)
        if content is not None:
            kwargs["content"] = unquote(content)
        if disabled_raw is not None:
            kwargs["disabled"] = parse_flag(disabled_raw)
        if not kwargs:
            return self._error(400, "nothing to update")
        try:
            payload = update_workspace_skill(self._skills_workspace_path, name, **kwargs)
            return self._json(payload)
        except PermissionError as e:
            return self._error(403, str(e))
        except FileNotFoundError as e:
            return self._error(404, str(e))
        except Exception:
            self._log.exception("Skill update failed")
            return self._error(500, "skill update failed")
