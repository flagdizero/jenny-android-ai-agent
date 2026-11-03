"""Il promemoria per cancellare le migrazioni scadute.

Ogni migrazione dice da quale versione di Jenny il suo modulo va tolto
(``remove_after``). Quando ``pyproject.toml`` ci arriva, questo test diventa
rosso: si cancella il modulo e la sua riga in ``registry.migrations``. Quando
l'elenco è vuoto si cancellano il pacchetto, la riga del container e questa
cartella di test.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from jenny.db_migration.registry import migrations

ROOT = Path(__file__).resolve().parents[2]


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", text)[:3])


def test_no_migration_has_outlived_its_version() -> None:
    current = _version(tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"])
    expired = [m.name for m in migrations() if _version(m.remove_after) <= current]
    assert not expired, (
        f"migrazioni da cancellare (versione {current}): {expired}. Togli il modulo da "
        "jenny/db_migration/, la sua riga in registry.migrations() e i suoi test."
    )


def test_every_migration_names_a_version() -> None:
    names = [m.name for m in migrations()]
    assert len(names) == len(set(names))
    for m in migrations():
        assert len(_version(m.remove_after)) == 3, m.name
