"""Le ancore di un fatto: i nomi propri e i numeri che lo rendono quel fatto.

**Perché esiste** (misurato sul telefono il 25/11/2026). Le voci di ``USER.md``
venivano riscritte in media sette volte, ognuna un po' più corta, e a ogni
giro cadeva qualcosa: «mesi da Oreste a Cremona» era diventato «mesi da Oreste» (nomi inventati: il repository è pubblico).
Su 189 nomi e numeri passati per le voci ancora vive, 72 non erano più in
nessun file vivo. L'archivio conservava le formulazioni vecchie, ma il codice
le contava come «la stessa cosa detta peggio» (``pair_successors``), quindi
nessuno se ne accorgeva.

Un nome proprio o un numero è la parte di un fatto che **non si riassume**: un
aggettivo si può accorciare, «Cremona» o «1.250» no — o c'è o è perso. Questo
modulo li riconosce, così chi riscrive una voce può sapere cosa ha lasciato
per strada.

Solo codice e libreria standard, nessun modello: il riconoscimento è grezzo e
lo sa. Le regole e i loro limiti sono scritti accanto a ciascuna.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field

from jenny.utils.wiki_paths import fold

# Un numero: cifre con i loro separatori («1.250», «12/08/2026», «2026-11-24»),
# ed eventualmente lettere attaccate («42k», «4K»).
_NUM = r"\d+(?:[.,/:'’-]\d+)*(?:[^\W\d_]+)?"
# Una parola: comincia con una lettera, può contenere cifre e trattini
# («Kojo-no-Mai», «Ryzen7»). L'apostrofo la unisce solo se c'è una lettera dopo.
_WORD = r"[^\W\d_][^\W_]*(?:['’-][^\W_]+)*"
_TOKEN_RE = re.compile(rf"{_NUM}|{_WORD}")

# Cosa può precedere un inizio di frase **sulla stessa riga**: un elenco, un
# rientro, un grassetto. Una parola maiuscola dopo questo è maiuscola perché è
# la prima, non perché è un nome.
_LINE_START_RE = re.compile(r"^\s*(?:[-*+•>]\s*)?(?:\d+[.)]\s*)?[*_`]*\s*$")
# Dopo questi caratteri comincia una frase. Non i due punti: in italiano e in
# inglese dopo «Amici:» si continua minuscolo, e «Amici: Tiziana» porta un nome.
# Sì la chiusa di un'etichetta fra quadre, perché le righe del diario sono
# «[said] Oreste vive a Cremona».
_SENTENCE_END = frozenset(".!?…—–«\"“]")

# Un'ora non è un'ancora: «22:53» davanti a ogni riga di diario la renderebbe la
# parola più frequente di tutte.
_CLOCK_RE = re.compile(r"^\d{1,2}:\d{2}$")
# Una data corta («10/10», «12/08/2026») o ISO («2026-11-24»).
_DATE_RE = re.compile(r"^(?:\d{1,2}/\d{1,2}(?:/\d{2,4})?|\d{4}-\d{2}-\d{2})$")

# Sotto questa lunghezza una sigla tutta maiuscola è quasi sempre una parola
# grammaticale o un codice di lingua («IT», «EN», «OK»), non un nome.
_ALL_CAPS_MIN = 3
# Una parola maiuscola di una lettera sola («I» inglese) non è un nome.
_NAME_MIN = 2

# Parole che si scrivono maiuscole senza essere ancore di un fatto: i giorni e
# i mesi (in inglese vanno maiuscoli), le parole dell'app stessa e dei suoi
# file. Piegate (``fold``). Il nome che l'utente ha dato a Jenny si aggiunge da
# fuori (``extra_stop``), perché qui non si legge la config.
_STOPWORDS = frozenset(
    fold(w)
    for w in (
        "monday tuesday wednesday thursday friday saturday sunday "
        "lunedì martedì mercoledì giovedì venerdì sabato domenica "
        "january february march april may june july august september october "
        "november december gennaio febbraio marzo aprile maggio giugno luglio "
        "agosto settembre ottobre novembre dicembre "
        "jenny dream user memory soul agents md json api http https url ok ai llm "
        "webui telegram android"
    ).split()
)


@dataclass(frozen=True, slots=True)
class _Token:
    text: str
    start: int


def _tokens(text: str) -> list[_Token]:
    return [_Token(m.group(0), m.start()) for m in _TOKEN_RE.finditer(text)]


def _sentence_initial(text: str, start: int) -> bool:
    """Se la parola che comincia in *start* è la prima della sua frase."""
    line_start = text.rfind("\n", 0, start) + 1
    before = text[line_start:start]
    if _LINE_START_RE.match(before):
        return True
    stripped = before.rstrip()
    return bool(stripped) and stripped[-1] in _SENTENCE_END


def _number_key(token: str) -> str | None:
    """La chiave di un numero che vale come ancora, o ``None``.

    Vale un numero con almeno tre cifre («800», «1.250», «2026»), una data, o un
    numero con lettere attaccate («42k»). Non vale un'ora, né un numero di una o
    due cifre da solo («3», «12»): sono troppo comuni per dire quale fatto sia.
    """
    if _CLOCK_RE.match(token):
        return None
    has_suffix = token[-1].isalpha()
    digits = sum(c.isdigit() for c in token)
    if digits >= 3 or has_suffix or _DATE_RE.match(token):
        return fold(token)
    return None


def _capitalised(token: str) -> bool:
    return token[:1].isupper()


def _all_caps(token: str) -> bool:
    letters = [c for c in token if c.isalpha()]
    return len(letters) > 1 and all(c.isupper() for c in letters)


def _name_key(token: str, extra_stop: frozenset[str]) -> str | None:
    """La chiave di una parola maiuscola che può essere un nome, o ``None``."""
    if len(token) < _NAME_MIN or not _capitalised(token):
        return None
    if _all_caps(token) and len(token) < _ALL_CAPS_MIN:
        return None
    key = fold(token)
    if key in _STOPWORDS or key in extra_stop:
        return None
    return key


@dataclass
class Vocabulary:
    """Cosa si sa delle parole maiuscole guardando tutti i testi insieme.

    Una parola maiuscola a inizio frase è ambigua: «Oreste vive a Cremona» comincia
    con un nome, «Vuole un glicine» no. Una sola frase non basta a decidere, un
    corpus sì: se «Oreste» compare maiuscola **anche a metà frase** da qualche
    parte, è un nome anche quando apre una frase. Lo stesso corpus dice quali
    nomi sono anche parole comuni («Neve» il nome, «neve» il tempo): li si conta minuscoli.
    """

    names: set[str] = field(default_factory=set)
    capitalised: Counter[str] = field(default_factory=Counter)
    lowercase: Counter[str] = field(default_factory=Counter)

    @classmethod
    def from_texts(cls, texts: Iterable[str], *,
                   extra_stop: Iterable[str] = ()) -> Vocabulary:
        vocab = cls()
        stop = frozenset(fold(w) for w in extra_stop)
        for text in texts:
            vocab.add(text, extra_stop=stop)
        return vocab

    def add(self, text: str, *, extra_stop: frozenset[str] = frozenset()) -> None:
        for token in _tokens(text):
            if token.text[:1].isdigit():
                continue
            if not _capitalised(token.text):
                self.lowercase[fold(token.text)] += 1
                continue
            if _sentence_initial(text, token.start):
                continue
            key = _name_key(token.text, extra_stop)
            if key is not None:
                self.names.add(key)
                self.capitalised[key] += 1

    def is_common_word(self, key: str) -> bool:
        """Se il nome si legge più spesso come parola comune che come nome."""
        return self.lowercase[key] > self.capitalised[key]


def extract(text: str, vocab: Vocabulary | None = None, *,
            extra_stop: Iterable[str] = ()) -> frozenset[str]:
    """Le ancore di *text*: nomi propri e numeri, piegati (``fold``).

    Una parola maiuscola conta se non apre la frase; se la apre, conta solo
    quando *vocab* l'ha già vista maiuscola a metà frase altrove. Senza *vocab*
    le parole a inizio frase non contano mai: si perde qualche nome, non si
    inventano ancore dalle maiuscole di grammatica.

    Limiti accettati: un nome scritto sempre minuscolo non si riconosce; un
    nome di più parole («Trade Republic») dà più ancore, una per parola.
    """
    stop = frozenset(fold(w) for w in extra_stop)
    found: set[str] = set()
    for token in _tokens(text):
        if token.text[:1].isdigit():
            key = _number_key(token.text)
        else:
            key = _name_key(token.text, stop)
            if key is not None and _sentence_initial(text, token.start):
                if vocab is None or key not in vocab.names:
                    key = None
        if key is not None:
            found.add(key)
    return frozenset(found)


def words(text: str) -> frozenset[str]:
    """Tutte le parole e i numeri di *text*, piegati, per chiedere «c'è ancora?».

    Un'ancora sopravvive a una riscrittura se la sua parola c'è ancora, in
    qualunque posizione e maiuscola: «Oreste» che da metà frase passa a inizio
    frase non è caduta.
    """
    return frozenset(fold(t.text) for t in _tokens(text))


def surface_forms(texts: Iterable[str]) -> dict[str, str]:
    """Per ogni parola piegata, come compare scritta la prima volta in *texts*.

    Le ancore sono piegate (``fold``) per confrontarle; per mostrarle a chi legge
    si rimettono come erano scritte: «Città di Castello», non «citta».
    """
    forms: dict[str, str] = {}
    for text in texts:
        for token in _tokens(text):
            forms.setdefault(fold(token.text), token.text)
    return forms


def delta(old: str, new: str, vocab: Vocabulary | None = None, *,
          extra_stop: Iterable[str] = ()) -> tuple[frozenset[str], frozenset[str]]:
    """Cosa una riscrittura ha tolto e cosa ha aggiunto: ``(cadute, nuove)``.

    Le cadute sono le ancore di *old* la cui parola non c'è più in *new*; le
    nuove quelle di *new* che in *old* non c'erano. Una riscrittura che toglie
    e non aggiunge ha limato; una che aggiunge ha detto qualcosa di diverso.
    """
    stop = tuple(extra_stop)
    old_words, new_words = words(old), words(new)
    dropped = frozenset(a for a in extract(old, vocab, extra_stop=stop) if a not in new_words)
    added = frozenset(a for a in extract(new, vocab, extra_stop=stop) if a not in old_words)
    return dropped, added
