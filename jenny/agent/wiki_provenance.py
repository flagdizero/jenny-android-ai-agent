"""La provenienza di una pagina di wiki, e il gancio che la impone in scrittura.

Estratto da ``jenny/agent/gardener.py`` il 26/08/2026, e l'estrazione **è** la
correzione. Fino a quel giorno questo gancio era montato in un posto solo — la
passata del giardiniere (``run_gardener``) — e la conseguenza si è vista sul
telefono: la passata con **meno** contesto era l'unica trattenuta, e la
conversazione, che ha i corpi delle pagine, la giornata intera e la libertà di
ristrutturare, non era trattenuta affatto. Il 26/08 in ``wikis/salute`` una
richiesta di sistemare la wiki ha riscritto la ``source:`` di una pagina come
lista YAML, e i due lettori che la interpretano hanno dato due risposte diverse
(``_page_frontmatter`` → ``'- raw/journal/...'``, trattino incluso e quindi
irrisolvibile; il ``parse_frontmatter`` del lint → la seconda voce): la
provenienza di quella pagina è diventata illeggibile e niente l'ha detto.

Il modulo è **foglia di proposito**. Il gancio universale
(:func:`wiki_page_provenance_guard`) lo monta ``_FsTool``, cioè un tool, e un
tool che importasse ``gardener`` si tirerebbe dentro ``internal_run`` e da lì
mezzo repo. Qui dentro non c'è nulla oltre alla libreria standard e a
``jenny.utils.wiki_paths``, che è un'altra foglia.

Chi lo usa, e come:

- ``run_gardener`` compone :func:`_provenance_guard` con radice e ``wiki/`` note
  (v. ``_compose_write_guards``);
- ``_FsTool._check_write_size`` chiama :func:`wiki_page_provenance_guard`, che
  ricava la radice **dal percorso** — quindi vale per la conversazione, per un
  subagent e per qualunque altro scrittore, senza che nessuno debba ricordarsi
  di montarlo.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from jenny.security.workspace_policy import is_path_within
from jenny.utils.wiki_paths import wiki_page_rel

_PROVENANCE_REFUSAL_TEMPLATE = (
    "Refused: `{page}` declares `state: {state}`, but {why}. Only a line the conversation "
    "attributed to the user can carry a state above `open` — an answer to a question the "
    "assistant asked is not the user's statement, and that is the mistake this hook exists to "
    "stop. Write the page at `state: open` and put the question in the map's open section, "
    "naming the page. Nothing else about the page needs to change."
)

# I marcatori che valgono «detto dall'utente». ``[recovered]`` c'e' perche' una
# passata recupera solo fatti che l'utente ha detto e che la cattura ha perso: e'
# il contratto del suo prompt (v. ``JournalAppendTool``).
_SOURCE_LIST_REFUSAL = (
    "Refused: `{page}` writes `source:` as a list ({got!r}). **`source:` is one value.** "
    "Two readers parse a list two different ways — one keeps the leading `- ` and gets a path "
    "that resolves to nothing, the other takes the second item — so the page's provenance "
    "becomes unreadable and no check says so. Keep the single source that carries the page's "
    "`state:` and name the other one in the body, where prose belongs."
)

# Una voce di lista YAML, nelle due forme che si scrivono a mano: il blocco
# (``- valore`` sulla riga dopo, che ``_page_frontmatter`` legge col trattino
# attaccato) e il flusso (``[a, b]``). Non è un parser: è il riconoscimento delle
# due forme che il 26/08 sono passate zitte.
_SOURCE_IS_A_LIST = re.compile(r"^(-\s|\[.*\]$)")

# Una riga di diario, per come la scrive ``JournalAppendTool``. Serve a una sola
# domanda — «questo file è un giorno di diario?» — che è quel che distingue una
# ``source:`` riparabile aggiungendo l'ora da una a cui l'ora non si può
# aggiungere affatto. Il gemello nel lint è ``lint_wiki._journal_markers``.
_JOURNAL_LINE = re.compile(r"^-\s\d{2}:\d{2}\s\u2014\s", re.MULTILINE)

_SAID_MARKERS = ("[said]", "[recovered]")
_STATES_NEEDING_A_SAID_LINE = ("decided", "done")

_FRONTMATTER_VALUE = re.compile(r"^(state|source)\s*:\s*(.+?)\s*$", re.MULTILINE)


def _page_frontmatter(text: str) -> dict[str, list[str]]:
    """Tutti i valori di ``state`` e ``source`` nella frontmatter, in ordine.

    Un parser di due campi e non YAML: la guardia gira **prima di ogni
    scrittura**, e le due chiavi che le servono stanno in cima. Se la frontmatter
    non c'e', il dizionario e' vuoto e la guardia non ha niente da dire — non e'
    lei a decidere se una pagina debba averla (lo dice il lint, su tutte le pagine
    e non solo su quelle che passano da qui).

    **Liste e non un valore, e qui la prima versione sbagliava.** Davanti a due
    ``state:`` prendeva il primo «come farebbe un parser YAML» — e quella e' una
    via d'uscita, non una compatibilita': ``state: open`` in cima e
    ``state: decided`` sotto passavano il gancio, e chi legge la pagina con un
    parser vero (dove fra chiavi duplicate vince l'**ultima**) ci trova
    ``decided``. Una guardia non deve indovinare quale valore vale: prende tutti e
    decide sul piu' impegnativo. L'ordine non conta piu', che e' il punto.
    """
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    head = text[: end if end != -1 else len(text)]
    out: dict[str, list[str]] = {}
    for match in _FRONTMATTER_VALUE.finditer(head):
        out.setdefault(match.group(1), []).append(match.group(2).strip().strip("\"'"))
    return out


# I quattro esiti di una ``source:``, e sono quattro perche' si riparano in quattro
# modi. ``SAID`` passa; ``INFERRED`` si ripara cambiando lo stato della pagina;
# ``UNRESOLVED`` cambiando l'ancoraggio; ``AMBIGUOUS`` **aggiungendo** l'ordinale.
# Il rifiuto e' lo stesso per gli ultimi tre (fail-closed), ma la frase che il
# modello legge deve dire quale: un rifiuto su cui non si puo' agire e' un rifiuto
# che si riprova identico.
_SAID = "said"
_INFERRED = "inferred"
_UNRESOLVED = "unresolved"
_AMBIGUOUS = "ambiguous"

# ``HH:MM`` o ``HH:MM.N``, con *N* la posizione della riga **dentro quel minuto**,
# da 1. La forma con l'ordinale non cambia una virgola del diario — v.
# ``_journal_line_provenance``.
_ANCHOR_RE = re.compile(r"^(\d{2}:\d{2})(?:\.(\d+))?$")


def _journal_line_provenance(root: Path, source: str) -> str:
    """A chi e' attribuita la riga citata da *source*, o perche' non si sa.

    **D13, e non era un difetto di tracciabilita'.** L'ancoraggio e' al *minuto*,
    quindi ``#13:55`` combacia con **tutte** le righe di quel minuto; la prima
    versione tornava alla prima che trovava. In un minuto ad attribuzione mista —
    ``[said]`` appesa prima, ``[inferred]`` dopo — una pagina che citava il fatto
    dedotto passava come ``decided`` perche' la guardia aveva letto *l'altra riga*.
    Cioe' D1, il difetto che questa guardia esiste per chiudere, rientrato dalla
    finestra in un verso solo e in silenzio. E il minuto misto non e' un caso
    esotico: da T4 la cattura fa **una chiamata per fatto**, quindi un turno in cui
    l'utente dice una cosa e Jenny ne deduce la conseguenza produce esattamente
    quelle due righe allo stesso minuto.

    **Il minuto ambiguo si rifiuta, ma solo se e' davvero ambiguo.** Se tutte le
    righe di quel minuto sono dell'utente, quale delle due la pagina intenda non
    cambia la risposta: passa. E' lo stesso ragionamento che il chiamante applica a
    due ``source:`` diverse — ognuna deve reggere — applicato dentro un minuto.

    **L'ordinale non tocca il diario.** ``#13:55.2`` vuol dire «la seconda riga di
    quel minuto» e si risolve contando, quindi il file resta byte per byte quello
    di prima: nessuna migrazione, nessun secondo aggiunto al formato, e le
    ``source:`` gia' scritte continuano a valere dove il minuto ha una riga sola.
    Il diario e' append-only per costruzione (``JournalAppendTool``), quindi
    dentro un minuto la posizione di una riga non cambia piu': e' quel contratto a
    rendere un ordinale un indirizzo stabile invece di un numero fortunato.

    Quel che resta aperto, e va detto qui: un ordinale **sbagliato** su un minuto
    misto passa, se punta a una riga detta. E' la stessa cosa che dichiarare
    ``[said]`` su un fatto dedotto — la provenienza la dichiara un modello e il
    codice impone solo la conseguenza (v. ``jenny/agent/tools/journal.py``). Il
    verso in cui si sbaglia da qui e' esplicito, non accidentale.
    """
    rel, _, anchor = source.partition("#")
    match = _ANCHOR_RE.match(anchor.strip())
    if match is None:
        return _UNRESOLVED
    minute, ordinal = match.group(1), match.group(2)
    page = root / rel.strip()
    # Contenuta nel progetto: ``source:`` e' testo che il modello scrive, quindi
    # ``../..`` e' una cosa che puo' capitare — qui non serve leggere fuori.
    # Il percorso grezzo, non gia' risolto: e' ``is_path_within`` a risolverlo,
    # e un loop di symlink (``RuntimeError`` su Python 3.11) diventa un no.
    if not is_path_within(page, root):
        return _UNRESOLVED
    try:
        text = page.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return _UNRESOLVED
    prefix = f"- {minute} \u2014 "
    bodies = [
        line[len(prefix):].lstrip() for line in text.splitlines() if line.startswith(prefix)
    ]
    if not bodies:
        return _UNRESOLVED
    if ordinal is not None:
        index = int(ordinal) - 1
        # Fuori range e' un ancoraggio che non risolve, non un minuto ambiguo:
        # ``#13:55.4`` su tre righe e' un errore di conto, e si ripara contando.
        if not 0 <= index < len(bodies):
            return _UNRESOLVED
        bodies = [bodies[index]]
    if all(body.startswith(_SAID_MARKERS) for body in bodies):
        return _SAID
    return _AMBIGUOUS if len(bodies) > 1 else _INFERRED


def _provenance_guard(root: Path, pages: Path) -> Any:
    """Il gancio che impedisce a una pagina nuova di certificare cio' che nessuno ha detto.

    **T3, D1.** Il 24/08 una pagina e' nata ``state: decided`` su un fatto che
    l'utente non aveva detto — era l'opzione B di una domanda che Jenny aveva fatto
    lei — ed e' finita sotto «Decided» nella mappa, che entra a ogni turno.
    ``agent/gardener.md`` la regola ce l'aveva gia' scritta («only the user's own
    words … can justify anything stronger»), ma non era **rispettabile**: il
    giardiniere promuove dal diario, dove una riga citata e una dedotta erano
    tipograficamente identiche. Ora il diario le distingue, e questo gancio e' il
    lettore che quel marcatore non aveva.

    **Fail-closed su tutte e quattro le vie di non-sapere** — riga ``[inferred]``,
    ``source:`` senza ancoraggio, ancoraggio che non risolve, e un minuto che tiene
    piu' righe di cui non tutte dell'utente (**D13**, v.
    ``_journal_line_provenance``). Il verso opposto
    («se non riesco a controllare, lascio passare») e' precisamente il difetto:
    quel che passa e' una certificazione, e una certificazione sbagliata resta
    scritta finche' qualcuno non la nota. ``open`` non e' un castigo — e' quel che
    la pagina vale, e la pagina si scrive comunque.

    **Solo verso l'alto, e solo dentro ``wiki/``.** Una pagina che si dichiara
    ``open`` o ``hypothesis`` non passa da qui, e nemmeno la mappa (che di
    ``state:`` non ne ha): il gancio non ha nessuna opinione sulla prosa, solo su
    chi si dichiara deciso.
    """

    def _guard(path: Any, text: str) -> str | None:
        try:
            target = Path(path).resolve()
            target.relative_to(pages)
        except (ValueError, OSError, RuntimeError, TypeError):
            # ``RuntimeError``: un loop di symlink, su Python 3.11.
            return None
        return _check_page(root, target, text)

    return _guard


def wiki_page_provenance_guard() -> Any:
    """Lo stesso controllo, con la radice ricavata **dal percorso**. 26/08/2026.

    :func:`_provenance_guard` va costruito con radice e ``wiki/`` in mano, e le ha
    solo chi gira dentro un progetto solo: la passata del giardiniere. La
    conversazione monta la sua cassetta una volta per tutto il workspace e serve
    ogni progetto, quindi lì non c'era modo di montarlo — ed è **il difetto**, non
    un dettaglio di costruzione: chi ha meno contesto era l'unico trattenuto.

    La radice si ricava da :func:`jenny.utils.wiki_paths.wiki_page_rel`, che è
    già la definizione canonica di «questo file è una pagina che il blocco di
    progetto inietta» — quindi le esclusioni sono le sue e non un secondo
    elenco: niente ``index.md`` (la mappa non ha ``state:``), niente nascosti,
    niente ``summaries/``. Per tutto il resto del workspace torna ``None`` e
    questo gancio non esiste.

    Montato in ``_FsTool._check_write_size``, quindi vale per la conversazione, per
    un subagent e per qualunque futuro scrittore senza che nessuno se lo debba
    ricordare. Sulla passata del giardiniere gira **due volte** — il suo gancio
    composto se lo porta già — con lo stesso verdetto e la stessa frase: costa una
    lettura di frontmatter e chiude la strada a una divergenza fra le due.
    """

    def _guard(path: Any, text: str) -> str | None:
        try:
            target = Path(path).resolve()
        except (OSError, RuntimeError, TypeError):
            # ``RuntimeError``: un loop di symlink, su Python 3.11.
            return None
        rel = wiki_page_rel(target)
        if rel is None:
            return None
        # Risalire di tanti livelli quanti ne ha il relativo porta a ``wiki/``, e
        # sopra c'è la radice: è lo stesso cammino che ``wiki_page_rel`` ha già
        # percorso, letto al contrario, così le due non possono discordare su
        # quale sia il progetto.
        pages = target
        for _ in range(len(Path(rel).parts)):
            pages = pages.parent
        return _check_page(pages.parent, target, text)

    return _guard


def _check_page(root: Path, target: Path, text: str) -> str | None:
    """Il verdetto su *text* come pagina di *root*, o ``None`` se va bene.

    Corpo comune ai due ganci: la sola differenza fra loro è **come** arrivano a
    *root*, e tenerne una copia per ciascuno vorrebbe dire due politiche che
    divergono al primo cambio.
    """
    front = _page_frontmatter(text)
    # **La forma prima dello stato, e questo è l'ordine che conta.** Il difetto del
    # 26/08 è passato su una pagina a ``state: open``: tutto quel che segue si
    # pronuncia solo verso l'alto, quindi una ``source:`` malformata su una pagina
    # che non rivendica niente non incontrava nessuno. E non è un difetto minore
    # perché la pagina è a ``open``: è la provenienza a diventare illeggibile, cioè
    # la cosa che rende una pagina sbagliata correggibile invece che soltanto
    # sbagliata — e il giorno in cui qualcuno prova a promuoverla il rifiuto che
    # riceve parla di un'ancora, non della lista che l'ha causato.
    for candidate in front.get("source", []):
        if _SOURCE_IS_A_LIST.match(candidate):
            return _SOURCE_LIST_REFUSAL.format(page=target.name, got=candidate)
    # Lo stato piu' impegnativo fra quelli dichiarati, non "il" dichiarato:
    # v. ``_page_frontmatter``.
    claimed = [v.lower() for v in front.get("state", [])]
    strong = [v for v in claimed if v in _STATES_NEEDING_A_SAID_LINE]
    if not strong:
        return None
    state = strong[0]
    # E **ogni** ``source:`` deve reggere, non almeno una: due sorgenti di cui
    # una dedotta sono una pagina che si dichiara decisa in parte, cioe' una
    # pagina che si dichiara decisa.
    sources = front.get("source", [""])
    verdict = _SAID
    source = ""
    for candidate in sources:
        outcome = _journal_line_provenance(root, candidate)
        if outcome != _SAID:
            verdict, source = outcome, candidate
            break
    if verdict == _SAID:
        return None
    if verdict == _INFERRED:
        why = (
            "its `source:` line is `[inferred]` — the assistant concluded it, the user "
            "did not say it"
        )
    elif verdict == _AMBIGUOUS:
        # **La riparazione e' un'aggiunta, non una correzione**, e la frase lo
        # deve dire: l'ancoraggio non e' sbagliato, e' incompleto. Detto come
        # «non punta a una riga» il modello riscriverebbe il minuto, che e'
        # l'unica cosa che qui e' giusta.
        why = (
            "that minute holds more than one journal line and they are not all the "
            "user's, so its `source:` does not say which one this page rests on. Keep "
            "the minute and add the line's place within it, counting from 1: "
            f"`{source}.2` is the second line at that minute"
        )
    elif _names_a_document(root, source):
        # **Il consiglio impossibile, chiuso il 26/08.** Detto come «aggiungi
        # l'ora dopo un `#`» su un documento copiato in ``raw/`` si manda a una
        # riparazione che non esiste: lì non c'è nessun minuto. È lo stesso
        # difetto che lo stesso giorno è stato corretto nel lint
        # (``_decided_cap_reason``), e va corretto due volte perché sono due
        # lettori diversi della stessa situazione.
        why = (
            f"its `source:` names a document copied into `raw/` ({source!r}), not a journal "
            "line — nothing in it attributes the fact to the user, so nothing there can carry "
            "a state above `open`. If the user did say it, capture it as a journal line first "
            "(`attribution: said`) and point `source:` at that line"
        )
    else:
        why = (
            "its `source:` does not point at one journal line: add the line's own time "
            "after a `#` (`source: raw/journal/<day>.md#HH:MM`, or `#HH:MM.2` for the "
            f"second line at that minute). Got {source!r}"
        )
    return _PROVENANCE_REFUSAL_TEMPLATE.format(
        page=target.name, state=state, why=why
    )


def _names_a_document(root: Path, source: str) -> bool:
    """*source* nomina un file che non è un giorno di diario?

    Fail-**open** di proposito, al contrario del resto di questo modulo: un file
    illeggibile o fuori dalla radice torna ``False``, cioè si tiene la frase
    generica. Qui non si decide se rifiutare — quello è già deciso — ma solo
    quale delle due riparazioni suggerire, e in dubbio la generica è quella che
    non manda da nessuna parte in particolare.
    """
    rel = source.partition("#")[0].strip()
    if not rel:
        return False
    page = root / rel
    # Il percorso grezzo: lo risolve ``is_path_within``, che tratta anche un loop
    # di symlink (``RuntimeError`` su Python 3.11) come un no.
    if not is_path_within(page, root):
        return False
    try:
        text = page.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return False
    return _JOURNAL_LINE.search(text) is None
