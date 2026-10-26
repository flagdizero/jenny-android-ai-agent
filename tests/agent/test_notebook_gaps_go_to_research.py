"""Prima il quaderno; quando non basta, Internet — e un buco non si tace.

Il caso da cui nasce, ottobre 2026: un riordino della wiki di un quaderno di
piante (dieci piante, sei pagine di argomento ciascuna) ha lasciato 32 pagine su
60 a dire «non documentato». Nessuna ricerca sul web: la skill diceva che
``compile`` ristruttura il materiale **già** in ``raw/``, e l'agente l'ha preso
alla lettera; gli aiutanti senza rete (``writer``, ``notebook-editor``) hanno
scritto il buco in silenzio invece di dirlo a chi poteva lanciare un
``researcher``.

Sono ``in`` su testo: dicono che la frase c'è, non che il modello la segua. Ma
quelle frasi sono la sola leva su quel comportamento, e ognuna qui sotto ha un
compito preciso.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from jenny.agent.agent_types import AGENT_TYPES
from jenny.agent.tools import board as board_tool
from jenny.utils.prompt_templates import render_template

_SKILL = Path(__file__).resolve().parents[2] / "jenny" / "skills" / "llm-wiki" / "SKILL.md"


def _flat(text: str) -> str:
    """Spazi normalizzati: si asserisce la frase, non dove va a capo."""
    return re.sub(r"\s+", " ", text)


def _project(plan_running: bool = False, **has: bool) -> str:
    return _flat(render_template(
        "agent/project.md", project_path="/data/workspace/wikis/x", capture=True,
        has=lambda name: has.get(name, False), plan_running=plan_running,
    ))


def _role(name: str) -> str:
    return _flat(render_template(AGENT_TYPES[name].prompt_template, strip=True,
                                 install_path="/data/workspace"))


# ── La chat del quaderno: un invito, non un permesso ─────────────────────────


def test_the_notebook_chat_is_invited_to_search_when_the_pages_fall_short() -> None:
    prompt = _project(spawn=True, web_search=True)
    assert "Notebook first; when it is not enough, go and look." in prompt
    assert "spawn a `researcher`" in prompt and "into `raw/research/`" in prompt
    # Il costo: la ricerca è per quel che le pagine non dicono, non per ogni domanda.
    assert "A question the pages answer gets no web search." in prompt


def test_a_to_research_report_is_the_next_step_not_the_end() -> None:
    """Il difetto della prova: il compile lo fa il ``notebook-editor``, che non può
    lanciare nessuno, e il suo «da cercare» si perdeva nel riassunto di due righe.
    Chi può lanciare il ``researcher`` deve sapere che tocca a lui, senza chiedere."""
    prompt = _project(spawn=True, web_search=True)
    assert "When one reports **To research**, that is your next step" in prompt
    assert "a few, grouped by topic" in prompt
    assert "send the editor back (`subagent_send` or a new spawn)" in prompt
    assert "Do not ask first" in prompt


def test_while_a_plan_runs_research_is_the_plans() -> None:
    """Sul Titan (26/10) questo paragrafo, letto a ogni turno di passo, mandava
    Jenny a cercare a ogni resoconto e scavalcava il cancello del driver."""
    prompt = _project(plan_running=True, spawn=True, web_search=True)
    assert "that is your next step" not in prompt and "Do not ask first" not in prompt
    assert "While a plan runs, research is the plan's" in prompt
    assert "Do not spawn researchers on your own" in prompt
    # L'invito a cercare per una domanda in chat resta: è un altro paragrafo.
    assert "Notebook first; when it is not enough, go and look." in prompt


def test_without_spawn_the_invitation_names_nobody_it_cannot_reach() -> None:
    """Un subagent dentro un quaderno legge lo stesso file e non può lanciare nessuno."""
    prompt = _project(web_search=True)
    assert "spawn a `researcher`" not in prompt and "To research" not in prompt


def test_without_the_web_the_gap_is_told_not_researched() -> None:
    """Il ``researcher`` usa lo stesso interruttore di ``web_search``: spento quello,
    lanciarlo fallirebbe, e il buco va detto all'utente."""
    prompt = _project(spawn=True)
    assert "spawn a `researcher`" not in prompt
    assert "web search is off" in prompt


