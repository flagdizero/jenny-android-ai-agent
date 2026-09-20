"""I prompt di sistema si aggiornano, i file dell'utente no.

Il difetto osservato in produzione: `sync_workspace_templates` estraeva tutto
`jenny/templates/` con `skip_existing=True`, per non calpestare `SOUL.md` e
`USER.md`. Effetto collaterale, invisibile perché sembra funzionare: anche i
prompt di sistema erano congelati. Un telefono aggiornato per mesi girava con
`identity.md` della versione in cui era stato installato — un file *nuovo*
arrivava, un file *corretto* no.

Verificato sul dispositivo il 2026-08-06: dopo un aggiornamento con tre prompt
modificati e uno aggiunto, il log diceva `Extracted 1 files`.

Le politiche sono tre e questo file le documenta tutte: il file dell'utente che
si crea una volta sola, il prompt di sistema che si riscrive a ogni avvio, e in
mezzo il ritiro sul posto — l'unica scrittura dentro un file dell'utente, e solo
quando quel file è ancora, byte per byte, una versione nostra ritirata.
"""

from __future__ import annotations

import os
import stat
from contextlib import suppress
from pathlib import Path

import pytest

from jenny.utils.android_assets import (
    _RETIRED_TEMPLATE_DIGESTS,
    _SYSTEM_PROMPT_TEMPLATES,
    _TEMPLATES_MANIFEST,
    _USER_OWNED_TEMPLATES,
    extract_package_dir,
    retire_withdrawn_templates,
)
from jenny.utils.helpers import load_bundled_template, sync_workspace_templates

MARKER = "MODIFICATO A MANO — non deve sopravvivere a un aggiornamento\n"


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir(parents=True, exist_ok=True)
    sync_workspace_templates(root, silent=True)
    return root


# -- le due politiche -------------------------------------------------------


def test_a_system_prompt_is_restored_on_the_next_sync(workspace: Path) -> None:
    """Il caso che il difetto rendeva impossibile: una correzione che arriva."""
    target = workspace / "agent" / "identity.md"
    original = target.read_text(encoding="utf-8")
    target.write_text(MARKER, encoding="utf-8")

    sync_workspace_templates(workspace, silent=True)

    assert target.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("name", _USER_OWNED_TEMPLATES)
def test_a_user_file_is_never_overwritten(workspace: Path, name: str) -> None:
    """L'altra metà, e il motivo per cui il difetto esisteva.

    ``SOUL.md`` e ``USER.md`` li riscrive Dream, ``AGENTS.md`` e
    ``HEARTBEAT.md`` l'utente: la copia del pacchetto è un punto di partenza,
    non la verità. Riscriverli cancellerebbe la personalità del bot.
    """
    target = workspace / name
    target.write_text(MARKER, encoding="utf-8")

    sync_workspace_templates(workspace, silent=True)

    assert target.read_text(encoding="utf-8") == MARKER


def test_every_prompt_under_agent_is_treated_as_a_system_prompt(workspace: Path) -> None:
    """Chi aggiunge un prompt non deve doversi ricordare di questa distinzione.

    La regola è posizionale: tutto ciò che sta sotto ``agent/`` è codice.
    Se una voce nuova finisce nella lista sbagliata, l'aggiornamento smette di
    arrivare per quel file soltanto — il tipo di guasto che nessuno nota.
    """
    for name in _SYSTEM_PROMPT_TEMPLATES:
        assert name.startswith("agent/"), f"{name} non è un prompt di sistema"
    for name in _USER_OWNED_TEMPLATES:
        assert not name.startswith("agent/"), f"{name} non è un file dell'utente"


def test_the_two_lists_cover_the_manifest_exactly(workspace: Path) -> None:
    """Nessun file può cadere fra le due politiche, né essere in entrambe."""
    assert set(_USER_OWNED_TEMPLATES).isdisjoint(_SYSTEM_PROMPT_TEMPLATES)
    assert set(_USER_OWNED_TEMPLATES) | set(_SYSTEM_PROMPT_TEMPLATES) == set(
        _TEMPLATES_MANIFEST
    )


