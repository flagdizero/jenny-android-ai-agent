"""Le righe ``role: "user"`` che l'utente non ha scritto, e dove il marcatore finisce.

Il fatto misurato sul device il 05/09/2026: tre cose si persistono con quel ruolo
senza essere l'utente — un turno di cron, il rientro di un subagent iniettato a
metà turno, lo sprone a un sustained goal — e solo la prima era marcata. Le altre
due passavano per parole dell'utente ai tre lettori che ne dipendono.

Il marcatore deve stare **solo** nel JSONL: se raggiungesse l'API sarebbe un campo
sconosciuto su un endpoint che ne rifiuta. Le due whitelist che lo trattengono sono
verificate qui sotto, perché sono la ragione per cui è lecito appenderlo a un dict
che è anche il payload della richiesta in corso.
"""

from __future__ import annotations

from jenny.cron.session_turns import CRON_HISTORY_META
from jenny.providers.base import LLMProvider
from jenny.providers.openai_compat_helpers import _ALLOWABLE_MSG_KEYS
from jenny.session.history_meta import (
    GOAL_CONTINUE_EVENT,
    INJECTED_EVENT_META,
    LENGTH_RECOVERY_EVENT,
    SUBAGENT_RESULT_EVENT,
    is_synthetic_history_row,
)
from jenny.session.manager import Session
from jenny.utils.runtime import (
    build_budget_exhausted_finalization_message,
    build_finalization_retry_message,
    build_length_recovery_message,
)


def test_a_typed_message_is_not_synthetic() -> None:
    assert is_synthetic_history_row({"role": "user", "content": "ciao"}) is False


def test_nothing_at_all_is_not_synthetic() -> None:
    assert is_synthetic_history_row(None) is False
    assert is_synthetic_history_row({}) is False


def test_the_three_synthetic_rows() -> None:
    assert is_synthetic_history_row({"role": "user", CRON_HISTORY_META: True}) is True
    assert (
        is_synthetic_history_row({"role": "user", INJECTED_EVENT_META: SUBAGENT_RESULT_EVENT})
        is True
    )
    assert (
        is_synthetic_history_row({"role": "user", INJECTED_EVENT_META: GOAL_CONTINUE_EVENT})
        is True
    )
    assert (
        is_synthetic_history_row({"role": "user", INJECTED_EVENT_META: LENGTH_RECOVERY_EVENT})
        is True
    )


def test_a_legacy_announce_persisted_as_assistant_counts_too() -> None:
    """Fino al 02/10/2026 il ramo d'annuncio autonomo scriveva ``role: "assistant"``.

    Oggi ``TurnPersistenceMixin._persist_subagent_followup`` scrive ``user``, ma le
    sessioni esistenti portano ancora le righe vecchie: è lo stesso fatto, e i
    lettori le saltano allo stesso modo.
    """
    row = {"role": "assistant", INJECTED_EVENT_META: SUBAGENT_RESULT_EVENT}
    assert is_synthetic_history_row(row) is True


def test_an_unknown_injected_event_is_not_swallowed() -> None:
    """Il predicato riconosce un elenco, non «ha una chiave ``injected_event``».

    Un evento nuovo deve essere aggiunto di proposito: passare in silenzio
    vorrebbe dire nascondere righe che nessuno ha deciso di nascondere.
    """
    assert is_synthetic_history_row({"role": "user", INJECTED_EVENT_META: "qualcosa"}) is False


def test_the_synthetic_builders_carry_their_marker() -> None:
    assert build_length_recovery_message()[INJECTED_EVENT_META] == LENGTH_RECOVERY_EVENT




def test_the_finalization_builders_stay_unmarked() -> None:
    """Non è una dimenticanza: quei due non arrivano mai alla storia.

    Finiscono in una copia della lista (``_finalization_retry_messages``,
    ``_budget_exhausted_finalization_messages``) che serve alla richiesta e al
    conteggio token. Marcarli direbbe il falso su dove vanno a finire.
    """
    assert INJECTED_EVENT_META not in build_finalization_retry_message()
    assert INJECTED_EVENT_META not in build_budget_exhausted_finalization_message()


def test_the_marker_never_reaches_the_provider() -> None:
    """Prima whitelist: i provider filtrano le chiavi prima del filo.

    È quel che rende lecito appendere il marcatore a un dict che è anche il
    payload della richiesta in corso — il turno lo persiste, l'API non lo vede.
    """
    marked = build_length_recovery_message()
    sanitized = LLMProvider._sanitize_request_messages([marked], _ALLOWABLE_MSG_KEYS)

    assert sanitized == [{"role": "user", "content": marked["content"]}]


def test_the_marker_does_not_survive_history_replay() -> None:
    """Seconda whitelist: ``get_history`` ricostruisce le righe chiave per chiave.

    Al turno dopo il modello rilegge un messaggio utente normale — che è quel che
    deve vedere: il rientro del subagent *è* successo, e la conversazione non
    deve fingere il contrario.
    """
    session = Session(key="websocket:replay")
    session.messages = [
        {
            "role": "user",
            "content": "[Subagent 'x' completed successfully]…",
            INJECTED_EVENT_META: SUBAGENT_RESULT_EVENT,
            "subagent_task_id": "ff0941f9",
        },
        {"role": "assistant", "content": "backup ok"},
    ]

    replayed = session.get_history()

    assert replayed == [
        {"role": "user", "content": "[Subagent 'x' completed successfully]…"},
        {"role": "assistant", "content": "backup ok"},
    ]
