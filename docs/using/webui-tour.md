# Tour of the WebUI

Jenny's interface is a mobile web app (a "WebUI") running inside the Android app, and it comes in two parts. The **home** is what opens: a row of pages you swipe between, the way any launcher works, with the conversation in the middle. The **workshop** is the other half — the full console with Jenny's thoughts, tool calls and timings, and every setting — and you reach it from the home's Settings page. This page is a map of both: how you move around, what the Android back button does, and where each thing lives.

## The home

### The pages

The home is a row of pages, and their names run along the top of the screen: the page you are on is written large, the others small. Tap a name to jump to it, or swipe sideways anywhere on the page. Four pages are always there:

| Page | What it is |
|---|---|
| **Apps** | The app drawer: your Android apps and your [mini-apps](mini-apps.md), with a search box and the ones you use most at the top. See [App launcher](app-launcher.md). |
| **Jenny** | The conversation. This page is named after the conversation it shows: *Jenny* for the personal one, the notebook's name when you are talking inside a notebook. See [Chat basics](chat.md). |
| **Notebooks** | Who you are talking to: the personal conversation and every notebook, with a check mark on the one you are in. Tap a row to switch the chat to it; the round **+** makes a new notebook. See [Projects](projects.md). |
| **Settings** | The settings of whoever uses the phone — theme, who answers, Jenny herself, updates, backup — and, at the bottom, the door to the workshop. |

The home always opens on **Jenny**. Beside the four you can keep up to eight pages of your own: press and hold a mini-app in the drawer, or a notebook in **Notebooks**, and choose **Add as a page**. A mini-app that opens outside Jenny, or one that is broken, shows that row greyed out with the reason. A notebook page is a shortcut, not a second chat: landing on it switches the one conversation to that notebook.

