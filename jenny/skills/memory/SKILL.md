---
name: memory
description: Where the user's long-term memory lives and how to find something said in the past.
always: true
internal: true
---

# Memory

- `SOUL.md`, `USER.md`, `memory/MEMORY.md` — personality, the user, long-term facts.
  **Dream manages them: never edit them.** Something outdated is fixed when Dream runs next.
- `memory/history.jsonl` — past conversations, one JSON object per line, never loaded into the
  prompt. Do not read it whole.
- `wikis/<name>/` — the user's notebooks; `wikis/<name>/wiki/index.md` lists their pages. Do not
  copy their content into the files above.

To find something said in the past, use `recall` for facts that were moved out of the files
above, and `recall_history` for details of past conversations that Dream did not keep.
