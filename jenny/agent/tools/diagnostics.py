"""Diagnostics tool: read recent runtime log lines.

Loguru's default stderr sink ends up in Logcat on Android, unreachable
without adb. A small in-memory ring buffer captures the same stream so the
agent (and the user, through it) can see why a tool failed at runtime.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from loguru import logger

from jenny.agent.tools.base import Tool, tool_parameters
from jenny.agent.tools.schema import IntegerSchema, StringSchema, tool_parameters_schema

_BUFFER_SIZE = 500
_DEFAULT_COUNT = 50
_MAX_COUNT = 200

_LOG_BUFFER: deque[str] = deque(maxlen=_BUFFER_SIZE)
_SINK_ID: int | None = None


def install_log_buffer() -> None:
    """Attach the in-memory ring-buffer sink to loguru (idempotent).

    Called once at gateway startup; keeps the last _BUFFER_SIZE formatted
    log lines available to the get_recent_logs tool.
    """
    global _SINK_ID
    if _SINK_ID is not None:
        return

    def _sink(message: Any) -> None:
        _LOG_BUFFER.append(str(message).rstrip("\n"))

    _SINK_ID = logger.add(_sink, level="DEBUG")


@tool_parameters(
    tool_parameters_schema(
        module_filter=StringSchema(
            "Optional substring to filter log lines by module/message, "
            "e.g. 'android_web'"
        ),
        count=IntegerSchema(
            _DEFAULT_COUNT,
            description=f"Max lines to return (1-{_MAX_COUNT})",
            minimum=1,
            maximum=_MAX_COUNT,
        ),
        required=[],
    )
)
class GetRecentLogsTool(Tool):
    """Return recent runtime log lines from the in-memory buffer."""

    _scopes = {"core", "orchestrator", "subagent"}

    name = "get_recent_logs"
    description = (
        "Read recent runtime log lines (DEBUG and above) from the gateway's "
        "in-memory buffer. Use module_filter to narrow to one subsystem "
        "(e.g. 'android_web' to debug web_search failures). Lines are "
        "returned in chronological order."
    )

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return ctx.config.diagnostics.enable

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls()

    @property
    def read_only(self) -> bool:
        return True

    async def execute(
        self,
        module_filter: str | None = None,
        count: int | None = None,
        **kwargs: Any,
    ) -> str:
        n = min(max(count or _DEFAULT_COUNT, 1), _MAX_COUNT)
        lines = list(_LOG_BUFFER)
        if module_filter:
            needle = module_filter.lower()
            lines = [line for line in lines if needle in line.lower()]
        if not lines:
            if module_filter:
                return f"No recent log lines matching '{module_filter}'."
            return "No recent log lines captured yet."
        return "\n".join(lines[-n:])


# Registrazione esplicita dei tool di questo modulo (Fase 5.3): il
# ToolLoader legge questa lista invece della reflection dir(). Un nuovo
# tool va aggiunto qui esplicitamente.
TOOLS = [GetRecentLogsTool]
