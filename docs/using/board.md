# Notebook board

A [notebook](./projects.md) opens on its **board**: a few cards pinned to one screen, one card per subject the notebook keeps coming back to. Each card opens a **drawer**, a short summary of that subject written from the notebook's wiki pages, with links to the pages themselves. The wiki stays the source of truth; the board is the face of it, and the way in.

The board replaced the notebook's page list and its graph map. Both are gone from the home, and so is the file that remembered where you had dragged the map's nodes (`workspace/.jenny/map-layout.json`, removed at the next start). Every page is still there and still reachable, from a drawer or from search.

## Getting there

Open a notebook from the **Notebooks** page: the header row reads **← Notebooks › name**, with a **Chat | Board** switch at the right. **Board** opens the board, **Chat** goes back to the conversation. On a notebook you pinned as a page of its own, the pill at the left of the composer (the notebook's name and its page count) opens the board too.

Back from the board returns to the notebook's chat. Back from a drawer returns to the board, and back from a page returns to the drawer you opened it from — or to the board, if you opened it from search.

## What is on the board

<p align="center"><img src="../img/notebook-board.png" alt="A notebook's board: six polaroid cards, a sticky note for a dated reminder, two red threads, the envelope with one proposal, and the Since you last looked row" width="300"></p>

| Thing | What it is |
|---|---|
| **Cards** | Polaroids, one per subject — *Maple*, *Monstera* — with an icon and the subject's name. Tap one to open its drawer. |
| **Sticky notes** | Yellow notes for the open reminders of a card, with what and when. They sit next to their card; tapping one opens that card's drawer. |
| **Threads** | Thin red lines between two cards that a page ties together. See [Threads](#threads). |
| **The envelope** | Bottom left: Jenny's proposals for changing the board's shape, with a count. See [The envelope](#the-envelope-jennys-proposals). |
| **Since you last looked · N new** | A row at the top, shown only when something changed since you last opened the board, or proposals are waiting. See [Since you last looked](#since-you-last-looked). |

The board is one fixed frame, not a canvas that scrolls: it holds at most **12 items**, cards and sticky notes together. Seven to nine is the comfortable number. Positions are stored as percentages of the frame, so the same board fits a square screen and a tall one.

Two buttons in the header work on the board: the **lens** opens [search](#search), and the **list** icon swaps the board for a plain list of the same cards — name, next reminder, signal, and the envelope as its last row — for anyone who prefers a list, and for screen readers.

### One signal per card

A card shows at most one signal, so that when something lights up it means something. In order of priority:

| Signal | Meaning |
|---|---|
| Red corner — *problem* | A [mini-app section](#mini-app) in this card's drawer is in alarm, or its data has stopped arriving. |
| Yellow corner — *coming up* | The card's nearest dated reminder is due within **14 days**, or is overdue and still open. |
| Blue dot — *new* | The drawer was rewritten since you last opened it. |

No signal means nothing needs you.

### Gestures

- **Tap** a card or a sticky note to open the drawer.
- **Drag** a card or a sticky note to move it. Its pin turns red: a red pin marks something you placed, a grey one something placed by the code when it was created. Jenny never moves a card or a note — not the ones you placed, not the ones she placed. A card's position is chosen once, when it is created, in the free spot farthest from everything else.
- **Press and hold** a card (about half a second) to light its threads: they turn bold and show their labels, the cards at the other end lift, and the rest of the board dims. Hold it again to put them back. A card with no threads says so.

### Threads

A thread is always visible as a thin line with no words on it; its label — the reason two cards are tied, in a few words, such as *seeds taken from it* — appears when you press and hold either card. A drawer also lists its card's threads, and tapping one opens the card at the other end; the **×** beside it removes the thread, and Jenny will not propose that thread again. A board has at most **8** threads: twenty threads would be the graph the board replaced. Jenny adds threads only by proposing them.

## The drawer

<p align="center"><img src="../img/notebook-drawer.png" alt="The drawer of the card Acero: its brief, the next reminder as a sticky note, and a section with a correction waiting, with links to its two source pages" width="300"></p>

Tap a card and its drawer opens. From the top:

1. **The title**, the number of wiki pages the drawer covers and when it was last updated.
2. **The brief**: one or two sentences on the state of this subject now.
3. A warning when a mini-app section in this drawer is in alarm or stalled, saying that the data comes from the app, not from the wiki.
4. **Next up**: the card's nearest open reminder, with a **Done** button.
5. **The sections.** Every notebook has a *skeleton*, the list of sections every drawer has, in the same order on every card — for a plant notebook, *Pot and repotting, Water, Feeding, Light, History*. Each section is two short sentences, with chips that open the pages it was written from. A section the pages have nothing on is left out rather than filled with "no data". A blue dot beside a section's title means its text changed since you last opened the drawer.
6. **Your sections**, if you added any (see below), then **+ Add a section**.
7. **Threads**, if the card has any.
8. **The pages (N)**: a collapsed list of every page in this drawer.

A drawer that has not been written yet says so, and is written on the next gardener pass.

### Correcting a section

The drawer is written by Jenny from the wiki, so you do not edit it directly: you correct it, and the correction goes to the page it came from.

1. Tap **Correct** under a section. Its text becomes a text field, and a line under it names the page the correction will go to — the section's first source page.
2. Write what it should say, and tap **Send to Jenny**.
3. The correction is filed as a report on that page — the same kind of audit file as [Report](./wiki.md#telling-jenny-something-is-wrong) in the reader, marked as coming from the board. Until it is dealt with, the section shows your text in italics with a paperclip: *your correction, I'm working it in*.
4. On the next [board pass](./gardener.md#the-board-pass) the gardener applies the correction to the wiki page, closes the report, and rewrites the drawer from the corrected page. The section then shows **✓ Applied to the wiki** until you next open the drawer.

Unlike Report, a correction does not take you to the chat: it is handled between conversations, by the gardener.

### Adding a section

**+ Add a section** offers six kinds: note, reminder, micro app, cron, photo and other. They are yours: Jenny does not add, change or remove them on her own, and each one can be moved up or removed (removing a section never changes the wiki). A micro app or a cron is the one exception to "on her own": Jenny puts it in the drawer **because you asked for it**, and it is marked as made on request.

The rule behind micro apps and crons: **everything a notebook does by itself lives in the drawer of its card, where you can see it.** A micro app reads a value for free; a cron is a job Jenny does on a schedule. Both carry the same signature — how often, the last run, the next one, and whether it uses the model.

#### Note

Something to remember, in your words — *it stays on the south windowsill only in winter*. The note stays on the card as you wrote it, and it is also written to the notebook's journal as a line marked `[said]`, so the gardener promotes it into the wiki like anything you say in the chat. The note shows **waiting** until a gardener pass has read that line, then **✓ in the wiki**. Editing a note writes a new journal line; the old one stays, because the journal is append-only.

#### Reminder

Something to do, with a date: **What?** and **When?**, and a **Month only** box for something like "around March". The reminder becomes a sticky note on the board and the card's **Next up** when it is the nearest. **Done** closes it — it is not deleted, it is marked done and a `[said]` journal line records that it was done. Removing the section drops the reminder. See [Reminders and the notebook clock](#reminders-and-the-notebook-clock) for when you are told.

#### Micro app

A small Python program attached to the drawer that reads **one thing** — a sensor, a price, a count — and keeps it fresh. It is not a [mini-app](./mini-apps.md): no screen, no grid, no manifest. It lives in the notebook, and you look at it rather than use it.

**Micro app** asks *What do you want to see?*; your sentence goes to the notebook's chat, and Jenny writes the program there. She **runs it once before saving it**: the micro app appears in the drawer **on trial**, showing the first value actually read and where it came from — *Acerello: 38 % · online, read from the WaterBot hub at 14:02* — together with what it will reach (the skill it uses, the hosts it connects to). **Keep it** makes it live; **Discard** throws it away. Nothing runs on its own until you tap Keep.

Once kept, the notebook clock reads it every *N* minutes (at least 15, an hour by default) with **no model turn**, so a reading costs nothing. The section shows the value big, up to two more values as chips, a seven-day trend, and the signature *every 30 min · from waterbot-imh*; **⟳** reads it now, **Remind me when…** sets a reminder on its value, **Change** asks Jenny for a change in the chat, **Pause** stops it, **Remove** takes it away (its files go to `board/micro/.trash/`).

When something goes wrong the last good value stays, greyed, with its age, and the section says who fixes it:

| What happened | What you see |
|---|---|
| the device or the network did not answer | a note under the value; it tries again by itself, and this never counts as broken |
| the program failed three times in a row | **It broke**, the card's red corner, and **Ask Jenny to fix it**, which opens the chat with the error already in the sentence |
| a reading never stopped | it is put on hold until it is fixed |
| the program was changed outside Jenny | it does not run until you **Approve again** |

A fix by Jenny that shows the same things (same values, same units, same skill and hosts) runs at once, and a *since you last looked* row says so; a change to what you see goes back on trial.

#### Cron

A job Jenny does by herself on a schedule, attached to this card — *every morning look at the weather and tell me whether to water*, *every Monday look for new listings*. **Cron** opens the chat with *In the … drawer, every …* for you to finish; you can also just ask in the notebook's chat, and Jenny asks which card when it is not clear.

The section shows what the job does, how often, its next run, its last result — *Last result (Monday 09:02): 3 new listings* — and **uses the model**, because every run is a real turn. A run that has something to say also delivers it in the notebook's chat, with the usual notification; a run with nothing to say only updates the section. **Pause**, **Change** and **Remove** are here; removing the section removes the job. Notebook crons are also listed in the workshop with their place, *piante › acero*. See [Notebook crons](./scheduling.md#notebook-crons).

#### Mini app

**Or link one of your apps…**, at the bottom of the micro app sheet, attaches live data from one of your [mini-apps](./mini-apps.md), for an app that declares a drawer view (a `section` block in its `app.json`). The list offers only those apps; when the app asks for parameters — which sensor, which plant — you are asked for them, so the same app can sit in two drawers showing two different things. **Make a new one…** takes you to the chat with a sentence ready to ask Jenny for one.

The section shows the app's latest value with its label and unit, its state, a signature — when the value was recorded and which app it comes from — a small trend line when the app declares one (one point per day, up to 31 days), up to two of the app's actions as buttons, and **Open the app**. States:

| State | What the section says |
|---|---|
| ok | updated *when* |
| alarm | in alarm · *when* — the value crossed the threshold the app declares |
| stale | stalled since *when* — no new data for longer than the app allows |
| no data | no data yet |
| gone, broken, no view | the app was deleted, its manifest is broken, or it no longer declares a drawer view |

Alarm and stale light the card's red corner. The section only **reads** what the app left in its data — it never calls the app; keeping the data fresh is the app's own job. When a section changes state (goes into alarm, stalls, comes back to normal), Jenny writes **one** journal line marked `[inferred]` and one *since you last looked* row; the readings themselves never go into the wiki.

#### Photo

A picture — how the plant looked when it arrived, say. It is resized on the phone before it is sent (longest side 1,600 pixels, JPEG) and stored in the notebook's `raw/photos/`.

#### Other

Opens the notebook's chat with a sentence ready — *In the Maple drawer I'd like a section that…* — for you to finish and send.

## Pages, from a drawer

Tapping a page chip opens the page in the reader, with **Edit** and **Report** as described in [Wiki](./wiki.md#reading-a-page). Links inside a page are shown through the board, in three kinds:

| Link | Looks like | Opens |
|---|---|---|
| A page in the **same drawer** | an ordinary link | that page, in place of the current one |
| A page in **another drawer** | a link with a chip naming the card, *▸ Monstera* | that card's drawer |
| A page **not on the board** | a dotted link | a sheet with the first ~170 characters of the page, **Close**, and **I want it on the board** |

The pages themselves do not change: the `[[links]]` stay as they are written, and only the reader shows them this way.

**I want it on the board** records the request. On the next board pass the gardener reads it and either proposes attaching the page to a drawer — usually the one you opened it from — or leaves it off the board. Either way the request is used once.

## Search

The lens is the one way to everything in the notebook, including pages that are in no drawer. It searches card names and every page, with the same rules the page search always had: page contents, not just titles; accents optional; the last word matching by prefix; titles ranking above a word buried in a paragraph. Each result says where it lives: **card** for a card, the card's name for a page in a drawer, **not on the board** for a page that is in none. A card opens its drawer, a page in a drawer opens in the reader (and back returns to that drawer), and a page off the board opens the preview sheet.

## The envelope: Jenny's proposals

The board's shape — which cards, where, which threads — is yours. Jenny does not change it; she proposes, and the proposal waits in the envelope until you answer. Tap the envelope to read them:

| Proposal | When the gardener may make it |
|---|---|
| **Add a card** | A subject off the board has at least **3** pages of its own. Not when the board is full: then it proposes putting a card away first. |
| **Put a card away** | A card whose drawer and pages have not changed for **90 days**, with no open reminders. Its pages stay, and search finds them. |
| **Add a thread** | A page clearly ties two cards that are not tied yet. |
| **Attach a page** | A page off the board belongs in a drawer — typically one you asked for. |
| **Add a mini-app section** | An installed app with a drawer view could show live data in a drawer. |

Each proposal is a sentence or two in your language, with **Later**, **No** and **Yes** (for putting a card away: **Leave it** and **Put it away**). **Yes** applies it; a new card goes into the free spot farthest from everything else. **No** is remembered: the same proposal is not made again — except a new card, which can come back only once the subject has at least twice the pages it had when you said no. **Later** keeps it out of the envelope for the next **3** board passes.

The gardener adds at most **2** new proposals per pass, and the envelope holds at most **4** waiting. Proposing nothing is a normal outcome.

### A notebook's first board

A notebook that has pages but no board gets its first board as a single proposal: the skeleton of the drawers (unless you already chose one when you created the notebook) and up to **9** cards, each with its pages — with every drawer already written, so the board is complete the moment you accept. Dated plans it finds in the pages (an unchecked `[ ]` with a date) become reminders on the right card. While the proposal waits, the empty board says *I've prepared a board for you: it's in the envelope.* If you decline it, it is not proposed again on its own.

A page that fits no card stays off the board, which is fine: it remains searchable.

### A new notebook

When you create a notebook in the home, two questions follow the name and *what is it about*, and both can be skipped:

1. **What do you want to keep track of, for each thing?** The skeleton, separated by commas — *Pot and repotting, Water, Feeding, Light, History*. Left empty, Jenny proposes one once the notebook has a few pages.
2. **What do you want me to remind you of?** — *repotting and feeding*. The gardener looks for those first when it takes reminders from the journal.

The notebook then opens on its empty board. *The board is empty* comes with an example sentence; tapping it opens the chat with the sentence ready to finish. The board fills as you talk and as the gardener works.

## Since you last looked

The row at the top counts what happened since you last opened the board: cards added or put away, threads added, pages put into drawers, drawers rewritten, reminders taken from the journal, corrections applied, a reminder that came due, a mini-app section that changed state — plus one line when proposals are waiting in the envelope. Tap it for the list.

Every line is written by the code, from what was actually done, not by the model. Opening the board marks them as seen.

## Reminders and the notebook clock

A notebook's reminders live in its own `board/reminders.json`, not in the [reminder list](./scheduling.md) of the personal chat. They come from two places: the ones you add in a drawer, and the ones the gardener takes from new journal lines — a line that decides or plans something with a date (*repot it around March*) becomes a reminder on the card it is about, pointing at the page that explains why.

A system job, **`reminders`** — the notebook clock — runs **every 15 minutes** over every notebook. It first runs the micro apps that are due, then reads every notebook's reminders:

- A **dated** reminder is announced from **09:00** (your time zone) on its day, or on the first of the month when only the month is known. If the app was not running at nine, it is announced at the next round.
- The announcement is written by Jenny in a silent turn whose only way to reach you is a message, and it has to say **why**: what is due, the reason taken from the page the reminder points to, and a short question about when you will do it. It arrives in the personal chat like her other proactive messages (see [Android notifications](./scheduling.md#android-notifications)), and a *since you last looked* row is added on the board.
- Each reminder is announced **once**. It stays on the board until you tap **Done** or remove its section.

The reminder format also allows a trigger on a micro app's or a mini-app's data instead of a date — *when moisture is below 20* — which the clock checks against the latest value recorded, and only if that value is fresh: stale data triggers nothing. It is announced once when the condition becomes true, re-armed only after it has been false at **two checks in a row** (a sensor hovering on the threshold does not fire at every round), and never announced twice within **12 hours**. You set one from the section in a drawer: **Remind me when…** asks what to do and whether the value should drop below or rise above a threshold.

The same round is the one that notices when a micro app or mini-app section changes state. The job is registered when the wiki is enabled (`wiki.enabled`), is listed among the protected [system jobs](./scheduling.md#protected-system-jobs), and costs nothing in a round with nothing due: a micro app's reading never involves the model.

## What Jenny sees in a notebook with a board

In the notebook's conversation, the board takes the place of the map (`wiki/index.md`) in the block Jenny gets on every turn: one line per card, with its brief, what runs by itself in its drawer (*auto: Acerello 38 % · cron Weather*), its nearest reminder, and a problem flag when a micro app or mini app in its drawer is in alarm, stalled or broken, within **2,000 characters**, plus how many pages are off the board. When your message names a card, that card's whole drawer is added to that turn — up to two drawers per message. See [What Jenny actually sees](./projects.md#what-jenny-actually-sees-the-board-the-map-and-the-pages).

## What the gardener does to the board

After its pass over the wiki, the [gardener](./gardener.md) makes a second pass over the board, in the same run: it applies your corrections to the wiki, puts new pages into drawers, rewrites only the drawers whose pages changed, takes dated reminders from new journal lines, and makes proposals. See [The board pass](./gardener.md#the-board-pass).

What it cannot do is enforced by the code, not left to the prompt: it has no tool to move, add or remove a card, to touch your sections, threads or skeleton, or to take a page out of a drawer, and any write of Jenny's that would change something of yours is refused. Research wikis — the ones with `concepts/` or `entities/` folders and no journal — get no board; a notebook you keep a journal in gets one whatever its folders look like.

## Where it is stored

Everything lives inside the notebook's folder, so it is part of your [backups and snapshots](./backup.md):

```text
workspace/wikis/<notebook>/
  board/
    board.json         the board: skeleton, cards and their positions, your sections,
                       threads, the envelope, the "no" and "later" answers, since rows
    reminders.json     the reminders, open and closed
    cards/<id>.json    each drawer's written summary — derived from the wiki, regenerable
  raw/photos/          photos added to drawers
```

The files under `board/cards/` are summaries of the wiki, and the next board pass rewrites any that are missing or out of date. `board.json` and `reminders.json` are yours: losing them loses your layout, sections and reminders.

## See also

- [Notebooks](./projects.md) — what a notebook is, capture, and what Jenny sees on every turn.
- [The gardener](./gardener.md) — the background pass that keeps the wiki and the board current.
- [Wiki](./wiki.md) — reading, editing and reporting a page.
- [Mini-apps](./mini-apps.md) — the apps whose data can sit in a drawer.
- [Scheduling and proactivity](./scheduling.md) — the system jobs, the notebook clock among them.
