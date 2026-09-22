# Projects

Everything you say to Jenny normally lands in one continuous personal conversation. A **project** is the other kind: a separate conversation bound to one folder, which remembers what it is told by *writing it down* in that folder instead of by feeding Jenny's personal memory.

You switch between them with the chip above the message box. The chip says `jenny` when you are in the personal chat, and `wikis › <name>` when you are inside a project.

## What a project is

A project is a folder under `workspace/wikis/<name>/` — the same place [wikis](./wiki.md) live, because a project *is* a wiki. There is no separate `projects/` directory, and a project you create from the chip is a notebook like any other.

A freshly created project looks like this:

```text
workspace/wikis/greenhouse/
  AGENTS.md              this project's own instructions (its scope, and its id)
  wiki/
    index.md             THE MAP: what this is, what is decided, what is open, which pages exist
    <one page per thing>.md
  raw/
    journal/YYYYMMDD.md  the working journal — one file per day, append-only
    research/            what arrives from outside, copied in verbatim
  log/YYYYMMDD.md        one line per operation
  audit/                 the human correction channel (see the Wiki page)
    resolved/
```

Subfolders under `wiki/` are allowed, not required. Wikis that existed before this layout keep whatever structure they already have — Jenny follows the structure it finds, and that project's `AGENTS.md` is the authority on how that one works.

## How a project differs from the personal chat

