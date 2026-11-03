"""Il registro delle scritture di un aiutante, e il suo annulla.

Un aiutante (subagent) che scrive nel workspace lascia qui, per ogni file che
cambia, com'era subito prima e com'è subito dopo. Serve a una cosa sola: poter
togliere **il suo** lavoro, e solo il suo, quando l'utente o Jenny lo chiedono
(«Ferma e annulla», ``subagent_cancel undo=true``).

Due foto del quaderno, una all'inizio e una alla fine, direbbero *cosa* è
cambiato in quel tempo, non *chi* l'ha cambiato: annullerebbero anche quello che
nel frattempo hanno scritto l'utente, il giardiniere o un altro aiutante. Il
registro invece lo scrive chi scrive, nei punti da cui ogni scrittura di un
aiutante passa già.

**Chi lo tiene.** Il codice, mai il modello. Chi lavora per conto di qualcosa
lega il suo registro con :func:`bind_journal` (una ``ContextVar``, che arriva
anche nei thread di ``python_exec``): ``SubagentManager._run_subagent`` quello
dell'aiutante. I recinti di scrittura chiamano :meth:`WriteJournal.touch` del
registro legato subito prima di scrivere (``subagent_policy.check_write`` e
``journal_touch``), e il runner chiama :meth:`WriteJournal.settle` dopo ogni
strumento: lì si misura il «dopo» e si annota la riga. Senza registro legato
niente di questo modulo gira: il giardiniere, Dream e l'utente non ne hanno.

**La chiave è il lineage**, non il tentativo: un aiutante fatto continuare dopo
un budget, un 429 o un riavvio è lo stesso lavoro, e l'annulla toglie tutto ciò
che il lineage ha scritto.

**La chiave** di un registro è ``journal_key(kind, ident)``: ``helper-<lineage>``
per un aiutante. Il lineage e non il tentativo: un aiutante fatto continuare
dopo un budget, un 429 o un riavvio è lo stesso lavoro.

**Su disco**, in ``<runtime_root>/db/undo.sqlite`` (``jenny/db``), fuori dal
workspace: i tool del modello non ci arrivano, e non entra né nelle foto né nel
backup (si svuota a un ripristino, ``jenny/db/restore``).

* ``entries`` — una riga per file cambiato in uno strumento: percorso, ``before``
  e ``after``, ora del tocco, tentativo, strumento. ``before`` e ``after`` sono
  firme (:func:`signature`): lo sha256 del contenuto, ``None`` se il file non
  c'era, ``"dir"`` per una cartella. Le righe di uno strumento entrano in una
  transazione sola.
* ``objects`` + ``undo-objects/<hh>/<sha>`` — il contenuto «prima», salvato
  **solo alla prima** scrittura del registro su quel file (zlib, chiave lo
  sha256, come lo store degli snapshot). Le scritture dopo annotano solo le
  firme, che bastano a riconoscere chi è passato in mezzo. Un contenuto che due
  registri salvano è un file solo; si toglie quando nessuna riga lo nomina più.
* ``undos`` — lo stato dell'annulla: ``running`` mentre rimette i file (al boot
  si finisce, :func:`finish_interrupted_undos`), ``done`` col resoconto.

**L'annulla, file per file** (:meth:`WriteJournal.undo`). Si rimette il primo
«prima» solo se il file è ancora esattamente come l'ha lasciato l'aiutante, e
se nessuno ci è passato fra due sue scritture. Altrimenti il file resta com'è e
il resoconto dice quale e perché: mai sovrascrivere il lavoro di altri.

**La bacheca si annulla per pezzi.** ``board.json`` di un quaderno è un file
unico che scrivono in tanti: la casa ci timbra ``lastSeen`` ogni volta che
ridisegna la bacheca, l'orologio dei quaderni gli stati delle app. Annullati a file intero risultavano quasi sempre «cambiati da altri» (prova
su jenny_s22 del 26/10/2026: la bacheca ridisegnata dopo la mini-me). Per loro il
registro tiene anche il «dopo» di ogni scrittura, e l'annulla toglie dal
contenuto di adesso solo il cambiamento dell'aiutante, chiave per chiave e
scheda per scheda (``id``): :func:`_revert`. Se qualcun altro ha toccato la
stessa scheda, la bacheca resta com'è.

**Limiti, detti chiaro.** Restano fuori gli effetti che non sono file del
workspace (un messaggio mandato, una ricerca, un promemoria già suonato, i job
cron, che riscrive lo scheduler), le bozze (non sono scritture finché l'utente
non le installa) e quel che il codice nativo scrive senza passare dalle funzioni
intercettate di ``python_exec``.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
import zlib
from collections.abc import Iterable, Iterator
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.config.paths import DEFAULT_WIKIS_DIR
from jenny.db.schema import Schema
from jenny.utils.path import atomic_write

#: Le tabelle del diario in ``undo.sqlite``.
SCHEMA = Schema("undo", (
    """
    CREATE TABLE journals (
      key TEXT PRIMARY KEY,
      created_at REAL NOT NULL,
      task_id TEXT,
      attempt INTEGER
    );
    CREATE TABLE entries (
      id INTEGER PRIMARY KEY,
      journal TEXT NOT NULL REFERENCES journals(key) ON DELETE CASCADE,
      ts REAL NOT NULL,
      path TEXT NOT NULL,
      before TEXT,
      after TEXT,
      saved INTEGER,
      shared INTEGER NOT NULL DEFAULT 0,
      tool TEXT,
      task_id TEXT,
      attempt INTEGER,
      step TEXT
    );
    CREATE INDEX entries_journal ON entries(journal, path, ts);
    CREATE INDEX entries_path ON entries(path, ts);
    CREATE TABLE objects (
      journal TEXT NOT NULL REFERENCES journals(key) ON DELETE CASCADE,
      sha TEXT NOT NULL,
      size INTEGER NOT NULL,
      PRIMARY KEY (journal, sha)
    );
    CREATE INDEX objects_sha ON objects(sha);
    CREATE TABLE undos (
      key TEXT PRIMARY KEY,
      sources TEXT NOT NULL,
      state TEXT NOT NULL,
      started_at REAL,
      report TEXT,
      merged TEXT NOT NULL DEFAULT '[]'
    );
    """,
))

HELPER = "helper"


def journal_key(kind: str, ident: str) -> str:
    """La chiave di un registro: l'unico posto che la compone."""
    return f"{kind}-{ident}"

#: Firma di una cartella. Una cartella non ha contenuto da salvare: quel che
#: conta è se c'era (per toglierla vuota, o rifarla).
DIR = "dir"

#: Oltre questo peso il «prima» non si copia: si annota la firma (per sapere se
#: qualcuno l'ha toccato) e il file risulta non annullabile. Le immagini grandi
#: in ``raw/`` sono il caso: copiarle a ogni prima scrittura costerebbe più del
#: lavoro dell'aiutante.
MAX_BLOB_BYTES = 5 * 1024 * 1024

#: Il tetto dei contenuti salvati per lineage. Oltre, il registro continua ad
#: annotare le firme ma smette di copiare, e i file nuovi risultano non
#: annullabili: meglio un annulla parziale e detto che un telefono pieno.
MAX_LINEAGE_BYTES = 200 * 1024 * 1024

