"""Gli strumenti di Jenny dicono il vero su un aiutante fermato al budget.

Prima del 24/10/2026 ``subagent_send`` prometteva di riprendere un aiutante
«appena finito» e di rilanciare da zero uno «fallito» — e un aiutante fermato al
budget era ``failed``, con la storia cancellata. Ora si riprende davvero, e le
descrizioni e l'elenco di ``subagent_status`` lo dicono.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

from jenny.agent.tools.subagent_control import (
    SubagentRestartTool,
    SubagentSendTool,
    SubagentStatusTool,
)


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_send_says_a_budget_stop_resumes_with_its_work() -> None:
    description = _flat(SubagentSendTool(manager=SimpleNamespace()).description)
    assert "stopped at its budget" in description
    assert "what it read and wrote" in description


def test_restart_points_a_budget_stop_to_send() -> None:
    description = _flat(SubagentRestartTool(manager=SimpleNamespace()).description)
    assert "starts again from zero" in description
    assert "stopped at its budget use subagent_send" in description


def test_the_status_list_marks_a_resumable_helper() -> None:
    entry = {
        "task_id": "abcd1234", "lineage_id": "lin1", "label": "plants", "agent_type": "writer",
        "state": "budget", "stop_reason": "max_iterations", "ended_at": time.time(),
        "can_restart": True, "resumable": True, "result_summary": "half done",
    }
    line = SubagentStatusTool._recent_line(entry, preview=200)
    assert "state=budget" in line
    assert "resumable=yes (continue it with subagent_send)" in line
    assert "resumable" not in SubagentStatusTool._recent_line(
        {**entry, "resumable": False}, preview=200
    )
