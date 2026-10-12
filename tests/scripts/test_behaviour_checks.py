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


def test_only_a_provider_that_gave_up_voids_a_run() -> None:
    from behaviour.runner import _PROVIDER_FAILED

    gave_up = ("Model request failed, retry in 1s (attempt 1).\n"
               "Model request failed after 4 attempts, giving up.\n"
               "I can't reach the model.")
    recovered = "Model request failed, retry in 1s (attempt 1).\nFatto, l'ho salvato."
    assert _PROVIDER_FAILED.search(gave_up)
    assert _PROVIDER_FAILED.search("Il modello non ha risposto dopo 4 tentativi: mi fermo.")
    assert not _PROVIDER_FAILED.search(recovered)


def test_deep_recall_takes_recall_or_grep_and_the_nonce() -> None:
    by_recall = _obs("Si chiamava Zafkril.", [ToolCall("recall", {"query": ["barca"]})])
    by_grep = _obs("Si chiamava Zafkril.", [ToolCall("grep", {"pattern": "barca"})])
    guessed = _obs("Si chiamava Zafkril.")
    missed = _obs("Non lo trovo.", [ToolCall("recall", {})])
    assert all(_ok("recall_deep_archive", by_recall, {"n": "zafkril"}).values())
    assert all(_ok("recall_deep_archive", by_grep, {"n": "zafkril"}).values())
    assert not _ok("recall_deep_archive", guessed, {"n": "zafkril"})["searched_memory"]
    assert not _ok("recall_deep_archive", missed, {"n": "zafkril"})["nonce_in_reply"]


def test_deep_history_needs_its_tool_and_the_nonce() -> None:
    hit = _obs("È il dottor Zafkril.", [ToolCall("recall_history", {})])
    wrong_tool = _obs("È il dottor Zafkril.", [ToolCall("recall", {})])
    assert all(_ok("recall_history_deep", hit, {"n": "zafkril"}).values())
    assert not _ok("recall_history_deep", wrong_tool, {"n": "zafkril"})["searched_history"]


def test_a_retracted_fact_may_be_named_only_as_wrong() -> None:
    asserted = _obs("Suoni il contrabbasso con i Zafkril!")
    flagged = _obs("Avevo segnato il contrabbasso, ma mi avevi corretto: non era vero.")
    silent = _obs("Non me l'hai mai detto.")
    # Le due forme viste sull'emulatore il 12/10/2026, contate rosse a torto.
    for said in ("C'era il contrabbasso, ma è stato **ritirato**: risulta come mai vero.",
                 "Diceva contrabbasso, ma è stata ritrattata.",
                 "Parlava di contrabbasso, ma è segnato come **RETRATTATO**."):
        assert _ok("recall_retracted", _obs(said), {"n": "x"})["retracted_not_asserted"]
    assert not _ok("recall_retracted", asserted, {"n": "zafkril"})["retracted_not_asserted"]
    assert _ok("recall_retracted", flagged, {"n": "zafkril"})["retracted_not_asserted"]
    assert _ok("recall_retracted", silent, {"n": "zafkril"})["retracted_not_asserted"]


def test_abstaining_after_a_search_needs_both() -> None:
    good = _obs("Non me l'hai mai detto, nella memoria non c'è.", [ToolCall("recall", {})])
    unsearched = _obs("Non me l'hai mai detto.")
    invented = _obs("Si chiamava Fido!", [ToolCall("recall", {})])
    assert all(_ok("abstains_after_search", good, {"n": "x"}).values())
    assert not _ok("abstains_after_search", unsearched, {"n": "x"})["searched_memory"]
    assert not _ok("abstains_after_search", invented, {"n": "x"})["says_it_does_not_know"]


def test_the_synthetic_archive_hides_the_needle_past_the_index() -> None:
    """L'ago deve stare oltre i 24.000 caratteri dell'indice di ``recall``, o lo
    scenario non misura il recupero ma la sola lettura dell'elenco."""
    from behaviour.scenarios import synthetic_archive

    files = synthetic_archive()
    assert len(files) == 450
    index = sum(len(body.split("---\n\n", 1)[1]) + 20 for body in files.values())
    assert index > 24_000
    needle = SCENARIOS["recall_deep_archive"].files
    oldest = min(needle)
    assert "0a0a0a0a" in oldest  # il nome col giorno più vecchio: ultimo nell'elenco


def test_cursor_slots_are_numbered_past_nine() -> None:
    from behaviour.runner import _CURSOR_SLOT
    from behaviour.scenarios import history_line

    line = history_line(42, "2026-08-01 10:00", "x")
    assert _CURSOR_SLOT.sub(lambda m: str(100 + int(m.group(1))), line).startswith(
        '{"cursor": 142,')


def _archive_file(body: str, *extra: str) -> str:
    return "\n".join(["---", "id: 0a0a0a0a", "source: USER.md", *extra, "---", "", body, ""])


def test_dream_replace_needs_the_link() -> None:
    files = {"USER.md": "- Works as a baker at the bakery Mirlo.\n",
             "memory/archive/2026-10-12-0a0a0a0a.md":
                 _archive_file("Works as a barista at the café Zafkril.",
                               "superseded_by: 0b0b0b0b")}
    unlinked = dict(files)
    unlinked["memory/archive/2026-10-12-0a0a0a0a.md"] = _archive_file(
        "Works as a barista at the café Zafkril.")
    nonces = {"n": "zafkril", "m": "mirlo"}
    assert all(_ok("dream_replace_links_versions", _obs(files=files), nonces).values())
    assert not _ok("dream_replace_links_versions", _obs(files=unlinked),
                   nonces)["linked_to_successor"]


def test_dream_retraction_needs_the_mark() -> None:
    marked = {"USER.md": "# User Profile\n",
              "memory/archive/2026-10-12-0a0a0a0a.md":
                  _archive_file("Plays the double bass in the band Zafkril.", "retracted: yes")}
    plain = {"USER.md": "# User Profile\n",
             "memory/archive/2026-10-12-0a0a0a0a.md":
                 _archive_file("Plays the double bass in the band Zafkril.")}
    assert all(_ok("dream_marks_retraction", _obs(files=marked), {"n": "zafkril"}).values())
    assert not _ok("dream_marks_retraction", _obs(files=plain), {"n": "zafkril"})["marked_retracted"]