# -- il sottoinsieme dichiarato ---------------------------------------------


def test_extracting_a_file_outside_the_manifest_is_an_error(tmp_path: Path) -> None:
    """``only`` resta un sottoinsieme dichiarato, non una scorciatoia.

    Il manifest esiste perché sul dispositivo nessun file arrivi o manchi senza
    traccia; un ``only`` capace di aggirarlo riaprirebbe quella porta.
    """
    with pytest.raises(ValueError, match="not in the manifest"):
        extract_package_dir(
            "jenny.templates", tmp_path, only=["agent/does_not_exist.md"],
        )


# -- la terza politica: ritiro sul posto ------------------------------------
#
# Riconoscere una versione ritirata la tiene fuori dal prompt ma la lascia sul
# disco, e lì resta a un tasto dall'essere peggio di prima: la prima riga che
# l'utente ci aggiunge promuove tutto il manuale ritirato a "scritto
# dall'utente", per sempre e senza etichetta. Qui quel testo se ne va davvero —
# ma solo quando è, byte per byte, ancora nostro.


def _retired_fixture(name: str) -> str:
    """Un template ritirato, letto da ``tests/agent/fixtures/``.

    Stesso motivo per cui li legge di lì ``test_context_builder``: alcune di
    quelle righe finiscono con uno spazio, e trascritte in un sorgente Python
    ``ruff`` (W291) le pulirebbe. Il digest non combacerebbe più con quello che
    c'è sui telefoni e il test proverebbe qualcos'altro.
    """
    return (Path(__file__).resolve().parents[1] / "agent" / "fixtures" / name).read_text(
        encoding="utf-8"
    )


# Ogni versione ritirata elencata nel registro, con il file che la contiene.
# Sono tutte candidate vive: un telefono porta per sempre quella che era bundled
# al *suo* primo avvio, indipendentemente da quanti aggiornamenti ha preso dopo.
_RETIRED_FIXTURES = [
    ("AGENTS.md", "agents_md_retired_v0.3.0.md"),
    ("AGENTS.md", "agents_md_retired_6c5dba8_unreleased.md"),
    ("AGENTS.md", "agents_md_retired_v0.6.6.md"),
    ("USER.md", "user_md_retired_0.3.0.md"),
    ("USER.md", "user_md_retired_97d7b38_unreleased.md"),
    ("memory/MEMORY.md", "memory_md_retired_v0.3.0.md"),
]


@pytest.mark.parametrize(("name", "fixture"), _RETIRED_FIXTURES)
def test_a_withdrawn_version_is_retired_to_the_empty_bundle(
    workspace: Path, name: str, fixture: str
) -> None:
    """Il caso del Titan 2: il manuale ritirato se ne va davvero dal disco.

    Da 0.8.0 la versione bundled di questi tre file è **zero byte** —
    ``AGENTS.md``, ``USER.md`` e ``memory/MEMORY.md`` spediscono vuoti apposta —
    quindi ritirare vuol dire portare a zero byte anche la copia sul disco.

    Per un commit questo test asseriva l'opposto, e la ragione era una guardia
    scritta male: ``if not data`` trattava un asset vuoto come illeggibile, si
    prendeva tutti e tre i digest ritirati, e la migrazione non avveniva più —
    con tre ``logger.warning`` a ogni boot su ogni installazione, per sempre.
    ``read_asset`` distingue già i due casi nel tipo di ritorno (``None`` per un
    fallimento, ``b""`` per un asset davvero vuoto), e la protezione dell'utente
    non era quella guardia: è il digest esatto, v.
    :func:`test_one_line_of_the_users_own_is_enough_to_stop_it`.

    **Se questo test inizia a fallire** perché il file non è vuoto ma contiene la
    nuova copia bundled, qualcuno ha rimesso del testo in un template
    dell'utente: l'asserzione giusta diventa
    ``target.read_text() == load_bundled_template(name)``.
    """
    target = workspace / name
    target.write_text(_retired_fixture(fixture), encoding="utf-8")

    sync_workspace_templates(workspace, silent=True)

    assert (load_bundled_template(name) or "") == "", (
        f"{name} non è più un template vuoto: v. il docstring"
    )
    assert target.read_text(encoding="utf-8") == ""


