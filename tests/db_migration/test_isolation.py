"""Lo strumento di migrazione resta separato, così si cancella in un colpo.

Lo importa solo ``runtime/container.py``, una volta, al boot. Se un altro modulo
del core ci si appoggia, cancellare il pacchetto quando le migrazioni scadono
romperebbe quel modulo: questo test lo dice prima.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = "jenny.db_migration"
ALLOWED = {Path("jenny/runtime/container.py")}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def test_only_the_container_imports_the_migration_tool() -> None:
    offenders = []
    for base in ("jenny", "jenny_extensions"):
        for path in (ROOT / base).rglob("*.py"):
            rel = path.relative_to(ROOT)
            if rel.parts[:2] == ("jenny", "db_migration") or rel in ALLOWED:
                continue
            if any(n == PACKAGE or n.startswith(PACKAGE + ".") for n in _imports(path)):
                offenders.append(str(rel))
    assert not offenders, (
        f"{PACKAGE} è da cancellare fra qualche versione: lo importa solo il container. "
        f"Lo importano anche: {offenders}"
    )


def test_the_container_calls_it_once() -> None:
    src = (ROOT / "jenny/runtime/container.py").read_text(encoding="utf-8")
    assert src.count("run_pending(") == 1
