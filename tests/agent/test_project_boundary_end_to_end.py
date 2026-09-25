"""Il confine, guardato da un capo all'altro — e va in **un senso solo**.

Passo **6** di ``roadmap/progetti-passi.md``, la prova che chiude la v1.

Gli anelli sono provati uno per uno altrove: il prompt in
``test_project_prompt_contract.py``, la scrittura in
``test_project_write_boundary.py``, le viste, la sola lettura, i nomi dei file.
Questo file prova la **catena**, e lo fa perché il difetto del 21/08 —
``_effective_session_key`` con ``UNIFIED_SESSION_KEY`` cablato dentro — non lo
prendeva nessun test: *«tutti provavano gli anelli e non la catena»*.

Le due direzioni non sono simmetriche, ed è il punto:

- **da un progetto verso un altro:** niente. Un turno di ``patreon`` non deve
  contenere una riga di ``etf``, né il suo nome;
- **da un progetto verso il personale:** niente. Il diario non deve contenere
  niente di nessun progetto;
- **dal personale verso un progetto:** chi sei viaggia. ``SOUL.md`` e ``USER.md``
  entrano, perché Jenny resta Jenny anche al lavoro.

L'asimmetria è la decisione del 21/08 in una riga: *chi sei viaggia, dove altro
lavori no.*

**``MEMORY.md`` stava nel primo elenco fino al 24/08, e non ci sta più.** La riga
di confine non è cambiata: era la classificazione di quel file a essere sbagliata.
Contate una per una, le sue voci servono ognuna a **un** progetto — cioè sono
«dove altro lavori», non «chi sei», che resta in ``SOUL.md`` e ``USER.md``. Al suo
posto un progetto riceve una riga che dice dov'è il file, perché un file che il
modello non sa esistere è, dal suo punto di vista, cancellato.
"""

from __future__ import annotations

import pathlib

import pytest

from jenny.agent.context import ContextBuilder
from jenny.session.keys import is_personal_session_key, is_project_session_key


@pytest.fixture
def install(tmp_path: pathlib.Path) -> pathlib.Path:
    """Un'installazione con due progetti e i file personali, tutti riconoscibili.

    Radice **per test** e non quella della suite: ``conftest`` ne monta una sola
    per tutta la sessione, e scriverci dentro fa trovare i propri file a chi gira
    dopo (già successo il 22/08, con un test caduto a tre cartelle di
    distanza).
    """
    for name, secret in (("patreon", "PAROLA-PATREON"), ("etf", "PAROLA-ETF")):
        project = tmp_path / "wikis" / name
        (project / "wiki").mkdir(parents=True)
        (project / "AGENTS.md").write_text(
            # Lo scope di ``etf`` porta il marcatore dell'elenco: e' quel che il
            # blocco ``## Wikis`` mostra alla chat personale e a nessun progetto.
            f"---\nsummary: {name}{' PAROLA-RUBRICA' if name == 'etf' else ''}\n---\n\n"
            f"# {name}\n\n{secret}\n", encoding="utf-8"
        )
    (tmp_path / "SOUL.md").write_text("Sono Jenny. PAROLA-ANIMA\n", encoding="utf-8")
    (tmp_path / "USER.md").write_text("Si chiama Marta. PAROLA-UTENTE\n", encoding="utf-8")
    memory = tmp_path / "memory"
    memory.mkdir()
    (memory / "MEMORY.md").write_text("# Memoria\n\nPAROLA-MEMORIA\n", encoding="utf-8")
    return tmp_path


def _prompt(install: pathlib.Path, name: str | None) -> str:
    """Il prompt di sistema di un turno di progetto, o della chat personale."""
    builder = ContextBuilder(install)
    if name is None:
        return builder.build_system_prompt(session_key="unified:default")
    return builder.build_system_prompt(
        workspace=install / "wikis" / name, session_key=f"project:{name}"
    )


# ── Da un progetto verso un altro: niente ────────────────────────────────


def test_a_project_turn_carries_nothing_of_the_other_project(install: pathlib.Path) -> None:
    prompt = _prompt(install, "patreon")
    assert "PAROLA-PATREON" in prompt, "le sue istruzioni ci devono essere"
    assert "PAROLA-ETF" not in prompt, "quelle dell'altro no"


