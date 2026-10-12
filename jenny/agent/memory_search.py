"""Testo della memoria messo a confronto: somiglianza fra fatti, e ricerca.

Due usi. L'archivio vuole sapere quale voce nuova ha preso il posto di quale
vecchia quando una scrittura a file intero (``apply_patch``, ``edit_file``) ne
cambia più d'una insieme (v. ``memory_entries.make_entry_archiver``): è
:func:`similarity`. ``recall`` e ``recall_history`` cercano fra centinaia di
voci con le parole che il modello sceglie: è :func:`search`.

La ricerca è per **sottostringa**, come il ``grep`` che il modello già usa e che
sull'emulatore (12/10/2026) ha trovato al primo colpo un fatto archiviato con
``grandfather|nonno|sailing|barca|vela|boat``. Non per radice: "vet" deve
trovare "veterinario", e una radice di cinque lettere non lo fa. Il bilinguismo
lo porta il modello, che passa le parole in tutte e due le lingue; qui si
pesano soltanto, perché una parola che compare dappertutto non distingua niente.

Solo libreria standard, come tutta la memoria: niente encoder né embedding sul
telefono (impegno 5 del piano della memoria).
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterable

_TOKEN_RE = re.compile(r"[0-9a-z]+")

# Sotto i tre caratteri un token è quasi sempre un articolo, una preposizione o
# un numero d'ordine: "il", "di", "of", "2". Non dice niente su quale fatto sia.
_MIN_TOKEN_LEN = 3

# La radice è il token tagliato a cinque caratteri: grezza, ma tiene insieme
# singolare e plurale, maschile e femminile, le coniugazioni più comuni —
# "pianta"/"piante", "lavora"/"lavorare", "plant"/"plants" — in tutte e due le
# lingue, senza un dizionario per ciascuna.
_STEM_LEN = 5


def fold(text: str) -> str:
    """NFKD, via i diacritici, minuscolo.

    Copia di ``jenny.webui.wiki_search.fold`` e non un import: quel modulo tira
    dentro ``jenny.webui.wiki`` e con lui il pacchetto ``markdown``, che il
    livello degli agenti non deve richiedere per confrontare due righe.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    without_marks = "".join(c for c in decomposed if not unicodedata.combining(c))
    return without_marks.lower()


def stems(text: str) -> list[str]:
    """Le radici dei token di *text*, nell'ordine in cui compaiono."""
    return [
        t[:_STEM_LEN] for t in _TOKEN_RE.findall(fold(text)) if len(t) >= _MIN_TOKEN_LEN
    ]


def similarity(a: str, b: str) -> float:
    """Quanto due fatti parlano della stessa cosa, fra 0 e 1.

    Jaccard sulle radici: due versioni dello stesso fatto condividono il
    soggetto e quasi tutti i nomi anche quando una aggiunge un dettaglio o lo
    toglie, due fatti diversi della stessa sezione condividono al più qualche
    parola di contorno.
    """
    sa, sb = set(stems(a)), set(stems(b))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def query_terms(query: Iterable[str]) -> list[str]:
    """Le parole di ricerca, piegate e senza doppioni, nell'ordine dato.

    Ogni stringa si spezza in parole: "sailing boat" cerca "sailing" e "boat",
    non la frase intera, che in un archivio scritto in un'altra lingua non c'è.
    """
    seen: dict[str, None] = {}
    for raw in query:
        for token in _TOKEN_RE.findall(fold(str(raw))):
            if len(token) >= _MIN_TOKEN_LEN:
                seen.setdefault(token, None)
    return list(seen)


def search(docs: list[str], terms: list[str]) -> list[tuple[int, float, list[str]]]:
    """I documenti che contengono almeno una parola, dal più pertinente.

    Ritorna ``(indice, punteggio, parole trovate)``. Il punteggio è la somma
    dell'IDF delle parole trovate: due parole rare battono una comune, e una che
    compare in metà dell'archivio pesa poco. A parità di punteggio vince
    l'ordine d'ingresso, che i chiamanti danno dal più recente.
    """
    folded = [fold(d) for d in docs]
    n = len(folded)
    hits: dict[str, list[int]] = {
        term: [i for i, text in enumerate(folded) if term in text] for term in terms
    }
    weight = {term: math.log(1 + n / len(found)) for term, found in hits.items() if found}
    scores: dict[int, float] = {}
    matched: dict[int, list[str]] = {}
    for term, found in hits.items():
        for i in found:
            scores[i] = scores.get(i, 0.0) + weight[term]
            matched.setdefault(i, []).append(term)
    ranked = sorted(scores, key=lambda i: (-scores[i], i))
    return [(i, scores[i], matched[i]) for i in ranked]
