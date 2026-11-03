"""Discovery, frontmatter, wikilink e fingerprint delle wiki del workspace.

Layer neutro, senza dipendenze su ``webui/`` o ``agent/``: entrambi devono
sapere dove vivono le wiki, come leggerne l'intestazione e dove porta un
``[[link]]``, e farlo importare all'uno dall'altro sarebbe un'inversione di
layer. ``webui/wiki.py`` (il renderer, che tira dentro ``markdown``) importa da
qui, non il contrario.
"""

from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, overload

if TYPE_CHECKING:
    from datetime import date

from loguru import logger

# La frontmatter di una pagina, letta come la legge il lint
# (``lint_wiki.FRONTMATTER_RE``, una copia: il lint non importa ``jenny``): un BOM
# o una riga vuota prima del ``---``, e il ``---`` finale senza a capo. Una sola
# forma per tutti i lettori, guardia di provenienza compresa: se la guardia
# leggesse «nessuna frontmatter» dove il lint legge ``state: decided``, una riga
# vuota in cima basterebbe a passarla.
FRONTMATTER_RE = re.compile(r"^\ufeff?\s*---[ \t]*\n(.*?)\n---[ \t]*(?:\n|\Z)", re.DOTALL)

# Il **registro delle wiki**: ``wikis/_index.md``, una riga per wiki. Non e' la
# mappa di una wiki — quella e' :data:`WIKI_INDEX_FILENAME`, ``wiki/index.md``,
# e i due nomi distavano un underscore (T3.12). Sono due file diversi a due
# livelli diversi dell'albero: qui "registry" e la' "index", cosi' che leggere
# uno dei due non richieda di ricordarsi dell'altro.
WIKIS_REGISTRY_FILENAME = "_index.md"
# Il file di istruzioni di una wiki. **Uno solo**, da quando il passo 7 migra le
# wiki esistenti a ogni avvio (``utils/wiki_migration.py``).
#
# Fino al 22/08 qui c'era anche ``CLAUDE.md``, ed era il ripiego che teneva in
# piedi le wiki scritte a mano prima che il nome cambiasse. Toglierlo e' il 7.5,
# e la ragione non e' l'ordine: due nomi per lo stesso file sono due nomi da
# tenere allineati in ogni lettore, e i lettori sono quattro. Il ripiego era il
# prezzo che il passo 2 aveva accettato per non toccare cartelle vere; adesso le
# cartelle vere sono migrate.
#
# Cosa si perde: una wiki copiata da un'installazione vecchia **mentre Jenny
# gira** ha le sue istruzioni invisibili fino al riavvio successivo, che e'
# quando la migrazione la rinomina. Una finestra piccola e che si chiude da se'.
WIKI_SCHEMA_FILENAME = "AGENTS.md"

# Il nome di prima. Serve ancora a **due** posti, e a nessun lettore: la
# migrazione, che lo rinomina, e ``wiki_id``, che deve poter leggere l'identita'
# di una wiki non ancora migrata — se non ci riuscisse, quella wiki perderebbe la
# propria chat proprio nella finestra in cui e' piu' fragile.
LEGACY_WIKI_SCHEMA_FILENAME = "CLAUDE.md"


# ── Il diario ────────────────────────────────────────────────────────────────

# Dove una wiki tiene la cattura della conversazione: una pagina al giorno,
# append-only. Sta sotto ``raw/`` e non sotto ``wiki/`` perche' *e'* materiale
# grezzo — il taccuino che poi diventa pagine — e per una conseguenza pratica che
# vale da sola: albero, grafo e ricerca camminano solo ``wiki/``, quindi un
# diario qui non chiede a nessuno di quei sottosistemi di imparare a non
# trattarlo da pagina, e il grafo resta la mappa delle *cose* invece di
# diventare un calendario.
#
# In inglese come tutte le sorelle (``wiki/``, ``raw/``, ``log/``, ``audit/``) e
# come le chiavi di frontmatter che il codice legge (``id:``, ``summary:``):
# questo nome e' una costante di programma, non testo per l'utente. Quel che
# l'utente scrive *dentro* e' nella sua lingua.
JOURNAL_DIRNAME = "raw/journal"


def wiki_journal_dir(wiki_root: Path) -> Path:
    """La cartella del diario di *wiki_root*. Non garantisce che esista."""
    return wiki_root / JOURNAL_DIRNAME


# Le cartelle di ``wiki/`` che dichiarano il layout di ricerca. Copia di
# ``lint_wiki.RESEARCH_SUBDIRS`` (lo script della skill non importa ``jenny``):
# ``tests/board/test_notebook_ops.py`` le tiene uguali.
RESEARCH_SUBDIRS = ("concepts", "entities", "summaries")


def is_research_layout(pages_dir: Path) -> bool:
    """Se le **pagine** di ``wiki/`` (*pages_dir*) sono una biblioteca di ricerca.

    La regola del lint (``lint_wiki.is_research_layout``): almeno un ``.md`` in
    una delle :data:`RESEARCH_SUBDIRS`. :func:`is_research_wiki` chiede di più,
    e per un'altra domanda: se un quaderno merita una bacheca.
    """
    return any(
        (pages_dir / name).is_dir() and any(p.is_file() for p in (pages_dir / name).rglob("*.md"))
        for name in RESEARCH_SUBDIRS
    )


def is_research_wiki(wiki_root: Path) -> bool:
    """Se *wiki_root* e' un archivio di ricerca: la forma di ricerca
    (``concepts/``/``entities/``) **e** nessuna pagina di diario.

    La forma da sola non basta: un quaderno vissuto puo' avere le pagine
    nelle cartelle di ricerca e un diario che il giardiniere legge ogni
    giorno, ed e' proprio il quaderno che una bacheca deve avere. Il diario
    e' il segno che ci si vive dentro; un archivio di ricerca senza diario
    si apre con la ricerca, e la sua bacheca si decide piu' avanti.

    Sta qui e non nella passata della bacheca perche' la domanda se la fanno in
    tre — la passata, il blocco di progetto del prompt, la risposta di
    ``/gardener`` — e tre copie sarebbero tre politiche.
    """
    pages = wiki_root / "wiki"
    if not ((pages / "concepts").is_dir() or (pages / "entities").is_dir()):
        return False
    journal = wiki_journal_dir(wiki_root)
    return not (journal.is_dir() and any(journal.glob("*.md")))


