"""``/init`` scrive *il* file di istruzioni del progetto, non un secondo.

Passo **2.5** del piano dei progetti.

Il difetto che questi test esistono per non ripetere è stato trovato sul telefono
il 22/08, e nessun test l'aveva preso perché non ce n'erano. La prima versione
del prompt diceva soltanto «scrivi ``AGENTS.md``». Lanciata su ``zz-prova-due``,
che ha ancora il vecchio ``CLAUDE.md``, il modello ha constatato — correttamente
— che ``AGENTS.md`` non c'era, e **ne ha creato un secondo accanto**: cioè
proprio lo stato che i lettori (passo 2.3) sanno solo disambiguare e non
risolvere, e che da lì in poi fa uscire un warning a ogni turno di quella wiki.

Lo scaffolder quella guardia ce l'aveva già; ``/init`` no, perché lì a scrivere
è il modello e l'unica leva è il prompt. La correzione non è stata renderlo più
insistente: è stato **calcolare** la destinazione — ``_expand_project_init``
risolve la cartella, chiede a ``wiki_schema_file`` quale file esiste, e passa il
percorso già deciso al template. Il test che conta qui sotto è
``test_the_prompt_names_the_file_that_already_exists``: senza,
``{{ instructions_path }}`` può sparire dal template e tutto resta verde.

L'altra decisione fissata qui è che ``/init`` **non è un comando del router**: un
handler risponde e basta, e non farebbe girare l'agente, che è tutto il punto.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from support.agent import make_loop

from jenny.agent.loop import PROJECT_INIT_COMMAND, AgentLoop
from jenny.bus.events import InboundMessage


def _loop(tmp_path: Path) -> AgentLoop:
    return make_loop(tmp_path, context_window_tokens=None, bare=True)


def _msg(content: str = PROJECT_INIT_COMMAND) -> InboundMessage:
    return InboundMessage(
        channel="websocket", chat_id="default", sender_id="u", content=content
    )


def _wiki(root: Path, name: str, schema: str | None) -> Path:
    """Una wiki sotto ``wikis/``, col file di istruzioni che le si vuole dare."""
    project = root / "wikis" / name
    (project / "wiki").mkdir(parents=True, exist_ok=True)
    if schema:
        (project / schema).write_text(f"---\nsummary: {name}\n---\n", encoding="utf-8")
    return project


# ── La destinazione si calcola ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("present", "expected"),
    [
        (None, "AGENTS.md"),          # wiki nuova: il nome di oggi
        ("AGENTS.md", "AGENTS.md"),
        # Dal passo 7.5 ``CLAUDE.md`` non è più un file di istruzioni: la
        # migrazione lo rinomina a ogni avvio, quindi `/init` scrive dove i
        # lettori guarderanno. Il difetto originale — creare un *secondo* file
        # accanto — resta chiuso dal fatto che la destinazione la calcola il
        # codice, non il modello.
        ("CLAUDE.md", "AGENTS.md"),
    ],
)
async def test_the_prompt_names_the_file_that_already_exists(
    tmp_path: Path, present: str | None, expected: str
) -> None:
    loop = _loop(tmp_path)
    project = _wiki(tmp_path, "prova", present)

    expanded = await loop._expand_project_init(_msg(), "project:prova")

    assert expanded is not None
    assert str(project / expected) in expanded.content, (
        "il percorso deve arrivare deciso dal codice: lasciato al modello, su una wiki con "
        "`CLAUDE.md` constata che `AGENTS.md` manca e ne crea un secondo (visto il 22/08)"
    )
    other = "CLAUDE.md" if expected == "AGENTS.md" else "AGENTS.md"
    assert str(project / other) not in expanded.content


async def test_with_both_files_it_writes_the_one_the_reader_will_use(
    tmp_path: Path,
) -> None:
    """Una wiki già rotta non si peggiora: si scrive quello che entra nel prompt."""
    loop = _loop(tmp_path)
    project = _wiki(tmp_path, "prova", "AGENTS.md")
    (project / "CLAUDE.md").write_text("vecchio\n", encoding="utf-8")

    expanded = await loop._expand_project_init(_msg(), "project:prova")

    assert expanded is not None
    assert str(project / "AGENTS.md") in expanded.content
    assert str(project / "CLAUDE.md") not in expanded.content


# ── Espansione, non comando ───────────────────────────────────────────────


async def test_inside_a_project_the_literal_never_reaches_the_agent(
    tmp_path: Path,
) -> None:
    loop = _loop(tmp_path)
    _wiki(tmp_path, "prova", None)

    expanded = await loop._expand_project_init(_msg(), "project:prova")

    assert expanded is not None
    assert expanded.content.strip() != PROJECT_INIT_COMMAND
    assert "instructions file" in expanded.content, "è il prompt reso, non un segnaposto"
    # Tutto il resto del messaggio resta quello che era: cambia solo il testo.
    assert expanded.channel == "websocket" and expanded.chat_id == "default"


async def test_outside_a_project_it_refuses_instead_of_running_a_turn(
    tmp_path: Path,
) -> None:
    loop = _loop(tmp_path)
    published: list[str] = []

    async def capture(message) -> None:
        published.append(message.content)

    loop.bus.publish_outbound = capture  # type: ignore[assignment]

    expanded = await loop._expand_project_init(_msg(), "unified:default")

    assert expanded is None, "None vuol dire «già risposto»: nessun turno parte"
    assert published and PROJECT_INIT_COMMAND in published[0]
    assert "notebook" in published[0].lower(), "il rifiuto deve dire dove funziona"


def test_it_is_not_a_router_command(tmp_path: Path) -> None:
    """Due proprietari sarebbero due comportamenti da tenere allineati.

    Un handler del router *risponde*: non fa girare l'agente, che qui è tutto il
    punto — leggere la wiki e scrivere il suo file. Se qualcuno lo registrasse,
    il router lo intercetterebbe **prima** dell'espansione e `/init` tornerebbe a
    essere una frase.
    """
    loop = _loop(tmp_path)

    assert not loop.commands.is_dispatchable_command(PROJECT_INIT_COMMAND)
    assert not loop.commands.is_priority(PROJECT_INIT_COMMAND)


# ── Il contorno ───────────────────────────────────────────────────────────


def test_the_prompt_is_a_system_template_so_a_correction_arrives() -> None:
    """Stessa ragione del blocco del 2.1, e dell'``AGENTS.md`` di sistema prima.

    ``agent/**`` si riscrive a ogni avvio; un file dell'utente si crea una volta
    sola. Fuori da quella lista, una correzione al prompt di ``/init`` non
    arriverebbe mai su un telefono aggiornato da mesi.
    """
    from jenny.utils.android_assets import _SYSTEM_PROMPT_TEMPLATES

    assert "agent/project_init.md" in _SYSTEM_PROMPT_TEMPLATES


def test_help_lists_it() -> None:
    from jenny.command.builtin import build_help_text

    assert PROJECT_INIT_COMMAND in build_help_text()


@pytest.mark.parametrize("orchestrator", [False, True])
async def test_the_orchestrator_is_told_who_writes_the_file(
    tmp_path: Path, orchestrator: bool
) -> None:
    """In modalità orchestratore la chat non ha un tool che scrive un file:
    «Then write it» da solo ordina una chiamata che lì non esiste."""
    loop = _loop(tmp_path)
    loop.orchestrator_mode = orchestrator
    _wiki(tmp_path, "prova", None)

    expanded = await loop._expand_project_init(_msg(), "project:prova")

    assert expanded is not None
    assert ("`notebook-editor`" in expanded.content) is orchestrator
