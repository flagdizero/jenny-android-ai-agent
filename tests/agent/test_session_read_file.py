"""Tests for SessionManager.read_session_file and safe_key."""

from pathlib import Path

from jenny.session.manager import Session, SessionManager


def _seed(workspace: Path, key: str = "websocket:abc") -> SessionManager:
    sm = SessionManager(workspace)
    session = Session(key=key)
    session.add_message("user", "hello")
    session.add_message("assistant", "hi back")
    sm.save(session)
    return sm


def test_read_session_file_returns_metadata_and_messages(tmp_path: Path) -> None:
    sm = _seed(tmp_path, "websocket:abc")
    data = sm.read_session_file("websocket:abc")
    assert data is not None
    assert data["key"] == "websocket:abc"
    assert isinstance(data["messages"], list)
    assert [m["role"] for m in data["messages"]] == ["user", "assistant"]
    assert data["created_at"]
    assert data["updated_at"]


def test_read_session_file_does_not_populate_cache(tmp_path: Path) -> None:
    sm = _seed(tmp_path, "websocket:abc")
    sm.invalidate("websocket:abc")
    assert "websocket:abc" not in sm._cache
    sm.read_session_file("websocket:abc")
    assert "websocket:abc" not in sm._cache


def test_read_session_file_missing(tmp_path: Path) -> None:
    sm = SessionManager(tmp_path)
    assert sm.read_session_file("nope:none") is None
