"""``transcripts``: i verbali della WebUI nel database.

Le fixture sono la forma vera di prima: un segmento ruotato, il file attivo con
una riga troncata, la chiave di un progetto con un ``_`` nel nome.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jenny import db as jdb
from jenny.db_migration import run_pending
from jenny.db_migration.registry import DONE
from jenny.db_migration.transcripts import key_of


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch):
    ws = tmp_path / "workspace"
    (ws / ".jenny" / "webui").mkdir(parents=True)
    monkeypatch.setattr("jenny.config.paths.get_webui_dir", lambda: ws / ".jenny" / "webui")
    yield ws
    jdb.close_all()


def _turn(text: str) -> str:
    return "\n".join(json.dumps(r) for r in (
        {"event": "user", "text": text}, {"event": "message", "text": "ok"},
        {"event": "turn_end"})) + "\n"


def test_the_keys_come_back_from_the_file_names() -> None:
    assert key_of("websocket_default") == "websocket:default"
    assert key_of("websocket_project_orto_bio") == "websocket:project:orto_bio"
    assert key_of("token-usage") is None


def test_segments_and_the_active_file_come_in_in_order(workspace: Path) -> None:
    from jenny.webui.transcript_store import _read_chunk_turns, read_transcript_lines

    webui = workspace / ".jenny" / "webui"
    (webui / "websocket_project_orto_bio.segments").mkdir()
    (webui / "websocket_project_orto_bio.segments" / "000001.jsonl").write_text(_turn("primo"))
    (webui / "websocket_project_orto_bio.segments" / "manifest.json").write_text("{}")
    (webui / "websocket_project_orto_bio.jsonl").write_text(_turn("secondo") + '{"event": "us')

    assert run_pending(workspace)["transcripts"] == DONE

    key = "websocket:project:orto_bio"
    users = [r["text"] for r in read_transcript_lines(key) if r.get("event") == "user"]
    assert users == ["primo", "secondo"]
    assert len(_read_chunk_turns(key, "active")) == 2
    assert not (webui / "websocket_project_orto_bio.jsonl").exists()
    assert (workspace / ".jenny" / "webui.migrated" / "websocket_project_orto_bio.jsonl").is_file()
