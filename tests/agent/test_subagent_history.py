"""Tests per la retention Tier-2 dei subagent (la conversazione riprendibile)."""

from __future__ import annotations

import json
import time
from pathlib import Path

from jenny.agent.subagent_history import (
    HISTORY_TTL_S,
    MAX_HISTORY_PER_ORIGIN,
    SubagentHistoryStore,
)
from jenny.session.keys import (
    SUBAGENT_SESSION_PREFIX,
    is_internal_session_key,
    subagent_session_key,
)
from jenny.session.manager import SessionManager

_MESSAGES = [
    {"role": "system", "content": "you are a subagent"},
    {"role": "user", "content": "write the report"},
    {"role": "assistant", "content": "done, see report.md"},
]


def _store(tmp_path: Path, **kw) -> tuple[SubagentHistoryStore, SessionManager]:
    sessions = SessionManager(tmp_path)
    return SubagentHistoryStore(sessions, **kw), sessions


class TestSaveLoad:
    def test_round_trip(self, tmp_path: Path) -> None:
        store, _ = _store(tmp_path)
        assert store.save("L1", "unified:default", _MESSAGES) is True
        assert store.load("L1") == _MESSAGES

    def test_history_lives_under_the_subagent_session_prefix(self, tmp_path: Path) -> None:
        store, sessions = _store(tmp_path)
        store.save("L1", "unified:default", _MESSAGES)
        files = [p.name for p in sessions.sessions_dir.glob("*.jsonl")]
        assert files == ["subagent_L1.jsonl"]
        assert subagent_session_key("L1").startswith(SUBAGENT_SESSION_PREFIX)

    def test_save_invalidates_the_session_cache(self, tmp_path: Path) -> None:
        """Senza invalidate ogni conversazione di subagent resterebbe in RAM."""
        store, sessions = _store(tmp_path)
        store.save("L1", "unified:default", _MESSAGES)
        assert sessions._cache == {}
        # E la lettura non la reintroduce (usa read_session_file, non il cache).
        assert store.load("L1") == _MESSAGES
        assert sessions._cache == {}

    def test_load_of_unknown_lineage_is_none(self, tmp_path: Path) -> None:
        store, _ = _store(tmp_path)
        assert store.load("nope") is None

    def test_empty_messages_are_not_persisted(self, tmp_path: Path) -> None:
        store, _ = _store(tmp_path)
        assert store.save("L1", "o", []) is False
        assert store.save("L1", "o", None) is False
        assert store.load("L1") is None

    def test_disabled_store_degrades_instead_of_raising(self, tmp_path: Path) -> None:
        store = SubagentHistoryStore(None)
        assert store.enabled is False
        assert store.save("L1", "o", _MESSAGES) is False
        assert store.load("L1") is None
        store.drop("L1")  # no-op


class TestExpiry:
    def test_ttl_expiry_drops_the_history(self, tmp_path: Path) -> None:
        store, sessions = _store(tmp_path)
        store.save("L1", "o", _MESSAGES, now=time.time() - HISTORY_TTL_S - 60)
        assert store.load("L1") is None
        assert not (sessions.sessions_dir / "subagent_L1.jsonl").exists()

    def test_within_ttl_survives(self, tmp_path: Path) -> None:
        store, _ = _store(tmp_path)
        store.save("L1", "o", _MESSAGES, now=time.time() - HISTORY_TTL_S + 60)
        assert store.load("L1") == _MESSAGES

    def test_missing_timestamp_counts_as_expired(self, tmp_path: Path) -> None:
        store, sessions = _store(tmp_path)
        store.save("L1", "o", _MESSAGES)
        path = sessions.sessions_dir / "subagent_L1.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines()
        meta = json.loads(lines[0])
        meta["metadata"].pop("saved_at")
        path.write_text(
            "\n".join([json.dumps(meta)] + lines[1:]) + "\n", encoding="utf-8"
        )
        assert store.load("L1") is None

    def test_garbage_file_degrades_to_none(self, tmp_path: Path) -> None:
        """Un file corrotto porta al rilancio, non a un'eccezione."""
        store, sessions = _store(tmp_path)
        (sessions.sessions_dir / "subagent_L1.jsonl").write_text(
            "{not json at all\n\x00\x00", encoding="utf-8"
        )
        assert store.load("L1") is None

    def test_history_without_usable_messages_is_dropped(self, tmp_path: Path) -> None:
        store, sessions = _store(tmp_path)
        store.save("L1", "o", _MESSAGES)
        path = sessions.sessions_dir / "subagent_L1.jsonl"
        meta = path.read_text(encoding="utf-8").splitlines()[0]
        path.write_text(meta + "\n" + json.dumps({"no_role": 1}) + "\n", encoding="utf-8")
        assert store.load("L1") is None
        assert not path.exists()


