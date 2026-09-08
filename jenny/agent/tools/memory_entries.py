"""Scrittura *per voce* sui file di memoria a lungo termine.

Oggi l'unità di scrittura è il **file**: chi vuole aggiungere un fatto a
``USER.md`` riscrive tutto il file, e l'unico modo di sapere se il fatto è
arrivato su disco è confrontare le dimensioni prima e dopo — una stima, con una
classe di falsi negativi nota (una correzione che accorcia *portandosi dentro* il
fatto nuovo legge come "non è atterrato niente"). Da lì nasce metà del registro
dei difetti in ``.agent/memory-plan.md``.

Qui l'unità è la **voce**: un bullet sotto la sua intestazione. ``add`` dice
quale fatto aggiungere, e "è atterrato?" diventa una verifica invece che una
misura. È la stessa forma di ``memory add|replace|remove`` di Hermes, adottata
per la stessa ragione.

Tre vincoli che il modulo si porta dietro dal piano, e che non vanno allentati
senza rileggerlo:

1. **Il formato dei file non cambia.** Le voci sono i bullet markdown che ci
   sono già, sotto le intestazioni ``##`` che ci sono già. Quei file si aprono
   dal browser Workspace e si correggono a mano: un formato interno li
   trasformerebbe in un database che si guarda da fuori, e la memoria di una
   persona non è un database che si guarda da fuori.
2. **Gli id sono hash del contenuto, mai posizioni.** Una posizione è valida
   solo finché nessun altro scrive, e qui scrivono Dream, il review pass e
   l'utente dal browser. Un id posizionale sotto due scritture concorrenti non
   sbaglia rumorosamente: cancella la voce sbagliata.
3. **Solo due destinazioni**, ``USER.md`` e ``memory/MEMORY.md``, risolte qui e
   non passate dal chiamante. ``SOUL.md`` resta a scrittura-file: è prosa con
   una struttura, non un elenco, e il mestiere di ridurlo è del review pass che
   legge prima di decidere. Non c'è nessun parametro di path, quindi non c'è
   nessuna superficie di traversal da difendere.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from jenny.agent.memory_archive import ArchivedEntry, archive_entry
from jenny.agent.tools.base import Tool
from jenny.agent.tools.file_state import FileStates
from jenny.utils.path import atomic_write

if TYPE_CHECKING:
    from jenny.agent.tools.context import ToolContext
    from jenny.agent.tools.filesystem import WriteSizeGuard

# Nome logico → path relativo al workspace. Gli stessi nomi di
# ``/dream budget <memory|user|soul>``: chi legge i log del comando e chi legge
# le chiamate del tool sta guardando gli stessi due file, e chiamarli in due modi
# diversi costa una traduzione a ogni lettura.
MEMORY_TARGETS: dict[str, str] = {
    "user": "USER.md",
    "memory": "memory/MEMORY.md",
}

# Un bullet di primo livello: nessun rientro, ``-`` o ``*``, e del testo dopo.
# Il rientro è ciò che distingue una voce nuova da una continuazione della
# precedente, quindi la sua assenza è parte del match e non un dettaglio.
_BULLET = re.compile(r"^[-*][ \t]+\S")
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.*)$")

# Lunghezza dell'id. Otto esadecimali sono 4 miliardi di valori: su file da
# qualche decina di voci la collisione è un non-problema, e la stringa resta
# corta abbastanza da stare in una riga di elenco senza mangiarsela.
_ID_CHARS = 8

# Un marcatore di elenco qualsiasi, non solo quello che fa una voce: ``-``,
# ``*``, ``+`` e i numerati. Serve per **confrontare** le righe, non per
# riconoscerle: promuovere una riga di prosa a bullet, o togliere il numero a un
# elenco, non fa sparire il testo, e archiviarlo come perduto riempirebbe
# l'archivio di roba che è ancora nel file — cioè renderebbe l'archivio rumore.
_LIST_PREFIX = re.compile(r"^([-*+]|\d+[.)])[ \t]+")

# Una riga senza nemmeno un carattere di parola non è contenuto: sono i ``---``,
# le recinzioni di codice, i separatori di tabella. Sparire per loro non è una
# perdita, ed è la struttura del file che si sta riscrivendo.
_HAS_WORD = re.compile(r"\w")

# Come si presenta in archivio un frammento che non era una voce. Finisce nel
# campo ``heading``, che è l'unico dei metadati che ``recall`` mostra al modello:
# la riga aperta dice "da USER.md › Preferences (body text, not an entry)", e
# quel "not an entry" è tutto il punto. Un paragrafo di prosa recuperato senza
# quella qualifica si leggerebbe come un fatto che qualcuno ha affermato, mentre
# è il testo che stava *intorno* ai fatti.
_FRAGMENT_NOTE = "(body text, not an entry)"


@dataclass(frozen=True, slots=True)
class Entry:
    """Una voce: il bullet e le sue continuazioni, con dove sta nel file.

    ``start``/``end`` sono indici di riga (``end`` escluso) e valgono **solo**
    per il testo da cui la voce è stata estratta: servono a riscrivere il file
    subito dopo averlo letto, non a essere conservati. L'identità che sopravvive
    è ``id``, che è funzione del solo contenuto.
    """

    id: str
    heading: str
    text: str
    start: int
    end: int


def entry_id(text: str) -> str:
    """Id stabile di una voce, funzione del solo contenuto.

    Normalizza gli spazi di bordo e i fine-riga prima di digerire: una voce che
    ha guadagnato un a-capo finale in un salvataggio dal browser è la stessa
    voce, e un id che cambiasse per quello manderebbe un ``remove`` a vuoto.
    Tutto il resto — maiuscole, punteggiatura, spazi interni — conta, perché
    cambiarlo cambia il fatto.
    """
    normalized = "\n".join(line.rstrip() for line in text.strip().splitlines())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:_ID_CHARS]


def _is_continuation(line: str) -> bool:
    """Una riga che appartiene al bullet precedente invece di aprirne uno nuovo.

    Cioè: non vuota e rientrata. I bullet annidati (``  - ...``) ricadono qui,
    ed è voluto — un sotto-elenco è parte della voce che lo introduce, e
    staccarlo produrrebbe voci orfane che nessuno può né leggere né rimuovere.
    """
    return bool(line.strip()) and line[:1] in (" ", "\t")


def parse_entries(text: str) -> list[Entry]:
    """Estrae le voci di primo livello con l'intestazione sotto cui stanno.

    Tollerante di proposito su tutto ciò che non è una voce: titolo del file,
    paragrafi di prosa, righe vuote, intestazioni senza bullet sotto. Quello che
    non è un bullet non è una voce e resta dov'è — questo modulo non riformatta
    i file, li modifica in un punto.
    """
    lines = text.splitlines()
    entries: list[Entry] = []
    heading = ""
    i = 0
    while i < len(lines):
        line = lines[i]
        match = _HEADING.match(line)
        if match:
            # Solo ``##`` e più profonde sono sezioni: la ``#`` è il titolo del
            # file ("# User Profile"), e usarla come intestazione di sezione
            # farebbe finire ogni voce sotto un nome che nel file non indicizza
            # niente.
            heading = match.group(2).strip() if len(match.group(1)) >= 2 else ""
            i += 1
            continue
        if not _BULLET.match(line):
            i += 1
            continue
        start = i
        i += 1
        while i < len(lines) and _is_continuation(lines[i]):
            i += 1
        body = "\n".join(lines[start:i])
        entries.append(
            Entry(id=entry_id(body), heading=heading, text=body, start=start, end=i)
        )
    return entries


@dataclass(frozen=True, slots=True)
class Fragment:
    """Testo che lascia un file di memoria senza essere una voce.

    Un paragrafo, una riga di prosa sotto un elenco, una voce numerata: tutto
    ciò che ``parse_entries`` ignora di proposito perché non è un bullet. Non ha
    id qui dentro — lo prende da ``entry_id`` come tutto il resto, al momento di
    archiviarlo — perché la sua identità è il testo, esattamente come per una
    voce.
    """

    text: str
    heading: str


def _line_key(line: str) -> str:
    """La riga ridotta a ciò di cui si chiede "c'è ancora?".

    Cioè: senza spazi di bordo e senza marcatore di elenco. Le tre forme
    ``Odia le riunioni``, ``- Odia le riunioni`` e ``1. Odia le riunioni``
    portano lo stesso testo, e un confronto che le distinguesse dichiarerebbe
    perduta una riga che il review pass ha solo promossa a voce.
    """
    return _LIST_PREFIX.sub("", line.strip()).strip()


def lost_fragments(before: str, new_text: str) -> list[Fragment]:
    """I blocchi di testo che c'erano e non ci sono più, voci escluse.

    Esiste perché la rete al confine del file, finché guardava solo le voci,
    aveva una maglia della dimensione del prompt del review pass: quel prompt
    chiede esplicitamente di cancellare prosa ("l'introduzione che spiega a cosa
    serve il file"), e una riscrittura che toglieva un paragrafo, una voce
    numerata e una riga sciolta ne archiviava zero. Un testo che sparisce senza
    passare da nessuna parte è la perdita definitiva che tutta la fase 2 esiste
    per impedire, e non diventa meno definitiva perché il testo non cominciava
    con un trattino.

    Cosa **non** è un frammento, e perché:

    - Le **intestazioni**. Sono la struttura del file, non il suo contenuto;
      riorganizzare le sezioni è il mestiere del review pass, e archiviare un
      ``## Preferences`` produrrebbe un "fatto" che non dice niente.
    - Le righe **di una voce**. Quelle hanno già il loro percorso, con id e
      deduplica, e passarle due volte darebbe due file per la stessa perdita.
    - Le righe **senza caratteri di parola** (``---``, code fence, separatori).
    - Le righe **ancora presenti**, confrontate per ``_line_key``: spostate di
      sezione, indentate, promosse a bullet — non se ne è andato niente.

    Le righe contigue si raggruppano in un frammento solo. Un paragrafo spezzato
    in cinque file d'archivio non è un paragrafo recuperabile: è cinque frasi
    orfane, e chi le rilegge non sa più in che ordine stavano.
    """
    surviving = {_line_key(line) for line in new_text.splitlines() if line.strip()}
    surviving.discard("")

    in_entry: set[int] = set()
    for entry in parse_entries(before):
        in_entry.update(range(entry.start, entry.end))

    out: list[Fragment] = []
    block: list[str] = []
    block_heading = ""
    heading = ""

    def flush() -> None:
        nonlocal block
        if block:
            out.append(Fragment(text="\n".join(block), heading=block_heading))
            block = []

    for i, line in enumerate(before.splitlines()):
        match = _HEADING.match(line)
        if match:
            flush()
            # Stessa regola di ``parse_entries``: la ``#`` è il titolo del file,
            # non una sezione, e usarla come indirizzo manderebbe ogni frammento
            # sotto un nome che nel file non indicizza niente.
            heading = match.group(2).strip() if len(match.group(1)) >= 2 else ""
            continue
        key = _line_key(line)
        if (
            not line.strip()
            or i in in_entry
            or not _HAS_WORD.search(key)
            or key in surviving
        ):
            flush()
            continue
        if not block:
            block_heading = heading
        block.append(line.rstrip())
    flush()
    return out


def fragment_heading(heading: str) -> str:
    """L'indirizzo con cui un frammento si presenta a ``recall``."""
    return f"{heading} {_FRAGMENT_NOTE}".strip() if heading else _FRAGMENT_NOTE


def find_entry(entries: list[Entry], target: str) -> tuple[Entry | None, str]:
    """Risolve un id o un frammento di testo in **una** voce.

    Ritorna ``(voce, "")`` oppure ``(None, motivo)``. L'ambiguità è un errore,
    non una scelta: con due voci che contengono lo stesso frammento, indovinare
    significa cancellare quella sbagliata, e il modello ha in mano gli id per
    disambiguare da solo. Il motivo le elenca, così la seconda chiamata è
    risolutiva senza rileggere il file.
    """
    needle = target.strip()
    if not needle:
        return None, "empty target: pass an entry id or some of its text"

    exact = [e for e in entries if e.id == needle]
    if exact:
        return exact[0], ""

    matches = [e for e in entries if needle.lower() in e.text.lower()]
    if not matches:
        return None, f"no entry matches {needle!r}"
    if len(matches) > 1:
        listed = ", ".join(f"{e.id} ({_summarize(e.text)})" for e in matches[:5])
        return None, (
            f"{len(matches)} entries match {needle!r}: {listed}. "
            "Pass one of those ids instead."
        )
    return matches[0], ""


def _summarize(text: str, limit: int = 60) -> str:
    """Prima riga della voce, accorciata, per elenchi e messaggi d'errore."""
    first = text.strip().splitlines()[0] if text.strip() else ""
    first = re.sub(r"^[-*][ \t]+", "", first).strip()
    return first if len(first) <= limit else first[: limit - 1] + "…"


def _as_bullet(text: str) -> str:
    """Il testo del modello come bullet, senza raddoppiare quello che c'è già."""
    body = text.strip()
    if _BULLET.match(body) or body.startswith(("- ", "* ")):
        return body
    return f"- {body}"


def requested_heading(raw: Any) -> str | None:
    """La sezione chiesta dal modello, coercita — o ``None`` se non l'ha chiesta.

    ``action`` e ``file`` passavano da ``str(...)`` e ``heading`` no: arrivava
    crudo fino a ``heading.strip()`` dentro :func:`add_entry`, quindi un provider
    che serializza ``{"heading": 3}`` faceva ``AttributeError`` — raccolto come
    errore soft del tool, cioè uno slot di ``ToolErrorBudget`` speso per una
    conversione mancante. ``str(...)`` conserva l'intenzione: la sezione ``3``
    esiste come qualunque altra.

    Il vuoto diventa ``None`` e non ``""``: con ``""`` :func:`add_entry` cerca la
    sezione *senza titolo* e, non trovandola, apre una sezione nuova scrivendo
    ``## `` — un'intestazione senza nome, in un file che il modello poi rilegge.
    ``None`` è invece il caso documentato "non si sa": la voce va in fondo al
    file, visibile e facile da spostare.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def add_entry(text: str, new_text: str, *, heading: str | None = None) -> tuple[str, str]:
    """Aggiunge una voce in coda alla sua sezione.

    Ritorna ``(nuovo_testo, errore)``. In coda alla *sezione* e non al file
    perché l'intestazione è l'unica struttura che questi file hanno, e una voce
    finita sotto quella sbagliata è peggio di una voce mancante: si legge come
    vera in un contesto che non è il suo.

    Con ``heading=None`` la voce va in fondo al file, che è il posto giusto
    quando non si sa: visibile, in coda, facile da spostare a mano.
    """
    body = _as_bullet(new_text)
    if not body.strip("-* \t"):
        return "", "refusing to add an empty entry"

    lines = text.splitlines()
    entries = parse_entries(text)

    duplicate = next((e for e in entries if e.id == entry_id(body)), None)
    if duplicate is not None:
        # Non è un errore da propagare come fallimento del turno: il fatto *è*
        # in memoria, che è il risultato che il chiamante voleva. Ma dirlo
        # cambia il seguito — il consolidatore ri-estrae di continuo gli stessi
        # fatti (v. D5 nel piano), e un modello che si sente dire "già presente"
        # ha l'informazione per smettere di riproporlo.
        return text, f"already present as {duplicate.id}"

    if heading is None:
        insert_at = len(lines)
    else:
        wanted = heading.strip().lower()
        in_section = [e for e in entries if e.heading.strip().lower() == wanted]
        if in_section:
            insert_at = max(e.end for e in in_section)
        else:
            # Sezione assente: si crea in fondo. L'alternativa — rifiutare —
            # costringerebbe il modello a un giro di lettura per scoprire un
            # nome che poteva scrivere lui, e le sezioni di questi file nascono
            # esattamente così.
            lines = _ensure_trailing_blank(lines)
            lines.append(f"## {heading.strip()}")
            insert_at = len(lines)
    new_lines = lines[:insert_at] + [body] + lines[insert_at:]
    return _joined(new_lines), ""


def replace_entry(text: str, target: str, new_text: str) -> tuple[str, str]:
    """Sostituisce una voce mantenendone il posto."""
    body = _as_bullet(new_text)
    if not body.strip("-* \t"):
        return "", "refusing to replace an entry with an empty one"

    entries = parse_entries(text)
    entry, why = find_entry(entries, target)
    if entry is None:
        return "", why
    lines = text.splitlines()
    new_lines = lines[: entry.start] + body.splitlines() + lines[entry.end :]
    return _joined(new_lines), ""


def remove_entry(text: str, target: str) -> tuple[str, str, Entry | None]:
    """Toglie una voce dal file e la restituisce.

    La voce torna indietro perché il chiamante deve poterne fare qualcosa: nella
    fase 2 del piano ``remove`` non cancella più, **degrada** — scrive la voce
    nell'archivio prima di toglierla dal file, dentro il tool e senza che il
    modello sappia che l'archivio esiste. Questa firma è il gancio per quel
    passo, ed è il motivo per cui qui non si perde il testo.
    """
    entries = parse_entries(text)
    entry, why = find_entry(entries, target)
    if entry is None:
        return "", why, None
    lines = text.splitlines()
    new_lines = lines[: entry.start] + lines[entry.end :]
    return _joined(new_lines), "", entry


def _ensure_trailing_blank(lines: list[str]) -> list[str]:
    """Una riga vuota prima di una nuova intestazione, se non c'è già."""
    if lines and lines[-1].strip():
        return [*lines, ""]
    return list(lines)


def _joined(lines: list[str]) -> str:
    """Riassembla il file con un solo a-capo finale.

    Questi file li legge anche un umano in un editor, e un file di testo finisce
    con un a-capo. Normalizzare solo il fondo, e non il resto, tiene il diff
    della modifica limitato al punto che è stato toccato.
    """
    return "\n".join(lines).rstrip("\n") + "\n"


def render_entries(entries: list[Entry]) -> str:
    """Elenco delle voci con i loro id, per la risposta del tool."""
    if not entries:
        return "(no entries)"
    out: list[str] = []
    heading = None
    for entry in entries:
        if entry.heading != heading:
            heading = entry.heading
            out.append(f"## {heading}" if heading else "## (no section)")
        out.append(f"  {entry.id}  {_summarize(entry.text)}")
    return "\n".join(out)



def make_entry_archiver(workspace: Path) -> Callable[[Path, str], None]:
    """Gancio pre-scrittura che degrada le voci in uscita da un file di memoria.

    Perché non basta farlo dentro ``memory remove`` (v. 2.4b del piano). Il
    review pass — cioè lo scrittore i cui errori sono definitivi, quello che il
    2026-08-18 ha tolto cinque voci vere da ``USER.md`` — **non** pota con il tool
    per voci: usa ``apply_patch`` e ``edit_file`` sui file interi, che è
    esattamente ciò che gli serve per ristrutturare. Una degradazione che vive in
    un solo tool protegge il turno incrementale e lascia scoperto proprio il
    passaggio che cancella.

    Quindi la difesa sta al **confine del file** e non dentro un tool: qualunque
    cosa stia per riscrivere ``USER.md`` o ``memory/MEMORY.md`` passa di qui, si
    confronta ciò che c'è su disco con ciò che sta per prendere il suo posto, e le
    voci che spariscono finiscono in archivio. Non serve nessuna collaborazione
    dal modello, che è il principio portante del piano.

    Tre proprietà volute, non effetti collaterali:

    - **Non protegge solo i bullet.** Qualunque riga di contenuto che c'era e non
      c'è più finisce in archivio, voce o no (v. :func:`lost_fragments`). Il
      prompt del review pass chiede *esplicitamente* di cancellare prosa, e una
      rete che guardasse solo le voci lascerebbe scoperto proprio ciò che quel
      prompt manda a cancellare.
    - **Una voce riscritta viene archiviata nella sua versione vecchia.** L'id è
      l'hash del contenuto, quindi cambiare il testo la fa sparire come voce e
      ricomparire come un'altra. È un po' di rumore in archivio in cambio del
      fatto che nessuna formulazione precedente sia irrecuperabile, ed è il verso
      giusto dello scambio: il testo costa poco, un fatto perso no.
    - **Non solleva mai.** Un archivio che non si scrive non deve impedire una
      scrittura legittima né far fallire un run: qui la degradazione è una rete,
      non una condizione. L'ordine forte — archivia, e solo allora togli — resta
      dentro ``memory remove``, dove la voce da salvare si conosce con certezza.
    """
    root = Path(workspace)
    memory_dir = root / "memory"
    known = {(root / rel).resolve(): rel for rel in MEMORY_TARGETS.values()}

    def archiver(path: Path, new_text: str) -> None:
        source = known.get(Path(path).resolve())
        if source is None:
            return
        try:
            before = Path(path).read_text(encoding="utf-8")
        except OSError:
            # File che non c'è ancora: non se ne sta andando niente.
            return
        def save(text: str, heading: str) -> None:
            archived_id = entry_id(text)
            try:
                archive_entry(
                    memory_dir,
                    ArchivedEntry(
                        id=archived_id,
                        text=text,
                        source=source,
                        heading=heading,
                    ),
                )
            except OSError:
                logger.exception(
                    "Could not archive entry {} leaving {}", archived_id, source,
                )

        surviving = {entry.id for entry in parse_entries(new_text)}
        for entry in parse_entries(before):
            if entry.id in surviving:
                continue
            save(entry.text, entry.heading)
        # E poi tutto il resto che se ne va. Le voci hanno il loro giro sopra, con
        # id e deduplica intatti; qui passa ciò che ``parse_entries`` non vede —
        # prosa, elenchi numerati, righe sciolte — che fino a oggi lasciava il
        # file senza copia da nessuna parte.
        for fragment in lost_fragments(before, new_text):
            save(fragment.text, fragment_heading(fragment.heading))

    return archiver


class MemoryEntryTool(Tool):
    """``memory add|replace|remove`` sui due file a voci."""

    _scopes = {"core"}
    # Non scopribile come plugin: chi lo monta lo fa esplicitamente, perché a
    # chi darlo è una decisione aperta (punto 1.12 del piano) e un tool che si
    # auto-registra la prenderebbe per omissione.
    _plugin_discoverable = False

    def __init__(
        self,
        workspace: Path,
        *,
        file_states: FileStates | None = None,
        write_size_guard: "WriteSizeGuard | None" = None,
        entry_archiver: Callable[[Path, str], None] | None = None,
        allowed_targets: set[str] | None = None,
    ) -> None:
        self._workspace = Path(workspace)
        # Le destinazioni che *questo* montaggio del tool concede. ``None`` vuol
        # dire tutte, che e' il caso di Dream sulla conversazione personale e il
        # comportamento di sempre.
        #
        # Serve a un caso solo, ed e' meccanico di proposito: un run di Dream su
        # un batch di **progetto** riceve ``{"user"}``, perche' un fatto che
        # nasce dentro un progetto puo' diventare identita' e non inventario
        # (v. ``.agent/project-memory-plan.md``). Un rifiuto qui si prova con un
        # test; la stessa regola scritta nel prompt no — e questa e' l'unica
        # differenza che conta fra le due, perche' il prompt lo legge un modello
        # e questa riga no.
        #
        # Filtrato contro ``MEMORY_TARGETS`` alla costruzione: un nome sbagliato
        # nell'insieme darebbe un tool che rifiuta tutto, cioe' un guasto che si
        # vede solo a run finito.
        self._allowed_targets = (
            set(MEMORY_TARGETS)
            if allowed_targets is None
            else {t for t in allowed_targets if t in MEMORY_TARGETS}
        )
        # Gli stessi due contratti dei tool filesystem di Dream, e non per
        # simmetria: ``dream_should_advance_cursor`` legge questi contatori per
        # decidere se il cursore avanza. Un tool che scrivesse senza contarsi
        # produrrebbe un run con ``writes_attempted == 0`` — che quella funzione
        # legge come "non c'era niente da scrivere" — e il batch avanzerebbe
        # anche quando il fatto è rimasto fuori.
        self._file_states = file_states if file_states is not None else FileStates()
        self._write_size_guard = write_size_guard
        # Lo stesso gancio dei tool file, e serve per una maglia larga trovata sul
        # Titan 2 il 2026-08-19: il review pass ha riformulato una voce di
        # ``USER.md`` con ``memory replace``, e la versione vecchia è sparita senza
        # passare da nessuna parte. ``remove`` degradava, la rete al confine del
        # file copriva ``apply_patch`` — e in mezzo restava il tool per voci, che
        # scrive per conto suo. Una difesa vale la sua maglia più larga.
        self._entry_archiver = entry_archiver
        # Esito del run in voci, non in byte. È tutto il punto della fase 1: "il
        # fatto è atterrato?" smette di essere una differenza di dimensioni e
        # diventa una risposta. I tre contatori restano distinti perché rispondono
        # a domande diverse, e chi li legge (``batch_was_not_consolidated``) li
        # tratta in modo diverso a ragione.
        self.entries_added = 0
        self.entries_replaced = 0
        # Una ``add`` di un fatto che c'era già. Non è un fallimento e non è un
        # no-op: dice che *quel contenuto è in memoria*, cioè esattamente quel che
        # il chiamante voleva sapere. Senza questo numero un batch di soli
        # duplicati è indistinguibile da un batch mancato, e distinguerli
        # richiedeva una soglia di riempimento tarata a occhio.
        self.entries_already_present = 0

    @property
    def name(self) -> str:
        return "memory"

    @property
    def description(self) -> str:
        return (
            "Add, replace or remove a single entry in long-term memory. "
            "Entries are the markdown bullets in USER.md (about the user) and "
            "memory/MEMORY.md (about projects and the world). Prefer this over "
            "rewriting the whole file: it says what changed, and it cannot lose "
            "the rest of the file. Every call returns the file's entries with "
            "their ids, so a follow-up needs no extra read."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["add", "replace", "remove", "list"],
                    "description": "What to do with the entry.",
                },
                "file": {
                    "type": "string",
                    # L'elenco concesso, non tutto ``MEMORY_TARGETS``: dichiarare
                    # una destinazione che verrebbe rifiutata e' un invito a
                    # spendere un turno per sentirsi dire di no.
                    "enum": sorted(self._allowed_targets),
                    "description": (
                        "'user' for USER.md (the person: preferences, history, "
                        "relationships), 'memory' for memory/MEMORY.md "
                        "(projects, systems, facts about the world)."
                    ),
                },
                "text": {
                    "type": "string",
                    "description": (
                        "One entry, for add and replace. One fact, one line. "
                        "The leading '- ' is optional."
                    ),
                },
                "texts": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Several entries to add in one call — the whole batch at "
                        "once. The answer says, per fact, whether it was added or "
                        "was already there, so this replaces reading the file "
                        "first to work that out."
                    ),
                },
                "target": {
                    "type": "string",
                    "description": (
                        "Which entry to replace or remove: its id, or enough of "
                        "its text to identify it uniquely."
                    ),
                },
                "heading": {
                    "type": "string",
                    "description": (
                        "Section to add under, e.g. 'Preferences'. Created if "
                        "missing. Omit to append at the end of the file."
                    ),
                },
            },
            "required": ["action", "file"],
        }

    def _path(self, file: str) -> Path:
        return self._workspace / MEMORY_TARGETS[file]

    def _read(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            # File assente è lo stato normale di un'installazione nuova, non un
            # errore: la prima ``add`` lo crea.
            return ""

    def _report(self, file: str, path: Path, headline: str) -> str:
        text = self._read(path)
        entries = parse_entries(text)
        return "\n".join([
            headline,
            f"{MEMORY_TARGETS[file]}: {len(text):,} chars, {len(entries)} entries",
            "",
            render_entries(entries),
        ])

    @staticmethod
    def _facts(kwargs: dict[str, Any]) -> list[str]:
        """I fatti da aggiungere, da ``texts`` o da ``text``.

        Accetta entrambe le forme perché il modello arriva da entrambe: una sola
        ``add`` con un fatto resta la cosa naturale quando ce n'è uno, e obbligare
        a impacchettarlo in una lista sarebbe attrito senza guadagno.
        """
        raw = kwargs.get("texts")
        if isinstance(raw, str):
            raw = [raw]
        items = list(raw) if isinstance(raw, list) else []
        single = kwargs.get("text")
        if single:
            items.append(single)
        return [str(i).strip() for i in items if str(i).strip()]

    def _add_many(
        self,
        file: str,
        path: Path,
        text: str,
        facts: list[str],
        heading: str | None,
    ) -> str:
        """Aggiunge N fatti in **una** scrittura, e dice com'è andata voce per voce.

        Esiste per una ragione misurata (D10 nel piano). Con la sola ``add`` di un
        fatto alla volta, il modello non la usa per sapere se un fatto c'è già:
        chiama ``list``, che risponde per tutto il file in una chiamata, e filtra
        da sé. Sceglie bene — una ``list`` contro N ``add`` non è una preferenza,
        è aritmetica — ma così il run non produce **nessuna** evidenza per voce, e
        un batch di soli duplicati viene trattenuto pur essendo consolidato.
        Chiedergli di aggiungere alla cieca sarebbe chiedergli di pagare di più a
        ogni run per sempre; conviene invece cambiare l'aritmetica, così la mossa
        economica e quella che produce evidenza sono la stessa chiamata.

        La scrittura è una sola, ma i fatti si applicano **uno per uno** e il tetto
        si controlla a ogni passo: se il quinto non ci sta, i primi quattro restano
        salvati e il quinto viene dichiarato rifiutato. Tutto-o-niente qui sarebbe
        peggio — sotto pressione perderebbe anche i fatti che ci stavano.
        """
        current = text
        added: list[str] = []
        present: list[str] = []
        refused: list[str] = []
        problems: list[str] = []

        for fact in facts:
            candidate, why = add_entry(current, fact, heading=heading)
            if why.startswith("already present"):
                self.entries_already_present += 1
                present.append(_summarize(fact))
                continue
            if why:
                problems.append(f"{_summarize(fact)}: {why}")
                continue
            refusal = self._refusal(path, candidate, record=_as_bullet(fact))
            if refusal is not None:
                # Ci si ferma al primo che non entra: i fatti dopo non entrerebbero
                # comunque, e provarli tutti moltiplicherebbe i rifiuti aperti su
                # ``FileStates`` senza salvare una riga in più.
                refused.append(f"{_summarize(fact)} — {refusal}")
                break
            current = candidate
            added.append(_summarize(fact))

        if current != text:
            self._commit(path, current)
            self.entries_added += len(added)

        headline = ", ".join(filter(None, [
            f"{len(added)} added" if added else "",
            f"{len(present)} already present" if present else "",
            f"{len(refused)} refused" if refused else "",
            f"{len(problems)} rejected" if problems else "",
        ])) or "nothing to do"
        detail = [f"  + {a}" for a in added]
        detail += [f"  = {p} (already there)" for p in present]
        detail += [f"  ! {r}" for r in refused]
        detail += [f"  ! {q}" for q in problems]
        return self._report(file, path, "\n".join([f"{headline}.", *detail]))

    async def execute(self, **kwargs: Any) -> Any:
        action = str(kwargs.get("action") or "").strip().lower()
        file = str(kwargs.get("file") or "").strip().lower()
        if file not in MEMORY_TARGETS:
            return (
                f"unknown file {file!r}: pass one of "
                f"{', '.join(sorted(self._allowed_targets))}"
            )
        if file not in self._allowed_targets:
            # Distinto da "unknown": il nome esiste, la porta no. Loggato a
            # WARNING perche' su un run di progetto vuol dire che il prompt non ha
            # tenuto — e' il segnale che quel testo va rivisto, e senza log
            # sarebbe invisibile (il modello riceve il rifiuto e tira dritto).
            logger.warning(
                "memory tool: write to {!r} refused, this run may only write {}",
                file, ", ".join(sorted(self._allowed_targets)) or "nothing",
            )
            return (
                f"Cannot write {MEMORY_TARGETS[file]} in this run: it is not one of "
                f"{', '.join(sorted(self._allowed_targets))}. This is not a "
                f"restriction to work around — a fact that does not belong in "
                f"{' or '.join(MEMORY_TARGETS[t] for t in sorted(self._allowed_targets))} "
                f"has no other home here, and should be left out."
            )
        path = self._path(file)
        text = self._read(path)

        if action == "list":
            return self._report(file, path, f"{MEMORY_TARGETS[file]} entries:")

        if action == "add":
            facts = self._facts(kwargs)
            if not facts:
                return f"Cannot add to {MEMORY_TARGETS[file]}: no text given"
            return self._add_many(
                file, path, text, facts, requested_heading(kwargs.get("heading")),
            )

        if action == "replace":
            new_text, why = replace_entry(
                text, str(kwargs.get("target") or ""), str(kwargs.get("text") or ""),
            )
            if why:
                return f"Cannot replace in {MEMORY_TARGETS[file]}: {why}"
            refusal = self._write(path, new_text)
            if refusal is not None:
                return refusal
            self.entries_replaced += 1
            return self._report(file, path, "Replaced.")

        if action == "remove":
            new_text, why, entry = remove_entry(text, str(kwargs.get("target") or ""))
            if why or entry is None:
                return f"Cannot remove from {MEMORY_TARGETS[file]}: {why}"
            # Prima l'archivio, poi la rimozione — l'ordine è la garanzia. È la
            # stessa regola che il prompt del review pass insegna per spostare un
            # fatto ("scrivi la destinazione, conferma, poi cancella l'origine"),
            # e per lo stesso motivo: dei due esiti possibili di un fallimento a
            # metà, "il fatto è in due posti" si ripara guardandolo, "il fatto non
            # è in nessuno dei due" no, e niente lo direbbe.
            #
            # Il modello questo percorso non lo conosce e non gli viene passato:
            # la degradazione la fa il runtime da qui dentro. Nominare una quarta
            # destinazione nel prompt di Dream è già costato run interi, e una
            # destinazione che il modello può dimenticare non è una garanzia.
            try:
                archived = self._demote(file, entry)
            except OSError as exc:
                return (
                    f"Cannot remove from {MEMORY_TARGETS[file]}: the entry could not "
                    f"be archived first ({exc}), so it was left where it is."
                )
            refusal = self._write(path, new_text)
            if refusal is not None:
                # Praticamente irraggiungibile — una rimozione rimpicciolisce e il
                # guard lascia sempre passare chi rimpicciolisce — ma il ramo c'è
                # perché quella è una proprietà del guard, non di questo tool, e
                # ingoiare un rifiuto direbbe "rimossa" su un file intatto.
                return refusal
            return self._report(
                file, path, f"Removed {entry.id}. Kept in the archive as {archived.name}.",
            )

        return f"unknown action {action!r}: pass add, replace, remove or list"

    def _write(self, path: Path, text: str) -> str | None:
        """Scrive, o ritorna il messaggio di rifiuto del budget.

        L'intento si conta **prima** di qualunque controllo, come in
        ``_FsTool._resolve_write``: un tentativo bloccato è comunque un tentativo,
        ed è la differenza fra ``attempted > 0, ok == 0`` (il run ha provato e non
        c'è riuscito, il cursore non deve avanzare) e ``attempted == 0`` (non
        c'era niente da scrivere).

        Il tetto lo applica ancora, e di proposito: renderlo consultivo è la
        fase 3 del piano, una decisione che va presa lì e non qui di straforo.
        Finché vale, deve valere per tutte le scritture di Dream — un secondo
        percorso di scrittura non sottoposto al guard sarebbe un buco nel budget
        aperto per distrazione, non per scelta.
        """
        refusal = self._refusal(path, text)
        if refusal is not None:
            return refusal
        self._commit(path, text)
        return None

    def _demote(self, file: str, entry: Entry) -> Path:
        """Sposta la voce nel tier freddo prima che lasci il file caldo.

        Questa è la riga per cui la fase 2 esiste: da qui in poi togliere una voce
        è uno **spostamento**, non una cancellazione. Ne discendono tre cose che
        il piano si aspetta più avanti — far spazio riesce sempre, quindi nessuna
        scrittura ha più bisogno di essere rifiutata; il review pass non può più
        perdere niente, solo ricollocarlo (misurato il 2026-08-18: un secondo
        passaggio consecutivo tolse cinque voci vere da ``USER.md``, e l'unico
        recupero era uno snapshot); e il pavimento del "non cancellare mai" smette
        di poter bloccare un run, perché non blocca un *trasloco*.
        """
        return archive_entry(
            self._workspace / "memory",
            ArchivedEntry(
                id=entry.id,
                text=entry.text,
                source=MEMORY_TARGETS[file],
                heading=entry.heading,
            ),
        )

    def _refusal(self, path: Path, text: str, *, record: str | None = None) -> str | None:
        """Il guard direbbe di no? Conta il tentativo e apre il rifiuto se sì.

        Separato da :meth:`_commit` perché ``_add_many`` deve poter chiedere "ci
        sta?" per ogni fatto e scrivere una volta sola alla fine.

        *record* è il testo con cui **registrare** il rifiuto, che non sempre è
        quello passato al guard. Il guard vuole il file intero, perché il tetto è
        sulla dimensione del file. ``FileStates`` vuole invece il solo contenuto
        che non è atterrato: ricava le righe nuove rispetto al disco e chiude il
        rifiuto quando ne vede arrivare una. Passandogli il file cumulativo, in un
        batch dove i primi fatti entrano e il quinto no, fra quelle righe ci
        sarebbero anche le prime quattro — e la scrittura parziale chiuderebbe un
        rifiuto ancora aperto, facendo avanzare il cursore su un fatto perso.
        """
        if self._write_size_guard is None:
            return None
        refusal = self._write_size_guard(path, text)
        if refusal is not None:
            self._file_states.record_write_attempt()
            self._file_states.record_write_refused(path, record if record is not None else text)
        return refusal

    def _commit(self, path: Path, text: str) -> None:
        """La scrittura vera, contata come riuscita.

        Prima passa dal gancio di degradazione, per ogni azione e non solo per
        ``remove``: qui la domanda non è "cosa ha chiesto il modello" ma "cosa
        sta per sparire da questo file", e la risposta la dà il confronto, non
        l'intenzione. ``remove`` archivia comunque anche per conto suo, con un
        ordine più forte — se lì l'archivio fallisce la voce non si tocca — e
        l'archiviazione è idempotente, quindi i due percorsi non si pestano.
        """
        if self._entry_archiver is not None:
            self._entry_archiver(path, text)
        self._file_states.record_write_attempt()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Stesso ``atomic_write`` dei cursori di Dream: su Android il processo
        # può morire in qualsiasi momento, e questi file non hanno un formato che
        # renda evidente un troncamento — un ``USER.md`` tagliato a metà si legge
        # benissimo, semplicemente non contiene più la seconda metà della persona.
        atomic_write(path, text)
        self._file_states.record_write(path)

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        """**Rifiuta di costruirsi da un ``ToolContext``**, e non è pigrizia.

        Tre dipendenze fanno di questo tool un tool sicuro, e ``ToolContext`` non
        ne porta nessuna delle due che contano: ``write_size_guard`` (il tetto di
        budget) e ``entry_archiver`` (la copia in ``memory/archive/`` di quel che
        sta per sparire dal file). Senza il secondo, ``memory replace`` riscrive
        una voce e la versione precedente non finisce in nessun posto — è la
        maglia larga misurata sul Titan 2 il 2026-08-19, quella per cui esiste
        ``self._entry_archiver``. ``remove`` degrada, ``apply_patch`` è coperto
        dalla rete al confine del file, e un ``replace`` costruito da qui
        tornerebbe a essere il buco fra i due.

        Il percorso è irraggiungibile oggi (nessun ``TOOLS``,
        ``_plugin_discoverable = False``), quindi la scelta è fra un commento e un
        rifiuto per il giorno in cui qualcuno mette questo modulo in
        ``_HARDCODED_TOOL_MODULES``. Vale il rifiuto: un ``create()`` che solleva
        non aborta il boot — ``ToolLoader`` lo registra in ``failures`` e logga a
        ERROR il messaggio qui sotto (v. ``ToolLoadFailure``) — mentre un commento
        non impedisce niente. Dei due esiti, "il tool `memory` non c'è e il log
        dice perché" si scopre subito; "il tool c'è e perde la formulazione
        precedente" si scopre rileggendo ``USER.md`` fra un mese.

        Chi lo vuole davvero montare lo costruisce esplicitamente con le sue
        dipendenze, come fa ``MemoryStore.build_dream_tools``; darle a
        ``ToolContext`` è la decisione aperta del punto 1.12 del piano, non un
        default da prendere per omissione.
        """
        raise RuntimeError(
            "MemoryEntryTool cannot be built from a ToolContext: it needs a "
            "write_size_guard (the memory budget) and an entry_archiver (the "
            "archive copy that keeps a replaced entry from vanishing), and the "
            "context carries neither. Construct it explicitly, the way "
            "MemoryStore.build_dream_tools does."
        )


# Nessun ``TOOLS = [...]``: questo modulo non è ancora in
# ``_HARDCODED_TOOL_MODULES``. È il passo 1.2 del piano, e dipende dal 1.12 —
# se il tool serva anche l'agente principale o solo Dream.
