"""La passata della bacheca: cosa decide il codice, e cosa lascia fare al modello.

Il modello qui è un agente finto che chiama gli strumenti della cassetta come
li chiamerebbe lui; si guarda quel che resta su disco. Le regole che contano
— il giardiniere non tocca mai quel che è dell'utente, il tetto delle proposte,
un «no» che non torna, un cassetto con l'impronta invariata che non si riscrive
— le fanno valere gli strumenti e l'imbuto, e qui si misurano.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from jenny.agent.board_pass import BoardPass, board_needs_pass, run_board_pass
from jenny.agent.board_tools import BoardPassState, build_board_tools
from jenny.board import model
from jenny.board.corrections import create_correction, open_corrections
from jenny.board.store import (
    mutate_board,
    read_board,
    read_card_summary,
    read_reminders,
    write_card_summary,
)

PAGES = {
    "travaso-acero.md": "# travaso acero\n\nDal vaso 14 al vaso 20, in dormienza verso marzo.\n",
    "irrigazione-acero.md": "# irrigazione acero\n\nQuando i primi 2 cm sono asciutti.\n",
    "monstera.md": "# monstera\n\nIn soggiorno.\n",
    "ginkgo.md": "# ginkgo\n\nArrivato a settembre.\n",
    "ginkgo-talea.md": "# talea\n\nIn acqua.\n",
    "ginkgo-vaso.md": "# vaso ginkgo\n\nNon registrato.\n",
    "dormienza.md": "# dormienza\n\nIl riposo invernale.\n- [ ] Travaso dell'acero a marzo 2027\n",
}


def _notebook(tmp_path: Path) -> tuple[Path, Path]:
    ws = tmp_path / "workspace"
    root = ws / "wikis" / "piante"
    (root / "wiki").mkdir(parents=True)
    (root / "raw" / "journal").mkdir(parents=True)
    (root / "audit" / "resolved").mkdir(parents=True)
    (root / "wiki" / "index.md").write_text("# Piante\n")
    for name, text in PAGES.items():
        (root / "wiki" / name).write_text(text)
    return ws, root


def _seed_board(root: Path) -> None:
    def apply(board):
        board["skeleton"] = ["Vaso e travaso", "Acqua"]
        board["cards"] = [
            {"id": "acero", "title": "Acero", "pages": ["travaso-acero.md", "irrigazione-acero.md"],
             "pos": {"x": 5, "y": 6, "by": "user"}},
            {"id": "monstera", "title": "Monstera", "pages": ["monstera.md"],
             "pos": {"x": 47, "y": 4, "by": "jenny"}},
        ]
    mutate_board(root, apply)


def _state(root: Path, ws: Path) -> BoardPassState:
    return BoardPass(root, ws).build_state()


def _tools(state: BoardPassState) -> dict[str, Any]:
    return {t.name: t for t in build_board_tools(state)}


# ── Quando gira ─────────────────────────────────────────────────────────────


def test_a_notebook_without_a_board_asks_for_its_first(tmp_path) -> None:
    ws, root = _notebook(tmp_path)
    assert BoardPass(root, ws).reason() == "migrate"
    assert board_needs_pass(root, ws)


def test_a_research_wiki_stays_without_a_board(tmp_path) -> None:
    ws, root = _notebook(tmp_path)
    (root / "wiki" / "concepts").mkdir()
    assert BoardPass(root, ws).reason() is None


def test_a_research_shaped_notebook_with_a_journal_gets_a_board(tmp_path) -> None:
    """Le pagine in ``concepts/`` non fanno di un quaderno vissuto un archivio."""
    ws, root = _notebook(tmp_path)
    (root / "wiki" / "concepts").mkdir()
    (root / "raw" / "journal" / "20261002.md").write_text("- Ginkgo arrivato.\n")
    assert BoardPass(root, ws).reason() == "migrate"


def test_a_drawer_with_unchanged_pages_is_not_rewritten(tmp_path) -> None:
    ws, root = _notebook(tmp_path)
    _seed_board(root)
    bp = BoardPass(root, ws)
    assert {c["id"] for c in bp.stale_cards()} == {"acero", "monstera"}
    for card in read_board(root)["cards"]:
        write_card_summary(root, card["id"], {
            "brief": "x", "fingerprint": bp.card_fingerprint(card), "sections": [],
        })
    bp = BoardPass(root, ws)
    assert bp.stale_cards() == []
    # Una pagina cambiata riapre solo il suo cassetto.
    (root / "wiki" / "monstera.md").write_text("# monstera\n\nSpostata in camera.\n")
    assert [c["id"] for c in BoardPass(root, ws).stale_cards()] == ["monstera"]


def test_the_brake_holds_a_reason_that_stays_true(tmp_path) -> None:
    ws, root = _notebook(tmp_path)
    _seed_board(root)
    mutate_board(root, lambda b: b.__setitem__("lastPassAt", model.now_iso()), actor="jenny")
    assert BoardPass(root, ws).reason() is None, "cassetti da scrivere, ma la passata e' appena girata"
    # Le righe di diario nuove passano comunque: le porta la passata della wiki.
    assert BoardPass(root, ws).reason(journal_lines=3) == "journal"
    later = datetime.now().astimezone() + timedelta(hours=7)
    assert BoardPass(root, ws, now=later).reason() == "drawers"


# ── Gli strumenti ───────────────────────────────────────────────────────────


async def test_assign_takes_only_pages_off_the_board(tmp_path) -> None:
    ws, root = _notebook(tmp_path)
    _seed_board(root)
    state = _state(root, ws)
    tools = _tools(state)
    out = await tools["board_assign_page"].execute(page="travaso-acero.md", card="monstera")
    assert out.startswith("Refused")
    out = await tools["board_assign_page"].execute(page="dormienza.md", card="acero")
    assert "dormienza.md" in model.card_by_id(read_board(root), "acero")["pages"]
    assert "acero" in state.stale


async def test_a_summary_keeps_to_the_skeleton_and_to_its_pages(tmp_path) -> None:
    ws, root = _notebook(tmp_path)
    _seed_board(root)
    state = _state(root, ws)
    tool = _tools(state)["board_write_summary"]
    out = await tool.execute(card="acero", brief="In vaso 14.", sections=[
        {"title": "Vaso e travaso", "text": "Vaso 20 in dormienza.", "sources": ["travaso-acero.md"]},
        {"title": "Inventata", "text": "x", "sources": ["travaso-acero.md"]},
        {"title": "Acqua", "text": "Due cm asciutti.", "sources": ["monstera.md"]},
    ])
    assert "1 sections" in out
    summary = read_card_summary(root, "acero")
    assert [s["title"] for s in summary["sections"]] == ["Vaso e travaso"]
    assert summary["fingerprint"] == state.fingerprints["acero"]
    # Scritto il cassetto, una seconda scrittura nella stessa passata e' rifiutata.
    again = await tool.execute(card="acero", brief="x", sections=[])
    assert again.startswith("Refused")


async def test_an_unchanged_section_keeps_its_moment(tmp_path) -> None:
    """Il puntino blu dice «questa sezione e' cambiata»: una sezione copiata
    uguale non deve riaccenderlo."""
    ws, root = _notebook(tmp_path)
    _seed_board(root)
    write_card_summary(root, "acero", {"brief": "x", "fingerprint": "old", "sections": [
        {"title": "Acqua", "text": "Due cm asciutti.", "sources": ["irrigazione-acero.md"],
         "changed": "2026-09-01T10:00:00+02:00"},
    ]})
    state = _state(root, ws)
    await _tools(state)["board_write_summary"].execute(card="acero", brief="x", sections=[
        {"title": "Acqua", "text": "Due cm asciutti.", "sources": ["irrigazione-acero.md"]},
        {"title": "Vaso e travaso", "text": "Vaso 20.", "sources": ["travaso-acero.md"]},
    ])
    changed = {s["title"]: s["changed"] for s in read_card_summary(root, "acero")["sections"]}
    assert changed["Acqua"] == "2026-09-01T10:00:00+02:00"
    assert changed["Vaso e travaso"] != "2026-09-01T10:00:00+02:00"
    assert state.summaries["acero"] == 1


async def test_reminders_are_not_added_twice(tmp_path) -> None:
    ws, root = _notebook(tmp_path)
    _seed_board(root)
    tool = _tools(_state(root, ws))["board_add_reminder"]
    await tool.execute(card="acero", text="Travaso nel vaso 20", date="2027-03-01",
                       precision="month", why="travaso-acero.md")
    out = await tool.execute(card="acero", text="travaso nel vaso 20 ", date="2027-03-01")
    assert out.startswith("Already")
    rems = read_reminders(root)["reminders"]
    assert len(rems) == 1 and rems[0]["by"] == "jenny"
    assert rems[0]["when"] == {"kind": "date", "date": "2027-03-01", "precision": "month"}
    bad = await tool.execute(card="acero", text="Potatura", date="a marzo")
    assert bad.startswith("Refused")


async def test_the_gardener_can_give_a_reminder_to_the_whole_notebook(tmp_path) -> None:
    ws, root = _notebook(tmp_path)
    _seed_board(root)
    tool = _tools(_state(root, ws))["board_add_reminder"]
    out = await tool.execute(text="Porta dentro tutte le piante", date="2026-12-01")
    assert "the whole notebook" in out
    [rem] = read_reminders(root)["reminders"]
    assert rem["card"] == "" and rem["by"] == "jenny"
    again = await tool.execute(card="", text="porta dentro tutte le piante", date="2026-12-01")
    assert again.startswith("Already")
    nope = await tool.execute(card="cantina", text="x", date="2026-12-01")
    assert nope.startswith("Refused") and "whole notebook" in nope


def test_a_cron_in_a_drawer_is_listed_as_already_scheduled(tmp_path) -> None:
    """Un piano gia' in agenda come cron non si cerca: si legge nel prompt.

    L'11/10/2026 sull'emulatore la passata trovo' «promemoria per novembre» nel
    diario, le attese dicevano «nessuna» — era un cron nel cassetto — e lo cerco'
    per sei minuti. Nome, messaggio, data e ambito vengono da ``cron/jobs.json``,
    che dal 20/10/2026 è l'unica cosa che dice se un cron esiste."""
    import json

    ws, root = _notebook(tmp_path)
    _seed_board(root)
    (ws / "cron").mkdir()
    (ws / "cron" / "jobs.json").write_text(json.dumps({"jobs": [
        {"id": "ab12cd34", "name": "Travaso", "enabled": True,
         "payload": {"message": "Ricorda il travaso\n dell'acero.",
                     "board": {"notebook": root.name, "card": "acero", "section": "@auto"}},
         "state": {"nextRunAtMs": 1803974400000}},
        {"id": "ef56ab78", "name": "Gelo", "enabled": True,
         "payload": {"message": "avvisa se gela",
                     "board": {"notebook": root.name, "card": "@notebook", "section": "@auto"}}},
        {"id": "aa11bb22", "name": "Altrove", "enabled": True,
         "payload": {"board": {"notebook": "altro", "card": "acero", "section": "@auto"}}},
    ]}), encoding="utf-8")
    bp = BoardPass(root, ws)
    prompt = bp.build_prompt(bp.build_state(), journal="- 18:46 — promemoria per marzo: travaso.")
    block = prompt.split("## Already scheduled", 1)[1].split("\n## ", 1)[0]
    assert "- cron «Travaso» in the drawer of `acero`: Ricorda il travaso dell'acero. (next 2027-" in block
    assert "- cron «Gelo» for the whole notebook: avvisa se gela" in block
    assert "Altrove" not in block
    assert "Reminders already on the list" not in prompt
    # Uno store illeggibile non rompe la passata.
    (ws / "cron" / "jobs.json").write_text("{rotto", encoding="utf-8")
    assert "Travaso" not in BoardPass(root, ws).build_prompt(bp.build_state(), journal="x")


