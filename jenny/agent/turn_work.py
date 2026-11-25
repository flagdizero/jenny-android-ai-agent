"""Cosa ha toccato un turno ordinario di Jenny, per «Ho fatto» e «Torna a prima».

Un turno che lavora per un piano scrive nel registro del piano, uno che lavora
per un'altra Jenny in quello della richiesta (``agent.loop``). Tutti gli altri
turni con un id del filo — quello della WebUI, ``webui_turn_id``, che è anche
il ``turn_id`` del verbale: la casa non deve tradurre niente — legano il
registro ``turn-<id>`` (``agent.write_journal``), e qui si tiene il resto:

- **la riga del turno**, in ``undo.sqlite`` accanto ai registri e ai piani
  (sparisce con loro a un ripristino, ``jenny/db/restore``). Si scrive solo
  quando c'è qualcosa da annotare: un turno fermo non lascia niente. Porta i
  registri degli aiutanti lanciati dal turno (``SubagentSpec.origin_turn``), i
  promemoria creati (``CronTool``, con l'etichetta: un promemoria singolo che
  suona sparisce) e quel che non si annulla (``Tool.outside_undo``, annotato
  dal runner);
- **l'elenco** di cosa ha toccato, scritto dal codice nella forma di quello
  delle richieste fra Jenny (``jenny.peers.acting``): ``{"kind", "label",
  "undoable"}``;
- **la scadenza**: dopo :data:`RETENTION_S` la riga tiene l'elenco congelato
  (niente più si annulla) e i registri del turno se ne vanno; oltre
  :data:`MAX_ROWS` righe le più vecchie spariscono. La spazzata gira al boot,
  accanto a quella dei registri degli aiutanti, e i registri degli aiutanti
  che una riga viva nomina restano (:func:`kept_journal_keys`).

**I turni senza id.** Una risposta scritta in una notifica, nel fumetto della
mascotte o sull'orologio arriva senza ``webui_turn_id`` (``runtime/native_input``),
e il suo finale entra nel verbale senza un turno a cui la casa possa
agganciarsi: quei turni non legano un registro e non lasciano righe. Telegram,
i turni di sistema della WebUI e quelli dei promemoria l'id ce l'hanno.

Funzioni sincrone e brevi (una scrittura SQLite): il loop le chiama dal suo
filo, come fanno i recinti delle scritture.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.bus.events import WEBUI_TURN_METADATA_KEY
from jenny.db.schema import Schema

#: La tabella dei turni in ``undo.sqlite``. ``journals`` sono i registri degli
#: aiutanti lanciati dal turno (il suo, ``turn-<id>``, si ricava dall'id);
#: ``jobs`` i promemoria creati, ``[{"id", "label"}]``; ``notes`` le voci che
#: non si annullano; ``frozen`` l'elenco congelato alla scadenza.
SCHEMA = Schema("turn_work", (
    """
    CREATE TABLE turn_work (
      turn TEXT PRIMARY KEY,
      session TEXT NOT NULL DEFAULT '',
      chat TEXT NOT NULL DEFAULT '',
      started_at REAL NOT NULL,
      journals TEXT NOT NULL DEFAULT '[]',
      jobs TEXT NOT NULL DEFAULT '[]',
      notes TEXT NOT NULL DEFAULT '[]',
      state TEXT NOT NULL DEFAULT 'open',
      undone_at REAL,
      frozen TEXT
    );
    CREATE INDEX turn_work_started ON turn_work(started_at);
    """,
))

TURN = "turn"
UNDO_TURN = "undo-turn"

#: Quanto a lungo si può tornare a prima di un turno.
RETENTION_S = 14 * 24 * 3600
#: Quante righe al massimo: oltre, le più vecchie se ne vanno, elenco compreso.
MAX_ROWS = 2000
#: Quante voci al massimo in un elenco, e quante note e promemoria in una riga.
MAX_ITEMS = 40

STATE_OPEN = "open"
STATE_UNDONE = "undone"
STATE_EXPIRED = "expired"

# Lo stesso alfabeto degli id del verbale (``webui.transcript_recorder``): un id
# che non ci sta non è un turno che la casa conosce, e non diventa una chiave.
_TURN_ID_RE = re.compile(r"\A[A-Za-z0-9._:-]{1,128}\Z")


def turn_id_of(metadata: Mapping[str, Any] | None) -> str | None:
    """L'id del filo del turno, se i metadati ne portano uno valido."""
    if not isinstance(metadata, Mapping):
        return None
    value = metadata.get(WEBUI_TURN_METADATA_KEY)
    if isinstance(value, str) and _TURN_ID_RE.match(value.strip()):
        return value.strip()
    return None


