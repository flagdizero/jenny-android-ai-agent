"""Il registro delle scritture di un aiutante, e il suo annulla.

Si fissa la promessa del modulo (``agent.write_journal``): l'annulla toglie
il lavoro di **quell'**aiutante e di nessun altro. I casi sono quelli del piano:
un annulla semplice, chi passa dopo, chi passa in mezzo, ``python_exec`` sul
percorso vero (``run_in_executor``), un annulla interrotto da un kill, un
lineage ripreso dopo il budget, un file troppo grande, le cartelle nuove; poi il
giro intero, con un aiutante finto che scrive coi suoi strumenti.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest

from jenny import db as jdb
from jenny.agent import write_journal
from jenny.agent.subagent import SubagentManager, SubagentUndoError
from jenny.agent.tools.python_exec import PythonExecTool
from jenny.agent.tools.python_exec_builtins import _register_builtin_functions
from jenny.agent.write_journal import (
    HELPER,
    KEPT_CHANGED_AFTER,
    KEPT_CHANGED_DURING,
    KEPT_NOT_SAVED,
    UndoConflictError,
    WriteJournal,
    bind_journal,
    finish_interrupted_undos,
    journal_key,
    objects_dir_for,
    read_undo_state,
    sweep_orphan_journals,
    undo_set,
)
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


@pytest.fixture(autouse=True)
def _close_databases():
    yield
    jdb.close_all()


def _key(lineage: str = "lin1") -> str:
    return journal_key(HELPER, lineage)


def _entries(ws: Path, lineage: str = "lin1") -> list[dict]:
    return WriteJournal(ws, _key(lineage))._read_entries()


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    wiki = root / "wikis" / "piante" / "wiki"
    wiki.mkdir(parents=True)
    for name in ("acero.md", "felce.md", "ficus.md"):
        (wiki / name).write_text(f"# {name}\n\nstato iniziale\n", encoding="utf-8")
    return root


@dataclass
class _Helper:
    """Un aiutante finto: la sua politica e il suo registro."""

    policy: SubagentRunPolicy
    journal: WriteJournal


def _helper(ws: Path, lineage: str = "lin1", task: str = "t1", attempt: int = 1) -> _Helper:
    journal = WriteJournal(ws, _key(lineage))
    journal.begin(task, attempt)
    return _Helper(SubagentRunPolicy.for_run(task, ws), journal)


@contextmanager
def _at_work(helper: _Helper):
    """Come lega ``_run_subagent``: la politica e il registro, insieme."""
    with enter_subagent_policy(helper.policy), bind_journal(helper.journal):
        yield


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
    with _at_work(_helper(ws)):
        _write(page, "nuovo")
        check_write(ws / "wikis/piante/wiki/felce.md")  # toccato e non scritto
        settle_writes("apply_patch")
    lines = _entries(ws)
    assert len(lines) == 1
    entry = lines[0]
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
    with _at_work(policy):
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
    with _at_work(policy):
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
    with _at_work(a):
        _write(page, "A1")
    with _at_work(b):
        _write(page, "B1")
    with _at_work(a):
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
    with _at_work(a):
        _write(page, "di A")
    with _at_work(b):
        _write(page, "di B")
    b.journal.undo()
    assert page.read_text() == "di A", "si toglie B, e A resta com'era"


def test_simultaneous_writes_are_shared_and_left_alone(ws: Path) -> None:
    page = ws / "wikis/piante/wiki/acero.md"
    a, b = _helper(ws, "linA", "tA"), _helper(ws, "linB", "tB")
    with _at_work(a):
        check_write(page)
    with _at_work(b):
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
    first = _helper(ws, task="t1")
    with _at_work(first):
        _write(wiki / "acero.md", "primo tentativo")
    # Un processo nuovo: il registro si rilegge dal database.
    jdb.close_all()
    again = _helper(ws, task="t2")
    with _at_work(again):
        _write(wiki / "acero.md", "ripresa")
        _write(wiki / "felce.md", "ripresa")
    assert again.journal.changed_files == 2
    again.journal.undo()
    assert _tree(ws) == before


def test_a_file_too_large_to_copy_is_not_undoable(ws: Path, monkeypatch) -> None:
    monkeypatch.setattr(write_journal, "MAX_BLOB_BYTES", 10)
    page = ws / "wikis/piante/wiki/acero.md"
    policy = _helper(ws)
    with _at_work(policy):
        _write(page, "x" * 50)
    report = policy.journal.undo()
    assert page.read_text() == "x" * 50
    assert report.kept == [{"path": "wikis/piante/wiki/acero.md", "reason": KEPT_NOT_SAVED}]


def test_an_undo_killed_halfway_is_finished_at_boot(ws: Path, monkeypatch) -> None:
    wiki = ws / "wikis/piante/wiki"
    before = _tree(ws)
    policy = _helper(ws)
    with _at_work(policy):
        _write(wiki / "acero.md", "uno")
        _write(wiki / "felce.md", "due")
    calls = {"n": 0}
    real = write_journal._UndoRun._undo_file

    def _dies_on_the_second(self, key, chain, report, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise SystemExit("kill")
        return real(self, key, chain, report, **kw)

    monkeypatch.setattr(write_journal._UndoRun, "_undo_file", _dies_on_the_second)
    with pytest.raises(SystemExit):
        policy.journal.undo()
    monkeypatch.setattr(write_journal._UndoRun, "_undo_file", real)
    state = read_undo_state(ws, _key())
    assert state is not None and state["state"] == "running"
    jdb.close_all()  # un processo nuovo
    assert finish_interrupted_undos(ws) == 1
    assert _tree(ws) == before
    assert read_undo_state(ws, _key())["state"] == "done"


def test_a_new_folder_copied_in_one_go_is_undone(ws: Path) -> None:
    """Una cartella nata intera in uno strumento (una bozza d'app, un
    ``copytree``): i suoi file non passano uno per uno da un recinto."""
    target = ws / "apps" / ".drafts" / "meteo"
    policy = _helper(ws)
    with _at_work(policy):
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
    monkeypatch.setattr(write_journal, "MAX_DIR_FILES", 1)
    wiki = ws / "wikis/piante/wiki"
    policy = _helper(ws)
    with _at_work(policy):
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


def _helper_edits_board(ws: Path) -> _Helper:
    """L'editore toglie una pagina da una scheda e ne aggiunge una nuova."""
    root = ws / "wikis" / "piante"
    policy = _helper(ws)
    with _at_work(policy):
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
    from jenny.agent.write_journal import _revert

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
    with _at_work(policy):
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
    monkeypatch.setattr(write_journal, "MAX_BLOB_BYTES", 10)
    raw = ws / "wikis/piante/raw"
    raw.mkdir(parents=True)
    (raw / "foto.jpg").write_bytes(b"x" * 100)
    policy = _helper(ws)
    with _at_work(policy):
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
    with _at_work(policy):
        _write(ws / "wikis/piante/wiki/acero.md", "x")
    first = policy.journal.undo(by="jenny")
    again = policy.journal.undo(by="user")
    assert again.to_dict() == first.to_dict()


