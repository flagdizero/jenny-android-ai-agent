# Concepts

The mental model behind Jenny: one small core loop, one long-running gateway process, and a single continuous conversation.

Use this page when you want to understand how Jenny works before touching advanced settings. It explains the moving parts without requiring you to read the source first. If you want source-file ownership and extension points instead, read [Architecture](./architecture.md) after this page.

## Runtime shape

| Part | What it does |
|---|---|
| Agent loop | Builds context, selects the session, calls the provider, runs tools, and publishes replies |
| Providers | User-defined LLM endpoints, each using the OpenAI-compatible or Anthropic wire format — no built-in provider catalog |
| Channels | Two: the built-in WebSocket channel (the WebUI, always on) and an optional paired Telegram bot |
| Tools | Capabilities the model may call — files, `python_exec`, Android web search/fetch, location, cron, subagents, mini-app actions, and more |
| Memory | Workspace files and session history that keep useful context across turns, consolidated periodically by Dream |
| Gateway | The single long-running process that serves every channel and the HTTP API behind the WebUI |

The long-running path is the gateway, started by the Android runtime via `jenny.android_entry.run_gateway()`: it receives messages from the Android WebView over WebSocket (and, if paired, from Telegram), publishes them to the agent loop, and sends replies back out through the channels via the outbound dispatcher.

## Config vs workspace

Jenny is Android-only. The gateway's workspace lives inside the app's own private storage, not on shared/external storage and not inside the project checkout:

| Path | Meaning |
|---|---|
| `<filesDir>/workspace/config.json` | Instance configuration: providers, model defaults, tools, gateway, and runtime options |
| `<filesDir>/workspace/` | Agent workspace: memory, sessions, uploads, heartbeat tasks, cron jobs, skills, mini-apps, and generated artifacts |

Because the workspace is app-private storage, nothing outside the Jenny app can read or write it directly — not even other apps on the same phone — and it disappears if the app is uninstalled (see [Backup and restore](../using/backup.md) for how to get data out before that happens). The config file controls what Jenny may use; the workspace is where Jenny keeps state for that install.

## Config format

`config.json` accepts both camelCase and snake_case keys on read, but Jenny always writes the file back out with camelCase aliases — for example `apiKey`, `modelPresets`, `restrictToWorkspace`, and `maxToolResultChars`. The docs use camelCase for the same reason.

`config.json` is created automatically on first boot with a per-install secret and no provider configured; you normally never hand-edit it (the file is even hidden from the in-app file browser). See [First run](../start/first-run.md) for the onboarding flow, and [Configuration reference](../reference/configuration.md) if you do need the full key-by-key reference.

## One agent turn

A normal turn follows this flow:

