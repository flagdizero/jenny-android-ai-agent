"""Indice full-text delle pagine wiki, spedito al grafo per accendere i nodi.

Il problema che questo modulo risolve non è "cercare": è cercare *mentre si
digita*, su un telefono, senza che ogni tasto premuto costi un giro di I/O.

La forma della soluzione discende da tre vincoli:

1. **L'I/O è l'unica parte cara.** Tokenizzare qualche centinaio di KB di
   markdown costa millisecondi; leggerli dalla flash ne costa centinaia. Perciò
   l'indice si costruisce nella *stessa* passata del grafo
   (:func:`jenny.webui.wiki.read_pages`) e si ricostruisce solo quando il
   contenuto è davvero cambiato — verifica che si fa con degli ``stat``, senza
   aprire un file.
2. **Chi interroga è il client, non il server.** L'indice viaggia una volta
   sola insieme al grafo e poi vive nella WebView: da lì in poi ogni carattere
   digitato è aritmetica locale, zero round-trip, zero latenza percepita.
3. **Il risultato deve essere già nella forma che serve al grafo.** Le postings
   non contengono id di pagina ma **indici numerici nell'array ``nodes`` del
   grafo**, così il client trasforma una query in una maschera ``Uint8Array``
   indicizzabile direttamente nel ciclo di disegno, senza mappe intermedie.

Il punto 3 è anche il motivo per cui indice e grafo viaggiano nella *stessa*
risposta HTTP: sono due metà di un unico oggetto, e servirli da due endpoint
distinti aprirebbe la finestra in cui la wiki cambia fra le due chiamate,
lasciando postings che puntano a nodi diversi da quelli disegnati.

Cosa resta in memoria fra una richiesta e l'altra: il grafo e l'indice
*impacchettato*, non il corpus. Il testo delle pagine viene letto, consumato e
buttato dentro la stessa chiamata.

Limite noto e deliberato: i token sono ``[0-9a-z]+`` **dopo** il folding NFKD
(v. :func:`fold`), cioè accenti e diacritici spariscono e "però" indicizza
"pero". Copre italiano e inglese, che sono il corpus, e ha il pregio di essere
riproducibile alla lettera in JavaScript — dove ``\\w`` resta ASCII anche col
flag ``u``, e una classe di caratteri "unicode-aware" divergerebbe in silenzio
fra i due lati. Una wiki in greco o cirillico non sarebbe cercabile: il rimedio
sarebbe allargare :data:`_TOKEN_RE` **e** il suo gemello nel client insieme.
"""

from __future__ import annotations

import base64
import hashlib
import re
import sys
import threading
from array import array
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jenny.utils.wiki_paths import fold, split_frontmatter
from jenny.webui.wiki import (
    GraphData,
    PageSource,
    build_graph_from_pages,
    iter_page_files,
    read_pages,
)

# ── Tokenizzazione ───────────────────────────────────────────────────────────

_TOKEN_RE = re.compile(r"[0-9a-z]+")
_HEADING_RE = re.compile(r"^#{1,6}\s+(.+)$", re.M)
_TAGS_RE = re.compile(r"^tags:[ \t]*(.*(?:\n[ \t]+-[ \t]*.*)*)", re.M)

# Token più corti di così non entrano nel dizionario: sono quasi solo articoli e
# preposizioni, gonfierebbero i termini senza distinguere niente. Il client li
# tratta come "nessun vincolo" invece che come "zero risultati" — v. la nota
# sui termini universali in :func:`pack_index`.
_MIN_TOKEN_LEN = 2

# Pesi per campo. Un titolo che contiene la parola cercata dice molto di più di
# una sua occorrenza a metà di un paragrafo. Servono all'ordinamento della lista
# risultati; per accendere i nodi basta l'appartenenza.
_W_TITLE = 4
_W_PATH = 3
_W_TAG = 3
_W_HEADING = 2
_W_BODY = 1

_WEIGHT_MAX = 255  # i pesi viaggiano in un Uint8Array

# Sopra questa frazione di documenti un termine non discrimina più niente: le
# sue postings peserebbero quanto tutto il resto dell'indice per non escludere
# nessuno. La soglia assoluta evita che su una wiki di cinque pagine "il 60%"
# significhi "tre pagine", trasformando parole normalissime in universali.
_UNIVERSAL_DF_RATIO = 0.6
_UNIVERSAL_MIN_DOCS = 34


def tokenize(text: str) -> list[str]:
    """Token indicizzabili di un testo, nell'ordine in cui compaiono."""
    return [t for t in _TOKEN_RE.findall(fold(text)) if len(t) >= _MIN_TOKEN_LEN]