#: Il tetto dei contenuti «prima» tenuti in memoria in attesa del «dopo», per
#: strumento. Una ``rmtree`` su una cartella grande ne toccherebbe migliaia: oltre
#: il tetto i file si annotano con la sola firma, e risultano non annullabili.
MAX_PENDING_BYTES = 64 * 1024 * 1024

#: Quanti file di una cartella si annotano quando un aiutante la tocca intera
#: (``shutil.rmtree``, una rinomina): oltre, la cartella non è annullabile.
MAX_DIR_FILES = 2000

# Motivi per cui un file resta com'è. Sono chiavi, non frasi: la casa le
# traduce, e Jenny le legge dal suo resoconto in inglese (:meth:`UndoReport.text`).
KEPT_CHANGED_AFTER = "changed_after"
KEPT_CHANGED_DURING = "changed_during"
KEPT_NOT_SAVED = "not_saved"
KEPT_ERROR = "error"
# Si sarebbe rimesso, ma è in un gruppo che chi annulla ha chiesto di tenere.
KEPT_GROUP = "kept_group"
# Non torna mai indietro (il diario di un quaderno): non è un «lasciato».
KEPT_EXCLUDED = "excluded"

_KEPT_TEXT = {
    KEPT_CHANGED_AFTER: "changed by someone else after the helper",
    KEPT_CHANGED_DURING: "changed by someone else while the helper was working",
    KEPT_NOT_SAVED: "its earlier content was not saved (too large, or the journal was full)",
    KEPT_ERROR: "could not be written back",
    KEPT_GROUP: "kept on request",
}

_MISSING: Any = object()


class UndoConflictError(Exception):
    """I file non sono più quelli dell'anteprima: niente è stato rimesso.

    Il gettone dell'anteprima (``undo_set(dry_run=True)``) fotografa le firme dei
    file che l'annulla toccherebbe; alla conferma si rifà, e se qualcosa è
    cambiato in mezzo il conto mostrato non è più vero.
    """

    code = "conflict"


class _ConflictError(Exception):
    """Lo stesso pezzo cambiato dall'aiutante e, dopo, da qualcun altro."""


def _mergeable(rel: str) -> bool:
    """La bacheca di un quaderno: JSON che si annulla per pezzi."""
    parts = rel.split("/")
    return (len(parts) == 4 and parts[0] == DEFAULT_WIKIS_DIR and parts[2] == "board"
            and parts[3] == "board.json")


def _id_list(value: Any) -> bool:
    """Una lista di oggetti con ``id`` unici (le schede, le attese)."""
    if not isinstance(value, list):
        return False
    ids = [item.get("id") for item in value if isinstance(item, dict)]
    return (len(ids) == len(value) and all(isinstance(i, str) for i in ids)
            and len(set(ids)) == len(ids))


def _key(item: Any) -> str:
    return json.dumps(item, sort_keys=True, ensure_ascii=False)


def _revert(before: Any, after: Any, now: Any) -> Any:
    """*now* senza il cambiamento *before* → *after*, o :class:`_ConflictError`.

    Le tre regole: un pezzo che l'aiutante non ha cambiato resta com'è adesso;
    uno che nessuno ha cambiato dopo di lui torna com'era prima; uno cambiato da
    entrambi è un conflitto, e lascia com'è tutto il file. Gli oggetti si
    guardano chiave per chiave, le liste di oggetti con ``id`` elemento per
    elemento, le altre liste come insiemi (quel che lui ha aggiunto esce, quel
    che ha tolto rientra). ``_MISSING`` è «non c'era».
    """
    if after == before:
        return now
    if now == after:
        return before
    if isinstance(before, dict) and isinstance(after, dict) and isinstance(now, dict):
        out: dict[str, Any] = {}
        for k in [*now, *(k for k in before if k not in now)]:
            v = _revert(before.get(k, _MISSING), after.get(k, _MISSING), now.get(k, _MISSING))
            if v is not _MISSING:
                out[k] = v
        return out
    if (isinstance(before, list) and isinstance(after, list) and isinstance(now, list)
            and _id_list(before) and _id_list(after) and _id_list(now)):
        return _revert_by_id(before, after, now)
    if all(isinstance(v, list) for v in (before, after, now)):
        added = {_key(x) for x in after} - {_key(x) for x in before}
        removed = [x for x in before if _key(x) not in {_key(y) for y in after}]
        present = {_key(x) for x in now}
        return [x for x in now if _key(x) not in added] + [
            x for x in removed if _key(x) not in present]
    raise _ConflictError


def _revert_by_id(before: list, after: list, now: list) -> list:
    by_before = {e["id"]: e for e in before}
    by_after = {e["id"]: e for e in after}
    present = {e["id"] for e in now}
    out: list[Any] = []
    for item in now:
        v = _revert(by_before.get(item["id"], _MISSING), by_after.get(item["id"], _MISSING), item)
        if v is not _MISSING:
            out.append(v)
    # Quel che l'aiutante ha tolto rientra, dopo il vicino che aveva prima.
    for index, item in enumerate(before):
        if item["id"] in present:
            continue
        v = _revert(item, by_after.get(item["id"], _MISSING), _MISSING)
        if v is _MISSING:
            continue
        at = 0
        for prev in reversed(before[:index]):
            spot = next((k for k, x in enumerate(out) if x.get("id") == prev["id"]), None)
            if spot is not None:
                at = spot + 1
                break
        out.insert(at, v)
    return out


#: Un annulla alla volta per processo: due annulla su lineage diversi che
#: toccano lo stesso file devono vedere l'uno il risultato dell'altro.
_UNDO_LOCK = threading.Lock()


def _undo_db(workspace: Path):
    from jenny import db

    database = db.database_for_workspace(workspace, db.UNDO)
    database.require(SCHEMA)
    return database


def objects_dir_for(workspace: Path) -> Path:
    from jenny.db.restore import UNDO_OBJECTS_DIR_NAME
    from jenny.snapshot.locations import db_dir_for

    return db_dir_for(workspace) / UNDO_OBJECTS_DIR_NAME


def _object_path(objects: Path, sha: str) -> Path:
    return objects / sha[:2] / sha


def _read_object(objects: Path, sha: str) -> bytes:
    """Il contenuto salvato di *sha*, verificato: mai un contenuto sbagliato."""
    content = zlib.decompress(_object_path(objects, sha).read_bytes())
    if hashlib.sha256(content).hexdigest() != sha:
        raise ValueError(f"saved content {sha[:12]} does not match its hash")
    return content


def _is_sha(sig: str | None) -> bool:
    return isinstance(sig, str) and len(sig) == 64 and all(c in "0123456789abcdef" for c in sig)


