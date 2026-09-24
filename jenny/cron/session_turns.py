"""Shared metadata helpers for scheduled cron session turns."""

from __future__ import annotations

from typing import Any, Mapping

from jenny.cron.types import CronJob
from jenny.session.keys import CRON_SESSION_PREFIX

CRON_TRIGGER_META = "_cron_trigger"
CRON_DEFER_UNTIL_IDLE_META = "_cron_defer_until_session_idle"
CRON_HISTORY_META = "_cron_turn"
# Marca il turno come "monitor": lavoro schedulato che gira in silenzio. La
# soppressione dell'output è governata dalla visibilità del turno
# (:mod:`jenny.session.turn_visibility`); questo flag resta il fatto di dominio
# "questo job è un monitor", che decide prompt, sessione isolata, semantica della
# run record e fan-out proattivo del tool ``message``.
CRON_MONITOR_META = "_cron_monitor"


def cron_trigger(metadata: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Return structured cron trigger metadata when present."""
    raw = (metadata or {}).get(CRON_TRIGGER_META)
    return raw if isinstance(raw, dict) else None


def is_cron_turn(metadata: Mapping[str, Any] | None) -> bool:
    return cron_trigger(metadata) is not None


def defer_cron_until_session_idle(metadata: Mapping[str, Any] | None) -> bool:
    return bool(
        is_cron_turn(metadata)
        and (metadata or {}).get(CRON_DEFER_UNTIL_IDLE_META) is True
    )


def monitor_session_key(job_id: str) -> str:
    """Sessione isolata di un monitor: non sporca la conversazione d'origine."""
    return f"{CRON_SESSION_PREFIX}{job_id}"


def cron_run_id(metadata: Mapping[str, Any] | None) -> str | None:
    trigger = cron_trigger(metadata)
    if not trigger:
        return None
    value = trigger.get("run_id")
    return value if isinstance(value, str) and value else None


def cron_history_overrides(metadata: Mapping[str, Any] | None) -> tuple[str | None, dict[str, Any]]:
    """Return session-history text/metadata overrides for a cron turn."""
    trigger = cron_trigger(metadata)
    if not trigger:
        return None, {}
    persist_content = trigger.get("persist_content")
    text = (
        persist_content
        if isinstance(persist_content, str) and persist_content.strip()
        else None
    )
    return text, {
        CRON_HISTORY_META: True,
        "cron_job_id": trigger.get("job_id"),
        "cron_job_name": trigger.get("job_name"),
        "cron_run_id": trigger.get("run_id"),
        "cron_prompt_ref": trigger.get("prompt_ref"),
    }


def is_bound_cron_job(job: CronJob) -> bool:
    """True for session-bound cron jobs with complete delivery context."""
    payload = job.payload
    return (
        payload.kind == "agent_turn"
        and bool(payload.session_key)
        and bool(payload.origin_channel)
        and bool(payload.origin_chat_id)
    )
