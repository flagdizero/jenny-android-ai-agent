"""Tool ``ui_view``: fa vedere a Jenny la schermata corrente della WebUI.

Modello pull: Jenny non riceve mai il contesto dello schermo di default; quando
le serve chiama questo tool, che interroga il client WebUI da cui è partito il
turno e ne restituisce l'HTML (della vista attiva e — se aperta — della Jenny
app). Il round-trip domanda→risposta passa dal :class:`UiQueryCoordinator`.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from jenny.agent.tools.base import Tool, tool_parameters
from jenny.agent.tools.context import ContextAware, RequestContext, ToolContext
from jenny.agent.tools.result import ToolResult
from jenny.agent.tools.schema import tool_parameters_schema
from jenny.channels.ui_query import UiQueryTimeoutError, UiQueryUnavailableError

# Cap difensivo per blocco HTML nel risultato (sotto la troncatura del runner).
_HTML_CAP = 48 * 1024


def _cap_html(html: str) -> str:
    if len(html) <= _HTML_CAP:
        return html
    return html[:_HTML_CAP] + "\n<!--[truncated]-->"


@tool_parameters(tool_parameters_schema())
class UiViewTool(Tool, ContextAware):
    """Restituisce l'HTML della schermata che l'utente sta guardando."""

    _scopes = {"core", "orchestrator"}

    QUERY_TIMEOUT_S = 6.0

    def __init__(self, ui_query: Any) -> None:
        self._ui_query = ui_query
        self._metadata: ContextVar[dict[str, Any]] = ContextVar("ui_view_metadata", default={})

    @property
    def name(self) -> str:
        return "ui_view"

    @property
    def description(self) -> str:
        return (
            "See what the user is currently looking at in the app's WebUI. Returns the "
            "raw HTML of the active screen (chat, wiki, workspace, apps, settings, graph) "
            "and, when a Jenny app is open, the HTML of that app too — infer the content "
            "and the available actions from it. Use it when the user refers to 'this "
            "screen', 'here', 'what I'm seeing', asks what they can do from the current "
            "view, or when you need to inspect an open app to analyze or edit it. It only "
            "works while the app is open in the foreground: it fails fast when no WebUI "
            "client is attached and times out (~6s) when the app is backgrounded. If it "
            "fails, just ask the user what they see instead of retrying."
        )

    @property
    def read_only(self) -> bool:
        return True

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        # Presenza del servizio, non "client connesso" (che è dinamico per turno).
        return ctx.ui_query_service is not None

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        return cls(ctx.ui_query_service)

    def set_context(self, ctx: RequestContext) -> None:
        self._metadata.set(dict(ctx.metadata or {}))

    async def execute(self, **kwargs: Any) -> ToolResult:
        conn_id = self._metadata.get().get("conn_id")
        if not conn_id:
            return ToolResult.failure(
                "No WebUI client is attached to this turn (the request did not come from "
                "the app UI, e.g. a scheduled/background turn). Ask the user what they see "
                "instead.",
                code="ui_unavailable",
            )

        try:
            payload = await self._ui_query.query(conn_id, timeout_s=self.QUERY_TIMEOUT_S)
        except UiQueryTimeoutError:
            return ToolResult.failure(
                "The WebUI did not reply within ~6s — the app is likely backgrounded or "
                "the screen is off. Ask the user what they see instead.",
                code="ui_timeout",
            )
        except UiQueryUnavailableError as exc:
            return ToolResult.failure(
                f"Could not read the WebUI: {exc}. Ask the user what they see instead.",
                code="ui_unavailable",
            )

        return ToolResult.success(self._render(payload))

    def _render(self, payload: dict[str, Any]) -> str:
        view = payload.get("view") or "unknown"
        drawer = payload.get("drawer")
        lines = [f"view: {view}" + (f" (drawer open: {drawer})" if drawer else "")]
        # La chat aperta sopra la pagina (dal 05/10/2026 il tocco sulla
        # mascotte la porta su): la vista descritta e' quella sotto, che
        # l'utente vede in trasparenza ed e' di solito cio' di cui parla.
        if payload.get("over") == "chat":
            lines.append(
                "your chat with the user is open full screen over this view; the user "
                "still sees this view beneath it, and is probably asking about it."
            )

        app = payload.get("app")
        if isinstance(app, dict):
            lines.append(f"app open: {app.get('name') or app.get('slug')}")
            if not app.get("responded"):
                lines.append(
                    "the open app did not expose its DOM (SDK missing / no reply) — "
                    "describe it from the shell HTML only."
                )
            lines.append(
                "the open app's actions are already available to you as tools named "
                "`<slug>_<action>`."
            )

        html = payload.get("html")
        if isinstance(html, str) and html:
            lines.append("\n--- active view HTML ---")
            lines.append(_cap_html(html))

        app_html = app.get("html") if isinstance(app, dict) else None
        if isinstance(app_html, str) and app_html:
            lines.append("\n--- open app HTML ---")
            lines.append(_cap_html(app_html))

        return "\n".join(lines)


TOOLS = [UiViewTool]
