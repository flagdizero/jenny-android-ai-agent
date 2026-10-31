"""Lo store dei piani: ``<runtime_root>/plans/<plan_id>/plan.json``.

``plan.json`` è l'unica fonte dello stato di un piano: scheda, striscia,
bollino e riga nel contesto di Jenny lo leggono, e solo questo modulo lo scrive.
Le regole sono quelle di ``config/store.py::mutate`` e di ``board/store.py``:

- **si rilegge dentro il lock con cui si scrive** (:meth:`PlanStore.update`),
  così nessun chiamante salva una copia vecchia sopra una nuova;
- **scrittura atomica** (``atomic_write``);
- **niente I/O lento nel callback**: le foto si fanno prima, qui si annota l'id.

**I lock sono ``threading.Lock`` e le funzioni sincrone**, come la bacheca: i
chiamanti async passano da ``asyncio.to_thread``, il corpo critico è un file
piccolo, e un lock di thread non si lega a un event loop.

Due livelli di lock. Uno **per piano** serializza le letture-modifica-scrittura
di quel ``plan.json``. Uno **per store** serializza le operazioni che guardano
più piani insieme — creare (la regola «al più un piano aperto per quaderno»),
rinominare, cancellare, potare — e si prende sempre *prima* di quello del piano,
mai dopo, così due operazioni non si aspettano a vicenda. La regola del piano
aperto regge con il solo lock dello store perché l'unico modo di diventare
aperto è nascere: nessuna transizione porta un piano chiuso ad aperto.
"""

from __future__ import annotations

import json
import re
import secrets
import shutil
import threading
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.plan.model import (
    CLOSED_STATUSES,
    PLAN_FORMAT,
    RAN_STATUSES,
    Plan,
    PlanError,
    PlanFormatError,
    Proposal,
    apply_transition,
    now_iso,
    tag_new_version,
)
from jenny.session.keys import project_session_key
from jenny.snapshot.locations import plans_dir_for
from jenny.utils.path import atomic_write

PLAN_FILE_NAME = "plan.json"
PHOTOS_DIR_NAME = "photos"
ROLLBACK_JOURNAL_NAME = "rollback.journal"
# Le pianificazioni in corso, una per quaderno (v. :meth:`PlanStore.drafting_update`).
DRAFTING_FILE_NAME = "drafting.json"

_PLAN_ID_RE = re.compile(r"\Ap_[0-9a-f]{12}\Z")


def is_plan_id(value: object) -> bool:
    """*value* ha la forma di un id di piano: ``p_`` e 12 cifre esadecimali.

    L'id arriva dalla WebUI (``plan.get {id}``) e finisce in un percorso che
    :meth:`PlanStore.delete_for_notebook` e la potatura cancellano: uno che non
    ne ha la forma non è un piano, come per ``is_snapshot_id``.
    """
    return isinstance(value, str) and _PLAN_ID_RE.match(value) is not None


def new_plan_id() -> str:
    return f"p_{secrets.token_hex(6)}"


class PlanNotFoundError(PlanError, FileNotFoundError):
    """Nessun piano con quell'id (o un id che non ne ha la forma)."""


class PlanConflictError(PlanError):
    """Il quaderno ha già un piano aperto che una proposta non può sostituire."""

    def __init__(self, notebook: str, plan_id: str, status: str) -> None:
        super().__init__(
            f"notebook {notebook!r} already has an open plan ({plan_id}, {status}); "
            "only a proposal can be replaced by a new one"
        )
        self.notebook = notebook
        self.plan_id = plan_id
        self.status = status


_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    key = str(path.resolve(strict=False))
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


