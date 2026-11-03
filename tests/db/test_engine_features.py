"""Solo il SQLite che c'è sul telefono.

Chaquopy 17 porta SQLite 3.50.4 **senza** FTS3/4/5 (misurato il 03/11/2026 sulla
``libsqlite3_python.so`` dell'APK), mentre i Python del Mac hanno 3.53 con FTS5:
una tabella ``fts5`` passerebbe tutti i test e romperebbe il gateway al primo
avvio sul telefono. Qui si cercano, nelle stringhe del codice (non nei commenti
né nelle docstring), le parole delle estensioni che mancano. Se un giorno
Chaquopy le porta, si rimisura e si toglie la parola.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

_MISSING = re.compile(r"\bfts[345]\b|\bvec0\b|\bsqlite_vec\b|\bload_extension\b", re.IGNORECASE)


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
    tree = ast.parse('"""doc fts5"""\nSQL = "CREATE VIRTUAL TABLE t USING fts5(x)"\n')
    skip = _docstrings(tree)
    found = [n for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
             and id(n) not in skip and _MISSING.search(n.value)]
    assert len(found) == 1
