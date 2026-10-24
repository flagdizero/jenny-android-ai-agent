"""La logica dello strumento ``notebook``: controllo, mappa, letture a gruppi.

Il test che conta di più è la **parità con ``lint_wiki.py``**: ``check`` e il lint
sono due implementazioni degli stessi controlli (il lint non si può importare:
stampa, scrive ``.jenny/lint_journal.json`` e su Android non importa ``jenny``),
e qui si tengono insieme sulle voci in comune, su una wiki di prova che le
sbaglia tutte.

Le differenze volute, che il test non deve nascondere:

- ``sources:`` scalare (``sources: raw/x.md``) per ``check`` è una fonte, per il
  lint no (guarda solo le liste);
- i ``.md`` nascosti sotto ``wiki/`` (``.bozza.md``) ``check`` non li legge, il
  lint sì;
- il passo 11 (fonti di ``raw/`` senza riassunto) ``check`` lo fa solo nel layout
  di ricerca: in un quaderno piatto ``raw/notes`` sono appunti dell'utente;
- il passo 3 (pagine assenti dalla mappa) e gli orfani ``check`` non li fa: nei
  progetti la mappa elenca cartelle e hub. Ogni risposta lo dice
  (``ops.NOT_CHECKED``).
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import re
from pathlib import Path

import pytest

from jenny.board import notebook_ops as ops
from jenny.board.store import mutate_board, mutate_reminders, write_card_summary

_LINT = Path(__file__).resolve().parents[2] / "jenny" / "skills" / "llm-wiki" / "scripts"


@pytest.fixture(scope="module")
def lint_wiki():
    """``lint_wiki.py`` caricato come modulo, come fa ``tests/skills/llm_wiki``."""
    spec = importlib.util.spec_from_file_location("lint_wiki_parity", _LINT / "lint_wiki.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def broken(tmp_path: Path) -> Path:
    """Un quaderno (layout piatto) con un esempio di ogni difetto in comune col lint."""
    root = tmp_path / "wikis" / "orto"
    _write(root / "raw" / "journal" / "20261001.md", "# 2026-10-01\n- 10:00 — [said] ok\n")
    _write(root / "raw" / "notes" / "terra.md", "terra\n")
    _write(root / "wiki" / "index.md",
           "# Mappa\n\n- [[acero]]\n- [[fantasma]]\n\n" + "Prosa della mappa. " * 140)
    _write(root / "wiki" / "acero.md",
           "---\nstate: open\nsource: raw/journal/20261001.md#10:00\n"
           "sources: [raw/notes/terra.md]\n---\n# Acero\n\nVedi [[fico]] e [[Nessuno]].\n"
           "Un esempio: `[[esempio]]`.\n")
    _write(root / "wiki" / "fico.md", "# Fico\n\nNiente frontmatter. [[acero]]\n")
    _write(root / "wiki" / "pino.md",
           "---\nstate: forse\nsource: raw/journal/19990101.md\n"
           "sources:\n  - raw/articles/sparito.md\n---\n# Pino\n\n[[acero]]\n")
    _write(root / "wiki" / "lungo.md",
           "---\nstate: open\nsource: raw/journal/20261001.md#10:00\n---\n# Lungo\n\n"
           + "Una frase che si ripete. " * 300 + "\n[[acero]]\n")

    def board(b):
        b["skeleton"] = ["Identità"]
        b["cards"].append({"id": "acero", "title": "Acero", "pages": ["acero.md", "sparita.md"],
                           "pos": {"x": 1, "y": 1}})

    mutate_board(root, board)
    write_card_summary(root, "acero", {"card": "acero", "sections": [
        {"title": "Identità", "text": "Un acero.", "sources": ["acero.md", "sparita.md"]}]})

    def rems(r):
        r["reminders"].append({"id": "r-rinvaso", "card": "acero", "text": "Rinvaso",
                               "when": {"kind": "date", "date": "2099-03-01"},
                               "why": "sparita.md", "by": "user", "state": "open"})

    mutate_reminders(root, rems)
    return root


def _lint_output(lint_wiki, root: Path) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        lint_wiki.lint(str(root))
    return buf.getvalue()


def _section(out: str, header: str) -> list[str]:
    """Le voci (``wiki/…``) sotto l'intestazione del lint che contiene *header*."""
    lines = out.split("\n")
    for i, line in enumerate(lines):
        if header in line:
            block = []
            for nxt in lines[i + 1:]:
                if not nxt.strip() or not nxt.startswith("   "):
                    break
                if nxt.strip().startswith("wiki/"):
                    block.append(nxt.strip())
            return block
    return []


def _kinds(report: ops.CheckReport, kind: str) -> list[ops.Defect]:
    return [d for d in report.defects if d.kind == kind]


