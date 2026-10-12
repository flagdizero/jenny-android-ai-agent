"""Testo della memoria messo a confronto: quanto si somigliano due fatti.

Serve all'archivio per capire quale voce nuova ha preso il posto di quale
vecchia quando una scrittura a file intero (``apply_patch``, ``edit_file``) ne
cambia più d'una insieme (v. ``memory_entries.make_entry_archiver``).

Solo libreria standard, come tutta la memoria: niente encoder né embedding sul
telefono (impegno 5 del piano della memoria).
"""

from __future__ import annotations

import re
import unicodedata

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