async def test_at_most_two_proposals_and_a_no_does_not_come_back(tmp_path) -> None:
    ws, root = _notebook(tmp_path)
    _seed_board(root)

    def declined(board):
        board["declined"].append({"kind": "add_connection", "subject": "Acero — Monstera",
                                  "pagesAtDecline": 0})

    mutate_board(root, declined)
    state = _state(root, ws)
    suggest = _tools(state)["board_suggest"]
    no = await suggest.execute(kind="add_connection", text="Li lego?", a="acero", b="monstera",
                               label="vasi")
    assert no.startswith("Refused") and "said no" in no
    few = await suggest.execute(kind="add_card", text="Un posto al ginkgo?", subject="Ginkgo",
                                pages=["ginkgo.md"])
    assert few.startswith("Refused")
    ok = await suggest.execute(kind="add_card", text="Un posto al ginkgo?", subject="Ginkgo",
                               pages=["ginkgo.md", "ginkgo-talea.md", "ginkgo-vaso.md"],
                               glyph="plant")
    assert ok.startswith("Proposal")
    ok2 = await suggest.execute(kind="attach_page", text="La dormienza nell'acero?",
                                card="acero", page="dormienza.md")
    assert ok2.startswith("Proposal")
    third = await suggest.execute(kind="attach_page", text="x", card="monstera", page="dormienza.md")
    assert third.startswith("Refused")
    assert [s["kind"] for s in read_board(root)["suggestions"]] == ["add_card", "attach_page"]


