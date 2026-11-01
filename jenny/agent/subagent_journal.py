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

**Chi lo tiene.** Il codice, mai il modello. :class:`WriteJournal` sta sulla
politica del lavoro (``security.subagent_policy.SubagentRunPolicy.journal``),
che ``SubagentManager._run_subagent`` lega a una ``ContextVar`` e che arriva
anche nei thread di ``python_exec``. I recinti di scrittura chiamano
:meth:`WriteJournal.touch` subito prima di scrivere (``subagent_policy.check_write``
e ``journal_touch``), e il runner chiama :meth:`WriteJournal.settle` dopo ogni
strumento: lì si misura il «dopo» e si annota la riga. Fuori da un aiutante non
c'è politica, e niente di questo modulo gira: Jenny principale, il giardiniere,
Dream e l'utente non hanno registro.

**La chiave è il lineage**, non il tentativo: un aiutante fatto continuare dopo
un budget, un 429 o un riavvio è lo stesso lavoro, e l'annulla toglie tutto ciò
che il lineage ha scritto.

**Su disco**, sotto ``<installazione>/subagents/journal/<lineage>/``:

* ``entries.jsonl`` — una riga per file cambiato in uno strumento:
  ``{"path", "before", "after", "ts", "task_id", "attempt", "tool"}``. ``before``
  e ``after`` sono firme (:func:`signature`): lo sha256 del contenuto, ``None``
  se il file non c'era, ``"dir"`` per una cartella. JSONL perché una riga
  troncata da un kill si salta e il resto resta leggibile.
* ``blobs/<sha>`` — il contenuto «prima», salvato **solo alla prima** scrittura
  del lineage su quel file. Le scritture dopo annotano solo le firme, che bastano
  a riconoscere chi è passato in mezzo. Un ``blobs/`` semplice e non l'archivio
  dello ``SnapshotEngine``: la vita del registro è quella del lineage, e
  cancellarlo è togliere una cartella, senza contare riferimenti in uno store
  condiviso.
* ``undo.json`` — lo stato dell'annulla: ``running`` mentre rimette i file (al
  boot si finisce, :func:`finish_interrupted_undos`), ``done`` col resoconto.

La cartella è SECRET per gli aiutanti (``subagent_policy._SECRET_DIRS``) ed
esclusa dalle foto e dai backup (``snapshot.engine.ALWAYS_EXCLUDED_DIRS``): sono
copie temporanee, e un aiutante non deve né leggere i «prima» di un altro né
riscrivere il proprio registro.

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
import shutil
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.utils.helpers import safe_filename
from jenny.utils.path import atomic_write

#: Dove stanno i registri, relativo alla radice dell'installazione.
JOURNAL_REL = ("subagents", "journal")

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

_KEPT_TEXT = {
    KEPT_CHANGED_AFTER: "changed by someone else after the helper",
    KEPT_CHANGED_DURING: "changed by someone else while the helper was working",
    KEPT_NOT_SAVED: "its earlier content was not saved (too large, or the journal was full)",
    KEPT_ERROR: "could not be written back",
}

_MISSING: Any = object()


class _ConflictError(Exception):
    """Lo stesso pezzo cambiato dall'aiutante e, dopo, da qualcun altro."""


