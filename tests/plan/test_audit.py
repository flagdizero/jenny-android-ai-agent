"""Il controllo a macchina di fine piano (``jenny/plan/audit.py``).

Le righe dei buchi sono prese dalla forma che le pagine vere avevano nella
riprova del 25/10/2026 (con piante e testi inventati): la macchina deve vedere
quelle e non le frasi normali che contengono «da cercare» o «todo».
"""

from __future__ import annotations

from pathlib import Path

from jenny.plan.audit import (
    EMPTY_BODY_CHARS,
    audit_notebook,
    baseline_keys,
    remove_empty_folders,
    render_repair,
    summary_line,
)

_BODY = "Testo vero della pagina, con abbastanza parole per non essere vuota. " * 6


def _page(root: Path, rel: str, body: str = _BODY, *, fm: str = "state: open\n") -> Path:
    path = root / "wiki" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{fm}---\n# {path.stem}\n\n{body}\n", encoding="utf-8")
    return path


def _kinds(root: Path, **kw) -> list[str]:
    return sorted(f.kind for f in audit_notebook(root, **kw).findings)


def test_a_clean_notebook_has_nothing_to_repair(tmp_path: Path) -> None:
    _page(tmp_path, "piante/salvia/index.md")
    _page(tmp_path, "piante/salvia/acqua.md", _BODY + "\n## Fonti\n- https://example.org/salvia\n")
    audit = audit_notebook(tmp_path)
    assert audit.findings == []
    assert "nothing to repair" in summary_line(audit)


def test_lines_left_to_search_are_found_and_ordinary_words_are_not(tmp_path: Path) -> None:
    _page(tmp_path, "piante/salvia/terra.md", _BODY + (
        "\n- **pH**: **non documentato** (query da fare: «salvia soil ph»).\n"
        "- Le proporzioni del terriccio restano da cercare.\n"
        "- Queries to run: «sage potting mix».\n"
        "- È il parassita da cercare per primo sulle foglie.\n"
        "- Identità, origine, stato, todo.\n"
        "- **Talee**: non documentato (query fatte: «salvia talee»).\n"
    ))
    audit = audit_notebook(tmp_path)
    gaps = [f for f in audit.findings if f.kind == "unsearched_gap"]
    assert [f.line for f in gaps] == [7, 8, 9]
    assert audit.not_documented == 2


def test_a_sources_section_without_links_is_a_defect(tmp_path: Path) -> None:
    _page(tmp_path, "piante/salvia/luce.md",
          _BODY + "\n## Fonti\n\n- `raw/research/2026-10-01-salvia.md` — luce e ore di sole.\n"
          "\n## Altro\nhttps://example.org/non-conta\n")
    _page(tmp_path, "piante/salvia/acqua.md", _BODY + "\n## Sources\n- https://example.org/a\n")
    found = [f for f in audit_notebook(tmp_path).findings if f.kind == "sources_no_link"]
    assert [f.path for f in found] == ["wiki/piante/salvia/luce.md"]


def test_an_empty_page_is_a_defect_but_an_index_is_not(tmp_path: Path) -> None:
    _page(tmp_path, "piante/salvia/potatura.md", "## Quando\n\n## Come\n")
    _page(tmp_path, "piante/salvia/index.md", "- [[potatura]]")
    _page(tmp_path, "piante/salvia/acqua.md", "x" * EMPTY_BODY_CHARS)
    found = [f for f in audit_notebook(tmp_path).findings if f.kind == "empty_page"]
    assert [f.path for f in found] == ["wiki/piante/salvia/potatura.md"]


def test_defects_already_there_at_approve_are_counted_not_asked(tmp_path: Path) -> None:
    _page(tmp_path, "vecchia.md", _BODY + "\nVedi [[non-esiste]].\n")
    base = baseline_keys(tmp_path)
    # Una riga aggiunta sopra sposta il link: lo stesso difetto.
    _page(tmp_path, "vecchia.md", "Riga nuova.\n\n" + _BODY + "\nVedi [[non-esiste]].\n")
    _page(tmp_path, "nuova.md", _BODY + "\nVedi [[sparita]].\n")
    audit = audit_notebook(tmp_path, baseline=base)
    assert [(f.kind, f.path) for f in audit.findings] == [("dead_link", "wiki/nuova.md")]
    assert audit.preexisting == 1
    assert "1 defect(s) were there before the plan" in summary_line(audit)


def test_expected_pages_missing_are_listed(tmp_path: Path) -> None:
    for plant in ("salvia", "rosmarino"):
        _page(tmp_path, f"piante/{plant}/index.md")
        _page(tmp_path, f"piante/{plant}/acqua.md")
    _page(tmp_path, "piante/salvia/luce.md")
    expect = {"folders": "wiki/piante/*", "files": ["index", "acqua", "luce"]}
    audit = audit_notebook(tmp_path, expect=expect)
    assert [(f.kind, f.path) for f in audit.findings] == [
        ("expect_missing", "wiki/piante/rosmarino/luce.md"),
    ]


def test_the_repair_list_is_grouped_by_page_with_what_to_do(tmp_path: Path) -> None:
    _page(tmp_path, "a.md", _BODY + "\n- query da fare: «x»\n\n## Fonti\n- raw/x.md\n")
    groups = render_repair(audit_notebook(tmp_path))
    assert [g["path"] for g in groups] == ["wiki/a.md"]
    assert len(groups[0]["items"]) == 2
    assert all("→" in item for item in groups[0]["items"])


def test_the_repair_list_is_capped(tmp_path: Path) -> None:
    for n in range(10):
        _page(tmp_path, f"p{n}.md", _BODY + "\n- query da fare: «x»\n")
    groups = render_repair(audit_notebook(tmp_path), limit=4)
    assert sum(len(g["items"]) for g in groups) == 4


def test_empty_folders_under_wiki_go_and_nothing_else(tmp_path: Path) -> None:
    (tmp_path / "wiki" / "concepts" / "Vecchia" / "sotto").mkdir(parents=True)
    (tmp_path / "wiki" / "tenuta").mkdir(parents=True)
    (tmp_path / "wiki" / "tenuta" / ".gitkeep").write_text("")
    (tmp_path / "raw" / "vuota").mkdir(parents=True)
    _page(tmp_path, "piante/salvia/acqua.md")
    gone = remove_empty_folders(tmp_path)
    assert gone == ["wiki/concepts/", "wiki/concepts/Vecchia/", "wiki/concepts/Vecchia/sotto/"]
    assert (tmp_path / "wiki").is_dir()
    assert (tmp_path / "wiki" / "tenuta").is_dir()
    assert (tmp_path / "raw" / "vuota").is_dir()
    assert (tmp_path / "wiki" / "piante" / "salvia").is_dir()
