"""Dove stanno le conversazioni: tabelle di ``state.sqlite``.

Fino alla 2.x ogni sessione era ``sessions/<chiave>.jsonl`` — una riga di
metadati, poi un messaggio per riga — riscritto intero a ogni salvataggio, con
accanto il diario del turno in corso (``<chiave>.turn-journal``) perché i
checkpoint non riscrivessero il file *n²* volte. Qui:

- ``sessions``: una riga per chiave, i metadati e una ``version`` che cresce a
  ogni salvataggio;
- ``session_messages``: un messaggio per riga, ``(key, seq)``. Un salvataggio
  scrive solo i messaggi cambiati o nuovi (``SessionManager.save``);
- ``session_turns`` e ``session_turn_messages``: il diario del turno in corso,
  in append, col suo ``stamp``.

La chiave è quella vera (``project:orto``), non un nome di file che perde i
``:``. Il formato vecchio lo legge solo ``jenny/db_migration/sessions.py``.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from jenny.db.schema import Schema

SCHEMA = Schema(
    "sessions",
    (
        """
        CREATE TABLE sessions (
          key TEXT PRIMARY KEY,
          created_at TEXT,
          updated_at TEXT,
          metadata TEXT NOT NULL DEFAULT '{}',
          last_consolidated INTEGER NOT NULL DEFAULT 0,
          version INTEGER NOT NULL DEFAULT 0,
          count INTEGER NOT NULL DEFAULT 0,
          bytes INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE session_messages (
          key TEXT NOT NULL,
          seq INTEGER NOT NULL,
          data TEXT NOT NULL,
          PRIMARY KEY (key, seq)
        ) WITHOUT ROWID;
        CREATE TABLE session_turns (
          key TEXT PRIMARY KEY,
          stamp TEXT NOT NULL
        );
        CREATE TABLE session_turn_messages (
          key TEXT NOT NULL,
          seq INTEGER NOT NULL,
          data TEXT NOT NULL,
          PRIMARY KEY (key, seq)
        ) WITHOUT ROWID;
        """,
    ),
    # Negli snapshot: i messaggi di una conversazione a blocchi di 200, così i
    # blocchi vecchi restano lo stesso blob mentre la coda cresce.
    shards={
        "sessions": "key",
        "session_messages": "key || '/' || (seq / 200)",
        "session_turns": "key",
        "session_turn_messages": "key",
    },
)


def session_database(workspace: Path):
    from jenny import db

    database = db.database_for_workspace(Path(workspace), db.STATE)
    database.require(SCHEMA)
    return database


def load(database, key: str) -> dict[str, Any] | None:
    """La sessione *key* come dict (``created_at``, ``updated_at``, ``metadata``,
    ``last_consolidated``, ``version``, ``messages``, ``known``), o ``None``.

    ``known`` è l'impronta di ogni messaggio come sta salvato: chi salva dopo
    la confronta per scrivere solo quel che cambia. Un messaggio illeggibile si
    salta.
    """
    with database.reading() as conn:
        head = conn.execute("SELECT * FROM sessions WHERE key = ?", (key,)).fetchone()
        if head is None:
            return None
        rows = conn.execute("SELECT data FROM session_messages WHERE key = ? ORDER BY seq",
                            (key,)).fetchall()
    messages: list[dict[str, Any]] = []
    known: list[int] = []
    for row in rows:
        try:
            message = json.loads(row["data"])
        except ValueError:
            continue
        if isinstance(message, dict):
            messages.append(message)
            known.append(hash(row["data"]))
    return {
        "key": key,
        "created_at": head["created_at"],
        "updated_at": head["updated_at"],
        "metadata": _metadata(head["metadata"]),
        "last_consolidated": head["last_consolidated"],
        "version": head["version"],
        "messages": messages,
        "known": known,
    }


def head(database, key: str) -> dict[str, Any] | None:
    """Solo i metadati di *key* (``created_at``, ``updated_at``, ``metadata``,
    ``count``, ``bytes``), o ``None``."""
    rows = database.read("SELECT * FROM sessions WHERE key = ?", (key,))
    if not rows:
        return None
    row = rows[0]
    return {
        "key": key,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "metadata": _metadata(row["metadata"]),
        "count": row["count"],
        "bytes": row["bytes"],
    }


def save(database, key: str, *, created_at: str, updated_at: str, metadata: str,
         last_consolidated: int, lines: Sequence[str], known: Sequence[int] | None,
         known_version: int | None) -> tuple[int, list[int]]:
    """Salva *lines* per *key*; ritorna la versione nuova e le impronte salvate.

    Con *known* (le impronte delle righe salvate l'ultima volta da chi salva,
    alla *known_version*) si scrivono solo le righe diverse e si tolgono quelle
    in più. Se nel frattempo qualcun altro ha salvato (la versione è un'altra),
    o se *known* manca, si riscrive tutto. Le impronte sono ``hash`` di
    processo: valgono solo nella memoria di chi le ha calcolate.
    """
    hashes = [hash(line) for line in lines]
    with database.write() as conn:
        row = conn.execute("SELECT version FROM sessions WHERE key = ?", (key,)).fetchone()
        current = row["version"] if row else None
        version = (current or 0) + 1
        conn.execute(
            "INSERT INTO sessions(key, created_at, updated_at, metadata, last_consolidated, "
            "version, count, bytes) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(key) DO UPDATE "
            "SET created_at = excluded.created_at, updated_at = excluded.updated_at, "
            "metadata = excluded.metadata, last_consolidated = excluded.last_consolidated, "
            "version = excluded.version, count = excluded.count, bytes = excluded.bytes",
            (key, created_at, updated_at, metadata, last_consolidated, version, len(lines),
             sum(len(line) for line in lines)),
        )
        if known is None or current is None or current != known_version:
            conn.execute("DELETE FROM session_messages WHERE key = ?", (key,))
            changed = list(enumerate(lines))
        else:
            changed = [(i, line) for i, line in enumerate(lines)
                       if i >= len(known) or known[i] != hashes[i]]
            if len(known) > len(lines):
                conn.execute("DELETE FROM session_messages WHERE key = ? AND seq >= ?",
                             (key, len(lines)))
        conn.executemany(
            "INSERT INTO session_messages(key, seq, data) VALUES (?, ?, ?) "
            "ON CONFLICT(key, seq) DO UPDATE SET data = excluded.data",
            [(key, i, line) for i, line in changed],
        )
    return version, hashes


def keys(database, prefix: str = "") -> list[str]:
    """Le chiavi che cominciano con *prefix*, dalla salvata meno di recente."""
    rows = database.read(
        "SELECT key FROM sessions WHERE substr(key, 1, ?) = ? ORDER BY updated_at, key",
        (len(prefix), prefix),
    )
    return [r["key"] for r in rows]


def delete(database, key: str) -> bool:
    with database.write() as conn:
        gone = conn.execute("DELETE FROM sessions WHERE key = ?", (key,)).rowcount > 0
        conn.execute("DELETE FROM session_messages WHERE key = ?", (key,))
        conn.execute("DELETE FROM session_turns WHERE key = ?", (key,))
        conn.execute("DELETE FROM session_turn_messages WHERE key = ?", (key,))
    return gone


def rename(database, old: str, new: str) -> bool:
    """La sessione *old* (e il suo diario del turno) diventa *new*. False se *new* c'è già
    o *old* non c'è."""
    with database.write() as conn:
        if conn.execute("SELECT 1 FROM sessions WHERE key = ?", (new,)).fetchone():
            return False
        if not conn.execute("SELECT 1 FROM sessions WHERE key = ?", (old,)).fetchone():
            return False
        for table in ("sessions", "session_messages", "session_turns", "session_turn_messages"):
            conn.execute(f"UPDATE {table} SET key = ? WHERE key = ?", (new, old))
    return True


# -- il diario del turno in corso ---------------------------------------------


def turn_reset(database, key: str, stamp: str, lines: Sequence[str]) -> None:
    with database.write() as conn:
        conn.execute("DELETE FROM session_turn_messages WHERE key = ?", (key,))
        conn.execute("INSERT INTO session_turns(key, stamp) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET stamp = excluded.stamp", (key, stamp))
        conn.executemany("INSERT INTO session_turn_messages(key, seq, data) VALUES (?, ?, ?)",
                         [(key, i, line) for i, line in enumerate(lines)])


def turn_append(database, key: str, start: int, lines: Sequence[str]) -> None:
    with database.write() as conn:
        conn.executemany("INSERT OR REPLACE INTO session_turn_messages(key, seq, data) "
                         "VALUES (?, ?, ?)",
                         [(key, start + i, line) for i, line in enumerate(lines)])


def turn_read(database, key: str) -> tuple[str | None, list[str]]:
    with database.reading() as conn:
        row = conn.execute("SELECT stamp FROM session_turns WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None, []
        lines = [r["data"] for r in conn.execute(
            "SELECT data FROM session_turn_messages WHERE key = ? ORDER BY seq", (key,))]
    return row["stamp"], lines


def turn_drop(database, key: str) -> None:
    with database.write() as conn:
        conn.execute("DELETE FROM session_turns WHERE key = ?", (key,))
        conn.execute("DELETE FROM session_turn_messages WHERE key = ?", (key,))


def _metadata(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}
