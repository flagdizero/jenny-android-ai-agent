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
# Quello che il turno dice nella sua chat arriva anche ai canali accoppiati
# (Telegram), come copia ``_mirror``: lo mette ``bound_runner`` sul promemoria di
# un quaderno, e lo leggono il ``ChannelDeliverer`` (il tool ``message``) e lo
# stesso ``bound_runner`` (la risposta finale, v. ``AgentLoop.mirror_delivery``).
CRON_MIRROR_META = "_cron_mirror"
# Il turno si scrive senza la chat recente della sessione in cui gira: lo mette
# ``bound_runner`` sul promemoria di un quaderno, che parla nella chat del
# quaderno ma nasce dal job e dalle pagine, non dall'ultimo discorso. Il
# 25/11/2026 una seduta serale si è aperta con «ci siamo lasciati con una patch
# a metà», perché aveva davanti due ore di lavoro tecnico. Lo legge
# ``AgentLoop._state_build``: cronologia vuota e niente riassunto archiviato.
# La riga del trigger e la risposta si salvano nella sessione come sempre.
CRON_FRESH_START_META = "_cron_fresh_start"


def cron_trigger(metadata: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Return structured cron trigger metadata when present."""
    raw = (metadata or {}).get(CRON_TRIGGER_META)
    return raw if isinstance(raw, dict) else None


def is_cron_turn(metadata: Mapping[str, Any] | None) -> bool:
    return cron_trigger(metadata) is not None


def cron_fresh_start(metadata: Mapping[str, Any] | None) -> bool:
    """Vero per un turno cron che non deve vedere la chat recente (v. ``CRON_FRESH_START_META``)."""
    return bool(
        is_cron_turn(metadata)
        and (metadata or {}).get(CRON_FRESH_START_META) is True
    )


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
