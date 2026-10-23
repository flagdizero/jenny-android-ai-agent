"""Le foto del quaderno di un piano, il loro confronto e il ritorno indietro.

Una «foto» è uno snapshot del quaderno (``wikis/<nome>/``) nello store del
piano (``plans/<id>/photos/``): lo stesso ``SnapshotEngine`` del workspace, con
un'altra radice e un altro store. Si fa all'Approva (A), prima e dopo ogni
passo, alla fine (B), prima e dopo un ritorno (C, R).

**Il ritorno lo fa il codice, mai il modello**, con una regola a tre vie per
ogni percorso: *t* è l'hash nella foto a cui tornare, *e* quello che ci si
aspetta di trovare (la foto da cui si torna), *n* quello che c'è adesso.

- ``n == e``: il file è come l'ha lasciato il piano → lo si rimette come in *t*
  (scritto, o cancellato se in *t* non c'era);
- ``n == t``: è già com'era → niente;
- altrimenti è cambiato dopo → lo si lascia com'è.

La stessa funzione serve i due versi: «Torna a prima» è
``rollback(target=A, expected=B)``, «Annulla il ritorno» è
``rollback(target=C, expected=R)``.

Un percorso con ``t == e`` non lo tocca nessun ramo (il piano non l'ha
cambiato), quindi non entra nel conto: il resoconto parla solo di quello che
il piano ha cambiato.
"""

from __future__ import annotations

import json
import os
import stat
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Iterable

from loguru import logger

from jenny.board.model import BOARD_DIR
from jenny.snapshot.engine import SnapshotEngine
from jenny.snapshot.store import get_blob, hash_content, object_path
from jenny.snapshot.types import unsafe_entry_reason
from jenny.utils.path import atomic_write
from jenny.utils.wiki_paths import JOURNAL_DIRNAME
from jenny.webui.project_scaffold import PROJECT_DIRS

PHOTO_TRIGGER = "plan"

# Le esclusioni di una foto. Non quelle del workspace (``DEFAULT_EXCLUDE_GLOBS``):
# ``ui/**`` o ``logs/**`` lì sono cartelle di sistema, in un quaderno sarebbero
# cartelle dell'utente che la foto non vedrebbe e il ritorno non rimetterebbe.
# Restano fuori i temporanei (quelli di ``atomic_write`` compresi), le cache di
# Python e ``.jenny/`` del quaderno: le uscite salvate dei tool
# (``.jenny/tool-results/``) sono cache del lavoro, non lavoro, e contarle
# riempiva il conto delle modifiche di nomi ``call_….txt`` che l'utente non ha
# mai visto (la prova del 23/10: «9 nuovi in altri file»).
NOTEBOOK_EXCLUDE_GLOBS: tuple[str, ...] = (
    "**/__pycache__/**",
    "*.tmp",
    "*.tmp.*",
    ".jenny/**",
)


def is_photo_excluded(path: str) -> bool:
    """*path* (relativo al quaderno) è fuori dalle foto **adesso**.

    Le stesse regole di ``SnapshotEngine`` su :data:`NOTEBOOK_EXCLUDE_GLOBS`,
    applicate a un percorso scritto in una foto. Servono alle foto **vecchie**:
    una foto fatta prima che un'esclusione esistesse ha ancora quei file, e
    senza questo filtro il confronto con una foto nuova li vedrebbe «tolti» —
    e il ritorno li cancellerebbe o li rimetterebbe. Un percorso escluso adesso
    è escluso anche nel passato.
    """
    parts = path.split("/")
    name = parts[-1]
    for glob in NOTEBOOK_EXCLUDE_GLOBS:
        if glob.endswith("/**"):
            base = glob[:-3]
            if base.startswith("**/"):
                if base[3:] in parts[:-1]:
                    return True
            elif path == base or path.startswith(base + "/"):
                return True
        elif fnmatch(name, glob) or fnmatch(path, glob):
            return True
    return False

