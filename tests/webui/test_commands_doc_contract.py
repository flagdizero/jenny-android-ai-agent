"""La pagina pubblica dei comandi RPC dice il vero su codici e rifiuti.

``docs/reference/websocket.md`` finisce anche sul sito (``jenny-site``): un
comando che manca dalla tabella, o un codice d'errore che il gateway manda e la
pagina non nomina, e' un contratto sbagliato per chiunque scriva un client. Q3
della revisione profonda: ``conflict`` era descritto solo come «il file e'
cambiato sotto», mentre rinomino e cancellazione lo usano per «Jenny sta ancora
lavorando li'».
"""

from __future__ import annotations

import re
from pathlib import Path

from jenny.webui import commands

ROOT = Path(__file__).resolve().parents[2]
DOC = (ROOT / "docs" / "reference" / "websocket.md").read_text(encoding="utf-8")


def _codes_raised() -> set[str]:
    sources = [
        ROOT / "jenny" / "webui" / "commands.py",
        ROOT / "jenny" / "webui" / "project_rename.py",
    ]
    found: set[str] = set()
    for path in sources:
        text = path.read_text(encoding="utf-8")
        found |= set(re.findall(r'CommandError\(\s*"([a-z_]+)"', text))
        found |= set(re.findall(r'code="([a-z_]+)"', text))
    return found


def _doc_codes() -> set[str]:
    row = next(r for r in DOC.splitlines() if r.startswith("Error codes:"))
    return set(re.findall(r"`([a-z_]+)`", row))


def _row(method: str) -> str:
    return next(r for r in DOC.splitlines() if r.startswith(f"| `{method}` |"))


def test_every_code_the_gateway_sends_is_documented() -> None:
    missing = _codes_raised() - _doc_codes()
    assert not missing, f"codici non documentati in websocket.md: {sorted(missing)}"


def test_every_documented_code_is_in_the_closed_set_of_the_docstring() -> None:
    doc = commands.CommandError.__doc__ or ""
    for code in _doc_codes():
        assert f"``{code}``" in doc, code


def test_every_command_has_a_row() -> None:
    for method in commands.COMMANDS:
        assert _row(method)


def test_conflict_is_defined_for_both_of_its_meanings() -> None:
    definition = next(r for r in DOC.splitlines() if r.startswith("`conflict` is"))
    assert "page.write" in definition
    assert "project.rename" in definition and "project.delete" in definition
    assert "`conflict`" in _row("project.rename")
    assert "`conflict`" in _row("project.delete")
