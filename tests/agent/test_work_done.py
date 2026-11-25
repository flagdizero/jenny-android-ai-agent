"""Il pezzo comune di «cosa ha toccato» e del suo annulla (``agent.work_done``)."""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny import db as jdb
from jenny.agent.work_done import journal_file_items, undo_journals
from jenny.agent.write_journal import (
    UndoConflictError,
    bind_journal,
    journal_for,
    journal_key,
)
from jenny.security.subagent_policy import check_write, settle_writes


@pytest.fixture(autouse=True)
def _close_databases():
    yield
    jdb.close_all()


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    (root / "notes").mkdir(parents=True)
    (root / "notes" / "a.md").write_text("a0", encoding="utf-8")
    return root


def _write(ws: Path, key: str, rel: str, text: str) -> None:
    path = ws / rel
    with bind_journal(journal_for(ws, key)):
        check_write(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        settle_writes("write_file")


def test_the_file_list_names_each_file_once_and_no_folder(ws: Path) -> None:
    _write(ws, "peer-q1", "notes/a.md", "a1")
    _write(ws, "helper-l1", "notes/new/b.md", "b1")
    _write(ws, "helper-l1", "notes/a.md", "a2")
    items = journal_file_items(ws, ["peer-q1", "helper-l1", "peer-missing"])
    assert items == [
        {"kind": "file", "label": "notes/a.md", "undoable": True},
        {"kind": "file", "label": "notes/new/b.md", "undoable": True},
    ]


def test_several_journals_are_undone_together_into_their_own(ws: Path) -> None:
    _write(ws, "peer-q1", "notes/a.md", "a1")
    _write(ws, "helper-l1", "notes/new/b.md", "b1")
    into = journal_key("undo-peer", "q1")
    preview = undo_journals(ws, ["peer-q1", "helper-l1"], into, dry_run=True)
    assert preview is not None and preview.token
    assert (ws / "notes" / "a.md").read_text(encoding="utf-8") == "a1"
    (ws / "notes" / "a.md").write_text("by the user", encoding="utf-8")
    with pytest.raises(UndoConflictError):
        undo_journals(ws, ["peer-q1", "helper-l1"], into, token=preview.token)
    (ws / "notes" / "a.md").write_text("a1", encoding="utf-8")
    report = undo_journals(ws, ["peer-q1", "helper-l1"], into)
    assert report is not None
    assert (ws / "notes" / "a.md").read_text(encoding="utf-8") == "a0"
    assert not (ws / "notes" / "new").exists()
    # L'annulla dell'annulla: il registro *into* ha le sue righe.
    assert journal_file_items(ws, [into])


def test_nothing_to_undo_is_none(ws: Path) -> None:
    assert undo_journals(ws, ["peer-none"], "undo-peer-none") is None