def _mergeable(rel: str) -> bool:
    """La bacheca di un quaderno: JSON che si annulla per pezzi."""
    parts = rel.split("/")
    return (len(parts) == 4 and parts[0] == "wikis" and parts[2] == "board"
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


_ENTRIES = "entries.jsonl"
_UNDO = "undo.json"
_BLOBS = "blobs"

#: Un annulla alla volta per processo: due annulla su lineage diversi che
#: toccano lo stesso file devono vedere l'uno il risultato dell'altro.
_UNDO_LOCK = threading.Lock()


def journal_root(install_root: Path) -> Path:
    return Path(install_root).joinpath(*JOURNAL_REL)


def journal_dir(install_root: Path, lineage_id: str) -> Path:
    return journal_root(install_root) / (safe_filename(lineage_id) or "_")


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
    """Com'è andato un annulla: rimessi, lasciati (col motivo), chi e quando."""

    restored: list[str] = field(default_factory=list)
    kept: list[dict[str, str]] = field(default_factory=list)
    by: str = "user"
    at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {"restored": list(self.restored), "kept": [dict(k) for k in self.kept],
                "by": self.by, "at": self.at}

    @classmethod
    def from_dict(cls, raw: Any) -> UndoReport:
        raw = raw if isinstance(raw, dict) else {}
        kept = [
            {"path": str(k.get("path", "")), "reason": str(k.get("reason", ""))}
            for k in raw.get("kept") or [] if isinstance(k, dict)
        ]
        return cls(
            restored=[str(p) for p in raw.get("restored") or [] if isinstance(p, str)],
            kept=kept,
            by=str(raw.get("by") or "user"),
            at=float(raw.get("at") or 0.0),
        )

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
    """Il registro di un lineage. Uno per processo e per lineage (il manager li
    tiene), condiviso fra i suoi tentativi; thread-safe, perché ``python_exec``
    tocca i file dal suo thread."""

    def __init__(self, install_root: Path, lineage_id: str) -> None:
        self.root = Path(install_root).resolve()
        self.lineage_id = lineage_id
        self.dir = journal_dir(self.root, lineage_id)
        self._skip = journal_root(self.root).resolve()
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

    def _load(self) -> None:
        for entry in self._read_entries():
            path = entry["path"]
            if path not in self._seen:
                self._note_path(path)
        blobs = self.dir / _BLOBS
        if blobs.is_dir():
            try:
                self._saved_bytes = sum(p.stat().st_size for p in blobs.iterdir() if p.is_file())
            except OSError:
                pass

    def _read_entries(self) -> list[dict[str, Any]]:
        path = self.dir / _ENTRIES
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except OSError as e:
            logger.warning("Subagent journal unreadable {}: {}", path, e)
            return []
        out: list[dict[str, Any]] = []
        for line in raw.splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict) and isinstance(entry.get("path"), str):
                out.append(entry)
        return out

    def begin(self, task_id: str, attempt: int) -> None:
        """Un tentativo nuovo del lineage comincia a lavorare.

        Un annulla già fatto non vale più come stato del lineage: chi riparte
        scrive da capo, e la casa non deve continuare a dire «annullato».
        """
        with self._lock:
            self.task_id = task_id
            self.attempt = attempt
        undo = self.dir / _UNDO
        state = read_undo_state(self.dir)
        if state is not None and state.get("state") == "done":
            try:
                undo.unlink()
            except OSError:
                pass

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
        if path == self._skip or self._skip in path.parents:
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
        pending = _Pending(before=before, content=content, walk=before is None)
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
                    "ts": time.time(),
                    "task_id": self.task_id,
                    "attempt": self.attempt,
                }
                if tool:
                    entry["tool"] = tool
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
                    born.append((child_rel, _Pending(before=None)))
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
        blob = self.dir / _BLOBS / sha
        if blob.exists():
            return True
        if hashlib.sha256(content).hexdigest() != sha:
            # Cambiato fra la firma e la lettura: non è il contenuto di quella firma.
            return False
        if self._saved_bytes + len(content) > MAX_LINEAGE_BYTES:
            return False
        try:
            atomic_write(blob, content, fsync_dir=False)
        except OSError as e:
            logger.warning("Subagent journal could not save {}: {}", blob, e)
            return False
        self._saved_bytes += len(content)
        return True

    def _append(self, entries: list[dict[str, Any]]) -> None:
        path = self.dir / _ENTRIES
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
        except OSError as e:
            logger.warning("Subagent journal could not write {}: {}", path, e)

    # -- annullare -----------------------------------------------------------

    def undo(self, *, by: str = "user") -> UndoReport:
        """Rimette ogni file com'era prima del lineage, se nessun altro l'ha toccato.

        Per ogni file, la catena delle sue righe: se una riga non comincia dove
        finiva la precedente qualcuno è passato in mezzo (``changed_during``); se
        il file adesso non è com'era all'ultima riga qualcuno è passato dopo
        (``changed_after``). In entrambi i casi il file resta. Altrimenti si
        rimette il primo «prima»: il contenuto salvato, o niente se non c'era.
        Prima i file, poi le cartelle dalla più profonda: una cartella nuova si
        toglie solo se è rimasta vuota.

        Idempotente: un file che è già com'era conta come rimesso. È ciò che
        permette di finire al boot un annulla interrotto (``undo.json``).
        """
        with _UNDO_LOCK, self._lock:
            entries = self._read_entries()
            previous = read_undo_state(self.dir) or {}
            if not entries and previous.get("state") == "done":
                # Già annullato (Jenny e il tuo tocco, o due finestre): il
                # resoconto vero resta quello, non uno vuoto.
                return UndoReport.from_dict(previous)
            started = time.time()
            # Le bacheche già fuse da un giro interrotto: rifonderle darebbe un
            # conflitto finto (``_revert`` vede «prima» dove aspetta «dopo»).
            merged = [k for k in previous.get("merged") or [] if isinstance(k, str)] \
                if previous.get("state") == "running" else []
            self._undo_state = {"state": "running", "by": by, "started_at": started,
                                "merged": merged}
            self._write_undo_state(self._undo_state)
            report = UndoReport(by=by, at=started)
            chains: dict[str, list[dict[str, Any]]] = {}
            for entry in entries:
                chains.setdefault(entry["path"], []).append(entry)
            # Le firme dei file che non si possono rimettere (troppo grandi, oltre
            # i tetti): un file nato dall'aiutante con una di queste firme è la
            # loro sola copia (una rinomina), e non si cancella.
            self._only_copies = {
                str(chain[0].get("before")) for chain in chains.values()
                if chain[0].get("before") not in (None, DIR)
                and not (_is_sha(chain[0].get("before"))
                         and (self.dir / _BLOBS / str(chain[0].get("before"))).is_file())
            }
            files = [p for p in chains if not p.endswith("/")]
            dirs = sorted((p for p in chains if p.endswith("/")),
                          key=lambda p: p.count("/"), reverse=True)
            # Le cartelle che c'erano e sono sparite si rifanno prima dei file.
            for key in sorted(dirs, key=lambda p: p.count("/")):
                if chains[key][0].get("before") == DIR:
                    try:
                        (self.root / key.rstrip("/")).mkdir(parents=True, exist_ok=True)
                    except OSError:
                        pass
            for key in files:
                self._undo_file(key, chains[key], report)
            for key in dirs:
                self._undo_dir(key, chains[key], report)
            self._write_undo_state({"state": "done", **report.to_dict()})
            self._drop_entries()
            return report

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

    def _undo_file(self, key: str, chain: list[dict[str, Any]], report: UndoReport) -> None:
        target = self.root / key
        first = chain[0].get("before")
        now = signature(target)
        if now == first or key in self._undo_state.get("merged", ()):
            report.restored.append(key)
            return
        reason = self._verdict(chain, now)
        if reason is not None and _mergeable(key) and self._merge_back(key, chain):
            self._undo_state["merged"].append(key)
            self._write_undo_state(self._undo_state)
            report.restored.append(key)
            return
        if reason is not None:
            report.kept.append({"path": key, "reason": reason})
            return
        try:
            if first is None:
                if now in self._only_copies:
                    # Spostato qui da un file che non si può rimettere:
                    # cancellarlo perderebbe l'unica copia.
                    report.kept.append({"path": key, "reason": KEPT_NOT_SAVED})
                    return
                if target.is_symlink() or target.is_file():
                    target.unlink()
            elif _is_sha(first) and (blob := self.dir / _BLOBS / first).is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                atomic_write(target, blob.read_bytes())
            else:
                report.kept.append({"path": key, "reason": KEPT_NOT_SAVED})
                return
        except OSError as e:
            logger.warning("Subagent undo could not restore {}: {}", target, e)
            report.kept.append({"path": key, "reason": KEPT_ERROR})
            return
        report.restored.append(key)

    def _merge_back(self, key: str, chain: list[dict[str, Any]]) -> bool:
        """Toglie dalla bacheca di adesso i cambiamenti dell'aiutante, a pezzi.

        Una scrittura alla volta, dall'ultima alla prima: ognuna toglie solo il
        suo «prima → dopo», quindi quel che altri hanno scritto in mezzo resta.
        Vero se l'ha fatto; falso (file com'è) se manca un contenuto, se il file
        non c'era prima o non c'è adesso, o se un pezzo è conteso.
        """
        if any(e.get("shared") for e in chain):
            return False
        try:
            pairs = [(self._blob_json(e.get("before")), self._blob_json(e.get("after")))
                     for e in chain]
        except (OSError, ValueError):
            return False
        from jenny.board.store import rewrite_json

        def change(current: Any) -> Any:
            if current is None:
                raise _ConflictError
            for before, after in reversed(pairs):
                current = _revert(before, after, current)
            return current

        try:
            rewrite_json(self.root / key, change)
        except (_ConflictError, OSError, ValueError):
            return False
        return True

    def _blob_json(self, sig: Any) -> Any:
        if not _is_sha(sig):
            raise ValueError("no content for this version")
        return json.loads((self.dir / _BLOBS / str(sig)).read_text(encoding="utf-8"))

    def _undo_dir(self, key: str, chain: list[dict[str, Any]], report: UndoReport) -> None:
        # Una cartella nuova si toglie se è rimasta vuota; una che c'era è già
        # stata rifatta in testa. Le cartelle non entrano nel conto dei file: la
        # casa dice «N file», e una cartella vuota che resta non è lavoro perso.
        if chain[0].get("before") is not None:
            return
        try:
            (self.root / key.rstrip("/")).rmdir()
        except OSError:
            pass

    def _write_undo_state(self, state: dict[str, Any]) -> None:
        try:
            atomic_write(self.dir / _UNDO, json.dumps(state, ensure_ascii=False))
        except OSError as e:
            logger.warning("Subagent journal could not write undo state: {}", e)

    def _drop_entries(self) -> None:
        """Dopo un annulla le righe e i contenuti non servono più: resta solo
        ``undo.json`` col resoconto, finché il lineage non riparte."""
        try:
            (self.dir / _ENTRIES).unlink()
        except OSError:
            pass
        shutil.rmtree(self.dir / _BLOBS, ignore_errors=True)
        self._seen.clear()
        self._paths.clear()
        self._changed = 0
        self._saved_bytes = 0


