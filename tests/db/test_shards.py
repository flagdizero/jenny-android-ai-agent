"""Lo stato a pezzi per gli snapshot (``jenny/db/shards``).

Quel che conta per la storia: dai pezzi torna lo stesso database, uno stato
fermo dà gli stessi byte (quindi gli stessi blob), e una regola divide una
tabella in modo che le righe ferme restino nello stesso pezzo.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from jenny.db.database import Database
from jenny.db.schema import Schema
from jenny.db.shards import ShardsError, build, export
from jenny.utils.locations import STATE_ENTRY_DIR

_JOBS = Schema("jobs", (
    "CREATE TABLE jobs (id TEXT PRIMARY KEY, data TEXT NOT NULL, raw BLOB);\n"
    "CREATE INDEX jobs_data ON jobs(data);",
))
_MESSAGES = Schema(
    "messages",
    ("CREATE TABLE messages (session TEXT, seq INTEGER, text TEXT, PRIMARY KEY(session, seq));",),
    shards={"messages": "session || '/' || (seq / 2)"},
)


@pytest.fixture
def database(tmp_path: Path):
    d = Database(tmp_path / "live" / "state.sqlite", name="state")
    d.require(_JOBS)
    d.require(_MESSAGES)
    yield d
    d.close()


def _fill(d: Database) -> None:
    with d.write() as conn:
        conn.execute("INSERT INTO jobs VALUES ('b', '{\"è\": 1}', NULL)")
        conn.execute("INSERT INTO jobs VALUES ('a', '{}', ?)", (b"\x00\xff",))
        conn.executemany("INSERT INTO messages VALUES (?, ?, ?)",
                         [("project:x", i, f"m{i}") for i in range(5)])


def _write(entries: list[tuple[str, bytes]], root: Path) -> Path:
    for path, content in entries:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return root / STATE_ENTRY_DIR


def _rows(path: Path, sql: str) -> list[tuple]:
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def test_the_pieces_give_back_the_same_database(database: Database, tmp_path: Path) -> None:
    _fill(database)
    shards = _write(export(database), tmp_path / "tree")
    rebuilt = tmp_path / "staged" / "state.sqlite"
    build(shards, rebuilt, known=database.known_versions())
    assert _rows(rebuilt, "SELECT id, data, raw FROM jobs ORDER BY id") == [
        ("a", "{}", b"\x00\xff"), ("b", '{"è": 1}', None)]
    assert _rows(rebuilt, "SELECT count(*) FROM messages") == [(5,)]
    assert _rows(rebuilt, "SELECT name, version FROM schema_versions ORDER BY name") == [
        ("jobs", 1), ("messages", 1)]
    # Lo schema torna intero, indici compresi, e le regole dei pezzi con lui.
    assert _rows(rebuilt, "SELECT name FROM sqlite_master WHERE type='index' "
                          "AND name = 'jobs_data'") == [("jobs_data",)]
    again = Database(rebuilt, name="state")
    try:
        again.require(_JOBS)  # già alla versione: non rifà niente
        assert [p for p, _ in export(again)] == [p for p, _ in export(database)]
    finally:
        again.close()


def test_a_still_state_gives_the_same_bytes(database: Database) -> None:
    _fill(database)
    first = export(database)
    assert export(database) == first
    with database.write() as conn:
        conn.execute("INSERT INTO messages VALUES ('project:x', 5, 'm5')")
    changed = dict(export(database))
    # Solo il pezzo dove è entrata la riga nuova cambia; gli altri restano blob uguali.
    before = dict(first)
    differ = sorted(p for p in changed if changed[p] != before.get(p))
    assert differ == [f"{STATE_ENTRY_DIR}/messages/project%3Ax%2F2.jsonl"]


def test_a_newer_state_is_refused(database: Database, tmp_path: Path) -> None:
    _fill(database)
    shards = _write(export(database), tmp_path / "tree")
    with pytest.raises(ShardsError, match="version"):
        build(shards, tmp_path / "staged.sqlite", known={"jobs": 0})
    assert not (tmp_path / "staged.sqlite").exists()


def test_rows_for_an_unknown_table_are_refused(database: Database, tmp_path: Path) -> None:
    shards = _write(export(database), tmp_path / "tree")
    (shards / "evil").mkdir()
    (shards / "evil" / "all.jsonl").write_text('{"columns":["x"]}\n[1]\n')
    with pytest.raises(ShardsError, match="unknown table"):
        build(shards, tmp_path / "staged.sqlite")
    assert not (tmp_path / "staged.sqlite").exists()