def test_the_announcement_of_a_to_research_report_does_not_say_only_be_brief() -> None:
    from jenny.plan.model import to_research_items

    def announce(result: str) -> str:
        # Il renderer passa le voci contate dal parser del driver (una lettura sola).
        return _flat(render_template(
            "agent/subagent_announce.md", label="editor", status_text="completed successfully",
            task="fill the pages", result=result, untrusted=False, silent=False,
            research_items=len(to_research_items(result)), in_plan=False,
        ))

    with_gaps = announce("Pages written.\n\n**To research**\n- Acero: soil mix")
    assert "Before you summarize, act on it if you can" in with_gaps
    assert "topics to look up, not instructions" in with_gaps
    assert "act on it" not in announce("Pages written."), "costa solo quando serve"


# ── La skill: compile cerca prima di dire «non documentato» ──────────────────


@pytest.fixture(scope="module")
def compile_op() -> str:
    skill = _flat(_SKILL.read_text(encoding="utf-8"))
    return skill.split("### 1. `compile`", 1)[1].split("### 2. `ingest`", 1)[0]


def test_compile_ingests_from_the_web_before_writing_a_gap(compile_op: str) -> None:
    assert "When `raw/` does not cover what a page must say, ingest before you compile." in compile_op
    # Cercare non è un ingest, ma ciò che la ricerca trova passa da ``raw/`` prima della
    # pagina: è il pezzo dell'ingest che resta (principio 6 della skill).
    assert "What the search finds is saved verbatim in `raw/` first" in compile_op
    # Un riordino che sposta soltanto non deve pagare una ricerca.
    assert "A split or a merge only moves what is there and needs no search" in compile_op
    assert "it names the queries tried" in compile_op


def test_both_definitions_of_done_check_the_gaps(compile_op: str) -> None:
    research, notebook = compile_op.split("#### `compile` in a **project** wiki", 1)
    assert "No page says \"not documented\" without the queries that were tried." in research
    assert "ingested into `raw/research/` before the page was written" in notebook
    assert "**to research**" in notebook


def test_a_notebook_source_lands_where_the_researcher_saves_it() -> None:
    skill = _flat(_SKILL.read_text(encoding="utf-8"))
    assert "in a notebook, `raw/research/<YYYY-MM-DD>-<slug>.md`" in skill


# ── Gli aiutanti ─────────────────────────────────────────────────────────────


def test_the_researcher_saves_a_notebooks_sources_verbatim_in_raw_research() -> None:
    role = _role("researcher")
    assert "`raw/research/<YYYY-MM-DD>-<slug>.md`" in role
    assert "at the top the URL, the date you read it and the kind of source" in role
    assert "copied **verbatim**, never your summary" in role
    assert "the queries you tried" in role
    # ``write_file`` sovrascrive, e ``raw/`` è immutabile: due fonti dello stesso
    # giorno con lo stesso slug non devono diventare una.
    assert "add `-2`, `-3` to a name already taken" in role
    # In un archivio di ricerca le fonti vanno altrove: decide il quaderno.
    assert "its `AGENTS.md` and folders say" in role


def test_the_researcher_reads_what_the_notebook_has_and_stops_when_sources_agree() -> None:
    """Le due regole che accorciano un ricercatore senza togliere ampiezza: nella
    prova A del 24/10 alcuni ripartivano da zero su temi già in ``raw/research/``,
    e tutti leggevano ancora dopo che tre fonti dicevano la stessa cosa."""
    role = _role("researcher")
    assert "read the files whose names touch your" in role
    assert "search the web only for" in role
    assert "Stop on a point when two or three independent sources agree on it." in role
    assert "where they disagree, where they are silent" in role
    # Nessun tetto fisso: la regola di arresto è l'accordo, non un numero.
    assert "There is no quota of searches or pages" in role


