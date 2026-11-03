"""Storage layer for the WebUI display transcript (estratto da ``transcript.py``).

Il verbale di una conversazione — tutto quel che la WebUI ha mostrato, riletto
uguale alla riapertura — sta in ``state.sqlite``: una riga per frame
(``transcript_rows``), con il numero del turno a cui appartiene. Un turno finisce
con il suo ``turn_end``. Fino alla 2.x era ``.jenny/webui/<chiave>.jsonl``, che
oltre 8 MB ruotava i turni vecchi in segmenti numerati con un manifest; lo
importa ``jenny/db_migration/transcripts.py``.

Per la paginazione (``transcript.py``) i turni si leggono a gruppi, i *chunk*:
:data:`TURNS_PER_CHUNK` turni ciascuno, e l'ultimo — quello dove si scrive — si
chiama :data:`_TRANSCRIPT_ACTIVE_CHUNK_ID`. Nessuna logica di replay/rendering
vive qui: è il solo strato di persistenza.
"""

from __future__ import annotations

import json
import secrets
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.db.schema import Schema

# Un frame oltre questa misura non si salva: il verbale non deve portarsi dentro
# un file intero arrivato per sbaglio come testo.
_MAX_TRANSCRIPT_LINE_BYTES = 8 * 1024 * 1024
TURNS_PER_CHUNK = 50
_TRANSCRIPT_ACTIVE_CHUNK_ID = "active"

SCHEMA = Schema(
    "transcripts",
    (
        """
        CREATE TABLE transcripts (
          key TEXT PRIMARY KEY,
          next_seq INTEGER NOT NULL DEFAULT 0,
          turn INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE transcript_rows (
          key TEXT NOT NULL,
          seq INTEGER NOT NULL,
          turn INTEGER NOT NULL,
          data TEXT NOT NULL,
          PRIMARY KEY (key, seq)
        ) WITHOUT ROWID;
        CREATE INDEX transcript_rows_turn ON transcript_rows(key, turn);
        """,
        # Fin dove i turni di un verbale sono già compatti (``compact_turn``).
        """
        CREATE TABLE transcript_compacted (
          key TEXT PRIMARY KEY,
          through_turn INTEGER NOT NULL
        );
        """,
    ),
    # Negli snapshot: un pezzo per chunk di turni, così i turni vecchi restano
    # lo stesso blob mentre la coda cresce.
    shards={
        "transcripts": "key",
        "transcript_rows": f"key || '/' || (turn / {TURNS_PER_CHUNK})",
        "transcript_compacted": "key",
    },
)

# -- i pezzetti dello streaming ---------------------------------------------------
#
# Un turno arriva a pezzetti: un ``delta`` o un ``reasoning_delta`` per ogni
# chunk del provider. Nel verbale erano il 95% delle righe (287 mila su 303 mila
# sul telefono di prova, il 03/11/2026, e 52 dei 67 MB di dati). Il replay li
# incolla comunque (``transcript_replay``: i ``delta`` consecutivi fanno un testo,
# i ``reasoning_delta`` consecutivi un ragionamento), quindi a turno chiuso si
# fondono: un frame per tratto, col testo di tutti e gli altri campi dell'ultimo
# (il replay tiene il ``turn_seq`` dell'ultimo pezzo). Misurato sui dati veri: il
# replay è identico su tutti i 2056 turni, e 8 volte più veloce. Il turno in corso
# resta a pezzetti: una WebUI che si riconnette a metà lo rivede come prima.
_FUSIBLE_EVENTS = frozenset({"delta", "reasoning_delta"})
# I campi che cambiano da un pezzetto all'altro dello stesso tratto.
_PER_CHUNK_FIELDS = frozenset({"text", "at", "turn_seq", "ts"})


