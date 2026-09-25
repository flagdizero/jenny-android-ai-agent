# Making Long-Term Memory Reliable

A staged plan for `SOUL.md` / `USER.md` / `memory/MEMORY.md` / `history.jsonl` and the two
processes that write them (Dream, and the main agent). Every defect below was **observed
running on the Titan 2 on 2026-08-18**, not inferred; the measurement is quoted with each one so
a future reader can tell a fact from a guess.

Read [`design.md`](./design.md) first — the "less structure, more intelligence" rule is the one
this plan deliberately pushes back on, and the reason is in [The root finding](#the-root-finding).

**Progress lives in [`memory-plan-checklist.md`](./memory-plan-checklist.md)** — one box per
action, ticked only when its verification has run. This file holds the reasoning; that one holds
the state.

## Status: closed 2026-08-21

**Done and verified on device:** phases 0, 1, 1b, 2, 4, 5, 6 (bar 6.1), and 7.1. Eleven defects
closed, `D1`–`D11`. The destructive one is gone: the review pass no longer deletes, it demotes, and
the phase-2 device run moved sixteen entries with nothing lost. The plan's last unticked line — a
fact from a held batch landing after room is freed — was finally seen on 2026-08-19.

**Deliberately not done, and none of these is forgotten work:**

- **Phase 3 beyond 3.1** — its own measurement argued against it. The two files inject ~708 tokens,
  ~3% of the prompt, already under the figure a cap would have set. The cost of a memory write is
  the *downstream* prefix, so capping the block's size buys nothing; the whole prize is ~1% of daily
  tokens. Left open only because 3.4 (visible truncation) keeps a little value for the day there is
  something to truncate. See [3.1](#31-the-measurement--done-2026-08-20-and-it-argues-against-most-of-this-phase).
- **6.1** — lowering `reviewEveryRuns` below 12. Phase 2 removed the reason the floor existed, but a
  faster cadence *and* a fresh permission is two changes at once, and no run has asked for it.

  > **The 12 is now enforced, 2026-08-23.** It had been a decision written in three places and
  > held by none: `/dream budget review 1` was accepted, and the only defence was a printed note
  > whose threshold was 6 (`docs/using/memory.md` said 6 too). The floor lives in
  > `command/builtin.py::_REVIEW_CADENCE_FLOOR` — the command refuses below it — and the schema
  > deliberately stays `ge=1`, because raising a bound on a shipped field quarantines the
  > `config.json` of anyone who already has a lower value. The escape hatch is a typed phrase,
  > `i-accept-back-to-back-reviews`, and it exists *for this item*: every device measurement in
  > this plan was taken at `reviewEveryRuns: 1`, and there is no root shell on the phone. So 6.1
  > is still reachable from chat — it just has to say so out loud.
- **7.2** — embeddings. The plan says not to build them early and there is now a log that will say
  when the index outgrows a context. At 4k of a 24k cap, that day is not close.

**A caveat on the second half of this plan.** Phase 2 changed what a failure costs — from losing a
user's facts to wasting tokens — and the phases after it were still sized against the older stake.
Phases 5, 6.2 and 7.1 are correct work carried out with more urgency than the risk warranted. Worth
re-reading before treating anything left here as pressing.

**Four guarantees here are prompt-shaped**, not mechanical, and are checked by hand:
[`memory-probes.md`](./memory-probes.md). Run them after editing anything under
`jenny/templates/agent/`, and after changing model.

## The root finding

**The model does not free space and then write.** Six consecutive Dream runs were driven with
`USER.md` between 96% and 99% of its budget and a batch of retained facts pending. In all six the
model pruned an existing line and stopped: 2,399 → 2,378 → 2,357 chars, zero facts added. The
refusal message asks for exactly two steps ("free space first… then write the shortened text")
and the model performs the first one.

This is not a prompt bug to iterate on. It is the load-bearing assumption of the design Jenny
inherited — Hermes refuses the write and trusts the agent to consolidate and retry, and it can,
because that refusal lands on a good model inside a live session. Jenny's lands at 04:00 on
`deepseek-v4-flash` at `reasoningEffort: low`, unattended.

So the organising principle of this plan:

> **Correctness must not depend on the model choosing to do a second thing.**
> Where a guarantee is needed, put it in code. Leave the model the judgment calls only.

Every phase below is a step from "we asked the model to" toward "the runtime did".

## Defect register

| # | Defect | Evidence (2026-08-18) | Phase |
|---|---|---|---|
| D1 | The budget guard does not bind the **main agent** — only `build_dream_tools` mounts `write_size_guard`; the registry from `tools/loader.py` has none | A chat turn wrote `USER.md` to **2,399/2,400 chars**. The main agent can create the saturation that then paralyses Dream | 0 ✅ |
| D2 | `forced_at_stuck` is never reset, so the "force a review at `stuck == 2`" escape hatch fires **once per install** | `stuck` reached 2 with `forced_at_stuck: 2` from an earlier episode → `stuck != forced_at` false → no review. It only came at `stuck == 4`. Read again on the device after the fix was written: `{"stuck_runs": 0, "forced_at_stuck": 4}` — armed in production | 0 ✅ |
| D3 | The model prunes without adding under pressure | 6/6 runs, above | 2, 3 |
| D4 | The review pass deletes real facts on a second consecutive forced pass | Lost five entries — two open questions, a plan, a biographical detail and one insight; the `[permanent]` one survived. −564 bytes | 2, 6 |
| D5 | Extraction is redundant — the consolidator re-extracts facts already consolidated | Cursors 92/93/94 (one topic, three times), 99/100/101 (one conversation, three times), 102/103 | 4 ✅ |
| D6 | `stuck` conflates "no room" with "nothing new to save"; only the first is fixable by a review | A review pass was forced on files at 77% and 79% with nothing to free | 5 |
| D7 | Deletion is terminal — the only recovery is snapshots, and Dream churns them | 43 `pre_dream` snapshots spanning **6.6 days** on this device (`retentionMaxAgeDays: 7`, shipped default `0`, `retentionRecent: 20` thinning alongside). Upstream's git-backed history was dropped for Android | 2 |
| D8 | `consolidation_landed` is a size heuristic with a known false-positive class: a shrinking correction that *carries* the new fact reads as "nothing landed" | By construction; documented in the function | 1 |
| D9 | The unit of writing is the **file**, not the **fact**, so "did this fact land" has no answer to read | Same; it is why D8 has to be an estimate at all | 1 |
| D11 | `memory replace` discarded the old version of an entry with no archive: `remove` demoted, the file-boundary hook covered `apply_patch`, and the entry tool wrote through neither | Titan 2, 2026-08-19: a forced review pruned four entries from `USER.md` — three via `apply_patch`, archived; the fourth reworded via `memory replace`, and its old text is gone. A defence is worth its widest gap | 2 ✅ |
| D10 | `already_present` only fires if the model calls `add`. It prefers `list` and filters the batch itself, so a duplicate-only batch produces **no** entry evidence and is held | Titan 2, 2026-08-18 18:0x: `memory list user` + `memory list memory`, then *"Both facts in this batch are already captured in USER.md"*, zero adds → cursor held, `stuck` 0→1. The run was right and the guard was wrong | 1b |

D1 and D2 predate the current work. D8 and D9 are properties of the shape, not bugs.

## Design commitments

Settled by the reference research (`Hermes` — NousResearch, hard per-file caps and no
auto-compaction; `OpenClaw` — advisory storage, hard **injection** cap; upstream `nanobot` —
no cap, git-backed history) plus what was measured here.

1. **A write that carries a fact is never refused.** Refusal is what produced D3's consequence.
2. **Enforce at injection, not at storage.** OpenClaw's split: the file on disk stays whole, the
   copy that enters the prompt is capped. Storage cost is disk; injection cost is every turn.
3. **Deletion becomes demotion.** Nothing is removed, things move to a colder tier. This is the
   single change that makes "make room" always succeed — and it removes the termination hazard
   documented in `dream_cycle.py` (the `"Un terzo trigger"` comment, currently line 393 —
   line numbers there drift, the comment does not), because a `never delete` floor cannot block
   a *move*.
4. **The fact is the unit.** Entry-addressed writes make verification a check instead of an
   estimate (D8, D9).
5. **Pure Python only.** No native wheels — see `FORK_BOUNDARY.md:40` and the `pydantic_compat/`
   precedent. This rules out local embedding models, `sqlite-vec`, faiss and chroma.
6. **No new prompt destinations for Dream.** Naming a fourth write target in its prompt has cost
   whole runs before. New destinations get written by the runtime, not by the model.

## Phase 0 — Stop the bleeding

Small, independent, no design risk. Ship first. **Done and verified on device 2026-08-18** — a
release build of `73fec16` on the Titan 2, PID 16163. `forced_at_stuck` went from 4 to 0 on the
first run that closed a cycle, and the new attribution line renders in the live `/dream budget`.

### 0.1 Reset `forced_at_stuck` when the cursor advances (D2) — **done 2026-08-18**

`finish_dream_cycle` zeroes `stuck` on a successful run but leaves `forced_at_stuck`. After a
reset-and-climb, `stuck == forced_at` again and the escape hatch is skipped.

Shipped as written: `set_review_state` clears `forced_at_stuck` in the same write when
`stuck_runs` normalises to 0. It sits in the store rather than the caller because it is an
**invariant of the file** — the two fields cannot describe different states — and not a policy:
the only caller that passes an explicit `forced_at_stuck` is the livelock branch, which requires
`stuck > 0`. Omission still preserves the value while a climb continues.

Four tests, each confirmed red before the fix: three against the real store
(`test_dream.py::TestForcedAtStuckDoesNotOutliveItsClimb`, including a negative `stuck` so the
two fields cannot diverge through normalisation) and one end-to-end through
`begin_dream_cycle`/`finish_dream_cycle` — climb, force, advance, climb again, force again. The
fake store in `test_dream_cycle.py` had to learn the invariant too, or the end-to-end test would
have been green against a double that never had the bug.

- Watch for: this makes reviews *more* frequent, which is only safe after Phase 2. Until then,
  keep `reviewEveryRuns` at 12 and treat forced reviews as the rare path they are meant to be.
- **Verified on device.** The state read before shipping was the mine, armed:
  `{"runs_since_review": 2, "stuck_runs": 0, "forced_at_stuck": 4}`. After one `/dream` on the new
  build: `{"runs_since_review": 3, "stuck_runs": 0, "forced_at_stuck": 0}`. Note what cleared it —
  a run with **no history to consolidate** (`advanced is None`), which still closes the cycle
  through `finish_dream_cycle` and so still writes `stuck_runs=0`. The disarm does not wait for a
  successful consolidation, which is the right way round: an install sitting in the frozen-counter
  state repairs itself on its next scheduled run, unattended.

### 0.2 Decide what the budget means for the main agent (D1) — **decided 2026-08-18: advisory**

Two honest options, and the state before this was neither:

- **(a) Mount the guard.** Pass `write_size_guard` when the main agent's registry is built for
  the four notebooks. Consistent, and immediately exposes the main agent to D3's refusal
  behaviour — which is why it should land *with* Phase 3, not before.
- **(b) Declare budgets advisory for the main agent** and say so in `docs/using/memory.md` and in
  the gauge. Cheap, honest, and correct once Phase 3 makes injection the real limit.

**(b) was chosen.** Behaviour is unchanged — the guard was never mounted there — but the omission
is now a stated decision with a reason and a cost, in three places: the comment at the mount point
in `agent/tools/filesystem.py`, a behavioural test that loads the real registry and writes 50,000
characters to `USER.md` and `memory/MEMORY.md` unrefused
(`test_write_size_guard.py::TestTheCapsAreAdvisoryForTheMainAgent`), and a paragraph in
`docs/using/memory.md`. The table row there said a write "is refused" without saying whose; it now
says a *Dream* write.

The argument, recorded so it is not relitigated: a refusal mid-conversation lands on the one
writer that has the user sitting there, and trades a visible failure for an invisible one — the
fact just asked for, quietly not saved. The cost is stated rather than hidden: a chat turn can
leave a file saturated, and Dream is what then finds no room. That is D1 → D3, and Phase 2 is what
actually removes it.

### 0.3 Make the gauge say who it binds — **done 2026-08-18**

`/dream budget` reported sizes as if they were enforced for everyone. One line now names the
writer, printed under the file list:

> Enforced on Dream's own writes only. A chat turn is never refused by these numbers, so a
> conversation can fill a file to its cap — and it is Dream that then finds no room.

It carries the consequence, not just the fact, because this is the view someone lands on *after*
noticing a file at its cap — the second sentence is the answer to the question that brought them.
Suppressed when all three budgets are `0`: with nothing enforced there is no constraint to
attribute. Two tests, one per branch, plus the live render off the device.

Note this is `_format_dream_budget_report`, the **user's** view, not `render_gauge` — that one is
written for the model and goes only into Dream's own prompt, where "the write is refused" is true
for its reader.

## Phase 1 — Make the fact the unit

The enabler for Phases 2, 5 and 6. Nothing after this works well without it.

**The tool is written and green** (`f26dd76`, 50 tests) and **mounted nowhere**: no `TOOLS` list,
`_HARDCODED_TOOL_MODULES` untouched. That is deliberate — the list *is* the mount, and who gets
the tool is open decision 4. A module that carried a `TOOLS` list would answer that by omission.
Three properties of what shipped, worth keeping if it is extended:

- **`remove` returns the entry it removed.** Phase 2 turns removal into demotion, and the entry has
  to reach the archive before it leaves the file. That signature is the hook; dropping the text
  here would mean rebuilding it there.
- **No path parameter exists at all.** The two targets are resolved inside the tool, so there is no
  traversal surface to defend rather than a defended one.
- **An `add` that duplicates an existing entry reports it and changes nothing.** Not a failure —
  the fact is in memory, which is what the caller wanted — but saying so hands the consolidator
  the one thing it lacks in D5: knowing that it is re-proposing what is already there.

The test sample reproduces the shape measured on the device rather than an invented one, including
`MEMORY.md`'s habit of putting bullets directly under a heading with no blank line, which `USER.md`
does not do. A parser that only works on the shape its own test invents proves nothing.

**A `memory` tool with entry granularity**, in the spirit of Hermes' `memory add|replace|remove`:

- New module `jenny/agent/tools/memory_entries.py` (**done**), mounted in
  `MemoryStore.build_dream_tools` — **not** in `_HARDCODED_TOOL_MODULES`, which this plan named by
  mistake: that list is the *main agent's* registry, and Dream's is hand-built. The tool takes the
  run's `FileStates` and the same `write_size_guard` as the file tools, because
  `dream_should_advance_cursor` reads those counters and because a second write path outside the
  guard would be a hole in the budget opened by inattention rather than by decision — making the
  caps advisory is Phase 3's call to make, deliberately, not a side effect of this step.
- Actions: `add(file, text)`, `replace(file, old_id|old_text, text)`, `remove(file, id|text)`.
- Targets: `USER.md`, `memory/MEMORY.md` only. `SOUL.md` stays file-edited — it is prose with
  structure, not a bullet list, and the review pass is the right tool there.
- Entries are the existing markdown bullets under their existing headings. **No format change**
  to the files: they must stay human-readable and hand-editable from the Workspace browser.
- Stable ids: content hash, not position. Position is what makes concurrent edits lose data.
- The tool returns current usage and the entry list on every call — Hermes' error response
  carries both, which is what lets a refused write be fixed without a second read round-trip.

Then:

- `dream.md` changes from "inspect the file and make surgical edits" to "propose entries"
  (**done**). Fewer degrees of freedom is the point: the measured failure was the model choosing a
  cosmetic rewrite over an addition. Four things the new text has to keep doing —

  1. It exempts the entry tool from the call-frugality rule. That rule is *why* pruning beats
     adding: batching pressure makes one tidy rewrite look better than two honest calls.
  2. It says the two files need no read first, because every call answers with the entries and
     their ids. A prompt that still implied a read would spend the turn's first call on one.
  3. It tells the model that an "already present" answer means stop proposing that fact — the only
     signal the consolidator has against D5.
  4. **The Budget paragraph lost its last line.** It read *"a run that only prunes is a run well
     spent"*, which is a blessing of the measured failure, written before it was measured. It now
     says the pruning is the first half and ends on `Finish with the `add`.`

  Verification note: `agent/**` templates are re-extracted on every boot
  (`sync_workspace_templates`), unlike the user-owned ones, so this reaches an installed device on
  restart. A prompt fix that only landed on fresh installs was a real production bug once.
- `batch_was_not_consolidated` keeps its shape and its brakes; its **evidence** got better
  (**done**). Three counters now come from the tool, and each answers a different question:

  - `added` — a fact entered. The positive signal that did not exist before; nothing to infer.
  - `already_present` — the model proposed a fact and it was **already on disk**. This is what let
    the pressure threshold be deleted rather than tuned. A batch of pure duplicates (the majority,
    per D5) used to be indistinguishable from a missed batch, and the only way not to hold it was
    to exclude it statistically: *below 90% full, believe the model*. That number rested on three
    observations of one model. The tool now says the content is in memory, which is the same
    conclusion reached by looking instead of guessing.
  - `replaced` — counted **only when the batch carries a `[correction]`**, and the condition is the
    whole point: the measured failure *is* a replace — an existing line rewritten shorter without
    the new fact. Counting it unconditionally would readmit through the window exactly what this
    predicate exists to catch. When the batch asks for a correction, replacing in place is the
    right move and the prompt asks for it, so there it counts.

  `consolidation_landed` stays as a net: the file tools are still mounted and a fact can still
  arrive that way. `under_write_pressure` and `_PRESSURE_PCT` are deleted.

  **A known widening, accepted with open eyes.** Without the threshold, a run that carries facts
  and produces *no* entry at all is now held even at low fill, where it used to be believed. That
  is the case Phase 5 separates properly (`no_room_runs` vs `nothing_new_runs`). Until then it
  costs at most four runs, the log says so each time, and the prompt now asks the model to call
  `add` even just to be told "already present" — which is the call that closes the case.

Keep the file tools mounted for the review pass, which legitimately restructures (**done**, and
now pinned). The positive reason, which was not written down before: the review prompt asks for a
fact to be moved between files in **one** `apply_patch` call, because that tool is all-or-nothing.
Two entry-tool calls across two files are not atomic, and they fail in the worst available way —
the fact removed from the source and never arrived at the destination, with nothing to say so.

The hazard the entry tool introduced here has its own tests: the review pass and the incremental
turn build **two** registries, and their counters must not be shared. `batch_was_not_consolidated`
reads the incremental turn's. If the review pass's leaked in, a review that legitimately adds an
entry while moving a fact would make a batch look landed that the following turn never saved —
the original defect, re-entering through a new door.

## Phase 1b — Let the model batch on the action that produces evidence (D10)

Found by the on-device run of 2026-08-18, and it is the interesting kind of failure: every piece
worked as designed and the design was still wrong.

`already_present` was supposed to remove the need for a pressure threshold — the tool would say
"that fact is already on disk" and the batch would advance. But that signal only exists if the
model *calls* `add`. It does not. Given `list`, which answers for the whole file in one call, it
reads the entries, decides for itself which facts are new, and adds only those. On a batch where
nothing is new it adds nothing at all, and the run produces no entry evidence whatsoever — so the
predicate holds a batch that was, in fact, fully consolidated. That is the 14:01 false positive,
back through a door the fix itself opened.

Asking the model to `add` blindly instead of reading first is the wrong repair, and against this
plan's organising principle: it is asking the model to choose the more expensive path, every run,
forever. One `list` for a whole file against one `add` per fact is not a preference, it is
arithmetic.

So change the arithmetic. **`add` takes a list of facts**, and answers per item: added, or already
present. The economical move and the evidence-producing move become the same call. `list` keeps
its real job — finding the id of an entry you mean to `replace` or `remove`.

- `add` accepts `text` (one fact) or `texts` (many); the result names each outcome.
- `entries_added` and `entries_already_present` count per fact, as they already do.
- `dream.md`: propose the batch in one `add`; use `list` when you need an id.
- The regression case is exact and cheap to replay: a history entry whose facts are all already in
  `USER.md` must advance the cursor, not hold it.

**What the device then said, and it goes deeper than the diagnosis above.** The batched `add` works
— on its first opportunity the model called `memory add` with `texts`, exactly as asked. But on a
batch of pure duplicates it makes **no tool call at all**: one iteration, zero calls, and the
answer *"entrambi i fatti nel batch sono già presenti in USER.md, quindi non c'è nulla da
scrivere"*. It does not need to look. `USER.md` is injected into its prompt, so it answers from
context. No per-entry evidence can exist in such a run, however cheap `add` is made.

So the brake the device points at is `attempted`: hold only a run that **tried to write**. This
function exists to catch the run that wrote something cosmetic and moved on — the 12:01 case, an
`edit_file` shortening a line — and that run does attempt. A run that attempts nothing has not
missed a consolidation; it decided there was none to make, which is the reading
`dream_should_advance_cursor` already gives `writes_attempted == 0`. Holding it means replaying the
same batch in front of the same model with the same context, which answers the same way — four
times, plus a forced review on files with nothing to free. That is the cost measured twice now.

What stays uncovered: the run that *should* have saved and silently decided not to. It is not
distinguishable from a legitimate "nothing new", and judging that is what the design delegates to
the model on purpose. Phase 5 gives it visibility through `nothing_new_runs` rather than a hold.

## Phase 2 — Demotion instead of deletion

Closes D7, defuses D4, and removes the reason D3 hurts.

- `memory/archive/` — **decided and checked, 2026-08-18.** Four things had to hold before the path
  could be claimed, and all four do:

  - **Nothing else uses it.** `memory/` holds named files only (`MEMORY.md`, `WIKI.md`,
    `WIKI_POLICY.md`, `history.jsonl`, three dotfiles, `.atlas_state.json`); no code globs the
    directory, so a subdirectory disturbs nothing.
  - **Snapshots cover it.** `DEFAULT_EXCLUDE_GLOBS` excludes `ui/`, `logs/`, `.jenny/**` and
    `__pycache__` — not `memory/`. The archive is therefore inside the backup and the `.jbk`
    export from the first file it holds.
  - **The user can see it.** The file browser hides only dotfiles and the runtime-internal paths
    (`config.json`, `agent/`, `cron/`, `sessions/`, `ui/`). `memory/archive/` is neither, so a
    demoted fact stays reachable by hand — which is what makes demotion honest rather than a
    quieter delete.
  - **Dream cannot write there, and that is the point.** Its registry allows exactly `SOUL.md`,
    `USER.md`, `memory/MEMORY.md` and `skills/<name>/SKILL.md`. The archive is written by the
    runtime inside the tool (commitment 6); the model is never given the path, so it cannot be
    talked into filing things there and cannot forget to.

  Layout (**done**, `jenny/agent/memory_archive.py`): one file per demoted entry,
  `YYYY-MM-DD-<id>.md`. Frontmatter carries the metadata — `id`, `source`, `heading`, `retention`,
  `demoted` — and the body is the fact alone, with the bullet dash stripped, because the dash
  belonged to the list it came from and here it is a sentence. Unknown fields are omitted rather
  than left empty: a bare `retention:` would claim the information was looked for and lost, when
  the truth is it was never there (the Consolidator's tags are stripped before the text reaches the
  files).

  Many small files, not one growing log: the agent's `grep` silently skips large files, and that
  false negative reads as "I never knew that" — the exact failure the archive exists to prevent.

  Two properties worth keeping. The name sorts by time on its own and carries the same content hash
  the entry tool uses, so a demoted fact is findable from its text without opening anything. And
  archiving is **idempotent**: re-demoting a fact returns the existing file instead of writing a
  second one. The archive is a set of facts, not a log of events — a fact can be re-added and
  removed again, and two files with the same text and different dates would only be noise for
  whoever searches. The first demotion keeps its date.

  The body is also already the right shape for Phase 7: when something eventually embeds this, the
  fact alone is what gets vectorised, with no packaging to strip first.
- **The demotion is mechanical** (**done**). `remove` archives from inside the tool, and the order
  is the guarantee: archive first, shrink the file second. It is the same rule the review prompt
  already teaches for moving a fact between files, for the same reason — of the two ways a
  half-failure can land, "the fact is in both places" is repairable by looking at it and "the fact
  is in neither" is not, and nothing would say so. If the archive write fails, the removal does not
  happen and the answer says why.

  The model is never asked to write there, never told the path, and cannot forget — which is the
  measured failure mode for extra destinations, and commitment 6. It *is* told the fact was kept,
  because that is what makes pruning a reversible choice rather than a gamble.

  **What this does not cover, and it matters.** The review pass does not prune with `memory
  remove`; it uses `apply_patch` and `edit_file` on whole files, which is exactly what it needs to
  restructure. So D4 — the defect this phase exists to defuse, the second consecutive forced pass
  that removed five real entries from `USER.md` — is **still terminal**. Demotion through the entry
  tool protects the incremental turn, not the pass that actually does the deleting.

  The repair is at the file boundary rather than in one tool (**done**, 2.4b): `make_entry_archiver`
  is a pre-write hook mounted beside `write_size_guard` on all four of Dream's tools. Whatever is
  about to rewrite `USER.md` or `memory/MEMORY.md` has its new text compared against what is on
  disk, and every entry that disappears is archived first. `apply_patch`, `edit_file`, `write_file`
  and the entry tool are covered in one place, and no cooperation is asked of the model — which is
  this plan's whole organising principle. **This is what closes D4**, and what Phase 6 actually
  depends on.

  Two properties of the hook, both deliberate:

  - It is mounted **beside** the guard, not folded into it, and it fires inside the write loop
    rather than with the budget check. `apply_patch` checks every target before writing any, so
    archiving at check time would demote entries of a patch that is then refused.
  - It **never raises**. Here demotion is a net, not a condition: an archive that cannot be written
    must not block a legitimate write or fail a run. The strong ordering — archive, and only then
    remove — stays in `memory remove`, where the entry to save is known with certainty and the
    removal is abandoned if archiving fails.

  One consequence worth knowing: an entry that is *reworded* is archived in its old form, because
  the id is a content hash. That is noise in exchange for no previous wording ever being
  unrecoverable, and it is the right direction of the trade — text is cheap, a lost fact is not.
- One flat line in the system prompt (**done**), beside Atlas's wiki directory and for the same
  reason: an index nobody knows about is never opened. An archive invisible to the model is, from
  where it stands, indistinguishable from a deletion — and then deleting would have been simpler.

  Three constraints keep the line honest. It is **flat in the size of the archive** — one count,
  which costs zero extra tokens when the archive doubles; a stub per entry would spend the hot
  budget this phase exists to protect. It **disappears while the archive is empty**, so a new
  install pays nothing for a directory that does not exist. And it **says the directory is not
  writable by the model**: that path lands in a prompt Dream also receives, its allowlist does not
  include it, and for Dream a refused write is not a wasted call but a whole run that commits
  nothing. The runtime files the entries; the line declares that rather than leaving it to be
  guessed.
- Retention: none, and it stays none **by omission** — nothing prunes the archive on any schedule.
  Text is cheap; the point is that nothing is ever unrecoverable again.

With this in place, "free space" is a *move* that always succeeds, so the write need never be
refused — and the review pass can no longer lose anything, only relocate it. That is the actual
repair for D4, and the reason Phase 6 comes after this instead of instead of it.

**Confirmed, and only half of it is done.** The mechanical half holds and is pinned end-to-end
against the real budget guard: starting from a file already over its cap, a removal is never
refused (a shrinking write always passes, or an over-budget file could never be pruned), it costs
nothing because the fact moves, and the full remove → remove → add cycle lands the new fact with
**every** original still readable — each one either in the hot file or in the archive, checked by
id. That is the sequence the model failed six times out of six; the runtime now permits it
without loss.

The other half is a sentence, and it is not written yet. Both prompts still carry a **Never
delete** list — user preferences, personality traits, identity, standing rules — and the model
reads that as "do not take these out of the file". The runtime no longer needs that protection:
taking such an entry out is now a move, and the fact stays readable. Until the wording changes, the
review pass will keep refusing to free the space it is now safe to free, and the termination hazard
the `"Un terzo trigger"` comment describes keeps its premise — *"the rest is stuff the rules mark
never delete"*.

So the capability arrived before the permission. The rewording is **item 6.0**, at the head of
Phase 6 — it is a permission, not a mechanism, and everything else in that phase depends on it.
(It was briefly numbered 2.7b, after the moment it was found rather than the place it belongs. A
plan numbered by discovery order stops being a plan.)

### Verified on device, 2026-08-19

A release build of `0866162` on the Titan 2 (PID 464), with `reviewEveryRuns: 1` and `USER.md`
pushed over its cap, so the review pass had real work and every reason to do it aggressively.
Across two runs it removed three entries with `apply_patch`, reworded seven with `memory replace`,
and rewrote the whole file once with `write_file`. **Ten entries reached `memory/archive/` and
nothing was lost** — every line that left the file was found again, checked one by one against the
copy taken before the run. The model answers "10 voci in memory/archive/" from the prompt line
alone, without opening anything.

Three of the facts recovered by the first run are the same ones a forced review destroyed on
2026-08-18 — the state, the open question and the proposal. That pass is why this phase exists, and
it now relocates instead of deleting.

The run also found D11 in its first ten minutes, which is the pattern by now: the mechanism was
right and its coverage was not.

## Phase 3 — Enforce at injection, not at storage

- Storage caps become advisory (or very generous). `write_size_guard` stops refusing.
- The **injection** cap is hard and applied at prompt build, where `render_gauge` already
  measures. `context.py` already does exactly this for `WIKI.md` via
  `get_wiki_memory_context(max_tokens)` — the seam and the precedent both exist.
- Truncation must be **visible and structured**: drop whole entries from the tail of a section,
  never mid-bullet, and leave a marker the model can act on — `USER.md: 3 entries not shown, read
  the file`. Neither Hermes nor OpenClaw tells the model it is reading a truncated copy; Jenny can,
  because the model has `read_file`.
- New config: `memoryInjectionMaxTokens` / `userInjectionMaxTokens`, next to
  `atlas.maxContextTokens` which is the same idea already shipped.

### 3.1 The measurement — **done 2026-08-20, and it argues against most of this phase**

The gate was: do not port Hermes' ~1,300 tokens on faith, measure the configured endpoint first.
Measured on the Titan 2 against DeepSeek (`api.deepseek.com`, `deepseek-v4-flash`), which does
prefix caching **automatically** — no `cache_control` markers are emitted for it at all
(`_supports_prompt_caching` only fires for OpenRouter + Claude), and `prompt_cache_hit_tokens` is
already parsed into `cached_tokens`.

**The cache works, and well.** 22 days of recorded usage: 2,776 requests, 65.2M prompt tokens,
**81.4% served from cache**, in a 74–86% band with no trend. The heaviest Dream day of this whole
project (2026-08-19) came in at 80.6% — dead average.

**What a memory write actually costs**, isolated over four consecutive turns of the same prompt:

| turn | prompt | cached | uncached | hit |
|---|---|---|---|---|
| B — warm, no memory change | 26,583 | 22,656 | 3,927 | 85.2% |
| C — first turn after Dream wrote MEMORY.md | 26,528 | 4,736 | **21,792** | 17.9% |
| D — the turn after that | 26,578 | 20,608 | 5,970 | 77.5% |

Dream's edit was **+254 chars (~64 tokens)**. It cost **~17,900 extra uncached tokens** on the next
turn — about 280× the size of the edit — and the cache recovered immediately after.

**Three conclusions, and two of them cut against the plan.**

1. **Do not port the 1,300.** `MEMORY.md` (1,172 chars) and `USER.md` (1,661) inject **~708 tokens
   combined**, against an average prompt of ~19–26k. That is **~3%**, already half of Hermes'
   figure, with no cap in force. A cap set anywhere near that number binds nothing. For scale, the
   `# Recent History` block in the same prompt is capped at **8,000 tokens** — eleven times the
   memory it sits below.
2. **Capping the size does not buy cache stability.** The 17,900 is *everything downstream of the
   memory block* — active skills, the skills and apps summaries, recent history, the tool
   inventory, and the whole conversation. It does not depend on how big the memory block is.
   Halving `MEMORY.md` would not move that number by a token.
3. **The lever is position, not size.** `# Memory` is emitted early in `build_system_prompt` and is
   followed by nearly everything else. Moving it last would leave the stable prefix intact across a
   Dream write. That is a real change and a small one — and it is not in this phase.

**And the prize is small.** Dream writes a handful of times a day against ~150–200 user requests,
so this costs on the order of **1% of daily tokens**. Which is the honest reading of a measurement
gate that was allowed to say "do not build this": Phase 3's cache argument does not survive contact
with the numbers, and its context argument is weak at 3% of the prompt. What survives is 3.4
(visible, structured truncation) — and there is nothing to truncate yet.

## Phase 4 — Stop paying for redundant extraction (D5) — **done 2026-08-19**

The consolidator cannot see what has already been consolidated, so it re-extracts the same facts
every pass. Three costs: LLM turns, noise in `history.jsonl`, and a batch of pure duplicates —
which is what made a pressure threshold necessary in Phase 0's world at all.

`MemoryStore.get_known_facts_context()` builds the block; `Consolidator.archive()` appends it to
the system message when it is non-empty. Same shape as `get_archive_context`: the template stays a
static file the user can edit, and the runtime supplies what changes.

**Two sources, and the second is the one that matters.** The plan offered the two hot files *or*
the last N consolidated entries; it takes both, because they answer different halves of the
question. The files say what Dream has filed. The history tail past `.dream_cursor` says what is
already extracted and waiting. With the files alone, the 99/100/101 case — one conversation
consolidated three times before Dream ran once — would stay duplicated exactly as before, because
at the second extraction the files are still empty. The tail is the dominant source, and the only
one of the two without a cap of its own, hence `_KNOWN_FACTS_PENDING_ENTRIES`.

**What must not break, and is what the extra instruction buys.** A blanket "skip what you have
seen before" freezes memory as it grows: a fact that *changes* one already recorded reads as known
and gets dropped, and Jenny stops being able to be corrected. So the block states two exceptions
before it lists anything — a contradiction is a `[correction]` and must be extracted, and an
addition to a known fact is new information about a known subject. The instructions sit *above*
the list because truncation eats the tail: what is lost first must be a fact to compare, never the
rule for comparing.

Three smaller decisions with teeth:

- **The static template no longer says the opposite.** Its old line — *"do not mark something
  `[skip]` merely because it might already exist in long-term memory"* — was right while the model
  could not see that memory: "might already exist" was a guess, and a `[skip]` on a guess loses the
  fact. The rule is now "never `[skip]` on a *guess*", true whether or not the block is present,
  which is what a static file has to be when the block beside it is dynamic.
- **Raw dumps never re-enter the prompt.** When the LLM call fails, a whole conversation lands in
  `history.jsonl` under `[RAW]`. The block is built from annotated fact lines only, so re-injecting
  it would be putting back exactly what consolidation exists to remove.
- **The block is subtracted from the conversation's token budget.** A system block added without
  taking it out of `_input_token_budget` is a request over the window — that is, a failed
  consolidation that raw-dumps the conversation it was meant to compress.

Measurement: `Consolidation for {key}: N facts extracted, K already recorded shown, R verbatim
repeats`. `R` counts **verbatim** repeats only and is therefore a lower bound — a fact re-extracted
in different words does not move it. That is deliberate: the alternative is a fuzzy comparison,
which yields a higher number and a less true one. It is a signal, not a ratio — above zero means
the block is in the prompt and the model is ignoring it, which is the one outcome of this phase no
local test can see.

### Verified on device, 2026-08-19

Release build of `31a9f8b` on the Titan 2 (PID 5770), driven over the `adb forward` WS client.
`/new` is the lever: it calls `consolidator.archive()` on the unconsolidated tail directly, so no
waiting for a token overflow. Two rounds, every fact stated to Jenny true.

**Round 1 — the files.** One restatement of a fact verbatim in `MEMORY.md`, one genuinely new
fact. `2 facts extracted, 33 already recorded shown, 0 verbatim repeats` → cursor 112 carries the
new fact **only**. The restatement never reached `history.jsonl`. That is the phase working.

**Round 2 — the queue, and it failed.** Cursor 112 was now pending (`.dream_cursor` still 110), so
restating its fact should have been a no-op. It was re-extracted, reworded. The `[correction]`
went through correctly, so the escape hatch holds — but the count gave it away: `33 already
recorded shown` both before *and* after a run that had added a fact. Reproduced against a local
mirror of the device's three files: the block was **5,239 characters against a 4,800 cap**, and
the truncation cut the pending entries, which were last. Three defects behind one number:

- **The cap was estimated, not measured.** 1,200 tokens came from adding the two file budgets and
  forgot the instructions above the list and the queue itself.
- **The queue was last** — the dominant source of duplication in the position truncation eats
  first. It now goes first, with a share of its own (`_KNOWN_FACTS_PENDING_SHARE`). The share is a
  floor and not a ceiling — what the queue does not spend returns to the files — and deliberately
  not half: a Dream stalled for days is the very failure this plan is about, and a queue that took
  the whole block would blank the files out of it and re-extract all of `USER.md` at the worst
  possible moment.
- **Truncation cut mid-fact.** Half an entry under a heading reading "already recorded" is a
  *different* fact, so the comparison the block exists to enable runs against something nobody
  wrote. Entries pack whole now, and what did not fit is counted in plain text.

Fixed in `c3fd5cb`; the same mirror then renders all 36 facts with nothing omitted and the waiting
ones first. **Round 3, on the fixed build** (PID 6447): the same restatement of a pending fact —
`1 facts extracted, 36 already recorded shown` — produced only the one fact that was genuinely
new. The repeat is gone.

**Residual, not a defect:** a fact Dream has *consumed but not filed* is in neither source, so it
can be extracted again. That is arguably correct — Dream dropped it on purpose, so it is not
recorded — but it means a fact can cycle. Worth watching, not worth a third source.

## Phase 5 — Split `stuck` into two counters (D6)

`stuck` answers "how many runs since Dream consolidated", and forces a review as the remedy. But
only one of its two causes has that remedy:

- **`no_room_runs`** — a write was refused. A review can free space. Force it.
- **`nothing_new_runs`** — nothing landed and nothing was refused. A review cannot help; it will
  prune files that had nothing to give (observed at 77% / 79%). Log, notify past a threshold, do
  not force.

`format_stuck_alarm` names the actual cause again (**done**). Its hedge was a scar from being wrong
twice: first "keep being refused", wrong for two cases out of three; then "are not landing", true
and useless. With only one cause reaching it, precision is available again and it points at a
remedy that exists — raise a cap.

Two things worth knowing about how this landed:

- **A policy-blocked write is `nothing_new`, not `no room`.** A path outside Dream's allowlist has
  nothing to do with space, and freeing some does not make that file writable — so the old code
  forced a review that could not help. A test now pins that it does not.
- **The two counters reset together**, because the same event resets them: the cursor advancing.
  One left standing by omission would describe a block that is not there.

The give-up branch in `batch_was_not_consolidated` takes the **sum**: "how many runs in a row has
this batch failed to land" is one question regardless of why, and that is what bounds the replay.

### Verified on device, 2026-08-19 — and the first two attempts failed to reproduce

Release build of `c3fd5cb` (PID 6447), four `/dream` runs over the WS client, budgets moved only
through `/dream budget` so every write went through `config/store.py::mutate()`.

**Making a file over-budget is no longer enough to cause a refusal.** Run A, both files capped 41
chars under their size: *"Done. Both files were over budget, so I freed space then wrote."* Cursor
advanced, `stuck_runs` 0. Run B at 144% of cap: same, and the batch was judged unworthy anyway.
This is Phase 2 working as designed and it invalidates the premise the counter was built on — a
cap that binds is now a cap the model routes around, because demoting costs it nothing.

**So the refusal has to be unavoidable, not merely likely.** It happens when the model issues a
*growing* write while the file is over its cap, before pruning. Run C, MEMORY.md capped at 1,600
against 2,311 with a batch of three facts it actually wanted: it pruned to 1,508, saved the facts
**in abbreviated form**, and one refused write was never recovered — the refused text never
reached disk verbatim, so the record stayed open. Result: `{"stuck_runs": 1, "nothing_new_runs":
0}`, cursor held at 115.

That single line is **Phase 5 verified**: the one cause a review pass can act on incremented its
own counter, and the counter that forces nothing stayed at zero. `nothing_new_runs` is also absent
from `/dream budget` while zero (5.2), and present in `.dream_review` once written.

**Then the line the plan had never seen.** Cap raised back to 3,000 — room freed, the exact remedy
`format_stuck_alarm` names — and run D replayed the held batch: *"Both durable facts from this
batch were already in MEMORY.md — they'd been saved in abbreviated form"*, and it wrote them out in
full. MEMORY.md 1,508 → 1,709, cursor 115 → 116, `stuck_runs` 1 → 0. **A fact from a held batch
landed after room was freed.** `6a11ff2` is a repair, not just a brake with an alarm.

**Nothing lost, checked line by line.** 16 entries left the hot files across the four runs, merged
or reworded; all 16 are in `memory/archive/`. MEMORY.md ended leaner than it started (2,441 →
1,703) with everything demoted recoverable and the review pass never having run (6 of 12).

**What this costs the plan.** Phase 3 removes this observation's preconditions: with
`write_size_guard` no longer refusing, `refused` is structurally zero, `stuck_runs` cannot climb,
and the `attempted` brake never fires. The evidence above is therefore the *only* evidence this
path will ever produce, which is why it was taken before Phase 3 rather than after.

## Phase 6 — Give the review pass its trust back

Unblocked by Phase 2: with demotion, a pass that over-prunes has *relocated* facts and the archive
holds them. What remains is telling it so, and watching what it does with the permission.

**Done 2026-08-19, 6.2 first and 6.0 in the same change.** What follows describes what shipped.

**6.0 is a sentence.** Both prompts still carry a *Never delete* list, and the
model reads it as "do not take these out of the file". The floor should read *never lose* instead:
the criteria for what deserves to stay hot stay conservative, but removal is no longer terminal.
Without this the review pass keeps declining to free space that is now safe to free, and the
termination hazard in `dream_cycle` keeps its premise.

**6.0 ships with the logging, not before it.** Demotion is not free: an archived fact is out of the
prompt, so the observable effect of a pass that moves too much is "Jenny forgot" — recoverable, but
only once someone thinks to look. The post-condition below is what makes that visible, and shipping
a broader permission without it is the one ordering that could do real damage.

- A review that demotes more than **five** entries in one pass logs *what* it moved, not just how
  many: the count says how much, and someone reading a warning needs to know what, or they cannot
  decide whether to go and look. Five is about a quarter of a real `USER.md` (19 entries on the
  device). The list also rides in `ReviewOutcome.demoted`, because "how much did it free" and "what
  did it take out" are different questions and the second finally has an exact answer instead of a
  delta in characters.

One thing the rewording had to get right: the archive covers the two **entry** files. `SOUL.md` is
not one of them — nothing archives what leaves it, and a line removed from it is gone. So its floor
stays literal while the other two move to *never lose*. A single relaxed sentence covering all
three would have quietly licensed the one deletion that is still terminal.
- **Only then**, and only if the device justifies it, `reviewEveryRuns` comes down from 12. The
  documented floor of 6 exists because of terminal deletion and that premise is gone, but a faster
  cadence arriving together with a fresh permission is two changes at once on the pass with the
  worst measured failure.

### Verified on device, 2026-08-19

Release build of `135fc48`, PID 3287, same setup as the Phase 2 run: `reviewEveryRuns: 1` and
`USER.md` pushed over its cap. The behaviour changed exactly where the wording did. Under *never
delete* the same pass had reworded seven entries to scrape back 109 characters and removed nothing.
Under *never lose* it **merged and demoted**: six entries left the file, four rewritten ones took
their place, 181 characters freed. Two facts were demoted outright with no replacement — the
permission being used, not just tolerated.

All six are in `memory/archive/`; nothing was lost, checked line by line against the copy taken
before the run. And 6.2 said so, by name rather than by count:

> `Dream review demoted 6 entries to memory/archive/ in one pass: Preferisce le riunioni corte del
> mattino e rifiuta quelle del venerdì…; **Strumenti**: usa il portatile solo in trasferta…`

*(Both entries above are invented — the repo is public. The shape is the real one: the first line of
each demoted fact, truncated, and as many as the note names.)*

That is the pair working as intended: a broader permission, and a line in the log that tells you it
was used and on what. **6.1 stays untouched** — `reviewEveryRuns` is back at 12. One run is not a
basis for making this happen more often.

## Phase 7 — Retrieval for the cold tier

Only once the archive has content, and in this order:

1. **LLM over a compact index** — **done 2026-08-19**, `jenny/agent/tools/memory_recall.py`.
   One line per archived entry, and it selects on **salience**, which is what personal memory
   needs and what cosine similarity has no notion of.

   Three departures from this paragraph as it was written, each for a reason found while building
   it:

   - **No `spawn`.** The machinery exists, but it is asynchronous by contract — *"the result
     arrives on its own, do not poll"* — and a recall that answers three turns later is not a
     recall. Worse, it puts the choice in a model that cannot see the conversation, and the
     conversation is what says which of the archived facts is the relevant one. The list goes to
     the model that asked. A selection pass earns its place only when the list stops fitting a
     tool result, which is the same threshold this section already names for embeddings.
   - **No index on disk.** The list is derived from the directory on every call. A stored index is
     a second truth to keep aligned, and the first time it drifts it lies at exactly the point
     where this tool has to be trustworthy. One small file per entry was chosen in Phase 2 so that
     re-reading them costs nothing.
   - **The prompt line stops saying `grep`.** `get_archive_context` now names `recall` and says
     why not `grep`: substring matching cannot find an Italian fact from an English question, and
     it skips large files without saying so. Both failures read as *"I never knew that"*.

   The cap on the list is 24,000 chars, and passing 75% of it **logs**. That is deliberate: the
   trigger for step 2 below — "the index stops fitting a context" — is not observable unless
   something measures it, and the alternative is choosing the moment by feel.
2. **Embeddings, later and only if the index stops fitting a context** (order of a few thousand
   entries). Then: remote embeddings (no local model is possible under commitment 5), binary
   quantisation, and Hamming distance via big-int `XOR` + `int.bit_count()` — C-speed inside
   CPython, no numpy. Phase 2's archive layout is already shaped for this; do not build it early.

Embeddings buy two things `grep` can never do: no silent skip on large files, and cross-language
recall — this memory is bilingual, and a fact stored in Italian is invisible to an English query
under substring search.

### Verified on device, 2026-08-20

Release build of `78b3b39` (PID 7271), 44 entries in the archive.

**It is reached for without being asked.** An English question about a fact recorded in Italian —
where a plant was originally collected, a detail that survives only in the cold tier — produced
`recall({})` in the same round as a wiki `grep`, on a prompt that never mentions the tool by name.
The archive line in the system prompt is what puts it there, and naming the tool instead of `grep`
is what made it the obvious move.

**Both actions are correct on the real directory.** The index reported 44 of 44 with nothing
truncated, and `recall({"ids": ["028efef8"]})` returned the entry with `from USER.md › Work,
demoted 2026-08-19` — the provenance the archive format exists to carry. The crowding log did not
fire, correctly: ~4k chars against a 24k cap.

**Two things the run taught, neither a defect.**

- **Same-day entries have no meaningful order among themselves.** The filename sorts by date first
  and by content hash second, so "newest first" is true by day and arbitrary within one. It only
  shows when a whole archive was written in a single afternoon, as this one was.
- **Today's archive holds almost no facts that are actually gone.** Nearly every entry is a
  superseded wording of something still present in the hot files, because `replace` archives the
  old version (the D11 fix). Two attempts to find a genuinely absent fact to ask about both failed
  that way. This is good news about how little has been lost, and it means the tool's value is
  still mostly *latent* — it will be paid out by the demotions Phase 3 makes routine, not by the
  ones taken so far.

## Verification

Unit tests are necessary and were **not sufficient**: the on-device run exposed three defects the
suite could not see (a double log line with a contradictory second message, a message claiming
"wrote to disk" on a run whose only tool call was `read_file`, and a false-positive class that
fired on every duplicate batch). Each phase needs both.

**Suite** — `ruff check jenny/ tests/`, `npx pyright` on the blocking subset, and the tests on
**both** interpreters. `python3` here is 3.14, the device runs 3.11, and that gap has hidden a real
bug before:

```bash
/tmp/py311/bin/python -m pytest -q
```

**On device** — the loop that found today's defects, worth keeping:

```bash
adb -s <phone serial> forward tcp:18790 tcp:18790
```

then a `websockets` client to `ws://127.0.0.1:18790/?client_id=x&token=<websocket.token_issue_secret>`
for slash commands and exact timings. Read state with `su -c cat` on
`workspace/memory/{.cursor,.dream_cursor,.dream_review}`; **never write workspace files as root** —
wrong owner and SELinux label, and the app fails on them afterwards.

To create pressure deliberately: `/dream budget user <just above the current size>`, then drive
`/dream` and watch `.dream_review`. Restore the budget afterwards. Attribute results **by PID**,
not by clock — an install restarts the app within the same second and a cron cycle that fired
before the swap belongs to the previous build.

Each phase also needs the thing today's test could not produce: **a fact from a held batch landing
after room is freed.** It has never been observed. Until it is, the guard is a brake with an alarm,
not a repair.

## What this plan does not cover, and now has its own

Four of the guarantees shipped here are not mechanisms — they are requests written in a prompt, and
they hold because the model honours them: the consolidator not re-extracting (Phase 4), Dream
freeing space instead of taking a refusal (Phase 2), the review pass demoting rather than emptying
(Phase 6), and `recall` being reached for at all (Phase 7.1). Each was observed working by hand,
once or twice. None is checked by anything, and when one stops holding **nothing goes red**.

That is a different problem from this plan's, and it outlives it: see
[`behaviour-harness-plan.md`](./behaviour-harness-plan.md).

## Non-goals

- **Do not copy Hermes' numbers.** 2,200 / 1,375 against an unpruned `USER.md` of 3,524 is
  permanent saturation.
- **Do not arm `soulBudgetChars`.** That file mixes Jenny's identity with platform notes and a
  size limit cannot tell them apart. The review pass reads before deciding; the guard does not.
- **Do not add a native dependency** for any of this (commitment 5).
- **Do not put a compaction/compression proxy in front of the provider.** Wrong layer: the
  measured failure is an editorial decision, and compressing transport does not change one.
  (A content-aware replacement for the flat `maxToolResultChars` cut is a real, separate win —
  today a 20k traceback loses its tail, which is where the error is.)
- **Do not make MEMORY.md the store.** After Phase 2 it is the working set; the store is the
  archive. Most of the pain in the register comes from those two being the same file.

## Open decisions

1. ~~**Phase 0.2**: advisory-for-the-main-agent, or mount the guard?~~ **Closed 2026-08-18:
   advisory.** Storage caps are the wrong lever once Phase 3 lands, so there is nothing left for
   the guard to protect there.
2. ~~**Phase 2's cold tier home**: `memory/archive/` versus reusing Atlas's `wikis/` tier.~~
   **Closed 2026-08-18: `memory/archive/`.** The wikis are topical knowledge bases (`ricette`,
   `astronomia`, `bricolage`, `scacchi`) — knowledge *about a subject*. A demoted entry is not
   that; it is a piece of someone's history, and filing it beside Android partition layouts
   degrades both. The decisive difference is mechanical, though: Atlas compiles `wikis/` into
   `WIKI.md`, and `WIKI.md` **enters the prompt**. The archive must not — it is the cold tier, the
   place you go and look. Putting it under `wikis/` would put it back in the hot budget by a route
   nobody chose.
3. ~~**Phase 3's injection numbers** — measure the provider's prefix-cache behaviour first.~~
   **Closed 2026-08-20: do not port the 1,300, and do not cap for cache reasons.** The two files
   inject ~708 tokens (~3% of the prompt) with no cap in force, and the cost of a memory write is
   the *downstream* prefix — ~17,900 tokens, independent of the block's size. The lever is where
   the block sits, not how big it is, and the whole prize is ~1% of daily tokens. See 3.1.
4. ~~**Phase 1 scope**: does the `memory` tool also serve the main agent, or Dream only?~~
   **Closed 2026-08-18: Dream first.** A flaw in a tool that writes memory costs a replayed batch
   when a nightly run finds it and the user's turn when a conversation does. The main agent keeps
   whole-file writes until the unattended path has exercised this one; giving it the tool is then a
   one-line change plus its own verification.
