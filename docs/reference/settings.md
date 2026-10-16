# Settings

Every control in the Settings screens, what it does, and its default value.

Settings lives in two places:

- The home's **Settings** page, for the everyday choices: the theme, **Who answers**, **Jenny**, **Updates** and **Backup**. At the bottom of that page a **Workshop** row ("watch, tune, repair") opens the second place.
- The **workshop**, whose dock has four entries: **Console** and three drawers, **Brain**, **Hands** and **Memory**. Each drawer is a list of named groups, always open, and a few rows in them open a panel of their own when tapped (a provider, an SSH host, Telegram, Skills, Local history, the memory budgets).

There is no global Save button. Most controls save as soon as you change them and confirm with a toast. The theme and the mascot's visibility and size live entirely on the device and never touch `config.json`: those are called out below.

## How saving works

In the workshop, text and number fields (the model parameters, the web search fields) save when the field reports a change, which on a phone means when you confirm or leave the field, and then after a **600 ms** debounce. They do not save on every keystroke. The Dream and gardener numbers save on the same change event, without the debounce. Toggles, segmented controls and the theme save on tap. A successful write shows **"Saved!"** (or a more specific toast). A failed write shows the error instead (the Location toggle only says "Couldn't save"), and toggles roll back to their previous state.

On the home's **Jenny** page, **Her name** and **The rules you gave her** are different: each has its own **Save** button, which appears only when there is something new to save.

Most controls take effect the moment you use them. These ask you to confirm first:

