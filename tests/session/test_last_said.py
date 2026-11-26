"""Se l'utente ha risposto a un promemoria: per posizione nella chat, non per orario."""

from __future__ import annotations

from pathlib import Path

from jenny.cron.session_turns import CRON_HISTORY_META
from jenny.session import store
from jenny.session.history_meta import INJECTED_EVENT_META, SUBAGENT_RESULT_EVENT
from jenny.session.last_said import cron_replies
from jenny.session.manager import SessionManager


def _cron(session, job_id: str) -> None:
    session.add_message("user", "Scheduled cron job triggered",
                        **{CRON_HISTORY_META: True, "cron_job_id": job_id})
    session.add_message("assistant", "come stai stasera?")


def test_a_reply_is_a_line_the_user_wrote_after_the_last_run(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    manager = SessionManager(ws)
    session = manager.get_or_create("project:orto")
    session.add_message("user", "sistema la patch")      # prima: non conta
    _cron(session, "seduta")
    session.add_message("user", "stanco, ma ok")         # la risposta
    session.add_message("assistant", "cosa ti ha stancato?")
    _cron(session, "meteo")                               # un altro job, dopo
    # Righe «utente» che l'utente non ha scritto.
    session.add_message("user", "[Subagent done]", **{INJECTED_EVENT_META: SUBAGENT_RESULT_EVENT})
    session.add_message("user", "/stop", _command=True)
    manager.save(session)

    assert cron_replies(ws, "project:orto") == {"seduta": True, "meteo": False}


def test_only_the_last_run_of_a_job_counts(tmp_path: Path) -> None:
    """Ha risposto ieri, non stasera: il post-it dice «nessuna risposta»."""
    ws = tmp_path / "ws"
    manager = SessionManager(ws)
    session = manager.get_or_create("project:orto")
    _cron(session, "seduta")
    session.add_message("user", "ieri ho risposto")
    _cron(session, "seduta")
    manager.save(session)

    assert cron_replies(ws, "project:orto") == {"seduta": False}
    assert cron_replies(ws, "project:nessuno") == {}


def test_tail_reads_from_the_newest(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    manager = SessionManager(ws)
    session = manager.get_or_create("project:orto")
    for i in range(5):
        session.add_message("user", f"m{i}")
    manager.save(session)

    rows = store.tail(store.session_database(ws), "project:orto", 2)
    assert [r["content"] for r in rows] == ["m4", "m3"]
