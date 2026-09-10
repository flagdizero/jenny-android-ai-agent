# Scheduling and proactivity

Jenny can remind you of things, watch a checklist in the background, work a long task across many turns, and delegate side work to a helper agent — all by asking in chat, no separate scheduling screen involved.

## The one thing to know before you rely on this

**Everything in this page lives inside the app's own gateway process.** There is no server in the cloud keeping time for you. If Android kills the app (or you swipe it away, or the battery optimizer freezes it) at the moment a reminder was supposed to fire, here is what actually happens:

- A **one-shot reminder** ("remind me at 6pm") whose time passed while the app was dead now fires **late, once**, shortly after the app comes back. Before 0.6.6 it was lost forever and silently: recomputing the next run of a one-shot whose time had already passed came back empty, and the job sat there enabled but unrunnable — never retried, never reported missed, nothing telling you it hadn't happened. Late delivery is deliberate: for a reminder, hours late beats never.
- A **recurring reminder** ("every 30 minutes", "every day") keeps its deadline across restarts, and *does* catch up: a daily reminder that came due at 9am while the app was dead fires shortly after the app comes back, not 24 hours later. Before 0.6.0 this was not the case — every restart reset the interval to "now + interval", so on a phone that restarts the app often, a long interval could go indefinitely without ever firing. The same fix covers the three built-in jobs below.
- **Catch-up is not the same as punctuality**, and until 0.6.6 it wasn't even close. A foreground service keeps the *process* alive, not the *processor*: with the screen off the phone suspends, and the timer a job was sleeping on stops advancing along with the CPU. It didn't run late because anything was slow — the clock had stopped. On a test device a 30-minute job was observed firing between 30 and 83 minutes apart for exactly that reason.

### What 0.6.6 changed, and what it didn't

Two mechanisms address the frozen-clock problem directly:

- **The deadline now lives on Android's clock, not on Jenny's.** Alongside the in-process timer, the scheduler asks the OS to wake the phone at the next job's real deadline (`power.alarmDrivenCron`, on by default). An OS alarm fires through Doze; a suspended timer does not. So a due job no longer has to wait for the phone to wake up on its own.
- **The CPU is held awake around the work itself.** By default (`power.keepAwake: "turns"`) Jenny takes a wake lock for the duration of a turn, a scheduled job or an SSH command, and releases it straight after. Without it a job could fire on time and then freeze halfway through — mid-provider-call, mid-tool, mid-write.

And when the process is killed rather than merely frozen, several nets try to bring it back: a self-chaining watchdog alarm (`power.watchdogEnabled`, base 15 minutes, spaced out in Doze), a 15-minute periodic worker, an 8-hourly alarm-clock wake-up, plus opportunistic restarts when the network returns or you open the app. The gateway also comes back by itself after an app update, which it previously did not — it used to stay down until you next opened Jenny by hand.

What none of that fixes, and you should plan around:

- **A missed cron-expression occurrence is still dropped.** A `0 9 * * *` job that came due while the app was dead is recomputed from now, so that morning's run is skipped without a word; the next one arrives normally. Only one-shot and interval schedules catch up.
- **A recovered one-shot arrives with no sense of how late it is.** It fires whenever the app next comes up — hours or days after the fact — and the message is the one you wrote, unchanged. If that would be worse than silence for a particular reminder, a one-shot is the wrong tool for it.
- **After a reboot, nothing runs until you unlock the phone.** Jenny's workspace, config and runtime live in storage that Android keeps encrypted until the first unlock, so the gateway cannot start before it — deliberately, since the alternative is keeping your API keys and memory outside that encryption. A phone that reboots at 3am and sits locked until 8 is a phone with a five-hour hole in it.
- **Exact alarms can be switched off.** If Android's "Alarms & reminders" permission isn't granted, Jenny falls back to inexact alarms: they still fire in Doze, but they slip. Settings → Background activity tells you which of the two you're getting.
- **Your phone's own battery manager outranks all of it.** Samsung, Xiaomi/MIUI, Huawei/Honor, Oppo and Vivo kill background apps on their own terms, and no application code can prevent it. What Jenny can now do is *notice*: a stretch of downtime longer than `power.gapWarningMin` (default 60 minutes) is recorded and listed under **Settings → Background activity**, with the manufacturer-specific advice for turning the restriction off.

