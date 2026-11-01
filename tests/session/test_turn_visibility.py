"""Copertura per ``jenny.session.turn_visibility``.

Il modulo è il confine unico fra "questo turno può parlare all'utente" e "questo
turno è lavoro interno". Prima esistevano tre meccanismi incompatibili — il gate
LLM dell'heartbeat, ``suppress_response`` per i cron monitor, e *niente* per il
turno di annuncio di un subagent, che è proprio quello che finiva in chat.
"""

from __future__ import annotations

from jenny.bus.events import INTERNAL_CHANNEL
from jenny.cron.session_turns import monitor_session_key
from jenny.session.keys import UNIFIED_SESSION_KEY
from jenny.session.turn_visibility import (
    TURN_VISIBILITY_META,
    TurnVisibility,
    is_silent_turn,
    mark_silent_turn,
    resolve_turn_visibility,
    silent_turn_metadata,
)

# Un turno silenzioso di lavoro interno: la sessione di un monitor del cron.
MONITOR_SESSION_KEY = monitor_session_key("rain-check")


def _resolve(metadata=None, *, channel="websocket", session_key=UNIFIED_SESSION_KEY):
    return resolve_turn_visibility(metadata, channel=channel, session_key=session_key)


class TestTheDefaultIsTheProvenance:
    def test_a_user_turn_is_visible(self) -> None:
        assert _resolve({}) is TurnVisibility.VISIBLE

    def test_internal_work_on_a_user_channel_is_silent(self) -> None:
        """Il caso che rompeva tutto: l'heartbeat gira su ``websocket:default``."""
        assert _resolve({}, session_key=MONITOR_SESSION_KEY) is TurnVisibility.SILENT

    def test_a_monitor_session_is_silent_by_provenance_alone(self) -> None:
        assert _resolve({}, session_key="cron:job-1") is TurnVisibility.SILENT

    def test_a_subagent_session_is_silent(self) -> None:
        assert _resolve({}, session_key="subagent:lin-1") is TurnVisibility.SILENT

    def test_the_internal_channel_stays_visible(self) -> None:
        """Nessun utente da raggiungere: l'outbound è il valore di ritorno con cui
        Dream e il giardiniere leggono l'esito del proprio run, e va preservato."""
        assert (
            _resolve({}, channel=INTERNAL_CHANNEL, session_key="dream:20260810-100000")
            is TurnVisibility.VISIBLE
        )

    def test_absent_metadata_behaves_like_empty(self) -> None:
        assert _resolve(None) is TurnVisibility.VISIBLE
        assert _resolve(None, session_key=MONITOR_SESSION_KEY) is TurnVisibility.SILENT


class TestAnExplicitMarkWins:
    def test_a_marked_turn_is_silent_even_on_a_user_session(self) -> None:
        assert _resolve({TURN_VISIBILITY_META: "silent"}) is TurnVisibility.SILENT

    def test_a_marked_visible_turn_beats_the_provenance(self) -> None:
        assert (
            _resolve({TURN_VISIBILITY_META: "visible"}, session_key=MONITOR_SESSION_KEY)
            is TurnVisibility.VISIBLE
        )

    def test_a_meaningless_mark_falls_back_to_the_provenance(self) -> None:
        """Nel dubbio decide la provenienza, non un valore che nessuno riconosce."""
        assert _resolve({TURN_VISIBILITY_META: "maybe"}) is TurnVisibility.VISIBLE
        assert (
            _resolve({TURN_VISIBILITY_META: "maybe"}, session_key=MONITOR_SESSION_KEY)
            is TurnVisibility.SILENT
        )
        assert _resolve({TURN_VISIBILITY_META: True}) is TurnVisibility.VISIBLE


class TestTheMetadataOnlyReading:
    """``is_silent_turn`` serve a chi non ha canale e session key nella firma."""

    def test_only_the_mark_counts(self) -> None:
        assert is_silent_turn({TURN_VISIBILITY_META: "silent"}) is True
        assert is_silent_turn({TURN_VISIBILITY_META: "visible"}) is False
        assert is_silent_turn({}) is False
        assert is_silent_turn(None) is False

    def test_marking_is_idempotent(self) -> None:
        metadata: dict = {}
        mark_silent_turn(metadata)
        mark_silent_turn(metadata)
        assert metadata == {TURN_VISIBILITY_META: "silent"}
        assert is_silent_turn(metadata) is True

    def test_the_helper_copy_does_not_touch_the_original(self) -> None:
        original = {"webui": True}
        copy = silent_turn_metadata(original)
        assert is_silent_turn(copy) is True
        assert is_silent_turn(original) is False
        assert copy["webui"] is True

    def test_the_mark_round_trips_through_the_resolver(self) -> None:
        metadata: dict = {}
        mark_silent_turn(metadata)
        assert _resolve(metadata) is TurnVisibility.SILENT


class TestInheritanceAcrossDerivedTurns:
    """La visibilità deve sopravvivere ai turni che nascono da un altro turno."""

    def test_a_goal_continuation_of_a_silent_turn_stays_silent(self) -> None:
        """La continuation copia il dict metadata: se il marchio non passasse, la
        seconda metà di un controllo silenzioso parlerebbe in chat.

        (È il gemello del bug che il vecchio segnale ``_cron_monitor_spoke`` aveva
        proprio qui: viaggiava in un dict che la continuation copiava.)
        """
        from jenny.session.turn_continuation import _internal_continuation_metadata

        inherited = _internal_continuation_metadata(silent_turn_metadata({"webui": True}))

        assert is_silent_turn(inherited) is True
        assert resolve_turn_visibility(
            inherited, channel="websocket", session_key=UNIFIED_SESSION_KEY
        ) is TurnVisibility.SILENT

    def test_a_continuation_of_a_visible_turn_stays_visible(self) -> None:
        from jenny.session.turn_continuation import _internal_continuation_metadata

        inherited = _internal_continuation_metadata({"webui": True})

        assert is_silent_turn(inherited) is False


class TestTheEnum:
    def test_silent_is_the_only_silent_value(self) -> None:
        assert TurnVisibility.SILENT.silent is True
        assert TurnVisibility.VISIBLE.silent is False

    def test_the_wire_values_are_stable(self) -> None:
        """Il valore finisce nei metadata di un messaggio: rinominarlo è un
        cambio di formato, non un refactor."""
        assert TurnVisibility.SILENT.value == "silent"
        assert TurnVisibility.VISIBLE.value == "visible"