def turn_journal_key(turn: str) -> str:
    from jenny.agent.write_journal import journal_key

    return journal_key(TURN, turn)


def undo_journal_key(turn: str) -> str:
    """Il registro del «Torna a prima» di *turn*: annullarlo è annullare il ritorno."""
    from jenny.agent.write_journal import journal_key

    return journal_key(UNDO_TURN, turn)


# -- il turno in corso ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TurnRef:
    """Il turno per cui si lavora: dove sta la sua riga, e cosa scriverci se nasce."""

    root: Path
    turn: str
    session: str = ""
    chat: str = ""
    started_at: float = 0.0


_CURRENT: ContextVar[TurnRef | None] = ContextVar("jenny_turn_work", default=None)


def current() -> TurnRef | None:
    return _CURRENT.get()


def current_turn() -> str | None:
    """L'id del turno per cui si lavora adesso (anche dentro un suo aiutante)."""
    ref = _CURRENT.get()
    return ref.turn if ref is not None else None


def bind(ref: TurnRef | None) -> Token:
    """Lega *ref* fino a :func:`reset`: chi lavora qui dentro annota nella sua riga."""
    return _CURRENT.set(ref)


def reset(token: Token) -> None:
    _CURRENT.reset(token)


@contextmanager
def bound(ref: TurnRef | None) -> Iterator[None]:
    token = _CURRENT.set(ref)
    try:
        yield
    finally:
        _CURRENT.reset(token)


def begin(
    root: Path, metadata: Mapping[str, Any] | None, *, session: str = "", chat: str = "",
) -> TurnRef | None:
    """Il turno ordinario che parte, se ha un id; ``None`` se non ne ha (niente registro)."""
    turn = turn_id_of(metadata)
    if turn is None:
        return None
    return TurnRef(Path(root), turn, session=session, chat=chat, started_at=time.time())


def end(ref: TurnRef, journal: Any) -> None:
    """Il turno è finito: la riga se ha scritto qualcosa, e il registro lasciato andare.

    Non solleva: la fine di un turno non fallisce per il suo registro.
    """
    from jenny.agent.write_journal import release_journal

    try:
        if journal is not None and (journal.changed_files or journal.busy):
            _ensure(ref)
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).warning("Turn {}: its record was not written", ref.turn)
    try:
        release_journal(ref.root, turn_journal_key(ref.turn))
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).debug("Turn {}: journal not released", ref.turn)


# -- la riga ------------------------------------------------------------------------


def _db(root: Path):
    from jenny import db

    return db.store_database(root, db.UNDO, SCHEMA)


def _ensure_row(conn: sqlite3.Connection, ref: TurnRef) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO turn_work(turn, session, chat, started_at) VALUES (?, ?, ?, ?)",
        (ref.turn, ref.session, ref.chat, ref.started_at or time.time()))


def _ensure(ref: TurnRef) -> None:
    with _db(ref.root).write() as conn:
        _ensure_row(conn, ref)