async def test_a_full_board_refuses_new_cards_and_only_still_cards_go_away(tmp_path) -> None:
    ws, root = _notebook(tmp_path)

    def full(board):
        board["skeleton"] = ["Storia"]
        board["cards"] = [
            {"id": f"c{i}", "title": f"C{i}", "pos": {"x": 1, "y": 1}, "created": "2020-01-01T00:00:00+00:00"}
            for i in range(model.MAX_ITEMS)
        ]

    mutate_board(root, full)
    state = _state(root, ws)
    suggest = _tools(state)["board_suggest"]
    out = await suggest.execute(kind="add_card", text="x", subject="Ginkgo",
                                pages=["ginkgo.md", "ginkgo-talea.md", "ginkgo-vaso.md"])
    assert "full" in out
    # Le schede vuote nate nel 2020 sono ferme da tanto: si possono proporre.
    assert "c0" in state.remove_candidates
    ok = await suggest.execute(kind="remove_card", text="Metto via C0?", card="c0")
    assert ok.startswith("Proposal")


async def test_a_correction_is_resolved_by_the_code(tmp_path) -> None:
    ws, root = _notebook(tmp_path)
    _seed_board(root)
    create_correction(root, card_id="acero", card_title="Acero", section="Acqua",
                      sources=["irrigazione-acero.md"], old_text="Due cm.", correction="Tre cm.")
    assert BoardPass(root, ws).reason() == "corrections"
    state = _state(root, ws)
    cid = open_corrections(root)[0]["id"]
    out = await _tools(state)["board_resolve_correction"].execute(id=cid, resolution="Fixed.")
    assert "closed" in out
    assert open_corrections(root) == []


