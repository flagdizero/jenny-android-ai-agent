The user has asked you to tidy this project's wiki: `{{ project_path }}`.

This is the `compile` operation, and it has a recipe. **Read the `llm-wiki` skill first** and follow
it.
{% if notebook %}
This project is in the **notebook** layout — flat pages under `wiki/`, each with `state:` and
`source:`, fed by an append-only `raw/journal/` — so the section you want is `compile` in a
**project** wiki (notebook layout). It carries the five rules that differ from the research layout,
and each of them has been got wrong before: the ceiling is characters and not words, text moves
**verbatim** with no merge and no re-wording, `source:` is a single value and never a list, `state:`
does not go up during a restructure, and the map's page list comes out whole.
{% else %}
This project is in the **research** layout — its pages live under `concepts/`, `entities/` and
`summaries/` — so the recipe is the five numbered steps of `compile` as written. The section below
them, `compile` in a **project** wiki, describes the *other* layout: do not apply its rules here,
except the one thing that belongs to neither, which is the per-turn ceiling in the measurements
below. The injector does not care which layout a page is in.
{% endif %}
**Follow the structure you find.** The folder is the authority, not your preference: put new pages
where this project's organisation says they go, and open a new subfolder only when a group of pages
has clearly earned one.
{% if orchestrator %}
**In this chat you read and decide; a `notebook-editor` writes.** You have no tool that writes a
page or runs code here. Read the recipe and the pages, settle with the user what has to be settled,
then spawn one `notebook-editor` with the moves spelled out — which text goes from which page to
which page, word for word — and the measurements below. Do not ask it to "tidy the wiki": it does
what the task says and nothing else, so a vague task moves nothing or moves the wrong thing.
{% endif %}

## What the measurements say, right now

These are read from disk by the code that runs the injection, not estimated — so they are the same
numbers a turn in this project actually pays.

- **The map** (`{{ project_path }}/wiki/index.md`): {{ map_chars }} characters against a ceiling of
  {{ map_target }}.{% if map_over_budget %} **It is over.** Past that ceiling the map arrives cut
  off in every turn: the model reads its head and never learns the rest exists. Move the prose out
  into the pages it is about — word for word — and leave a `[[link]]` behind. Prune prose, never
  entries.{% else %} It fits.{% endif %}

- **The pages**: {{ page_count }} in all, and a turn can inject {{ page_max }} characters of them,
  in the order the map names them.
{%- if pages_over %}

{{ pages_over }}

  Each of those is past the whole per-turn budget on its own, so it is **skipped whole** in every
  conversation in this project — it is on disk and nobody can read it. Splitting one is the first
  thing to do here.

{%- else %}
  No page is over the budget on its own. That does not mean nothing needs splitting: a merely large
  page starves the pages the map lists after it, and the map's order decides who gets read.

{%- endif %}


## What you have that a maintenance pass does not

**The user is here.** That changes two things, and only two:

- A contradiction between pages, or a journal line that denies a page, does not have to be parked in
  the map's open section. **Ask.** If they settle it, say so in the log line — that is the difference
  between a decision and a pass that decided on its own.
- You may read any page in full. The pages in your context are the ones the map names first, not all
  of them; open the rest before moving their text.

{% if notebook %}
Everything else is unchanged, and the strongest rule is the one that is easiest to break with the
user's encouragement in hand: **add and move, do not rewrite.** The sentences in these pages came
from their own words through an append-only journal. A re-wording detaches a page from the `source:`
that justifies it, and no one will notice for weeks.
{% else %}
It does not lift the recipe's own brake, and with the user's encouragement in hand that is the first
thing to go: `compile` says **confirm a split or a merge with them before writing it**. Being asked
to tidy the wiki is not that confirmation — it is what makes the question worth asking, and they are
right here to answer it.
{% endif %}


## When you are done

{% if orchestrator %}
The log line — `## [HH:MM] compile | <what moved where>` — is part of the `notebook-editor`'s task:
name it there. When its report comes back, run `notebook action=check` and paste its literal output
into your reply. It covers the structural checks, not the full lint: say so, instead of calling the
wiki clean.
{% else %}
Write the log line — `## [HH:MM] compile | <what moved where>` — and run the lint, pasting its
literal output into your reply:

```
python_exec(
    working_dir="skills/llm-wiki/scripts",
    code="import lint_wiki; lint_wiki.lint('{{ project_abs }}')",
)
```

The path there is absolute on purpose: `working_dir` puts you inside the scripts folder, and the
workspace-relative form this project uses everywhere else would not resolve from there.
{% endif %}

If the wiki was already in good shape, say so in one line and change nothing. A tidy that moves
nothing is a correct outcome, and inventing work to justify the command is the one failure this
operation can produce that nobody will catch.
