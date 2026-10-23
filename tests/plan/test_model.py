"""Il modello del piano: transizioni, validazione di una proposta, tag della v2."""

from __future__ import annotations

import pytest

from jenny.plan.model import (
    MAX_STEP_DETAIL,
    PLAN_STATUSES,
    PLAN_TRANSITIONS,
    Plan,
    PlanFormatError,
    PlanTransitionError,
    PlanValidationError,
    Step,
    apply_transition,
    current_step,
    next_pending_step,
    step_counts,
    tag_new_version,
    validate_proposal,
)


def _steps(*titles: str) -> list[dict[str, str]]:
    return [{"title": t, "detail": f"do {t}", "done_when": f"{t} done"} for t in titles]


COV = [{"item": "Acero", "have": "una pagina", "missing": "le cure", "strategy": "notebook"}]


def _plan(status: str = "proposed") -> Plan:
    proposal = validate_proposal("Rifare la wiki", _steps("Uno", "Due", "Tre"), COV)
    return Plan(
        id="p_0123456789ab",
        notebook="piante",
        session_key="project:piante",
        title=proposal.title,
        steps=proposal.steps,
        status=status,
    )


# ── Transizioni ──────────────────────────────────────────────────────────────


def test_every_status_has_a_row_in_the_table() -> None:
    assert set(PLAN_TRANSITIONS) == set(PLAN_STATUSES)
    for targets in PLAN_TRANSITIONS.values():
        assert targets <= set(PLAN_STATUSES)


@pytest.mark.parametrize(
    ("current", "wanted"),
    [
        ("proposed", "running"),
        ("proposed", "discarded"),
        ("proposed", "superseded"),
        ("running", "pausing"),
        ("pausing", "paused"),
        ("paused", "running"),
        ("running", "blocked"),
        ("blocked", "running"),
        ("running", "done"),
        ("running", "stopped"),
        ("running", "paused"),
        ("done", "rolled_back"),
        ("stopped", "rolled_back"),
        ("rolled_back", "done"),
    ],
)
def test_allowed_transitions(current: str, wanted: str) -> None:
    plan = apply_transition(_plan(current), wanted, at="2026-10-23T10:00:00+00:00")
    assert plan.status == wanted
    assert plan.history[-1] == {
        "at": "2026-10-23T10:00:00+00:00",
        "event": wanted,
        "from": current,
    }
    assert plan.updated_at == "2026-10-23T10:00:00+00:00"


@pytest.mark.parametrize(
    ("current", "wanted"),
    [
        ("proposed", "done"),
        ("proposed", "paused"),
        ("running", "running"),
        ("running", "proposed"),
        ("paused", "pausing"),
        ("done", "running"),
        ("superseded", "running"),
        ("discarded", "proposed"),
        ("rolled_back", "running"),
        ("running", "nonsense"),
    ],
)
def test_forbidden_transitions_raise_and_change_nothing(current: str, wanted: str) -> None:
    plan = _plan(current)
    with pytest.raises(PlanTransitionError) as info:
        apply_transition(plan, wanted)
    assert info.value.current == current
    assert plan.status == current
    assert plan.history == []


def test_leaving_blocked_clears_the_question() -> None:
    plan = _plan("blocked")
    plan.blocked = {"question": "Quale acero?", "step": "s2", "at": "x"}
    apply_transition(plan, "running", event="answered")
    assert plan.blocked is None
    assert plan.history[-1]["event"] == "answered"


# ── Proposta ─────────────────────────────────────────────────────────────────