# ── Gruppi ───────────────────────────────────────────────────────────────────
#
# Il conto del ritorno ha una casella per gruppo («Tieni le 12 ricerche»). La
# mappa viene dalla forma di un quaderno (``webui/project_scaffold.py``) e dalla
# bacheca (``board/model.py``):
#
#   wiki/…            → pages    le pagine
#   raw/journal/…     → journal  il diario: mai nel ritorno (v. sotto)
#   raw/…             → sources  quel che arriva da fuori: ``raw/research/`` del
#                                ricercatore, e ``raw/articles|papers|notes|refs``
#                                delle wiki di ricerca
#   board/…           → board    la bacheca: ``board.json``, promemoria, schede
#   tutto il resto    → other    ``AGENTS.md``, ``log/``, ``audit/``, …
#
# ``journal`` è un quinto valore e non un gruppo da spuntare: il diario è
# escluso sempre, quindi nel conto del ritorno non compare mai fra le caselle.
GROUP_PAGES = "pages"
GROUP_SOURCES = "sources"
GROUP_BOARD = "board"
GROUP_OTHER = "other"
GROUP_JOURNAL = "journal"
KEEPABLE_GROUPS: tuple[str, ...] = (GROUP_PAGES, GROUP_SOURCES, GROUP_BOARD, GROUP_OTHER)

_JOURNAL_PREFIX = JOURNAL_DIRNAME.rstrip("/") + "/"


def group_of(path: str) -> str:
    """Il gruppo di un percorso relativo alla radice del quaderno."""
    if path.startswith(_JOURNAL_PREFIX):
        return GROUP_JOURNAL
    head = path.split("/", 1)[0]
    if head == "wiki":
        return GROUP_PAGES
    if head == "raw":
        return GROUP_SOURCES
    if head == BOARD_DIR:
        return GROUP_BOARD
    return GROUP_OTHER


def is_always_excluded(path: str) -> bool:
    """Il diario non torna mai indietro.

    È append-only, e il giardiniere lo legge come immobile: riscriverlo
    all'indietro ne romperebbe il contratto. Quel che Jenny ha annotato durante
    il piano resta annotato.
    """
    return path.startswith(_JOURNAL_PREFIX)


# Cartelle che il ritorno non toglie anche se restano vuote: quelle con cui un
# quaderno nasce. Una ``raw/research/`` vuota alla foto A non risulta in nessun
# manifest (le foto vedono file, non cartelle), e senza questa lista il primo
# file scritto lì dal piano, tolto al ritorno, si porterebbe via la cartella.
_SCAFFOLD_DIRS: frozenset[str] = frozenset(PROJECT_DIRS)


# ── Resoconto ────────────────────────────────────────────────────────────────

OUTCOMES: tuple[str, ...] = ("restored", "removed", "already", "left", "kept", "excluded")


@dataclass
class RollbackReport:
    """Cosa fa (o farebbe, con ``dry_run``) un ritorno, percorso per percorso.

    - ``restored``: rimesso com'era in *target*;
    - ``removed``: tolto, perché in *target* non c'era;
    - ``already``: già com'era;
    - ``left``: cambiato dopo il piano, lasciato com'è;
    - ``kept``: si sarebbe rimesso, ma è in un gruppo che l'utente tiene;
    - ``excluded``: il diario, che non torna mai.

    ``before`` è la foto di com'era il quaderno subito prima (C nel verso
    «Torna a prima»), ``after`` quella di dopo (R); ``after`` è ``None`` in
    anteprima.
    """

    target: str
    expected: str
    dry_run: bool
    before: str
    after: str | None = None
    restored: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    already: list[str] = field(default_factory=list)
    left: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        return {outcome: len(getattr(self, outcome)) for outcome in OUTCOMES}

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "target": self.target,
            "expected": self.expected,
            "dry_run": self.dry_run,
            "before": self.before,
            "after": self.after,
            "counts": self.counts,
        }
        for outcome in OUTCOMES:
            data[outcome] = list(getattr(self, outcome))
        return data


