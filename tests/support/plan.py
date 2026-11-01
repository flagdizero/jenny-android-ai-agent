"""Il piano a lista nei test: un ospite e un bus finti, e un quaderno da fotografare.

L'ospite finto risponde alle domande che il driver fa all'agente (turno in volo,
aiutanti, cancellazione, cartella del quaderno, note); il bus finto
raccoglie quel che il driver pubblica. Lo usano i test del driver
(``tests/plan``) e quelli delle RPC (``tests/webui``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

KEY = "project:piante"


class FakeBus:
    def __init__(self) -> None:
        self.inbound: list[Any] = []
        self.outbound: list[Any] = []

    async def publish_inbound(self, msg: Any) -> None:
        self.inbound.append(msg)

    async def publish_outbound(self, msg: Any) -> None:
        self.outbound.append(msg)


class FakeHost:
    """L'ospite finto. ``abandon=True`` fa come il loop vero con un turno che non
    muore entro la grazia: ``cancel`` lo abbandona, e il turno resta in volo."""

    def __init__(self, workspace: Path, *, abandon: bool = False) -> None:
        self.workspace = workspace
        self.abandon = abandon
        self.busy: set[str] = set()
        self.helpers: dict[str, set[str]] = {}
        self.cancelled: list[str] = []
        self.notes: list[tuple[str, str]] = []
        self.root_lookups = 0
        # Gli aiutanti interrotti da un riavvio, come ``SubagentManager.interrupted_helpers``.
        self.interrupted: list[dict[str, Any]] = []

    def turn_in_flight(self, session_key: str) -> bool:
        return session_key in self.busy

    def interrupted_helpers(self, session_key: str) -> list[dict[str, Any]]:
        return list(self.interrupted)

    def running_helpers(self, session_key: str) -> frozenset[str]:
        return frozenset(self.helpers.get(session_key, ()))

    async def cancel(self, session_key: str) -> int:
        self.cancelled.append(session_key)
        count = len(self.helpers.get(session_key, ())) + (session_key in self.busy)
        if not self.abandon:
            self.helpers.pop(session_key, None)
            self.busy.discard(session_key)
        return count

    def notebook_root(self, notebook: str) -> Path | None:
        self.root_lookups += 1
        root = self.workspace / "wikis" / notebook
        return root if root.is_dir() else None

    def append_note(self, session_key: str, text: str) -> None:
        self.notes.append((session_key, text))

    def notebook_drafts(self, notebook: str) -> list[dict[str, Any]]:
        from jenny.agent.staged_writes import list_staged

        prefix = f"wikis/{notebook}/"
        return [
            {"id": m["id"], "target": m["target"], "created_ms": m.get("created_ms")}
            for m in list_staged(self.workspace)
            if str(m.get("target", "")).startswith(prefix)
        ]

    def discard_draft(self, draft_id: str) -> bool:
        from jenny.agent.staged_writes import discard

        return discard(self.workspace, draft_id)


# Una copertura minima e valida: ``plan_propose`` la vuole sempre.
COVERAGE = [
    {"item": "Acero e Ginkgo", "have": "una pagina vecchia", "missing": "le cure",
     "strategy": "notebook", "why": ""},
]


async def start_drafting(driver: Any, key: str = KEY, *, reads: int = 1) -> None:
    """Apre la pianificazione come il tasto Piano, con *reads* letture del quaderno."""
    await driver.start_drafting(key)
    for _ in range(reads):
        await driver.note_read(key)


async def propose(driver: Any, title: str, steps: Any, *, key: str = KEY, **kw: Any) -> str:
    """Una proposta come la fa il modello: dentro una pianificazione che ha letto."""
    await start_drafting(driver, key)
    kw.setdefault("coverage", COVERAGE)
    return await driver.propose(key, title, steps, **kw)


def write_file(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


STEPS = [
    {"title": "Cerco Acero e Ginkgo", "detail": "Due ricerche, una per pianta.", "kind": "research",
     "done_when": "Una fonte salvata per pianta.", "covers": ["c1"]},
    {"title": "Scrivo le pagine", "detail": "Una pagina per pianta in wiki/.", "kind": "write",
     "done_when": "Due pagine, ognuna con una fonte."},
    {"title": "Controllo i link", "detail": "Ogni link della wiki porta a una pagina.",
     "kind": "check", "done_when": "Zero link morti."},
]
