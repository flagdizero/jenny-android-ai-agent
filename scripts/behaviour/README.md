# Behaviour check

Some of Jenny's guarantees are not code. They are sentences in a prompt or a tool description,
and they hold because the model chooses to honour them: "forget X" is answered with "Dream will
apply it", a conditional check becomes a `monitor` and not a reminder that speaks every hour, a
fact said in a notebook goes to the journal with one `journal_append`. Each of these was once
broken, found by hand on a phone, and fixed with a sentence. When one stops holding — a model
changes, a prompt grows and the sentence drifts into the middle of forty others — **nothing goes
red**: lint, types and the whole test suite still pass.

`scripts/behaviour_check.py` is the detector. It is not a test and it does not pass or fail: for
each scenario it runs N times on each model and records the **rate** of every property, compared
with the baseline committed in [`baseline.json`](./baseline.json) — rates only. Every run behind it, raw replies and tool calls included, goes to `baseline-runs.json` next to it, which git ignores: the replies quote whatever the emulator's workspace already holds, not only the invented fixtures. `3/3 → 3/3` is silence;
`3/3 → 0/3` is the signal, and it names the promise that stopped being kept.

## What it is not

- **Not in `pytest`, not in CI.** It spends real tokens and its outcome is a distribution, not a
  boolean. A flaky check in CI gets skipped within a fortnight and then lies about coverage.
  The check functions themselves are pure and are tested offline in
  `tests/scripts/test_behaviour_checks.py`.
- **No LLM judge.** A second model-dependent component in a tool whose job is to notice
  model-dependence would drift in the direction that is hardest to see. Every property is read
  off something the harness controls: a tool call and its arguments, a file, or a **nonce** — an
  invented word (`zanbrives42`), new on every run, planted in the fixture and searched for as a
  substring.
- **Not tuned to one model.** A promise only one model keeps lives in the reader, not in the
  prompt. Run it against at least two (`--models a,b`); disagreement is the most useful result.

## How it runs

It drives the app installed on an **emulator** (a debug build, because it uses `adb run-as`),
with the provider the app already has: the key never leaves the device. Over `adb forward` it
talks to the gateway the way the WebUI does — chat turns over the WebSocket, `/new`, `/dream`,
the cron and subagent routes, `project.create`/`project.delete` — and reads the outcome from the
session JSONL and the workspace files.

- **Snapshot.** At the start it `tar`s the mutable part of the workspace (`memory/`, `USER.md`,
  `SOUL.md`, `skills/`, `cron/`, `sessions/`, `wikis/`) and moves the system jobs
  (Dream, gardener, notebook clock) a day ahead, so none of them fires in the middle of a
  scenario. At the end — also after an error or Ctrl-C — it puts the snapshot back and restarts
  the app. If a run died without restoring, `--restore` does it.
- **A fresh session per run.** A session that has seen a behaviour imitates it, so every run
  starts with `/new` (or in a throwaway notebook). `/new` archives the discarded conversation in
  the background with a model call; the runner waits for that entry to land in
  `history.jsonl` before writing the next fixture.
- **Fixtures.** Before each run the files listed in `runner.RESET_FILES` go back to how they were
  at the start, then the scenario writes its own. History entries get cursors above the
  session's floor, or they would not reach the prompt.
- **Across chats.** A personal-chat scenario can first speak in a throwaway notebook
  (`notebook_prelude`): those turns run there, the runner waits for the notebook's subagents, and
  their tool calls go to `extra["prelude_calls"]` — so a red in the personal chat can be told
  apart from a fact the notebook never wrote down. Stage 4 (the diary) is built on this.
- **Cleanup.** Subagents the run started are cancelled and waited for, cron jobs it created are
  removed, its notebook is deleted.

## Calibration

A check that has never failed proves nothing. `--calibrate` runs each scenario a second time
with its guarantee changed, live: prompts are rendered from `workspace/agent/*.md` and skills from
`workspace/skills/`, Jinja reloads a template when it changes, and the app re-extracts the bundle
at start, so a restart undoes the edit.

Two kinds of edit, and the difference matters:

- **Removal** — take out the sentence that carries the promise. If the rate holds, the promise
  is carried somewhere else too. On 11/10/2026 removing the `recall` line from the memory skill
  and the "never a plain reminder" sentence from `scheduling.md` changed nothing on DeepSeek:
  the tool descriptions, which live in Python, say the same thing.
- **Inversion** — replace it with a sentence asking for the wrong behaviour. This is what proves
  the *detector* works: the rate must collapse.

Guarantees that live only in Python (which subagent type `spawn` picks, for instance) cannot be
edited live; they are calibrated with a variant build, and the scenario says so in its
`calibration_note`.

The first calibration (11/10/2026, DeepSeek v4.1 flash, 2+2 runs) inverted live: forget → names
Dream 2→0, notebook fact → journal 2→0, board change → notebook-editor 2→1, follow-up →
`subagent_send` 2→0, chatter stays out 2→1, conditional check → monitor 2→1, recall 2→1, tool
error owned 2→1. The five that live in Python were calibrated on 12/10/2026 with a variant build
(the inverted sentences in a throwaway worktree, `assembleDebug`, then the normal build back),
3+3 runs: researcher type 3→0, consolidator skip 3→0, heartbeat silence 3→0, goal stays open
3→0; plain reminder only 3→2 — the check reads it right, but DeepSeek keeps `reminder` for a
daily nudge even when told to use `monitor`. Inverting Dream's budget rule did not move it, so that scenario is a probe. A scenario whose promise has no sentence at all is a **probe**
(`kind="probe"`): it says how Jenny behaves, not whether a prompt edit changed it.

## Usage

```bash
python scripts/behaviour_check.py                          # every scenario, the app's model, N=3
python scripts/behaviour_check.py --models deepseek-v4.1-flash,claude-haiku-5-5 --runs 3
python scripts/behaviour_check.py --stage 1 --calibrate    # with the guarantees inverted
python scripts/behaviour_check.py --only forget_names_dream --runs 5
python scripts/behaviour_check.py --save-baseline          # after a deliberate change
python scripts/behaviour_check.py --restore                # after a run that died badly
```

Run it after editing anything under `jenny/templates/agent/` or a tool description, and after
changing model.

## Adding a scenario

A scenario (`scenarios.py`) is a promise written down as: fixture files with `{n}` nonces, the
user's turns, a check that returns named properties, and a calibration. It belongs here only if
the promise rests on a sentence — something a test cannot pin. All people and facts in a fixture are invented.
