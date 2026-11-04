"""Cosa è già stato migrato: la tabella ``migration_runs`` in ``state.sqlite``."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from jenny.db.schema import Schema

if TYPE_CHECKING:
    from jenny.db.database import Database
    from jenny.db_migration.registry import MigrationResult

SCHEMA = Schema("migration_runs", (
    """
    CREATE TABLE migration_runs (
      name TEXT PRIMARY KEY,
      started_at REAL NOT NULL,
      finished_at REAL,
      status TEXT NOT NULL,
      sources TEXT NOT NULL DEFAULT '[]',
      counts TEXT NOT NULL DEFAULT '{}',
      error TEXT
    );
    """,
))


def last_run(state: Database, name: str) -> dict | None:
    state.require(SCHEMA)
    rows = state.read("SELECT * FROM migration_runs WHERE name = ?", (name,))
    return dict(rows[0]) if rows else None


def record(state: Database, name: str, result: MigrationResult, *,
           started_at: float, finished_at: float) -> None:
    state.require(SCHEMA)
    with state.write() as conn:
        conn.execute(
            "INSERT INTO migration_runs(name, started_at, finished_at, status, sources, counts, "
            "error) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(name) DO UPDATE SET "
            "started_at = excluded.started_at, finished_at = excluded.finished_at, "
            "status = excluded.status, sources = excluded.sources, counts = excluded.counts, "
            "error = excluded.error",
            (name, started_at, finished_at, result.status,
             json.dumps(result.sources, ensure_ascii=False), json.dumps(result.counts),
             result.error),
        )


def mark(state: Database, name: str, status: str) -> None:
    state.require(SCHEMA)
    with state.write() as conn:
        conn.execute("UPDATE migration_runs SET status = ? WHERE name = ?", (status, name))