def test_a_board_merge_finished_before_a_kill_is_not_redone(board_ws: Path, monkeypatch) -> None:
    root = board_ws / "wikis" / "piante"
    before = _board(board_ws)
    policy = _helper_edits_board(board_ws)
    with _at_work(policy):
        _write(board_ws / "wikis/piante/wiki/acero.md", "x")
    mutate_board(root, lambda b: b.update(lastSeen="t"))
    real = write_journal._UndoRun._undo_file

    def _dies_after_the_board(self, key, chain, report, **kw):
        real(self, key, chain, report, **kw)
        if key.endswith("board.json"):
            raise SystemExit("kill")

    monkeypatch.setattr(write_journal._UndoRun, "_undo_file", _dies_after_the_board)
    with pytest.raises(SystemExit):
        policy.journal.undo()
    monkeypatch.setattr(write_journal._UndoRun, "_undo_file", real)
    assert finish_interrupted_undos(board_ws) == 1
    state = read_undo_state(board_ws, _key())
    assert state is not None and state["kept"] == [] and "wikis/piante/board/board.json" in state["restored"]
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
    with _at_work(policy):
        out = await tool.execute(code=code)
        settle_writes("python_exec")
    assert "ok" in out
    assert policy.journal.changed_files == 4
    report = policy.journal.undo()
    assert report.kept == []
    assert _tree(ws) == before
    assert not (ws / "out").exists()