class TestRetentionCap:
    def test_only_the_most_recent_lineages_per_origin_survive(
        self, tmp_path: Path
    ) -> None:
        store, sessions = _store(tmp_path)
        base = time.time()
        total = MAX_HISTORY_PER_ORIGIN + 2
        for i in range(total):
            store.save(f"L{i}", "unified:default", _MESSAGES, now=base + i)

        alive = sorted(p.name for p in sessions.sessions_dir.glob("subagent_*.jsonl"))
        assert alive == sorted(f"subagent_L{i}.jsonl" for i in range(2, total))
        assert store.load("L0") is None
        assert store.load(f"L{total - 1}") == _MESSAGES

    def test_the_cap_covers_every_slot_of_a_plan_step(self) -> None:
        """Un passo di piano fa girare ``PLAN_HELPER_SLOTS`` normali piu uno breve
        nella stessa sessione: col tetto a 3 i primi fermati al budget perdevano
        la storia prima che Jenny potesse farli continuare."""
        from jenny.agent.subagent import PLAN_HELPER_SLOTS

        assert MAX_HISTORY_PER_ORIGIN >= PLAN_HELPER_SLOTS + 1

    def test_finished_histories_go_before_budget_ones(self, tmp_path: Path) -> None:
        from jenny.agent.subagent_history import KIND_BUDGET

        store, _ = _store(tmp_path, max_per_origin=2)
        base = time.time()
        store.save("stopped", "project:nb", _MESSAGES, now=base, kind=KIND_BUDGET)
        store.save("done1", "project:nb", _MESSAGES, now=base + 1)
        store.save("done2", "project:nb", _MESSAGES, now=base + 2)

        # Il budget e il piu vecchio, ma aspetta di continuare: esce un ``done``.
        assert store.load("stopped") == _MESSAGES
        assert store.load("done1") is None
        assert store.has("stopped") and store.has("done2") and not store.has("done1")

    def test_the_kind_survives_a_restart(self, tmp_path: Path) -> None:
        from jenny.agent.subagent_history import KIND_BUDGET

        store, sessions = _store(tmp_path, max_per_origin=2)
        base = time.time()
        store.save("stopped", "project:nb", _MESSAGES, now=base, kind=KIND_BUDGET)
        store.save("done1", "project:nb", _MESSAGES, now=base + 1)
        fresh = SubagentHistoryStore(sessions, max_per_origin=2)
        fresh.save("done2", "project:nb", _MESSAGES, now=base + 2)
        assert fresh.load("stopped") == _MESSAGES
        assert fresh.load("done1") is None

    def test_cap_is_per_origin_session_key(self, tmp_path: Path) -> None:
        store, _ = _store(tmp_path, max_per_origin=1)
        base = time.time()
        store.save("A1", "origin-a", _MESSAGES, now=base)
        store.save("B1", "origin-b", _MESSAGES, now=base + 1)
        # Una seconda storia per "origin-a" espelle solo la sua, non quella di B.
        store.save("A2", "origin-a", _MESSAGES, now=base + 2)
        assert store.load("A1") is None
        assert store.load("A2") == _MESSAGES
        assert store.load("B1") == _MESSAGES

    def test_prune_invalidates_the_dropped_keys(self, tmp_path: Path) -> None:
        """La potatura deve passare da invalidate, non solo cancellare il file."""
        store, sessions = _store(tmp_path, max_per_origin=1)
        seen: list[str] = []
        original = sessions.invalidate
        sessions.invalidate = lambda key: (seen.append(key), original(key))[1]  # type: ignore[method-assign]

        base = time.time()
        store.save("A1", "o", _MESSAGES, now=base)
        store.save("A2", "o", _MESSAGES, now=base + 1)
        assert subagent_session_key("A1") in seen
        assert sessions._cache == {}

    def test_reconcile_adopts_files_left_by_a_dead_process(self, tmp_path: Path) -> None:
        """Un processo ucciso lascia file: il nuovo store li deve poter potare."""
        first, sessions = _store(tmp_path, max_per_origin=1)
        base = time.time()
        first.save("OLD", "o", _MESSAGES, now=base)

        fresh = SubagentHistoryStore(sessions, max_per_origin=1)
        fresh.save("NEW", "o", _MESSAGES, now=base + 1)
        assert fresh.load("OLD") is None
        assert fresh.load("NEW") == _MESSAGES


class TestKeys:
    def test_subagent_keys_are_internal(self) -> None:
        assert is_internal_session_key(subagent_session_key("L1")) is True
        assert is_internal_session_key("unified:default") is False
        assert is_internal_session_key("websocket:default") is False

    def test_webui_http_routes_cannot_read_a_subagent_session(self) -> None:
        """Le route /api/ accettano conversazioni, non lavoro interno.

        Test di sola lettura sul confine: se quel gate cambiasse, le storie dei
        subagent diventerebbero leggibili come conversazioni. Da quando esistono
        le sessioni-progetto il gate accetta due forme invece di una — una chat
        di progetto e' una conversazione — ma il lato che conta e' lo stesso:
        nessuna chiave interna passa.
        """
        from jenny.webui.ws_http import _is_webui_readable_session_key

        assert _is_webui_readable_session_key(subagent_session_key("L1")) is False
        assert _is_webui_readable_session_key("cron:job-1") is False
        assert _is_webui_readable_session_key("heartbeat") is False
        assert _is_webui_readable_session_key("websocket:default") is True
        assert _is_webui_readable_session_key("project:palestra") is True

    def test_dream_prune_glob_does_not_match_subagent_histories(
        self, tmp_path: Path
    ) -> None:
        from jenny.agent.memory import MemoryStore

        store, sessions = _store(tmp_path)
        store.save("L1", "o", _MESSAGES)
        assert MemoryStore.prune_dream_sessions(sessions.sessions_dir, keep=0) == []
        assert store.load("L1") == _MESSAGES