def _loads(raw: Any) -> list[Any]:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def _append(ref: TurnRef, column: str, item: Any, *, same: Any = None,
            evict: Any = None) -> None:
    """Aggiunge *item* alla lista *column* della riga di *ref*, nata se serve.

    Letta e scritta nella stessa transazione: due strumenti dello stesso turno
    che annotano insieme non si cancellano a vicenda. *same* dice se una voce
    c'è già (di default, l'uguaglianza); a lista piena *evict* sceglie quale
    voce lascia il posto (un indice), o nessuna.
    """
    with _db(ref.root).write() as conn:
        _ensure_row(conn, ref)
        row = conn.execute(
            f"SELECT {column} FROM turn_work WHERE turn = ?", (ref.turn,)).fetchone()
        items = _loads(row[0] if row is not None else None)
        if any((same or (lambda a, b: a == b))(old, item) for old in items):
            return
        if len(items) >= MAX_ITEMS:
            drop = evict(items, item) if evict is not None else None
            if drop is None:
                return
            items.pop(drop)
        items.append(item)
        conn.execute(f"UPDATE turn_work SET {column} = ? WHERE turn = ?",
                     (json.dumps(items, ensure_ascii=False), ref.turn))


def read_row(root: Path, turn: str) -> dict[str, Any] | None:
    """La riga di *turn*, con le liste già lette; ``None`` se il turno non ha annotato niente."""
    try:
        rows = _db(root).read("SELECT * FROM turn_work WHERE turn = ?", (turn,))
    except sqlite3.Error:
        return None
    if not rows:
        return None
    row = rows[0]
    frozen = row["frozen"]
    return {
        "turn": row["turn"], "session": row["session"], "chat": row["chat"],
        "started_at": float(row["started_at"]),
        "journals": [k for k in _loads(row["journals"]) if isinstance(k, str)],
        "jobs": [j for j in _loads(row["jobs"]) if isinstance(j, dict)],
        "notes": [n for n in _loads(row["notes"]) if isinstance(n, dict)],
        "state": row["state"], "undone_at": row["undone_at"],
        "frozen": _loads(frozen) if frozen is not None else None,
    }


# -- chi annota ---------------------------------------------------------------------


def ref_for(root: Path, turn: str, *, session: str = "", chat: str = "") -> TurnRef:
    """Un riferimento a *turn* fuori dal suo turno (un aiutante ripreso, un test)."""
    return TurnRef(Path(root), turn, session=session, chat=chat, started_at=time.time())


def helper_started(
    root: Path, turn: str, journal_key: str, lineage: str, *, session: str = "", chat: str = "",
) -> None:
    """Un aiutante lanciato dal turno *turn* parte (o riparte): il suo registro entra nella riga."""
    if turn_id_of({WEBUI_TURN_METADATA_KEY: turn}) is None:
        return
    try:
        _append(ref_for(root, turn, session=session, chat=chat), "journals", journal_key)
    except Exception:  # noqa: BLE001 — l'aiutante lavora comunque
        logger.opt(exception=True).warning("Turn {}: helper {} not noted", turn, lineage)


def note_job(job_id: str, label: str) -> None:
    """Un promemoria creato nel turno in corso: id ed etichetta, perché un singolo
    che suona sparisce, e l'elenco deve poterlo nominare lo stesso."""
    ref = _CURRENT.get()
    if ref is None or not job_id:
        return
    try:
        _append(ref, "jobs", {"id": str(job_id), "label": str(label or job_id)[:120]},
                same=lambda a, b: isinstance(a, dict) and a.get("id") == b["id"])
    except Exception:  # noqa: BLE001 — il promemoria è creato comunque
        logger.opt(exception=True).warning("Turn {}: job {} not noted", ref.turn, job_id)


def _evict_a_search(items: list[Any], item: Mapping[str, Any]) -> int | None:
    """A note piene, un messaggio o un comando prendono il posto della ricerca più vecchia.

    Un aiutante di ricerca apre decine di pagine: senza, l'elenco si riempiva
    di ricerche e il messaggio mandato dopo non ci stava più.
    """
    if item.get("what") == "web":
        return None
    return next((i for i, old in enumerate(items)
                 if isinstance(old, dict) and old.get("what") == "web"), None)