def _parse_iso(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class PlanStore:
    """I piani di un'installazione, uno per cartella sotto *plans_dir*."""

    def __init__(self, plans_dir: Path) -> None:
        self.plans_dir = Path(plans_dir)
        self._store_lock = _lock_for(self.plans_dir)

    @classmethod
    def for_workspace(cls, workspace: Path) -> PlanStore:
        return cls(plans_dir_for(workspace))

    # -- percorsi -----------------------------------------------------------

    def plan_dir(self, plan_id: str) -> Path:
        if not is_plan_id(plan_id):
            raise PlanNotFoundError(f"no plan {str(plan_id)[:80]!r}")
        return self.plans_dir / plan_id

    def photos_dir(self, plan_id: str) -> Path:
        return self.plan_dir(plan_id) / PHOTOS_DIR_NAME

    def journal_path(self, plan_id: str) -> Path:
        return self.plan_dir(plan_id) / ROLLBACK_JOURNAL_NAME

    def _plan_file(self, plan_id: str) -> Path:
        return self.plan_dir(plan_id) / PLAN_FILE_NAME

    @property
    def drafting_path(self) -> Path:
        return self.plans_dir / DRAFTING_FILE_NAME

    # -- pianificazioni in corso (``drafting``) -----------------------------
    #
    # **Una fonte sola, qui accanto ai piani**, e non nei metadati della
    # sessione ``project:<nome>``. Tre ragioni. Sopravvive a un riavvio come i
    # piani, e con le stesse garanzie (lock, scrittura atomica). La legge
    # ``plan.get`` anche senza agente, dallo store, come legge i piani: i
    # metadati della sessione stanno nel ``SessionManager`` del loop, che le RPC
    # non hanno. E segue il quaderno nei rinomini e nelle cancellazioni insieme
    # ai suoi piani (:meth:`follow_rename`, :meth:`delete_for_notebook`),
    # mentre i metadati di una sessione li riscrive il turno intero — un
    # ripristino di checkpoint, un ``/new`` — senza sapere che c'è un piano in
    # preparazione. Il driver ne tiene una copia in memoria come per l'indice
    # dei piani, e la riscrive a ogni cambio.

    def _read_drafting(self) -> dict[str, dict[str, Any]]:
        try:
            data = json.loads(self.drafting_path.read_text("utf-8"))
        except FileNotFoundError:
            return {}
        except ValueError as exc:
            # Un file rotto non ferma i piani: si riparte senza pianificazioni
            # in corso, che è lo stato più innocuo (il prossimo messaggio è una
            # chat normale).
            logger.warning("Unreadable {}: {}", DRAFTING_FILE_NAME, exc)
            return {}
        entries = data.get("notebooks") if isinstance(data, dict) else None
        if not isinstance(entries, dict):
            return {}
        return {
            str(name): dict(entry)
            for name, entry in entries.items()
            if isinstance(entry, dict)
        }

    def _write_drafting(self, entries: dict[str, dict[str, Any]]) -> None:
        self.plans_dir.mkdir(parents=True, exist_ok=True)
        atomic_write(
            self.drafting_path,
            json.dumps(
                {"format": PLAN_FORMAT, "notebooks": entries}, ensure_ascii=False, indent=1
            ),
        )

    def drafting_all(self) -> dict[str, dict[str, Any]]:
        """``{quaderno: voce}`` delle pianificazioni in corso, scadute comprese."""
        with _lock_for(self.drafting_path):
            return self._read_drafting()

    def drafting_get(self, notebook: str) -> dict[str, Any] | None:
        return self.drafting_all().get(notebook)

    def drafting_update(
        self,
        notebook: str,
        fn: Callable[[dict[str, Any] | None], dict[str, Any] | None],
    ) -> dict[str, Any] | None:
        """Rilegge la voce di *notebook* dentro il lock, le applica *fn*, riscrive.

        *fn* riceve la voce (o ``None``) e ritorna quella nuova, o ``None`` per
        toglierla. Come :meth:`update`: si rilegge dentro il lock con cui si
        scrive, così un conto delle letture non ne cancella un altro.
        """
        with _lock_for(self.drafting_path):
            entries = self._read_drafting()
            current = entries.get(notebook)
            result = fn(dict(current) if current is not None else None)
            if result is None:
                if current is None:
                    return None
                entries.pop(notebook, None)
            else:
                entries[notebook] = dict(result)
            self._write_drafting(entries)
            return result

    # -- lettura/scrittura di un piano --------------------------------------

    def _read(self, plan_id: str) -> Plan:
        path = self._plan_file(plan_id)
        try:
            data = json.loads(path.read_text("utf-8"))
        except FileNotFoundError:
            raise PlanNotFoundError(f"no plan {plan_id}") from None
        except ValueError as exc:
            raise PlanFormatError(f"plan {plan_id}: {exc}") from exc
        plan = Plan.from_dict(data)
        # Il file dice di essere un altro piano: non è sano, come un manifest
        # di snapshot che non si chiama col suo id.
        if plan.id != plan_id:
            raise PlanFormatError(f"plan file {plan_id} says it is {plan.id[:80]!r}")
        return plan

    def _write(self, plan: Plan) -> None:
        atomic_write(
            self._plan_file(plan.id),
            json.dumps(plan.to_dict(), ensure_ascii=False, indent=1),
        )

    def load(self, plan_id: str) -> Plan:
        with _lock_for(self.plan_dir(plan_id)):
            return self._read(plan_id)

    def save(self, plan: Plan) -> Plan:
        """Scrive *plan* così com'è. Per cambiare uno stato si usa :meth:`update`."""
        with _lock_for(self.plan_dir(plan.id)):
            plan.updated_at = now_iso()
            self._write(plan)
        return plan

    def update(self, plan_id: str, fn: Callable[[Plan], Plan | None]) -> Plan:
        """Rilegge il piano dentro il lock, gli applica *fn* e lo riscrive.

        *fn* muta il piano (o ne ritorna uno nuovo); se solleva, non si scrive
        niente. Deve essere veloce: il lock resta preso per tutta la sua durata.
        """
        with _lock_for(self.plan_dir(plan_id)):
            plan = self._read(plan_id)
            result = fn(plan)
            if result is not None:
                plan = result
            if plan.id != plan_id:
                raise PlanError(f"update of {plan_id} returned plan {plan.id}")
            plan.updated_at = now_iso()
            self._write(plan)
            return plan

    # -- elenchi ------------------------------------------------------------

    def list_all(self) -> list[Plan]:
        """Tutti i piani leggibili, dal più recente. Uno illeggibile si salta con un avviso."""
        plans: list[Plan] = []
        if not self.plans_dir.is_dir():
            return plans
        for entry in self.plans_dir.iterdir():
            if not is_plan_id(entry.name) or not entry.is_dir():
                continue
            try:
                plans.append(self.load(entry.name))
            except PlanNotFoundError:
                continue
            except PlanFormatError as exc:
                logger.warning("Skipping unreadable plan {}: {}", entry.name, exc)
        plans.sort(key=lambda p: (p.created_at, p.version), reverse=True)
        return plans

    def list_for_notebook(self, notebook: str) -> list[Plan]:
        return [p for p in self.list_all() if p.notebook == notebook]

    def open_plan_for(self, notebook: str) -> Plan | None:
        for plan in self.list_for_notebook(notebook):
            if plan.is_open:
                return plan
        return None

    # -- creazione ----------------------------------------------------------

    def create(self, notebook: str, proposal: Proposal, *, notes: str | None = None) -> Plan:
        """Scrive una proposta nuova per *notebook* e la ritorna.

        Se il quaderno ha già una proposta, la nuova ne è la versione seguente:
        la vecchia diventa ``superseded`` nello stesso passaggio, e i passi
        della nuova ricevono i tag (``new``/``moved``/``changed``) calcolati
        qui, dal codice. Se il quaderno ha un piano aperto che non è una
        proposta (in corsa, in pausa, bloccato), :class:`PlanConflictError`.
        """
        with self._store_lock:
            current = self.open_plan_for(notebook)
            stamp = now_iso()
            plan = Plan(
                id=new_plan_id(),
                notebook=notebook,
                session_key=project_session_key(notebook),
                title=proposal.title,
                steps=proposal.steps,
                notes=notes,
                created_at=stamp,
                updated_at=stamp,
                coverage=[dict(c) for c in proposal.coverage],
                assumptions=list(proposal.assumptions),
            )
            if current is not None:
                with _lock_for(self.plan_dir(current.id)):
                    # Riletto dentro il suo lock: fra la lista e qui un'altra
                    # strada (l'Approva) può averlo fatto partire.
                    current = self._read(current.id)
                    if current.status != "proposed":
                        if current.is_open:
                            raise PlanConflictError(notebook, current.id, current.status)
                    else:
                        plan.version = current.version + 1
                        plan.replaces = current.id
                        plan.dropped = tag_new_version(current.steps, plan.steps)
                        apply_transition(current, "superseded", at=stamp, by=plan.id)
                        self._write(current)
            plan.history.append({"at": stamp, "event": "proposed", "version": plan.version})
            self._write(plan)
            logger.info(
                "Plan {} proposed for notebook {!r} (v{}, {} steps)",
                plan.id,
                notebook,
                plan.version,
                len(plan.steps),
            )
            return plan

    # -- quaderni -----------------------------------------------------------

    def follow_rename(self, old: str, new: str) -> list[str]:
        """Porta i piani di *old* su *new*: ``notebook`` e ``session_key``.

        Le foto restano valide: i loro percorsi sono relativi alla cartella del
        quaderno, non al suo nome.
        """
        moved: list[str] = []
        with self._store_lock:
            for plan in self.list_for_notebook(old):

                def _rename(p: Plan) -> None:
                    p.notebook = new
                    p.session_key = project_session_key(new)

                self.update(plan.id, _rename)
                moved.append(plan.id)
            self._move_drafting(old, new)
        if moved:
            logger.info("Plans of notebook {!r} follow it to {!r}: {}", old, new, len(moved))
        return moved

    def delete_for_notebook(self, notebook: str) -> list[str]:
        """Cancella i piani di *notebook*, foto comprese. Ritorna gli id."""
        removed: list[str] = []
        with self._store_lock:
            for plan in self.list_for_notebook(notebook):
                self._remove(plan.id)
                removed.append(plan.id)
            self._move_drafting(notebook, None)
        if removed:
            logger.info("Deleted {} plan(s) of notebook {!r}", len(removed), notebook)
        return removed

    def drafting_remove(self, notebook: str) -> dict[str, Any] | None:
        """Toglie la pianificazione di *notebook*; ritorna la voce che c'era, o ``None``."""
        with _lock_for(self.drafting_path):
            entries = self._read_drafting()
            entry = entries.pop(notebook, None)
            if entry is not None:
                self._write_drafting(entries)
            return entry

    def _move_drafting(self, old: str, new: str | None) -> None:
        """La pianificazione in corso di *old* va su *new*, o via (``None``)."""
        with _lock_for(self.drafting_path):
            entries = self._read_drafting()
            entry = entries.pop(old, None)
            if entry is None:
                return
            if new is not None:
                entries[new] = entry
            self._write_drafting(entries)

    def _remove(self, plan_id: str) -> None:
        with _lock_for(self.plan_dir(plan_id)):
            shutil.rmtree(self.plan_dir(plan_id), ignore_errors=True)

    def prune(
        self,
        *,
        keep_closed: int = 5,
        max_age_days: int = 30,
        now: datetime | None = None,
    ) -> list[str]:
        """Pota i piani chiusi, per quaderno; ritorna gli id cancellati.

        Un piano chiuso va via se non è fra i *keep_closed* più recenti del suo
        quaderno **o** se è chiuso da più di *max_age_days* giorni: il primo
        limite dei due che scatta. Le foto pesano, e dopo la potatura «Torna a
        prima» sparisce dalla scheda: senza foto non si promette.

        I piani che hanno girato (``done``/``stopped``/``rolled_back``) e quelli
        che non sono mai partiti (``discarded``/``superseded``) si contano a
        parte: cinque revisioni di una proposta non devono spingere fuori le
        foto dell'ultimo piano vero. Un piano aperto non si pota mai.
        """
        moment = now or datetime.now(timezone.utc)
        cutoff = moment - timedelta(days=max_age_days)
        removed: list[str] = []
        with self._store_lock:
            by_bucket: dict[tuple[str, bool], list[Plan]] = {}
            for plan in self.list_all():
                if plan.status not in CLOSED_STATUSES:
                    continue
                key = (plan.notebook, plan.status in RAN_STATUSES)
                by_bucket.setdefault(key, []).append(plan)
            for plans in by_bucket.values():
                plans.sort(key=lambda p: p.updated_at, reverse=True)
                for rank, plan in enumerate(plans):
                    closed_at = _parse_iso(plan.updated_at)
                    too_old = closed_at is not None and closed_at < cutoff
                    if rank >= keep_closed or too_old:
                        self._remove(plan.id)
                        removed.append(plan.id)
        if removed:
            logger.info("Pruned {} closed plan(s)", len(removed))
        return removed
