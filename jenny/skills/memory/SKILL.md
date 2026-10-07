---
name: memory
description: Where the user's long-term memory lives and how to find something said in the past.
always: true
internal: true
---

# Memory

- `SOUL.md`, `USER.md`, `memory/MEMORY.md` — personality, the user, long-term facts.
  **Dream manages them: never edit them.** Something outdated is fixed when Dream runs next
  (the user sees what it did with `/dream-log`).
- `memory/history.jsonl` — past conversations, one JSON object per line, never loaded into the
  prompt. Do not read it whole.
- `wikis/<name>/` — the user's notebooks; `wikis/<name>/wiki/index.md` lists their pages. Do not
  copy their content into the files above.

To find something said in the past, use `recall`: it searches the memory and the history for
you, and says where each match comes from.
