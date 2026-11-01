"""La riga nel prompt della sola lettura, e perché qui se la guadagna.

Passo **4.4** del piano dei progetti.

Al passo 3 abbiamo deciso di **non** mettere niente nel prompt per il rifiuto dei
promemoria, e il criterio era: una riga nel blocco se la guadagna la regola che
sbatteresti addosso di continuo e che ti costringe a ripianificare. Un promemoria
è raro e sta in piedi da solo; **scrivere è quel che Jenny fa a ogni turno**, e
scoprire a metà lavoro che non può le fa buttare la chiamata *e* rifare il piano.

Applicato a due casi, lo stesso criterio decide al contrario. È la prova che non
era una scusa per non scrivere — e ``test_the_cron_refusal_stayed_out_of_the_prompt``
è quel che impedisce che la simmetria si perda per strada.

Il difetto che questi test esistono per non ripetere è stato trovato **sul
telefono** il 22/08: l'agente principale sapeva di essere in sola lettura, ha
delegato a un subagent, e quello ha pianificato e scritto sei file — tutti
rifiutati. Il confine ha tenuto e non è stato creato niente, ma il lavoro è stato
buttato: ``agent/subagent_system.md`` non aveva il blocco. **L'esecuzione eredita
lo scope, la conoscenza no.**
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny.utils.prompt_templates import render_template

SRC = Path(__file__).resolve().parents[2] / "jenny"
TEMPLATES = SRC / "templates" / "agent"


@pytest.fixture(autouse=True)
def _templates(monkeypatch):
    """Rende i template leggibili senza un workspace configurato."""
    monkeypatch.setattr(
        "jenny.utils.prompt_templates._get_templates_root",
        lambda: TEMPLATES.parent,
    )
    from jenny.utils import prompt_templates

    prompt_templates._environment.cache_clear()
    yield
    prompt_templates._environment.cache_clear()


def _block() -> str:
    return render_template("agent/readonly.md")


def _flat() -> str:
    """Il blocco su una riga sola, minuscolo.

    Il testo è a capo a 100 colonne come tutti i template, quindi una frase da
    cercare cade a metà su un newline: asserire sul testo grezzo darebbe
    fallimenti che dipendono da dove è andata a finire la riga.
    """
    return " ".join(render_template("agent/readonly.md").lower().split())


# ── Cosa dice ────────────────────────────────────────────────────────────

# **Le regole, e la frase con cui oggi sono scritte — in un posto solo.**
#
# Questo file esiste per un blocco di prompt il cui mestiere è *essere
# riscritto*: la sola cosa che si può fare quando il modello sbaglia sul telefono
# è cambiare le parole. Con le frasi sparse in sei asserzioni, una riscrittura
# costava sei modifiche e ogni fallimento diceva «la stringa non c'è» invece di
# «la regola non c'è» (T8.7, I13). Qui la frase sta accanto al nome della regola
# e alla ragione per cui la regola esiste, quindi una riscrittura è **una**
# modifica e il fallimento nomina quel che si è perso.
#
# Il nome e la ragione sono il contratto; la frase è come lo diciamo oggi. Chi
# riscrive il template cambia la colonna di destra. Chi vuole *togliere* una
# riga di questa tabella sta togliendo una regola, e allora è la colonna di
# mezzo che deve smettere di essere vera.
_READONLY_RULES: tuple[tuple[str, str, str], ...] = (
    (
        "descrivi invece di scrivere",
        "la descrizione della modifica *è* il prodotto del turno, non il preambolo di un "
        "tentativo: senza, il modello risponde «non posso» e l'utente resta a mani vuote",
        "describe the change",
    ),
    (
        "e dillo al posto del tentativo",
        "l'altra metà della stessa frase: «descrivi» da solo si legge come «descrivi, poi prova»",
        "instead of making it",
    ),
    (
        "nessun altro percorso vale la pena",
        "la prima strada che un modello prova quando un tool dice no. **Non è un universale "
        "sul codice**: T4.16 ha misurato che la porta dei thread nudi resta aperta, e ha "
        "tenuto la sottostringa cambiando quel che afferma — da «non esiste un giro» a "
        "«prendi il rifiuto come definitivo». La sottostringa è il cardine di quel lavoro: "
        "va riscritta sapendolo, non per far tornare un test",
        "no other path or other tool",
    ),
    (
        "delegare non solleva il vincolo",
        "la seconda strada, e la sola misurata: il 22/08 sul telefono l'agente ha delegato "
        "a un subagent, che ha pianificato e scritto sei file, tutti rifiutati. Il confine "
        "ha tenuto, il lavoro è stato buttato",
        "delegating does not lift it",
    ),
    (
        "chi può riaccenderla",
        "la terza strada è chiedere di nuovo: senza dire di chi è l'interruttore, il "
        "modello lo chiede due volte",
        "switch above the composer",
    ),
    (
        "leggere resta intatto",
        "senza questo «sola lettura» si legge come «muta», e il turno non fa nemmeno "
        "quello che potrebbe fare",
        "reading is untouched",
    ),
    (
        "parlare resta intatto",
        "l'altra metà: un blocco che vieta tutto vieta anche di rispondere",
        "messaging the user",
    ),
)


@pytest.mark.parametrize(
    ("rule", "why", "phrase"), _READONLY_RULES, ids=[r[0] for r in _READONLY_RULES]
)
def test_the_block_states_every_rule_it_has_to_state(rule: str, why: str, phrase: str) -> None:
    assert phrase in _flat(), f"{rule}: {why}"


def test_no_rule_is_stated_twice_by_accident() -> None:
    """La tabella è l'elenco delle regole, non un elenco di stringhe.

    Due righe con la stessa frase sarebbero una regola contata due volte, e
    toglierne una lascerebbe il test verde: la tabella smetterebbe di essere il
    posto dove si legge cosa il blocco deve dire.
    """
    phrases = [phrase for _, _, phrase in _READONLY_RULES]
    assert len(set(phrases)) == len(phrases)


def test_the_block_stays_small() -> None:
    """Si paga a ogni turno in cui l'interruttore è giù.

    Stesso meccanismo di ``agent/project.md`` e ``agent/scheduling.md``: il
    dettaglio non ha un posto dove andare, quindi il tetto è ciò che tiene il
    blocco un blocco.
    """
    rendered = _block()
    assert len(rendered) <= 1500, (
        f"agent/readonly.md è {len(rendered)} caratteri: la modalità si spiega in un "
        "blocco, non in un manuale"
    )


# ── Dove arriva ──────────────────────────────────────────────────────────


def test_it_is_a_system_template_so_a_correction_arrives() -> None:
    """``agent/**`` si riscrive a ogni avvio; un file dell'utente una volta sola."""
    from jenny.utils.android_assets import _SYSTEM_PROMPT_TEMPLATES

    assert "agent/readonly.md" in _SYSTEM_PROMPT_TEMPLATES


def test_the_subagent_gets_the_same_block_included_not_copied() -> None:
    """Il difetto del 22/08. Due copie divergerebbero alla prima correzione."""
    sub = (TEMPLATES / "subagent_system.md").read_text(encoding="utf-8")
    assert "{% include 'agent/readonly.md' %}" in sub
    assert "{% if readonly %}" in sub


def test_the_subagent_is_told_only_when_the_turn_is_read_only() -> None:
    src = (SRC / "agent" / "subagent.py").read_text(encoding="utf-8")
    assert "readonly=not (" in src, "il valore deve venire dallo scope legato, non da un default"
    assert "current_workspace_scope()" in src




def test_a_turn_with_no_bound_scope_is_writable() -> None:
    """Test, ispezione e sessioni interne costruiscono il prompt fuori da un turno.

    Là non c'è nessuno scope legato — che vuol dire scrivibile, non il
    contrario: un default chiuso avrebbe messo il blocco nel prompt di cron
    e Dream.
    """
    from jenny.agent.context import _turn_is_writable

    assert _turn_is_writable() is True


def test_read_only_removes_the_capture_rule(tmp_path) -> None:
    """T2: la cattura è una scrittura, quindi in sola lettura non si chiede.

    Non c'è un secondo divieto da scrivere: ce n'è uno di **ordine**. Dal 22/08
    ``agent/project.md`` dice di scrivere nel diario prima di rispondere, e
    ``agent/readonly.md`` dice che niente su questo telefono può cambiare. Le due
    frasi si contraddicono per costruzione, e quella che vince è **l'ultima** —
    per la stessa ragione per cui il blocco readonly sta in fondo (v. il commento
    in ``context.py``: deve vincere anche su un ``AGENTS.md`` di progetto che
    dica di scrivere).

    L'ordine dei blocchi resta come era (readonly in fondo, così vince su un
    ``AGENTS.md`` di progetto che dica di scrivere): quello che cambia è che in
    sola lettura non c'è più niente da far vincere.
    """
    from jenny.agent.context import ContextBuilder
    from jenny.security.workspace_access import (
        WorkspaceScope,
        bind_workspace_scope,
        reset_workspace_scope,
        workspace_sandbox_status,
    )

    project = tmp_path / "wikis" / "viaggio"
    (project / "wiki").mkdir(parents=True)
    scope = WorkspaceScope(
        project_path=project,
        access_mode="restricted",
        restrict_to_workspace=True,
        sandbox_status=workspace_sandbox_status(
            restrict_to_workspace=True, workspace=project
        ),
        writable=False,
    )
    token = bind_workspace_scope(scope)
    try:
        prompt = ContextBuilder(tmp_path).build_system_prompt(
            workspace=project, session_key="project:viaggio"
        )
    finally:
        reset_workspace_scope(token)

    assert "# Project Folder" in prompt and "# Read-Only Turn" in prompt
    # **La regola di cattura non c'è affatto**, ed è la correzione del collaudo
    # del 22/08: con la regola presente e il divieto più in basso — cioè
    # nell'ordine che vince — l'agente ha provato due volte a catturare, e al
    # secondo tentativo ha istruito il subagent a cercare una scappatoia
    # (`apply_patch`). L'ordine risolve la contraddizione per il modello; non
    # gli toglie la voglia di provarci. Meglio non dare l'ordine.
    assert "before you answer" not in prompt
    assert "Do not ask permission" not in prompt
    # La pianta però resta: in sola lettura si legge e si descrive, e per farlo
    # bisogna sapere com'è fatta la cartella.
    assert "raw/journal/YYYYMMDD.md" in prompt


def test_a_writable_turn_has_the_capture_rule_and_no_read_only_block(tmp_path) -> None:
    """Il rovescio del test sopra, sullo stesso progetto: scrivibile, la regola di
    cattura c'è e il blocco di sola lettura no. Insieme i due provano che il
    prompt principale segue la scrivibilità del turno (``_turn_is_writable``),
    cosa che prima si fissava leggendo il sorgente di ``context.py``."""
    from jenny.agent.context import ContextBuilder
    from jenny.security.workspace_access import (
        WorkspaceScope,
        bind_workspace_scope,
        reset_workspace_scope,
        workspace_sandbox_status,
    )

    project = tmp_path / "wikis" / "viaggio"
    (project / "wiki").mkdir(parents=True)
    scope = WorkspaceScope(
        project_path=project,
        access_mode="restricted",
        restrict_to_workspace=True,
        sandbox_status=workspace_sandbox_status(
            restrict_to_workspace=True, workspace=project
        ),
        writable=True,
    )
    token = bind_workspace_scope(scope)
    try:
        prompt = ContextBuilder(tmp_path).build_system_prompt(
            workspace=project, session_key="project:viaggio"
        )
    finally:
        reset_workspace_scope(token)

    assert "# Project Folder" in prompt
    assert "# Read-Only Turn" not in prompt
    assert "before you answer" in prompt


# ── La simmetria col passo 3 ─────────────────────────────────────────────


def test_the_cron_refusal_stayed_out_of_the_prompt() -> None:
    """Lo stesso criterio, applicato al passo 3, decide al contrario.

    Se un domani qualcuno mettesse anche quella regola in un blocco, il criterio
    smetterebbe di essere un criterio e tornerebbe a essere un'abitudine.
    """
    for name in ("project.md", "readonly.md", "scheduling.md"):
        text = (TEMPLATES / name).read_text(encoding="utf-8").lower()
        assert "cannot schedule" not in text, (
            f"{name}: il rifiuto dei promemoria dentro un progetto vive nel tool "
            "(deciso il 22/08), non nel prompt"
        )
