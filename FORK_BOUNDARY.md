# Fork Boundary

This document defines what Jenny keeps, removes, and changes relative to upstream [nanobot](https://github.com/HKUDS/nanobot).

## What This Fork Is

Jenny is an Android-only AI agent framework focused on:

- **Gateway** — Single async entry point serving the WebView UI and WebSocket channel.
- **WebSocket channel** — The sole communication path between the in-app WebView and the agent.
- **Python exec** — Code execution via `python_exec` tool (replaces shell/sandbox exec).
- **Android WebView UI** — Mobile-first HTML/JS SPA embedded in the Android app (no React, no CLI dependency).

## What Was Removed

| Subsystem | Reason |
|-----------|--------|
| Multi-channel integrations (Discord, Slack, generic multi-platform bots) | Out of scope for a single-user Android agent. Telegram was later reintroduced as a dedicated personal-bot channel (see Kept table). |
| Multi-channel abstraction (`channels/base.py`, `registry.py`, `ChannelManager`) | No generic channel registry; channels are wired explicitly in `WebSocketDispatcher` (WebSocket, Telegram, and the Android-only Notification and Floating channels). |
| Audio transcription / STT (`nanobot/audio/`, `providers/transcription.py`) | Voice input removed; text-only agent. |
| React-based WebUI | Replaced with lightweight vanilla HTML/JS SPA embedded in the Android WebView. |
| Bridge / gateway bridge | No multi-process orchestration needed. |
| Shell / sandbox exec | Replaced by `python_exec` tool. |
| HTTP-based web tools | Replaced by Android WebView-backed `android_web.py` tools. |
| MCP client integration | SDK transports unavailable on Android; removed entirely. |
| Git-backed Dream history | `dulwich` unavailable on Android; removed. |
| Desktop/browser fallbacks | Android is the only supported runtime target. |
| Removed providers (if any pruned during fork) | Kept only providers actively tested. |

## What Is Intentionally Kept

All of the following subsystems are present and actively used:

| Subsystem | Path | Decision | Rationale |
|-----------|------|----------|-----------|
| Android entry point | `jenny/android_entry.py` | **KEEP** | Single entry point invoked by the Android/Chaquopy runtime. |
| Outbound dispatcher | `jenny/channels/dispatcher.py` | **KEEP** | Routes bus messages to the five channels — WebSocket, Telegram and, on Android, Notification, Floating and Wear — (retry, coalescing, progress filtering). |
| Telegram channel | `jenny/channels/telegram.py` | **KEEP** | Personal-bot channel re-introduced alongside WebSocket; paired via pairing code, mirrors WebUI turns. |
| Explicit tool registration (fixed module list + per-module `TOOLS`) | `jenny/agent/tools/loader.py` | **KEEP** | `loader.py` imports a fixed module list (`_HARDCODED_TOOL_MODULES`); each module declares `TOOLS = [...]`. A name collision — like a module without `TOOLS`, or an `allow` entry that names no known tool — raises `ToolLoadError` and aborts startup; a failing `enabled()`/`create()` only disables that one tool, logged at ERROR and recorded in `ToolLoader.failures`. |
| SSH client (native Android) | `jenny/agent/tools/ssh_backends/`, `android/…/SshBridge.kt` | **ADDED** | Remote shell access via jsch + Bouncy Castle behind a Kotlin bridge, not a Python SSH library. The pure-Python rule below is the reason: every Python option needs `cryptography`, whose only Chaquopy wheel for cp311 is 42.0.8 (June 2024) and cannot be updated — a maintenance dead end on top of being the APK's first native binding. Native crypto updates with a Gradle version bump instead. The cost is a bridge that only runs on-device, paid for by a second `asyncssh` backend used **solely by the test suite** (same split as `snapshot/crypto_backends/`, and guarded by `tests/agent/tools/test_asyncssh_is_dev_only.py`). |
| Pydantic-style config layer (stdlib-only) | `jenny/pydantic_compat/` | **KEEP** | Reimplementation of `BaseModel`/`Field`/validators over stdlib dataclasses. Real Pydantic v2 depends on `pydantic-core` (Rust), unavailable on Chaquopy. API surface deliberately minimal — only what its users need: `config_base.py`, `config/schema.py`, `config/tool_schemas.py`, `config/loader.py` and `channels/websocket.py` (`WebSocketConfig`). Do not extend to emulate unused features. |
| Wiki system | `jenny/webui/wiki.py`, `jenny/webui/ws_http.py` | **KEEP** | Core feature: markdown knowledge base with wikilinks, graph, and audit. |
| Audit system | `jenny/webui/audit.py`, API routes | **KEEP** | Companion to wiki; tracks feedback and review items. |
| Skills subsystem | `jenny/skills/`, `jenny/agent/skills.py` | **KEEP** | Core feature: built-in and custom skill definitions loaded into agent context. |
| App manifest protocol | `jenny/apps/manifest.py` | **KEEP** | Small, self-contained manifest shape for agent app metadata. |

## Migration Notes from Upstream

If migrating from upstream nanobot to Jenny:

1. **Config** — `workspace/config.json` schema is compatible. Remove any channel-specific config keys (Discord token, Slack bot token, etc.).
2. **Shell exec** — Any agent instructions referencing `shell_exec` or `bash` tool should be updated to use `python_exec`.
3. **WebUI** — The React WebUI is gone. The new UI is embedded in the Android app via WebView at `jenny/templates/ui/` and is served automatically by the gateway on loopback.
4. **Channels** — There are five: WebSocket, Telegram, and the Android-only Notification, Floating and Wear channels. The Android WebView connects to the gateway over `127.0.0.1` inside the same app process; Telegram is an optional paired personal-bot channel (`jenny/channels/telegram.py`); Notification (`notification.py`) carries a reply typed into a notification Floating (`floating.py`) the floating mascot's bubble, and Wear (`wear.py`) the reply to a question asked on the watch (the Data Layer side is in the `play` flavor only).
5. **Providers** — Provider config is unchanged. All OpenAI-compatible endpoints work as before.
6. **Runtime target** — Android is the only supported runtime target. Desktop/browser deployment is not supported.
