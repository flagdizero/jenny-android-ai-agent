"""Il registro delle scritture di un aiutante, e il suo annulla.

Si fissa la promessa del modulo (``agent.subagent_journal``): l'annulla toglie
il lavoro di **quell'**aiutante e di nessun altro. I casi sono quelli del piano:
un annulla semplice, chi passa dopo, chi passa in mezzo, ``python_exec`` sul
percorso vero (``run_in_executor``), un annulla interrotto da un kill, un
lineage ripreso dopo il budget, un file troppo grande, le cartelle nuove; poi il
giro intero, con un aiutante finto che scrive coi suoi strumenti.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jenny.agent import subagent_journal
from jenny.agent.subagent import SubagentManager, SubagentUndoError
from jenny.agent.subagent_journal import (
    KEPT_CHANGED_AFTER,
    KEPT_CHANGED_DURING,
    KEPT_NOT_SAVED,
    WriteJournal,
    finish_interrupted_undos,
    journal_dir,
    sweep_orphan_journals,
)
from jenny.agent.tools.python_exec import PythonExecTool
from jenny.agent.tools.python_exec_builtins import _register_builtin_functions
from jenny.board.store import mutate_board
from jenny.bus.queue import MessageBus
from jenny.config.tool_schemas import PythonExecConfig
from jenny.providers.base import LLMResponse, ToolCallRequest
from jenny.security.subagent_policy import (
    SubagentRunPolicy,
    SubagentSecretError,
    check_read,
    check_write,
    enter_subagent_policy,
    settle_writes,
)
from jenny.snapshot.engine import SnapshotEngine
from tests.support.aio import wait_until
from tests.support.subagent_provider_fakes import fake_provider


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    wiki = root / "wikis" / "piante" / "wiki"
    wiki.mkdir(parents=True)
    for name in ("acero.md", "felce.md", "ficus.md"):
        (wiki / name).write_text(f"# {name}\n\nstato iniziale\n", encoding="utf-8")
    return root


def _helper(ws: Path, lineage: str = "lin1", task: str = "t1", attempt: int = 1):
    """Una politica di lavoro col suo registro, come la lega ``_run_subagent``."""
    policy = SubagentRunPolicy.for_run(task, ws)
    policy.journal = WriteJournal(ws, lineage)
    policy.journal.begin(task, attempt)
    return policy


def _write(path: Path, text: str) -> None:
    """Una scrittura di un aiutante: il recinto, la scrittura, il «dopo»."""
    check_write(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    settle_writes("write_file")


def _delete(path: Path) -> None:
    check_write(path)
    path.unlink()
    settle_writes("python_exec")


def _tree(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): p.read_text(encoding="utf-8")
        for p in sorted(root.rglob("*")) if p.is_file() and "subagents" not in p.parts
    }


# -- il registro -------------------------------------------------------------


def test_the_journal_notes_before_and_after_only_for_real_changes(ws: Path) -> None:
    page = ws / "wikis/piante/wiki/acero.md"
    with enter_subagent_policy(_helper(ws)):
        _write(page, "nuovo")
        check_write(ws / "wikis/piante/wiki/felce.md")  # toccato e non scritto
        settle_writes("apply_patch")
    lines = (journal_dir(ws, "lin1") / "entries.jsonl").read_text().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["path"] == "wikis/piante/wiki/acero.md"
    assert entry["before"] != entry["after"] and entry["saved"] is True
    assert entry["tool"] == "write_file" and entry["task_id"] == "t1"


def test_the_main_agent_has_no_journal(ws: Path) -> None:
    page = ws / "wikis/piante/wiki/acero.md"
    check_write(page)
    page.write_text("Jenny scrive", encoding="utf-8")
    settle_writes()
    assert not (ws / "subagents" / "journal").exists()


# -- l'annulla ---------------------------------------------------------------


def test_undo_puts_back_three_written_pages_and_one_deleted(ws: Path) -> None:
    before = _tree(ws)
    wiki = ws / "wikis/piante/wiki"
    policy = _helper(ws)
    with enter_subagent_policy(policy):
        _write(wiki / "acero.md", "riscritta")
        _write(wiki / "nuove" / "quercia.md", "nuova pagina")
        _write(wiki / "felce.md", "riscritta anche questa")
        _delete(wiki / "ficus.md")
    report = policy.journal.undo(by="user")
    assert _tree(ws) == before
    assert not (wiki / "nuove").exists(), "la cartella nata col suo lavoro va via vuota"
    assert sorted(report.restored) == [
        "wikis/piante/wiki/acero.md", "wikis/piante/wiki/felce.md",
        "wikis/piante/wiki/ficus.md", "wikis/piante/wiki/nuove/quercia.md",
    ]
    assert report.kept == []


def test_a_page_changed_after_the_helper_is_left_alone(ws: Path) -> None:
    wiki = ws / "wikis/piante/wiki"
    policy = _helper(ws)
    with enter_subagent_policy(policy):
        _write(wiki / "acero.md", "dell'aiutante")
        _write(wiki / "felce.md", "dell'aiutante")
    (wiki / "acero.md").write_text("dell'utente, dopo", encoding="utf-8")
    report = policy.journal.undo()
    assert (wiki / "acero.md").read_text() == "dell'utente, dopo"
    assert (wiki / "felce.md").read_text().endswith("stato iniziale\n")
    assert report.kept == [{"path": "wikis/piante/wiki/acero.md", "reason": KEPT_CHANGED_AFTER}]


def test_two_helpers_on_the_same_page_never_undo_each_other(ws: Path) -> None:
    page = ws / "wikis/piante/wiki/acero.md"
    a, b = _helper(ws, "linA", "tA"), _helper(ws, "linB", "tB")
    with enter_subagent_policy(a):
        _write(page, "A1")
    with enter_subagent_policy(b):
        _write(page, "B1")
    with enter_subagent_policy(a):
        _write(page, "A2")
    report_a = a.journal.undo()
    assert page.read_text() == "A2", "B è passato in mezzo: il lavoro di A resta"
    assert report_a.kept[0]["reason"] == KEPT_CHANGED_DURING
    report_b = b.journal.undo()
    assert page.read_text() == "A2", "A è passato dopo B: niente da rimettere per B"
    assert report_b.kept[0]["reason"] == KEPT_CHANGED_AFTER


def test_the_last_helper_on_a_page_can_be_undone_alone(ws: Path) -> None:
    page = ws / "wikis/piante/wiki/acero.md"
    a, b = _helper(ws, "linA", "tA"), _helper(ws, "linB", "tB")
    with enter_subagent_policy(a):
        _write(page, "di A")
    with enter_subagent_policy(b):
        _write(page, "di B")
    b.journal.undo()
    assert page.read_text() == "di A", "si toglie B, e A resta com'era"


def test_simultaneous_writes_are_shared_and_left_alone(ws: Path) -> None:
    page = ws / "wikis/piante/wiki/acero.md"
    a, b = _helper(ws, "linA", "tA"), _helper(ws, "linB", "tB")
    with enter_subagent_policy(a):
        check_write(page)
    with enter_subagent_policy(b):
        check_write(page)
    page.write_text("di chi?", encoding="utf-8")
    a.journal.settle()
    b.journal.settle()
    assert a.journal.undo().kept[0]["reason"] == KEPT_CHANGED_DURING
    assert b.journal.undo().kept[0]["reason"] == KEPT_CHANGED_DURING
    assert page.read_text() == "di chi?"


def test_a_resumed_lineage_is_undone_as_a_whole(ws: Path) -> None:
    """Fermato al budget e fatto continuare: lo stesso lavoro, un annulla solo."""
    wiki = ws / "wikis/piante/wiki"
    before = _tree(ws)
    journal = WriteJournal(ws, "lin1")
    first = SubagentRunPolicy.for_run("t1", ws)
    first.journal = journal
    journal.begin("t1", 1)
    with enter_subagent_policy(first):
        _write(wiki / "acero.md", "primo tentativo")
    # Un processo nuovo: il registro si rilegge dal disco.
    again = SubagentRunPolicy.for_run("t2", ws)
    again.journal = WriteJournal(ws, "lin1")
    again.journal.begin("t2", 1)
    with enter_subagent_policy(again):
        _write(wiki / "acero.md", "ripresa")
        _write(wiki / "felce.md", "ripresa")
    assert again.journal.changed_files == 2
    again.journal.undo()
    assert _tree(ws) == before


def test_a_file_too_large_to_copy_is_not_undoable(ws: Path, monkeypatch) -> None:
    monkeypatch.setattr(subagent_journal, "MAX_BLOB_BYTES", 10)
    page = ws / "wikis/piante/wiki/acero.md"
    policy = _helper(ws)
    with enter_subagent_policy(policy):
        _write(page, "x" * 50)
    report = policy.journal.undo()
    assert page.read_text() == "x" * 50
    assert report.kept == [{"path": "wikis/piante/wiki/acero.md", "reason": KEPT_NOT_SAVED}]


def test_an_undo_killed_halfway_is_finished_at_boot(ws: Path, monkeypatch) -> None:
    wiki = ws / "wikis/piante/wiki"
    before = _tree(ws)
    policy = _helper(ws)
    with enter_subagent_policy(policy):
        _write(wiki / "acero.md", "uno")
        _write(wiki / "felce.md", "due")
    calls = {"n": 0}
    real = WriteJournal._undo_file

    def _dies_on_the_second(self, key, chain, report):
        calls["n"] += 1
        if calls["n"] == 2:
            raise SystemExit("kill")
        return real(self, key, chain, report)

    monkeypatch.setattr(WriteJournal, "_undo_file", _dies_on_the_second)
    with pytest.raises(SystemExit):
        policy.journal.undo()
    monkeypatch.setattr(WriteJournal, "_undo_file", real)
    state = json.loads((journal_dir(ws, "lin1") / "undo.json").read_text())
    assert state["state"] == "running"
    assert finish_interrupted_undos(ws) == 1
    assert _tree(ws) == before
    assert json.loads((journal_dir(ws, "lin1") / "undo.json").read_text())["state"] == "done"


def test_a_new_folder_copied_in_one_go_is_undone(ws: Path) -> None:
    """Una cartella nata intera in uno strumento (una bozza d'app, un
    ``copytree``): i suoi file non passano uno per uno da un recinto."""
    target = ws / "apps" / ".drafts" / "meteo"
    policy = _helper(ws)
    with enter_subagent_policy(policy):
        check_write(target)
        (target / "app").mkdir(parents=True)
        (target / "app.json").write_text("{}", encoding="utf-8")
        (target / "app" / "index.html").write_text("<p>", encoding="utf-8")
        settle_writes("app")
    assert policy.journal.changed_files == 2
    policy.journal.undo()
    assert not (ws / "apps").exists()


def test_files_already_in_a_partly_noted_folder_are_never_taken_for_new(
    ws: Path, monkeypatch,
) -> None:
    """Oltre ``MAX_DIR_FILES`` la cartella non si annota per intero: un suo file
    non annotato potrebbe esserci stato già, e l'annulla non deve cancellarlo."""
    monkeypatch.setattr(subagent_journal, "MAX_DIR_FILES", 1)
    wiki = ws / "wikis/piante/wiki"
    policy = _helper(ws)
    with enter_subagent_policy(policy):
        check_write(wiki, tree=True)
        (wiki / "nuova.md").write_text("nuova", encoding="utf-8")
        settle_writes("python_exec")
    policy.journal.undo()
    assert sorted(p.name for p in wiki.iterdir()) == [
        "acero.md", "felce.md", "ficus.md", "nuova.md",
    ]