def test_a_project_turn_does_not_even_name_the_other(install: pathlib.Path) -> None:
    """Non è solo il contenuto: è l'*elenco*.

    «Claude Code non ti parla degli altri tuoi repository». L'elenco delle
    wiki è il catalogo di dove sta la roba, e dentro un progetto
    la scoperta è già finita — col chip l'hai scelto tu.
    """
    prompt = _prompt(install, "patreon")
    assert "etf" not in prompt.lower()
    assert "PAROLA-RUBRICA" not in prompt


def test_the_two_projects_do_not_leak_into_each_other(install: pathlib.Path) -> None:
    """Simmetrico, e vale per costruzione: nessuno dei due è privilegiato."""
    assert "PAROLA-PATREON" not in _prompt(install, "etf")
    assert "PAROLA-ETF" not in _prompt(install, "patreon")


# ── Dal personale verso un progetto: chi sei viaggia ─────────────────────


@pytest.mark.parametrize(
    ("marker", "file"),
    [("PAROLA-ANIMA", "SOUL.md"), ("PAROLA-UTENTE", "USER.md")],
)
def test_who_she_is_travels_into_a_project(
    install: pathlib.Path, marker: str, file: str
) -> None:
    """Senza questi, legare uno scope le faceva perdere personalità e memoria.

    Era il difetto del passo 1.2, e succedeva **senza un errore e senza una riga
    di log**.

    ``MEMORY.md`` era il terzo caso di questa parametrizzazione fino al 24/08, ed
    è uscito: non è identità (v. il test qui sotto). Ciò che quel difetto del 1.2
    riguardava — la personalità e chi è l'utente — sta in questi due, quindi la
    regressione che questo test protegge resta protetta.
    """
    assert marker in _prompt(install, "patreon"), f"{file} non è arrivato nel progetto"


def test_the_long_term_memory_does_not_travel_but_says_where_it_is(
    install: pathlib.Path,
) -> None:
    """L'inventario resta fuori, e lascia un indirizzo.

    Le due asserzioni sono una sola decisione presa da due lati: se il puntatore
    sparisse insieme al contenuto, il file diventerebbe irraggiungibile in pratica
    — e ``recall`` non lo copre, perché legge ``memory/archive/``, il tier freddo,
    non il file vivo.
    """
    from jenny.agent.memory import MemoryStore

    prompt = _prompt(install, "patreon")

    assert "PAROLA-MEMORIA" not in prompt, "il contenuto è «dove altro lavori»"
    assert MemoryStore(install).get_memory_pointer_context() in prompt


def test_the_personal_chat_keeps_the_directory(install: pathlib.Path) -> None:
    """La rubrica non è sbagliata: è fuori posto dentro un progetto.

    Nella conversazione personale serve, ed è dove deve restare — altrimenti
    questo passo avrebbe rotto la scoperta invece di stringere un confine.
    """
    prompt = _prompt(install, None)
    assert "PAROLA-RUBRICA" in prompt


def test_the_personal_chat_carries_no_project_instructions(install: pathlib.Path) -> None:
    """L'altro verso dell'asimmetria: il personale non eredita il lavoro."""
    prompt = _prompt(install, None)
    assert "PAROLA-PATREON" not in prompt
    assert "PAROLA-ETF" not in prompt


# ── Il diario personale è di chi parla, non di dove si lavora ────────────


def test_only_the_personal_conversation_can_become_long_term_memory() -> None:
    """Dream consolida da una whitelist, e ha **un solo** membro.

    È scritto come whitelist e non come negazione di "sessione interna" apposta:
    una chiave ``project:`` non è interna e non è personale, e con la negazione
    ogni progetto sarebbe finito nel diario.
    """
    assert is_personal_session_key("unified:default") is True
    for key in ("project:patreon", "project:etf", "cron:update_check", "dream:x"):
        assert is_personal_session_key(key) is False, f"{key} non può diventare memoria"


def test_a_project_key_is_neither_personal_nor_internal() -> None:
    """Il terzo tipo esiste, ed è riconosciuto in un punto solo."""
    key = "project:patreon"
    assert is_project_session_key(key) is True
    assert is_personal_session_key(key) is False
    from jenny.session.keys import is_internal_session_key

    assert is_internal_session_key(key) is False, (
        "una sessione di progetto non è interna: si vede negli elenchi e i suoi token "
        "sono dell'utente"
    )
