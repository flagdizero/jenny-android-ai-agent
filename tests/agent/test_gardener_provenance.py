"""Una pagina non può certificare ciò che nessuno ha detto. **Fase 3, D1.**

Il caso che ha prodotto questo file è registrato sul dispositivo e riproducibile.
Il 24/08, nella wiki `viaggio-lento`:

* Jenny chiede «il telescopio te lo porti in macchina, **o quello resta a casa**?»
* l'utente risponde «il telescopio che cenrtra?» — una domanda, nessuna scelta
* la cattura scrive nel diario «Il telescopio non c'entra col viaggio — **resta a
  casa**», cioè l'opzione B della domanda di Jenny, come decisione dell'utente
* la passata la promuove a `state: decided` e la mappa la mette sotto «Decided»,
  che entra in **ogni** turno del progetto.

Nessuno dei due messaggi dell'utente contiene la parola «casa» — verificato
contandoli, non a occhio.

**La regola c'era già** in `agent/gardener.md` («only the user's own words … can
justify anything stronger»), e non era **rispettabile**: il giardiniere promuove
dal diario, dove una riga citata e una dedotta erano tipograficamente identiche.
La regola non è stata ignorata, era inapplicabile per costruzione.

**Cosa NON prova questo file, e va detto qui perché è la tentazione ovvia.** Non
prova che il marcatore dica il vero: quel bit lo dichiara un modello. Tre varianti
di «verifica la citazione contro le parole dell'utente» sono state provate su
questo stesso caso e cadono tutte; l'ultima boccia la fabbricazione **e** boccia
`fibra.md`, che registra una decisione vera e detta chiaramente, solo
parafrasata. La parafrasi è legittima e pervasiva, quindi nessun controllo a
livello di stringa le separa: chi vuole «rafforzare» questi test con un confronto
di stringhe boccerebbe proprio le parafrasi oneste.

Quel che il codice impone è la **conseguenza** del bit, e quella è meccanica.
"""

from __future__ import annotations

import pathlib

import pytest

from jenny.agent.gardener import _compose_write_guards
from jenny.agent.wiki_provenance import _page_frontmatter, _provenance_guard

JOURNAL = (
    "# 2026-08-24\n"
    "\n"
    "- 19:19 — [inferred] Il telescopio non c'entra col viaggio — resta a casa.\n"
    "- 19:20 — [said] La connessione la risolve con la fibra.\n"
    "- 19:21 — [recovered] Base Roma.\n"
    "- 19:22 — Una riga di prima che i marcatori esistessero.\n"
    # Il minuto misto, nell'ordine che fa danno: la riga detta **prima** di quella
    # dedotta. V. la sezione D13 in fondo al file.
    "- 19:23 — [said] Il furgone è un Ducato.\n"
    "- 19:23 — [inferred] Quindi il letto sta in fondo.\n"
    # E un minuto con due righe entrambe dell'utente: la metà che non va punita.
    "- 19:24 — [said] Si parte il 12.\n"
    "- 19:24 — [recovered] Rientro il 20.\n"
)


@pytest.fixture
def project(tmp_path: pathlib.Path) -> pathlib.Path:
    (tmp_path / "wiki").mkdir()
    (tmp_path / "raw" / "journal").mkdir(parents=True)
    (tmp_path / "raw" / "journal" / "20260824.md").write_text(JOURNAL, encoding="utf-8")
    return tmp_path


@pytest.fixture
def guard(project: pathlib.Path):
    return _provenance_guard(project.resolve(), (project / "wiki").resolve())


def _page(state: str, source: str, body: str = "Il contenuto.") -> str:
    return f"---\ntitle: X\nstate: {state}\nsource: {source}\n---\n\n# X\n\n{body}\n"


# ── Il caso vero, dai due lati ───────────────────────────────────────────────


