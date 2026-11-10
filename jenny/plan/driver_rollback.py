"""Il ritorno a prima del piano, e il suo annulla.

Torna a prima: l'anteprima, il ritorno dai registri dell'annulla, l'annulla del ritorno.

Un pezzo di :class:`jenny.plan.driver.PlanDriver` (mixin): il testo dei metodi è
quello di prima, spostato qui per responsabilità.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

from loguru import logger

from jenny.plan import work
from jenny.plan.driver_common import (
    _KIND_ROLLBACK,
    _KIND_UNDO,
    PENDING_UNDO_KEY,
    PlanCommandError,
    _cut,
    place_of,
)
from jenny.plan.model import HELD_STATUSES, Plan, apply_transition, now_iso
from jenny.plan.place import PlanPlace

if TYPE_CHECKING:
    from jenny.plan.driver_common import PlanHost, _Live
    from jenny.plan.store import PlanStore


# Un ritorno (o il suo annullamento) partito e non finito: nell'``extra`` del
# piano, con l'errore. Il piano resta ``done``/``stopped`` (o ``rolled_back``),
# perché il quaderno è a metà fra i due; la scheda lo legge da qui e Riprova è
# sicuro, perché la regola a tre vie vede le operazioni già fatte come «già
# com'era». Un ritorno riuscito lo toglie.
ROLLBACK_FAILED_KEY = "rollback_failed"


# Quanti registri di ritorno il piano ha aperto: la chiave del prossimo.
_UNDO_COUNT = "undo_count"


OUTCOMES: tuple[str, ...] = ("restored", "removed", "already", "left", "kept", "excluded")


def card_report(report: Any, place: PlanPlace) -> dict[str, Any]:
    """Un resoconto d'annulla (``write_journal.UndoReport``) come lo legge la scheda.

    Gli esiti per lista, coi percorsi come li mostra il posto, i conti, e i conti
    per gruppo per le caselle («Tieni le 12 ricerche»).
    """
    outcomes = report.outcomes()
    data: dict[str, Any] = {
        outcome: [place.shown(p) for p in outcomes.get(outcome, ())] for outcome in OUTCOMES
    }
    data["counts"] = {outcome: len(outcomes.get(outcome, ())) for outcome in OUTCOMES}
    data["dry_run"] = bool(report.dry_run)
    groups: dict[str, dict[str, int]] = {}
    group_of: dict[str, str] = {}
    for outcome in OUTCOMES:
        for path in outcomes.get(outcome, ()):
            group = place.group_of(path)
            group_of[place.shown(path)] = group
            bucket = groups.setdefault(group, {})
            bucket[outcome] = bucket.get(outcome, 0) + 1
    data["groups"] = groups
    # Per la scheda: il gruppo di ogni percorso (il conto si rifà quando l'utente
    # spunta un gruppo) e quali gruppi si possono tenere, nell'ordine.
    data["group_of"] = group_of
    data["keepable"] = [g for g in groups if place.keepable(g)]
    return data


def _keep_groups(keep: Iterable[Any], place: PlanPlace) -> set[str]:
    if keep is None:
        return set()
    if isinstance(keep, (str, bytes)) or not isinstance(keep, Iterable):
        raise PlanCommandError("bad_request", "keep must be a list of groups")
    groups = set()
    for value in keep:
        if not isinstance(value, str) or not place.keepable(value):
            raise PlanCommandError("bad_request", f"unknown group: {str(value)[:40]!r}")
        groups.add(str(value))
    return groups


def _drop_pending_undo(plan: Plan) -> None:
    plan.extra.pop(PENDING_UNDO_KEY, None)


def _mark_rolled_back(
    plan: Plan,
    *,
    journal: str,
    keep: Iterable[str],
    counts: Mapping[str, int],
    recovered: bool = False,
) -> None:
    """Il piano dopo un ritorno: il suo registro, ``rolled_back``. Lo stesso dal vivo e al boot."""
    plan.rollback_journal = journal
    plan.undo_journal = None
    plan.extra.pop(PENDING_UNDO_KEY, None)
    plan.extra.pop(ROLLBACK_FAILED_KEY, None)
    details: dict[str, Any] = {"keep": sorted(keep), "counts": dict(counts)}
    if recovered:
        details["recovered"] = True
    apply_transition(plan, "rolled_back", **details)


def _mark_rollback_undone(
    plan: Plan, counts: Mapping[str, int], *, journal: str, recovered: bool = False
) -> None:
    """Il piano dopo «Annulla il ritorno»: lo stato di prima, e il registro dell'annullamento."""
    previous = _status_before_rollback(plan)
    plan.rollback_journal = None
    plan.undo_journal = journal
    plan.extra.pop(PENDING_UNDO_KEY, None)
    plan.extra.pop(ROLLBACK_FAILED_KEY, None)
    details: dict[str, Any] = {"counts": dict(counts)}
    if recovered:
        details["recovered"] = True
    apply_transition(plan, previous, event="rollback_undone", **details)


