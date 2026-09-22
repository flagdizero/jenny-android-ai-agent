---
name: llm-wiki
description: >-
  Build and maintain a Karpathy-style LLM knowledge base — a self-compiling
  Markdown wiki where an Agent ingests raw sources, compiles
  cross-linked concept/entity/summary pages, answers queries against the
  corpus, lints the graph for health, and audits in-context human feedback
  filed into the audit/ directory. Use when (1) scaffolding a
  new knowledge base for any research topic, (2) ingesting
  articles/papers/PDFs/web pages into raw/, (3) compiling or restructuring
  wiki articles from existing raw material, (4) answering questions
  against the wiki and filing durable answers back, (5) running lint
  passes for dead links / orphan pages / coverage gaps / audit shape,
  (6) processing human feedback from the audit/ directory and applying
  corrections. Not for general note-taking or daily journals.
locked: true
user_summary:
  it: "La wiki è il tuo secondo cervello: usala per fare ingest di articoli, note e ricerche. Jenny compila e collega le pagine per te, e puoi farle domande sui contenuti raccolti."
  en: "The wiki is your second brain: use it to ingest articles, notes and research. Jenny compiles and links the pages for you, and you can ask her questions about what's inside."
---

# LLM Wiki — Karpathy Knowledge Base Pattern