def test_a_page_cannot_be_decided_on_a_line_the_assistant_concluded(project, guard) -> None:
    """Il telescopio del 24/08: è **questo** che non deve più poter succedere."""
    refusal = guard(project / "wiki" / "telescopio.md", _page("decided", "raw/journal/20260824.md#19:19"))

    assert refusal is not None
    assert "`[inferred]`" in refusal, "deve dire quale delle due cose è andata storta"
    assert "state: open" in refusal or "`open`" in refusal, "e cosa fare invece"


def test_a_page_can_be_decided_on_a_line_the_user_said(project, guard) -> None:
    """La fibra: la decisione **c'era**, ed era parafrasata.

    Questo test è il più importante del file, e non è ridondante col precedente:
    senza di lui un rifiuto può essere corretto per il motivo sbagliato — bocciare
    tutto — ed è esattamente l'errore in cui è caduta la terza variante scartata.
    """
    assert guard(project / "wiki" / "fibra.md", _page("decided", "raw/journal/20260824.md#19:20")) is None


def test_a_recovered_line_counts_as_said(project, guard) -> None:
    """Non è una gentilezza: una passata recupera **solo** fatti che l'utente ha
    detto e che la cattura ha perso. È il contratto del suo prompt, non una scelta
    che le si concede — e infatti la sua cassetta non riceve il parametro."""
    assert guard(project / "wiki" / "roma.md", _page("decided", "raw/journal/20260824.md#19:21")) is None


# ── Le tre vie di non-sapere, tutte fail-closed ──────────────────────────────


@pytest.mark.parametrize(
    ("source", "case"),
    [
        ("raw/journal/20260824.md", "il giorno nudo: punta a N righe, non a una"),
        ("raw/journal/20260824.md#23:59", "un'ora che nel file non c'è"),
        ("raw/journal/19990101.md#19:20", "un giorno che non esiste"),
        ("../../../etc/passwd#19:20", "fuori dal progetto"),
        ("", "nessuna source"),
    ],
)
def test_what_cannot_be_checked_cannot_be_decided(project, guard, source, case) -> None:
    """Il verso opposto — «se non riesco a controllare lascio passare» — *è* il
    difetto: quel che passerebbe è una certificazione, e una certificazione
    sbagliata resta scritta finché qualcuno non la nota."""
    refusal = guard(project / "wiki" / "p.md", _page("decided", source))

    assert refusal is not None, case
    assert "#" in refusal, "il rifiuto deve dire come ancorare, non solo che manca qualcosa"


def test_an_unmarked_line_cannot_be_decided_either(project, guard) -> None:
    """Le righe scritte prima che i marcatori esistessero sono «non si sa», non «detto».

    È il verso che rende inutile una migrazione: le pagine già sul disco non si
    toccano, e solo le promozioni **future** hanno bisogno di un marcatore.
    """
    assert guard(project / "wiki" / "p.md", _page("decided", "raw/journal/20260824.md#19:22")) is not None


# ── D13: un minuto tiene più righe, e il minuto nudo non dice quale ─────────
#
# **Non era un difetto di tracciabilità, era il buco della guardia.** L'ancoraggio
# è al minuto, quindi `#19:23` combacia con *tutte* le righe di quel minuto, e la
# prima versione tornava alla prima che trovava. Con `[said]` appesa prima e
# `[inferred]` dopo — l'ordine che un turno normale produce — una pagina che citava
# il fatto dedotto **passava** come `decided`: D1 rientrato dalla finestra, in
# silenzio e in un verso solo.
#
# E il minuto misto non è esotico: da Fase 4 la cattura fa una chiamata per fatto,
# quindi un turno in cui l'utente dice una cosa e Jenny ne deduce la conseguenza
# scrive esattamente quelle due righe allo stesso `HH:MM`.


def test_the_bare_minute_cannot_certify_when_that_minute_is_mixed(project, guard) -> None:
    """Il buco, dal lato in cui faceva danno. **La regressione da tenere.**

    Prima del 25/08 questa pagina passava: la guardia trovava `[said]` — la *prima*
    riga di 19:23 — e non guardava oltre. La pagina rivendica il fatto dedotto,
    che è la seconda.
    """
    refusal = guard(
        project / "wiki" / "letto.md", _page("decided", "raw/journal/20260824.md#19:23")
    )

    assert refusal is not None