def _rollback_note(plan: Plan, counts: Mapping[str, int], drafts: int = 0) -> str:
    return (
        f'The user rolled {place_of(plan).subject} back to before the plan "{plan.title}": '
        f"{counts.get('restored', 0)} file(s) restored, {counts.get('removed', 0)} removed, "
        f"{counts.get('left', 0)} left as they were because they changed afterwards"
        + (f", {counts['kept']} kept on purpose" if counts.get("kept") else "")
        + (f", {drafts} pending draft(s) of the plan discarded" if drafts else "")
        + "."
    )


def _undo_note(plan: Plan, counts: Mapping[str, int]) -> str:
    return (
        f'The user undid the rollback of the plan "{plan.title}": {place_of(plan).subject} is '
        f"back as the plan left it ({counts.get('restored', 0)} file(s) restored, "
        f"{counts.get('removed', 0)} removed)."
    )


def _status_before_rollback(plan: Plan) -> str:
    for entry in reversed(plan.history):
        if entry.get("event") == "rolled_back" and entry.get("from") in ("done", "stopped"):
            return str(entry["from"])
    return "done"


class RollbackMixin:
    """Torna a prima: l'anteprima, il ritorno dai registri dell'annulla, l'annulla del ritorno.

    Mixin di ``PlanDriver``.
    """

    if TYPE_CHECKING:
        # Contratto host↔mixin (solo per il type-checker; nessun effetto a
        # runtime): gli attributi di ``PlanDriver.__init__`` e i metodi degli
        # altri pezzi che questi metodi usano.
        _index: dict[str, _Live]
        host: PlanHost
        store: PlanStore

        async def _changed(self, plan: Plan) -> None: ...
        def _drafts_or_empty(self, plan: Plan) -> list[dict[str, str]]: ...
        def _failed_at_boot(self, plan: Plan, error: str, *, kind: Any=None) -> Plan: ...
        def _journal_line(self, plan: Plan, key: str) -> None: ...
        def _load(self, plan_id: str) -> Plan: ...
        def _lock(self, session_key: str) -> asyncio.Lock: ...
        def _remember(self, plan: Plan) -> None: ...
        def _require_idle(self, session_key: str, *, code: str='busy') -> None: ...
        def holds_notebook(self, notebook: str) -> bool: ...

    def _refuse_if_held(self, plan: Plan) -> None:
        """``plan_running`` se un piano più nuovo tiene il quaderno di *plan*.

        Tornare indietro con un piano vecchio mentre uno nuovo gira riscriverebbe
        il quaderno sotto i passi di quest'ultimo, e il suo ritorno troverebbe
        i suoi file cambiati da un altro: è la stessa regola per cui
        l'utente non scrive in un quaderno tenuto.
        """
        live = self._index.get(plan.session_key)
        busy = (self.holds_notebook(plan.notebook) if plan.notebook else
                live is not None and live.plan_id != plan.id and live.status in HELD_STATUSES)
        if busy:
            raise PlanCommandError(
                "plan_running",
                f"notebook {plan.notebook!r} is held by a running plan: wait for it, "
                "or stop it first",
            )

    async def rollback_preview(self, plan_id: str) -> dict[str, Any]:
        """Il conto del ritorno, e il gettone dei file com'erano quando è stato fatto.

        ``expect_end`` è quel gettone (``write_journal.undo_set``): la conferma
        (:meth:`rollback`) lo rifà e, se è un altro, i file sono cambiati dopo che
        l'utente ha letto il conto — ``conflict``.
        """
        plan = self._load(plan_id)
        if plan.status not in ("done", "stopped"):
            raise PlanCommandError("bad_state", f"the plan is {plan.status}")
        self._refuse_if_held(plan)
        place = place_of(plan)
        async with self._lock(plan.session_key):
            report = await asyncio.to_thread(self._undo, plan, dry_run=True)
        return {"report": card_report(report, place), "expect_end": report.token}

    def _undo(self, plan: Plan, *, dry_run: bool = False, keep: Iterable[str] = (),
              token: str | None = None, into: str | None = None, of: str | None = None) -> Any:
        """Un ``undo_set`` sui registri del piano (o su quello del ritorno *of*)."""
        from jenny.agent.write_journal import undo_set

        root = self.store.workspace
        place = place_of(plan)
        sources = [work.journal(root, of)] if of else work.plan_journals(root, plan)
        return undo_set(
            sources, by="user", into=work.journal(root, into) if into else None,
            keep_groups=keep, group_of=place.group_of, never=place.never,
            dry_run=dry_run, token=token,
        )

    async def rollback(
        self, plan_id: str, keep: Iterable[Any] = (), expect_end: Any = None
    ) -> dict[str, Any]:
        """Torna a prima: annulla il lavoro del piano in un registro suo, ``rolled_back``.

        *expect_end* è il gettone che :meth:`rollback_preview` ha ritornato (i
        file su cui l'utente ha letto il conto). Il registro del ritorno si
        scrive nel piano **prima** di toccare un file (``pending_undo``): un
        crollo a metà lo finisce il boot, e il piano sa dove guardare.
        """
        plan = self._load(plan_id)
        keep_groups = _keep_groups(keep, place_of(plan))
        async with self._lock(plan.session_key):
            plan = self._load(plan_id)
            if plan.status not in ("done", "stopped"):
                raise PlanCommandError("bad_state", f"the plan is {plan.status}")
            self._refuse_if_held(plan)
            self._require_idle(plan.session_key, code="conflict")
            if not isinstance(expect_end, str) or not expect_end:
                raise PlanCommandError(
                    "conflict", "the notebook changed since the summary was shown: read it again"
                )
            into = await asyncio.to_thread(self._start_undo, plan_id, _KIND_ROLLBACK, keep_groups)
            report = await self._run_rollback(
                plan, _KIND_ROLLBACK, self._undo, plan, keep=keep_groups, token=expect_end,
                into=into,
            )
            counts = card_report(report, place_of(plan))["counts"]
            plan = await asyncio.to_thread(
                self.store.update, plan_id,
                lambda p: _mark_rolled_back(p, journal=into, keep=keep_groups, counts=counts),
            )
            self._remember(plan)
            discarded = await asyncio.to_thread(self._discard_plan_drafts, plan)
            self.host.append_note(
                plan.session_key, _rollback_note(plan, counts, len(discarded))
            )
            await asyncio.to_thread(self._journal_line, plan, "plan.journal_rolled_back")
            logger.info("Plan {} rolled back: {}", plan_id, counts)
        await self._changed(plan)
        return {
            "report": card_report(report, place_of(plan)),
            "discarded_drafts": discarded,
        }

    def _start_undo(self, plan_id: str, kind: str, keep: Iterable[str] = ()) -> str:
        """Il registro di un ritorno (o del suo annullamento), scritto nel piano prima
        di toccare un file. Ritorna la sua chiave."""
        chosen: list[str] = []

        def _apply(p: Plan) -> None:
            n = int(p.extra.get(_UNDO_COUNT) or 0) + 1
            key = work.undo_journal_key(p.id, n)
            p.extra[_UNDO_COUNT] = n
            p.extra[PENDING_UNDO_KEY] = {"journal": key, "kind": kind, "keep": sorted(keep),
                                         "of": p.rollback_journal if kind == _KIND_UNDO else None}
            chosen.append(key)

        self.store.update(plan_id, _apply)
        return chosen[-1]

    def _discard_plan_drafts(self, plan: Plan) -> list[dict[str, str]]:
        """Butta le bozze del piano ancora in attesa (sincrono).

        Dopo un ritorno il quaderno è com'era prima del piano: una bozza del
        piano rimasta lì si potrebbe ancora installare dalla scheda delle bozze,
        e rimetterebbe un pezzo del lavoro appena disfatto. L'annullamento del
        ritorno non le rimette: non erano mai state installate.
        """
        discarded: list[dict[str, str]] = []
        for draft in self._drafts_or_empty(plan):
            try:
                if self.host.discard_draft(draft["id"]):
                    discarded.append(draft)
            except Exception:  # noqa: BLE001 — una bozza rimasta non disfa il ritorno
                logger.opt(exception=True).warning(
                    "Plan {}: draft {} not discarded", plan.id, draft["id"]
                )
        if discarded:
            logger.info("Plan {}: {} pending draft(s) discarded with the rollback",
                        plan.id, len(discarded))
        return discarded

    async def undo_rollback(self, plan_id: str) -> dict[str, Any]:
        """Annulla il ritorno: annulla il registro del ritorno, e lo stato di prima."""
        plan = self._load(plan_id)
        async with self._lock(plan.session_key):
            plan = self._load(plan_id)
            if plan.status != "rolled_back":
                raise PlanCommandError("bad_state", f"the plan is {plan.status}")
            if not plan.rollback_journal:
                raise PlanCommandError("bad_state", "there is no rollback to undo")
            self._refuse_if_held(plan)
            self._require_idle(plan.session_key, code="conflict")
            of = plan.rollback_journal
            into = await asyncio.to_thread(self._start_undo, plan_id, _KIND_UNDO)
            report = await self._run_rollback(plan, _KIND_UNDO, self._undo, plan, into=into, of=of)
            counts = card_report(report, place_of(plan))["counts"]
            plan = await asyncio.to_thread(
                self.store.update, plan_id,
                lambda p: _mark_rollback_undone(p, counts, journal=into),
            )
            self._remember(plan)
            self.host.append_note(plan.session_key, _undo_note(plan, counts))
            await asyncio.to_thread(self._journal_line, plan, "plan.journal_rollback_undone")
        await self._changed(plan)
        return {"report": card_report(report, place_of(plan))}

    async def _run_rollback(self, plan: Plan, kind: str, fn: Any, *args: Any, **kw: Any) -> Any:
        """Esegue un ritorno in un thread e traduce i suoi errori per la scheda.

        I file cambiati dopo il conto sono ``conflict``, e niente è stato
        toccato. Un errore a metà (il disco) resta scritto nel piano
        (``rollback_failed``) e risponde ``rollback_failed``: i file sono a metà,
        e la scheda lo deve dire invece di mostrare un piano finito come se
        niente fosse.
        """
        from jenny.agent.write_journal import UndoConflictError

        try:
            return await asyncio.to_thread(fn, *args, **kw)
        except UndoConflictError as exc:
            await asyncio.to_thread(self.store.update, plan.id, _drop_pending_undo)
            raise PlanCommandError("conflict", str(exc)) from exc
        except Exception as exc:
            await asyncio.to_thread(self._record_rollback_failure, plan.id, kind, str(exc))
            await self._changed(self._load(plan.id))
            raise PlanCommandError(
                "rollback_failed",
                f"the rollback stopped halfway: {exc}. The files are partly rolled back; "
                "try again",
            ) from exc

    def _record_rollback_failure(
        self,
        plan_id: str,
        kind: str,
        error: str,
        *,
        applied: int | None = None,
        total: int | None = None,
    ) -> Plan:
        """Scrive nel piano che un ritorno non è finito (sincrono)."""
        stamp = now_iso()
        failure: dict[str, Any] = {"at": stamp, "kind": kind, "error": _cut(error, 500, "error")}
        if applied is not None:
            failure["applied"] = applied
        if total is not None:
            failure["total"] = total

        def _apply(p: Plan) -> None:
            p.extra[ROLLBACK_FAILED_KEY] = failure
            p.history.append({"at": stamp, "event": "rollback_failed", **failure})

        plan = self.store.update(plan_id, _apply)
        logger.error("Plan {}: {} did not complete: {}", plan_id, kind, error)
        return plan

    def _recover_undo(self, plan: Plan) -> Plan:
        """Chiude al boot un ritorno (o un annullamento) rimasto a metà.

        I file li ha già finiti il container, prima dell'agente
        (``write_journal.finish_interrupted_undos``): qui si legge come è
        andata e il piano va dove il ritorno l'avrebbe portato — ``rolled_back``,
        o lo stato di prima per un annullamento — e Jenny ne riceve la nota,
        come per un ritorno normale. Un annulla che il diario non conosce (un
        ripristino da backup l'ha svuotato) lascia il piano com'è con
        ``rollback_failed``.
        """
        from jenny.agent.write_journal import read_undo_state

        pending = plan.extra.get(PENDING_UNDO_KEY) or {}
        key = str(pending.get("journal") or "")
        kind = pending.get("kind")
        state = read_undo_state(self.store.workspace, key) if key else None
        if state is None or state.get("state") != "done":
            self.store.update(plan.id, _drop_pending_undo)
            return self._failed_at_boot(plan, "the rollback was interrupted and not finished",
                                        kind=kind)
        from jenny.agent.write_journal import UndoReport

        counts = card_report(UndoReport.from_dict(state), place_of(plan))["counts"]
        keep = [str(g) for g in pending.get("keep") or ()]

        def _settle(p: Plan) -> None:
            if kind == _KIND_ROLLBACK and p.status in ("done", "stopped"):
                _mark_rolled_back(p, journal=key, keep=keep, counts=counts, recovered=True)
            elif kind == _KIND_UNDO and p.status == "rolled_back":
                _mark_rollback_undone(p, counts, journal=key, recovered=True)
            else:
                p.extra.pop(PENDING_UNDO_KEY, None)

        updated = self.store.update(plan.id, _settle)
        if updated.status == plan.status:
            return updated
        note = _undo_note(updated, counts) if kind == _KIND_UNDO else _rollback_note(
            updated, counts
        )
        try:
            self.host.append_note(updated.session_key, note)
        except Exception:  # noqa: BLE001 — la nota persa non vale un boot fallito
            logger.opt(exception=True).warning("Plan {}: recovery note not saved", plan.id)
        self._journal_line(
            updated,
            "plan.journal_rollback_undone" if kind == _KIND_UNDO else "plan.journal_rolled_back",
        )
        logger.warning("Plan {}: interrupted {} finished at boot", plan.id, kind)
        return updated
