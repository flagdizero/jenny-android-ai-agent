# WebSocket Protocol

Jenny exposes a WebSocket server channel used by the Android WebView UI and any compatible client — this page documents the wire protocol for integrators writing their own client.

## On the Android device, this is not optional

Everything below describes the general-purpose channel as configured through `config.json`'s `websocket` object. On the shipped Android app, the runtime overrides several of these fields at startup regardless of what `config.json` says:

- The gateway binds host `127.0.0.1` and a single port, **18790**, shared by both the WebSocket upgrade and the HTTP `/api/` and `/webui/` routes — one origin for the WebView to talk to. These are the defaults of `run_gateway(data_dir, android_context=None, *, host="127.0.0.1", port=18790)`, and `run_gateway` **always** passes them on: at startup they overwrite `gateway.host`, `gateway.port`, `websocket.host` and `websocket.port` from config, on or off Android. Kotlin calls it with the defaults, so on the phone the values in `config.json` never win.
- `websocket.enabled` ends up `true` in practice: the auto-generated `config.json` created on first run writes `"websocket": {"enabled": true, ...}` explicitly, and the same override fills in `enabled: true` if the key is ever missing. The schema-level default of `enabled: false` (documented below) only applies when the gateway is started without that override — see [Quick Start](#quick-start-off-device--standalone-gateway).

So: the `enabled: false` default, the `8765` default port, and a custom `host` are real and correct for **off-device** use of this channel (running the gateway standalone on a workstation), but through `run_gateway` they take effect only if you pass them as `host=` and `port=`. The Android APK always ends up on `ws://127.0.0.1:18790/`. Everything else in the `websocket` object (`path`, `allowFrom`, the token and TLS fields, `streaming`) is read from `config.json` as documented.

## Features

- Bidirectional real-time communication over WebSocket
- Streaming support — receive agent responses token by token
- Secret-based authentication (single shared secret for WebSocket and HTTP APIs)
- One personal chat shared by every connection (`chat_id` `"default"`), plus one chat per project (`chat_id` `"project:<name>"`) that a connection follows only after asking for it
- TLS/SSL support (WSS) with enforced TLSv1.2 minimum
- Client allow-list via `allowFrom`
- Auto-cleanup of dead connections

## Quick Start (off-device / standalone gateway)

### 1. Configure

Add to `config.json` under the top-level `websocket` object:

```json
{
  "websocket": {
  "enabled": true,
  "host": "127.0.0.1",
  "port": 8765,
  "path": "/",
  "websocketRequiresToken": false,
  "allowFrom": ["*"],
  "streaming": true
  }
}
```

The default `host: 127.0.0.1` is intended for loopback use. External connections can be allowed by setting `host` to `"0.0.0.0"` (or `"::"`), which the config refuses unless `tokenIssueSecret` is set, and configuring `allowFrom` carefully.

Only `path`, `allowFrom`, the token and TLS fields and `streaming` are honoured from this block when you start the gateway through `run_gateway`: `host` and `port` are overwritten (see the next step), and `enabled` defaults to `true` when the key is missing.

### 2. Start the gateway

Use the same entry point the Android runtime uses, and pass the address you want:

```python
from jenny.android_entry import run_gateway
run_gateway("/path/to/data_dir", host="127.0.0.1", port=8765)
```

Note the first argument is a *data directory*, not the workspace itself — the gateway creates and uses `<data_dir>/workspace`. Passing a path that already ends in `workspace` produces a nested `workspace/workspace`.

Called as `run_gateway("/path/to/data_dir")`, with no `host=`/`port=`, it binds `127.0.0.1:18790` whatever `config.json` says — the same as the app. `host` and `port` set both the gateway and the WebSocket channel, since they share one port.

If you would rather have `websocket.host` and `websocket.port` come from `config.json`, skip `run_gateway` and call the lower-level `jenny.gateway_runtime._run_gateway(config=None)` yourself: with no overrides given, it loads the config as it is and the schema defaults (`enabled: false`, port `8765`) apply. It is a private, test-patchable function and it does none of the workspace preparation `run_gateway` does — you set the workspace (`jenny.config.paths.set_workspace_dir`) and create the config first.

You should see:

```text
WebSocket server listening on ws://127.0.0.1:8765/
```

with the host, port and path you actually ended up with (with no arguments to `run_gateway`, and on the Android device, it reads `ws://127.0.0.1:18790/`).

### 3. Connect a client

Connect to `ws://{host}:{port}{path}?client_id={id}&token={secret}` from the Android WebView or another WebSocket client. The wire protocol is described below.

## Connection URL

```text
ws://{host}:{port}{path}?client_id={id}&token={secret}
```

| Parameter | Required | Description |
|-----------|----------|-------------|
| `client_id` | No | Identifier for `allowFrom` authorization. Auto-generated as `anon-xxxxxxxxxxxx` if omitted. Truncated to 128 chars. |
| `token` | Conditional | The `token_issue_secret` value. Required when `websocketRequiresToken` is `true` (the default). |

## Wire Protocol

All frames are JSON text. A server frame names itself with an `event` field; a client envelope with a `type` field (plain-text legacy frames aside, see below).

### Server → Client

**`ready`** — sent immediately after connection is established:

```json
{
  "event": "ready",
  "chat_id": "default",
  "client_id": "alice",
  "conn_id": "3f9c0a7e5b1d4c2a8e6f0b9d7c5a3e1f"
}
```

`chat_id` is the personal chat, which the connection is already subscribed to. `conn_id` is a
random id the gateway gives this one connection; it is how a `ui_query` (below) targets the
connection that sent a turn rather than every client of the chat.

**`message`** — full agent response:

```json
{
  "event": "message",
  "chat_id": "default",
  "text": "Hello! How can I help?",
  "media": ["/data/…/workspace/out/chart.png"],
  "media_urls": [{"url": "/api/media/…", "name": "chart.png", "kind": "image", "path": "/data/…/workspace/out/chart.png"}]
}
```

`media` (local filesystem paths) and `media_urls` (one signed, fetchable URL per attachment,
with `kind` `image`/`video`/`file`) are present only when the reply has attachments;
`media_urls` leaves out a path the gateway could not sign. Other optional fields: `origin` (the
channel the turn came from, e.g. `"telegram"`), `latency_ms`, `tool_events`, `agent_ui`,
`session_boundary: true` (after `/new`: the client draws a separator instead of a bubble) and
`kind` — `"tool_hint"` or `"progress"` for an interim activity line rather than an answer.

**`user`** — a user message that entered from another channel (e.g. Telegram), echoed to the
subscribers of that chat so an open view shows it live:

```json
{"event": "user", "chat_id": "default", "text": "Remind me at 6", "origin": "telegram"}
```

With attachments it also carries `media_paths` and `media_urls` (same shape as in `message`).

**`delta`** — streaming text chunk (only when `streaming: true`):

```json
{
  "event": "delta",
  "chat_id": "default",
  "text": "Hello",
  "stream_id": "s1"
}
```

**`stream_end`** — signals the end of a streaming segment:

```json
{
  "event": "stream_end",
  "chat_id": "default",
  "stream_id": "s1"
}
```

**`reasoning_delta`** — incremental model reasoning / thinking chunk for the active assistant turn. Mirrors `delta` but targets the reasoning bubble above the answer rather than the answer body:

```json
{
  "event": "reasoning_delta",
  "chat_id": "default",
  "text": "Let me decompose ",
  "stream_id": "r1"
}
```

**`reasoning_end`** — close marker for the active reasoning stream. WebUI uses this to lock the in-place bubble and switch from the shimmer header to a static collapsed state:

```json
{
  "event": "reasoning_end",
  "chat_id": "default",
  "stream_id": "r1"
}
```

Reasoning frames only flow when the channel's `showReasoning` is `true` (default) and the model returns reasoning content (DeepSeek-R1 / Kimi / MiMo / OpenAI reasoning models, Anthropic extended thinking, or inline `<think>` / `<thought>` tags). Models without reasoning produce zero `reasoning_delta` frames.

**`runtime_model_updated`** — broadcast when the gateway runtime model changes, for example after `/model <preset>`:

```json
{
  "event": "runtime_model_updated",
  "model_name": "openai/gpt-4.1-mini",
  "model_preset": "fast"
}
```

`model_preset` is omitted when no named preset is active. The frame can also carry a `provider` field (the name of the active provider entry), present only when the gateway knows it. WebUI clients use this event to keep the displayed model badge in sync across slash commands, config reloads, and settings changes.

**`subagent_status`** — snapshot of the background subagents, pushed on every state transition (and, at most every 5 seconds, at the end of an iteration, so `last_tool` and `elapsed_s` stay fresh) to the chat that spawned them, and scoped to that chat's session (a project chat sees its own subagents, the personal chat its own; work started by silent internal turns publishes nothing):

```json
{
  "event": "subagent_status",
  "chat_id": "default",
  "running": [{
    "task_id": "d2ee4342", "lineage_id": "aa94c60b", "attempt": 1,
    "label": "fix parser", "agent_type": "coder", "state": "running",
    "phase": "awaiting_tools", "iteration": 2,
    "elapsed_s": 12.5, "idle_s": 0.5, "last_tool": "grep"
  }],
  "recent": [{
    "task_id": "822ead40", "lineage_id": "b202f4e6", "attempt": 1,
    "label": "price research", "agent_type": "researcher", "state": "failed",
    "stop_reason": "error", "result_summary": "page not reachable",
    "ended_at": 1785841304.462998, "can_restart": true
  }]
}
```

`state` is one of `running`, `done`, `failed`, `cancelled`, `stalled`; `recent` is newest-first and capped at 10 entries. `idle_s` is seconds since the last observed sign of progress — it is what distinguishes a subagent stuck for four minutes from one working for four minutes. `can_restart` reflects the cap on *automatic* restarts only: a human pressing Relaunch is never refused.

The frame is a recomputable refresh hint — it is never persisted to the transcript and never retried, since the next snapshot replaces it. The same payload is served verbatim by `GET /api/subagents`, which is how the WebUI panel comes back after a page reload instead of waiting for the next transition; pass `?session_key=` (`websocket:default` or `project:<name>`) for the same per-chat scope the frame has. `POST`-style actions ride on GET like every other gateway route (see the transport constraint in [Write a mini-app](../contribute/write-a-mini-app.md)): `GET /api/subagents/<task_id>/restart` (always a manual relaunch) and `GET /api/subagents/<task_id>/cancel`.

**`work_status`** — the state of a long piece of work that someone is waiting for in a chat: a gardener pass (`/gardener`, or an automatic pass on the notebook you have open) or a Dream cycle started with `/dream`. The home WebUI draws it as the *work bubble* at the bottom of the chat. It goes only to the connections subscribed to that `chat_id` (`project:<name>` for a notebook, `default` for the personal chat), so an automatic pass on a notebook nobody is looking at reaches nobody:

```json
{
  "event": "work_status",
  "chat_id": "project:orto",
  "id": "gardener-3f9a1c2b7d4e",
  "kind": "gardener",
  "state": "running",
  "manual": true,
  "phases": ["pages", "board"],
  "phase": "pages",
  "writes": 3,
  "retry": {"attempt": 2, "of": 3},
  "started_at": 1791215890.9,
  "updated_at": 1791215962.1,
  "project": "orto"
}
```

`kind` is `gardener` or `dream`; `state` is `running`, `done` or `failed`. `phases` are the steps of the work (`pages` and `board` for the gardener, `review` and `memory` for Dream — Dream learns whether it reviews only once it has started, so its first frame may have none). `writes` counts the page writes as they happen. `retry` is set while the provider is retrying a failed model call and clears when the model answers. `manual: false` marks an automatic gardener pass: a message in that notebook makes it stand down. A finished frame adds `ended_at` and an `outcome` of plain fields, never prose — for the gardener `status`, `writes`, `board`, `proposals` (proposals waiting in the board's envelope), `absence` (why the notebook has no board) and, when it failed, `reason`; for Dream `status` and `reviewed`. Updates for the same work are throttled to one every 2 seconds; transitions go out at once. Like `subagent_status` it is never persisted and never retried. `GET /api/work?session_key=` (`websocket:default` or `project:<name>`) serves `{"work": [...]}`, the same payloads for the work in progress and for work finished in the last minute, which is how the bubble comes back after a reload. The command's text replies — the immediate `Gardening <name>...` / `Dreaming...` and the final report — carry the same id as `work_id` on their `message` frame (and in the transcript), so a client that draws the bubble can skip them.

`GET /api/subagents/<task_id>/digest` serves the condensed "what did it do" of one subagent, for the block the chat shows under its result: `{"task_id", "events", "count", "source"}`, up to 300 events. `source` says how complete it is — `"digest"` is the persisted, immutable one written when the subagent finished, `"live"` is a preview built from the running subagent's activity (it will change), and `"none"` means there is nothing to show (empty `events`, never a 404). Unlike `/activity`, it has no cursor.

**`subagent_activity`** — the fine-grained activity of one subagent, sent **only** to the
connections that asked for it with `subagent_watch` (see [Client → Server](#client--server)):

```json
{
  "event": "subagent_activity", "chat_id": "default", "task_id": "d2ee4342",
  "events": [{"seq": 41, "ts": 1785841290.1, "kind": "tool_end", "name": "grep",
              "call_id": "call_1", "status": "ok", "summary": "grep parser.py: 3 matches",
              "duration_ms": 120}],
  "since_seq": 40, "first_seq": 41, "last_seq": 41, "latest_seq": 41,
  "dropped": 0, "gap": false, "initial": true
}
```

The reply to a watch comes at once, even when empty (`latest_seq: 0` means nothing has
happened yet), and carries `initial: true`: the client replaces its list instead of appending.
After that the gateway pushes new events about every 0.4 s while someone is watching, at most 40
per frame. `gap: true` means the window does not start right after `since_seq` — events were
lost — and the client should resync with `GET /api/subagents/<task_id>/activity`, which serves
the same shape. Never persisted. A frame that fails to send is not retried as such, but that
watcher's cursor does not move, so the next tick sends the same events again.

**`subagent_unwatched`** — a watch ended: `reason` is `"client"` (the answer to
`subagent_unwatch`) or `"watch_limit"` (a connection watches at most 3 tasks, and the oldest
watch was dropped to make room):

```json
{"event": "subagent_unwatched", "task_id": "d2ee4342", "reason": "client"}
```

**`attached`** — confirmation for `attach` inbound envelopes, with the chat the connection is
now subscribed to (see [The shared chat](#the-shared-chat)):

```json
{"event": "attached", "chat_id": "project:garden"}
```

**`detached`** — confirmation for a `detach` envelope: the connection no longer receives that
project chat's frames. Sent only for a project chat; the personal chat cannot be left:

```json
{"event": "detached", "chat_id": "project:garden"}
```

**`ui_query`** — the gateway asks one connection what is on its screen (the agent's `ui_view`
tool). Sent only to the connection that sent the turn (by `conn_id`), never fanned out; the
client answers with a `ui_result` frame carrying the same `correlation_id`, within 6 seconds:

```json
{"event": "ui_query", "correlation_id": "uiq-5d0c9b2e7a4f41c6b8e3a1f0d2c4b6a8"}
```

**`turn_end`** — the turn is over; `latency_ms` is present when it was measured:

```json
{"event": "turn_end", "chat_id": "default", "latency_ms": 4210}
```

**`goal_status`** — a turn started or finished, as a wall-clock hint for the UI. Sent only to
subscribers of that chat, and never retried: it is an idempotent refresh hint, so the next
status simply replaces a pending one. `started_at` appears only with `"running"`:

```json
{"event": "goal_status", "chat_id": "default", "status": "running", "started_at": 1756640000.0}
```

**`mascot_mood`** — how Jenny feels about the reply she just gave, for the on-screen mascot,
read by the server from the emoji in that reply (no model request). Sent after `turn_end`, only to subscribers of that chat, never retried and never persisted: the
next turn replaces it, and a reload starts from a neutral face. `mood` is one of `happy`, `sad`,
`angry` — the three expressions that exist as artwork (a neutral verdict sends nothing). `turn_id` is present when the turn had
one, so the client can drop a reaction to a reply that is no longer the latest. Off with
`agents.defaults.mascotMood: false` (see [Configuration](configuration.md)):

```json
{"event": "mascot_mood", "chat_id": "default", "mood": "happy", "turn_id": "webui:A"}
```

**`file_edit`** — one or more file edits made during the turn, with their line counts. Also
appended to the transcript, so a reload replays it:

```json
{"event": "file_edit", "chat_id": "default", "edits": [{"path": "SOUL.md", "added": 2, "deleted": 1, "status": "done"}]}
```

**`app_data_changed`** — a Jenny App's stored data changed, so an open app iframe should
refresh itself. Broadcast to every connection, not just one chat's subscribers:

```json
{"event": "app_data_changed", "slug": "notes"}
```

**`apps_list_changed`** — an app was installed, removed or edited; the Apps view should
re-read the list. Broadcast, and carries no payload beyond the event name:

```json
{"event": "apps_list_changed"}
```

**`error`** — soft error for malformed inbound envelopes. The connection stays open. `detail`
is human-readable, `reason` is the stable token for a client to branch on (`missing_content`,
`unknown_type`, `invalid_task_id`, `invalid_project_name`, or — with `detail: "image_rejected"`
— one of `malformed`, `decode`, `size`, `too_many_images`, `too_many_videos`, `too_many_files`):

```json
{"event": "error", "detail": "missing content", "reason": "missing_content"}
```

**`rpc_result`** — the single reply to an `rpc` request, correlated by `id`. See
[Commands (rpc)](#commands-rpc):

```json
{"event": "rpc_result", "id": "rpc-9f2a", "ok": true, "result": {"path": "SOUL.md", "bytes": 4213}}
```

### Client → Server

**Legacy (default chat):** send a plain string, or a JSON object with a recognized text field:

```json
"Hello Jenny!"
```

```json
{"content": "Hello Jenny!"}
```

Recognized fields: `content`, `text`, `message` (checked in that order). Invalid JSON is treated as plain text. These frames route to the personal chat (`default`, announced in `ready`).

**Typed envelopes:** any JSON object with a string `type` field is a typed envelope:

| `type` | Fields | Effect |
|--------|--------|--------|
| `attach` | `chat_id` | Subscribe the connection to a chat, e.g. a project's, or again after a reconnect. Replies with `attached`. |
| `detach` | `chat_id` | Stop following a project chat (`"project:<name>"`). Replies with `detached`. Anything else — the personal chat, a missing or unrecognised `chat_id` — is ignored with no reply. |
| `message` | `content`, `chat_id`, `media`, `webui`, `turn_id`, `readonly` | Send a user message on that chat, subscribing the connection to it if it was not already. |
| `subagent_watch` | `task_id`, `since` | Start receiving `subagent_activity` frames for that subagent, from the event after `since` (default `0`). An unknown task is not an error: the answer is an empty window and the watch stays. |
| `subagent_unwatch` | `task_id` | Stop them. Replies with `subagent_unwatched`. Idempotent. |
| `ui_result` | `correlation_id`, `payload` or `error` | Answer a `ui_query`. `payload` is a JSON object of at most 256 KB (the WebUI sends `{view, drawer, html, app?}`); `error` reports that the client could not collect it. An answer from a connection other than the one asked, or for an unknown or expired `correlation_id`, is dropped. |
| `rpc` | `id`, `method`, `params` | Run a gateway command and reply with `rpc_result`. See [Commands (rpc)](#commands-rpc). |

**`chat_id`** (in `attach`, `detach` and `message`) picks the conversation: `"project:<name>"`
is that project's chat, and anything else — missing, `"default"`, an unknown string — is the
personal chat. A project-shaped id whose name cannot be a project (letters, digits, `.`, `_`, `-`,
at most 64 characters, starting with a letter or digit) is refused with an `error` whose `reason`
is `invalid_project_name`, and nothing is attached or written.

**`message` fields.** `content` is a required string; it may be empty only when `media` is
present. `media` is a list of `{"data_url": "data:<mime>;base64,…", "name"?: "photo.jpg"}`
items, decoded and saved by the gateway before the turn starts: at most 4 images (PNG, JPEG,
WebP, GIF; 8 MB each), 1 video (MP4, WebM, QuickTime; 20 MB) and 4 other files (20 MB each). If
one item fails, nothing is kept and the answer is an `error` with `detail: "image_rejected"`.
`webui: true` plus an optional `turn_id` mark the message as sent by the WebUI, and
`readonly: true` runs that one turn with the workspace read-only (any other value is ignored).

```json
{"type": "message", "chat_id": "project:garden", "content": "What did we plant in May?", "webui": true}
```

See [The shared chat](#the-shared-chat) for the full flow.

## Commands (rpc)

Operations that carry **content** — the text of a file, a free-text note — travel on this
channel, not on `/api/`. The HTTP surface is served from the WebSocket handshake hook, which
never reads a request body: its parameters can only ride the query string or a header, where
they are capped at 8192 bytes per line and restricted to ISO-8859-1 (a browser refuses to
put an emoji in a header at all). A WebSocket frame is framed and UTF-8, so a file with
emoji in it goes through unchanged.

Two more kinds of operation travel here for the same reason — the query string is the only
other place their parameters could go. Writes to the workspace (delete, rename, copy), which
a read-only `/api/` surface should not carry; and settings that carry a secret (a provider's
API key — the first one too, from onboarding —, the Telegram bot token, an SSH password),
which in a request line would end up in access logs and anywhere a URL is logged.

Request:

```json
{"type": "rpc", "id": "rpc-9f2a", "method": "workspace.write",
 "params": {"path": "SOUL.md", "content": "# Chi sono\n…"}}
```

Reply — always one `rpc_result` per request, correlated by the opaque `id`:

```json
{"event": "rpc_result", "id": "rpc-9f2a", "ok": true, "result": {"path": "SOUL.md", "bytes": 4213}}
```

```json
{"event": "rpc_result", "id": "rpc-9f2a", "ok": false,
 "error": {"code": "too_large", "message": "file too large to save (1300000 > 1000000 bytes)"}}
```

Error codes: `bad_request`, `forbidden`, `not_found`, `too_large`, `conflict`, `name_taken`, `unavailable`, `internal`.
`name_taken` means the name asked for already belongs to something else (today: a notebook's new name is already a folder's or a conversation's).
`conflict` is the only one that is not about the request but about the world: the request was fine, and the world moved underneath it — the file changed since the client read it (`page.write`, `workspace.write` with a `base`), or Jenny is still writing in that notebook (`project.rename`, `project.delete`). A client that gets it should not correct what it sent: re-read, or try again once she has finished.
A frame whose `id` is missing or malformed is dropped with a log line — there is nothing to
correlate a reply to.

| `method` | `params` | Effect |
|----------|----------|--------|
| `workspace.write` | `path`, `content`, `base` | Write a workspace text file (1 MB cap). `base`, optional, is the text the editor opened: if the file changed underneath (line endings aside), the answer is `conflict` and nothing is written. For the live `config.json` it is required, the save goes through the configuration store, and the reply carries in `content` the text now on disk — the base of the next save. Honours `workspace.enabled` / `workspace.allow_write`. |
| `workspace.delete` | `path` | Delete a workspace file or folder (the file manager's Delete). A symlink is removed as a link, never followed. Refused with `forbidden` for the workspace root, `wikis/` and any folder that holds notebooks (a notebook goes with `project.delete`). Honours `workspace.enabled` / `workspace.allow_delete`. |
| `workspace.rename` | `old_path`, `new_path` | Rename or move a workspace file or folder. Never over something that exists (`name_taken`, except a change of case only), never a notebook folder or an ancestor of one (that is `project.rename`). A symlink is renamed as a link. Honours `workspace.enabled` / `workspace.allow_write`. |
| `workspace.copy` | `path`, `dest` | Copy a workspace file or folder. Without `dest` the copy goes next to the original under a free name (`note (copy).md`). Never over something that exists. Refused with `forbidden` for the workspace root (next to it is outside the workspace) and any folder that holds notebooks, and with `bad_request` for a folder copied into itself. Honours `workspace.enabled` / `workspace.allow_write`. |
| `soul.rules.write` | `content` | Save the user's standing rules (2 000-character cap) and re-project them into the marked block in `SOUL.md`. Honours the same workspace flags. |
| `page.write` | `wiki`, `page`, `content`, `base` | Save a notebook page edited by hand from the reader. `base` is the markdown the editor opened on: if the file changed underneath, the answer is `conflict` and nothing is written. Honours `wiki.enabled` plus the same workspace flags. |
| `audit.create` | `wiki`, `target`, `sel_start`, `sel_end`, `comment`, `author` | File a report anchored to a passage of a notebook page. `sel_start`/`sel_end` are offsets in the page's markdown source, which the gateway re-reads to compute the anchors; `target` defaults to `index.md`, `author` to `anonymous`. Replies `{id, filename, path}`. An unknown notebook is `bad_request`, a target outside the notebook's pages `forbidden`, a missing page `not_found`, a selection that cannot anchor `bad_request`. Honours `wiki.enabled`, and refuses with `unavailable` when the configuration cannot be read. |
| `project.create` | `name`, `seed` | Create a project chat with its seed instruction. Both are required and whitespace-collapsed. |
| `project.delete` | `name` | Delete a project chat and its session. Refused with `conflict` before anything is touched while something is still writing under that notebook (a turn, a subagent started there, a gardener pass, the autocompact), and with `bad_request` if the name is no project. A home page pinned to it goes with it. |
| `project.rename` | `name`, `new_name` | Rename a project: its folder first, then its chat follows (the same order as a folder renamed by hand, which the gateway already knows how to finish after a crash). Refused before anything is touched if the new name would not open (`bad_request`), is already taken by a folder or a conversation (`name_taken`), or the old name is not a notebook (`not_found`), and with `conflict` while something is still writing under either name (a turn, a subagent started there, a gardener pass, the autocompact). A home page pinned to it follows the new name. |
| `home.pages.set` | `pages`, `order` | Save the home pages: the whole list of added pages (`{id, kind, ref}`, at most 8, unique ids) and the order of every page, fixed ones included — each exactly once, or the answer is `bad_request` and nothing is written. Replies `{ok, pages, order}`. The list is read with `GET /api/home/pages`, which answers `{pages, order, fixed, max, kinds}`. |
| `settings.provider.models` | `provider`, `format`, `api_base`, `api_key` | List a provider's models, also for a provider not saved yet (onboarding and the provider card ask with the key just typed). Read-only: the configuration is not touched. Replies `{status, message, model_count, …}`. |
| `settings.provider.update` | `name`, `format`, `api_base`, `api_key`, `ca_bundle`, `ca_bundle_clear` | Create or update a provider with its API key, then hot-reload the active provider. |
| `telegram.save` | `token` | Save the Telegram bot token (checked with `getMe`) and restart the channel. |
| `ssh.host.save` | `alias`, `host`, `port`, `username`, `auth`, `password`, `description`, `job_log_dir` | Create or update an SSH host. A missing `password` keeps the saved one. |
| `onboarding.save` | `provider_name`, `format`, `api_key`, `api_base`, `model`, `bot_name`, `bot_icon`, `locale` | The first-run setup: save the provider with its API key, the model and Jenny's name, then wake the agent that was waiting for it and write the welcome message into the conversation. Replies `{status, chat_id, welcome_message}`. A missing provider or model is `bad_request` and nothing is saved. |
| `apps.keep` | `slug` | "Keep it" on an app on trial: the draft becomes the app (its data kept when the app already existed) and its code is approved — the hash and what it touches, stored outside the workspace. With no draft, approves the code the app has now (an app changed outside Jenny). An app with a reader reads once right away. Replies the app's trust `state`. |
| `apps.discard` | `slug` | Throw a trial away. An app that already existed stays as it was. |
| `apps.trash` | — | The app trash, newest first: `{trash: [{id, slug, name, at, records}]}`. Deleting an app moves its folder here, data included. |
| `apps.restore` | `id` | Put an app back from the trash (`id` is the trash entry). `conflict` when an app with that slug exists again. |
| `apps.purge` | `id` | Delete a trash entry for good, data included. The only way an app's files are erased. |
| `board.setup` | `wiki`, `skeleton`, `remind` | The guided birth of a notebook: the drawer sections every card will have (`skeleton`, a list or a comma-separated line) and what the user wants to be reminded of. Both optional; the first board pass uses the user's skeleton instead of proposing one. |
| `board.seen` | `wiki`, `card` | Record that the user looked: the notebook's board (no `card`), or one card's drawer. Drives the "since you last looked" row and the blue dots. A notebook without a board is a no-op. |
| `board.move` | `wiki`, `kind`, `id`, `x`, `y` | Move a card (`kind: card`) or a reminder's sticky note (`kind: note`) to `x`,`y` percent of the board. The position becomes the user's, and the gardener never moves it again. |
| `board.card.add` | `wiki`, `title`, `near`?, `glyph`?, `app`?, `link`? | Make a card yourself. With `app` (`{slug, params}`) the card **is** that app: its value takes the place of the photo, a tap opens the app, and the title defaults to the app's name; with `link` (a card id) it is tied to that card by a thread and lands next to it. It lands in the free spot nearest `near` (`{x, y}` in board coordinates: the point the user held on the empty wall, or the centre of the view), with no pages yet; its title and place are the user's. A full board refuses with `conflict`. Replies `{card: {id, title, pos}}`. |
| `board.card.rename` | `wiki`, `id`, `title` | Rename a card. The title becomes the user's, and the gardener no longer rewrites it. |
| `board.card.remove` | `wiki`, `id` | Remove a card from the board, with an undo. The card, its threads and its place are kept for 7 days (at most 5 cards); its notebook crons and micro apps are paused and its reminders stay quiet meanwhile. The wiki is not touched. Replies `{title}`. |
| `board.card.restore` | `wiki`, `id` | The undo of `board.card.remove`: the card goes back where it was, with its threads, and what it paused resumes. Pages another card took meanwhile stay with that card. `conflict` when the board is full. |
| `board.suggestion.respond` | `wiki`, `id`, `answer`, `near`? | Answer one of Jenny's proposals in the envelope with `yes`, `no` or `later`. `yes` applies it (a card placed in a free spot — near `near`, `{x, y}` in board coordinates, the centre of what the user is looking at, when given — a thread, a page attached…); `no` is remembered for that subject and kind; `later` defers it for a few passes. A full board refuses a new card with `conflict`. |
| `board.connection.remove` | `wiki`, `a`, `b` | Remove the thread between two cards. Only the user removes threads. |
| `board.offboard.want` | `wiki`, `page`, `from` | "I want to see it on the board": a page that is in no drawer is flagged for the next board pass, which attaches it to a drawer or proposes a card. |
| `board.preview` | `wiki`, `page` | Count one preview of a page that is in no drawer. The board pass reads the count: a page the user keeps peeking at is one the board is neglecting. |
| `board.fix` | `wiki`, `card`, `section`, `text` | Correct a drawer section. Nothing on the board is written: the correction becomes an audit (`source: board`) on the first page the section comes from, the board pass applies it to the wiki and the summary is rewritten from there. Replies `{id, path, target}`. |
| `board.section.add` | `wiki`, `card`, `kind`, … | Add a section to a drawer: `note` (`text`, also journaled as a `[said]` line), `reminder` (`text`, `when`) or `photo` (`mime`, base64 `data`, `caption`; saved under `raw/photos/`). An app is never a section (`kind: "app"` is refused: it is a card of its own, see `board.card.add`). Replies `{id}`. |
| `board.section.update` | `wiki`, `card`, `id`, `text`, `when` | Edit a note (a new journal line: the journal is append-only) or a reminder. |
| `board.section.remove` | `wiki`, `card`, `id` | Remove a section the user added. The wiki is not touched; a reminder section drops its reminder, a micro app goes to `board/micro/.trash/` and loses its approval, a notebook cron is removed from the scheduler. |
| `board.section.reorder` | `wiki`, `card`, `order` | Reorder the sections the user added (`order` is their ids). |
| `board.reminder.done` | `wiki`, `id` | Mark a reminder done: it leaves the board and becomes a journal line. |
| `board.micro.keep` | `wiki`, `micro` | Keep a micro app on trial: the draft becomes live and its code is approved (the hash is stored outside the workspace). Also re-approves code changed outside Jenny. |
| `board.micro.discard` | `wiki`, `micro` | Throw a trial away; with no live version, its section goes too. |
| `board.micro.run` | `wiki`, `micro` | Read a micro app now. Refused (`conflict`) when its code is not approved. |
| `board.micro.pause` | `wiki`, `micro`, `paused` | Pause (`true`) or resume (`false`) a micro app. |
| `board.cron.pause` | `wiki`, `job`, `paused` | Pause (`true`) or resume (`false`) a notebook cron from its drawer; only a job bound to this notebook. |

**Authorization is the handshake's, not the frame's.** Only a connection that presented
`token_issue_secret` at handshake time may run a command, even if `websocket_requires_token`
is `false` — otherwise a mutation would sit on a weaker gate than `/api/`. And like `/api/`,
which answers `401` to everyone when no secret is set, without a secret every command is
refused with `forbidden`. Commands live in `jenny/webui/commands.py`
(transport-agnostic); the frame handling is `jenny/channels/ws_rpc.py`.

## Configuration Reference

All fields go under the top-level `websocket` object in `config.json`. These are the schema-level defaults — remember that on the Android device `enabled`, `host`, and `port` are overridden at startup as described above, regardless of what is written here.

### Connection

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable the WebSocket server. The Android runtime writes `true` via `setdefault`, so an explicit `false` you put here survives and does disable the channel — `channels/dispatcher.py` reads it and returns. |
| `host` | string | `"127.0.0.1"` | Bind address. Use `"0.0.0.0"` to accept external connections; `"0.0.0.0"` and `"::"` are refused at load time unless `tokenIssueSecret` is set. Forced to `127.0.0.1` on Android. |
| `port` | int | `8765` | Listen port. Forced to `18790` (shared with HTTP) on Android. |
| `path` | string | `"/"` | WebSocket upgrade path. Trailing slashes are normalized (root `/` is preserved). |
| `maxMessageBytes` | int | `37748736` | Maximum inbound message size in bytes (1 KB – 40 MB). Default (36 MB) is sized to accept up to 4 base64-encoded image attachments at ~6 MB each after the client's Worker normalization: 4 × 6 MB × 1.37 for base64 overhead, plus envelope framing, stays under it. Lower it if the channel only carries text. |

### Authentication

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `websocketRequiresToken` | bool | `true` | When `true`, clients must present `token_issue_secret` as `?token=...` during the WebSocket handshake. Set to `false` to allow unauthenticated connections (only safe for local/trusted networks). |
| `tokenIssueSecret` | string | `""` | Shared secret for WebSocket and HTTP API authentication. If empty, the bootstrap endpoint falls back to localhost-only on `127.0.0.1`/`::1`, and HTTP API routes reject all requests. Must be set for non-loopback deployments. On Android this is auto-generated per install (see [Security Notes](#security-notes)) and should never be edited out. |

### Access Control

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `allowFrom` | list of string | `["*"]` | Allowed `client_id` values. `"*"` allows all; `[]` denies all. |

### Streaming

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `streaming` | bool | `true` | Enable streaming mode. The agent sends `delta` + `stream_end` frames instead of a single `message`. |
| `sendProgress` | bool | `true` | Send interim progress text while a turn runs. With it off the client sees nothing until the turn produces its answer. |
| `sendToolHints` | bool | `false` | Include one-line tool hints in that progress stream ("reading SOUL.md", "searching…"). Off by default: it is the noisiest of the four. With it off the hint text is dropped but the `tool_events` it carried (the tools that are starting) still arrive, as a `progress` message with no text, wherever `sendProgress` is on. |
| `showReasoning` | bool | `true` | Forward `reasoning_delta` / `reasoning_end` frames when the provider exposes incremental reasoning. |
| `sendMaxRetries` | int | `3` | Attempts the dispatcher makes for one outbound frame before dropping it. Refresh-hint frames (`goal_status`, `mascot_mood`, `subagent_status`, `work_status`, `runtime_model_updated`, `app_data_changed`, `apps_list_changed`) are exempt by design: the next one replaces a pending one, so retrying them is pointless. `subagent_activity` is not retried as a frame either — the watcher's cursor simply does not move, so the next tick resends the same events. |

### Keep-alive

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `pingIntervalS` | float | `20.0` | WebSocket ping interval in seconds (5 – 300). |
| `pingTimeoutS` | float | `20.0` | Time to wait for a pong before closing the connection (5 – 300). |

### TLS/SSL

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `sslCertfile` | string | `""` | Path to the TLS certificate file (PEM). Both `sslCertfile` and `sslKeyfile` must be set to enable WSS. |
| `sslKeyfile` | string | `""` | Path to the TLS private key file (PEM). Minimum TLS version is enforced as TLSv1.2. |

## Authentication Flow

For production deployments where `websocketRequiresToken: true` (the default, and the effective state on Android), clients authenticate with the shared `tokenIssueSecret` directly.

### How it works

1. The legitimate client (e.g. the Android WebView) reads `token_issue_secret` from the private workspace `config.json`.
2. The client calls `GET /webui/bootstrap` with `Authorization: Bearer <secret>` or `X-Jenny-Auth: <secret>` to receive connection metadata (WebSocket URL, model name, etc.).
3. The client opens the WebSocket with `?token=<secret>&client_id=...`.
4. The same secret is used for all subsequent HTTP API requests via `Authorization: Bearer <secret>`.

On the Android app specifically, Kotlin reads the secret from `config.json` and hands it to the WebView as a URL **fragment** (`#bs=...`), never as a query parameter, so it never ends up in logs or server-side request records; the WebView's JavaScript exchanges it at `/webui/bootstrap` for the actual WebSocket URL.

### Example setup

```json
{
  "websocket": {
  "enabled": true,
  "port": 8765,
  "path": "/ws",
  "tokenIssueSecret": "your-secret-here",
  "websocketRequiresToken": true,
  "allowFrom": ["*"],
  "streaming": true
  }
}
```

Client flow:

1. Read `websocket.token_issue_secret` from the app's private workspace.
2. Call `GET /webui/bootstrap` with `X-Jenny-Auth: your-secret-here`.
3. Connect to the WebSocket with `?client_id=alice&token=your-secret-here`.
4. Call HTTP APIs with `Authorization: Bearer your-secret-here`.

## The shared chat

Every connection starts subscribed to the personal chat (`chat_id` `"default"`), and every client presenting a valid credential sees and shares that same conversation (e.g. several Android activities, or desktop browser tabs during local testing). There is no per-connection or per-user chat. Each project also has its own chat, `chat_id` `"project:<name>"`: a connection receives its frames only after an `attach` (or a `message`) naming it, and until it sends `detach`.

### Typical flow

```text
client                                   server
  | --- connect ----------------------->  |
  | <-- {"event":"ready",                 |
  |      "chat_id":"default", ...}        |
  |                                       |
  | --- {"type":"message",                |
  |      "content":"hi"} -------------->  |
  | <-- {"event":"delta", ...}            |
  | <-- {"event":"stream_end", ...}       |
  |                                       |
  | --- {"type":"attach",                 |  # the user opens a project
  |      "chat_id":"project:garden"} -->  |
  | <-- {"event":"attached",              |
  |      "chat_id":"project:garden"}      |
  |                                       |
  | --- {"type":"detach",                 |  # ...and leaves it
  |      "chat_id":"project:garden"} -->  |
  | <-- {"event":"detached",              |
  |      "chat_id":"project:garden"}      |
```

After a reconnect the connection is back on the personal chat only, so a client re-sends `attach` for each project chat it still shows.

### Rules

- A chat's frames (`message`, `user`, `delta`, `stream_end`, `reasoning_*`, `turn_end`, `goal_status`, `mascot_mood`, `file_edit`, `subagent_status`, `work_status`) carry the `chat_id` they belong to and go only to the connections subscribed to it.
- Some frames carry no `chat_id`: `runtime_model_updated`, `app_data_changed` and `apps_list_changed` go to every connection; `error`, `rpc_result`, `ui_query` and `subagent_unwatched` go to one.
- The personal chat cannot be left: proactive messages and the mascot's frames reach a connection there even while it is showing a project.
- Errors (invalid envelope, unknown `type`, missing `content`, an impossible project name) are soft: the server replies with `{"event":"error","detail":"...","reason":"..."}` and keeps the connection open.

### Backward compatibility

Legacy clients that only send plain text or `{"content": ...}` keep working unchanged: those frames route to the personal chat. No config flag is needed.

### Security boundary

Anyone holding a valid WebSocket auth credential joins the personal conversation and sees its output, and can attach to any project chat. This is safe for Jenny's local, single-user model; auth on the handshake is the single line of defense.

## Security Notes

- **Timing-safe comparison**: Secret validation uses `hmac.compare_digest` to prevent timing attacks.
- **Defense in depth**: `allowFrom` is checked at both the HTTP handshake level and the message level.
- **Shared chat**: see [The shared chat](#the-shared-chat). Auth on the WebSocket handshake is the single line of defense; callers who pass it join the unified conversation.
- **TLS enforcement**: When SSL is enabled, TLSv1.2 is the minimum allowed version.
- **Default-secure**: `websocketRequiresToken` defaults to `true`. Explicitly set it to `false` only on trusted networks.
- **Per-install secret on Android**: the app generates `token_issue_secret` once at first boot (`secrets.token_urlsafe(32)`) and writes it into `config.json` with permissions restricted to `0600`. If you strip it out by hand, it is silently regenerated on the next start (unless a `token` field is already set, which is left untouched). It is never transmitted anywhere except between the WebView and the local gateway.

## Media Files

Outbound `message` events may include a `media` field containing local filesystem paths. Remote clients cannot access these files directly — they need either:

- A shared filesystem mount, or
- An HTTP file server serving the Jenny media directory

## Common Patterns

These are off-device / standalone-gateway patterns — they do not apply to the Android app, which always forces `host: 127.0.0.1`, `port: 18790`, `enabled: true`. The `host` and `port` in the examples below reach the server only if you start the gateway with `run_gateway(data_dir, host=..., port=...)` (or through `_run_gateway(config=None)`, see [Quick Start](#quick-start-off-device--standalone-gateway)): a bare `run_gateway(data_dir)` overwrites them with `127.0.0.1:18790`.

### Trusted local network (no auth)

```json
{
  "websocket": {
  "enabled": true,
  "host": "192.168.1.50",
  "port": 8765,
  "websocketRequiresToken": false,
  "allowFrom": ["*"],
  "streaming": true
  }
}
```

Bind the machine's own LAN address (here `192.168.1.50`), not `0.0.0.0`: a wildcard host
(`"0.0.0.0"` or `"::"`) with no `tokenIssueSecret` is refused when the config is loaded
(`host is 0.0.0.0 (all interfaces) but token_issue_secret is not set`). Without a secret the
chat and the `rpc` commands are open to anyone who can reach the port, while every `/api/`
route refuses every request.

### Shared secret (simple auth)

```json
{
  "websocket": {
  "enabled": true,
  "tokenIssueSecret": "my-shared-secret",
  "allowFrom": ["alice", "bob"]
  }
}
```

Clients connect with `?token=my-shared-secret&client_id=alice` and send HTTP APIs with `Authorization: Bearer my-shared-secret`.

### Public endpoint with authentication

```json
{
  "websocket": {
  "enabled": true,
  "host": "0.0.0.0",
  "port": 8765,
  "path": "/ws",
  "tokenIssueSecret": "production-secret",
  "websocketRequiresToken": true,
  "sslCertfile": "/etc/ssl/certs/server.pem",
  "sslKeyfile": "/etc/ssl/private/server-key.pem",
  "allowFrom": ["*"]
  }
}
```

### Custom path

```json
{
  "websocket": {
  "enabled": true,
  "path": "/chat/ws",
  "allowFrom": ["*"]
  }
}
```

Clients connect to `ws://127.0.0.1:8765/chat/ws?client_id=...`. Trailing slashes are normalized, so `/chat/ws/` works the same.

## See also

- [Configuration reference](configuration.md) — full `config.json` field reference, including `websocket.*`.
- [Settings](settings.md) — what is and is not exposed in the WebUI for this channel (none of `websocket.*` is UI-configurable).
- [Security model](../internals/security-model.md) — how the bootstrap secret fits into Jenny's overall security boundaries.