def signature(path: Path) -> str | None:
    """La firma di *path* adesso: sha256, ``None`` se non c'è, ``"dir"``.

    Un link porta il suo bersaglio (``link:<target>``), un file oltre
    :data:`MAX_BLOB_BYTES` peso e data (``big:<size>:<mtime_ns>``): leggerlo
    tutto per l'hash costerebbe quanto copiarlo, e non sarà comunque
    annullabile. Bastano a dire se qualcuno l'ha toccato.
    """
    try:
        st = path.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        return None
    if path.is_symlink():
        try:
            return f"link:{os.readlink(path)}"
        except OSError:
            return "link:?"
    if path.is_dir():
        return DIR
    if st.st_size > MAX_BLOB_BYTES:
        return f"big:{st.st_size}:{st.st_mtime_ns}"
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


@dataclass(slots=True)
class _Pending:
    """Un file toccato nello strumento in corso, in attesa del «dopo»."""

    before: str | None
    content: bytes | None = None
    shared: bool = False
    # L'ora del tocco, non del «dopo»: due registri che scrivono lo stesso file
    # uno dopo l'altro si mettono in fila per quando hanno scritto davvero.
    ts: float = 0.0
    # Il passo del piano per cui si scriveva, se il registro era legato a uno.
    step: str | None = None
    # Una cartella di cui si conosce tutto il contenuto al tocco (nuova, o
    # annotata per intero): al «dopo» ogni file che ci si trova e non era
    # annotato è nato in questo strumento. Una cartella annotata solo in parte
    # (oltre ``MAX_DIR_FILES``) no: un suo file non annotato potrebbe esserci
    # stato già, e l'annulla lo cancellerebbe.
    walk: bool = False


# Chi ha in corso una scrittura su un file, fra tutti i registri vivi: due
# aiutanti che scrivono lo stesso file nello stesso momento non possono sapere
# chi ha fatto cosa, e le loro righe lo dicono (``shared``), così l'annulla non
# ne rimette nessuno.
_OWNERS: dict[str, set[WriteJournal]] = {}
_OWNERS_LOCK = threading.Lock()


@dataclass(slots=True)
class UndoReport:
    """Com'è andato (o andrebbe, con ``dry_run``) un annulla.

    ``restored`` sono i file tornati com'erano, compresi quelli tolti perché
    prima non c'erano (anche in ``removed``) e quelli che lo erano già (anche in
    ``already``). ``kept`` sono quelli lasciati com'erano adesso, col motivo;
    ``excluded`` quelli che non tornano mai (il diario di un quaderno).
    """

    restored: list[str] = field(default_factory=list)
    kept: list[dict[str, str]] = field(default_factory=list)
    by: str = "user"
    at: float = field(default_factory=time.time)
    removed: list[str] = field(default_factory=list)
    already: list[str] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    kept_groups: list[str] = field(default_factory=list)
    token: str | None = None
    dry_run: bool = False
    groups: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "restored": list(self.restored), "kept": [dict(k) for k in self.kept],
            "by": self.by, "at": self.at, "removed": list(self.removed),
            "already": list(self.already), "excluded": list(self.excluded),
            "kept_groups": list(self.kept_groups), "dry_run": self.dry_run,
        }
        if self.token is not None:
            data["token"] = self.token
        if self.groups:
            data["groups"] = dict(self.groups)
            data["counts"] = {k: len(v) for k, v in self.outcomes().items()}
        return data

    @classmethod
    def from_dict(cls, raw: Any) -> UndoReport:
        raw = raw if isinstance(raw, dict) else {}
        kept = [
            {"path": str(k.get("path", "")), "reason": str(k.get("reason", ""))}
            for k in raw.get("kept") or [] if isinstance(k, dict)
        ]

        def paths(name: str) -> list[str]:
            return [str(p) for p in raw.get(name) or [] if isinstance(p, str)]

        groups = raw.get("groups")
        return cls(
            restored=paths("restored"), kept=kept,
            by=str(raw.get("by") or "user"), at=float(raw.get("at") or 0.0),
            removed=paths("removed"), already=paths("already"), excluded=paths("excluded"),
            kept_groups=paths("kept_groups"),
            token=raw.get("token") if isinstance(raw.get("token"), str) else None,
            dry_run=bool(raw.get("dry_run")),
            groups={str(k): str(v) for k, v in groups.items()} if isinstance(groups, dict) else {},
        )

    def outcomes(self) -> dict[str, list[str]]:
        """Il resoconto per esiti separati, come lo legge la scheda di un piano:
        rimessi, tolti, già com'erano, lasciati (cambiati da altri, non salvati),
        tenuti su richiesta, mai rimessi."""
        put_back = set(self.removed) | set(self.already)
        return {
            "restored": [p for p in self.restored if p not in put_back],
            "removed": list(self.removed),
            "already": list(self.already),
            "left": [k["path"] for k in self.kept if k["reason"] != KEPT_GROUP],
            "kept": [k["path"] for k in self.kept if k["reason"] == KEPT_GROUP],
            "excluded": list(self.excluded),
        }

    def summary(self) -> dict[str, Any]:
        """La forma corta per lo snapshot: contatori e i primi file lasciati."""
        return {"restored": len(self.restored), "kept": self.kept[:5],
                "kept_count": len(self.kept), "by": self.by, "at": self.at}

    def text(self) -> str:
        """Il resoconto per Jenny, in inglese."""
        if not self.restored and not self.kept:
            return "There was nothing to undo: the helper had not changed any file."
        lines = [f"Put back {len(self.restored)} file(s) as they were before the helper."]
        if self.kept:
            lines.append(f"Left {len(self.kept)} file(s) as they are now:")
            for k in self.kept[:10]:
                lines.append(f"- {k['path']}: {_KEPT_TEXT.get(k['reason'], k['reason'])}")
            if len(self.kept) > 10:
                lines.append(f"- … and {len(self.kept) - 10} more")
        return "\n".join(lines)