# -- confini -----------------------------------------------------------------


def test_the_journal_lives_outside_the_workspace(ws: Path, tmp_path: Path) -> None:
    """Fuori dal workspace: un aiutante non ci arriva coi suoi strumenti, e né le
    foto né il backup lo portano."""
    policy = _helper(ws)
    with _at_work(policy):
        _write(ws / "wikis/piante/wiki/acero.md", "x")
    blob = next(p for p in objects_dir_for(ws).rglob("*") if p.is_file())
    assert ws.resolve() not in blob.resolve().parents
    assert (ws.parent / "db" / "undo.sqlite").is_file()
    engine = SnapshotEngine(ws, tmp_path / "snaps", exclude_globs=("logs/**",))
    tracked = [rel for rel, _ in engine.iter_tracked_files()]
    assert tracked and not any("undo" in rel for rel in tracked)


def test_the_old_journal_folder_stays_secret_until_it_is_removed(ws: Path) -> None:
    old = ws / "subagents" / "journal" / "lin1" / "entries.jsonl"
    old.parent.mkdir(parents=True)
    old.write_text("{}", encoding="utf-8")
    with _at_work(_helper(ws)), pytest.raises(SubagentSecretError):
        check_read(old)


def test_orphan_journals_are_swept(ws: Path) -> None:
    for lineage, page in (("vivo", "acero.md"), ("orfano", "felce.md")):
        policy = _helper(ws, lineage, lineage)
        with _at_work(policy):
            _write(ws / "wikis/piante/wiki" / page, "x")
    saved = lambda: [p for p in objects_dir_for(ws).rglob("*") if p.is_file()]  # noqa: E731
    assert len(saved()) == 2
    assert sweep_orphan_journals(ws, keep={"vivo"}) == 1
    assert _entries(ws, "vivo") and not _entries(ws, "orfano")
    # Il contenuto salvato dall'orfano se ne va con lui; quello del vivo resta.
    assert len(saved()) == 1


def test_a_pending_drafts_journal_is_not_swept(ws: Path) -> None:
    """Il record dell'aiutante scade in 7 giorni, la bozza no: finché aspetta,
    il registro di chi l'ha scritta resta, o il suo annulla non la toglierebbe."""
    from jenny.agent import staged_writes

    policy = _helper(ws, "scrittore", "t9")
    with _at_work(policy):
        _write(ws / "wikis/piante/wiki/acero.md", "x")
        staged_writes.stage(ws, "USER.md", b"y", sources=("web_search",), task_id="t9")
    keys = staged_writes.pending_journal_keys(ws)
    assert keys == {journal_key(HELPER, "scrittore")}
    assert sweep_orphan_journals(ws, keep=set(), keep_keys=keys) == 0
    assert _entries(ws, "scrittore")


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


# -- il registro legato, fuori da un aiutante --------------------------------


def test_a_bound_journal_notes_writes_outside_a_helper(ws: Path) -> None:
    """Il turno di Jenny in un piano non ha politica d'aiutante, ma un registro
    sì: le sue scritture passano dagli stessi recinti e si annotano lo stesso,
    col passo del piano."""
    page = ws / "wikis/piante/wiki/acero.md"
    journal = WriteJournal(ws, journal_key("plan", "p1"))
    with bind_journal(journal, step="s2"):
        _write(page, "dal piano")
    entry = _entries_of(ws, journal.key)[0]
    assert entry["path"] == "wikis/piante/wiki/acero.md" and entry["step"] == "s2"
    journal.undo()
    assert page.read_text() == "# acero.md\n\nstato iniziale\n"


