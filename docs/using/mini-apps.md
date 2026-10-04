# Mini-apps (Jenny Apps)

A Jenny App is a small app with its own screen that Jenny builds for you on request — a shopping list, a counter, a custom dashboard for a device on your network.

## Apps vs. skills

Jenny has two ways of gaining a new capability, and they answer different questions:

- **If it needs a screen, it's an app.** A Jenny App has its own UI you open from the app drawer (see [Phone app launcher](app-launcher.md)): a list you check off, a form you fill in, a chart you look at.
- **If it only lives in chat, it's a skill.** A skill just teaches Jenny a procedure — there's nothing to open. See [Skills](skills.md).

An app can still talk to Jenny (see [Directionality](#jenny-and-the-app-talk-in-one-direction) below), and it can integrate with an external server, but the defining trait is: does the user need to look at a screen for this, or does it just happen in conversation?

## Creating an app

Apps are created only through chat — there is no dedicated app editor or "new app" button in the WebUI. Just ask, in your own words: "make me an app for tracking my plants."

<p align="center"><img src="../img/apps.png" alt="A mini-app Jenny wrote on request: a meal log for a cat, with today's total and the meals" width="300"></p>

Jenny then walks you through the design conversation (what the app tracks, what actions it needs) using the built-in `app-creator` skill, confirms with you, and writes the files. Editing an existing app works the same way: long-press the app's row in the app drawer and choose **Edit** — this takes you to the chat and writes `I want to edit the Jenny App "{name}" (slug: {slug}). Can you help me?` into the message box, ready for you to add what you want changed and send, rather than opening any in-app editor.

In the home, the same card also has **Add as a page**, which puts the app in the row of pages along the top, next to the chat: see [Tour of the WebUI](webui-tour.md#the-pages).

## Where an app lives

Every app is a folder under `workspace/apps/<slug>/`:

| Path | Contents |
|---|---|
| `app.json` | The manifest: name, icon, an optional external server, the (optional) list of typed actions, and an optional drawer view (`section`, see below). |
| `AGENT.md` | Context for Jenny about the app — what it's for, preferences, thresholds. Not part of the technical contract, just briefing notes. |
| `app/index.html` | The UI. HTML and JavaScript only — there is no per-app Python. Only this `app/` folder is served over the web; the manifest, `AGENT.md`, and `data/` are never reachable by URL. |
| `data/` | JSONL collections that hold the app's data, shared between the app and Jenny. |

Because it's a normal folder in the workspace, you can also open and edit these files by hand from the workspace files in the workshop's **Memory** drawer (see [WebUI tour](webui-tour.md)) if you want — Jenny doesn't have to be the one writing them.

## Every action is also a tool

Each action declared in `app.json` becomes a native tool that the LLM can call directly, named `<slug>_<action>` — for a "plants" app with a `water` action, that's `plants_water`. These tools are re-registered on the fly whenever `app.json` changes, with no restart needed.

This has a consequence worth calling out explicitly: **Jenny can read and modify an app's data even while the app is closed.** A closed app is just unrendered HTML; nothing stops Jenny from appending a record to its `data/` collection because you asked her to in chat. The reverse is not true — see below.

### Jenny and the app talk in one direction

- **Jenny → app**: always possible, whether or not the app is open on screen.
- **App → Jenny**: never on its own. The app can only hand off to chat through an explicit user action, `jenny.discuss()`, which switches you to the chat view with the app's context pre-filled. Jenny's replies always land in chat — they are never rendered inside the app itself. The app stays a deterministic screen; the thinking happens in one place only.

## Apps in a notebook's drawers

An app whose data is about a subject — a soil-moisture sensor for the monstera, say — can also show that data inside a card's drawer on a notebook's [board](board.md), and stay in the app drawer as well. It does so by declaring an optional `section` block in `app.json`: which collection and field to show, the parameters to ask for when attaching it (which sensor), when the data counts as stale, an alarm threshold, a trend of up to 31 days, and up to two of its actions as buttons. Ask Jenny for it when you create or edit the app; the `app-creator` skill knows the format.

In the drawer, **+ Add a section → Mini app** lists only the apps that declare one. The section shows the latest value with its time and source, and lights the card's red corner when the value is in alarm or the data has stalled. It only reads what the app stored — the app's own cron job keeps it fresh — and the readings never go into the wiki: only a change of state is written to the notebook's journal, as one line. See [Notebook board → Mini app](board.md#mini-app).

## What an app can and cannot do

Apps run inside a sandboxed iframe with `allow-scripts` only. That means:

- No native `<form>` submission, no `alert()`/`confirm()`/`prompt()`, and no direct `fetch()` calls out of the app's own JavaScript. All data access goes through the app SDK's `jenny.action()` call.
- The app cannot navigate the rest of the WebUI or touch the chat DOM.
- The key the app is handed opens only its own files and actions. It is not the key the WebUI itself uses, so an app — or something injected into one — cannot change your settings, read your conversation, or talk to Jenny through the gateway.

Storage actions (append/set/update/delete/query on a `data/` collection):

| Limit | Value | What happens beyond it |
|---|---|---|
| Collection size | 5 MB per collection | Appends, sets and updates fail with HTTP 413. |
| Query result size | 200 records by default | Records are read oldest-first and the list is cut at the limit, so once a collection passes it, the *newest* records are the ones silently left out — not the oldest — unless the app asks for a higher limit. |
| Malformed line in a collection | — | Skipped silently; only a warning is written to the log. |
| Updating/deleting a record that doesn't exist | — | Fails with HTTP 404. |

Actions that call an external server (a `http`-kind action, e.g. talking to a LAN device):

| Limit | Value |
|---|---|
| Request timeout | 20 seconds |
| Response size | 512 KB |
| Reachability | LAN addresses and the carrier-grade-NAT range Tailscale uses are allowed; loopback and link-local (cloud-metadata) addresses are blocked |
| Redirects | Never followed |
| Parameters (GET, ~6 KB budget) | Requests with a larger encoded parameter payload fail with HTTP 413 |

**Authenticated external servers are not supported.** There is no credential store for app servers, so a manifest that declares a `server.auth` block is rejected when the app is loaded: the app shows up as broken, with an error telling Jenny to remove the `auth` block. Don't build an app around the assumption that it can log in to a service on your behalf — it can only talk to servers that don't require authentication (e.g. a plain LAN device).

## Apps whose screen is a server: external views

Normally an app's screen is its own `app/index.html`. An app can instead declare, in `app.json`, `"view": {"kind": "external"}` together with a `server.baseUrl`: then its screen *is* the page that server serves — a dashboard already running on a device in your house, say. A manifest that asks for an external view without a `server.baseUrl` is rejected and the app shows up as broken.

Jenny does not put that address straight in a frame, because the app's network policy allows plain `http://` only to the phone itself. Instead the gateway starts a small proxy on the phone's loopback address, in front of that one server, and the app opens through it. The proxy exists only while the app is open (and closes itself after 30 idle minutes), it only ever talks to the server named in the manifest, and the address it hands the WebView cannot be used by another app on the phone. If it cannot start, you get "Could not open the external view" with the reason.

What you notice as a user:

- The app's row in the drawer carries a small cloud glyph, tooltip **Has its own server** (any app with a `server.baseUrl` gets it, external view or not).
- **Add as a page** is greyed out with the reason "It opens outside Jenny, so it can't be a page": an external view is not something the home can hold as one of its own pages.
- A plain `http://` address in an ordinary app's frame does not load; the message says it needs an https server or an external-view app.

## When an app is broken

If a manifest fails to load — malformed JSON, an invalid action definition — the gateway never crashes. The app simply shows up in the app drawer with an alert glyph and the readable error in red on the second line of its row, where the description would be. Tapping it prompts: `The app "{name}" is broken: {error}. Ask Jenny to fix it?` — confirming writes the error into the chat's message box, ready to send, so Jenny can look at the files and repair them. Since the app generator is itself an LLM, occasionally getting a manifest wrong is expected, and this is the recovery path.

## Deleting an app

Long-press an app's row in the app drawer and choose **Delete**. If you had pinned the app as a page of the home, the page goes with it. The confirmation reads `Delete app "{name}"? It will be removed permanently.` — and it means it: this deletes the whole `workspace/apps/<slug>/` folder, including its `data/`. There is no trash or undo from the UI. Your only safety net is the automatic workspace [snapshot](backup.md) history, which is not something you can browse per-app — restoring one means restoring the entire workspace to an earlier point in time.

## See also

- [Skills](skills.md) — the chat-only counterpart to apps.
- [Notebook board](board.md) — the drawers an app's `section` view appears in.
- [Backup and restore](backup.md) — apps and their data are included in both encrypted backups and local snapshots.
- [Tool reference](../reference/tools.md) — how app-generated tools fit alongside Jenny's built-in tools.
