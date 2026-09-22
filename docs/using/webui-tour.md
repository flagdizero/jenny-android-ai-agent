# Tour of the WebUI

Jenny's entire interface is a mobile web app (a "WebUI") running inside the Android app; this page is a map of its chrome — the tab dock, how you move between views, what the Android back button does, and the "Session Info" popover you get by tapping the Jenny row above the chat.

## The tab dock

A row of five icons pinned to the bottom of the screen switches between the app's main views, in this order:

| Icon | Tab | What it is |
|---|---|---|
| ✿ | **Console** | The conversation with Jenny, with everything under it on show: thoughts, tool calls, timings. See [Chat basics](chat.md). |
| brain | **Brain** | Which model answers and which brands exist, the generation parameters, and what the phone lets her do while the screen is off. |
| hand | **Hands** | What she can reach — web, location, SSH, Telegram, skills, mini-apps — and the jobs that start by themselves. |
| database | **Memory** | What she remembers: the three memory files and their caps, Dream and the gardener that fill them, the workspace files, the local snapshot history. |

A sixth tab, **Setup**, exists in the same dock but stays hidden (`display:none`) once onboarding is complete — it only appears during first run, when it also disables the other five tabs so you can't wander off mid-wizard. See [First run](../start/first-run.md).

Tapping a dock icon switches views immediately; the active tab is highlighted. Brain, Hands and Memory are three drawers of one screen — they share a controller, so moving between them does not reload anything.

The app launcher drawer, the Apps tab, the file browser and the Wiki, which used to have their own dock slots, now open from a row at the top of the drawer that will host them.

## Moving around: swipe and the back button

### Horizontal swipe

You can also switch tabs by swiping left/right anywhere in the main content area — the current view slides out and the neighboring tab slides in, with a light "peek" effect and a gray overlay while you drag, and it snaps back if you don't drag far enough (roughly 22% of the screen width, or a quick enough flick).

A few guards keep this from fighting with normal scrolling:

- If the content under your finger can scroll horizontally in the direction you're dragging (for example, a wide code block or a horizontally scrollable list), that content gets the gesture instead of the tab swipe.
- A mostly-vertical drag is treated as ordinary scrolling, not a tab change.
- Swipe navigation is disabled entirely during onboarding, and while a drawer/side panel is open (it owns its own vertical swipe).
- Swipe only moves between the four dock tabs in the table above, in dock order. Notebooks are not one of them: their pages are reached from the conversation they belong to (see [Wiki](wiki.md)), and reading one is a state the dock doesn't know about.

### The Android hardware back button

The launcher delegates the back button entirely to the app itself, with this priority:

1. **Inside a Jenny mini-app**, back first tries to go back *within* the mini-app (if it has its own internal navigation depth). Only once the mini-app has nothing left to go back to does back close the mini-app and return you to the Apps tab.
2. Outside of a mini-app, back replays whatever in-app navigation history exists (for example, back out of a Wiki article to the page you came from, or back out of a settings sub-view).
3. At the root — nothing left in history and no mini-app open — back does **nothing**. Jenny is also set up as an Android launcher (see [Set it as your launcher](../start/launcher-setup.md)), so there is deliberately no "exit the app" behavior: there's no home screen underneath to fall back to, and you should never be dropped out of a running task by an accidental back-press.

## The identity row and connection status

Above the chat there's an identity row, "✿ Jenny", with a small status dot next to it. This row is **not a fixed header** — it's the first item in the scrollable message list, so once you scroll up into your conversation history it scrolls away with everything else.

The dot reflects only the state of the WebSocket connection between the WebUI (running in the WebView) and the local gateway (running inside the same app) — it says nothing about your phone's internet connection:

| Dot | Label | Meaning |
|---|---|---|
| Gray | *(no label)* | No connection attempt has completed yet (just after opening the app) |
| Green | `online` | The WebUI is connected to the gateway |
| Red | `offline` | The socket has dropped |

Reconnection is automatic and has no attempt limit: the app retries with a growing delay starting at 3 seconds and multiplying by 1.5 each time, capped at 30 seconds. Two things reset that backoff and force an immediate retry: bringing the app back to the foreground, and the device regaining network connectivity. In practice "offline" is often just Android briefly suspending the app or the WebView (e.g., screen off) — it clears itself and you don't need to do anything.

Tapping anywhere on the identity row opens the **Session Info** popover, covered next.