class WriteJournal:
    """Un registro. Uno per processo e per chiave (:func:`journal_for` li
    tiene), condiviso fra i tentativi di un lineage; thread-safe, perché
    ``python_exec`` tocca i file dal suo thread."""

    def __init__(self, install_root: Path, key: str) -> None:
        self.root = Path(install_root).resolve()
        self.key = key
        self.objects = objects_dir_for(self.root)
        self._lock = threading.RLock()
        self._pending: dict[str, _Pending] = {}
        self._pending_bytes = 0
        # File in corso che un altro registro ha toccato nello stesso momento.
        self._shared: set[str] = set()
        # I file che hanno già una riga: il loro «prima» è già salvato (o
        # dichiarato non salvabile), le righe dopo portano solo le firme.
        self._seen: set[str] = set()
        self._paths: list[str] = []
        # Il conto dei file cambiati, letto senza lock dallo snapshot (sul loop):
        # un annulla in corso tiene il lock per tutto il giro, e lo snapshot non
        # deve aspettarlo.
        self._changed = 0
        # Thread di ``python_exec`` di questo lineage ancora in corsa: possono
        # scrivere anche dopo che il task è stato cancellato.
        self._threads = 0
        self._saved_bytes = 0
        self.task_id = ""
        self.attempt = 0
        self._undo_state: dict[str, Any] = {}
        self._only_copies: set[str] = set()
        self._load()

    # -- stato ---------------------------------------------------------------

    def _db(self):
        return _undo_db(self.root)

    def _load(self) -> None:
        for entry in self._read_entries():
            path = entry["path"]
            if path not in self._seen:
                self._note_path(path)
        try:
            rows = self._db().read(
                "SELECT COALESCE(SUM(size), 0) FROM objects WHERE journal = ?", (self.key,))
            self._saved_bytes = int(rows[0][0])
        except sqlite3.Error as e:
            logger.warning("Write journal {} unreadable: {}", self.key, e)

    def _read_entries(self) -> list[dict[str, Any]]:
        try:
            rows = self._db().read(
                "SELECT path, before, after, ts, saved, shared, tool, task_id, attempt, step "
                "FROM entries WHERE journal = ? ORDER BY ts, id", (self.key,))
        except sqlite3.Error as e:
            logger.warning("Write journal {} unreadable: {}", self.key, e)
            return []
        return [_entry(row) for row in rows]

    def _has_object(self, sha: str) -> bool:
        rows = self._db().read(
            "SELECT 1 FROM objects WHERE journal = ? AND sha = ?", (self.key, sha))
        return bool(rows) and _object_path(self.objects, sha).is_file()

    def begin(self, task_id: str, attempt: int) -> None:
        """Un tentativo nuovo del lineage comincia a lavorare.

        Un annulla già fatto non vale più come stato del lineage: chi riparte
        scrive da capo, e la casa non deve continuare a dire «annullato».
        """
        with self._lock:
            self.task_id = task_id
            self.attempt = attempt
        try:
            with self._db().write() as conn:
                conn.execute("DELETE FROM undos WHERE key = ? AND state = 'done'", (self.key,))
        except sqlite3.Error as e:
            logger.warning("Write journal {} could not reset its undo: {}", self.key, e)

    def _note_path(self, key: str) -> None:
        self._seen.add(key)
        self._paths.append(key)
        if not key.endswith("/"):
            self._changed += 1

    @property
    def changed_files(self) -> int:
        """Quanti file (non cartelle) il lineage ha cambiato finora. Senza lock."""
        return self._changed

    @property
    def busy(self) -> bool:
        """Un thread di ``python_exec`` di questo lineage sta ancora girando."""
        return self._threads > 0

    def enter_thread(self) -> None:
        with self._lock:
            self._threads += 1

    def exit_thread(self) -> None:
        """Il thread è finito: quel che ha toccato si annota adesso, perché dopo
        una cancellazione il runner non arriva più al suo «dopo»."""
        with self._lock:
            self._threads = max(0, self._threads - 1)
        self.settle("python_exec")

    # -- annotare ------------------------------------------------------------

    def _rel(self, path: Path) -> str | None:
        try:
            rel = path.relative_to(self.root)
        except ValueError:
            return None
        return rel.as_posix() if rel.parts else None

    def touch(self, path: str | Path, *, tree: bool = False) -> None:
        """Un aiutante sta per scrivere, cancellare o rinominare *path*.

        Da chiamare **prima** della scrittura. La firma di adesso diventa il
        «prima» della riga; se è la prima volta del lineage su questo file se ne
        tiene il contenuto, che si salva solo se lo strumento lo cambia davvero.
        Le cartelle che mancano fra il file e la radice si annotano come nuove,
        così l'annulla le toglie se restano vuote.

        Una cartella che esiste si annota file per file **solo con** *tree*: per
        chi la toglie o la sposta intera (``rmtree``, la sorgente di una
        rinomina) o ci scrive dentro senza passare da un recinto per file (la
        bozza e la pubblicazione di un'app). Senza, un ``makedirs(exist_ok=True)``
        su una cartella piena avrebbe dato all'aiutante tutto quel che altri ci
        scrivevano durante lo strumento — e l'annulla l'avrebbe tolto.

        Non solleva mai: un registro che non riesce ad annotare non deve fermare
        il lavoro, al più rende quel file non annullabile.
        """
        try:
            # La cartella risolta, il nome no: la radice è risolta (``/data/user/0``
            # può essere un link), e un link come ultimo pezzo resta un link.
            raw = os.path.abspath(path)
            target = Path(os.path.realpath(os.path.dirname(raw))) / os.path.basename(raw)
            rel = self._rel(target)
            if rel is None:
                return
            with self._lock:
                self._touch_missing_parents(target)
                if tree and target.is_dir() and not target.is_symlink():
                    self._touch_tree(target)
                else:
                    self._touch_one(target, rel)
        except Exception as e:  # noqa: BLE001 — vedi docstring
            logger.warning("Subagent journal could not note {}: {}", path, e)

    def _touch_missing_parents(self, target: Path) -> None:
        for parent in reversed(target.parents):
            if parent == self.root or self.root not in parent.parents:
                continue
            if not os.path.lexists(parent):
                rel = self._rel(parent)
                if rel is not None:
                    self._touch_one(parent, rel)

    def _touch_tree(self, top: Path) -> None:
        count = 0
        folders: list[str] = []
        complete = True
        for current, dirs, files in os.walk(top):
            base = Path(current)
            rel = self._rel(base)
            if rel is not None:
                self._touch_one(base, rel)
                folders.append(rel)
            for name in files:
                count += 1
                if count > MAX_DIR_FILES:
                    complete = False
                    break
                child = base / name
                child_rel = self._rel(child)
                if child_rel is not None:
                    self._touch_one(child, child_rel)
            if not complete:
                break
            # Un link a una cartella non si segue: il suo contenuto non è qui.
            complete = complete and not any((base / d).is_symlink() for d in dirs)
            dirs[:] = [d for d in dirs if not (base / d).is_symlink()]
        if complete:
            for rel in folders:
                if (pending := self._pending.get(rel)) is not None:
                    pending.walk = True

    def _touch_one(self, target: Path, rel: str) -> None:
        if rel in self._pending:
            return
        before = signature(target)
        content: bytes | None = None
        if (rel not in self._seen or _mergeable(rel)) and _is_sha(before):
            try:
                content = target.read_bytes()
            except OSError:
                content = None
            if content is not None:
                if self._pending_bytes + len(content) > MAX_PENDING_BYTES:
                    content = None
                else:
                    self._pending_bytes += len(content)
        pending = _Pending(before=before, content=content, walk=before is None, ts=time.time(),
                           step=current_step() if current_journal() is self else None)
        self._pending[rel] = pending
        key = str(target)
        with _OWNERS_LOCK:
            owners = _OWNERS.setdefault(key, set())
            others = [j for j in owners if j is not self]
            owners.add(self)
        if others:
            pending.shared = True
            for other in others:
                other._mark_shared(rel)

    def _mark_shared(self, rel: str) -> None:
        # Senza il lock di questo registro: chi chiama tiene il proprio, e due
        # aiutanti che si marcano a vicenda si aspetterebbero per sempre. Un
        # ``add`` su un set è atomico, e ``settle`` lo legge sotto il suo lock.
        self._shared.add(rel)

    def settle(self, tool: str = "") -> int:
        """Misura il «dopo» dei file toccati e annota quelli cambiati.

        Da chiamare dopo ogni strumento (il runner) e alla fine del lavoro. Un
        file toccato e non cambiato (una scrittura rifiutata, un'anteprima) non
        lascia righe. Ritorna quante righe ha scritto.
        """
        with self._lock:
            pending, self._pending = self._pending, {}
            self._pending_bytes = 0
            if not pending:
                return 0
            entries: list[dict[str, Any]] = []
            for rel, p in [*pending.items(), *self._born_inside(pending)]:
                target = self.root / rel
                with _OWNERS_LOCK:
                    owners = _OWNERS.get(str(target))
                    if owners is not None:
                        owners.discard(self)
                        if not owners:
                            _OWNERS.pop(str(target), None)
                after = signature(target)
                shared = p.shared or rel in self._shared
                self._shared.discard(rel)
                if after == p.before:
                    continue
                entry: dict[str, Any] = {
                    "path": rel + ("/" if DIR in (p.before, after) else ""),
                    "before": p.before,
                    "after": after,
                    "ts": p.ts or time.time(),
                    "task_id": self.task_id,
                    "attempt": self.attempt,
                }
                if tool:
                    entry["tool"] = tool
                if p.step:
                    entry["step"] = p.step
                if shared:
                    entry["shared"] = True
                key = entry["path"]
                if key not in self._seen:
                    entry["saved"] = self._save_before(p)
                    self._note_path(key)
                elif _mergeable(key):
                    self._save_before(p)
                if _mergeable(key) and isinstance(after, str) and _is_sha(after):
                    # Il «dopo» della bacheca, per toglierne il cambiamento a pezzi.
                    try:
                        self._save_blob(after, target.read_bytes())
                    except OSError:
                        pass
                entries.append(entry)
            if entries:
                self._append(entries)
            return len(entries)

    def _born_inside(self, pending: dict[str, _Pending]) -> list[tuple[str, _Pending]]:
        """I file e le cartelle nati in questo strumento dentro una cartella
        annotata per intero (:attr:`_Pending.walk`): una cartella nuova copiata
        o rinominata qui, una bozza d'app creata da zero. Non passano uno per
        uno da un recinto, ma prima non c'erano."""
        born: list[tuple[str, _Pending]] = []
        found: set[str] = set()
        for rel, p in pending.items():
            top = self.root / rel
            if not p.walk or top.is_symlink() or not top.is_dir():
                continue
            for current, dirs, files in os.walk(top):
                base = Path(current)
                dirs[:] = [d for d in dirs if not (base / d).is_symlink()]
                for name in [*dirs, *files]:
                    child_rel = self._rel(base / name)
                    if child_rel is None or child_rel in pending or child_rel in found:
                        continue
                    found.add(child_rel)
                    born.append((child_rel, _Pending(before=None, ts=p.ts, step=p.step)))
                    if len(born) >= MAX_DIR_FILES:
                        return born
        return born

    def _save_before(self, p: _Pending) -> bool:
        """Salva il contenuto «prima», se serve e se ci sta. Vero se
        l'annulla potrà rimetterlo."""
        if p.before is None or p.before == DIR:
            return True
        if not _is_sha(p.before) or p.content is None:
            return False
        return self._save_blob(p.before, p.content)

    def _save_blob(self, sha: str, content: bytes) -> bool:
        if self._has_object(sha):
            return True
        if hashlib.sha256(content).hexdigest() != sha:
            # Cambiato fra la firma e la lettura: non è il contenuto di quella firma.
            return False
        if self._saved_bytes + len(content) > MAX_LINEAGE_BYTES:
            return False
        blob = _object_path(self.objects, sha)
        try:
            if not blob.is_file():
                atomic_write(blob, zlib.compress(content), fsync_dir=False)
            with self._db().write() as conn:
                self._ensure_row(conn)
                conn.execute("INSERT OR IGNORE INTO objects(journal, sha, size) VALUES (?, ?, ?)",
                             (self.key, sha, len(content)))
        except (OSError, sqlite3.Error) as e:
            logger.warning("Write journal {} could not save {}: {}", self.key, sha[:12], e)
            return False
        self._saved_bytes += len(content)
        return True

    def _ensure_row(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT INTO journals(key, created_at, task_id, attempt) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET task_id = excluded.task_id, "
            "attempt = excluded.attempt",
            (self.key, time.time(), self.task_id or None, self.attempt or None))

    def _append(self, entries: list[dict[str, Any]]) -> None:
        try:
            with self._db().write() as conn:
                self._ensure_row(conn)
                conn.executemany(
                    "INSERT INTO entries(journal, ts, path, before, after, saved, shared, tool, "
                    "task_id, attempt, step) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [(self.key, e["ts"], e["path"], e.get("before"), e.get("after"),
                      None if "saved" not in e else int(bool(e["saved"])),
                      int(bool(e.get("shared"))), e.get("tool"), e.get("task_id") or None,
                      e.get("attempt"), e.get("step")) for e in entries])
        except sqlite3.Error as e:
            logger.warning("Write journal {} could not write its entries: {}", self.key, e)

    # -- annullare -----------------------------------------------------------

    def undo(self, *, by: str = "user") -> UndoReport:
        """Rimette ogni file com'era prima di questo registro (:func:`undo_set`).

        Dopo, le righe e i contenuti non servono più: resta solo il resoconto,
        finché il lineage non riparte.
        """
        return undo_set([self], by=by)

    def _drop_entries(self) -> None:
        """Dopo un annulla le righe e i contenuti non servono più: resta solo
        la riga dell'annulla col resoconto, finché il lineage non riparte."""
        try:
            with self._db().write() as conn:
                shas = [r[0] for r in conn.execute(
                    "SELECT sha FROM objects WHERE journal = ?", (self.key,))]
                conn.execute("DELETE FROM entries WHERE journal = ?", (self.key,))
                conn.execute("DELETE FROM objects WHERE journal = ?", (self.key,))
            _drop_unreferenced(self.root, shas)
        except sqlite3.Error as e:
            logger.warning("Write journal {} could not drop its entries: {}", self.key, e)
        self._seen.clear()
        self._paths.clear()
        self._changed = 0
        self._saved_bytes = 0


