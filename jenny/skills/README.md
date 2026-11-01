# Jenny Skills

This directory contains built-in skills that extend Jenny's capabilities.

## Skill Format

Each skill is a directory containing a `SKILL.md` file with:
- YAML frontmatter (name, description, metadata)
- Markdown instructions for the agent

When skills reference large local documentation or logs, prefer Jenny's built-in
`grep` tool to narrow the search space before loading full files.
Use `grep(output_mode="count")` / `files_with_matches` for broad searches first,
use `head_limit` / `offset` to page through large result sets,
and `grep(glob="*.md")` to filter by file name pattern.

## Attribution

These skills are adapted from the nanobot skill system.
The skill format and metadata structure follow nanobot's conventions.

## Available Skills

| Skill | Description |
|-------|-------------|
| `http-client` | Make HTTP requests using Python httpx |
| `data-processing` | Process data with Python — JSON, CSV, regex, hashing |
| `skill-creator` | Create new skills |
| `app-creator` | Create Jenny Apps (typed-actions manifest + HTML UI in `workspace/apps/`) |
| `llm-wiki` | Karpathy-style knowledge base — scaffold, ingest, compile, query, lint, audit |