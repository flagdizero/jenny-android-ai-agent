"""I fatti persi da un piano: numeri con unità che c'erano e non ci sono più.

Un piano che riscrive le pagine di un quaderno può perdere per strada i dati
concreti — l'umidità ideale di una pianta, la temperatura, la dimensione del
vaso — senza che nessuno l'abbia deciso: la pagina nuova è più ordinata, e il
numero non c'è più. Misurato su una prova vera: delle 384 occorrenze nelle
pagine prima del piano, 119 non comparivano in nessuna pagina dopo.

Il controllo è volutamente grezzo: un «fatto» è un numero con un'unità (°C,
cm, %, giorni, pH…), un intervallo o un rapporto. Non capisce il testo; dice
solo «questo numero c'era e adesso non c'è in nessuna pagina». Decide il
modello, nel passo di verifica: lo rimette, o dice perché è andato.

Per tenere pochi i falsi positivi, prima del confronto ogni fatto si
normalizza: virgola decimale, trattini, spazi, zeri finali, «da X a Y» e
«X to Y» come «X-Y», e le unità ridotte a una forma sola per le due lingue
(«anni» e «years» sono lo stesso fatto: una citazione tradotta non è una
perdita).

Modulo puro: niente I/O, lo chiama il driver in un thread.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Mapping

#: Le voci al massimo nell'elenco per il modello; il totale vero si dice a parte.
MAX_LOST = 40
_MAX_CONTEXT = 160
_WIKI_PREFIX = "wiki/"

# Le unità, dalla forma scritta a quella canonica. L'ordine conta: le forme
# lunghe prima delle corte («ml» prima di «m», «settimane» prima di «s…»).
_UNITS: tuple[tuple[str, str], ...] = (
    (r"[°º]\s?C", "°c"),
    (r"[°º]\s?F", "°f"),
    (r"[°º]", "°"),
    (r"millilitri|millilitro|milliliters?|millilitres?|ml", "ml"),
    (r"litri|litro|liters?|litres?|l", "l"),
    (r"mm", "mm"),
    (r"cm", "cm"),
    (r"metri|metro|meters?|metres?|m", "m"),
    (r"kg", "kg"),
    (r"grammi|grammo|grams?|g", "g"),
    (r"%", "%"),
    (r"ore|ora|hours?|hrs?|h", "h"),
    (r"giorni|giorno|days?|gg", "d"),
    (r"settimane|settimana|weeks?|wks?", "w"),
    (r"mesi|mese|months?", "mo"),
    (r"anni|anno|years?|yrs?", "y"),
    (r"lux", "lux"),
    (r"ppm", "ppm"),
    (r"pollici|pollice|inches|inch", "in"),
    (r"ft|feet|foot|piedi", "ft"),
)
# Un'unità non è seguita da una lettera («m» di «mesi» no, «g» di «gelo» no);
# il «%» e il «°» possono esserlo.
_UNIT_RX = "|".join(f"(?:{rx})" for rx, _ in _UNITS)
_UNIT = rf"(?:{_UNIT_RX})(?![^\W\d_²³'’])"
_CANON = [(re.compile(rf"^(?:{rx})$", re.IGNORECASE), canon) for rx, canon in _UNITS]

# Un numero, con il segno solo se attaccato («-5 °C», non il trattino di un
# elenco) e mai a metà di una parola o di un altro numero.
_NUM = r"(?<![\w.,])(?:-(?=\d))?\d+(?:[.,]\d+)?"
_NUM_BARE = r"-?\d+(?:[.,]\d+)?"

# Gli intervalli scritti a parole diventano «X-Y» prima della ricerca.
_WORD_RANGE = re.compile(
    rf"\b(?:da|tra|fra|from|between)\s+({_NUM_BARE})\s*(?:{_UNIT}\s*)?"
    rf"(?:a|e|to|and)\s+({_NUM_BARE})(?=\s*{_UNIT})",
    re.IGNORECASE,
)
_TO_RANGE = re.compile(rf"({_NUM})\s*(?:{_UNIT}\s*)?\bto\s+({_NUM_BARE})(?=\s*{_UNIT})", re.IGNORECASE)
# «18 °C - 27 °C»: l'unità ripetuta sui due capi dell'intervallo.
_UNIT_RANGE = re.compile(rf"({_NUM})\s*({_UNIT})\s*-\s*({_NUM_BARE})\s*({_UNIT})", re.IGNORECASE)

_FACT = re.compile(
    rf"(?P<ph>\bpH(?:\s+[^\W\d_]+){{0,2}}\s*[:=]?\s*)?"
    # «pH5,5»: dopo «pH» il numero può stare attaccato.
    rf"(?P<num>(?:(?<=pH)|(?<![\w.,]))(?:-(?=\d))?\d+(?:[.,]\d+)?)"
    rf"(?:\s*-\s*(?P<hi>{_NUM_BARE})|(?P<ratio>:\d+(?![\d:])))?"
    rf"(?:\s*(?P<unit>{_UNIT}))?",
    re.IGNORECASE,
)
_DASHES = str.maketrans({"–": "-", "—": "-", "‑": "-", "−": "-", "‒": "-"})
_EMPHASIS = re.compile(r"\*\*|__|`")
_FRONTMATTER = re.compile(r"\A---\s*\n.*?\n---\s*(?:\n|\Z)", re.DOTALL)


@dataclass(frozen=True)
class LostFact:
    """Un fatto di una pagina di prima che non è in nessuna pagina di adesso."""

    page: str
    fact: str
    context: str


@dataclass
class LostFacts:
    """L'elenco per il modello (al massimo :data:`MAX_LOST`) e il totale vero."""

    items: list[LostFact] = field(default_factory=list)
    total: int = 0


