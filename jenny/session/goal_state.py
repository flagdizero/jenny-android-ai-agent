"""Session metadata helpers for sustained goals (e.g. ``long_task`` / ``complete_goal``).

Tools set ``metadata[GOAL_STATE_KEY]``. Callers use ``goal_state_runtime_lines``
and ``runner_wall_llm_timeout_s`` without importing tool implementations.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Mapping, MutableMapping

from jenny.session.manager import SessionManager
from jenny.session.plan_probe import plan_running_in_session

GOAL_STATE_KEY = "goal_state"
_MAX_OBJECTIVE_IN_RUNTIME = 4000
# Campi del blob che marcano «goal vivo, ma in attesa di una risposta dell'utente».
# Restano dentro un goal ``active``: lo stato non cambia, così wall-timeout,
# continuazione interna, expire e ``/stop`` si comportano esattamente come prima.
_AWAITING_INPUT = "awaiting_input"
_AWAITING_SINCE = "awaiting_since"


def goal_state_raw(metadata: Mapping[str, Any] | None) -> Any:
    """Return the session goal blob under :data:`GOAL_STATE_KEY`."""
    if not metadata:
        return None
    return metadata.get(GOAL_STATE_KEY)


def sustained_goal_active(metadata: Mapping[str, Any] | None) -> bool:
    """True when this session has an active sustained objective (``long_task`` bookkeeping)."""
    goal = parse_goal_state(goal_state_raw(metadata))
    return isinstance(goal, dict) and goal.get("status") == "active"


def goal_awaiting_input(metadata: Mapping[str, Any] | None) -> bool:
    """True when the active goal is parked waiting for the user's reply.

    Un goal in attesa è ancora ``active`` (sopravvive, riprende, si può ``/stop``),
    ma non va spronato: ripetergli «continua» produce solo la stessa domanda.
    """
    goal = parse_goal_state(goal_state_raw(metadata))
    if not isinstance(goal, dict) or goal.get("status") != "active":
        return False
    return bool(goal.get(_AWAITING_INPUT))


def mark_goal_awaiting_input(
    metadata: MutableMapping[str, Any] | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Park the active goal: it asked the user something and cannot progress alone.

    Chiamata quando il runner rifiuta la goal-continuation sintetica (v.
    ``AgentRunResult.goal_stalled``). Serve a dare al modello un terzo stato oltre
    «continua» e «complete_goal»: senza di esso l'unica uscita da un'attesa era
    chiudere il goal con un recap falso. Idempotente — ri-parcheggiare non sposta
    ``awaiting_since`` — e no-op se nessun goal è ``active``.

    Returns the updated blob (mutated in ``metadata`` in place) or ``None``.
    """
    if metadata is None:
        return None
    goal = parse_goal_state(goal_state_raw(metadata))
    if not isinstance(goal, dict) or goal.get("status") != "active":
        return None
    if goal.get(_AWAITING_INPUT) and goal.get(_AWAITING_SINCE):
        return goal
    stamp = (_as_naive(now) if now is not None else datetime.now()).isoformat()
    updated = {**goal, _AWAITING_INPUT: True, _AWAITING_SINCE: stamp}
    metadata[GOAL_STATE_KEY] = updated
    return updated


def clear_goal_awaiting_input(metadata: MutableMapping[str, Any] | None) -> dict[str, Any] | None:
    """Resume a parked goal: a real user message ends the wait.

    Call at the START of a turn driven by an actual user message (see
    ``jenny.session.turn_continuation.should_persist_user_message``). Unlike
    :func:`mark_goal_awaiting_input` this also cleans a non-active blob, so a
    completed/expired goal cannot leave the flag behind for the next objective.

    Returns the updated blob (mutated in ``metadata`` in place) or ``None``.
    """
    if metadata is None:
        return None
    goal = parse_goal_state(goal_state_raw(metadata))
    if not isinstance(goal, dict):
        return None
    if _AWAITING_INPUT not in goal and _AWAITING_SINCE not in goal:
        return None
    updated = {k: v for k, v in goal.items() if k not in (_AWAITING_INPUT, _AWAITING_SINCE)}
    metadata[GOAL_STATE_KEY] = updated
    return updated


