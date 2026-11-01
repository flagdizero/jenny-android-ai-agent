# Troubleshooting

Symptom-first fixes for the app, not the Python package — nothing here needs a terminal, `adb`, or a desktop.

## First: ask Jenny to check her own logs

Before anything else, try asking Jenny directly: *"Check your recent logs and tell me what went wrong."* She can call a built-in diagnostics tool that reads the last lines of the gateway's own runtime log — this is often the fastest way to find out *why* a tool or a provider call failed, especially since Android normally hides this log inside `adb logcat`, which most users can't reach.

Keep in mind:

- The log is an in-memory ring buffer of the last **500 lines** (DEBUG level and up). It is **cleared every time the app restarts** — if the app has been killed and relaunched since the problem happened, that evidence is already gone.
- You can ask Jenny to filter by a keyword (e.g. "check the logs for `android_web`") and how many lines to show (up to 200 at a time).
- Log lines can contain URLs visited and file names — worth keeping in mind before you paste a log excerpt somewhere or share a screenshot.

## "Connection lost, retrying" / the status dot is gray

Both say the same thing: the WebSocket connection between the WebUI and the gateway running on your phone is down. Neither says anything about your internet connection.

- In the home, a line under the conversation reads **Connection lost, retrying** once the link has been down for more than a couple of seconds, and goes away on its own when it is back.
- In the workshop's Console, the dot next to the "✿" line with her name turns gray, and if you try to send a message you get:

```text
WebSocket not connected. Waiting for reconnection...
```

there's usually nothing to actively fix: the WebUI retries forever, with a backoff starting at 3 seconds and capped at 30 seconds between attempts, and reconnects immediately as soon as the app comes back to the foreground. In practice:

1. Just wait a few seconds — it typically reconnects on its own.
2. If it doesn't, closing and reopening the app (or switching away and back) forces an immediate reconnect attempt.
3. If it never reconnects, the gateway itself may have crashed or failed to start — force-stop the app from Android settings and reopen it; if the problem persists, check the logs as described above (once you can reach a working session) or reinstall.

## Chat looks fine but nothing happens after onboarding

If the app loads, the input works, but sending a message never produces a reply (or immediately errors), the most common cause is a provider problem: go to **Settings → Who answers** and confirm a provider is actually configured with a valid key. Providers are added and removed in the workshop, under **Brain → Who thinks**.

Exact errors you might see appended after "Error: " in the chat, and what they mean:

| Error | Meaning |
|---|---|
| `No provider configured. Add a provider in Settings or edit workspace/config.json to set providers.providers[0].` | Onboarding was interrupted before "Launch", or the provider list was later emptied. Add one in the workshop under **Brain → Who thinks**. |
| `Provider '<name>': api_key is required.` | A provider entry exists but its API key field is empty. Edit it in Settings → Who answers and paste the key again. |
| `401` / Unauthorized | The API key is wrong, expired, or was pasted with extra whitespace. Regenerate it on the provider's dashboard and update it in Settings → Who answers. |
| `429` / rate limit | You've hit the provider's rate limit. Wait and retry, or switch to a different model in Settings → Who answers. |
| `404` / model not found | The model ID doesn't exist for that provider — a display name was used instead of the API model ID, or the model was deprecated. Pick a different one in Settings → Who answers. |
| Connection refused | Only relevant if you pointed the provider at a self-hosted endpoint (Ollama, LM Studio, vLLM) — the server isn't reachable from the phone (and outside the phone itself it should be served over HTTPS). See [Local models](../reference/local-models.md). |

Changing the model or provider in Settings applies immediately — there's no restart required to try again.

## An attachment is refused

Attachments are checked twice, and both checks tell you why in plain words rather than dropping the file silently:

- **In the composer**, before anything is sent: more than 4 images, more than 1 video or more than 4 other files in one message, a file over its size cap, or a file the phone couldn't read. The file is not added and the app says which limit it hit (a line in the home chat, a toast in the workshop) ("Too many images in one message", "The file is too large", "I couldn't open this file", …). Attach fewer files, or smaller ones.
- **On the gateway**, after sending: the same limits, plus files it couldn't decode. The chat shows the same kind of explanation, and the message is treated as not sent.

See [Files and attachments](./attachments.md) for the exact limits.

## Reminders and periodic checks aren't firing

If you asked Jenny to remind you about something and nothing arrived, this is almost always about the app being killed, not a bug in the reminder itself:

- **Reminders only fire while the app (and its background service) is alive.** Nothing fires while Android has the app killed. What happens when it comes back depends on the kind of reminder — the reminder list is saved to disk after every job that runs, so none of this depends on the app having shut down cleanly:
- A **one-time reminder** whose time passed while the app was dead is not lost: it fires late, as soon as the app is running again (the device log records how overdue it was). Late is still late — a "remind me at 3pm" can arrive at 5pm if that's when Jenny came back.
- A **recurring reminder on an interval** (e.g. "every 2 hours") keeps its deadline across a restart, so one that came due while the app was dead fires shortly after it comes back rather than waiting a full interval. What you don't get is a replay — the cycles that fell inside the dead window are gone, not queued up one by one.
- A **recurring reminder on a clock time** (e.g. "every day at 8:00") is recomputed from the clock at startup: an occurrence missed while the app was dead is skipped, and the next one arrives on time.
- Doze can stretch things even while the app is alive — treat every interval as a floor, not a promise. Since 0.6.6 Jenny asks Android to wake the phone at each job's real deadline and holds the CPU awake for the length of the run, which is aimed exactly at that stretching; it doesn't make an interval a guarantee.

**Start at the workshop's Brain → Background activity.** This is the page that tells you which of the two problems you have — an app that was killed, or an app that was merely slowed down — instead of leaving you to guess:

- **Recorded outages** lists every stretch of at least an hour (`power.gapWarningMin`) when Jenny was not running at all. Reminders and scheduled jobs due inside one of those windows did not fire. Several outages, especially recurring ones overnight, are the signature of the phone's own battery manager shutting Jenny down.
- **Current state** shows three plain yes/no lines: whether the battery-optimization exemption is in force, whether exact alarms are permitted, and whether the CPU is being kept awake right now. A "no" on the first is the single most useful thing to fix, and the **Exempt from battery** button is on that same page (it's also offered during first-run setup, and re-offered after a system update quietly resets it).
- When an outage has been recorded, the page adds a card saying plainly that this is the phone's battery manager rather than a Jenny fault, with a link to the [dontkillmyapp.com](https://dontkillmyapp.com/) page for your manufacturer and, where the phone allows it, a button that opens the manufacturer's own battery screen. That restriction can only be lifted by hand, there — no amount of app code can work around it.

An empty outage list is genuinely good news: it means Jenny stayed up, and a missed reminder needs a different explanation (for example a monitor that ran and chose to stay quiet — see below).

See [Scheduling and proactivity](./scheduling.md) for the full model, and [Configuration](../reference/configuration.md#power) for the `power.*` keys behind that page.

## A periodic check never writes to me

If you asked Jenny for something like *"every 10 minutes check whether the site is back up and tell me"*, she may have created it as a **monitor**: a recurring job that runs quietly and only messages you when the check actually finds something. A monitor that never speaks is usually working, not broken — so before assuming a fault, tell the four cases apart.

Ask Jenny to **list your reminders** and look at the job's `Last run:` line:

| What you see | What it means | What to do |
|---|---|---|
| `Last run: <recent time> — silenced` | It ran, it looked, there was nothing worth reporting. This is the normal outcome of a healthy monitor: "I set up a check and never hear anything" is the feature working. | Nothing. If you'd rather hear from it every time, ask for a plain reminder instead ("tell me the result every hour, even if nothing changed"). |
| `Last run:` far in the past, or no `Last run:` at all | The app was killed and the cycles in that window simply didn't run — there's no catch-up replay, as described above. | Check the workshop's **Brain → Background activity**: if the window shows up under "Recorded outages", the phone shut Jenny down. Exempt her from battery optimization from that same page and keep the app from being swiped away. |
| `Last run: <time> — could_not_check` (usually with the reason in parentheses), plus a `Could not check: N consecutive run(s), since …` line | The cycle ran, but the check itself never happened — a helper script is missing, a device or host is unreachable, a tool broke. This is the case that used to be invisible: it produced the same silence as a healthy run. | Nothing for the first two cycles; a blip is normal. After three in a row Jenny writes to you once by herself. The reason in parentheses is the fix to chase — most often a script that was moved or a device that is off. |
| `Last run: <time> — error` (usually with the reason in parentheses) | The job genuinely failed — a provider error, a tool that couldn't reach the target. | Ask Jenny to check her logs (first section of this page); fix the underlying cause, or recreate the job. |

Two things that look like faults but aren't:

- **Silence costs tokens anyway.** Every monitor cycle is a real agent turn even when it says nothing — the check has to run before anything can decide there's nothing to report. If you see token usage from a job you never hear from, that's the mechanism, not a leak. Remove monitors you no longer need.
- **A monitor deliberately doesn't answer in your chat.** It runs in its own private session, so you won't find a trail of its checks in the conversation. The only thing it ever puts in chat is the message it decided was worth sending.

If you wanted a one-off check ("at 6pm see whether the deploy finished"), it cannot be a monitor at all — Jenny refuses that combination, because a single run that might stay silent would never reach you. Ask for a plain reminder in that case.

## Telegram bot isn't responding

Telegram only works while your phone (running the app) is alive — the phone *is* the server, there's no cloud component. Check, in order:

1. **Is the app actually running?** If Android killed it, or the screen has been off long enough for aggressive Doze to suspend background work, the bot goes silent. Reopening the app resumes polling immediately. The workshop's **Brain → Background activity** answers this properly: "Recorded outages" tells you whether Jenny was down and for how long, "Current state" tells you whether the battery-optimization exemption is in force, and the **Exempt from battery** button is right there. If an outage matches the silence, see the OEM guidance in [Reminders and periodic checks aren't firing](#reminders-and-periodic-checks-arent-firing) above — the phone's battery manager is the cause, and it has to be told to stop by hand. One case is *not* a fault at all: with the screen off and the phone suspended, an inbound Telegram message can simply sit in the queue until the device wakes, because the long-poll deliberately doesn't hold the CPU awake while it waits. Nothing is lost, but the reply isn't instant — see [Telegram bridge](./telegram.md#battery-exemption).
2. **Was it paired with a group?** Pairing is now accepted only from a private chat, and in the paired chat Jenny answers only messages whose sender is the paired person. A bot paired with a group by an older version therefore stops answering there, silently. Unpair in the workshop's **Hands → Telegram** and pair again from a private chat with the bot — see [Telegram bridge](./telegram.md#a-pairing-made-with-a-group-has-to-be-redone).
3. **Did you burn your 5 pairing attempts?** Pairing a new bot requires sending a 6-digit code, and it's capped at 5 attempts per chat as an anti-brute-force measure — this cap also applies to you if you mistype the code repeatedly. Once it's hit, that chat can no longer pair even with the *correct* code. The fix is to go back to **Hands → Telegram** in the workshop and either unpair or save a new token, which resets the counter.
4. **Is the channel on, and is the bot paired?** The **Channel active** switch keeps the token and the pairing but turns the channel off; **Unpair** clears the pairing and generates a new code. Both are in the workshop's **Hands → Telegram** panel, and a channel that is off says so there.

Messages sent while the app was closed are **not** all answered when it comes back. Of the messages Telegram still holds, Jenny answers only those less than five minutes old; older ones are dropped (with a warning in the device log). A restart a few seconds after you wrote doesn't lose your message; a message sent an hour before the app came back is never answered — send it again.

## Web search shows a CAPTCHA / verification page

`web_search` runs through a hidden Chrome WebView using Bing (it's the only supported search engine), which occasionally shows a CAPTCHA or "verify you're human" page instead of results. When that happens Jenny reports it plainly rather than working around it — there's no bypass. Just try again after a bit, or ask a differently-worded question so the underlying request looks less automated.

## "URL blocked" errors

If a tool refuses a URL with a message like "URL blocked" or "blocked address", that's the SSRF (server-side request forgery) protection working as intended: by default, Jenny's network tools (`web_fetch`, `download_file`, and `python_exec`'s HTTP helpers) refuse to reach private, loopback, link-local, or carrier-grade-NAT addresses — even ones on your own home network or a VPN like Tailscale.

If you deliberately want the agent to reach something on your own private network (e.g. a self-hosted Ollama box, a home NAS), you can add its address range to `security.ssrfWhitelist` in `config.json` (for example `100.64.0.0/10` for Tailscale). This has to be edited in the config file directly — there is no UI control for it. See [Configuration reference](../reference/configuration.md) and [Security model](../internals/security-model.md).

**Don't do this for an SSH host.** SSH targets are checked against their own, looser policy that already allows private LAN ranges (RFC1918), IPv6 ULA, and the CGNAT range `100.64.0.0/10` that Tailscale uses — no whitelist entry is needed, and adding one would widen `web_fetch`, `download_file` and Jenny Apps to the same range for no benefit. If an SSH host is rejected as blocked, the address is one of the ones blocked in *every* policy: loopback, link-local, or `0.0.0.0/8` — all of which resolve to the phone itself. See [SSH access](./ssh.md).

Note that this whitelist only affects the agent's *tools*. It does not affect calls to your configured LLM provider itself — those never pass through the SSRF filter at all, so a self-hosted provider endpoint on a private network is not blocked by this setting either way.

## A notebook's pages show an error (503)

If opening a notebook's pages says the wiki is switched off, the feature has been disabled in configuration (`wiki.enabled: false` in `config.json`). Re-enable it there; the setting is read on every request, so the pages come back without restarting the app. The notebook list itself still shows what is on disk — it reads the folders, not the wiki API.

## A new version doesn't show up, or won't install

Open the home's **Settings → Updates**. It shows the last successful check; if the checks keep starting but never reach the update server, it says so in words, and **Check now** asks the server right away and reports what happened ("No update: you already have the latest version", or that the server could not be reached). The periodic daily check follows `updates.enabled` in `config.json`; **Check now** works even with that switched off.

**Install now** ends in an Android confirmation prompt that only you can accept. If nothing appeared on screen, look for the "Update ready to install" notification and tap it; if you dismissed the prompt, press **Install now** again. Android may also ask once for permission to install from Jenny. See [the tour](webui-tour.md#updates).

## Collecting information before asking for help

If none of the above resolves it, gather this before reporting the problem:

- What Jenny told you after you asked her to check her logs (see the first section above).
- The app version, shown in the workshop under **Brain → System**.
- Your Android version and device model.
- A description of what you did right before the problem appeared, and whether it happens every time.
- Never share your API key — if you need to show a config snippet, redact it first.

## See also

- [Chat basics](./chat.md) for what the online/offline indicator and error banners look like in context.
- [Scheduling and proactivity](./scheduling.md) for the full model of one-time and recurring jobs, the two modes, and their limits.
- [Telegram bridge](./telegram.md) for pairing, throttling, and what does and doesn't work over Telegram.
- [Jenny from your other devices](./remote-access.md) for what to check when another device can't reach the phone.
- [Files and attachments](./attachments.md) for exact attachment limits.
- [SSH access](./ssh.md) for the separate network policy SSH hosts are checked against, and why a restore doesn't restore access.
- [Security model](../internals/security-model.md) for the SSRF whitelist and the workspace policy boundary.
- [Providers and models](../reference/providers.md) for provider setup and connection errors in more detail.
