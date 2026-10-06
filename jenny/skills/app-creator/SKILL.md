---
name: app-creator
description: >
  Create or update Jenny Apps (folders in workspace/apps/ with a typed-actions manifest,
  a self-contained HTML UI, and agent context). Use when:
  - User asks, in any language, to create or build an app ("create an app", "make me a Jenny App for...")
  - User taps the "+" button in the Jenny Apps grid
  - User wants to connect an external server (REST API) as an app with UI
  Do NOT use for skills (chat-only capabilities, no screen) — use skill-creator for those.
locked: true
---

# App Creator

A Jenny App is a folder in `apps/<slug>/` (workspace-relative). It is always **general** — it
lives in the App page, not inside a notebook — and every part is optional: a screen for the
user, typed actions for the agent, a **reader** that reads something by itself on a clock, and
a **summary**, the way it shows in a card's drawer or on a card's cover on a notebook board.
If it needs a screen, a live value or a place on a board, it's an app; if it only lives in
chat, it's a skill.

```
apps/<slug>/
├── app.json        # manifest: name, icon, server, actions, reader, summary (the contract)
├── AGENT.md        # context for the agent: what the app is, preferences, thresholds
├── run.py          # optional reader: `def run(ctx)`, run every N minutes with no model
├── app/
│   └── index.html  # optional screen (HTML/JS); only app/ is web-served
└── data/           # app state (collections), shared between UI and agent — never web-served
```

<rule>
**Write apps only with the `app` tool — never with write_file, edit_file or python_exec**
(they refuse app code). `app action=draft` writes the files as a **trial**: it validates them,
runs the reader once, and tells you the real first value. A new app — or a change that
reaches a new host, writes a new collection, adds an action or changes the summary — **waits
on trial**: no tools, no screen, no reader, until the user taps **Keep** on its trial card (in
the App page, or in the drawer where it is placed). You cannot keep it for them. A fix that
adds nothing goes live at once. Tell the user what the app reaches and writes, and that they
keep it from the trial card.
</rule>

<rule>
**Every app that keeps state declares a `summary`** (see the reference): a reader, or any
storage action that writes, means there is something to show at a glance — "38 %", "7 to
buy", "€ 12,430". Do not ask the user whether they want it: decide the summary yourself
from what the app is for. An app with no state (a calculator) has none.
</rule>

For the full manifest schema, action kinds, and UI conventions, read
[references/manifest.md](references/manifest.md) before writing any file.

<rule>
**The app UI runs in an iframe sandboxed with `allow-scripts` and nothing else.** Three
things therefore fail *silently* — no error anywhere, the app just looks finished and does
nothing when tapped:

- **Never use `<form>`.** Submission is blocked *before* the `submit` event fires, so
  `event.preventDefault()` never runs and cannot rescue it. Use
  `<button type="button">` with a click handler, plus a `keydown` listener for Enter on
  the input, and call `jenny.action()` from the handler.
- **Never use `alert()`, `confirm()`, `prompt()`.** There is no `allow-modals`. Build
  dialogs with `<dialog>` or kit markup.
- **Never call `/api/apps/` with `fetch`.** Always go through `jenny.action()` — the
  gateway is GET-only and answers no CORS preflight.

`scripts/validate_app.py` rejects the first as an error and warns on the others.
</rule>

<rule>
**Every internal screen change goes through `jenny.navigate(label, state)`**, and the app
repaints the previous screen on the `popstate` event (`jenny.back()` for the app's own "←"
buttons). The app fills the screen and the phone's back button is the only way out: a screen
change the SDK never heard about means the next Back press closes the *whole app*, taking the
sub-screen or the half-filled form with it. A `<dialog>` counts as a level by itself — the SDK
sees it and closes it first — which is one more reason to use `<dialog>` rather than a
hand-rolled overlay `<div>`. See "Internal navigation and the Android back button" in
[references/manifest.md](references/manifest.md).
</rule>

<rule>
**A sideways swipe belongs to the home screen, unless the component under the finger is
dragged sideways.** The user can pin an app as a page of the home, and a left/right swipe
there changes page — a button never keeps it, a sideways-dragged component always does:

- Buttons act on `click`, never on `pointerdown`/`touchstart`: a press that counts on
  touch-down has already counted when the finger turns out to be swiping. Hold-to-repeat
  starts after a delay and stops on `pointercancel` — the app gets one when the page takes
  the swipe.
- A component dragged sideways by your own code (carousel, swipe-to-delete row, map,
  drawing area) declares it with CSS `touch-action: pan-y` (`none` if it also takes
  vertical drags). `<input type="range">` and `overflow-x: auto` rows need nothing.
- `touch-action: none` on `body` takes the swipe from the whole app: only for a full-screen
  game.
</rule>

