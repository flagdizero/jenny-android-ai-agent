"""Le wiki che esistevano prima del passo 7, portate alla forma di oggi.

Passo **7.4** del piano dei progetti. Due cose, entrambe idempotenti e
nessuna delle due distruttiva:

1. ``CLAUDE.md`` diventa ``AGENTS.md``. **Non e' un ritiro di template** —
   ``retire_withdrawn_templates`` serve alla parte ancora identica a un template
   spedito; qui il contenuto l'ha scritto l'utente, quindi e' un rinomino e il
   testo non si tocca. Se ``AGENTS.md`` esiste gia', non si tocca niente: quello
   e' lo stato che i lettori del passo 2.3 sanno disambiguare, e sceglierne uno
   qui vorrebbe dire buttare l'altro.
2. Ogni wiki prende un **id**, se non ce l'ha. Serve solo a ritrovare la propria
   chat dopo un rinomino della cartella (v. ``session/project_rename.py``): non
   e' un indirizzo, non finisce in nessun nome di file, e una wiki senza id
   continua a funzionare come prima.
3. Ogni wiki prende il suo **diario** (``raw/journal/``), se non ce l'ha —
   passo T1 del piano del taccuino. E' la presa sulla conversazione, e
   la politica che ci scrive dentro e' **universale**: vale per un progetto
   creato oggi e per una wiki di ricerca di mesi fa, perche' ogni conversazione
   di progetto contiene fatti stabili. Legare il diario al formato nuovo
   vorrebbe dire lasciare le sette wiki vere senza la cosa che il taccuino
   esiste per dare. E' l'unico dei tre punti che **crea** qualcosa invece di
   riscriverlo: una cartella vuota, nessun file toccato.

Gira **a ogni avvio**, come l'estrazione dei template, e per la stessa ragione:
e' l'unico modo in cui arriva su un telefono installato da mesi. Il che vuol dire
che il costo a regime deve essere zero — e lo e': niente id da scrivere, niente
file da rinominare, nessuna scrittura.

Una wiki che non ha **nessun** file di istruzioni ne riceve uno **minimo**: la
frontmatter con l'id piu' un titolo. Non lo scaffold completo, che e' mestiere di
``/init`` o dello scaffolder — v. la decisione del 21/08, ``AGENTS.md`` di un
progetto nasce quasi vuoto.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from jenny.utils.path import atomic_write
from jenny.utils.wiki_paths import (
    LEGACY_WIKI_SCHEMA_FILENAME,
    PREVIOUS_ID_KEY,
    WIKI_ID_KEY,
    WIKI_SCHEMA_FILENAME,
    discover_wikis,
    find_wiki_by_id,
    is_valid_wiki_id,
    new_wiki_id,
    settle_wiki_id_lines,
    wiki_id,
    wiki_journal_dir,
    wiki_schema_file,
)

# I due nomi vengono da ``wiki_paths``: la migrazione e' l'unico posto che li
# conosce entrambi come nomi *da scrivere*, e non deve poterne inventare un terzo.
# Lo stesso segnaposto che scrive lo scaffolder, cosi' ``read_wiki_scope`` lo
# riconosce come "da riempire" invece di leggerlo come uno scope vero.
_SUMMARY_PLACEHOLDER = "<one-line scope — shown next to this wiki in wikis/_index.md>"

_AGENTS = WIKI_SCHEMA_FILENAME
_LEGACY = LEGACY_WIKI_SCHEMA_FILENAME


def _title_of(name: str) -> str:
    return name.replace("-", " ").replace("_", " ").title()


def _rename_legacy(root: Path) -> bool:
    """``CLAUDE.md`` -> ``AGENTS.md``, solo se non c'e' già il secondo."""
    legacy, target = root / _LEGACY, root / _AGENTS
    if not legacy.is_file():
        return False
    if target.exists():
        logger.warning(
            "wiki {}: has both {} and {}, leaving both alone — picking one would discard the other",
            root.name, _LEGACY, _AGENTS,
        )
        return False
    legacy.rename(target)
    logger.info("wiki {}: {} rinominato in {}", root.name, _LEGACY, _AGENTS)
    return True


def recover_wiki_id(root: Path, workspace: Path | None) -> str | None:
    """L'id che la chat di *root* si era annotata, se lo si puo' rimettere.

    Una wiki che ha **perso** l'id — un aiutante che riscrive la frontmatter, un
    ``python_exec``, adb — non e' una wiki nuova, e dargliene uno nuovo la
    separa dalla sua chat per sempre: e' quel che e' successo a Piante sul Titan
    il 26/10 (la migrazione ha coniato ``80bf…``, la sessione ricordava
    ``097d…``, e ``_refuse_reincarnated_project`` ha chiuso fuori ogni turno).
    La sessione ``project:<nome>`` sa qual era: si rimette quello.

    **Solo se nessun'altra cartella lo dichiara.** Se l'id e' altrove, la
    conversazione e' di quella cartella (rinominata, o copiata) e questa e'
    un'altra: si conia, come prima, e il rifiuto dice cos'e' successo.

    Il caso che resta scoperto, e si sa: una cartella tolta da fuori Jenny e
    una nuova fatta a mano, **senza** id, con lo stesso nome — qui eredita la
    chat della vecchia. Per arrivarci bisogna passare da adb o da ``su``: la
    cancellazione dalla UI porta via anche la sessione, e lo scaffolder scrive
    l'id alla nascita.
    """
    if workspace is None:
        return None
    # Import locali: ``session/`` sta sopra ``utils/``, e ``project_traces``
    # tira dentro ``SessionManager`` (v. ``tests/session/test_cold_imports.py``).
    from jenny.session.keys import project_session_key
    from jenny.session.project_traces import recorded_wiki_id

    recorded = recorded_wiki_id(workspace, project_session_key(root.name))
    if not is_valid_wiki_id(recorded):
        return None
    claimed = find_wiki_by_id(root.parent, str(recorded))
    if claimed is not None and claimed.resolve() != root.resolve():
        return None
    return recorded


def _write_id(root: Path, value: str) -> None:
    """Scrive *value* come ``id:`` del file di istruzioni di *root*, creandolo se manca."""
    schema = wiki_schema_file(root)
    if schema is None:
        # ``summary`` resta il **segnaposto**, non il nome della cartella. Con il
        # nome, ``wikis/_index.md`` mostrerebbe «ricette — ricette», che sembra una
        # descrizione e non lo è: la voce di prima diceva «(no AGENTS.md)», cioè
        # la verità. ``read_wiki_scope`` riconosce i segnaposto fra ``<>`` e
        # continua a dire «(no scope set)» — che è un invito a riempirlo, non
        # una descrizione finta.
        atomic_write(
            root / _AGENTS,
            f"---\n{WIKI_ID_KEY}: {value}\nsummary: {_SUMMARY_PLACEHOLDER}\n---\n\n"
            f"# {_title_of(root.name)}\n",
        )
        logger.info("wiki {}: created a minimal {} carrying the id", root.name, _AGENTS)
        return
    # Un id scritto a mano e non conforme (``id: tesi-2024``) **non si cancella**,
    # e fino al 26/10 restava anche al suo posto, sotto il nostro: due ``id:``
    # nella stessa frontmatter, che a un modello sembrano un doppione da pulire —
    # e su Piante l'aiutante ha pulito via quello giusto. Ora passa a
    # ``previous_id:`` col suo valore (``settle_wiki_id_lines``), e l'``id:`` resta
    # uno. Senza riserializzare il blocco: ``yaml.dump`` avrebbe riordinato le
    # chiavi, normalizzato le virgolette e perso i commenti di un file scritto a
    # mano.
    text = schema.read_text(encoding="utf-8")
    atomic_write(schema, settle_wiki_id_lines(text, value))


def restore_wiki_id(root: Path, workspace: Path | None) -> str | None:
    """Rimette a *root* l'id che la sua chat ricorda. Ritorna l'id, o ``None``.

    ``None`` = niente da fare: la wiki un id ce l'ha, o non ce n'e' uno da
    rimettere (:func:`recover_wiki_id`). Lo chiama la migrazione all'avvio e
    ``AgentLoop._refuse_reincarnated_project`` a meta' giornata, quando l'id
    sparisce mentre Jenny gira: senza, il turno passerebbe e il prossimo avvio
    conierebbe un id nuovo — il guasto rimandato, non evitato.
    """
    if wiki_id(root) is not None:
        return None
    recovered = recover_wiki_id(root, workspace)
    if recovered is None:
        return None
    _write_id(root, recovered)
    logger.info("wiki {}: id {} restored from its chat", root.name, recovered)
    return recovered


def _ensure_id(root: Path, workspace: Path | None = None) -> str | None:
    """Scrive un id nella frontmatter del file di istruzioni, se non c'e' già.

    Ritorna l'id **scritto** quando l'ha scritto, ``None`` quando non c'era niente
    da fare — così il chiamante può dire quante wiki ha davvero toccato. L'id e'
    quello che la chat della wiki ricorda (:func:`restore_wiki_id`), se c'e';
    altrimenti uno nuovo.
    """
    # «Ce l'ha già, un id?» si chiede **al lettore che lo legge**. ``wiki_id`` usa
    # una regex di proposito: una riga di scope con un due punti dentro rende il
    # blocco non parsabile e ``yaml.safe_load`` non perde quella riga, perde
    # **tutte** le altre (difetto visto sul telefono il 22/08 — v. il docstring
    # di ``wiki_paths.wiki_id``). Chiederlo a YAML qui voleva dire non vedere
    # l'id che c'era e scriverne uno nuovo a *ogni* avvio: l'identità della wiki
    # cambiava sotto i piedi e la sua chat non era più ritrovabile, mentre il log
    # diceva ogni volta «1 identificate». Ora scrittore e lettore concordano per
    # costruzione.
    if wiki_id(root) is not None:
        return None
    restored = restore_wiki_id(root, workspace)
    if restored is not None:
        return restored
    value = new_wiki_id()
    had_schema = wiki_schema_file(root) is not None
    _write_id(root, value)
    if had_schema:
        logger.info("wiki {}: id written to {}", root.name, _AGENTS)
    return value


def _settle_id_lines(root: Path) -> bool:
    """Un solo ``id:`` nella frontmatter di una wiki che l'id ce l'ha. True se ha scritto.

    Per le wiki che la migrazione di prima ha lasciato con due ``id:`` — il
    nostro sopra, quello scritto a mano sotto: Piante sul Titan ce l'ha — e per
    un id valido finito **sotto** una riga non conforme, che ``wiki_id`` non
    vedeva (legge il primo). A regime non scrive niente.
    """
    schema = wiki_schema_file(root)
    if schema is None:
        return False
    text = schema.read_text(encoding="utf-8")
    settled = settle_wiki_id_lines(text)
    if settled == text:
        return False
    atomic_write(schema, settled)
    logger.info("wiki {}: hand-written id kept as {}", root.name, PREVIOUS_ID_KEY)
    return True


def _ensure_journal(root: Path) -> bool:
    """Crea ``raw/journal/`` se non c'e'. True se l'ha creata.

    Solo la cartella, e nessuna pagina: il diario e' append-only e la sua prima
    pagina la scrive la prima cattura. Un file vuoto creato qui sarebbe una
    pagina di un giorno in cui non e' stato detto niente.
    """
    journal = wiki_journal_dir(root)
    if journal.is_dir():
        return False
    journal.mkdir(parents=True, exist_ok=True)
    logger.info("wiki {}: journal created at {}", root.name, journal.name)
    return True


def migrate_wikis(wikis_dir: Path, workspace: Path | None = None) -> dict[str, list[str]]:
    """Porta tutte le wiki sotto *wikis_dir* alla forma del passo 7.

    *workspace* e' dove stanno le sessioni: serve a rimettere a una wiki l'id
    che ha perso invece di coniarne uno nuovo (:func:`recover_wiki_id`). Senza,
    si conia come prima.

    Ritorna ``{"renamed": [...], "identified": [...], "journals": [...]}`` — i
    nomi delle wiki toccate da ciascuno dei tre punti, così l'avvio può dire cosa
    ha fatto invece di farlo in silenzio. Non solleva: una wiki illeggibile viene
    saltata con un log, perché un avvio che muore su una cartella storta non
    migra nemmeno le altre.
    """
    renamed: list[str] = []
    identified: list[str] = []
    journals: list[str] = []
    result = {"renamed": renamed, "identified": identified, "journals": journals}
    if not wikis_dir.is_dir():
        return result
    for index in discover_wikis(wikis_dir).values():
        root = index.parent
        try:
            if _rename_legacy(root):
                renamed.append(root.name)
            _settle_id_lines(root)
            if _ensure_id(root, workspace) is not None:
                identified.append(root.name)
            if _ensure_journal(root):
                journals.append(root.name)
        except Exception:
            logger.opt(exception=True).error("Migrazione della wiki {} fallita", root.name)
    if renamed or identified or journals:
        logger.info(
            # «identificate» diceva di piu' di quel che era vero: fino al 22/08 il
            # conto includeva le wiki che l'id ce l'avevano gia' e a cui la
            # migrazione ne stava scrivendo un altro. Il conto e' delle wiki a cui
            # e' stato **scritto** un id, e la riga adesso lo dice.
            "Wiki migration: {} renamed ({}), {} with id written ({}), {} with a new journal ({})",
            len(renamed), ", ".join(renamed) or "-",
            len(identified), ", ".join(identified) or "-",
            len(journals), ", ".join(journals) or "-",
        )
    return result
