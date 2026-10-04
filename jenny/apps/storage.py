"""Storage action executor: typed mutations on JSONL collections in data/.

One collection = one ``<app>/data/<collection>.jsonl`` file. Records carry an
auto-assigned ``id`` (12 hex chars) and ``ts`` (ISO-8601). Appends go through
a per-file asyncio.Lock; rewrites (set/update/delete) are atomic.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from jenny.apps.manifest import COLLECTION_RE, AppAction
from jenny.security.workspace_access import (
    READONLY_TOOL_REFUSAL,
    current_turn_is_readonly,
    current_workspace_scope,
)
from jenny.session.keys import is_project_session_key
from jenny.utils.path import atomic_write
from jenny.utils.wiki_paths import is_wiki_root

DEFAULT_QUERY_LIMIT = 200
DEFAULT_MAX_COLLECTION_BYTES = 5_000_000

_LOCKS: dict[str, asyncio.Lock] = {}


class StorageError(Exception):
    """Structured storage failure; ``status`` maps to an HTTP-ish code."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _lock_for(path: Path) -> asyncio.Lock:
    key = str(path)
    lock = _LOCKS.get(key)
    if lock is None:
        lock = _LOCKS[key] = asyncio.Lock()
    return lock


def reset_storage_locks() -> None:
    """Dimentica i lock per-collezione prima di un nuovo event loop.

    Simmetrico a ``config.store.reset_config_store_state``, e per la stessa
    ragione: una ``asyncio.Lock`` si lega al loop la prima volta che qualcuno ci
    si accoda, e il gateway riparte apposta nello stesso processo (retry di
    ``run_gateway``). Da quel momento ogni append conteso su quella collezione
    solleva ``RuntimeError: ... is bound to a different event loop``. Va
    chiamata da ``android_entry.run_gateway`` accanto agli altri ``reset_*``.
    """
    _LOCKS.clear()


def _collection_path(app_dir: Path, collection: str) -> Path:
    if not COLLECTION_RE.match(collection):
        raise StorageError(f"invalid collection name '{collection}'")
    return app_dir / "data" / f"{collection}.jsonl"