def _board(ws: Path) -> dict:
    return json.loads((ws / "wikis/piante/board/board.json").read_text(encoding="utf-8"))


@pytest.fixture
def board_ws(ws: Path) -> Path:
    def seed(b):
        b["cards"].extend([
            {"id": "c1", "title": "Acero", "pages": ["acero.md"]},
            {"id": "c2", "title": "Felce", "pages": ["felce.md"]},
        ])

    mutate_board(ws / "wikis" / "piante", seed)
    return ws


def _helper_edits_board(ws: Path) -> SubagentRunPolicy:
    """L'editore toglie una pagina da una scheda e ne aggiunge una nuova."""
    root = ws / "wikis" / "piante"
    policy = _helper(ws)
    with enter_subagent_policy(policy):
        def edit(b):
            b["cards"][0]["pages"] = []
            b["cards"].append({"id": "c3", "title": "Ficus", "pages": ["ficus.md"]})

        mutate_board(root, edit)
        settle_writes("board")
    return policy


def test_the_board_written_by_a_helper_is_undone(board_ws: Path) -> None:
    before = _board(board_ws)
    policy = _helper_edits_board(board_ws)
    assert policy.journal.changed_files == 1, "il .bak è la copia di rotazione, non un lavoro"
    report = policy.journal.undo()
    assert report.restored == ["wikis/piante/board/board.json"]
    assert _board(board_ws) == before


