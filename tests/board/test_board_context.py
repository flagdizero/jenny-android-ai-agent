"""La bacheca nel prompt: una riga per scheda, dentro il tetto, e il cassetto quando lo nomini."""

from __future__ import annotations

from pathlib import Path

from jenny.agent.context import ContextBuilder
from jenny.board import model
from jenny.board.context_block import BOARD_BLOCK_MAX_CHARS, board_block, turn_lines
from jenny.board.store import mutate_board, mutate_reminders, write_card_summary


def _notebook(tmp_path: Path) -> Path:
    root = tmp_path / "wikis" / "piante"
    (root / "wiki").mkdir(parents=True)
    (root / "raw" / "journal").mkdir(parents=True)
    (root / "wiki" / "index.md").write_text("# Piante\n\nLA-MAPPA-INTERA\n")
    (root / "wiki" / "travaso-acero.md").write_text("# travaso acero\n\nVaso 20.\n")
    (root / "wiki" / "dormienza.md").write_text("# dormienza\n\nRiposo.\n")
    return root


def _cards(root: Path, n: int, brief: str = "In vaso 14, sano. " * 20) -> None:
    def apply(board):
        board["skeleton"] = ["Vaso", "Acqua"]
        board["cards"] = [
            {"id": f"pianta-{i}", "title": f"Pianta {i}", "pos": {"x": 1, "y": 1},
             "pages": ["travaso-acero.md"] if i == 0 else []}
            for i in range(n)
        ]

    mutate_board(root, apply)
    for i in range(n):
        write_card_summary(root, f"pianta-{i}", {"brief": brief, "sections": [
            {"title": "Vaso", "text": "Vaso 20 a marzo.", "sources": ["travaso-acero.md"]},
        ]})


def test_a_full_wall_stays_under_the_ceiling(tmp_path) -> None:
    root = _notebook(tmp_path)
    _cards(root, model.MAX_ITEMS)
    mutate_reminders(root, lambda r: r["reminders"].append({
        "id": "r-1", "card": "pianta-0", "text": "Travaso nel vaso 20",
        "when": {"kind": "date", "date": "2027-03-01", "precision": "month"},
    }))
    block = board_block(root, alert=lambda s: False)
    assert len(block) <= BOARD_BLOCK_MAX_CHARS
    # Quaranta schede non ci stanno tutte coi riassunti: le ultime diventano «e altre N».
    shown = block.count("- **Pianta ")
    assert shown >= 12
    if shown < model.MAX_ITEMS:
        assert f"and {model.MAX_ITEMS - shown} more card(s)" in block
    assert "[next: Travaso nel vaso 20, 2027-03]" in block
    assert "Off the board: 1 page(s)" in block


def test_a_problem_reaches_the_block(tmp_path) -> None:
    root = _notebook(tmp_path)
    _cards(root, 1, brief="Sensore.")
    mutate_board(root, lambda b: b["cards"][0]["userSections"].append(
        {"id": "s-app", "kind": "app", "app": "waterbot", "params": {}}))
    assert "[PROBLEM" in board_block(root, alert=lambda s: True)


def test_the_board_replaces_the_map_in_the_project_block(tmp_path) -> None:
    root = _notebook(tmp_path)
    builder = ContextBuilder(tmp_path)
    assert "LA-MAPPA-INTERA" in builder._project_block_vars(root)["project_map"]
    _cards(root, 2, brief="In vaso 14.")
    fed = builder._project_block_vars(root)
    assert fed["project_map"] == ""
    assert "**Pianta 0** — In vaso 14." in fed["project_board"]


def test_naming_a_card_brings_its_drawer_into_the_turn(tmp_path) -> None:
    root = _notebook(tmp_path)
    _cards(root, 2, brief="In vaso 14.")
    assert turn_lines(root, "che ne dici di pianta 1 adesso?")[0].startswith(
        "Drawer of the card «Pianta 1»")
    assert turn_lines(root, "parliamo d'altro") == []
    # Il titolo intero, non un pezzo di parola.
    assert turn_lines(root, "Pianta 10 non esiste") == []
    messages = ContextBuilder(tmp_path).build_messages(
        [], "Come sta Pianta 0?", workspace=root, channel="websocket", chat_id="project:piante",
    )
    assert "Drawer of the card «Pianta 0»" in str(messages[-1]["content"])
    assert "Drawer of the card" not in messages[0]["content"], "il cassetto nel prefisso in cache"


def test_the_open_questions_of_the_map_still_reach_the_turn(tmp_path) -> None:
    """Con la bacheca la mappa non entra piu' a ogni turno, ma la sua sezione
    «Open» si': e' li' che il giardiniere lascia le domande per l'utente."""
    root = _notebook(tmp_path)
    (root / "wiki" / "index.md").write_text(
        "# Piante\n\n## Aperto\n\n- [[travaso-acero]] dice marzo, [[dormienza]] dice febbraio.\n"
        "\n## Pagine\n\n- [[travaso-acero]]\n"
    )
    _cards(root, 1, brief="In vaso 14.")
    block = board_block(root)
    assert "Open in the map: - [[travaso-acero]] dice marzo" in block
    assert "## Pagine" not in block


def test_a_micro_app_and_a_cron_reach_the_block_and_the_drawer(tmp_path) -> None:
    from jenny.board import micro as mc

    root = _notebook(tmp_path)
    _cards(root, 1, brief="In vaso 14.")
    mc.write_micro(root, "m-acerello", {
        "title": "Acerello", "fields": {"moisture": {"label": "Umidità", "unit": "%"}},
    }, "def run(ctx):\n    return {'moisture': 38}\n", draft=False)
    from datetime import datetime, timezone

    mc.append_reading(mc.micro_dir(root, "m-acerello"), {"moisture": 38},
                      now=datetime.now(timezone.utc))

    def apply(board):
        board["cards"][0]["userSections"] += [
            {"id": "s-micro", "kind": "micro", "micro": "m-acerello"},
            {"id": "s-cron", "kind": "cron", "job": "ab12cd34", "name": "Meteo"},
        ]
        board["cronResults"] = {"pianta-0/s-cron": {"at": "2026-10-05T09:00:00+02:00",
                                                   "status": "ok", "text": "Oggi piove."}}

    mutate_board(root, apply)
    assert "[auto: Acerello 38% · cron Meteo]" in board_block(root)
    drawer = turn_lines(root, "come sta pianta 0?")[0]
    assert "Micro app «Acerello» (`m-acerello`, micro_app tool): Umidità 38%" in drawer
    assert "board/micro/m-acerello/run.py" in drawer
    assert "Notebook cron «Meteo»" in drawer and "Oggi piove." in drawer
