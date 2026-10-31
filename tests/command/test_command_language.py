"""I comandi slash rispondono nella lingua dell'utente.

Fino all'11/10/2026 ogni risposta dei comandi era inglese fisso, anche su una
WebUI in italiano: le frasi stanno ora in ``runtime/user_text.py``. La suite
fissa l'inglese (``tests/conftest.py``), quindi qui si passa all'italiano a mano.
"""

from __future__ import annotations

import time

import pytest

from jenny.agent.gardener import GardenerOutcome
from jenny.command.builtin import (
    _format_dream_no_input_message,
    _format_gardener_outcome,
    build_help_text,
)
from jenny.command.scope import refusal_for_line
from jenny.command.specs import BUILTIN_COMMAND_SPECS
from jenny.runtime import user_text
from jenny.runtime.user_text import say
from jenny.utils.helpers import build_status_content


@pytest.fixture()
def italian(monkeypatch):
    monkeypatch.setattr(user_text, "current_language", lambda: "it")


def test_the_english_help_line_is_the_spec_description_word_for_word() -> None:
    """La tendina mostra ``description`` come ripiego e ``/help`` la chiave."""
    for spec in BUILTIN_COMMAND_SPECS:
        assert say(spec.help_key, "en") == spec.description, spec.command
        if spec.scope_note:
            assert say(spec.scope_note_key, "en") == spec.scope_note, spec.command


def test_every_command_has_an_italian_help_line() -> None:
    for spec in BUILTIN_COMMAND_SPECS:
        assert say(spec.help_key, "it") != spec.description, spec.command


def test_help_speaks_italian(italian) -> None:
    text = build_help_text("unified:default")
    assert text.startswith("✿ comandi di jenny:")
    assert say("cmd.help.dream", "it") in text


def test_a_scope_refusal_speaks_italian(italian) -> None:
    text = refusal_for_line("/dream", "project:viaggio")
    assert text is not None
    assert "quaderno" in text
    assert say("cmd.scope_note.dream", "it") in text


def test_status_speaks_italian(italian) -> None:
    text = build_status_content(
        version="1.0.0", model="m", start_time=time.time(),
        last_usage={"prompt_tokens": 100, "completion_tokens": 10, "cached_tokens": 50},
        context_window_tokens=131072, session_msg_count=4, context_tokens_estimate=2000,
    )
    assert "Modello: m" in text
    assert "(50% dalla cache)" in text
    assert "Session:" not in text


def test_dream_without_input_speaks_italian(italian) -> None:
    assert _format_dream_no_input_message().startswith("Dream non ha ancora")


_GARDENER_OUTCOMES = [
    GardenerOutcome(status=status, elapsed=1.0, lines=3, writes=2, detail="x")
    for status in (
        "skipped_no_delta", "written", "nothing_to_promote", "partial_write",
        "commit_failed", "aborted_user_active", "already_running", "no_write",
        "incomplete", "failed",
    )
] + [
    GardenerOutcome(status="written", map_pass=True, map_before=9000, map_after=3000),
    GardenerOutcome(status="written", map_pass=True, map_before=9000, map_after=8000),
    GardenerOutcome(status="no_write", map_pass=True, map_before=9000, map_after=9000),
    GardenerOutcome(status="written", lines=3, writes=2, board="done"),
    GardenerOutcome(status="skipped_no_delta", board="incomplete"),
    GardenerOutcome(status="written", lines=3, writes=2, board="failed"),
]


@pytest.mark.parametrize("outcome", _GARDENER_OUTCOMES)
def test_every_gardener_outcome_speaks_italian(italian, outcome) -> None:
    text = _format_gardener_outcome("serra", outcome)
    assert any(word in text for word in ("giardiniere", "diario", "Niente")), text
    for english in ("journal", "The gardener", "board", "pass "):
        assert english not in text, (outcome, text)


@pytest.mark.parametrize(
    "absence", ["research", "no_pages", "proposed", "declined", "later", "next_pass"],
)
def test_every_missing_board_reason_speaks_italian(italian, absence) -> None:
    outcome = GardenerOutcome(status="skipped_no_delta")
    text = _format_gardener_outcome("serra", outcome, absence=absence)
    assert "bacheca" in text
    assert "board" not in text
