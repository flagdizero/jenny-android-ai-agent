"""L'indice delle chat: le parole giuste, in pari coi verbali, e rifatto da solo.

È derivato dai verbali (``transcript_store``) e sta in un altro database, quindi
la cosa da provare è che resti in pari in ogni modo in cui un verbale cambia: un
turno che si chiude, un progetto rinominato o cancellato, un ripristino, un
kill a metà.
"""

from __future__ import annotations

from typing import Any

import pytest

from jenny import db
from jenny.webui import chat_index as ci
from jenny.webui import transcript_store as ts


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)
    return ts._workspace()


KEY = "websocket:default"


def _say(key: str, user: str, answer: list[str], *, close: bool = True,
         origin: str | None = None, at: int = 1_793_000_000_000) -> None:
    frame: dict[str, Any] = {"event": "user", "text": user, "at": at}
    if origin:
        frame["origin"] = origin
    ts.append_transcript_object(key, frame)
    ts.append_transcript_object(key, {"event": "reasoning_delta", "text": "penso al vaso"})
    for piece in answer:
        ts.append_transcript_object(key, {"event": "delta", "text": piece, "stream_id": "s",
                                          "turn_phase": "answer", "at": at + 1})
    ts.append_transcript_object(key, {"event": "stream_end", "stream_id": "s"})
    if close:
        ts.append_transcript_object(key, {"event": "turn_end"})


def _found(ws, *words: str, keys=None) -> list[tuple[str, str]]:
    return [(h.role, h.snippet) for h in ci.search(ws, list(words), keys=keys)[1]]


def test_the_words_of_a_turn_are_the_user_and_the_answer() -> None:
    frames = [
        (0, 0, {"event": "user", "text": "Quanto è grande il vaso?", "origin": "floating",
                "at": 5}),
        (0, 1, {"event": "reasoning_delta", "text": "pensiero"}),
        (0, 2, {"event": "message", "kind": "progress", "text": "read_file"}),
        (0, 3, {"event": "delta", "text": "Venti ", "stream_id": "a", "turn_phase": "answer"}),
        (0, 4, {"event": "delta", "text": "centimetri.", "stream_id": "a"}),
        (0, 5, {"event": "stream_end", "stream_id": "a"}),
        (0, 6, {"event": "message", "text": "New session started.", "session_boundary": True}),
        (1, 7, {"event": "message", "text": "Promemoria: annaffia.", "origin_channel": "cron"}),
    ]
    texts = ci.texts_of_frames(KEY, frames)
    assert [(t.turn, t.seq, t.role, t.source, t.text) for t in texts] == [
        (0, 0, ci.USER, "floating", "Quanto è grande il vaso?"),
        (0, 3, ci.JENNY, None, "Venti centimetri."),
        (1, 7, ci.JENNY, "cron", "Promemoria: annaffia."),
    ]
    assert texts[1].at == 5


def test_a_closed_turn_is_searchable_an_open_one_not_yet(ws) -> None:
    _say(KEY, "Come si chiama il veterinario?", ["È la dottoressa ", "Bianchi."])
    _say(KEY, "E il negozio di piante?", ["Si chiama ", "Verdepiù."], close=False)
    assert _found(ws, "bianchi") == [(ci.JENNY, "È la dottoressa [Bianchi].")]
    assert _found(ws, "verdepiu") == []
    ts.append_transcript_object(KEY, {"event": "turn_end"})
    assert _found(ws, "verdepiu") == [(ci.JENNY, "Si chiama [Verdepiù].")]


def test_words_match_by_their_start_and_without_accents(ws) -> None:
    _say(KEY, "Perché la monstera ingiallisce?", ["Troppa acqua, probabilmente."])
    assert [r for r, _s in _found(ws, "perche")] == [ci.USER]
    assert [r for r, _s in _found(ws, "monst")] == [ci.USER]
    assert [r for r, _s in _found(ws, "probabil")] == [ci.JENNY]
    assert _found(ws, "tropp") and not _found(ws, "acquario")