def test_check_finds_what_lint_wiki_finds(lint_wiki, broken: Path) -> None:
    out = _lint_output(lint_wiki, broken)
    report = ops.check_notebook(broken)

    lint_dead = {tuple(m.groups()) for line in _section(out, "Dead wikilinks")
                 if (m := re.match(r"(\S+) → \[\[(.+)\]\]$", line))}
    mine_dead = {(d.path, re.search(r"\[\[(.+)\]\]", d.detail).group(1))  # type: ignore[union-attr]
                 for d in _kinds(report, "dead_link")}
    assert lint_dead == mine_dead == {("wiki/index.md", "fantasma"), ("wiki/acero.md", "Nessuno")}

    lint_missing = {tuple(m.groups()) for line in _section(out, "citing sources not found")
                    if (m := re.match(r"(\S+) → sources: (.+)$", line))}
    mine_missing = {(d.path, d.detail.split("sources: ")[1].split(" — ")[0])
                    for d in _kinds(report, "missing_source")}
    assert lint_missing == mine_missing == {("wiki/pino.md", "raw/articles/sparito.md")}

    lint_dangling = {tuple(m.groups()) for line in _section(out, "names a file that is not there")
                     if (m := re.match(r"(\S+) → source: (.+)$", line))}
    mine_dangling = {(d.path, d.detail.split("source: ")[1].split(" — ")[0])
                     for d in _kinds(report, "dangling_source")}
    assert lint_dangling == mine_dangling == {("wiki/pino.md", "raw/journal/19990101.md")}

    lint_state = {line.split(" — ")[0] for line in _section(out, "no valid `state:`")}
    mine_state = {d.path for d in report.defects if d.kind in ("no_state", "no_frontmatter")}
    assert lint_state == mine_state == {"wiki/fico.md", "wiki/pino.md"}

    lint_long = {line.split(" — ")[0] for line in _section(out, "too long to be injected")}
    assert lint_long == {d.path for d in _kinds(report, "over_ceiling")} == {"wiki/lungo.md"}

    assert "The map is" in out
    assert [d.path for d in _kinds(report, "map_over")] == ["wiki/index.md"]

    assert "card `acero` lists pages that no longer exist: sparita.md" in out
    assert [d.detail for d in _kinds(report, "card_missing_page")] == [
        "card `acero` lists sparita.md, which does not exist"]
    assert "cites missing pages: sparita.md" in out
    assert len(_kinds(report, "drawer_missing_source")) == 1
    # Il lint non guarda il ``why``: questo è in più, e serve a ``remove_pages``.
    assert [d.detail for d in _kinds(report, "reminder_dead_why")] == [
        "reminder `r-rinvaso`: why sparita.md does not exist"]


def test_the_shared_constants_stay_equal(lint_wiki) -> None:
    from jenny.agent.gardener import MAP_TARGET_CHARS, page_ceiling

    assert ops.PAGE_STATES == lint_wiki.PAGE_STATES
    assert ops.RESEARCH_SUBDIRS == lint_wiki.RESEARCH_SUBDIRS
    assert page_ceiling() == lint_wiki.PAGE_MAX_CHARS
    assert MAP_TARGET_CHARS == lint_wiki.MAP_MAX_CHARS
    assert ops._WIKILINK_RE.pattern == lint_wiki.WIKILINK_RE.pattern
    for text in ("---\nstate: open\n---\n", "\ufeff\n---\nstate: open\n---", "# no\n"):
        assert bool(ops._FRONTMATTER_RE.match(text)) == bool(lint_wiki.FRONTMATTER_RE.match(text))


def test_check_gives_the_line_and_writes_nothing(broken: Path) -> None:
    before = {p: p.stat().st_mtime_ns for p in broken.rglob("*")}
    report = ops.check_notebook(broken)
    dead = {d.path: d.line for d in _kinds(report, "dead_link")}
    assert dead["wiki/acero.md"] == 8  # la riga di «Vedi [[fico]] e [[Nessuno]]»
    state = _kinds(report, "no_state")[0]
    assert (state.path, state.line) == ("wiki/pino.md", 2)
    missing = _kinds(report, "missing_source")[0]
    assert missing.line == 5  # la voce della lista a blocco, non la chiave
    assert {p: p.stat().st_mtime_ns for p in broken.rglob("*")} == before
    assert ops.check_notebook(broken).defects == report.defects