<rule>
**The app speaks the language the user is speaking in this conversation — not the language of
the examples in this skill.** That covers everything the user sees (the `name` and
`description` in `app.json`, UI labels, buttons, placeholders, empty and loading states,
error texts) and everything the agent reads (action and param names, their descriptions,
collection names, `AGENT.md`). `<html lang>` matches it: `lang="en"` for an English
conversation. The examples below are in English only because they are examples: an app
requested in German gets German labels, descriptions and identifiers. If the language is
unclear (e.g. the user tapped "+" and has written nothing yet), use English.
</rule>

<rule>
**Follow the Guided Conversation Flow below.** Ask ONE question at a time. Only write files
AFTER the user has confirmed name and actions in Phase 3.
Never write real secrets into app.json or index.html, and never declare `server.auth` at all (see Secrets below).

**This conversation is not a sustained goal: do not call `long_task` for it.** Each phase
ends by asking the user something and waiting, which is the one thing a goal cannot do for
you — registering one only makes the runtime prod you to keep going while you have nothing to
go on. Ask the question of the current phase and end the turn.
</rule>

## Guided Conversation Flow

The quoted lines show what to ask, not the words to use: ask in the user's language.

### Phase 1: Understand Purpose

> Sure! Tell me: what should this app do? What do you want to see when you open it?

If the answer is vague, ask ONE clarifying question with a concrete example.

### Phase 2: Understand Data and Actions

Figure out where the data lives and what the app (and the agent) must be able to do:

> Does the data live on an external server (give me the base URL and endpoints), or do we keep it locally?

From the answers, derive the action list. Each thing the UI shows or changes, and each thing
the agent should be able to do on the user's behalf, becomes one action:

- Local data (notes, lists, logs) → `storage` actions on collections in `data/`.
- External server (e.g. a LAN plant server) → `http` actions mapped onto its endpoints.

If an endpoint needs auth, it cannot be used yet — say so (see Secrets).

### Phase 3: Propose and Confirm

> Here is my proposal:
> - **Name:** Plants (`plants`)
> - **Actions:** `list_plants` (http GET /plants), `plant_humidity` (http GET /plants/{id}/humidity), `log_care` (storage append to `care`)
>
> Does that work, or shall we change something?

Slug rules: lowercase alphanumeric with single hyphens, max 32 chars, folder named exactly
after the slug. Action names: snake_case, unique within the app — the agent will see them as
tools named `<slug>_<action>`.

### Phase 4: Draft the App

Only after confirmation, call `app action=draft slug=<slug>` with `files`:

1. `app.json` — follow [references/manifest.md](references/manifest.md) exactly, including the
   `summary` (and the `reader`, if the app reads something by itself).
2. `AGENT.md` — 5–15 lines: what the app is for, user preferences and thresholds learned in the
   conversation (e.g. "water the basil when humidity drops below 20%"), anything the agent
   needs to act well. NOT a copy of the manifest.
3. `run.py` — the reader, if any: `def run(ctx):` returning exactly the reader's fields.
4. `app/index.html` — the screen, if any: built on the Jenny Kit (theme tokens, classless base,
   component vocabulary, chart helpers) following the conventions in the reference. Never invent
   a custom design or load anything from an external host.

Collections in `data/` are created on first write. If the tool reports a problem, fix it with
`app action=edit` (one exact replacement in one file) and it tries again.

### Phase 5: Trial and Place

The tool's answer says whether the app is live or on trial, and the first real value. Tell the
user, in one or two lines, what it does, what it reaches and writes, and that they keep it with
**Keep** on its trial card. If the app is about a subject of a notebook (a plant, a portfolio),
offer to put it on that notebook's board — `app action=place`, as a section in a card's drawer
(`card`) or as a card of its own — and do it when the user agrees. Placing it in a notebook also
lets that notebook's chat use it.

## Secrets

**Never write an `auth` block in `app.json` — not even `{"secretRef": "<name>"}`.** The
credential store is not implemented, and the http executor is fail-closed: a manifest that
declares `server.auth` has **every** http action refused with 501, and since Sept 2026 the
app is rejected at load as broken. This section previously said to write `secretRef` anyway
"so manifests keep working when the store lands"; that advice shipped a real app whose only
action was dead on arrival, and it is withdrawn.

So: an app can only talk to an endpoint that needs no credentials (a LAN or Tailscale server
without auth is the normal case). If the user's endpoint *does* need a token, say plainly
that Jenny Apps cannot authenticate to an app server yet, and do not write a manifest that
pretends otherwise. Never put a raw token in `app.json` or `index.html`. If the user pastes a
token in chat, do not echo it and do not write it to any file in the workspace.

## Boundaries

- Apps live in `apps/`, never in `ui/` — `ui/` is re-extracted from the package on every
  startup and would overwrite them.
- Actions are declarative (`storage`/`http` only). The only Python an app has is its reader
  (`run.py`), which reads and never writes; real server-side logic belongs in the app's external
  server behind an `http` action.
- The app never contacts the agent on its own. The only app→agent path is the user's explicit
  hand-off to chat. Proactivity ("let me know if...") is scheduled work on the agent side, offered
  as a follow-up after the app works — where it goes is decided by the recurring-work rule, not
  here.
