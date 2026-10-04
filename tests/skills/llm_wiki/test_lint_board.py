"""Il passo della bacheca nel lint della wiki: cassetti stantii, fonti sparite, fili, attese."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from jenny.board import model
from jenny.board.store import mutate_board, mutate_reminders, write_card_summary

_SCRIPTS = Path(__file__).resolve().parents[3] / "jenny" / "skills" / "llm-wiki" / "scripts"


@pytest.fixture(scope="module")
def lint_wiki():
    spec = importlib.util.spec_from_file_location("lint_wiki", _SCRIPTS / "lint_wiki.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _notebook(tmp_path: Path) -> Path:
    root = tmp_path / "piante"
    (root / "wiki").mkdir(parents=True)
    (root / "wiki" / "index.md").write_text("# Piante\n")
    (root / "wiki" / "acero.md").write_text("# acero\n\nVaso 14.\n")
    (root / "wiki" / "dormienza.md").write_text("# dormienza\n")
    mutate_board(root, lambda b: b["cards"].append(
        {"id": "acero", "title": "Acero", "pages": ["acero.md"], "pos": {"x": 1, "y": 1}}))
    return root


def test_the_two_fingerprints_and_ceilings_agree(lint_wiki) -> None:
    texts = [("b.md", "uno"), ("a.md", "due")]
    assert lint_wiki.board_fingerprint(texts) == model.fingerprint(texts)
    assert lint_wiki.BOARD_MAX_CONNECTIONS == model.MAX_CONNECTIONS


def test_a_clean_board_has_no_issues(tmp_path, lint_wiki, capsys) -> None:
    root = _notebook(tmp_path)
    write_card_summary(root, "acero", {
        "fingerprint": model.fingerprint([("acero.md", "# acero\n\nVaso 14.\n")]),
        "sections": [{"title": "Vaso", "text": "14.", "sources": ["acero.md"]}],
    })
    mutate_board(root, lambda b: b.__setitem__("previews", {"dormienza.md": 4}))
    assert lint_wiki.lint_board(root, today="2026-10-04") == 0
    out = capsys.readouterr().out
    assert "1 cards, 1 of 2 pages off the board" in out
    assert "dormienza.md (4)" in out


def test_stale_drawers_missing_sources_threads_and_overdue(tmp_path, lint_wiki, capsys) -> None:
    root = _notebook(tmp_path)
    write_card_summary(root, "acero", {
        "fingerprint": "sha1:old",
        "sections": [{"title": "Vaso", "text": "14.", "sources": ["sparita.md"]}],
    })

    def threads(board):
        board["connections"].append({"a": "acero", "b": "fico", "label": "x"})

    mutate_board(root, threads)
    mutate_reminders(root, lambda r: r["reminders"].append({
        "id": "r-1", "card": "acero", "text": "Concime",
        "when": {"kind": "date", "date": "2026-09-01"},
    }))
    assert lint_wiki.lint_board(root, today="2026-10-04") == 4
    out = capsys.readouterr().out
    assert "stale drawer: `acero`" in out
    assert "cites missing pages: sparita.md" in out
    assert "no longer on the board" in out
    assert "past their date and still open: Concime" in out


def test_a_notebook_without_a_board_is_not_linted_for_one(tmp_path, lint_wiki) -> None:
    root = tmp_path / "ricerca"
    (root / "wiki").mkdir(parents=True)
    assert lint_wiki.lint_board(root) == 0
