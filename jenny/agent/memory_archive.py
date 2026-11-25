"""Il tier freddo della memoria: dove finisce una voce quando lascia i file caldi.

La fase 2 del piano della memoria sostituisce la cancellazione con la
**degradazione**. Non è una sfumatura di parole: è ciò che rende il "fai spazio"
un'operazione che riesce sempre, e quindi toglie la ragione per cui una scrittura
deve essere rifiutata. Ed è la riparazione vera per il review pass, i cui errori
oggi sono definitivi — misurato il 2026-08-18, un secondo passaggio consecutivo
ha tolto cinque voci vere da ``USER.md``, e l'unico recupero era uno snapshot.

Tre scelte di formato, e nessuna è estetica.

**Un file per voce.** Non un registro che cresce. L'agente cerca qui con ``grep``,
e ``grep`` salta i file grandi in silenzio: un falso negativo che si legge come
"non l'ho mai saputo", che è esattamente il fallimento che l'archivio esiste per
impedire. File piccoli non hanno quel modo di fallire.

**Il testo separato dai metadati.** Il corpo è il fatto e basta, senza il trattino
del bullet e senza etichette intorno. Chi cerca vede la frase, non l'imballaggio;
e il giorno in cui ci passa sopra un modello di embedding, il corpo è già la sola
cosa da vettorizzare.

**Il nome del file porta la data e l'id.** ``2026-08-18-a1b2c3d4.md``: si ordina da
solo per tempo, e l'id è lo stesso hash del contenuto che usa il tool per voci
(v. ``agent/tools/memory_entries.py``), quindi una voce degradata si ritrova a
partire dal suo testo senza aprire niente.

Nessuna ritenzione, di proposito: il testo costa poco e il punto di tutto questo è
che niente sia più irrecuperabile. Il percorso non lo conosce il modello — ce lo
scrive il runtime da dentro il tool — perché nominare una quarta destinazione nel
prompt di Dream è già costato run interi.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from jenny.agent.anchors import Vocabulary, extract, surface_forms, words
from jenny.utils.path import atomic_write

ARCHIVE_DIRNAME = "archive"

# Dove vanno le voci di ``history.jsonl`` che escono dal tetto (v.
# ``MemoryStore.compact_history``): un file al mese, stesse righe JSON del file
# vivo. Sta qui e non in ``memory.py`` perché la leggono sia lo store sia
# ``recall_history``, e questo modulo è la foglia che entrambi importano già.
HISTORY_ARCHIVE_DIRNAME = "history-archive"

# Il trattino del bullet appartiene al file di provenienza, non al fatto: lì era
# un elenco, qui è una frase. Toglierlo tiene il corpo pulito per chi legge e per
# qualunque cosa lo indicizzi.
_BULLET_PREFIX = re.compile(r"^[-*][ \t]+")

_SEPARATOR = "---"

# Un id come lo scrive ``entry_id``: niente che un ``glob`` legga come jolly.
_PLAIN_ID = re.compile(r"[A-Za-z0-9]{1,64}")


@dataclass(frozen=True, slots=True)
class ArchivedEntry:
    """Una voce degradata, con il minimo che serve per ritrovarla e rimetterla.

    ``source`` e ``heading`` sono l'indirizzo da cui è arrivata: senza, una voce
    riletta fra sei mesi è una frase senza contesto, e rimetterla al suo posto
    diventa un indovinello. ``retention`` è il tag del Consolidator quando si
    conosce — spesso non si conosce, perché i tag vengono tolti dal testo prima
    che arrivi nei file, quindi è opzionale e non si inventa.

    ``superseded_by`` è l'id della voce che ha preso il suo posto, quando la si
    conosce: senza, sette riformulazioni dello stesso fatto stanno in archivio
    come sette fatti, e nessuna dice di essere vecchia (misurato sul telefono
    il 12/10/2026: 271 file su 479 erano versioni di 65 fatti). ``retracted``
    dice che il fatto non era mai stato vero, non solo che è cambiato: un fatto
    smentito, altrimenti, si rilegge in archivio identico a uno vero.

    ``forgotten`` dice che l'ha tolta **l'utente**: non deve più tornare, né
    col richiamo per nome (``agent/facts.py``) né con ``recall``. Il file resta
    come lapide, e non per pigrizia: se Dream riaggiunge la stessa frase, la
    lapide è ciò che la riconosce (stesso id, perché l'id è l'hash del testo).
    """

    id: str
    text: str
    source: str
    heading: str = ""
    retention: str = ""
    demoted: str = ""
    superseded_by: str = ""
    retracted: bool = False
    forgotten: bool = False


def archive_dir(memory_dir: Path) -> Path:
    return Path(memory_dir) / ARCHIVE_DIRNAME


def history_archive_dir(memory_dir: Path) -> Path:
    return Path(memory_dir) / HISTORY_ARCHIVE_DIRNAME


def archive_filename(entry_id: str, when: date) -> str:
    return f"{when.isoformat()}-{entry_id}.md"


def render_archived(entry: ArchivedEntry) -> str:
    """Il contenuto del file: intestazione di metadati, poi il fatto.

    La forma è quella della frontmatter che le skill del workspace già usano, per
    non introdurre un secondo dialetto in una cartella che l'utente apre a mano.
    Le chiavi vuote si omettono invece di comparire vuote: una riga
    ``retention:`` senza valore direbbe che l'informazione è stata cercata e
    persa, mentre la verità è che non c'era.
    """
    fields = [
        ("id", entry.id),
        ("source", entry.source),
        ("heading", entry.heading),
        ("retention", entry.retention),
        ("demoted", entry.demoted),
        ("superseded_by", entry.superseded_by),
        ("retracted", "yes" if entry.retracted else ""),
        ("forgotten", "yes" if entry.forgotten else ""),
    ]
    head = [f"{k}: {v}" for k, v in fields if v]
    body = _BULLET_PREFIX.sub("", entry.text.strip()).strip()
    return "\n".join([_SEPARATOR, *head, _SEPARATOR, "", body, ""])


def find_archived(memory_dir: Path, entry_id: str) -> Path | None:
    """Il file di questa voce, se è già stata degradata una volta.

    L'archivio è un **insieme di fatti**, non un registro di eventi: la stessa
    voce tolta due volte è lo stesso fatto, e due file con lo stesso testo e date
    diverse sarebbero solo rumore per chi cerca. Il primo degrado è quello che
    resta, con la sua data.
    """
    directory = archive_dir(memory_dir)
    if not directory.is_dir():
        return None
    matches = sorted(directory.glob(f"*-{entry_id}.md"))
    return matches[0] if matches else None


def archive_entry(
    memory_dir: Path, entry: ArchivedEntry, *, when: date | None = None,
) -> Path:
    """Scrive la voce nel tier freddo e ritorna il file che la contiene.

    Idempotente: se quella voce è già in archivio ritorna il file esistente senza
    riscriverlo. Serve perché nella fase 2 questa funzione la chiama ``remove``,
    e un fatto può benissimo essere riaggiunto e ritolto — l'archivio deve dire
    "questo fatto è passato di qui", non quante volte.

    Con un'eccezione sola: ``superseded_by``, ``retracted`` e ``forgotten`` si
    **aggiungono** al file esistente se lì mancano. Due percorsi archiviano la stessa voce nella
    stessa scrittura — ``memory replace`` col legame, poi la rete al confine del
    file senza — e quale arrivi primo non deve decidere se il legame resta. Un
    campo già pieno non si tocca: la prima sostituzione è quella che lo ha
    tolto dai file caldi.
    """
    existing = find_archived(memory_dir, entry.id)
    if existing is not None:
        _add_lineage(existing, entry)
        return existing

    day = when or date.today()
    stamped = ArchivedEntry(
        id=entry.id,
        text=entry.text,
        source=entry.source,
        heading=entry.heading,
        retention=entry.retention,
        demoted=entry.demoted or day.isoformat(),
        superseded_by=entry.superseded_by,
        retracted=entry.retracted,
        forgotten=entry.forgotten,
    )
    directory = archive_dir(memory_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / archive_filename(entry.id, day)
    # ``atomic_write`` come per i cursori: qui il file *è* l'unica copia rimasta
    # del fatto nel momento in cui ``remove`` lo toglie da USER.md, e un
    # troncamento a metà sarebbe la perdita che tutta la fase esiste per impedire.
    atomic_write(path, render_archived(stamped))
    return path


def _add_lineage(path: Path, entry: ArchivedEntry) -> None:
    """Aggiunge a un file già in archivio i campi di discendenza che gli mancano.

    Riga per riga dentro l'intestazione, e non ri-renderizzando il file: un
    file aperto e ritoccato a mano può portare chiavi che questo modulo non
    conosce, e riscriverlo da :class:`ArchivedEntry` le butterebbe.
    """
    wanted = {
        "superseded_by": entry.superseded_by,
        "retracted": "yes" if entry.retracted else "",
        # Una voce che Dream aveva già archiviato e che poi l'utente toglie: la
        # lapide si aggiunge al file che c'è, che tiene la sua data di allora.
        "forgotten": "yes" if entry.forgotten else "",
    }
    if not any(wanted.values()):
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    fields, body = _parse_archived(text)
    missing = [f"{k}: {v}" for k, v in wanted.items() if v and not fields.get(k)]
    if not missing or not body:
        return
    lines = text.splitlines()
    if lines and lines[0].strip() == _SEPARATOR:
        close = next(
            i for i, line in enumerate(lines[1:], 1) if line.strip() == _SEPARATOR
        )
        lines[close:close] = missing
        updated = "\n".join(lines) + "\n"
    else:
        # Un file senza intestazione (semplificato a mano) ne riceve una con i
        # soli campi nuovi: il corpo resta com'è.
        updated = "\n".join([_SEPARATOR, *missing, _SEPARATOR, "", body, ""])
    atomic_write(path, updated)


def unarchive(memory_dir: Path, entry_id: str) -> bool:
    """Toglie dall'archivio il file di *entry_id*, quando quella frase torna viva.

    Una formulazione viva e la sua copia in archivio direbbero la stessa cosa in
    due posti, e l'archivio smetterebbe di voler dire «non è più nei file». Le
    versioni più vecchie che puntano a questo id (``superseded_by``) non si
    toccano: :func:`archive_chains` tratta già un legame che esce dall'archivio
    come la fine della catena, e quella fine adesso è la voce viva.

    Ritorna ``True`` se ha tolto qualcosa. Un id assente non è un errore.
    """
    directory = archive_dir(memory_dir)
    # Solo lettere e cifre: l'id finisce in un ``glob``, e un ``*`` arrivato da
    # chi chiama toglierebbe l'archivio intero invece di un file.
    if not _PLAIN_ID.fullmatch(entry_id or "") or not directory.is_dir():
        return False
    removed = False
    for path in directory.glob(f"*-{entry_id}.md"):
        try:
            path.unlink()
            removed = True
        except FileNotFoundError:
            continue
    return removed


def archived_ids(memory_dir: Path) -> set[str]:
    """I nomi dei file in archivio, per fotografarlo prima e dopo un run."""
    directory = archive_dir(memory_dir)
    try:
        return {p.name for p in directory.glob("*.md")}
    except OSError:
        return set()


def summarize_archived(memory_dir: Path, name: str) -> str:
    """Il fatto contenuto in un file d'archivio, per un log leggibile.

    Il nome del file porta data e id, che bastano a ritrovarlo e non dicono
    niente su *cosa* sia stato spostato — ed è cosa, non quanto, che serve a chi
    legge un avviso e deve decidere se andare a guardare.
    """
    path = archive_dir(memory_dir) / name
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return name
    _, _, body = text.partition(f"{_SEPARATOR}\n\n")
    first = body.strip().splitlines()
    return first[0] if first else name


def _parse_archived(text: str) -> tuple[dict[str, str], str]:
    """Separa l'intestazione dal fatto in un file d'archivio.

    Tollerante come ``parse_entries``: un file senza intestazione — perché
    l'utente l'ha aperto a mano e l'ha semplificato, cosa che questa cartella
    invita a fare essendo visibile nel file browser — resta leggibile, e il suo
    corpo è tutto il testo. Perdere il fatto perché mancano i metadati sarebbe
    il fallimento esatto che l'archivio esiste per impedire.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != _SEPARATOR:
        return {}, text.strip()
    fields: dict[str, str] = {}
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == _SEPARATOR:
            return fields, "\n".join(lines[i + 1:]).strip()
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip()
    # Intestazione mai chiusa: il file è troncato. Si ritorna quel che c'è
    # invece di niente, per la stessa ragione di sopra.
    return fields, ""