1. The WebSocket channel (or the Telegram channel, if that's where the message came from) receives a user message and publishes it to the message bus.
2. The agent loop resolves the session — in normal use this is always the single unified session, described below — and builds context from the workspace, skills, memory, recent messages, channel metadata, and runtime settings.
3. The provider receives the model request.
4. If the model asks for tools, the runner executes them and feeds the results back to the model, looping until a final answer or a runtime limit is hit.
5. The final reply is saved to the session and sent back out through the dispatcher — to the channel the turn came from, and, for messages sent proactively via the `message` tool, potentially to the other channel too.

## Gateway and WebUI

There is one entry point and one process: `jenny.android_entry.run_gateway()`, started by the Android app and never invoked as a separate desktop process in normal use. It serves the embedded WebUI, the heartbeat, Dream, and every channel out of the same asyncio event loop.

**There is no `/health` endpoint.** On the device, the WebSocket handshake and the HTTP API behind the WebUI (`/api/...`) share one port — `gateway.port`, `18790` by default — so the WebView can reach both from a single origin without a CORS story. That sharing is enforced by the Android entry point itself (`run_gateway(..., port=18790)` forces both the gateway port and the WebSocket port to the same value at startup), not by anything you configure. The production UI is served over that same WebSocket channel, rendered inside the Android app's WebView; nothing about it assumes a desktop browser.

## Provider and model selection

Providers are entries you define under `providers.providers`; the active one is the entry named by `providers.default`, otherwise the first in the list. There is no provider auto-detection and no bundled catalog of known providers or models.

**Changing the active provider or model from the Settings screen applies immediately, without restarting the app.** The gateway watches for settings changes and hot-reloads the live agent's provider and model in place. You only need to relaunch the app when you hand-edit `config.json` directly (nothing watches the file for external edits) or when a specific settings change is flagged as needing a restart internally — as of this writing the UI doesn't surface that flag, so a small set of settings (timezone, assistant name/icon, tool-hint length) can silently need a relaunch to take effect. See [Architecture — Providers](./architecture.md#providers) for the exact mechanism.

The active model should normally come from a named `modelPresets` entry selected by `agents.defaults.modelPreset`; when no preset is selected, the direct `agents.defaults.model` and related fields apply.

```json
{
  "modelPresets": {
    "primary": {
      "provider": "my-llm",
      "model": "served-model-name"
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary"
    }
  }
}
```

See [Providers and models](../reference/providers.md) for practical setup and [Configuration reference](../reference/configuration.md#providers) for the full schema.

## Channels and sessions

Jenny has four channels: the built-in WebSocket channel (always active — it's how the in-app WebUI talks to the gateway), an optional Telegram bot paired from Settings, and two that exist only on Android — a reply typed into one of Jenny's notifications, and the floating mascot's bubble. All four are owned by the same outbound dispatcher, which fans replies out to whichever channels are active.

Every inbound message from any channel routes into the same single unified conversation session (`unified:default`) — Jenny is a single-user assistant with one continuous thread, not a per-channel or per-device conversation. This has a sharp edge worth knowing up front: because there is only one session, `/new` issued from Telegram resets the WebUI's conversation too, and vice versa — whoever has access to the paired bot can reset the chat on the phone. Internal work (cron, Dream, heartbeat) runs under separate internal session keys so it never pollutes the user-visible conversation.

There is one user-visible exception to "a single session": a **project** conversation (`project:<name>`), opened from the scope chip and bound to one folder under `<workspace>/wikis/`. It has its own session file, its own transcript, and its own memory rules — its content is never archived into `history.jsonl`, so Dream cannot see it. Only the WebSocket channel can name a project; a Telegram message stays personal whatever it contains. Session keys are therefore classified three ways, not two — `personal`, `project`, `internal` — and anything unrecognised is treated as internal rather than personal, so an unregistered kind can never reach `MEMORY.md`. See [Projects](../using/projects.md) for the user-facing design.

See [Telegram bridge](../using/telegram.md) for the channel-specific asymmetries (Telegram doesn't see WebUI-only turns, has no streaming, etc.).

## Memory, sessions, and Dream

Jenny uses three related stores:

| Store | Location | Purpose |
|---|---|---|
| Sessions | `<workspace>/sessions/*.jsonl` | Recent conversation turns replayed into model context; compacted after 15 minutes idle by default, capped at 120 messages |
| Memory | `<workspace>/memory/MEMORY.md` and `<workspace>/memory/history.jsonl` | Long-term facts and consolidated history that survive session compaction |
| Wikis | `<workspace>/wikis/<name>/` | Your own knowledge bases; the personal prompt lists them by name and scope, read from disk, and opens pages on demand |

Dream is a periodic consolidation job (`jenny/agent/memory.py`): every 2 hours by default, it reads accumulated `history.jsonl` entries (capped at 1000, oldest dropped first) and rewrites `MEMORY.md`, `SOUL.md`, `USER.md`, and skill files — it prunes stale material as well as adding new material, and it takes a workspace snapshot right before running so a bad consolidation can be rolled back. Dream's interval restarts from zero every time the app (and its gateway process) restarts; it is not anchored to wall-clock time.

## Tools and safety

Tools are **not** discovered by scanning the filesystem — they are registered explicitly. A fixed list of 16 modules under `jenny/agent/tools/` each declare a `TOOLS = [...]` list of tool classes, collected once at startup by `jenny/agent/tools/loader.py`; a module missing that declaration, or a name collision between two tools, is a startup error rather than a silent gap. Common tool groups include:

- file read/write/edit, search, and multi-file patching, scoped to the workspace;
- Python code execution (`python_exec`), in-process, with a module allowlist that is explicitly not a security sandbox;
- Android web search and web fetch (via a hidden WebView) with SSRF checks on outbound network tools;
- location (last-known injected into context, or an on-demand fresh GPS fix);
- cron reminders and heartbeat-driven proactive tasks;
- long-running goals and subagent spawning;
- dynamic per-mini-app tools, registered and unregistered as apps are created or removed.

Tool behavior is part of the model contract: names, schemas, and error messages are effectively an interface the model has learned to use, so changing them is a compatibility-affecting change, not just a refactor. See [Architecture — Tools](./architecture.md#tools) for the full 16-module table and [Tool reference](../reference/tools.md) for per-tool behavior and limits.

## Further reading

- [Configuration reference](../reference/configuration.md) for the config schema;
- [Settings](../reference/settings.md) for what's actually reachable from the UI;
- [Providers and models](../reference/providers.md) for provider setup;
- [WebSocket protocol](../reference/websocket.md) for wire-level details;
- [Memory and Dream](../using/memory.md) for the consolidation pipeline;
- [The agent turn](./agent-turn.md) for a closer look at context, compaction, and the three memory levels;
- [Security model](./security-model.md) for the containment boundaries mentioned above.