@pytest.mark.parametrize(("name", "fixture"), _RETIRED_FIXTURES)
def test_the_retirement_does_not_come_back_on_the_next_boot(
    workspace: Path, name: str, fixture: str
) -> None:
    """Ritirato una volta, e poi silenzio.

    A zero byte il file non combacia più con nessun digest ritirato, quindi il
    boot successivo non ha niente da fare e non stampa niente: è la differenza fra
    una migrazione e un avviso a vita.
    """
    target = workspace / name
    target.write_text(_retired_fixture(fixture), encoding="utf-8")
    sync_workspace_templates(workspace, silent=True)

    assert retire_withdrawn_templates(workspace) == []
    assert target.read_text(encoding="utf-8") == ""


@pytest.mark.parametrize(("name", "fixture"), _RETIRED_FIXTURES)
def test_a_bom_does_not_hide_a_withdrawn_version(
    workspace: Path, name: str, fixture: str
) -> None:
    """Un BOM UTF-8 non è testo dell'utente, e ``strip()`` non lo rimuove.

    ``"\\ufeff"`` è categoria Cf, quindi ``str.isspace()`` è ``False`` e
    sopravvive allo ``strip()`` che precede l'hash. Basta aprire e salvare il file
    una volta con un editor Windows — Notepad lo aggiunge da sé — perché un
    template che l'utente non ha mai scritto smetta di combaciare con qualunque
    digest: rientra in ogni prompt come prosa dell'utente, senza nemmeno
    l'etichetta "default intatto", e il ritiro non lo vede più.

    Da non confondere con i fine-riga: quelli sono già normalizzati, perché ogni
    lettore passa da ``Path.read_text`` e ``newline=None`` porta un CRLF a ``\\n``
    prima dell'hash. La pista del CRLF è stata seguita per sbaglio; l'innesco è
    questo.
    """
    target = workspace / name
    target.write_text("﻿" + _retired_fixture(fixture), encoding="utf-8")

    assert retire_withdrawn_templates(workspace) == [name]


@pytest.mark.parametrize(("name", "fixture"), _RETIRED_FIXTURES)
def test_the_rewrite_still_works_when_there_is_something_to_write(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, name: str, fixture: str
) -> None:
    """Il ramo che scrive, tenuto vivo con un bundle finto.

    Oggi nessun template dell'utente ha byte da spedire, quindi il rewrite non
    si esercita più da solo: senza questo test il ritiro diventerebbe codice mai
    percorso, e il giorno in cui un template torna non vuoto si scoprirebbe rotto
    su un telefono. Il bundle è iniettato qui invece di essere letto dal package
    proprio perché il package non ne ha uno.
    """
    target = workspace / name
    target.write_text(_retired_fixture(fixture), encoding="utf-8")
    monkeypatch.setattr(
        "jenny.utils.android_assets.read_asset", lambda *args, **kwargs: b"# Nuovo\n"
    )

    assert retire_withdrawn_templates(workspace) == [name]

    assert target.read_text(encoding="utf-8") == "# Nuovo\n"


@pytest.mark.parametrize(("name", "fixture"), _RETIRED_FIXTURES)
def test_one_line_of_the_users_own_is_enough_to_stop_it(
    workspace: Path, name: str, fixture: str
) -> None:
    """Questo è il test che protegge l'utente, ed è il motivo per cui il ritiro
    non chiede né uno snapshot né un consenso.

    La condizione è un digest esatto: una riga aggiunta in fondo e il file non è
    più nostro, quindi non si tocca. Non è prudenza applicata bene, è
    impossibilità per costruzione — chi allentasse il confronto (match
    approssimato, "quasi uguale", prefisso) cancellerebbe testo che l'utente ha
    scritto e non ha altrove.
    """
    target = workspace / name
    edited = _retired_fixture(fixture) + "\n- Deploy con `./gradlew`.\n"
    target.write_text(edited, encoding="utf-8")

    sync_workspace_templates(workspace, silent=True)

    assert target.read_text(encoding="utf-8") == edited


