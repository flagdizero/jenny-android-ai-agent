"""``sessions``: le conversazioni di ``sessions/`` nel database.

Le fixture sono la forma vera di prima: un JSONL con la riga di metadati e una
riga troncata da un kill, il diario di un turno interrotto, una sessione di
Atlas (ritirato), il giornale di un rinomino aperto.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jenny import db as jdb
from jenny.db_migration import KEEP_MIGRATED_S, run_pending
from jenny.db_migration.registry import DONE
from jenny.session.manager import SessionManager


@pytest.fixture
def workspace(tmp_path: Path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    yield ws
    jdb.close_all()


def _old_sessions(ws: Path) -> None:
    old = ws / "sessions"
    old.mkdir()
    meta = {"_type": "metadata", "key": "project:orto_bio", "created_at": "2026-10-01T10:00:00",
            "updated_at": "2026-10-02T10:00:00", "metadata": {"title": "Orto"},
            "last_consolidated": 1}
    lines = [json.dumps(meta), json.dumps({"role": "user", "content": "ciao"}),
             json.dumps({"role": "assistant", "content": "eccomi"}), '{"role": "us']
    # Il nome del file perde i ``:`` (``project_orto_bio``): la chiave vera è nei metadati.
    (old / "project_orto_bio.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (old / "project_orto_bio.turn-journal").write_text(
        json.dumps({"_type": "turn_journal", "stamp": "s1"}) + "\n"
        + json.dumps({"role": "tool", "content": "letto"}) + "\n", encoding="utf-8")
    (old / "atlas_20260901-192635.jsonl").write_text(
        json.dumps({"_type": "metadata", "key": "atlas:20260901-192635"}) + "\n")
    (old / ".project-rename-pending.json").write_text(
        json.dumps([{"old": "project:a", "new": "project:b"}]))


def test_the_conversations_come_in_with_their_real_keys(workspace: Path) -> None:
    _old_sessions(workspace)
    assert run_pending(workspace)["sessions"] == DONE
    sessions = SessionManager(workspace)
    assert sessions.list_keys() == ["project:orto_bio"]
    chat = sessions.get_or_create("project:orto_bio")
    assert [m["content"] for m in chat.messages] == ["ciao", "eccomi"]
    assert chat.metadata == {"title": "Orto"} and chat.last_consolidated == 1
    assert sessions.turn_read("project:orto_bio")[0] == "s1"
    assert (workspace / ".jenny" / "project-rename-pending.json").is_file()
    assert not (workspace / "sessions").exists()
    assert (workspace / "sessions.migrated" / "project_orto_bio.jsonl").is_file()


def test_a_second_pass_keeps_what_is_there(workspace: Path) -> None:
    _old_sessions(workspace)
    run_pending(workspace, now=1000.0)
    sessions = SessionManager(workspace)
    chat = sessions.get_or_create("project:orto_bio")
    chat.add_message("user", "dopo")
    sessions.save(chat)
    (workspace / "sessions.migrated").rename(workspace / "sessions")
    assert run_pending(workspace, now=2000.0)["sessions"] == DONE
    again = SessionManager(workspace).get_or_create("project:orto_bio")
    assert again.messages[-1]["content"] == "dopo"
    run_pending(workspace, now=2000.0 + KEEP_MIGRATED_S)
    assert not (workspace / "sessions.migrated").exists()
