# Jenny App Manifest Reference

Contents: [app.json fields](#appjson-fields) · [storage actions](#storage-actions) ·
[http actions](#http-actions) · [Complete example](#complete-example) ·
[AGENT.md](#agentmd) · [UI conventions](#ui-conventions)

## app.json fields

| Field | Required | Notes |
|-------|----------|-------|
| `name` | yes | Display name shown in the Jenny Apps grid |
| `description` | yes | One line: what the app does (shown in the grid and to the agent) |
| `icon` | no | Tabler icon name (e.g. `ti-plant`); defaults to `ti-apps` |
| `server` | no | Only for apps backed by an external API: `{"baseUrl": "..."}` |
| `actions` | no | Array of typed actions (the contract — see below). Omit it for a display-only app |
| `view` | no | `{"kind": "external"}` — the app's screen is its server's UI, not `app/index.html` |

> **Never declare `server.auth`.** There is no credential store yet, and the http executor is
> fail-closed: with `auth` present, **every** http action is refused with 501, and since
> Sept 2026 the app is rejected at load as broken. This table used to show
> `"auth": {"secretRef": "..."}` as part of `server`, which is how a real app came to declare
> it and ship dead. Point the `baseUrl` at an endpoint that needs no credentials.

> **`actions` is optional.** An app whose only job is to draw a screen has nothing
> agent-facing to declare — omit `actions` entirely. Do **not** invent a filler action (a
> health-check ping, a no-op query) to satisfy the schema: that happened, and it produced an
> app with a permanently broken tool the user never asked for.

Fields common to every action:

| Field | Required | Notes |
|-------|----------|-------|
| `name` | yes | snake_case, unique in the app; exposed to the agent as tool `<slug>_<name>` |
| `description` | yes | What it does — this is what the agent reads to pick the tool |
| `kind` | yes | `storage` or `http` (no other kinds exist) |
| `params` | no | Map of param name → JSON Schema (`{"type": "string", "description": "..."}`) |
| `required` | no | Array of param names that are mandatory (default: none) |

The gateway validates params against the schema before executing; a mismatch returns a
structured error, it never half-executes. Params not declared in the schema are **rejected**
(`unknown params: ...`) — declare every field the action accepts.

## storage actions

Typed operations on collections stored as JSONL under `data/<collection>.jsonl`. Every record
gets two auto-assigned fields: `id` (12 hex chars) and `ts` (ISO-8601 UTC timestamp of the
append) — never declare params with these names on `append`.

| Extra field | Required | Notes |
|-------------|----------|-------|
| `op` | yes | `append`, `set`, `update`, `delete`, or `query` |
| `collection` | yes | Collection name: lowercase alphanumeric/hyphens |

Semantics: `append` adds a record from params; `set` writes a full record by `id`; `update`
merges params into the record with matching `id`; `delete` removes by `id`; `query` returns
records, optionally filtered by params.

Reserved params (auto-added to the action's schema, don't declare them):

- `id` — on `set`/`update`/`delete`: the target record id, always required.
- `limit` — on `query`: max records returned (default 200). It is a **page size, not a
  filter** — never declare a param named `limit` to filter records by.

```json
{ "name": "log_care", "description": "Log a care task done on a plant",
  "kind": "storage", "op": "append", "collection": "care",
  "params": { "plant": {"type": "string"}, "note": {"type": "string"} },
  "required": ["plant"] }
```

**Response shape.** `jenny.action()` never resolves to a bare array or record — every storage
op resolves to an envelope object, and the frontend must unwrap the field it needs:

| `op` | Resolves to |
|------|-------------|
| `append` | `{ok: true, record: {...}}` |
| `set` | `{ok: true, record: {...}}` |
| `update` | `{ok: true, record: {...}}` |
| `delete` | `{ok: true, deleted: "<id>"}` |
| `query` | `{ok: true, records: [...], count: N}` |

A `query` action with no declared `params` still returns **every** record in the collection
(no filter/match param is ever required) — but the array is under `.records`, not the
top-level value. The most common bug in generated apps is treating the resolved value itself
as the array:

```js
// WRONG — notes is {ok, records, count}; notes.length is undefined, notes.forEach throws
const notes = await jenny.action('list_notes');
notes.forEach(...)

// RIGHT
const { records: notes } = await jenny.action('list_notes');
notes.forEach(...)
```

## http actions

Mapped onto calls to `server.baseUrl`, executed through the gateway proxy (SSRF-checked; no
credentials are ever attached — see Secrets in SKILL.md).

| Extra field | Required | Notes |
|-------------|----------|-------|
| `method` | yes | `GET`, `POST`, `PUT`, `PATCH`, `DELETE` |
| `path` | yes | Endpoint path; `{param}` placeholders are filled from params |

Params not consumed by path placeholders go into the query string (`GET`/`DELETE`) or the
JSON body (other methods).

```json
{ "name": "plant_humidity", "description": "Current humidity of a plant",
  "kind": "http", "method": "GET", "path": "/plants/{id}/humidity",
  "params": { "id": {"type": "string", "description": "The plant's ID"} },
  "required": ["id"] }
```

**Response shape.** Like storage actions, an `http` action never resolves to the bare response
body — it resolves to `{ok: true, status: 200, data: <parsed body>}` (`ok` follows the HTTP
status, `data` is the server's JSON, parsed). Read the payload from `.data`:

```js
const { data: plants } = await jenny.action('list_plants');
```

## External view (`view: {"kind": "external"}`)

When the user asks for an app that just **shows an existing web UI on their own server**, this
is the shape — do not write an `index.html` with an `<iframe>` pointing at it:

```json
{ "name": "Remote", "description": "The home server's remote control",
  "icon": "ti-device-tv",
  "server": { "baseUrl": "http://192.168.1.50:8091" },
  "view": { "kind": "external" } }
```

No `app/index.html`, no `actions`. The gateway serves the server's UI through a loopback proxy
and the SPA frames that.

**A hand-written `<iframe src="http://...">` does NOT work, and fails silently.** The APK's
network policy permits cleartext only to the local gateway, so the WebView refuses the frame
with `ERR_CLEARTEXT_NOT_PERMITTED` before any request goes out — the user sees a blank panel.
That is exactly how one such app shipped looking finished and doing nothing. `view: external`
is the supported way to express it.

Constraints:

- requires `server.baseUrl`;
- `http://` only — an `https` server needs no proxy, frame it directly;
- the server must be reachable from the phone when the app is opened (LAN, or Tailscale — the
  SSRF policy allows both). If it is not, say so rather than adding a "ping" action to check.

## Summary (`summary`) — required for any app that keeps state

The summary is how the app shows **outside its screen**: on its own card on a notebook board
(the value in place of the photo; a tap opens the screen, or — for an app with no screen — the
card's drawer with the whole summary). It is computed by code from the app's data, never by the
model, and the drawer draws it in one of five shapes. An app that
keeps state — a reader, or any storage action that writes — **must** declare one; an app with
no state (a calculator, a converter) declares none.

Two kinds of summary:

**Over a collection** (an app without a reader):

```json
"summary": {
  "collection": "items", "view": "list", "where": { "done": false },
  "label": "to buy", "columns": { "what": { "label": "What" } },
  "verbs": ["add_item"]
}
```

- `view` — `value` (one big value, the default), `chips` (several values side by side),
  `progress` (a number towards `max`), `list` / `table` (the records that match, newest
  first, at most 20, with up to 4 `columns`).
- `aggregate` — `last` (the latest record's `field`, the default for value/chips/progress) or
  `count` (how many records match `where`: "7 to buy"; always the case for list and table).
- `field` — the value shown (required with `aggregate: last`), with optional `label`, `unit`.
- `where` + `params` — the filter; `params` are asked when the app is placed on a board
  (which sensor, which plant), and used in `where` as `$name`. The same app can be two cards
  with two different params.
- `staleAfterMinutes`, `alarm` (`{field, op, value}`), `trendDays` (0–31), `verbs` (at most two
  of this app's actions, shown as buttons in the drawer).

**Over the reader** (an app with a `reader`, below): `view`, `primary` (the reader field shown
big), `alarm`, `staleAfterMinutes`, `params`/`where`, `chips` (which other reader fields to show)
and `verbs`; labels, units and types come from the reader's fields. The readings live in the
app's `readings` collection, so the screen and `query` actions see them too.

The older `section` block (`value`/`status`/`trend`/`verbs`) is still read, as a `value`
summary; write `summary` in new apps.

## Reader (`reader` + `run.py`)

A reader is a small Python program next to the manifest that reads something **by itself**,
every N minutes, with **no model turn** — a sensor, a price, a count — or recomputes a total
from the app's own data. It replaces the old notebook "micro apps".

```json
"reader": {
  "every": 30, "net": ["192.168.1.40"], "uses": "waterbot-imh", "key": "sensor",
  "fields": {
    "sensor":   { "label": "Sensor", "type": "text" },
    "moisture": { "label": "Moisture", "type": "number", "unit": "%", "min": 0, "max": 100 },
    "online":   { "label": "Online", "type": "bool" }
  }
}
```

```python
def run(ctx):
    hub = ctx.http.get_json("http://192.168.1.40/sensors")
    return [{"sensor": s["name"], "moisture": s["moisture"], "online": s["online"],
             "_source": f"hub, seen {s['seen']}"} for s in hub if s["name"] in
            {w["sensor"] for w in ctx.wanted}]
```

- `every` — minutes between readings, at least 15 (default 60).
- `fields` — exactly what `run(ctx)` returns, at most 8: `number` (with optional `min`/`max`),
  `bool`, `text`, or one `rows` field (a list of dicts with declared `columns`, up to 4 columns
  and 20 rows) for a list or table. A missing, wrong-typed or out-of-range value is an **error**,
  never a value; the optional `_source` text says where the value came from.
- `key` — a text field naming the series when one reader reads several things (three sensors):
  then `run` returns a **list** of dicts, one per series, and `ctx.wanted` lists the params the
  placements on the boards need (`[{"sensor": "acerello"}, …]`).
- `net` — the only hosts `ctx.http.get_json / get_text / post_json` may reach (at most 4).
- `uses` — at most one installed skill whose scripts `run` imports: reuse it instead of
  re-implementing an API, and its credentials stay where they are. **Never put a credential in
  `run.py` or `app.json`** — the `app` tool refuses it.
- `ctx.now` (ISO), `ctx.prev` (the last reading), `ctx.data("collection")` (the app's own
  records, read-only: a reader that **aggregates** — a portfolio's value from its operations and
  current prices — also runs right after every write, so the summary always matches the data).
- The code reads, never writes; at most 6 KB; 20 s per run, three errors in a row mark it broken.

## Complete example

```json
{
  "name": "Plants",
  "description": "Houseplant monitor: humidity, status, care log",
  "icon": "ti-plant",
  "server": { "baseUrl": "http://192.168.1.50:8080" },
  "actions": [
    { "name": "list_plants", "description": "List the plants with their status",
      "kind": "http", "method": "GET", "path": "/plants" },
    { "name": "plant_humidity", "description": "Current humidity of a plant",
      "kind": "http", "method": "GET", "path": "/plants/{id}/humidity",
      "params": { "id": {"type": "string"} }, "required": ["id"] },
    { "name": "log_care", "description": "Log a care task done on a plant",
      "kind": "storage", "op": "append", "collection": "care",
      "params": { "plant": {"type": "string"}, "note": {"type": "string"} },
      "required": ["plant"] }
  ]
}
```

## AGENT.md

Context for the agent, loaded when it works with this app. Keep it 5–15 lines. Include: what
the app is for, user preferences/thresholds ("water the basil below 20%"), and
conventions for the data ("one note in `care` per task, in the user's language"). Do NOT
repeat the manifest — the agent already sees the actions as tools.

## UI conventions

`app/index.html` is rendered in a sandboxed full-screen iframe inside the SPA. It never loads
anything from an external host (the device may be offline): app-specific CSS/JS is inline,
and everything shared comes from the **Jenny Kit** served by the gateway on the same origin.
The app talks to the world only through its own action endpoints and never renders agent
output.

### The Jenny Kit (the graphical standard)

Every app links the kit in `<head>` — never write a custom design from scratch:

```html
<link rel="stylesheet" href="/html-mobile/assets/apps/jenny-kit.css">
```

The kit provides:

1. **Theme tokens** — the kit's own CSS variables, fed at runtime from whichever of Jenny's
   7 themes the user picked (the SDK stamps `data-theme` and applies the palette; the values
   in the kit stylesheet are only the fallback). **Always color with the variables, never
   with hardcoded hex values** — a hex ignores the theme and stays identical on all 7, which
   is exactly what makes an app look foreign. The vocabulary:

   | Group | Tokens |
   |-------|--------|
   | Surfaces | `--bg-solid` (page), `--bg` (topbar), `--bg2` (card), `--bg3` (code/pre) |
   | Text | `--text`, `--text2` (secondary), `--text3` (faint), `--heading` |
   | Lines | `--border`, `--border2`, `--glass-border` |
   | Fills | `--glass`, `--glass-strong`, `--hover-bg` |
   | Accent | `--accent`, `--accent-hover` (pressed), `--accent-subtle`, `--on-accent` (text *on* accent) |
   | Status | `--green`, `--warning`, `--error` (+ `--success-bg`, `--warning-bg`, `--error-bg`) |

   Note `--green` is a fixed green on purpose: it is the one signal where the color carries
   the whole message (`.badge-ok`), and some themes resolve their own "ok" to ivory or to
   the same yellow as their warning.
2. **Classless base** — semantic HTML is styled out of the box: `h1`–`h3`, `p`, `button`,
   `input`, `select`, `textarea`, `table`, `dialog` all look native with zero classes.
   Prefer semantic HTML; reach for classes only when a component below fits.
3. **Component vocabulary** (the only classes to use):

| Class | Use |
|-------|-----|
| `.topbar` | Sticky header for **contextual action icons only** (search, filter, add...) — see note below |
| `.card` | Grouped content block with padding and subtle border |
| `.list` / `.list-row` | Tappable rows; put a `<small>` inside for the secondary line |
| `.badge` (+ `.badge-ok` `.badge-warn` `.badge-err`) | Small status pill |
| `.stat` | Big value + label, for dashboard numbers |
| `.btn-primary` | Accent call-to-action (plain `<button>` is the neutral variant) |
| `.fab` | Floating action button, bottom-right |
| `.grid` | Responsive 2-column card grid |
| `.empty` | Centered muted placeholder for empty states |

**Do not put the app's `<h1>`/title inside `.topbar` (or anywhere).** The host SPA already
renders a chrome bar above the iframe showing the app's display name (from `app.json`'s
`name`) plus a close button — an `<h1>` inside the app repeats that title a second time,
stacked right below it. `.topbar` exists only for **contextual action icons** the app itself
needs (search, filter, add, sort...); if the app has none, omit `<header class="topbar">`
entirely and start the body directly with `<main id="app">`.

4. **Icons** — Tabler webfont, imported by the kit itself: `<i class="ti ti-plant"></i>`.
   Never inline custom SVG icon sets.
5. **Charts** — for any graph, use the kit helpers built on the bundled d3 (do not write raw
   d3):

```html
<script src="/html-mobile/assets/vendor/d3@7/d3.min.js"></script>
<script src="/html-mobile/assets/apps/jenny-charts.js"></script>
<script>
  JennyCharts.line(el, points);        // [{x: Date|number, y: number}] — trends over time
  JennyCharts.bars(el, items);         // [{label, value}] — comparisons
  JennyCharts.gauge(el, value, max);   // single percentage/level (e.g. humidity)
</script>
```

### Skeleton

```html
<!DOCTYPE html>
<html lang="en"> <!-- the user's language -->
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Plants</title>
  <link rel="stylesheet" href="/html-mobile/assets/apps/jenny-kit.css">
  <script src="/html-mobile/assets/apps/jenny-sdk.js"></script>
  <style>/* app-specific tweaks only — keep minimal */</style>
</head>
<body>
  <!-- No <header class="topbar"><h1>...</h1></header> — the host chrome already shows the
       app name. Add a .topbar only if you need action icons (search, filter, add...). -->
  <main id="app"><div class="empty">Loading…</div></main>
  <script>
    async function render() {
      const { data: plants } = await jenny.action('list_plants');
      /* build DOM from data using the kit vocabulary */
    }
    render();
    // Re-render when the agent changes this app's data while the app is open.
    window.addEventListener('jenny:data-changed', render);
  </script>
</body>
</html>
```

The SDK (`jenny-sdk.js`, load it in `<head>` before app code) handles everything transport-
and theme-related: it stamps the theme, exposes `jenny.action(name, params)` (resolves the
envelope object described above — `.records`/`.record`/`.deleted` for storage, `.data` for
http — throws `Error` with the structured message on failure), `jenny.discuss(text)`,
`jenny.navigate(label, state)` / `jenny.back()` (see below), and re-dispatches agent-side
data changes as the `jenny:data-changed` window event.

### Internal navigation and the Android back button

The app fills the whole screen and the phone's back button is the only way out of it. The
host SPA has no idea what the app is showing (the iframe has an opaque origin), so it asks:
**every internal screen change must be declared with `jenny.navigate()`, otherwise Back
closes the whole app instead of going up one level** — and the user loses the sub-screen,
the half-filled form, everything.

```js
function openDetail(id) {
  jenny.navigate('#detail', { id });   // declare the level BEFORE painting it
  paintDetail(id);
}

// Back (hardware button or jenny.back()) replays the previous level here:
window.addEventListener('popstate', (e) => {
  if (e.state && e.state.id) paintDetail(e.state.id);
  else paintList();
});
```

- `jenny.navigate(label, state)` pushes one logical level. `label` is only a readable name
  for the screen — the SDK deliberately never writes the browser history (entries pushed
  from the iframe end up in the WebView's joint history and survive the app being closed,
  leaving dead back presses behind). `state` comes back in the `popstate` event.
- `jenny.back()` pops one level and fires the synthetic `popstate`. Wire the app's own "←"
  buttons to it so they behave exactly like the hardware key.
- A `<dialog>` opened inside the app counts as a level automatically — the SDK watches for
  it and closes the topmost one on the first Back press. Nothing to declare, but do use
  `<dialog>` (or kit markup) rather than a hand-rolled `<div>` overlay, or Back will skip
  straight past it and close the app.

### Sandbox rules (the iframe is sandboxed — these WILL break the app if ignored)

- **No `alert()`, `confirm()`, `prompt()`** — the sandbox has no `allow-modals`; they
  silently do nothing. Build dialogs with `<dialog>` or kit markup.
- **No `<form>` at all** — there is no `allow-forms`, and the submission is blocked *before*
  the `submit` event is fired, so `event.preventDefault()` never runs and cannot rescue it.
  Use a plain `<button type="button">` with a click handler, add a `keydown` listener for
  Enter on the input, and call `jenny.action(...)` from the handler. `validate_app.py`
  rejects any `<form>` in `index.html`.
- **Never call the actions API with `fetch` directly, and never with POST or custom
  headers** — the gateway is GET-only and cannot answer CORS preflights. Always go through
  `jenny.action()`, which issues the correct simple GET.
- **Keep a single action's params under ~6 KB** (they travel in the request line).
- **Inline everything app-specific** — only the `app/` subfolder is web-served (manifest,
  AGENT.md and `data/` are never reachable over HTTP); prefer a single `app/index.html` and
  link only `/html-mobile/assets/...` shared resources.

### Rules

- Mobile-first; the iframe is full-screen on a phone.
- Every internal screen change goes through `jenny.navigate()`, and the app restores the
  previous screen on `popstate` — otherwise the back button closes the whole app instead of
  going up one level.
- All state changes go through actions — never write files or call external hosts directly
  (CORS and auth are handled by the gateway proxy).
- No external hosts anywhere (`https://...` in `src`/`href` fails validation); gateway paths
  (`/html-mobile/assets/...`) are the only allowed shared resources.
- Hand-off to chat (e.g. a "Discuss with Jenny" button on selected content) uses the SDK's
  `jenny.discuss(text)`; the reply arrives in chat, never inside the app.
