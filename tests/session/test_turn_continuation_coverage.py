"""Copertura complementare per ``jenny.session.turn_continuation``.

``test_turn_continuation.py`` copre il confine di salvataggio
(``_save_skip_for_turn``); qui ``should_persist_user_message`` e
``prepare_save_boundary``. La continuazione interna del goal sostenuto, che il
modulo ospitava, è stata tolta col goal il 01/11/2026.
"""

from __future__ import annotations

from types import SimpleNamespace

from jenny.bus.events import InboundMessage
from jenny.session.turn_continuation import (
    SKIP_USER_PERSIST_META,
    prepare_save_boundary,
    should_persist_user_message,
)


def test_should_persist_user_message_false_when_skip_flag_set():
    assert should_persist_user_message({SKIP_USER_PERSIST_META: True}) is False


def test_should_persist_user_message_true_for_ordinary_message():
    assert should_persist_user_message({}) is True
    assert should_persist_user_message(None) is True


def _ctx(*, session, metadata=None, initial_messages=None):
    return SimpleNamespace(
        session=session,
        msg=InboundMessage(
            channel="websocket", sender_id="u", chat_id="c", content="x",
            metadata=metadata or {},
        ),
        initial_messages=initial_messages or [{"role": "system"}, {"role": "user", "content": "x"}],
        history=[],
        user_persisted_early=False,
    )


def test_prepare_save_boundary_sets_the_append_boundary():
    ctx = _ctx(session=SimpleNamespace(metadata={}))
    prepare_save_boundary(ctx)
    # Il messaggio corrente non e' stato salvato prima: si salva da qui.
    assert ctx.save_skip == len(ctx.initial_messages) - 1


def test_prepare_save_boundary_skips_an_input_not_to_persist():
    ctx = _ctx(session=SimpleNamespace(metadata={}), metadata={SKIP_USER_PERSIST_META: True})
    prepare_save_boundary(ctx)
    assert ctx.save_skip == len(ctx.initial_messages)


def test_prepare_save_boundary_works_without_a_session():
    ctx = _ctx(session=None, initial_messages=[{"role": "system"}])
    prepare_save_boundary(ctx)
    assert ctx.save_skip == len(ctx.initial_messages)