def read_archived(path: Path) -> ArchivedEntry | None:
    """Rilegge una voce degradata dal suo file, o ``None`` se illeggibile."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    fields, body = _parse_archived(text)
    if not body:
        return None
    # L'id del nome file vince su quello dell'intestazione: il nome è ciò con
    # cui la voce è stata trovata, e un'intestazione modificata a mano non deve
    # poter restituire una voce che risponde a un id diverso da quello chiesto.
    stem_id = path.stem.split("-")[-1]
    return ArchivedEntry(
        id=stem_id or fields.get("id", ""),
        text=body,
        source=fields.get("source", ""),
        heading=fields.get("heading", ""),
        retention=fields.get("retention", ""),
        demoted=fields.get("demoted", ""),
        superseded_by=fields.get("superseded_by", ""),
        retracted=fields.get("retracted", "").lower() in {"yes", "true"},
        forgotten=fields.get("forgotten", "").lower() in {"yes", "true"},
    )


def list_archived(memory_dir: Path) -> list[ArchivedEntry]:
    """Tutte le voci degradate, **dalla più recente**.

    L'ordine non è estetico: è ciò che decide chi sopravvive quando l'elenco
    viene tagliato. Una voce tolta la settimana scorsa ha molte più probabilità
    di essere quella che l'utente sta cercando di una tolta a marzo, e il nome
    del file — che porta la data in testa — rende l'ordinamento una `sort` sul
    nome invece di 41 letture.
    """
    directory = archive_dir(memory_dir)
    try:
        paths = sorted(directory.glob("*.md"), reverse=True)
    except OSError:
        return []
    entries = [read_archived(p) for p in paths]
    return [e for e in entries if e is not None]


@dataclass
class ArchiveChain:
    """Le formulazioni archiviate di uno stesso fatto, dalla più recente."""

    versions: list[ArchivedEntry] = field(default_factory=list)

    @property
    def latest(self) -> ArchivedEntry:
        return self.versions[0]


def archive_chains(entries: list[ArchivedEntry]) -> list[ArchiveChain]:
    """Raggruppa le voci per catena di ``superseded_by``, nell'ordine dell'archivio.

    Ogni voce segue i legami finché restano in archivio; dove escono (la voce
    nuova è viva, o non c'è più) quella è la testa della catena. Un ciclo — la
    stessa formulazione tolta, rimessa e ritolta — si ferma alla prima voce già
    vista. La catena prende il posto della sua voce più recente nell'elenco.
    """
    by_id = {e.id: e for e in entries}

    def head(entry: ArchivedEntry) -> str:
        seen = {entry.id}
        current = entry
        while current.superseded_by in by_id and current.superseded_by not in seen:
            current = by_id[current.superseded_by]
            seen.add(current.id)
        return current.id

    # ``entries`` arriva già dalla più recente, e un dict tiene l'ordine
    # d'inserimento: ogni catena prende il posto della sua prima voce vista.
    groups: dict[str, ArchiveChain] = {}
    for entry in entries:
        groups.setdefault(head(entry), ArchiveChain()).versions.append(entry)
    return list(groups.values())


@dataclass(frozen=True, slots=True)
class Erosion:
    """Quanto le riscritture hanno consumato le voci ancora vive.

    ``passed`` sono i nomi e i numeri passati per una voce che esiste ancora;
    ``lost`` quelli fra loro che non sono più in nessun file vivo. Le voci tolte
    del tutto non contano: lì è Dream che ha deciso di togliere un fatto, qui una
    riscrittura che ne ha perso un pezzo per strada.
    """

    lost: frozenset[str]
    passed: frozenset[str]


def erosion(entries: list[ArchivedEntry], live_texts: Iterable[str],
            live_ids: Collection[str], *, extra_stop: Iterable[str] = ()) -> Erosion:
    """L'erosione delle voci vive: v. :class:`Erosion`.

    Una catena è viva quando una delle sue formulazioni è stata sostituita da
    una voce che c'è ancora (*live_ids*), o quando una sua formulazione è tornata
    identica nel file. Le ancore si contano su tutte le versioni della catena,
    e una è persa quando la sua parola non compare più in nessun file vivo, in
    qualunque voce: un nome spostato da una riga all'altra non è perso.

    Misurato sul telefono il 25/11/2026: 67 ancore perse su 151.
    """
    live = list(live_texts)
    stop = tuple(extra_stop)
    vocab = Vocabulary.from_texts([e.text for e in entries] + live, extra_stop=stop)
    present = words("\n".join(live))
    passed: set[str] = set()
    for chain in archive_chains(entries):
        if not any(v.superseded_by in live_ids or v.id in live_ids for v in chain.versions):
            continue
        for version in chain.versions:
            passed |= extract(version.text, vocab, extra_stop=stop)
    return Erosion(
        lost=frozenset(a for a in passed if a not in present),
        passed=frozenset(passed),
    )


@dataclass(frozen=True, slots=True)
class LostDetail:
    """Un nome o un numero che solo l'archivio ha ancora.

    ``anchor`` è la chiave piegata con cui si confronta (``anchors.extract``),
    ``shown`` la forma con cui il testo lo scriveva («Cremona», non «cremona»),
    ``version`` la formulazione archiviata **più recente** che lo porta: è la
    frase da rimettere se lo si vuole indietro, e la più recente perché fra le
    formulazioni che lo dicevano è quella più vicina a come il fatto è oggi.
    """

    anchor: str
    shown: str
    version: ArchivedEntry


def lost_details(
    chains: list[ArchiveChain],
    live_text: str,
    *,
    extra_stop: Iterable[str] = (),
    only: Collection[int] | None = None,
) -> dict[int, list[LostDetail]]:
    """Per ogni catena, i nomi e i numeri che nessun file vivo nomina più.

    La chiave è l'indice della catena in *chains*; una catena senza perdite non
    compare. *live_text* è il testo dei file vivi, tutto insieme: un nome
    spostato da una riga all'altra non è perso. *only* limita la misura ad
    alcune catene (``recall`` la chiede per quelle trovate), ma il vocabolario
    si fa sempre su tutte, perché è il confronto fra tutti i testi a dire se una
    parola maiuscola è un nome o l'inizio di una frase.

    Le formulazioni ``retracted`` e ``forgotten`` non portano dettagli: una
    smentita non era vera, e una che l'utente ha tolto non deve tornare,
    nemmeno come «rimetti questo nome». Ogni elenco è in ordine di chiave, così
    la stessa domanda dà la stessa riga.

    Il cuore viene da ``MemoryRecallTool._lost_details``, ora uno dei chiamanti:
    l'altro sarà la stanza «Cosa ricorda di te», che mostra «Rimetti» accanto
    alla voce viva.
    """
    stop = tuple(extra_stop)
    texts = [e.text for chain in chains for e in chain.versions]
    vocab = Vocabulary.from_texts([*texts, live_text], extra_stop=stop)
    present = words(live_text)
    forms = surface_forms(texts)
    out: dict[int, list[LostDetail]] = {}
    for index, chain in enumerate(chains):
        if only is not None and index not in only:
            continue
        carrier: dict[str, ArchivedEntry] = {}
        # Dalla più recente: la prima versione che porta un'ancora è quella che
        # la tiene.
        for version in chain.versions:
            if version.retracted or version.forgotten:
                continue
            for anchor in extract(version.text, vocab, extra_stop=stop):
                if anchor not in present:
                    carrier.setdefault(anchor, version)
        if carrier:
            out[index] = [
                LostDetail(anchor=a, shown=forms.get(a, a), version=carrier[a])
                for a in sorted(carrier)
            ]
    return out
