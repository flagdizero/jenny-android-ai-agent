"""Il prompt non deve insegnare tool che non esistono in quello scope.

Sul telefono Jenny ha chiamato ``grep`` in modalita orchestratore, dove non
c'era. Non se l'era inventato: glielo dicevano ``identity.md`` e la skill
``memory``, che ha ``always: true`` e quindi entra in *ogni* prompt con cinque
esempi. A sette righe di distanza il contratto dei tool dichiarava il contrario.
Il modello ha seguito l'istruzione piu concreta — quella con gli esempi.

Questo file e il guardiano di quella regola. Confronta solo contro nomi di tool
*veri*, quindi non produce falsi positivi su prosa qualunque, e obbliga chi in
futuro scrivera un nome di tool in una skill a fare una scelta consapevole
invece che per distrazione.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from jenny.agent.context import ContextBuilder
from jenny.agent.tools.loader import ToolLoader, declared_tool_name

# Nomi che il prompt cita *per vietarli* o per descrivere un altrove: la
# citazione e voluta, e toglierla peggiorerebbe il prompt. Ogni voce va
# giustificata qui, cosi aggiungerne una e una decisione e non una svista.
MENTIONED_ON_PURPOSE = {
    # Il contratto dei tool vieta esplicitamente di usare python_exec come
    # scorciatoia universale, e l'orchestratore elenca cio che NON ha.
    "python_exec",
    "web_search",
    "web_fetch",
    "download_file",
    "write_file",
    "edit_file",
    "apply_patch",
    "find_files",
    "write_stdin",
    "list_exec_sessions",
}

_BACKTICKED = re.compile(r"`([a-z][a-z0-9_]{2,})`")


def _tool_names_in_scope(scope: str) -> set[str]:
    """I tool che *appartengono* allo scope, a prescindere dalla config.

    Non si carica il registry: in un processo di test mancano il servizio cron,
    il manager dei subagent e il contesto Android, quindi meta dei tool
    risulterebbe assente per motivi che non hanno niente a che vedere con la
    domanda. Qui interessa se una frase del prompt parla di un tool che in
    quello scope non esiste *per costruzione*.
    """
    return {
        name
        for cls in ToolLoader().discover()
        if scope in getattr(cls, "_scopes", {"core"}) and (name := declared_tool_name(cls))
    }


def _every_declared_tool_name() -> set[str]:
    return {
        name for cls in ToolLoader().discover() if (name := declared_tool_name(cls))
    }


def _prompt(workspace: Path, *, orchestrator: bool) -> str:
    builder = ContextBuilder(
        workspace,
        orchestrator=orchestrator,
        available_tools=lambda: sorted(
            _tool_names_in_scope("orchestrator" if orchestrator else "core")
        ),
    )
    return builder.build_system_prompt(include_memory_recent_history=False)


@pytest.fixture
def workspace(tmp_path: Path):
    """Workspace vero: i template e le skill si leggono da li, non dal package."""
    from jenny.config import paths as paths_mod
    from jenny.runtime.context import get_runtime_context
    from jenny.utils.helpers import sync_workspace_templates

    previous = get_runtime_context().workspace_dir
    root = tmp_path / "workspace"
    root.mkdir(parents=True, exist_ok=True)
    paths_mod.set_workspace_dir(str(root))
    sync_workspace_templates(root, silent=True)
    try:
        yield root
    finally:
        paths_mod.set_workspace_dir(str(previous) if previous else "")


@pytest.mark.parametrize("orchestrator", [True, False])
def test_the_prompt_never_names_a_tool_that_is_not_there(workspace, orchestrator):
    """Ogni nome di tool citato dal prompt esiste, o e nella lista dei voluti."""
    scope = "orchestrator" if orchestrator else "core"
    live = _tool_names_in_scope(scope)
    declared = _every_declared_tool_name()

    prompt = _prompt(workspace, orchestrator=orchestrator)
    cited = {m for m in _BACKTICKED.findall(prompt) if m in declared}

    unavailable = cited - live - MENTIONED_ON_PURPOSE
    assert not unavailable, (
        f"Lo scope '{scope}' non ha {sorted(unavailable)}, ma il prompt li nomina. "
        "O il tool va aggiunto allo scope, o la frase va condizionata, oppure — se "
        "la citazione serve a vietarli — vanno elencati in MENTIONED_ON_PURPOSE."
    )


def test_the_allowlist_does_not_rot(workspace):
    """Una voce di ``MENTIONED_ON_PURPOSE`` che non serve piu va tolta.

    Senza questo la lista diventa il posto dove si insabbiano i problemi:
    cresce, nessuno la pota, e il primo test smette di poter fallire.
    """
    declared = _every_declared_tool_name()
    cited: set[str] = set()
    for orchestrator in (True, False):
        prompt = _prompt(workspace, orchestrator=orchestrator)
        cited |= {m for m in _BACKTICKED.findall(prompt) if m in declared}

    stale = MENTIONED_ON_PURPOSE - cited
    assert not stale, f"Voci non piu citate da nessun prompt: {sorted(stale)}"


# -- l'inventario generato --------------------------------------------------


@pytest.mark.parametrize("orchestrator", [True, False])
def test_the_prompt_ends_with_the_generated_inventory(workspace, orchestrator):
    """Deve stare in fondo: fra due istruzioni in conflitto vince l'ultima."""
    prompt = _prompt(workspace, orchestrator=orchestrator)
    tail = prompt.rsplit("\n\n---\n\n", 1)[-1]

    assert tail.lstrip().startswith("# The tools you actually have")
    for name in _tool_names_in_scope("orchestrator" if orchestrator else "core"):
        assert name in tail


def test_a_missing_inventory_template_does_not_break_the_prompt(workspace):
    """Workspace di una versione vecchia: si perde l'inventario, non il prompt."""
    (workspace / "agent" / "tool_inventory.md").unlink()

    prompt = _prompt(workspace, orchestrator=True)

    assert "# The tools you actually have" not in prompt
    assert "Orchestrator Mode" in prompt


def test_no_inventory_when_the_caller_does_not_offer_one(workspace):
    """Chi costruisce un prompt senza registry (Dream, test) non cambia."""
    prompt = ContextBuilder(workspace, orchestrator=True).build_system_prompt(
        include_memory_recent_history=False
    )

    assert "# The tools you actually have" not in prompt


def _skills_section(workspace: Path, *, orchestrator: bool, tools: list[str]) -> str:
    prompt = ContextBuilder(workspace, orchestrator=orchestrator).build_system_prompt(
        include_memory_recent_history=False, available_tools=tools,
    )
    return prompt.split("\n# Skills\n", 1)[1].split("\n---\n", 1)[0]


def test_who_delegates_does_not_see_the_app_builders_guide(workspace):
    """Il 28/10/2026 sul s22 Jenny leggeva ``app-creator`` e ne seguiva le fasi a
    domande prima di ogni ``spawn`` dell'``app-builder``: 30-40 s e una conferma in piu'."""
    orchestrating = _skills_section(workspace, orchestrator=True, tools=["read_file", "spawn"])
    assert "**cron**" in orchestrating
    assert "app-creator" not in orchestrating
    alone = _skills_section(workspace, orchestrator=False, tools=["read_file", "app"])
    assert "**app-creator**" in alone
