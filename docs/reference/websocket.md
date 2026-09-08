# WebSocket Protocol

Jenny exposes a WebSocket server channel used by the Android WebView UI and any compatible client — this page documents the wire protocol for integrators writing their own client.

## On the Android device, this is not optional

Everything below describes the general-purpose channel as configured through `config.json`'s `websocket` object. On the shipped Android app, the runtime overrides several of these fields at startup regardless of what `config.json` says:

- The gateway always binds host `127.0.0.1` and a single port, **18790**, shared by both the WebSocket upgrade and the HTTP `/api/` and `/webui/` routes — one origin for the WebView to talk to. This overrides `gateway.port` / `websocket.port` from config every time the app starts.
- `websocket.enabled` ends up `true` in practice: the auto-generated `config.json` created on first run writes `"websocket": {"enabled": true, ...}` explicitly, and the startup path additionally fills in `enabled: true` if the key is ever missing. The schema-level default of `enabled: false` (documented below) only applies when the gateway is run detached from the Android runtime — e.g. invoking `run_gateway(...)` yourself for local testing, where you own `config.json` and nothing forces it on.

So: the `enabled: false` default, the `8765` default port, and a custom `host`/`path` are all real and correct for **off-device** use of this channel (running the gateway standalone on a workstation), but not for the Android APK, which always ends up on `ws://127.0.0.1:18790/`.

## Features

- Bidirectional real-time communication over WebSocket
- Streaming support — receive agent responses token by token
- Secret-based authentication (single shared secret for WebSocket and HTTP APIs)
- Single shared chat — every connection joins the unified conversation (`chat_id` always `"default"`)
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

The default `host: 127.0.0.1` is intended for loopback use. External connections can be allowed by setting `host` to `"0.0.0.0"` and configuring `allowFrom` carefully.

### 2. Start the gateway

Use the same entry point the Android runtime uses:

```python
from jenny.android_entry import run_gateway
run_gateway("/path/to/data_dir")
```

Note the argument is a *data directory*, not the workspace itself — the gateway creates and uses `<data_dir>/workspace`. Passing a path that already ends in `workspace` produces a nested `workspace/workspace`.

You should see:

```text
WebSocket server listening on ws://127.0.0.1:8765/
```

(On the Android device this line always reads `ws://127.0.0.1:18790/` instead, per the note above.)

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

All frames are JSON text. Each message has an `event` field.

### Server → Client

**`ready`** — sent immediately after connection is established:

```json
{
  "event": "ready",
  "chat_id": "default",
  "client_id": "alice"
}
```

**`message`** — full agent response:

```json
{
  "event": "message",
  "chat_id": "default",
  "text": "Hello! How can I help?",
  "media": ["/tmp/image.png"],
  "reply_to": "msg-id"
}
```

`media` and `reply_to` are only present when applicable.

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

`model_preset` is omitted when no named preset is active. WebUI clients use this event to keep the displayed model badge in sync across slash commands, config reloads, and settings changes.

**`subagent_status`** — snapshot of the background subagents, pushed on every state transition:

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

The frame is a recomputable refresh hint — it is never persisted to the transcript and never retried, since the next snapshot replaces it. The same payload is served verbatim by `GET /api/subagents`, which is how the WebUI panel comes back after a page reload instead of waiting for the next transition. `POST`-style actions ride on GET like every other gateway route (see the transport constraint in [Write a mini-app](../contribute/write-a-mini-app.md)): `GET /api/subagents/<task_id>/restart` (always a manual relaunch) and `GET /api/subagents/<task_id>/cancel`.

