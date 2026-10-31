"""I campi nuovi del record su disco: ``budget``, ``handled_at``, ``resumable``, il passo.

Un record si rilegge dopo ogni riavvio del gateway: uno stato che il modulo non
conosce degradava a ``done``, quindi senza ``budget`` fra gli stati validi un
aiutante fermato al budget sarebbe risultato finito bene dopo un riavvio.
"""

from __future__ import annotations

import json

from jenny.agent.subagent_records import SubagentRecord, SubagentRecordStore, SubagentSpec


def _record(**kw) -> SubagentRecord:
    spec = SubagentSpec(task="t", label="l", session_key="project:p",
                        plan_id="plan-1", plan_step="s2")
    return SubagentRecord(task_id="abcd1234", lineage_id="lin1", attempt=1, spec=spec,
                          state="budget", ended_at=1000.0, **kw)


def test_a_budget_record_survives_the_round_trip() -> None:
    raw = json.loads(json.dumps(_record(handled_at=1001.5, resumable=True).to_dict()))
    back = SubagentRecord.from_dict(raw)
    assert back.state == "budget"
    assert back.handled_at == 1001.5
    assert back.resumable is True
    assert (back.spec.plan_id, back.spec.plan_step) == ("plan-1", "s2")


def test_an_old_record_without_the_new_keys_still_loads() -> None:
    raw = _record().to_dict()
    for key in ("handled_at", "resumable"):
        raw.pop(key)
    for key in ("plan_id", "plan_step"):
        raw["spec"].pop(key)
    back = SubagentRecord.from_dict(raw)
    assert back.handled_at is None and back.resumable is False
    assert back.spec.plan_id is None and back.spec.plan_step is None


def test_the_store_marks_handled_once(tmp_path) -> None:
    store = SubagentRecordStore(tmp_path, ttl_s=0)
    store.append(_record())

    assert store.mark_handled("abcd1234", 2000.0).handled_at == 2000.0
    assert store.mark_handled("abcd1234", 3000.0).handled_at == 2000.0
    assert store.load("project:p")[-1].handled_at == 2000.0
    assert store.mark_handled("missing0", 1.0) is None
