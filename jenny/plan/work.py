"""Il lavoro di un piano, letto dai registri dell'annulla.

Un piano non fotografa niente: quel che ha fatto sono le righe dei registri
(``agent.write_journal``) di chi ha lavorato per lui — il suo (``plan-<id>``:
i turni di Jenny nei passi, le scritture del driver) e quelli degli aiutanti dei
passi (``Step.helpers``). Ogni riga porta il passo per cui si scriveva, quindi
«cosa ha fatto il passo 2» è un filtro, non un confronto fra due foto.

I percorsi sono relativi al workspace; li traduce per la scheda il posto
(``plan.place.PlanPlace.shown``).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from jenny.agent.write_journal import WriteJournal
    from jenny.plan.model import Plan, Step

# ``jenny.agent`` al primo import carica il loop, che importa il driver dei
# piani: gli import del registro stanno dentro le funzioni.

PLAN = "plan"
UNDO = "undo"


def plan_journal_key(plan_id: str) -> str:
    return f"{PLAN}-{plan_id}"


def undo_journal_key(plan_id: str, n: int) -> str:
    return f"{UNDO}-{plan_id}-{n}"


def journal(root: Path, key: str) -> WriteJournal:
    from jenny.agent.write_journal import journal_for

    return journal_for(root, key)


def plan_journal(root: Path, plan: Plan) -> WriteJournal:
    return journal(root, plan_journal_key(plan.id))


def helper_keys(plan: Plan, step: Step | None = None) -> list[str]:
    steps = [step] if step is not None else plan.steps
    seen: dict[str, None] = {}
    for s in steps:
        for key in s.helpers:
            seen.setdefault(key, None)
    return list(seen)


def plan_journals(root: Path, plan: Plan) -> list[WriteJournal]:
    """Il registro del piano e quelli dei suoi aiutanti."""
    return [plan_journal(root, plan), *(journal(root, k) for k in helper_keys(plan))]


def step_journals(root: Path, plan: Plan, step: Step) -> list[WriteJournal]:
    """Il registro del piano e quelli degli aiutanti del passo (le righe si
    filtrano poi col passo: :func:`files`, :func:`first_texts`)."""
    return [plan_journal(root, plan), *(journal(root, k) for k in helper_keys(plan, step))]


def _rows(journals: list[WriteJournal], step: str | None) -> list[dict[str, Any]]:
    rows = [row for journal in journals for row in journal.entries()
            if step is None or row.get("step") == step]
    rows.sort(key=lambda r: r.get("ts") or 0.0)
    return rows


def files(journals: list[WriteJournal], *, step: str | None = None) -> dict[str, list[str]]:
    """I file nuovi, cambiati e tolti, dal primo «prima» all'ultimo «dopo».

    Un file nato e tolto nello stesso lavoro non conta: non è cambiato niente.
    Le cartelle non contano: la casa dice «N file».
    """
    chains: dict[str, list[dict[str, Any]]] = {}
    for row in _rows(journals, step):
        if not row["path"].endswith("/"):
            chains.setdefault(row["path"], []).append(row)
    from jenny.agent.write_journal import DIR

    out: dict[str, list[str]] = {"added": [], "changed": [], "removed": []}
    for path, chain in sorted(chains.items()):
        first, last = chain[0].get("before"), chain[-1].get("after")
        if first == last or DIR in (first, last):
            continue
        if first is None:
            out["added"].append(path)
        elif last is None:
            out["removed"].append(path)
        else:
            out["changed"].append(path)
    return out


def first_texts(journals: list[WriteJournal], *, under: str, suffix: str = ".md",
                step: str | None = None) -> dict[str, str]:
    """Il testo di ogni file sotto *under* com'era prima che il lavoro lo toccasse.

    ``""`` per un file che non c'era; manca un file il cui «prima» non è stato
    salvato (troppo grande): chi confronta lo tratta come non letto.
    """
    by_key = {j.key: j for j in journals}
    firsts: dict[str, tuple[str | None, str, float]] = {}
    for journal in journals:
        for row in journal.entries():
            path = row["path"]
            if not path.startswith(under) or not path.endswith(suffix):
                continue
            if step is not None and row.get("step") != step:
                continue
            ts = float(row.get("ts") or 0.0)
            known = firsts.get(path)
            if known is None or ts < known[2]:
                firsts[path] = (row.get("before"), journal.key, ts)
    out: dict[str, str] = {}
    for path, (before, key, _ts) in firsts.items():
        if before is None:
            out[path] = ""
            continue
        content = by_key[key].saved_content(str(before))
        if content is not None:
            out[path] = content.decode("utf-8", errors="replace")
    return out


def kept_journal_keys(workspace: Path) -> set[str]:
    """I registri che i piani nello store nominano ancora: il loro, quelli dei
    ritorni e quelli degli aiutanti dei passi. La pulizia dei registri li tiene."""
    from jenny.plan.store import PlanStore

    keys: set[str] = set()
    for plan in PlanStore.for_workspace(workspace).list_all():
        keys.add(plan_journal_key(plan.id))
        keys.update(helper_keys(plan))
        count = plan.extra.get("undo_count")
        for n in range(1, (count if isinstance(count, int) else 0) + 1):
            keys.add(undo_journal_key(plan.id, n))
    return keys
