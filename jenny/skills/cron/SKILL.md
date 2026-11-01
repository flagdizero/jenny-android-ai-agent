---
name: cron
description: Schedule reminders, recurring checks and conditional alerts.
locked: true
---

# Cron

Use the `cron` tool to schedule work. Every scheduled job runs a full agent turn.

## One-time or recurring

There are only two kinds of job:

- **One-time**: `at` for an exact time, or `date` for a day (`"YYYY-MM-DD"`, rings
  at 9:00) or just a month (`"YYYY-MM"`, "around March", rings at 9:00 on the 1st).
  It disappears once it has fired. A day the user named without a time is `date`,
  never `at` with an hour you picked: it is shown as a day, not as an appointment.
- **Recurring**: `every_seconds` or `cron_expr`. It is a `reminder` or a `monitor`
  (below), and it may carry a `condition` on a mini app's value: the app is read in
  code at each run, at no cost, and the job speaks only when the condition has just
  become true (once, until it goes back to false).

Each run costs a full turn: pick the widest interval that does the job.

## Pick the mode first

`mode` is a real parameter of `action="add"`. It decides whether the job is
**allowed to speak on its own**, and it is the single most important choice here —
getting it wrong is what turns a check into chat spam.

| `mode` | Speaks | Session | Use when |
|---|---|---|---|
| `reminder` (default) | Every run, automatically | this conversation | the user wants to hear from it every time it fires |
| `monitor` | **Only** if you call the `message` tool | its own isolated session | the run should stay silent unless it finds something |

`monitor` requires `every_seconds` or `cron_expr`; it is never valid with `at` or `date`.

**Any request phrased as a condition is a `monitor`.** Listen for: "only tell me
if…", "warn me when…", "let me know if it goes below…", "check every 10 minutes
whether it's back up". If the user described *when they want to be interrupted*,
they also described when they do **not** — that is `mode="monitor"`.

In a `monitor` run, silence is a **correct, successful outcome**. Do not call
`message` to report that everything is normal, and never send filler like
"All clear.", "All done." or "nothing to report" — the user asked to be left
alone in exactly that case. A monitor keeps its earlier runs in its own session,
so it can report a *change* instead of repeating itself.

## Examples

Fixed reminder — speaks every time:
```
cron(action="add", message="Time to take a break!", every_seconds=1200, mode="reminder")
```

Conditional alert — speaks only when the condition fires:
```
cron(action="add", message="Check the chance of rain in every city on the list; if any is above 70%, warn me. Otherwise say nothing.", every_seconds=3600, mode="monitor")
```

Recurring check whose result the user always wants:
```
cron(action="add", message="Check GitHub stars and report the count", every_seconds=600, mode="reminder")
```

One-time scheduled task (compute ISO datetime from current time; auto-deletes):
```
cron(action="add", message="Remind me about the meeting", at="<ISO datetime>")
```

One-time, a day or a month without a time:
```
cron(action="add", message="Renew the passport", date="2027-03")
```

Recurring with a condition on a mini app — reads the app every 6 hours, speaks only
when the value crosses the threshold:
```
cron(action="add", message="The basil's soil is dry: remind me to water it.", every_seconds=21600, mode="reminder", condition={"app": "garden", "field": "humidity", "op": "<", "value": 15})
```

Timezone-aware schedule:
```
cron(action="add", message="Morning standup", cron_expr="0 9 * * 1-5", tz="America/Vancouver", mode="reminder")
```

List, pause, resume, remove:
```
cron(action="list")
cron(action="pause", job_id="abc123")
cron(action="resume", job_id="abc123")
cron(action="remove", job_id="abc123")
```

`action="list"` reports each job's last status. `silenced` there means a monitor
ran fine and had nothing to say — it is a success, not an error.

## Notebook jobs

A job belongs to the personal chat or to a notebook (`notebook`; in a notebook chat
it is that notebook by default), and then to the card it is about (`card`), or to
the whole notebook when `card` is empty. `why` names the notebook's wiki page that
explains why the job exists. A notebook job runs inside that notebook and delivers
in its chat.

## Time Expressions

| User says | Parameters |
|-----------|------------|
| every 20 minutes | every_seconds: 1200 |
| every hour | every_seconds: 3600 |
| every day at 8am | cron_expr: "0 8 * * *" |
| weekdays at 5pm | cron_expr: "0 17 * * 1-5" |
| 9am Vancouver time daily | cron_expr: "0 9 * * *", tz: "America/Vancouver" |
| at a specific time | at: ISO datetime string (compute from current time) |
| on 3 March, no time | date: "2027-03-03" |
| around March | date: "2027-03" |

## Timezone

Use `tz` with `cron_expr` to schedule in a specific IANA timezone. Without `tz`, the server's local timezone is used.