def test_and_the_refusal_asks_for_the_missing_half_not_a_new_anchor(project, guard) -> None:
    """La riparazione è un'**aggiunta**, e la frase lo deve dire.

    Detto «non punta a una riga», il modello riscriverebbe il minuto — l'unica
    parte che qui è giusta. Un rifiuto su cui non si può agire è un rifiuto che si
    riprova identico.
    """
    refusal = guard(
        project / "wiki" / "letto.md", _page("decided", "raw/journal/20260824.md#19:23")
    )

    assert "#19:23.2" in refusal, "deve mostrare l'ancoraggio da scrivere, non descriverlo"
    assert "line not found" not in refusal
    assert "does not point at one journal line" not in refusal


def test_the_ordinal_says_which_line_and_the_verdict_follows_it(project, guard) -> None:
    """Le due metà dello stesso minuto, e devono decidere in modo opposto.

    Insieme sono la prova che l'ordinale **si legge** invece di essere ignorato: un
    solo caso passerebbe anche con «accetta tutto ciò che ha un ordinale».
    """
    said = guard(
        project / "wiki" / "furgone.md", _page("decided", "raw/journal/20260824.md#19:23.1")
    )
    inferred = guard(
        project / "wiki" / "letto.md", _page("decided", "raw/journal/20260824.md#19:23.2")
    )

    assert said is None, "la prima riga di quel minuto l'utente l'ha detta"
    assert inferred is not None
    assert "`[inferred]`" in inferred, "ora si sa quale riga è: il rifiuto è quello preciso"


def test_a_minute_where_every_line_is_the_user_s_needs_no_ordinal(project, guard) -> None:
    """La metà che non va punita, ed è la ragione per cui l'ambiguo non è «più di
    una riga» ma «più di una riga e non tutte dell'utente».

    A 19:24 ci sono due righe, `[said]` e `[recovered]`: quale delle due la pagina
    intenda non cambia la risposta, quindi il minuto nudo basta. Senza questo test
    «rifiuta ogni minuto con più di una riga» sarebbe verde — e romperebbe ogni
    `source:` già scritta su un turno che ha catturato due fatti.
    """
    assert guard(
        project / "wiki" / "date.md", _page("decided", "raw/journal/20260824.md#19:24")
    ) is None


@pytest.mark.parametrize(
    ("anchor", "case"),
    [
        ("#19:23.3", "oltre il numero di righe di quel minuto"),
        ("#19:23.0", "gli ordinali contano da 1, non da 0"),
        ("#19:20.2", "un ordinale su un minuto che ha una riga sola"),
    ],
)
def test_an_ordinal_that_points_nowhere_is_not_a_free_pass(project, guard, anchor, case) -> None:
    """Un errore di conto si ripara contando, quindi è «non risolve» e non «ambiguo».

    Il verso conta: se un ordinale fuori range ricadesse sul minuto nudo, scriverne
    uno qualsiasi diventerebbe il modo di aggirare il controllo su un minuto misto.
    """
    refusal = guard(
        project / "wiki" / "p.md", _page("decided", f"raw/journal/20260824.md{anchor}")
    )

    assert refusal is not None, case


def test_the_ordinal_also_works_where_it_is_not_needed(project, guard) -> None:
    """`.1` su un minuto con una riga sola non è un errore: è la forma generale, e
    un modello che la scrive sempre non deve trovarsi rifiutato per questo."""
    assert guard(
        project / "wiki" / "fibra.md", _page("decided", "raw/journal/20260824.md#19:20.1")
    ) is None


# ── Dove il gancio non ha opinioni ──────────────────────────────────────────


@pytest.mark.parametrize("state", ["open", "hypothesis"])
def test_a_page_that_claims_nothing_passes(project, guard, state) -> None:
    """`open` non è un castigo: è quel che la pagina vale, e la pagina si scrive."""
    assert guard(project / "wiki" / "p.md", _page(state, "raw/journal/20260824.md#19:19")) is None


