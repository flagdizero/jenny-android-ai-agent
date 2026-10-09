# The gardener

Capture, inside a [notebook](./projects.md), is deliberately cheap: a fact you mention becomes one line in that day's journal and nothing more. The **gardener** is what happens to those lines afterwards. It is a background pass that runs between conversations, reads the journal lines nobody has read yet, turns the ones that earn it into pages, and brings the map back in line with what now exists. Then, in the same run, it brings the notebook's [board](./board.md) up to date.

It is the second periodic worker after [Dream](./memory.md), and like it, it is **on by default**. Unlike Dream, it writes inside *your* folders rather than the memory files — which is why almost everything below is about what it will not touch.

## What one pass does

1. **Promote what earns a page.** A journal line that names something the notebook will keep talking about — a place, a person, a constraint, a decision — becomes, or joins, a page named after that thing. Several lines about one thing are one page, not three.
2. **Leave the rest.** Chatter, one-off logistics, a detail that only made sense that day: these pass through. A pass that promotes nothing has done its job correctly, and most passes are that.
3. **Link.** A page that names something with a page of its own links to it with `[[page-name]]`. Those links are what make the folder a wiki.
4. **Then fix the map**, last: `wiki/index.md` describes what now exists.

After that comes a second, separate turn over the notebook's board — see [The board pass](#the-board-pass).

Every page it writes carries frontmatter: a `state:` of `open`, `hypothesis`, `decided` or `done`, and a `source:` pointing at the journal day the content came from — the trail from a page back to the sentence that caused it. A newly promoted line starts at `state: open`; only your own words, or a page that already says so, can justify anything stronger.

## When it runs

Three clocks have to agree before a pass starts, and then there has to be something to do.

| Clock | Default | What it means |
|---|---|---|
| Interval | every **30 min** | How often it *looks* for work. A tick that finds nothing makes no LLM call and costs nothing. |
| Required silence | **30 min** | How long that notebook's conversation must have been quiet. The gardener works on cold material: entering mid-conversation would promote half of a discussion and rewrite the map while you are reading it. |
| Distance between passes | **6 h** | How long before it comes back to the *same* notebook. This is the measured failure mode of Dream written as a number — a second close pass on one subject reworks what the first wrote instead of adding to it. Counted per notebook, and counted from *attempts*, not successes, so a notebook that keeps failing cannot monopolise every tick. |

On top of that:

- **One notebook per tick.** Every eligible notebook is collected and the least-recently-touched one runs; never-gardened notebooks come first. The others are named in the log and wait for the next tick.
- **A notebook with a turn in flight is skipped outright**, whatever the silence setting says.
- **One pass per notebook at a time.** A second pass on the same notebook is *refused*, not queued — two passes would overwrite each other's pages.
- **You always win.** The pass re-checks whether you are active in that notebook before *every* write. If you come back mid-pass it stands down, keeps whatever pages already landed, and leaves the journal marked unread so the next pass sees those lines again. (`/stop` in the notebook chat does not cancel a pass — the pass runs under its own session key. Coming back is the mechanism.)
- Being a periodic job, it only runs while Jenny is running — see [Scheduling and proactivity](./scheduling.md).

There is a **second reason** a pass can start, with no new journal lines at all: the notebook's map has grown past its 2,000-character ceiling *and* is bigger than the last pass left it. Then the gardener goes in for the map alone, moving prose out to the pages it belongs to. If a prune leaves the map still over the ceiling, it is not retried until the map grows again — a reason that stays true after a pass would otherwise loop forever.

There is a **third reason**, also with no new journal lines: the notebook's [board](./board.md) needs work — a first board to propose, drawers whose pages changed, new pages that are in no drawer, a correction you made from a drawer, or a page you asked to see on the board. Then only the [board pass](#the-board-pass) runs. The same clocks above apply, and the board has a brake of its own on top: after a board pass, the next one waits **60 minutes** if the reason is yours (a correction, a page you asked for) and **6 hours** otherwise, so a reason the pass could not settle does not bring it back on every tick.

Each pass reads at most **200** unread journal lines; if there are more, the overflow is disclosed in the pass's own prompt and picked up next time.

## What it may write, and what it must not touch