def cancel_active_goal(metadata: MutableMapping[str, Any] | None) -> dict[str, Any] | None:
    """Reset an active sustained goal to ``cancelled`` after a hard turn cancellation.

    Call this only from a user-initiated hard-cancel path (e.g. ``/stop``), never from
    normal turn completion. Without it, cancelling a hung goal turn leaves the goal
    stuck ``active`` forever, which permanently disables the LLM wall-clock timeout for
    the session (see :func:`runner_wall_llm_timeout_s`) even for unrelated future turns.

    Returns the updated goal blob (and mutates ``metadata[GOAL_STATE_KEY]`` in place)
    when there was an active goal to cancel, or ``None`` when there was nothing to do.
    """
    if metadata is None:
        return None
    goal = parse_goal_state(goal_state_raw(metadata))
    if not isinstance(goal, dict) or goal.get("status") != "active":
        return None
    updated = {**goal, "status": "cancelled", "cancelled_at": datetime.now().isoformat()}
    metadata[GOAL_STATE_KEY] = updated
    return updated


def _as_naive(dt: datetime) -> datetime:
    """Drop tzinfo so comparisons stay consistent with the module's naive-local stamps."""
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return _as_naive(datetime.fromisoformat(value))
    except ValueError:
        return None


def _goal_last_activity(goal: Mapping[str, Any]) -> datetime | None:
    """Most recent activity for an active goal: ``max(started_at, last_turn_at)``.

    ``None`` means neither timestamp is present/valid (a legacy ``active`` blob),
    which callers treat as stale.
    """
    stamps = [
        dt
        for dt in (_parse_iso(goal.get("started_at")), _parse_iso(goal.get("last_turn_at")))
        if dt is not None
    ]
    return max(stamps) if stamps else None


