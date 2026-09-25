"""Test di regressione per `scaffold.py`: crea quel che manca, non tocca il resto.

Prima di questa modifica `_write()` era un `open(full, "w")` secco, quindi
rilanciare lo scaffold su una wiki esistente per "aggiungere quel che manca" ne
azzerava `wiki/index.md` e riscriveva il log del giorno. La fixture centrale qui
e' modellata sulla deriva misurata sul telefono tra `main/` e `patreon-creator/`:
la seconda non ha il file di istruzioni, ne' `audit/`, ne' `outputs/`, ma ha contenuto vero
in `wiki/index.md` e nel log di oggi.

Gli script della skill non fanno parte del package `jenny` importabile, quindi la
dir `scripts/` viene aggiunta a `sys.path`.
"""

from __future__ import annotations

import hashlib
import importlib.util
from datetime import date
from pathlib import Path

import pytest

_SCRIPTS_DIR = (
    Path(__file__).resolve().parents[3] / "jenny" / "skills" / "llm-wiki" / "scripts"
)


@pytest.fixture(scope="module")
def scaffold():
    """Carica `scaffold.py` come modulo (la dir non è un package importabile).

    Senza toccare ``sys.path``: ``spec_from_file_location`` non lo consulta, e
    per i moduli fratelli ci pensa lo script stesso — ``scaffold.py`` si inserisce
    la propria directory in testa prima di importarli. L'``insert`` che stava
    qui non serviva a nulla e restava in piedi per tutta la sessione.
    """
    spec = importlib.util.spec_from_file_location("scaffold", _SCRIPTS_DIR / "scaffold.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _digests(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _today_log_name() -> str:
    return f"{date.today():%Y%m%d}.md"


@pytest.fixture
def drifted_wiki(tmp_path: Path) -> Path:
    """Una wiki vera e incompleta: manca il file di istruzioni, audit/, outputs/."""
    root = tmp_path / "wikis" / "patreon-creator"
    (root / "wiki" / "concepts").mkdir(parents=True)
    (root / "raw" / "notes").mkdir(parents=True)
    (root / "log").mkdir(parents=True)
    (root / "wiki" / "index.md").write_text(
        "# Index — Patreon\n\n## Concepts\n- [[concepts/Pricing]]\n", encoding="utf-8"
    )
    (root / "research-plan.md").write_text("# Plan\n\nscritto a mano.\n", encoding="utf-8")
    (root / "log" / _today_log_name()).write_text(
        f"# {date.today().isoformat()}\n\n## [09:12] ingest | 3 fonti\n", encoding="utf-8"
    )
    return root


# ── il top-up ────────────────────────────────────────────────────────────────


def test_topup_crea_i_buchi_e_lascia_intatto_il_resto(scaffold, drifted_wiki, capsys):
    before = _digests(drifted_wiki)

    created = scaffold.scaffold(str(drifted_wiki), "Patreon Creator")
    capsys.readouterr()

    # Quel che mancava ora c'e'.
    assert (drifted_wiki / "AGENTS.md").is_file()
    assert (drifted_wiki / "audit" / "resolved").is_dir()
    assert (drifted_wiki / "outputs" / "queries").is_dir()
    assert "AGENTS.md" in created

    # E niente di quel che c'era prima e' cambiato di un byte. Questo assert sta
    # tra il top-up e lo svuotamento di una wiki vera.
    after = _digests(drifted_wiki)
    assert {k: after[k] for k in before} == before


def test_topup_non_appende_al_log_di_oggi(scaffold, drifted_wiki, capsys):
    log = drifted_wiki / "log" / _today_log_name()
    before = log.read_text(encoding="utf-8")

    scaffold.scaffold(str(drifted_wiki), "Patreon Creator")
    capsys.readouterr()

    # La skill chiede di loggare ogni operazione, ma un top-up non tocca un file
    # che esiste: il report va su stdout. Se un giorno si decide di appendere,
    # va deciso, non scoperto rompendo questo test.
    assert log.read_text(encoding="utf-8") == before


def test_topup_scrive_il_log_di_oggi_se_manca_ed_elenca_quel_che_ha_aggiunto(
    scaffold, drifted_wiki, capsys
):
    (drifted_wiki / "log" / _today_log_name()).unlink()

    scaffold.scaffold(str(drifted_wiki), "Patreon Creator")
    capsys.readouterr()

    log = (drifted_wiki / "log" / _today_log_name()).read_text(encoding="utf-8")
    assert log.startswith(f"# {date.today().isoformat()}\n")
    assert "scaffold | Topped up Patreon Creator scaffolding" in log
    assert "- Created AGENTS.md" in log
    # Non annuncia di aver creato quel che c'era gia'.
    assert "wiki/index.md" not in log


def test_un_secondo_run_non_crea_nulla(scaffold, drifted_wiki, capsys):
    scaffold.scaffold(str(drifted_wiki), "Patreon Creator")
    snapshot = _digests(drifted_wiki)

    created = scaffold.scaffold(str(drifted_wiki), "Patreon Creator")
    out = capsys.readouterr().out

    assert created == []
    assert _digests(drifted_wiki) == snapshot
    assert "Nothing to add" in out


def test_write_non_sovrascrive_un_file_esistente(scaffold, tmp_path: Path):
    """Il confine sta in `_write`, in un punto solo: pin diretto."""
    (tmp_path / "già.md").write_text("mio", encoding="utf-8")

    assert scaffold._write(str(tmp_path), "già.md", "sovrascritto") is False
    assert (tmp_path / "già.md").read_text(encoding="utf-8") == "mio"

    assert scaffold._write(str(tmp_path), "nuovo/file.md", "creato") is True
    assert (tmp_path / "nuovo" / "file.md").read_text(encoding="utf-8") == "creato"


# ── la wiki nuova, che deve restare quella di prima ──────────────────────────


def test_una_wiki_nuova_nasce_completa(scaffold, tmp_path: Path, capsys):
    root = tmp_path / "wikis" / "nuova"

    created = scaffold.scaffold(str(root), "Nuova")
    capsys.readouterr()

    for rel in (
        "AGENTS.md",
        "wiki/index.md",
        f"log/{_today_log_name()}",
        "audit/.gitkeep",
        "audit/resolved/.gitkeep",
    ):
        assert (root / rel).is_file(), rel
    for rel in ("raw/articles", "raw/papers", "raw/notes", "raw/refs", "outputs/queries"):
        assert (root / rel).is_dir(), rel
    assert (root.parent / "_index.md").is_file()
    assert "AGENTS.md" in created


def test_la_voce_di_log_di_una_wiki_nuova_e_quella_di_prima(scaffold, tmp_path: Path, capsys):
    """La forma del log fresco non cambia: `lint_wiki` ne verifica l'H1 e la
    skill dice di ricopiarla a mano quando appende."""
    root = tmp_path / "wikis" / "nuova"

    scaffold.scaffold(str(root), "Nuova")
    capsys.readouterr()

    log = (root / "log" / _today_log_name()).read_text(encoding="utf-8")
    assert log.startswith(f"# {date.today().isoformat()}\n\n## [")
    assert "scaffold | Initialized Nuova knowledge base\n" in log
    assert log.endswith(
        "- Created directory tree (raw/, wiki/, log/, audit/, outputs/)\n"
        "- Created AGENTS.md with the wiki's scope\n"
        "- Created wiki/index.md category skeleton\n"
    )


# ── Il rinomino non deve produrre due file (passo 2.4) ────────────────────


@pytest.fixture
def wiki_col_nome_vecchio(tmp_path: Path) -> Path:
    """Una delle sette di prima: il file di istruzioni si chiama `CLAUDE.md`.

    Incompleta come le altre — le manca `audit/` — cosi' il top-up ha una ragione
    vera per girarci sopra, che e' esattamente la situazione in cui il difetto si
    presenterebbe.
    """
    root = tmp_path / "wikis" / "android-rom"
    (root / "wiki").mkdir(parents=True)
    (root / "wiki" / "index.md").write_text("# Index\n\nroba vera\n", encoding="utf-8")
    (root / "CLAUDE.md").write_text(
        "---\nsummary: architettura delle partizioni Android\n---\n\n# Android ROM\n",
        encoding="utf-8",
    )
    return root


def test_il_topup_non_affianca_agents_a_un_claude_esistente(
    scaffold, wiki_col_nome_vecchio: Path, capsys
):
    """Due file di istruzioni alla radice e' uno stato che non sceglie nessuno.

    I lettori (passo 2.3) lo sanno solo disambiguare — vince `AGENTS.md`, l'altro
    esce dal prompt e un warning lo dice — e crearlo di nascosto durante un
    top-up vorrebbe dire produrlo di proposito sulle sette wiki vere. Il rinomino
    e' il passo 7, e passa da li'.
    """
    before = (wiki_col_nome_vecchio / "CLAUDE.md").read_bytes()

    created = scaffold.scaffold(str(wiki_col_nome_vecchio), "Android ROM")
    capsys.readouterr()

    assert not (wiki_col_nome_vecchio / "AGENTS.md").exists(), (
        "il top-up ha affiancato un secondo file di istruzioni a quello che c'era"
    )
    assert "AGENTS.md" not in created
    assert (wiki_col_nome_vecchio / "CLAUDE.md").read_bytes() == before, (
        "il file di chi l'ha scritto a mano non si tocca: il rinomino e' il passo 7"
    )
    # Il resto del top-up deve comunque aver lavorato, sennò il test passerebbe
    # anche se lo scaffold non avesse fatto niente.
    assert "audit/" in created


def test_la_voce_di_log_nomina_il_file_che_ha_creato_davvero(
    scaffold, wiki_col_nome_vecchio: Path, capsys
):
    """Un log che dice «Created AGENTS.md» dove non c'e' manda a cercare un fantasma."""
    scaffold.scaffold(str(wiki_col_nome_vecchio), "Android ROM")
    capsys.readouterr()

    log = (wiki_col_nome_vecchio / "log" / _today_log_name()).read_text(encoding="utf-8")
    assert "AGENTS.md" not in log


# ── T6.2: il top-up non impone il formato dell'altro scaffolder ──────────────
#
# `SKILL.md` consiglia di rilanciare questo script su una wiki che esiste, e su
# un progetto-taccuino il rilancio aggiungeva `wiki/concepts|entities|summaries`:
# la tassonomia di un formato che quel progetto non usa. Finché il lint decideva
# il modo dalle cartelle, quelle tre directory vuote gli spegnevano il controllo
# su `state:` — un invariante 🔴 che sparisce senza una riga di output.


@pytest.fixture
def taccuino(tmp_path: Path) -> Path:
    """Un progetto come lo crea il picker della UI: pagine piatte, un diario."""
    root = tmp_path / "wikis" / "orto"
    (root / "wiki").mkdir(parents=True)
    (root / "raw" / "journal").mkdir(parents=True)
    (root / "log").mkdir(parents=True)
    (root / "wiki" / "index.md").write_text(
        "# Orto\n\n## Pages\n\n- [[semine]]\n", encoding="utf-8"
    )
    (root / "wiki" / "semine.md").write_text(
        "---\nstate: open\n---\n\n# Semine\n\nVedi [[index]].\n", encoding="utf-8"
    )
    return root


def test_il_topup_su_un_taccuino_non_aggiunge_la_tassonomia(scaffold, taccuino, capsys):
    created = scaffold.scaffold(str(taccuino), "Orto")
    out = capsys.readouterr().out

    for rel in ("wiki/concepts", "wiki/entities", "wiki/summaries", "raw/articles",
                "raw/papers", "raw/refs", "outputs/queries"):
        assert not (taccuino / rel).exists(), rel
    # E il resto del top-up ha comunque lavorato, nella forma del progetto.
    assert (taccuino / "raw" / "research").is_dir()
    assert (taccuino / "audit" / "resolved").is_dir()
    assert "raw/research/" in created and "audit/" in created
    assert "already a notebook project" in out


def test_il_topup_su_un_taccuino_non_cambia_il_modo_del_lint(scaffold, taccuino, capsys):
    """(d) del passo: il modo prima e dopo è lo stesso. La regola è quella del
    lint e arriva da lui — lo scaffolder e il controllore non devono poter
    dissentire sul formato, perché è il dissenso che ha prodotto il difetto.

    Ora è protetto due volte, e le due difese sono indipendenti: lo scaffolder non
    crea più la tassonomia, e anche se la creasse il lint decide dalle pagine.
    Serve riportare **entrambi** i difetti perché questo test cada — verificato
    per mutazione."""
    lint_wiki = scaffold.lint_wiki
    assert lint_wiki.is_research_layout(taccuino / "wiki") is False

    scaffold.scaffold(str(taccuino), "Orto")
    capsys.readouterr()

    assert lint_wiki.is_research_layout(taccuino / "wiki") is False


def test_un_taccuino_senza_mappa_prende_la_mappa_piatta(scaffold, taccuino, capsys):
    """Un albero rimasto a metà è proprio quel che si viene a riparare, e la
    mappa è il primo file che l'agente legge: tre sezioni di tassonomia lì sono
    tre inviti a un formato che questo progetto non usa."""
    (taccuino / "wiki" / "index.md").unlink()

    scaffold.scaffold(str(taccuino), "Orto")
    capsys.readouterr()

    mappa = (taccuino / "wiki" / "index.md").read_text(encoding="utf-8")
    assert "## Pages" in mappa and "raw/journal/" in mappa
    assert "Concepts" not in mappa and "Entities" not in mappa


def test_una_biblioteca_con_la_tassonomia_vuota_prende_l_albero_intero(
    scaffold, drifted_wiki, capsys
):
    """Il verso in cui sbagliare costa di più. `patreon-creator` misurata ha le
    cartelle della ricerca ancora vuote e le pagine dove capita: leggerla come un
    taccuino le negherebbe `outputs/queries` e la lascerebbe rotta. Il diario
    assente è il segno che quella cartella non è un progetto — sul telefono il
    diario ce l'hanno tutte, quindi qui conta la sua **assenza**."""
    assert not (drifted_wiki / "raw" / "journal").exists()

    created = scaffold.scaffold(str(drifted_wiki), "Patreon Creator")
    out = capsys.readouterr().out

    assert (drifted_wiki / "wiki" / "summaries").is_dir()
    assert (drifted_wiki / "outputs" / "queries").is_dir()
    assert "wiki/summaries/" in created
    assert "already a notebook project" not in out


def test_una_wiki_nuova_non_e_un_taccuino(scaffold, tmp_path: Path, capsys):
    """La cartella che nasce adesso non ha pagine, e senza pagine non c'è niente
    da leggere: prende l'albero di ricerca intero, che è il motivo per cui questo
    script viene chiamato."""
    root = tmp_path / "wikis" / "nuova"

    scaffold.scaffold(str(root), "Nuova")
    capsys.readouterr()

    assert (root / "wiki" / "concepts").is_dir()
    assert (root / "outputs" / "queries").is_dir()


# ── T6.8: lo scaffolder dice la verità, e c'e' una sola forma ─────────────────
#
# **H15.** `_write` chiedeva `os.path.exists`, che dice sì anche a una *cartella*
# chiamata `AGENTS.md` o `wiki/index.md`: il report diceva "already there — left
# as it is" e il run si dichiarava riuscito su una wiki dove il lint poi muore
# con `IsADirectoryError`.
#
# **H16.** `scaffold.py` non creava `raw/journal/`, mentre lo scaffolder del
# package sì e SKILL.md dà il diario per universale: due scaffolder, due forme.
# Ora il comune è definito una volta (`project_scaffold.PROJECT_DIRS`, ricopiato
# in `_COMMON_DIRS`) e il confronto sta qui, così la prossima divergenza è un
# test che cade.


def test_una_cartella_dove_va_un_file_non_e_un_successo(scaffold, tmp_path: Path, capsys):
    root = tmp_path / "wikis" / "storta"
    (root / "wiki").mkdir(parents=True)
    (root / "AGENTS.md").mkdir()

    scaffold.scaffold(str(root), "Storta")
    out = capsys.readouterr().out

    assert "Wrong kind of thing in the way" in out
    assert "AGENTS.md — a directory where a file belongs" in out
    assert "still incomplete" in out
    # E soprattutto non la frase che diceva che va tutto bene.
    assert "AGENTS.md already there — left as it is" not in out
    assert "Nothing to add" not in out
    # La cartella non si tocca: spostare roba dell'utente non e' compito suo.
    assert (root / "AGENTS.md").is_dir()


def test_una_cartella_al_posto_della_mappa_e_una_collisione(scaffold, tmp_path: Path, capsys):
    root = tmp_path / "wikis" / "storta"
    (root / "wiki" / "index.md").mkdir(parents=True)

    scaffold.scaffold(str(root), "Storta")
    out = capsys.readouterr().out

    assert "wiki/index.md — a directory where a file belongs" in out
    assert "wiki/index.md already there — left as it is" not in out


def test_un_file_dove_va_una_cartella_non_fa_esplodere_il_run(
    scaffold, tmp_path: Path, capsys
):
    """`makedirs` moriva con un `FileExistsError` a metà scaffold, lasciando la
    wiki peggio di come l'ha trovata e senza dire perché."""
    root = tmp_path / "wikis" / "storta"
    root.mkdir(parents=True)
    (root / "log").write_text("non sono una cartella\n", encoding="utf-8")

    scaffold.scaffold(str(root), "Storta")
    out = capsys.readouterr().out

    assert "log/ — a file where a directory belongs" in out
    assert "still incomplete" in out
    assert (root / "wiki").is_dir(), "il resto dell'albero deve essere stato creato"


def test_write_distingue_un_file_da_una_cartella(scaffold, tmp_path: Path):
    """Pin diretto sul confine, che sta in un punto solo."""
    (tmp_path / "cartella.md").mkdir()
    collisions: list[str] = []

    assert scaffold._write(str(tmp_path), "cartella.md", "x", collisions) is False
    assert collisions == ["cartella.md"]
    assert (tmp_path / "cartella.md").is_dir()


def test_anche_l_albero_di_ricerca_ha_il_diario(scaffold, tmp_path: Path, capsys):
    """Il diario è universale: lo controlla il lint in ogni layout, ci scrive la
    cattura in ogni layout, e SKILL.md lo dice. Una wiki di ricerca nata da qui
    non aveva il posto dove quella scrittura va."""
    root = tmp_path / "wikis" / "nuova"

    created = scaffold.scaffold(str(root), "Nuova")
    capsys.readouterr()

    assert (root / "raw" / "journal").is_dir()
    assert "raw/journal/" in created


def test_i_due_scaffolder_non_possono_divergere(scaffold):
    """La definizione unica di com'e' fatto un progetto vive nel package —
    ``jenny/webui/project_scaffold.py::PROJECT_DIRS`` — e questa e' la copia che
    il checkout della skill non puo' importare. Il confronto sta qui perche' due
    liste che devono restare uguali vivono in due file."""
    from jenny.webui.project_scaffold import PROJECT_DIRS

    assert set(scaffold._NOTEBOOK_DIRS) == set(PROJECT_DIRS)
    # E il comune e' comune: l'albero di ricerca lo contiene tutto.
    assert set(scaffold._COMMON_DIRS) <= set(scaffold._RESEARCH_DIRS)
    assert set(scaffold._COMMON_DIRS) <= set(PROJECT_DIRS)


def test_un_secondo_topup_su_una_biblioteca_non_la_legge_come_taccuino(
    scaffold, drifted_wiki, capsys
):
    """Il corollario di H16, e il difetto che avrebbe reintrodotto: da quando
    anche l'albero di ricerca crea `raw/journal/`, l'assenza del diario non
    distingue più niente al secondo giro. La cartella si dichiara con le sue
    cartelle di ricerca, che vincono."""
    scaffold.scaffold(str(drifted_wiki), "Patreon Creator")
    capsys.readouterr()

    scaffold.scaffold(str(drifted_wiki), "Patreon Creator")
    out = capsys.readouterr().out

    assert "already a notebook project" not in out
    assert scaffold._is_existing_notebook(str(drifted_wiki)) is False


def test_un_taccuino_resta_un_taccuino_anche_col_diario(scaffold, taccuino, capsys):
    """Il verso opposto del test sopra: la nuova condizione non deve trasformare
    un progetto in una biblioteca."""
    scaffold.scaffold(str(taccuino), "Orto")
    capsys.readouterr()

    assert scaffold._is_existing_notebook(str(taccuino)) is True
    scaffold.scaffold(str(taccuino), "Orto")
    assert "already a notebook project" in capsys.readouterr().out