> **Experimental skill — iterating.**
> Original skill by Lewis Liu (lylewis@outlook.com). Multi-wiki version extended and maintained by Ludovico Ragno (flagdizero). · Inspired by [Karpathy's llm-wiki Gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)

## Core idea

Instead of RAG (re-retrieving raw docs on every query), the LLM **compiles** raw sources into a persistent, cross-linked wiki. Every ingest, query, lint, and audit pass makes the wiki richer. Knowledge compounds — and the human stays in the loop via a structured feedback channel instead of ad-hoc corrections that get lost.

- **You** own: sourcing raw material, asking good questions, steering direction, filing feedback on anything the AI got wrong.
- **LLM** owns: all writing, cross-referencing, filing, bookkeeping, and acting on your feedback.

The wiki is a living artifact with **five operations** — `compile`, `ingest`, `query`, `lint`, `audit`. See "Session start & wiki selection" below for what to read before doing anything.

## What this skill covers, and what it does not

**This is the manual of the research pattern**: a wiki that digests *documents* — papers, articles, fetched pages — into compiled, cross-linked pages. The layout below, the `concepts`/`entities`/`summaries` split and the five operations all belong to that pattern, and the wikis built on it are the reason this skill exists.

**It is not the authority on how a project is laid out.** Projects created from the app have their own shape — flat pages under `wiki/`, a map at `wiki/index.md`, a working journal under `raw/journal/` — described in the project's system prompt and visible in the folder itself. Two shapes exist on disk; nothing declares which is which, so **read the structure you are in** and treat that project's `AGENTS.md` as the local authority. Where this skill's layout and the folder in front of you disagree, the folder wins.

What still applies everywhere: raw material lands verbatim before it becomes a page; a page cites where it came from; `lint` and `audit` are how quality and human corrections stay visible. And **the conversation itself is raw material** — a stable fact the user tells you is captured in the journal, which is that project's `raw/` for spoken material, not a shortcut around ingest.

## Workspace layout (canonical)

This is the research pattern's layout (v. the section above: a project created from the app has its own). A workspace holds many **isolated** wikis under `wikis/`, bridged by a single top-level `wikis/_index.md`. Even a single wiki lives at `wikis/<name>/` — there is no standalone-wiki mode, and the registry is common to both shapes.

```
workspace/
├── wikis/
│   ├── _index.md          ← Workspace registry — the ONLY bridge across wikis
│   ├── erbario/           ← A wiki root (wikis/<name>/)
│   ├── loops/
│   └── nanobot/
└── skills/llm-wiki/       ← This skill (a checkout of the llm-wiki repo)
```

Each wiki root is `wikis/<name>/`. **The skill operates on one wiki root at a time** (see wiki selection below).

### Running the scripts

There is no shell on this platform: `python_exec` is the only execution tool. The scripts live at `skills/llm-wiki/scripts/`, so every invocation in this doc has the same shape — `working_dir` on the scripts directory (which puts it at the head of `sys.path`), then a plain import and a function call:

```
python_exec(
    working_dir="<workspace>/skills/llm-wiki/scripts",
    code="import lint_wiki; lint_wiki.lint('<workspace>/wikis/<name>')",
)
```

**Wiki paths passed to a script must be absolute** (`<workspace>/wikis/<name>`): the scripts walk the tree with `pathlib`, which measures a relative path from the process directory, not from the workspace. Never drop `working_dir` — without it the import fails.

### `wikis/_index.md` — the workspace registry

`_index.md` is the only place a link may cross wikis. It has a free-form header (your curated notes about how the wikis relate) plus an **auto-generated registry block** delimited by markers:

```markdown
# Workspace Index

> Curated notes about how these wikis relate go here.

<!-- BEGIN wiki-registry (auto-generated by reindex_wikis.py — do not edit) -->
- [[erbario/wiki/index|erbario]] — one-line scope from erbario/AGENTS.md
- [[loops/wiki/index|loops]] — one-line scope from loops/AGENTS.md
<!-- END wiki-registry -->
```

Each scope line comes from the wiki's `AGENTS.md` — its `summary:` frontmatter field if present, otherwise the first bullet under `## Scope`. Only the text between the markers is machine-managed; everything else is yours. Regenerate it with:

```
python_exec(
    working_dir="<workspace>/skills/llm-wiki/scripts",
    code="import reindex_wikis; print(reindex_wikis.regenerate_index('<workspace>/wikis'))",
)
```

(`reindex_wikis.check_index('<workspace>/wikis')` returns the list of drift problems without writing.)

`scaffold.py` calls this automatically when it creates a wiki, so a new wiki is registered immediately. `lint --workspace` verifies the block is in sync with the wikis on disk. Rename or delete a wiki → rerun `regenerate_index`.

### Session start & wiki selection

At the start of every session, in order:

1. Read `wikis/_index.md` to see which wikis exist and what each covers.
2. Select the target wiki for this task (`wikis/<name>/`). If the request doesn't make the target obvious, ask the user which wiki.
3. Read that wiki's schema file `wikis/<name>/AGENTS.md` and its `wikis/<name>/wiki/index.md`.

Then perform operations on that one wiki root. Do not touch other wikis in the same operation.

### Lint the whole workspace
```
python_exec(
    working_dir="<workspace>/skills/llm-wiki/scripts",
    code="import lint_wiki; lint_wiki.lint_workspace('<workspace>/wikis')",
)
```
This lints every wiki and checks that `wikis/_index.md` is in sync. To lint a single wiki, call `lint_wiki.lint('<workspace>/wikis/<name>')` instead. Pass `fix=True` to `lint_workspace` to repair registry drift.

### Scaffold a new wiki
```
python_exec(
    working_dir="<workspace>/skills/llm-wiki/scripts",
    code="import scaffold; scaffold.scaffold('<workspace>/wikis/<new-name>', '<Topic Title>')",
)
```

## Directory layout

`<wiki-root>` is always `wikis/<name>/`. Inside it:

```
<wiki-root>/                 (= wikis/<name>/)
├── AGENTS.md          ← This wiki's instructions: scope, exclusions, its own notes
├── log/               ← Per-day operation log (one file per day)
│   ├── 20260409.md
│   └── 20260410.md
├── audit/             ← Human feedback inbox (one file per comment)
│   ├── 20260409-143022-claude-code-size.md
│   └── resolved/      ← Processed feedback, archived with resolution notes
├── raw/               ← Immutable source documents (LLM reads, never writes)
│   ├── journal/       ← The conversation itself, one file per day, append-only
│   ├── articles/
│   ├── papers/
│   ├── notes/
│   └── refs/          ← Pointer files for large binaries kept outside raw/
├── wiki/              ← LLM-generated knowledge (LLM writes, you read)
│   ├── index.md       ← Master catalog — every page, structured by category
│   ├── concepts/      ← Concept/topic pages (split into subfolders when >1200 words)
│   ├── entities/      ← People, tools, papers, organizations
│   └── summaries/     ← Per-source summary pages
└── outputs/
    └── queries/       ← Query answers (promote durable ones to wiki/)
```

`raw/journal/` is in **every** wiki, whatever its shape: it is where the conversation itself is captured, one file per day, append-only. `scaffold.py` creates it in both layouts, and the lint checks it in both — the folders that a project and a research library have in common are defined once, in the app's project scaffolder (`jenny/webui/project_scaffold.py::PROJECT_DIRS`), and the skill's script carries a pinned copy.

`AGENTS.md` is the wiki's **instructions file** — what this wiki covers, what it excludes, and anything true of it and no other wiki. Read `references/schema-guide.md` for what to put in it. Read it at the start of every session.

The folder layout and the five operations are **not** written there — the agent already carries them, so a copy in each wiki would only go stale.

## Core principles

Five rules govern everything below. If a future instruction contradicts one, flag it to the user before acting.

### 1. Divide and conquer

A single concept page should **never** try to cover a complex topic end-to-end. Target: **400–1200 words per page**. When a topic would blow past that:

- Create a subfolder: `wiki/concepts/<topic>/`
- Put a short index page at `wiki/concepts/<topic>/index.md` — definition, list of sub-pages, one-line summaries
- Put each aspect in its own file: `wiki/concepts/<topic>/<aspect>.md`
- In `wiki/index.md`, show the hierarchy via indented bullets

Example layout (from a real wiki):
```
wiki/tech/claude-code/
├── index.md                         (overview + links to sub-pages)
├── Claude_Code_Architecture.md
├── Claude_Code_Agent_Framework.md
├── Claude_Code_Bridge_System.md
├── Claude_Code_Query_Engine.md
├── Claude_Code_Skills_Plugins.md
├── Claude_Code_State_Management.md
└── Claude_Code_Tool_System.md
```

One fat file covering all seven aspects would be unreadable and unlinkable. Seven focused files + an index page give you navigation, selective reading, clean backlinks, and small audit targets.

### 2. Mermaid for diagrams, KaTeX for formulas

- **Any flow, sequence, hierarchy, or state diagram** must be written in mermaid — never ASCII art. ASCII boxes rot fast and are impossible to annotate.
  ````
  ```mermaid
  flowchart LR
      A[raw/article.md] --> B[summary]
      B --> C[concept page]
      C --> D[index.md]
  ```
  ````
- **Any formula** must be written in KaTeX: inline `$f(x) = \sum_i w_i x_i$` or block `$$...$$`.

Both render in Obsidian with default settings and in most Markdown viewers.

### 3. Raw file policy

Small text-based sources (md, txt, small pdfs, small images) → copy into `raw/<subfolder>/`.

Large binaries (videos, model weights, installers, datasets, large PDFs >10 MB) → **do not copy**. Instead:

- Create a pointer file at `raw/refs/<slug>.md` with:
  ```yaml
  ---
  kind: ref
  external_path: /Volumes/external/models/llama-3-70b/
  size: ~140 GB
  ---
  ```
  followed by a short description of what it is and why it matters to this wiki.
- Wiki pages cite it like any other source — but as a **path**, `raw/refs/<slug>.md`, not as `[[raw/refs/<slug>]]`. A wikilink only reaches pages under `wiki/`, so `[[raw/…]]` renders as a dead link in the app; `lint` reports it. The same holds for `log/`, `audit/` and anything in another project: a `[[link]]` that leaves `wiki/` is dead.

This keeps the wiki repo git-friendly and portable.

### 4. Audit is the human feedback surface

The wiki is AI-written; it will be wrong sometimes. The raw sources are human-written; they will contradict each other. The `audit/` directory is how humans correct both without losing the corrections in chat history.

- Humans (or the agent) file feedback by adding one file to `audit/` with YAML frontmatter (anchor, target) and a markdown body.
- The AI **must** periodically run the `audit` op — never silently ignore `audit/*.md` files.
- When feedback is applied, the file moves to `audit/resolved/` with a `# Resolution` section appended and a log entry recorded in `log/YYYYMMDD.md`.

See `references/audit-guide.md` for the full file format and processing workflow.

### 5. Wikis are isolated

Each wiki under `wikis/` is a closed namespace. **Never write a wikilink that points into another wiki** — not `[[../other/wiki/...]]`, not a bare `[[Page]]` that only exists elsewhere. Every `[[Target]]` must resolve inside its own wiki.

- The **only** bridge across wikis is `wikis/_index.md` (the registry block links each wiki's `wiki/index.md`).
- If a page needs to reference material in another wiki, mention it in prose and point at `wikis/_index.md`, not a wikilink.
- `lint` enforces this: a cross-wiki link resolves to nothing and is reported as a **dead wikilink**. That is intended, not a false positive.

---

## The five operations

Every action on the wiki is one of these five. Each appends an entry to the current day's log file (`log/YYYYMMDD.md`).

**Recognizing which operation you're in.** Map every action to one of the five — including ones that don't feel like a wiki edit. In particular, **pulling material from the web is not a separate "research" mode: a web search or page fetch you use is an `ingest`.** Whether a source arrives as a file, a paste, or your own live fetch, bringing it into the wiki means running the `ingest` recipe below — raw/ first. Do not improvise a shortcut path around it.

### 1. `compile`

(Re)structure wiki content from existing `raw/` material — including splitting oversized pages, merging near-duplicates, and rebuilding `index.md`.

**When to run**: after a big ingest batch, when an existing page has outgrown 1200 words, when `index.md` no longer reflects reality, or when the user says "clean up the wiki".

**Steps**:
1. Read `AGENTS.md`, `wiki/index.md`, and every file in the target subtree.
2. For each page over ~1200 words: plan a split into `concepts/<topic>/` with an index + sub-pages. Confirm the plan with the user before writing.
3. For each pair of near-duplicate pages: propose a merge. Confirm, then rewrite.
4. Regenerate `wiki/index.md` so every page is listed exactly once.
5. Log: `## [HH:MM] compile | <what you did — files touched, splits, merges>`

**Definition of done** — do NOT report the compile as complete until:
- [ ] Every page appears exactly once in `wiki/index.md`.
- [ ] No diagram left in ASCII — flows/hierarchies/state are mermaid (core principle 2).
- [ ] A log line for this compile exists in `log/YYYYMMDD.md`.
- [ ] **You actually ran `lint_wiki.lint('<workspace>/wikis/<name>')` and it exits clean** (or every issue is fixed / explicitly deferred to the user). This is a hard gate: run the script, read its real output, fix, re-run — then **paste the literal lint output into your reply**. Verification is by running the tool, not by remembering the rules; a summary of "looks satisfied" is not acceptable.

#### `compile` in a **project** wiki (notebook layout)

The steps above assume the research layout — `concepts/`, `entities/`, `summaries/`, pages measured in words. A **project** wiki (see `agent/project.md`) is flat pages under `wiki/`, each with `state:` and `source:`, fed by an append-only `raw/journal/`. Same operation, and it is the one the user asks for with *"tidy up the wiki"* or *"split the concepts"* — but five things differ, and every one of them has been got wrong in the field.

1. **The trigger is characters, not words, and the number is 6000.** That is the total budget for *all* pages injected into a turn (`_PROJECT_PAGES_MAX_CHARS`). Pages are offered in **the order the map names them** and any that does not fit is skipped whole — not shortened, not summarised — in every conversation in that project. So a single page over 6000 can never be read by anyone, and a merely *large* page silently starves the pages the map lists after it. Split by the things the page talks about; each part becomes a page named after its own thing.

2. **Move text verbatim. Do not merge and do not re-word.** This is the hard inversion of step 3 above: in a research wiki you may propose a merge and rewrite; here you may not. The sentences came from the user's own words through an append-only journal, and a re-wording detaches the page from the `source:` that justifies it. Nothing is deleted; text that moves into a page the original links to is still there and still reachable. Rewriting is how a wiki decays one careful pass at a time.

3. **`source:` is a single value, and its shape is load-bearing.** Either a journal line — `raw/journal/<day>.md#HH:MM`, with `#HH:MM.2` for the second line of a mixed minute — or a document copied into `raw/`. **Never a YAML list.** Measured on 26/08: a pass turned one `source:` into a two-item list, and the two readers that parse it disagreed — one returned `- raw/journal/...` with the dash attached, the other returned the second item — so the page's provenance became unreadable and nothing said so. When a page now rests on two sources, keep the one that carries its `state:` and name the other in the body.

4. **`state:` never goes up during a compile.** Restructuring moves text; it does not certify. Only the user's own words justify anything above `open`, and a page whose `source:` is a document or an `[inferred]` line is capped at `open` for good — which is the right value, not a defect to fix.

5. **A contradiction between pages goes into the map's open section** — one line, naming both — unless the user is present and settles it, which during a compile they usually are. If they do, say so in the log line: that is the difference between a decision and a pass that decided on its own.

And the map's **page list comes out of a compile whole**: every page it named before is still named after. The list is how a future conversation learns which pages exist, so an entry dropped from it is a page that has stopped existing.

**Definition of done**, replacing the checklist above:
- [ ] Every page appears exactly once in `wiki/index.md`, and no entry was lost.
- [ ] No page is over 6000 characters, and the map is inside its own ceiling (2000).
- [ ] Every page written or moved carries `state:` and a single-valued `source:`.
- [ ] A log line exists: `## [HH:MM] compile | <what moved where>` (`split` is also a valid op when the pass was only a split).
- [ ] **You actually ran `lint_wiki.lint('<workspace>/wikis/<name>')` and it exits clean**, and pasted its literal output into your reply. Same hard gate as everywhere else in this skill.

### 2. `ingest`

Add a new source. **One source typically touches 5–15 wiki pages.**

**Steps**:
1. Save the source **verbatim** to the right subfolder — this happens FIRST, before any wiki page is written. A source you fetched from the web is a source like any other:
   - web page / search result → `raw/articles/<slug>.md` (save the fetched text, **not your summary of it**)
   - paper → `raw/papers/<slug>.md` (extracted text for big PDFs)
   - note → `raw/notes/<slug>.md`
   - large binary → `raw/refs/<slug>.md` pointer file (see raw file policy)

   **Verbatim means byte-for-byte the tool's output, not your rewrite of it.** What you write into `raw/` must be the `text` field the fetch/read tool actually returned — copied whole, including the `[External content …]` wrapper if present. Do not condense, clean up, re-order, or re-phrase it on the way in; that is a summary, and summaries belong in `wiki/summaries/`, not `raw/`.
   > ❌ Real failure to avoid: `web_fetch` returned ~40 000 characters of scraped Wikipedia; the agent wrote a tidy ~3 000-character hand-written recap into `raw/articles/citrus.md` with a plausible `source:`/`retrieved:` frontmatter. That file *looks* like a source but is already a paraphrase — every downstream claim is now traceable only to the agent's memory, and the audit trail is poisoned at step 1. If the verbatim text is large, save all of it anyway (or use a `raw/refs/` pointer for true binaries); never shrink it by rewriting.
2. Read the source in full **from the saved file in `raw/`**. Write every wiki page from that raw file, never from a fetch you are only holding in context. Round-tripping through `raw/` is what keeps the source immutable, citable, and auditable — and is your main defense against corrupting a fact between reading it and writing it down (source says X → page must not say not-X).
3. Create `wiki/summaries/<slug>.md` (200–400 words — key takeaways, not a rewrite; see `references/article-guide.md`).
4. Create or update relevant concept pages in `wiki/concepts/`. **Before creating a page, check `wiki/index.md` (and grep existing titles) for an existing page on the concept — update it instead of making a near-duplicate under a slightly different name.** Set each page's `sources:` frontmatter to the `raw/` slugs it draws from. Respect divide-and-conquer: if a concept page would exceed 1200 words, split instead of cramming.
5. Create or update entity pages in `wiki/entities/` for any new people / tools / papers / organizations referenced.
6. Update `wiki/index.md` so the new pages appear under the right category.
7. Log: `## [HH:MM] ingest | <slug> — <one-line description> (touched N pages)`

**Definition of done** — do NOT report the ingest as complete until every box holds. A source is not "ingested" just because concept pages exist:
- [ ] A `wiki/summaries/<slug>.md` exists for this source (step 3 — not optional).
- [ ] Every entity the source introduces (people, tools, papers, orgs) has a page in `wiki/entities/` (step 5).
- [ ] Every concept and entity page written or touched has a non-empty `sources:` frontmatter pointing at the `raw/` slug it draws from — this is a precondition of writing a page, not a later cleanup. It is also the only thing that makes a claim traceable back to its source for later audit. (Summary pages are exempt: they *are* the source's representation and use `source_url`/`source_type` instead — see `references/article-guide.md`.)
- [ ] The new pages are cross-linked to at least one existing concept, not just listed in `index.md`.
- [ ] No diagram written in ASCII — any flow/hierarchy/state is mermaid (core principle 2).
- [ ] A log line for this ingest exists in `log/YYYYMMDD.md`.
- [ ] **You actually ran `lint_wiki.lint('<workspace>/wikis/<name>')` and it exits clean.** This is a hard gate, not a self-assessment: run the script (see below), read its real output, fix every issue (or explicitly defer it to the user with a reason), and re-run until clean. The lint catches exactly the mistakes this checklist is about — missing summaries, orphan/dead links, pages whose `sources:` don't resolve to `raw/`, un-cross-linked pages, malformed log entries.

**Verification is by running the tool, not by remembering the rules.** Do not report an ingest as done from memory. End the operation by actually invoking:

```
python_exec(
    working_dir="<workspace>/skills/llm-wiki/scripts",
    code="import lint_wiki; lint_wiki.lint('<workspace>/wikis/<name>')",
)
```

and **paste the script's literal output into your reply.** If you cannot show a clean (or explicitly-deferred) lint run, the ingest is not done. Pasting real output is the contract — a summary of "the checklist looks satisfied" is not acceptable, because that is precisely the step that has silently failed before.

**When sources conflict**, do not silently pick one number or claim. Record the discrepancy — cite both values on the page with their sources, and add an entry to `AGENTS.md` "Open research questions" so a human can adjudicate. A contradiction you smoothed over is a fact you invented.

Note: `lint` verifies *shape and provenance*, not *truth*. It confirms a page cites a real source; it cannot tell you the page misread that source. Getting facts right stays on you — keep the source open while writing and do not add facts that aren't in `raw/`.

### 3. `query`

Answer a question **grounded in the wiki**, not general knowledge.

**Steps**:
1. Read `wiki/index.md`. Scan for relevant pages by category.
2. Read the identified pages in full; follow one level of wikilinks.
3. If the wiki doesn't have enough material, say so and suggest what to ingest next instead of making something up.
4. Synthesize the answer, citing pages inline with `[[Page Name]]`.
5. Save to `outputs/queries/<YYYY-MM-DD>-<question-slug>.md`.
6. If the answer is durable (a comparison, analysis, or new synthesis) → promote a cleaned-up version to `wiki/concepts/`, add to `index.md`.
7. Log: `## [HH:MM] query | <question-slug>` (and a separate `## [HH:MM] promote | ...` line if promoted).

### 4. `lint`

Health check. Run one wiki, or the whole workspace:

```
python_exec(
    working_dir="<workspace>/skills/llm-wiki/scripts",
    code="import lint_wiki; lint_wiki.lint('<workspace>/wikis/<name>')",
)
python_exec(
    working_dir="<workspace>/skills/llm-wiki/scripts",
    code="import lint_wiki; lint_wiki.lint_workspace('<workspace>/wikis')",
)
```

Per-wiki, the script reports:
- **Encoding** — every page under `wiki/` must be UTF-8, and this is printed first. Reported 🔴: the project block reads pages as strict UTF-8 and skips a page it cannot decode **whole**, every turn — so a page in latin-1 exists, is linked, declares its state, and is still invisible to you; and `wiki/index.md` not decoding does not fail quietly. Re-save the file as UTF-8. The checks below keep reading it, with `�` in place of the bad bytes, so their findings on it are usable but its text is not exact.
- **Dead wikilinks** — `[[Target]]` where `Target.md` doesn't exist (includes cross-wiki links, which are dead by design — see core principle 5)
- **Orphan pages** — pages with no inbound wikilinks
- **Missing index entries** — pages not listed in `wiki/index.md`
- **Frequently-linked missing pages** — `[[X]]` referenced 3+ times but no page
- **log/ shape** — stray files or wrong filenames in `log/`
- **audit/ shape** — malformed frontmatter, non-unique id, or filename/id timestamp mismatch in `audit/*.md`
- **Audit target resolution** — every open audit's `target` file must exist
- **Duplicate pages** — pages whose titles normalize to the same key (case, punctuation, word order, stop-words) — the classic "two pages for one concept" mistake
- **Source integrity** — every concept/entity page has a non-empty `sources:` frontmatter (inline `[a, b]` or block-style `- a`), and every cited slug resolves to a file under `raw/`
- **Cross-link coverage** — every concept/entity page has at least one wikilink to or from another content page (being listed in `index.md` alone doesn't count)
- **Summary completeness** — every ingested source under `raw/{articles,papers,notes}` has a matching `wiki/summaries/<slug>.md`

Three checks are about what **every turn of that project pays for**, and they were missing from this list while the script had them:
- **Map size** — `wiki/index.md` over 2,000 characters. Reported 🟡: the project block injects the map into every turn and cuts it there, so past that ceiling the rest exists and you never see it. What outgrew a few lines belongs on a page.
- **Page size** — a page over 6,000 characters. Reported 🟡, and the difference from the map is what happens past the ceiling: the map arrives truncated, a page does not arrive **at all** — nothing enters half a page, so an oversized one is skipped whole, every turn, and the selection is alphabetical, so no question can call it up. Split it along the things it talks about.
- **A page list inside `AGENTS.md`** — over 1,000 characters of it. Reported 🟡: nothing there is false, it is paid for twice. `AGENTS.md` is injected **whole** into every turn of the project, with no ceiling, while the map it duplicates is cut at 2,000 — and a list here is a second index that nobody curates and that no check reads, so an entry naming a deleted page stays there silently (in the map, "Dead wikilinks" would catch it). Move the entries into `wiki/index.md` and leave `AGENTS.md` the scope, the conventions and the open questions. **Do not trim the tail instead** — that is where the open questions live, and it is the only part nothing else carries.

In a **notebook** layout (flat pages under `wiki/`) two more checks apply to every page:
- **Page state** — every page declares `state:` from the closed vocabulary `open` / `hypothesis` / `decided` / `done`. Reported 🔴: a page without a state says something false, because an idea jotted down in passing reads a month later as a settled fact.
- **Page source** — `source:` is the trail from a page back to the sentence that caused it (normally `source: raw/journal/<day>.md`). Two **separate** 🟡 findings, because they are two different facts: a page with **no** `source:` is not wrong, it is *unverifiable*; a page whose `source:` **names a file that is not there** had a trail that now leads nowhere — a journal day pruned or renamed, or a wrong value — and since the journal is append-only, a day that vanished is itself worth a look. `<placeholder>` values and URLs are not path-checked.

**Exit codes**, because a lint that cannot be told apart from a clean run is worse than no lint:

| code | meaning |
|------|---------|
| `0` | linted, no issues |
| `1` | linted, issues found |
| `2` | **nothing was linted** — no `wiki/` under the given root, no wikis under the given workspace, or bad arguments |

Everything, errors included, goes to **stdout** (the `wiki_lint` builtin captures only that). So `2` reads as a `🔴 wiki/ directory not found at …` line, never as an empty report: a path typo must not look like a healthy wiki.

Every list in the report is **capped at 20 entries** and then says `…and N more`; the count in each heading is always the real total. That cap is not cosmetic — `python_exec` keeps the head and tail of an over-long result and drops the middle, so an uncapped list would silently swallow whole later checks.

The **first line of the report names the layout it linted in** — `research` or `notebook` — and why. Research means at least one page actually lives under `concepts/`, `entities/` or `summaries/`; an empty folder decides nothing. Read that line: it tells you which checks are in play, and a check that quietly stops running is worse than one that never existed.

Two things about the **journal**, which is checked in every layout. Its append-only property is verified against the previous run, so the first run on a wiki records a baseline and says so — and only says so when the baseline was really written (in a read-only turn it cannot be). And an append-only violation **stays reported until the file is actually put back**: re-running the lint does not clear it, because "lint, fix, re-run until clean" must not be a way to erase the record that a promoted line was altered.

`lint_workspace` lints every wiki, then verifies `wikis/_index.md`'s registry block is in sync with the wikis on disk (missing, extra, or stale entries). Pass `fix=True` to repair registry drift automatically, or call `reindex_wikis.regenerate_index('<workspace>/wikis')`.

**One bad wiki costs one wiki.** Each wiki is linted in isolation, and so is the registry pass: a crash prints `🔴 the lint crashed on this wiki: …` and the run carries on. If a whole lint dies anyway, what you get back is still every finding it had already printed, followed by `🔴 the lint crashed before it finished: …` — read that line, because it means the checks after that point never ran: the wiki is neither clean nor a known set of issues, and the count above the line is not the total.

For each issue, propose a fix, confirm with the user, then apply. Log: `## [HH:MM] lint | <N> issues found, <M> fixed`.

### 5. `audit`

Process human feedback from `audit/`.

**Steps**:
1. Get a grouped list of the open audits:
   ```
   python_exec(
       working_dir="<workspace>/skills/llm-wiki/scripts",
       code="import audit_review; audit_review.main('<workspace>/wikis/<name>', 'open')",
   )
   ```
   Modes are `open`, `resolved`, `all`. Use `audit_review.run_workspace('<workspace>/wikis', 'open')` to survey audits across all wikis.
2. For each open audit, read the file. Use the `anchor_before` / `anchor_text` / `anchor_after` window to locate the exact range in the target file (line numbers may have drifted).
3. Decide the action:
   - **Accept**: apply the correction to the target file.
   - **Partially accept**: apply what makes sense, note the rest in the resolution.
   - **Reject**: explain why in the resolution — the feedback may be based on a misreading of scope or a contradictory source.
   - **Defer**: add to `AGENTS.md` "Open research questions" and leave the audit in place with a comment.
4. For applied audits, append a `# Resolution` section to the audit file:
   ```markdown
   # Resolution

   2026-04-10 · accepted.
   Fixed the file count (was "~1,900", corrected to "~1,800" per commit abc123).
   Updated: tech/Claude_Code.md lines 47–48.
   ```
5. Move the file from `audit/` to `audit/resolved/`. Filename unchanged.
6. Log per resolved audit:
   ```
   ## [HH:MM] audit | resolved 20260409-143022-a1b2 — <one-line what>
   ```
7. Never delete audit files. Rejected ones still go to `resolved/` with the rejection rationale in their resolution section — that's valuable history.

See `references/audit-guide.md` for the full audit file format.

---

## Tooling

| Tool | Purpose |
|------|---------|
| `scripts/scaffold.py` | Create a wiki under `wikis/` — or top up one that is missing pieces — and register it in `_index.md` |
| `scripts/reindex_wikis.py` | Regenerate / `--check` the `wikis/_index.md` registry block |
| `scripts/lint_wiki.py` | Per-wiki health check; `--workspace` lints all wikis + registry |
| `scripts/audit_review.py` | Group open/resolved audits by target; `--workspace` covers all wikis |
| [Obsidian](https://obsidian.md) | Optional — browse the wiki; renders mermaid/KaTeX/wikilinks; graph view shows connections |
| [qmd](https://github.com/tobi/qmd) | Optional local semantic search (useful at >100 pages) |

Audit files are plain Markdown with a YAML frontmatter anchor (see `references/audit-guide.md`), so they can be filed by hand or by any tool that follows that format.

**Run these scripts; do not reimplement them.** Invoke `scaffold.py`/`lint_wiki.py`/`reindex_wikis.py`/`audit_review.py` directly (via `python_exec`, see “Running the scripts” above) rather than hand-rolling their logic inline — the scripts are the source of truth for file shapes. In particular, when you append to `log/YYYYMMDD.md` by hand, keep the exact format `scaffold.py` established: the H1 is the ISO date `# YYYY-MM-DD` (e.g. `# 2026-07-17`), **not** the compact `# YYYYMMDD`. `lint_wiki.py` flags a mismatched H1 as a log-shape issue.

## Starting a new wiki

```
python_exec(
    working_dir="<workspace>/skills/llm-wiki/scripts",
    code="import scaffold; scaffold.scaffold('<workspace>/wikis/<new-name>', '<Topic Title>')",
)
```

Creates the full tree (including `raw/journal/`, `log/<today>.md`, `audit/`, `audit/resolved/`), a blank `AGENTS.md` based on the new template, and a blank `wiki/index.md` with the recommended category layout. It also registers the new wiki in `wikis/_index.md`.

If something of the **wrong kind** is in the way — a *directory* named `AGENTS.md` or `wiki/index.md`, a *file* named `log` — the script writes nothing there and reports it as a collision instead of claiming success: read the `🔴 Wrong kind of thing in the way` block, fix those paths by hand, and re-run. A run that ends with that block has left the wiki incomplete, and the lint will fail on it.

**Safe to re-run on a wiki that already exists.** Every file is written only when it is absent, so run it on an older wiki to add what the scaffold has grown since — the pieces appear, everything already on disk stays byte-identical, and the return value (also printed) lists what was added. It does *not* append to today's log when that file already exists: read the printed report instead, and log the top-up yourself if the wiki's history should carry it.

**On a folder that is already a notebook project it tops up that shape instead** — the journal and the plain tree, no `concepts/`, `entities/` or `summaries/`, and a flat map if the map is missing. It says so in its output when it does. The marker is a working journal plus pages that live directly under `wiki/`; a research wiki whose taxonomy is still empty is *not* a notebook and still gets the whole tree.

After scaffolding:
1. Fill in `AGENTS.md` — define scope, naming conventions, initial research questions.
2. Start ingesting sources.
3. Ask questions to build up `outputs/queries/`; promote durable answers.
4. Run `lint` periodically.
5. Run `audit` whenever new feedback accumulates.

## `wiki/index.md` format

The LLM rebuilds `index.md` on every compile and touches it on every ingest. Format:

```markdown
# Index — <Topic>

> One-sentence scope of the wiki.

## 🔖 Navigation
- [[#Concepts]] · [[#Entities]] · [[#Summaries]] · [[#Open Questions]]

## Concepts
### <Category A>
- [[concepts/Foo]] — one-line summary
- [[concepts/Bar/index|Bar]] — (folder-split) one-line summary
    - [[concepts/Bar/aspect-1]] — ...
    - [[concepts/Bar/aspect-2]] — ...

### <Category B>
- ...

## Entities
- [[entities/Andrej Karpathy]] — AI researcher, author of the llm-wiki pattern

## Summaries (chronological)
- 2026-04-09 — [[summaries/llm-wiki-gist]] — Karpathy's original Gist

## Open Questions
- Q1: ...
```

Rules:
- Every wiki page must appear exactly once in `index.md`. `lint` checks half of that: it reports a page **no** wikilink in `index.md` resolves to. A page listed twice is on you — nothing enforces the "once".
- Folder-split concepts show hierarchy via indented bullets.
- **How a `[[link]]` resolves**, in the app and in `lint` alike: the exact path under `wiki/`, then the folder's `index.md`, then the same path ignoring case, spaces and accents, then **any page whose path ends with the link** (so `[[Bar/aspect-1]]` finds `concepts/Bar/aspect-1.md` from anywhere), then — for a link with no `/` — any page with that name. A link with a `/` in it must match a real *tail* of the page's path: `[[Baz/aspect-1]]` is dead even when `aspect-1.md` exists under another folder. When two pages could match, the one closest to `wiki/` wins.
- After selecting the target wiki (see "Session start & wiki selection"), its `wiki/index.md` + `AGENTS.md` are what the AI reads before operating on it.

## `log/` format

See `references/log-guide.md` for full details. Minimum:

- One file per day: `log/YYYYMMDD.md`
- H1 = the date; H2 per entry with `## [HH:MM] <op> | <one-line description>`
- Ops: `compile`, `ingest`, `query`, `lint`, `audit`, `promote`, `split`, `scaffold`
- `gardener` is also valid, and is the one op you do not write: the gardener pass logs its own line (`## [HH:MM] gardener | …`) from code, not from a prompt.

Quick grep across history: `grep -rh "^## \[" log/ | tail -20`.

## Use cases

- **Research deep-dive** — reading papers/articles on a topic over weeks; the wiki evolves with your understanding, and the audit trail keeps AI mistakes from silently accumulating
- **Personal wiki** — journal entries, notes, ideas compiled into a personal encyclopedia; comment on anything you disagree with later, the AI corrects it
- **Team knowledge base** — fed by Slack threads, meeting notes, docs; team members file corrections into `audit/`
- **Reading companion** — filing each book chapter as you go; builds a rich companion wiki by the end

## References

- `references/schema-guide.md` — What to put in `AGENTS.md`
- `references/article-guide.md` — How to write good wiki articles (length, wikilinks, mermaid, math, divide-and-conquer)
- `references/log-guide.md` — The `log/` folder convention
- `references/audit-guide.md` — Audit file format, anchor strategy, processing workflow
- `references/tooling-tips.md` — viewing the wiki, qmd semantic search, workspace scripts

