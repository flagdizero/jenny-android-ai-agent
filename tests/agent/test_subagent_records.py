"""Tests per la retention Tier-1 dei subagent (spec + record su disco)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from jenny.agent.subagent_records import (
    MAX_RECORDS_PER_SESSION,
    RECORD_TTL_S,
    SubagentRecord,
    SubagentRecordStore,
    SubagentSpec,
    default_label,
)
from jenny.security.workspace_access import (
    WorkspaceScope,
    workspace_sandbox_status,
)


def _record(
    task_id: str = "t1",
    *,
    lineage: str = "L1",
    attempt: int = 1,
    session_key: str | None = "s1",
    ended_at: float | None = None,
    state: str = "done",
    summary: str = "ok",
) -> SubagentRecord:
    stamp = time.time() if ended_at is None else ended_at
    return SubagentRecord(
        task_id=task_id,
        lineage_id=lineage,
        attempt=attempt,
        spec=SubagentSpec(task="do the thing", label="lbl", session_key=session_key),
        state=state,
        stop_reason="completed",
        result_summary=summary,
        started_at=stamp - 1,
        ended_at=stamp,
    )


# ---------------------------------------------------------------------------
# SubagentSpec
# ---------------------------------------------------------------------------


class TestSubagentSpec:
    def test_round_trip_preserves_fields(self):
        spec = SubagentSpec(
            task="research",
            label="lbl",
            agent_type="operator",
            temperature=0.7,
            origin_channel="websocket",
            origin_chat_id="42",
            session_key="unified:default",
            origin_message_id="m-1",
            quick=True,
        )
        assert SubagentSpec.from_dict(spec.to_dict()) == spec

    def test_round_trip_rebuilds_workspace_scope(self, tmp_path: Path):
        scope = WorkspaceScope(
            project_path=tmp_path,
            access_mode="restricted",
            restrict_to_workspace=True,
            sandbox_status=workspace_sandbox_status(
                restrict_to_workspace=True, workspace=tmp_path
            ),
        )
        spec = SubagentSpec(task="t", label="l", workspace_scope=scope)
        restored = SubagentSpec.from_dict(spec.to_dict())
        assert restored.workspace_scope is not None
        assert restored.workspace_scope.project_path == tmp_path
        assert restored.workspace_scope.access_mode == "restricted"
        assert restored.workspace_scope.restrict_to_workspace is True

    def test_extra_instructions_appended_as_note(self):
        spec = SubagentSpec(task="original task", label="l")
        patched = spec.with_extra_instructions("  do not use grep  ")
        assert patched is not spec
        assert patched.task.startswith("original task")
        assert "do not use grep" in patched.task
        # La spec originale resta intatta: e la descrizione del lavoro.
        assert spec.task == "original task"

    def test_extra_instructions_empty_returns_same_spec(self):
        spec = SubagentSpec(task="t", label="l")
        assert spec.with_extra_instructions(None) is spec
        assert spec.with_extra_instructions("   ") is spec

    def test_records_key_falls_back_to_origin(self):
        spec = SubagentSpec(task="t", label="l", origin_channel="telegram", origin_chat_id="9")
        assert spec.records_key == "telegram:9"
        assert SubagentSpec(task="t", label="l", session_key="s1").records_key == "s1"

    def test_from_dict_rejects_spec_without_task(self):
        with pytest.raises(ValueError):
            SubagentSpec.from_dict({"label": "l"})


# ---------------------------------------------------------------------------
# SubagentRecordStore — retention
# ---------------------------------------------------------------------------


class TestRecordStoreRetention:
    def test_append_and_load_round_trip(self, tmp_path: Path):
        store = SubagentRecordStore(tmp_path)
        store.append(_record("t1", summary="found it"))
        loaded = store.load("s1")
        assert [r.task_id for r in loaded] == ["t1"]
        assert loaded[0].result_summary == "found it"
        assert loaded[0].spec.task == "do the thing"

    def test_records_live_under_workspace_subagents_dir(self, tmp_path: Path):
        store = SubagentRecordStore(tmp_path)
        store.append(_record("t1"))
        assert (tmp_path / "subagents" / "records" / "s1.jsonl").is_file()

    def test_session_key_is_sanitized_into_filename(self, tmp_path: Path):
        store = SubagentRecordStore(tmp_path)
        store.append(_record("t1", session_key="websocket:chat/1"))
        files = list((tmp_path / "subagents" / "records").glob("*.jsonl"))
        assert len(files) == 1
        assert ":" not in files[0].name and "/" not in files[0].stem

    def test_cap_keeps_only_last_twenty_per_session(self, tmp_path: Path):
        store = SubagentRecordStore(tmp_path)
        now = time.time()
        for i in range(25):
            store.append(_record(f"t{i}", ended_at=now + i))
        loaded = store.load("s1")
        assert len(loaded) == MAX_RECORDS_PER_SESSION == 20
        assert [r.task_id for r in loaded] == [f"t{i}" for i in range(5, 25)]

    def test_cap_is_per_session_key(self, tmp_path: Path):
        store = SubagentRecordStore(tmp_path)
        store.append(_record("a1", session_key="s1"))
        store.append(_record("b1", session_key="s2"))
        assert [r.task_id for r in store.load("s1")] == ["a1"]
        assert [r.task_id for r in store.load("s2")] == ["b1"]
        assert len(store.load_all()) == 2

    def test_ttl_drops_expired_records(self, tmp_path: Path):
        store = SubagentRecordStore(tmp_path)
        now = time.time()
        store.append(_record("old", ended_at=now - RECORD_TTL_S - 10), now=now)
        store.append(_record("fresh", ended_at=now), now=now)
        assert [r.task_id for r in store.load("s1", now=now)] == ["fresh"]

    def test_ttl_expiry_is_persisted_on_next_append(self, tmp_path: Path):
        store = SubagentRecordStore(tmp_path)
        now = time.time()
        store.append(_record("old", ended_at=now - RECORD_TTL_S - 10), now=now)
        store.append(_record("fresh", ended_at=now), now=now)
        raw = (tmp_path / "subagents" / "records" / "s1.jsonl").read_text(encoding="utf-8")
        assert "old" not in raw

    def test_find_by_task_id_and_lineage(self, tmp_path: Path):
        store = SubagentRecordStore(tmp_path)
        now = time.time()
        store.append(_record("t1", lineage="L1", attempt=1, ended_at=now))
        store.append(_record("t2", lineage="L1", attempt=2, ended_at=now + 1))
        assert store.find("t1").task_id == "t1"
        # Su un lineage con piu tentativi vince l'attempt piu alto.
        assert store.find("L1").attempt == 2
        assert store.find("nope") is None


# ---------------------------------------------------------------------------
# SubagentRecordStore — tolleranza ai file rotti
# ---------------------------------------------------------------------------


class TestRecordStoreCorruption:
    def _path(self, store: SubagentRecordStore, key: str = "s1") -> Path:
        path = store._path_for(key)
        assert path is not None
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def test_truncated_line_is_skipped_not_raised(self, tmp_path: Path):
        store = SubagentRecordStore(tmp_path)
        good = json.dumps(_record("good").to_dict(), ensure_ascii=False)
        path = self._path(store)
        # Ultima riga troncata: kill a metà scrittura.
        path.write_text(f"{good}\n{good[:40]}", encoding="utf-8")
        loaded = store.load("s1")
        assert [r.task_id for r in loaded] == ["good"]

    def test_garbage_and_wrong_shape_lines_are_skipped(self, tmp_path: Path):
        store = SubagentRecordStore(tmp_path)
        good = json.dumps(_record("good").to_dict(), ensure_ascii=False)
        path = self._path(store)
        path.write_text(
            "\n".join([
                "not json at all",
                json.dumps({"task_id": "no-spec"}),
                json.dumps([1, 2, 3]),
                "",
                good,
            ]),
            encoding="utf-8",
        )
        assert [r.task_id for r in store.load("s1")] == ["good"]

    def test_fully_corrupt_file_loads_as_empty(self, tmp_path: Path):
        store = SubagentRecordStore(tmp_path)
        self._path(store).write_text("\x00\x01 broken", encoding="utf-8")
        assert store.load("s1") == []
        assert store.load_all() == []

    def test_append_after_corruption_still_persists(self, tmp_path: Path):
        store = SubagentRecordStore(tmp_path)
        self._path(store).write_text("garbage\n", encoding="utf-8")
        store.append(_record("t1"))
        assert [r.task_id for r in store.load("s1")] == ["t1"]

    def test_missing_file_loads_as_empty(self, tmp_path: Path):
        store = SubagentRecordStore(tmp_path)
        assert store.load("never-written") == []
        assert store.find("whatever") is None

    def test_unusable_workspace_is_a_noop(self):
        """Il manager deve restare costruibile anche senza un workspace usabile."""
        store = SubagentRecordStore(object())
        assert store.root is None
        store.append(_record("t1"))  # nessuna eccezione
        assert store.load("s1") == []
        assert store.load_all() == []


def test_the_default_label_stops_at_a_word() -> None:
    """Senza label la casa mostra le prime parole del task: mai mezza parola."""
    assert default_label("Research the best way to configure the home router firewall") == (
        "Research the best way to configure the…"
    )
    assert default_label("  short\n task ") == "short task"
    assert default_label("A" * 50) == "A" * 40 + "…"