def journal_page_name(day: "date") -> str:
    """Il nome della pagina di diario di *day*: ``AAAAMMGG.md``.

    Un file per giorno, e il nome ordinabile: un ``ls`` della cartella e' la
    cronologia, che e' la ragione per cui non e' ``AAAA-MM-GG``.
    """
    return f"{day.strftime('%Y%m%d')}.md"


def wiki_schema_file(wiki_root: Path) -> Path | None:
    """Il file di istruzioni di una wiki, o ``None`` se non ne ha nessuno."""
    candidate = wiki_root / WIKI_SCHEMA_FILENAME
    return candidate if candidate.is_file() else None


# ── La piegatura della ricerca ───────────────────────────────────────────────


def fold(text: str) -> str:
    """Normalizza per la ricerca: NFKD, via i diacritici, minuscolo.

    «Città» e «citta» diventano la stessa parola; «東京» e «Москва» restano
    loro stesse — si tolgono gli accenti, non le lettere. La usano la ricerca
    delle wiki, quella della memoria e la mappa di un quaderno. Il gemello
    JavaScript è ``foldText`` in ``shared/wiki-search.js``: le due devono restare
    identiche, altrimenti il client cerca termini che il server non ha mai
    scritto nel dizionario.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    without_marks = "".join(c for c in decomposed if not unicodedata.combining(c))
    return without_marks.lower()


# ── Frontmatter ──────────────────────────────────────────────────────────────


def extract_title(text: str) -> str | None:
    """Titolo di una pagina: ``title:`` nel frontmatter, altrimenti il primo H1."""
    fm = FRONTMATTER_RE.match(text)
    if fm:
        t = re.search(r"^title:\s*(.+)$", fm.group(1), re.M)
        if t:
            return t.group(1).strip().strip('"').strip("'")
    h1 = re.search(r"^#\s+(.+?)\s*$", text, re.M)
    return h1.group(1) if h1 else None


def split_frontmatter(text: str) -> tuple[str, str]:
    """``(testo grezzo del frontmatter, corpo)`` senza parsare lo YAML.

    Serve a chi deve solo *guardare* il frontmatter — l'indice full-text ne
    pesca i tag — e non può permettersi un ``yaml.safe_load`` per pagina su
    centinaia di file. Senza frontmatter il primo elemento è la stringa vuota.
    """
    m = FRONTMATTER_RE.match(text)
    if not m:
        return "", text
    return m.group(1), text[m.end() :]


def strip_frontmatter(text: str) -> tuple[dict[str, Any] | None, str, str | None]:
    """Return ``(frontmatter, body, title)`` for a page's raw markdown."""
    m = FRONTMATTER_RE.match(text)
    frontmatter: dict[str, Any] | None = None
    body = text
    if m:
        import yaml

        try:
            parsed = yaml.safe_load(m.group(1))
            frontmatter = parsed if isinstance(parsed, dict) else {}
        except Exception:
            frontmatter = {}
        body = text[m.end() :]
    title = None
    if frontmatter and "title" in frontmatter:
        title = frontmatter["title"]
    else:
        h1 = re.search(r"^#\s+(.+?)\s*$", body, re.M)
        if h1:
            title = h1.group(1)
    return frontmatter, body, title


# ── Che cosa e' una pagina ───────────────────────────────────────────────────

# Sottocartelle di ``wiki/`` che non contengono pagine di contenuto.
# ``summaries/`` e' il layer di citazione del pattern di ricerca: un riassunto
# per documento grezzo, non una cosa di cui la wiki parla.
WIKI_PAGES_SKIP_DIRS = frozenset({"summaries"})


def is_wiki_page_rel(
    rel: Path, *, skip_dirs: frozenset[str] = WIKI_PAGES_SKIP_DIRS
) -> bool:
    """Vero se *rel* — percorso di un ``.md`` **relativo alla pages-dir** — e' una pagina.

    **Una regola sola per i quattro camminatori** (T9.5). Prima ce n'erano
    quattro, e le differenze non erano decisioni: :func:`iter_wiki_pages`
    saltava i nascosti, ``webui/wiki.py::iter_page_files`` (ora :func:`wiki_page_files`) no — quindi un
    ``.draft.md`` sotto ``wiki/`` non arrivava al modello ma diventava un nodo
    del grafo e un risultato di ricerca — e ``_walk`` saltava i nascosti a ogni
    livello, cioe' **anche le cartelle**, mentre gli altri due guardavano solo
    il nome del file. Il risultato: l'albero dei file nascondeva una cartella
    ``.qualcosa/`` che il prompt iniettava a ogni turno.

    Due cose *non* si decidono qui, e per ragioni diverse:

    * **L'indice.** ``wiki/index.md`` e' la mappa, e per il prompt e' un blocco
      a se' (v. :data:`WIKI_INDEX_FILENAME`): chi elenca le pagine da iniettare
      lo esclude *dopo*. Per grafo, albero e ricerca invece e' una pagina come
      le altre — e' il nodo centrale, e cercarci dentro e' la cosa piu' ovvia
      del mondo. La differenza e' voluta: il chiamante la scrive in una riga
      accanto a questa, cosi' si vede.
    * **``log/`` e ``audit/``.** Non sono in *questo* insieme perche' sono
      **sorelle** di ``wiki/``, non figlie: nessuna delle camminate le
      raggiunge. Le nomina :data:`_FINGERPRINT_SKIP_DIRS`, e solo per il giorno
      in cui una delle due finisse sotto ``wiki/``.

    *skip_dirs* e' un parametro e non una costante letta dentro perche' il
    fingerprint ha un insieme suo (piu' largo, per quella ragione).

    **Una quinta copia esiste e resta**, in
    ``jenny/skills/llm-wiki/scripts/lint_wiki.py::is_injected_page``: quello
    script e' un checkout della skill, gira anche fuori dall'app e non puo'
    importare ``jenny``. Come per :data:`MAP_MAX_CHARS`, il prezzo e' che le due
    copie si tengono allineate a mano, e il commento la' punta qui.
    """
    parts = rel.parts
    if any(part.startswith(".") for part in parts):
        return False
    # Il confronto e' col **primo segmento intero**: una pagina che si chiamasse
    # ``summaries.md`` nella radice della wiki e' una pagina — il filtro nomina
    # una cartella, non un prefisso.
    return not (parts and parts[0] in skip_dirs)