def note(item: Mapping[str, Any]) -> None:
    """Una cosa fatta nel turno in corso che il registro non può annullare."""
    ref = _CURRENT.get()
    if ref is None:
        return
    label = item.get("label")
    if not isinstance(label, str) or not label.strip():
        return
    entry = {
        "kind": "egress", "what": str(item.get("what") or "other"),
        "label": " ".join(label.split())[:120], "undoable": False,
    }
    try:
        _append(ref, "notes", entry, evict=_evict_a_search)
    except Exception:  # noqa: BLE001 — quel che è fatto è fatto
        logger.opt(exception=True).warning("Turn {}: a note was not written", ref.turn)


def note_tool(tool: Any, params: Any) -> None:
    """Dopo una chiamata riuscita: quel che lo strumento dichiara fuori dal registro
    (``Tool.outside_undo``) entra nella riga del turno in corso. Lo chiama il runner."""
    if _CURRENT.get() is None or tool is None:
        return
    declare = getattr(tool, "outside_undo", None)
    if not callable(declare):
        return
    try:
        item = declare(params if isinstance(params, dict) else {})
    except Exception:  # noqa: BLE001 — una dichiarazione rotta non ferma il turno
        logger.opt(exception=True).debug("outside_undo of {} failed", getattr(tool, "name", "?"))
        return
    if isinstance(item, Mapping):
        note(item)


# -- l'elenco -------------------------------------------------------------------------


def _job_items(jobs: list[dict[str, Any]], cron: Any) -> list[dict[str, Any]]:
    items = []
    for job in jobs:
        alive = False
        if cron is not None:
            try:
                alive = cron.get_job(str(job.get("id") or "")) is not None
            except Exception:  # noqa: BLE001 — nel dubbio, non si toglie
                alive = False
        items.append({"kind": "job", "label": str(job.get("label") or job.get("id") or ""),
                      "undoable": alive})
    return items


def turn_items(root: Path, turn: str, *, cron: Any = None) -> list[dict[str, Any]]:
    """Cosa ha toccato *turn*: file (del turno e dei suoi aiutanti), promemoria, il resto.

    Un promemoria che non c'è più (un singolo che ha suonato, uno tolto) resta
    nell'elenco e non si annulla. Senza *cron* nessun promemoria risulta
    annullabile: non si sa se c'è ancora. Una riga scaduta dà il suo elenco
    congelato.
    """
    from jenny.agent.work_done import journal_file_items

    row = read_row(root, turn)
    if row is not None and row["state"] == STATE_EXPIRED:
        return [dict(i) for i in row["frozen"] or [] if isinstance(i, dict)]
    keys = [turn_journal_key(turn), *(row["journals"] if row is not None else [])]
    items = journal_file_items(root, keys)
    if row is not None:
        items.extend(_job_items(row["jobs"], cron))
        items.extend(dict(n) for n in row["notes"])
    return items[:MAX_ITEMS]


def turn_view(root: Path, turn: str, *, cron: Any = None, working: bool = False,
              ) -> dict[str, Any]:
    """L'elenco di *turn* con il suo stato: ``{"turn", "items", "working", "state"}``.

    *working* lo dice chi sa degli aiutanti (``SubagentManager.working_for_turn``):
    un aiutante del turno ancora al lavoro aggiungerà file all'elenco.
    """
    row = read_row(root, turn)
    return {
        "turn": turn,
        "items": turn_items(root, turn, cron=cron),
        "working": bool(working),
        "state": row["state"] if row is not None else STATE_OPEN,
    }


# -- la scadenza ------------------------------------------------------------------------