## The Session Info popover

Tapping the "✿ Jenny" row opens a small popover titled **"Session Info"**. Close it with the X in its corner, by tapping anywhere outside it, or with the **Esc** key (handy if you're on a device with a physical keyboard).

Everything in the popover is a **snapshot taken the moment it opens** — with one exception, the Status timer, none of the rows update live while it's open. If something changes while the popover is open (a turn finishes, the model is switched), close it and reopen it to see the new values.

Row by row:

| Row | Value | Meaning |
|---|---|---|
| **Session** | always `default` | Jenny has a single, unified conversation — there's no session picker or multi-session support here. This value is a fixed string in the UI, not something read from the backend. |
| **Channel** | always `websocket` | Also fixed. It stays `websocket` even for turns that came in from Telegram, since the whole conversation is one unified thread — see [Telegram bridge](telegram.md). |
| **Model** / **Preset** | see below | Currently unreliable — see the warning below. |
| **Project** | an absolute path | The workspace folder the agent reads and writes files in (Jenny's private storage on the device, not shared/general phone storage). |
| **Access** | a badge with a lock icon | Whether the agent's file tools are confined to that workspace folder or can reach outside it — see below. |
| **Status** | `Running` or `Idle` | Whether a turn is currently being processed, with a live timer if so — see below. |

### Model / Preset: currently broken, check Settings instead

<!-- TODO: verify on-device -->
As of this writing, the **Model** row does not reliably show the model actually in use. A backend/frontend field mismatch means it typically reads as `—` when you haven't switched models at all during the current app session, and something like `Unknown / —` after you do switch. A fix is in progress, but until it lands, don't trust this row — check **Settings → Model** instead to see which provider and model are actually configured. The **Preset** row, when it does work, is meant to only appear if a model preset is active.

### Access: what "Restricted" vs "Full access" means

The Access badge reflects the `security.restrictToWorkspace` config setting (default `true`):

| Badge | Config state | Meaning |
|---|---|---|
| **Restricted** | `restrictToWorkspace: true` (default) | The agent's file tools (read/write/edit/list/etc.) are confined inside the Project folder shown above. |
| **Full access** | `restrictToWorkspace: false` | The agent's file tools can also reach outside the workspace folder. |
| **Default** | — | A transient placeholder shown only while the chat history hasn't finished loading yet, not a real third access level. Once history loads you'll always see Restricted or Full access. |

There's no toggle in the popover (or anywhere in the app UI) to flip this — it's set only in `config.json`. Also worth being precise about: this restriction is enforced by Jenny's own code, not by an Android OS sandbox — it's an application-level boundary, not a system-level one. See [Security model](../internals/security-model.md).

The Project/Access values are also only refreshed when the chat's history loads (e.g., on app start), not continuously — if you change the config, you'll see the old value until you reload the chat.

### Status: the turn timer

If the agent is actively processing a message, Status shows **Running** with a spinner and an elapsed-time counter (seconds, or minutes and seconds once it passes a minute). Otherwise it shows **Idle**.

The timer starts the moment you send a message and is **backed by the actual turn start time on the gateway**, so it survives reloading the page or reopening the app mid-turn — it doesn't reset to zero just because the WebUI restarted. It only reflects turns from the WebUI/websocket channel: a turn started from Telegram does not turn this indicator on, even though you'll see the resulting messages appear in the same unified chat.

## The Subagents strip

One more piece of chrome lives on the chat screen and isn't part of the dock: a **Subagents** strip pinned just above the message box. It appears on its own when background work starts and vanishes when the turn ends, so most of the time you won't see it at all.

It exists because Jenny delegates by default: the real work usually happens in subagents, and without this the chat would just be silent for minutes. Collapsed, it is a single header line with a running count; expanded, it's one card per job with its type, elapsed and idle time, current step, and a **Stop** button — plus a tap-through detail sheet with the full task and a live activity stream.

It is deliberately not a history view: only work from the current turn is ever shown, and it does not survive the turn that started it. Full behavior in [Chat basics](chat.md#the-subagents-panel).

## Where to go next

- [Chat basics](chat.md) — sending messages, reading a response, the Subagents panel, `/stop`.
- [Slash commands](slash-commands.md) — the full command list.
- [Settings](../reference/settings.md) — the reliable place to check which model/provider is active.
- [Security model](../internals/security-model.md) — what "Restricted" access actually enforces.
