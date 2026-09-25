"""Tests for session fsync and flush_all on graceful shutdown."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from jenny.session.manager import SessionManager


@pytest.fixture
def sessions_dir(tmp_path: Path) -> Path:
    d = tmp_path / "sessions"
    d.mkdir()
    return tmp_path


@pytest.fixture
def manager(sessions_dir: Path) -> SessionManager:
    return SessionManager(workspace=sessions_dir)


class TestSaveFsync:
    """Verify that save(fsync=True) calls os.fsync."""

    def test_save_without_fsync_does_not_call_fsync(self, manager: SessionManager):
        session = manager.get_or_create("test:no-fsync")
        session.add_message("user", "hello")

        with patch("os.fsync") as mock_fsync:
            manager.save(session, fsync=False)
            mock_fsync.assert_not_called()

    def test_save_with_fsync_calls_fsync(self, manager: SessionManager):
        session = manager.get_or_create("test:with-fsync")
        session.add_message("user", "hello")

        with patch("os.fsync") as mock_fsync:
            manager.save(session, fsync=True)
            # Android/POSIX: file fsync + directory fsync.
            assert mock_fsync.call_count == 2

    def test_save_default_no_fsync(self, manager: SessionManager):
        """Default save() should not fsync (backward compat)."""
        session = manager.get_or_create("test:default")
        session.add_message("user", "hello")

        with patch("os.fsync") as mock_fsync:
            manager.save(session)
            mock_fsync.assert_not_called()


class TestFlushAll:
    """Verify flush_all re-saves all cached sessions with fsync."""

    def test_flush_all_empty_cache(self, manager: SessionManager):
        assert manager.flush_all() == 0

    def test_flush_all_saves_cached_sessions(self, manager: SessionManager):
        s1 = manager.get_or_create("test:session-1")
        s1.add_message("user", "msg 1")
        manager.save(s1)

        s2 = manager.get_or_create("test:session-2")
        s2.add_message("user", "msg 2")
        manager.save(s2)

        flushed = manager.flush_all()
        assert flushed == 2

    def test_flush_all_uses_fsync(self, manager: SessionManager):
        session = manager.get_or_create("test:fsync-check")
        session.add_message("user", "important")
        manager.save(session)

        with patch("os.fsync") as mock_fsync:
            manager.flush_all()
            # Android/POSIX: file fsync + directory fsync.
            assert mock_fsync.call_count == 2

    def test_flush_all_continues_on_error(self, manager: SessionManager):
        """One broken session should not prevent others from flushing."""
        s1 = manager.get_or_create("test:good")
        s1.add_message("user", "ok")
        manager.save(s1)

        s2 = manager.get_or_create("test:bad")
        s2.add_message("user", "ok")
        manager.save(s2)

        original_save = manager.save
        call_count = {"n": 0}

        def patched_save(session, *, fsync=False):
            call_count["n"] += 1
            if session.key == "test:bad":
                raise OSError("disk on fire")
            original_save(session, fsync=fsync)

        manager.save = patched_save
        flushed = manager.flush_all()

        # One succeeded, one failed — flush_all returns successful count
        assert flushed == 1
        assert call_count["n"] == 2

    def test_flush_all_does_not_create_a_file_for_a_session_nobody_wrote(
        self, manager: SessionManager
    ):
        """Una sessione che ``get_or_create`` ha inventato solo per leggerla
        (dopo un rinomino di progetto, per esempio) non deve comparire su disco
        allo spegnimento: il file orfano sotto il nome vecchio bloccava il
        rinomino all'indietro con ``name_taken``."""
        manager.get_or_create("project:viaggio")
        assert manager.flush_all() == 0
        assert not manager._get_session_path("project:viaggio").exists()

    def test_flush_all_still_rewrites_an_emptied_session_on_disk(
        self, manager: SessionManager
    ):
        """Vuota ma con un file: la versione vuota è quella vera (``/new``),
        e il file vecchio coi messaggi non deve sopravviverle."""
        session = manager.get_or_create("test:cleared")
        session.add_message("user", "old")
        manager.save(session)
        session.messages.clear()
        assert manager.flush_all() == 1
        reloaded = SessionManager(workspace=manager.workspace).get_or_create("test:cleared")
        assert reloaded.messages == []

    def test_flush_all_keeps_metadata_written_without_a_save(
        self, manager: SessionManager
    ):
        session = manager.get_or_create("test:meta-only")
        session.metadata["title"] = "Viaggio"
        assert manager.flush_all() == 1
        assert manager._get_session_path("test:meta-only").exists()

    def test_flush_all_data_survives_reload(self, sessions_dir: Path):
        """Data flushed by flush_all should survive a fresh SessionManager load."""
        mgr1 = SessionManager(workspace=sessions_dir)
        session = mgr1.get_or_create("test:persist")
        session.add_message("user", "remember this")
        session.add_message("assistant", "noted")
        mgr1.save(session)
        mgr1.flush_all()

        # Simulate process restart — new manager, cold cache
        mgr2 = SessionManager(workspace=sessions_dir)
        reloaded = mgr2.get_or_create("test:persist")
        history = reloaded.get_history(max_messages=100)

        assert len(history) == 2
        assert history[0]["content"] == "remember this"
        assert history[1]["content"] == "noted"
