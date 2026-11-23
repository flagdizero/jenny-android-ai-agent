"""Dove sta quello che una Jenny ricorda delle altre.

Due posti, scelti per quel che deve succedere a un ripristino:

- **fuori dal workspace**, in ``<runtime_root>/peers/``: l'identità di questa
  Jenny (``self_id``) e un segreto per ogni Jenny abbinata (``<id>.key``, 0600).
  Non entrano negli snapshot né nelle copie: un telefono ripristinato da un
  altro non si ritrova lo stesso id né i suoi segreti, e l'abbinamento si rifà.
  Due telefoni con la stessa identità sarebbero il guasto;
- **in ``state.sqlite``**, tabelle di questo modulo: la coda di uscita, i
  messaggi già ricevuti (per non trattarli due volte), lo stato di ogni Jenny
  e la cronologia che l'Officina mostra.

I dati non segreti di un abbinamento (nome, indirizzo) stanno in
``config.json`` (``peers.peers``) e passano da ``store.mutate`` come ogni altra
impostazione.
"""

from __future__ import annotations

import json
import secrets
import time
from pathlib import Path
from typing import Any

from jenny.db.schema import Schema
from jenny.utils.locations import runtime_root_for
from jenny.utils.path import atomic_write

from .model import is_id

PEERS_DIR_NAME = "peers"
_SELF_ID_FILE = "self_id"
_SECRET_SUFFIX = ".key"
SECRET_BYTES = 32

# Dopo un tentativo fallito, quanto aspettare prima del prossimo. Il primo
# riprova presto (un Tailscale che si sta rialzando); poi si allarga fino a
# mezz'ora. Una tailnet che torna su sveglia la coda prima (``PeerLink.kick``).
RETRY_DELAYS_S = (30, 60, 120, 300, 600, 1800)

# I messaggi già ricevuti si ricordano una settimana: abbastanza per scartare un
# messaggio rimandato perché l'``ack`` si era perso, poco per non crescere.
INBOX_KEEP_MS = 7 * 24 * 3600 * 1000

# La cronologia di ogni Jenny: le ultime righe, non una storia completa.
LOG_KEEP_PER_PEER = 500

