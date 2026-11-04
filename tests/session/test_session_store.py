"""Le conversazioni in ``state.sqlite`` (``session.store`` e ``SessionManager``).

Quel che il file JSONL doveva difendere a mano — una scrittura a metà, una riga
troncata — lo fa la transazione. Qui si prova quel che il database aggiunge:
un salvataggio scrive solo i messaggi cambiati, uno di un'altra istanza nel
frattempo fa riscrivere tutto, e la coda accorciata non lascia righe in più.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from jenny.session import store
from jenny.session.manager import SessionManager


def _rows(manager: SessionManager, key: str) -> list[tuple[int, str]]:
    return [(r["seq"], r["data"]) for r in manager._db.read(
        "SELECT seq, data FROM session_messages WHERE key = ? ORDER BY seq", (key,))]


def test_a_session_comes_back_whole(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path / "ws")
    session = manager.get_or_create("project:orto")
    session.add_message("user", "ciao")
    session.add_message("assistant", "eccomi")
    session.metadata["title"] = "Orto"
    manager.save(session)
    again = SessionManager(tmp_path / "ws").get_or_create("project:orto")
    assert [m["content"] for m in again.messages] == ["ciao", "eccomi"]
    assert again.metadata["title"] == "Orto"


def test_a_save_writes_only_what_changed(tmp_path: Path, monkeypatch) -> None:
    manager = SessionManager(tmp_path / "ws")
    session = manager.get_or_create("k:1")
    for i in range(5):
        session.add_message("user", f"m{i}")
    manager.save(session)
    written: list[int] = []
    real = store.save

    def spy(database, key, **kw):  # type: ignore[no-untyped-def]
        known = kw["known"]
        written.append(sum(1 for i, line in enumerate(kw["lines"])
                           if known is None or i >= len(known) or known[i] != hash(line)))
        return real(database, key, **kw)

    monkeypatch.setattr(store, "save", spy)
    session.add_message("assistant", "nuovo")
    manager.save(session)
    session.messages[1]["content"] = "ritoccato"
    manager.save(session)
    assert written == [1, 1]
    assert [r[1] for r in _rows(manager, "k:1")][1].count("ritoccato") == 1


def test_a_shorter_session_leaves_no_rows_behind(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path / "ws")
    session = manager.get_or_create("k:1")
    for i in range(4):
        session.add_message("user", f"m{i}")
    manager.save(session)
    session.messages[:] = session.messages[2:]
    manager.save(session)
    assert [seq for seq, _ in _rows(manager, "k:1")] == [0, 1]
    assert [m["content"] for m in SessionManager(tmp_path / "ws").get_or_create("k:1").messages] \
        == ["m2", "m3"]


def test_another_instance_in_between_makes_a_full_rewrite(tmp_path: Path) -> None:
    first = SessionManager(tmp_path / "ws")
    a = first.get_or_create("k:1")
    a.add_message("user", "uno")
    first.save(a)
    other = SessionManager(tmp_path / "ws")
    b = other.get_or_create("k:1")
    b.messages[:] = [{"role": "user", "content": "altro"}]
    other.save(b)
    a.add_message("user", "due")
    first.save(a)  # sa di una versione vecchia: riscrive tutto, niente righe mescolate
    assert [m["content"] for m in SessionManager(tmp_path / "ws").get_or_create("k:1").messages] \
        == ["uno", "due"]


def test_a_failed_save_leaves_the_previous_session(tmp_path: Path, monkeypatch) -> None:
    manager = SessionManager(tmp_path / "ws")
    session = manager.get_or_create("k:1")
    session.add_message("user", "salvato")
    manager.save(session)
    session.add_message("user", "perso")
    real = manager._db.write

    def broken():  # type: ignore[no-untyped-def]
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(type(manager._db), "write", lambda self: broken())
    with pytest.raises(sqlite3.OperationalError):
        manager.save(session)
    monkeypatch.undo()
    assert real is not None
    reloaded = SessionManager(tmp_path / "ws").get_or_create("k:1")
    assert [m["content"] for m in reloaded.messages] == ["salvato"]


def test_an_unreadable_message_row_costs_only_itself(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path / "ws")
    session = manager.get_or_create("k:1")
    session.add_message("user", "buono")
    manager.save(session)
    with manager._db.write() as conn:
        conn.execute("INSERT INTO session_messages(key, seq, data) VALUES ('k:1', 1, '{rotto')")
    assert [m["content"] for m in SessionManager(tmp_path / "ws").get_or_create("k:1").messages] \
        == ["buono"]


def test_list_delete_and_rename(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path / "ws")
    for key in ("dream:1", "dream:2", "project:a"):
        session = manager.get_or_create(key)
        session.add_message("user", key)
        manager.save(session)
    assert manager.list_keys("dream:") == ["dream:1", "dream:2"]
    assert manager.stats("project:a")[0] == 1
    assert store.rename(manager._db, "project:a", "project:b")
    assert not store.rename(manager._db, "project:a", "project:c")
    assert manager.delete("dream:1") and not manager.exists("dream:1")
    assert manager.list_keys() == ["dream:2", "project:b"]
