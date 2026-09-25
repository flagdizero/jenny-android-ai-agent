"""Il tier freddo della memoria: dove finisce una voce quando lascia i file caldi.

La fase 2 di ``.agent/memory-plan.md`` sostituisce la cancellazione con la
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
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from jenny.utils.path import atomic_write

ARCHIVE_DIRNAME = "archive"

# Il trattino del bullet appartiene al file di provenienza, non al fatto: lì era
# un elenco, qui è una frase. Toglierlo tiene il corpo pulito per chi legge e per
# qualunque cosa lo indicizzi.
_BULLET_PREFIX = re.compile(r"^[-*][ \t]+")

_SEPARATOR = "---"


@dataclass(frozen=True, slots=True)
class ArchivedEntry:
    """Una voce degradata, con il minimo che serve per ritrovarla e rimetterla.

    ``source`` e ``heading`` sono l'indirizzo da cui è arrivata: senza, una voce
    riletta fra sei mesi è una frase senza contesto, e rimetterla al suo posto
    diventa un indovinello. ``retention`` è il tag del Consolidator quando si
    conosce — spesso non si conosce, perché i tag vengono tolti dal testo prima
    che arrivi nei file, quindi è opzionale e non si inventa.
    """

    id: str
    text: str
    source: str
    heading: str = ""
    retention: str = ""
    demoted: str = ""


def archive_dir(memory_dir: Path) -> Path:
    return Path(memory_dir) / ARCHIVE_DIRNAME


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
    """
    existing = find_archived(memory_dir, entry.id)
    if existing is not None:
        return existing

    day = when or date.today()
    stamped = ArchivedEntry(
        id=entry.id,
        text=entry.text,
        source=entry.source,
        heading=entry.heading,
        retention=entry.retention,
        demoted=entry.demoted or day.isoformat(),
    )
    directory = archive_dir(memory_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / archive_filename(entry.id, day)
    # ``atomic_write`` come per i cursori: qui il file *è* l'unica copia rimasta
    # del fatto nel momento in cui ``remove`` lo toglie da USER.md, e un
    # troncamento a metà sarebbe la perdita che tutta la fase esiste per impedire.
    atomic_write(path, render_archived(stamped))
    return path


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
