"""Le tracce che una conversazione di progetto lascia su disco.

**Il padrone dell'elenco.** Quattro percorsi nascono dal nome di un progetto, e
tre sottosistemi diversi li scrivono senza sapere l'uno dell'altro: la sessione
(quel che Jenny rilegge), la trascrizione della WebUI (quel che vedi, piu' i suoi
segmenti), i record dei subagent. Il quinto — ``.jenny/tool-results/`` — vive
*dentro* la cartella della wiki, quindi segue la cartella e non e' qui.

Enumerarli in un posto solo e' l'unica difesa che c'e': un'operazione che ne
dimentichi uno lascia una traccia sotto un nome che qualcun altro puo' prendere,
ed e' esattamente il difetto riprodotto sul telefono il 24/08/2026 — cartella
cancellata dal file manager, conversazione rimasta, progetto nuovo con lo stesso
nome che se la riprende. Quando nascera' una quinta traccia, **questo e' il posto
in cui aggiungerla**, e ``tests/session/test_project_session_files.py`` e' quello
che se ne accorge.

Il modulo e' nato dentro :mod:`jenny.session.project_rename`, che per un po' e'
stato l'unico a chiederne l'elenco. Da quando anche la cancellazione lo chiede,
l'elenco non appartiene piu' a chi insegue un rinomino: ne e' un *utente*, come
lo e' la cancellazione. Qui non si sa niente di rinomini ne' di wiki — solo quali
file portano il nome di una conversazione, quanto pesano e come si tolgono.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from jenny.session.keys import webui_transcript_key
from jenny.session.manager import SessionManager

# Tetto sul file di sessione che si accetta di contare per una conferma. Oltre,
# il numero non si mostra: v. :func:`describe_project_traces`.
_COUNT_MAX_BYTES = 8_000_000


# Chiave nei metadati di sessione in cui vive l'id della wiki di appartenenza.
# Scritta una volta, al primo turno in cui la cartella c'e' — v.
# ``AgentLoop._remember_project_id``.
PROJECT_WIKI_ID_KEY = "project_wiki_id"


def project_trace_paths(workspace: Path, session_key: str) -> list[Path]:
    """I percorsi che portano il nome di *session_key*, esistenti o no.

    Sono le tracce che un rinomino deve portarsi dietro. L'elenco sta qui e non
    sparso fra i tre sottosistemi che le scrivono: quando ne nascera' una quarta,
    questo e' il posto in cui aggiungerla — e
    ``tests/session/test_project_session_files.py`` e' quello che se ne accorge.
    """
    # Import locali, e non e' igiene: ``agent/subagent_records`` tira dentro
    # ``agent/loop``, che importa questo modulo — un ciclo, e un modulo di
    # ``session/`` che importa ``agent/`` a livello di modulo e' anche
    # un'inversione di layer. Preso da ``tests/session/test_cold_imports.py``,
    # che prova ogni modulo come **primo** import di un interprete.
    from jenny.agent.subagent_records import _RECORDS_DIRNAME, SUBAGENTS_DIRNAME
    from jenny.config.paths import get_webui_dir

    stem = SessionManager.safe_key(session_key)
    webui_stem = SessionManager.safe_key(webui_transcript_key(session_key))
    webui = get_webui_dir()
    return [
        workspace / "sessions" / f"{stem}.jsonl",
        webui / f"{webui_stem}.jsonl",
        webui / f"{webui_stem}.segments",
        webui / f"{webui_stem}.json",  # thread legacy, se questa installazione ne ha uno
        workspace / SUBAGENTS_DIRNAME / _RECORDS_DIRNAME / f"{stem}.jsonl",
    ]


def recorded_wiki_id(workspace: Path, session_key: str) -> str | None:
    """L'id della wiki che *session_key* si e' annotato, o ``None``.

    Si legge il file di sessione **direttamente**, senza ``SessionManager``: chi
    fa questa domanda sta decidendo cosa fare di una conversazione che
    probabilmente sta per sparire, e mettere in cache una sessione in quel
    momento e' il modo di farla riscrivere subito dopo.
    """
    path = project_trace_paths(workspace, session_key)[0]
    try:
        with path.open("r", encoding="utf-8") as handle:
            first = handle.readline()
    except OSError:
        return None
    try:
        record = json.loads(first)
    except ValueError:
        return None
    if not isinstance(record, dict):
        return None
    metadata = record.get("metadata")
    if not isinstance(metadata, dict):
        return None
    found = metadata.get(PROJECT_WIKI_ID_KEY)
    return str(found) if found else None


@dataclass(frozen=True)
class TraceReport:
    """Cosa una cancellazione sta per portare via, in una forma mostrabile.

    ``messages`` e' ``None`` quando non si e' potuto contare — file illeggibile,
    o piu' grande di :data:`_COUNT_MAX_BYTES`. E' un caso da dire («la sua
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
    paths = project_trace_paths(workspace, session_key)
    files = 0
    total = 0
    for path in paths:
        for item in _walk(path):
            files += 1
            try:
                total += item.stat().st_size
            except OSError:
                pass
    return TraceReport(files=files, bytes=total, messages=_count_messages(paths[0]))


def _walk(path: Path) -> Iterator[Path]:
    """*path* se e' un file, i file dentro se e' una cartella, niente se non c'e'."""
    if path.is_dir():
        yield from (p for p in path.rglob("*") if p.is_file())
    elif path.exists():
        yield path


def _count_messages(session_file: Path) -> int | None:
    """I messaggi nel file di sessione, o ``None`` se non si e' potuto contare.

    Conta le righe che hanno un ``role``, che e' quel che distingue un messaggio
    dal record di metadati in testa. Non usa ``SessionManager`` di proposito:
    questo conto serve a una conferma, e una conferma non deve poter mettere in
    cache la sessione che sta per sparire.
    """
    try:
        if session_file.stat().st_size > _COUNT_MAX_BYTES:
            return None
    except OSError:
        return None
    count = 0
    try:
        with session_file.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    # Una riga storta non invalida il conto delle altre: il file
                    # e' append-only e l'ultima puo' essere tronca.
                    continue
                if isinstance(record, dict) and record.get("role"):
                    count += 1
    except OSError:
        return None
    return count


def delete_project_traces(workspace: Path, session_key: str) -> list[str]:
    """Rimuove le tracce di *session_key*. Ritorna i nomi di quelle rimosse.

    Va a fondo su ognuna e **non si ferma alla prima che resiste**: una traccia
    rimasta e' quella che poi riappare sotto un nome riusato, quindi provarle
    tutte lascia lo stato migliore che si possa lasciare. Quel che non e' andato
    via finisce nel log e *non* nella lista di ritorno, che percio' dice sempre
    la verita' sul disco.
    """
    removed: list[str] = []
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