# ── La passata intera, con un modello finto ─────────────────────────────────


class _Agent:
    """Un modello finto: chiama gli strumenti che il caso gli dice, in ordine."""

    def __init__(self, script):
        self.script = script
        self.prompts: list[str] = []

    async def process_direct(self, prompt, *, tools, **_):
        self.prompts.append(prompt)
        for name, args in self.script:
            await tools.get(name).execute(**args)
        return SimpleNamespace(metadata={"_stop_reason": "completed"})


class _Store:
    def __init__(self, root: Path, ws: Path):
        self.root = root
        self.workspace = ws
        self.name = root.name

    def build_tools(self, *, write_guard=None):
        from jenny.agent.tools.registry import ToolRegistry

        return ToolRegistry()


async def test_the_first_board_is_proposed_with_its_drawers_written(tmp_path, monkeypatch) -> None:
    ws, root = _notebook(tmp_path)
    agent = _Agent([
        ("board_propose_board", {
            "skeleton": ["Vaso e travaso", "Acqua"],
            "cards": [
                {"title": "Acero", "glyph": "leaf",
                 "pages": ["travaso-acero.md", "irrigazione-acero.md"]},
                {"title": "Ginkgo", "glyph": "non-esiste",
                 "pages": ["ginkgo.md", "ginkgo-talea.md", "ginkgo-vaso.md"]},
            ],
            "text": "Ho preparato la bacheca: va bene così?",
        }),
        ("board_write_summary", {"card": "acero", "brief": "In vaso 14.", "sections": [
            {"title": "Acqua", "text": "Due cm.", "sources": ["irrigazione-acero.md"]},
        ]}),
        ("board_add_reminder", {"card": "acero", "text": "Travaso", "date": "2027-03-01",
                                "precision": "month", "source": "dormienza.md"}),
    ])
    outcome = await run_board_pass(agent, _Store(root, ws), session_key="gardener:piante-x-board")
    assert outcome.status == "done"
    assert "has no board yet" in agent.prompts[0]
    assert "- [ ] Travaso dell'acero" in agent.prompts[0]
    board = read_board(root)
    assert board["cards"] == []
    proposal = board["suggestions"][0]
    assert proposal["kind"] == "initial_board"
    assert [c["id"] for c in proposal["cards"]] == ["acero", "ginkgo"]
    assert proposal["cards"][1]["glyph"] == "", "un glifo fuori elenco non passa"
    assert read_card_summary(root, "acero")["brief"] == "In vaso 14."
    assert board["passes"] == 1 and board["lastPassAt"]
    assert not BoardPass(root, ws).is_migrating()

    # Accettata, la bacheca nasce coi cassetti gia' scritti.
    from jenny.board.edit import accept_suggestion

    def accept(b):
        accept_suggestion(b, b["suggestions"][0], read_reminders(root))
        b["suggestions"] = []

    mutate_board(root, accept)
    board = read_board(root)
    assert [c["id"] for c in board["cards"]] == ["acero", "ginkgo"]
    assert board["skeleton"] == ["Vaso e travaso", "Acqua"]
    acero = model.card_by_id(board, "acero")
    assert [c["id"] for c in BoardPass(root, ws).stale_cards()] == ["ginkgo"], (
        "il cassetto scritto alla proposta vale anche dopo l'accettazione"
    )
    assert acero["pos"]["by"] == "jenny"


