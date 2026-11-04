"""Lo store dei piani: righe di ``undo.sqlite``, accanto al diario dell'annulla.

Il piano è l'unica fonte del suo stato: scheda, striscia, bollino e riga nel
contesto di Jenny lo leggono, e solo questo modulo lo scrive. Le regole sono
quelle di ``config/store.py::mutate``: **si rilegge dentro la transazione con
cui si scrive** (:meth:`PlanStore.update`), così nessun chiamante salva una
copia vecchia sopra una nuova; e **niente I/O lento nel callback**: i conti dai
registri si fanno prima, qui si annotano.

**Perché in ``undo.sqlite`` e non nello stato che viaggia.** Un piano vive del
suo diario («cosa ha fatto il passo», «torna a prima»), e il diario resta sul
telefono e si svuota a ogni ripristino (``jenny/db/restore``): piani e diario
spariscono insieme, e nessun piano resta a promettere un ritorno che non c'è
più. Fino alla 2.x i piani stavano in ``<runtime_root>/plans/<id>/plan.json``;
li importa ``jenny/db_migration/plans.py``.

Le funzioni sono sincrone: i chiamanti async passano da ``asyncio.to_thread``.
"""

from __future__ import annotations

import json
import re
import secrets
import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.db.database import Database
from jenny.db.schema import Schema
from jenny.plan.model import (
    CLOSED_STATUSES,
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

_PLAN_ID_RE = re.compile(r"\Ap_[0-9a-f]{12}\Z")


def is_plan_id(value: object) -> bool:
    """*value* ha la forma di un id di piano: ``p_`` e 12 cifre esadecimali.

    L'id arriva dalla WebUI (``plan.get {id}``): uno che non ne ha la forma
    non è un piano, e non si cerca nemmeno.
    """
    return isinstance(value, str) and _PLAN_ID_RE.match(value) is not None


def slot_id(place: str) -> str:
    """L'id del posto (``nb:<nome>`` o ``personal``) da un id o da un nome di quaderno.

    Il driver passa sempre id; il nome nudo resta per chi chiede di un quaderno
    (i test, la rinomina).
    """
    from jenny.plan.place import NOTEBOOK_PREFIX, PERSONAL

    return place if place == PERSONAL or place.startswith(NOTEBOOK_PREFIX) else (
        NOTEBOOK_PREFIX + place)


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


def _parse_iso(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


SCHEMA = Schema(
    "plans",
    (
        """
        CREATE TABLE plans (
          id TEXT PRIMARY KEY,
          place TEXT NOT NULL,
          notebook TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL,
          created_at TEXT NOT NULL,
          version INTEGER NOT NULL,
          updated_at TEXT NOT NULL,
          data TEXT NOT NULL
        );
        CREATE INDEX plans_place ON plans(place);
        CREATE TABLE plan_drafting (
          slot TEXT PRIMARY KEY,
          data TEXT NOT NULL
        );
        """,
    ),
)


class PlanStore:
    """I piani di un'installazione, righe di ``undo.sqlite``."""

    def __init__(self, plans_dir: Path, *, workspace: Path | None = None) -> None:
        # ``plans_dir`` è dove stavano i piani prima del database: serve solo a
        # trovare il workspace, accanto (``snapshot.locations``).
        self.plans_dir = Path(plans_dir)
        # Il workspace dei registri dell'annulla (``plan.work``), e del
        # database dove i piani stanno insieme a loro.
        self.workspace = Path(workspace) if workspace is not None else (
            self.plans_dir.parent / "workspace")
        self._database: Database | None = None

    @classmethod
    def for_workspace(cls, workspace: Path) -> PlanStore:
        return cls(plans_dir_for(workspace), workspace=workspace)

    @property
    def _db(self) -> Database:
        # Tenuto: il driver legge lo store a ogni turno. Uno chiuso (lo
        # spegnimento, un test che chiude tutto) si riapre.
        if self._database is None or self._database.closed:
            from jenny import db

            self._database = db.database_for_workspace(self.workspace, db.UNDO)
            self._database.require(SCHEMA)
        return self._database

    # -- pianificazioni in corso (``drafting``) -----------------------------
    #
    # **Una fonte sola, qui accanto ai piani**, e non nei metadati della
    # sessione ``project:<nome>``. Tre ragioni. Sopravvive a un riavvio come i
    # piani, e con le stesse garanzie. La legge ``plan.get`` anche senza
    # agente, dallo store, come legge i piani: i metadati della sessione stanno
    # nel ``SessionManager`` del loop, che le RPC non hanno. E segue il quaderno
    # nei rinomini e nelle cancellazioni insieme ai suoi piani
    # (:meth:`follow_rename`, :meth:`delete_for_notebook`), mentre i metadati di
    # una sessione li riscrive il turno intero — un ripristino di checkpoint, un
    # ``/new`` — senza sapere che c'è un piano in preparazione. Il driver ne
    # tiene una copia in memoria come per l'indice dei piani, e la riscrive a
    # ogni cambio.

    @staticmethod
    def _read_drafting(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
        entries: dict[str, dict[str, Any]] = {}
        for row in conn.execute("SELECT slot, data FROM plan_drafting"):
            try:
                entry = json.loads(row["data"])
            except ValueError:
                logger.warning("Unreadable planning for {}", row["slot"])
                continue
            if isinstance(entry, dict):
                entries[row["slot"]] = entry
        return entries

    @staticmethod
    def _write_drafting(conn: sqlite3.Connection, slot: str, entry: dict[str, Any] | None) -> None:
        if entry is None:
            conn.execute("DELETE FROM plan_drafting WHERE slot = ?", (slot,))
            return
        conn.execute(
            "INSERT INTO plan_drafting(slot, data) VALUES (?, ?) "
            "ON CONFLICT(slot) DO UPDATE SET data = excluded.data",
            (slot, json.dumps(entry, ensure_ascii=False)),
        )

    def drafting_all(self) -> dict[str, dict[str, Any]]:
        """``{posto: voce}`` delle pianificazioni in corso, scadute comprese."""
        with self._db.reading() as conn:
            return self._read_drafting(conn)

    def drafting_get(self, place: str) -> dict[str, Any] | None:
        return self.drafting_all().get(slot_id(place))

    def drafting_update(
        self,
        place: str,
        fn: Callable[[dict[str, Any] | None], dict[str, Any] | None],
    ) -> dict[str, Any] | None:
        """Rilegge la voce di *place* nella transazione, le applica *fn*, riscrive.

        *fn* riceve la voce (o ``None``) e ritorna quella nuova, o ``None`` per
        toglierla. Come :meth:`update`: si rilegge dentro la transazione con cui
        si scrive, così un conto delle letture non ne cancella un altro.
        """
        key = slot_id(place)
        with self._db.write() as conn:
            current = self._read_drafting(conn).get(key)
            result = fn(dict(current) if current is not None else None)
            if result is None and current is None:
                return None
            self._write_drafting(conn, key, None if result is None else dict(result))
            return result

    def drafting_remove(self, place: str) -> dict[str, Any] | None:
        """Toglie la pianificazione di *place*; ritorna la voce che c'era, o ``None``."""
        key = slot_id(place)
        with self._db.write() as conn:
            entry = self._read_drafting(conn).get(key)
            if entry is not None:
                self._write_drafting(conn, key, None)
            return entry

    # -- lettura/scrittura di un piano --------------------------------------

    @staticmethod
    def _check_id(plan_id: str) -> None:
        if not is_plan_id(plan_id):
            raise PlanNotFoundError(f"no plan {str(plan_id)[:80]!r}")

    @staticmethod
    def _parse(plan_id: str, raw: str) -> Plan:
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise PlanFormatError(f"plan {plan_id}: {exc}") from exc
        plan = Plan.from_dict(data)
        # La riga dice di essere un altro piano: non è sana.
        if plan.id != plan_id:
            raise PlanFormatError(f"plan row {plan_id} says it is {plan.id[:80]!r}")
        return plan

    def _read(self, conn: sqlite3.Connection, plan_id: str) -> Plan:
        self._check_id(plan_id)
        row = conn.execute("SELECT data FROM plans WHERE id = ?", (plan_id,)).fetchone()
        if row is None:
            raise PlanNotFoundError(f"no plan {plan_id}")
        return self._parse(plan_id, row["data"])

    @staticmethod
    def _write(conn: sqlite3.Connection, plan: Plan) -> None:
        conn.execute(
            "INSERT INTO plans(id, place, notebook, status, created_at, version, updated_at, data) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
            "place = excluded.place, notebook = excluded.notebook, status = excluded.status, "
            "created_at = excluded.created_at, version = excluded.version, "
            "updated_at = excluded.updated_at, data = excluded.data",
            (plan.id, plan.place, plan.notebook, plan.status, plan.created_at, plan.version,
             plan.updated_at, json.dumps(plan.to_dict(), ensure_ascii=False)),
        )

    def load(self, plan_id: str) -> Plan:
        with self._db.reading() as conn:
            return self._read(conn, plan_id)

    def save(self, plan: Plan) -> Plan:
        """Scrive *plan* così com'è. Per cambiare uno stato si usa :meth:`update`."""
        self._check_id(plan.id)
        with self._db.write() as conn:
            plan.updated_at = now_iso()
            self._write(conn, plan)
        return plan

    def update(self, plan_id: str, fn: Callable[[Plan], Plan | None]) -> Plan:
        """Rilegge il piano nella transazione, gli applica *fn* e lo riscrive.

        *fn* muta il piano (o ne ritorna uno nuovo); se solleva, non si scrive
        niente. Deve essere veloce: la transazione resta aperta per tutta la
        sua durata, e con lei la scrittura di chiunque altro sul database.
        """
        with self._db.write() as conn:
            plan = self._read(conn, plan_id)
            result = fn(plan)
            if result is not None:
                plan = result
            if plan.id != plan_id:
                raise PlanError(f"update of {plan_id} returned plan {plan.id}")
            plan.updated_at = now_iso()
            self._write(conn, plan)
            return plan

    # -- elenchi ------------------------------------------------------------

    def _list(self, conn: sqlite3.Connection, where: str = "", params: tuple = ()) -> list[Plan]:
        plans: list[Plan] = []
        for row in conn.execute(f"SELECT id, data FROM plans {where}", params):
            try:
                plans.append(self._parse(row["id"], row["data"]))
            except PlanFormatError as exc:
                logger.warning("Skipping unreadable plan {}: {}", row["id"], exc)
        plans.sort(key=lambda p: (p.created_at, p.version), reverse=True)
        return plans

    def list_all(self) -> list[Plan]:
        """Tutti i piani leggibili, dal più recente. Uno illeggibile si salta con un avviso."""
        with self._db.reading() as conn:
            return self._list(conn)

    def list_for_notebook(self, notebook: str) -> list[Plan]:
        with self._db.reading() as conn:
            return self._list(conn, "WHERE notebook = ?", (notebook,))

    def list_for_place(self, place: str) -> list[Plan]:
        with self._db.reading() as conn:
            return self._list(conn, "WHERE place = ?", (slot_id(place),))

    def open_plan_for(self, place: str) -> Plan | None:
        """Il piano aperto di un posto (un id, o il nome di un quaderno)."""
        for plan in self.list_for_place(place):
            if plan.is_open:
                return plan
        return None

    # -- creazione ----------------------------------------------------------

    def create(self, place: str, proposal: Proposal, *, notes: str | None = None) -> Plan:
        """Scrive una proposta nuova per *place* (un id, o il nome di un quaderno).

        Se il quaderno ha già una proposta, la nuova ne è la versione seguente:
        la vecchia diventa ``superseded`` nella stessa transazione, e i passi
        della nuova ricevono i tag (``new``/``moved``/``changed``) calcolati
        qui, dal codice. Se il quaderno ha un piano aperto che non è una
        proposta (in corsa, in pausa, bloccato), :class:`PlanConflictError`.
        La regola «al più un piano aperto per posto» regge sulla transazione:
        l'unico modo di diventare aperto è nascere.
        """
        from jenny.plan.place import place_from_id

        where = place_from_id(slot_id(place))
        if where is None:
            raise PlanError(f"no place {place!r}")
        with self._db.write() as conn:
            current = next(
                (p for p in self._list(conn, "WHERE place = ?", (where.id,)) if p.is_open), None)
            stamp = now_iso()
            plan = Plan(
                id=new_plan_id(),
                notebook=where.notebook or "",
                place=where.id,
                session_key=where.session_key,
                title=proposal.title,
                steps=proposal.steps,
                notes=notes,
                created_at=stamp,
                updated_at=stamp,
                coverage=[dict(c) for c in proposal.coverage],
                assumptions=list(proposal.assumptions),
            )
            if current is not None:
                if current.status != "proposed":
                    raise PlanConflictError(where.id, current.id, current.status)
                plan.version = current.version + 1
                plan.replaces = current.id
                plan.dropped = tag_new_version(current.steps, plan.steps)
                apply_transition(current, "superseded", at=stamp, by=plan.id)
                self._write(conn, current)
            plan.history.append({"at": stamp, "event": "proposed", "version": plan.version})
            self._write(conn, plan)
        logger.info(
            "Plan {} proposed for {} (v{}, {} steps)",
            plan.id,
            where.id,
            plan.version,
            len(plan.steps),
        )
        return plan

    # -- quaderni -----------------------------------------------------------

    def follow_rename(self, old: str, new: str) -> list[str]:
        """Porta i piani di *old* su *new*: ``notebook``, ``place`` e ``session_key``."""
        moved: list[str] = []
        with self._db.write() as conn:
            for plan in self._list(conn, "WHERE notebook = ?", (old,)):
                plan.notebook = new
                plan.place = slot_id(new)
                plan.session_key = project_session_key(new)
                plan.updated_at = now_iso()
                self._write(conn, plan)
                moved.append(plan.id)
            entry = self._read_drafting(conn).get(slot_id(old))
            if entry is not None:
                self._write_drafting(conn, slot_id(old), None)
                self._write_drafting(conn, slot_id(new), entry)
        if moved:
            logger.info("Plans of notebook {!r} follow it to {!r}: {}", old, new, len(moved))
        return moved

    def delete_for_notebook(self, notebook: str) -> list[str]:
        """Cancella i piani di *notebook*. Ritorna gli id."""
        with self._db.write() as conn:
            removed = [r["id"] for r in conn.execute(
                "SELECT id FROM plans WHERE notebook = ?", (notebook,))]
            conn.execute("DELETE FROM plans WHERE notebook = ?", (notebook,))
            self._write_drafting(conn, slot_id(notebook), None)
        if removed:
            logger.info("Deleted {} plan(s) of notebook {!r}", len(removed), notebook)
        return removed

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
        limite dei due che scatta. Un piano potato perde solo la scheda e «Torna
        a prima»: senza il piano non si sa più quali registri sono suoi.

        I piani che hanno girato (``done``/``stopped``/``rolled_back``) e quelli
        che non sono mai partiti (``discarded``/``superseded``) si contano a
        parte: cinque revisioni di una proposta non devono spingere fuori
        l'ultimo piano vero, e il suo ritorno. Un piano aperto non si pota mai.
        """
        moment = now or datetime.now(timezone.utc)
        cutoff = moment - timedelta(days=max_age_days)
        removed: list[str] = []
        with self._db.write() as conn:
            by_bucket: dict[tuple[str, bool], list[Plan]] = {}
            for plan in self._list(conn):
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
                        removed.append(plan.id)
            conn.executemany("DELETE FROM plans WHERE id = ?", [(i,) for i in removed])
        if removed:
            logger.info("Pruned {} closed plan(s)", len(removed))
        return removed
