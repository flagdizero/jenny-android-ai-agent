"""Le tabelle di un archivio, e come si portano da una versione alla successiva.

Ogni archivio dichiara il suo ``Schema`` nel suo modulo e lo chiede con
``Database.require`` prima di usarlo: non c'è un elenco centrale da tenere
allineato, né un ordine di import da rispettare al boot. Il passo *i* dello
schema porta le tabelle dalla versione *i* alla *i + 1*; la versione è il numero
dei passi, così non può dire una cosa diversa da quel che i passi fanno.

Un file scritto da una Jenny più nuova (versione più alta di quella che il
codice conosce) non si tocca: ``SchemaTooNewError``. Tornare a un APK vecchio non
deve mai riscrivere uno schema che non capisce.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

from jenny.db.database import SchemaError, SchemaTooNewError, split_statements

if TYPE_CHECKING:
    from jenny.db.database import Database

Step = str | Callable[[sqlite3.Connection], None]


@dataclass(frozen=True)
class Schema:
    name: str
    steps: tuple[Step, ...]

    @property
    def version(self) -> int:
        return len(self.steps)


def apply(database: Database, schema: Schema) -> None:
    """I passi che mancano a *schema* in *database*, in una transazione sola."""
    with database.write() as conn:
        row = conn.execute(
            "SELECT version FROM schema_versions WHERE name = ?", (schema.name,)
        ).fetchone()
        current = int(row["version"]) if row else 0
        if current > schema.version:
            raise SchemaTooNewError(
                f"{database.name}.{schema.name} is at version {current}, "
                f"this Jenny knows {schema.version}"
            )
        if current == schema.version:
            return
        try:
            for step in schema.steps[current:]:
                if callable(step):
                    step(conn)
                else:
                    for statement in split_statements(step):
                        conn.execute(statement)
        except sqlite3.Error as exc:
            raise SchemaError(
                f"{database.name}.{schema.name}: step to version {current + 1}+ failed: {exc}"
            ) from exc
        conn.execute(
            "INSERT INTO schema_versions(name, version) VALUES (?, ?) "
            "ON CONFLICT(name) DO UPDATE SET version = excluded.version",
            (schema.name, schema.version),
        )
    logger.info("Database {}: {} {} -> {}", database.name, schema.name, current, schema.version)