async def test_a_pass_never_moves_what_the_user_placed(tmp_path) -> None:
    ws, root = _notebook(tmp_path)
    _seed_board(root)
    before = model.card_by_id(read_board(root), "acero")["pos"]
    agent = _Agent([
        ("board_assign_page", {"page": "dormienza.md", "card": "acero"}),
        ("board_write_summary", {"card": "acero", "brief": "x", "sections": [
            {"title": "Acqua", "text": "Due cm.", "sources": ["irrigazione-acero.md"]}]}),
        ("board_write_summary", {"card": "monstera", "brief": "y", "sections": []}),
    ])
    outcome = await run_board_pass(agent, _Store(root, ws), session_key="k")
    assert outcome.assigned == 1 and outcome.summaries == 2
    board = read_board(root)
    assert model.card_by_id(board, "acero")["pos"] == before
    kinds = [row["kind"] for row in board["since"]]
    assert "pages_assigned" in kinds and "sections_regenerated" in kinds
    assert set(board["orphansSeen"]) == {"ginkgo.md", "ginkgo-talea.md", "ginkgo-vaso.md"}


async def test_a_failing_model_still_stamps_the_pass(tmp_path) -> None:
    ws, root = _notebook(tmp_path)
    _seed_board(root)

    class Broken:
        async def process_direct(self, *a, **k):
            raise RuntimeError("provider down")

    outcome = await run_board_pass(Broken(), _Store(root, ws), session_key="k")
    assert outcome.status == "failed"
    assert read_board(root)["lastPassAt"], "senza timbro ripartirebbe a ogni battito"


def test_glyphs_are_known_icon_names() -> None:
    """Il glifo di una scheda lo sceglie il modello da un elenco chiuso: ogni
    nome dell'elenco deve esistere nel set di icone che la casa porta con se'."""
    css = (Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets" / "vendor"
           / "@tabler" / "icons-webfont@3.19.0" / "dist" / "tabler-icons.min.css").read_text()
    for name in model.GLYPHS:
        assert f".ti-{name}:before" in css, name


async def test_the_first_board_keeps_the_skeleton_the_user_chose(tmp_path) -> None:
    ws, root = _notebook(tmp_path)

    def setup(board):
        board["skeleton"] = ["Semina", "Raccolto"]

    mutate_board(root, setup)
    bp = BoardPass(root, ws)
    assert bp.is_migrating()
    state = bp.build_state()
    prompt = bp.build_prompt(state)
    assert "The skeleton the user chose" in prompt
    out = await _tools(state)["board_propose_board"].execute(
        skeleton=["Altro", "Ancora"],
        cards=[{"title": "Acero", "pages": ["travaso-acero.md"]}],
        text="Ecco la bacheca.",
    )
    assert out.startswith("Board proposed")
    assert read_board(root)["suggestions"][0]["skeleton"] == ["Semina", "Raccolto"]