def test_the_board_is_undone_by_pieces_when_the_home_stamped_it_after(board_ws: Path) -> None:
    """La casa che ridisegna la bacheca ci timbra ``lastSeen`` (prova su jenny_s22
    del 26/10/2026): a file intero risultava «cambiata da altri» quasi sempre."""
    root = board_ws / "wikis" / "piante"
    before = _board(board_ws)
    policy = _helper_edits_board(board_ws)
    mutate_board(root, lambda b: b.update(lastSeen="2026-10-26T11:17:40+01:00"))
    mutate_board(root, lambda b: b["cards"][1].update(title="Felce di Boston"))
    report = policy.journal.undo()
    assert report.restored == ["wikis/piante/board/board.json"] and report.kept == []
    after = _board(board_ws)
    assert after["lastSeen"] == "2026-10-26T11:17:40+01:00", "il timbro della casa resta"
    assert after["cards"][1]["title"] == "Felce di Boston", "la scheda toccata da te resta"
    assert [c["id"] for c in after["cards"]] == ["c1", "c2"]
    assert after["cards"][0]["pages"] == before["cards"][0]["pages"]


def test_a_card_changed_by_both_leaves_the_board_as_it_is(board_ws: Path) -> None:
    root = board_ws / "wikis" / "piante"
    policy = _helper_edits_board(board_ws)
    # La scheda che l'aiutante ha creato, poi rinominata da te: toglierla
    # butterebbe il tuo lavoro.
    mutate_board(root, lambda b: b["cards"][2].update(title="Ficus benjamin"))
    now = _board(board_ws)
    report = policy.journal.undo()
    assert report.kept == [{"path": "wikis/piante/board/board.json",
                            "reason": KEPT_CHANGED_AFTER}]
    assert _board(board_ws) == now