def test_no_bound_journal_notes_nothing(ws: Path) -> None:
    _write(ws / "wikis/piante/wiki/acero.md", "dell'utente")
    assert not (ws.parent / "db" / "undo.sqlite").exists() or not _entries(ws)


def test_binding_none_hides_an_inherited_journal(ws: Path) -> None:
    journal = WriteJournal(ws, _key())
    with bind_journal(journal), bind_journal(None):
        _write(ws / "wikis/piante/wiki/acero.md", "da un task nato altrove")
    assert not _entries(ws)


def _entries_of(ws: Path, key: str) -> list[dict]:
    return WriteJournal(ws, key)._read_entries()


# -- più registri insieme: il ritorno di un piano ----------------------------


def _plan_helpers(ws: Path) -> tuple[_Helper, _Helper]:
    """Due aiutanti dello stesso piano: il primo cambia acero e felce, il secondo
    riscrive acero dopo di lui e crea una pagina nuova."""
    wiki = ws / "wikis/piante/wiki"
    a, b = _helper(ws, "la", "ta"), _helper(ws, "lb", "tb")
    with _at_work(a):
        _write(wiki / "acero.md", "da a")
        _write(wiki / "felce.md", "da a")
    with _at_work(b):
        _write(wiki / "acero.md", "da b")
        _write(wiki / "nuova.md", "nuova, da b")
    return a, b


def test_two_journals_on_the_same_file_make_one_chain(ws: Path) -> None:
    before = _tree(ws)
    a, b = _plan_helpers(ws)
    into = WriteJournal(ws, journal_key("undo", "p1-1"))
    report = undo_set([a.journal, b.journal], by="user", into=into)
    assert report.kept == []
    assert _tree(ws) == before
    assert "wikis/piante/wiki/nuova.md" in report.removed


def test_the_undo_of_the_undo_puts_the_work_back(ws: Path) -> None:
    a, b = _plan_helpers(ws)
    after_work = _tree(ws)
    into = WriteJournal(ws, journal_key("undo", "p1-1"))
    undo_set([a.journal, b.journal], into=into)
    again = WriteJournal(ws, journal_key("undo", "p1-2"))
    report = undo_set([into], into=again)
    assert report.kept == []
    assert _tree(ws) == after_work


def test_kept_groups_and_never_stay_as_they_are(ws: Path) -> None:
    a, b = _plan_helpers(ws)
    into = WriteJournal(ws, journal_key("undo", "p1-1"))
    group = {"wikis/piante/wiki/felce.md": "sources"}
    report = undo_set(
        [a.journal, b.journal], into=into, keep_groups={"sources"},
        group_of=lambda p: group.get(p, "pages"),
        never=lambda p: p.endswith("nuova.md"))
    wiki = ws / "wikis/piante/wiki"
    assert (wiki / "felce.md").read_text() == "da a"
    assert (wiki / "nuova.md").read_text() == "nuova, da b"
    assert report.excluded == ["wikis/piante/wiki/nuova.md"]
    outcomes = report.outcomes()
    assert outcomes["kept"] == ["wikis/piante/wiki/felce.md"]
    assert outcomes["restored"] == ["wikis/piante/wiki/acero.md"]
    assert report.to_dict()["counts"]["kept"] == 1


def test_the_preview_touches_nothing_and_its_token_guards_the_confirm(ws: Path) -> None:
    a, b = _plan_helpers(ws)
    after_work = _tree(ws)
    preview = undo_set([a.journal, b.journal], dry_run=True)
    assert _tree(ws) == after_work and preview.dry_run and preview.token
    into = WriteJournal(ws, journal_key("undo", "p1-1"))
    for bad in ("nope", "0" * 16):
        with pytest.raises(UndoConflictError):
            undo_set([a.journal, b.journal], into=into, token=bad)
    assert _tree(ws) == after_work
    (ws / "wikis/piante/wiki/acero.md").write_text("dell'utente, dopo l'anteprima")
    with pytest.raises(UndoConflictError):
        undo_set([a.journal, b.journal], into=into, token=preview.token)
    fresh = undo_set([a.journal, b.journal], dry_run=True)
    report = undo_set([a.journal, b.journal], into=into, token=fresh.token)
    assert {"path": "wikis/piante/wiki/acero.md", "reason": KEPT_CHANGED_AFTER} in report.kept