- **Deleting a provider.**
- **Restoring a local snapshot.** Restoring from a backup file asks for no confirmation beyond the file picker and the passphrase; the note under the button says it replaces everything.
- **Unpairing Telegram.**
- **Deleting a scheduled job.**
- **Regenerating an SSH key**, because it revokes the access already installed on the server.
- **Deleting an SSH host.**
- **Accepting a host key fingerprint**, in a dialog showing the fingerprint with Cancel and Accept.
- **Replacing a host key that changed**, which asks **twice**: once in the side-by-side dialog showing the old and new fingerprints, and again in a plain confirmation. That is deliberate: a changed key is treated as a possible man-in-the-middle, not as an update.
- **Setting the Dream review cadence below 12** (see [Memory](#memory)).

**One silent restart:** changing the assistant's name flips a `requires_restart` flag on the backend, but the **Her name** Save button only says "Saved". If you rename her and she still introduces herself with the old name, restart Jenny. The other fields that flip that flag (timezone, bot icon, `tool_hint_max_length`) have no control in either screen.

## The home's Settings page

| Control | Effect | Default |
|---|---|---|
| **Theme** | A row of theme pills, each a three-colour swatch with the theme's short name. Tap one to switch instantly, with no confirmation. The full name sits beside **Theme** at the top of the card, and a one-line description under the row. See [Themes and mascot](../using/themes-mascot.md). | Synthwave '84 |
| **Who answers** | Opens the page where you choose the model. The row shows the provider that answers now. | — |
| **Jenny** (her name, if you renamed her) | Opens her page: name, mascot and rules (below). The page's title is her name too. The row shows how she is now, for example "small · floating". | — |
| **Updates** | Opens the update check and install page. The row shows the installed version, and the new one when an update is waiting. | — |
| **Backup** | Opens export, restore and the local history — and, in the Play Store build, the copy on Google Drive. The row says when you last exported a file, or "never". In the Play Store build, with the Drive copy on, it says how the copy is going instead ("safe · today at 3:12 AM", "no copy for 4 days", "stopped: needs your permission"), and with it off, "only on this phone" until you export a file. | — |
| **Workshop** | Opens the workshop. | — |

There is no language setting. The interface follows the phone's language (Italian or English, English for anything else).

The theme, and the mascot's visibility and size, live in the WebView's `localStorage` on this device. They are **not** part of `config.json` and are **not** included in encrypted backups. Reinstalling the app, or clearing its data, resets them to their defaults.

### Who answers

Model and provider are chosen **together, as one decision**. The page shows one tile per configured provider. Tapping a tile does **not** change who answers: it shows that provider's models, fetched live from its model-list endpoint. Tapping a model saves both `model` and `default_provider` in a single request and applies it to the running agent right away, with no restart and no confirmation dialog.

The model in use is always listed first, even if the provider's list doesn't include it. When the list can't be shown, the page says why: it is still loading, the provider needs a key first, it has no base URL yet (set it in the workshop), or the list didn't arrive. Any other message comes from the backend, in English.

Under the tiles, **Key** shows the masked key of the provider you're looking at, with **Add** or **Replace**. The field starts empty, because the stored key never comes back to the client. **Save** stays off until you paste something, and saving reloads that provider's model list.

There is no field for typing a model ID by hand on this page. You pick an ID from the provider's list, or enter one as the **First model** when you add a provider in the workshop.

### Jenny

| Control | Effect | Default |
|---|---|---|
| **Her name** | The name she introduces herself with (up to 40 characters). Saved with its own **Save** button. See the silent restart note above. | "Jenny" |
| **Mascot size** | Small / Medium / Large: the side of her square, 120 / 160 / 210 px. | Small |
| **Floating mascot** | Only on Android. Puts her in a window above other apps, where tapping her lets you talk and the answer comes in a bubble, in the same conversation. It needs Android's "Display over other apps" permission: if that is missing, the switch stays on and a note explains what to allow. Stored in `config.json` (`floating.enabled`). | Off |
| **The rules you gave her** | Free text (up to 2,000 characters) that she reads every turn and never rewrites. Saved with its own **Save** button. | empty |

She always docks on the right edge. That is not a setting.

### Updates

| Control | Effect |
|---|---|
| **Check now** | Asks the update server right away, whatever `updates.enabled` says. While it runs the button reads "Checking…", and the answer comes as a line under it: no update, the new version, or that the server could not be reached. |
| **What changed** | A link to the release notes, shown only when a newer version exists. It opens outside the app. |
| **Install now** | Shown only when a newer version exists. Downloads it and hands it to Android, which asks you to confirm before replacing the app. A progress line says which phase it is in (downloading, installing, waiting for your confirmation). |

Under the buttons, a line says when the last successful check happened. When the checks keep starting but never reach the server, it says so in words, because otherwise you would never hear about a new version. The whole flow, including what to do if Android's prompt does not appear: [Updates in the WebUI tour](../using/webui-tour.md#updates).

### Backup

In the GitHub and F-Droid build, three rows, all open: **Copy to a file** makes one encrypted file with a passphrase you choose (the row says when you last did), **Restore from a file** replaces everything with the file's contents and restarts Jenny, and **Local history** opens the list of snapshots right below it — tapping one restores it after a confirmation (the same restore as the workshop's, see [Local history](#local-history)).

In the Play Store build, two blocks. The **copy on Google Drive** card: its state in one line, the last seven nights as dots, what's in it, where, and one big button that is always the gesture needed now — **Turn on the Google Drive copy**, **Make a copy now**, **Give Google permission again**, **Type the copy's password**, **Choose a password** or **Find my copy**. Turning the copy on asks for the copy's password (at least 8 characters, twice); it's needed again only on a new phone. **More options**, closed (just its name): **Copy to a file** makes one encrypted file with a passphrase you choose, **Restore from a file** replaces everything with the file's contents and restarts Jenny, **Local history** opens the list of snapshots right below it, and tapping one restores it after a confirmation (the same restore as the workshop's, see [Local history](#local-history)), **The copy's password** changes it (the old one isn't needed), and **Turn off the Drive copy**. Full details, including the irrecoverable-passphrase warning: [Backup and restore](../using/backup.md).

## Brain

### Who thinks

The configured providers, one group each, with their models inside. One tap on a model makes it answer: it saves `model` and `default_provider` in a single request, the same write the home's **Who answers** page makes, so a model can never end up active under another provider's name.

Each group's header shows a coloured dot, the name, and a second line with the format, the base URL (or "(default)" if unset) and the masked key. The key hint is the first 4 and last 4 characters of the stored key, joined with `...` (e.g. `sk-a...j8f9`); the full key is never sent back to the browser. The provider that answers now carries an **answers** tag, and when its group is closed the second line says **answers with** and the model instead. A closed group also shows how many models its list has.

Tapping the header opens or closes the group; on entering the page only the provider that answers is open. The models are fetched live from the provider's model-list endpoint and kept until the key or base URL changes (which fetches them again) or the app is reopened. A list that didn't arrive is tried again the next time you enter the page. The one in use sits at the top with a check mark, and the order is fixed when you enter the page, so picking a model moves the mark, not the rows. Six models show at first, with **Show all** below them and a filter field once a list is longer than that. **A model the list doesn't have** takes a model id typed by hand, for one the provider serves but doesn't list; since a wrong name would leave her unable to answer, it asks before using an id the list doesn't have. While a choice is being saved the rows are disabled, and tapping the model already in use does nothing. If the list can't be fetched, the group says why, including the server's message when there is one (a CA certificate problem shows up here).

The settings button beside each header opens the provider's panel: its format, address, key and CA certificate, with **Edit** and **Delete**.

**Add provider** opens a dialog with Name, Format (OpenAI Compatible / Anthropic Compatible), API Key, Base URL, CA certificate, a **Use it now** switch (on by default) and, while the switch is on, **First model**. The base URL placeholder follows the format (`https://api.openai.com/v1` for OpenAI-compatible, `https://api.anthropic.com` for Anthropic), and the address must start with `http://` or `https://` — anything else is refused before saving, and a capitalized scheme is stored in lower case. With **Use it now** on, saving also makes that provider and its first model the ones that answer; with it off there's no First model to fill in, and you pick the model when you switch to that brand. If that second step fails, the provider stays saved and a toast says it was not activated.

**CA certificate** is only for a server whose certificate is signed by your own CA: the path to a PEM file to trust *on top of* the default roots, relative to the workspace unless absolute. Installing that CA on the phone does not help, because the Python runtime inside the APK has its own trust store. A path that can't be read is refused on save, naming the file, rather than being accepted and quietly ignored. Details: [Self-signed certificates](./providers.md#self-signed-certificates).

The UI refuses to delete the last remaining provider ("Cannot delete the last provider"). That check is client-side only: the backend has no equivalent guard, so it protects you inside the WebUI, not as a data-level rule.

**Editing a provider.** The Edit dialog leaves the API Key field **empty** and shows the masked hint as its placeholder, with a note saying to leave it blank to keep the stored key. Blank means exactly that, so you can change the Format or the Base URL without retyping the key. The name can't be changed, and the dialog has no First model field.

### Parameters

| Field | Range | Default |
|---|---|---|
| Context window | 65,536 or 262,144 tokens (the list comes from the server) | 65,536 |
| Max Tokens | integer ≥ 1 | 32000 |
| Temperature | 0.0 – 2.0 | 0.1 |
| Reasoning Effort | empty / low / medium / high | medium |

Max Tokens is the ceiling for a single reply. On a reasoning model the thinking counts against that same budget, which is why the default is 32000 (as in OpenCode) rather than the 8192 and then 16384 of earlier versions: a turn that planned at length could spend the whole allowance before saying anything. What is sent is never more than a quarter of the context window, so a model with a small window keeps room for the prompt. Reasoning Effort defaults to `medium` instead of leaving the provider to decide, for the same reason: an unbridled reasoning model will happily spend the entire output budget thinking about an open-ended task.

A rejected value comes back as an error rather than being silently clamped: Max Tokens must parse as an integer of at least 1, and Temperature must land inside 0.0–2.0. Temperature accepts a comma as the decimal separator, since that is what an Italian-locale number input produces.

Saving any of these also rebuilds the provider when needed, so the new value applies to your very next message. Nothing needs restarting.

Reasoning Effort has values beyond the four in the select. The API accepts `none` (disable thinking explicitly) and `minimal` (plus `minimum`, a DashScope-native alias normalized to `minimal`), so those reach `config.json` if you write them through the endpoint; the select simply doesn't offer them. `adaptive` (Anthropic adaptive thinking) is the exception: the endpoint **rejects** it, so it can only be set by editing `config.json` directly. The select can't represent a value it doesn't list: if `config.json` holds `adaptive`, the field renders blank, and touching it replaces the value. <!-- verified in code: jenny/webui/settings_api.py (_parse_max_tokens / _parse_temperature / _parse_reasoning_effort) + jenny/webui/settings_routes.py (provider rebuild) + jenny/providers/anthropic_provider.py (adaptive) -->

You can still set these per-model instead of globally, by defining a [model preset](configuration.md) with its own override and switching to it with `/model`.

### Background activity

Everything about Jenny surviving a screen that's been off for hours. The group ends with a line pointing out that Dream and the gardener live in **Memory**.

| Control | Effect | Default |
|---|---|---|
| **Exempt from battery** | Opens Android's own "ignore battery optimizations" prompt. Once granted, the button is replaced by a confirmation line rather than disappearing. The same request appears during first-run setup and in the Telegram panel, and is offered again when a system update has silently reset it. Shown only in the Android app. | Not exempt |
| **Keep the CPU awake** | The `power.keepAwake` mode, as three buttons: *Never* (best battery, scheduled work can slip by hours), *While working* (recommended: awake for a turn, a cron job or an SSH command, then released), *Always* (nothing slips, drains battery constantly, for a phone on charge). The cost of the selected mode is written under the buttons. **Takes effect at the next Jenny restart**, which the UI says under the control: the service-lifetime lock is taken once, at startup. | While working |
| **Current state** | Three yes/no lines: battery optimisation exemption, exact alarms, CPU kept awake right now. Refreshed when you come back from a system dialog. When exact alarms are not permitted, an **Allow exact alarms** button opens the system screen that grants them. | — |
| **Recorded outages** | The last few stretches of at least `power.gapWarningMin` (default 60) minutes when Jenny was not running, with duration and date. Empty is the healthy state. When the list isn't empty, a card explains that the phone's battery manager is the cause, with a link to dontkillmyapp.com for your brand and, where the phone allows it, a button that opens the manufacturer's battery screen. | Empty |

Only the wake-lock mode is editable here. The rest of the `power.*` family (wake-lock rotation, the restart watchdog, alarm-driven cron, the alarm-clock fallback, the outage threshold) is `config.json`-only; see [Configuration](./configuration.md#power). Outside the Android app the group still shows the wake-lock mode, which lives in the gateway's config, but not the battery exemption card.

### System

| Item | Shows |
|---|---|
| Version | The installed app version string. Checking for and installing updates is on the home's **Updates** page. |
| Token Usage | 7 local usage statistics. See below. |

Seven statistics, all computed from data stored locally on the device (no external usage-tracking service):

| Stat | Meaning |
|---|---|
| Total Tokens | Lifetime total. |
| Last 30 Days | Tokens used in the trailing 30 days. |
| Last 365 Days | Tokens used in the trailing 365 days. |
| Peak Day | The single highest-usage day on record. |
| Current Streak | Consecutive days with at least one turn. |
| Active Days (30d) | Number of days used out of the last 30. |
| Requests (30d) | Number of LLM requests in the last 30 days. |

If no usage has been recorded yet, the block shows "No usage data yet" instead of zeros.

## Hands

### Web Search

| Field | Range | Default |
|---|---|---|
| Search engine | dropdown | bing |
| Max results | 1 – 10 | 5 |
| Timeout (sec) | 1 – 120 | 30 |
| Fetch max chars | 1000 – 200000 | 50000 |

The Search engine dropdown looks like a choice but has exactly one working option: the on-device web search tool only supports Bing, and the backend rejects any other value. All four fields save together.

### Location

A single toggle, **"Share my location"**, default **on**. Its hint explains the model: a recent last-known position is injected into the conversation context on every message (free, no GPS fix), and a precise fix is only requested on demand. It applies immediately on toggle, with a toast confirming "Location enabled"/"Location disabled" and a rollback if the request fails. Switching it on also asks Android for the permission, in the same tap.

The toggle records your preference; it does not grant the Android permission, and both have to be satisfied for location to reach the agent. When the toggle is on and Android has not allowed Jenny to use the location, the group says so with a warning notice and an **Allow location** button. The button asks Android for the permission, and if Android will no longer ask (it was denied for good), it opens Jenny's page in the system app settings instead. The notice disappears as soon as the permission is granted, and it only appears in the Android app — a browser has no permission to ask for.

Two related values exist only in `config.json`, with no UI control: `tools.location.telegram_ttl_s` (default 3600, how long a location shared from Telegram stays valid) and `tools.location.fresh_timeout_s` (default 15, how long Jenny waits for a fresh GPS fix). See [Location](../using/location.md).

### SSH

The two decisions that cannot be delegated to the agent: **which machines exist**, and **which host key is the right one**.

| Control | Effect | Default |
|---|---|---|
| **Enable SSH access** | Master switch (`tools.ssh.enable`). Off means the agent has no SSH tools at all; the host list stays visible and editable so you can fix things with the switch down. | **Off** |
| **Add host** → Alias | The only name the agent ever uses for this machine, and also the name of its key file. 1–32 chars, `A–Z a–z 0–9 - _`, must start alphanumeric. **Cannot be changed later**: there is no rename. | — |
| **Add host** → Host / Port / User | Address, port and login account. | port 22 |
| **Add host** → Description | Free text, shown **to the model** so it can pick between machines ("the home NAS"). Not decoration. | empty |
| **Add host** → Authentication | `ed25519 key` or `Password` (`auth`). Existing hosts stay on key. | **ed25519 key** |
| **Add host** → Password | Only shown with `Password` selected. Required: saving a password host with an empty password is refused, so you can't end up with a host that looks configured and fails on the first command. Blank when editing (the saved password is never sent back to the screen), and blank means "keep the saved one". Switching the host back to key **deletes** the stored password. | none |

Each host is one row: the alias, `user@host:port`, and two marks, a filled or empty circle with a word. The first is the credential (**key** or **password**, depending on the authentication mode) and the second is the **fingerprint**. Both have to be filled before the agent can connect.

Tapping a host opens its panel:

| Control | Effect |
|---|---|
| **Generate key** / **Regenerate key** | Creates an ed25519 pair *for that alias* on the device and shows the public line to paste into `~/.ssh/authorized_keys`, with a **Copy** button. Regenerating asks for confirmation, because it revokes the access already installed on the server. Hidden on a password host, along with the public key, since there is nothing to install there. |
| **Verify fingerprint** → **Accept** | Reads the key the host presents (without authenticating), shows its SHA256 fingerprint, and pins it on acceptance. |
| **Edit** (pencil icon) | Reopens the host dialog to change host, port, user, description, or authentication mode. The alias is the one field that cannot change. Editing the address or port **clears the accepted fingerprint**; see below. |
| **Delete** (bin icon) | Removes the host **and** its private key, its public key and its accepted fingerprint. |

Five behaviours worth knowing before you use this screen:

- **No restart, in either direction.** Switching SSH *on*, or adding your first host, takes effect on the next job: the `sysadmin` subagent builds its tools from the current configuration each time one starts. Switching it *off* applies immediately, even to a subagent already working on a server: that is the emergency stop. See [SSH](../using/ssh.md#no-restart-needed).
- **There is no trust-on-first-use.** Until you have accepted a fingerprint, every SSH call for that alias fails and tells the agent to ask you. A fingerprint reading older than 10 minutes is refused and has to be taken again.
- **Pinning is required in both authentication modes, and matters more with a password.** With a key, an unverified host gets a signature it can't reuse; with a password, it gets your password. The fingerprint dialog says so explicitly on a password host. There is no way to skip the step in either mode.
- **A changed host key is treated as an attack, not an update.** If a host presents a key different from the one you accepted, Jenny shows both fingerprints side by side and requires a second explicit confirmation to replace it.
- **Editing the address or port of an existing host clears its verified fingerprint** (and forgets the `known_hosts` line), because a verification of the old address says nothing about the new one. You have to verify again.

The private key and the `known_hosts` file live **outside** the workspace, so they are not in snapshots and not in an encrypted backup: after a restore you have to generate new keys and install them on each server again, and the group says which hosts need it. A **password** is stored in `config.json` like the Telegram token and the API keys, unencrypted at rest, inside the workspace and therefore inside backups. That's the trade: more convenient, weaker, and a dedicated key can be revoked without touching the password you log in with yourself. One field has no UI at all: the per-host `jobLogDir` (default `/tmp/jenny-jobs`), which is `config.json`-only. Full walkthrough: [SSH access](../using/ssh.md).

### Telegram

One row shows the state (for example "paired with …", "on, not paired" or "off"). Tapping it opens the pairing panel, the same widget used during onboarding: paste a BotFather token, get a 6-digit pairing code, send it to the bot from Telegram. The panel also has the **Channel active** switch, **Change token** and **Unpair**, which asks first. Changes apply immediately, with no app restart. Full walkthrough: [Telegram bridge](../using/telegram.md).

### Skills

One row, **What she knows**, counts the skills that come with the app and the ones that are yours. Tapping it opens the list in two blocks. You can switch your own skills on and off there; the built-in ones can't be turned off. To create, change or delete a skill, ask Jenny in chat.

### When she acts on her own

Your own scheduled jobs and the heartbeat, with what each one did last time and when it runs next. Dream and the gardener are not listed here: their switches and schedules are in [Memory](#memory).

| What it shows | Why |
|---|---|
| **One row per job**, with its name, how the last run ended and when it runs next | The outcomes are not coloured alike: "looked, nothing to report" means a monitor looked and had nothing to say, which is success, not a warning. "could not check" is its own state (it ran, but the check did not happen) and stays distinct from "error". |
| **Tags on the row** | *system*, *speaks only if it has something to report*, *once only*, *paused*, *off*, *does nothing*. They change what the row means: a job that is off with "in 4 minutes" beside it would be a lie. |
| **Overdue instead of a past date** | Next-run times are stored, not computed on the fly: they are recalculated at startup and after each run. With the scheduler stopped, or right after the phone comes back from a long doze, the stored time is in the past, so the row shows how long ago it was due ("5 min ago"), in the warning colour, instead of a date that looks like the future. |
| **Armed but does nothing** | A system job survives the setting behind it: switch the heartbeat off, or leave `HEARTBEAT.md` with no active tasks, and its job stays scheduled and records `ok`. A notice above the list tells you. |
| **A rebuilt list** | If `cron/jobs.json` was recovered at startup, the group says so above the list, not just in the notice at the top of the screen: a short list looks like a correct list. |
| **Times in the job's own timezone** | A job created with an explicit timezone is shown in it, named only when it differs from the phone's, so the panel and what Jenny says in chat never disagree. |
| **"As of HH:MM", with a Refresh button** | The panel does not poll. On a phone where scheduled work is kept punctual through deep doze, a screen that woke the gateway every few seconds would undo that, so it reads once when you open the drawer and the timestamp says it is a snapshot. |

Tapping a job opens its details: what a system job is for, the reminder's full text, the recent runs with their errors, and, for the heartbeat, the checks it can see in `HEARTBEAT.md`.

Your own jobs can be managed from there. An active job has **Pause** and **Delete**, and a paused one has **Resume** and **Delete**. Pausing doesn't ask, because resuming undoes it. **Delete** asks first, and a deleted job only comes back if you ask Jenny again. A one-time reminder whose time passed while it was paused can only be deleted. System jobs have no buttons: their switches are in their own settings. There is no "run now", because that would start an agent turn, which costs tokens and may message you. Creating or changing a job is still done by asking Jenny. See [Scheduling and proactivity](../using/scheduling.md).

## Memory

### How much she remembers

The three files that go into every prompt, `MEMORY.md`, `USER.md` and `SOUL.md`, each with its size, a bar against its budget, and how many characters are left. **Change the budgets** opens a panel with the three caps (`memoryBudgetChars`, `userBudgetChars`, `soulBudgetChars`). Zero means measure and never refuse.

### Dream

- **Periodic consolidation** and **Every how many hours**: `agents.defaults.dream.enabled` and `intervalH`. The deadline survives a restart; a run missed while the app was down happens at the next tick.
- **Review pass every N runs**: `reviewEveryRuns`. Setting it below **12** opens a confirmation dialog, and the dialog is the point: a forced back-to-back review pass has been measured removing real entries. The change is only sent after you confirm.
- Under them, how many runs have passed since the last review, and two stall counters that appear only when they aren't zero.

### Gardener

- **Periodic pass**, **How often it looks (min)**, **Silence required in the notebook (min)**, **Gap between two passes on one notebook (h)**: the gardener's `enabled`, `intervalMin`, `idleMin` and `minHoursBetweenPasses`. A hint says that turning it off is not uninstalling it: `/gardener` inside a notebook still runs a pass by hand.
- **Notebook history** → **Archive an idle notebook's chat**: `compactProjectsWhenIdle`. It takes effect from the next gateway start, which the group says on the spot.

### Local history

One row with the number of workspace snapshots and the age of the oldest. Tapping it opens a panel with **Keep history for** (1 week / 1 month / 1 year / Forever, default **Forever**), **Create snapshot now**, and the list of snapshots, each of which can be restored after a confirmation. See [Backup and restore](../using/backup.md).

### The real files

The file manager for the workspace, inside the group: folders open in place, and a **+** creates a new item. Service files (hidden files, `config.json`, `cron/`, `sessions/`, `agent/` and similar) are never listed. Opening a file shows it on a screen of its own.

## What's only in config.json

Settings intentionally does not expose everything the backend supports. The following exist and work, but have no control in either screen: they must be edited directly in `workspace/config.json` (see [Configuration](configuration.md) for the full reference):

- `agents.defaults.timezone`: has a working update endpoint but no field in the UI; empty string means "use the device's timezone"
- `agents.defaults.bot_icon`: the emoji shown next to the bot's name; has a working update endpoint but no field in the UI
- `agents.defaults.tool_hint_max_length`: has a working update endpoint but no field in the UI (default 40, range 20–500)
- `agents.defaults.language`: written once by onboarding, from the phone's language at that moment
- `agents.defaults.reasoning_effort` = `adaptive`: the Parameters select saves the effort (see above), but `adaptive` is not one of the values the endpoint accepts
- `gateway.heartbeat.*`: the proactive Heartbeat cadence and behaviour
- `websocket.show_reasoning`: whether the "reasoning" pill is shown/recorded at all for the WebUI channel (default true)
- `tools.*.enable` toggles for individual tools (file tools, `python_exec`, `my`, introspection, diagnostics, etc.): only Location and SSH have a switch in the workshop
- `tools.location.telegram_ttl_s`, `tools.location.fresh_timeout_s`: see Location above
- `tools.ssh.*` beyond the on/off switch and the host list: timeouts, output and transfer caps, keepalive, and the per-host `job_log_dir`
- `security.restrict_to_workspace`, `security.ssrf_whitelist`: sandboxing and network policy
- `power.*` except `power.keep_awake`: wake-lock rotation, the restart watchdog, alarm-driven cron, the alarm-clock fallback and the outage threshold

## Cross-references

- [Configuration (config.json)](configuration.md): full key-by-key reference for everything above and beyond the UI
- [Themes and mascot](../using/themes-mascot.md)
- [SSH access](../using/ssh.md)
- [Telegram bridge](../using/telegram.md)
- [Backup and restore](../using/backup.md)
- [Location](../using/location.md)
- [Scheduling and proactivity](../using/scheduling.md)
