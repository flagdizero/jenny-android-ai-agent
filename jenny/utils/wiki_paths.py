"""Discovery, frontmatter e fingerprint delle wiki del workspace.

Layer neutro, senza dipendenze su ``webui/`` o ``agent/``: entrambi devono
sapere dove vivono le wiki e come leggerne l'intestazione, e farlo importare
all'uno dall'altro sarebbe un'inversione di layer. ``webui/wiki.py`` re-importa
questi nomi in cima al modulo, così la sua API pubblica (``discover_wikis``) e i
suoi helper privati restano dove i chiamanti li hanno sempre trovati.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, overload

if TYPE_CHECKING:
    from datetime import date

from loguru import logger

_FRONTMATTER_RE = re.compile(r"^---\n([\s\S]*?)\n---\n?")

# Il **registro delle wiki**: ``wikis/_index.md``, una riga per wiki. Non e' la
# mappa di una wiki — quella e' :data:`WIKI_INDEX_FILENAME`, ``wiki/index.md``,
# e i due nomi distavano un underscore (T3.12). Sono due file diversi a due
# livelli diversi dell'albero: qui "registry" e la' "index", cosi' che leggere
# uno dei due non richieda di ricordarsi dell'altro.
_WIKIS_REGISTRY_FILENAME = "_index.md"
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


# ── Frontmatter ──────────────────────────────────────────────────────────────


def extract_title(text: str) -> str | None:
    """Titolo di una pagina: ``title:`` nel frontmatter, altrimenti il primo H1."""
    fm = _FRONTMATTER_RE.match(text)
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
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return "", text
    return m.group(1), text[m.end() :]


def strip_frontmatter(text: str) -> tuple[dict[str, Any] | None, str, str | None]:
    """Return ``(frontmatter, body, title)`` for a page's raw markdown."""
    m = _FRONTMATTER_RE.match(text)
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
    saltava i nascosti, ``webui/wiki.py::iter_page_files`` no — quindi un
    ``.bozza.md`` sotto ``wiki/`` non arrivava al modello ma diventava un nodo
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
# Da non confondere con :data:`_WIKIS_REGISTRY_FILENAME` (``wikis/_index.md``),
# che e' il registro delle wiki e non la mappa di una.
WIKI_INDEX_FILENAME = "index.md"


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
    ``context.py::_PROJECT_MAP_MAX_CHARS``) e l'estensione. Il confronto su
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
    su una copia nello scratchpad con alberi duplicati e una wiki blackberry che sul
    telefono non c'e', quindi i valori assoluti qui sopra non sono quelli del
    dispositivo: vale il prima/dopo, non il numero.

    **Una manopola e non una seconda funzione**: la lettura e' la stessa
    camminata, e un secondo nome sarebbe il nome che il chiamante nuovo non
    conosce. E il valore torna in una forma **diversa** — percorsi nudi, non
    coppie col titolo vuoto — perche' un titolo finto e' un titolo che finisce in
    un prompt: cosi' chi lo usasse per sbaglio si rompe subito invece di
    stampare una riga muta. Gli ``@overload`` servono a dirlo al type checker.
    """
    if not pages_dir.is_dir():
        return []
    found: list[tuple[str, Path]] = []
    for path in sorted(pages_dir.rglob("*.md")):
        rel = path.relative_to(pages_dir)
        if not is_wiki_page_rel(rel):
            continue
        # L'esclusione dell'indice sta **qui e non nel predicato**: e' l'unica
        # cosa che distingue questo elenco da quello di grafo, albero e ricerca,
        # e per la mappa il prompt ha un blocco suo.
        #
        # **Il confronto e' sensibile alle maiuscole, di proposito** (T9.5). Un
        # ``wiki/INDEX.md`` sull'unico runtime che esiste — Android, filesystem
        # sensibile alle maiuscole — **non e'** il file che l'iniettore apre come
        # mappa (``context.py::_read_map_source`` chiede esattamente
        # ``index.md``). Confrontare senza maiuscole lo escluderebbe da qui
        # *senza* che entri come mappa: il suo contenuto smetterebbe di
        # raggiungere il modello in tutt'e due i modi. Oggi almeno arriva come
        # pagina, che e' il peggio-caso giusto.
        if rel.as_posix() == WIKI_INDEX_FILENAME:
            continue
        found.append((rel.as_posix(), path))
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
# file — l'indirizzo di una chat resta il nome della cartella (v.
# ``roadmap/progetti-passi.md``, passo 7, strada B) — quindi non deve essere
# leggibile, deve solo essere improbabile da ripetere. Se un domani diventasse
# l'indirizzo, i nomi dei file diventerebbero ``project_<id>.jsonl``, cioe'
# illeggibili con adb: e' una delle ragioni per cui non lo e'.
_WIKI_ID_RE = re.compile(r"^[0-9a-f]{12}$")


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
        raw, _ = split_frontmatter(schema.read_text(encoding="utf-8"))
    except OSError:
        return None
    match = re.search(rf"^{WIKI_ID_KEY}:\s*(\S+)\s*$", raw, re.M)
    value = match.group(1).strip("\"'") if match else None
    return value if is_valid_wiki_id(value) else None


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
    m = _FRONTMATTER_RE.match(text)
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
