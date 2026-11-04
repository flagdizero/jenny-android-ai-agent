"""Solo il SQLite che c'è sul telefono.

Il SQLite del telefono è quello che porta Chaquopy, non quello dei Python del
Mac: una funzione che passa tutti i test può rompere il gateway al primo avvio.
Qui si cercano, nelle stringhe del codice (non nei commenti né nelle docstring),
le parole delle estensioni che mancano; se un giorno Chaquopy le porta, si
rimisura e si toglie la parola.

FTS3/4/5 non ci sono più: fino al plugin 17.0.0 Chaquopy portava SQLite 3.50.4
senza, dal 17.1.0 porta Python 3.11.17 con SQLite 3.53.4 compilato con FTS3/4/5 e
RTREE (misurato il 03/11/2026 sulla ``libsqlite3_python.so`` dell'APK, e provato
sul telefono). In cambio il plugin non deve tornare sotto il 17.1.0: lo dice
:func:`test_the_plugin_brings_fts5`. Le estensioni caricabili restano fuori: il
``_sqlite3`` di Chaquopy è compilato senza ``enable_load_extension``.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

_MISSING = re.compile(r"\bvec0\b|\bsqlite_vec\b|\bload_extension\b", re.IGNORECASE)


def _docstrings(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def test_no_sqlite_feature_the_phone_lacks() -> None:
    hits = []
    for base in ("jenny", "jenny_extensions"):
        for path in (ROOT / base).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            skip = _docstrings(tree)
            for node in ast.walk(tree):
                if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                        and id(node) not in skip and _MISSING.search(node.value)):
                    hits.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not hits, "il SQLite del telefono non ha queste estensioni:\n" + "\n".join(hits)


def test_the_check_catches_a_virtual_table() -> None:
    tree = ast.parse('"""doc vec0"""\nSQL = "CREATE VIRTUAL TABLE t USING vec0(x)"\n')
    skip = _docstrings(tree)
    found = [n for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
             and id(n) not in skip and _MISSING.search(n.value)]
    assert len(found) == 1


# Il primo plugin che porta un SQLite con FTS5 (Chaquopy 17.1.0, #1466).
_FTS5_SINCE = (17, 1, 0)


def test_the_plugin_brings_fts5() -> None:
    gradle = (ROOT / "android" / "build.gradle.kts").read_text(encoding="utf-8")
    found = re.search(r'id\("com\.chaquo\.python"\) version "(\d+)\.(\d+)\.(\d+)"', gradle)
    assert found, "la versione del plugin Chaquopy non si legge più da android/build.gradle.kts"
    version = tuple(int(part) for part in found.groups())
    assert version >= _FTS5_SINCE, (
        f"Chaquopy {'.'.join(map(str, version))} porta un SQLite senza FTS5: "
        "il codice che lo usa si romperebbe sul telefono")