def test_a_card_the_helper_removed_comes_back_in_its_place() -> None:
    from jenny.agent.subagent_journal import _revert

    before = {"cards": [{"id": "a"}, {"id": "b"}, {"id": "c"}], "orphansSeen": ["x.md"]}
    after = {"cards": [{"id": "a"}, {"id": "c"}], "orphansSeen": ["x.md", "y.md"]}
    now = {"cards": [{"id": "a"}, {"id": "c"}, {"id": "d"}], "orphansSeen": ["x.md", "y.md", "z.md"],
           "lastSeen": "t"}
    assert _revert(before, after, now) == {
        "cards": [{"id": "a"}, {"id": "b"}, {"id": "c"}, {"id": "d"}],
        "orphansSeen": ["x.md", "z.md"], "lastSeen": "t",
    }


def test_touching_an_existing_folder_does_not_hand_its_files_to_the_helper(ws: Path) -> None:
    """Revisione del 26/10/2026: ``makedirs(exist_ok=True)`` passa da
    ``check_write``, e annotava tutto l'albero; quel che altri scrivevano
    durante lo strumento diventava dell'aiutante, e l'annulla lo toglieva."""
    wiki = ws / "wikis/piante/wiki"
    policy = _helper(ws)
    with enter_subagent_policy(policy):
        check_write(wiki)  # os.makedirs(wiki, exist_ok=True)
        (wiki / "acero.md").write_text("dell'utente, durante", encoding="utf-8")
        (wiki / "utente.md").write_text("nuova dell'utente", encoding="utf-8")
        settle_writes("python_exec")
    policy.journal.undo()
    assert (wiki / "acero.md").read_text() == "dell'utente, durante"
    assert (wiki / "utente.md").exists()