def test_valid_proposal_is_cleaned_and_numbered() -> None:
    proposal = validate_proposal(
        "  Rifare la wiki  ",
        [
            {"title": " Cerco Acero ", "detail": " cerca ", "kind": "research",
             "done_when": " una fonte "},
            {"title": "Scrivo", "detail": "scrivi", "same_as": "s3", "done_when": "scritto"},
        ],
        COV,
    )
    assert proposal.title == "Rifare la wiki"
    assert [s.id for s in proposal.steps] == ["s1", "s2"]
    assert proposal.steps[0].title == "Cerco Acero"
    assert proposal.steps[0].detail == "cerca"
    assert proposal.steps[0].kind == "research"
    assert proposal.steps[1].kind == "other"
    assert proposal.steps[1].same_as == "s3"
    assert proposal.steps[0].done_when == "una fonte"
    assert all(s.status == "pending" for s in proposal.steps)


@pytest.mark.parametrize(
    ("title", "steps", "fragment"),
    [
        ("", _steps("a", "b"), "non-empty title"),
        ("x" * 81, _steps("a", "b"), "within 80"),
        ("due\nrighe", _steps("a", "b"), "single line"),
        ("T", _steps("solo"), "A one-step task does not need a plan"),
        ("T", _steps(*[f"p{i}" for i in range(13)]), "Merge related steps"),
        ("T", [{"title": "a", "detail": "x"}, {"title": "", "detail": "x"}], "Step 2 needs"),
        ("T", [{"title": "a\nb", "detail": "x"}, {"title": "c", "detail": "x"}], "single line"),
        ("T", [{"title": "a" * 61, "detail": "x"}, {"title": "c", "detail": "x"}], "within 60"),
        ("T", [{"title": "a", "detail": ""}, {"title": "c", "detail": "x"}], "`detail`"),
        (
            "T",
            [{"title": "a", "detail": "x" * (MAX_STEP_DETAIL + 1)}, {"title": "c", "detail": "x"}],
            "split the step",
        ),
        ("T", [{"title": "a", "detail": "x", "kind": "magic"}, *_steps("c")], "`kind` must be"),
        ("T", _steps("a", "a"), "same title as step 1"),
        ("T", "not a list", "must be a list"),
        ("T", ["just a string", *_steps("c")], "must be an object"),
    ],
)
def test_invalid_proposals_explain_the_fix(title: str, steps: object, fragment: str) -> None:
    with pytest.raises(PlanValidationError) as info:
        validate_proposal(title, steps, COV)
    assert fragment in str(info.value)


def test_all_problems_are_reported_at_once() -> None:
    with pytest.raises(PlanValidationError) as info:
        validate_proposal(
            "", [{"title": "", "detail": ""}, {"title": "ok", "detail": "ok"}], COV
        )
    message = str(info.value)
    assert "plan needs a non-empty title" in message
    assert "Step 1 needs a non-empty title" in message
    assert "Step 1 needs a non-empty `detail`" in message


# ── Tag della versione nuova ─────────────────────────────────────────────────


def _new(*specs: tuple[str, str] | tuple[str, str, str]) -> list[Step]:
    steps = []
    for i, spec in enumerate(specs, start=1):
        title, detail = spec[0], spec[1]
        same_as = spec[2] if len(spec) == 3 else None
        steps.append(Step(id=f"s{i}", title=title, detail=detail, same_as=same_as))
    return steps


OLD = _new(("A", "a"), ("B", "b"), ("C", "c"), ("D", "d"))


def test_unchanged_steps_have_no_tag() -> None:
    new = _new(("A", "a"), ("B", "b"), ("C", "c"), ("D", "d"))
    assert tag_new_version(OLD, new) == []
    assert [s.tag for s in new] == [None, None, None, None]


def test_new_changed_and_dropped() -> None:
    new = _new(("A", "a"), ("B", "b2"), ("X", "x"), ("D", "d"))
    dropped = tag_new_version(OLD, new)
    assert [s.tag for s in new] == [None, "changed", "new", None]
    assert dropped == [{"id": "s3", "title": "C"}]


def test_a_step_inserted_on_top_does_not_move_the_others() -> None:
    new = _new(("X", "x"), ("A", "a"), ("B", "b"), ("C", "c"), ("D", "d"))
    tag_new_version(OLD, new)
    assert [s.tag for s in new] == ["new", None, None, None, None]