class RollbackError(RuntimeError):
    """Un ritorno rifiutato prima di scrivere: un percorso insicuro, un blob che manca."""


class RollbackConflictError(RollbackError):
    """Il quaderno non è più quello che l'utente ha visto nel conto del ritorno.

    La foto di «adesso» che il ritorno fa prima di scrivere non è quella
    dell'anteprima (``expect_now``): qualcosa è cambiato fra il conto e la
    conferma, e il conto mostrato non è più vero. Niente è stato scritto.
    """


class RollbackIncompleteError(RuntimeError):
    """Un ritorno partito e caduto a metà per un errore del disco.

    Una parte delle operazioni è sul disco, il resto no. Il journal è già stato
    tolto: il processo è vivo, quindi non c'è un crollo da recuperare al boot,
    e un journal lasciato lì scatterebbe a ogni avvio sopra quello che l'utente
    scrive nel frattempo. Riprovare il ritorno è sicuro: la regola a tre vie
    vede le operazioni già fatte come «già com'era».
    """

    def __init__(self, message: str, *, applied: int, total: int) -> None:
        super().__init__(message)
        self.applied = applied
        self.total = total


@dataclass(frozen=True)
class _Op:
    """Un'operazione del ritorno: «il percorso deve avere questo contenuto».

    ``hash`` ``None`` vuol dire «non deve esistere». ``expect`` è l'hash che il
    percorso aveva quando il ritorno è stato deciso (``None`` = non c'era): il
    recupero al boot applica l'operazione solo se il file è ancora così, o se
    è già come in ``hash``. Senza ``expect`` un journal riapplicato giorni dopo
    — l'utente ha intanto ricreato il file — cancellerebbe il suo lavoro.
    """

    path: str
    hash: str | None
    expect: str | None = None


# Il formato del journal. Il 2 porta ``expect`` per ogni operazione e
# l'intestazione del ritorno (piano, verso, foto): un journal senza ``expect``
# non si può riapplicare in sicurezza, quindi non si legge.
JOURNAL_FORMAT = 2

# Un hash che nessun file ha: un percorso che adesso è una cartella (o un
# file che non si legge) non coincide con niente, quindi si lascia com'è.
_UNREADABLE = "unreadable"


@dataclass
class RecoveryResult:
    """L'esito di un ritorno ripreso al boot (:meth:`PlanPhotos.recover_pending`).

    ``header`` è l'intestazione del journal (``plan_id``, ``kind``, le foto,
    i conti del ritorno originale): il driver la usa per portare il piano allo
    stato che il ritorno avrebbe scritto. ``error`` non è vuoto se il recupero
    è caduto a sua volta; ``after`` è la foto del quaderno a recupero finito.
    """

    header: dict[str, Any]
    applied: list[str] = field(default_factory=list)
    already: list[str] = field(default_factory=list)
    left: list[str] = field(default_factory=list)
    error: str | None = None
    after: str | None = None

    @property
    def complete(self) -> bool:
        return self.error is None


# Un lock per store di foto, di processo: due ``PlanPhotos`` sullo stesso piano
# (il driver ne crea uno per chiamata) e due thread — un ``/stop`` che cancella
# il gancio di fine turno a metà di ``to_thread(photos.take)`` lascia il thread
# a scrivere mentre il Ferma fa la sua foto — non devono scrivere l'indice e i
# manifest insieme. Rientrante: ``rollback`` fa le sue foto dentro il lock.
_PHOTO_LOCKS: dict[str, threading.RLock] = {}
_PHOTO_LOCKS_GUARD = threading.Lock()


def _photo_lock(photos_dir: Path) -> threading.RLock:
    key = str(Path(photos_dir).resolve(strict=False))
    with _PHOTO_LOCKS_GUARD:
        lock = _PHOTO_LOCKS.get(key)
        if lock is None:
            lock = _PHOTO_LOCKS[key] = threading.RLock()
        return lock


# ── Foto ─────────────────────────────────────────────────────────────────────


