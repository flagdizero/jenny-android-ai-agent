---
name: memory
description: Where the user's long-term memory lives and how to find something said in the past.
always: true
internal: true
---

# Memory

- `SOUL.md`, `USER.md`, `memory/MEMORY.md` — personality, the user, long-term facts.
  **Dream manages them: never edit them.** Something outdated is fixed when Dream runs next.
  So when the user asks you to remember, change or forget something, it is not done yet: say
  it will be applied at the next Dream pass, which `/dream` starts now.
- `memory/history.jsonl` — past conversations, one JSON object per line, never loaded into the
  prompt; the oldest move to `memory/history-archive/`. Do not read them whole.
- `wikis/<name>/` — the user's notebooks; `wikis/<name>/wiki/index.md` lists their pages. Do not
  copy their content into the files above.

To find something said in the past, use `recall` for facts that were moved out of the files
above, and `recall_history` for details of past conversations that Dream did not keep.
Search them with `query`, in both Italian and English: the memory is written in both.