# ── Costruzione dell'indice ──────────────────────────────────────────────────


def _extract_tags(frontmatter_text: str) -> str:
    """Valori di ``tags:`` come testo grezzo (lista inline o a trattini)."""
    m = _TAGS_RE.search(frontmatter_text)
    return m.group(1) if m else ""


def _page_weights(page: PageSource) -> dict[str, int]:
    """Token → peso per una singola pagina.

    Le occorrenze *non* si contano: un termine ripetuto trenta volte in una
    pagina lunga non la rende più pertinente di una pagina che lo ha nel
    titolo, e contarle farebbe vincere sistematicamente le pagine più prolisse.
    Conta invece *dove* compare, una volta per campo.
    """
    frontmatter_text, body = split_frontmatter(page.text)
    weights: dict[str, int] = {}

    def add(text: str, weight: int) -> None:
        for token in set(tokenize(text)):
            weights[token] = min(_WEIGHT_MAX, weights.get(token, 0) + weight)

    add(page.title, _W_TITLE)
    # Il path è cercabile: una pagina ``concepts/doze-mode.md`` deve rispondere
    # a "doze" anche se il titolo è "Sonno profondo di Android".
    add(page.rel.removesuffix(".md").replace("/", " ").replace("-", " "), _W_PATH)
    add(_extract_tags(frontmatter_text), _W_TAG)
    for heading in _HEADING_RE.findall(body):
        add(heading, _W_HEADING)
    add(body, _W_BODY)
    return weights


@dataclass(frozen=True)
class SearchIndex:
    """Indice invertito pronto per l'impacchettamento.

    ``postings[term]`` mappa *indice del nodo nel grafo* → peso. Non id: gli
    indici sono ciò che il client usa per scrivere direttamente nella maschera.
    """

    doc_count: int
    postings: dict[str, dict[int, int]]

    @classmethod
    def from_pages(cls, pages: list[PageSource], graph: GraphData) -> SearchIndex:
        """Indicizza le pagine contro l'ordine dei nodi di *graph*.

        Le pagine che non hanno un nodo nel grafo vengono ignorate: la ricerca
        accende nodi, e un risultato senza nodo da accendere non è un risultato.
        """
        node_index = {node.id: i for i, node in enumerate(graph.nodes)}
        postings: dict[str, dict[int, int]] = {}
        for page in pages:
            idx = node_index.get(page.node_id)
            if idx is None:
                continue
            for token, weight in _page_weights(page).items():
                postings.setdefault(token, {})[idx] = weight
        return cls(doc_count=len(graph.nodes), postings=postings)


# ── Formato di trasporto ─────────────────────────────────────────────────────


def _b64_ints(values: list[int], typecode: str) -> str:
    """Impacchetta interi little-endian in base64.

    Il client li rilegge con ``new Int32Array(buffer)`` / ``Uint16Array``: una
    memcpy, invece di centinaia di migliaia di ``JSON.parse`` di numeri. Le
    TypedArray usano l'endianness della piattaforma e non c'è modo di forzarla,
    ma qui server e client girano sullo *stesso* dispositivo — resta il
    byteswap difensivo per l'unico caso in cui la costruzione avvenisse su un
    host big-endian.
    """
    buf = array(typecode, values)
    if sys.byteorder != "little":
        buf.byteswap()
    return base64.b64encode(buf.tobytes()).decode("ascii")


def _b64_uint8(values: list[int]) -> str:
    return base64.b64encode(bytes(values)).decode("ascii")


