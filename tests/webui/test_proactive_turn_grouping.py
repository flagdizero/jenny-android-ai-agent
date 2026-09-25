"""Ogni avviso proattivo deve arrivare al client come un turno proprio.

Il client raggruppa i messaggi assistant per ``turnId`` (``mobile-chat.js``,
``_buildTurns``): finché due messaggi consecutivi condividono l'id vengono
concatenati in una bolla sola. Con ``turnId`` assente su entrambi i lati
l'uguaglianza ``undefined !== undefined`` è falsa, quindi **tutti** gli avvisi
senza id finivano in un'unica bolla.

Misurato sul dispositivo il 2026-08-13: quattro avvisi heartbeat consegnati fra
01:31 e 05:02 stanno nel transcript come quattro record ``message`` consecutivi
(righe 17720-17723) **tutti** con ``turn_id: None``, mentre le righe precedenti
(turno utente) e successive (turno cron) hanno il proprio id. Passando il payload
reale di questo modulo a una copia verbatim di ``_buildTurns``: 1 bolla da 4
paragrafi prima della fix, 4 bolle dopo.

Il fold e la paginazione lato server erano innocenti — restituivano i quattro
messaggi in ogni configurazione provata (2/100/300 turni precedenti, con e senza
``limit``). Questi test fissano il **contratto verso il client**: un id presente
e distinto per ogni consegna proattiva.
"""

from __future__ import annotations

import pytest

from jenny.webui.transcript import append_transcript_object, build_webui_thread_response

KEY = "websocket:default"
NOTICES = (
    "Ciao papi, il monitoraggio delle piante non sta girando",
    "papi, ti segnalo che il controllo WaterBot non sta girando",
    "ehi papi, ti dico che il check non sta girando da un po'",
    "Papi, il monitoraggio automatico non funziona piu",
)


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)
    return tmp_path


def _closed_user_turn(turn_id: str) -> None:
    """Un turno utente normale: chiude con ``turn_end``, come in produzione."""
    append_transcript_object(KEY, {"event": "user", "chat_id": "default",
                                   "text": "domanda", "turn_id": turn_id,
                                   "turn_phase": "user", "turn_seq": 1})
    append_transcript_object(KEY, {"event": "delta", "chat_id": "default",
                                   "text": "risposta", "turn_id": turn_id,
                                   "turn_phase": "answer", "turn_seq": 2})
    append_transcript_object(KEY, {"event": "stream_end", "chat_id": "default",
                                   "turn_id": turn_id, "turn_phase": "answer",
                                   "turn_seq": 3})
    append_transcript_object(KEY, {"event": "turn_end", "chat_id": "default",
                                   "turn_id": turn_id, "turn_phase": "complete",
                                   "turn_seq": 4, "latency_ms": 1000})


def _proactive_alerts(*, with_turn_ids: bool, closed: bool = False) -> None:
    """I quattro avvisi consecutivi.

    ``closed`` è la forma prodotta oggi: il turno silenzioso non emette nessun
    ``turn_end``, quindi lo emette ``ChannelDeliverer._close_webui_turn`` con
    l'id dell'avviso. Senza (il default) è la forma **già scritta** su disco
    prima di quella fix — un turno aperto e mai chiuso — che va comunque
    ricostruita bene.
    """
    for i, text in enumerate(NOTICES):
        rec: dict = {"event": "message", "chat_id": "default", "text": text}
        if with_turn_ids:
            rec |= {"turn_id": f"proactive:{i}", "turn_phase": "answer", "turn_seq": 1}
        append_transcript_object(KEY, rec)
        if closed and with_turn_ids:
            append_transcript_object(KEY, {
                "event": "turn_end", "chat_id": "default",
                "turn_id": f"proactive:{i}", "turn_phase": "complete", "turn_seq": 2,
            })


def _assistant_texts(messages: list[dict]) -> list[str]:
    return [
        str(m.get("content") or "")
        for m in messages
        if m.get("role") == "assistant" and m.get("kind") != "trace"
    ]


def test_each_proactive_alert_is_its_own_message(data_dir) -> None:
    _closed_user_turn("u1")
    _proactive_alerts(with_turn_ids=True)
    payload = build_webui_thread_response(KEY)
    assert payload is not None
    texts = _assistant_texts(payload["messages"])
    for notice in NOTICES:
        assert notice in texts, f"avviso perso o accorpato: {notice!r}"


def test_each_proactive_alert_carries_a_distinct_turn_id(data_dir) -> None:
    """Il contratto su cui il client si basa per non concatenarli."""
    _closed_user_turn("u1")
    _proactive_alerts(with_turn_ids=True)
    payload = build_webui_thread_response(KEY)
    assert payload is not None
    ids = [
        m.get("turnId")
        for m in payload["messages"]
        if str(m.get("content") or "") in NOTICES
    ]
    assert len(ids) == len(NOTICES)
    assert all(isinstance(i, str) and i for i in ids), f"turnId assente: {ids}"
    assert len(set(ids)) == len(NOTICES), f"turnId condivisi fra avvisi: {ids}"


def test_alerts_are_not_folded_into_the_previous_user_turn(data_dir) -> None:
    """Nessun avviso deve ereditare il turno della risposta che lo precede: è da
    lì che il client capisce dove finisce una bolla e dove inizia la successiva."""
    _closed_user_turn("u1")
    _proactive_alerts(with_turn_ids=True)
    payload = build_webui_thread_response(KEY)
    assert payload is not None
    for m in payload["messages"]:
        if str(m.get("content") or "") in NOTICES:
            assert m.get("turnId") != "u1"


def test_closed_proactive_turns_stay_four_distinct_messages(data_dir) -> None:
    """La forma prodotta oggi: ogni avviso è seguito dal proprio ``turn_end``.

    Il delimitatore serve al client live (chiude il turno: mascotte a idle,
    stato di stream azzerato, bolla nuova per l'avviso dopo) e allo split del
    transcript. Qui si verifica che rileggendoli non cambi niente di ciò che il
    fold restituiva prima: quattro messaggi, quattro turni distinti.
    """
    _closed_user_turn("u1")
    _proactive_alerts(with_turn_ids=True, closed=True)
    payload = build_webui_thread_response(KEY)
    assert payload is not None
    texts = _assistant_texts(payload["messages"])
    for notice in NOTICES:
        assert notice in texts, f"avviso perso o accorpato: {notice!r}"
    ids = [
        m.get("turnId")
        for m in payload["messages"]
        if str(m.get("content") or "") in NOTICES
    ]
    assert len(set(ids)) == len(NOTICES), f"turnId condivisi fra avvisi: {ids}"


def test_legacy_alerts_without_turn_id_still_arrive_as_separate_messages(data_dir) -> None:
    """La cronologia già scritta non avrà mai quell'id: il server la restituisce
    comunque come messaggi distinti, ed è la guardia in ``_buildTurns`` (un
    messaggio senza turno non entra nel turno precedente) a tenerli separati
    nel rendering."""
    _closed_user_turn("u1")
    _proactive_alerts(with_turn_ids=False)
    payload = build_webui_thread_response(KEY)
    assert payload is not None
    texts = _assistant_texts(payload["messages"])
    for notice in NOTICES:
        assert notice in texts
    # Nessun accorpamento lato server: gli avvisi non finiscono in un unico testo.
    assert not any(
        sum(1 for a in NOTICES if a in t) > 1 for t in texts
    ), "due o più avvisi concatenati nello stesso messaggio"