async def test_the_proposal_hands_back_the_drawers_one_at_a_time(tmp_path) -> None:
    """I cassetti si scrivono da quel che tornano gli strumenti, uno per volta.

    Con le pagine di tutti i cassetti in un colpo solo, su un quaderno vero
    quelle oltre il tetto arrivavano «non mostrate», il modello le leggeva una a
    una e il contesto perdeva le prime mentre leggeva le ultime: 107 letture e
    nessun cassetto sul Titan il 05/10. Qui ogni risposta porta un cassetto solo.
    """
    ws, root = _notebook(tmp_path)
    (root / "wiki" / "ginkgo.md").write_text("# ginkgo\n\n" + "Foglia a ventaglio. " * 400)
    bp = BoardPass(root, ws)
    state = bp.build_state()
    assert "Do not read the pages first" in bp.build_prompt(state)
    tools = _tools(state)
    out = await tools["board_propose_board"].execute(
        skeleton=["Vaso"],
        cards=[{"title": "Acero", "pages": ["travaso-acero.md"]},
               {"title": "Ginkgo", "pages": ["ginkgo.md"]}],
        text="Ecco.",
    )
    assert "### Next drawer: `acero` — Acero (1 more after this one)" in out
    assert PAGES["travaso-acero.md"].strip().splitlines()[0] in out
    assert "Foglia a ventaglio" not in out

    out = await tools["board_write_summary"].execute(
        card="acero", brief="Vaso 20 a marzo.",
        sections=[{"title": "Vaso", "text": "Dal 14 al 20.", "sources": ["travaso-acero.md"]}],
    )
    assert out.startswith("Drawer of `acero` written")
    assert "### Next drawer: `ginkgo` — Ginkgo (the last one)" in out
    assert "Foglia a ventaglio" in out and "_(cut here)_" in out
    assert len(out) < 12000

    out = await tools["board_write_summary"].execute(
        card="ginkgo", brief="Arrivato.",
        sections=[{"title": "Vaso", "text": "Non registrato.", "sources": ["ginkgo.md"]}],
    )
    assert out.endswith("That was the last drawer.")
    assert not state.queue


async def test_a_refused_drawer_stays_next(tmp_path) -> None:
    ws, root = _notebook(tmp_path)
    state = BoardPass(root, ws).build_state()
    tools = _tools(state)
    await tools["board_propose_board"].execute(
        skeleton=["Vaso"],
        cards=[{"title": "Acero", "pages": ["travaso-acero.md"]},
               {"title": "Ginkgo", "pages": ["ginkgo.md"]}],
    )
    out = await tools["board_write_summary"].execute(
        card="acero", sections=[{"title": "Vaso", "text": "x", "sources": ["ginkgo.md"]}],
    )
    assert out.startswith("Refused")
    assert state.queue[0][0] == "acero"


async def test_drawers_to_rewrite_come_one_at_a_time(tmp_path) -> None:
    """Accettata la prima bacheca, tutti i cassetti sono da scrivere: la stessa
    catena vale per la passata che li riscrive, non solo per la proposta."""
    ws, root = _notebook(tmp_path)
    _seed_board(root)
    (root / "wiki" / "monstera.md").write_text("# monstera\n\n" + "In soggiorno, luce filtrata. " * 300)
    bp = BoardPass(root, ws)
    state = bp.build_state()
    assert [q[0] for q in state.queue] == ["acero", "monstera"]
    prompt = bp.build_prompt(state)
    assert "- `acero` — Acero\n- `monstera` — Monstera" in prompt
    assert "### Next drawer: `acero` — Acero (1 more after this one)" in prompt
    assert "luce filtrata" not in prompt

    out = await _tools(state)["board_write_summary"].execute(
        card="acero", brief="Vaso 20.",
        sections=[{"title": "Vaso e travaso", "text": "Dal 14 al 20.",
                   "sources": ["travaso-acero.md"]}],
    )
    assert "### Next drawer: `monstera` — Monstera (the last one)" in out
    assert "luce filtrata" in out