def pack_index(index: SearchIndex, version: str) -> dict[str, Any]:
    """Serializza l'indice nel formato che il client sa montare senza parsare.

    Struttura sul filo:

    - ``terms``: i termini ordinati, uniti da ``\\n``. Ordinati perché il client
      cerca il *range di prefisso* dell'ultimo token digitato per bisezione —
      è ciò che rende la ricerca incrementale mentre si scrive.
    - ``offsets``: ``n_terms + 1`` interi; le postings del termine *i* stanno in
      ``[offsets[i], offsets[i+1])``.
    - ``postings``: indici di nodo, ordinati crescenti dentro ogni termine.
      Larghi 16 bit — sono l'array dominante del payload, e una wiki con più di
      65.535 pagine su un telefono non esiste; sopra quella soglia si passa a
      32 bit invece di troncare in silenzio. Il campo ``bits`` dice al client
      quale TypedArray montare.
    - ``weights``: paralleli alle postings, un byte ciascuno.

    **Termini universali.** Un termine presente in quasi tutti i documenti non
    restringe nulla, e le sue postings peserebbero quanto mezzo indice. Quei
    termini restano nel dizionario ma con un range di postings *vuoto*: il
    client li interpreta come "nessun vincolo" invece che come "zero
    risultati", che è la semantica giusta per un articolo o una preposizione.
    L'invariante che rende leggibile la codifica è che un termine esiste solo
    se almeno un documento lo contiene, quindi un range vuoto non può mai
    significare davvero "nessun documento".
    """
    universal_threshold = max(
        _UNIVERSAL_MIN_DOCS, int(index.doc_count * _UNIVERSAL_DF_RATIO)
    )

    terms = sorted(index.postings)
    offsets: list[int] = [0]
    flat_nodes: list[int] = []
    flat_weights: list[int] = []
    universal = 0

    for term in terms:
        docs = index.postings[term]
        if len(docs) > universal_threshold:
            universal += 1
        else:
            for node_idx in sorted(docs):
                flat_nodes.append(node_idx)
                flat_weights.append(docs[node_idx])
        offsets.append(len(flat_nodes))

    bits = 16 if index.doc_count <= 0xFFFF else 32
    return {
        "version": version,
        "docs": index.doc_count,
        "terms": "\n".join(terms),
        "offsets": _b64_ints(offsets, "i"),
        "postings": _b64_ints(flat_nodes, "H" if bits == 16 else "i"),
        "bits": bits,
        "weights": _b64_uint8(flat_weights),
        "universal": universal,
    }


# ── Cache ────────────────────────────────────────────────────────────────────


def fingerprint(pages_dir: Path) -> str:
    """Impronta del contenuto di una wiki, calcolata senza leggere i file.

    ``(rel, mtime_ns, size)`` per ogni pagina che finirebbe nel grafo: un'impronta
    dell'insieme del grafo, per il solo consumatore della ricerca.
    """
    digest = hashlib.sha256()
    for rel, path in iter_page_files(pages_dir):
        try:
            st = path.stat()
            marker = f"{st.st_mtime_ns}:{st.st_size}"
        except OSError:
            marker = "-"
        digest.update(f"{rel}\x00{marker}\n".encode())
    return digest.hexdigest()


@dataclass(frozen=True)
class WikiBundle:
    """Grafo e indice della stessa istantanea della wiki."""

    version: str
    graph: GraphData
    search: dict[str, Any]


class WikiSearchService:
    """Cache di :class:`WikiBundle` per wiki, invalidata dal fingerprint.

    Il caso normale — si entra nella tab e nulla è cambiato dall'ultima volta —
    costa una passata di ``stat`` e nient'altro: prima di questa cache ogni
    ingresso rileggeva da capo il contenuto di tutta la wiki solo per
    ridisegnare lo stesso grafo.

    Il caso "qualcosa è cambiato" ricostruisce tutto, non solo il file toccato.
    È una scelta: tenere le postings per-pagina permetterebbe un aggiornamento
    incrementale, ma costringerebbe a trattenere in memoria il vocabolario di
    ogni pagina — e comunque non eviterebbe la rifusione dell'indice, che è
    metà del lavoro. Su una wiki personale (decine o poche centinaia di pagine)
    la ricostruzione completa sta abbondantemente sotto il secondo, in un
    thread di executor, e succede al più una volta per ogni scrittura
    dell'agente. La soglia oltre cui il conto cambia sono le migliaia di
    pagine; a quel punto il posto dove intervenire è qui.
    """

    def __init__(self, max_wikis: int = 8) -> None:
        self._max_wikis = max_wikis
        self._entries: OrderedDict[str, WikiBundle] = OrderedDict()
        # Le route girano in thread di executor: due richieste concorrenti sulla
        # stessa wiki devono ricostruire una volta sola, non due.
        self._lock = threading.Lock()

    def bundle(self, pages_dir: Path) -> WikiBundle:
        """Grafo + indice della wiki, dalla cache se il contenuto non è mutato."""
        key = str(pages_dir)
        with self._lock:
            version = fingerprint(pages_dir)
            cached = self._entries.get(key)
            if cached is not None and cached.version == version:
                self._entries.move_to_end(key)
                return cached

            pages = read_pages(pages_dir)
            graph = build_graph_from_pages(pages)
            index = SearchIndex.from_pages(pages, graph)
            bundle = WikiBundle(version=version, graph=graph, search=pack_index(index, version))

            self._entries[key] = bundle
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_wikis:
                self._entries.popitem(last=False)
            return bundle

    def invalidate(self) -> None:
        """Svuota la cache (usata dai test; in produzione basta il fingerprint)."""
        with self._lock:
            self._entries.clear()