def test_the_map_passes(project, guard) -> None:
    """La mappa non ha `state:`, e il gancio non ha opinioni sulla prosa."""
    assert guard(project / "wiki" / "index.md", "---\ntitle: X\n---\n\n# X\n\n- [[a]]\n") is None


def test_a_write_outside_the_pages_passes(project, guard) -> None:
    """Il diario non passa da qui — e la cassetta gliene vieta comunque la scrittura."""
    assert guard(project / "raw" / "journal" / "20260824.md", _page("decided", "x")) is None


# ── La composizione, che è il vincolo dichiarato dal codice ──────────────────


def test_the_first_guard_wins_and_the_second_never_runs() -> None:
    """Lo slot pre-scrittura è **uno** (`write_size_guard`), e la docstring di
    `build_tools` dice che montarne un secondo gemello è stato rifiutato di
    proposito. Composti, l'ordine è la cessione del passo per prima: quando
    l'utente è rientrato quel rifiuto è l'unica cosa vera da dire, e `aborted` va
    riempito con **quel** motivo.
    """
    ran: list[str] = []

    def first(_path, _text):
        ran.append("first")
        return "cede il passo"

    def second(_path, _text):
        ran.append("second")
        return "provenienza"

    composed = _compose_write_guards(first, second)

    assert composed(pathlib.Path("x"), "y") == "cede il passo"
    assert ran == ["first"], "il secondo non deve nemmeno girare: il primo rifiuto decide"


def test_composing_runs_them_all_when_nobody_refuses() -> None:
    ran: list[str] = []
    composed = _compose_write_guards(
        lambda _p, _t: ran.append("a"),  # type: ignore[func-returns-value]
        lambda _p, _t: ran.append("b"),  # type: ignore[func-returns-value]
    )

    assert composed(pathlib.Path("x"), "y") is None
    assert ran == ["a", "b"]


def test_composing_a_lone_guard_returns_it_unwrapped() -> None:
    """Fuori dal gateway `_yield_to_user_guard` è `None`: la composizione non deve
    trasformare quel caso in un gancio che gira a vuoto a ogni scrittura."""
    def only(_path, _text):
        return None

    assert _compose_write_guards(None, only) is only
    assert _compose_write_guards(None, None) is None


# ── Il parser, sui casi che una frontmatter vera produce ─────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("---\nstate: decided\nsource: a.md#1\n---\n", {"state": ["decided"], "source": ["a.md#1"]}),
        ('---\nstate: "decided"\n---\n', {"state": ["decided"]}),
        ("---\nstate:   decided   \n---\n", {"state": ["decided"]}),
        ("# Nessuna frontmatter\n\nstate: decided\n", {}),
        ("---\nstate: open\nstate: decided\n---\n", {"state": ["open", "decided"]}),
    ],
)
def test_the_frontmatter_parser_keeps_every_value(text, expected) -> None:
    """L'ultimo caso è il solo che vale una riga, e la prima versione l'aveva
    sbagliato: prendeva il **primo** valore «come farebbe YAML». È una via
    d'uscita, non una compatibilità — `state: open` in cima e `state: decided`
    sotto passavano il gancio, mentre un parser vero (dove fra chiavi duplicate
    vince l'**ultima**) legge `decided`."""
    assert {k: v for k, v in _page_frontmatter(text).items() if k in expected or expected == {}} == expected


def test_the_strongest_claim_decides_whatever_the_order(project, guard) -> None:
    """Il bypass che il parser a valore singolo apriva, provato dai due versi."""
    inferred = "raw/journal/20260824.md#19:19"
    for head, tail in (("open", "decided"), ("decided", "open")):
        page = f"---\ntitle: X\nstate: {head}\nstate: {tail}\nsource: {inferred}\n---\n\n# X\n"
        assert guard(project / "wiki" / "p.md", page) is not None, f"{head} poi {tail}"