So: intervals are still a floor, not a promise — but the floor moved a long way up. Measured on the development phone (Unihertz Titan 2, Android 16), unplugged and idle for nine hours overnight, with both the battery exemption and the "Alarms & reminders" permission granted: a 30-minute job fired 19 times in a row and **every single interval landed between 30m00s and 30m02s**, including right through an uninterrupted four-hour stretch of deep Doze with no maintenance windows at all. The gateway was never killed and never restarted; the battery went from 80% to 77% over 9.4 hours.

Read that for what it is: one phone, in the configuration where everything is granted. It says nothing about a phone where you grant neither permission, and since nothing killed the gateway during the run, the watchdog's repair path was never actually exercised. Your manufacturer's battery manager remains the variable nobody's code can control.

If reminders matter to you, the practical measures are unchanged:

- Grant the battery-optimization exemption Jenny offers during first-run setup (or later from **Settings → Background activity**, or from Android's own battery settings) so the OS is less likely to freeze the background service.
- Keep the phone charged and connected when a reminder is close to due.
- Treat "at" reminders as best-effort, not guaranteed alarms — for anything truly time-critical, use your phone's own alarm clock as a backup.

<!-- TODO: verify on-device (O-5): the granted-everything case was measured on the Titan 2 on 2026-08-09 and is written up above. Still unmeasured: the drift of the same job with neither permission granted, whether the watchdog really recovers a gateway that was killed (nothing killed it during the run), and how often the recorded-outage panel finds a gap in normal use. -->

## Reminders (the `cron` tool)

You don't configure this from a screen — you just ask, in plain language, and Jenny translates it into a scheduled job:

- "Remind me to take the pizza out in 20 minutes."
- "Every day at 9am, ask me how I slept."
- "Every 2 hours, check if it's raining and tell me if so."
- "List my reminders."
- "Cancel the pizza reminder."

Under the hood there are three schedule kinds:

| Kind | How you'd phrase it | Behavior |
|---|---|---|
| One-shot (`at`) | "remind me at 6pm", "in 20 minutes" | Fires once, then the job deletes itself automatically. Cannot be combined with monitor mode (see below). |
| Interval (`every`) | "every 30 minutes", "every 2 hours" | Repeats forever at a fixed interval, counted from when the job was (re)armed — not from a fixed clock time. |
| Cron expression (`cron_expr`) | "every day at 9am", "every Monday at 8" | Standard 5-field cron syntax (e.g. `0 9 * * *`); accepts an optional IANA timezone (e.g. `America/Vancouver`) for that one job. |

The timezone used when you don't specify one is your **device's timezone**, resolved once when the app starts (falls back to UTC if the device timezone can't be determined). A timezone override only applies to cron-expression schedules — one-shot and interval schedules always use the device timezone.

When a reminder fires — a plain reminder, that is, the default mode described in [Two modes](#two-modes-one-that-always-speaks-one-that-speaks-only-if-it-has-to) below — it runs as a completely normal agent turn in the same chat you created it from, seeded with an instruction along the lines of "the scheduled time has arrived, execute this job and report the result." The reply lands in your chat exactly like any other message from Jenny — and, if the app isn't in the foreground at that moment, it also rings as an Android notification titled "Jenny ⏰ <job name>" (see [Notifications](#android-notifications) below). If a reminder fires while you're already mid-conversation with Jenny on that same chat, it politely waits and delivers once your current turn is idle, rather than interrupting it.

A few smaller things worth knowing:

- A reminder job cannot schedule further jobs from inside its own execution — this only matters if you ask Jenny to "set up a reminder that then sets another reminder" in one step.
- The job's default name is just the first 30 characters of your reminder's message, unless you give it something more memorable.
- You can list and remove reminders by asking — there is no dedicated screen for this; it's entirely conversational (e.g. "what reminders do I have?", "remove job xyz").

### Two modes: one that always speaks, one that speaks only if it has to

Every job also has a **mode**, and it isn't something you type — you describe what you want and Jenny picks it. The difference is whether a run that has nothing to say still writes into your chat.

| Mode | How you'd ask for it | What each run does |
|---|---|---|
| Reminder (default) | "Remind me to take the pizza out in 20 minutes", "Every day at 9am, ask me how I slept", "Every 2 hours, check if it's raining and tell me if so" | Runs in the chat you created it from and **always** replies — even when the answer amounts to "nothing to report". |
| Monitor | "Every 10 minutes check if my site is back up and tell me when it is", "Keep an eye on that page every hour and only ping me if the price drops", "Check the backup job each morning and only tell me if it failed" | Runs out of sight and stays quiet, unless the check actually turns up something worth your attention. When it does, it messages you deliberately. |

The wording that tips Jenny toward a monitor is *only* / *just if* / *let me know when it changes*; the wording that keeps it an ordinary reminder is asking to be told something at a time, full stop. If she guesses wrong, say so ("no, only write to me if it's actually down") and she can recreate the job the other way.

A monitor runs in a private session of its own, kept apart from your conversation. That has two consequences worth understanding:

- Your chat doesn't fill up with hourly "still down" replies you never asked to read.
- The job remembers its own previous checks, and that memory is precisely what lets it tell you **when something changed** instead of repeating the same sentence every cycle. That private history is trimmed to the last handful of checks, so it can't grow without bound.

When a monitor does decide to speak, the delivery is proactive in the same way Heartbeat's is: it reaches the WebUI chat and, if you've paired a Telegram bot, that chat too — not just whichever one you happen to be looking at.

**A monitor may stay silent forever, and that is the feature working, not failing.** Exactly as with Heartbeat below, "I set up a check and never hear anything" is the expected outcome when nothing noteworthy ever happens. To confirm it's alive, ask Jenny to list your reminders: a run that had nothing to report is recorded as `Last run: … — silenced`. That is the healthy line to see; `error` is the unhealthy one.

**A check that couldn't run is not the same as a check that found nothing**, and Jenny keeps them apart. When a cycle can't actually perform its check — a script is missing, a device is unreachable, a tool broke — the run is recorded as `could_not_check` rather than `silenced`, and the listing adds a line like `Could not check: 3 consecutive run(s), since …`. She still says nothing the first couple of times, because a check that fails once is usually just a network blip. After **three cycles in a row** with no check at all she writes to you once, in plain terms, and then goes quiet again until something changes. So a monitor that has quietly broken can no longer look exactly like a healthy one, and a healthy one still costs you nothing.

**Silence saves you the notification, not the tokens.** Every monitor cycle is still a real agent turn against your provider — the check has to actually run before anything can conclude there's nothing to say. A monitor every 10 minutes is ~144 turns a day whether or not it ever speaks a word, so pick the loosest interval that still catches what you care about, and remove the job once the thing you were watching is settled. This is the same warning as the Heartbeat one below, for the same reason.

Monitor mode only makes sense on a repeating schedule, so **it cannot be combined with a one-shot ("remind me at 6pm") job**: a single firing that might decide to say nothing would simply never reach you. Jenny refuses that combination outright rather than schedule something that can silently do nothing.

### Protected system jobs

When you ask Jenny to list reminders, you'll also see jobs you didn't create: **`dream`**, **`heartbeat`**, and — when they are on — the [gardener](gardener.md) and the update check. These are system-managed and will show up as protected — visible for inspection, but Jenny will refuse to remove them if asked (a removal attempt gets a reply along the lines of "this is a protected system-managed cron job" and cannot be removed). The way to stop one is its config switch, not the reminder list.

| Job | Runs | Config | What it costs you |
|---|---|---|---|
| `dream` | every **2 hours** | `agents.defaults.dream.enabled` (default on), `agents.defaults.dream.intervalH` (default `2`) | One agent run — a real turn against your provider, several calls if it uses tools — whenever there is new conversation to consolidate. Takes a snapshot first, so a bad run is undoable. |
| `heartbeat` | every **30 minutes** | `gateway.heartbeat.enabled` (default on), `gateway.heartbeat.intervalS` (default `1800`) | Nothing when `## Active Tasks` is empty; one real turn when it isn't. See below. |

`dream` runs the memory-consolidation pass, described in [Memory and Dream](memory.md); `heartbeat` is described next.

### If the reminder list itself gets damaged

Your reminders live in one file, `cron/jobs.json` inside the workspace, and a phone can leave a file unreadable — storage trouble, or the system killing the process mid-write.

Since 0.6.6 that file is handled the same way as `config.json`. Jenny keeps the previous good copy as `cron/jobs.json.bak` and refreshes it before every save. If the live file can't be read at startup, the backup is used and promoted; if there's no usable backup either, the unreadable file is set aside as `cron/jobs.json.corrupt-<timestamp>` and Jenny starts with **no reminders at all**. Either way the app comes online, and Settings shows a notice saying which of the two happened and where the broken file went.

That notice matters more here than it does for settings. A reminder that has stopped existing looks exactly like a reminder that hasn't come due yet, so without being told, you'd find out when it didn't go off. If you see the "started with none" version, your own reminders need recreating — the system jobs above come back on their own.

Settings → **Scheduling** is where you check the outcome: it lists everything scheduled and repeats the recovery notice above the list itself, because further down the screen a short list of reminders looks exactly like a correct one. See [Settings → Scheduling](../reference/settings.md#scheduling).

The single case where Jenny still refuses to start is when the broken file can't be moved aside at all. Starting anyway would mean the next save overwrites it, and that file is the only copy of your reminders left.

## Heartbeat: a periodic checklist

Heartbeat is Jenny's own background watchdog, driven entirely by one file: `workspace/HEARTBEAT.md` in your workspace. You can edit it directly through the Workspace file browser, or just ask Jenny to add something to it.

Only the section literally named `## Active Tasks` is read — anything you write under a different heading, or outside any heading, is ignored. The file ships with a comment reminding you of this and to delete tasks once they're done rather than leaving them checked off.

Every **30 minutes** by default (`gateway.heartbeat.intervalS`, default `1800` seconds), Jenny reads that section. If it's empty (only headers, blank lines, or HTML comments), the cycle is skipped entirely before any model call happens — so an empty Heartbeat costs you nothing. If there's at least one task line, Jenny runs a real turn to check on it.

**That turn is silent by construction.** Whatever Jenny writes as its answer is not delivered anywhere and nobody reads it; the only way a Heartbeat cycle reaches you is Jenny deciding, during the turn, to send you a message explicitly. So "I set a task and never hear anything" is a real, expected outcome if nothing noteworthy ever comes up, not a bug — and the flip side matters too: a check written as a condition ("…and warn me only if humidity drops below 15%") will not report the uneventful case at all. Before 0.6.6 that same restraint was attempted the other way around: the turn was told to answer "All clear." when it had nothing to say, and a second LLM call then guessed whether to hide it. That guess ran with a small token ceiling and, with a reasoning model, routinely ran out of budget before deciding — falling back to its default instead of judging. It's gone; silence is now a property of the turn, not an opinion about its text. Since 0.9.6 Jenny also has a way to *declare* that silence — a `nothing_to_report` step that delivers nothing and reaches nobody. It exists because "say nothing" is an instruction with nothing to do, and a small model was expressing it by sending you a message containing a placeholder word instead.

When Heartbeat does decide to speak, the message is delivered proactively to **both** the WebUI chat and, if you've paired a Telegram bot, that chat too — it's not confined to whichever channel you're currently looking at.

The practical rule of thumb: **write tasks under `## Active Tasks`, and delete them once they're done.** Every cycle where that section has content triggers one real LLM call — a forgotten task left in the file keeps costing tokens every 30 minutes indefinitely, even if Heartbeat never finds anything worth reporting.

Example of something reasonable to put there: "Check the weather forecast around 7am and warn me if it looks like rain."

**An empty checklist is free, and now it is also visible.** Skipping the cycle costs nothing, but the job still records a normal `ok` on every beat — so from the outside a heartbeat that is checking nothing is indistinguishable from a healthy one, and the only trace was a debug log line. Settings → **Scheduling** says it in words: the tasks it can actually see in `HEARTBEAT.md`, or a notice that there are none. The same panel names a task that has not been carried out for several cycles, and whether Jenny has already told you about it.

**Heartbeat has one schedule for the whole file.** Every line under `## Active Tasks` is looked at on the same 30-minute beat; there's no per-task cadence, and adding a second heartbeat job isn't the way to get one. If a particular check needs its own rhythm — every 10 minutes, or only on weekday mornings — that's a monitor job ([Two modes](#two-modes-one-that-always-speaks-one-that-speaks-only-if-it-has-to) above), which gives you an independent schedule and the same "only speaks if it's worth it" behavior. Heartbeat stays the right home for the shared, ambient checklist.

## `/goal` and long-running tasks

For work that should span many turns — a multi-step research task, an ongoing project, a "keep an eye on this and get back to me" ask — use `/goal <description>` instead of a plain message.

```
/goal Research flight options from Milan to Tokyo for the first two weeks of September and put together a comparison table in the workspace.
```

What changes once a goal is active:

- The objective is pinned into the model's context every single turn (up to 4000 characters), so it survives conversation compaction and idle-timeout summarization instead of quietly falling out of view.
- The usual LLM wall-clock timeout is **disabled** for the whole session while the goal is active, so a turn is allowed to run much longer than normal without being cut off mid-work.
- Only **one** goal can be active per chat at a time. If you try `/goal` again while one is already running, Jenny replies telling you to `/stop` first.
- A goal that sits inactive for **12 hours** expires on its own at the start of the next turn (`JENNY_GOAL_INACTIVITY_TTL_H`, default 12 — see [Environment variables](../reference/environment-variables.md)).
- `/stop` cancels the active goal outright.
- Jenny is expected to close the loop herself by calling her own "mark goal complete" step with an honest recap — whether the goal succeeded, was cancelled, or was redirected. There's no separate "goal progress" view: while a turn is running (goal-related or not) you'll see the same "Agent running" banner with a timer at the bottom of the chat, which survives a page reload if the turn is still in flight.

`/goal` doesn't spin up a separate orchestrator or a hidden process — it's still the same chat, using the same tools, just with the objective kept in view and the timeout removed. If Android kills the app mid-goal, the goal's state lives in the session, so it either resumes on your next message or quietly expires after the 12-hour inactivity window, same as above.

## Subagents (`spawn`)

**Delegation is not the exception, it is how Jenny works.** With `agents.defaults.orchestratorMode` at its default of `true`, the agent you talk to is an *orchestrator*: it can read files, search, schedule, message you and drive subagents, but it cannot write files, run code, or touch the web itself. Anything in those categories reaches a subagent by definition, not because Jenny judged the task big enough. What is left to judgment is *how* the work is split, not whether to split it.

The reason is context. Everything the main agent does stays in your conversation permanently, and a page fetch or a test run is exactly the kind of large, low-value output that would sit there forever. A subagent's tool output lives and dies with the subagent; only its conclusion comes back.

### The six kinds of subagent

`spawn` picks a **type**, and the type decides which tools that agent gets. The split is a safety boundary, not a convenience: whoever reads untrusted web pages is never also the one who runs code.

| Type | For | Notably cannot |
|---|---|---|
| `researcher` | Gathering material online | Run code |
| `writer` | Docs, wiki pages, synthesis from material already gathered | Reach the network at all |
| `coder` | Writing and changing code, running tests | Reach the network |
| `analyst` | Computation, data, charts | Reach the network |
| `sysadmin` | Remote machines over SSH | Use the web, or run code locally |
| `operator` | Everything else — the default | (holds the general subagent toolset) |

Full tool lists and sampling defaults are in the [Tool reference](../reference/tools.md).

### Watching and steering the work

Because a subagent can run for minutes, the chat gives you a **Subagents panel** just above the message box: one card per running job with its type, elapsed time, idle time and current step, plus **Stop** and **Relaunch** buttons and a tap-through detail sheet showing what it actually did. It appears when work starts and disappears when the turn ends. See [Chat basics](chat.md#the-subagents-panel).

Jenny has the same controls from her side: she can check on a subagent's status, send it a correction mid-run ("no, use the other table") without restarting it, relaunch a failed one, and cancel one that's going nowhere. Those tools exist only in orchestrator mode and are never given to a subagent — a subagent cannot drive its siblings.

### How a delegation behaves

- You'll see this happen as a short confirmation in chat, something like *"Subagent [research] started (id: xxxxxxxx). I'll notify you when it completes."* — after that the chat is free for you to keep talking about anything else.
- When the subagent finishes, its result is fed back in as a fresh turn of the main conversation: Jenny reads the outcome and summarizes it for you naturally (the announcement is explicitly told not to mention "subagent" or task IDs in the final reply, so it may just read like an ordinary answer).
- By default up to **3** subagents can run at a time (`agents.defaults.maxConcurrentSubagents`, default `3`), and one slot is always kept free for a short job — so an ordinary delegation is refused once two are already running. Asking for one past the limit gets a plain "concurrency limit reached" reply instead of being queued.
- A subagent is **blind to your conversation** — it only knows what task text it was handed. If a delegated task comes back disappointing, the usual cause is that the task description didn't carry enough context, not that the subagent "misunderstood."
- Subagents use the same model/provider as your main agent and consume tokens like a full turn — delegating is not free.
- `/goal` refuses to start while a subagent is still active on the session.
- `/stop` disowns any subagents running for that chat: their in-flight work is abandoned, and if one finishes anyway after being disowned, its stale result is silently discarded rather than injected into the chat.
- A subagent that goes quiet for longer than `agents.defaults.subagentStallThresholdSeconds` (default 180s) is flagged as stalled. It is never cancelled for you: relaunching is a decision, and a subagent that starts making progress again is un-flagged on its own.
- If the app process dies while a subagent is working, its in-flight work is gone. What survives is a small record of each finished attempt (task, outcome, result summary) under `workspace/subagents/records/`, kept for the last 20 attempts per chat and 7 days — enough to see what happened and replay the work.

## Android notifications

None of the proactive messages above are guaranteed to make a sound — whether a notification fires depends entirely on whether the app is in the foreground at the moment the message is delivered:

- **App in the foreground:** no notification at all — you're already looking at the message as it streams in.
- **App in the background or closed:** a system notification is posted on a dedicated channel named **"Jenny · avvisi"** (the channel name is fixed by the app and not translated). Reminders show a title like `Jenny ⏰ <job name>`, Heartbeat shows `Jenny · monitoraggio`, and anything else uses a plain `Jenny` title. The notification body is the message text collapsed to a single line and capped at **200 characters** (cut off with an ellipsis beyond that).
- Notifications on this channel use high importance (sound and vibration by default), and you can customize or silence that from Android's own per-app notification settings — there is no volume/sound toggle inside Jenny itself. This channel is separate from the silent, persistent "Jenny ✦ online" notification the foreground service keeps up at all times.
- Two notifications from the same source (e.g. the same reminder firing twice in a row) replace each other rather than stacking — you'll only ever see the latest one for that job.
- Tapping a notification opens the app; simply opening the app (bringing it to the foreground) clears any pending Jenny alerts, read or not.
- **The chat message is always there regardless.** Whether or not a notification actually rang, the reply from a reminder, Heartbeat, or a completed subagent is written into the chat exactly the same way — the notification is only ever an added ping layered on top of a delivery that already happened.
- On Android 13 and newer, posting notifications requires the runtime `POST_NOTIFICATIONS` permission, which the app requests automatically the first time it starts. If you deny it, everything above still happens in chat — you simply never get the ringing/vibrating notification for it. <!-- TODO: verify on-device (O-4): confirm exactly when/how the POST_NOTIFICATIONS prompt appears on a real Android 13+ device and what the app does if it's denied and later revisited. -->

## Related pages

- [Memory and Dream](memory.md) — what the `dream` system job (visible in your reminders list) actually does.
- [Telegram bridge](telegram.md) — how proactive deliveries (Heartbeat, reminders, monitors) reach a paired Telegram chat.
- [Troubleshooting](troubleshooting.md) — what to check when a reminder never arrives, and how to tell a silent monitor apart from a broken one.
- [Slash commands](slash-commands.md) — full reference for `/goal`, `/stop`, and the rest.
- [Android permissions](../reference/android-permissions.md) — the full permission table, including `POST_NOTIFICATIONS` and battery-optimization exemption.
- [Configuration reference](../reference/configuration.md) — `gateway.heartbeat.*`, `agents.defaults.timezone`, `agents.defaults.maxConcurrentSubagents`, and related keys; the anti-doze knobs are under [`power`](../reference/configuration.md#power).
- [Environment variables](../reference/environment-variables.md) — `JENNY_GOAL_INACTIVITY_TTL_H` and other `JENNY_*` knobs.
