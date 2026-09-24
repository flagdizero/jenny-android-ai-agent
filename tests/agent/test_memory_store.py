"""Tests for the restructured MemoryStore — pure file I/O layer."""

import json
import threading
import time

import pytest

from jenny.agent.memory import _HISTORY_ENTRY_HARD_CAP, MemoryStore


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path)


class TestMemoryStoreCore:
    def test_read_memory_returns_empty_when_missing(self, store):
        assert store.read_memory() == ""

    def test_write_and_read_memory(self, store):
        store.memory_file.write_text("hello", encoding="utf-8")
        assert store.read_memory() == "hello"

    def test_get_memory_context_returns_empty_when_missing(self, store):
        assert store.get_memory_context() == ""

    def test_get_memory_context_returns_formatted_content(self, store):
        store.memory_file.write_text("important fact", encoding="utf-8")
        ctx = store.get_memory_context()
        assert "Long-term Memory" in ctx
        assert "important fact" in ctx

    def test_get_memory_context_is_not_capped(self, store):
        """Il blocco entra **intero**, e l'assenza di un tetto in lettura è una scelta.

        Non è una dimenticanza da chiudere aggiungendo un
        ``truncate_text_to_tokens`` come quello di
        :meth:`MemoryStore.get_wiki_memory_context`. Le due cose non sono
        simmetriche:

        - Il tetto in scrittura ha dietro **la review pass**, che legge il file
          prima di decidere e *sposta* quel che non gli appartiene. Un taglio al
          momento dell'iniezione non ha nessuno dietro: non distingue l'identità
          da una nota stantia, taglia quel che capita in coda, e lo fa a ogni
          turno senza dirlo a nessuno che possa rimediare.
        - L'elenco delle wiki è un **indice**: quel che il tetto le toglie è un
          link, e la pagina resta a un ``read_file`` di distanza. La coda di
          ``MEMORY.md`` non è scritta in nessun altro posto, quindi un avviso
          "il resto è là" non avrebbe dove puntare.

        La decisione e l'argomento stanno in ``docs/using/memory.md``
        ("The budgets bound what Dream writes, not what a turn pays"). Se un
        giorno un tetto serve davvero, questo test va cambiato di proposito — e
        insieme a lui quella sezione.
        """
        body = "\n".join(f"- fact number {i}" for i in range(2000))
        store.memory_file.write_text(body, encoding="utf-8")
        assert store.get_memory_context() == f"## Long-term Memory\n{body}"


