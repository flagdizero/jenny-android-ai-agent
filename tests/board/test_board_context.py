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


def test_a_cron_reaches_the_block_and_the_drawer(tmp_path) -> None:
    root = _notebook(tmp_path)
    _cards(root, 1, brief="In vaso 14.")

    def apply(board):
        board["cards"][0]["userSections"] += [
            {"id": "s-cron", "kind": "cron", "job": "ab12cd34", "name": "Meteo"},
        ]
        board["cronResults"] = {"pianta-0/s-cron": {"at": "2026-10-05T09:00:00+02:00",
                                                   "status": "ok", "text": "Oggi piove."}}

    mutate_board(root, apply)
    # Solo i nomi nel prompt di sistema: l'esito arriva col cassetto.
    assert "[auto: cron Meteo]" in board_block(root)
    assert "Oggi piove." not in board_block(root)
    drawer = turn_lines(root, "come sta pianta 0?")[0]
    assert "Notebook cron «Meteo»" in drawer and "Oggi piove." in drawer
    # Una citazione datata, non una frase al presente da eseguire.
    assert ("its last result (2026-10-05T09:00:00+02:00, its own words, may be out of date): "
            "«Oggi piove.»") in drawer


# ── Senza bacheca: il perche' arriva nel prompt ─────────────────────────────


def _research_archive(tmp_path: Path) -> tuple[Path, Path]:
    """La forma di ricerca senza diario: il caso che il 05/10 ha prodotto una causa inventata."""
    ws = tmp_path / "workspace"
    root = ws / "wikis" / "finanza"
    (root / "wiki" / "concepts").mkdir(parents=True)
    (root / "wiki" / "entities").mkdir()
    (root / "wiki" / "index.md").write_text("# Finanza\n")
    (root / "wiki" / "concepts" / "etf-basics.md").write_text("# ETF basics\n\nNo frontmatter.\n")
    return ws, root


def test_a_research_archive_says_why_it_has_no_board(tmp_path) -> None:
    from jenny.agent.board_pass import BoardPass
    from jenny.board.context_block import board_absence

    ws, root = _research_archive(tmp_path)
    assert board_absence(root) == "research"
    # La stessa risposta della passata: nessuna bacheca da proporre.
    assert BoardPass(root, ws).reason() is None


def test_the_first_journal_line_turns_an_archive_into_a_notebook(tmp_path) -> None:
    from jenny.agent.board_pass import BoardPass
    from jenny.board.context_block import board_absence

    ws, root = _research_archive(tmp_path)
    (root / "raw" / "journal").mkdir(parents=True)
    (root / "raw" / "journal" / "20261005.md").write_text("# 2026-10-05\n\n- 17:14 — una riga\n")
    assert board_absence(root) == "next_pass"
    assert BoardPass(root, ws).reason() == "migrate"


def test_the_absence_follows_the_first_board_through_its_answers(tmp_path) -> None:
    from jenny.board.context_block import board_absence

    root = _notebook(tmp_path)
    assert board_absence(root) == "next_pass"

    mutate_board(root, lambda b: b["suggestions"].append({"id": "s-1", "kind": "initial_board"}))
    assert board_absence(root) == "proposed"

    def decline(board):
        board["suggestions"] = []
        board["declined"].append({"kind": "initial_board", "at": "2026-10-05T17:00:00+02:00"})

    mutate_board(root, decline)
    assert board_absence(root) == "declined"

    def postpone(board):
        board["declined"] = []
        board["later"].append({"kind": "initial_board", "untilPass": board["passes"] + 3})

    mutate_board(root, postpone)
    assert board_absence(root) == "later"

    _cards(root, 1, brief="In vaso 14.")
    assert board_absence(root) == ""


def test_a_notebook_without_pages_waits_for_them(tmp_path) -> None:
    from jenny.board.context_block import board_absence

    root = tmp_path / "wikis" / "vuoto"
    (root / "wiki").mkdir(parents=True)
    (root / "wiki" / "index.md").write_text("# Vuoto\n")
    assert board_absence(root) == "no_pages"


def test_the_prompt_tells_why_there_is_no_board_and_what_does_not_decide_it(tmp_path) -> None:
    from jenny.utils.prompt_templates import render_template

    ws, root = _research_archive(tmp_path)
    fed = ContextBuilder(ws)._project_block_vars(root)
    assert fed["project_board"] == ""
    assert fed["project_board_absence"] == "research"

    text = render_template("agent/project.md", project_path="wikis/finanza", **fed)
    assert "This notebook has no board yet" in text
    assert "research archive" in text
    assert "not the pages' frontmatter" in text


def test_a_board_with_cards_keeps_the_absence_out_of_the_prompt(tmp_path) -> None:
    from jenny.utils.prompt_templates import render_template

    root = _notebook(tmp_path)
    _cards(root, 1, brief="In vaso 14.")
    fed = ContextBuilder(tmp_path)._project_block_vars(root)
    assert fed["project_board_absence"] == ""

    text = render_template("agent/project.md", project_path="wikis/piante", **fed)
    assert "This notebook opens on its **board**" in text
    assert "has no board yet" not in text


def test_who_changes_the_board_is_said_to_each_reader(tmp_path) -> None:
    """La chat lancia l'editore, l'editore usa il tool, gli altri subagent non la
    toccano. Fino al 09/10/2026 la chat leggeva «you do not change it: the
    gardener proposes», e mandava al giardiniere richieste che non poteva fare."""
    from jenny.utils.prompt_templates import render_template

    root = _notebook(tmp_path)
    _cards(root, 1, brief="In vaso 14.")
    fed = ContextBuilder(tmp_path)._project_block_vars(root)

    def render(*tools: str) -> str:
        return render_template("agent/project.md", project_path="wikis/piante",
                               has=lambda name: name in tools, **fed)

    chat = render("spawn", "journal_append")
    assert "is the job of a `notebook-editor`" in chat and "ask once first" in chat
    assert "you do not change it" not in chat
    assert "the `board` tool, exactly as asked" in render("board", "write_file")
    assert "you do not change it — the main agent does" in render("write_file")


def test_the_chat_knows_what_waits_in_the_envelope(tmp_path) -> None:
    """Senza, a «accetta la proposta» Jenny la cercava con grep (09/10/2026)."""
    root = _notebook(tmp_path)
    _cards(root, 1, brief="In vaso 14.")
    mutate_board(root, lambda b: b["suggestions"].append({
        "id": "sg-x", "kind": "add_connection", "subject": "Ginkgo — Fico", "text": "Un filo?"}),
        actor="jenny")
    assert "In the envelope, waiting for the user: «Ginkgo — Fico» (add_connection)." in \
        board_block(root)