def test_a_moved_file_that_could_not_be_saved_is_never_deleted(ws: Path, monkeypatch) -> None:
    """Una rinomina di un file troppo grande per il registro: la destinazione è
    l'unica copia, e l'annulla non la cancella."""
    monkeypatch.setattr(subagent_journal, "MAX_BLOB_BYTES", 10)
    raw = ws / "wikis/piante/raw"
    raw.mkdir(parents=True)
    (raw / "foto.jpg").write_bytes(b"x" * 100)
    policy = _helper(ws)
    with enter_subagent_policy(policy):
        check_write(raw / "foto.jpg", tree=True)
        check_write(raw / "rinominata.jpg", tree=True)
        (raw / "foto.jpg").rename(raw / "rinominata.jpg")
        settle_writes("python_exec")
    report = policy.journal.undo()
    assert (raw / "rinominata.jpg").read_bytes() == b"x" * 100
    assert {k["path"] for k in report.kept} == {
        "wikis/piante/raw/foto.jpg", "wikis/piante/raw/rinominata.jpg"}


def test_a_second_undo_keeps_the_first_report(ws: Path) -> None:
    policy = _helper(ws)
    with enter_subagent_policy(policy):
        _write(ws / "wikis/piante/wiki/acero.md", "x")
    first = policy.journal.undo(by="jenny")
    again = policy.journal.undo(by="user")
    assert again.to_dict() == first.to_dict()


def test_a_board_merge_finished_before_a_kill_is_not_redone(board_ws: Path, monkeypatch) -> None:
    root = board_ws / "wikis" / "piante"
    before = _board(board_ws)
    policy = _helper_edits_board(board_ws)
    with enter_subagent_policy(policy):
        _write(board_ws / "wikis/piante/wiki/acero.md", "x")
    mutate_board(root, lambda b: b.update(lastSeen="t"))
    real = WriteJournal._undo_file

    def _dies_after_the_board(self, key, chain, report):
        real(self, key, chain, report)
        if key.endswith("board.json"):
            raise SystemExit("kill")

    monkeypatch.setattr(WriteJournal, "_undo_file", _dies_after_the_board)
    with pytest.raises(SystemExit):
        policy.journal.undo()
    monkeypatch.setattr(WriteJournal, "_undo_file", real)
    assert finish_interrupted_undos(board_ws) == 1
    state = json.loads((journal_dir(board_ws, "lin1") / "undo.json").read_text())
    assert state["kept"] == [] and "wikis/piante/board/board.json" in state["restored"]
    assert {k: v for k, v in _board(board_ws).items() if k != "lastSeen"} == {
        k: v for k, v in before.items() if k != "lastSeen"}


