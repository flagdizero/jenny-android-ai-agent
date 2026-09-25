# Mini-apps (Jenny Apps)

A Jenny App is a small app with its own screen that Jenny builds for you on request — a shopping list, a counter, a custom dashboard for a device on your network.

## Apps vs. skills

Jenny has two ways of gaining a new capability, and they answer different questions:

- **If it needs a screen, it's an app.** A Jenny App has its own UI you open from the Jenny Apps room: a list you check off, a form you fill in, a chart you look at.
- **If it only lives in chat, it's a skill.** A skill just teaches Jenny a procedure — there's nothing to open. See [Skills](skills.md).

An app can still talk to Jenny (see [Directionality](#jenny-and-the-app-talk-in-one-direction) below), and it can integrate with an external server, but the defining trait is: does the user need to look at a screen for this, or does it just happen in conversation?

## Creating an app

Apps are created only through chat — there is no dedicated app editor in the WebUI. Two ways to start:

1. Just ask, in your own words: "make me an app for tracking my plants."
2. Tap **New App** at the top of the **Jenny Apps** room in the Apps tab. It opens the chat with this guided prompt already typed:

   > I want to create a new Jenny App. Use the "app-creator" skill and guide me step by step.

Jenny then walks you through the design conversation (what the app tracks, what actions it needs) using the built-in `app-creator` skill, confirms with you, and writes the files. Editing an existing app works the same way: long-press the app's row and choose **Edit** — this sends the chat prompt `I want to edit the Jenny App "{name}" (slug: {slug}). Can you help me?` rather than opening any in-app editor.

## Where an app lives

Every app is a folder under `workspace/apps/<slug>/`:

| Path | Contents |
|---|---|
| `app.json` | The manifest: name, icon, an optional external server, and the list of typed actions. |
| `AGENT.md` | Context for Jenny about the app — what it's for, preferences, thresholds. Not part of the technical contract, just briefing notes. |
| `app/index.html` | The UI. HTML and JavaScript only — there is no per-app Python. Only this `app/` folder is served over the web; the manifest, `AGENT.md`, and `data/` are never reachable by URL. |
| `data/` | JSONL collections that hold the app's data, shared between the app and Jenny. |

Because it's a normal folder in the workspace, you can also open and edit these files by hand from the [Workspace tab](webui-tour.md) if you want — Jenny doesn't have to be the one writing them.

## Every action is also a tool

Each action declared in `app.json` becomes a native tool that the LLM can call directly, named `<slug>_<action>` — for a "plants" app with a `water` action, that's `plants_water`. These tools are re-registered on the fly whenever `app.json` changes, with no restart needed.

This has a consequence worth calling out explicitly: **Jenny can read and modify an app's data even while the app is closed.** A closed app is just unrendered HTML; nothing stops Jenny from appending a record to its `data/` collection because you asked her to in chat. The reverse is not true — see below.

### Jenny and the app talk in one direction

- **Jenny → app**: always possible, whether or not the app is open on screen.
- **App → Jenny**: never on its own. The app can only hand off to chat through an explicit user action, `jenny.discuss()`, which switches you to the chat view with the app's context pre-filled. Jenny's replies always land in chat — they are never rendered inside the app itself. The app stays a deterministic screen; the thinking happens in one place only.

## What an app can and cannot do

Apps run inside a sandboxed iframe with `allow-scripts` only. That means:

- No native `<form>` submission, no `alert()`/`confirm()`/`prompt()`, and no direct `fetch()` calls out of the app's own JavaScript. All data access goes through the app SDK's `jenny.action()` call.
- The app cannot navigate the rest of the WebUI or touch the chat DOM.
- The key the app is handed opens only its own files and actions. It is not the key the WebUI itself uses, so an app — or something injected into one — cannot change your settings, read your conversation, or talk to Jenny through the gateway.

Storage actions (append/set/update/delete/query on a `data/` collection):

| Limit | Value | What happens beyond it |
|---|---|---|
| Collection size | 5 MB per collection | Writes fail with HTTP 413. |
| Query result size | 200 records by default | Records are read oldest-first and the list is cut at the limit, so once a collection passes it, the *newest* records are the ones silently left out — not the oldest — unless the app asks for a higher limit. |
| Malformed line in a collection | — | Skipped silently; only a warning is written to the log. |
| Updating/deleting a record that doesn't exist | — | Fails with HTTP 404. |

Actions that call an external server (a `http`-kind action, e.g. talking to a LAN device):

| Limit | Value |
|---|---|
| Request timeout | 20 seconds |
| Response size | 512 KB |
| Reachability | LAN addresses are allowed; loopback and cloud-metadata addresses are blocked |
| Redirects | Never followed |
| Parameters (GET, ~6 KB budget) | Requests with a larger encoded parameter payload fail with HTTP 413 |

**Authenticated external servers are not supported.** An app's manifest can *describe* a `server.auth` credential reference, but there is no credential store behind it. If an app declares `server.auth`, every call to that server is refused fail-closed with HTTP 501 ("no credential store is configured") rather than being sent without credentials. Don't build an app around the assumption that it can log in to a service on your behalf — it can only talk to servers that don't require authentication (e.g. a plain LAN device).

## When an app is broken

If a manifest fails to load — malformed JSON, an invalid action definition — the gateway never crashes. The app simply shows up in the Jenny Apps room with an alert glyph on its row, and the readable error in a **warning band at the top of the room** — above the list, not tucked under the app's name, so one glance tells you something needs attention without hunting for which row. Tapping it prompts: `The app "{name}" is broken: {error}. Ask Jenny to fix it?` — confirming sends the error straight to chat so Jenny can look at the files and repair them. Since the app generator is itself an LLM, occasionally getting a manifest wrong is expected, and this is the recovery path.

## Deleting an app

Long-press an app's row and choose **Delete**. The confirmation reads `Delete app "{name}"? It will be removed permanently.` — and it means it: this deletes the whole `workspace/apps/<slug>/` folder, including its `data/`. There is no trash or undo from the UI. Your only safety net is the automatic workspace [snapshot](backup.md) history, which is not something you can browse per-app — restoring one means restoring the entire workspace to an earlier point in time.

## See also

- [Skills](skills.md) — the chat-only counterpart to apps.
- [Backup and restore](backup.md) — apps and their data are included in both encrypted backups and local snapshots.
- [Tool reference](../reference/tools.md) — how app-generated tools fit alongside Jenny's built-in tools.
