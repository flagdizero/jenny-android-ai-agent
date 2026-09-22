# Wiki

A wiki — a **notebook**, in the app — is a browsable knowledge base that Jenny builds for you out of sources you feed it, not something you write by hand from scratch. You reach it through the conversation it belongs to: there is no separate tab for it.

## What it is

A wiki, here, is a set of cross-linked Markdown pages that Jenny compiles from raw material — articles, notes, PDFs, web pages — into concept and entity pages that reference each other with `[[wikilinks]]`. Jenny builds and maintains it using a built-in skill called `llm-wiki`, which you invoke conversationally rather than through any dedicated button:

- "Create a wiki about X" — scaffolds a new one.
- "Ingest this article/PDF/page into the X wiki" — adds a raw source.
- "Compile the notes I gave you into pages" — turns raw material into wiki pages.
- "What does the wiki say about Y?" — answers a question from what's already compiled.
- "Run a lint pass on the wiki" — checks for dead links, orphan pages, and coverage gaps.

There is no in-app "new wiki" form: everything starts as a chat request.

**The wiki does not update itself.** Ingesting a source doesn't automatically compile it into pages, and pages don't automatically get relinted after you edit them — each of those steps only happens when you (or a scheduled task you've set up) explicitly asks Jenny to do it.

The one thing that *does* happen on its own is the reverse direction: the personal chat's system prompt lists every wiki you have — name and one-line scope, read from disk on every turn — so Jenny knows a wiki exists and what it is about before she opens it. See [How Jenny knows which wikis you have](./memory.md#how-jenny-knows-which-wikis-you-have).

## Wikis and projects

Everything on this page describes a wiki as something you *ask* Jenny to build and maintain. There is a second way to work with one: open it as a **[project](./projects.md)** from the chip above the message box. A project is a wiki — the same folder, the same pages, the same graph — but the conversation is bound to it, its map and pages are put in front of Jenny on every turn, facts you mention are captured into a journal inside it, and a background pass (the [gardener](./gardener.md)) turns those journal lines into pages between conversations.

So the two views are not alternatives: a wiki you created by asking can be opened as a project tomorrow, and a project you created from the chip is immediately a notebook you can open from the chat.

## Multiple wikis

You can have more than one wiki side by side — for example, one about a research topic and a separate one for a hobby project. Each one lives under `workspace/wikis/<name>/`, entirely isolated from the others; Jenny works on one wiki root at a time and won't mix content across them unless you ask it to. A top-level `wikis/_index.md` file lists all of them.

Because everything is plain Markdown under the workspace, wiki pages are files like any other: you can open and edit them from the file manager as well as from the page itself (see [Tour of the WebUI](webui-tour.md)), and they're included in [backups and snapshots](backup.md) exactly like the rest of your workspace.

## Opening a notebook

A wiki is reached through the conversation that belongs to it, not from a tab of
its own. Pick a notebook from the chevron next to the title at the top of the
chat, and the header grows a pill with that notebook's page count. Tapping the
pill opens its pages.

There are two tabs over the same data — **Pages** and **Map** — and one search
box shared by both.

### Pages

A list, grouped by kind (Concepts, Entities, Other) and alphabetical inside each
group. The grouping only appears when it separates something: a notebook whose
pages are all of one kind gets a plain list instead of one heading over
everything.

Tapping a page opens it. Back returns to the list.

### Map

The same pages as nodes, links as edges, laid out by a force simulation. Drag to
pan, pinch to zoom, drag a node to move it — and a node you have moved **stays
where you put it** across openings, kept per notebook in
`workspace/.jenny/map-layout.json`. It is not frozen: it keeps following the
physics from the position you gave it, so the rest of the map still settles
around it.

Names are drawn for up to forty nodes, chosen by how connected they are —
because the one question a map answers better than a list is where the notebook
knots together. Below that cap every name is drawn.

### Searching

The search box searches **page contents**, not just titles: as you type, matching
pages stay and the rest fall away, and on the map the matches stay lit while the
others dim. The last word matches by prefix, so results narrow letter by letter.

- Accents are optional — typing `citta` finds "Città".
- Page titles, file paths, headings and frontmatter tags all count, and a hit in
  a title ranks above one buried in a paragraph.
- Very common words are ignored rather than treated as a failed search.

Search runs entirely on-device: the index is built alongside the map and rebuilt
only when a page actually changes, so typing never waits on anything.

### Reading a page

The page is rendered from its Markdown, with `[[wikilinks]]` clickable. A link
that leaves the notebook is not pretended to be openable — it says so instead.

Mermaid diagrams are the one place in Jenny's UI where they render as diagrams:
a fenced ` ```mermaid ` block in a wiki page is drawn. Chat replies do not render
Mermaid at all — see [Chat basics](chat.md). LaTeX renders here too, including
`$inline$` maths, which chat deliberately leaves alone (there a `$` is a price).

## Fixing a page yourself

The pencil in the header opens the page's Markdown source in a plain text box.
Save, and the page reloads from the server — what you see afterwards is the
server's rendering of what you wrote, not a guess.

**Jenny writes these pages too**, so the save carries the text you started from.
If the file changed underneath while you had the editor open, nothing is written
and you are told: reload and lose what you typed, or keep it and sort it out.
Leaving the editor with unsaved changes asks first.

## Telling Jenny something is wrong

Editing is for things you can fix. When a page is wrong on the substance — and
the fix means going back to the source, not rewording a line — select the passage
and use **Report**.

1. Select a stretch of text in a page. A **Report** bar appears at the bottom.
2. Tap it, and write what is wrong in your own words.
3. Confirm. Two things happen at once: an audit file is written next to the
   notebook's pages, anchored to the exact passage, and you land in that
   notebook's chat with the message already sent.

So a report is not filed and forgotten — it starts a conversation, and Jenny
answers there, where she has the files and the wiki skill. The audit file is the
durable half: it survives the conversation, the linter checks it, and it is what
lets her mark the thing as done afterwards.

If the selected text can't be found exactly once in the page's source — because
it spans formatting, or because that phrase appears twice — you are told, rather
than the comment being anchored to a guess.

Audits carry no priority. There used to be a four-level severity picker, and it
was removed: grading your own complaint is a triage step, and triage is
something a team does. Jenny works them oldest first.

There is no list of open reports in the app. Each one has a conversation you can
scroll back to, and processing them is Jenny's job, not a screen.

## Privacy notes

- The wiki only shows a small, fixed set of page metadata to the UI: title, type, entity type, tags, and created/updated dates. Anything else in a page's frontmatter — including source URLs and provenance notes Jenny records internally — stays server-side and out of the interface, even though it's present in the raw file — which the page's own editor shows you in full.
- Every ingested source keeps a `summaries/` folder of per-source digest pages. These are deliberately kept out of the page list, the map and search to avoid cluttering navigation, but they are **not** encrypted or otherwise access-controlled — the pages are still readable if you know (or Jenny gives you) a direct link, and they still live as plain files in your workspace.

## Turning it off

A config key, `wiki.enabled` (default `true`), can disable the wiki backend entirely; when it's off, wiki API calls fail. There is no toggle for this in Settings — it's config.json-only, see [Configuration reference](../reference/configuration.md).

With it off, the notebook list still shows what is on disk, and opening a
notebook's pages reports that the wiki is switched off rather than failing
silently.

## See also

- [Projects](projects.md) — opening a wiki as a conversation: capture, the map, and the write boundary.
- [The gardener](gardener.md) — the pass that maintains a project's pages on its own.
- [Tour of the WebUI](webui-tour.md) — overall navigation, dock, and tab layout.
- [Backup and restore](backup.md) — wiki content is part of your regular workspace backup.
- [Phone app launcher](app-launcher.md) — the app drawer, and the other corner of the Apps tab.
