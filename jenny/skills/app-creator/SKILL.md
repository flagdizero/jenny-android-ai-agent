---
name: app-creator
description: >
  Create or update Jenny Apps (folders in workspace/apps/ with a typed-actions manifest,
  a self-contained HTML UI, and agent context). Use when:
  - User says "voglio creare una jenny app", "crea un'app", "fammi un'app per...", "create an app"
  - User taps the "+" button in the Jenny Apps grid
  - User wants to connect an external server (REST API) as an app with UI
  Do NOT use for skills (chat-only capabilities, no screen) — use skill-creator for those.
locked: true
user_summary:
  it: "Le Jenny Apps sono piccole app con una loro interfaccia che Jenny costruisce su richiesta (una lista della spesa, un contatore, uno strumento su misura). Basta chiederglielo in chat."
  en: "Jenny Apps are small apps with their own interface that Jenny builds on request (a shopping list, a counter, a custom tool). Just ask for it in chat."
---

# App Creator

A Jenny App is a folder in `apps/<slug>/` (workspace-relative) that packages a UI for the
user and typed actions for the agent. If it needs a screen, it's an app; if it only lives in
chat, it's a skill.

```
apps/<slug>/
├── app.json        # manifest: name, icon, external server, typed actions (the contract)
├── AGENT.md        # context for the agent: what the app is, preferences, thresholds
├── app/
│   └── index.html  # the UI (HTML/JS only — no per-app Python); only app/ is web-served
└── data/           # app state (collections), shared between UI and agent — never web-served
```

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
**Follow the Guided Conversation Flow below.** Ask ONE question at a time. Only write files
AFTER the user has confirmed name and actions in Phase 3.
Never write real secrets into app.json or index.html, and never declare `server.auth` at all (see Secrets below).

**This conversation is not a sustained goal: do not call `long_task` for it.** Each phase
ends by asking the user something and waiting, which is the one thing a goal cannot do for
you — registering one only makes the runtime prod you to keep going while you have nothing to
go on. Ask the question of the current phase and end the turn.
</rule>

## Guided Conversation Flow

### Phase 1: Understand Purpose

> Certo! Dimmi: cosa dovrebbe fare questa app? Cosa vuoi vedere quando la apri?

If the answer is vague, ask ONE clarifying question with a concrete example.

### Phase 2: Understand Data and Actions

Figure out where the data lives and what the app (and the agent) must be able to do:

> I dati stanno su un server esterno (dammi base URL ed endpoint) o li gestiamo noi in locale?

From the answers, derive the action list. Each thing the UI shows or changes, and each thing
the agent should be able to do on the user's behalf, becomes one action:

- Local data (notes, lists, logs) → `storage` actions on collections in `data/`.
- External server (e.g. a LAN plant server) → `http` actions mapped onto its endpoints.

If an endpoint needs auth, it cannot be used yet — say so (see Secrets).

### Phase 3: Propose and Confirm

> Ok, propongo:
> - **Nome:** Piante (`piante`)
> - **Azioni:** `lista_piante` (http GET /plants), `umidita_pianta` (http GET /plants/{id}/humidity), `annota_cura` (storage append su `cure`)
>
> Ti va bene o cambiamo qualcosa?

Slug rules: lowercase alphanumeric with single hyphens, max 32 chars, folder named exactly
after the slug. Action names: snake_case, unique within the app — the agent will see them as
tools named `<slug>_<action>`.

### Phase 4: Write the Files

Only after confirmation, create the folder and write, in this order:

1. `apps/<slug>/app.json` — follow [references/manifest.md](references/manifest.md) exactly.
2. `apps/<slug>/AGENT.md` — 5–15 lines: what the app is for, user preferences and thresholds
   learned in the conversation (e.g. "sotto il 20% di umidità il basilico va annaffiato"),
   anything the agent needs to act well. NOT a copy of the manifest.
3. `apps/<slug>/app/index.html` — UI built on the Jenny Kit (theme tokens, classless base,
   component vocabulary, chart helpers) following the conventions in the reference. Never
   invent a custom design or load anything from an external host.
4. `apps/<slug>/data/` — create the directory; leave collections to be created on first write.

### Phase 5: Validate

Run the validator and fix anything it reports:

```
python_exec(
    working_dir="<workspace>/skills/app-creator/scripts",
    code="import validate_app; validate_app.main(['validate_app.py', '<workspace>/apps/<slug>'])",
)
```

`working_dir` is what makes the bare `import` resolve; the app path must be absolute, because
the script walks it with `pathlib`.

Then tell the user the app is ready and will appear in the Jenny Apps grid.

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
- No per-app Python and no code execution in actions: actions are declarative
  (`storage`/`http` only). If an app needs real server-side logic, it belongs in the app's
  external server behind an `http` action.
- The app never contacts the agent on its own. The only app→agent path is the user's explicit
  hand-off to chat. Proactivity ("avvisami se...") is scheduled work on the agent side, offered
  as a follow-up after the app works — where it goes is decided by the recurring-work rule, not
  here.
