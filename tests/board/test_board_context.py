"""La bacheca nel prompt: una riga per scheda, dentro il tetto, e il cassetto quando lo nomini."""

from __future__ import annotations

from pathlib import Path

from jenny.agent.context import ContextBuilder
from jenny.board import model
from jenny.board.context_block import (
    BOARD_BLOCK_MAX_CHARS,
    board_block,
    drawer_text,
    named_cards,
    turn_lines,
)
from jenny.board.store import mutate_board, mutate_reminders, read_board, write_card_summary


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


def _jobs(tmp_path: Path, monkeypatch, jobs: list[dict]) -> None:
    """I cron del quaderno stanno nello scheduler: ``cron/jobs.json`` del workspace."""
    import json

    (tmp_path / "cron").mkdir(exist_ok=True)
    (tmp_path / "cron" / "jobs.json").write_text(json.dumps({"jobs": jobs}), encoding="utf-8")
    monkeypatch.setattr("jenny.config.paths.get_workspace_path", lambda: tmp_path)


def test_a_cron_reaches_the_block_and_the_drawer(tmp_path, monkeypatch) -> None:
    root = _notebook(tmp_path)
    _cards(root, 1, brief="In vaso 14.")
    _jobs(tmp_path, monkeypatch, [
        {"id": "ab12cd34", "name": "Meteo", "enabled": True,
         "payload": {"message": "guarda il meteo",
                     "board": {"notebook": "piante", "card": "pianta-0", "section": "@auto"}}},
        {"id": "ef56ab78", "name": "Gelo", "enabled": True,
         "payload": {"message": "avvisa se gela",
                     "board": {"notebook": "piante", "card": "@notebook", "section": "@auto"}}},
    ])
    mutate_board(root, lambda b: b.__setitem__("cronResults", {
        "ab12cd34": {"at": "2026-10-05T09:00:00+02:00", "status": "ok", "text": "Oggi piove."}}))
    # Solo i nomi nel prompt di sistema: l'esito arriva col cassetto.
    assert "[auto: cron Meteo]" in board_block(root)
    assert "Oggi piove." not in board_block(root)
    drawer = turn_lines(root, "come sta pianta 0?")[0]
    assert "Notebook cron «Meteo»" in drawer and "Oggi piove." in drawer
    # Il cron di tutto il quaderno non è nel cassetto di una scheda.
    assert "Gelo" not in drawer
    # Una citazione datata, non una frase al presente da eseguire.
    assert ("its last result (2026-10-05T09:00:00+02:00, its own words, may be out of date): "
            "«Oggi piove.»") in drawer


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


# ── Le foto del cassetto, le schede nominate per parola, le aggiunte recenti ──


def _acero(root: Path) -> None:
    (root / "raw" / "photos").mkdir(parents=True, exist_ok=True)
    for i in range(8):
        (root / "raw" / "photos" / f"a{i}.jpg").write_bytes(b"jpg")

    def apply(board):
        board["skeleton"] = ["Vaso"]
        board["cards"] = [
            {"id": "acero", "title": "Acero giapponese", "pos": {"x": 1, "y": 1}, "pages": [],
             "userSections": [
                 {"id": f"s-photo-{i}", "kind": "photo", "path": f"raw/photos/a{i}.jpg",
                  "created": f"2026-10-{i + 1:02d}T10:00:00+02:00",
                  "caption": "Il germoglio è rosso" if i == 7 else ""}
                 for i in range(8)
             ] + [{"id": "s-photo-gone", "kind": "photo", "path": "raw/photos/gone.jpg",
                   "created": "2026-10-30T10:00:00+02:00", "caption": "senza file"}]},
            {"id": "acero-rosso", "title": "Acero rosso", "pos": {"x": 40, "y": 1}, "pages": []},
            {"id": "limone", "title": "Limone", "pos": {"x": 80, "y": 1}, "pages": [],
             "userSections": [{"id": "s-note-1", "kind": "note", "text": "Acqua calcarea?",
                               "created": "2026-10-14T17:50:00+02:00"}]},
        ]

    mutate_board(root, apply)


def test_the_drawer_in_the_turn_lists_the_photos(tmp_path) -> None:
    root = _notebook(tmp_path)
    _acero(root)
    drawer = drawer_text(root, model.card_by_id(read_board(root), "acero"))
    line = next(x for x in drawer.splitlines() if x.startswith("- Photos the user pinned"))
    # Dalla più recente, con data, nota e percorso; sei, poi quante altre.
    assert line.startswith("- Photos the user pinned (8, newest first): "
                           "08 Oct «Il germoglio è rosso» raw/photos/a7.jpg; "
                           "07 Oct (no note) raw/photos/a6.jpg;")
    assert "raw/photos/a2.jpg" in line and "raw/photos/a1.jpg" not in line
    assert "+2 more" in line and "read_file a path" in line
    # Una foto il cui file non c'è più non si nomina.
    assert "gone.jpg" not in line


def test_a_word_of_the_title_names_a_card_only_when_it_is_its_own(tmp_path) -> None:
    root = _notebook(tmp_path)
    _acero(root)
    # «giapponese» sta in un titolo solo; «acero» in due, e non nomina nessuno.
    assert [c["id"] for c in named_cards(root, "e l'acero giapponese?")] == ["acero"]
    assert [c["id"] for c in named_cards(root, "il giapponese come sta?")] == ["acero"]
    assert named_cards(root, "ho messo una foto dell'acero") == []
    assert [c["id"] for c in named_cards(root, "il limone ha sete")] == ["limone"]
    # Un pezzo di parola non conta.
    assert named_cards(root, "limoncello") == []


def test_a_single_word_of_a_title_is_enough(tmp_path) -> None:
    root = _notebook(tmp_path)

    def apply(board):
        board["cards"] = [{"id": "acero", "title": "Acero giapponese", "pos": {"x": 1, "y": 1}}]

    mutate_board(root, apply)
    assert [c["id"] for c in named_cards(root, "Ho messo una foto dell'acero la vedi?")] \
        == ["acero"]


def test_what_was_pinned_since_the_last_message_reaches_the_turn(tmp_path) -> None:
    root = _notebook(tmp_path)
    _acero(root)
    # Nessuna scheda nominata, e lo stesso il turno sa della foto e della nota.
    lines = turn_lines(root, "l'hai vista?", since="2026-10-07T12:00:00+02:00")
    assert lines == [
        "Since the last message the user pinned on the board: "
        "a note on «Limone»: «Acqua calcarea?»; "
        "a photo on «Acero giapponese» (raw/photos/a7.jpg, their note: «Il germoglio è rosso»). "
        "Their notes are already in the notebook's journal: do not write them there again."
    ]
    # Una foto il cui file non c'è più non si nomina. Un istante senza fuso (come i messaggi della sessione) si legge come locale.
    assert turn_lines(root, "l'hai vista?", since="2099-01-01T00:00:00") == []
    assert turn_lines(root, "l'hai vista?") == []


def test_the_turn_gets_board_since_from_the_caller(tmp_path) -> None:
    root = _notebook(tmp_path)
    _acero(root)
    messages = ContextBuilder(tmp_path).build_messages(
        [], "la vedi?", workspace=root, channel="websocket", chat_id="project:piante",
        board_since="2026-10-14T17:00:00+02:00",
    )
    assert "a note on «Limone»" in str(messages[-1]["content"])
    assert "pinned on the board" not in messages[0]["content"], "nel prefisso in cache"
