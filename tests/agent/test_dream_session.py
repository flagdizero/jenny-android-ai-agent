"""Tests for Dream session key generation and rotation."""
import asyncio
import os
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from support.agent import make_loop

from jenny.agent.memory import MemoryStore


class TestDreamSessionKey:
    def test_contains_timestamp(self):
        key = MemoryStore.dream_session_key()
        assert key.startswith("dream:")
        ts_part = key.split(":", 1)[1]
        datetime.strptime(ts_part, "%Y%m%d-%H%M%S")

    def test_unique_across_calls(self):
        """E resta unica di proposito, non per caso.

        È il motivo per cui il lock per sessione **non** esclude due run di Dream
        fra loro — chiave diversa, lock diverso — e la tentazione è collassarla su
        un valore fisso per ottenere la mutua esclusione gratis. Non si fa: la
        chiave nomina il file di sessione (``sessions/dream_<ts>.jsonl``), ed è
        l'unica cosa che ``prune_internal_sessions`` ordina e pota (tiene i 10 più
        recenti, ``TestPruneDreamSessions`` qui sotto), che l'eviction delle cache
        di ``AgentLoop`` usa come identità, e che il transcript e
        ``docs/internals/agent-turn.md`` documentano come "una sessione per run".
        Fissarla farebbe di ogni run un'aggiunta allo stesso file, che nessuno
        pota più. La mutua esclusione sta altrove, in
        ``dream_cycle.claim_dream_cycle``.
        """
        now = datetime(2026, 5, 28, 10, 0, 0)
        with patch("jenny.agent.memory.datetime") as mock_dt:
            mock_dt.now.side_effect = [now, now + timedelta(seconds=1)]
            k1 = MemoryStore.dream_session_key()
            k2 = MemoryStore.dream_session_key()

        assert k1 != k2


class TestPruneDreamSessions:
    def test_keeps_n_most_recent(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        for i in range(15):
            key = f"dream:20260528-{100000 + i:06d}"
            safe_key = key.replace(":", "_")
            path = sessions_dir / f"{safe_key}.jsonl"
            path.write_text(
                f'{{"_type": "metadata", "key": "{key}", '
                f'"created_at": "2026-05-28T10:00:{i:02d}", '
                f'"updated_at": "2026-05-28T10:00:{i:02d}"}}\n',
                encoding="utf-8",
            )

        normal_path = sessions_dir / "websocket_123.jsonl"
        normal_path.write_text('{"_type": "metadata"}\n', encoding="utf-8")

        MemoryStore.prune_dream_sessions(sessions_dir, keep=10)

        dream_files = sorted(sessions_dir.glob("dream_*.jsonl"))
        assert len(dream_files) == 10
        remaining_keys = [f.stem for f in dream_files]
        assert "dream_20260528-100000" not in remaining_keys
        assert "dream_20260528-100014" in remaining_keys
        assert normal_path.exists()

    def test_noop_when_under_limit(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        for i in range(3):
            key = f"dream:20260528-{100000 + i:06d}"
            safe_key = key.replace(":", "_")
            (sessions_dir / f"{safe_key}.jsonl").write_text("{}", encoding="utf-8")

        MemoryStore.prune_dream_sessions(sessions_dir, keep=10)
        assert len(list(sessions_dir.glob("dream_*.jsonl"))) == 3

    def test_empty_dir_noop(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        MemoryStore.prune_dream_sessions(sessions_dir, keep=10)
        assert list(sessions_dir.iterdir()) == []

    def test_returns_original_session_keys_of_removed_files(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        keys = [f"dream:20260528-{100000 + i:06d}" for i in range(15)]
        for i, key in enumerate(keys):
            path = sessions_dir / f"{key.replace(':', '_')}.jsonl"
            path.write_text(
                f'{{"_type": "metadata", "key": "{key}"}}\n', encoding="utf-8"
            )
            os.utime(path, (i, i))

        removed = MemoryStore.prune_dream_sessions(sessions_dir, keep=10)

        assert set(removed) == set(keys[:5])
        assert removed == sorted(removed, key=lambda k: keys.index(k))

    def test_noop_prune_returns_empty_list(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        for i in range(3):
            key = f"dream:20260528-{100000 + i:06d}"
            (sessions_dir / f"{key.replace(':', '_')}.jsonl").write_text("{}", encoding="utf-8")
        assert MemoryStore.prune_dream_sessions(sessions_dir, keep=10) == []


class TestEvictPrunedSessions:
    """Cover the leak fix: pruned Dream session files must also drop their
    ``SessionManager`` cache entry and ``AgentLoop`` task/lock bookkeeping,
    unless the session is genuinely still in flight.
    """

    def _make_loop(self, tmp_path):
        from jenny.session.manager import SessionManager

        workspace = MagicMock()
        workspace.__truediv__ = MagicMock(return_value=MagicMock())
        return make_loop(
            workspace, model=None, context_window_tokens=None, bare=True,
            patches=("jenny.agent.loop.ContextBuilder", "jenny.agent.loop.SubagentManager"),
            session_manager=SessionManager(tmp_path),
        )

    @pytest.mark.asyncio
    async def test_pruned_keys_evicted_finished_keys_kept(self, tmp_path):
        loop = self._make_loop(tmp_path)

        keys = [f"dream:20260528-{100000 + i:06d}" for i in range(15)]
        for i, key in enumerate(keys):
            session = loop.sessions.get_or_create(key)
            loop.sessions.save(session)
            os.utime(loop.sessions._get_session_path(key), (i, i))

            # Populate the same bookkeeping process_direct() creates for a
            # real Dream run, all already finished/released.
            loop._session_locks.get(key)  # crea l'entry del lock (non tenuto)
            done_task = asyncio.create_task(asyncio.sleep(0))
            await done_task
            loop._active_tasks[key] = [done_task]

        assert all(k in loop.sessions._cache for k in keys)
        assert all(k in loop._session_locks for k in keys)
        assert all(k in loop._active_tasks for k in keys)

        pruned_keys = MemoryStore.prune_dream_sessions(loop.sessions.sessions_dir, keep=10)
        assert set(pruned_keys) == set(keys[:5])

        loop.evict_pruned_sessions(pruned_keys)

        for key in keys[:5]:
            assert key not in loop.sessions._cache
            assert key not in loop._session_locks
            assert key not in loop._active_tasks
        for key in keys[5:]:
            assert key in loop.sessions._cache
            assert key in loop._session_locks
            assert key in loop._active_tasks

    @pytest.mark.asyncio
    async def test_held_lock_is_not_evicted(self, tmp_path):
        """A session mid-consolidation (lock held) must survive eviction."""
        loop = self._make_loop(tmp_path)
        key = "dream:20260528-100000"
        session = loop.sessions.get_or_create(key)
        loop.sessions.save(session)

        # Tieni il lock condiviso della sessione durante l'eviction.
        async with loop._session_locks.get(key):
            loop.evict_pruned_sessions([key])
            assert key in loop.sessions._cache
            assert key in loop._session_locks

    @pytest.mark.asyncio
    async def test_unfinished_task_is_not_evicted(self, tmp_path):
        """A session with a still-running task must survive eviction."""
        loop = self._make_loop(tmp_path)
        key = "dream:20260528-100001"
        session = loop.sessions.get_or_create(key)
        loop.sessions.save(session)

        running = asyncio.Event()

        async def _hang():
            await running.wait()

        task = asyncio.create_task(_hang())
        loop._active_tasks[key] = [task]

        loop.evict_pruned_sessions([key])

        assert key in loop.sessions._cache
        assert key in loop._active_tasks

        running.set()
        await task
