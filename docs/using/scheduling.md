# Scheduling and proactivity

Jenny can remind you of things once or on a schedule, keep an eye on something in the background and speak only when it matters, work a long task across many turns, and delegate side work to a helper agent — all set up by asking in chat. The workshop shows your scheduled jobs, and lets you pause, resume or delete the ones you created.

## The one thing to know before you rely on this

**Everything in this page lives inside the app's own gateway process.** There is no server in the cloud keeping time for you. If Android kills the app (or you swipe it away, or the battery optimizer freezes it) at the moment a reminder was supposed to fire, here is what actually happens:

- A **one-shot reminder** ("remind me at 6pm") whose time passed while the app was dead now fires **late, once**, shortly after the app comes back. Before 0.6.6 it was lost forever and silently: recomputing the next run of a one-shot whose time had already passed came back empty, and the job sat there enabled but unrunnable — never retried, never reported missed, nothing telling you it hadn't happened. Late delivery is deliberate: for a reminder, hours late beats never. The same holds when the app is up but the reminder could not be delivered — the provider failed, or Jenny was not set up yet: the one-shot stays and is retried every five minutes, for up to a day past its time, and only then is it dropped, with an error in the log.
- A **recurring reminder** ("every 30 minutes", "every day") keeps its deadline across restarts, and *does* catch up: a daily reminder that came due at 9am while the app was dead fires shortly after the app comes back, not 24 hours later. Before 0.6.0 this was not the case — every restart reset the interval to "now + interval", so on a phone that restarts the app often, a long interval could go indefinitely without ever firing. The same fix covers the built-in jobs below.
- **Catch-up is not the same as punctuality**, and until 0.6.6 it wasn't even close. A foreground service keeps the *process* alive, not the *processor*: with the screen off the phone suspends, and the timer a job was sleeping on stops advancing along with the CPU. It didn't run late because anything was slow — the clock had stopped. On a test device a 30-minute job was observed firing between 30 and 83 minutes apart for exactly that reason.

### What 0.6.6 changed, and what it didn't

Two mechanisms address the frozen-clock problem directly:

