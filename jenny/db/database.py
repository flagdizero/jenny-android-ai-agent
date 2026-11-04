"""Un file SQLite di Jenny, aperto una volta e usato da qualunque thread.

Lo stato di Jenny (non i contenuti: quelli restano file, v. ``jenny/db/__init__``)
vive in pochi database sotto ``<runtime_root>/db/``. Questa classe è tutto
quello che un archivio deve sapere per usarne uno: una transazione per
scrivere, una lettura, la copia per il backup.

**Due connessioni.** Una scrive, sotto un lock: in SQLite c'è comunque un solo
scrittore alla volta, e tenerlo in fila qui evita i ``database is locked`` fra i
thread dello stesso processo. L'altra legge: in WAL chi legge non aspetta chi
scrive e vede l'ultima transazione chiusa. Dentro una transazione si legge con
la connessione che ``write`` restituisce, o non si vede quel che si è appena
scritto.

**Solo il motore che c'è sul telefono.** È il SQLite di Chaquopy, non quello dei
Python del Mac: una query che passa i test può non esistere sul telefono. Dal
plugin 17.1.0 è SQLite 3.53.4 con FTS3/4/5 (misurato il 03/11/2026), senza
estensioni caricabili. Vale per tutto ``jenny/``:
``tests/db/test_engine_features.py`` lo controlla.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypeVar

from loguru import logger

_T = TypeVar("_T")

# Quanto un'altra connessione (un altro processo, o ``sqlite3`` lanciato a mano
# sul telefono) può tenere il file prima che una scrittura rinunci.
BUSY_TIMEOUT_MS = 5000
WAL_SIZE_LIMIT = 16 * 1024 * 1024


# Le tabelle di ogni database, prima di qualunque schema: la versione di ogni
# archivio, e come l'export per gli snapshot divide in pezzi le sue tabelle
# (``jenny/db/shards``). Le crea anche chi ricostruisce un database dai pezzi.
META_TABLES = (
    "CREATE TABLE IF NOT EXISTS schema_versions ("
    "name TEXT PRIMARY KEY, version INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS table_shards (tbl TEXT PRIMARY KEY, expr TEXT NOT NULL)",
)
META_TABLE_NAMES = ("schema_versions", "table_shards")


class SchemaError(RuntimeError):
    """Uno schema non si è potuto portare alla versione del codice."""


class SchemaTooNewError(SchemaError):
    """Il file è di una versione di Jenny più nuova: non si tocca."""


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(
        str(path),
        check_same_thread=False,
        # Le transazioni le apre ``write`` a mano: il modulo non ne apre di sue.
        isolation_level=None,
        timeout=BUSY_TIMEOUT_MS / 1000,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    # In WAL ``NORMAL`` non perde mai la coerenza del file; dopo un crash di
    # corrente può perdere l'ultima transazione. Per lo stato di Jenny basta.
    conn.execute("PRAGMA synchronous=NORMAL")
    # Dopo un checkpoint il ``-wal`` torna a questa misura: senza, resta grande
    # quanto la transazione più grossa che abbia mai visto (una migrazione da
    # centinaia di MB lo lasciava lì per sempre).
    conn.execute(f"PRAGMA journal_size_limit={WAL_SIZE_LIMIT}")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    return conn


def split_statements(sql: str) -> list[str]:
    """Le istruzioni di *sql*, una per una.

    ``executescript`` chiude da solo la transazione aperta prima di partire,
    quindi un passo di schema fatto con quello non tornerebbe indietro tutto
    insieme. Si eseguono le istruzioni a una a una, divise dove SQLite dice che
    un'istruzione è completa (un ``;`` dentro una stringa o un trigger non conta).
    """
    out: list[str] = []
    buf = ""
    for line in sql.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            if buf.strip():
                out.append(buf.strip())
            buf = ""
    if buf.strip():
        out.append(buf.strip())
    return out


class Database:
    """Un file SQLite: ``write`` per le transazioni, ``read`` per le letture."""

    def __init__(self, path: Path, *, name: str) -> None:
        self.path = Path(path)
        self.name = name
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.RLock()
        self._read_lock = threading.Lock()
        self._depth = 0
        self._writer = _connect(self.path)
        self._reader = _connect(self.path)
        self._schemas: dict[str, int] = {}
        self._closed = False
        for ddl in META_TABLES:
            self._writer.execute(ddl)

    # -- scrivere e leggere ---------------------------------------------------

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        """Una transazione: tutto quel che si fa dentro entra insieme, o niente.

        Annidata nello stesso thread, fa parte della transazione di fuori.
        """
        with self._write_lock:
            self._check_open()
            if self._depth:
                self._depth += 1
                try:
                    yield self._writer
                finally:
                    self._depth -= 1
                return
            self._writer.execute("BEGIN IMMEDIATE")
            self._depth = 1
            try:
                yield self._writer
            except BaseException:
                self._depth = 0
                self._writer.execute("ROLLBACK")
                raise
            self._depth = 0
            self._writer.execute("COMMIT")

    def read(self, sql: str, params: Any = ()) -> list[sqlite3.Row]:
        with self._read_lock:
            self._check_open()
            return self._reader.execute(sql, params).fetchall()

    @contextmanager
    def reading(self) -> Iterator[sqlite3.Connection]:
        """Più letture che vedono tutte lo stesso momento (una transazione di lettura)."""
        with self._read_lock:
            self._check_open()
            self._reader.execute("BEGIN")
            try:
                yield self._reader
            finally:
                self._reader.execute("COMMIT")

    async def aread(self, sql: str, params: Any = ()) -> list[sqlite3.Row]:
        return await asyncio.to_thread(self.read, sql, params)

    async def awrite(self, fn: Callable[[sqlite3.Connection], _T]) -> _T:
        """``fn(conn)`` dentro una transazione, fuori dal loop."""

        def run() -> _T:
            with self.write() as conn:
                return fn(conn)

        return await asyncio.to_thread(run)

    # -- schema ---------------------------------------------------------------

    def require(self, schema: Any) -> None:
        """Porta *schema* (``jenny.db.schema.Schema``) alla versione del codice.

        Lo chiama ogni archivio prima di usare le sue tabelle; dopo la prima
        volta costa un dizionario.
        """
        if self._schemas.get(schema.name) == schema.version:
            return
        with self._write_lock:
            if self._schemas.get(schema.name) == schema.version:
                return
            from jenny.db.schema import apply

            apply(self, schema)
            self._schemas[schema.name] = schema.version

    def known_versions(self) -> dict[str, int]:
        """Le versioni degli schemi che questo processo ha chiesto finora."""
        return dict(self._schemas)

    def schema_version(self, name: str) -> int:
        rows = self.read("SELECT version FROM schema_versions WHERE name = ?", (name,))
        return int(rows[0]["version"]) if rows else 0

    # -- copia e chiusura -----------------------------------------------------

    def backup_to(self, dest: Path) -> None:
        """Una copia coerente e compatta del database in *dest*, mentre gli altri scrivono.

        ``VACUUM INTO`` e non una copia del file: il file vivo ha accanto il suo
        ``-wal``, e copiarlo a metà di una transazione darebbe un database rotto.
        Nemmeno l'API di backup, che copia pagina per pagina anche quelle rimaste
        vuote: dopo che si tolgono righe (i pezzetti dei verbali, il 03/11/2026)
        il backup si sarebbe portato dietro lo spazio libero.
        """
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.unlink(missing_ok=True)
        with self._read_lock:
            self._check_open()
            self._reader.execute("VACUUM INTO ?", (str(dest),))

    def vacuum(self) -> None:
        """Riscrive il file senza le pagine vuote, e riporta il ``-wal`` piccolo.

        Tiene la scrittura per tutto il tempo (secondi, su un file grande): chi
        scrive aspetta il lock, non fallisce. Serve circa la misura del file in
        spazio libero.
        """
        with self._write_lock:
            self._check_open()
            self._writer.execute("VACUUM")
            self._writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def close(self) -> None:
        with self._write_lock, self._read_lock:
            if self._closed:
                return
            self._closed = True
            for conn in (self._reader, self._writer):
                try:
                    conn.close()
                except sqlite3.Error as exc:
                    logger.warning("Database {} did not close cleanly: {}", self.name, exc)

    @property
    def closed(self) -> bool:
        return self._closed

    def _check_open(self) -> None:
        if self._closed:
            raise sqlite3.ProgrammingError(f"database {self.name} is closed")
