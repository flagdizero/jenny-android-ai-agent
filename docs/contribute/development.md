# Development

Repository layout and a map of where each subsystem lives, for anyone working on Jenny's Python side.

Android is the only runtime target for the finished product, but nearly all of the logic — the agent loop, tools, providers, sessions, config — is plain Python that also runs under CPython on your desktop for testing. See [Build from source](build-from-source.md) for getting a real APK on a device, including the desktop-only `run_gateway()` shortcut for iterating on the Python side without one.

## Repository layout

| Path | What's there |
|---|---|
| `jenny/` | The Python package: agent core, providers, channels, tools, config, WebUI backend. This is what gets bundled into the APK via Chaquopy. |
| `jenny/templates/ui/` | The mobile-first WebUI's static assets (HTML/CSS/JS), served by the gateway and rendered inside the Android WebView. |
| `jenny/templates/` (top-level `.md` files) | Prompt templates (`HEARTBEAT.md`, `dream.md`, etc.) copied into a fresh workspace on first run. |
| `jenny/skills/` | Built-in skill definitions (`SKILL.md` folders) loaded into agent context. |
| `android/` | The native Android project: Kotlin `MainActivity`/`GatewayService`, the Gradle build, the Chaquopy configuration that embeds the `jenny` package and its pinned dependencies. |
| `tests/` | The pytest suite. Mirrors the `jenny/` package structure directory-for-directory (`tests/agent/` for `jenny/agent/`, `tests/webui/` for `jenny/webui/`, and so on) — a new module usually gets a matching test directory in the same relative location. |
| `docs/` | This documentation. |
| `.agent/` | Contributor-facing design notes — 18 files. The four `AGENTS.md` points at are `design.md` (architecture constraints), `security.md` (security boundaries), `gotchas.md` (common gotchas) and `jenny-apps.md` (Jenny Apps design); the rest are per-feature plans and handover notes from work already landed. |
| `scripts/` | Standalone helper scripts (`check_dco.sh`, `vendorize_ui.py`, `capture_screenshots.sh`), kept outside CI YAML so they're runnable and testable on a developer machine too. |
| `AGENTS.md` | The canonical architecture reference for AI coding agents (and a good orientation doc for humans too) — this page summarizes it, but `AGENTS.md` is the source of truth if the two ever disagree. |

## Setting up a Python environment

```bash
git clone https://github.com/flagdizero/jenny-android-ai-agent.git jenny
cd jenny
pip install -e .
pip install ruff pytest pytest-asyncio cryptography asyncssh
```

That is what CI's `test` job installs. The last two are **dev-only and not optional if you intend to run the suite**:

- `cryptography` — the encrypted-backup tests. On Android backup crypto uses `javax.crypto` instead, so this must never end up in `requirements-android.txt` / `requirements-android.lock.txt`.
- `asyncssh` — the desktop SSH backend raises an in-process server for the suite. Without it **eight SSH test modules fail at collection**, which looks like a broken checkout rather than a missing package. On Android the client is jsch through a native bridge, so the same rule applies: never in the Android requirements.

One more thing the suite can want, optional:

- **node** — about twenty WebUI suites execute the real JS. They *skip* without it, so the suite still goes green while ~200 behaviour tests quietly do not run. None of them needs jsdom: the ones that touch the DOM bring a minimal fake of their own.

`android/image_source/gen_icons.py` and `gen_pose_webp.py` additionally need **Pillow**. They regenerate the shipped icon and mascot assets and are not part of a normal build, which is why Pillow is in none of the requirement files.

## Where subsystems live

The high-level data flow: an async `MessageBus` (`jenny/bus/queue.py`) decouples channels from the agent core. The WebSocket channel (`jenny/channels/websocket.py`) receives messages from the WebUI and publishes them as `InboundMessage` events onto the bus; `AgentLoop` (`jenny/agent/loop.py`) consumes them, builds context, and coordinates the turn; `AgentRunner` (`jenny/agent/runner.py`) drives the actual multi-turn LLM conversation — sending messages, receiving tool calls, executing tools, streaming responses; results come back out as `OutboundMessage` events. See [Architecture](../internals/architecture.md) and [The agent turn](../internals/agent-turn.md) for the full picture.

