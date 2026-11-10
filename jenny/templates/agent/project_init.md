Write this project's instructions file from what the folder actually contains.

**The file is `{{ instructions_path }}`** — that exact path, whatever it is
called. Do not create a second one: a wiki with two instructions files at its
root is a state nobody chose, and every reader can then only pick one and warn.

First read: `wiki/index.md`, the pages under `wiki/`, the most recent files in
`log/`, and that file itself if it already exists. Then write it.
{% if orchestrator %}
You have no tool that writes a file in this chat: once you have read the folder and know what the
file must say, spawn one `notebook-editor` with the exact text and that exact path. The reading and
the judgement below stay yours; only the write is delegated.
{% endif %}

What belongs in it:

- **Scope** — what this wiki covers, and what it deliberately excludes. Both halves; the exclusions are what stop it drifting.
- **Conventions actually in use** — naming, language, depth, how sources are cited. Read them off the existing pages; do not prescribe what the pages do not do.
- **Open questions** — what is unresolved, and what is worth ingesting next.

What does not belong in it:

- The folder layout, and the five operations. You already have both; a copy here only goes stale.
- Anything you cannot see in the folder. If the wiki is empty or too thin to characterise, **stop and ask** — one invented line of scope is inherited by everything filed afterwards.

If the file already has content, **update it rather than replace it**: keep every
line someone wrote on purpose, drop what the pages contradict, and say in your
reply what you changed and why. Keep it short — this file is read on every turn
of this project.