def test_quotes_and_operators_in_a_word_do_not_break_the_query(ws) -> None:
    _say(KEY, "il file wiki/acero.md", ["fatto"])
    for word in ('"acero', "NOT", "acero-md", "wiki/acero"):
        ci.search(ws, [word])


def test_the_index_follows_a_rename_and_a_delete(ws) -> None:
    _say("websocket:project:orto", "le zucchine", ["a maggio"])
    assert ts.rename_webui_transcript("websocket:project:orto", "websocket:project:orto2")
    hits = ci.search(ws, ["zucchine"])[1]
    assert [h.key for h in hits] == ["websocket:project:orto2"]
    assert ts.delete_webui_transcript("websocket:project:orto2")
    assert ci.search(ws, ["zucchine"]) == (0, [])


def test_keys_limit_the_search(ws) -> None:
    _say(KEY, "il glicine personale", ["ok"])
    _say("websocket:project:piante", "il glicine del quaderno", ["ok"])
    mine = ci.search(ws, ["glicine"], keys=["websocket:project:piante"])[1]
    assert [h.key for h in mine] == ["websocket:project:piante"]
    assert ci.search(ws, ["glicine"], keys=[]) == (0, [])
    assert len(ci.search(ws, ["glicine"])[1]) == 2


def test_reconcile_rebuilds_a_lost_index(ws) -> None:
    _say(KEY, "il limone sul balcone", ["d'estate"])
    db.database_for_workspace(ws, db.INDEX).close()
    path = db.database_for_workspace(ws, db.INDEX).path
    db.close_all()
    for suffix in ("", "-wal", "-shm"):
        path.with_name(path.name + suffix).unlink(missing_ok=True)
    assert ci.search(ws, ["limone"]) == (0, [])
    assert ci.reconcile(ws) == 2
    assert [r for r, _s in _found(ws, "limone")] == [ci.USER]
    assert ci.reconcile(ws) == 0


def test_reconcile_catches_up_what_a_kill_left_behind(ws, monkeypatch) -> None:
    monkeypatch.setattr(ts, "_turn_closed", lambda key: None)  # il kill: indice non avvisato
    _say(KEY, "la potatura del fico", ["a febbraio"])
    monkeypatch.undo()
    assert ci.search(ws, ["potatura"]) == (0, [])
    assert ci.reconcile(ws) == 2
    assert ci.search(ws, ["potatura"])[0] == 1


def test_a_transcript_that_went_back_is_indexed_again(ws) -> None:
    for word in ("primo", "secondo", "terzo"):
        _say(KEY, f"messaggio {word}", ["ok"])
    with ts._db().write() as conn:  # un ripristino: il verbale ha meno turni
        conn.execute("DELETE FROM transcript_rows WHERE key = ? AND turn >= 1", (KEY,))
        conn.execute("UPDATE transcripts SET turn = 1 WHERE key = ?", (KEY,))
    ci.reconcile(ws)
    assert ci.search(ws, ["terzo"]) == (0, [])
    assert ci.search(ws, ["primo"])[0] == 1


def test_reconcile_drops_a_chat_that_is_gone(ws, monkeypatch) -> None:
    _say("websocket:project:via", "la vite americana", ["ok"])
    monkeypatch.setattr(ts, "_transcript_dropped", lambda key: None)
    ts.delete_webui_transcript("websocket:project:via")
    monkeypatch.undo()
    assert ci.search(ws, ["vite"])[0] == 1
    ci.reconcile(ws)
    assert ci.search(ws, ["vite"]) == (0, [])


def test_turn_texts_opens_whole_turns(ws) -> None:
    _say(KEY, "uno", ["risposta uno"])
    _say(KEY, "due", ["risposta ", "due"])
    texts = ci.turn_texts(ws, KEY, [1])
    assert [(t.role, t.text) for t in texts] == [(ci.USER, "due"), (ci.JENNY, "risposta due")]