def test_expect_lists_missing_and_extra_files(tmp_path: Path) -> None:
    root = tmp_path / "q"
    for plant in ("acero", "fico"):
        _write(root / "wiki" / "concepts" / plant / "index.md", "# x\n")
    _write(root / "wiki" / "concepts" / "acero" / "substrato.md", "# s\n")
    _write(root / "wiki" / "concepts" / "fico" / "appunti.md", "# a\n")
    report = ops.check_notebook(root, expect={"folders": "wiki/concepts/*",
                                              "files": ["index", "substrato"]})
    assert [d.path for d in _kinds(report, "expect_missing")] == [
        "wiki/concepts/fico/substrato.md"]
    assert [d.path for d in _kinds(report, "expect_extra")] == ["wiki/concepts/fico/appunti.md"]
    assert "expect: 2 folder(s) match wiki/concepts/*" in report.notes
    flat = ops.check_notebook(root, expect={"files": ["wiki/concepts/acero/index", "wiki/x"]})
    assert [d.path for d in _kinds(flat, "expect_missing")] == ["wiki/x.md"]


def test_render_respects_limit_and_the_char_cap(tmp_path: Path) -> None:
    root = tmp_path / "q"
    _write(root / "wiki" / "molti.md", "---\nstate: open\n---\n" + "".join(
        f"[[morto-{i}-{'x' * 10}]]\n" for i in range(200)))
    report = ops.check_notebook(root)
    assert len(_kinds(report, "dead_link")) == 200
    short = ops.render_check(report, limit=5, max_chars=12000)
    assert short.count("\n- ") == 5 and "(+195 more" in short
    capped = ops.render_check(report, limit=200, max_chars=3000)
    assert len(capped) <= 3000 and "more: raise limit" in capped


def test_map_counts_subject_by_topic_with_file_and_line(tmp_path: Path) -> None:
    root = tmp_path / "q"
    _write(root / "wiki" / "acero.md", "# Acero\n\nIl substrato è acido.\nLuce piena.\n")
    _write(root / "wiki" / "fico.md", "# Fico\n\nAcqua poca.\n")
    _write(root / "raw" / "notes" / "misto.md", "Il ficus e l'acero: terriccio drenante.\n")
    _write(root / ".jenny" / "tool-results" / "x.md", "acero substrato\n")
    subjects = ops.parse_terms([{"name": "Acero", "aliases": ["Acer"]},
                                {"name": "Fico", "aliases": ["ficus"]}], "aliases")
    topics = ops.parse_terms([{"name": "substrato", "keywords": ["terriccio"]},
                              {"name": "acqua"}], "keywords")
    out = ops.map_notebook(root, subjects, topics, ["wiki", "raw"], per_cell=3, max_chars=12000)
    assert "Map of 3 text files" in out  # .jenny/ non si legge
    assert "| Acero (2) | 2 | 0 |" in out
    assert "| Fico (2) | 1 | 1 |" in out
    assert "- wiki/acero.md:3 — Il substrato è acido." in out
    assert "- raw/notes/misto.md:1 —" in out
    assert "Empty cells (1): Acero × acqua" in out


def test_read_many_splits_the_budget_and_says_what_it_cut(tmp_path: Path) -> None:
    root = tmp_path / "q"
    _write(root / "wiki" / "corta.md", "---\nstate: open\n---\n# Corta\n")
    _write(root / "wiki" / "lunga.md", "# Lunga\n" + "riga di testo\n" * 2000)
    res = ops.read_many(root, ["wiki/corta.md", "lunga.md", "../fuori.md"], mode="full",
                        lines=20, max_chars=None, total_chars=3000)
    assert len(res.text) <= 3000
    assert "=== wiki/corta.md (5 lines) ===\n---\nstate: open" in res.text
    assert "../fuori.md: not a file inside this notebook" in res.text
    assert re.search(r"Truncated \(1\): wiki/lunga\.md \(from line \d+: read_file offset=\d+\)",
                     res.text)
    assert [p.name for p, _ in res.read] == ["corta.md", "lunga.md"]
    head = ops.read_many(root, ["wiki/lunga.md"], mode="head", lines=3, max_chars=None,
                         total_chars=12000)
    assert head.text.split("\n")[1:4] == ["# Lunga", "riga di testo", "riga di testo"]
    assert head.read[0][1] == 3
    fm = ops.read_many(root, ["wiki/corta.md", "wiki/lunga.md"], mode="frontmatter", lines=20,
                       max_chars=None, total_chars=12000)
    assert "---\nstate: open\n---" in fm.text and "(no frontmatter)\n# Lunga" in fm.text


# ── Revisione del 24/10/2026 ─────────────────────────────────────────────────