def test_a_reordered_step_is_moved() -> None:
    new = _new(("A", "a"), ("D", "d"), ("B", "b"), ("C", "c"))
    tag_new_version(OLD, new)
    assert [s.tag for s in new] == [None, "moved", None, None]


def test_same_as_wins_over_the_title_and_a_renamed_step_is_changed() -> None:
    new = _new(("A", "a"), ("Bee", "b", "s2"), ("C", "c"), ("D", "d"))
    assert tag_new_version(OLD, new) == []
    assert [s.tag for s in new] == [None, "changed", None, None]


def test_unknown_same_as_falls_back_to_the_title() -> None:
    new = _new(("A", "a", "s99"), ("B", "b"), ("C", "c"), ("D", "d"))
    tag_new_version(OLD, new)
    assert [s.tag for s in new] == [None, None, None, None]


def test_an_old_step_is_matched_once() -> None:
    new = _new(("A", "a"), ("A2", "a", "s1"), ("B", "b"), ("C", "c"))
    dropped = tag_new_version(OLD, new)
    assert [s.tag for s in new] == [None, "new", None, None]
    assert dropped == [{"id": "s4", "title": "D"}]


# ── Lettura e (de)serializzazione ────────────────────────────────────────────


def test_helpers_read_the_list() -> None:
    plan = _plan("running")
    plan.steps[0].status = "done"
    plan.steps[1].status = "now"
    assert current_step(plan) is plan.steps[1]
    assert next_pending_step(plan) is plan.steps[2]
    plan.steps[2].status = "cut"
    counts = step_counts(plan)
    assert counts["total"] == 2
    assert counts["finished"] == 1
    assert counts["now"] == 1


def test_round_trip_keeps_unknown_keys() -> None:
    plan = _plan("running")
    data = plan.to_dict()
    data["future_field"] = {"x": 1}
    data["steps"][0]["outside"] = [{"type": "cron"}]
    again = Plan.from_dict(data)
    out = again.to_dict()
    assert out["future_field"] == {"x": 1}
    assert out["steps"][0]["outside"] == [{"type": "cron"}]
    assert out["format"] == 1
    assert out["photos"] == {
        "start": None,
        "end": None,
        "before_rollback": None,
        "after_rollback": None,
    }
    assert Plan.from_dict(out).to_dict() == out


def test_unknown_step_kind_reads_as_other() -> None:
    data = _plan().to_dict()
    data["steps"][0]["kind"] = "teleport"
    assert Plan.from_dict(data).steps[0].kind == "other"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(format=2),
        lambda d: d.update(status="flying"),
        lambda d: d.pop("id"),
        lambda d: d["steps"][0].update(status="maybe"),
        lambda d: d.update(steps="nope"),
    ],
)
def test_unreadable_plans_raise_format_error(mutate) -> None:
    data = _plan().to_dict()
    mutate(data)
    with pytest.raises(PlanFormatError):
        Plan.from_dict(data)


# ── Copertura, assunzioni, criteri di «fatto» ────────────────────────────────


def _research_plan(detail: str) -> list[dict[str, str]]:
    return [
        {"title": "Cerco", "detail": detail, "kind": "research", "done_when": "fonti salvate"},
        {"title": "Scrivo", "detail": "scrivo", "kind": "write", "done_when": "pagine scritte"},
    ]


def _cov(**entry: str) -> list[dict[str, str]]:
    base = {"item": "Glicine: potatura", "have": "niente", "missing": "tutto"}
    return [{**base, **entry}]