# L'indice **e'** la mappa, non una voce dell'elenco delle pagine.
#
# **Il nome del file, per tutti** (T3.12). Chi lo escludeva dall'elenco lo
# leggeva da qui, ma chi la mappa la *apriva* — ``context.py::_read_map_source``,
# ``GardenerStore.map_path`` — se lo scriveva a mano: cambiare questa costante
# avrebbe fatto sparire la mappa dal prompt **e** iniettato ``index.md`` come se
# fosse una pagina, in silenzio e nella stessa mossa. Ora e' un nome solo, e
# ``tests/utils/test_wiki_paths.py::TestTheIndexFilenameHasOneDefinition`` cade se
# ne ricompare un secondo.
#
# Da non confondere con :data:`WIKIS_REGISTRY_FILENAME` (``wikis/_index.md``),
# che e' il registro delle wiki e non la mappa di una.
WIKI_INDEX_FILENAME = "index.md"

# Il tetto della mappa, in caratteri: quanto ne entra nel blocco di progetto di
# ogni turno (``agent/context.py``, T3) e la soglia oltre cui il giardiniere la
# pota (``agent/gardener.py``). Si paga a **ogni** turno del progetto, quindi è
# una soglia sui caratteri e non sui token: è quella che si legge a occhio nel
# file. Duemila caratteri sono circa una schermata piena sul telefono, cioè
# esattamente quel che la mappa dichiara di essere.
#
# Una misura per due lettori, qui e non in uno dei due:
# ``context.py`` tira dentro mezzo repo, e il giardiniere se lo copiava per non
# importarlo. **La copia che resta è voluta**: ``MAP_MAX_CHARS`` in
# ``jenny/skills/llm-wiki/scripts/lint_wiki.py``, che gira anche fuori dall'app e
# non importa ``jenny`` (v. ``FORK_BOUNDARY.md``); li tiene uguali
# ``tests/skills/llm_wiki/test_lint_wiki.py::test_the_ceiling_matches_the_one_the_prompt_uses``.
WIKI_MAP_MAX_CHARS = 2000


def read_wiki_map(wiki_root: Path) -> str:
    """Il testo di ``wiki/index.md`` spogliato ai bordi, o ``""`` se non c'è.

    L'unico lettore della mappa: il blocco di progetto ci costruisce la mappa e
    l'**ordine** delle pagine (che, con da 1 a 4 pagine nel tetto, è la loro
    selezione), il giardiniere ci misura se va potata. Due lettori diversi
    davano due misure: il giardiniere leggeva in UTF-8 stretto e su un indice in
    latin-1 misurava 0, quindi non lo potava mai, mentre il prompt lo mostrava.

    **Un file che non è UTF-8 non è un errore** (T6.12): si sostituiscono i byte
    guasti invece di buttare la mappa. Degradarla ad «assente» costerebbe la
    sezione della mappa e, peggio, l'ordine delle pagine, che a mappa vuota
    ricade sull'alfabeto — cioè cambierebbe *quali* pagine il modello vede,
    senza dirlo. Il segno resta visibile nel testo (il carattere di
    sostituzione), e il rimedio lo dà il lint («Pages that are not UTF-8»).
    """
    try:
        return (
            (wiki_root / "wiki" / WIKI_INDEX_FILENAME)
            .read_text(encoding="utf-8", errors="replace")
            .strip()
        )
    except OSError:
        return ""


def safe_wiki_page_path(input_path: str) -> str | None:
    """Normalizza e valida un path di pagina wiki relativo.

    Rifiuta path assoluti o che risalgono fuori dalla wiki (``..``). Ritorna il
    path normalizzato relativo, la mappa (:data:`WIKI_INDEX_FILENAME`) se vuoto,
    o ``None`` se invalido.

    **E' una guardia sulla stringa, non sul filesystem**: un link simbolico
    dentro ``wiki/`` la supera senza obiezioni e finisce comunque fuori. Il
    secondo cancello e' il contenimento (``resolve().relative_to(...)``), e i
    due servono a cose diverse — v. ``test_wiki_routes_server_scope``.

    Sta qui, nel layer neutro, e non nell'adapter HTTP che l'aveva scritta:
    la stessa domanda se la fa ora anche ``webui/commands.py``, che di trasporti
    non sa niente e non puo' importare da ``wiki_routes``.
    """
    if not input_path:
        return WIKI_INDEX_FILENAME
    if os.path.isabs(input_path):
        return None
    normalized = os.path.normpath(input_path).replace(os.sep, "/")
    if normalized.startswith(".."):
        return None
    return normalized


def page_chars(text: str) -> int:
    """La misura di una pagina in caratteri: **una regola sola** (T9.12).

    E' quella che il tetto di iniezione guarda — il testo *spogliato* ai bordi —
    e vive qui perche' i suoi lettori stanno in due strati che non si importano:
    ``GardenerStore._page_chars_if_over`` (che annota l'inventario della passata,
    T3.14) e i tool di scrittura (``_FsTool._wiki_page_ceiling_note``, che
    avvisano quando una scrittura ha appena reso una pagina non iniettabile). Un
    secondo modo di contare la stessa cosa sarebbe il modo di avvisare su pagine
    che entravano — o di tacere su pagine che non entrano.

    **Il ``replace`` non e' cosmetico**, ed e' quel che permette di misurare una
    stringa in memoria come se venisse da disco: chi la pagina la legge usa
    ``read_text(encoding="utf-8")``, cioe' newline universali, che traduce
    ``\\r\\n`` in ``\\n`` — e quella traduzione **accorcia**. Un ``\\r`` solitario
    diventa anch'esso ``\\n``, ma a lunghezza invariata, quindi non serve
    nominarlo. Senza questo, un tool che pesa il testo che sta per scrivere su un
    file CRLF conterebbe piu' caratteri di quanti l'iniettore ne leggera'.
    """
    return len(text.replace("\r\n", "\n").strip())


