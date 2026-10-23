"""Il piano a lista nei test: un ospite e un bus finti, e un quaderno da fotografare.

L'ospite finto risponde alle domande che il driver fa all'agente (turno in volo,
aiutanti, cancellazione, goal, cartella del quaderno, note); il bus finto
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
        self.goals: set[str] = set()
        self.cancelled: list[str] = []
        self.notes: list[tuple[str, str]] = []
        self.root_lookups = 0

    def turn_in_flight(self, session_key: str) -> bool:
        return session_key in self.busy

    def running_helpers(self, session_key: str) -> frozenset[str]:
        return frozenset(self.helpers.get(session_key, ()))

    async def cancel(self, session_key: str) -> int:
        self.cancelled.append(session_key)
        count = len(self.helpers.get(session_key, ())) + (session_key in self.busy)
        if not self.abandon:
            self.helpers.pop(session_key, None)
            self.busy.discard(session_key)
        return count

    def goal_active(self, session_key: str) -> bool:
        return session_key in self.goals

    def notebook_root(self, notebook: str) -> Path | None:
        self.root_lookups += 1
        root = self.workspace / "wikis" / notebook
        return root if root.is_dir() else None

    def append_note(self, session_key: str, text: str) -> None:
        self.notes.append((session_key, text))


def write_file(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


STEPS = [
    {"title": "Cerco Acero e Ginkgo", "detail": "Due ricerche, una per pianta.", "kind": "research"},
    {"title": "Scrivo le pagine", "detail": "Una pagina per pianta in wiki/.", "kind": "write"},
    {"title": "Controllo i link", "detail": "Ogni link della wiki porta a una pagina.",
     "kind": "check"},
]
