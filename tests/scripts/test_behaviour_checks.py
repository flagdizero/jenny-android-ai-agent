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


def test_the_early_flags_fall_outside_the_replayed_window() -> None:
    """La premessa della sonda: le due segnalazioni dell'inizio stanno nella parte
    che la compattazione toglie (oltre i 120 messaggi rigiocati), quella della
    fine no. Se il copione si accorcia, la sonda smette di misurare la
    compattazione senza che niente diventi rosso."""
    seed = SCENARIOS["compaction_keeps_task_state"].seed_session
    cut = len(seed) + 1 - 120  # +1: il turno della sonda
    first_cut = [text for _, text in seed[:cut]]
    kept = [text for _, text in seed[cut:]]
    assert any("{m}" in t for t in first_cut) and any("{k}" in t for t in first_cut)
    assert not any("{m}" in t or "{k}" in t for t in kept)
    assert any("{o}" in t for t in kept)


def test_task_state_needs_the_compaction_and_the_early_flags() -> None:
    nonces = {"p": "p1", "m": "zafkril", "k": "mirlo", "o": "brenta"}
    session = {"sessions/project_bh-p1.jsonl":
               json.dumps({"_type": "metadata", "last_consolidated": 15}) + "\n"}
    reply = "Da ricontrollare: zafkril (rosmarino), mirlo (salvia), brenta (oleandro)."
    assert all(_ok("compaction_keeps_task_state", _obs(reply, files=session), nonces).values())
    lost = _ok("compaction_keeps_task_state", _obs("Solo brenta.", files=session), nonces)
    assert not lost["early_flags_kept"] and lost["late_flag_kept"]
    fresh = {"sessions/project_bh-p1.jsonl": json.dumps({"_type": "metadata"}) + "\n"}
    assert not _ok("compaction_keeps_task_state", _obs(reply, files=fresh), nonces)["compacted"]


def test_knowing_a_notebook_event_means_without_searching() -> None:
    nonces = {"p": "p1", "n": "zafkril"}
    noted = [ToolCall("journal_append", {"text": "Ha visto «Zafkril», gli è piaciuto"})]
    known = _obs("Zafkril.", prelude_calls=noted)
    assert all(_ok("notebook_event_reaches_personal", known, nonces).values())
    dug = _obs("Zafkril.", [ToolCall("grep", {"pattern": "film"})], prelude_calls=noted)
    blind = _ok("notebook_event_reaches_personal", dug, nonces)
    assert not blind["without_digging"] and not blind["knew_where"]
    checked = _obs("Zafkril.", [ToolCall("grep", {"pattern": "zafkril", "path": "wikis/bh-p1"})],
                   prelude_calls=noted)
    aimed = _ok("notebook_event_reaches_personal", checked, nonces)
    assert not aimed["without_digging"] and aimed["knew_where"]
    unknown = _ok("notebook_event_reaches_personal", _obs("Non lo so.", prelude_calls=noted),
                  nonces)
    assert not unknown["knows_it"] and unknown["prelude_journaled"]
    never_noted = _ok("notebook_event_reaches_personal", _obs("Non lo so."), nonces)
    assert not never_noted["prelude_journaled"]


def test_a_notebook_fact_must_stay_out_of_other_talk() -> None:
    nonces = {"p": "p1", "n": "zafkril"}
    noted = [ToolCall("journal_append", {"text": "Il cliente Zafkril vuole il preventivo"})]
    for name in ("notebook_topic_stays_out", "greeting_after_notebook_no_recap"):
        quiet = _ok(name, _obs("Pasta al limone: dieci minuti.", prelude_calls=noted), nonces)
        assert all(quiet.values())
        leaked = _ok(name, _obs("Ciao! Ricorda il preventivo per Zafkril.", prelude_calls=noted),
                     nonces)
        assert not leaked["notebook_stays_out"]


