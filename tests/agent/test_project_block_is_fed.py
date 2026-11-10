"""Il blocco di progetto arriva pieno a **entrambi** i prompt, o fallisce a voce.

Passo **T3.9** delle correzioni al taccuino.

``agent/project.md`` ha due chiamanti: ``ContextBuilder.build_system_prompt`` e
``SubagentManager._build_subagent_prompt``. Il secondo lo rendeva senza
``project_map`` e senza ``project_pages``, quindi le due sezioni di contenuto
— la mappa e le pagine — si rendevano **vuote**. In silenzio: Jinja valuta falso
un ``{% if %}`` su una variabile assente invece di sollevare.

Due costi, e il secondo e' il peggiore:

1. il subagent e' l'attore che **scrive** le pagine e cura la mappa, e lo faceva
   senza averle davanti;
2. una rinomina o un errore di battitura su uno dei due nomi in ``context.py``
   avrebbe cancellato le stesse sezioni dal prompt **principale**, sempre senza
   dire niente. La stessa trappola documentata su
   ``ContextBuilder._tool_predicate``.

**Perche' un test e non ``StrictUndefined``.** ``render_template`` carica i
template dal **workspace**, non dal package (``FileSystemLoader`` su
``get_workspace_path()``): sono file sincronizzati, che l'utente puo' avere
vecchi o modificati a mano. Con ``StrictUndefined`` un template rimasto a una
versione precedente — che nomina una variabile che il codice di oggi non passa
piu' — smetterebbe di rendersi: nel prompt principale il ``suppress(Exception)``
lo trasformerebbe nella **sparizione dell'intero blocco**, cioe' un guasto piu'
grande di quello che deve prevenire, e nel subagent in un run morto all'avvio.
E' anche piu' stretto: cade solo sui percorsi che un test esercita, e solo se
quella variabile viene davvero *usata* durante quel render.

Il cancello qui sotto e' l'opposto su tutte e tre le dimensioni: legge quel che i
template **dichiarano** (l'AST, non il render), lo confronta con quel che i
chiamanti **passano**, e vale per **entrambi** i prompt e per ogni rinomina in
uno dei due versi. Non tocca la produzione, quindi non puo' rompere un workspace
vecchio.
"""

from __future__ import annotations

import importlib
import pathlib
from unittest.mock import MagicMock

from jenny.agent.context import ContextBuilder
from jenny.agent.subagent import SubagentManager
from jenny.bus.queue import MessageBus
from jenny.providers.base import LLMProvider
from jenny.session.manager import SessionManager

_MAP = "# casa\n\n## Pagine\n\n- [[furgone]] — il Ducato\n"
_PAGES = {"furgone.md": "---\nstate: open\n---\n\n# Furgone\n\nDucato 2011, turbo da cambiare."}

_MAP_HEADING = "### The map, as it stands"
_PAGES_HEADING = "### The pages, as they stand"


def _wiki(root: pathlib.Path, name: str = "casa", *, filled: bool = True) -> pathlib.Path:
    """Una wiki e' una cartella che contiene ``wiki/`` — la definizione del picker."""
    project = root / "wikis" / name
    (project / "wiki").mkdir(parents=True, exist_ok=True)
    if filled:
        (project / "wiki" / "index.md").write_text(_MAP, encoding="utf-8")
        for page, body in _PAGES.items():
            (project / "wiki" / page).write_text(body, encoding="utf-8")
    return project


def _manager(root: pathlib.Path) -> SubagentManager:
    provider = MagicMock(spec=LLMProvider)
    provider.get_default_model.return_value = "test-model"
    return SubagentManager(
        provider=provider,
        workspace=root,
        bus=MessageBus(),
        model="test-model",
        max_tool_result_chars=16_000,
        stall_threshold_s=0.0,
        session_manager=SessionManager(root),
    )