def test_a_killed_undo_set_is_finished_at_boot_with_its_choices(ws: Path, monkeypatch) -> None:
    before = _tree(ws)
    a, b = _plan_helpers(ws)
    into = WriteJournal(ws, journal_key("undo", "p1-1"))
    calls = {"n": 0}
    real = write_journal._UndoRun._undo_file

    def _dies_on_the_second(self, key, chain, report, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise SystemExit("kill")
        return real(self, key, chain, report, **kw)

    monkeypatch.setattr(write_journal._UndoRun, "_undo_file", _dies_on_the_second)
    with pytest.raises(SystemExit):
        undo_set([a.journal, b.journal], into=into,
                 never=lambda p: p.endswith("felce.md"))
    monkeypatch.setattr(write_journal._UndoRun, "_undo_file", real)
    jdb.close_all()
    write_journal._LIVE.clear()
    assert finish_interrupted_undos(ws) == 1
    tree = _tree(ws)
    # La scelta fatta alla partenza vale anche al boot: felce resta.
    assert tree["wikis/piante/wiki/felce.md"] == "da a"
    assert tree["wikis/piante/wiki/acero.md"] == before["wikis/piante/wiki/acero.md"]
    assert "wikis/piante/wiki/nuova.md" not in tree


def test_a_file_put_back_before_its_row_gets_a_repair_row(ws: Path, monkeypatch) -> None:
    a, b = _plan_helpers(ws)
    into = WriteJournal(ws, journal_key("undo", "p1-1"))
    real = WriteJournal.settle

    def _dies_once(self, tool=""):
        if self is into and tool == "undo":
            monkeypatch.setattr(WriteJournal, "settle", real)
            self._pending.clear()  # il kill porta via quel che era in memoria
            raise SystemExit("kill")
        return real(self, tool)

    monkeypatch.setattr(WriteJournal, "settle", _dies_once)
    with pytest.raises(SystemExit):
        undo_set([a.journal, b.journal], into=into)
    assert finish_interrupted_undos(ws) == 1
    repaired = [e for e in into._read_entries() if e.get("tool") == "undo"]
    assert repaired and all("path" in e for e in repaired)
    report = undo_set([into], into=WriteJournal(ws, journal_key("undo", "p1-2")))
    assert {"path": "wikis/piante/wiki/acero.md", "reason": KEPT_NOT_SAVED} in report.kept


def test_several_journals_need_a_journal_of_their_own(ws: Path) -> None:
    a, b = _plan_helpers(ws)
    with pytest.raises(ValueError):
        undo_set([a.journal, b.journal])


# -- le bozze ----------------------------------------------------------------


def test_an_installed_draft_is_undone_with_whoever_wrote_it(ws: Path) -> None:
    """La bozza la scrive l'aiutante, la installa l'utente dalla casa (fuori dal
    suo lavoro): conta lo stesso come lavoro dell'aiutante."""
    from jenny.agent import staged_writes

    page = ws / "wikis/piante/wiki/acero.md"
    original = page.read_text()
    helper = _helper(ws)
    with _at_work(helper):
        ref = staged_writes.stage(ws, "wikis/piante/wiki/acero.md", b"dalla bozza",
                                  sources=("web",), task_id="t1")
    meta, _content = staged_writes.read_staged(ws, ref.id)
    assert meta["journal"] == helper.journal.key
    assert staged_writes.install(ws, ref.id, staged_writes.sha256_of(page)) == "installed"
    assert page.read_text() == "dalla bozza"
    report = WriteJournal(ws, helper.journal.key).undo()
    assert report.kept == [] and page.read_text() == original