- **The deadline now lives on Android's clock, not on Jenny's.** Alongside the in-process timer, the scheduler asks the OS to wake the phone at the next job's real deadline (`power.alarmDrivenCron`, on by default). An OS alarm fires through Doze; a suspended timer does not. So a due job no longer has to wait for the phone to wake up on its own.
- **The CPU is held awake around the work itself.** By default (`power.keepAwake: "turns"`, shown as **While working** in the workshop's **Brain → Background activity → Keep the CPU awake**, where the other choices are **Never** and **Always**) Jenny takes a wake lock for the duration of a turn, a scheduled job or an SSH command, and releases it straight after. A change there applies from the next Jenny restart, and the same card shows whether the CPU is being held awake right now. Without it a job could fire on time and then freeze halfway through — mid-provider-call, mid-tool, mid-write.

And when the process is killed rather than merely frozen, several nets try to bring it back: a self-chaining watchdog alarm (`power.watchdogEnabled`, base 15 minutes, spaced out in Doze), a 15-minute periodic worker, an 8-hourly alarm-clock wake-up, plus opportunistic restarts when the network returns or you open the app. The gateway also comes back by itself after an app update, which it previously did not — it used to stay down until you next opened Jenny by hand.

What none of that fixes, and you should plan around:

- **A missed cron-expression occurrence is still dropped.** A `0 9 * * *` job that came due while the app was dead is recomputed from now, so that morning's run is skipped without a word; the next one arrives normally. Only one-shot and interval schedules catch up.
- **A recovered one-shot arrives with no sense of how late it is.** It fires whenever the app next comes up — hours or days after the fact — and the message is the one you wrote, unchanged. If that would be worse than silence for a particular reminder, a one-shot is the wrong tool for it.
- **After a reboot, nothing runs until you unlock the phone.** Jenny's workspace, config and runtime live in storage that Android keeps encrypted until the first unlock, so the gateway cannot start before it — deliberately, since the alternative is keeping your API keys and memory outside that encryption. A phone that reboots at 3am and sits locked until 8 is a phone with a five-hour hole in it.
- **Exact alarms can be switched off.** If Android's "Alarms & reminders" permission isn't granted, Jenny falls back to inexact alarms: they still fire in Doze, but they slip. The workshop's **Brain → Background activity** tells you which of the two you're getting, and offers a button to grant the permission.
- **Your phone's own battery manager outranks all of it.** Samsung, Xiaomi/MIUI, Huawei/Honor, Oppo and Vivo kill background apps on their own terms, and no application code can prevent it. What Jenny can now do is *notice*: a stretch of downtime longer than `power.gapWarningMin` (default 60 minutes) is recorded and listed under the workshop's **Brain → Background activity**, with the manufacturer-specific advice for turning the restriction off.

So: intervals are still a floor, not a promise — but the floor moved a long way up. Measured on the development phone (Unihertz Titan 2, Android 16), unplugged and idle for nine hours overnight, with both the battery exemption and the "Alarms & reminders" permission granted: a 30-minute job fired 19 times in a row and **every single interval landed between 30m00s and 30m02s**, including right through an uninterrupted four-hour stretch of deep Doze with no maintenance windows at all. The gateway was never killed and never restarted; the battery went from 80% to 77% over 9.4 hours.

Read that for what it is: one phone, in the configuration where everything is granted. It says nothing about a phone where you grant neither permission, and since nothing killed the gateway during the run, the watchdog's repair path was never actually exercised. Your manufacturer's battery manager remains the variable nobody's code can control.

If reminders matter to you, the practical measures are unchanged:

- Grant the battery-optimization exemption Jenny offers during first-run setup (or later from the workshop's **Brain → Background activity**, or from Android's own battery settings) so the OS is less likely to freeze the background service.
- Keep the phone charged and connected when a reminder is close to due.
- Treat one-time reminders as best-effort, not guaranteed alarms — for anything truly time-critical, use your phone's own alarm clock as a backup.

## What you can schedule (the `cron` tool)

You don't create these from a screen — you just ask, in plain language, and Jenny translates it into a scheduled job:

- "Remind me to take the pizza out in 20 minutes."
- "Remind me to renew the passport in March."
- "Every day at 9am, ask me how I slept."
- "Every 2 hours, check if my site is up and tell me only if it's down."
- "List my reminders."
- "Cancel the pizza reminder."

Everything Jenny does for you later is one of **two kinds of job**, and both live in the same list (`cron/jobs.json` in the workspace):

| Kind | How you'd phrase it | Behavior |
|---|---|---|
| One-time (`date`, and `time` if you said one) | "remind me at 6pm", "in 20 minutes", "on 3 March", "around March" | Fires once, then the job deletes itself. With a time, it fires at that time; with only a day it rings at **09:00** that day, and with only a month at 09:00 on the 1st. The workshop shows a day-only job as a date with no time, and a month-only one as "March 2027". Cannot be a monitor (see below). |
| Recurring, by interval (`every_seconds`) | "every 30 minutes", "every 6 hours" | Repeats forever at a fixed interval, counted from when the job was (re)armed — not from a fixed clock time. |
| Recurring, by clock (`cron_expr`) | "every day at 9am", "every Monday at 8" | Standard 5-field cron syntax (e.g. `0 9 * * *`); accepts an optional IANA timezone (e.g. `America/Vancouver`) for that one job. |

The timezone used when you don't specify one is your **device's timezone**, resolved once when the app starts (falls back to UTC if the device timezone can't be determined). A timezone override only applies to cron-expression schedules — one-time and interval schedules always use the device timezone.

When a reminder fires — a plain reminder, that is, the default mode described in [Two modes](#two-modes-one-that-always-speaks-one-that-speaks-only-if-it-has-to) below — it runs as a completely normal agent turn in the chat it belongs to, seeded with an instruction along the lines of "the scheduled time has arrived, execute this job and report the result." The reply lands in your chat exactly like any other message from Jenny — and, if the app isn't in the foreground at that moment, it also rings as an Android notification titled with Jenny's name, "⏰ Reminder" and the job's name (see [Notifications](#android-notifications) below). If a reminder fires while you're already mid-conversation with Jenny on that same chat, it politely waits and delivers once your current turn is idle, rather than interrupting it.

A few smaller things worth knowing:

- A reminder job cannot schedule further jobs from inside its own execution — this only matters if you ask Jenny to "set up a reminder that then sets another reminder" in one step.
- Jenny gives the job a short name in your language when she creates it (it is what the notification title shows). A job without a name has none: its notification title is just "⏰ Reminder".
- You can list, pause, resume and remove jobs by asking (e.g. "what reminders do I have?", "pause the rain check", "remove job xyz"). You create them only by asking.
- In the workshop, **Hands → When she acts on her own** lists them too, and tapping one opens its detail with **Pause** and **Delete**. On a paused one the buttons are **Resume** and **Delete**. Pausing needs no confirmation because resuming undoes it. Deleting asks first. A resumed job counts from the moment you resume it and does not catch up on the runs it missed: an hourly one runs an hour later, a daily 8:00 one at the next 8:00. A one-time job whose time passed while it was paused can only be deleted, because resuming it would fire it at once, late. System jobs have no buttons, since their switch lives in their own settings. There is no "run now": that would start an agent turn, which costs tokens and may message you.
- Jenny still sees a paused job when she lists them, marked as paused, so she won't set it up again thinking it's gone.

### Two modes: one that always speaks, one that speaks only if it has to

Every recurring job also has a **mode**, and it isn't something you type — you describe what you want and Jenny picks it. The difference is whether a run that has nothing to say still writes into your chat.

| Mode | How you'd ask for it | What each run does |
|---|---|---|
| Reminder (default) | "Remind me to take the pizza out in 20 minutes", "Every day at 9am, ask me how I slept", "Every 2 hours, tell me the temperature" | Runs in the chat it belongs to and **always** replies — even when the answer amounts to "nothing to report". |
| Monitor | "Every 10 minutes check if my site is back up and tell me when it is", "Keep an eye on that page every hour and only ping me if the price drops", "Check the backup job each morning and only tell me if it failed" | Runs out of sight and stays quiet, unless the check actually turns up something worth your attention. When it does, it messages you deliberately. |

The wording that tips Jenny toward a monitor is *only* / *just if* / *let me know when it changes*; the wording that keeps it an ordinary reminder is asking to be told something at a time, full stop. If she guesses wrong, say so ("no, only write to me if it's actually down") and she can recreate the job the other way.

A monitor runs in a private session of its own, kept apart from your conversation. That has two consequences worth understanding:

- Your chat doesn't fill up with hourly "still down" replies you never asked to read.
- The job remembers its own previous checks, and that memory is precisely what lets it tell you **when something changed** instead of repeating the same sentence every cycle. That private history is trimmed to the last handful of checks, so it can't grow without bound.

When a monitor does decide to speak, the delivery is proactive: it reaches the WebUI chat and, if you've paired a Telegram bot, that chat too — not just whichever one you happen to be looking at. A notebook's monitor speaks in that notebook's chat, and its Telegram copy starts with the notebook's name in brackets (`[plants] …`) so you can tell where it comes from.

**A monitor may stay silent forever, and that is the feature working, not failing.** "I set up a check and never hear anything" is the expected outcome when nothing noteworthy ever happens. To confirm it's alive, ask Jenny to list your reminders: a run that had nothing to report is recorded as `Last run: … — silenced`. That is the healthy line to see; `error` is the unhealthy one. A monitor's run is silent by construction — what Jenny writes as its answer is delivered nowhere, and the only way it reaches you is Jenny sending you a message on purpose — and she has a `nothing_to_report` step to *declare* the silence, so that a small model doesn't express "nothing to say" by sending you a placeholder word.

**A check that couldn't run is not the same as a check that found nothing**, and Jenny keeps them apart. When a cycle can't actually perform its check — a script is missing, a device is unreachable, a tool broke — the run is recorded as `could_not_check` rather than `silenced`, and the listing adds a line like `Could not check: 3 consecutive run(s), since …`. She still says nothing the first couple of times, because a check that fails once is usually just a network blip. After **three cycles in a row** with no check at all she writes to you once, in plain terms, and then goes quiet again until something changes. So a monitor that has quietly broken can no longer look exactly like a healthy one, and a healthy one still costs you nothing.

**A check handed to a helper waits for the helper.** Jenny can't run code or read the web herself, so a monitor that needs to often hands its check to a [subagent](#subagents-spawn). That run is recorded as `delegated` (in the workshop, "handed to a mini-me") until the helper's result comes back; Jenny judges it then, and the run becomes `silenced`, `ok` or `could_not_check` like any other. A verdict that never comes back is closed by the next run.

**Silence saves you the notification, not the tokens.** Every monitor cycle is still a real agent turn against your provider — the check has to actually run before anything can conclude there's nothing to say. A monitor every 10 minutes is ~144 turns a day whether or not it ever speaks a word, so pick the widest interval that still catches what you care about, and remove the job once the thing you were watching is settled.

Monitor mode only makes sense on a repeating schedule, so **it cannot be combined with a one-time job** ("remind me at 6pm"): a single firing that might decide to say nothing would simply never reach you. Jenny refuses that combination outright rather than schedule something that can silently do nothing.

### Conditions on an app's value

When what you are waiting for is a number one of your [apps](board.md#app) already reads — the soil humidity of a plant, a price, a sensor — a recurring job can carry a **condition** on it: "every 6 hours, check whether the basil's humidity is below 15 and tell me". At each run the app is read and compared **in code**, before any model call: while the condition is false the run ends there and costs nothing. When it becomes true, one turn starts with the value just read, and the job then stays quiet until the condition goes back to false — you are told once, not every six hours. The cadence is yours: if you don't say how often to check, Jenny asks. The workshop shows the condition in front of the schedule, like "humidity < 15 · every 6 hours".

### Notebook crons

A job belongs either to the personal chat or to a [notebook](projects.md), and inside a notebook to **one card** or to the **whole notebook**. **Jenny decides which** from what you asked, without asking you: tied to the card it is about, or to the whole notebook when it concerns all of it, several cards or nothing on the board. You can ask for a notebook's job from any chat. It runs inside the notebook (it can write only there, like the notebook's chat), delivers in the notebook's chat with the usual notifications when it has something to say — and, if you've paired a [Telegram](telegram.md) bot, a copy there too, starting with the notebook's name in brackets (`[plants] …`), reminders included — and shows on the notebook's [board](board.md). It may also point to the notebook page that explains why it exists, and then the message says what is due *and why*, from that page. In a notebook, *list* and *remove* see only that notebook's jobs. However it is removed — from the board, from the workshop, from any chat — what it left on the board goes with it. The personal chat's own jobs stay where they are.

### Protected system jobs

When you ask Jenny to list reminders, you'll also see jobs you didn't create: **`dream`** and — when they are on — the [gardener](gardener.md), the notebook clock (`reminders`) and the update check. These are system-managed and will show up as protected — visible for inspection, but Jenny will refuse to remove them if asked (a removal attempt gets a reply along the lines of "this is a protected system-managed cron job" and cannot be removed). The way to stop one is its config switch, not the reminder list: Dream's and the gardener's are in the workshop's **Memory** drawer, the notebook clock's and the update check's are in `config.json`.

| Job | Runs | Config | What it costs you |
|---|---|---|---|
| `dream` | every **2 hours** | `agents.defaults.dream.enabled` (default on), `agents.defaults.dream.intervalH` (default `2`) | One agent run — a real turn against your provider, several calls if it uses tools — whenever there is new conversation to consolidate. Takes a snapshot first, so a bad run is undoable. |
| `gardener` | every **30 minutes** (how often it *looks*) | `agents.defaults.gardener.enabled` (default on), `agents.defaults.gardener.intervalMin` (default `30`) | Nothing when no notebook has new journal lines, or none has been quiet long enough; otherwise one pass on that notebook, which is a real turn against your provider. See [The gardener](gardener.md). |
| `reminders` | every **15 minutes** | registered when `wiki.enabled` is on (default on) | Nothing from your provider: it runs the apps' readers, notes when an app placed on a board changes state, and clears cards removed more than a week ago. Despite the name it announces nothing — a notebook's reminders are ordinary jobs in the list above. See [Reminders and the notebook clock](board.md#reminders-and-the-notebook-clock). |
| `update_check` | every **24 hours** | `updates.enabled` (default on), `updates.checkIntervalH` (default `24`) | One small HTTP request to the update manifest; a real turn only on the day a new version is first announced in chat (`updates.notifyInChat`). See [Updates](webui-tour.md#updates). |

`dream` runs the memory-consolidation pass, described in [Memory and Dream](memory.md); `gardener` is described in [The gardener](gardener.md).

Earlier versions also had a **heartbeat**: a checklist in `HEARTBEAT.md` that one built-in job went through every 30 minutes. It is gone. Each of those checks is now a recurring job of its own, usually a monitor, with its own interval — ask Jenny to set one up for each line you still want. A `HEARTBEAT.md` left in your workspace is no longer read.

### If the reminder list itself gets damaged

Your reminders live in one file, `cron/jobs.json` inside the workspace, and a phone can leave a file unreadable — storage trouble, or the system killing the process mid-write.

Since 0.6.6 that file is handled the same way as `config.json`. Jenny keeps the previous good copy as `cron/jobs.json.bak` and refreshes it before every save. If the live file can't be read at startup, the backup is used and promoted; if there's no usable backup either, the unreadable file is set aside as `cron/jobs.json.corrupt-<timestamp>` and Jenny starts with **no reminders at all**. Either way the app comes online, and the workshop shows a notice at the top of its drawers saying which of the two happened and where the broken file went.

That notice matters more here than it does for settings. A reminder that has stopped existing looks exactly like a reminder that hasn't come due yet, so without being told, you'd find out when it didn't go off. If you see the "started with none" version, your own reminders need recreating — the system jobs above come back on their own.

The workshop's **Hands → When she acts on her own** is where you check the outcome: it lists your jobs, and repeats the recovery notice above the list itself, because further down the screen a short list of reminders looks exactly like a correct one. See [Settings → When she acts on her own](../reference/settings.md#when-she-acts-on-her-own).

The single case where Jenny still refuses to start is when the broken file can't be moved aside at all. Starting anyway would mean the next save overwrites it, and that file is the only copy of your reminders left.

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

### The eight kinds of subagent

`spawn` picks a **type**, and the type decides which tools that agent gets. The split is a safety boundary, not a convenience: whoever reads untrusted web pages is never also the one who runs code.

| Type | For | Notably cannot |
|---|---|---|
| `researcher` | Gathering material online | Run code |
| `writer` | Docs and synthesis from material already gathered, outside a notebook | Reach the network at all |
| `coder` | Writing and changing code, running tests | Search or read the web (its code can still call a public site through `http_get`/`http_post`) |
| `analyst` | Computation, data, charts | Search or read the web (same `python_exec` network as `coder`) |
| `sysadmin` | Remote machines over SSH | Use the web, or run code locally |
| `app-builder` | Writing and testing a new version of one of your apps | Use the web, or publish what it wrote |
| `notebook-editor` | Changing a notebook as you asked: pages, map, board | Reach the network, or run code |
| `operator` | Everything else — the default | (holds the general subagent toolset) |

Full tool lists and sampling defaults are in the [Tool reference](../reference/tools.md).

### Changes waiting for your approval

A helper that has read something from outside — a web page, a search, a download — does not change on its own the files Jenny uses by herself: a skill, a reminder, `USER.md`, her memory. The change is kept as a draft, Jenny tells you there is something to approve, and a card in the home chat shows which file, where the content came from and what would change, with **Install** and **Discard**. Nothing is written until you tap **Install**. If the same helper has also read your personal files, it can send data only to the sites you named in the request. Why, and what this does not cover: [Security model](../internals/security-model.md#subagents-and-untrusted-content).

### Watching and steering the work

Because a subagent can run for minutes, the workshop's Console gives you a **Subagents panel** just above the message box: one card per running job with its type, elapsed time, idle time and current step, plus **Stop** and **Relaunch** buttons and a tap-through detail sheet showing what it actually did. It appears when work starts and disappears when the work is done. The home's chat shows the short version, where each subagent is a *mini-me* of Jenny's: a bubble saying what she is doing ("I sent a mini-me: …"), a short note when she is done, and, if she gets stuck, the reason and a button to continue or start her again. From the bubble you can also stop her, and put back the files she changed. Pressing and holding a bubble opens the workshop. See [Helpers: stop, undo, continue](helpers.md) and [Chat basics](chat.md#the-subagents-panel).

Jenny has the same controls from her side: she can check on a subagent's status, send it a correction mid-run ("no, use the other table") without restarting it, relaunch a failed one, and cancel one that's going nowhere. Those tools exist only in orchestrator mode and are never given to a subagent — a subagent cannot drive its siblings.

### How a delegation behaves

- You'll see this happen as a short confirmation in chat, in Jenny's words — she tells you the job started, and her turn ends there: it does not wait for the subagent, so the chat is free for you to keep talking about anything else. On Telegram that means two messages, the confirmation now and the result later.
- When the subagent finishes, its result is fed back in as a fresh turn of the main conversation (or folded into the turn already running, if you are talking to her at that moment): Jenny reads the outcome and summarizes it for you naturally (the announcement is explicitly told not to mention "subagent" or task IDs in the final reply, so it may just read like an ordinary answer).
- By default up to **3** subagents can run at a time (`agents.defaults.maxConcurrentSubagents`, default `3`), and one slot is always kept free for a short job — so an ordinary delegation is refused once two are already running. Asking for one past the limit gets a plain "concurrency limit reached" reply instead of being queued.
- A subagent is **blind to your conversation** — it only knows what task text it was handed. If a delegated task comes back disappointing, the usual cause is that the task description didn't carry enough context, not that the subagent "misunderstood."
- Subagents use the same model/provider as your main agent and consume tokens like a full turn — delegating is not free.
- `/goal` refuses to start while a subagent is still active on the session.
- `/stop` leaves running subagents alone: it ends the current turn, and each subagent is stopped from its own bubble. `/stop all` disowns every subagent running for that chat: their in-flight work is abandoned, and if one finishes anyway after being disowned, its stale result is silently discarded rather than injected into the chat.
- A subagent that goes quiet for longer than `agents.defaults.subagentStallThresholdSeconds` (default 180s) is flagged as stalled. It is never cancelled for you: relaunching is a decision, and a subagent that starts making progress again is un-flagged on its own.
- If the app process dies while a subagent is working, its conversation is saved every 30 seconds or so as it works, and the home chat offers **Let her continue** from the last saved point, for up to 6 hours (an `app-builder` is the exception: it starts over). Besides that, a small record of each attempt (task, outcome, result summary) stays under `workspace/subagents/records/`, for the last 20 attempts per chat and 7 days. See [Helpers](helpers.md#when-a-helper-is-interrupted).

## Android notifications

None of the proactive messages above are guaranteed to make a sound — whether a notification fires depends entirely on whether the app is in the foreground at the moment the message is delivered:

- **App in the foreground:** no notification at all — you're already looking at the message as it streams in.
- **App in the background or closed:** a system notification is posted on a dedicated channel named **"Alerts"** (**"Avvisi"** on a phone set to Italian: the channel name is an Android string resource and follows the phone's language, not Jenny's). The title starts with the name you gave Jenny (Settings → the bot name; `Jenny` by default) and, after it, what kind of notice it is, in Jenny's language (`agents.defaults.language`): reminders and monitors show `Jenny · ⏰ Reminder · <job name>` (no job name when the job has none), an announcement of a new version shows `Jenny · Update`, a reply delivered in a notebook's chat shows `Jenny · <notebook>`, and anything else uses the plain name. The notification body is the message as plain text — Markdown marks (bold, headings, list bullets, link targets) are removed — collapsed to a single line and capped at **200 characters** (cut off with an ellipsis beyond that).
- Notifications on this channel use high importance (sound and vibration by default), and you can customize or silence that from Android's own per-app notification settings — there is no volume/sound toggle inside Jenny itself. This channel is separate from the silent, persistent "Jenny is running" notification (*"Jenny è attiva"* in Italian) the foreground service keeps up at all times.
- **You can answer from the notification.** An alert carries a **Reply** field — except one from a notebook, whose answer would land in the personal chat instead: what you type there goes to Jenny as an ordinary message in the one conversation, and her answer comes back as a system alert. If she was not running and the reply could not be delivered, a second notification, "Jenny didn't get your message", offers **Send again** with your text kept. The floating mascot has its own bubble; when it already holds a conversation its button reads **Continue in the app** and opens the app on it.
- Two notifications from the same source (e.g. the same reminder firing twice in a row) replace each other rather than stacking — you'll only ever see the latest one for that job. Each notebook has its own notifications, separate from the personal chat's and from the other notebooks'.
- Tapping a notification opens the chat where the message is, even if the workshop was the last thing you had open: the personal conversation, raised over the page you were on, or — for a message delivered in a notebook, a reply or a notebook cron — that notebook, with its board underneath and its chat on top. A notebook deleted in the meantime falls back to the personal chat. Pending Jenny alerts are cleared when their own conversation is actually on screen: tapping one, or coming back to the app while that chat is showing. Opening the app on another page, or on another conversation, leaves them in place.
- **The chat message is always there regardless.** Whether or not a notification actually rang, the reply from a reminder, a monitor, or a completed subagent is written into the chat exactly the same way — the notification is only ever an added ping layered on top of a delivery that already happened.
- On Android 13 and newer, posting notifications requires the runtime `POST_NOTIFICATIONS` permission, which the app requests automatically the first time it starts. If you deny it, everything above still happens in chat — you simply never get the ringing/vibrating notification for it.

## Related pages

- [Memory and Dream](memory.md) — what the `dream` system job (visible in your reminders list) actually does.
- [Notebook board](board.md) — where a notebook's jobs show, and the `reminders` job that keeps its apps fresh.
- [Telegram bridge](telegram.md) — how proactive deliveries (reminders, monitors) reach a paired Telegram chat.
- [Troubleshooting](troubleshooting.md) — what to check when a reminder never arrives, and how to tell a silent monitor apart from a broken one.
- [Slash commands](slash-commands.md) — full reference for `/goal`, `/stop`, and the rest.
- [Android permissions](../reference/android-permissions.md) — the full permission table, including `POST_NOTIFICATIONS` and battery-optimization exemption.
- [Configuration reference](../reference/configuration.md) — `agents.defaults.timezone`, `agents.defaults.maxConcurrentSubagents`, and related keys; the anti-doze knobs are under [`power`](../reference/configuration.md#power).
- [Environment variables](../reference/environment-variables.md) — `JENNY_GOAL_INACTIVITY_TTL_H` and other `JENNY_*` knobs.