SCHEMA = Schema(
    "peers",
    (
        """
        CREATE TABLE peer_outbox (
          id TEXT PRIMARY KEY,
          peer TEXT NOT NULL,
          kind TEXT NOT NULL,
          body TEXT NOT NULL,
          created_ms INTEGER NOT NULL,
          attempts INTEGER NOT NULL DEFAULT 0,
          next_try_ms INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX peer_outbox_peer ON peer_outbox(peer, created_ms);
        CREATE TABLE peer_inbox (
          peer TEXT NOT NULL,
          id TEXT NOT NULL,
          received_ms INTEGER NOT NULL,
          PRIMARY KEY (peer, id)
        );
        CREATE TABLE peer_state (
          peer TEXT PRIMARY KEY,
          last_seen_ms INTEGER NOT NULL DEFAULT 0,
          last_try_ms INTEGER NOT NULL DEFAULT 0,
          last_error TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE peer_log (
          seq INTEGER PRIMARY KEY AUTOINCREMENT,
          peer TEXT NOT NULL,
          at_ms INTEGER NOT NULL,
          direction TEXT NOT NULL,
          kind TEXT NOT NULL,
          data TEXT NOT NULL
        );
        CREATE INDEX peer_log_peer ON peer_log(peer, seq);
        """,
        # Le domande fra Jenny (``jenny.peers.questions``), nei due versi:
        # ``out`` quelle che questa ha fatto, ``in`` quelle che le hanno fatto.
        # ``state``: ``queued`` (in coda), ``answering`` (si sta rispondendo),
        # ``answered``, ``refused``, ``failed``, ``expired``; per una richiesta
        # di fare (passo dopo) anche ``working`` e ``undone``.
        """
        CREATE TABLE peer_questions (
          qid TEXT NOT NULL,
          peer TEXT NOT NULL,
          direction TEXT NOT NULL,
          text TEXT NOT NULL,
          asked_ms INTEGER NOT NULL,
          state TEXT NOT NULL,
          outbox_id TEXT NOT NULL DEFAULT '',
          answered_ms INTEGER NOT NULL DEFAULT 0,
          answer TEXT NOT NULL DEFAULT '',
          PRIMARY KEY (peer, direction, qid)
        );
        CREATE INDEX peer_questions_open ON peer_questions(direction, state, asked_ms);
        """,
        # Le richieste di fare (``jenny.peers.acting``): ``mode`` è ``read`` o
        # ``act``; per una richiesta di fare ``state`` può essere anche
        # ``working`` (ci lavorano ancora degli aiutanti) e ``undone``.
        # ``done``: l'elenco JSON di cosa ha toccato, scritto dal codice;
        # ``journals``: i registri dell'annulla che la richiesta ha riempito.
        # Un passo a sé, non colonne aggiunte al precedente: un telefono che ha
        # già il passo delle domande non lo rifarebbe (visto su jenny_s22).
        """
        ALTER TABLE peer_questions ADD COLUMN mode TEXT NOT NULL DEFAULT 'read';
        ALTER TABLE peer_questions ADD COLUMN done TEXT NOT NULL DEFAULT '[]';
        ALTER TABLE peer_questions ADD COLUMN journals TEXT NOT NULL DEFAULT '[]';
        """,
        # Le linee passate (``jenny.peers.line``), nei due versi. ``out``: una
        # chat di questa Jenny passata a un'altra, ``key`` = canale e chat
        # (``websocket\ndefault``); ``in``: la linea che un'altra ha aperto
        # qui, ``key`` = il suo id. ``place`` è la chat di là: vuoto per quella
        # di sempre, il nome del quaderno altrimenti. Sta su disco perché il
        # gateway su Android riparte spesso: una linea dimenticata vorrebbe
        # dire che il messaggio dopo va alla Jenny sbagliata.
        """
        CREATE TABLE peer_lines (
          direction TEXT NOT NULL,
          key TEXT NOT NULL,
          peer TEXT NOT NULL,
          lid TEXT NOT NULL,
          place TEXT NOT NULL DEFAULT '',
          state TEXT NOT NULL DEFAULT 'open',
          since_ms INTEGER NOT NULL,
          last_ms INTEGER NOT NULL,
          PRIMARY KEY (direction, key)
        );
        """,
    ),
    shards={"peer_log": "at_ms / 86400000"},
)


def now_ms() -> int:
    return int(time.time() * 1000)


def _json_list(raw: Any) -> list[Any]:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def _question_row(row: Any) -> dict[str, Any]:
    out = dict(row)
    out["done"] = [d for d in _json_list(out.get("done")) if isinstance(d, dict)]
    out["journals"] = [k for k in _json_list(out.get("journals")) if isinstance(k, str)]
    return out


def new_id() -> str:
    return secrets.token_hex(16)


# -- Fuori dal workspace ----------------------------------------------------


def peers_dir(workspace: Path) -> Path:
    return runtime_root_for(workspace) / PEERS_DIR_NAME


def self_id(workspace: Path) -> str:
    """L'identità di questa Jenny, creata la prima volta che serve."""
    path = peers_dir(workspace) / _SELF_ID_FILE
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        value = ""
    if is_id(value):
        return value
    value = new_id()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, value + "\n", chmod=0o600)
    return value


def _secret_path(workspace: Path, peer_id: str) -> Path:
    if not is_id(peer_id):
        raise ValueError("invalid peer id")
    return peers_dir(workspace) / f"{peer_id}{_SECRET_SUFFIX}"


def read_secret(workspace: Path, peer_id: str) -> bytes | None:
    try:
        data = _secret_path(workspace, peer_id).read_bytes()
    except (OSError, ValueError):
        return None
    return data if len(data) == SECRET_BYTES else None


def write_secret(workspace: Path, peer_id: str, secret: bytes) -> None:
    if len(secret) != SECRET_BYTES:
        raise ValueError("a peer secret is 32 bytes")
    path = _secret_path(workspace, peer_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, secret, chmod=0o600)


def delete_secret(workspace: Path, peer_id: str) -> None:
    try:
        _secret_path(workspace, peer_id).unlink(missing_ok=True)
    except (OSError, ValueError):
        pass