def _same_stretch(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if a.get("event") != b.get("event") or a.get("event") not in _FUSIBLE_EVENTS:
        return False
    if not isinstance(a.get("text"), str) or not isinstance(b.get("text"), str):
        return False
    return ({k: v for k, v in a.items() if k not in _PER_CHUNK_FIELDS}
            == {k: v for k, v in b.items() if k not in _PER_CHUNK_FIELDS})


def fuse_frames(
    frames: list[tuple[int, dict[str, Any]]],
) -> list[tuple[int, dict[str, Any], list[int]]]:
    """I tratti da fondere di un turno: ``(seq tenuta, frame fuso, seq da togliere)``.

    *frames* sono ``(seq, frame)`` in ordine. Un frame che non si fonde con niente
    non compare.
    """
    out: list[tuple[int, dict[str, Any], list[int]]] = []
    i = 0
    while i < len(frames):
        j = i + 1
        while j < len(frames) and _same_stretch(frames[j - 1][1], frames[j][1]):
            j += 1
        if j - i > 1:
            stretch = [frame for _seq, frame in frames[i:j]]
            fused = dict(stretch[-1], text="".join(f["text"] for f in stretch))
            out.append((frames[i][0], fused, [seq for seq, _f in frames[i + 1:j]]))
        i = j
    return out


def compact_turn(conn: Any, session_key: str, turn: int) -> int:
    """Fonde i pezzetti del turno *turn* di *session_key*; ritorna quante righe ha tolto.

    Dentro la transazione di chi chiama. Rifarla non cambia niente.
    """
    frames: list[tuple[int, dict[str, Any]]] = []
    for row in conn.execute("SELECT seq, data FROM transcript_rows WHERE key = ? AND turn = ? "
                            "ORDER BY seq", (session_key, turn)):
        try:
            record = json.loads(row["data"])
        except ValueError:
            continue
        if isinstance(record, dict):
            frames.append((row["seq"], record))
    removed = 0
    for seq, fused, dropped in fuse_frames(frames):
        conn.execute("UPDATE transcript_rows SET data = ? WHERE key = ? AND seq = ?",
                     (_record_json_line(fused), session_key, seq))
        conn.executemany("DELETE FROM transcript_rows WHERE key = ? AND seq = ?",
                         [(session_key, s) for s in dropped])
        removed += len(dropped)
    return removed


def _note_compacted(conn: Any, session_key: str, turn: int) -> None:
    """Il segnalibro avanza solo di un turno alla volta: ``through_turn`` vuol dire
    «tutti i turni fino a qui», e i turni di prima di questa versione li compatta
    :func:`compact_closed_turns`, che lo porta avanti lei."""
    row = conn.execute("SELECT through_turn FROM transcript_compacted WHERE key = ?",
                       (session_key,)).fetchone()
    through = row["through_turn"] if row else -1
    if through == turn - 1:
        conn.execute("INSERT INTO transcript_compacted(key, through_turn) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET through_turn = excluded.through_turn",
                     (session_key, turn))


def _is_user_transcript_row(row: dict[str, Any]) -> bool:
    return row.get("event") == "user" or row.get("role") == "user"


def _is_session_boundary_row(row: dict[str, Any]) -> bool:
    """La riga che ``/new`` lascia nel transcript.

    La scrive ``channels/ws_sender.py`` (``payload["session_boundary"]``) e la
    rilegge ``transcript_replay.py`` per renderla come separatore. Sta qui,
    accanto all'altro predicato di riga, perche' da questa versione decide anche
    **dove comincia la cronologia visibile**: v. ``_select_transcript_page``.
    """
    return row.get("session_boundary") is True


def _split_transcript_turns(lines: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    turns: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for rec in lines:
        current.append(rec)
        if rec.get("event") == "turn_end":
            turns.append(current)
            current = []
    if current:
        turns.append(current)
    return turns


def _record_json_line(record: dict[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False, separators=(",", ":"))


# -- dove ---------------------------------------------------------------------


def _workspace() -> Path:
    """Il workspace dei verbali, ricavato dalla loro cartella di sempre.

    ``.jenny/webui`` sta dentro il workspace; i test spostano quella cartella
    altrove per isolarsi, e allora la cartella stessa fa da workspace.
    """
    from jenny.config.paths import get_webui_dir

    webui = Path(get_webui_dir())
    return webui.parent.parent if webui.parent.name == ".jenny" else webui


def _db():
    from jenny import db

    database = db.database_for_workspace(_workspace(), db.STATE)
    database.require(SCHEMA)
    return database


def _rows(where: str, params: tuple) -> list[tuple[int, dict[str, Any]]]:
    """``(turno, frame)`` del verbale, in ordine; una riga illeggibile si salta."""
    out: list[tuple[int, dict[str, Any]]] = []
    for row in _db().read(f"SELECT turn, data FROM transcript_rows WHERE {where} ORDER BY seq",
                          params):
        try:
            record = json.loads(row["data"])
        except ValueError:
            logger.warning("bad transcript row ({} chars)", len(row["data"]))
            continue
        if isinstance(record, dict):
            out.append((row["turn"], record))
    return out


# -- chunk, per la paginazione --------------------------------------------------


def _last_chunk(session_key: str) -> int | None:
    rows = _db().read("SELECT max(turn) AS turn FROM transcript_rows WHERE key = ?",
                      (session_key,))
    turn = rows[0]["turn"] if rows else None
    return None if turn is None else int(turn) // TURNS_PER_CHUNK


def _has_active_chunk(session_key: str) -> bool:
    return _last_chunk(session_key) is not None


def _read_segment_manifest_entries(session_key: str) -> list[dict[str, Any]]:
    """I chunk prima di quello attivo: ``{id, bytes, turn_count, user_count}``."""
    last = _last_chunk(session_key)
    if last is None or last == 0:
        return []
    rows = _db().read(
        f"SELECT turn / {TURNS_PER_CHUNK} AS chunk, count(DISTINCT turn) AS turns, "
        "sum(length(data)) AS bytes, sum(CASE WHEN json_extract(data, '$.event') = 'user' "
        "OR json_extract(data, '$.role') = 'user' THEN 1 ELSE 0 END) AS users "
        f"FROM transcript_rows WHERE key = ? AND turn < ? GROUP BY chunk ORDER BY chunk",
        (session_key, last * TURNS_PER_CHUNK),
    )
    return [{"id": f"{r['chunk']:06d}", "bytes": int(r["bytes"] or 0),
             "turn_count": int(r["turns"]), "user_count": int(r["users"] or 0)} for r in rows]


def _read_chunk_turns(session_key: str, chunk_id: str) -> list[list[dict[str, Any]]]:
    if chunk_id == _TRANSCRIPT_ACTIVE_CHUNK_ID:
        last = _last_chunk(session_key)
        if last is None:
            return []
        chunk = last
    else:
        try:
            chunk = int(chunk_id)
        except ValueError:
            return []
    rows = _rows("key = ? AND turn >= ? AND turn < ?",
                 (session_key, chunk * TURNS_PER_CHUNK, (chunk + 1) * TURNS_PER_CHUNK))
    turns: dict[int, list[dict[str, Any]]] = {}
    for turn, record in rows:
        turns.setdefault(turn, []).append(record)
    return [turns[t] for t in sorted(turns)]


def read_transcript_lines(session_key: str) -> list[dict[str, Any]]:
    return [record for _turn, record in _rows("key = ?", (session_key,))]


def iter_transcript_backwards(session_key: str, *, block: int = 200) -> Iterator[dict[str, Any]]:
    """I frame del verbale dal più recente al più vecchio, *block* alla volta.

    Chi vuole la coda (il giardiniere) smette quando ne ha abbastanza, senza
    leggere un verbale lungo per intero.
    """
    before: int | None = None
    while True:
        rows = _db().read(
            "SELECT seq, data FROM transcript_rows WHERE key = ? AND (? IS NULL OR seq < ?) "
            "ORDER BY seq DESC LIMIT ?", (session_key, before, before, block))
        if not rows:
            return
        for row in rows:
            try:
                record = json.loads(row["data"])
            except ValueError:
                continue
            if isinstance(record, dict):
                yield record
        before = rows[-1]["seq"]


# -- chi segue i verbali ----------------------------------------------------------
#
# L'indice di ricerca delle chat (``webui/chat_index``) si tiene in pari da qui:
# questo modulo è l'unico che scrive i verbali. Sta in un altro database, quindi
# non nella stessa transazione: se un aggiornamento manca (un kill in mezzo), lo
# rimette in pari la riconciliazione dell'indice. Un errore qui non ferma mai la
# scrittura del verbale.


def _follow(action: str, *args: str) -> None:
    try:
        from jenny.webui import chat_index

        getattr(chat_index, action)(_workspace(), *args)
    except Exception:  # noqa: BLE001 — l'indice si rimette in pari da solo
        logger.opt(exception=True).warning("Chat index: {} failed", action)


def _turn_closed(session_key: str) -> None:
    _follow("turn_closed", session_key)


def _transcript_dropped(session_key: str) -> None:
    _follow("transcript_dropped", session_key)


def _transcript_renamed(old_key: str, new_key: str) -> None:
    _follow("transcript_renamed", old_key, new_key)


# -- scrivere -------------------------------------------------------------------


# La revisione di ogni filo: quante righe ci sono entrate da quando il gateway
# e' partito, col prefisso di questo avvio. Serve a una finestra per sapere, con
# una richiesta piccola, se il filo che ha a schermo e' ancora quello vero (la
# casa del telefono quando torni a guardarla, dopo una chat dal Mac). Non sta su
# disco e non deve: a un riavvio il prefisso cambia, ogni confronto fallisce e
# chi chiede rilegge — che e' anche quel che fa gia' alla riconnessione.
_BOOT = secrets.token_hex(4)
_REVISIONS: dict[str, int] = {}


def transcript_revision(session_key: str) -> str:
    """La revisione attuale del filo *session_key* (``"<avvio>.<righe>"``)."""
    return f"{_BOOT}.{_REVISIONS.get(session_key, 0)}"


def _bump_revision(session_key: str) -> None:
    _REVISIONS[session_key] = _REVISIONS.get(session_key, 0) + 1


def append_transcript_object(session_key: str, obj: dict[str, Any]) -> None:
    """Un frame in coda al verbale, nel turno in corso; un ``turn_end`` lo chiude."""
    raw = _record_json_line(obj)
    if len(raw.encode("utf-8")) > _MAX_TRANSCRIPT_LINE_BYTES:
        msg = "webui transcript line too large"
        raise ValueError(msg)
    with _db().write() as conn:
        row = conn.execute("SELECT next_seq, turn FROM transcripts WHERE key = ?",
                           (session_key,)).fetchone()
        seq, turn = (row["next_seq"], row["turn"]) if row else (0, 0)
        conn.execute("INSERT INTO transcript_rows(key, seq, turn, data) VALUES (?, ?, ?, ?)",
                     (session_key, seq, turn, raw))
        closes = obj.get("event") == "turn_end"
        conn.execute(
            "INSERT INTO transcripts(key, next_seq, turn) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET next_seq = excluded.next_seq, turn = excluded.turn",
            (session_key, seq + 1, turn + 1 if closes else turn),
        )
        if closes:
            compact_turn(conn, session_key, turn)
            _note_compacted(conn, session_key, turn)
    _bump_revision(session_key)
    if closes:
        _turn_closed(session_key)


def delete_webui_transcript(session_key: str) -> bool:
    with _db().write() as conn:
        removed = conn.execute("DELETE FROM transcripts WHERE key = ?",
                               (session_key,)).rowcount > 0
        removed = conn.execute("DELETE FROM transcript_rows WHERE key = ?",
                               (session_key,)).rowcount > 0 or removed
        conn.execute("DELETE FROM transcript_compacted WHERE key = ?", (session_key,))
    if removed:
        _transcript_dropped(session_key)
        _bump_revision(session_key)
    return removed


def rename_webui_transcript(old_key: str, new_key: str) -> bool:
    """Il verbale di *old_key* passa a *new_key*. False se *new_key* ne ha già uno
    o *old_key* non ne ha."""
    with _db().write() as conn:
        if conn.execute("SELECT 1 FROM transcripts WHERE key = ?", (new_key,)).fetchone():
            return False
        if not conn.execute("SELECT 1 FROM transcripts WHERE key = ?", (old_key,)).fetchone():
            return False
        conn.execute("UPDATE transcripts SET key = ? WHERE key = ?", (new_key, old_key))
        conn.execute("UPDATE transcript_rows SET key = ? WHERE key = ?", (new_key, old_key))
        conn.execute("DELETE FROM transcript_compacted WHERE key = ?", (new_key,))
        conn.execute("UPDATE transcript_compacted SET key = ? WHERE key = ?", (new_key, old_key))
    _transcript_renamed(old_key, new_key)
    _bump_revision(old_key)
    _bump_revision(new_key)
    return True


def compact_closed_turns(*, batch: int = 10, pause: float = 0.02) -> int:
    """Compatta i turni chiusi che non lo sono ancora; ritorna quante righe ha tolto.

    Al boot, in un thread: i verbali di prima di questa versione, quelli
    importati dalla migrazione e quelli riportati da uno snapshot vecchio. Il
    segnalibro (``transcript_compacted``) la fa costare una query quando non c'è
    niente da fare. A lotti di *batch* turni, ognuno una transazione, e fra un
    lotto e l'altro *pause* secondi: il lock di scrittura non è equo, e senza la
    pausa questo thread lo riprendeva prima che chi aspettava si svegliasse —
    sul telefono, il 03/11/2026, una richiesta della casa ha aspettato tutti i
    16 secondi della prima passata.
    """

    database = _db()
    removed = 0
    for row in database.read(
        "SELECT t.key, t.turn, coalesce(c.through_turn, -1) AS through FROM transcripts t "
        "LEFT JOIN transcript_compacted c ON c.key = t.key WHERE t.turn - 1 > "
        "coalesce(c.through_turn, -1)"
    ):
        key, closed, done = row["key"], int(row["turn"]) - 1, int(row["through"])
        while done < closed:
            last = min(done + batch, closed)
            with database.write() as conn:
                for turn in range(done + 1, last + 1):
                    removed += compact_turn(conn, key, turn)
                conn.execute(
                    "INSERT INTO transcript_compacted(key, through_turn) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET through_turn = excluded.through_turn",
                    (key, last))
            done = last
            time.sleep(pause)
    if removed:
        logger.info("Transcripts: fused {} streaming rows", removed)
    return removed


def transcript_bytes(session_key: str) -> int:
    rows = _db().read("SELECT sum(length(data)) FROM transcript_rows WHERE key = ?",
                      (session_key,))
    return int(rows[0][0] or 0) if rows else 0


def has_webui_transcript(session_key: str) -> bool:
    return bool(_db().read("SELECT 1 FROM transcripts WHERE key = ?", (session_key,)))