def note_goal_turn(
    metadata: MutableMapping[str, Any] | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Stamp ``last_turn_at`` (ISO) on the active goal after a goal turn completes.

    Sustained goals advance across ordinary turns (including turns that survive a
    restart, see :mod:`jenny.session.turn_continuation`); recording each turn keeps
    :func:`expire_stale_goal` from expiring a goal that is still making progress.
    No-op unless a goal is ``active``. Retro-compatible: an active blob without
    ``last_turn_at`` falls back to ``started_at`` for the inactivity measure, so this
    field is purely additive.

    Returns the updated blob (mutated in ``metadata`` in place) or ``None``.
    """
    if metadata is None:
        return None
    goal = parse_goal_state(goal_state_raw(metadata))
    if not isinstance(goal, dict) or goal.get("status") != "active":
        return None
    stamp = (_as_naive(now) if now is not None else datetime.now()).isoformat()
    updated = {**goal, "last_turn_at": stamp}
    metadata[GOAL_STATE_KEY] = updated
    return updated


def expire_stale_goal(
    metadata: MutableMapping[str, Any] | None,
    *,
    ttl_h: float,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Lazily expire an ``active`` sustained goal idle past ``ttl_h`` hours.

    Inactivity is measured from ``max(started_at, last_turn_at)`` (see
    :func:`note_goal_turn`), never from total duration: a goal that keeps advancing
    stamps ``last_turn_at`` every turn and therefore never expires. A legacy ``active``
    blob without either timestamp is anomalous after a restart and is treated as stale.

    Without this, a process killed mid-goal (the normal case on Android) leaves the
    goal stuck ``active`` forever, permanently disabling the LLM wall-clock timeout for
    the session (see :func:`runner_wall_llm_timeout_s`) even for unrelated future turns.
    Call at the START of a turn (lazy repair; no session-load hook). ``now`` is injectable
    for tests (default: current local time, matching the module's other timestamps).

    Returns the expired blob (mutated in ``metadata`` in place) or ``None`` when there
    was nothing to expire.
    """
    if metadata is None:
        return None
    goal = parse_goal_state(goal_state_raw(metadata))
    if not isinstance(goal, dict) or goal.get("status") != "active":
        return None
    now_dt = _as_naive(now) if now is not None else datetime.now()
    last_activity = _goal_last_activity(goal)
    if last_activity is not None and (now_dt - last_activity).total_seconds() <= ttl_h * 3600.0:
        return None
    updated = {**goal, "status": "expired", "expired_at": now_dt.isoformat()}
    metadata[GOAL_STATE_KEY] = updated
    return updated


def sustained_goal_turn(
    metadata: Mapping[str, Any] | None,
    *,
    message_metadata: Mapping[str, Any] | None = None,
    session_key: str | None = None,
) -> bool:
    """True when this turn should use sustained-goal runtime limits.

    Un piano a lista che gira conta come un goal sostenuto: un passo può
    durare quanto un goal, e il tetto del modello lo romperebbe a metà. Un
    posto solo che decide, non due.
    """
    if sustained_goal_active(metadata):
        return True
    if plan_running_in_session(session_key):
        return True
    if not message_metadata:
        return False
    return str(message_metadata.get("original_command") or "").strip() == "/goal"


def parse_goal_state(blob: Any) -> dict[str, Any] | None:
    if blob is None:
        return None
    if isinstance(blob, dict):
        return blob
    if isinstance(blob, str):
        try:
            parsed = json.loads(blob)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def goal_state_runtime_lines(metadata: Mapping[str, Any] | None) -> list[str]:
    """Lines appended inside the Runtime Context block when a goal is active."""
    if not metadata:
        return []
    goal = parse_goal_state(goal_state_raw(metadata))
    if not isinstance(goal, dict) or goal.get("status") != "active":
        return []
    objective = str(goal.get("objective") or "").strip()
    if not objective:
        return ["Goal: active (no objective text stored)."]
    if len(objective) > _MAX_OBJECTIVE_IN_RUNTIME:
        objective = objective[:_MAX_OBJECTIVE_IN_RUNTIME].rstrip() + "\n… (truncated)"
    out = ["Goal (active):", objective]
    hint = str(goal.get("ui_summary") or "").strip()
    if hint:
        out.append(f"Summary: {hint}")
    # Riga decisiva contro il complete_goal di comodo: senza dirlo, un goal in
    # attesa spinge il modello a chiuderlo (recap falso) per uscire dallo stallo.
    if goal.get(_AWAITING_INPUT):
        since = str(goal.get(_AWAITING_SINCE) or "").strip()
        waiting = "Status: waiting for the user's reply"
        if since:
            waiting += f" since {since}"
        out.append(
            waiting
            + ". The goal is still open: pick it up where it stopped as soon as they answer."
            " Do not call complete_goal just because you are waiting, and do not repeat the"
            " question you already asked."
        )
    return out


def runner_wall_llm_timeout_s(
    sessions: SessionManager,
    session_key: str | None,
    *,
    metadata: Mapping[str, Any] | None = None,
    message_metadata: Mapping[str, Any] | None = None,
) -> float | None:
    """Wall-clock cap for :class:`~jenny.agent.runner.AgentRunner` when streaming an LLM.

    Returns ``0.0`` to disable ``asyncio.wait_for`` around the request when this is a
    sustained-goal turn; ``None`` means use ``JENNY_LLM_TIMEOUT_S``. Pass in-memory
    ``metadata`` when the caller already holds :attr:`~jenny.session.manager.Session.metadata`
    for this turn.
    """
    meta: Mapping[str, Any] | None = metadata
    if meta is None and session_key:
        meta = sessions.get_or_create(session_key).metadata
    sustained = sustained_goal_turn(
        meta, message_metadata=message_metadata, session_key=session_key
    )
    return 0.0 if sustained else None
