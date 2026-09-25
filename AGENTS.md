This file provides guidance to AI coding agents working with this repository.

## Project Overview

Jenny is a lightweight AI agent framework written in Python with a built-in mobile-first WebUI served from `jenny/templates/ui/`. It centers around an async agent loop that receives messages over WebSocket, invokes an LLM provider, executes tools, and manages session memory.

**Android is the only runtime target.**

## Development Commands

```bash
pytest tests/config/ -v
ruff check jenny/
```

The gateway is started by the Android runtime via `jenny.android_entry.run_gateway()`.

## Android Build & Deploy

The Android project lives in the `android/` directory. Build and install the debug APK on the attached device with:

```bash
cd android && ./gradlew app:installDebug
```

This builds `app-debug.apk` and installs it via `adb` on the connected device (e.g. Unihertz Titan 2). Verify the device is detected first with `adb devices`.

## High-Level Architecture

### Core Data Flow

Messages flow through an async `MessageBus` (`jenny/bus/queue.py`) that decouples the WebSocket channel from the agent core:

1. **WebSocket Channel** (`jenny/channels/websocket.py`) receives messages from the mobile WebUI and publishes `InboundMessage` events to the bus.
2. **`AgentLoop`** (`jenny/agent/loop.py`) consumes inbound messages, builds context, and coordinates the turn.
3. **`AgentRunner`** (`jenny/agent/runner.py`) handles the actual LLM conversation loop: send messages to the provider, receive tool calls, execute tools, and stream responses.
4. Responses are published as `OutboundMessage` events back to the WebSocket channel.

### Key Subsystems