def _token(current: dict[str, str | None]) -> str:
    """Il gettone di un'anteprima: le firme di adesso dei file che toccherebbe."""
    payload = json.dumps(sorted(current.items()), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


# Gli esiti di un file in un annulla (``_UndoRun._decide``).
_EXCLUDED = "excluded"
_ALREADY = "already"
_MERGED_BEFORE = "merged_before"
_KEEP = "keep"
_MERGE = "merge"
_REMOVE = "remove"
_RESTORE = "restore"


def undo_set(
    journals: list[WriteJournal], *, by: str = "user", into: WriteJournal | None = None,
    keep_groups: Iterable[str] = (), group_of: Any = None, never: Any = None,
    dry_run: bool = False, token: str | None = None,
) -> UndoReport:
    """Rimette i file com'erano prima di *journals*, se nessun altro li ha toccati.

    Le righe di tutti i registri dati, per file e in ordine di ora, fanno una
    catena sola: due aiutanti dello stesso piano sullo stesso file si annullano
    insieme. Per ogni catena: se una riga non comincia dove finiva la precedente
    qualcuno è passato in mezzo (``changed_during``); se il file adesso non è
    com'era all'ultima riga qualcuno è passato dopo (``changed_after``). In
    entrambi i casi il file resta (la bacheca si prova a pezzi). Altrimenti si
    rimette il primo «prima»: il contenuto salvato, o niente se non c'era. Prima
    i file, poi le cartelle dalla più profonda.

    - *group_of* (percorso → gruppo) e *keep_groups*: i file dei gruppi tenuti
      restano com'erano adesso (``kept_group``);
    - *never*: i percorsi che non tornano mai (``excluded``);
    - *dry_run*: il resoconto senza toccare niente, col ``token`` delle firme di
      adesso; passato alla conferma, se i file sono cambiati in mezzo
      :class:`UndoConflictError` e niente si tocca;
    - *into*: il registro dell'annulla stesso. Ogni file rimesso ci lascia la
      sua riga, così annullare l'annulla è ``undo_set([into])``. Con *into* i
      registri annullati restano (il piano può tornarci); senza, le loro righe
      se ne vanno, e resta solo il resoconto.

    Idempotente: un file che è già com'era conta come rimesso. Lo stato
    ``running`` si scrive prima del primo file, così un kill a metà si finisce al
    boot (:func:`finish_interrupted_undos`) con le stesse scelte.
    """
    journals = list(journals)
    if not journals:
        raise ValueError("nothing to undo")
    if len(journals) > 1 and into is None and not dry_run:
        raise ValueError("an undo of several journals needs a journal of its own (into)")
    run = _UndoRun(journals, by=by, into=into, keep_groups=frozenset(keep_groups),
                   group_of=group_of, never=never)
    held = sorted({j.key: j for j in [*journals, *([into] if into else [])]}.items())
    with _UNDO_LOCK, ExitStack() as stack:
        for _key, journal in held:
            stack.enter_context(journal._lock)
        return run.go(dry_run=dry_run, token=token)


class _UndoRun:
    """Un annulla, dal conto delle catene al resoconto."""

    def __init__(self, journals: list[WriteJournal], *, by: str, into: WriteJournal | None,
                 keep_groups: frozenset[str], group_of: Any, never: Any) -> None:
        self.journals = journals
        self.root = journals[0].root
        self.objects = journals[0].objects
        self.keys = [j.key for j in journals]
        self.by = by
        self.into = into
        self.state_key = into.key if into is not None else self.keys[0]
        self.keep_groups = keep_groups
        self.group_of = group_of
        self.never = never
        self.frozen: dict[str, str] = {}
        self._undo_state: dict[str, Any] = {}
        self._only_copies: set[str] = set()

    def _in(self) -> str:
        return ",".join("?" * len(self.keys))

    def entries(self) -> list[dict[str, Any]]:
        rows = _undo_db(self.root).read(
            "SELECT path, before, after, ts, saved, shared, tool, task_id, attempt, step "
            f"FROM entries WHERE journal IN ({self._in()}) ORDER BY ts, id", self.keys)
        return [_entry(row) for row in rows]

    def _has_object(self, sha: str) -> bool:
        rows = _undo_db(self.root).read(
            f"SELECT 1 FROM objects WHERE sha = ? AND journal IN ({self._in()}) LIMIT 1",
            (sha, *self.keys))
        return bool(rows) and _object_path(self.objects, sha).is_file()

    # -- il giro --------------------------------------------------------------

    def go(self, *, dry_run: bool, token: str | None) -> UndoReport:
        entries = self.entries()
        previous = read_undo_state(self.root, self.state_key) or {}
        resuming = previous.get("state") == "running"
        if not entries and previous.get("state") == "done" and not dry_run:
            # Già annullato (Jenny e il tuo tocco, o due finestre): il resoconto
            # vero resta quello, non uno vuoto.
            return UndoReport.from_dict(previous)
        chains: dict[str, list[dict[str, Any]]] = {}
        for entry in entries:
            chains.setdefault(entry["path"], []).append(entry)
        files = [p for p in chains if not p.endswith("/")]
        dirs = sorted((p for p in chains if p.endswith("/")),
                      key=lambda p: p.count("/"), reverse=True)
        if resuming and isinstance(previous.get("frozen"), dict):
            # Le scelte fatte alla partenza (gruppi tenuti, percorsi esclusi): il
            # boot che finisce l'annulla non ha più chi le aveva chieste.
            self.frozen = {str(k): str(v) for k, v in previous["frozen"].items()}
        else:
            self.frozen = {key: reason for key in files
                           if (reason := self._choice(key)) is not None}
        # Le firme dei file che non si possono rimettere (troppo grandi, oltre i
        # tetti): un file nato dentro questi registri con una di queste firme è la
        # loro sola copia (una rinomina), e non si cancella.
        self._only_copies = {
            str(chain[0].get("before")) for chain in chains.values()
            if chain[0].get("before") not in (None, DIR)
            and not (_is_sha(chain[0].get("before"))
                     and self._has_object(str(chain[0].get("before"))))
        }
        merged = [k for k in previous.get("merged") or [] if isinstance(k, str)] \
            if resuming else []
        now = {key: signature(self.root / key) for key in files}
        current = _token(now)
        report = UndoReport(by=self.by, at=time.time(), dry_run=dry_run,
                            kept_groups=sorted(self.keep_groups))
        if dry_run:
            for key in files:
                outcome, reason = self._decide(key, chains[key], now[key], merged)
                self._count(report, key, outcome, reason)
            report.token = current
            return report
        if token is not None and token != current:
            raise UndoConflictError("the files changed since the preview")
        self._undo_state = {"state": "running", "by": self.by, "started_at": report.at,
                            "merged": merged, "frozen": self.frozen}
        self._write_state(self._undo_state)
        with bind_journal(self.into):
            # Le cartelle che c'erano e sono sparite si rifanno prima dei file.
            for key in sorted(dirs, key=lambda p: p.count("/")):
                if chains[key][0].get("before") == DIR:
                    try:
                        (self.root / key.rstrip("/")).mkdir(parents=True, exist_ok=True)
                    except OSError:
                        pass
            for key in files:
                self._undo_file(key, chains[key], report, resuming=resuming)
            for key in dirs:
                self._undo_dir(key, chains[key])
        self._write_state({"state": "done", **report.to_dict()})
        if self.into is None:
            for journal in self.journals:
                journal._drop_entries()
        return report

    def _choice(self, key: str) -> str | None:
        if self.never is not None and self.never(key):
            return KEPT_EXCLUDED
        if self.group_of is not None and self.group_of(key) in self.keep_groups:
            return KEPT_GROUP
        return None

    def _decide(self, key: str, chain: list[dict[str, Any]], now: str | None,
                merged: list[str]) -> tuple[str, str | None]:
        """Cosa succede a *key*, senza toccarlo."""
        if self.frozen.get(key) == KEPT_EXCLUDED:
            return _EXCLUDED, None
        first = chain[0].get("before")
        if key in merged:
            return _MERGED_BEFORE, None
        if now == first:
            return _ALREADY, None
        if self.frozen.get(key) == KEPT_GROUP:
            return _KEEP, KEPT_GROUP
        reason = self._verdict(chain, now)
        if reason is not None:
            if _mergeable(key) and self._merged_board(key, chain) is not None:
                return _MERGE, None
            return _KEEP, reason
        if first is None:
            if now in self._only_copies:
                # Spostato qui da un file che non si può rimettere: cancellarlo
                # perderebbe l'unica copia.
                return _KEEP, KEPT_NOT_SAVED
            return _REMOVE, None
        if _is_sha(first) and self._has_object(first):
            return _RESTORE, None
        return _KEEP, KEPT_NOT_SAVED

    def _count(self, report: UndoReport, key: str, outcome: str, reason: str | None) -> None:
        if self.group_of is not None:
            report.groups[key] = str(self.group_of(key))
        if outcome == _EXCLUDED:
            report.excluded.append(key)
        elif outcome == _KEEP:
            report.kept.append({"path": key, "reason": reason or KEPT_ERROR})
        else:
            report.restored.append(key)
            if outcome == _REMOVE:
                report.removed.append(key)
            elif outcome == _ALREADY:
                report.already.append(key)

    def _verdict(self, chain: list[dict[str, Any]], now: str | None) -> str | None:
        """Il motivo per lasciare il file com'è, o ``None`` se si può rimettere."""
        if any(e.get("shared") for e in chain):
            return KEPT_CHANGED_DURING
        for prev, cur in zip(chain, chain[1:]):
            if cur.get("before") != prev.get("after"):
                return KEPT_CHANGED_DURING
        if now != chain[-1].get("after"):
            return KEPT_CHANGED_AFTER
        return None

    # -- scrivere -------------------------------------------------------------

    def _undo_file(self, key: str, chain: list[dict[str, Any]], report: UndoReport, *,
                   resuming: bool) -> None:
        target = self.root / key
        now = signature(target)
        outcome, reason = self._decide(key, chain, now, self._undo_state["merged"])
        if outcome == _ALREADY and resuming:
            self._repair(key, chain)
        if outcome in (_EXCLUDED, _ALREADY, _MERGED_BEFORE, _KEEP):
            self._count(report, key, outcome, reason)
            return
        try:
            if self.into is not None:
                self.into.touch(target)
            if outcome == _MERGE:
                if not self._merge_back(key, chain):
                    self._count(report, key, _KEEP, self._verdict(chain, now) or KEPT_ERROR)
                    return
                self._undo_state["merged"].append(key)
                self._write_state(self._undo_state)
            elif outcome == _REMOVE:
                if target.is_symlink() or target.is_file():
                    target.unlink()
            else:
                content = _read_object(self.objects, str(chain[0].get("before")))
                target.parent.mkdir(parents=True, exist_ok=True)
                atomic_write(target, content)
        except (OSError, ValueError, zlib.error) as e:
            logger.warning("Undo could not restore {}: {}", target, e)
            self._count(report, key, _KEEP, KEPT_ERROR)
            return
        finally:
            if self.into is not None:
                self.into.settle("undo")
        self._count(report, key, outcome, None)

    def _repair(self, key: str, chain: list[dict[str, Any]]) -> None:
        """Un file rimesso da un annulla poi ucciso prima della sua riga: la riga
        si scrive adesso, o annullare l'annulla non lo vedrebbe. Il contenuto di
        prima dell'annulla non c'è più, quindi la riga dice «non salvato»."""
        into = self.into
        first, last = chain[0].get("before"), chain[-1].get("after")
        if into is None or last == first:
            return
        if any(e["path"] == key for e in into._read_entries()):
            return
        with into._lock:
            into._append([{"path": key, "before": last, "after": first, "ts": time.time(),
                           "saved": False, "tool": "undo"}])
            into._note_path(key)

    def _merged_board(self, key: str, chain: list[dict[str, Any]]) -> Any:
        """La bacheca di adesso senza i cambiamenti dei registri, o ``None``."""
        if any(e.get("shared") for e in chain):
            return None
        try:
            current = json.loads((self.root / key).read_text(encoding="utf-8"))
            return self._reverted(chain)(current)
        except (_ConflictError, OSError, ValueError, zlib.error):
            return None

    def _reverted(self, chain: list[dict[str, Any]]):
        pairs = [(self._blob_json(e.get("before")), self._blob_json(e.get("after")))
                 for e in chain]

        def change(current: Any) -> Any:
            if current is None:
                raise _ConflictError
            for before, after in reversed(pairs):
                current = _revert(before, after, current)
            return current

        return change

    def _merge_back(self, key: str, chain: list[dict[str, Any]]) -> bool:
        """Toglie dalla bacheca di adesso i cambiamenti dei registri, a pezzi.

        Una scrittura alla volta, dall'ultima alla prima: ognuna toglie solo il
        suo «prima → dopo», quindi quel che altri hanno scritto in mezzo resta.
        Vero se l'ha fatto; falso (file com'è) se manca un contenuto, se il file
        non c'era prima o non c'è adesso, o se un pezzo è conteso.
        """
        if any(e.get("shared") for e in chain):
            return False
        try:
            change = self._reverted(chain)
        except (OSError, ValueError, zlib.error):
            return False
        from jenny.board.store import rewrite_json

        try:
            rewrite_json(self.root / key, change)
        except (_ConflictError, OSError, ValueError):
            return False
        return True

    def _blob_json(self, sig: Any) -> Any:
        if not _is_sha(sig):
            raise ValueError("no content for this version")
        return json.loads(_read_object(self.objects, str(sig)).decode("utf-8"))

    def _undo_dir(self, key: str, chain: list[dict[str, Any]]) -> None:
        # Una cartella nuova si toglie se è rimasta vuota; una che c'era è già
        # stata rifatta in testa. Le cartelle non entrano nel conto dei file: la
        # casa dice «N file», e una cartella vuota che resta non è lavoro perso.
        if chain[0].get("before") is not None:
            return
        target = self.root / key.rstrip("/")
        try:
            if self.into is not None and target.is_dir() and not any(target.iterdir()):
                self.into.touch(target)
            target.rmdir()
        except OSError:
            pass
        finally:
            if self.into is not None:
                self.into.settle("undo")

    def _write_state(self, state: dict[str, Any]) -> None:
        report = {k: v for k, v in state.items() if k not in ("state", "merged", "started_at")}
        try:
            with _undo_db(self.root).write() as conn:
                conn.execute(
                    "INSERT INTO undos(key, sources, state, started_at, report, merged) "
                    "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
                    "sources = excluded.sources, state = excluded.state, "
                    "started_at = excluded.started_at, report = excluded.report, "
                    "merged = excluded.merged",
                    (self.state_key, json.dumps(self.keys), state.get("state", ""),
                     state.get("started_at"), json.dumps(report, ensure_ascii=False),
                     json.dumps(state.get("merged") or [])))
        except sqlite3.Error as e:
            logger.warning("Undo {} could not write its state: {}", self.state_key, e)


@dataclass(frozen=True, slots=True)
class _Binding:
    journal: WriteJournal | None
    step: str | None


_CURRENT: ContextVar[_Binding | None] = ContextVar("jenny_write_journal", default=None)


def current_journal() -> WriteJournal | None:
    """Il registro legato a questo lavoro, se c'è."""
    binding = _CURRENT.get()
    return binding.journal if binding is not None else None


def current_step() -> str | None:
    """Il passo del piano del registro legato, se è legato a uno."""
    binding = _CURRENT.get()
    return binding.step if binding is not None else None


@contextmanager
def bind_journal(journal: WriteJournal | None, *, step: str | None = None) -> Iterator[None]:
    """Le scritture fatte qui dentro vanno in *journal* (``None``: in nessuno).

    ``None`` serve a chi parte da un contesto copiato e non deve ereditare per
    caso il registro di un altro (un task nato da un thread).
    """
    token = _CURRENT.set(_Binding(journal, step))
    try:
        yield
    finally:
        _CURRENT.reset(token)


def _entry(row: sqlite3.Row) -> dict[str, Any]:
    """Una riga del diario come dizionario, con le chiavi di sempre."""
    entry: dict[str, Any] = {"path": row["path"], "before": row["before"],
                             "after": row["after"], "ts": row["ts"]}
    for name in ("task_id", "attempt", "tool", "step"):
        if row[name] is not None:
            entry[name] = row[name]
    if row["saved"] is not None:
        entry["saved"] = bool(row["saved"])
    if row["shared"]:
        entry["shared"] = True
    return entry


def _drop_unreferenced(workspace: Path, shas: Iterable[str]) -> None:
    """Toglie i contenuti salvati che nessun registro nomina più."""
    database = _undo_db(workspace)
    objects = objects_dir_for(workspace)
    for sha in set(shas):
        if not database.read("SELECT 1 FROM objects WHERE sha = ? LIMIT 1", (sha,)):
            _object_path(objects, sha).unlink(missing_ok=True)


# I registri vivi del processo, uno per chiave: il manager e lo strumento
# ``app`` (che annota nel registro dell'app-builder la pubblicazione della sua
# bozza) devono scrivere nello stesso oggetto, o i conti in memoria divergono.
_LIVE: dict[tuple[str, str], WriteJournal] = {}
_LIVE_LOCK = threading.Lock()


def journal_for(install_root: Path, key: str) -> WriteJournal:
    """Il registro vivo di *key*, creato (e riletto dal database) alla prima richiesta."""
    live = (str(Path(install_root).resolve()), key)
    with _LIVE_LOCK:
        journal = _LIVE.get(live)
        if journal is None:
            journal = _LIVE[live] = WriteJournal(install_root, key)
        return journal


def live_journal(install_root: Path, key: str) -> WriteJournal | None:
    """Il registro vivo di *key*, se questo processo ne ha uno."""
    with _LIVE_LOCK:
        return _LIVE.get((str(Path(install_root).resolve()), key))


def read_undo_state(install_root: Path, key: str) -> dict[str, Any] | None:
    """L'ultimo annulla di *key*: ``{"state", "by", "started_at", "merged", …resoconto}``."""
    try:
        rows = _undo_db(install_root).read(
            "SELECT state, started_at, report, merged FROM undos WHERE key = ?", (key,))
    except sqlite3.Error:
        return None
    if not rows:
        return None
    row = rows[0]
    try:
        report = json.loads(row["report"] or "{}")
        merged = json.loads(row["merged"] or "[]")
    except ValueError:
        report, merged = {}, []
    out: dict[str, Any] = dict(report) if isinstance(report, dict) else {}
    out.update(state=row["state"], started_at=row["started_at"],
               merged=merged if isinstance(merged, list) else [])
    return out


def finish_interrupted_undos(install_root: Path) -> int:
    """Al boot: finisce gli annulla che un kill ha lasciato a metà. Ritorna quanti.

    L'annulla è idempotente (un file già rimesso conta come rimesso), quindi
    finirlo è rifarlo, con le scelte fatte alla partenza (``frozen``).
    Best-effort: un boot non fallisce per questo.
    """
    try:
        rows = _undo_db(install_root).read(
            "SELECT key, sources, report FROM undos WHERE state = 'running'")
    except sqlite3.Error as e:
        logger.warning("Could not read interrupted undos: {}", e)
        return 0
    finished = 0
    for row in rows:
        try:
            by = str((json.loads(row["report"] or "{}") or {}).get("by") or "user")
            sources = [str(k) for k in json.loads(row["sources"] or "[]")]
        except (ValueError, AttributeError):
            by, sources = "user", []
        key = row["key"]
        try:
            if not sources or sources == [key]:
                WriteJournal(install_root, key).undo(by=by)
            else:
                undo_set([WriteJournal(install_root, k) for k in sources], by=by,
                         into=journal_for(install_root, key))
            finished += 1
        except Exception as e:  # noqa: BLE001 — vedi docstring
            logger.warning("Could not finish interrupted undo {}: {}", key, e)
    return finished


def sweep_orphan_journals(install_root: Path, keep: Iterable[str],
                          keep_keys: Iterable[str] = ()) -> int:
    """Toglie i registri degli aiutanti che non hanno più record. Ritorna quanti.

    La vita del registro di un aiutante è quella dei record del suo lineage (TTL
    e potatura di ``subagent_records``): senza record nessuno può più chiedere
    l'annulla, e le copie sono solo spazio. *keep* sono i lineage vivi;
    *keep_keys* i registri che qualcun altro nomina ancora (gli aiutanti dei
    passi di un piano, che il piano può riportare indietro). I registri che non
    sono di un aiutante (``plan-*``, ``undo-*``) li toglie chi li ha creati. Poi
    i contenuti salvati che nessuna riga nomina più.
    """
    alive = {journal_key(HELPER, k) for k in keep} | set(keep_keys)
    prefix = f"{HELPER}-"
    try:
        database = _undo_db(install_root)
        known = {r[0] for r in database.read("SELECT key FROM journals")} | {
            r[0] for r in database.read("SELECT key FROM undos")}
        gone = sorted(k for k in known if k.startswith(prefix) and k not in alive)
        if gone:
            with database.write() as conn:
                for key in gone:
                    conn.execute("DELETE FROM journals WHERE key = ?", (key,))
                    conn.execute("DELETE FROM undos WHERE key = ?", (key,))
        referenced = {r[0] for r in database.read("SELECT DISTINCT sha FROM objects")}
    except sqlite3.Error as e:
        logger.warning("Could not sweep the write journals: {}", e)
        return 0
    objects = objects_dir_for(install_root)
    if objects.is_dir():
        for shard in objects.iterdir():
            if not shard.is_dir():
                continue
            for blob in shard.iterdir():
                if blob.name not in referenced:
                    blob.unlink(missing_ok=True)
    with _LIVE_LOCK:
        for live in [k for k in _LIVE if k[1].startswith(prefix) and k[1] not in alive]:
            del _LIVE[live]
    return len(gone)


def journal_stats(install_root: Path, key: str) -> tuple[int, UndoReport | None]:
    """File cambiati e ultimo annulla di un registro, letti dal database."""
    try:
        rows = _undo_db(install_root).read(
            "SELECT COUNT(DISTINCT path) FROM entries WHERE journal = ? AND path NOT LIKE '%/'",
            (key,))
    except sqlite3.Error:
        return 0, None
    state = read_undo_state(install_root, key)
    report = UndoReport.from_dict(state) if state and state.get("state") == "done" else None
    return int(rows[0][0]), report