def _read_records(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    records: list[dict] = []
    skipped = 0
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue
            if isinstance(record, dict):
                records.append(record)
            else:
                skipped += 1
    if skipped:
        logger.warning("Skipped {} corrupt line(s) in {}", skipped, path)
    return records


def read_collection(app_dir: Path, collection: str) -> list[dict]:
    """I record di una collezione, nell'ordine in cui sono stati scritti.

    Lettura pubblica e senza lock, per chi guarda i dati di un'app senza
    passare da un'azione: la sezione compatta della bacheca
    (``jenny/board/sections.py``). Le scritture sono append di righe intere, e
    una riga a metà si salta come una corrotta.
    """
    return _read_records(_collection_path(app_dir, collection))


def _check_size(path: Path, max_bytes: int) -> None:
    if path.is_file() and path.stat().st_size >= max_bytes:
        raise StorageError(
            f"collection '{path.stem}' exceeds the {max_bytes} byte limit", status=413
        )


def _ends_without_newline(path: Path) -> bool:
    """Il file esiste, non e' vuoto e l'ultimo byte non e' un a capo."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            if f.tell() == 0:
                return False
            f.seek(-1, 2)
            return f.read(1) != b"\n"
    except FileNotFoundError:
        return False


def _new_record(params: dict) -> dict:
    record = {k: v for k, v in params.items() if k != "id"}
    return {
        "id": secrets.token_hex(6),
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **record,
    }


# Le op che cambiano il contenuto di una collezione. ``query`` non c'e' apposta:
# una mini-app deve poter ancora *mostrare* i suoi dati.
_MUTATING_OPS = frozenset({"append", "set", "update", "delete"})

# Il rifiuto dentro un progetto. Dice **dove** si fa, come quello dei promemoria
# del passo 3: e' l'unico posto in cui Jenny lo viene a sapere, e da cui lo
# ridice all'utente.
_PROJECT_REFUSAL = (
    "Not from here: the mini-apps and their data are personal, and this is a project "
    "conversation, which writes only inside its own folder. Reading them works — this is "
    "about changing them. Tell the user to switch to the personal chat (the chip above the "
    "composer) if they want it saved there; do not look for another way to write it."
)


def _in_a_project(app_dir: Path) -> bool:
    """Se il turno in corso e' la conversazione di un progetto.

    **Due sorgenti in OR, perche' nessuna delle due copre tutti i chiamanti.**
    Prima era solo la seconda, e la seconda da sola risponde a una domanda di
    filesystem al posto di una domanda sul turno (T4.9, 23/08):

    1. **La chiave del turno.** E' quel che *definisce* una sessione-progetto —
       ``WorkspaceScopeResolver.for_project`` deduce la cartella da lei — quindi
       e' la risposta autorevole quando c'e'. Copre il caso che la sola forma
       della cartella sbaglia: un progetto la cui **cartella manca**.
       ``for_project`` tiene di proposito il percorso che non esiste (le
       scritture falliscono, ma il lavoro non finisce nel workspace personale),
       e una cartella che non esiste non contiene ``wiki/``: il gate si apriva, e
       i dati personali delle mini-app tornavano scrivibili da dentro un
       progetto.
    2. **La forma della cartella dello scope.** Resta perche' la chiave non c'e'
       sempre: il subagent non ne ha una di progetto (la sua e'
       ``subagent:<lineage>``) ma **eredita lo scope**, e le route HTTP delle
       mini-app non hanno nessun turno. E' l'unico segnale che hanno.

    In OR e non in "una vince sull'altra": due segnali parziali su una chiusura
    si compongono chiudendo. Il caso che *apre* e' il terzo ramo — l'app vive
    **dentro** la radice del turno — che non e' un'eccezione ma la domanda del
    confine di scrittura: se ``app_dir`` sta dentro quel che questo turno puo'
    cambiare, non c'e' niente da rifiutare. E' anche quel che evita il rifiuto
    assurdo del caso opposto: un workspace personale che un giorno si trovasse un
    ``wiki/`` in radice diventerebbe "un progetto" per la sola forma, e
    rifiuterebbe *ogni* scrittura personale sulle mini-app.
    """
    # Import dentro la funzione: ``jenny.apps`` sotto ``jenny.agent`` e' una
    # dipendenza che esiste gia' (``executor.py`` importa ``agent.tools.base``),
    # ma questo modulo lo caricano anche le route HTTP delle mini-app, che di
    # ``agent`` non hanno bisogno — e il gateway lo importa all'avvio.
    from jenny.agent.tools.context import current_request_session_key

    if is_project_session_key(current_request_session_key() or ""):
        return True
    scope = current_workspace_scope()
    if scope is None:
        return False
    # ``write_root()`` e non ``project_path``: dal passo T4.4 la radice scrivibile
    # del turno la dice un solo metodo. Risolto da entrambi i lati perche' su
    # Android la dir dati e' raggiungibile con due nomi e ``parents`` confronta
    # per componenti, non per inode (v. il difetto del 23/08 nel giardiniere).
    root = scope.write_root()
    if root in app_dir.resolve(strict=False).parents:
        return False
    return is_wiki_root(root)


def _dump(record: dict) -> str:
    return json.dumps(record, ensure_ascii=False)


def _rewrite(path: Path, records: list[dict]) -> None:
    content = "".join(_dump(r) + "\n" for r in records)
    atomic_write(path, content)


def _require_id(params: dict) -> str:
    record_id = params.get("id")
    if not isinstance(record_id, str) or not record_id:
        raise StorageError("'id' parameter is required for this operation")
    return record_id


async def execute_storage_action(
    app_dir: Path,
    action: AppAction,
    params: dict,
    *,
    max_bytes: int = DEFAULT_MAX_COLLECTION_BYTES,
) -> dict:
    """Execute one storage op; returns a JSON-safe result dict."""
    assert action.collection is not None and action.op is not None
    # Due chiusure, e sono regole diverse. ``query`` passa in entrambe: una
    # mini-app deve poter *mostrare* i suoi dati sempre.
    #
    # 1. Sola lettura (passo 4): questo turno non cambia niente sul telefono.
    # 2. Progetto (passo 6): **le app sono personali** — deciso il 22/08.
    #    ``app_dir`` viene dalla radice dell'installazione e non da quella del
    #    turno, quindi il confine di scrittura non la vedeva passare: il 22/08
    #    la Todo personale e' stata aggiornata da dentro un progetto, mentre il
    #    prompt di quel progetto diceva «scrivi solo dentro questa cartella».
    #    L'alternativa era dichiarare le app condivise e spendere una riga di
    #    prompt a ogni turno per l'eccezione; una Todo per progetto darebbe
    #    sette liste vuote invece di quella che usi.
    if action.op in _MUTATING_OPS:
        if current_turn_is_readonly():
            raise StorageError(READONLY_TOOL_REFUSAL)
        if _in_a_project(app_dir):
            raise StorageError(_PROJECT_REFUSAL)
    path = _collection_path(app_dir, action.collection)

    async with _lock_for(path):
        if action.op == "append":
            _check_size(path, max_bytes)
            record = _new_record(params)
            path.parent.mkdir(parents=True, exist_ok=True)
            # Un append interrotto (processo ucciso) lascia l'ultima riga senza a
            # capo, e il record nuovo le si attaccava: la riga unita non si legge
            # piu', e spariva anche lui. L'a capo la chiude; resta una riga rotta
            # che la lettura salta e segnala, come prima.
            lead = "\n" if _ends_without_newline(path) else ""
            with open(path, "a", encoding="utf-8") as f:
                f.write(lead + _dump(record) + "\n")
                f.flush()
            return {"ok": True, "record": record}

        if action.op == "query":
            filters = {k: v for k, v in params.items() if k != "limit"}
            limit = params.get("limit")
            if not isinstance(limit, int) or limit <= 0:
                limit = DEFAULT_QUERY_LIMIT
            records = _read_records(path)
            matched = [
                r for r in records
                if all(r.get(k) == v for k, v in filters.items())
            ]
            return {"ok": True, "records": matched[:limit], "count": len(matched)}

        if action.op == "set":
            _check_size(path, max_bytes)
            record_id = _require_id(params)
            records = _read_records(path)
            replacement = {**{k: v for k, v in params.items() if k != "id"}, "id": record_id}
            for i, record in enumerate(records):
                if record.get("id") == record_id:
                    replacement.setdefault("ts", record.get("ts"))
                    records[i] = replacement
                    break
            else:
                replacement.setdefault(
                    "ts", datetime.now(timezone.utc).isoformat(timespec="seconds")
                )
                records.append(replacement)
            _rewrite(path, records)
            return {"ok": True, "record": replacement}

        if action.op == "update":
            # Come ``set``: senza, aggiornare lo stesso record con un campo
            # sempre piu' grande faceva crescere la collezione senza tetto.
            _check_size(path, max_bytes)
            record_id = _require_id(params)
            records = _read_records(path)
            for i, record in enumerate(records):
                if record.get("id") == record_id:
                    updated = {**record, **{k: v for k, v in params.items() if k != "id"}}
                    records[i] = updated
                    _rewrite(path, records)
                    return {"ok": True, "record": updated}
            raise StorageError(f"record '{record_id}' not found", status=404)

        if action.op == "delete":
            record_id = _require_id(params)
            records = _read_records(path)
            remaining = [r for r in records if r.get("id") != record_id]
            if len(remaining) == len(records):
                raise StorageError(f"record '{record_id}' not found", status=404)
            _rewrite(path, remaining)
            return {"ok": True, "deleted": record_id}

    raise StorageError(f"unknown storage op '{action.op}'")