| Subsystem | Lives in | Notes |
|---|---|---|
| Agent loop / turn coordination | `jenny/agent/loop.py`, `runner.py` | `AgentLoop` manages session keys, hooks, context building; `AgentRunner` executes the tool-calling conversation loop. |
| LLM providers | `jenny/providers/` | `base.py` is the common provider interface; `factory.py` builds the runtime provider from config. See [Add a provider](add-a-provider.md) for how to add a new one — that's a separate page, not duplicated here. |
| Channels | `jenny/channels/` | WebSocket (`websocket.py`) and Telegram (`telegram.py`) are the two channels; `dispatcher.py` routes outbound bus messages to both, with retry, delta coalescing, and progress filtering. |
| Tools | `jenny/agent/tools/` | Filesystem, `python_exec`, Android web search/fetch, cron, subagent spawning, long-running tasks, self-modification. Tools are registered **explicitly**: `loader.py` imports a fixed module list and each module declares its own `TOOLS = [...]`; name collisions raise at startup — there is no automatic module scanning. See [Write a tool](write-a-tool.md). |
| Memory | `jenny/agent/memory.py` | Session history persistence plus Dream two-phase memory consolidation. Atomic writes with `fsync` for durability. |
| Sessions | `jenny/session/` | History persistence, context compaction, TTL-based auto-compaction (`manager.py`), sustained-goal state tracking (`goal_state.py`). The user conversation is a single unified session (`unified:default`, see `keys.py`); internal work (cron, Dream, heartbeat) uses separate internal keys via `session_key_override`. |
| Config | `jenny/config/schema.py`, `loader.py` | Pydantic-*style* config (`jenny/pydantic_compat/` — a stdlib-only reimplementation, see `FORK_BOUNDARY.md`) loaded from `workspace/config.json`. Supports camelCase aliases for JSON compatibility. |
| WebUI (frontend) | `jenny/templates/ui/` | The mobile-first SPA. Talks to the gateway over the same WebSocket used for chat, plus HTTP routes under `/api/`. |
| WebUI (backend) | `jenny/webui/` | The `/api/` route handlers backing the SPA — apps, settings, media, skills, transcript, token usage, workspaces, file preview — plus gateway service/token wiring. |
| Jenny Apps | `jenny/apps/` | Runtime for user-authored mini-apps: `manifest.py`, `executor.py`, `storage.py`, `summary.py`, `http.py`. See [Write a mini-app](write-a-mini-app.md) and `.agent/jenny-apps.md`. |
| Command router | `jenny/command/` | Slash command routing and built-in command handlers. |
| Skills | `jenny/skills/` | Built-in skill definitions loaded into agent context. |
| Security | `jenny/security/` | Workspace policy/access control plus SSRF network protections. |
| Gateway entry point | `jenny/android_entry.py` → `gateway_runtime._run_gateway()` → `jenny/runtime/container.py::GatewayContainer` | `GatewayContainer` is the explicit composition root: builds the whole object graph and owns runtime state (onboarding-deferred agent creation, ordered shutdown drain). |
| Runtime state | `jenny/runtime/context.py::RuntimeContext` | The single source of truth for the workspace directory, Android context, and config-path override. Accessors like `get_workspace_path()` / `get_android_context()` / `get_config_path()` delegate here rather than reading scattered module globals. |
| Cron / delivery | `jenny/runtime/cron_dispatch.py::CronDispatcher`, `jenny/runtime/delivery.py::ChannelDeliverer` | |

### Large classes are split into mixins

Several large classes are decomposed into focused mixins/leaf modules composed via MRO, with behavior kept identical to a monolithic version — this is a structural pattern worth knowing before you go looking for a method and can't find it on the class you expected:

- `AgentLoop` ← `turn_states` / `turn_persistence` / `loop_provider` / `loop_tasks` (+ `turn_types`)
- `AgentRunner` ← `request_execution` / `tool_execution` (+ `context_governor`, `usage_accounting`, `history_repair`, `tool_error_policy`)
- `OpenAICompatProvider` ← `openai_compat_parsing` (+ `openai_compat_helpers`)
- `AnthropicProvider` ← `anthropic_conversion`
- `WebSocketChannel` ← `ws_sender` (+ `ws_parsing`)
- `transcript` → `transcript_store` / `recorder` / `replay` / `markdown` / `tool_events`
- `ws_http` → `*_routes` families (e.g. `settings_routes.py`, `wiki_routes.py`, `workspace_routes.py`, `apps_routes.py`, `backup_routes.py`)

## Config and security boundaries

`jenny/config/schema.py::SecurityConfig` (`config.security`) is the canonical home for `restrict_to_workspace` / `ssrf_whitelist` (`ToolsConfig` mirrors them for the tool layer; a validator migrates legacy locations). `jenny/config/runtime_env.py` is the single layer for operational `JENNY_*` environment knobs — see [Environment variables](../reference/environment-variables.md). See [Security model](../internals/security-model.md) for what these boundaries actually enforce (and don't).

## Code style and conventions

Python 3.11+, asyncio throughout, 100-character line length. See [Code style](code-style.md) for the full lint/type-check/language conventions — the short version: `ruff` (rules E, F, I, N, W; `E501` ignored) and Italian docstrings/comments for new code (inherited upstream code stays in English; identifiers, log messages, and commit-facing strings are always English). Run the full check before opening a PR — see [Testing](testing.md).

## Opening a PR

Contribution flow, the Developer Certificate of Origin sign-off requirement, and licensing are covered in [`CONTRIBUTING.md`](../../CONTRIBUTING.md) at the repository root — read that before your first PR, since CI's `dco` job blocks merges on unsigned commits.

## See also

- [Build from source](build-from-source.md) — getting a device build running, plus the desktop gateway shortcut
- [Testing](testing.md) — running the test suite and the lint/type-check gates
- [Write a tool](write-a-tool.md) — adding a new agent tool
- [Write a mini-app](write-a-mini-app.md) — the Jenny Apps runtime
- [Add a provider](add-a-provider.md) — adding a new LLM provider integration
- [Architecture](../internals/architecture.md) — the full data-flow diagram and extension points
