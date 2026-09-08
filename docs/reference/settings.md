# Settings

Every control in the Settings screen, what it does, and its default value.

Settings is a single accordion of 10 sections — Personalization, Model, Tools, **Memory**, **Wiki and projects**, Background activity, SSH, Telegram, Backup & restore, System — all collapsed the first time you open the screen (Background activity opens itself when the battery-optimization exemption is missing). There is no global Save button: almost every field saves itself, with a "Saved!" toast confirming the write. A few controls (theme, mascot, Home button, language, Developer mode) live entirely on the device and never touch `config.json` at all — those are called out explicitly below.

## How saving works

Text and number fields (bot name, the advanced model parameters, the web search fields) save on a **600 ms debounce** after you stop typing — not on every keystroke, and not only on blur. Toggles, the theme picker, the model catalog, and the language switch save immediately on click, no debounce. Every successful write shows a **"Saved!"** toast; a failed write shows the error message instead (and toggles roll back visually to their previous state).

Most controls take effect the instant you interact with them. The exceptions — everything that asks you to confirm first — are:

- **Deleting a provider.**
- **Importing a backup**, and **restoring a local snapshot**.
- **Regenerating an SSH key** (it revokes the access already installed on the server).
- **Deleting an SSH host.**
- **Accepting a host key fingerprint** — a dialog showing the fingerprint, with Cancel and Accept.
- **Replacing a host key that changed**, which asks **twice**: once in the side-by-side dialog showing the old and new fingerprints, and again in a plain confirmation. That is deliberate — a changed key is treated as a possible man-in-the-middle, not as an update.

Everything not on that list saves on touch.

**Silent restarts (important):** a few fields — timezone, bot name, bot icon, `tool_hint_max_length` — flip a `requires_restart` flag on the backend when changed. The current WebUI never reads or shows that flag: you get the same "Saved!" toast as any other field, with no indication that the change won't fully apply until you restart the app. If you rename the bot and it still introduces itself with the old name, restart Jenny.

## Personalization

| Control | Effect | Default |
|---|---|---|
| **Theme** | Tap a card to switch instantly across the whole UI (7 named themes, each card is a live preview). See [Themes and mascot](../using/themes-mascot.md). | Chanel |
| **Mascot** — Show mascot | Toggle the floating companion on/off. | On |
| **Mascot** — Mascot size | Small / Medium / Large — the side of her square, 120 / 160 / 210 px. Which edge she docks on is not a setting: it's where she last landed after a throw. | Small |
| **Home button** | Which view Android's Home button lands on when Jenny is your launcher: Chat, Apps, Workspace, or "Wherever I was". See [Launcher setup](../start/launcher-setup.md). | Chat |
| **Bot Name** | Free-text field for the assistant's name used in chat and the welcome message. Saves with the 600 ms debounce. Changing it flips `requires_restart` server-side, but the UI never tells you — see above. | "Jenny" |
| **Language** | Segmented Italian/English switch. Changes the UI's own strings instantly; does **not** change `agents.defaults.language` in `config.json` (that field is only ever written once, during onboarding, and drives backend-generated text like the welcome message). | Detected from the browser/WebView locale at first launch |

The mascot size control below the toggle stays on screen when she's switched off — greyed out and inert, not removed. That's deliberate: hiding it meant that turning her off, which is exactly what someone does right before going looking for a way to tame her, deleted the answer to the question from the page.

Theme, mascot preferences, the Home button destination, and UI language all live in the browser's `localStorage`, per device — they are **not** part of `config.json` and are **not** included in encrypted backups. Reinstalling the app, or clearing app data, resets all of them to their defaults.

## Model

### In use

A card showing the active model name and `via <provider> · <format>`. Next to it, a **Change model** button opens the unified catalog.

Model and provider are chosen **together, as one decision**: there is no separate "which provider" step. The catalog groups models by configured provider; each group is fetched live from that provider's model-list endpoint, includes a search box ("Search all models…") that filters across every group, and ends with a manual **"Custom model ID…"** field (press Enter to confirm) for providers with no list endpoint. Tapping any model in the catalog, or confirming a custom ID, immediately sends both `model` and `default_provider` in a single request and reloads settings — there is no confirmation dialog, and the change is applied to the running agent right away (hot-reload, no app restart).

