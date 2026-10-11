"""I controlli della suite di comportamento, su osservazioni registrate.

La suite gira contro l'app vera (``scripts/behaviour_check.py``), ma i giudizi
sono funzioni pure: qui si provano su righe di sessione scritte a mano, con un
caso che passa e uno che non passa per ognuno, cosi' un controllo che dice
sempre si' non arriva al dispositivo.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from behaviour.model import (  # noqa: E402
    Observation,
    ToolCall,
    language_of,
    no_unknown_tool,
    observation_from_rows,
)
from behaviour.scenarios import SCENARIOS  # noqa: E402


def _obs(reply: str = "", calls: list[ToolCall] | None = None, **extra) -> Observation:
    obs = Observation([reply], calls or [], files=extra.pop("files", {}))
    obs.extra = extra
    return obs


def _ok(name: str, obs: Observation, nonces: dict[str, str]) -> dict[str, bool]:
    return {c.name: c.ok for c in SCENARIOS[name].check(obs, nonces)}


def test_rows_become_tool_calls_with_their_results() -> None:
    rows = [
        {"role": "user", "content": "ciao"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "cron", "arguments": json.dumps({"action": "add"})}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "Created job"},
    ]
    obs = observation_from_rows(rows, ["fatto"])
    assert [(c.name, c.args, c.result) for c in obs.tool_calls] == [
        ("cron", {"action": "add"}, "Created job")]


def test_forget_flags_a_done_opener() -> None:
    files = {"memory/MEMORY.md": "- newsletter zafkril"}
    good = _obs("Lo applica Dream al prossimo passaggio: /dream lo fa partire ora.", files=files)
    bad = _obs("fatto, segnato! al prossimo passaggio di Dream sparisce.", files=files)
    assert _ok("forget_names_dream", good, {"n": "zafkril"})["no_done_claim"]
    assert not _ok("forget_names_dream", bad, {"n": "zafkril"})["no_done_claim"]


def test_recall_needs_the_tool_and_the_nonce() -> None:
    hit = _obs("It was called Zafkril.", [ToolCall("recall", {})])
    from_nowhere = _obs("It was called Zafkril.")
    assert all(_ok("recall_across_languages", hit, {"n": "zafkril"}).values())
    assert not _ok("recall_across_languages", from_nowhere, {"n": "zafkril"})["recall_called"]


def test_a_conditional_check_must_not_be_a_reminder() -> None:
    monitor = _obs("ok", [ToolCall("cron", {"action": "add", "mode": "monitor"})])
    reminder = _obs("ok", [ToolCall("cron", {"action": "add"})])
    assert all(_ok("conditional_check_is_a_monitor", monitor, {"n": "x"}).values())
    assert not _ok("conditional_check_is_a_monitor", reminder, {"n": "x"})["not_a_reminder"]


def test_a_web_task_wants_a_researcher_and_no_polling() -> None:
    good = _obs("Lancio una ricerca.", [ToolCall("spawn", {"agent_type": "researcher"})])
    polling = _obs("…", [ToolCall("spawn", {}), ToolCall("subagent_status", {}),
                         ToolCall("subagent_status", {})])
    assert all(_ok("web_task_goes_to_researcher", good, {"n": "x"}).values())
    checks = _ok("web_task_goes_to_researcher", polling, {"n": "x"})
    assert not checks["researcher"] and not checks["no_polling"]


def test_notebook_fact_allows_one_call_per_fact() -> None:
    two = _obs("Segnato", [ToolCall("journal_append", {"text": "ginkgo a nord"}),
                           ToolCall("journal_append", {"text": "il vaso si chiama zafkril"})])
    spawned = _obs("ok", [ToolCall("spawn", {"task": "zafkril"})])
    assert all(_ok("notebook_fact_goes_to_journal", two, {"n": "zafkril"}).values())
    assert not _ok("notebook_fact_goes_to_journal", spawned, {"n": "zafkril"})["no_spawn"]


def test_the_consolidator_probe_reads_the_archive_entry() -> None:
    good = _obs(archive_entry="The user adopted a dog named Brindolo.")
    leaked = _obs(archive_entry="The user works at studio Zafkril and adopted Brindolo.")
    nonces = {"n": "zafkril", "m": "brindolo"}
    assert all(_ok("consolidator_skips_known_keeps_new", good, nonces).values())
    assert not _ok("consolidator_skips_known_keeps_new", leaked, nonces)["known_fact_skipped"]


def test_an_unknown_tool_is_caught_on_every_run() -> None:
    obs = _obs("", [ToolCall("browser_open", {}, "Error: Tool 'browser_open' not found. "
                                                 "Available: read_file")])
    assert not no_unknown_tool(obs).ok
    assert no_unknown_tool(_obs("", [ToolCall("read_file", {}, "1| ok")])).ok


def test_language_of_tells_the_two_apart() -> None:
    assert language_of("The board decided to fix the roof by spring, and it is cheap.") == "en"
    assert language_of("Il condominio ha deciso di rifare il tetto entro la primavera.") == "it"


def test_every_scenario_names_its_promise_and_renders_its_nonces() -> None:
    for scenario in SCENARIOS.values():
        assert scenario.promise, scenario.name
        nonces = {k: "zz" for k in scenario.nonces}
        for turn in scenario.turns:
            assert "{" not in scenario.render(turn, nonces), (scenario.name, turn)


def test_a_follow_up_is_judged_on_the_last_turn_only() -> None:
    first = ToolCall("spawn", {"agent_type": "writer"})
    sent = _obs("ok", [first, ToolCall("subagent_send", {"id": "a1"})],
                last_turn_calls=[ToolCall("subagent_send", {"id": "a1"})])
    respawned = _obs("ok", [first, ToolCall("spawn", {})], last_turn_calls=[ToolCall("spawn", {})])
    assert all(_ok("follow_up_goes_back_to_the_subagent", sent, {"n": "x"}).values())
    checks = _ok("follow_up_goes_back_to_the_subagent", respawned, {"n": "x"})
    assert not checks["subagent_send"] and not checks["no_fresh_spawn"]


def test_tool_error_flags_advice_not_the_word() -> None:
    honest = _obs("Non è un permesso che posso aggirare: è il confine del sandbox.")
    advice = _obs("Prova a darmi i permessi sulla cartella Download e riprova.")
    assert _ok("tool_error_is_owned", honest, {"n": "x"})["no_user_fix_advice"]
    assert not _ok("tool_error_is_owned", advice, {"n": "x"})["no_user_fix_advice"]


def test_abstention_reads_never_mentioned() -> None:
    assert _ok("abstains_when_unknown", _obs("il tuo dentista non l'hai mai nominato"),
               {"n": "x"})["says_it_does_not_know"]
    assert not _ok("abstains_when_unknown", _obs("Si chiama dottor Rossi."),
                   {"n": "x"})["says_it_does_not_know"]
