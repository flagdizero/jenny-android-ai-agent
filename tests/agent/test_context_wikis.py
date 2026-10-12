"""Il blocco ``## Wikis`` nel system prompt.

**Il prompt conosce le wiki per nome e scope; il contenuto si legge.** Il blocco
e' l'elenco delle cartelle sotto ``wikis/`` con la riga di scope di ognuna, reso
dal disco a ogni build — e' quel che restava di utile della rubrica compilata
da un modello, ed era il suo *input*.

Le trappole da tenere chiuse sono quattro: che l'elenco arrivi a un progetto o
al giardiniere (e' l'inventario degli *altri* soggetti, cioe' la fuga che il
confine dei progetti chiude); che uno scope mancante venga nascosto invece di
stampato; che il blocco cambi fra due build a wiki ferma (prefisso del prompt);
che uno ``summary:`` scritto come un saggio si porti il proprio costo su ogni
turno.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny.agent.context import ContextBuilder

pytestmark = pytest.mark.usefixtures("_configure_jenny_workspace")

WIKIS = "## Wikis"


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    (workspace / "memory").mkdir(parents=True)
    return workspace


def _wiki(workspace: Path, name: str, *, summary: str | None, dir_name: str = "wikis") -> Path:
    """Una wiki minima: ``wiki/`` (cio' che la rende una wiki) e ``AGENTS.md``."""
    root = workspace / dir_name / name
    (root / "wiki").mkdir(parents=True)
    front = f"summary: {summary}\n" if summary is not None else ""
    (root / "AGENTS.md").write_text(f"---\nid: {name}0000\n{front}---\n\n# {name}\n", encoding="utf-8")
    return root


def _section(prompt: str) -> str:
    """Il solo blocco ``## Wikis``, fino alla sezione successiva."""
    assert WIKIS in prompt
    body = prompt.split(WIKIS, 1)[1]
    for stop in ("\n## ", "\n# "):
        if stop in body:
            body = body.split(stop, 1)[0]
    return body


class TestPersonalChat:
    def test_every_wiki_has_a_line_with_its_scope_and_index(self, tmp_path):
        workspace = _workspace(tmp_path)
        _wiki(workspace, "erbario", summary="piante di casa e irrigazione")
        _wiki(workspace, "orto", summary="l'orto sul balcone")

        prompt = ContextBuilder(workspace).build_system_prompt(session_key="unified:default")

        block = _section(prompt)
        assert "- **erbario** — piante di casa e irrigazione → wikis/erbario/wiki/index.md" in block
        assert "- **orto** — l'orto sul balcone → wikis/orto/wiki/index.md" in block

    def test_order_is_alphabetical_whatever_the_disk_says(self, tmp_path):
        """Il prefisso del prompt deve dipendere dalle wiki, non dall'ordine di ``iterdir``."""
        workspace = _workspace(tmp_path)
        for name in ("zeta", "alfa", "mezzo"):
            _wiki(workspace, name, summary=name)

        block = _section(ContextBuilder(workspace).build_system_prompt(session_key="unified:default"))

        assert block.index("**alfa**") < block.index("**mezzo**") < block.index("**zeta**")

    def test_a_missing_scope_is_printed_not_hidden(self, tmp_path):
        """``(no scope set)`` e' il sintomo che fa riempire ``summary:``."""
        workspace = _workspace(tmp_path)
        _wiki(workspace, "muta", summary="<one-line scope — shown next to this wiki>")

        block = _section(ContextBuilder(workspace).build_system_prompt(session_key="unified:default"))

        assert "- **muta** — (no scope set) → wikis/muta/wiki/index.md" in block

    def test_the_lead_names_the_configured_folder(self, tmp_path):
        workspace = _workspace(tmp_path)
        _wiki(workspace, "erbario", summary="piante", dir_name="progetti")

        prompt = ContextBuilder(workspace, wikis_dir_name="progetti").build_system_prompt(
            session_key="unified:default"
        )

        block = _section(prompt)
        assert "Your wikis live under `progetti/`" in block
        assert "→ progetti/erbario/wiki/index.md" in block
        assert "wikis/" not in block

    def test_no_pages_are_counted(self, tmp_path):
        """Il blocco e' piatto nel numero di pagine: una camminata per wiki per turno costa."""
        workspace = _workspace(tmp_path)
        root = _wiki(workspace, "erbario", summary="piante")
        for i in range(7):
            (root / "wiki" / f"p{i}.md").write_text(f"# p{i}\n", encoding="utf-8")

        block = _section(ContextBuilder(workspace).build_system_prompt(session_key="unified:default"))

        assert "7" not in block
        assert "page" not in block.split("\n- ", 1)[1]


class TestAbsence:
    def test_no_wikis_folder_adds_no_block(self, tmp_path):
        prompt = ContextBuilder(_workspace(tmp_path)).build_system_prompt(session_key="unified:default")

        assert WIKIS not in prompt

    def test_a_folder_without_wiki_subdir_is_not_a_wiki(self, tmp_path):
        workspace = _workspace(tmp_path)
        (workspace / "wikis" / "appunti").mkdir(parents=True)
        (workspace / "wikis" / "_index.md").write_text("# registro\n", encoding="utf-8")

        prompt = ContextBuilder(workspace).build_system_prompt(session_key="unified:default")

        assert WIKIS not in prompt

    def test_disabled_wiki_feature_adds_no_block(self, tmp_path):
        workspace = _workspace(tmp_path)
        _wiki(workspace, "erbario", summary="piante")

        prompt = ContextBuilder(workspace, wikis_enabled=False).build_system_prompt(
            session_key="unified:default"
        )

        assert WIKIS not in prompt
        assert "erbario" not in prompt

    def test_a_project_does_not_see_the_other_wikis(self, tmp_path):
        """Si asserisce il **nome** dell'altra wiki, non solo l'intestazione: e' la fuga."""
        workspace = _workspace(tmp_path)
        project = _wiki(workspace, "erbario", summary="piante")
        _wiki(workspace, "segreta", summary="l'altro soggetto")

        prompt = ContextBuilder(workspace).build_system_prompt(
            workspace=project, session_key="project:erbario"
        )

        assert WIKIS not in prompt
        assert "segreta" not in prompt

    def test_a_gardener_pass_does_not_see_the_wikis(self, tmp_path):
        workspace = _workspace(tmp_path)
        project = _wiki(workspace, "erbario", summary="piante")
        _wiki(workspace, "segreta", summary="l'altro soggetto")

        prompt = ContextBuilder(workspace).build_system_prompt(
            workspace=project, session_key="gardener:erbario-20260903-120000"
        )

        assert WIKIS not in prompt
        assert "segreta" not in prompt


class TestShape:
    def test_two_builds_are_identical(self, tmp_path):
        workspace = _workspace(tmp_path)
        _wiki(workspace, "erbario", summary="piante")
        _wiki(workspace, "orto", summary="balcone")
        builder = ContextBuilder(workspace)

        first = _section(builder.build_system_prompt(session_key="unified:default"))
        second = _section(builder.build_system_prompt(session_key="unified:default"))

        assert first == second

    def test_a_new_wiki_is_there_on_the_next_build(self, tmp_path):
        """Nessuno stato da aggiornare: il disco e' la fonte."""
        workspace = _workspace(tmp_path)
        _wiki(workspace, "erbario", summary="piante")
        builder = ContextBuilder(workspace)
        before = _section(builder.build_system_prompt(session_key="unified:default"))
        _wiki(workspace, "orto", summary="balcone")

        after = _section(builder.build_system_prompt(session_key="unified:default"))

        assert "**orto**" not in before
        assert "**orto**" in after

    def test_an_essay_in_summary_is_cut_at_the_ceiling(self, tmp_path):
        workspace = _workspace(tmp_path)
        for i in range(10):
            _wiki(workspace, f"w{i}", summary="parola " * 400)  # ~2,8 kB a wiki

        block = _section(ContextBuilder(workspace).build_system_prompt(session_key="unified:default"))

        assert "... (truncated)" in block
        # Il tetto e' dell'elenco; la frase dei cassetti, fissa, gli sta dopo.
        from jenny.agent.context import _WIKIS_BLOCK_DRAWERS, _WIKIS_BLOCK_MOVE

        rest = block.replace(_WIKIS_BLOCK_DRAWERS, "").replace(_WIKIS_BLOCK_MOVE, "")
        assert len(rest) < 1500 * 4 + 200

    def test_survives_an_untouched_memory_template(self, tmp_path):
        """La regressione piu' facile: annidare il blocco nella guardia di MEMORY.md."""
        from jenny.utils.helpers import load_bundled_template

        workspace = _workspace(tmp_path)
        template = load_bundled_template("memory/MEMORY.md")
        assert template is not None
        (workspace / "memory" / "MEMORY.md").write_text(template, encoding="utf-8")
        _wiki(workspace, "erbario", summary="piante")

        prompt = ContextBuilder(workspace).build_system_prompt(session_key="unified:default")

        assert WIKIS in prompt
        assert "## Long-term Memory" not in prompt
        assert prompt.count("# Memory\n") == 1


def test_the_drawers_rule_comes_with_the_tool_that_files(tmp_path):
    """Dire «archivia con journal_append» a chi non ce l'ha e' un invito a sbagliare."""
    from jenny.agent.context import _WIKIS_BLOCK_DRAWERS

    workspace = _workspace(tmp_path)
    _wiki(workspace, "camper", summary="Il camper: manutenzione e viaggi")
    builder = ContextBuilder(workspace)
    with_tool = builder.build_system_prompt(session_key="unified:default",
                                            available_tools=["journal_append", "read_file"])
    without = builder.build_system_prompt(session_key="unified:default",
                                          available_tools=["read_file"])
    assert _WIKIS_BLOCK_DRAWERS in with_tool
    assert _WIKIS_BLOCK_DRAWERS not in without
    assert "camper" in without