class TestHistoryWithCursor:
    def test_append_history_returns_cursor(self, store):
        cursor = store.append_history("event 1")
        assert cursor == 1
        cursor2 = store.append_history("event 2")
        assert cursor2 == 2

    def test_append_history_includes_cursor_in_file(self, store):
        store.append_history("event 1")
        content = store.read_file(store.history_file)
        data = json.loads(content)
        assert data["cursor"] == 1

    def test_append_history_includes_session_key_when_provided(self, store):
        store.append_history("event 1", session_key="websocket:chat-1")
        content = store.read_file(store.history_file)
        data = json.loads(content)
        assert data["session_key"] == "websocket:chat-1"

    def test_cursor_persists_across_appends(self, store):
        store.append_history("event 1")
        store.append_history("event 2")
        cursor = store.append_history("event 3")
        assert cursor == 3

    def test_append_history_strips_thinking_content(self, store):
        """`strip_think` must run before persistence — well-formed thinking
        blocks shouldn't land in history."""
        cursor = store.append_history("<think>reasoning</think>final answer")
        content = store.read_file(store.history_file)
        data = json.loads(content)
        assert data["cursor"] == cursor
        assert data["content"] == "final answer"

    def test_append_history_drops_pure_leak_content(self, store):
        """Regression: entries that strip down to empty (pure template-token
        leak) must NOT fall back to the raw leak. Persisting the raw text
        would re-pollute context via consolidation / replay, undoing the
        protection `strip_think` provides."""
        cursor = store.append_history("<think>nothing user-facing</think>")
        content = store.read_file(store.history_file)
        data = json.loads(content)
        assert data["cursor"] == cursor
        assert data["content"] == ""

    def test_append_history_drops_malformed_leak_prefix(self, store):
        """Channel-marker / malformed opening leaks should not survive."""
        cursor = store.append_history("<channel|>")
        content = store.read_file(store.history_file)
        data = json.loads(content)
        assert data["cursor"] == cursor
        assert data["content"] == ""

    def test_read_unprocessed_history(self, store):
        store.append_history("event 1")
        store.append_history("event 2")
        store.append_history("event 3")
        entries = store.read_unprocessed_history(since_cursor=1)
        assert len(entries) == 2
        assert entries[0]["cursor"] == 2

    def test_read_unprocessed_history_returns_all_when_cursor_zero(self, store):
        store.append_history("event 1")
        store.append_history("event 2")
        entries = store.read_unprocessed_history(since_cursor=0)
        assert len(entries) == 2

    def test_unified_prompt_history_excludes_internal_cron_sessions(self, store):
        store.append_history("legacy entry without session")
        store.append_history("unified entry", session_key="unified:default")
        store.append_history("channel entry", session_key="websocket:chat-1")
        store.append_history("cron internal entry", session_key="cron:job-1")

        entries = store.read_recent_history_for_prompt(
            since_cursor=0,
            session_key="unified:default",

        )

        assert [e["content"] for e in entries] == [
            "legacy entry without session",
            "unified entry",
            "channel entry",
        ]

    def test_unified_cron_prompt_history_includes_own_cron_entry(self, store):
        store.append_history("unified entry", session_key="unified:default")
        store.append_history("other cron entry", session_key="cron:job-2")
        store.append_history("own cron entry", session_key="cron:job-1")

        entries = store.read_recent_history_for_prompt(
            since_cursor=0,
            session_key="cron:job-1",

        )

        assert [e["content"] for e in entries] == ["unified entry", "own cron entry"]

    def test_read_unprocessed_skips_entries_without_cursor(self, store):
        """Regression: entries missing the cursor key should be silently skipped."""
        store.history_file.write_text(
            '{"timestamp": "2026-04-01 10:00", "content": "no cursor"}\n'
            '{"cursor": 2, "timestamp": "2026-04-01 10:01", "content": "valid"}\n'
            '{"cursor": 3, "timestamp": "2026-04-01 10:02", "content": "also valid"}\n',
            encoding="utf-8",
        )
        entries = store.read_unprocessed_history(since_cursor=0)
        assert [e["cursor"] for e in entries] == [2, 3]

    def test_read_unprocessed_skips_malformed_history_payloads(self, store):
        """Externally edited JSONL can keep an int cursor but miss required payload fields."""
        store.history_file.write_text(
            '{"cursor": 1, "timestamp": "2026-04-01 10:00", "content": "valid"}\n'
            '{"cursor": 2, "timestamp": "2026-04-01 10:01"}\n'
            '{"cursor": 3, "content": "missing timestamp"}\n'
            '{"cursor": 4, "timestamp": "2026-04-01 10:03", "content": 123}\n'
            '{"cursor": 5, "timestamp": "2026-04-01 10:04", "content": "bad session", "session_key": 42}\n'
            '{"cursor": 6, "timestamp": "2026-04-01 10:05", "content": "also valid", "session_key": "websocket:chat-1"}\n',
            encoding="utf-8",
        )

        entries = store.read_unprocessed_history(since_cursor=0)

        assert [e["cursor"] for e in entries] == [1, 6]
        assert [e["content"] for e in entries] == ["valid", "also valid"]

    def test_next_cursor_falls_back_when_last_entry_has_no_cursor(self, store):
        """Regression: _next_cursor should not KeyError on entries without cursor."""
        store.history_file.write_text(
            '{"timestamp": "2026-04-01 10:01", "content": "no cursor"}\n',
            encoding="utf-8",
        )
        # Delete .cursor file so _next_cursor falls back to reading JSONL
        store._cursor_file.unlink(missing_ok=True)
        # Last entry has no cursor — should safely return 1, not KeyError
        cursor = store.append_history("new event")
        assert cursor == 1

    def test_append_history_allocates_unique_cursors_under_concurrent_writes(self, store):
        """Regression: concurrent appends must not allocate duplicate cursors."""
        import threading

        writers = 16
        start = threading.Barrier(writers)
        cursors: list[int] = []
        lock = threading.Lock()

        def worker(i):
            start.wait()
            c = store.append_history(f"event {i}")
            with lock:
                cursors.append(c)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(writers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(cursors) == writers
        assert len(set(cursors)) == writers, f"duplicate cursors: {sorted(cursors)}"
        assert sorted(cursors) == list(range(1, writers + 1))
        persisted = store.read_unprocessed_history(since_cursor=0)
        assert sorted(e["cursor"] for e in persisted) == list(range(1, writers + 1))

    def test_compact_history_drops_oldest(self, tmp_path):
        store = MemoryStore(tmp_path, max_history_entries=2)
        store.append_history("event 1")
        store.append_history("event 2")
        store.append_history("event 3")
        store.append_history("event 4")
        store.append_history("event 5")
        store.compact_history()
        entries = store.read_unprocessed_history(since_cursor=0)
        assert len(entries) == 2
        assert entries[0]["cursor"] in {4, 5}

    def test_compact_history_serializes_with_concurrent_append(self, tmp_path):
        """A ``append_history`` landing during compact's read→rewrite window must
        not be lost by the atomic overwrite. ``compact_history`` holds
        ``_append_lock`` across read+write, so a concurrent append blocks until
        the rewrite completes, then appends on top of the compacted file.

        Determinism: ``_read_entries`` is patched to signal it entered the
        critical section, then wait; a second thread attempts an append while
        compact holds the lock. On unfixed code the append would complete
        immediately (lock not held) and the assertion below fails; the entry
        would also be dropped by the rewrite.
        """
        store = MemoryStore(tmp_path, max_history_entries=3)
        for i in range(5):
            store.append_history(f"event {i}")  # cursors 1..5

        inside_compact = threading.Event()
        resume_compact = threading.Event()
        append_done = threading.Event()

        orig_read = store._read_entries

        def slow_read():
            entries = orig_read()
            inside_compact.set()  # compact is now inside the locked section
            resume_compact.wait(timeout=2.0)
            return entries

        store._read_entries = slow_read

        def appender():
            inside_compact.wait(timeout=2.0)
            store.append_history("late entry")  # blocks on _append_lock
            append_done.set()

        t_append = threading.Thread(target=appender)
        t_compact = threading.Thread(target=store.compact_history)
        t_append.start()
        t_compact.start()

        # While compact holds the lock, the append cannot complete.
        inside_compact.wait(timeout=2.0)
        time.sleep(0.1)
        assert not append_done.is_set(), "append ran without waiting on the lock"

        resume_compact.set()
        t_compact.join(timeout=2.0)
        t_append.join(timeout=2.0)
        assert append_done.is_set()

        contents = [e["content"] for e in store.read_unprocessed_history(since_cursor=0)]
        # The concurrent append survived the compaction rewrite.
        assert "late entry" in contents
        # Compaction still dropped the oldest entries down to the cap + 1 append.
        assert len(contents) == store.max_history_entries + 1

    def test_write_entries_uses_atomic_write(self, tmp_path):
        """_write_entries uses temp file + os.replace for atomicity."""
        store = MemoryStore(tmp_path)
        store.append_history("event 1")
        store.append_history("event 2")
        store.append_history("event 3")
        entries = store.read_unprocessed_history(since_cursor=0)

        # Monitor temp file existence
        tmp_path_obj = store.history_file.with_suffix(".jsonl.tmp")
        assert not tmp_path_obj.exists()  # Should not exist initially

        # Call _write_entries
        store._write_entries(entries)

        # Temp file should be cleaned up
        assert not tmp_path_obj.exists()
        # Original file should exist
        assert store.history_file.exists()

    def test_write_entries_cleans_up_tmp_on_exception(self, tmp_path, monkeypatch):
        """Exception during _write_entries cleans up the temp file."""
        store = MemoryStore(tmp_path)
        store.append_history("event 1")
        entries = store.read_unprocessed_history(since_cursor=0)

        tmp_path_obj = store.history_file.with_suffix(".jsonl.tmp")

        # Mock os.replace to raise an exception
        def failing_replace(*args, **kwargs):
            raise RuntimeError("Simulated failure")

        monkeypatch.setattr('os.replace', failing_replace)

        with pytest.raises(RuntimeError):
            store._write_entries(entries)

        # Temp file should be cleaned up
        assert not tmp_path_obj.exists()

        # Original file should still exist (because replace failed)
        assert store.history_file.exists()

    def test_append_history_fsyncs_history_file(self, store, monkeypatch):
        """append_history must fsync the history file, not just write it.

        Regression test for a durability bug where the append path had no
        flush()/os.fsync() at all, so an appended record (e.g. a
        consolidation summary) could be lost on a crash even though the
        original messages it replaced were already gone from memory.
        """
        import os as os_module

        real_fsync = os_module.fsync
        fsynced_fds: list[int] = []

        def spy_fsync(fd):
            fsynced_fds.append(fd)
            return real_fsync(fd)

        # Sul modulo ``os``: dal 24/09/2026 la scrittura passa da
        # ``utils.path.append_lines_durable``, e conta che il fsync avvenga, non
        # da quale modulo parta.
        monkeypatch.setattr(os_module, "fsync", spy_fsync)

        store.append_history("event 1")

        assert fsynced_fds, "os.fsync was never called during append_history"
        # The synced content must actually be on disk under the fd fsync'd.
        assert store.history_file.exists()
        assert "event 1" in store.history_file.read_text(encoding="utf-8")

    def test_append_history_writes_cursor_file_atomically(self, store, monkeypatch):
        """The cursor file must be replaced via atomic_write (temp+fsync+rename),
        matching how every other small full-file state blob in this codebase
        (cron store, session manager, sidebar state, ...) is persisted."""
        calls = []
        import jenny.agent.memory as memory_module

        real_atomic_write = memory_module.atomic_write

        def spy_atomic_write(path, content, **kwargs):
            calls.append(path)
            return real_atomic_write(path, content, **kwargs)

        monkeypatch.setattr(memory_module, "atomic_write", spy_atomic_write)

        store.append_history("event 1")

        assert store._cursor_file in calls
        assert store._cursor_file.read_text(encoding="utf-8").strip() == "1"

    def test_read_unprocessed_history_handles_entries_without_cursor(self, store):
        """JSONL entries with cursor=1 are correctly parsed and returned."""
        store.history_file.write_text(
            '{"cursor": 1, "timestamp": "2026-03-30 14:30", "content": "Old event"}\n',
            encoding="utf-8",
        )
        entries = store.read_unprocessed_history(since_cursor=0)
        assert len(entries) == 1
        assert entries[0]["cursor"] == 1


class TestAppendHistoryHardCap:
    """append_history has a defensive cap that catches new callers who forgot
    to set their own tighter cap. The default is intentionally larger than
    any current caller's per-call cap, so normal operation never trips it."""

    def test_oversized_entry_is_truncated(self, store):
        """An entry above _HISTORY_ENTRY_HARD_CAP is truncated before being persisted."""
        huge = "x" * (_HISTORY_ENTRY_HARD_CAP + 10_000)
        store.append_history(huge)
        entry = store.read_unprocessed_history(since_cursor=0)[0]
        assert len(entry["content"]) <= _HISTORY_ENTRY_HARD_CAP + 50

    def test_oversize_warning_is_emitted_once(self, store, caplog):
        """Repeated oversized writes should warn only on the first occurrence."""
        from loguru import logger as loguru_logger

        records: list[str] = []
        handler_id = loguru_logger.add(lambda m: records.append(m), level="WARNING")
        try:
            huge = "x" * (_HISTORY_ENTRY_HARD_CAP + 1)
            store.append_history(huge)
            store.append_history(huge)
            store.append_history(huge)
        finally:
            loguru_logger.remove(handler_id)

        oversize_warnings = [r for r in records if "exceeds" in r and "chars" in r]
        assert len(oversize_warnings) == 1

    def test_custom_max_chars_overrides_default(self, store):
        """Callers that pass max_chars should get their tighter cap applied."""
        store.append_history("a" * 500, max_chars=100)
        entry = store.read_unprocessed_history(since_cursor=0)[0]
        assert len(entry["content"]) <= 150  # 100 + "\n... (truncated)"

    def test_normal_sized_entries_unaffected(self, store):
        """The hard cap must not alter entries that fit within it."""
        msg = "normal short entry"
        store.append_history(msg)
        entry = store.read_unprocessed_history(since_cursor=0)[0]
        assert entry["content"] == msg


class TestDreamCursor:
    def test_initial_cursor_is_zero(self, store):
        assert store.get_last_dream_cursor() == 0

    def test_set_and_get_cursor(self, store):
        store.set_last_dream_cursor(5)
        assert store.get_last_dream_cursor() == 5

    def test_cursor_persists(self, store):
        store.set_last_dream_cursor(3)
        store2 = MemoryStore(store.workspace)
        assert store2.get_last_dream_cursor() == 3

    def test_failed_write_leaves_previous_cursor_readable(self, store, monkeypatch):
        """La scrittura passa dall'helper atomico, non da un write_text nudo.

        Con write_text, il file veniva troncato prima di fallire e il cursore
        tornava 0: Dream ricominciava da capo su tutta la storia. Il raise finto
        qui dimostra entrambe le cose — che l'helper è sulla strada, e che il
        contenuto precedente sopravvive a una scrittura fallita.
        """
        store.set_last_dream_cursor(3)

        def boom(*_args, **_kwargs):
            raise OSError("no space left on device")

        monkeypatch.setattr("jenny.agent.memory.atomic_write", boom)
        with pytest.raises(OSError):
            store.set_last_dream_cursor(9)
        assert store.get_last_dream_cursor() == 3