If a provider's model list fails to load or comes back empty, the group shows the backend's diagnostic message instead of a model list; these messages currently come through in English regardless of UI language (e.g. "Could not fetch models").

### API keys

Below the catalog, "API keys" is a plain credential keychain — it does not indicate which key is "in use" (that's the In use card above). Each provider gets a card showing:

- Name, and a format badge (OpenAI Compatible / Anthropic Compatible)
- Base URL, or "(default)" if unset
- A masked key hint: the first 4 and last 4 characters of the stored key, joined with `...` (e.g. `sk-a...j8f9`) — the full key is never sent back to the browser
- Edit and Delete actions

**Add provider** opens a dialog with Name, Format, API Key, Base URL, and CA certificate. The base URL placeholder switches automatically with the format (`https://api.openai.com/v1` for OpenAI-compatible, `https://api.anthropic.com` for Anthropic).

**CA certificate** is only for a server whose certificate is signed by your own CA: the path to a PEM file to trust *on top of* the default roots, relative to the workspace unless absolute. Installing that CA on the phone does not help — the Python runtime inside the APK has its own trust store. A path that can't be read is refused on save, naming the file, rather than being accepted and quietly ignored. Details: [Self-signed certificates](./providers.md#self-signed-certificates).

The UI refuses to delete the last remaining provider ("Cannot delete the last provider") — but this check is client-side only; there is no equivalent guard on the backend, so this protection exists only inside the WebUI, not as a data-level invariant.

**Editing a provider.** The Edit dialog leaves the API Key field **empty** and shows the masked hint (e.g. `sk-a...j8f9`) as its placeholder, with a note under it saying to leave it blank to keep the stored key. Blank means exactly that: the saved key is kept. So you can change the Format or the Base URL without retyping the key — type in that field only when you actually want to replace it.

### Advanced parameters

A collapsed disclosure under API keys with three fields:

| Field | Range | Default |
|---|---|---|
| Max Tokens | integer ≥ 1 | 16384 |
| Temperature | 0.0 – 2.0 | 0.1 |
| Reasoning Effort | empty / low / medium / high | medium |

All three save, with the same 600 ms debounce as Bot Name. (In 0.3.x they did not: the UI sent them and showed "Saved!", but no backend branch read them. If you remember typing into these fields and finding your value gone, that's why — the toast was a false positive.)

Max Tokens is the ceiling for a single reply. On a reasoning model the thinking counts against that same budget, which is why the default is 16384 rather than the 8192 of earlier versions: a turn that planned at length could spend the whole allowance before saying anything. Reasoning Effort defaults to `medium` instead of leaving the provider to decide, for the same reason — an unbridled reasoning model will happily spend the entire output budget thinking about an open-ended task.

A rejected value comes back as an error rather than being silently clamped: Max Tokens must parse as an integer of at least 1, and Temperature must land inside 0.0–2.0. Temperature accepts a comma as the decimal separator, since that is what an Italian-locale number input produces.

Saving any of the three also rebuilds the provider, so the new value applies to your very next message. Nothing needs restarting — which matters, because these fields don't flip the `requires_restart` flag and the UI would never have told you.

Reasoning Effort has values beyond the four in the select. The API accepts `none` (disable thinking explicitly) and `minimal` (plus `minimum`, a DashScope-native alias normalized to `minimal`), so those reach `config.json` if you write them through the endpoint — the select simply doesn't offer them. `adaptive` (Anthropic adaptive thinking) is the exception: the endpoint **rejects** it, so it can only be set by editing `config.json` directly. Note also that the select can't represent a value it doesn't list — if `config.json` holds `adaptive`, the field renders blank, and touching it replaces the value. <!-- verified in code: jenny/webui/settings_api.py (_parse_max_tokens / _parse_temperature / _parse_reasoning_effort) + jenny/webui/settings_routes.py (provider rebuild) + jenny/providers/anthropic_provider.py (adaptive) -->

You can still set all three per-model instead of globally, by defining a [model preset](configuration.md) with its own override and switching to it with `/model`.

## Tools

### Web Search

| Field | Range | Default |
|---|---|---|
| Search engine | dropdown | bing |
| Max results | 1 – 10 | 5 |
| Timeout (sec) | 1 – 120 | 30 |
| Fetch max chars | 1000 – 200000 | 50000 |

The Search engine dropdown looks like a choice but currently has exactly one working option: the on-device web search tool only supports Bing, and the backend rejects any other value outright. All four fields save together with the 600 ms debounce.

### Location

A single toggle, **"Share my location"**, default **on**. Its hint text explains the model in full: a recent last-known position is injected into the conversation context on every message (free, no GPS fix), and a precise fix is only requested on demand. It applies immediately on toggle (no debounce), with a toast confirming "Location enabled"/"Location disabled" and an automatic rollback if the request fails.

This toggle is a software gate only — it does **not** request or manage the Android location permission. If the OS permission was never granted, turning this toggle on does nothing by itself; both the toggle and the Android permission have to be satisfied for location to actually reach the agent. <!-- TODO: verify on-device (O-10): UI state when the Android permission is denied while the toggle is on -->

Two related values exist only in `config.json`, with no UI control: `tools.location.telegram_ttl_s` (default 3600 — how long a location shared from Telegram stays valid) and `tools.location.fresh_timeout_s` (default 15 — how long Jenny waits for a fresh GPS fix). See [Location](../using/location.md).

## Memory

Dream's schedule and the three file budgets, all of which used to be config-only.

- **On/off and cadence** — `agents.defaults.dream.enabled` and `intervalH`. The deadline survives a restart; a run missed while the app was down happens at the next tick.
- **Three budget gauges** — `memoryBudgetChars`, `userBudgetChars`, `soulBudgetChars`, each shown as a fill against its cap, so a file that is nearly full is visible before it starts costing you entries.
- **Review cadence** — `reviewEveryRuns`. Lowering it below **12** opens a confirmation dialog, and the dialog is the point: a forced back-to-back review pass has been measured removing real entries — two open questions, a plan, a biographical detail and an observation. The client only sends the change after you confirm.

## Wiki and projects

The other periodic worker, plus one project-side switch.

- **The gardener** — `enabled`, `intervalMin`, `idleMin`, `minHoursBetweenPasses`. The section carries a hint spelling out that turning it off is not uninstalling it: the wikis stay, nothing prunes them.
- **`compactProjectsWhenIdle`** — whether idle project chats get compacted. It takes effect from the next start, which the field says on the spot rather than leaving you to wonder why nothing changed.

## Background activity

Everything about Jenny surviving a screen that's been off for hours. It sits between Tools and SSH, and it opens by itself when the battery-optimization exemption is missing — that being the one thing here worth interrupting you for.

| Control | Effect | Default |
|---|---|---|
| **Exempt from battery** | Opens Android's own "ignore battery optimizations" prompt. Once granted, the button is replaced by a confirmation line rather than disappearing, so the section doesn't look broken. The same request appears during first-run setup and in the Telegram card, and is re-offered when a system update has silently reset it. | Not exempt |
| **Keep the CPU awake** | The `power.keepAwake` mode: *Never* (best battery, scheduled work can slip by hours), *Only while working* (recommended — awake for a turn, a cron job or an SSH command, then released), *Always* (nothing slips, drains battery constantly, for a phone on charge). **Takes effect at the next Jenny restart**, which the UI says under the control: the service-lifetime lock is taken once, at startup. | Only while working |
| **Current state** | Three read-only yes/no lines — battery exemption, exact alarms permitted, CPU kept awake right now. Refreshed when you come back from a system dialog. Diagnostics only: exact alarms are granted from Android's settings, not from here. | — |
| **Recorded outages** | The last few stretches of at least `power.gapWarningMin` (default 60) minutes when Jenny was not running, with date and duration. Empty is the healthy state. When the list isn't empty, a card appears explaining that the phone's battery manager is the cause, with a link to dontkillmyapp.com for your brand and, where the phone allows it, a button that opens the manufacturer's battery screen. | Empty |

Only the wake-lock mode is editable here; the rest of the `power.*` family (wake-lock rotation, the restart watchdog, alarm-driven cron, the alarm-clock fallback, the outage threshold) is `config.json`-only — see [Configuration](./configuration.md#power). Outside the Android app the whole section is hidden: there is no bridge to ask, and nothing it says would be true.

## SSH

Its own section between Background activity and Telegram, holding the two decisions that cannot be delegated to the agent: **which machines exist**, and **which host key is the right one**.

| Control | Effect | Default |
|---|---|---|
| **Enable SSH access** | Master switch (`tools.ssh.enable`). Off means the agent has no SSH tools at all; the host list stays visible and editable so you can fix things with the switch down. | **Off** |
| **Add host** → Alias | The only name the agent ever uses for this machine, and also the name of its key file. 1–32 chars, `A–Z a–z 0–9 - _`, must start alphanumeric. **Cannot be changed later** — there is no rename. | — |
| **Add host** → Host / Port / User | Address, port and login account. | port 22 |
| **Add host** → Description | Free text, shown **to the model** so it can pick between machines ("the home NAS"). Not decoration. | empty |
| **Add host** → Authentication | `ed25519 key` or `Password` (`auth`). Existing hosts stay on key — the default did not change under them. | **ed25519 key** |
| **Add host** → Password | Only shown with `Password` selected. Required: saving a password host with an empty password is refused, not accepted quietly, so you can't end up with a host that looks configured and fails on the first command. Blank when editing (the saved password is never sent back to the screen) and blank means "keep the saved one". Switching the host back to key **deletes** the stored password. | none |
| **Generate key** / **Regenerate key** | Creates an ed25519 pair *for that alias* on the device and shows the public line to paste into `~/.ssh/authorized_keys`. Regenerating asks for confirmation, because it revokes the access already installed on the server. Hidden on a password host, along with the public-key block — there is nothing to install there. | no key |
| **Verify fingerprint** → **Accept** | Reads the key the host presents (without authenticating), shows its SHA256 fingerprint, and pins it on acceptance. | not verified |
| **Copy public key** | On a key host that has a key, copies the full `ssh-ed25519 …` public line to the clipboard, so you can paste it into the server's `~/.ssh/authorized_keys` without transcribing it. Absent on a password host — there is nothing to install. | — |
| **Edit host** (pencil icon) | Reopens the same dialog to change host, port, username, description, or authentication mode. The alias is the one field that cannot change. Editing the address or port **clears the accepted fingerprint** — see below. | — |
| **Delete host** | Removes the host **and** its private key, its public key and its accepted fingerprint. | — |

Every host card shows the two states that decide whether it can be used: the **fingerprint** state as a badge in the card header (pinned or not), and the **credential** state as plain text in the card body — "key ready"/"no key" on a key host, "password set"/"no password" on a password host. Only the fingerprint is a badge; the credential line is not, so don't go looking for a second one. Both have to be satisfied before the agent can connect, and the credential wording follows the authentication mode, because "no key" on a password host would be an alarm about something that isn't needed.

Five behaviors worth knowing before you use this screen:

- **Enabling is not symmetric with disabling.** Switching SSH *off* applies immediately, even to a subagent already working on a server — that is the emergency stop. Switching it *on*, or adding your first host, needs an **app restart** before the agent actually has the tools, because they are built at startup. Adding a second host to an install that already worked is live, no restart.
- **There is no trust-on-first-use.** Until you have accepted a fingerprint, every SSH call for that alias fails and tells the agent to ask you. A fingerprint reading older than 10 minutes is refused and has to be taken again.
- **Pinning is required in both authentication modes, and matters more with a password.** With a key, an unverified host gets a signature it can't reuse; with a password, it gets your password. The fingerprint dialog says so explicitly on a password host. There is no way to skip the step in either mode.
- **A changed host key is treated as an attack, not an update.** If a host presents a key different from the one you accepted, Jenny shows both fingerprints side by side and requires a second explicit confirmation to replace it.
- **Editing the address or port of an existing host clears its verified fingerprint** (and forgets the `known_hosts` line), because a verification of the old address says nothing about the new one. You have to verify again.

The private key and the `known_hosts` file live **outside** the workspace, so they are not in snapshots and not in an encrypted backup: after a restore you have to generate new keys and reinstall them on each server. A **password** does not get that treatment — it is stored in `config.json` like the Telegram token and the API keys, unencrypted at rest, inside the workspace and therefore inside backups. That's the trade: more convenient, weaker, and a dedicated key can be revoked without touching the password you log in with yourself. One field has no UI at all — the per-host `jobLogDir` (default `/tmp/jenny-jobs`), which is `config.json`-only. Full walkthrough: [SSH access](../using/ssh.md).

## Telegram

Bot pairing lives in its own section, sharing the same widget used during onboarding: paste a BotFather token, get a 6-digit pairing code, send it to the bot from Telegram. All changes here (connecting, changing token, unpairing, disabling) apply immediately with no app restart. Full walkthrough: [Telegram bridge](../using/telegram.md).

## Backup & restore

Three blocks: **Export encrypted backup** (passphrase-protected, includes memory, conversations, settings, and API keys), **Restore from file** (replaces all current data, applied on next app restart), and **Local history** — automatic workspace snapshots with a retention selector (1 week / 1 month / 1 year / Forever, default **Forever**) and a manual "Create snapshot now" button. Full details, including the irrecoverable-passphrase warning and snapshot retention behavior: [Backup and restore](../using/backup.md).

## System

| Item | Shows |
|---|---|
| Version | The installed app version string. |
| Developer mode | Toggle, default **off**. See below. |
| Token Usage | 7 local usage statistics. See below. |

### Developer mode

A single toggle with the hint: *"Also shows what Jenny uses to work: system skills and internal files (memory, configuration) appear in the lists. Only useful for looking under the hood."*

This is a **client-side display filter only**, stored in `localStorage` (`jenny-advanced-mode`), default off. The backend already tags every skill and file with an `internal` flag; this toggle only decides whether the WebUI shows or hides items carrying that flag in the Skills and Workspace lists. It does **not** change any permission, does **not** unlock any tool, and does **not** affect what the agent itself can see or do — the agent already has full access to its own internals regardless of this toggle. Being in `localStorage`, it resets to off if you clear app data or reinstall, and it is **not** included in encrypted backups.

### Token usage

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

## What's only in config.json

Settings intentionally does not expose everything the backend supports. The following exist and work, but have no UI control anywhere in Settings — they must be edited directly in `workspace/config.json` (see [Configuration](configuration.md) for the full reference):

- `agents.defaults.timezone` — has a working update endpoint but no field in the UI; empty string means "use the device's timezone"
- `agents.defaults.bot_icon` — the emoji shown next to the bot's name; has a working update endpoint but no field in the UI
- `agents.defaults.context_window_tokens` — has a working update endpoint but no field in the UI (valid values: 65536 or 262144)
- `agents.defaults.tool_hint_max_length` — has a working update endpoint but no field in the UI (default 40, range 20–500)
- `agents.defaults.reasoning_effort` = `adaptive` — the Advanced Parameters select saves the effort (see above), but `adaptive` is not one of the values the endpoint accepts, so that one value is config-only
- `gateway.heartbeat.*` — the proactive Heartbeat cadence and behavior
- `websocket.show_reasoning` — whether the "reasoning" pill is shown/recorded at all for the WebUI channel (default true); no toggle in Settings
- `tools.*.enable` toggles for individual tools (file tools, `python_exec`, `my`, introspection, diagnostics, etc.) — only Web Search and Location get a Tools-section UI; everything else is config-only
- `tools.location.telegram_ttl_s`, `tools.location.fresh_timeout_s` — see Location above
- `tools.ssh.*` beyond the on/off switch and the host list — timeouts, output and transfer caps, keepalive, and the per-host `job_log_dir`; the SSH section covers hosts, keys and fingerprints and nothing else
- `security.restrict_to_workspace`, `security.ssrf_whitelist` — sandboxing and network policy
- `power.*` except `power.keep_awake` — wake-lock rotation, the restart watchdog, alarm-driven cron, the alarm-clock fallback and the outage threshold; the Background activity section exposes the wake-lock mode and nothing else

## Cross-references

- [Configuration (config.json)](configuration.md) — full key-by-key reference for everything above and beyond the UI
- [Themes and mascot](../using/themes-mascot.md)
- [SSH access](../using/ssh.md)
- [Telegram bridge](../using/telegram.md)
- [Backup and restore](../using/backup.md)
- [Location](../using/location.md)
