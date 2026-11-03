"""Le tracce che una conversazione di progetto lascia su disco.

**Il padrone dell'elenco.** Piu' percorsi nascono dal nome di un progetto, e
sottosistemi diversi li scrivono senza sapere l'uno dell'altro: la sessione
(quel che Jenny rilegge) col diario del turno in corso, la trascrizione della
WebUI (quel che vedi, piu' i suoi segmenti), i record dei subagent. Un'altra
traccia — ``.jenny/tool-results/`` — vive *dentro* la cartella della wiki,
quindi segue la cartella e non e' qui.

La sessione (col diario del turno) e i record dei subagent non sono più file:
sono righe di ``state.sqlite`` sotto la chiave della conversazione, e qui si
spostano e si tolgono con una query. Restano file i verbali della WebUI.

Enumerarli in un posto solo e' l'unica difesa che c'e': un'operazione che ne
dimentichi uno lascia una traccia sotto un nome che qualcun altro puo' prendere,
ed e' esattamente il difetto riprodotto sul telefono il 24/08/2026 — cartella
cancellata dal file manager, conversazione rimasta, progetto nuovo con lo stesso
nome che se la riprende. Quando nascera' una traccia nuova, **questo e' il posto
in cui aggiungerla**, e ``tests/session/test_project_session_files.py`` e' quello
che se ne accorge.

Il modulo e' nato dentro :mod:`jenny.session.project_rename`, che per un po' e'
stato l'unico a chiederne l'elenco. Da quando anche la cancellazione lo chiede,
l'elenco non appartiene piu' a chi insegue un rinomino: ne e' un *utente*, come
lo e' la cancellazione. Qui non si sa niente di rinomini ne' di wiki — solo quali
file portano il nome di una conversazione, quanto pesano e come si tolgono.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from jenny.session.keys import webui_transcript_key
from jenny.session.manager import SessionManager

# Chiave nei metadati di sessione in cui vive l'id della wiki di appartenenza.
# Scritta una volta, al primo turno in cui la cartella c'e' — v.
# ``AgentLoop._remember_project_id``.
PROJECT_WIKI_ID_KEY = "project_wiki_id"


def project_trace_paths(workspace: Path, session_key: str) -> list[Path]:
    """I percorsi che portano il nome di *session_key*, esistenti o no.

    Sono le tracce che un rinomino deve portarsi dietro. L'elenco sta qui e non
    sparso fra i sottosistemi che le scrivono: quando ne nascera' una nuova,
    questo e' il posto in cui aggiungerla — e
    ``tests/session/test_project_session_files.py`` e' quello che se ne accorge.
    """
    # La sessione, il diario del turno e i record degli aiutanti non sono più
    # file col nome della chat: stanno in ``state.sqlite``, e il rinomino e la
    # cancellazione li seguono con una query (``session.store``,
    # ``agent.subagent_records``).
    from jenny.config.paths import get_webui_dir

    webui_stem = SessionManager.safe_key(webui_transcript_key(session_key))
    webui = get_webui_dir()
    return [
        webui / f"{webui_stem}.jsonl",
        webui / f"{webui_stem}.segments",
        webui / f"{webui_stem}.json",  # thread legacy, se questa installazione ne ha uno
    ]


def recorded_wiki_id(workspace: Path, session_key: str) -> str | None:
    """L'id della wiki che *session_key* si e' annotato, o ``None``.

    Si legge il file di sessione **direttamente**, senza ``SessionManager``: chi
    fa questa domanda sta decidendo cosa fare di una conversazione che
    probabilmente sta per sparire, e mettere in cache una sessione in quel
    momento e' il modo di farla riscrivere subito dopo.
    """
    from jenny.session import store

    try:
        found = store.head(store.session_database(workspace), session_key)
    except Exception:  # noqa: BLE001 — una lettura mancata vale «nessun id»
        return None
    if found is None:
        return None
    recorded = found["metadata"].get(PROJECT_WIKI_ID_KEY)
    return str(recorded) if recorded else None


@dataclass(frozen=True)
class TraceReport:
    """Cosa una cancellazione sta per portare via, in una forma mostrabile.

    ``messages`` e' ``None`` quando non si e' potuto contare (nessuna sessione
    salvata, o il database non si legge). E' un caso da dire («la sua
    conversazione») e non da indovinare: un numero sbagliato in una conferma
    distruttiva e' peggio di nessun numero.
    """

    files: int
    bytes: int
    messages: int | None

    @property
    def exists(self) -> bool:
        return self.files > 0


def describe_project_traces(workspace: Path, session_key: str) -> TraceReport:
    """Quante tracce ci sono sotto *session_key*, quanto pesano, quanti messaggi.

    Solo letture, nessun effetto: la conferma la si costruisce **prima** di
    chiedere, e chiedere non deve poter cambiare niente.
    """
    from jenny.session import store

    files = 0
    total = 0
    for path in project_trace_paths(workspace, session_key):
        for item in _walk(path):
            files += 1
            try:
                total += item.stat().st_size
            except OSError:
                pass
    messages: int | None = None
    try:
        found = store.head(store.session_database(workspace), session_key)
    except Exception:  # noqa: BLE001 — un conto mancato si dice, non si indovina
        found = None
    if found is not None:
        # La sessione conta come una traccia: e' quella che una cancellazione
        # porta via per prima.
        files += 1
        total += found["bytes"]
        messages = int(found["count"])
    return TraceReport(files=files, bytes=total, messages=messages)


def _walk(path: Path) -> Iterator[Path]:
    """*path* se e' un file, i file dentro se e' una cartella, niente se non c'e'."""
    if path.is_dir():
        yield from (p for p in path.rglob("*") if p.is_file())
    elif path.exists():
        yield path


def delete_project_traces(workspace: Path, session_key: str) -> list[str]:
    """Rimuove le tracce di *session_key*. Ritorna i nomi di quelle rimosse.

    Va a fondo su ognuna e **non si ferma alla prima che resiste**: una traccia
    rimasta e' quella che poi riappare sotto un nome riusato, quindi provarle
    tutte lascia lo stato migliore che si possa lasciare. Quel che non e' andato
    via finisce nel log e *non* nella lista di ritorno, che percio' dice sempre
    la verita' sul disco.
    """
    removed: list[str] = []
    # Import locale: ``agent/subagent_records`` tira dentro ``agent/loop``, che
    # importa questo modulo (``tests/session/test_cold_imports.py``).
    from jenny.agent.subagent_records import delete_session_records
    from jenny.session import store

    try:
        if store.delete(store.session_database(workspace), session_key):
            removed.append("session")
    except Exception:  # noqa: BLE001 — come una traccia che resiste: si dice e si va avanti
        logger.opt(exception=True).error("Sessione non rimossa per {}", session_key)
    try:
        if delete_session_records(workspace, session_key):
            removed.append("subagent records")
    except Exception:  # noqa: BLE001 — come una traccia che resiste: si dice e si va avanti
        logger.opt(exception=True).error("Record degli aiutanti non rimossi per {}", session_key)
    for path in project_trace_paths(workspace, session_key):
        if not path.exists():
            continue
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        except OSError:
            logger.opt(exception=True).error(
                "Traccia non rimossa per {}: {} resta", session_key, path.name
            )
            continue
        removed.append(path.name)
    if removed:
        logger.info("Tracce rimosse per {}: {}", session_key, ", ".join(removed))
    return removed
