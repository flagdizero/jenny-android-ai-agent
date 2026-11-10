"""Lo stato in pezzi di testo, per gli snapshot; e il database rifatto dai pezzi.

Uno snapshot del workspace porta anche ``state.sqlite``, così un ripristino
dalla storia locale o da Drive riporta i job, gli aiutanti e le conversazioni
insieme ai file, come quando stavano nel workspace. Il file intero sarebbe un
blob nuovo a ogni cambiamento, e Drive non pota niente: lo stato entra invece
come file di testo sotto ``.jenny-state/``, uno per pezzo di tabella, scritti
sempre allo stesso modo. Un pezzo che non cambia dà gli stessi byte, quindi lo
stesso blob, e lo store content-addressed lo tiene una volta sola — come un
file del workspace che non cambia.

- ``schema.json``: il DDL delle tabelle, le versioni degli schemi, le regole
  dei pezzi.
- ``<tabella>/<pezzo>.jsonl``: la prima riga sono le colonne, poi un record per
  riga in ordine di chiave primaria. Il pezzo lo dice la regola della tabella
  (``Schema.shards``); senza regola la tabella è un pezzo solo, ``all``.

``build`` rifà un ``state.sqlite`` da quei file; al boot ``require`` porta le
tabelle alla versione del codice, come per un database vecchio qualunque.
"""

from __future__ import annotations

import base64
import json
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote

from jenny.db.database import META_TABLE_NAMES, META_TABLES, Database
from jenny.utils.locations import STATE_ENTRY_DIR

FORMAT = 1
SCHEMA_FILE = "schema.json"
_WHOLE = "all"
_SUFFIX = ".jsonl"


class ShardsError(ValueError):
    """I pezzi di uno snapshot non rifanno un database: mancano, sono rotti o troppo nuovi."""


def export(database: Database) -> list[tuple[str, bytes]]:
    """I file dei pezzi di *database*: ``(percorso sotto lo snapshot, contenuto)``.

    Tutto in una transazione di lettura: i pezzi descrivono uno stesso momento.
    """
    out: list[tuple[str, bytes]] = []
    with database.reading() as conn:
        rules = {r["tbl"]: r["expr"] for r in conn.execute("SELECT tbl, expr FROM table_shards")}
        versions = {
            r["name"]: int(r["version"])
            for r in conn.execute("SELECT name, version FROM schema_versions ORDER BY name")
        }
        ddl = [
            r["sql"]
            for r in conn.execute(
                "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL "
                "AND name NOT LIKE 'sqlite_%' AND tbl_name NOT IN (?, ?) "
                "ORDER BY CASE type WHEN 'table' THEN 0 ELSE 1 END, name",
                META_TABLE_NAMES,
            )
        ]
        schema = {"format": FORMAT, "ddl": ddl, "versions": versions,
                  "shards": dict(sorted(rules.items()))}
        out.append((f"{STATE_ENTRY_DIR}/{SCHEMA_FILE}", _dumps(schema).encode("utf-8")))
        for table in _tables(conn):
            out.extend(_export_table(conn, table, rules.get(table)))
    return out