@pytest.mark.parametrize(
    ("coverage", "fragment"),
    [
        (None, "`coverage` is required"),
        ([], "`coverage` is required"),
        ("tutto", "`coverage` is required"),
        (_cov(strategy="ask"), "is still `ask`"),
        (_cov(strategy="maybe"), "`strategy` must be one of"),
        (_cov(strategy="leave"), "say `why`"),
        ([{"item": "", "strategy": "notebook"}], "non-empty `item`"),
        (["x"], "must be an object"),
    ],
)
def test_coverage_is_required_and_checked(coverage: object, fragment: str) -> None:
    with pytest.raises(PlanValidationError) as info:
        validate_proposal("T", _research_plan("cerco il ficus"), coverage)
    assert fragment in str(info.value)


def test_a_web_item_needs_a_research_step_not_its_exact_words() -> None:
    """Basta un passo ``research``: il modello parafrasa le voci, e un confronto di
    stringhe lo farebbe riprovare all'infinito."""
    no_research = [
        {"title": "Scrivo", "detail": "scrivo", "kind": "write", "done_when": "scritte"},
        {"title": "Controllo", "detail": "controllo", "kind": "check", "done_when": "ok"},
    ]
    with pytest.raises(PlanValidationError) as info:
        validate_proposal("T", no_research, _cov(strategy="web"))
    assert "no `research` step" in str(info.value)
    proposal = validate_proposal(
        "T", _research_plan("Cerco come si pota il Wisteria."), _cov(strategy="web")
    )
    assert proposal.coverage[0]["strategy"] == "web"


def test_a_web_item_named_by_a_research_step_is_accepted() -> None:
    proposal = validate_proposal(
        "T",
        _research_plan("Cerco sul web: glicine: potatura, e salvo in raw/research/."),
        _cov(strategy="web", why=" il quaderno non ne parla "),
        [" Tengo la camelia. ", "Cancello le pagine vecchie."],
    )
    assert proposal.coverage == [{
        "item": "Glicine: potatura", "have": "niente", "missing": "tutto",
        "strategy": "web", "why": "il quaderno non ne parla",
    }]
    assert proposal.assumptions == ["Tengo la camelia.", "Cancello le pagine vecchie."]
    assert [s.done_when for s in proposal.steps] == ["fonti salvate", "pagine scritte"]


def test_every_step_needs_done_when() -> None:
    with pytest.raises(PlanValidationError) as info:
        validate_proposal("T", [{"title": "a", "detail": "x"}, *_steps("b")], COV)
    assert "Step 1 needs `done_when`" in str(info.value)
    assert "Step 2" not in str(info.value)


@pytest.mark.parametrize(
    ("assumptions", "fragment"),
    [("una frase", "must be a list"), ([""], "non-empty sentence"), (["a\nb"], "one line")],
)
def test_assumptions_are_short_sentences(assumptions: object, fragment: str) -> None:
    with pytest.raises(PlanValidationError) as info:
        validate_proposal("T", _steps("a", "b"), COV, assumptions)
    assert fragment in str(info.value)


def test_a_new_done_when_tags_the_step_changed() -> None:
    old = validate_proposal("T", _steps("Uno", "Due"), COV).steps
    new = validate_proposal(
        "T", [_steps("Uno")[0], {**_steps("Due")[0], "done_when": "più severo"}], COV
    ).steps
    tag_new_version(old, new)
    assert [s.tag for s in new] == [None, "changed"]


def test_drafting_expires_after_twelve_hours_of_silence() -> None:
    from datetime import datetime, timedelta, timezone

    from jenny.plan.model import drafting_expired, drafting_view

    now = datetime(2026, 10, 23, 20, 0, tzinfo=timezone.utc)
    fresh = (now - timedelta(hours=11)).isoformat()
    stale = (now - timedelta(hours=13)).isoformat()
    assert not drafting_expired({"since": stale, "touched": fresh}, now=now)
    assert drafting_expired({"since": stale, "touched": stale}, now=now)
    assert drafting_expired({}, now=now)
    assert drafting_view({"since": fresh, "touched": fresh, "reads": 2, "revise": ""}) == {
        "since": fresh, "revise": None, "reads": 2,
    }