def _content_sections(prompt: str) -> str:
    """Le due sezioni di contenuto del blocco, isolate da quel che le circonda."""
    if _MAP_HEADING not in prompt:
        return ""
    return prompt.split(_MAP_HEADING, 1)[1].split("\n## Depth", 1)[0]


# ── (a) il subagent le riceve, e nella stessa forma ───────────────────────


def test_the_subagent_inside_a_project_gets_the_map_and_the_pages(tmp_path) -> None:
    project = _wiki(tmp_path)

    prompt = _manager(tmp_path)._build_subagent_prompt(workspace=project)

    assert _MAP_HEADING in prompt
    assert _PAGES_HEADING in prompt
    assert "## Pagine" in prompt, "la mappa e' nominata ma non consegnata"
    assert "turbo da cambiare" in prompt, "le pagine sono nominate ma non consegnate"
    assert "Those are 1 of the project's 1 pages" in prompt


def test_the_two_sections_are_byte_identical_to_the_main_prompt(tmp_path) -> None:
    """**Stessa forma**, e il modo forte di dirlo e' byte per byte: il template e'
    uno, e i due chiamanti devono alimentarlo con gli stessi quattro valori. Una
    differenza qui vorrebbe dire che uno dei due ha ricominciato a calcolarseli.
    """
    project = _wiki(tmp_path)

    main = ContextBuilder(tmp_path).build_system_prompt(
        workspace=project, session_key="project:casa"
    )
    sub = _manager(tmp_path)._build_subagent_prompt(workspace=project)

    sections = _content_sections(main)
    assert len(sections) > 200, "le sezioni non si sono rese: il confronto sarebbe vuoto"
    assert sections == _content_sections(sub)


def test_the_counts_are_fed_together_with_the_text(tmp_path) -> None:
    """I due conteggi stanno **dentro** il ``{% if project_pages %}``: alimentare
    solo il testo fa rendere «Those are  of the project's  pages», che e' peggio
    della sezione assente perche' sembra un conteggio."""
    project = _wiki(tmp_path)
    for extra in ("tetto.md", "orto.md"):
        (project / "wiki" / extra).write_text(f"# {extra}\n\nx", encoding="utf-8")

    prompt = _manager(tmp_path)._build_subagent_prompt(workspace=project)

    assert "Those are 3 of the project's 3 pages" in prompt
    assert "Those are  of" not in prompt
    assert "the project's  pages" not in prompt


# ── (b) una variabile non alimentata e' un guasto, non una sezione vuota ──


def _declared_vars(name: str) -> set[str]:
    """I nomi che *name* legge, inclusi quelli dei template che include.

    Dall'AST e non da un render: quel che conta e' cosa il file **dichiara**, non
    cosa capita di attraversare con un certo insieme di argomenti.
    ``find_undeclared_variables`` non segue gli ``{% include %}``, quindi la
    camminata la fa questa funzione — altrimenti il cancello sul prompt del
    subagent salterebbe proprio ``agent/project.md``, che e' incluso.
    """
    from jinja2 import meta

    from jenny.utils.prompt_templates import _environment

    env = _environment()
    pending, seen, names = [name], set(), set()
    while pending:
        template = pending.pop()
        if template in seen:
            continue
        seen.add(template)
        tree = env.parse(env.loader.get_source(env, template)[0])
        names |= meta.find_undeclared_variables(tree)
        pending += [t for t in meta.find_referenced_templates(tree) if isinstance(t, str)]
    return names


def _fed_vars(monkeypatch, module: str) -> dict[str, set[str]]:
    """Per ogni template reso da *module*, i nomi che il chiamante gli passa."""
    mod = importlib.import_module(module)
    real = mod.render_template
    fed: dict[str, set[str]] = {}

    def spy(name: str, **kwargs):
        fed.setdefault(name, set()).update(kwargs)
        return real(name, **kwargs)

    monkeypatch.setattr(mod, "render_template", spy)
    return fed


