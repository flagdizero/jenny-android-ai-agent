"""Il richiamo per nome: le formulazioni archiviate tornano quando l'utente nomina qualcosa.

**Perché esiste** (misurato sul telefono il 25/11/2026). Dream fa spazio in
``USER.md`` riscrivendo le voci più corte, e una voce più corta può perdere un
dettaglio: una città, il nome di una pianta, una cifra. La formulazione vecchia
resta in ``memory/archive/``, ma nessun prompt la rileggeva, e il modello la
cercava solo se sapeva di non sapere — cioè mai, proprio nel caso che conta. Su
151 nomi e numeri passati per le voci ancora vive, 67 non erano più in nessun
file vivo; un utente ha dovuto ridire a Jenny dove abita un amico.

**Cosa fa.** Quando il messaggio dell'utente nomina qualcuno o qualcosa che
l'archivio conosce, il codice cerca le formulazioni archiviate che portano un
dettaglio che il turno non ha già, e le mette nel contesto del turno — accanto
al messaggio, come i cassetti della bacheca, mai nel prompt di sistema che è il
prefisso in cache. Datate, com'erano scritte: nessun riassunto, nessun modello.

**Cosa non fa.** Non scrive niente e non tiene stato su disco: il catalogo si
ricava dall'archivio, in memoria, e si rifà quando la cartella cambia. Non
riconosce un riferimento indiretto («mia sorella»), né un nome mai scritto con
la maiuscola in nessun appunto.

Le soglie vengono dalla misura del passo 0 (``scripts/memory_facts_probe.py``,
1.917 messaggi veri): il richiamo scatta sul 15% dei messaggi, porta un
dettaglio nuovo nel 64% di quei casi, e nel 95% dei turni sta sotto i 700
caratteri.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Collection, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.agent.anchors import Vocabulary, extract, words
from jenny.agent.diary import _in_history
from jenny.agent.memory_archive import ArchivedEntry, archive_chains, archive_dir, list_archived
from jenny.utils.helpers import clip
from jenny.utils.wiki_paths import fold

#: I file vivi di cui l'archivio conserva le formulazioni.
USER_SOURCE = "USER.md"
MEMORY_SOURCE = "memory/MEMORY.md"

#: Quanti nomi del messaggio fanno scattare il richiamo, al più: i più rari.
MAX_TRIGGERS = 2
#: Quante righe porta un turno, quanto lunga ognuna, quanto in tutto.
MAX_LINES = 4
MAX_LINE_CHARS = 240
MAX_BLOCK_CHARS = 700
#: Sotto questa lunghezza una parola non fa scattare niente: «Ugo» sì nei
#: fatti, ma tre lettere in un messaggio sono troppo spesso altro.
TRIGGER_MIN_CHARS = 4
#: Oltre quante volte il blocco è già nella cronologia replicata non se ne
#: aggiunge un altro: ogni blocco resta nella storia fino alla compattazione.
MAX_BLOCKS_IN_HISTORY = 6
#: Quanto un turno aspetta il catalogo che si sta rifacendo: sul Mac 1.100 voci
#: si leggono in 170 ms; oltre il tetto il turno usa il catalogo di prima.
WAIT_FOR_REBUILD_S = 0.5
#: Un'ancora che compare in più catene di così non distingue niente — il nome
#: dell'utente, la sua città — e non fa scattare: ``max(minimo, quota)``.
_DF_FLOOR = 8
_DF_SHARE = 0.05

#: La riga di testa del blocco: la cerca anche il conteggio nella cronologia.
#: «Answer from it directly» è la stessa frase del diario (``diary_section.md``):
#: senza, sull'emulatore il modello cercava lo stesso con ``recall`` e, fra i
#: risultati larghi («amico», «città»), prendeva un altro amico (25/11/2026, 2 giri su 3).
HEADER = (
    "Older wordings from your own memory archive about what this message names. "
    "What a line states, you know: answer from it directly, without searching for it. "
    "They are dated and may be out of date: what your memory files say now, a later "
    "line, or what the user says now wins. Never recite them unprompted."
)
#: In un quaderno: sono fatti della persona, non materiale del quaderno.
NOTEBOOK_NOTE = (
    "This is background about the user from outside this notebook: do not journal it here."
)


@dataclass(frozen=True, slots=True)
class _Version:
    entry: ArchivedEntry
    anchors: frozenset[str]


@dataclass(frozen=True, slots=True)
class _Chain:
    versions: tuple[_Version, ...]  # dalla più recente


@dataclass
class _State:
    chains: list[_Chain]
    by_anchor: dict[str, list[int]]
    vocab: Vocabulary
    stop: frozenset[str]
    df_ceiling: int
    stamp: int | None


def history_text(history: Sequence[Mapping[str, Any]]) -> str:
    """Il testo della cronologia replicata, blocchi di contesto compresi."""
    parts: list[str] = []
    for message in history:
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            parts.extend(str(b.get("text", "")) for b in content if isinstance(b, dict))
    return "\n".join(parts)


def blocks_in_history(history: Sequence[Mapping[str, Any]]) -> int:
    """Quante volte il blocco del richiamo è già nella cronologia replicata."""
    return history_text(history).count(HEADER)


class FactCatalogue:
    """Le catene dell'archivio, indicizzate per ancora, in memoria.

    Si costruisce in un thread (:meth:`refresh_async`) e si rifà quando la
    cartella dell'archivio cambia: ogni scrittura d'archivio passa da
    ``atomic_write``, che rinomina, quindi il ``mtime`` della cartella si muove.
    Un turno non costruisce mai: se il catalogo non è pronto, quel turno non
    richiama niente.
    """

    def __init__(self, workspace: Path, *, wikis_dir_name: str = "wikis",
                 extra_stop: Collection[str] = ()) -> None:
        self._workspace = Path(workspace)
        self._wikis_dir_name = wikis_dir_name
        self._extra_stop = tuple(extra_stop)
        self._state: _State | None = None
        self._lock = threading.Lock()
        self._building = False
        # Acceso quando nessuna costruzione è in corso: :meth:`wait` lo aspetta.
        self._idle = threading.Event()
        self._idle.set()

    @property
    def ready(self) -> bool:
        return self._state is not None

    def _stamp(self) -> int | None:
        try:
            return os.stat(archive_dir(self._workspace / "memory")).st_mtime_ns
        except OSError:
            return None

    def stale(self) -> bool:
        state = self._state
        return state is None or state.stamp != self._stamp()

    def refresh(self) -> None:
        """Ricostruisce il catalogo adesso, nel thread di chi chiama."""
        stamp = self._stamp()
        state = self._build(stamp)
        self._state = state
        logger.debug("Fact catalogue built: {} chains, {} anchors",
                     len(state.chains), len(state.by_anchor))

    def refresh_async(self) -> None:
        """Ricostruisce in un thread, se serve e se non lo sta già facendo."""
        with self._lock:
            if self._building or not self.stale():
                return
            self._building = True
            self._idle.clear()

        def run() -> None:
            try:
                self.refresh()
            except Exception:  # noqa: BLE001 — senza catalogo il richiamo tace
                logger.opt(exception=True).warning("Could not build the fact catalogue")
            finally:
                with self._lock:
                    self._building = False
                    self._idle.set()

        threading.Thread(target=run, name="fact-catalogue", daemon=True).start()

    def wait(self, timeout: float) -> bool:
        """Aspetta al più *timeout* secondi che finisca una costruzione in corso.

        Serve al turno che trova l'archivio appena cambiato — Dream ha appena
        spostato una formulazione — perché non risponda col catalogo di prima.
        Un'attesa corta e con un tetto, mai una costruzione nel turno: se non
        basta, il turno usa il catalogo che c'è.
        """
        return self._idle.wait(timeout)

    def _build(self, stamp: int | None) -> _State:
        memory = self._workspace / "memory"
        entries = list_archived(memory)
        texts = [e.text for e in entries]
        for rel in (USER_SOURCE, MEMORY_SOURCE):
            with suppress(OSError):
                texts.append((self._workspace / rel).read_text(encoding="utf-8"))
        # Il diario aiuta il vocabolario a contare le minuscole: più testo,
        # meglio si distingue un nome da una parola comune scritta maiuscola.
        with suppress(OSError):
            for line in (memory / "history.jsonl").read_text(encoding="utf-8").splitlines():
                with suppress(ValueError):
                    row = json.loads(line)
                    if isinstance(row, dict) and isinstance(row.get("content"), str):
                        texts.append(row["content"])
        stop = frozenset(fold(w) for w in (*self._extra_stop, *self._notebook_names()))
        vocab = Vocabulary.from_texts(texts, extra_stop=stop)

        chains: list[_Chain] = []
        by_anchor: dict[str, list[int]] = {}
        for chain in archive_chains(entries):
            if chain.latest.retracted:
                continue
            # Tolta dall'utente: la catena intera tace, anche se a portare la
            # lapide è una versione sola. Una formulazione più vecchia della
            # stessa frase che tornasse quando la si nomina sarebbe la voce tolta
            # che rientra dalla finestra.
            if any(v.forgotten for v in chain.versions):
                continue
            versions = tuple(
                _Version(v, extract(v.text, vocab, extra_stop=stop))
                for v in chain.versions if not v.retracted
            )
            index = len(chains)
            chains.append(_Chain(versions))
            for anchor in set().union(*(v.anchors for v in versions)):
                by_anchor.setdefault(anchor, []).append(index)
        ceiling = max(_DF_FLOOR, int(len(chains) * _DF_SHARE))
        return _State(chains, by_anchor, vocab, stop, ceiling, stamp)

    def _notebook_names(self) -> list[str]:
        # I nomi dei quaderni hanno già il loro blocco, e in un quaderno il suo
        # nome è in ogni messaggio: non sono ancore che fanno scattare.
        wikis = self._workspace / self._wikis_dir_name
        try:
            return [p.name for p in wikis.iterdir() if p.is_dir()]
        except OSError:
            return []

    def triggers(self, message: str) -> list[str]:
        """I nomi del messaggio che fanno scattare il richiamo, dal più raro.

        Il messaggio si legge piegato, perché sul telefono i nomi si scrivono
        minuscoli («dal mio amico oreste»). Un nome che è anche una parola
        comune («neve», «solo») fa scattare solo se il messaggio lo scrive
        maiuscolo a metà frase. I numeri non fanno scattare mai.
        """
        state = self._state
        if state is None or not message:
            return []
        named = extract(message, extra_stop=state.stop)
        found = []
        for anchor in words(message):
            if len(anchor) < TRIGGER_MIN_CHARS or anchor[:1].isdigit():
                continue
            hits = state.by_anchor.get(anchor)
            if not hits or len(hits) > state.df_ceiling or anchor in state.stop:
                continue
            if state.vocab.is_common_word(anchor) and anchor not in named:
                continue
            found.append(anchor)
        found.sort(key=lambda a: (len(state.by_anchor[a]), a))
        return found[:MAX_TRIGGERS]

    def recall_lines(self, message: str, *, known: str, sources: Collection[str],
                     history: Sequence[Mapping[str, Any]] = ()) -> list[str]:
        """Le righe richiamate per *message*: vuoto se niente porta un dettaglio nuovo.

        *known* è il testo che il turno ha già (file di memoria, cronologia,
        messaggio): una formulazione entra solo se porta un'ancora che lì non
        c'è, e solo se porta qualcosa che le righe già scelte non portano. Le
        righe dipendono solo dall'archivio: due turni uguali danno righe uguali,
        e una riga già nella cronologia non si rimanda.
        """
        state = self._state
        if state is None:
            return []
        known_words = words(known)
        out: list[str] = []
        brought: set[str] = set()
        used = 0
        for anchor in self.triggers(message):
            for index in state.by_anchor[anchor]:
                best: _Version | None = None
                best_new: frozenset[str] = frozenset()
                for version in state.chains[index].versions:
                    if version.entry.source not in sources or anchor not in version.anchors:
                        continue
                    new = version.anchors - known_words
                    if len(new) > len(best_new):
                        best, best_new = version, new
                if best is None or best_new <= brought:
                    continue
                line = _line(best.entry)
                if len(out) >= MAX_LINES or used + len(line) > MAX_BLOCK_CHARS:
                    return out
                # Lo stesso controllo del diario: una riga già partita è nella
                # cronologia replicata byte per byte, e rimandarla la pagherebbe due volte.
                if _in_history(line, history):
                    continue
                out.append(line)
                brought |= best_new
                used += len(line)
        return out


def _line(entry: ArchivedEntry) -> str:
    text = clip(" ".join(entry.text.split()), MAX_LINE_CHARS)
    when = entry.demoted or "undated"
    return f"- {when} · {entry.source} · {text}"