# I registri vivi del processo, uno per lineage: il manager e lo strumento
# ``app`` (che annota nel registro dell'app-builder la pubblicazione della sua
# bozza) devono scrivere nello stesso oggetto, o i conti in memoria divergono.
_LIVE: dict[tuple[str, str], WriteJournal] = {}
_LIVE_LOCK = threading.Lock()


def journal_for(install_root: Path, lineage_id: str) -> WriteJournal:
    """Il registro vivo di *lineage_id*, creato (e riletto dal disco) alla prima richiesta."""
    key = (str(Path(install_root).resolve()), lineage_id)
    with _LIVE_LOCK:
        journal = _LIVE.get(key)
        if journal is None:
            journal = _LIVE[key] = WriteJournal(install_root, lineage_id)
        return journal


def live_journal(install_root: Path, lineage_id: str) -> WriteJournal | None:
    """Il registro vivo di *lineage_id*, se questo processo ne ha uno."""
    with _LIVE_LOCK:
        return _LIVE.get((str(Path(install_root).resolve()), lineage_id))


def read_undo_state(directory: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads((directory / _UNDO).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def finish_interrupted_undos(install_root: Path) -> int:
    """Al boot: finisce gli annulla che un kill ha lasciato a metà. Ritorna quanti.

    L'annulla è idempotente (un file già rimesso conta come rimesso), quindi
    finirlo è rifarlo. Best-effort: un boot non fallisce per questo.
    """
    root = journal_root(install_root)
    finished = 0
    try:
        dirs = [d for d in root.iterdir() if d.is_dir()] if root.is_dir() else []
    except OSError:
        return 0
    for directory in dirs:
        state = read_undo_state(directory)
        if state is None or state.get("state") != "running":
            continue
        try:
            WriteJournal(install_root, directory.name).undo(by=str(state.get("by") or "user"))
            finished += 1
        except Exception as e:  # noqa: BLE001 — vedi docstring
            logger.warning("Could not finish interrupted undo in {}: {}", directory, e)
    return finished


def sweep_orphan_journals(install_root: Path, keep: Iterable[str]) -> int:
    """Toglie i registri dei lineage che non hanno più record. Ritorna quanti.

    La vita di un registro è quella dei record del suo lineage (TTL e potatura
    di ``subagent_records``): senza record nessuno può più chiedere l'annulla, e
    le copie sono solo spazio.
    """
    root = journal_root(install_root)
    alive = {safe_filename(k) or "_" for k in keep}
    removed = 0
    try:
        dirs = [d for d in root.iterdir() if d.is_dir()] if root.is_dir() else []
    except OSError:
        return 0
    for directory in dirs:
        if directory.name in alive:
            continue
        shutil.rmtree(directory, ignore_errors=True)
        removed += 1
    with _LIVE_LOCK:
        for key in [k for k in _LIVE if safe_filename(k[1]) not in alive]:
            del _LIVE[key]
    return removed


def journal_stats(install_root: Path, lineage_id: str) -> tuple[int, UndoReport | None]:
    """File cambiati e ultimo annulla di un lineage, letti dal disco."""
    directory = journal_dir(install_root, lineage_id)
    if not directory.is_dir():
        return 0, None
    journal = WriteJournal(install_root, lineage_id)
    state = read_undo_state(directory)
    report = UndoReport.from_dict(state) if state and state.get("state") == "done" else None
    return journal.changed_files, report
