"""Structured progress-event helpers shared by agent runtimes."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from jenny.agent.hook import AgentHookContext


def on_progress_accepts_tool_events(cb: Callable[..., Any]) -> bool:
    return _on_progress_accepts(cb, "tool_events")


def on_progress_accepts_file_edit_events(cb: Callable[..., Any]) -> bool:
    return _on_progress_accepts(cb, "file_edit_events")


def on_progress_accepts_reasoning(cb: Callable[..., Any]) -> bool:
    return _on_progress_accepts(cb, "reasoning")


def _on_progress_accepts(cb: Callable[..., Any], name: str) -> bool:
    try:
        sig = inspect.signature(cb)
    except (TypeError, ValueError):
        return False
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
        return True
    return name in sig.parameters


async def invoke_on_progress(
    on_progress: Callable[..., Awaitable[None]],
    content: str,
    *,
    tool_hint: bool = False,
    tool_events: list[dict[str, Any]] | None = None,
) -> None:
    if tool_events and on_progress_accepts_tool_events(on_progress):
        await on_progress(content, tool_hint=tool_hint, tool_events=tool_events)
        return
    await on_progress(content, tool_hint=tool_hint)


async def invoke_file_edit_progress(
    on_progress: Callable[..., Awaitable[None]],
    file_edit_events: list[dict[str, Any]],
) -> None:
    if not file_edit_events or not on_progress_accepts_file_edit_events(on_progress):
        return
    await on_progress("", file_edit_events=file_edit_events)


# Da quale famiglia e' ogni strumento: la parola con cui la casa dice cosa sta
# facendo Jenny («Legge», «Cerca», «Scrive»...). La decide il filo, e la casa
# la mostra: fino al 02/11/2026 la tabella stava solo nella WebUI
# (`home-activity.js::FAMILY_BY_TOOL`), e diciotto strumenti nati dopo non
# c'erano. La' resta come ripiego, per gli eventi registrati prima.
#
# ``busy`` e' il vocabolario generico («mi do da fare»): per chi non lavora su
# qualcosa che si possa nominare. ``cron`` e ``message`` ci stanno apposta: non
# mettono al lavoro nessuno, e come «delega» dicevano «chiamo una mini-me»
# mentre Jenny fissava un promemoria. Un nome che non e' qui vale ``busy``.
TOOL_FAMILIES: dict[str, str] = {
    # Legge: guarda qualcosa che gia' esiste, senza toccarlo.
    **dict.fromkeys(
        (
            "read_file", "list_dir", "get_source", "recall", "recall_history", "memory",
            "ui_view", "my", "subagent_status", "list_exec_sessions", "update_status",
            "ssh_hosts", "get_recent_logs", "notebook", "screen",
        ),
        "read",
    ),
    # Cerca: non sa ancora dove sia.
    **dict.fromkeys(("grep", "find_files", "web_search", "find_place"), "search"),
    # Scrive: lascia un segno.
    **dict.fromkeys(
        ("write_file", "edit_file", "apply_patch", "journal_append", "board", "app"), "write"
    ),
    # Esce: qualcosa fuori da questo telefono.
    **dict.fromkeys(
        (
            "web_fetch", "browser_open", "browser_read", "browser_do", "browser_close",
            "browser_snapshot", "download_file", "ssh_exec", "ssh_job", "ssh_transfer",
            "get_location", "install_update",
        ),
        "out",
    ),
    # Esegue: fa girare qualcosa.
    **dict.fromkeys(("python_exec", "write_stdin"), "run"),
    # Delega: manda una mini-me. Solo i subagent.
    **dict.fromkeys(
        ("spawn", "subagent_send", "subagent_cancel", "subagent_restart"), "delegate"
    ),
    # Il resto: niente da nominare.
    **dict.fromkeys(
        (
            "cron", "message", "nothing_to_report", "suggest_notebook", "plan_propose",
            "plan_step_done", "plan_step_skip", "plan_blocked",
        ),
        "busy",
    ),
}


def tool_family(name: Any) -> str:
    """La famiglia di *name* (``read``, ``search``, ...), ``busy`` se non si sa."""
    return TOOL_FAMILIES.get(name, "busy") if isinstance(name, str) else "busy"


def _tool_event_arguments(tool_call: Any) -> dict[str, Any]:
    arguments = getattr(tool_call, "arguments", {}) or {}
    return arguments if isinstance(arguments, dict) else {}


def build_tool_event_start_payload(tool_call: Any) -> dict[str, Any]:
    return {
        "version": 1,
        "phase": "start",
        "call_id": str(getattr(tool_call, "id", "") or ""),
        "name": getattr(tool_call, "name", ""),
        "family": tool_family(getattr(tool_call, "name", "")),
        "arguments": _tool_event_arguments(tool_call),
        "result": None,
        "error": None,
        "files": [],
        "embeds": [],
    }


def tool_event_result_extras(result: Any) -> tuple[list[Any], list[Any]]:
    if not isinstance(result, dict):
        return [], []
    files = result.get("files") if isinstance(result.get("files"), list) else []
    embeds = result.get("embeds") if isinstance(result.get("embeds"), list) else []
    return files, embeds


def build_tool_event_finish_payloads(context: AgentHookContext) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    count = min(len(context.tool_calls), len(context.tool_results), len(context.tool_events))
    for idx in range(count):
        tool_call = context.tool_calls[idx]
        result = context.tool_results[idx]
        event = context.tool_events[idx] if isinstance(context.tool_events[idx], dict) else {}
        status = event.get("status")
        phase = "end" if status == "ok" else "error"
        files, embeds = tool_event_result_extras(result)
        payload = {
            "version": 1,
            "phase": phase,
            "call_id": str(getattr(tool_call, "id", "") or ""),
            "name": getattr(tool_call, "name", ""),
            "family": tool_family(getattr(tool_call, "name", "")),
            "arguments": _tool_event_arguments(tool_call),
            "result": result if phase == "end" else None,
            "error": None,
            "files": files,
            "embeds": embeds,
        }
        if phase == "error":
            if isinstance(result, str) and result.strip():
                payload["error"] = result.strip()
            else:
                payload["error"] = str(event.get("detail") or "Tool execution failed")
        payloads.append(payload)
    return payloads