async def test_undo_waits_for_a_python_exec_thread_still_running(ws: Path) -> None:
    provider = fake_provider([LLMResponse(content="Fatto.", finish_reason="stop")])
    manager = _manager(ws, provider)
    manager.UNDO_WAIT_FOR_THREADS_S = 0.2
    await manager.spawn("niente", session_key="unified:default",
                        origin_channel="websocket", origin_chat_id="default")
    await wait_until(lambda: not manager._running_tasks, timeout=5.0)
    record = manager.list_records("unified:default")[-1]
    journal = manager._journal_for(record.lineage_id)
    journal.enter_thread()
    with pytest.raises(SubagentUndoError):
        await manager.undo(record.task_id)
    journal.exit_thread()
    assert (await manager.undo(record.task_id)).restored == []


# -- python_exec, sul percorso vero ------------------------------------------


async def test_python_exec_writes_are_journaled_through_the_executor(ws: Path) -> None:
    """``python_exec`` gira in ``run_in_executor``: il registro arriva lì dentro
    perché viaggia sulla politica, che il contesto copiato porta con sé."""
    cfg = PythonExecConfig()
    tool = PythonExecTool(
        working_dir=str(ws), timeout=30, allowed_modules=cfg.allowed_modules,
        blocked_modules=cfg.blocked_modules, restrict_to_workspace=True, workspace=str(ws),
    )
    _register_builtin_functions(tool.namespace, workspace=str(ws), restrict_to_workspace=True)
    before = _tree(ws)
    policy = _helper(ws)
    code = (
        "import os, shutil\n"
        "open('wikis/piante/wiki/acero.md', 'w').write('da python')\n"
        "os.remove('wikis/piante/wiki/ficus.md')\n"
        "os.makedirs('out/report')\n"
        "with open('out/report/r.md', 'w') as fh: fh.write('ciao')\n"
        "write_file('wikis/piante/wiki/felce.md', 'dal builtin')\n"
        "print('ok')\n"
    )
    with enter_subagent_policy(policy):
        out = await tool.execute(code=code)
        settle_writes("python_exec")
    assert "ok" in out
    assert policy.journal.changed_files == 4
    report = policy.journal.undo()
    assert report.kept == []
    assert _tree(ws) == before
    assert not (ws / "out").exists()


# -- confini -----------------------------------------------------------------


def test_the_journal_is_secret_for_helpers(ws: Path) -> None:
    policy = _helper(ws)
    with enter_subagent_policy(policy):
        _write(ws / "wikis/piante/wiki/acero.md", "x")
        blob = next((journal_dir(ws, "lin1") / "blobs").iterdir())
        with pytest.raises(SubagentSecretError):
            check_read(blob)
        with pytest.raises(SubagentSecretError):
            check_write(journal_dir(ws, "lin1") / "entries.jsonl")


def test_the_journal_stays_out_of_snapshots_and_backups(ws: Path, tmp_path: Path) -> None:
    policy = _helper(ws)
    with enter_subagent_policy(policy):
        _write(ws / "wikis/piante/wiki/acero.md", "x")
    # Anche con una lista di esclusioni salvata prima che il registro esistesse.
    engine = SnapshotEngine(ws, tmp_path / "snaps", exclude_globs=("logs/**",))
    tracked = [rel for rel, _ in engine.iter_tracked_files()]
    assert tracked and not any(rel.startswith("subagents/journal") for rel in tracked)


def test_orphan_journals_are_swept(ws: Path) -> None:
    for lineage in ("vivo", "orfano"):
        policy = _helper(ws, lineage, lineage)
        with enter_subagent_policy(policy):
            _write(ws / "wikis/piante/wiki" / f"{lineage}.md", "x")
    assert sweep_orphan_journals(ws, keep={"vivo"}) == 1
    assert journal_dir(ws, "vivo").is_dir() and not journal_dir(ws, "orfano").exists()


# -- il giro intero, con il manager ------------------------------------------