- **Agent Loop** (`jenny/agent/loop.py`, `runner.py`): The core processing engine. `AgentLoop` manages session keys, hooks, and context building. `AgentRunner` executes the multi-turn LLM conversation with tool execution.
- **LLM Providers** (`jenny/providers/`): Provider implementations (Anthropic, OpenAI-compatible, OpenAI Responses API converters) built on a common base (`base.py`). `factory.py` creates the provider from config.
- **Channel** (`jenny/channels/`): four channel classes — WebSocket (`websocket.py`, the WebUI), Telegram (`telegram.py`, a paired personal-bot channel), Notification (`notification.py`, a reply typed into an Android notification, answered with a system alert) and Floating (`floating.py`, the floating mascot's bubble); the last two exist only on Android. `dispatcher.py` owns them and routes outbound bus messages to whichever are active (retry, delta coalescing, progress filtering). All four feed the same unified session. Other platform integrations were removed from this fork.
- **Tools** (`jenny/agent/tools/`): Agent capabilities exposed to the LLM: filesystem (read/write/edit/list), `python_exec` for code execution, Android web search/fetch (`android_web.py`), cron, subagent spawning, long-running tasks / sustained goals (`long_task.py`), and self-modification. Tools are explicitly registered: `loader.py` imports a fixed module list of 23 (`_HARDCODED_TOOL_MODULES`) and each module declares `TOOLS = [...]`, yielding 41 tool classes. **Three more bypass the loader**, each because it needs a live reference the loader cannot provide: `my` (the running `AgentLoop`), `memory` (`MemoryEntryTool`, which needs the memory store) and the per-app action tool (synced per turn) — so this list is not a complete inventory of the tool surface, and `self.py`/`memory_entries.py` declaring no usable `TOOLS` is deliberate rather than an oversight. A name collision — like a module without `TOOLS` — raises `ToolLoadError` and aborts startup; a failing `enabled()`/`create()` only disables that one tool, logged at ERROR and recorded in `ToolLoader.failures`.
- **Memory** (`jenny/agent/memory.py`): Session history persistence with Dream two-phase memory consolidation. Uses atomic writes with fsync for durability.
- **Session Management** (`jenny/session/`): History persistence, context compaction, TTL-based auto-compaction (`manager.py`), and sustained goal state tracking (`goal_state.py`). The user conversation is a **single unified session** (`unified:default`, see `keys.py`); internal work (cron, Dream, heartbeat) uses separate internal keys via `session_key_override`.
- **Config** (`jenny/config/schema.py`, `loader.py`, `store.py`): Pydantic-*style* configuration (`jenny/pydantic_compat/`, stdlib-only — see [`FORK_BOUNDARY.md`](./FORK_BOUNDARY.md)) loaded from `workspace/config.json` inside the project root. Supports camelCase aliases for JSON compatibility. **Every write goes through `store.mutate()`** — see the rule under [Config & security](#config--security); calling `save_config()` directly reintroduces a silent data-loss bug that no test will catch for you.
- **WebUI** (`jenny/templates/ui/`): Mobile-first HTML/JS served by the gateway, in two shells that share `assets/shared/`: the home (`index.html` + `casa-*.js`, the default) and the workshop (`officina.html` + `mobile-*.js`), reached from the home's Settings page. Both talk to the gateway over the same WebSocket used for chat, plus HTTP routes under `/api/`.
- **WebUI HTTP API** (`jenny/webui/`): The `/api/` route handlers backing the SPA (apps, settings, media, skills, transcript, token usage, workspaces, file preview, etc.), plus gateway service/token wiring.
- **Jenny Apps** (`jenny/apps/`): Runtime for user-authored mini-apps — `manifest.py`, `executor.py`, `storage.py`, `summary.py`, `http.py`. See [`.agent/jenny-apps.md`](.agent/jenny-apps.md).
- **Command Router** (`jenny/command/`): Slash command routing and built-in command handlers.
- **Heartbeat** (`jenny/templates/HEARTBEAT.md`): Periodic task list checked via `cron` jobs.
- **Skills** (`jenny/skills/`): Built-in skill definitions loaded into agent context.
- **Security** (`jenny/security/`): Workspace policy/access + network SSRF protections.

### Gateway Entry Point

- **Gateway**: `jenny/android_entry.py::run_gateway()` → `gateway_runtime._run_gateway()` (thin, patchable) → **`jenny/runtime/container.py::GatewayContainer`**, the explicit composition root that builds the whole object graph and owns runtime state (onboarding-deferred agent creation, ordered shutdown drain).
- **Runtime state**: `jenny/runtime/context.py::RuntimeContext` is the single source of truth for workspace dir / Android context / config-path override (accessors `get_workspace_path`, `get_android_context`, `get_config_path` delegate here — no scattered module globals).
- **Cron/delivery**: `jenny/runtime/cron_dispatch.py::CronDispatcher`, `jenny/runtime/delivery.py::ChannelDeliverer`.

### Decomposed subsystems (mixins/leaf modules)

Large classes are split into focused mixins/leaf modules composed via MRO (behavior-identical): `AgentLoop` ← `turn_states`/`turn_persistence`/`loop_provider`/`loop_tasks` (+ `turn_types`); `AgentRunner` ← `request_execution`/`tool_execution` (+ `context_governor`, `usage_accounting`, `history_repair`, `tool_error_policy`); `OpenAICompatProvider` ← `openai_compat_parsing` (+ `openai_compat_helpers`); `AnthropicProvider` ← `anthropic_conversion`; `WebSocketChannel` ← `ws_sender` (+ `ws_parsing`); `transcript` → `transcript_store`/`transcript_recorder`/`transcript_replay`/`transcript_markdown`/`transcript_tool_events`; `ws_http` → `*_routes` families.

### Config & security

- **Never write `config.json` outside `config/store.py::mutate()`.** It reads the file *inside* the lock it writes under, so no caller can hold a stale copy; `save_config()` rewrites the whole file, so a stale copy silently erases whatever another writer just changed. Slow I/O (network, subprocess) belongs **before** entering `mutate`, never inside the callback — the lock is held for its whole duration. `mutate` also carries through keys this version's schema does not know, keeps a `.bak`, writes atomically and restores `chmod 600`; none of that happens if you bypass it. Two documented exceptions, both commented on the spot: `config/bootstrap.py` (runs before the event loop) and any wholesale restore.
- `config/schema.py::SecurityConfig` (`config.security`) is the canonical home for `restrict_to_workspace`/`ssrf_whitelist`. Only `restrict_to_workspace` is mirrored onto `ToolsConfig` for the tool layer — `ssrf_whitelist` lives on `SecurityConfig` alone, and writing `tools.ssrf_whitelist` is silently dropped. A validator migrates legacy config.
- `config/runtime_env.py` is the single layer for operational `JENNY_*` env knobs.
- Tools are registered via an explicit `TOOLS = [...]` list per module (read by `agent/tools/loader.py`); name collisions and missing `TOOLS` raise `ToolLoadError` at startup, a failing `enabled()`/`create()` disables only that tool.

## Project-Specific Notes

- Architecture constraints: [`.agent/design.md`](.agent/design.md)
- Security boundaries: [`.agent/security.md`](.agent/security.md)
- Common gotchas: [`.agent/gotchas.md`](.agent/gotchas.md)
- Jenny Apps design: [`.agent/jenny-apps.md`](.agent/jenny-apps.md)
- Fork boundary: [`FORK_BOUNDARY.md`](./FORK_BOUNDARY.md)
- **Design boards are not in the repo.** Plans under `.agent/` (and some code comments) cite
  «tavole» — design boards such as `Quaderno.dc.html` or the artifact *Jenny UI: Utente e
  Operatore* — that lived outside the repository. They are not needed to read a plan: the
  plans quote what they took from a board, and where a plan and the code disagree, the
  code and its comments win. Do not go looking for the files, and do not cite a board as the
  only justification for a new decision.
- **`docs/` has a second consumer outside this repo.** The website
  (`flagdizero/jenny-site`) generates its `/docs/**` routes from these files at build
  time, deriving each page's title from the `# H1` and its sidebar position from the
  `docs/<section>/` folder. So moving, renaming or re-nesting a file under `docs/`
  changes a public URL and the site's navigation — it is not a repo-local edit. The
  content itself stays canonical here: the site holds no copy.

## Contribution Flow

See [`CONTRIBUTING.md`](./CONTRIBUTING.md) for contribution flow and PR guidelines.

## Code Style

- Python 3.11+, asyncio throughout.
- Line length: 100.
- Linting: `ruff` with rules E, F, I, N, W (E501 ignored).
- pytest with `asyncio_mode = "auto"`.
- Language convention: docstrings/comments in Italian for new code; inherited upstream code keeps English — do not translate existing text. Identifiers, log messages and commit-facing strings: English. User-facing WebUI strings are localized via i18n JSON files (`jenny/templates/ui/assets/i18n/{it,en}.json`), not hardcoded.

## Verification Commands

Run these before committing or opening a PR:

```bash
# Lint
ruff check jenny/ tests/

# Static type check (pyright basic, zero runtime impact; config: pyrightconfig.json)
# BLOCKING subset — must stay green (already error-clean):
npx pyright jenny/bus jenny/command jenny/runtime jenny/session
# Full-perimeter visibility (non-blocking; shows residual errors to tighten over time):
npx pyright || true

# Tests
pytest -q

# Full CI-equivalent check (lint + type check + tests)
ruff check jenny/ tests/ && npx pyright jenny/bus jenny/command jenny/runtime jenny/session && pytest -q
```

## Common File Locations

- Config schema: `jenny/config/schema.py`; **write funnel: `jenny/config/store.py`** (file fidelity — atomicity, backup, recovery — in `jenny/config/loader.py`)
- Provider base / new provider template: `jenny/providers/base.py`
- WebSocket channel + dispatcher: `jenny/channels/websocket.py`, `jenny/channels/dispatcher.py`
- Tool registry: `jenny/agent/tools/registry.py`; explicit registration lists: `TOOLS` in each `jenny/agent/tools/*.py`, read by `loader.py`
- Composition root & runtime state: `jenny/runtime/container.py`, `jenny/runtime/context.py`
- Config schema + security: `jenny/config/schema.py` (`SecurityConfig`), env knobs: `jenny/config/runtime_env.py`
- WebUI assets: `jenny/templates/ui/`
- Tests mirror the `jenny/` package structure.
