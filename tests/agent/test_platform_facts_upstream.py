"""I fatti di piattaforma stanno nei template, non nella memoria di un device.

Una misura sul Titan 2 ha trovato 3.144 caratteri di documentazione del *nostro
codice* dentro ``SOUL.md``: il confine dello workspace, l'allowlist di
``python_exec``, cosa ``apply_patch`` non sa fare. Nessuno di quei caratteri
veniva dal template — l'agente aveva fatto reverse engineering dell'app in cui
gira e se l'era annotato, install per install, a proprie spese.

L'argomento decisivo non è il costo, è la *portata*: la riga sull'exec del
subagent ``operator`` è un consiglio **per il subagent operator**, che
``SOUL.md`` non lo vede mai. Spostarla non la rende solo più economica, la fa
arrivare.

Questi test tengono i fatti dove arrivano, e tengono chiusa la strada per cui
si riaccumulano:

* i fatti sono nel template il cui pubblico li usa;
* ``platform-notes`` — la destinazione che il review pass indica per una riga di
  piattaforma senza gemello a monte — **non** è una skill bundlata. Le skill
  bundlate si riestraggono a ogni boot senza ``skip_existing``: scriverci dentro
  è perdita di dati garantita al riavvio successivo;
* il routing di ``agent/dream.md`` non manda più i vincoli di runtime in
  ``SOUL.md``, e ``Never delete`` non li protegge più.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from jenny.utils.android_assets import _SKILLS_MANIFEST, _SYSTEM_PROMPT_TEMPLATES
from jenny.utils.helpers import load_bundled_template

_JENNY = Path(__file__).resolve().parents[2] / "jenny"


def _template(name: str) -> str:
    text = load_bundled_template(name)
    assert text is not None, f"template bundlato mancante: {name}"
    return text


# ---------------------------------------------------------------------------
# 1. I fatti di piattaforma stanno nel template del loro pubblico
# ---------------------------------------------------------------------------

# (template, etichetta del fatto, regex che deve trovarlo).
# Le regex sono volutamente lasche sulla formulazione e strette sul *contenuto*:
# devono sopravvivere a una riscrittura in prosa e cadere se il fatto sparisce.
_PLATFORM_FACTS: list[tuple[str, str, str]] = [
    # --- platform_policy.md: confine e ambiente ---
    (
        "agent/platform_policy.md",
        "il confine dello workspace rifiuta anche l'ENUMERAZIONE",
        r"os\.listdir.*os\.scandir.*glob|enumerat",
    ),
    (
        "agent/platform_policy.md",
        "l'esenzione Chaquopy è automatica, l'agente non deve nominarla",
        r"Chaquopy runtime extract root",
    ),
    (
        "agent/platform_policy.md",
        "solo stdlib, niente pip, niente install a runtime",
        r"[Ss]tandard library only",
    ),
    (
        "agent/platform_policy.md",
        "niente pip: un pacchetto terzo non si installa",
        r"no pip",
    ),
    (
        "agent/platform_policy.md",
        "le macchine remote si raggiungono solo con i tool SSH",
        r"SSH tools",
    ),
    # --- tool_contract.md: il contratto dei tool, per l'agente principale ---
    (
        "agent/tool_contract.md",
        "l'allowlist di python_exec è una allowlist vera",
        r"allowlist",
    ),
    (
        "agent/tool_contract.md",
        "i moduli rifiutati, per nome",
        r"`subprocess`.*`urllib`",
    ),
    (
        "agent/tool_contract.md",
        "`sys` è disponibile, ma come proxy con `.modules` filtrato",
        r"`sys`.*proxy.*`\.modules`|proxy whose `\.modules`",
    ),
    (
        "agent/tool_contract.md",
        "working_dir è un argomento della CHIAMATA, os.chdir è rifiutato",
        r"os\.chdir` is refused",
    ),
    (
        "agent/tool_contract.md",
        "os.getcwd() riporta working_dir e i path relativi si risolvono di lì",
        r"os\.getcwd\(\)` reports it",
    ),
    (
        "agent/tool_contract.md",
        "un modulo importato da working_dir si scarica a fine chiamata",
        r"unloaded when the call ends",
    ),
    (
        "agent/tool_contract.md",
        "apply_patch fa solo replace/add: cancellare richiede python_exec",
        r"`replace` and `add` only",
    ),
    (
        "agent/tool_contract.md",
        "web_fetch renderizza in un browser: i plain-text tornano vuoti",
        r"renders the URL in a real browser",
    ),
    (
        "agent/tool_contract.md",
        "per un URL non-HTML si usa http_get dentro python_exec",
        r"`http_get` builtin inside `python_exec`",
    ),
    (
        "agent/tool_contract.md",
        "l'output di web_fetch è troncato e marcato untrusted",
        r'"truncated": true.*"untrusted": true',
    ),
    (
        "agent/tool_contract.md",
        "ripetere la stessa lookup esterna viene bloccato",
        r"repeated external lookup blocked",
    ),
    (
        "agent/tool_contract.md",
        # Era «four or five» pagine: un tetto che i ricercatori trattavano come quota, e
        # che nei piani lasciava pagine scritte dagli snippet. Una risposta rapida resta
        # breve; un lavoro di ricerca legge quel che serve e cita le pagine lette.
        "una ricerca legge quel che serve e cita le pagine, non gli snippet",
        r"reads what the question needs, and cites the pages it read, not snippets",
    ),
    (
        "agent/tool_contract.md",
        "il sorgente di jenny si legge con get_source a path puntato",
        r"`get_source` by dotted path",
    ),
    # --- types/operator.md: consiglio PER il subagent operator ---
    (
        "agent/types/operator.md",
        "l'exec dell'operator ha visto lo workspace read-only",
        r"os\.unlink` failed",
    ),
    # --- subagent_system.md: dove la lettura-prima-della-creazione morde ---
    (
        "agent/subagent_system.md",
        "non leggere un file che stai per creare",
        r"[Dd]o not read a file you are about to create",
    ),
    (
        "agent/subagent_system.md",
        "il budget di errori tool di un subagent è piccolo",
        r"tool-error budget",
    ),
]


@pytest.mark.parametrize(
    ("template", "label", "pattern"),
    _PLATFORM_FACTS,
    ids=[f"{t.split('/')[-1]}::{lab}" for t, lab, _ in _PLATFORM_FACTS],
)
def test_platform_fact_is_in_its_template(template: str, label: str, pattern: str) -> None:
    text = _template(template)
    assert re.search(pattern, text, re.DOTALL), (
        f"{template} non contiene più il fatto di piattaforma «{label}». "
        "Se è stato spostato, spostalo in un altro template model-facing e "
        "aggiorna questa riga — non cancellarlo: torna nel SOUL.md di ogni device."
    )


def test_platform_fact_templates_are_extracted_on_device() -> None:
    """Un template non nel manifest non arriva mai sul telefono."""
    targets = sorted({t for t, _, _ in _PLATFORM_FACTS})
    missing = [t for t in targets if t not in _SYSTEM_PROMPT_TEMPLATES]
    assert not missing, (
        f"template con fatti di piattaforma fuori da _SYSTEM_PROMPT_TEMPLATES: {missing}"
    )


def test_untrusted_httpx_claim_is_not_reintroduced() -> None:
    """``httpx`` NON è importabile in ``python_exec``: non offrirlo come fallback.

    Il vecchio testo di ``platform_policy.md`` diceva «fall back to ... or
    ``httpx`` in ``python_exec``». È falso contro
    ``PythonExecConfig.allowed_modules`` (httpx e urllib sono deliberatamente
    fuori dall'allowlist per SSRF/LFI) e mandava il modello a sbattere.
    """
    from jenny.config.tool_schemas import PythonExecConfig

    allowed = set(PythonExecConfig().allowed_modules)
    assert "httpx" not in allowed and "urllib" not in allowed
    policy = _template("agent/platform_policy.md")
    assert "not importable" in policy


# ---------------------------------------------------------------------------
# 2. ``platform-notes`` non deve MAI essere una skill bundlata
# ---------------------------------------------------------------------------

_PLATFORM_NOTES = "platform-notes"


def test_platform_notes_is_not_a_bundled_skill() -> None:
    """La destinazione del review pass deve sopravvivere al riavvio.

    ``sync_workspace_templates`` chiama ``extract_package_dir("jenny.skills",
    …)`` **senza** ``skip_existing``: ogni file di una skill bundlata viene
    riscritto dal package a ogni boot. È voluto (le skill bundlate sono
    contenuto di sistema) e non va cambiato — ma significa che tutto ciò che
    Dream scrive dentro una di esse è distrutto al riavvio successivo. Perciò
    ``skills/platform-notes/`` deve restare una directory NUOVA, mai bundlata.
    """
    bundled = [entry for entry in _SKILLS_MANIFEST if entry.split("/")[0] == _PLATFORM_NOTES]
    assert not bundled, (
        f"'{_PLATFORM_NOTES}' è finita in _SKILLS_MANIFEST ({bundled}): da quel momento "
        "viene riestratta a ogni boot e tutto ciò che il review pass ci ha spostato "
        "dentro sparisce al riavvio successivo."
    )
    on_disk = (_JENNY / "skills" / _PLATFORM_NOTES)
    assert not on_disk.exists(), (
        f"jenny/skills/{_PLATFORM_NOTES}/ esiste nel package: la destinazione del "
        "review pass non può essere contenuto di sistema."
    )


def test_review_pass_names_platform_notes_as_a_new_directory() -> None:
    review = _template("agent/dream_review.md")
    assert f"skills/{_PLATFORM_NOTES}/SKILL.md" in review
    # Non basta nominarla: il prompt deve dire *perché* non può essere una
    # skill bundlata, altrimenti la regola «merge invece di creare» vince.
    assert "re-extracted from the package on every boot" in review
    assert "destroyed at the next restart" in review


def test_dream_forbids_merging_into_a_bundled_skill() -> None:
    """``dream.md`` e ``dream_review.md`` insieme erano una via di perdita dati.

    Entrambi dicono «merge nella skill esistente invece di crearne una
    ridondante». Applicato a ``platform-notes`` con una skill bundlata che
    sembra sovrapporsi, quel consiglio scrive dentro un file che il boot
    successivo riscrive.
    """
    dream = _template("agent/dream.md")
    assert "Never merge into a skill the app ships with" in dream
    assert "re-extracted from the package on every boot" in dream

    review = _template("agent/dream_review.md")
    assert "never into a skill the app ships with" in review


# ---------------------------------------------------------------------------
# 3. Il routing di Dream non manda più i vincoli di runtime in SOUL.md
# ---------------------------------------------------------------------------


def _soul_routing_row(dream: str) -> str:
    for line in dream.splitlines():
        if line.startswith("| SOUL.md |"):
            return line
    raise AssertionError("riga di routing di SOUL.md non trovata in agent/dream.md")


def test_soul_routing_row_is_about_jenny_not_the_app() -> None:
    row = _soul_routing_row(_template("agent/dream.md"))
    assert "never how the **app** behaves" in row, (
        "la riga di routing di SOUL.md non esclude più il comportamento dell'app: "
        "è esattamente la regola per cui 3.144 caratteri di piattaforma erano "
        "corretti da tenere lì."
    )
    assert "tool-use strategy" not in row, (
        "«tool-use strategy» nella riga di SOUL.md è ciò che ci instradava "
        "l'allowlist di python_exec e il confine dello workspace."
    )


def test_dream_routes_runtime_constraints_away_from_soul() -> None:
    dream = _template("agent/dream.md")
    assert "A runtime constraint is not a behavior rule" in dream
    assert "does this describe Jenny, or the app?" in dream
    assert f"skills/{_PLATFORM_NOTES}/SKILL.md" in dream


def test_never_delete_no_longer_protects_platform_text() -> None:
    dream = _template("agent/dream.md")
    assert "- Behavioral rules in SOUL.md" not in dream, (
        "«Never delete: Behavioral rules in SOUL.md» rende zero-caratteri-liberati "
        "l'output *corretto* del review pass su un SOUL.md pieno di piattaforma."
    )
    assert "identity, voice, guardrails, and standing rules the user gave" in dream


# ---------------------------------------------------------------------------
# 4. Il review pass sa distinguere le due popolazioni
# ---------------------------------------------------------------------------


def test_review_pass_has_the_two_population_test() -> None:
    review = _template("agent/dream_review.md")
    # Il test che il modello può davvero eseguire...
    assert "does this describe Jenny, or does it describe the app?" in review
    # ...e la verifica controllabile contro il prompt stesso.
    assert "is this fact already stated above, in this prompt?" in review
    assert "Always delete" in review


def test_review_pass_rejects_re_verified_as_a_reason_to_keep() -> None:
    review = _template("agent/dream_review.md")
    assert '"Re-verified against the running code" is not a reason to keep' in review
    # La guardia: vale su cosa una riga dichiara di sé, non è licenza di
    # scavalcare un'istruzione esplicita dell'utente.
    assert "not licence to overrule an explicit instruction the user gave" in review


def test_review_pass_carries_the_soul_drift_detector() -> None:
    """Nessun ``soul_budget_chars``: un numero in prosa, e solo come sintomo."""
    from jenny.config.schema import DreamConfig

    assert DreamConfig().soul_budget_chars == 0, (
        "soul_budget_chars deve restare 0: il residuo dopo lo split è identità, "
        "l'unica popolazione che Never delete protegge senza eccezioni."
    )
    review = _template("agent/dream_review.md")
    assert "~4,000 characters" in review
    assert "platform text that has re-accreted" in review


def test_t14_pristine_template_scope_paragraph_survives() -> None:
    """Il paragrafo aggiunto da T1.4 a ``## Scope`` non deve essere calpestato."""
    review = _template("agent/dream_review.md")
    assert "**A file the user has never written in is out of scope.**" in review
    assert "Leave it byte-for-byte as it is." in review