def _canon_unit(unit: str) -> str:
    compact = re.sub(r"\s+", "", unit)
    for rx, canon in _CANON:
        if rx.match(compact):
            return canon
    return compact.lower()


def _canon_number(text: str, unit: str) -> str:
    """«1,50» → «1.5»; «1.000 lux» → «1000». Il segno resta."""
    if re.fullmatch(r"-?\d{1,3}[.,]\d{3}", text) and unit in ("lux", "ppm"):
        # Il separatore delle migliaia, all'italiana: «1.000 lux» non è un lux.
        return text.replace(".", "").replace(",", "")
    text = text.replace(",", ".")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _clean_line(line: str) -> str:
    """La riga come la legge il confronto: senza enfasi, trattini uniformi."""
    return _EMPHASIS.sub("", line).translate(_DASHES)


def _normalise_ranges(line: str) -> str:
    line = _WORD_RANGE.sub(r"\1-\2", line)
    line = _TO_RANGE.sub(r"\1-\2", line)

    def _same_unit(match: re.Match[str]) -> str:
        if _canon_unit(match.group(2)) != _canon_unit(match.group(4)):
            return match.group(0)
        return f"{match.group(1)}-{match.group(3)} {match.group(4)}"

    return _UNIT_RANGE.sub(_same_unit, line)


def _is_clock(num: str, ratio: str) -> bool:
    """«15:40», «08:30»: un orario, non un rapporto come «1:1» o «1:10»."""
    right = ratio[1:]
    left = num.lstrip("-")
    return (len(left) == 2 and len(right) == 2) or (left.startswith("0") and len(left) > 1)


def _facts_of_line(line: str) -> list[str]:
    found: list[str] = []
    for match in _FACT.finditer(_normalise_ranges(line)):
        num, hi, ratio = match.group("num"), match.group("hi"), match.group("ratio")
        unit = _canon_unit(match.group("unit")) if match.group("unit") else ""
        if ratio:
            if _is_clock(num, ratio):
                continue
            found.append(f"{num}{ratio}")
            continue
        if not unit and not match.group("ph"):
            continue
        value = _canon_number(num, unit)
        if hi is not None:
            value = f"{value}-{_canon_number(hi, unit)}"
        found.append(f"ph{value}" if match.group("ph") else f"{value}{unit}")
    return found


def _context(line: str, fact: str) -> str:
    """La riga del fatto, accorciata intorno al suo primo numero se è lunga."""
    text = " ".join(line.split())
    if len(text) <= _MAX_CONTEXT:
        return text
    digits = re.search(r"\d+", fact)
    at = text.find(digits.group(0)) if digits else -1
    start = max(0, min(at - _MAX_CONTEXT // 3, len(text) - _MAX_CONTEXT)) if at >= 0 else 0
    piece = text[start:start + _MAX_CONTEXT].strip()
    return ("…" if start > 0 else "") + piece + ("…" if start + _MAX_CONTEXT < len(text) else "")


def extract_facts(text: str) -> dict[str, str]:
    """I fatti di *text*, normalizzati, ognuno con la riga dove compare la prima volta.

    Il frontmatter YAML si salta: date, stato e fonti non sono fatti della
    pagina, e una data aggiornata non è una perdita.
    """
    facts: dict[str, str] = {}
    body = _FRONTMATTER.sub("", text or "", count=1)
    for raw in body.splitlines():
        line = _clean_line(raw)
        if not any(ch.isdigit() for ch in line):
            continue
        for fact in _facts_of_line(line):
            facts.setdefault(fact, _context(line, fact))
    return facts


def _wiki_pages(files: Mapping[str, str]) -> dict[str, str]:
    return {
        path: text for path, text in files.items()
        if path.startswith(_WIKI_PREFIX) and path.endswith(".md")
    }


def lost_facts(before: Mapping[str, str], after: Mapping[str, str]) -> LostFacts:
    """I fatti delle pagine ``wiki/`` di *before* che non sono in nessuna pagina ``wiki/`` di *after*.

    Le chiavi sono i percorsi relativi al quaderno, i valori il testo. Un fatto
    spostato in un'altra pagina non è perso: conta l'insieme delle pagine di
    adesso, non la stessa pagina. Ordinati per pagina, poi nell'ordine del
    testo; al massimo :data:`MAX_LOST` voci, con il totale vero.
    """
    present: set[str] = set()
    for text in _wiki_pages(after).values():
        present.update(extract_facts(text))
    result = LostFacts()
    for page, text in sorted(_wiki_pages(before).items()):
        for fact, context in extract_facts(text).items():
            if fact in present:
                continue
            result.total += 1
            if len(result.items) < MAX_LOST:
                result.items.append(LostFact(page=page, fact=fact, context=context))
    return result