# -- In state.sqlite --------------------------------------------------------


class PeerStore:
    """Le tabelle della coda e della cronologia, su ``state.sqlite``."""

    def __init__(self, workspace: Path) -> None:
        self._workspace = Path(workspace)

    def _db(self):
        from jenny import db

        return db.store_database(self._workspace, db.STATE, SCHEMA)

    # -- Coda di uscita --

    def enqueue(self, peer: str, kind: str, body: dict[str, Any], *, at_ms: int | None = None) -> str:
        msg_id = new_id()
        when = now_ms() if at_ms is None else at_ms
        with self._db().write() as conn:
            conn.execute(
                "INSERT INTO peer_outbox(id, peer, kind, body, created_ms, attempts, next_try_ms) "
                "VALUES (?, ?, ?, ?, ?, 0, 0)",
                (msg_id, peer, kind, json.dumps(body, ensure_ascii=False), when),
            )
        return msg_id

    def pending(self, peer: str) -> list[dict[str, Any]]:
        """I messaggi in coda per *peer*, nell'ordine in cui sono entrati.

        Per ``rowid`` e non per ``created_ms, id``: due messaggi messi in coda
        nello stesso millisecondo avevano un ordine casuale (l'``id`` lo è), e
        chi cita un messaggio precedente deve arrivare dopo di lui. Vale per i
        pezzi di un file, che entrano tutti nello stesso millisecondo: l'ultimo
        partiva prima degli altri, e al suo ``ack`` la copia in uscita spariva.
        """
        rows = self._db().read(
            "SELECT id, kind, body, created_ms, attempts FROM peer_outbox "
            "WHERE peer = ? ORDER BY rowid",
            (peer,),
        )
        out = []
        for row in rows:
            try:
                body = json.loads(row["body"])
            except ValueError:
                body = {}
            out.append({
                "id": row["id"],
                "kind": row["kind"],
                "body": body if isinstance(body, dict) else {},
                "created_ms": row["created_ms"],
                "attempts": row["attempts"],
            })
        return out

    def due_peers(self, now: int) -> list[str]:
        """Le Jenny con almeno un messaggio pronto a partire."""
        rows = self._db().read(
            "SELECT DISTINCT peer FROM peer_outbox WHERE next_try_ms <= ?", (now,)
        )
        return [row["peer"] for row in rows]

    def next_due_ms(self) -> int | None:
        rows = self._db().read("SELECT MIN(next_try_ms) AS t FROM peer_outbox")
        value = rows[0]["t"] if rows else None
        return int(value) if value is not None else None

    def queued_count(self, peer: str) -> int:
        rows = self._db().read("SELECT COUNT(*) AS n FROM peer_outbox WHERE peer = ?", (peer,))
        return int(rows[0]["n"]) if rows else 0

    def remove(self, msg_id: str) -> None:
        with self._db().write() as conn:
            conn.execute("DELETE FROM peer_outbox WHERE id = ?", (msg_id,))

    def postpone(self, peer: str, now: int) -> int:
        """Un tentativo fallito per *peer*: i suoi messaggi aspettano. Ritorna quando."""
        with self._db().write() as conn:
            row = conn.execute(
                "SELECT MAX(attempts) AS a FROM peer_outbox WHERE peer = ?", (peer,)
            ).fetchone()
            attempts = int(row["a"] or 0) if row else 0
            delay = RETRY_DELAYS_S[min(attempts, len(RETRY_DELAYS_S) - 1)]
            when = now + delay * 1000
            conn.execute(
                "UPDATE peer_outbox SET attempts = attempts + 1, next_try_ms = ? WHERE peer = ?",
                (when, peer),
            )
        return when

    def defer(self, peer: str, now: int, until_ms: int) -> None:
        """I messaggi di *peer* pronti adesso aspettano fino a *until_ms*.

        Non è un tentativo fallito (``postpone``): non conta e non allunga i
        rinvii. Serve quando la coda la sta già mandando un'altra connessione.
        """
        with self._db().write() as conn:
            conn.execute(
                "UPDATE peer_outbox SET next_try_ms = ? WHERE peer = ? AND next_try_ms <= ?",
                (until_ms, peer, now),
            )

    def wake(self, peer: str | None = None) -> None:
        """Tutto quel che aspetta parte al prossimo giro (Tailscale è tornato, l'utente insiste)."""
        with self._db().write() as conn:
            if peer is None:
                conn.execute("UPDATE peer_outbox SET next_try_ms = 0")
            else:
                conn.execute("UPDATE peer_outbox SET next_try_ms = 0 WHERE peer = ?", (peer,))

    def drop_peer(self, peer: str) -> None:
        """Una Jenny scollegata: via la sua coda e il suo stato; la cronologia resta."""
        with self._db().write() as conn:
            conn.execute("DELETE FROM peer_outbox WHERE peer = ?", (peer,))
            conn.execute("DELETE FROM peer_inbox WHERE peer = ?", (peer,))
            conn.execute("DELETE FROM peer_state WHERE peer = ?", (peer,))
            conn.execute("DELETE FROM peer_lines WHERE peer = ?", (peer,))

    # -- Arrivi --

    def arrived(self, peer: str, msg_id: str) -> bool:
        """Vero se *msg_id* di *peer* è già stato trattato (v. :meth:`first_arrival`)."""
        rows = self._db().read(
            "SELECT 1 FROM peer_inbox WHERE peer = ? AND id = ? LIMIT 1", (peer, msg_id)
        )
        return bool(rows)

    def first_arrival(self, peer: str, msg_id: str, now: int) -> bool:
        """Vero la prima volta che *msg_id* arriva da *peer*; falso per un doppione."""
        with self._db().write() as conn:
            conn.execute(
                "DELETE FROM peer_inbox WHERE received_ms < ?", (now - INBOX_KEEP_MS,)
            )
            cur = conn.execute(
                "INSERT OR IGNORE INTO peer_inbox(peer, id, received_ms) VALUES (?, ?, ?)",
                (peer, msg_id, now),
            )
            return cur.rowcount == 1

    # -- Stato --

    def note_seen(self, peer: str, now: int) -> None:
        with self._db().write() as conn:
            conn.execute(
                "INSERT INTO peer_state(peer, last_seen_ms, last_try_ms, last_error) "
                "VALUES (?, ?, ?, '') ON CONFLICT(peer) DO UPDATE SET "
                "last_seen_ms = excluded.last_seen_ms, last_try_ms = excluded.last_try_ms, "
                "last_error = ''",
                (peer, now, now),
            )

    def note_failure(self, peer: str, now: int, error: str) -> None:
        with self._db().write() as conn:
            conn.execute(
                "INSERT INTO peer_state(peer, last_seen_ms, last_try_ms, last_error) "
                "VALUES (?, 0, ?, ?) ON CONFLICT(peer) DO UPDATE SET "
                "last_try_ms = excluded.last_try_ms, last_error = excluded.last_error",
                (peer, now, error[:200]),
            )

    def state(self, peer: str) -> dict[str, Any]:
        rows = self._db().read(
            "SELECT last_seen_ms, last_try_ms, last_error FROM peer_state WHERE peer = ?", (peer,)
        )
        if not rows:
            return {"last_seen_ms": 0, "last_try_ms": 0, "last_error": ""}
        row = rows[0]
        return {
            "last_seen_ms": int(row["last_seen_ms"]),
            "last_try_ms": int(row["last_try_ms"]),
            "last_error": str(row["last_error"]),
        }

    # -- Linee --

    def line_put(
        self, direction: str, key: str, *, peer: str, lid: str, place: str = "",
        state: str = "open", at_ms: int | None = None,
    ) -> None:
        when = now_ms() if at_ms is None else at_ms
        with self._db().write() as conn:
            conn.execute(
                "INSERT INTO peer_lines(direction, key, peer, lid, place, state, since_ms, last_ms) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(direction, key) DO UPDATE SET peer = excluded.peer, "
                "lid = excluded.lid, place = excluded.place, state = excluded.state, "
                "since_ms = excluded.since_ms, last_ms = excluded.last_ms",
                (direction, key, peer, lid, place, state, when, when),
            )

    def line(self, direction: str, key: str) -> dict[str, Any] | None:
        rows = self._db().read(
            "SELECT * FROM peer_lines WHERE direction = ? AND key = ?", (direction, key)
        )
        return dict(rows[0]) if rows else None

    def line_by_lid(self, direction: str, lid: str) -> dict[str, Any] | None:
        rows = self._db().read(
            "SELECT * FROM peer_lines WHERE direction = ? AND lid = ?", (direction, lid)
        )
        return dict(rows[0]) if rows else None

    def lines(self, direction: str | None = None) -> list[dict[str, Any]]:
        if direction is None:
            rows = self._db().read("SELECT * FROM peer_lines ORDER BY since_ms")
        else:
            rows = self._db().read(
                "SELECT * FROM peer_lines WHERE direction = ? ORDER BY since_ms", (direction,)
            )
        return [dict(r) for r in rows]

    def line_update(self, direction: str, key: str, **fields: Any) -> None:
        allowed = {k: v for k, v in fields.items() if k in ("place", "state", "last_ms")}
        if not allowed:
            return
        sets = ", ".join(f"{k} = ?" for k in allowed)
        with self._db().write() as conn:
            conn.execute(
                f"UPDATE peer_lines SET {sets} WHERE direction = ? AND key = ?",
                (*allowed.values(), direction, key),
            )

    def line_drop(self, direction: str, key: str) -> None:
        with self._db().write() as conn:
            conn.execute(
                "DELETE FROM peer_lines WHERE direction = ? AND key = ?", (direction, key)
            )

    # -- Domande --

    def add_question(
        self, peer: str, direction: str, qid: str, text: str, *, state: str,
        outbox_id: str = "", at_ms: int | None = None, mode: str = "read",
    ) -> bool:
        """Una domanda nuova; falso se c'era già (un doppione dalla coda)."""
        with self._db().write() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO peer_questions(qid, peer, direction, text, asked_ms, "
                "state, outbox_id, mode) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (qid, peer, direction, text, now_ms() if at_ms is None else at_ms, state,
                 outbox_id, mode),
            )
            return cur.rowcount == 1

    def set_question(
        self, peer: str, direction: str, qid: str, *, state: str, answer: str | None = None,
    ) -> None:
        with self._db().write() as conn:
            if answer is None:
                conn.execute(
                    "UPDATE peer_questions SET state = ? WHERE peer = ? AND direction = ? "
                    "AND qid = ?",
                    (state, peer, direction, qid),
                )
            else:
                conn.execute(
                    "UPDATE peer_questions SET state = ?, answer = ?, answered_ms = ? "
                    "WHERE peer = ? AND direction = ? AND qid = ?",
                    (state, answer, now_ms(), peer, direction, qid),
                )

    def set_outbox(self, peer: str, direction: str, qid: str, outbox_id: str) -> None:
        """Il messaggio in coda che porta la domanda: a scadenza si toglie da lì."""
        with self._db().write() as conn:
            conn.execute(
                "UPDATE peer_questions SET outbox_id = ? WHERE peer = ? AND direction = ? "
                "AND qid = ?",
                (outbox_id, peer, direction, qid),
            )

    def question(self, peer: str, direction: str, qid: str) -> dict[str, Any] | None:
        rows = self._db().read(
            "SELECT qid, peer, direction, text, asked_ms, state, outbox_id, answered_ms, answer, "
            "mode, done, journals FROM peer_questions WHERE peer = ? AND direction = ? "
            "AND qid = ?",
            (peer, direction, qid),
        )
        return _question_row(rows[0]) if rows else None

    def set_done(self, peer: str, direction: str, qid: str, done: list[dict[str, Any]]) -> None:
        """L'elenco di cosa ha toccato una richiesta di fare (lo scrive il codice)."""
        with self._db().write() as conn:
            conn.execute(
                "UPDATE peer_questions SET done = ? WHERE peer = ? AND direction = ? AND qid = ?",
                (json.dumps(done, ensure_ascii=False), peer, direction, qid),
            )

    def add_journal(self, peer: str, direction: str, qid: str, key: str) -> None:
        """Un registro dell'annulla in più per la richiesta *qid* (quello di un aiutante)."""
        with self._db().write() as conn:
            rows = conn.execute(
                "SELECT journals FROM peer_questions WHERE peer = ? AND direction = ? "
                "AND qid = ?",
                (peer, direction, qid),
            ).fetchall()
            if not rows:
                return
            keys = _json_list(rows[0][0])
            if key in keys:
                return
            keys.append(key)
            conn.execute(
                "UPDATE peer_questions SET journals = ? WHERE peer = ? AND direction = ? "
                "AND qid = ?",
                (json.dumps(keys), peer, direction, qid),
            )

    def find_question(self, direction: str, qid: str) -> dict[str, Any] | None:
        """La domanda *qid* in quel verso, di qualunque Jenny (gli id sono casuali)."""
        rows = self._db().read(
            "SELECT qid, peer, direction, text, asked_ms, state, outbox_id, answered_ms, answer, "
            "mode, done, journals FROM peer_questions WHERE direction = ? AND qid = ? LIMIT 1",
            (direction, qid),
        )
        return _question_row(rows[0]) if rows else None

    def recent_acts(self, peer: str, limit: int = 20) -> list[dict[str, Any]]:
        """Le ultime richieste di fare arrivate da *peer*, dalla più nuova."""
        rows = self._db().read(
            "SELECT qid, peer, direction, text, asked_ms, state, outbox_id, answered_ms, answer, "
            "mode, done, journals FROM peer_questions WHERE peer = ? AND direction = 'in' "
            "AND mode = 'act' ORDER BY asked_ms DESC LIMIT ?",
            (peer, limit),
        )
        return [_question_row(r) for r in rows]

    def open_questions_before(self, cutoff_ms: int) -> list[dict[str, Any]]:
        """Le domande fatte da qui, ancora senza risposta, più vecchie di *cutoff_ms*."""
        rows = self._db().read(
            "SELECT qid, peer, direction, text, asked_ms, state, outbox_id FROM peer_questions "
            "WHERE direction = 'out' AND state = 'queued' AND asked_ms < ?",
            (cutoff_ms,),
        )
        return [dict(r) for r in rows]

    def interrupted_questions(self) -> list[dict[str, Any]]:
        """Le domande arrivate qui e ancora ``answering``: v. ``PeerQuestions.recover_interrupted``."""
        rows = self._db().read(
            "SELECT qid, peer, mode FROM peer_questions "
            "WHERE direction = 'in' AND state = 'answering' ORDER BY asked_ms"
        )
        return [dict(r) for r in rows]

    def questions_since(self, peer: str, direction: str, since_ms: int) -> int:
        rows = self._db().read(
            "SELECT COUNT(*) AS n FROM peer_questions WHERE peer = ? AND direction = ? "
            "AND asked_ms >= ?",
            (peer, direction, since_ms),
        )
        return int(rows[0]["n"]) if rows else 0

    # -- Cronologia --

    def log(self, peer: str, direction: str, kind: str, data: dict[str, Any] | None = None) -> None:
        """Una riga di cronologia: ``direction`` è ``out``, ``in`` o ``sys``."""
        with self._db().write() as conn:
            conn.execute(
                "INSERT INTO peer_log(peer, at_ms, direction, kind, data) VALUES (?, ?, ?, ?, ?)",
                (peer, now_ms(), direction, kind, json.dumps(data or {}, ensure_ascii=False)),
            )
            conn.execute(
                "DELETE FROM peer_log WHERE peer = ? AND seq <= ("
                "SELECT seq FROM peer_log WHERE peer = ? ORDER BY seq DESC LIMIT 1 OFFSET ?)",
                (peer, peer, LOG_KEEP_PER_PEER),
            )

    def recent_log(self, peer: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._db().read(
            "SELECT at_ms, direction, kind, data FROM peer_log WHERE peer = ? "
            "ORDER BY seq DESC LIMIT ?",
            (peer, limit),
        )
        out = []
        for row in rows:
            try:
                data = json.loads(row["data"])
            except ValueError:
                data = {}
            out.append({
                "at_ms": int(row["at_ms"]),
                "direction": row["direction"],
                "kind": row["kind"],
                "data": data if isinstance(data, dict) else {},
            })
        return out
