"""Le parole delle chat, cercabili: l'indice dietro ``recall_chat``.

Jenny ricorda prima di tutto coi suoi appunti (memoria, quaderni, diario,
``recall_history``). Le parole esatte dette in chat stanno solo nei verbali della
WebUI (``transcript_store``), e da quando sono in ``state.sqlite`` i suoi
strumenti, che leggono file, non ci arrivano più. Questo indice le rende
cercabili: un messaggio per riga — dell'utente, o una risposta di Jenny — con un
indice FTS5 per parole (``unicode61``, senza accenti, ricerca per inizio di
parola). Misurato sul telefono di prova il 03/11/2026: tre mesi di chat sono
2160 messaggi e 1,65 MB, l'indice 0,8 MB, una ricerca sotto il millisecondo.

**È derivato.** Sta in ``index.sqlite``, fuori dal backup e dagli snapshot, e si
rifà dai verbali: un ripristino lo cancella (``db.restore``). Non è nella stessa
transazione dei verbali, quindi si tiene in pari con un segnalibro per chat
(``chat_indexed``: i turni fino a qui sono dentro) e una sola funzione di
recupero, :func:`catch_up`, che serve sia alla fine di ogni turno
(``transcript_store`` chiama :func:`turn_closed`) sia all'avvio
(:func:`reconcile`). Un turno in corso non è ancora cercabile.

Si indicizzano le parole che la chat ha mostrato, non la storia del modello: il
messaggio dell'utente (anche dalla mascotte, da Telegram, dalla tendina), la
risposta di Jenny in streaming (i ``delta`` della fase ``answer``, uniti) o no
(un ``message`` senza ``kind``). Non il ragionamento, non i risultati dei tool,
non le righe di sistema (``session_boundary``).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jenny.db.schema import Schema

SCHEMA = Schema(
    "chat_index",
    (
        """
        CREATE TABLE chat_texts (
          id INTEGER PRIMARY KEY,
          key TEXT NOT NULL,
          turn INTEGER NOT NULL,
          seq INTEGER NOT NULL,
          role TEXT NOT NULL,
          at INTEGER,
          source TEXT,
          text TEXT NOT NULL
        );
        CREATE INDEX chat_texts_turn ON chat_texts(key, turn, seq);
        CREATE VIRTUAL TABLE chat_fts USING fts5(
          text, content='chat_texts', content_rowid='id',
          tokenize='unicode61 remove_diacritics 2'
        );
        CREATE TRIGGER chat_texts_ai AFTER INSERT ON chat_texts BEGIN
          INSERT INTO chat_fts(rowid, text) VALUES (new.id, new.text);
        END;
        CREATE TRIGGER chat_texts_ad AFTER DELETE ON chat_texts BEGIN
          INSERT INTO chat_fts(chat_fts, rowid, text) VALUES ('delete', old.id, old.text);
        END;
        CREATE TABLE chat_indexed (
          key TEXT PRIMARY KEY,
          through_turn INTEGER NOT NULL
        );
        """,
    ),
)

USER = "user"
JENNY = "jenny"


@dataclass(frozen=True)
class ChatText:
    key: str
    turn: int
    seq: int
    role: str
    at: int | None
    source: str | None
    text: str


def _index(workspace: Path):
    from jenny import db

    return db.store_database(workspace, db.INDEX, SCHEMA)


def _state(workspace: Path):
    from jenny import db
    from jenny.webui.transcript_store import SCHEMA as TRANSCRIPTS

    return db.store_database(workspace, db.STATE, TRANSCRIPTS)


# -- dai frame alle parole --------------------------------------------------------


def texts_of_frames(key: str, frames: list[tuple[int, int, dict[str, Any]]]) -> list[ChatText]:
    """Le parole di una sequenza di frame ``(turno, seq, frame)`` in ordine.

    I ``delta`` della risposta consecutivi dello stesso stream fanno un testo
    solo, compattati o no; l'ora di un messaggio è quella del suo frame, o
    l'ultima vista prima nel turno.
    """
    out: list[ChatText] = []
    open_reply: dict[str, Any] | None = None
    last_at: int | None = None
    last_turn: int | None = None

    def close_reply() -> None:
        nonlocal open_reply
        if open_reply and open_reply["text"].strip():
            out.append(ChatText(key, open_reply["turn"], open_reply["seq"], JENNY,
                                open_reply["at"], None, open_reply["text"].strip()))
        open_reply = None

    for turn, seq, frame in frames:
        if turn != last_turn:
            close_reply()
            last_at, last_turn = None, turn
        at = frame.get("at")
        if isinstance(at, int) and not isinstance(at, bool):
            last_at = at
        event = frame.get("event")
        text = frame.get("text")
        if event == "delta" and frame.get("turn_phase") in (None, "answer"):
            if isinstance(text, str):
                stream = frame.get("stream_id")
                if open_reply is None or open_reply["stream"] != stream:
                    close_reply()
                    open_reply = {"turn": turn, "seq": seq, "at": last_at, "stream": stream,
                                  "text": ""}
                open_reply["text"] += text
            continue
        if event in ("delta", "reasoning_delta", "reasoning_end"):
            continue
        close_reply()
        if not isinstance(text, str) or not text.strip():
            continue
        source = frame.get("origin_channel") or frame.get("origin")
        source = source if isinstance(source, str) else None
        if event == "user":
            out.append(ChatText(key, turn, seq, USER, last_at, source, text.strip()))
        elif (event == "message" and frame.get("kind") is None
              and not frame.get("session_boundary")):
            out.append(ChatText(key, turn, seq, JENNY, last_at, source, text.strip()))
    close_reply()
    return out


# -- tenerlo in pari --------------------------------------------------------------


def catch_up(workspace: Path, key: str) -> int:
    """Indicizza i turni chiusi di *key* che non lo sono ancora; ritorna quanti testi."""
    import json

    state, index = _state(workspace), _index(workspace)
    rows = state.read("SELECT turn FROM transcripts WHERE key = ?", (key,))
    if not rows:
        transcript_dropped(workspace, key)
        return 0
    closed = int(rows[0]["turn"]) - 1
    have = index.read("SELECT through_turn FROM chat_indexed WHERE key = ?", (key,))
    through = int(have[0]["through_turn"]) if have else -1
    if through > closed:
        # Il verbale è tornato indietro (un ripristino, un progetto ricreato):
        # l'indice di questa chat si rifà da capo.
        transcript_dropped(workspace, key)
        through = -1
    if through >= closed:
        return 0
    frames: list[tuple[int, int, dict[str, Any]]] = []
    for row in state.read(
        "SELECT turn, seq, data FROM transcript_rows WHERE key = ? AND turn > ? AND turn <= ? "
        "ORDER BY seq", (key, through, closed),
    ):
        try:
            record = json.loads(row["data"])
        except ValueError:
            continue
        if isinstance(record, dict):
            frames.append((int(row["turn"]), int(row["seq"]), record))
    texts = texts_of_frames(key, frames)
    with index.write() as conn:
        conn.execute("DELETE FROM chat_texts WHERE key = ? AND turn > ?", (key, through))
        conn.executemany(
            "INSERT INTO chat_texts(key, turn, seq, role, at, source, text) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(t.key, t.turn, t.seq, t.role, t.at, t.source, t.text) for t in texts],
        )
        conn.execute("INSERT INTO chat_indexed(key, through_turn) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET through_turn = excluded.through_turn",
                     (key, closed))
    return len(texts)


def turn_closed(workspace: Path, key: str) -> None:
    catch_up(workspace, key)


def transcript_dropped(workspace: Path, key: str) -> None:
    with _index(workspace).write() as conn:
        conn.execute("DELETE FROM chat_texts WHERE key = ?", (key,))
        conn.execute("DELETE FROM chat_indexed WHERE key = ?", (key,))


def transcript_renamed(workspace: Path, old_key: str, new_key: str) -> None:
    with _index(workspace).write() as conn:
        conn.execute("DELETE FROM chat_texts WHERE key = ?", (new_key,))
        conn.execute("DELETE FROM chat_indexed WHERE key = ?", (new_key,))
        conn.execute("UPDATE chat_texts SET key = ? WHERE key = ?", (new_key, old_key))
        conn.execute("UPDATE chat_indexed SET key = ? WHERE key = ?", (new_key, old_key))


def reconcile(workspace: Path) -> int:
    """All'avvio: ogni chat in pari, e via quelle che non ci sono più. Ritorna i testi nuovi."""
    keys = {r["key"] for r in _state(workspace).read("SELECT key FROM transcripts")}
    for row in _index(workspace).read("SELECT DISTINCT key FROM chat_indexed"):
        if row["key"] not in keys:
            transcript_dropped(workspace, row["key"])
    return sum(catch_up(workspace, key) for key in sorted(keys))