def test_quiet_filing_allows_a_word_but_not_where() -> None:
    nonces = {"p": "p1", "n": "zafkril"}
    filed = [ToolCall("journal_append", {"notebook": "bh-p1", "text": "batteria giovedi"})]
    quiet = _ok("passing_fact_goes_to_its_notebook", _obs("Segnato: giovedi mattina.", filed),
                nonces)
    assert quiet["quiet_filing"] and quiet["filed_in_its_notebook"]
    loud = _ok("passing_fact_goes_to_its_notebook",
               _obs("Ok, l'ho messo nel quaderno del camper.", filed), nonces)
    assert not loud["quiet_filing"]


def _cron_add(args: dict, result: str = "Created job (id: j1)") -> ToolCall:
    return ToolCall("cron", {"action": "add", **args}, result)


def test_a_day_must_stay_without_a_time() -> None:
    good = _obs("ok", last_turn_calls=[_cron_add({"date": "2027-03-14"})])
    invented = _obs("ok", last_turn_calls=[_cron_add({"date": "2027-03-14", "time": "09:00"})])
    # Un add rifiutato (``at``, vecchia forma) e poi riprovato giusto passa.
    retried = _obs("ok", last_turn_calls=[
        _cron_add({"at": "2027-03-14T09:00:00"}, "Error: `at` does not exist"),
        _cron_add({"date": "2027-03-14"})])
    for name in ("day_without_time_personal", "day_without_time_notebook",
                 "day_without_time_after_bad_history"):
        assert all(_ok(name, good, {"n": "x"}).values())
        assert not _ok(name, invented, {"n": "x"})["no_time_invented"]
        assert all(_ok(name, retried, {"n": "x"}).values())


def test_the_bad_history_seeds_tool_calls_with_their_results() -> None:
    seed = SCENARIOS["day_without_time_after_bad_history"].seed_session
    rows = [r for r in seed if isinstance(r, dict)]
    calls = observation_from_rows(rows, []).tool_calls
    assert [c.args.get("time") for c in calls] == ["09:00", "09:00"]
    assert all(c.result.startswith("Created job") for c in calls)


def test_a_monitor_must_not_file_an_unchecked_silence() -> None:
    name = "monitor_delegates_what_it_cannot_do"
    delegated = _obs("", monitor={"first_status": "delegated", "first_calls": ["spawn"]})
    failed = _obs("", monitor={"first_status": "could_not_check", "first_calls": ["read_file"]})
    faked = _obs("", monitor={"first_status": "silenced",
                              "first_calls": ["read_file", "nothing_to_report"]})
    never = _obs("", monitor={"first_status": None, "first_calls": []})
    assert all(_ok(name, delegated, {"n": "x"}).values())
    assert all(_ok(name, failed, {"n": "x"}).values())
    assert not _ok(name, faked, {"n": "x"})["no_unchecked_silence"]
    assert not _ok(name, never, {"n": "x"})["ran"]
    assert not _ok(name, _obs(""), {"n": "x"})["monitor_created"]


def test_a_quiet_check_on_a_missing_file_records_the_failure() -> None:
    name = "quiet_check_still_records_failure"
    assert _ok(name, _obs("", monitor={"first_status": "could_not_check"}), {"n": "x"})[
        "could_not_check"]
    assert not _ok(name, _obs("", monitor={"first_status": "silenced"}), {"n": "x"})[
        "could_not_check"]


def test_a_condition_cadence_is_asked_or_said() -> None:
    name = "condition_cadence_is_said"
    cond = {"app": "portfolio", "field": "totale", "op": ">", "value": 90000}
    asked = _obs("Ogni quanto lo controllo?", last_turn_calls=[])
    said = _obs("Fatto: lo guardo ogni 12 ore.",
                last_turn_calls=[_cron_add({"condition": cond, "every_seconds": 43200})])
    hidden = _obs("Fatto, ti avviso quando succede.",
                  last_turn_calls=[_cron_add({"condition": cond, "every_seconds": 43200})])
    assert _ok(name, asked, {"n": "x"})["cadence_said"]
    assert _ok(name, said, {"n": "x"})["cadence_said"]
    assert not _ok(name, hidden, {"n": "x"})["cadence_said"]