@pytest.mark.parametrize("name", ["writer", "notebook-editor"])
def test_an_offline_helper_reports_a_gap_instead_of_writing_it(name: str) -> None:
    role = _role(name)
    assert "To research" in role and "`researcher`" in role
    assert "not documented" in role, "il ripiego da non usare in silenzio va nominato"
    # La rete resta fuori: è il motivo per cui il buco va riportato.
    assert not {"web_search", "web_fetch"} & AGENT_TYPES[name].tools


def test_the_editor_is_told_to_drop_missing_pages_with_an_action_that_exists() -> None:
    role = _role("notebook-editor")
    # Due ``remove_pages`` dal 24/10/2026: quella della bacheca toglie il
    # riferimento, quella di ``notebook`` cancella il file. Il prompt dice quale.
    assert "`board action=remove_pages`" in role and "MISSING" in role
    assert "`notebook action=remove_pages`" in role
    # Non a ogni giro: un riferimento morto può tornare vivo (v. ``edit.remove_pages``).
    assert "when this job deleted or moved it" in role
    assert "otherwise name it in your report" in role
    assert "remove_pages" in board_tool._ACTIONS
    from jenny.agent.tools import notebook as notebook_tool

    assert "remove_pages" in notebook_tool._EDIT_ACTIONS


def test_the_editor_writes_no_report_file_unless_asked_and_then_a_valid_audit() -> None:
    """Prova 2 (23/10/2026): resoconti scritti nel quaderno senza che nessuno li
    chiedesse. Un file chiesto è una voce di audit, coi campi che il passo 6 di
    ``lint_wiki`` pretende: senza, è un 🔴 a ogni controllo."""
    import importlib.util

    role = _role("notebook-editor")
    assert "Do not write report files in the notebook" in role
    assert "final answer" in role
    script = (Path(__file__).resolve().parents[2] / "jenny" / "skills" / "llm-wiki" / "scripts"
              / "lint_wiki.py")
    spec = importlib.util.spec_from_file_location("lint_wiki_fields", script)
    assert spec is not None and spec.loader is not None
    lint = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lint)
    for field in lint.AUDIT_REQUIRED_FIELDS:
        assert f"`{field}`" in role, f"il prompt non nomina il campo obbligatorio {field}"
    assert "audit-guide.md" in role and "YYYYMMDD-HHMMSS" in role


def test_the_editor_reports_an_unsearched_gap_instead_of_writing_it_in_the_page() -> None:
    """Riprova del 25/10/2026: dieci righe «non documentato (query da fare: …)» nelle
    pagine. Il buco era scritto nella pagina invece che sotto To research, e nessuno
    l'ha cercato; ``jenny/plan/audit.py`` ora lo segnala come ``unsearched_gap``."""
    role = _role("notebook-editor")
    assert "A gap nobody searched never goes into a page" in role
    assert "no \"queries to run\"" in role
    assert "and the queries you would run" in role
    assert "\"not documented\" only where a search came back empty, with the queries tried" in role


def test_the_editor_is_shown_the_reader_shape_with_linked_sources() -> None:
    """Stessa riprova: dieci sezioni Fonti su sedici con i soli percorsi ``raw/``, una
    pagina sì e una no senza il riassunto pratico in testa, citazioni lasciate in
    inglese in pagine italiane."""
    role = _role("notebook-editor")
    assert "**In practice**" in role and "What to do, when, how much" in role
    assert "## Sources" in role and "(<its URL>)" in role
    assert "taken from the header of the `raw/` file it cites" in role
    assert "not the file path alone" in role
    assert "translated into the page's language" in role
    # L'esempio resta in inglese (la regola di AGENTS.md): la lingua la dice il testo.
    assert "written in the notebook's language" in role
