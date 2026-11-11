"""Dove stanno i job: due tabelle di ``state.sqlite``.

Fino alla 2.x i job stavano in ``cron/jobs.json``, con accanto un giornale delle
azioni (``action.jsonl``) per chi scriveva a scheduler fermo, un lock su file,
il ``.bak`` e la quarantena di un file rotto, e un file per ogni esecuzione in
``runs/``. Il database fa tutto questo da sé: una transazione entra intera o
non entra, e chi scrive non aspetta un lock su file.

- ``cron_jobs``: un job per riga, il JSON di sempre (``CronService._job_to_dict``)
  e la posizione nella lista.
- ``cron_runs``: i record di esecuzione, una traccia per il post-mortem che non
  legge nessuno; ne restano gli ultimi ``RUNS_KEEP``.
- ``cron_wakes``: una riga per ogni risveglio del modello, tenuta ``WAKES_KEEP_MS``.

Il formato vecchio lo legge solo ``jenny/db_migration/cron.py``.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from jenny.db.schema import Schema

SCHEMA = Schema(
    "cron",
    (
        """
        CREATE TABLE cron_jobs (
          id TEXT PRIMARY KEY,
          pos INTEGER NOT NULL,
          data TEXT NOT NULL
        );
        CREATE TABLE cron_runs (
          run_id TEXT PRIMARY KEY,
          job_id TEXT NOT NULL,
          at_ms INTEGER NOT NULL,
          data TEXT NOT NULL
        );
        CREATE INDEX cron_runs_job ON cron_runs(job_id);
        CREATE INDEX cron_runs_at ON cron_runs(at_ms);
        """,
        # I risvegli: una riga ogni volta che un job sveglia il modello. Servono
        # al conto delle ultime 24 ore (``CronService.wakes``); il nome e la
        # firma stanno nella riga, perché un singolo scattato non c'è più.
        """
        CREATE TABLE cron_wakes (
          at_ms INTEGER NOT NULL,
          job_id TEXT NOT NULL,
          name TEXT NOT NULL,
          autonomous INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX cron_wakes_at ON cron_wakes(at_ms);
        """,
    ),
    # Negli snapshot i record di un giorno stanno insieme: quelli dei giorni
    # passati non cambiano più, e restano lo stesso blob.
    shards={"cron_runs": "at_ms / 86400000", "cron_wakes": "at_ms / 86400000"},
)

# Quanti record di esecuzione tenere. Un conteggio e non un'età: una scadenza a
# giorni non protegge da un job che gira ogni minuto. A due esecuzioni al giorno
# sono circa otto mesi; a una ogni cinque minuti, un giorno e mezzo.
RUNS_KEEP = 500


def cron_database(workspace: Path):
    """Lo ``state.sqlite`` del workspace, con le tabelle del cron."""
    from jenny import db

    return db.store_database(workspace, db.STATE, SCHEMA)


def load_rows(database) -> list[dict[str, Any]]:
    """I job come li ha salvati lo scheduler, in ordine; una riga illeggibile si salta."""
    out: list[dict[str, Any]] = []
    for row in database.read("SELECT id, data FROM cron_jobs ORDER BY pos"):
        try:
            data = json.loads(row["data"])
        except ValueError:
            from loguru import logger

            logger.error("Cron: job {} unreadable in the database, skipped", row["id"])
            continue
        if isinstance(data, dict):
            out.append(data)
    return out


def save_rows(database, jobs: list[dict[str, Any]], removed: Iterable[str] = ()) -> None:
    """Salva *jobs* e toglie gli id *removed*, in una transazione.

    Un job che c'è nel database ma non in *jobs* né in *removed* resta: l'ha
    aggiunto qualcun altro dopo l'ultima lettura di chi salva (lo scheduler,
    a metà di un giro, tiene la sua copia in memoria), e non va perso.
    """
    with database.write() as conn:
        conn.executemany("DELETE FROM cron_jobs WHERE id = ?", [(i,) for i in removed])
        conn.executemany(
            "INSERT INTO cron_jobs(id, pos, data) VALUES (?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET pos = excluded.pos, data = excluded.data",
            [(str(j["id"]), pos, json.dumps(j, ensure_ascii=False, sort_keys=True))
             for pos, j in enumerate(jobs)],
        )


def write_run(database, run_id: str, job_id: str, at_ms: int, record: dict[str, Any]) -> None:
    with database.write() as conn:
        conn.execute(
            "INSERT INTO cron_runs(run_id, job_id, at_ms, data) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(run_id) DO UPDATE SET at_ms = excluded.at_ms, data = excluded.data",
            (run_id, job_id, at_ms, json.dumps(record, ensure_ascii=False, sort_keys=True)),
        )


def prune_runs(database, keep: int = RUNS_KEEP) -> int:
    with database.write() as conn:
        return conn.execute(
            "DELETE FROM cron_runs WHERE run_id NOT IN "
            "(SELECT run_id FROM cron_runs ORDER BY at_ms DESC, run_id DESC LIMIT ?)",
            (keep,),
        ).rowcount


def remove_runs(database, job_id: str) -> int:
    with database.write() as conn:
        return conn.execute("DELETE FROM cron_runs WHERE job_id = ?", (job_id,)).rowcount


def read_runs(database, job_id: str | None = None) -> list[dict[str, Any]]:
    """I record di esecuzione, dal più recente (per i test e il post-mortem)."""
    rows = database.read(
        "SELECT data FROM cron_runs WHERE ? IS NULL OR job_id = ? ORDER BY at_ms DESC",
        (job_id, job_id),
    )
    return [json.loads(r["data"]) for r in rows]


# Quanto si tengono i risvegli: il conto guarda le ultime 24 ore, il doppio basta.
WAKES_KEEP_MS = 48 * 60 * 60 * 1000


def add_wake(database, at_ms: int, job_id: str, name: str, autonomous: bool) -> None:
    with database.write() as conn:
        conn.execute(
            "INSERT INTO cron_wakes(at_ms, job_id, name, autonomous) VALUES (?, ?, ?, ?)",
            (at_ms, job_id, name, 1 if autonomous else 0),
        )


def prune_wakes(database, now_ms: int, keep_ms: int = WAKES_KEEP_MS) -> int:
    with database.write() as conn:
        return conn.execute("DELETE FROM cron_wakes WHERE at_ms < ?", (now_ms - keep_ms,)).rowcount


def read_wakes(database, since_ms: int) -> list[dict[str, Any]]:
    """I risvegli da *since_ms* raggruppati per job, dal più frequente."""
    rows = database.read(
        "SELECT job_id, COUNT(*) AS n, MAX(at_ms) AS last_ms FROM cron_wakes "
        "WHERE at_ms >= ? GROUP BY job_id ORDER BY n DESC, last_ms DESC",
        (since_ms,),
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        last = database.read(
            "SELECT name, autonomous FROM cron_wakes WHERE job_id = ? AND at_ms = ? LIMIT 1",
            (row["job_id"], row["last_ms"]),
        )
        name, autonomous = (last[0]["name"], bool(last[0]["autonomous"])) if last else ("", False)
        out.append({"job_id": row["job_id"], "name": name, "autonomous": autonomous,
                    "count": int(row["n"]), "last_ms": int(row["last_ms"])})
    return out