def kept_journal_keys(root: Path) -> set[str]:
    """I registri che le righe vive nominano: il turno, il suo ritorno, i suoi aiutanti.

    Entra nell'insieme che la spazzata dei registri degli aiutanti tiene
    (``SubagentManager.recover_journals``): un aiutante i cui record sono
    potati resta annullabile finché il turno che l'ha lanciato lo è.
    """
    try:
        rows = _db(root).read(
            "SELECT turn, journals FROM turn_work WHERE state != ?", (STATE_EXPIRED,))
    except sqlite3.Error:
        return set()
    keep: set[str] = set()
    for row in rows:
        keep.add(turn_journal_key(row["turn"]))
        keep.add(undo_journal_key(row["turn"]))
        keep.update(k for k in _loads(row["journals"]) if isinstance(k, str))
    return keep


def _turn_journal_keys(root: Path) -> list[tuple[str, float]]:
    from jenny.agent.write_journal import _undo_db

    prefixes = (f"{TURN}-", f"{UNDO_TURN}-")
    rows = _undo_db(root).read("SELECT key, created_at FROM journals")
    return [(r["key"], float(r["created_at"] or 0.0)) for r in rows
            if str(r["key"]).startswith(prefixes)]


def sweep(root: Path, *, now: float | None = None) -> int:
    """Al boot: congela le righe scadute, pota le troppe, toglie i registri dei turni orfani.

    Un registro ``turn-*`` senza riga è un turno finito senza che la riga
    nascesse (un kill a metà): resta leggibile per id fino alla scadenza, poi
    va. Ritorna quante righe ha toccato. Best-effort: un boot non fallisce per
    questo.
    """
    from jenny.agent.write_journal import drop_journals

    now = time.time() if now is None else now
    limit = now - RETENTION_S
    touched = 0
    try:
        database = _db(root)
        expired = [r["turn"] for r in database.read(
            "SELECT turn FROM turn_work WHERE state != ? AND started_at < ?",
            (STATE_EXPIRED, limit))]
        for turn in expired:
            items = turn_items(root, turn)
            frozen = [{**i, "undoable": False} for i in items]
            with database.write() as conn:
                conn.execute(
                    "UPDATE turn_work SET state = ?, frozen = ? WHERE turn = ?",
                    (STATE_EXPIRED, json.dumps(frozen, ensure_ascii=False), turn))
            drop_journals(root, [turn_journal_key(turn), undo_journal_key(turn)])
            touched += 1
        over = [r["turn"] for r in database.read(
            "SELECT turn FROM turn_work ORDER BY started_at DESC LIMIT -1 OFFSET ?",
            (MAX_ROWS,))]
        if over:
            with database.write() as conn:
                conn.executemany("DELETE FROM turn_work WHERE turn = ?", [(t,) for t in over])
            drop_journals(root, [k for t in over
                                 for k in (turn_journal_key(t), undo_journal_key(t))])
            touched += len(over)
        named = kept_journal_keys(root)
        orphans = [key for key, created in _turn_journal_keys(root)
                   if key not in named and created < limit]
        drop_journals(root, orphans)
    except Exception as e:  # noqa: BLE001 — vedi docstring
        logger.warning("Turn records sweep failed: {}", e)
    return touched


def journals_of(root: Path, turn: str) -> list[str]:
    """I registri da annullare per *turn*: il suo e quelli dei suoi aiutanti."""
    row = read_row(root, turn)
    keys = [turn_journal_key(turn), *(row["journals"] if row is not None else [])]
    return list(dict.fromkeys(keys))


__all__ = [
    "MAX_ITEMS",
    "MAX_ROWS",
    "RETENTION_S",
    "SCHEMA",
    "TurnRef",
    "begin",
    "bind",
    "bound",
    "current",
    "current_turn",
    "end",
    "helper_started",
    "journals_of",
    "kept_journal_keys",
    "note",
    "note_job",
    "note_tool",
    "read_row",
    "reset",
    "sweep",
    "turn_id_of",
    "turn_items",
    "turn_journal_key",
    "turn_view",
    "undo_journal_key",
]