def build(shards_dir: Path, dest: Path, *, known: Mapping[str, int] | None = None) -> None:
    """Un ``state.sqlite`` nuovo in *dest* dai pezzi in *shards_dir*.

    *known* sono le versioni che questo codice conosce: un pezzo di una Jenny
    più nuova non si ripristina (``ShardsError``), come un database troppo nuovo
    non si apre. *dest* non deve esistere; se qualcosa va storto non resta.
    """
    shards_dir = Path(shards_dir)
    dest = Path(dest)
    try:
        schema = json.loads((shards_dir / SCHEMA_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ShardsError(f"state schema unreadable: {exc}") from exc
    if not isinstance(schema, dict) or schema.get("format") != FORMAT:
        raise ShardsError("state schema of an unknown format")
    versions = {str(k): int(v) for k, v in dict(schema.get("versions") or {}).items()}
    for name, version in versions.items():
        if known and name in known and version > known[name]:
            raise ShardsError(f"state {name} is at version {version}, this Jenny knows "
                              f"{known[name]}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.unlink(missing_ok=True)
    conn = sqlite3.connect(str(dest), isolation_level=None)
    try:
        conn.execute("BEGIN")
        for statement in (*META_TABLES, *[str(s) for s in schema.get("ddl") or []]):
            conn.execute(statement)
        tables = set(_tables(conn))
        for table_dir in sorted(p for p in shards_dir.iterdir() if p.is_dir()):
            if table_dir.name not in tables:
                raise ShardsError(f"state rows for an unknown table {table_dir.name!r}")
            for piece in sorted(table_dir.glob(f"*{_SUFFIX}")):
                _load_piece(conn, table_dir.name, piece)
        conn.executemany("INSERT INTO schema_versions(name, version) VALUES (?, ?)",
                         sorted(versions.items()))
        conn.executemany("INSERT INTO table_shards(tbl, expr) VALUES (?, ?)",
                         sorted(dict(schema.get("shards") or {}).items()))
        conn.execute("COMMIT")
    except ShardsError:
        conn.close()
        dest.unlink(missing_ok=True)
        raise
    except (sqlite3.Error, OSError, ValueError, TypeError) as exc:
        conn.close()
        dest.unlink(missing_ok=True)
        raise ShardsError(f"state rows could not be loaded: {exc}") from exc
    conn.close()


# -- dentro --------------------------------------------------------------------


def _tables(conn: sqlite3.Connection) -> list[str]:
    return [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' AND name NOT IN (?, ?) ORDER BY name",
            META_TABLE_NAMES,
        )
    ]


def _export_table(conn: sqlite3.Connection, table: str,
                  rule: str | None) -> list[tuple[str, bytes]]:
    info = conn.execute(f"PRAGMA table_info({_ident(table)})").fetchall()
    columns = [r["name"] for r in info]
    keys = [r["name"] for r in sorted((r for r in info if r["pk"]), key=lambda r: r["pk"])]
    order = ", ".join(_ident(k) for k in keys) if keys else "rowid"
    shard = f"({rule})" if rule else f"'{_WHOLE}'"
    pieces: dict[str, list[str]] = {}
    for row in conn.execute(
        f"SELECT {shard} AS shard_of_row, {', '.join(_ident(c) for c in columns)} "
        f"FROM {_ident(table)} ORDER BY {order}"
    ):
        values = [_encode(row[c]) for c in columns]
        pieces.setdefault(str(row["shard_of_row"]), []).append(_dumps(values))
    head = _dumps({"columns": columns})
    return [
        (f"{STATE_ENTRY_DIR}/{table}/{_piece_name(name)}",
         ("\n".join([head, *lines]) + "\n").encode("utf-8"))
        for name, lines in sorted(pieces.items())
    ]


def _load_piece(conn: sqlite3.Connection, table: str, path: Path) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        return
    columns = json.loads(lines[0]).get("columns")
    if not isinstance(columns, list) or not columns:
        raise ShardsError(f"state piece {table}/{path.name} has no columns")
    sql = (f"INSERT INTO {_ident(table)} ({', '.join(_ident(str(c)) for c in columns)}) "
           f"VALUES ({', '.join('?' for _ in columns)})")
    conn.executemany(sql, ([_decode(v) for v in json.loads(line)] for line in lines[1:] if line))


def _piece_name(shard: str) -> str:
    # Il pezzo viene dai dati (una chiave di sessione, per esempio): nel nome di
    # un file entra solo in forma sicura, e non è mai vuoto né ``.``/``..``.
    return quote(shard or "none", safe="") + _SUFFIX


def _encode(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"$b64": base64.b64encode(value).decode("ascii")}
    return value


def _decode(value: Any) -> Any:
    if isinstance(value, dict) and set(value) == {"$b64"}:
        return base64.b64decode(value["$b64"])
    return value


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'