def test_every_source_has_to_hold_not_just_one(project, guard) -> None:
    """Due sorgenti di cui una dedotta sono una pagina decisa a metà, cioè decisa."""
    page = (
        "---\ntitle: X\nstate: decided\n"
        "source: raw/journal/20260824.md#19:20\n"
        "source: raw/journal/20260824.md#19:19\n"
        "---\n\n# X\n"
    )
    assert guard(project / "wiki" / "p.md", page) is not None


# ── Che il gancio sia *montato*, che è un'altra cosa dal funzionare ──────────
#
# I test qui sopra provano `_provenance_guard`. Non provano che `run_gardener`
# gliela dia in mano alla passata: due mutazioni sono **sopravvissute** a tutto il
# file — togliere la guardia dal punto in cui si compone, e invertire l'ordine
# della composizione. Cioè l'intera funzionalità si poteva staccare e la suite
# restava verde. Questi due test girano il codice vero, dal `run_gardener` in giù.

from types import SimpleNamespace  # noqa: E402

from jenny.agent.gardener import GardenerStore, run_gardener  # noqa: E402
from jenny.security.workspace_access import (  # noqa: E402
    WorkspaceScope,
    bind_workspace_scope,
    reset_workspace_scope,
    workspace_sandbox_status,
)

_DECIDED_ON_AN_INFERRED_LINE = (
    "---\ntitle: Telescopio\nstate: decided\nsource: raw/journal/20260824.md#19:19\n---\n\n"
    "# Telescopio\n\nNon c'entra col viaggio — resta a casa.\n"
)


class _WritingThroughTheRealToolbox:
    """Un agente che prova davvero a scrivere la pagina, con i tool che riceve.

    È il punto: `process_direct` riceve `tools=` da `run_gardener`, quindi usare
    **quella** cassetta è l'unico modo di provare che la guardia ci è arrivata.
    Un test che costruisce la guardia da sé prova la guardia e non il montaggio —
    ed è esattamente il buco che due mutazioni sopravvissute hanno mostrato.
    """

    def __init__(self, workspace, *, user_in_flight: str | None = None) -> None:
        self.context = SimpleNamespace(memory=None, timezone="Europe/Rome")
        self.sessions = SimpleNamespace(sessions_dir=workspace / "sessions")
        self.sessions.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.results: list[str] = []
        self._in_flight = user_in_flight

    def active_session_keys(self):
        return (self._in_flight,) if self._in_flight else ()

    async def take_snapshot(self, trigger: str) -> bool:
        return True

    def evict_pruned_sessions(self, keys) -> None:
        pass

    def forget_file_reads(self, key) -> None:
        pass

    async def process_direct(self, prompt: str, **kwargs):
        tool = kwargs["tools"].get("write_file")
        self.results.append(
            await tool.execute(
                path="wikis/viaggio/wiki/telescopio.md", content=_DECIDED_ON_AN_INFERRED_LINE
            )
        )
        return SimpleNamespace(
            metadata={"_stop_reason": "completed"}, usage={}, content="NOTHING TO FLAG"
        )


def _installation_scope(workspace):
    """Lo scope con cui una passata gira davvero: quello **dell'installazione**.

    Non un dettaglio di fixture. La base dei percorsi relativi la dà lo scope del
    turno (`current_tool_workspace`), e una passata interna non lega il progetto —
    è la stessa ragione per cui `JournalAppendTool` si fa **iniettare** la radice
    invece di dedurla. Legare qui il progetto farebbe risolvere
    `wikis/viaggio/wiki/x.md` dentro `wikis/viaggio/`, cioè un test che fallisce su
    un percorso che in produzione funziona.
    """
    return bind_workspace_scope(
        WorkspaceScope(
            project_path=workspace,
            access_mode="restricted",
            restrict_to_workspace=True,
            sandbox_status=workspace_sandbox_status(
                restrict_to_workspace=True, workspace=workspace
            ),
            writable=True,
        )
    )


