"""Il motore dello stato (``jenny/db``): transazioni, schema, copia, più thread.

Si prova quel che gli archivi danno per scontato: una transazione entra tutta o
niente, uno schema sale di versione senza fermarsi a metà, un file di una
Jenny più nuova non si tocca, la copia per il backup è coerente mentre un altro
thread scrive.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from jenny import db as jdb
from jenny.db.database import Database, SchemaError, SchemaTooNewError, split_statements
from jenny.db.schema import Schema

_NOTES = Schema("notes", (
    "CREATE TABLE notes (id INTEGER PRIMARY KEY, text TEXT NOT NULL);",
))


@pytest.fixture
def database(tmp_path: Path):
    d = Database(tmp_path / "db" / "t.sqlite", name="t")
    yield d
    d.close()


def test_opens_in_wal_with_the_settings(database: Database) -> None:
    assert database.read("PRAGMA journal_mode")[0][0] == "wal"
    with database.write() as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_a_transaction_goes_in_whole_or_not_at_all(database: Database) -> None:
    database.require(_NOTES)
    with pytest.raises(RuntimeError), database.write() as conn:
        conn.execute("INSERT INTO notes(text) VALUES ('a')")
        raise RuntimeError("boom")
    assert database.read("SELECT count(*) FROM notes")[0][0] == 0
    with database.write() as conn:
        conn.execute("INSERT INTO notes(text) VALUES ('a')")
        with database.write() as inner:  # annidata: stessa transazione
            inner.execute("INSERT INTO notes(text) VALUES ('b')")
    assert database.read("SELECT count(*) FROM notes")[0][0] == 2


def test_two_threads_write_without_locking_each_other_out(database: Database) -> None:
    database.require(_NOTES)
    errors: list[BaseException] = []

    def writer(tag: str) -> None:
        try:
            for i in range(200):
                with database.write() as conn:
                    conn.execute("INSERT INTO notes(text) VALUES (?)", (f"{tag}{i}",))
        except BaseException as exc:  # pragma: no cover - lo dice l'assert
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(t,)) for t in "ab"]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert database.read("SELECT count(*) FROM notes")[0][0] == 400


def test_a_schema_climbs_step_by_step_and_remembers(database: Database) -> None:
    database.require(_NOTES)
    v2 = Schema("notes", _NOTES.steps + (
        "ALTER TABLE notes ADD COLUMN tag TEXT;\nCREATE INDEX notes_tag ON notes(tag);",
        lambda conn: conn.execute("UPDATE notes SET tag = 'x'"),
    ))
    with database.write() as conn:
        conn.execute("INSERT INTO notes(text) VALUES ('a')")
    database.require(v2)
    assert database.schema_version("notes") == 3
    assert database.read("SELECT tag FROM notes")[0]["tag"] == "x"


def test_a_failing_step_leaves_the_version_before(database: Database) -> None:
    database.require(_NOTES)
    broken = Schema("notes", _NOTES.steps + (
        "ALTER TABLE notes ADD COLUMN tag TEXT;",
        "ALTER TABLE nowhere ADD COLUMN x TEXT;",
    ))
    with pytest.raises(SchemaError):
        database.require(broken)
    assert database.schema_version("notes") == 1
    cols = [r["name"] for r in database.read("PRAGMA table_info(notes)")]
    assert "tag" not in cols


def test_a_file_from_a_newer_jenny_is_not_touched(database: Database) -> None:
    database.require(_NOTES)
    with database.write() as conn:
        conn.execute("UPDATE schema_versions SET version = 9 WHERE name = 'notes'")
    fresh = Database(database.path, name="t")
    try:
        with pytest.raises(SchemaTooNewError):
            fresh.require(_NOTES)
        assert fresh.schema_version("notes") == 9
    finally:
        fresh.close()


def test_backup_is_consistent_while_another_thread_writes(database: Database,
                                                          tmp_path: Path) -> None:
    database.require(_NOTES)
    stop = threading.Event()

    def writer() -> None:
        i = 0
        while not stop.is_set():
            with database.write() as conn:
                conn.execute("INSERT INTO notes(text) VALUES (?)", (str(i),))
            i += 1

    t = threading.Thread(target=writer)
    t.start()
    try:
        dest = tmp_path / "copy.sqlite"
        database.backup_to(dest)
    finally:
        stop.set()
        t.join()
    copy = sqlite3.connect(dest)
    try:
        assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        copy.execute("SELECT count(*) FROM notes").fetchone()
    finally:
        copy.close()


def test_statements_split_where_sqlite_says_they_end() -> None:
    sql = "CREATE TABLE a (x TEXT DEFAULT 'a;b');\nCREATE TABLE b (y INTEGER);\n"
    assert len(split_statements(sql)) == 2


def test_the_registry_gives_one_database_per_file(tmp_path: Path) -> None:
    root = tmp_path / "rt"
    try:
        first = jdb.database(root, "undo")
        assert jdb.database(root, "undo") is first
        assert jdb.database(tmp_path / "other", "undo") is not first
        assert (root / "db" / "undo.sqlite").is_file()
        jdb.close_all()
        assert first.closed
        assert jdb.database(root, "undo") is not first
        with pytest.raises(ValueError):
            jdb.database(root, "nope")
    finally:
        jdb.close_all()