class PlanPhotos:
    """Le foto di un piano: *notebook_root* fotografato in ``plan_dir/photos``."""

    def __init__(self, plan_dir: Path, notebook_root: Path) -> None:
        self.plan_dir = Path(plan_dir)
        self.engine = SnapshotEngine(
            root=Path(notebook_root),
            snapshots_dir=self.plan_dir / "photos",
            exclude_globs=NOTEBOOK_EXCLUDE_GLOBS,
        )
        self.root = self.engine.root
        self._lock = _photo_lock(self.plan_dir / "photos")

    def take(self, label: str) -> str:
        """Fa una foto e ne ritorna l'id.

        Se niente è cambiato dall'ultima, il motore non ne scrive una nuova e
        l'id è quello dell'ultima: due foto uguali di fila costano una ``stat``
        per file e nessun file nuovo nello store.

        Il riuso degli hash per ``(size, mtime_ns)`` è un parametro del motore e
        non un giro qui: il ciclo di scansione, il calcolo dell'id e l'indice
        sono suoi, e rifarli qui vorrebbe dire una seconda copia del commit di
        uno snapshot da tenere allineata.
        """
        with self._lock:
            manifest = self.engine.create_snapshot(
                trigger=PHOTO_TRIGGER, label=label, reuse_unchanged=True
            )
            if manifest is not None:
                return manifest.id
            head = self.engine.head_id()
        if head is None:  # pragma: no cover — create_snapshot crea sempre la prima
            raise RuntimeError("photo store has no head after an unchanged snapshot")
        return head

    def files(self, photo_id: str) -> dict[str, str]:
        """``{percorso: hash}`` di una foto, senza i percorsi esclusi adesso.

        Il filtro è qui perché ogni lettura di una foto passa da qui — il
        confronto, il conto delle modifiche, il ritorno — e una foto vecchia
        con dentro ``.jenny/`` deve valere per tutti come se non li avesse
        (:func:`is_photo_excluded`).
        """
        return {
            entry.path: entry.hash
            for entry in self.engine.load_manifest(photo_id).files
            if not is_photo_excluded(entry.path)
        }

    def diff(self, a: str, b: str) -> dict[str, list[str]]:
        """Cosa cambia dalla foto *a* alla foto *b*: percorsi ordinati."""
        before = self.files(a)
        after = self.files(b)
        return {
            "added": sorted(p for p in after if p not in before),
            "changed": sorted(p for p in after if p in before and before[p] != after[p]),
            "removed": sorted(p for p in before if p not in after),
        }

    # -- ritorno ------------------------------------------------------------

    def rollback(
        self,
        target: str,
        expected: str,
        *,
        journal_path: Path,
        keep_groups: Iterable[str] = (),
        dry_run: bool = False,
        expect_now: str | None = None,
        journal_header: Mapping[str, Any] | None = None,
        commit: Callable[[RollbackReport], Any] | None = None,
    ) -> RollbackReport:
        """Riporta il quaderno a *target* dove è ancora com'era in *expected*.

        Con ``dry_run`` ritorna lo stesso resoconto senza toccare il quaderno:
        è il conto che la scheda mostra prima della conferma. Anche l'anteprima
        fa la foto di «adesso» (è da lì che legge *n*), ma la scrive nello store
        del piano, non nel quaderno; il suo id è ``report.before``, ed è quello
        che la conferma passa come *expect_now*: se la foto di «adesso» della
        conferma è un'altra, il quaderno è cambiato dopo il conto e si solleva
        :class:`RollbackConflictError` senza scrivere niente.

        Prima di scrivere qualsiasi cosa: ogni percorso è controllato come in
        ``restore_snapshot`` e ogni blob da scrivere deve esserci, altrimenti
        :class:`RollbackError` e il quaderno resta intatto. Poi si scrive il
        journal (con *journal_header*, l'intestazione che il driver rilegge al
        boot), si applica, si fa la foto «dopo», si chiama *commit* col
        resoconto (il driver ci scrive ``plan.json``) e solo allora si toglie il
        journal: un crollo in qualunque punto prima lascia il journal, e il boot
        finisce il lavoro e lo stato del piano. Un errore del disco a metà
        toglie il journal e solleva :class:`RollbackIncompleteError`.
        """
        keep = set(keep_groups)
        unknown = keep - set(KEEPABLE_GROUPS)
        if unknown:
            raise ValueError(f"unknown groups to keep: {sorted(unknown)}")
        with self._lock:
            return self._rollback_locked(
                target,
                expected,
                journal_path=journal_path,
                keep=keep,
                dry_run=dry_run,
                expect_now=expect_now,
                journal_header=journal_header,
                commit=commit,
            )

    def _rollback_locked(
        self,
        target: str,
        expected: str,
        *,
        journal_path: Path,
        keep: set[str],
        dry_run: bool,
        expect_now: str | None,
        journal_header: Mapping[str, Any] | None,
        commit: Callable[[RollbackReport], Any] | None,
    ) -> RollbackReport:
        t_files = self.files(target)
        e_files = self.files(expected)
        now_id = self.take("rollback-preview" if dry_run else "before-rollback")
        if expect_now is not None and now_id != expect_now:
            raise RollbackConflictError(
                "the notebook changed since the summary was shown: read it again"
            )
        n_files = self.files(now_id)

        report = RollbackReport(target=target, expected=expected, dry_run=dry_run, before=now_id)
        ops: list[_Op] = []
        for path in sorted(set(t_files) | set(e_files) | set(n_files)):
            t, e, n = t_files.get(path), e_files.get(path), n_files.get(path)
            if t == e:
                continue
            if is_always_excluded(path):
                report.excluded.append(path)
                continue
            if n == e:
                if group_of(path) in keep:
                    report.kept.append(path)
                    continue
                ops.append(_Op(path, t, n))
            elif n == t:
                report.already.append(path)
            else:
                report.left.append(path)

        # Un file da rimettere dove adesso c'è una cartella che resta (dentro ha
        # un file che il ritorno non toglie), o sotto un file che resta: non si
        # può scrivere senza toccare quel che non è del piano. È «lasciato com'è»,
        # come un file cambiato dopo — deciderlo qui, prima del journal, invece
        # di scoprirlo con un ``IsADirectoryError`` a metà dell'applicazione.
        ops, blocked = _split_blocked(ops, n_files)
        report.left = sorted([*report.left, *blocked])
        for op in ops:
            (report.removed if op.hash is None else report.restored).append(op.path)

        # Prima le cancellazioni, poi le scritture: un piano che ha messo un
        # file ``a`` dove c'era la cartella ``a/`` (o il contrario) si disfa solo
        # in quest'ordine, perché la cartella rimasta vuota va via con la
        # cancellazione e lascia il posto al file.
        ops.sort(key=lambda op: (op.hash is not None, op.path))
        self._check_ops(ops, current=n_files)
        if dry_run:
            return report
        if not ops:
            report.after = now_id
            if commit is not None:
                commit(report)
            return report

        header = dict(journal_header or {})
        header.update(
            {
                "format": JOURNAL_FORMAT,
                "target": target,
                "expected": expected,
                "before": now_id,
                "keep": sorted(keep),
                "counts": report.counts,
            }
        )
        atomic_write(
            journal_path,
            json.dumps(
                {
                    **header,
                    "ops": [
                        {"path": op.path, "hash": op.hash, "expect": op.expect} for op in ops
                    ],
                },
                ensure_ascii=False,
            ),
        )
        applied = 0
        try:
            for op in ops:
                self._apply_one(op)
                applied += 1
        except OSError as exc:
            journal_path.unlink(missing_ok=True)
            logger.error(
                "Plan rollback in {} stopped after {}/{} operation(s): {}",
                self.root.name, applied, len(ops), exc,
            )
            raise RollbackIncompleteError(
                f"{type(exc).__name__}: {exc}", applied=applied, total=len(ops)
            ) from exc
        report.after = self.take("after-rollback")
        if commit is not None:
            commit(report)
        journal_path.unlink(missing_ok=True)
        logger.info(
            "Plan rollback applied in {}: {}",
            self.root.name,
            ", ".join(f"{k}={v}" for k, v in report.counts.items() if v),
        )
        return report

    @staticmethod
    def read_journal(journal_path: Path) -> tuple[dict[str, Any], list[_Op]] | None:
        """L'intestazione e le operazioni di un journal, o ``None`` se non c'è.

        :class:`RollbackError` se è malformato, o di un formato senza
        ``expect`` (che non si può riapplicare in sicurezza).
        """
        try:
            raw = journal_path.read_text("utf-8")
        except FileNotFoundError:
            return None
        try:
            data = json.loads(raw)
            if data.get("format") != JOURNAL_FORMAT:
                raise ValueError(f"unsupported journal format {data.get('format')!r}")
            ops = [
                _Op(
                    str(item["path"]),
                    None if item["hash"] is None else str(item["hash"]),
                    None if item["expect"] is None else str(item["expect"]),
                )
                for item in data["ops"]
            ]
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise RollbackError(f"rollback journal {journal_path} is malformed: {exc}") from exc
        header = {k: v for k, v in data.items() if k != "ops"}
        return header, ops

    def recover_pending(
        self,
        journal_path: Path,
        *,
        commit: Callable[[RecoveryResult], Any] | None = None,
    ) -> RecoveryResult | None:
        """Riprende un ritorno rimasto a metà (il gateway morto durante). ``None`` se non c'era.

        Ogni operazione dice «questo percorso deve avere questo contenuto», e da
        quale contenuto ci si arriva (``expect``). Il recupero la applica solo
        se il file è ancora com'era quando il ritorno è stato deciso; se è già
        come deve essere non fa niente; se è un'altra cosa — l'ha scritto
        qualcuno dopo — lo lascia e lo riporta. Così un journal che resta lì
        per giorni non disfa il lavoro dell'utente.

        A recupero finito si fa la foto «dopo», si chiama *commit* (il driver
        porta il piano allo stato del ritorno) e solo allora si toglie il
        journal: un secondo crollo durante il recupero si recupera allo stesso
        modo. Un errore del disco toglie il journal e ritorna l'esito con
        ``error``: non deve scattare a ogni avvio. Un journal malformato o con
        un percorso insicuro solleva :class:`RollbackError` senza toccare niente.
        """
        with self._lock:
            parsed = self.read_journal(journal_path)
            if parsed is None:
                return None
            header, ops = parsed
            # Un journal scritto prima di un'esclusione nuova può nominare un
            # percorso che adesso è fuori dalle foto: non è del piano, e il
            # recupero non lo tocca.
            ops = [op for op in ops if not is_photo_excluded(op.path)]
            self._check_ops(ops, current=None)
            result = RecoveryResult(header=header)
            try:
                for op in ops:
                    current = self._current_hash(op.path)
                    if current == op.hash:
                        result.already.append(op.path)
                    elif current == op.expect:
                        self._apply_one(op)
                        result.applied.append(op.path)
                    else:
                        result.left.append(op.path)
            except OSError as exc:
                journal_path.unlink(missing_ok=True)
                result.error = f"{type(exc).__name__}: {exc}"
                logger.error(
                    "Interrupted plan rollback in {} could not be finished: {}",
                    self.root.name, result.error,
                )
                return result
            result.after = self.take("after-rollback")
            if commit is not None:
                commit(result)
            journal_path.unlink(missing_ok=True)
        logger.warning(
            "Recovered an interrupted plan rollback in {}: {} applied, {} already, {} left",
            self.root.name, len(result.applied), len(result.already), len(result.left),
        )
        return result

    # -- interni ------------------------------------------------------------

    def _resolve(self, path: str, hash_hex: str) -> Path:
        """Il percorso assoluto di *path*, o ``RollbackError`` se esce dalla radice.

        Lo stesso controllo di ``restore_snapshot``: la stringa (niente ``..``,
        niente assoluti) e poi il percorso risolto, che vede anche un link
        simbolico in mezzo che porta fuori.
        """
        reason = unsafe_entry_reason(path, hash_hex)
        if reason is None:
            full = self.root / Path(*path.split("/"))
            if not full.resolve().is_relative_to(self.root):
                reason = f"unsafe path {path!r} leaves the notebook"
        if reason is not None:
            raise RollbackError(reason)
        return self.root / Path(*path.split("/"))

    def _check_ops(self, ops: list[_Op], *, current: dict[str, str] | None) -> None:
        for op in ops:
            if is_always_excluded(op.path):
                raise RollbackError(f"refusing to rewrite the journal: {op.path!r}")
            # Per una cancellazione non c'è un blob da controllare: l'hash che
            # ``unsafe_entry_reason`` vuole è quello attuale, o uno fittizio
            # ben formato (il controllo che conta lì è sul percorso).
            probe = op.hash or (current or {}).get(op.path) or "0" * 64
            self._resolve(op.path, probe)
            if op.hash is not None and not object_path(self.engine.objects_dir, op.hash).is_file():
                raise RollbackError(f"photo store has no content for {op.path!r}")

    def _current_hash(self, path: str) -> str | None:
        """L'hash del file a *path* adesso: ``None`` se non c'è.

        Una cartella, un link o un file che non si legge non coincidono con
        nessun hash (:data:`_UNREADABLE`): il recupero li lascia com'è.
        """
        full = self.root / Path(*path.split("/"))
        try:
            st = os.lstat(full)
        except FileNotFoundError:
            return None
        except OSError:
            return _UNREADABLE
        if not stat.S_ISREG(st.st_mode):
            return _UNREADABLE
        try:
            return hash_content(full.read_bytes())
        except OSError:
            return _UNREADABLE

    def _apply_one(self, op: _Op) -> None:
        full = self._resolve(op.path, op.hash or "0" * 64)
        if op.hash is None:
            try:
                full.unlink()
            except FileNotFoundError:
                pass
            self._prune_empty_dirs(full.parent)
            return
        atomic_write(full, get_blob(self.engine.objects_dir, op.hash))

    def _prune_empty_dirs(self, directory: Path) -> None:
        """Toglie le cartelle rimaste vuote risalendo verso la radice, esclusa.

        Si ferma alla prima che non è vuota, alla radice e alle cartelle con cui
        il quaderno nasce.
        """
        while directory != self.root and directory.is_relative_to(self.root):
            rel = directory.relative_to(self.root).as_posix()
            if rel in _SCAFFOLD_DIRS:
                return
            try:
                os.rmdir(directory)
            except OSError:
                return
            directory = directory.parent


def _split_blocked(ops: list[_Op], current: Mapping[str, str]) -> tuple[list[_Op], list[str]]:
    """Separa le scritture che non si possono fare senza toccare file che restano.

    Dopo le cancellazioni del ritorno restano i file di *current* che nessuna
    operazione toglie. Una scrittura in ``a/b`` è impossibile se ``a`` è uno di
    quei file, e una scrittura in ``a`` lo è se sotto ``a/`` ne resta uno:
    entrambe vanno fra i «lasciati com'è». Ritorna le operazioni da fare e i
    percorsi bloccati.
    """
    deleting = {op.path for op in ops if op.hash is None}
    staying = {path for path in current if path not in deleting}
    staying_dirs: set[str] = set()
    for path in staying:
        parts = path.split("/")
        for i in range(1, len(parts)):
            staying_dirs.add("/".join(parts[:i]))
    ok: list[_Op] = []
    blocked: list[str] = []
    for op in ops:
        if op.hash is not None:
            parts = op.path.split("/")
            above = any("/".join(parts[:i]) in staying for i in range(1, len(parts)))
            if op.path in staying_dirs or above:
                blocked.append(op.path)
                continue
        ok.append(op)
    return ok, blocked