def test_read_many_never_passes_its_cap(tmp_path: Path) -> None:
    root = tmp_path / "q"
    folder = root / "wiki" / "entities" / "a-long-folder-name-for-plants"
    paths = []
    for i in range(30):
        _write(folder / f"page-with-a-rather-long-name-{i:02d}.md", "line of text here\n" * 2000)
        paths.append(f"wiki/entities/a-long-folder-name-for-plants/page-with-a-rather-long-name-"
                     f"{i:02d}.md")
    for total in (12000, 14000):
        res = ops.read_many(root, paths, mode="full", lines=20, max_chars=None, total_chars=total)
        assert len(res.text) <= total and "Truncated (" in res.text
    bad = ["x" * 400 + str(i) for i in range(30)]
    res = ops.read_many(root, bad, mode="full", lines=20, max_chars=None, total_chars=12000)
    assert len(res.text) <= 12000 and res.text.startswith("Error: nothing was read.")


def test_read_many_reads_a_big_file_only_in_part(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "q"
    _write(root / "raw" / "big.pdf", "x")
    _write(root / "raw" / "big.md", "riga lunga di testo\n" * 300_000)  # ~6 MB
    seen: list[int] = []
    real = ops._read_capped

    def capped(path, max_bytes):
        seen.append(max_bytes)
        return real(path, max_bytes)

    monkeypatch.setattr(ops, "_read_capped", capped)
    res = ops.read_many(root, ["raw/big.pdf", "raw/big.md"], mode="head", lines=3,
                        max_chars=None, total_chars=12000)
    assert "raw/big.pdf: not a text file" in res.text
    assert "=== raw/big.md (" in res.text and "read in part" in res.text
    assert seen and seen[0] < 100_000


def test_check_does_not_load_a_huge_page(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "q"
    _write(root / "wiki" / "enorme.md", "---\nstate: open\n---\n" + "x" * 2_100_000)
    real = ops._read

    def guarded(path):
        assert path.name != "enorme.md", "letta per intero"
        return real(path)

    monkeypatch.setattr(ops, "_read", guarded)
    report = ops.check_notebook(root)
    assert [d.path for d in _kinds(report, "over_ceiling")] == ["wiki/enorme.md"]
    assert "too large to check" in _kinds(report, "over_ceiling")[0].detail


def test_map_stops_at_its_byte_budget(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "q"
    _write(root / "wiki" / "acero.md", "Acero: substrato acido.\n")
    for i in range(3):
        _write(root / "raw" / f"r{i}.md", "acero e substrato\n" * 100)
    monkeypatch.setattr(ops, "_MAP_BYTE_BUDGET", 2000)
    subjects = ops.parse_terms([{"name": "Acero"}], "aliases")
    topics = ops.parse_terms([{"name": "substrato"}], "keywords")
    out = ops.map_notebook(root, subjects, topics, ["wiki", "raw"], per_cell=3, max_chars=12000)
    assert "Map of 2 text files" in out  # wiki prima di raw
    assert "Not searched: 2 files" in out
    empty = ops.map_notebook(root, subjects, topics, ["log"], per_cell=3, max_chars=12000)
    assert empty.startswith("Error:")


def test_research_layout_needs_sources_and_summaries(lint_wiki, tmp_path: Path) -> None:
    """I passi 9a e 11 del lint, che ``check`` ora fa; il 3 (pagine assenti dalla
    mappa) no, apposta: nelle strutture dei progetti la mappa elenca le cartelle."""
    root = tmp_path / "q"
    _write(root / "wiki" / "index.md", "# Mappa\n\n- [[concepts/suolo]]\n")
    _write(root / "wiki" / "concepts" / "suolo.md", "---\ntitle: Suolo\n---\n# Suolo\n")
    _write(root / "wiki" / "entities" / "acero.md",
           "---\nsources: [raw/articles/a.md]\n---\n# Acero\n")
    _write(root / "raw" / "articles" / "a.md", "testo\n")
    out = _lint_output(lint_wiki, root)
    report = ops.check_notebook(root)
    assert "wiki/concepts/suolo.md" in out
    assert [d.path for d in _kinds(report, "no_sources")] == ["wiki/concepts/suolo.md"]
    assert "raw/articles/a.md → expected wiki/summaries/a.md" in out
    assert [d.path for d in _kinds(report, "no_summary")] == ["raw/articles/a.md"]
    rendered = ops.render_check(report, limit=40, max_chars=12000)
    assert rendered.endswith(ops.NOT_CHECKED) and "orphans" in ops.NOT_CHECKED


def test_quoted_page_text_counts_as_read(broken: Path, monkeypatch) -> None:
    shown: list[str] = []
    monkeypatch.setattr(ops, "_shown", lambda path: shown.append(path.name) or True)
    ops.check_notebook(broken)
    assert "pino.md" in shown  # state: forse è testo della pagina
    assert {"board.json", "acero.json", "reminders.json"} <= set(shown)
