# Work for Later

Anything for later is a `cron` job: one-time or recurring. Picking the wrong kind is what turns a check into chat spam.

- One-time → `date`: the day (`YYYY-MM-DD`) or just the month (`YYYY-MM`), plus `time` (`HH:MM`) only when the user said a time or a delay. A day the user named without a time has no `time`: never pick an hour for them. It disappears once it has fired.
- Recurring → `every_seconds` or `cron_expr`, and one of two modes:
  - `mode='reminder'`: speaks every time it fires.
  - `mode='monitor'`: runs silently and speaks only when there is something to report. The only way its runs reach the user is the `message` tool, so an uneventful run produces no message at all.

Any request phrased as a condition — "only tell me if…", "warn me when…", "check every 10 minutes whether it's back up" — is a `monitor`, never a plain reminder. When the condition is a mini app's value, give the recurring job a `condition` instead: it is checked in code at each run, and the job speaks only when it becomes true. Use the cadence the user said; if none, pick one and always say it in your reply.

Every run costs a full turn: pick the widest interval that does the job.

Writing a reminder into `memory/MEMORY.md` schedules nothing. Before scheduling, read `skills/cron/SKILL.md`: syntax, timezones, examples and `list` semantics live there.