def _project_with_a_journal(workspace):
    project = workspace / "wikis" / "viaggio"
    (project / "wiki").mkdir(parents=True)
    (project / "raw" / "journal").mkdir(parents=True)
    (project / "raw" / "journal" / "20260824.md").write_text(JOURNAL, encoding="utf-8")
    (project / "wiki" / "index.md").write_text("---\ntitle: V\n---\n\n# V\n", encoding="utf-8")
    (project / "AGENTS.md").write_text("---\nid: abc\nsummary: v\n---\n\n# V\n", encoding="utf-8")
    store = GardenerStore.for_project(workspace, "viaggio")
    assert store is not None
    return project, store


async def test_the_pass_really_receives_the_guard(tmp_path) -> None:
    """Il montaggio, non la guardia: senza questo test la si può staccare in silenzio."""
    project, store = _project_with_a_journal(tmp_path)
    agent = _WritingThroughTheRealToolbox(tmp_path)

    token = _installation_scope(tmp_path)
    try:
        await run_gardener(agent, store)
    finally:
        reset_workspace_scope(token)

    assert agent.results, "la passata non ha nemmeno provato a scrivere"
    assert "`[inferred]`" in agent.results[0], agent.results[0]
    assert not (project / "wiki" / "telescopio.md").exists(), "e la pagina non è su disco"


async def test_when_the_user_is_back_that_refusal_wins(tmp_path) -> None:
    """L'ordine della composizione, misurato dove è deciso e non su ganci finti.

    Quando l'utente è rientrato quello è l'unico motivo vero, e `aborted` va
    riempito con **quello**: una passata fermata per la cessione del passo che si
    sente dire «provenienza» racconta una storia diversa da quella per cui è stata
    fermata.
    """
    _, store = _project_with_a_journal(tmp_path)
    agent = _WritingThroughTheRealToolbox(tmp_path, user_in_flight="project:viaggio")

    token = _installation_scope(tmp_path)
    try:
        await run_gardener(agent, store)
    finally:
        reset_workspace_scope(token)

    assert agent.results
    assert "giving way" in agent.results[0], agent.results[0]
    assert "[inferred]" not in agent.results[0], "il secondo gancio non deve nemmeno girare"


def test_the_anchor_the_prompt_teaches_is_the_anchor_the_code_accepts() -> None:
    """Il montaggio fra la prosa e la grammatica, e senza è il difetto tipico.

    La regola su `source:` vive in `agent/gardener.md` e mostra la forma per
    esempio; il codice la impone con `_ANCHOR_RE`. Sono la stessa cosa detta due
    volte, quindi possono divergere — e la divergenza è muta nel verso peggiore: il
    modello scrive l'ancoraggio che il suo prompt gli insegna e la guardia lo
    rifiuta come «non risolve», su ogni pagina, senza che nessun test cada.

    Legge gli esempi **dal template**, non da un elenco qui: un elenco scritto a
    mano proverebbe l'accordo fra sé stesso e il codice.
    """
    import re

    import jenny
    from jenny.agent.wiki_provenance import _ANCHOR_RE

    template = (
        pathlib.Path(jenny.__file__).parent / "templates" / "agent" / "gardener.md"
    ).read_text(encoding="utf-8")
    taught = set(re.findall(r"\.md#(\d{2}:\d{2}(?:\.\d+)?)", template))

    assert len(taught) >= 2, "gli esempi non sono stati letti: l'asserzione sarebbe vuota"
    for anchor in sorted(taught):
        assert _ANCHOR_RE.match(anchor), anchor


@pytest.mark.parametrize("lead", ["\n", "﻿", "  \n\n", "﻿\n"])
def test_a_blank_line_or_a_bom_before_the_fence_does_not_hide_the_claim(
    project, guard, lead
) -> None:
    """Il lint e ``notebook_ops`` leggono la frontmatter anche dopo un BOM o una
    riga vuota: se la guardia non la vede, la pagina passa e si legge ``decided``."""
    page = lead + _page("decided", "raw/journal/20260824.md#19:19")
    assert guard(project / "wiki" / "telescopio.md", page) is not None