**`attached`** — confirmation for `attach` inbound envelopes (see [The shared chat](#the-shared-chat)):

```json
{"event": "attached", "chat_id": "default"}
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

**`mascot_mood`** — how Jenny feels about the reply she just gave, for the on-screen mascot.
Sent after `turn_end`, only to subscribers of that chat, never retried and never persisted: the
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

**`session_updated`** — the session changed underneath the client (compaction, rename, a
project switch); `scope` narrows what to refresh when present:

```json
{"event": "session_updated", "chat_id": "default", "scope": "history"}
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

**`error`** — soft error for malformed inbound envelopes. The connection stays open:

```json
{"event": "error", "detail": "invalid chat_id"}
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

Recognized fields: `content`, `text`, `message` (checked in that order). Invalid JSON is treated as plain text. These frames route to the shared chat (`default`, announced in `ready`).

**Typed envelopes:** any JSON object with a string `type` field is a typed envelope:

| `type` | Fields | Effect |
|--------|--------|--------|
| `attach` | — | (Re)subscribe to the shared chat, e.g. after a reconnect. Replies with `attached`. Any `chat_id` field is ignored. |
| `message` | `content` | Send `content` on the shared chat. Any `chat_id` field is ignored. |
| `rpc` | `id`, `method`, `params` | Run a gateway command and reply with `rpc_result`. See [Commands (rpc)](#commands-rpc). |

See [The shared chat](#the-shared-chat) for the full flow.

## Commands (rpc)

Operations that carry **content** — the text of a file, a free-text note — travel on this
channel, not on `/api/`. The HTTP surface is served from the WebSocket handshake hook, which
never reads a request body: its parameters can only ride the query string or a header, where
they are capped at 8192 bytes per line and restricted to ISO-8859-1 (a browser refuses to
put an emoji in a header at all). A WebSocket frame is framed and UTF-8, so a file with
emoji in it goes through unchanged.

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

Error codes: `bad_request`, `forbidden`, `not_found`, `too_large`, `unavailable`, `internal`.
A frame whose `id` is missing or malformed is dropped with a log line — there is nothing to
correlate a reply to.

| `method` | `params` | Effect |
|----------|----------|--------|
| `workspace.write` | `path`, `content` | Write a workspace text file (1 MB cap). Honours `workspace.enabled` / `workspace.allow_write`. |
| `audit.resolve` | `audit_id`, `wiki`, `resolution` | Close a wiki audit item with a resolution note. |
| `project.create` | `name`, `seed` | Create a project chat with its seed instruction. Both are required and whitespace-collapsed. |
| `project.delete` | `name` | Delete a project chat and its session. |

**Authorization is the handshake's, not the frame's.** When `token_issue_secret` is set, only
a connection that presented the token at handshake time may run a command, even if
`websocket_requires_token` is `false` — otherwise a mutation would sit on a weaker gate than
`/api/`, which fails closed without a secret. Commands live in `jenny/webui/commands.py`
(transport-agnostic); the frame handling is `jenny/channels/ws_rpc.py`.

## Configuration Reference

All fields go under the top-level `websocket` object in `config.json`. These are the schema-level defaults — remember that on the Android device `enabled`, `host`, and `port` are overridden at startup as described above, regardless of what is written here.

### Connection

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable the WebSocket server. The Android runtime writes `true` via `setdefault`, so an explicit `false` you put here survives and does disable the channel — `channels/dispatcher.py` reads it and returns. |
| `host` | string | `"127.0.0.1"` | Bind address. Use `"0.0.0.0"` to accept external connections. Forced to `127.0.0.1` on Android. |
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
| `sendToolHints` | bool | `false` | Include one-line tool hints in that progress stream ("reading SOUL.md", "searching…"). Off by default: it is the noisiest of the four. |
| `showReasoning` | bool | `true` | Forward `reasoning_delta` / `reasoning_end` frames when the provider exposes incremental reasoning. |
| `sendMaxRetries` | int | `3` | Attempts the dispatcher makes for one outbound frame before dropping it. Refresh-hint frames (`goal_status`, `app_data_changed`, `apps_list_changed`) are exempt by design: the next one replaces a pending one, so retrying them is pointless. |

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

Every connection participates in the single unified conversation: the server forces `chat_id` to `"default"` on all envelopes and fans outbound events out to every connected client (e.g. multiple Android activities or desktop browser tabs during local testing). There is no per-connection or per-user chat — any client presenting a valid credential sees and shares the same conversation.

### Typical flow

```text
client                                server
  | --- connect -------------------->  |
  | <-- {"event":"ready",              |
  |      "chat_id":"default"}          |
  |                                     |
  | --- {"type":"message",              |
  |      "content":"hi"} ------------>  |
  | <-- {"event":"delta", ...}          |
  | <-- {"event":"stream_end", ...}     |
  |                                     |
  | --- {"type":"attach"} ----------->  |  # after page reload
  | <-- {"event":"attached",            |
  |      "chat_id":"default"}           |
```

### Rules

- Every outbound event carries `chat_id`, always `"default"`.
- Client-supplied `chat_id` fields are ignored: everything routes to the shared chat.
- Errors (invalid envelope, unknown `type`, missing `content`) are soft: the server replies with `{"event":"error","detail":"..."}` and keeps the connection open.

### Backward compatibility

Legacy clients that only send plain text or `{"content": ...}` keep working unchanged: those frames route to the shared chat. No config flag is needed.

### Security boundary

Anyone holding a valid WebSocket auth credential joins the shared conversation and sees its output. This is safe for Jenny's local, single-user model; auth on the handshake is the single line of defense.

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

These are off-device / standalone-gateway patterns — they do not apply to the Android app, which always forces `host: 127.0.0.1`, `port: 18790`, `enabled: true`.

### Trusted local network (no auth)

```json
{
  "websocket": {
  "enabled": true,
  "host": "0.0.0.0",
  "port": 8765,
  "websocketRequiresToken": false,
  "allowFrom": ["*"],
  "streaming": true
  }
}
```

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