| Path | The gardener |
|---|---|
| `<notebook>/wiki/` — pages and the map | **writes.** This is the whole writable surface. |
| `<notebook>/raw/journal/` | appends only, and only to recover a fact you said that capture missed. Never rewrites a line. |
| `<notebook>/AGENTS.md` | refused — those are the notebook's premises, and they change when *you* change them, in conversation. |
| `<notebook>/raw/`, `<notebook>/audit/` | refused — `raw/` is verbatim by definition, and `audit/` is your channel, not its. (A board correction it has applied is closed by the code, with the sentence the pass gives it.) |
| `<notebook>/board/` | only through the board tools of the [board pass](#the-board-pass), never as files; a write that would touch anything of yours is refused. |
| `<notebook>/log/` | written by the code, not by the model: the pass records itself. |
| Anything else in the installation | refused. It cannot even read outside the notebook it was given. |

And these rules govern what it does inside `wiki/`:

- **Add and promote; do not rewrite.** On a page that already exists it appends, or it changes the `state:` — it does not re-word body text that was already right. Rewriting is how a wiki decays one careful pass at a time.
- **A page that outgrows the budget is split, not shortened.** Past 6,000 characters a page stops entering conversations entirely, so it is cut along the things it talks about: each part becomes a page named after its own thing, carrying the sentences that were already about it, moved word for word. The original keeps its name and one part and links to the others. Nothing is deleted and no sentence is re-worded — which is why a split counts as a promotion rather than a rewrite.
- **Never delete a page, or a section of one.** A split (and a map prune) move text verbatim into a page the original links to; that is the only exception, and it is not a deletion.
- **A workspace snapshot is taken before every pass**, tagged `pre_gardener`, so a pass that made a mess is recoverable from the [snapshot](./backup.md) list. Two honest limits: "never delete a page" is a prompt rule and not enforced by the toolbox — a page overwritten with nothing would succeed — and if the snapshot itself fails, the pass runs anyway and says so in the log rather than refusing to work.
- **It does not settle contradictions.** If two pages disagree, or a journal line denies a page marked `decided`, it leaves both alone and writes the question into the map's **Open** section — one line, naming both pages. In a notebook without a board, the map is part of every turn, so the question reaches the person who can answer it. In a notebook with a [board](./board.md), the board takes the map's place on every turn, and the Open section is read only when Jenny opens `wiki/index.md`. Deciding it itself is the one thing it must not do.

## What a pass can see

A pass is given: the unread journal lines, the map, the list of pages that exist (marked with which of them are already over the page budget), your recent messages in that notebook's conversation, and Jenny's identity files (`SOUL.md`, `USER.md`, `memory/MEMORY.md`).

It is **not** given the list of your other wikis (the `## Wikis` block) or the tail of your personal conversation. Both used to arrive and were closed deliberately: the choice of notebook has already been made, and a maintenance pass with no user to talk to has no business carrying either your private life or an inventory of your other notebooks into a page you will read. Jenny's identity stays, because the pass writes prose you read and the alternative is the one actor with no idea who it is writing for.

The transcript it sees is a window — the recent stretch, capped — not the whole record.

### Recovering what capture missed

Capture happens live, mid-conversation, and it misses things. So a pass also compares what you *said* over the recent stretch against what the journal actually recorded, and looks for one stable fact that never made it in. If it finds one it appends a single journal line, marked `[recovered]`, and the next pass promotes it like any other line.

It recovers the *line*, never the page — and it is told to leave anything it is unsure about, because the journal it can see is a window: a fact it cannot find there may simply have been recorded on an older day, and a duplicate line becomes a second page or a page that argues with itself.

## The board pass

A notebook with a [board](./board.md) is curated in a second turn, after the wiki pass and in the same run, with its own prompt and its own closed set of tools. It reads the result of the wiki pass — the pages just written, the ones that changed — and the same new journal lines. In this order:

1. **Your corrections first.** A correction made from a drawer is an audit file on the page the section came from. The pass applies it to that page — appending or adjusting the sentence, not rewriting the page — and closes it through a tool: the code writes the resolution and moves the audit file to `audit/resolved/`. The drawer is then rewritten from the corrected page.
2. **New pages into drawers.** A page that is in no drawer and new since the last pass goes into a card's drawer when it clearly belongs to one. Otherwise it stays off the board, still searchable.
3. **Rewrite the drawers whose pages changed** — and only those. The code keeps a fingerprint of each drawer's pages; a drawer whose pages are unchanged is not sent to the model at all. A rewritten drawer has a brief and the skeleton sections the pages have material for, two short sentences each, every one citing its pages. A section whose meaning did not change is copied as it was, so that the blue dot means something.
4. **Reminders from the journal.** A new journal line that decides or plans something with a date becomes a dated reminder on the card it is about, pointing at the page that explains why. Not a fact, not a wish without a date, not something already done, and never the same reminder twice.
5. **Proposals**, at most **2** per pass, into the [envelope](./board.md#the-envelope-jennys-proposals): a new card, putting a card away, a thread, a page into a drawer, a mini-app section. Proposing nothing is a normal outcome.

A notebook with pages and no board gets, instead, its **first board** as one proposal: the skeleton (yours, if you gave one when you created the notebook) and up to 9 cards, with every drawer already written.

What the board pass cannot do is decided by its tools and by the board's write funnel, not by its prompt: there is no tool to move, add or remove a card, to change your sections, threads or skeleton, or to take a page out of a drawer, and a proposal you already declined is refused. Its file tools write only inside `wiki/`, and it is told to use them only to apply a correction. The *since you last looked* rows on the board are written by the code from what the tools actually did.

The gardener does maintenance and proposals, and nothing you ask for: a change you want — split a card, move pages, put a card away — you ask for in the notebook's chat, where Jenny hands it to the [notebook editor](./board.md#asking-jenny). What the editor does counts as yours, so this pass cannot undo it; a journal line that records such a request is history to the pass, not a task.

Some things are left out on purpose:

- **Research wikis** — those with `concepts/` or `entities/` folders — get no board, and no board pass.
- **A failed wiki pass, or you coming back mid-pass, means no board pass**: the board waits for the next run, like the journal lines.
- **A board failure never changes the wiki pass's outcome.** It is logged, and a later run tries again.
- If the wiki pass did not run (a board-only reason), the board pass takes the `pre_gardener` snapshot itself, since applying a correction writes a page.

## What it tells you

Normally nothing — the folder is the output, and a pass that wrote nothing leaves no trace at all. Beyond that:

- What a board pass did shows on the board, as *since you last looked* rows: drawers rewritten, pages put into drawers, reminders taken from the journal, corrections applied, new proposals.

- Something it could not settle goes into the map's **Open** section (which every turn in a notebook without a board reads) *and* as one line in the notebook's `log/`.
- After **three** consecutive passes that failed to record any progress, you get a notification: *"The gardener has failed N passes in a row on 'X' (status): its journal is not becoming pages. Open that project and send /gardener to see the error."* (`status` is the last pass's outcome word; the alert's English text still says *project* where the rest of the app says *notebook*.) It costs no tokens and does not depend on the model, which in that situation may be exactly what is broken — and it repeats on every further failed pass rather than only at the crossing.

## Running one by hand: `/gardener`

Send `/gardener` **from inside the notebook**: it replies immediately with `Gardening <notebook>...` and posts the outcome when the pass finishes. It takes no arguments — the notebook you are in is the subject — and outside a notebook it is refused, with a line saying to open one.

There is no way to garden a notebook from the personal chat. That is deliberate and it matches the tool layer, where `journal_append` outside a notebook refuses and has no argument for reaching one; the command used to accept a notebook name, and it was the only thing in the system that did.

The outcome message distinguishes the ways a pass can do nothing — nothing new in the journal (no tokens spent), nothing that earned a page (journal marked read), finished without writing (journal left unread) — from the two ways it can do half the job: some writes refused, or pages written but the read-position not recorded. In both of those the pages are on disk and the journal is deliberately left unread, so the next pass sees those lines again. Re-promoting is safe by design; losing a line is not.

## Setting the periodic pass

Everything about the automatic pass is in the workshop's **Memory** drawer, in the **Gardener** group (the workshop opens from the bottom of the home's Settings page): whether it runs, how often it looks, how much silence it needs in that notebook's conversation, how long before it comes back to the same notebook — and, in the same group under *Notebook history*, whether an idle notebook's chat history is archived. See [Settings](../reference/settings.md#gardener).

Each control says what the value changes, and the numbers carry the ranges the schema enforces, so the field cannot offer one and the server refuse another. Every write goes through the config write funnel: a value you set is read back and kept, and setting a value to what it already is does not rewrite `config.json` at all. Turning the pass on or off, and changing the interval, re-arm the periodic job immediately — no restart.

## Turning it off

The **Periodic pass** switch in **Memory** → **Gardener**, and it applies live: the periodic pass stops looking. `/gardener` still works by hand from inside a notebook, so turning it off is not the same as losing the feature; turning it back on re-arms the job without a restart, even if the gateway started with it off.

Turning it off also stops the [board pass](#the-board-pass): drawers are no longer rewritten, corrections made from a drawer wait, and no proposals arrive. The notebook clock that announces reminders is a separate job (`reminders`, see [Notebook board](./board.md#reminders-and-the-notebook-clock)) and keeps running.

Turning the gardener off does **not** turn off capture: journal lines keep being written by the conversation itself, and they simply wait until a pass — periodic or manual — reads them. Capture is governed by the Writes/Read-only switch, not by this.

## Configuration reference

Under `agents.defaults.gardener` in `config.json`, all four settable from **Memory** → **Gardener** in the workshop:

| Key | Meaning | Default | Range |
|---|---|---|---|
| `enabled` | Register the periodic pass | `true` | — |
| `intervalMin` | How often it looks for work | `30` | 1–1440 |
| `idleMin` | Silence required in that notebook | `30` | 0–1440 |
| `minHoursBetweenPasses` | Gap before returning to one notebook | `6` | 0–8760 |

Plus one sibling under `agents.defaults`:

| Key | Meaning | Default |
|---|---|---|
| `compactProjectsWhenIdle` | Archive a notebook's chat history once it goes idle, like the personal one | `false` |

An out-of-range number in a `config.json` written by an older version is **clamped to the bound**, not rejected — a stricter schema that refused it would quarantine the whole file and take your provider settings with it. That clemency is also what makes the off switch work from such a file, which is the one path that must never fail.

## See also

- [Notebooks](./projects.md) — what a notebook is, capture, the board, the map and the pages, and the history fence.
- [Memory and Dream](./memory.md) — the other periodic worker, and the personal memory files.
- [Notebook board](./board.md) — the board the second half of a pass keeps current.
- [Wiki](./wiki.md) — reading and auditing the pages the gardener writes.
- [Configuration (config.json)](../reference/configuration.md) — the full config reference.