@pytest.mark.parametrize("name", sorted(_RETIRED_TEMPLATE_DIGESTS))
def test_a_file_already_current_is_not_written_at_all(workspace: Path, name: str) -> None:
    """Non "riscritto uguale": proprio non toccato.

    L'asserzione è sulla mtime e non sul contenuto perché una riscrittura con
    byte identici passerebbe un controllo sul contenuto ed è comunque un difetto:
    fa I/O inutile su ogni boot e stampa una riga di log che dichiara una
    migrazione mai avvenuta.
    """
    target = workspace / name
    os.utime(target, (1_000_000, 1_000_000))
    before = target.stat().st_mtime_ns

    sync_workspace_templates(workspace, silent=True)

    assert target.stat().st_mtime_ns == before


def test_a_missing_file_is_not_an_error(tmp_path: Path) -> None:
    """Un workspace vuoto non ha niente da ritirare: il seeding lo crea dopo."""
    assert retire_withdrawn_templates(tmp_path) == []
    assert not (tmp_path / "AGENTS.md").exists()


def test_an_unreadable_bundle_leaves_the_file_alone(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prima si leggono i byte nuovi, poi si scrive.

    L'ordine è ciò che garantisce che non esista un istante in cui il file
    dell'utente non c'è: senza il contenuto da mettere, non si cancella niente.
    """
    retired = _retired_fixture("agents_md_retired_v0.3.0.md")
    (workspace / "AGENTS.md").write_text(retired, encoding="utf-8")
    monkeypatch.setattr(
        "jenny.utils.android_assets.read_asset", lambda *args, **kwargs: None
    )

    assert retire_withdrawn_templates(workspace) == []

    assert (workspace / "AGENTS.md").read_text(encoding="utf-8") == retired


def test_an_empty_bundle_is_written_because_empty_is_what_we_ship(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Vuoto e illeggibile sono due cose diverse, e il tipo di ritorno lo dice già.

    ``read_asset`` dà ``None`` quando non è riuscita a leggere e ``b""`` per un
    asset che è davvero vuoto — che da 0.8.0 è il caso normale di tutti e tre i
    template dell'utente. Conflaterli con ``if not data`` disattivava il ritiro
    per l'intero registro.

    Il rischio da cui quella guardia difendeva — azzerare un file che l'utente
    aveva riempito — non è coperto da qui ma dal digest esatto: un file con una
    riga in più non combacia con nessuna versione ritirata e non arriva a questo
    punto.
    """
    (workspace / "AGENTS.md").write_text(
        _retired_fixture("agents_md_retired_v0.3.0.md"), encoding="utf-8"
    )
    monkeypatch.setattr(
        "jenny.utils.android_assets.read_asset", lambda *args, **kwargs: b""
    )

    assert retire_withdrawn_templates(workspace) == ["AGENTS.md"]

    assert (workspace / "AGENTS.md").read_text(encoding="utf-8") == ""


def test_the_retired_file_keeps_its_permissions(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Il ritiro porta via del testo nostro, non i permessi dell'utente.

    ``atomic_write`` scrive un file nuovo e lo mette al posto del vecchio: nasce
    col umask del processo, quindi un file tenuto a 0600 si ritroverebbe a 0644.
    Allargare i permessi di un file dell'utente è un secondo effetto che nessuno
    ha chiesto — ``config/store.py`` rimette il chmod a mano per lo stesso
    motivo.

    Il bundle è finto per lo stesso motivo di
    ``test_the_rewrite_still_works_when_there_is_something_to_write``: i template
    dell'utente spediscono vuoti, e senza byte da scrivere questo ramo non lo
    percorre più nessuno.
    """
    target = workspace / "AGENTS.md"
    target.write_text(_retired_fixture("agents_md_retired_v0.3.0.md"), encoding="utf-8")
    target.chmod(0o600)
    monkeypatch.setattr(
        "jenny.utils.android_assets.read_asset", lambda *args, **kwargs: b"# Nuovo\n"
    )

    assert retire_withdrawn_templates(workspace) == ["AGENTS.md"]

    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_a_symlinked_file_is_not_retired(workspace: Path, tmp_path: Path) -> None:
    """Un symlink è una decisione, e il ritiro non la ribalta.

    ``atomic_write`` fa ``os.replace`` sul path: al posto del link resterebbe un
    file regolare, l'utente perderebbe il collegamento che aveva scelto e le due
    copie divergerebbero senza dirlo a nessuno. Rinunciare è la mossa giusta —
    il ritiro è un'ottimizzazione, il link no.
    """
    real = tmp_path / "AGENTS-condiviso.md"
    retired = _retired_fixture("agents_md_retired_v0.3.0.md")
    real.write_text(retired, encoding="utf-8")
    target = workspace / "AGENTS.md"
    target.unlink()
    target.symlink_to(real)

    assert retire_withdrawn_templates(workspace) == []

    assert target.is_symlink()
    assert real.read_text(encoding="utf-8") == retired


def test_a_failed_retire_does_not_stop_the_prompt_refresh(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ritirare è un'ottimizzazione; aggiornare i prompt di sistema no.

    Il ritiro gira per primo e scrive con ``atomic_write``, che non ha le difese
    di ``_write_bytes_force`` (nato perché una scrittura fallita al boot manda in
    crash-loop il gateway). Un ``AGENTS.md`` non scrivibile alza da lì, e
    ``runtime/container.py`` — a differenza di ``android_entry.py`` — non
    raccoglie niente: quell'eccezione porterebbe via l'estrazione di ``agent/**``,
    cioè l'unico modo in cui una correzione a un prompt raggiunge un telefono già
    installato.
    """
    (workspace / "AGENTS.md").write_text(
        _retired_fixture("agents_md_retired_v0.3.0.md"), encoding="utf-8"
    )
    prompt = workspace / "agent" / "identity.md"
    original = prompt.read_text(encoding="utf-8")
    prompt.write_text(MARKER, encoding="utf-8")

    def _refuse(*args: object, **kwargs: object) -> None:
        raise PermissionError("workspace is read-only")

    monkeypatch.setattr("jenny.utils.android_assets.atomic_write", _refuse)

    sync_workspace_templates(workspace, silent=True)

    assert prompt.read_text(encoding="utf-8") == original


# -- il prompt del review pass ----------------------------------------------
#
# ``agent/dream_review.md`` è un prompt di sistema come gli altri, ma con una
# variabile che nessun altro prompt di Dream ha: la misura corrente dei file.
# Il guard che ogni voce del manifest esista su disco vive già in
# ``tests/utils/test_asset_manifests.py`` (``_TEMPLATES_MANIFEST`` è l'unione
# delle due liste, e ``test_the_two_lists_cover_the_manifest_exactly`` qui sopra
# tiene ``_SYSTEM_PROMPT_TEMPLATES`` dentro quell'unione); qui si copre il
# rendering, che quel guard non tocca.


@pytest.fixture
def rendered_from(workspace: Path, monkeypatch: pytest.MonkeyPatch):
    """Rende un template *dalla copia estratta nel workspace*.

    ``render_template`` carica da ``get_workspace_path()``, non dal package: un
    prompt che non arriva nel workspace non si rende affatto, quindi renderlo di
    lì prova le due cose insieme. L'ambiente Jinja è memoizzato a livello di
    processo (``lru_cache``), perciò va invalidato prima **e** dopo — la prima
    chiamata della suite fisserebbe altrimenti la root per tutti.
    """
    from jenny.runtime.context import get_runtime_context
    from jenny.utils import prompt_templates

    monkeypatch.setattr(get_runtime_context(), "workspace_dir", workspace)
    prompt_templates._environment.cache_clear()
    yield prompt_templates.render_template
    prompt_templates._environment.cache_clear()


def test_the_review_prompt_renders_with_the_budget_gauge(rendered_from) -> None:
    """La misura è l'unica cosa che il review pass sa e Dream no: deve arrivare."""
    gauge = "MEMORY.md [50% — 3,000/6,000 chars]"

    text = rendered_from("agent/dream_review.md", budget_gauge=gauge)

    assert gauge in text
    # Il nome della variabile è congelato: chi lo cambia qui lo deve cambiare
    # anche in chi la inietta, e un mismatch renderebbe silenziosamente vuoto.
    assert "{{" not in text


def test_an_empty_gauge_leaves_no_dangling_heading(rendered_from) -> None:
    """Senza misura da mostrare, la sezione non deve restare come intestazione vuota.

    Un ``## Budget`` seguito dal nulla non è un dettaglio estetico: al modello
    dice che una misura c'era e non è arrivata, che è peggio del non averla mai
    promessa.
    """
    text = rendered_from("agent/dream_review.md", budget_gauge="")

    assert "## Budget" not in text
    assert "\n\n\n" not in text, "riga vuota di troppo dove stava la sezione"
    # Il resto del prompt è intatto: sparisce la misura, non il mestiere.
    assert "## Scope" in text


# -- i fatti che il runtime calcola da sé --------------------------------------
#
# Ora, fuso e posizione del device arrivano già in ogni turno dentro il blocco
# Runtime Context, misurati e datati. Ricopiarli in un file di memoria produce
# una fotocopia scaduta nell'istante in cui la si scrive, e per giunta l'unica
# delle due che il modello legge come un fatto stabile.
#
# Il pin su ``USER.md`` che stava qui (``test_the_user_template_puts_runtime_facts
# _out_of_scope``) è stato rimosso, non spostato: il template è vuoto, e un
# template vuoto non può mettere niente "fuori scope". Quella regola aveva due
# copie — il template e ``agent/dream.md`` — e delle due il template era quella
# che *non* arrivava mai, perché ``USER.md`` si crea al primo avvio e non si
# aggiorna più. Resta la copia buona, coperta dal test qui sotto, che è anche
# l'unica che parla a chi scrive davvero in quel file.


def test_the_user_owned_templates_ship_no_prose() -> None:
    """La decisione di 0.8.0: nei file dell'utente non spediamo testo nostro.

    Un file di ``_USER_OWNED_TEMPLATES`` si estrae con ``skip_existing=True``:
    si crea una volta e non lo raggiunge nessun aggiornamento. La prosa che ci
    stava dentro si pagava in ogni prompt appena il file smetteva di combaciare
    col bundle, non la leggeva nessuno (nessuno apre un editor markdown sul
    telefono), e nel caso di ``memory/MEMORY.md`` insegnava il contrario delle
    regole di sistema: ``## User Information`` e ``## Preferences`` contro
    ``agent/dream.md``, che i fatti sull'utente li manda in ``USER.md``.

    Il lettore è la persona, e la persona sta nella WebUI.

    Le due eccezioni sono esplicite:

    * ``SOUL.md`` è la personalità di serie, cioè contenuto che *deve* stare nel
      prompt — è l'unico che riceve ``_BOOTSTRAP_TEMPLATE_NOTICE``.
    * ``HEARTBEAT.md`` tiene ``## Active Tasks`` perché non è prosa ma il
      delimitatore su cui si orienta il parser (``jenny/cron/heartbeat_tasks.py``:
      la scansione parte con ``in_active_section = False``). Svuotato il file, il
      primo task dell'utente non verrebbe letto e ``_run_heartbeat`` uscirebbe in
      silenzio dicendo "no active tasks": un controllo schedulato che non gira e
      non alza niente da nessuna parte.
    """
    for name in ("AGENTS.md", "USER.md", "memory/MEMORY.md"):
        assert (load_bundled_template(name) or "") == "", (
            f"{name} è tornato a spedire del testo: quel testo non raggiunge mai "
            "un'installazione esistente, e su una nuova si paga in ogni turno. "
            "Va sotto `jenny/templates/agent/`, in una skill, o nella WebUI."
        )

    heartbeat = [
        line for line in (load_bundled_template("HEARTBEAT.md") or "").splitlines() if line.strip()
    ]
    assert heartbeat == ["# Heartbeat Tasks", "## Active Tasks"], (
        "HEARTBEAT.md deve restare le due sole intestazioni: `## Active Tasks` è il "
        f"delimitatore del parser, il resto è prosa. Trovato: {heartbeat}"
    )


def test_dream_is_told_not_to_write_runtime_facts_and_why() -> None:
    """Il divieto senza il motivo si legge come "non è importante", che è falso.

    La posizione del device è importante, tanto che il runtime la mette in ogni
    prompt: quello che non va è la copia, perché nasce vecchia e nessuno la
    aggiorna quando l'utente si sposta.
    """
    text = load_bundled_template("agent/dream.md") or ""

    assert "Do not add what the runtime already reports" in text
    assert "`Device location`" in text
    assert "stale" in text, "il divieto arriva senza il motivo che lo regge"


def test_the_retired_digest_registry_has_exactly_one_definition() -> None:
    """Due copie di un insieme che deve restare allineato è il guasto che questo
    repo continua a dover riparare.

    I consumatori sono due — il riconoscimento nel prompt e la riscrittura al
    boot — e stanno in package diversi, che è esattamente la condizione in cui la
    seconda copia nasce. ``session/keys.py``, ``agent/memory.py`` e
    ``agent/autocompact.py`` sono tre copie divergenti della regola sui prefissi
    interni, e ``roadmap/project-sessions.md`` la chiama "a data-loss bug no test
    will catch". Questo è il test che la prende.
    """
    sources = list((Path(__file__).resolve().parents[2] / "jenny").rglob("*.py"))
    assert sources, "nessun sorgente trovato: il path del package è cambiato"

    for name, retired in _RETIRED_TEMPLATE_DIGESTS.items():
        for digest in retired:
            holders = [
                path.name for path in sources if digest in path.read_text(encoding="utf-8")
            ]
            assert holders == ["android_assets.py"], (
                f"il digest ritirato di {name} è scritto in {holders}: "
                "la definizione deve restare una sola"
            )


# -- la quarta politica: quel che non è cambiato non si riscrive -------------
#
# Le tre politiche qui sopra dicono *cosa* estrarre. Questa dice *quando* farlo
# costare qualcosa, e non le contraddice: un byte diverso atterra comunque.
#
# Misurato sul Titan 2 il 20/09/2026: ogni passata riscriveva 272 file, e su
# Android le passate sono due — `android_entry` e `runtime/container` sono due
# entry point che non sapevano l'uno dell'altro, e la ripetizione era invisibile
# nel log perché i due chiamanti nominavano la stessa cartella in due modi
# (`/data/user/0/<pkg>` e `/data/data/<pkg>`). 544 scritture su flash a ogni
# accensione per lasciare il disco identico.


def test_a_second_identical_pass_writes_nothing(tmp_path: Path) -> None:
    """La passata che non ha niente da fare non deve toccare il disco.

    Si guarda l'``mtime`` e non il conteggio: il conteggio è quel che la
    funzione *dice*, l'``mtime`` è quel che ha *fatto*.
    """
    dest = tmp_path / "ws"
    primo = extract_package_dir("jenny.templates", dest, only=_SYSTEM_PROMPT_TEMPLATES)
    assert primo > 0, "la prima passata deve estrarre davvero"

    campione = dest / _SYSTEM_PROMPT_TEMPLATES[0]
    prima = campione.stat().st_mtime_ns
    # Un mtime a grana grossa renderebbe il confronto cieco: si sposta indietro
    # di un secondo, così un'eventuale riscrittura si vede comunque.
    os.utime(campione, ns=(prima - 1_000_000_000, prima - 1_000_000_000))
    segnato = campione.stat().st_mtime_ns

    secondo = extract_package_dir("jenny.templates", dest, only=_SYSTEM_PROMPT_TEMPLATES)
    assert secondo == 0, f"la seconda passata ha riscritto {secondo} file identici"
    assert campione.stat().st_mtime_ns == segnato, "il file è stato riscritto uguale"


def test_a_changed_file_still_lands(tmp_path: Path) -> None:
    """**La casella che vale l'ottimizzazione.**

    È la promessa che il salto non deve rompere, ed è la ragione per cui questi
    file si estraggono senza ``skip_existing``: la correzione di un prompt deve
    arrivare su un telefono già installato. Se il confronto fosse sbagliato —
    per esempio se guardasse solo la taglia — un byte cambiato a lunghezza
    invariata resterebbe fermo per sempre, e nessuno se ne accorgerebbe.
    """
    dest = tmp_path / "ws"
    extract_package_dir("jenny.templates", dest, only=_SYSTEM_PROMPT_TEMPLATES)
    campione = dest / _SYSTEM_PROMPT_TEMPLATES[0]
    buono = campione.read_bytes()

    # Stessa lunghezza, un byte diverso: il caso che una `stat` non vede.
    guasto = bytearray(buono)
    guasto[0] = (guasto[0] + 1) % 256
    campione.write_bytes(bytes(guasto))

    scritti = extract_package_dir("jenny.templates", dest, only=_SYSTEM_PROMPT_TEMPLATES)
    assert scritti == 1, f"il file corrotto doveva essere riscritto, scritti={scritti}"
    assert campione.read_bytes() == buono, "il contenuto del pacchetto deve aver vinto"


def test_a_truncated_file_is_rewritten(tmp_path: Path) -> None:
    """Il caso che la taglia prende da sola, e che deve restare preso."""
    dest = tmp_path / "ws"
    extract_package_dir("jenny.templates", dest, only=_SYSTEM_PROMPT_TEMPLATES)
    campione = dest / _SYSTEM_PROMPT_TEMPLATES[0]
    buono = campione.read_bytes()
    campione.write_bytes(buono[: len(buono) // 2])

    assert extract_package_dir("jenny.templates", dest, only=_SYSTEM_PROMPT_TEMPLATES) == 1
    assert campione.read_bytes() == buono


def test_an_unreadable_file_is_rewritten_not_skipped(tmp_path: Path) -> None:
    """Non sapere vuol dire scrivere.

    Un confronto che non si può fare non deve diventare un "va bene così": è il
    modo in cui un file danneggiato resterebbe danneggiato. ``_write_bytes_force``
    esiste già per sopravvivere al file reso read-only, e questo lo esercita.
    """
    dest = tmp_path / "ws"
    extract_package_dir("jenny.templates", dest, only=_SYSTEM_PROMPT_TEMPLATES)
    campione = dest / _SYSTEM_PROMPT_TEMPLATES[0]
    buono = campione.read_bytes()
    campione.write_bytes(b"rotto")
    campione.chmod(0o000)
    try:
        scritti = extract_package_dir("jenny.templates", dest, only=_SYSTEM_PROMPT_TEMPLATES)
    finally:
        with suppress(OSError):
            campione.chmod(0o644)
    assert scritti == 1, "un file illeggibile va riscritto, non saltato"
    assert campione.read_bytes() == buono


def test_both_startup_paths_name_the_workspace_the_same_way() -> None:
    """I due entry point devono chiedere la stessa cartella con lo stesso nome.

    Non è pedanteria: su Android la cartella dati risponde a due nomi, e finché
    ``android_entry`` passava la sua variabile locale invece del valore
    canonico, nel log del boot le due passate sembravano **due destinazioni**
    invece che una ripetizione. È ciò che ha tenuto nascosto il doppio lavoro.
    """
    entry = (
        Path(__file__).resolve().parents[2] / "jenny" / "android_entry.py"
    ).read_text(encoding="utf-8")
    assert "sync_workspace_templates(get_workspace_path())" in entry, (
        "android_entry deve passare il percorso canonico, non la sua variabile locale"
    )
