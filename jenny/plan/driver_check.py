"""Il controllo a macchina dell'ultimo passo di un piano di quaderno.

L'audit del quaderno, i fatti persi, le lacune: quel che il codice verifica da sé.

Un pezzo di :class:`jenny.plan.driver.PlanDriver` (mixin): il testo dei metodi è
quello di prima, spostato qui per responsabilità.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from jenny.plan import work
from jenny.plan.audit import (
    Audit,
    audit_notebook,
    baseline_keys,
    empty_folders,
    remove_empty_folders,
    render_repair,
    summary_line,
    wiki_texts,
)
from jenny.plan.driver_common import (
    _AUDIT_BASELINE,
    _EMPTY_AT_START,
    _GAPS_LISTED,
    _MACHINE,
    OPEN_GAPS_PATH,
    _find,
    _machine_view,
    place_of,
)
from jenny.plan.facts import LostFacts, lost_facts
from jenny.plan.model import Plan, Step, now_iso
from jenny.utils.path import atomic_write

if TYPE_CHECKING:
    from collections.abc import Iterator
    from contextlib import contextmanager

    from jenny.plan.driver_common import PlanHost
    from jenny.plan.store import PlanStore


# I giri di riparazione al massimo: il secondo controllo accetta comunque, e
# quel che resta lo dice la nota.
_MAX_REPAIR_ROUNDS = 2


def _norm(text: str) -> str:
    """Una voce normalizzata per il confronto: spazi compressi, minuscole."""
    return " ".join(text.replace("**", "").split()).strip().lower()


def _repair_refusal(audit: Audit) -> str:
    """Il rifiuto del primo giro: cosa resta, raggruppato per pagina, e che è l'ultimo giro."""
    lines = [
        "Not closed yet: the code checked the notebook again, and these are still wrong "
        f"(or new). {summary_line(audit)}",
    ]
    for group in render_repair(audit):
        lines.append(f"- {group['path']}")
        lines.extend(f"  - {item}" for item in group["items"])
    lines.append(
        "Repair only these, then call `plan_step_done` again. This is the last round: "
        "after it the step closes, and what is still wrong goes in the plan's report."
    )
    return "\n".join(lines)


