# Audit Guide — human feedback on wiki content

The `audit/` directory is the human feedback surface. One file per feedback, YAML frontmatter + markdown body. Feedback is filed by hand (or by the agent) and **consumed by the AI during the `audit` operation**.

## Why it exists

AI-written content is wrong sometimes. Raw sources contradict each other. Feedback in chat is lost the moment the conversation ends. The audit directory gives corrections a permanent, location-anchored home that the AI and the lint script understand.

## Directory layout

```
<wiki-root>/audit/
├── 20260409-143022-claude-code-size.md    ← open feedback
├── 20260409-150110-rag-definition.md      ← open feedback
└── resolved/
    ├── 20260408-110505-typo-gemma.md      ← processed, with resolution
    └── 20260407-180012-rejected-scope.md  ← rejected, with rationale
```

- `audit/*.md` — open feedback, not yet processed.
- `audit/resolved/*.md` — processed feedback. Nothing ever gets deleted; rejections stay with their rationale.

## File format

Filename: `YYYYMMDD-HHMMSS-<short-slug>.md`. The prefix is the creation timestamp (local time); the slug is a human-readable hint derived from the selected text or the comment.

```markdown
---
id: 20260409-143022-a1b2
target: tech/Claude_Code.md
target_lines: [45, 52]
anchor_before: "## 技术概览\n\n| 维度 | 详情 |\n|------|------|\n"
anchor_text: "| **规模** | ~1,900 个文件，512,000+ 行代码 |"
anchor_after: "\n| **语言** | TypeScript（strict 模式） |"
author: your-name
source: manual
created: 2026-04-09T14:30:22+08:00
status: open
---

# Comment

实际应该是 ~1,800 个文件，参考 2026-03-31 commit abc123 的 tree。
`find . -type f | wc -l` 当时是 1817。这个数字直接影响下面几个估算。

# Resolution

<!-- Filled in when the audit is processed and moved to resolved/ -->
```

### Frontmatter fields

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | string | yes | Unique id: `YYYYMMDD-HHMMSS-<4hex>`. Must match filename prefix. |
| `target` | string | yes | Path relative to wiki root. Must be a file that exists (lint check). |
| `target_lines` | `[int, int]` | yes | Best-effort 1-indexed inclusive line range at the time of writing. May drift. |
| `anchor_before` | string | yes | Up to ~80 chars of text immediately before the selection. Verbatim, preserves newlines. |
| `anchor_text` | string | yes | The exact selected text. Verbatim. |
| `anchor_after` | string | yes | Up to ~80 chars of text immediately after the selection. Verbatim. |
| `author` | string | yes | Free text — who filed the feedback. |
| `source` | string | yes | Identifies who/what filed it. `manual` for hand-filed; any tool that writes audits may use its own id. |
| `created` | ISO 8601 | yes | Timestamp with timezone. |
| `status` | enum | yes | `open` for files in `audit/`, `resolved` for files in `audit/resolved/`. |

### Processing order

**Oldest first.** There is no severity field: audits carry no priority, and the
queue is the order they arrived in.

This used to be a four-level `severity` the filer chose before writing the
comment (`info` / `suggest` / `warn` / `error`), and it set this order. It was
removed on 2026-09-22: it is a triage field, and triage is what a team does. In
a wiki where the person who files the feedback, the person who applies it and
the owner of the corpus are the same one, nobody wants to grade their own
complaint — and every audit ended up at the default anyway.

If a corpus ever needs priority again, it comes back in two places: this field,
and the sort key in `scripts/audit_review.py`.

## Anchor strategy

Line numbers alone are fragile — any edit earlier in the file invalidates them. So every audit file carries a **text-based anchor window** alongside the line numbers.

On write (when filing an audit):
1. Capture `target_lines` from the selection range.
2. Extract `anchor_text` = the exact selected characters.
3. Extract `anchor_before` = up to 80 characters immediately before the selection start (clamped to start of file).
4. Extract `anchor_after` = up to 80 characters immediately after the selection end (clamped to end of file).

On read (AI during `audit`, or audit_review.py):
1. Try `target_lines` — check whether the text in that line range contains `anchor_text`.
2. If not, search the whole file for `anchor_text`. If exactly one match, use it.
3. If multiple matches, use `anchor_before + anchor_text + anchor_after` as a combined search key.
4. If still no match, the anchor is **stale** — flag to the user during the `audit` op. Do not silently drop; ask whether to re-anchor, reject, or archive.

This spec is the single source of truth for the anchor format.

## Processing workflow (the `audit` op)

See `SKILL.md` → "The five operations" → `audit` for the canonical version. In short:

1. `python_exec(working_dir="<workspace>/skills/llm-wiki/scripts", code="import audit_review; audit_review.main('<wiki-root>', 'open')")` → get a grouped list.
2. For each open audit:
   - Read the file, use the anchor to locate the range in the target.
   - Decide: accept / partial / reject / defer.
   - Apply edits in the target file (in the smallest edit that fixes the issue).
   - Append a `# Resolution` section to the audit file.
   - Flip `status: open` → `status: resolved` in the frontmatter.
   - Move the file to `audit/resolved/`.
   - Append a `## [HH:MM] audit | resolved <id> — <one-liner>` entry to `log/YYYYMMDD.md`.
3. If an audit is deferred (e.g., unresolvable contradiction), leave the file in `audit/` and add the question to `AGENTS.md` "Open research questions" with a reference to the audit id.

## Resolution section format

```markdown
# Resolution

2026-04-10 · accepted.
Fixed the file count (was "~1,900", corrected to "~1,800" per commit abc123).
Updated: tech/Claude_Code.md lines 47–48.
Log: [[log/20260410#1430 audit]]
```

Fields:
- Date · decision (`accepted`, `partial`, `rejected`, `deferred`).
- 1–3 sentences on what you did and why.
- Which files were touched (for non-trivial edits).
- Pointer to the log entry.

For `rejected` audits: explain **why** — most often "out of scope per AGENTS.md" or "contradicts more authoritative source X". Rejected audits still move to `resolved/` so they're not processed again, but they remain visible in case the scope changes.

## Tooling

- **`scripts/lint_wiki.py`** validates audit file shape and that every `target` file exists.
- **`scripts/audit_review.py`** lists and groups audits by target file.

To file an audit by hand: create `audit/YYYYMMDD-HHMMSS-<slug>.md` with the frontmatter above (fill the anchor fields from the selected text) and a `# Comment` body. `lint_wiki.py` will validate its shape.