def page_chars_if_over(path: Path, ceiling: int) -> int | None:
    """La misura di *path* se sfonda *ceiling*, altrimenti ``None``.

    **La misura è quella che il tetto guarda**: il testo *spogliato* ai bordi,
    come fa l'iniettore e come fa il lint. Un secondo modo di contare la stessa
    cosa sarebbe il modo di segnalare pagine che entravano, o di tacere su pagine
    che non entrano — quindi la regola non è scritta qui: è :func:`page_chars`, e
    da T9.12 la leggono anche i tool di scrittura, che avvisano *dentro* la
    passata quando una scrittura ha appena portato una pagina oltre il tetto.

    **Il primo passo è uno ``stat``, e non è un'ottimizzazione gratuita.**
    :func:`iter_wiki_pages` ha già letto ogni pagina per estrarne il titolo, ma
    butta la lunghezza e vive in uno strato neutro: rileggerle tutte vorrebbe dire
    una seconda lettura completa della wiki a ogni passata. In UTF-8 un carattere
    non pesa **mai meno di un byte** e ``strip()`` non aggiunge, quindi
    ``st_size <= ceiling`` implica «sotto il tetto»: il filtro non può perdere una
    pagina, e il testo si legge solo per le candidate. Misurato su ``main`` (52
    pagine, 9 candidate): 0,27 ms contro 0,75 ms della rilettura completa.

    **Era un metodo di ``GardenerStore``**, promosso qui il 26/08 quando `/tidy` è
    diventato il secondo lettore: la passata annota il proprio inventario e il
    comando misura la wiki per la conversazione, e due copie di questo conto
    sarebbero due risposte a «questa pagina entra in un turno?».
    """
    try:
        if path.stat().st_size <= ceiling:
            return None
    except OSError:
        return None
    try:
        chars = page_chars(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        # Illeggibile è un altro guasto, non questo: chi elenca la nomina
        # comunque, senza annotazione.
        return None
    return chars if chars > ceiling else None


def wiki_page_rel(path: Path) -> str | None:
    """Il percorso di *path* dentro la ``wiki/`` di un progetto, o ``None``.

    Risponde alla domanda che i tool di scrittura non sapevano porsi (T9.12):
    *questo file e' una pagina che il blocco di progetto inietta?* Sono loro il
    chiamante, e la risposta e' `None` per tutto il resto del workspace — in
    particolare per ``memory/``, che ha un budget suo e un guard suo
    (``memory_budget.make_write_size_guard``): un avviso sul tetto delle pagine,
    la', sarebbe sbagliato due volte.

    La forma della risposta e' il percorso relativo alla pages-dir perche' e' il
    nome con cui la mappa e l'inventario chiamano una pagina.

    **Le tre esclusioni sono quelle di :func:`iter_wiki_pages`, non altre**: il
    predicato :func:`is_wiki_page_rel` (niente nascosti, niente ``summaries/``),
    la mappa (``index.md``, che ha un tetto diverso — v.
    :data:`WIKI_MAP_MAX_CHARS`) e l'estensione. Il confronto su
    ``.md`` e' esatto e sensibile alle maiuscole come il ``rglob("*.md")`` di
    quella funzione, sul solo runtime che esiste.

    **Il ``wiki`` piu' esterno vince**, perche' e' da la' che l'iniettore
    cammina: una pagina ``concepts/wiki/x.md`` e' relativa alla ``wiki/`` del
    progetto, non a se stessa. E "progetto" e' la definizione che ha il resto del
    codice (:func:`is_wiki_root`): la cartella sopra contiene una ``wiki/``.
    Quel che resta fuori dalla portata di questa funzione e' una cartella
    chiamata ``wiki`` che non sia il progetto di nessuno — costerebbe una frase
    in piu' in un risultato di tool, non un rifiuto, e non c'e' modo di
    distinguerla senza chiedere allo scope (che chi scrive non ha).
    """
    if path.suffix != ".md":
        return None
    parts = path.parts
    # Fino al penultimo: l'ultimo segmento e' il file, e un file di nome ``wiki``
    # non e' la cartella delle pagine di nessuno.
    for i in range(len(parts) - 1):
        if parts[i] != "wiki":
            continue
        if not is_wiki_root(Path(*parts[:i])):
            continue
        rel = Path(*parts[i + 1 :])
        if not is_wiki_page_rel(rel) or rel.as_posix() == WIKI_INDEX_FILENAME:
            return None
        return rel.as_posix()
    return None


def _page_title(path: Path) -> str:
    """Il titolo di *path*, col nome del file come ripiego. **Costa una lettura.**

    Estratta da :func:`iter_wiki_pages` perche' e' l'unica riga di quella
    funzione che apre un file: averla per nome rende visibile — a chi legge e a
    chi profila — dove sta il costo dell'elenco.
    """
    try:
        return extract_title(path.read_text(encoding="utf-8")) or path.stem
    except (OSError, UnicodeDecodeError):
        return path.stem


def wiki_page_files(pages_dir: Path, *, include_index: bool) -> list[tuple[str, Path]]:
    """``(rel, path)`` delle pagine sotto *pages_dir* (``wiki/``), in ordine d'albero.

    L'unica camminata delle pagine: :func:`iter_wiki_pages` (prompt, giardiniere)
    senza l'indice, grafo e ricerca (``webui/wiki.read_pages``, il fingerprint di
    ``wiki_search``) con. **Quali file** lo decide :func:`is_wiki_page_rel`.

    L'indice e' la sola differenza, e voluta: per il prompt ``wiki/index.md`` e'
    la mappa e ha un blocco suo, per chi naviga e' il nodo centrale e cercarci
    dentro e' la cosa piu' ovvia. **Il confronto e' sensibile alle maiuscole, di
    proposito** (T9.5): un ``wiki/INDEX.md`` su Android **non e'** il file che
    l'iniettore apre come mappa (``context.py::_read_map_source`` chiede
    esattamente ``index.md``); escluderlo lo toglierebbe al modello in tutt'e due
    i modi, mentre cosi' almeno arriva come pagina.

    L'ordine e' quello dei ``Path`` (le pagine di una cartella stanno insieme:
    ``a/x.md`` prima di ``a-b/x.md``), e non dipende dal filesystem come quello di
    ``rglob``: da questa lista discende l'indice dei nodi del grafo, che e' la
    chiave delle postings della ricerca. Fino al 03/11/2026 grafo e ricerca
    ordinavano per stringa, che mette ``a-b/`` fra ``a.md`` e ``a/``.
    """
    if not pages_dir.is_dir():
        return []
    found: list[tuple[str, Path]] = []
    for path in sorted(pages_dir.rglob("*.md")):
        rel = path.relative_to(pages_dir)
        if not is_wiki_page_rel(rel):
            continue
        if not include_index and rel.as_posix() == WIKI_INDEX_FILENAME:
            continue
        found.append((rel.as_posix(), path))
    return found


@overload
def iter_wiki_pages(pages_dir: Path) -> list[tuple[str, str]]: ...


@overload
def iter_wiki_pages(pages_dir: Path, *, titles: Literal[True]) -> list[tuple[str, str]]: ...


@overload
def iter_wiki_pages(pages_dir: Path, *, titles: Literal[False]) -> list[str]: ...


def iter_wiki_pages(
    pages_dir: Path, *, titles: bool = True
) -> list[tuple[str, str]] | list[str]:
    """Le pagine di contenuto di una wiki, in ordine di percorso.

    Con *titles* (default) torna ``(percorso relativo a wiki/, titolo)`` per ogni
    pagina; con ``titles=False`` torna i soli percorsi e **non apre nessun file**.

    **Una regola sola per le due forme** su disco: tutto quel che sta sotto
    ``wiki/`` e passa :func:`is_wiki_page_rel` — quindi niente nascosti a nessun
    livello e niente ``summaries/`` — meno l'indice, che e' la mappa. Le
    pagine piatte del formato nuovo e le ``concepts/``/``entities/`` di una wiki
    di ricerca cadono qui insieme, e il percorso relativo dice da se' in quale
    delle due si e'.

    Sta in questo strato perche' ha piu' consumatori — l'inventario del
    giardiniere, l'autocompaction, il blocco di progetto del prompt — e
    elencare le pagine di una wiki non e' il mestiere di nessuno di loro.

    **Perche' il titolo e' opzionale (T3.11).** Estrarlo costa un
    ``read_text()`` **per pagina**, e chi lo usa e' una minoranza: lo mettono
    nell'elenco l'inventario del giardiniere, mentre
    ``ContextBuilder._read_project_pages`` lo buttava via — su ogni pagina di
    ogni wiki, dentro ``build_system_prompt``, cioe' **sul loop dell'evento a
    ogni turno**. Misurato il 23/08 sulle 11 wiki vere (471 pagine): elencare le
    139 pagine della piu' grande costa 3,0 ms coi titoli e 0,8 ms senza, su 5,3
    ms che il blocco iniettato costava in tutto.

    **Quel corpo non e' quello del telefono:** ricontato il 24/08 in sola lettura sono 8
    wiki / 274 pagine sotto wiki/ / la piu' grande (main) 65. La misura del 23/08 girava
    su una copia nello scratchpad con alberi duplicati e una wiki in piu' che sul
    telefono non c'e', quindi i valori assoluti qui sopra non sono quelli del
    dispositivo: vale il prima/dopo, non il numero.

    **Una manopola e non una seconda funzione**: la lettura e' la stessa
    camminata, e un secondo nome sarebbe il nome che il chiamante nuovo non
    conosce. E il valore torna in una forma **diversa** — percorsi nudi, non
    coppie col titolo vuoto — perche' un titolo finto e' un titolo che finisce in
    un prompt: cosi' chi lo usasse per sbaglio si rompe subito invece di
    stampare una riga muta. Gli ``@overload`` servono a dirlo al type checker.
    """
    found = wiki_page_files(pages_dir, include_index=False)
    if not titles:
        return [rel for rel, _ in found]
    return [(rel, _page_title(path)) for rel, path in found]


# ── L'identita' di una wiki ──────────────────────────────────────────────────

# La chiave di frontmatter in cui vive l'id, dentro il file di istruzioni della
# wiki (``AGENTS.md``, o ``CLAUDE.md`` sulle wiki non ancora migrate: v.
# :func:`wiki_id`, il solo lettore che accetta ancora il nome vecchio).
#
# Questo commento stava **venti righe sopra**, appiccicato a
# :data:`WIKI_PAGES_SKIP_DIRS`, e mentiva su tutt'e due le costanti insieme
# (T9.4/G8). Rimesso sul suo.
WIKI_ID_KEY = "id"

# Forma dell'id: 12 caratteri esadecimali. Non finisce **mai** in un nome di
# file — l'indirizzo di una chat resta il nome della cartella
# (v. ``jenny/session/project_rename.py``) — quindi non deve essere
# leggibile, deve solo essere improbabile da ripetere. Se un domani diventasse
# l'indirizzo, i nomi dei file diventerebbero ``project_<id>.jsonl``, cioe'
# illeggibili con adb: e' una delle ragioni per cui non lo e'.
_WIKI_ID_RE = re.compile(r"\A[0-9a-f]{12}\Z")


def is_valid_wiki_id(value: Any) -> bool:
    return isinstance(value, str) and bool(_WIKI_ID_RE.match(value))


def new_wiki_id() -> str:
    """Un id nuovo. Casuale e non derivato dal nome, che e' il punto: un id
    derivato dal nome cambierebbe insieme al nome."""
    import secrets

    return secrets.token_hex(6)


def wiki_id(wiki_root: Path) -> str | None:
    """L'id scritto dentro *wiki_root*, o ``None`` se non ce n'e' uno.

    **Un id assente non e' un errore.** Una wiki senza id si indirizza per nome,
    cioe' esattamente come prima del passo 7: quel che perde e' la capacita' di
    ritrovare la propria chat dopo un rinomino. Vale per le wiki create a mano e
    per quelle copiate da un'altra installazione; quelle create dallo scaffolder
    l'id ce l'hanno dalla nascita.

    **Si legge con una regex e non con YAML**, al contrario del resto della
    frontmatter. La ragione e' un difetto vero, visto sul telefono il 22/08: una
    riga di scope con un due punti dentro — «Prova del passo 7: la chat segue» —
    rende il blocco non parsabile, e ``yaml.safe_load`` non perde *quella* riga,
    perde **tutte** le altre. L'id serve a riparare una chat orfana: se lo si
    leggesse con YAML, sarebbe illeggibile esattamente nei file un po' storti,
    cioe' quelli in cui serve. La sua forma e' fissa e non ha bisogno di un
    parser.
    """
    schema = wiki_schema_file(wiki_root) or (
        legacy if (legacy := wiki_root / LEGACY_WIKI_SCHEMA_FILENAME).is_file() else None
    )
    if schema is None:
        return None
    try:
        return wiki_id_in_text(schema.read_text(encoding="utf-8"))
    except OSError:
        return None


def wiki_id_in_text(text: str) -> str | None:
    """L'id che :func:`wiki_id` leggerebbe da un file di istruzioni con questo testo.

    Separato da :func:`wiki_id` perche' la stessa domanda se la fa anche chi sta
    per **scrivere** quel file (:func:`wiki_id_at_risk`): lettore e recinto
    devono rispondere con la stessa regex, o il recinto lascerebbe passare un
    testo da cui il lettore non ritrova l'id.
    """
    raw, _ = split_frontmatter(text)
    match = re.search(rf"^{WIKI_ID_KEY}:\s*(\S+)\s*$", raw, re.M)
    value = match.group(1).strip("\"'") if match else None
    return value if is_valid_wiki_id(value) else None


# La chiave a cui passa un ``id:`` che non e' l'id della wiki — uno scritto a mano
# (``id: piante``), o il secondo di due. **Non si cancella**: e' testo
# dell'utente, e il valore resta dov'era; cambia solo la chiave, cosi' nella
# frontmatter c'e' **un** ``id:`` e nessuno lo scambia per un doppione da
# pulire. Il 26/10 sul Titan un aiutante ha «sistemato» i due ``id:`` di Piante
# tenendo quello sbagliato, e la chat del quaderno e' rimasta chiusa fuori.
PREVIOUS_ID_KEY = "previous_id"


def _id_line_value(line: str) -> str | None:
    match = re.match(rf"{WIKI_ID_KEY}:\s*(\S+)\s*$", line)
    return match.group(1).strip("\"'") if match else None


def settle_wiki_id_lines(text: str, value: str | None = None) -> str:
    """*text* con un solo ``id:`` nella frontmatter: *value*, o il primo valido.

    Ogni altra riga ``id:`` diventa ``previous_id:`` col suo valore intatto. Se
    *value* e' dato e nessuna riga lo porta, entra come **prima** riga della
    frontmatter (aprendone una se manca); senza *value* e senza un id valido le
    righe ``id:`` passano tutte a ``previous_id:`` e non si aggiunge niente —
    l'id nuovo, se serve, lo sceglie il chiamante.

    Testo, non file, e senza riserializzare lo YAML: chiavi, virgolette e
    commenti di un file scritto a mano restano come sono. Un testo gia' a posto
    torna identico, byte per byte — e' cosi' che la migrazione sa di non dover
    scrivere.
    """
    match = FRONTMATTER_RE.match(text)
    if match is None:
        if value is None:
            return text
        return f"---\n{WIKI_ID_KEY}: {value}\n---\n\n{text.lstrip()}"
    lines = match.group(1).split("\n")
    if value is None:
        for line in lines:
            found = _id_line_value(line)
            if is_valid_wiki_id(found):
                value = found
                break
    kept = False
    out: list[str] = []
    for line in lines:
        found = _id_line_value(line)
        if found is None:
            out.append(line)
        elif found == value and not kept:
            kept = True
            out.append(line)
        else:
            out.append(PREVIOUS_ID_KEY + line[len(WIKI_ID_KEY):])
    if value is not None and not kept:
        out.insert(0, f"{WIKI_ID_KEY}: {value}")
    raw = "\n".join(out)
    if raw == match.group(1):
        return text
    return text[: match.start(1)] + raw + text[match.end(1) :]


def wiki_id_at_risk(path: Path, new_text: str) -> str | None:
    """L'id che scrivere *new_text* in *path* toglierebbe alla sua wiki, o ``None``.

    Riguarda solo il file da cui :func:`wiki_id` legge: ``AGENTS.md`` (anche
    quando lo si crea accanto a un ``CLAUDE.md`` che porta l'id, perche' da quel
    momento e' lui che si legge), o ``CLAUDE.md`` su una wiki che non ha ancora
    ``AGENTS.md``. Una wiki **senza** id non ha niente da perdere: nessun
    recinto, come nessun rifiuto in ``AgentLoop._refuse_reincarnated_project``.
    """
    if path.name == LEGACY_WIKI_SCHEMA_FILENAME:
        if (path.parent / WIKI_SCHEMA_FILENAME).is_file():
            return None
    elif path.name != WIKI_SCHEMA_FILENAME:
        return None
    current = wiki_id(path.parent)
    if current is None or wiki_id_in_text(new_text) == current:
        return None
    return current


def wiki_id_refusal(path: Path, current: str) -> str:
    """Il rifiuto per una scrittura che toglierebbe l'id: dice cosa rifare."""
    return (
        f"Error: not written. {path.name} must keep the line `{WIKI_ID_KEY}: {current}` "
        "in its frontmatter: it is how this notebook finds its own chat again, and "
        "without it the chat is shut out. Write your change again leaving that line "
        "exactly as it is; any other change to the file is fine."
    )


def keep_wiki_id(path: Path, new_text: str) -> str:
    """*new_text* con l'id della wiki rimesso, se scriverlo in *path* lo toglierebbe.

    Per le scritture dell'utente — l'editor della WebUI, l'installazione di una
    bozza — dove un rifiuto sarebbe un cancello sul suo stesso file: l'id e' di
    Jenny, e lo si rimette in silenzio. Gli strumenti dell'agente invece
    **rifiutano** (:func:`wiki_id_refusal`): al modello il rifiuto insegna.
    """
    current = wiki_id_at_risk(path, new_text)
    if current is None:
        return new_text
    logger.info("wiki {}: id {} kept in {} on save", path.parent.name, current, path.name)
    return settle_wiki_id_lines(new_text, current)


def find_wiki_by_id(wikis_dir: Path, target: str) -> Path | None:
    """La cartella della wiki che dichiara *target*, o ``None``.

    **Rifiuta invece di scegliere quando due wiki dichiarano lo stesso id.** Ci
    si arriva copiando una cartella, ed e' il caso in cui indovinare mette il
    lavoro nel posto sbagliato: la lezione del passo 6. Meglio una chat che resta
    orfana e lo dice.

    Non c'e' nessuna cache e nessun registro: la mappa ``id -> cartella`` si
    ricalcola dalle cartelle ogni volta che serve. E' la ragione per cui non puo'
    divergere — non esiste una seconda copia da tenere allineata — e costa una
    lettura di frontmatter per wiki, su un giro che parte solo quando una
    cartella legata e' sparita.
    """
    if not is_valid_wiki_id(target):
        return None
    found: list[Path] = []
    for index in discover_wikis(wikis_dir).values():
        root = index.parent
        if wiki_id(root) == target:
            found.append(root)
    if len(found) == 1:
        return found[0]
    if len(found) > 1:
        logger.warning(
            "wiki id {} declared by {} folders ({}): no pick made, it is ambiguous",
            target,
            len(found),
            ", ".join(sorted(p.name for p in found)),
        )
    return None


# ── Wikilink ─────────────────────────────────────────────────────────────────
#
# Qui e non in ``webui/wiki.py``: la bacheca e i comandi la chiedono senza
# il renderer, che tira dentro il pacchetto ``markdown``.


def slugify(name: str) -> str:
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    name = name.lower()
    name = re.sub(r"[\s_]+", "-", name)
    name = re.sub(r"[^a-z0-9.\-]", "", name)
    return name.strip("-")


def _normalize_page_path(path: str) -> str:
    parts = path.split("/")
    slugged = [slugify(p) for p in parts]
    if not slugged[-1].endswith(".md"):
        slugged[-1] += ".md"
    return "/".join(slugged)


def strip_wiki_prefix(page_ref: str) -> str:
    """Remove a leading wiki/ prefix used when pages are stored under wiki/."""
    if page_ref.startswith("wiki/"):
        return page_ref[5:]
    return page_ref


def suffix_rank(path: Path) -> tuple[int, str]:
    """Ordine di preferenza fra più pagine che finiscono per lo stesso path.

    Vince la più vicina alla radice, a pari profondità la prima in ordine
    alfabetico. Serve solo a rendere la scelta **deterministica**: senza una
    regola, l'ordine di ``rglob`` è quello del filesystem, quindi lo stesso link
    poteva aprire due pagine diverse su due telefoni. La stessa regola è copiata
    in ``jenny/skills/llm-wiki/scripts/lint_wiki.py::page_for_link`` — il lint
    non può importare il package — e i due test la confrontano.
    """
    return (len(path.parts), path.as_posix())


def resolve_wikilink(wiki_root: Path, target: str) -> Path | None:
    """Resolve a wikilink target to a file path.

    Tries: exact path, with .md, case-insensitive, slug, path suffix, stem.
    wiki_root is the wiki root directory (contains wiki/ and audit/).

    Il ramo *path suffix* vale solo per un bersaglio a più segmenti, ed è la
    generalizzazione di quello per stem: ``[[<aspect>]]`` trovava una pagina
    ovunque sotto ``wiki/``, mentre ``[[<Topic>/<aspect>]]`` — più specifico, e
    la forma che ``references/article-guide.md`` raccomanda dentro un
    ``index.md`` diviso in cartella — non trovava niente, perché nessuno stem
    contiene una barra. Il link più preciso moriva dove quello più vago apriva.
    """
    pages_dir = wiki_root / "wiki"
    target = strip_wiki_prefix(target)
    # Exact path
    p = pages_dir / target
    if p.is_file():
        return p
    # Directory with index.md
    if p.is_dir():
        index = p / "index.md"
        if index.is_file():
            return index
    # With .md
    p = pages_dir / f"{target}.md"
    if p.is_file():
        return p
    # Case-insensitive on relative path
    target_norm = _normalize_page_path(target)
    # I candidati del ramo per suffisso si raccolgono nello stesso passaggio di
    # ``rglob``: su una wiki vera sono centinaia di file su flash, e leggere due
    # volte la stessa directory è l'unica parte davvero cara.
    suffixes: tuple[str, ...] = ()
    if "/" in target:
        low = target.lower()
        suffixes = (
            f"/{target_norm.lower()}",  # <Topic>/<aspect>  → …/<topic>/<aspect>.md
            f"/{low}/index.md",  # <Topic>/<sub>     → …/<topic>/<sub>/index.md
            f"/{target_norm.lower().removesuffix('.md')}/index.md",
        )
    candidates: list[Path] = []
    for f in pages_dir.rglob("*.md"):
        rel = f.relative_to(pages_dir).as_posix()
        if rel.lower() == target_norm.lower():
            return f
        if rel.lower().removesuffix(".md") == target.lower():
            return f
        if suffixes:
            rel_low = rel.lower()
            if rel_low.endswith(suffixes) or rel_low.removesuffix(".md").endswith(f"/{low}"):
                candidates.append(f)
    if candidates:
        return min(candidates, key=suffix_rank)
    # Stem search
    return _find_by_stem(pages_dir, target)


def _find_by_stem(dir_path: Path, target: str) -> Path | None:
    """La pagina con questo stem, **scelta in modo deterministico**.

    Due pagine con lo stesso nome in cartelle diverse (`a/nota.md`, `b/nota.md`)
    esistono davvero, e qui vinceva quella che ``rglob`` restituiva per prima —
    cioè l'ordine della directory sul filesystem. Lo stesso `[[nota]]` poteva
    aprire due pagine diverse, e il lint (che indicizza per chiave, non per
    ordine di lettura) non poteva concordare con nessuna delle due. Ora la regola
    è scritta: ``suffix_rank``, la stessa del ramo per suffisso.

    Un solo livello, insensibile alle maiuscole. Prima ce n'erano due — prima i
    nomi identici carattere per carattere, poi quelli uguali a meno di
    maiuscole — e il primo livello il lint non può esprimerlo, perché indicizza
    per chiave minuscola. Serviva soltanto per due pagine che differiscono *solo*
    per maiuscole nella stessa wiki, e in quel caso la regola di ordine sceglie
    comunque la stessa delle due.
    """
    lowered = target.lower()
    matches = [
        f
        for f in dir_path.rglob("*.md")
        if f.stem.lower() in (lowered, lowered.removesuffix(".md"))
    ]
    return min(matches, key=suffix_rank) if matches else None


# ── Discovery ────────────────────────────────────────────────────────────────


def is_wiki_root(path: Path) -> bool:
    """Vero se ``path`` e' la radice di una wiki, cioe' contiene ``wiki/``.

    E' la definizione che il picker usa gia' via :func:`discover_wikis`, estratta
    perche' ora la chiede anche il prompt: ``agent/project.md`` si rende solo
    quando la cartella del turno e' una wiki, e la stessa domanda gliela fa il
    subagent (che ha la cartella ma non la chiave di sessione). Un secondo modo
    di dire "questa e' una wiki" sarebbe un secondo modo di sbagliarlo.
    """
    return (path / "wiki").is_dir()


def notebook_dir(wikis_dir: Path, name: str) -> Path | None:
    """La cartella (risolta) del quaderno *name* in *wikis_dir*, o ``None`` se ne esce.

    Il nome arriva da una chiave di sessione, da un argomento del modello o da
    un client: un ``..``, un separatore o il nome vuoto non devono portare fuori
    dalla cartella dei quaderni, né in una sottocartella di un quaderno. Un
    quaderno è un figlio diretto di ``wikis/`` (come in :func:`discover_wikis`).
    Non guarda se esiste: chi vuole un quaderno vero aggiunge :func:`is_wiki_root`.
    """
    wikis = wikis_dir.resolve(strict=False)
    root = (wikis / name).resolve(strict=False)
    return root if root.parent == wikis and root != wikis else None


def discover_wikis(wikis_dir: Path) -> dict[str, Path]:
    """Scan wikis_dir for subdirectories containing a wiki/ folder.

    Returns {name: wikis_dir/name/wiki} sorted alphabetically by name.
    """
    if not wikis_dir.exists():
        return {}
    result: dict[str, Path] = {}
    for child in sorted(wikis_dir.iterdir()):
        if child.is_dir() and is_wiki_root(child):
            result[child.name] = child / "wiki"
    return result


def discover_wiki_roots(wikis_dir: Path) -> dict[str, Path]:
    """Come :func:`discover_wikis`, ma restituisce la *radice* di ogni wiki.

    ``discover_wikis`` punta alla pages-dir ``wikis/<name>/wiki``; chi deve
    leggere ``CLAUDE.md`` o contare gli audit ha bisogno del livello sopra.
    """
    return {name: pages.parent for name, pages in discover_wikis(wikis_dir).items()}


def read_wiki_scope(wiki_root: Path) -> str:
    """Riga di scope di una wiki, nello stesso ordine di priorità del registry.

    1. ``summary:`` (o ``scope:``) nel frontmatter del file di istruzioni
       (``AGENTS.md``, e **solo** quello: v. :func:`wiki_schema_file`).
    2. Primo bullet reale sotto "What this wiki covers" nella sezione ``## Scope``.
    3. Un fallback neutro, così l'output resta deterministico.

    I placeholder del template (``<...>``) sono ignorati a ogni livello. La
    logica è la stessa di ``skills/llm-wiki/scripts/reindex_wikis.py``, ma non la
    importiamo: quello script è un checkout della skill, destinato a essere
    copiato nel workspace e modificato dall'utente, non una libreria del package.

    **Nessun ripiego sul nome vecchio**, dal passo 7.5: una wiki non ancora
    migrata torna ``(no AGENTS.md)``, che è la verità — e la finestra la chiude
    l'avvio dopo, quando ``utils/wiki_migration.py`` la rinomina. Il nome vecchio
    resta noto a chi lo rinomina e a :func:`wiki_id`, che deve leggere l'identità
    di una wiki proprio dentro quella finestra; nessuno dei due legge uno scope.
    """
    schema = wiki_schema_file(wiki_root)
    if schema is None:
        return "(no AGENTS.md)"
    try:
        text = schema.read_text(encoding="utf-8")
    except OSError:
        return f"(unreadable {schema.name})"

    explicit = _frontmatter_scalar(text, "summary", "scope")
    if explicit:
        return explicit

    in_scope = False
    in_covers = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            in_scope = stripped[3:].strip().lower() == "scope"
            in_covers = False
            continue
        if not in_scope:
            continue
        if stripped.lower().startswith("what this wiki covers"):
            in_covers = True
            continue
        if in_covers and stripped.startswith("- "):
            bullet = stripped[2:].strip()
            return "(no scope set)" if _is_placeholder(bullet) else bullet
        if in_covers and stripped.lower().startswith("what this wiki"):
            break  # sezione "excludes" raggiunta senza un bullet reale
    return "(no scope set)"


def _is_placeholder(value: str) -> bool:
    return "<" in value and ">" in value


def _frontmatter_scalar(text: str, *keys: str) -> str | None:
    """Primo scalare non-placeholder fra *keys* nel frontmatter YAML iniziale."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return None
    for line in m.group(1).split("\n"):
        if ":" not in line or line.lstrip().startswith("#"):
            continue
        key, _, val = line.partition(":")
        if key.strip() in keys:
            v = val.strip().strip('"').strip("'")
            if v and not _is_placeholder(v):
                return v
    return None
