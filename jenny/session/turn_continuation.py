"""Turn persistence helpers: what of a turn becomes history, and from where.

The module used to hold the sustained goal's internal continuation (the
synthetic "keep going" slices queued at a budget boundary); that went with the
goal on 01/11/2026, and the name stayed with the two helpers that remain: whether
an inbound message is saved as user input, and where the turn's append to the
history starts.
"""

from __future__ import annotations

from typing import Any, Mapping

SKIP_USER_PERSIST_META = "_skip_user_persist"


def should_persist_user_message(metadata: Mapping[str, Any] | None) -> bool:
    """Return whether this inbound message should be persisted as user input."""
    return not (metadata and metadata.get(SKIP_USER_PERSIST_META) is True)


def prepare_save_boundary(ctx: Any) -> None:
    """Set the history append boundary for this turn."""
    ctx.save_skip = _save_skip_for_turn(
        message_metadata=ctx.msg.metadata,
        initial_message_count=len(ctx.initial_messages),
        history_count=len(ctx.history),
        user_persisted_early=ctx.user_persisted_early,
    )


def _save_skip_for_turn(
    *,
    message_metadata: Mapping[str, Any] | None,
    initial_message_count: int,
    history_count: int,
    user_persisted_early: bool,
) -> int:
    """Return the persisted-message append boundary for this turn."""
    if message_metadata and message_metadata.get(SKIP_USER_PERSIST_META) is True:
        return initial_message_count
    # build_messages may merge the current message into a same-role history tail.
    # Runner-appended messages start at initial_message_count in either shape.
    has_standalone_current = initial_message_count > 1 + history_count
    if has_standalone_current and not user_persisted_early:
        return initial_message_count - 1
    return initial_message_count