| | Personal chat | Project |
|---|---|---|
| Session history | `sessions/unified_default.jsonl` | `sessions/project_<name>.jsonl` — its own thread |
| Feeds long-term memory? | Yes — the [Dream](./memory.md) pipeline builds `MEMORY.md`, `USER.md`, `SOUL.md` from it | **No.** A project conversation never writes to the personal diary |
| Remembers by | Dream, between conversations | Writing facts into its own folder, during the conversation |
| Where writes may land | Anywhere in the workspace | **Only inside its own folder** |
| Reminders, cron, scheduled jobs | Yes | Refused — Jenny tells you to switch to the personal chat |
| Mini-app data | Read and write | Read yes, change no (mini-apps and their data are personal) |
| Prompt carries | Your profile, plus the [list of your wikis](./memory.md#how-jenny-knows-which-wikis-you-have) | Your profile, plus **this project's** map and pages — not the list of the other wikis |

The memory boundary is deliberately **one-directional**, and it is worth reading twice: *who you are travels into a project, where else you work does not.* `SOUL.md`, `USER.md` and `memory/MEMORY.md` are read from the installation root and reach a project's turns, so Jenny still knows your language, your habits and your context while working there. What does not reach a project is the cross-project inventory (the `## Wikis` block) and the tail of your personal conversation. And nothing said inside a project flows back the other way: it is not archived into `memory/history.jsonl`, so Dream never sees it and it cannot end up in `MEMORY.md`.

The practical consequence: **a project is not private from Jenny, and the personal chat is not informed by it.** If you want something you said in a project to be part of Jenny's general knowledge of you, say it in the personal chat too.

## Writes stay inside the folder, reads do not

Inside a project, Jenny may read anything in the installation — skills, other wikis, your notes, code — but may write only under `workspace/wikis/<name>/`. A write outside is refused at the tool, including a write into *another* project: there is no cross-project work.

This is enforced by the turn's own write boundary, not by a prompt asking nicely, and it survives delegation: a subagent spawned from a project turn gets the installation as a read-only extra and the project folder as its only writable root. It also applies to code — inside `python_exec`, `open(..., 'w')` and `os.remove` on a path outside the project are refused the same way.

## Creating a project

From the chip above the message box: tap the chip, then **New project...**. Two questions follow.

1. **Project name.** Letters, numbers, dot, dash and underscore; it must start with a letter or a digit and fit in 64 characters. No spaces and no accents — this name is a folder name *and* a conversation address, so `Greenhouse Notes` and `caffè` are refused. If the name already exists you get a warning rather than a refusal ("If it was left half-built I will finish it, otherwise the creation will be refused") with a **Try anyway** button.
2. **What is it about** — one line: what belongs here and what does not. This is required; without it the chat starts on nothing. It is stored as the project's summary and as the opening line of both `AGENTS.md` and the map, and it is capped at 500 characters.

On success the chip drops you straight into the new project. The scaffolding — folders, `AGENTS.md`, an empty-but-structured `wiki/index.md`, today's `log/` entry — is written for you; nothing that already exists is overwritten, which is why re-running the creation on a half-built folder repairs it instead of clobbering it.

Later, once the project has some pages, `/init` inside it rewrites that project's `AGENTS.md` from what the folder actually contains — its scope, the conventions the pages already follow, and the open questions. Outside a project, `/init` refuses and tells you to open one — the chip above the composer does it.

To **delete** a project, open the chip and tap the bin on its row, then confirm — the same place you created it. The confirmation names how many messages of its conversation go with it, because a project's chat is deleted together with its folder. The Workspace file browser offers the same thing from the project's folder. (Deleting the folder by hand, or over [ssh](./ssh.md), also works but leaves the conversation behind under a name that is now free — which is why the file browser refuses that route and routes you to the project delete instead.)

A folder whose name breaks the naming rule is **not listed in the chip** at all, and cannot be opened as a conversation. Renaming it (letters, numbers, dot, dash, underscore) makes it appear.

## The switch beside the chip: Writes or Read-only

Next to the chip is a two-state switch — **Writes** (pencil) and **Read-only** (eye). It applies to both kinds of conversation, and it answers one question about the message you are *about to send*: may it change anything on this device?

- **Writes** is the default. The chat can create and edit files, download, capture to a project's journal, schedule reminders, install an app update.
- **Read-only** means nothing on the device changes. Jenny still reads anything, still runs code that computes and reports, still answers and still messages you. What it does instead of writing is *describe* the change: which file, what would go in it, and why. That description is the deliverable, not a preamble to an attempt.

What read-only refuses, concretely: `write_file` / `edit_file` / `apply_patch`; every write route inside `python_exec` (including `open(..., 'w')`, `os.remove`, `shutil.rmtree`); downloading a file; appending to a project journal; adding, listing or removing scheduled jobs; starting a sustained goal or long task; changing mini-app data; installing an app update. Delegating does not lift it — a subagent runs under the same restriction. Two things stay open on purpose: finishing an *already active* goal (so a read-only turn is not trapped), and `ssh_exec`, because a remote machine is a different axis from this device.

The switch is remembered **per conversation, in memory only**. Reloading the WebUI starts you back in the personal chat with Writes on. The state is not held on the server: the flag rides along with each message you send, so what you saw on screen is what the turn actually got.

## Capture: the conversation is a source

This is the point of a project. In a project, with the switch on **Writes**, anything you say that will still be true next week gets written to the journal *before* Jenny answers you.

- **Yes**: a constraint, a decision, a preference, a name, a date.
- **No**: mood, courtesies, the thread of the discussion.

The gesture is one line appended to `raw/journal/<today>.md`, timestamped:

```text
# 2026-08-24

- 09:14 — the launch date moved to the second week of October
- 09:31 — prefers the quarterly plan over the annual one
```

The journal is append-only by construction — the tool that writes it can only append, to today's file, in the project you are in. Nothing rewrites a line once it is there.

Two things follow from this design that surprise people:

- **Jenny does not ask permission to write.** The switch already answered that question; asking again in words would reopen what you closed. If you do not want a turn to capture, flip the switch to Read-only — in read-only the capture instructions are not even part of the prompt, so Jenny does not attempt it and does not offer.
- **Capture is not authorship.** A journal line is not a page. Turning lines into pages, and keeping the map current, happens when you ask for it — or on its own, later, in a [gardener](./gardener.md) pass.

What arrives from outside — an article, a document, a page you pasted — goes verbatim into `raw/research/` first, and into a page second, with the page's `source:` pointing back at the raw copy.

## What Jenny actually sees: the map and the pages

Two things from the project folder are put in front of the model on **every** turn.

**The map** is `wiki/index.md`: what the project is, what is decided, what is open, and which pages exist. It is injected whole up to **2,000 characters**. Past that it is not head-truncated — that would deliver the prose and drop the index, which inverts the point — so instead the list of page links is kept in the order the map names them and whatever budget is left goes to the head of the file, cut at a line boundary. A notice reports the map's true size, and `(+N more)` if even the bare list did not fit.

This is why the map must stay short: it is paid for on every single message. When a section of it outgrows a few lines, that content belongs on its own page.

**The pages** come next, up to **6,000 characters** in total, in the order the map names them (pages the map never mentions go last, alphabetically). On real projects that is typically **one to four pages of twenty to fifty** — so the block states the count — *"Those are 2 of the project's 33 pages"* — and carries a notice naming how many were left out and reminding Jenny that `read_file` opens them.

Consequences worth knowing:

- **A page that is not there is not missing.** `read_file` opens it, and the map tells Jenny it exists. The order is the selection, and the order comes from your map — moving a page's link higher in `wiki/index.md` is how you make it arrive first.
- **No page ever enters half.** A page that does not fit is skipped whole, and the scan moves on to the next one.
- **A page over 6,000 characters never enters a turn at all**, in any conversation in that project. That is what a gardener split is for; you can also split it by hand.
- **Answers should cite.** Jenny is told to name the pages it leant on, as `[[page-name]]`. An answer that cites nothing is the visible sign that a project is not working yet.

Damaged files degrade rather than explode: a page that cannot be read is skipped and counted among the ones left out, and a `wiki/index.md` that is not valid UTF-8 is read with replacement characters rather than discarded (throwing it away would silently change *which* pages get selected). To find such files, ask Jenny to run a wiki lint pass — its first check names every non-UTF-8 page and the offending byte.

## History, and when it gets compacted

By default a project's conversation is **never** compacted for sitting idle. It can sit for three weeks and pick up exactly where it was — that is a project's job. The personal chat behaves differently; see [Memory](./memory.md).

The fence is about *time*, not *length*. A project conversation that grows long enough to pressure the model's context window is still consolidated the ordinary way: the oldest slice is summarised, the summary is carried forward, and those messages stop being replayed to the model. Nothing is removed from disk on that path, and none of it reaches the personal diary.

If a project's knowledge really does live in its pages, you can turn that fence off in **Settings → Wiki and projects**, under *Project history*. Read what it costs first: after that, an idle project's conversation is archived like the personal one, and Jenny then has in context what was *written* in the wiki, not what was *said*. The visible transcript is untouched, so you can still read back — the amnesia is the agent's, not the record's. The setting is read when the agent starts, so it takes effect from the next gateway start.

Two gates still protect a project even with compaction on, and both are checked every time:

1. The journal must be **fully promoted** — if there are journal lines no gardener pass has read yet, compaction is deferred, because those lines are knowledge that has not reached a page.
2. The project must have **at least one page**. A project with no pages is never compacted, whatever its journal says: the whole premise of compacting is that the knowledge is in the pages, and there it is nowhere.

When a project's history *is* compacted, the messages that leave the live session are replaced by a summary, and the visible transcript still holds the whole conversation — so nothing disappears from your screen. The one path that could have lost text is covered too: if the summarising call to the provider fails, the dropped messages are written verbatim to `<project>/raw/compacted/<YYYYMMDD-HHMMSS>.jsonl` *before* the session is trimmed, and if even that copy cannot be written, nothing is trimmed at all and the next idle window tries again. (On the personal chat that same failure lands in `memory/history.jsonl`; a project has no such destination by design, which is why it gets a file of its own.)

## If you rename a project's folder from outside Jenny

The folder name is the conversation's address, so renaming it — from the file browser, over ssh, from a computer — moves the address. Jenny records a stable id in each project's `AGENTS.md`, and uses it to chase the chat after the fact. On the next message you send to that project, one of these happens, and in **every** case Jenny does not read that message until the situation is resolved:

| What happened | What you see |
|---|---|
| Renamed to a valid name | Jenny says it moved the history to the new name, nothing was lost, and asks you to open the new name from the chip. |
| Renamed to a name that cannot be a conversation (spaces, accents) | Jenny says it found the folder but left the history under the old name rather than moving it somewhere nothing could open, names the character rule, and points out that renaming it back also works. Nothing is moved. |
| The folder is simply gone | Jenny says it could not find where it went, that nothing is lost, and that the chat comes back as soon as the folder does. |
| A previous move stopped halfway | Jenny says part of the history is under each name, that nothing was deleted, and that restarting it finishes the join on the way up. This is the one case that does **not** claim "nothing is lost" in the same breath. |
| A turn of that chat is still running | Jenny defers the chase and asks you to send the message again once the previous one has finished. |

Two folders swapping names is refused rather than guessed at, and a project whose `AGENTS.md` carries no id cannot be chased at all — the chat is simply left behind under the old name.

## See also

- [The gardener](./gardener.md) — the background pass that turns journal lines into pages, when it runs, and how to turn it off.
- [Wiki](./wiki.md) — the page list, the map, editing a page and reporting one, which work on projects too.
- [Memory and Dream](./memory.md) — the personal side of remembering, and the budgets.
- [Slash commands](./slash-commands.md) — `/gardener`, `/init` and the rest.
