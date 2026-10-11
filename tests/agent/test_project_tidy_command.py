"""``/tidy`` riordina la wiki **in questa conversazione**, con le misure in mano.

Il caso che l'ha chiesto, il 26/08/2026. L'utente ha parlato dentro
``wikis/sartoria`` e poi ha detto «sistema un po' la wiki, se necessario spezza i
concetti». Il risultato è stato buono, e per una ragione che nessuna passata
periodica può avere: le pagine erano nel turno, la giornata di discussione era nel
turno, e l'utente era lì a decidere. Quel che è mancato è tutto qui:

- la passata si è **inventata la forma** dell'operazione (il manuale c'era,
  ``compile``, e il blocco di progetto scoraggiava di leggerlo) — chiuso a parte,
  nella skill e nel puntatore;
- e ha spezzato una pagina **a occhio**: 5.870 caratteri, cioè *sotto* il tetto di
  6.000. Buon giudizio, zero misura.

Da cui le due decisioni che questo file fissa.

**``/tidy`` espande, non lancia.** È la forma di ``/init``
(``dataclasses.replace`` sul messaggio in arrivo), non quella di ``/gardener``,
che fa girare un run interno con sessione e cassetta sue. Lanciato come passata
sarebbe un giardiniere con un altro nome, e avrebbe buttato via l'unica cosa che
lo rende migliore di una passata.

**Le misure le porta il codice.** Mappa contro il suo tetto, pagine contro il
budget di iniezione, dai lettori del giardiniere e non da un secondo conto.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from jenny.agent.loop import PROJECT_TIDY_COMMAND, AgentLoop
from jenny.bus.events import InboundMessage
from jenny.bus.queue import MessageBus


def _loop(tmp_path: Path) -> AgentLoop:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    return AgentLoop(
        bus=MessageBus(), provider=provider, workspace=tmp_path, model="test-model"
    )


def _msg(content: str = PROJECT_TIDY_COMMAND) -> InboundMessage:
    return InboundMessage(
        channel="websocket", chat_id="default", sender_id="u", content=content
    )


def _wiki(root: Path, name: str = "sartoria", *, map_text: str = "# Sartoria\n\n## Pages\n") -> Path:
    project = root / "wikis" / name
    (project / "wiki").mkdir(parents=True, exist_ok=True)
    (project / "wiki" / "index.md").write_text(map_text, encoding="utf-8")
    return project


def _flat(text: str) -> str:
    """Il prompt con gli spazi normalizzati.

    Il template è prosa incolonnata a mano: una frase di cinque parole può
    finire a cavallo di due righe, e un ``in`` cadrebbe alla prima
    riformattazione invece che alla prima regola rimossa. Quel che si sorveglia è
    la frase, non dove finisce la riga.
    """
    return re.sub(r"\s+", " ", text)


async def _prompt(loop: AgentLoop, key: str = "project:sartoria") -> str:
    expanded = await loop._expand_project_tidy(_msg(), key)
    assert expanded is not None
    return _flat(expanded.content)


def _page(project: Path, rel: str, chars: int) -> Path:
    path = project / "wiki" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\nstate: open\n---\n\n# P\n\n" + "x" * chars, encoding="utf-8")
    return path


# ── Espande, e resta in questa conversazione ─────────────────────────────────


async def test_it_rewrites_the_turn_instead_of_launching_a_pass(tmp_path: Path) -> None:
    """**La proprietà che distingue ``/tidy`` da ``/gardener``.**

    Non basta che torni un prompt: deve tornare *questo* messaggio con un altro
    contenuto, così il turno eredita lo scope, il confine di scrittura, il blocco
    di progetto e la cronologia della conversazione. Un giorno in cui qualcuno lo
    trasformasse in un run interno — la strada che sembra più pulita — questo test
    è l'unico che se ne accorgerebbe.
    """
    loop = _loop(tmp_path)
    _wiki(tmp_path)
    original = _msg()

    expanded = await loop._expand_project_tidy(original, "project:sartoria")

    assert expanded is not None
    assert expanded.content != original.content, "il comando deve diventare un prompt"
    assert expanded.channel == original.channel
    assert expanded.chat_id == original.chat_id
    assert expanded.sender_id == original.sender_id


async def test_outside_a_project_it_says_where_to_go(tmp_path: Path) -> None:
    """Un rifiuto che dice **dove**, non solo che qui non si può.

    Stessa forma del rifiuto di ``/init`` e di ``journal_append``: il progetto si
    sceglie dal chip sopra il campo del messaggio, e chi non lo sa non lo indovina
    da «non funziona qui».

    Dal 31/08/2026 la frase **è** quella di :mod:`jenny.command.scope`, la stessa
    che il router dà a un comando fuori dal suo scope: era scritta a mano qui, un
    secondo rifiuto a mano in ``cmd_gardener``, e una terza copia della regola nel
    client. Il test confronta con quella funzione invece di ricopiarne il testo —
    così cambiarla non richiede di ricordarsi di questo file.
    """
    from jenny.command.scope import refusal, spec_for_line

    loop = _loop(tmp_path)
    published: list = []
    loop.bus.publish_outbound = _capture(published)

    expanded = await loop._expand_project_tidy(_msg(), "websocket:default")

    assert expanded is None, "None = ha già risposto, il turno non parte"
    assert len(published) == 1
    spec = spec_for_line("/tidy")
    assert spec is not None
    assert published[0].content == refusal(spec)
    assert "not a project" in published[0].content
    assert "chip above the composer" in published[0].content


def _capture(sink: list):
    async def _publish(message) -> None:
        sink.append(message)

    return _publish


# ── Le misure ────────────────────────────────────────────────────────────────


async def test_an_oversized_page_is_named_with_its_size(tmp_path: Path) -> None:
    """Il difetto del 26/08, all'incontrario: qui il numero non si indovina.

    La pagina grossa arriva nominata **con la sua misura** e col motivo per cui la
    misura conta — che a quel punto non è più «sembra lunga».
    """
    loop = _loop(tmp_path)
    project = _wiki(tmp_path)
    _page(project, "grossa.md", 7000)
    _page(project, "piccola.md", 100)

    prompt = await _prompt(loop)

    assert "`grossa.md`" in prompt
    assert "over the 6,000 budget" in prompt
    assert "skipped whole" in prompt
    assert "`piccola.md`" not in prompt, (
        "l'elenco è delle pagine oltre il tetto: nominarle tutte è la lista dei nomi "
        "che la mappa dà già"
    )


async def test_with_no_oversized_page_it_says_so_and_still_warns(tmp_path: Path) -> None:
    """Il contro-limite, e non è simmetrico.

    «Nessuna pagina è oltre» non vuol dire «non c'è niente da spezzare»: il budget
    è **complessivo** e nell'ordine della mappa, quindi una pagina solo *grande*
    affama quelle elencate dopo. Un messaggio che si fermasse a «tutto a posto»
    autorizzerebbe la conclusione sbagliata — che è precisamente quel che è
    successo il 26/08, dove la pagina spezzata era sotto il tetto.
    """
    loop = _loop(tmp_path)
    project = _wiki(tmp_path)
    _page(project, "piccola.md", 100)

    prompt = await _prompt(loop)

    assert "over the 6,000 budget" not in prompt
    assert "starves the pages the map lists after it" in prompt


@pytest.mark.parametrize(
    ("map_chars", "over"),
    [(120, False), (2600, True)],
    ids=["mappa dentro il tetto", "mappa oltre il tetto"],
)
async def test_the_prune_paragraph_only_appears_when_the_map_is_over(
    tmp_path: Path, map_chars: int, over: bool
) -> None:
    """Stessa regola del prompt del giardiniere: su una mappa che sta nel suo tetto
    un ordine di potare è un invito a potare per niente — e potare muove prosa
    dentro le pagine, cioè non è gratis."""
    loop = _loop(tmp_path)
    _wiki(tmp_path, map_text="# Sartoria\n\n" + "y" * map_chars)

    prompt = await _prompt(loop)

    assert ("It is over." in prompt) is over
    assert ("Prune prose, never entries" in prompt) is over


async def test_the_numbers_come_from_the_readers_that_charge_them(tmp_path: Path) -> None:
    """I due tetti nel prompt sono **quelli veri**, importati da dove si pagano.

    Un letterale nel template sarebbe un numero che dice al modello una soglia
    mentre l'iniettore ne applica un'altra: la stessa forma del difetto che T3.14
    ha chiuso fra la regola SPLIT e l'inventario della passata.
    """
    from jenny.agent.gardener import MAP_TARGET_CHARS, page_ceiling

    loop = _loop(tmp_path)
    _wiki(tmp_path)

    prompt = await _prompt(loop)

    assert f"against a ceiling of {MAP_TARGET_CHARS:,}" in prompt
    assert f"inject {page_ceiling():,} characters" in prompt


# ── Il puntatore alla ricetta ────────────────────────────────────────────────


async def test_it_sends_the_turn_to_the_recipe_that_exists(tmp_path: Path) -> None:
    """Il prompt non ripete il manuale: manda a leggerlo, e col titolo esatto.

    Se qualcuno rinomina la sezione nella skill e non qui, il comando manda a
    cercare una sezione che non c'è — e il silenzio è identico a quello del 26/08,
    quando il manuale c'era e nessuno l'ha aperto.
    """
    skill = (
        Path(__file__).resolve().parents[2]
        / "jenny" / "skills" / "llm-wiki" / "SKILL.md"
    )
    loop = _loop(tmp_path)
    _wiki(tmp_path)

    prompt = await _prompt(loop)
    title = "`compile` in a **project** wiki (notebook layout)"

    assert title in prompt
    assert title in _flat(skill.read_text(encoding="utf-8"))


async def test_it_carries_the_two_things_a_pass_cannot_have(tmp_path: Path) -> None:
    """Le due deroghe che valgono **solo** perché l'utente è nel turno.

    Sono la ragione per cui questa operazione non è il giardiniere con un altro
    nome, e vanno dette: senza, il turno applica le regole di una passata cieca
    pur avendo davanti chi può decidere.
    """
    loop = _loop(tmp_path)
    _wiki(tmp_path)

    prompt = await _prompt(loop)

    assert "**Ask.**" in prompt
    assert "You may read any page in full" in prompt
    # E il freno, che con l'incoraggiamento dell'utente in mano è il primo a cedere.
    assert "add and move, do not rewrite" in prompt


async def test_a_tidy_that_moves_nothing_is_allowed(tmp_path: Path) -> None:
    """Il rischio proprio di un comando: fa *sembrare* dovuto un lavoro.

    ``/gardener`` ce l'ha già scritto («a pass that promotes nothing has done its
    job correctly») e qui serve di più, perché qui il lavoro l'ha chiesto una
    persona e non un orologio — il che rende «non c'era niente da fare» una
    risposta che costa qualcosa da dare.
    """
    loop = _loop(tmp_path)
    _wiki(tmp_path)

    prompt = await _prompt(loop)

    assert "A tidy that moves nothing is a correct outcome" in prompt


# ── Il contorno ──────────────────────────────────────────────────────────────


def test_it_is_not_a_router_command(tmp_path: Path) -> None:
    """Due proprietari sarebbero due comportamenti da tenere allineati.

    Un handler del router *risponde* e basta: non fa girare l'agente, che qui è
    tutto il punto. Se qualcuno lo registrasse, il router intercetterebbe `/tidy`
    **prima** dell'espansione e tornerebbe a essere una frase.
    """
    loop = _loop(tmp_path)

    assert not loop.commands.is_dispatchable_command(PROJECT_TIDY_COMMAND)
    assert not loop.commands.is_priority(PROJECT_TIDY_COMMAND)


def test_the_prompt_is_a_system_template_so_a_correction_arrives() -> None:
    """``agent/**`` si riscrive a ogni avvio; un file dell'utente si crea una volta.

    Fuori da quella lista una correzione a questo prompt non arriverebbe mai su un
    telefono aggiornato da mesi — e il modo peggiore di sbagliare, perché un file
    *nuovo* arriva e uno *corretto* no.
    """
    from jenny.utils.android_assets import _SYSTEM_PROMPT_TEMPLATES

    assert "agent/tidy.md" in _SYSTEM_PROMPT_TEMPLATES


def test_help_lists_it() -> None:
    from jenny.command.builtin import build_help_text

    assert PROJECT_TIDY_COMMAND in build_help_text()


def test_the_expansion_is_wired_into_the_consume_loop() -> None:
    """L'unico punto di questo file che guarda il **codice** e non il comportamento.

    Il ramo che chiama l'espansione vive nel corpo del ciclo di ``run()``, che per
    esercitarlo vorrebbe un loop vero con bus, provider e sessioni. Senza
    nessuna asserzione, però, togliere quel ramo lascia tutto verde e `/tidy`
    arriva al modello **come testo**: lo interpreterebbe a caso e ristrutturerebbe
    comunque, senza nessuna delle misure che sono il senso del comando. Il
    controllo è grossolano di proposito — dice che il ramo c'è, non che funzioni —
    ed è il complemento del resto del file, che dice che funziona ma non che è
    collegato.
    """
    from pathlib import Path as _Path

    source = (_Path(__file__).resolve().parents[2] / "jenny" / "agent" / "loop.py").read_text(
        encoding="utf-8"
    )

    assert "_expand_project_tidy(msg, effective_key)" in source
    assert "raw == PROJECT_TIDY_COMMAND" in source


# ── I due layout ─────────────────────────────────────────────────────────────
#
# Il difetto trovato **misurando i progetti veri** prima di provare il comando: su
# nove wiki del telefono la maggioranza è in layout ricerca (`concepts/`,
# `entities/`, `summaries/`), e il prompt mandava tutte alla sezione *notebook*.
# Sarebbe stato lo stesso difetto che questo comando esiste per chiudere — mandare
# il turno alla metà sbagliata del manuale — ripetuto un livello più in su. E non
# è accademico: su una wiki di ricerca un `sources:` a lista è la forma **giusta**,
# e una fusione è permessa dopo conferma.


async def test_a_notebook_gets_the_notebook_section(tmp_path: Path) -> None:
    loop = _loop(tmp_path)
    project = _wiki(tmp_path)
    _page(project, "camminata.md", 100)

    prompt = await _prompt(loop)

    assert "notebook** layout" in prompt
    assert "`compile` in a **project** wiki (notebook layout)" in prompt
    assert "add and move, do not rewrite" in prompt


async def test_a_research_wiki_gets_the_five_numbered_steps(tmp_path: Path) -> None:
    """E soprattutto: gli si dice che la sezione dell'altro layout **non** si applica.

    Nominare la ricetta giusta non basta, perché la sezione sbagliata è nello
    stesso file, subito sotto, e la sua regola più forte — «`source:` è un valore
    solo, mai una lista» — contraddice quel che il layout ricerca prescrive.
    """
    loop = _loop(tmp_path)
    project = _wiki(tmp_path)
    _page(project, "concepts/istamina.md", 100)

    prompt = await _prompt(loop)

    assert "research** layout" in prompt
    assert "the five numbered steps of `compile` as written" in prompt
    assert "do not apply its rules here" in prompt
    assert "add and move, do not rewrite" not in prompt
    # Al posto di quel freno, il freno che la ricetta ricerca ha davvero.
    assert "confirm a split or a merge with them before writing it" in prompt


@pytest.mark.parametrize("rel", ["concepts/x.md", "entities/y.md", "summaries/z.md"])
async def test_any_of_the_three_folders_declares_the_research_layout(
    tmp_path: Path, rel: str
) -> None:
    """Le tre cartelle sono quelle del lint (``research_pages``), non due su tre.

    Una wiki che ha solo ``entities/`` è di ricerca esattamente come una che ha
    solo ``concepts/``, e il discriminante qui è una copia dichiarata di quello:
    se le liste divergono, il comando e il lint dicono due layout diversi della
    stessa cartella.
    """
    loop = _loop(tmp_path)
    project = _wiki(tmp_path)
    _page(project, rel, 100)

    prompt = await _prompt(loop)

    assert "research** layout" in prompt


async def test_the_measurements_are_in_both_layouts(tmp_path: Path) -> None:
    """Il tetto per turno **non** è una regola di layout, ed è il motivo per cui il
    comando serve su tutte e due: l'iniettore non guarda in che cartella sta una
    pagina. Su ``alpinismo`` (layout ricerca, misurata il 26/08) sono nove pagine
    che nessuna conversazione può leggere."""
    loop = _loop(tmp_path)
    project = _wiki(tmp_path)
    _page(project, "concepts/grossa.md", 7000)

    prompt = await _prompt(loop)

    assert "research** layout" in prompt
    assert "`concepts/grossa.md`" in prompt
    assert "over the 6,000 budget" in prompt


# ── La forma del testo, misurata come la rende la produzione ──────────────────


@pytest.mark.parametrize(
    ("kind", "pages", "map_text"),
    [
        ("taccuino, tutto a posto", ["camminata.md"], "# X\n\n## Pages\n"),
        ("taccuino, mappa oltre", ["camminata.md"], "# X\n\n" + "y" * 2600),
        ("ricerca, pagine oltre", ["concepts/grossa.md"], "# X\n\n## Pages\n"),
    ],
)
async def test_no_heading_or_bullet_is_glued_to_the_line_before_it(
    tmp_path: Path, kind: str, pages: list[str], map_text: str
) -> None:
    """**Il difetto visto sul telefono, e la ragione per cui va misurato qui.**

    Il 26/08 il prompt è arrivato al modello con ``2,000. It fits.- **The pages**``
    e ``thing to do here.## What you have``. L'avevo guardato renderizzato e mi
    sembrava a posto: l'avevo renderizzato con un ``Environment`` costruito a
    mano, **senza i flag della produzione** — ``jenny/utils/prompt_templates.py``
    monta ``trim_blocks=True``, che mangia il newline subito dopo un tag di
    blocco. È la stessa forma del difetto del 25/08 (``capture=True`` mancante):
    un prompt misurato con argomenti — qui con un *ambiente* — diverso da quello
    che la produzione usa davvero.

    Da cui la regola di questo test: passa da ``_expand_project_tidy``, cioè dal
    ``render_template`` vero, e guarda il testo **non normalizzato** — è l'unica
    asserzione del file a cui gli spazi interessano.
    """
    loop = _loop(tmp_path)
    project = _wiki(tmp_path, map_text=map_text)
    for rel in pages:
        _page(project, rel, 7000)

    expanded = await loop._expand_project_tidy(_msg(), "project:sartoria")

    assert expanded is not None
    glued = [
        line for line in expanded.content.splitlines()
        if ".## " in line or ".- **" in line or ".- `" in line
    ]
    assert not glued, f"{kind}: righe incollate da trim_blocks: {glued}"
    # E ogni titolo ha una riga vuota davanti, che è come si legge un titolo.
    lines = expanded.content.splitlines()
    orphans = [
        line for i, line in enumerate(lines)
        if line.startswith("## ") and i > 0 and lines[i - 1].strip()
    ]
    assert not orphans, f"{kind}: titoli senza riga vuota davanti: {orphans}"