def _writer_provider(paths: list[str]):
    """Un aiutante che scrive *paths* con ``write_file``, poi risponde."""
    calls = [
        LLMResponse(content="", finish_reason="tool_calls", tool_calls=[
            ToolCallRequest(id=f"c{i}", name="write_file",
                            arguments={"path": p, "content": f"scritto da lui {i}"}),
        ])
        for i, p in enumerate(paths)
    ]
    return fake_provider([*calls, LLMResponse(content="Fatto.", finish_reason="stop")])


def _manager(ws: Path, provider) -> SubagentManager:
    return SubagentManager(provider=provider, workspace=ws, bus=MessageBus(), model="m",
                           max_tool_result_chars=16000, max_iterations=10)


async def test_a_finished_helper_is_undone_from_its_task_id(ws: Path) -> None:
    before = _tree(ws)
    provider = _writer_provider(["wikis/piante/wiki/acero.md", "wikis/piante/wiki/nuova.md"])
    manager = _manager(ws, provider)
    await manager.spawn("scrivi due pagine", session_key="unified:default",
                        origin_channel="websocket", origin_chat_id="default")
    await wait_until(lambda: not manager._running_tasks, timeout=5.0)
    task_id = manager.list_records("unified:default")[-1].task_id
    recent = manager.status_snapshot("unified:default")["recent"][0]
    assert recent["changes"] == 2 and recent["undo"] is None

    report = await manager.undo(task_id, by="jenny")
    assert sorted(report.restored) == ["wikis/piante/wiki/acero.md", "wikis/piante/wiki/nuova.md"]
    assert _tree(ws) == before
    recent = manager.status_snapshot("unified:default")["recent"][0]
    assert recent["changes"] == 0
    assert recent["undo"]["by"] == "jenny" and recent["undo"]["restored"] == 2
    # Un processo nuovo legge lo stesso dal disco.
    fresh = _manager(ws, provider)
    assert fresh.status_snapshot("unified:default")["recent"][0]["undo"]["restored"] == 2


async def test_undo_refuses_a_helper_that_is_still_working(ws: Path) -> None:
    import asyncio

    gate = asyncio.Event()

    async def _slow(**_kw):
        await gate.wait()
        return LLMResponse(content="Fatto.", finish_reason="stop")

    manager = _manager(ws, fake_provider(_slow))
    await manager.spawn("aspetta", session_key="unified:default",
                        origin_channel="websocket", origin_chat_id="default")
    task_id = manager.status_snapshot("unified:default")["running"][0]["task_id"]
    with pytest.raises(SubagentUndoError):
        await manager.undo(task_id)
    report = await manager.cancel_and_undo(task_id, by="user")
    assert report.restored == []
    record = manager.list_records("unified:default")[-1]
    assert record.state == "cancelled" and record.stopped_by == "user"
    gate.set()


async def test_jenny_can_stop_and_undo_from_her_tool(ws: Path) -> None:
    from jenny.agent.tools.context import RequestContext
    from jenny.agent.tools.subagent_control import SubagentCancelTool

    before = _tree(ws)
    manager = _manager(ws, _writer_provider(["wikis/piante/wiki/felce.md"]))
    await manager.spawn("riscrivi felce", session_key="unified:default",
                        origin_channel="websocket", origin_chat_id="default")
    await wait_until(lambda: not manager._running_tasks, timeout=5.0)
    task_id = manager.list_records("unified:default")[-1].task_id
    tool = SubagentCancelTool(manager)
    tool.set_context(RequestContext(channel="websocket", chat_id="default",
                                    session_key="unified:default", turn_id="turn1"))
    out = await tool.execute(task_id=task_id, undo=True)
    assert "Put back 1 file(s)" in out
    assert _tree(ws) == before
    status = manager.status_snapshot("unified:default")["recent"][0]
    assert status["undo"]["by"] == "jenny"