# -- cercare ----------------------------------------------------------------------


@dataclass(frozen=True)
class Hit:
    key: str
    turn: int
    seq: int
    role: str
    at: int | None
    snippet: str


def match_expression(terms: list[str]) -> str:
    """Le parole come domanda FTS5: ognuna per inizio di parola, in OR.

    Le virgolette tengono fuori la sintassi di FTS5 (un ``-`` o un ``:`` nella
    parola); dentro, una virgoletta si raddoppia.
    """
    return " OR ".join('"' + t.replace('"', '""') + '"*' for t in terms if t)


def search(workspace: Path, terms: list[str], *, keys: list[str] | None = None,
           limit: int = 40) -> tuple[int, list[Hit]]:
    """``(quanti messaggi trovati in tutto, i migliori *limit*)``, dai più pertinenti.

    *keys* limita alle chat date (``None``: tutte).
    """
    expr = match_expression(terms)
    if not expr:
        return 0, []
    where, params = "chat_fts MATCH ?", [expr]
    if keys is not None:
        if not keys:
            return 0, []
        where += f" AND t.key IN ({','.join('?' * len(keys))})"
        params += keys
    index = _index(workspace)
    total = index.read(
        f"SELECT count(*) AS n FROM chat_fts JOIN chat_texts t ON t.id = chat_fts.rowid "
        f"WHERE {where}", params)[0]["n"]
    rows = index.read(
        f"SELECT t.key, t.turn, t.seq, t.role, t.at, "
        f"snippet(chat_fts, 0, '[', ']', '…', 24) AS snip FROM chat_fts "
        f"JOIN chat_texts t ON t.id = chat_fts.rowid WHERE {where} "
        f"ORDER BY bm25(chat_fts), t.at DESC LIMIT ?", [*params, limit])
    return int(total), [Hit(r["key"], int(r["turn"]), int(r["seq"]), r["role"], r["at"], r["snip"])
                        for r in rows]


def turn_texts(workspace: Path, key: str, turns: list[int]) -> list[ChatText]:
    """I messaggi dei turni *turns* di *key*, in ordine."""
    if not turns:
        return []
    rows = _index(workspace).read(
        f"SELECT key, turn, seq, role, at, source, text FROM chat_texts WHERE key = ? "
        f"AND turn IN ({','.join('?' * len(turns))}) ORDER BY turn, seq", [key, *turns])
    return [ChatText(r["key"], int(r["turn"]), int(r["seq"]), r["role"], r["at"], r["source"],
                     r["text"]) for r in rows]