def test_every_variable_the_two_templates_declare_is_fed(tmp_path, monkeypatch) -> None:
    """**Il cancello.** Se un template dichiara un nome che il suo chiamante non
    passa, Jinja rende la sezione vuota e non dice niente: qui invece si vede.

    Vale nei due versi — una variabile aggiunta al template e mai alimentata, e
    una rinominata nel codice e non nel template — e su **entrambi** i prompt,
    che e' il motivo per cui questo test esiste invece di un controllo sul solo
    percorso del subagent.
    """
    project = _wiki(tmp_path)
    context_fed = _fed_vars(monkeypatch, "jenny.agent.context")
    subagent_fed = _fed_vars(monkeypatch, "jenny.agent.subagent_prompt")

    ContextBuilder(tmp_path).build_system_prompt(
        workspace=project, session_key="project:casa"
    )
    _manager(tmp_path)._build_subagent_prompt(workspace=project)

    checks = (
        ("prompt principale", "agent/project.md", context_fed),
        ("prompt del subagent", "agent/subagent_system.md", subagent_fed),
    )
    for label, template, fed in checks:
        assert template in fed, f"{label}: {template} non e' stato reso, il test non misura"
        declared = _declared_vars(template)
        assert declared, f"{template} non dichiara niente: il cancello e' vuoto"
        missing = declared - fed[template]
        assert not missing, (
            f"{label}: {template} legge {sorted(missing)} e il chiamante non "
            f"gliel{'i' if len(missing) > 1 else 'o'} passa — Jinja rende la "
            f"sezione vuota e tace"
        )


def test_the_gate_would_notice_a_renamed_variable(tmp_path, monkeypatch) -> None:
    """Il cancello sa cadere: la stessa asserzione, con un nome che nessuno
    alimenta. Senza questo, un ``_declared_vars`` che tornasse vuoto per una
    ragione qualunque farebbe passare il test qui sopra per sempre."""
    project = _wiki(tmp_path)
    context_fed = _fed_vars(monkeypatch, "jenny.agent.context")

    ContextBuilder(tmp_path).build_system_prompt(
        workspace=project, session_key="project:casa"
    )

    declared = _declared_vars("agent/project.md") | {"project_mappa"}
    assert declared - context_fed["agent/project.md"] == {"project_mappa"}


# ── (c) niente pagine, niente intestazione vuota ──────────────────────────


def test_a_project_with_nothing_in_it_gets_no_empty_heading(tmp_path) -> None:
    """Una wiki appena creata e' muta, e il blocco deve dirlo tacendo: le due
    sezioni non si rendono affatto. Un'intestazione con niente sotto si legge
    come "qui non c'e' niente da sapere", che e' un'altra cosa."""
    project = _wiki(tmp_path, filled=False)

    prompt = _manager(tmp_path)._build_subagent_prompt(workspace=project)

    assert "# Project Folder" in prompt, "la pianta serve comunque: e' dove scrive"
    assert _MAP_HEADING not in prompt
    assert _PAGES_HEADING not in prompt
    assert "of the project's" not in prompt


def test_a_project_with_a_map_but_no_pages_only_shows_the_map(tmp_path) -> None:
    """Le due sezioni sono indipendenti: una mappa scritta a mano prima delle
    pagine e' il caso normale del primo giorno di un progetto."""
    project = _wiki(tmp_path, filled=False)
    (project / "wiki" / "index.md").write_text(_MAP, encoding="utf-8")

    prompt = _manager(tmp_path)._build_subagent_prompt(workspace=project)

    assert _MAP_HEADING in prompt
    assert _PAGES_HEADING not in prompt


def test_outside_a_project_neither_section_appears(tmp_path) -> None:
    """La radice dell'installazione non e' una wiki: nessuna delle due sezioni, e
    restano le regole del workspace."""
    prompt = _manager(tmp_path)._build_subagent_prompt(workspace=tmp_path)

    assert "# Project Folder" not in prompt
    assert _MAP_HEADING not in prompt
    assert _PAGES_HEADING not in prompt
    assert "Files you produce go under" in prompt