class MachineCheckMixin:
    """L'audit del quaderno, i fatti persi, le lacune: quel che il codice verifica da sé.

    Mixin di ``PlanDriver``.
    """

    if TYPE_CHECKING:
        # Contratto host↔mixin (solo per il type-checker; nessun effetto a
        # runtime): gli attributi di ``PlanDriver.__init__`` e i metodi degli
        # altri pezzi che questi metodi usano.
        host: PlanHost
        store: PlanStore

        async def _advance(self, plan: Plan) -> None: ...
        async def _changed(self, plan: Plan) -> None: ...
        @contextmanager
        def _journaled(self, plan: Plan, step_id: str) -> Iterator[Any]: ...
        def _remember(self, plan: Plan) -> None: ...
        async def _settle_closed(self, plan: Plan) -> Plan: ...

    async def _list_gaps(self, plan: Plan, step: Step, gaps: Mapping[str, list[str]]) -> int:
        """I buchi rimasti di *step* in ``raw/notes/open-gaps.md`` del quaderno. Quanti nuovi.

        Un file solo, una sezione per passo (data, piano, passo), una riga per
        voce; una voce già nel file (stesso testo, spazi normalizzati) non si
        riscrive. Scritto qui, sotto il lock, nel registro del piano col passo
        (``_journaled``) e **prima** dei conti di fine passo (``_settle_closed``):
        è lavoro del passo, e il ritorno lo toglie.
        Senza la radice del quaderno non si scrive niente, e lo si dice.
        """
        items: list[str] = []
        for lineage_items in gaps.values():
            for item in lineage_items:
                if item not in items:
                    items.append(item)
        root = self._notebook_root(plan)
        if root is None:
            logger.warning("Plan {}: notebook root missing, {} gap(s) not listed", plan.id, len(items))
            return 0
        title, step_id, step_title = plan.title, step.id, step.title

        def _write() -> int:
            with self._journaled(plan, step_id) as journal:
                return _write_gaps(journal)

        def _write_gaps(journal: Any) -> int:
            path = root / OPEN_GAPS_PATH
            journal.touch(path)
            existing = path.read_text(encoding="utf-8") if path.is_file() else ""
            known = {_norm(line[2:]) for line in existing.splitlines() if line.startswith("- ")}
            fresh = [item for item in items if _norm(item) not in known]
            if not fresh:
                return 0
            head = "" if existing else (
                "# Open gaps\n\nFacts the plans could not find, step by step: searched and "
                "not found, or left for later. The pages say \"not documented\" where they "
                "belong; the next plan reads this list and plans research for what it needs.\n"
            )
            section = (
                f"\n## {now_iso()[:10]} — {title} — {step_id} «{step_title}»\n\n"
                + "".join(f"- {item}\n" for item in fresh)
            )
            atomic_write(path, existing + head + section)
            return len(fresh)

        count = await asyncio.to_thread(_write)

        def _apply(p: Plan) -> None:
            _find(p, step_id).extra[_GAPS_LISTED] = items

        await asyncio.to_thread(self.store.update, plan.id, _apply)
        logger.info(
            "Plan {}: {} gap(s) of step {} listed in {} ({} new)",
            plan.id, len(items), step_id, OPEN_GAPS_PATH, count,
        )
        return len(items)

    def _audit_baseline(self, plan: Plan) -> tuple[list[str], list[str]]:
        """I difetti del quaderno all'Approva (:func:`baseline_keys`), che non sono del
        piano, e le sue cartelle vuote (:func:`empty_folders`), che restano.

        Senza ``expect``: all'Approva le pagine attese non ci sono ancora, e
        sono proprio il lavoro del piano. Un errore qui non ferma l'Approva: la
        base è vuota, e il controllo chiederà anche i difetti di prima.
        """
        root = self._notebook_root(plan)
        if root is None:
            return [], []
        try:
            return baseline_keys(root), empty_folders(root)
        except Exception:  # noqa: BLE001 — un aiuto al controllo, non una condizione dell'Approva
            logger.opt(exception=True).warning("Plan {}: audit baseline not computed", plan.id)
            return [], []

    def _notebook_root(self, plan: Plan) -> Path | None:
        """La cartella del quaderno del piano; ``None`` per un piano della chat personale.

        ``host.notebook_root("")`` non dà ``None``: dà la cartella dei quaderni.
        Il 04/11/2026 il passo «Controllo i numeri» di un piano personale ha
        controllato quella, ha trovato 0 pagine e si è chiuso da solo con «tutto a
        posto», senza che nessuno guardasse le cifre.
        """
        if not plan.notebook:
            return None
        return self.host.notebook_root(plan.notebook)

    def _run_audit(self, plan: Plan, step: Step) -> Audit | None:
        """Il controllo a macchina del quaderno, in un thread; ``None`` se non si può fare.

        Prima toglie le cartelle vuote sotto ``wiki/``: è meccanico, e non c'è
        motivo di chiederlo al modello. ``None`` (quaderno sparito, errore) fa
        aprire il passo come prima, col controllo affidato al modello.
        """
        root = self._notebook_root(plan)
        if root is None:
            return None
        try:
            with self._journaled(plan, step.id) as journal:
                removed = remove_empty_folders(root, plan.extra.get(_EMPTY_AT_START) or (),
                                               before_remove=journal.touch)
            audit = audit_notebook(
                root,
                expect=step.extra.get("expect") or None,
                baseline=plan.extra.get(_AUDIT_BASELINE) or (),
            )
        except Exception:  # noqa: BLE001 — senza controllo il passo lo fa il modello
            logger.opt(exception=True).warning("Plan {}: machine check failed", plan.id)
            return None
        audit.removed_dirs = len(removed)
        logger.info(
            "Plan {}: machine check of step {}: {} to repair, {} empty folder(s) removed",
            plan.id, step.id, len(audit.findings), len(removed),
        )
        return audit

    def _first_pages(self, plan: Plan, step: Step | None = None) -> dict[str, str] | None:
        """Le pagine ``wiki/`` toccate dal piano (o dal passo) com'erano prima.

        Chiavi relative al quaderno, come ``audit.wiki_texts``. Solo quelle
        toccate: le altre sono com'erano, e sono già fra le pagine di adesso.
        """
        if not plan.notebook:
            return None
        place = place_of(plan)
        root = self.store.workspace
        journals = (work.step_journals(root, plan, step) if step is not None
                    else work.plan_journals(root, plan))
        texts = work.first_texts(journals, under=place.prefix + "wiki/",
                                 step=step.id if step is not None else None)
        return {place.shown(path): text for path, text in texts.items()}

    def _lost_now(self, plan: Plan) -> int | None:
        """Quanti fatti di prima del piano mancano adesso dalle pagine; ``None`` se non si sa.

        ``None`` e non 0: un conteggio saltato non è «niente perso», e la nota lo
        dice («not counted»).
        """
        lost = self._lost_facts(plan)
        return lost.total if lost is not None else None

    def _lost_since(self, plan: Plan, step: Step) -> LostFacts | None:
        """I fatti delle pagine che il passo ha toccato, com'erano alla sua
        apertura, che adesso non sono in nessuna pagina."""
        return self._lost_facts(plan, step)

    def _lost_facts(self, plan: Plan, step: Step | None = None) -> LostFacts | None:
        """I fatti delle pagine di prima del piano (o del passo) che adesso non ci sono.

        In un thread. Un errore qui non ferma il passo: si apre senza il blocco,
        e il log lo dice.
        """
        root = self.host.notebook_root(plan.notebook) if plan.notebook else None
        if root is None:
            return None
        try:
            before = self._first_pages(plan, step)
            if before is None:
                return None
            return lost_facts(before, wiki_texts(root))
        except Exception:  # noqa: BLE001 — un aiuto al controllo, non una condizione del passo
            logger.opt(exception=True).warning("Plan {}: lost facts not computed", plan.id)
            return None

    async def _note_machine(self, plan: Plan, step_id: str, view: dict[str, Any]) -> Plan:
        def _apply(p: Plan) -> None:
            _find(p, step_id).extra[_MACHINE] = view

        plan = await asyncio.to_thread(self.store.update, plan.id, _apply)
        self._remember(plan)
        return plan

    async def _close_by_machine(self, plan: Plan, step_id: str, audit: Audit) -> None:
        """Il passo ``check`` senza niente da riparare: si chiude da solo, senza un turno."""
        view = _machine_view(audit, round_=0, final=True)

        def _close(p: Plan) -> None:
            s = _find(p, step_id)
            s.status = "done"
            s.ended_at = now_iso()
            s.note = None
            s.extra[_MACHINE] = view

        plan = await asyncio.to_thread(self.store.update, plan.id, _close)
        self._remember(plan)
        logger.info("Plan {}: step {} closed by the machine check", plan.id, step_id)
        plan = await self._settle_closed(plan)
        await self._changed(plan)
        await self._advance(plan)

    async def _recheck(self, plan: Plan, step: Step, outcome: str) -> str | None:
        """Il ricontrollo a ``plan_step_done`` del passo ``check``: un rifiuto, o ``None``.

        Al primo giro, con difetti ancora aperti, rifiuta una volta e dà la lista
        nuova. Al secondo (o con ``plan_step_skip``) accetta, e scrive nel passo
        quel che resta: la nota del codice, che la scheda mostra accanto a quella
        del modello. I fatti persi non si richiedono: la macchina non sa se uno è
        andato via apposta; se ne conta il numero.
        """
        machine = step.extra.get(_MACHINE)
        if step.kind != "check" or not isinstance(machine, dict) or machine.get("final"):
            return None
        audit = await asyncio.to_thread(self._run_audit, plan, step)
        if audit is None:
            return None
        round_ = int(machine.get("round") or 1)
        if audit.findings and outcome == "done" and round_ < _MAX_REPAIR_ROUNDS:
            await self._note_machine(plan, step.id, _machine_view(audit, round_=round_ + 1))
            logger.info(
                "Plan {}: step {} close refused, {} left after round {}",
                plan.id, step.id, len(audit.findings), round_,
            )
            return _repair_refusal(audit)
        view = _machine_view(audit, round_=round_, final=True)
        view["lost_left"] = await asyncio.to_thread(self._lost_now, plan)
        await self._note_machine(plan, step.id, view)
        return None
