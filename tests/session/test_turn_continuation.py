"""Where a turn's append to the history starts (``_save_skip_for_turn``)."""

from __future__ import annotations

from jenny.session.turn_continuation import _save_skip_for_turn


def test_save_skip_matches_prefix_when_current_message_merged():
    skip = _save_skip_for_turn(
        message_metadata=None,
        initial_message_count=2,  # [system, merged user]
        history_count=1,
        user_persisted_early=True,
    )
    assert skip == 2


def test_save_skip_unchanged_for_standalone_current_message():
    # [system, history user, current user] with the current user already saved.
    assert _save_skip_for_turn(
        message_metadata=None,
        initial_message_count=3,
        history_count=1,
        user_persisted_early=True,
    ) == 3
    assert _save_skip_for_turn(
        message_metadata=None,
        initial_message_count=3,
        history_count=1,
        user_persisted_early=False,
    ) == 2