Every page can be moved, the four fixed ones included: press and hold a name at the top, drag the names into the order you want, and tap **Done**. The pages you added carry a **×** to remove them; the fixed four cannot be removed. Back leaves that mode without saving. The order and the pages you added are stored in `config.json` — see [`casa` in Configuration](../reference/configuration.md#casa).

### The conversation

The **Jenny** page is the chat, kept deliberately plain: your messages, her answers, and a composer with a paperclip for [attachments](attachments.md) and a send button.

- **While she works**, a single line under the conversation says what she is doing, in a word from the family of the tools actually running (reading, searching, writing, going out, running code, delegating). It appears only if the turn lasts more than half a second and steps aside while her answer is being written. **Press and hold that line** to open the same turn in the workshop, with every thought and tool call.
- **While a turn is running**, the send button becomes **Stop**, which sends `/stop` — see [Slash commands](slash-commands.md).
- **If the connection to the gateway drops** for more than a couple of seconds, a line says *Connection lost, retrying*; it goes away on its own when the socket is back. It is about the link between the WebUI and the gateway inside the same app, not about your internet connection.
- **Messages that came from elsewhere** — Telegram, a notification you answered from the shade, the floating bubble — carry a small label saying where they came from.

Inside a notebook, a pill at the left of the composer shows how many pages the notebook has. Tap it for the notebook's pages: a **Pages** tab with a search box that filters as you type, and a **Map** tab with the pages drawn as a graph of their links. Tapping a page opens it in a reader, where **Edit** opens a plain text editor with **Save** and **Cancel** at the bottom, and selecting a piece of text offers **Report**, which sends Jenny that passage with your note on what is wrong. **Talk about it** takes you back to the conversation. See [Wiki](wiki.md).

### Settings

The **Settings** page is short on purpose, and every row shows its current value on the right:

| Row | Opens |
|---|---|
| **Theme** | A card on the page itself: pick a theme and see it applied at once. See [Themes and mascot](themes-mascot.md). |
| **Who answers** | The configured providers, the key of the one you are looking at, and its models. Tapping a model is what switches — provider and model together. |
| **Jenny** | Her name, whether the mascot is shown and how big, the floating mascot over other apps (on Android), and **The rules you gave her**: a text of yours that she reads every turn and never rewrites. |
| **Updates** | Whether the update check works, what is available, and the install. |
| **Backup** | When you last exported a backup, export and restore, and a note on the local workspace history. See [Backup](backup.md). |

Below them, **Workshop — watch, tune, repair** opens the workshop.

### The back button and Home

Jenny is also set up as an Android launcher (see [Set it as your launcher](../start/launcher-setup.md)), so back never closes the app. One press undoes one thing, from the top:

1. an open sheet (the one a long press opens), a mini-app opened from the drawer — which first goes back inside itself if it has its own screens — the page-ordering mode, a search typed in the drawer, the Report sheet, an enlarged image;
2. then the rooms, one per press: the page reader goes back to the pages, the pages go back to the chat, a room opened from Settings goes back to Settings;
3. then, from any page other than the chat, back returns to the chat, wherever it sits in the row;
4. then, if the chat is showing a notebook, back returns to the personal conversation.

In the personal conversation with nothing on top, back does **nothing**: there is no home screen underneath to fall back to. The Home button closes whatever is open and brings you back to the personal conversation.

## The workshop

The workshop is the full interface: the console with everything under a turn on show, and every setting the home leaves out. Open it from **Settings → Workshop**, or from the work line in the chat with a long press. The Console and each drawer carry a **Jenny** pill in their header that takes you back home.

### The tab dock

A row of four icons pinned to the bottom of the screen switches between the workshop's views, in this order:

| Icon | Tab | What it is |
|---|---|---|
| ✿ | **Console** | The conversation with Jenny, with everything under it on show: thoughts, tool calls, timings. See [Chat basics](chat.md). |
| brain | **Brain** | Which brands exist, which model she thinks with, the generation parameters, and what the phone lets her do while the screen is off. |
| hand | **Hands** | What she can do and with which permissions — web, location, SSH, Telegram, skills, mini-apps — and the jobs that start by themselves. |
| database | **Memory** | What she remembers: the three memory files and their caps, Dream and the gardener that fill them, the workspace files, the local snapshot history. |

A fifth tab, **Setup**, exists in the same dock but stays hidden once onboarding is complete — it only appears during first run, when it also disables the other four tabs so you can't wander off mid-wizard. See [First run](../start/first-run.md).

Tapping a dock icon switches views immediately; the active tab is highlighted. Brain, Hands and Memory are three drawers of one screen — they share a controller, so moving between them does not reload anything. The app drawer opens from the grid button at the left of the Console's message box.

### Horizontal swipe

You can also switch tabs by swiping left/right anywhere in the main content area — the current view slides out and the neighboring tab slides in, with a light "peek" effect while you drag, and it snaps back if you don't drag far enough (22% of the screen width, at least 60 px, or a quick enough flick).

A few guards keep this from fighting with normal scrolling:

- If the content under your finger can scroll horizontally (a wide code block, a horizontally scrollable list), that content gets the gesture instead of the tab swipe — even when it is already at its edge.
- A mostly-vertical drag is treated as ordinary scrolling, not a tab change.
- Swipe navigation is disabled during onboarding.
- Swipe only moves between the four dock tabs in the table above, in dock order.

### The back button

Back in the workshop follows the same rule as at home — one press, one thing: inside a mini-app it first goes back within the mini-app, then closes it; outside, it replays the in-app navigation history (for example, out of a settings sub-view). With nothing left, back does nothing.

### The identity row and connection status

Above the Console there's an identity row, "✿ Jenny", with a small status dot next to it. This row is **not a fixed header** — it's the first item in the scrollable message list, so once you scroll up into your conversation history it scrolls away with everything else.

The dot reflects only the state of the WebSocket connection between the WebUI and the local gateway — it says nothing about your phone's internet connection:

| Dot | Label | Meaning |
|---|---|---|
| Gray | *(no label)* | No connection attempt has completed yet (just after opening) |
| Green | `online` | The WebUI is connected to the gateway |
| Red | `offline` | The socket has dropped |

Reconnection is automatic and has no attempt limit, in the workshop and at home alike: the app retries with a growing delay starting at 3 seconds and multiplying by 1.5 each time, capped at 30 seconds. Two things force an immediate retry: bringing the app back to the foreground, and the device regaining network connectivity. In practice "offline" is often just Android briefly suspending the app or the WebView (e.g., screen off) — it clears itself.

Tapping anywhere on the identity row opens the **Session Info** popover.

### The Session Info popover

Close it with the X in its corner, by tapping anywhere outside it, or with back (the **Esc** key does the same on a physical keyboard).

| Row | Value | Meaning |
|---|---|---|
| **Session** | always `default` | A fixed string in the UI, not something read from the backend. |
| **Channel** | always `websocket` | Also fixed. It stays `websocket` even for turns that came in from Telegram — see [Telegram bridge](telegram.md). |
| **Model** | provider / model | The model answering now, colored with its provider's brand. It is filled from the gateway when the page loads and updated live when the model is switched. |
| **Preset** | a preset name | Shown only when a model preset is active. |
| **Project** | an absolute path | The workspace folder the agent reads and writes files in (Jenny's private storage on the device, not shared phone storage). |
| **Access** | a badge with a lock icon | Whether the agent's file tools are confined to that folder — see below. |
| **Status** | `Running` or `Idle` | Whether a turn is being processed, with a live timer if so — see below. |

**Access.** The badge reflects the `security.restrictToWorkspace` config setting (default `true`): **Restricted** means the file tools are confined inside the Project folder, **Full access** that they can also reach outside it. **Default** is a transient placeholder shown only until the chat history has loaded. There is no toggle for this in the app — it is set only in `config.json` — and the restriction is enforced by Jenny's own code, not by an Android sandbox. See [Security model](../internals/security-model.md). Project and Access are read when the chat history loads, so a config change shows after a reload.

**Status.** While a turn runs, Status shows **Running** with a spinner and an elapsed-time counter. The timer is backed by the turn's start time on the gateway, so it survives reloading the page mid-turn. It only reflects turns from the WebUI: a turn started from Telegram does not turn it on, even though its messages appear in the same unified chat.

### The Subagents strip

One more piece of the Console lives outside the message list: a **Subagents** strip pinned just above the message box. It appears on its own when background work starts and vanishes when the turn ends, so most of the time you won't see it.

It exists because Jenny delegates by default: the real work often happens in subagents, and without this the chat would be silent for minutes. Collapsed, it is a single header line with a running count; expanded, it's one card per job with its type, elapsed and idle time, current step, and a **Stop** button — plus a detail sheet with the full task and a live activity stream. It is not a history view: only work from the current turn is shown. Full behavior in [Chat basics](chat.md#the-subagents-panel).

## Where to go next

- [Chat basics](chat.md) — sending messages, reading a response, the Subagents panel, `/stop`.
- [Projects](projects.md) and [Wiki](wiki.md) — notebooks, their conversation and their pages.
- [Slash commands](slash-commands.md) — the full command list.
- [Settings](../reference/settings.md) — every setting, including the ones only the workshop shows.
- [Security model](../internals/security-model.md) — what "Restricted" access actually enforces.
