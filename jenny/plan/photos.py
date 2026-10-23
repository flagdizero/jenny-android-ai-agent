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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from loguru import logger

from jenny.board.model import BOARD_DIR
from jenny.snapshot.engine import SnapshotEngine
from jenny.snapshot.store import get_blob, object_path
from jenny.snapshot.types import unsafe_entry_reason
from jenny.utils.path import atomic_write
from jenny.utils.wiki_paths import JOURNAL_DIRNAME
from jenny.webui.project_scaffold import PROJECT_DIRS

PHOTO_TRIGGER = "plan"

# Le esclusioni di una foto. Non quelle del workspace (``DEFAULT_EXCLUDE_GLOBS``):
# ``ui/**`` o ``logs/**`` lì sono cartelle di sistema, in un quaderno sarebbero
# cartelle dell'utente che la foto non vedrebbe e il ritorno non rimetterebbe.
# Restano fuori solo i temporanei (quelli di ``atomic_write`` compresi) e le
# cache di Python.
NOTEBOOK_EXCLUDE_GLOBS: tuple[str, ...] = ("**/__pycache__/**", "*.tmp", "*.tmp.*")

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


@dataclass(frozen=True)
class _Op:
    """Un'operazione del ritorno: «il percorso deve avere questo contenuto».

    ``hash`` ``None`` vuol dire «non deve esistere». Così ogni operazione è
    idempotente, ed è quello che rende il journal riapplicabile dopo un crollo.
    """

    path: str
    hash: str | None


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
        """``{percorso: hash}`` di una foto."""
        return {entry.path: entry.hash for entry in self.engine.load_manifest(photo_id).files}

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
    ) -> RollbackReport:
        """Riporta il quaderno a *target* dove è ancora com'era in *expected*.

        Con ``dry_run`` ritorna lo stesso resoconto senza toccare il quaderno:
        è il conto che la scheda mostra prima della conferma. Anche l'anteprima
        fa la foto di «adesso» (è da lì che legge *n*), ma la scrive nello store
        del piano, non nel quaderno; di solito il quaderno non è cambiato dalla
        foto B e la foto è quella.

        Prima di scrivere qualsiasi cosa: ogni percorso è controllato come in
        ``restore_snapshot`` e ogni blob da scrivere deve esserci, altrimenti
        :class:`RollbackError` e il quaderno resta intatto. Poi si scrive il
        journal, si applica, si cancella il journal.
        """
        keep = set(keep_groups)
        unknown = keep - set(KEEPABLE_GROUPS)
        if unknown:
            raise ValueError(f"unknown groups to keep: {sorted(unknown)}")
        t_files = self.files(target)
        e_files = self.files(expected)
        now_id = self.take("rollback-preview" if dry_run else "before-rollback")
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
                (report.removed if t is None else report.restored).append(path)
                ops.append(_Op(path, t))
            elif n == t:
                report.already.append(path)
            else:
                report.left.append(path)

        # Prima le cancellazioni, poi le scritture: un piano che ha messo un
        # file ``a`` dove c'era la cartella ``a/`` (o il contrario) si disfa solo
        # in quest'ordine, perché la cartella rimasta vuota va via con la
        # cancellazione e lascia il posto al file.
        ops.sort(key=lambda op: (op.hash is not None, op.path))
        self._check_ops(ops, current=n_files)
        if dry_run or not ops:
            if not dry_run:
                report.after = now_id
            return report

        atomic_write(
            journal_path,
            json.dumps(
                {
                    "format": 1,
                    "target": target,
                    "expected": expected,
                    "ops": [{"path": op.path, "hash": op.hash} for op in ops],
                },
                ensure_ascii=False,
            ),
        )
        self._apply(ops)
        journal_path.unlink(missing_ok=True)
        report.after = self.take("after-rollback")
        logger.info(
            "Plan rollback applied in {}: {}",
            self.root.name,
            ", ".join(f"{k}={v}" for k, v in report.counts.items() if v),
        )
        return report

    def recover_pending(self, journal_path: Path) -> bool:
        """Riapplica un ritorno rimasto a metà (il gateway morto durante). True se c'era.

        Le operazioni dicono «questo percorso deve avere questo contenuto», non
        «cambia questo in quello»: rifarle tutte, anche quelle già fatte, porta
        allo stesso quaderno. Il journal si cancella solo a fine applicazione,
        così un secondo crollo durante il recupero si recupera allo stesso modo.
        """
        try:
            raw = journal_path.read_text("utf-8")
        except FileNotFoundError:
            return False
        try:
            data = json.loads(raw)
            ops = [
                _Op(str(item["path"]), None if item["hash"] is None else str(item["hash"]))
                for item in data["ops"]
            ]
        except (ValueError, KeyError, TypeError) as exc:
            raise RollbackError(f"rollback journal {journal_path} is malformed: {exc}") from exc
        self._check_ops(ops, current=None)
        self._apply(ops)
        journal_path.unlink(missing_ok=True)
        logger.warning(
            "Recovered an interrupted plan rollback in {} ({} operation(s))",
            self.root.name,
            len(ops),
        )
        return True

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

    def _apply(self, ops: list[_Op]) -> None:
        for op in ops:
            self._apply_one(op)

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
