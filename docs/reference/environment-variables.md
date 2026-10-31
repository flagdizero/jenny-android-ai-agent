# Environment Variables

Jenny reads a small set of `JENNY_*` process environment variables for operational tuning (timeouts, concurrency) that are deliberately kept out of `config.json`, plus four release-signing variables consumed only by the Android build.

## Who this page is for

These are not user settings. The Android app has no supported way for a normal user to set process environment variables for the gateway — nothing in the WebUI or Settings screen maps to them. They matter to:

- **Contributors** running the gateway on a workstation for development or tests (`pytest`, or a manual `run_gateway(...)` call outside the APK).
- **Anyone building a release APK from source**, for the signing variables.

If you are not building from source, you can skip this page.

## Operational tuning knobs

Centralized in `jenny/config/runtime_env.py`, which the codebase treats as the single layer for these operational knobs (see the module docstring: "un solo layer env"). Each falls back to its default when unset, blank, or unparseable — an invalid value (e.g. non-numeric) is logged as a warning and the default is used, it does not crash the gateway.

| Variable | Default | Effect | When you'd touch it |
|---|---|---|---|
| `JENNY_MAX_CONCURRENT_REQUESTS` | `3` | Caps the number of concurrent LLM turns the agent loop will run at once. `<= 0` means unlimited. | Lower it to throttle a rate-limited provider account; raise it (or set to `0`) if you're driving many parallel subagents and want no cap. |
| `JENNY_LLM_TIMEOUT_S` | `300` (seconds) | Hard wall-clock timeout for a single LLM request, so a stalled network connection can't starve the session lock forever. `<= 0` disables it. It is not applied to the turns of a sustained goal (`/goal`). | Raise it for a slow self-hosted/local model that legitimately takes longer than 5 minutes to respond; lower it to fail fast during development. |
| `JENNY_WS_SEND_TIMEOUT_S` | `12` (seconds) | Wall-clock timeout for a single `connection.send()` on the WebSocket channel. The outbound dispatcher is serial, so one zombie client (dead socket, backgrounded app on a slow mobile connection) would otherwise stall delivery to every connected client; on timeout that connection is closed and dropped instead of reused (a cancelled send can leave a partial frame on the wire). Unlike the timeouts above, `<= 0` does not disable it: it is rejected with a warning and the default is kept. | Rarely touched — the default is deliberately generous so healthy clients on slow networks aren't disconnected. Lower it only if you need faster detection of dead connections in a controlled environment. |
| `JENNY_GOAL_INACTIVITY_TTL_H` | `12` (hours) | TTL of inactivity after which an `active` sustained goal (`/goal`) is lazily expired at the start of the next turn. Inactivity is measured from the more recent of `started_at` and `last_turn_at` — a goal that keeps making progress updates `last_turn_at` every turn and never expires on its own. Exists because an abrupt Android process kill (the normal way the app dies) would otherwise leave a goal `active` forever, permanently disabling the runner's wall-clock LLM timeout for that session. `<= 0` is rejected with a warning and the default is kept (it would not mean "never expire"). | Shorten it if you want stuck goals to release the timeout guard sooner; lengthen it for a goal you expect to sit idle for a long stretch without being considered abandoned. |
| `JENNY_TOOL_TIMEOUT_S` | `300` (seconds) | Wall-clock timeout for a single tool execution — the safety net against a tool call that never returns (the most common cause of a turn stuck on "Agent running"). On timeout the tool execution is aborted and the error is returned to the model as a normal tool error, so the turn still completes. `<= 0` disables it. | Raise it for a tool you know legitimately runs long (e.g. a slow `python_exec` session or network call); lower it to surface hangs faster during development. |

## Provider transport timeouts

Also defined in `runtime_env.py`, but consumed by the model providers (`jenny/providers/`) rather than the agent loop. For all three, a value that is non-positive or unparseable is ignored with a warning and the default is kept.

| Variable | Default | Effect |
|---|---|---|
| `JENNY_STREAM_IDLE_TIMEOUT_S` | `90` (seconds), clamped to a maximum of `3600` | Idle timeout between chunks while streaming a response, once the model has produced its first output — before that, the variable below applies. Used by both the Anthropic and the OpenAI-compatible provider. A value above the maximum is clamped down to it (with a warning). |
| `JENNY_STREAM_FIRST_OUTPUT_TIMEOUT_S` | `600` (seconds) for a loopback endpoint, `300` otherwise; clamped to a maximum of `3600` | How long to wait for the model's *first* content, thinking or tool-call delta. Kept separate from the idle timeout because a server is legitimately silent while it reads the prompt — see [Local models](./local-models.md#the-first-token-takes-as-long-as-the-prompt). The provider waits for the larger of this and `JENNY_STREAM_IDLE_TIMEOUT_S`. |
| `JENNY_LLM_HTTP_TIMEOUT_S` (or the older name `JENNY_OPENAI_COMPAT_TIMEOUT_S`, read only when the first is unset) | `600` (seconds) for a loopback endpoint, `120` otherwise | HTTP request timeout for a model call, used by both the Anthropic and the OpenAI-compatible provider despite the older name. Set explicitly, it overrides both defaults. The HTTP read timeout is never lower than the longer of the first-output budget above and the idle timeout, plus 10 seconds (610 s on loopback and 310 s otherwise, by default), so a silent model is cut by the stream budget, not by an anonymous read timeout. |

## Sandbox markers

| Variable | Default | Effect |
|---|---|---|
| `JENNY_WORKSPACE_SANDBOX_ENFORCED`, `JENNY_SANDBOX_ENFORCED` (legacy alias, checked if the first is unset), `JENNY_WORKSPACE_SANDBOX_PROVIDER` | unset | Markers used to detect that the process is running inside an external dev/CI sandbox and, optionally, which one. Their names are declared in `runtime_env.py`; they are read by `jenny/security/workspace_access.py`. Not relevant to the Android runtime and not something an app user would ever set. |

## Release signing variables

Consumed only by `android/app/build.gradle.kts` when building the Android app, not by the Python gateway. They provide the four pieces of a release keystore so `./gradlew app:assembleFossRelease` can produce a signed, installable APK.

| Variable | Default | Effect |
|---|---|---|
| `JENNY_KEYSTORE_PATH` | unset | Absolute path to the release `.jks`/`.keystore` file. |
| `JENNY_KEYSTORE_PASSWORD` | unset | Password for that keystore. |
| `JENNY_KEY_ALIAS` | unset | Alias of the signing key inside the keystore. |
| `JENNY_KEY_PASSWORD` | unset | Password for that specific key (can differ from the keystore password). |

Notes:

- All four must be set (and the keystore file must actually exist at the given path) for the release build to be signed. The build also accepts the same four values from a gitignored `android/keystore.properties` file (`storeFile`, `storePassword`, `keyAlias`, `keyPassword`) as a local alternative to environment variables — environment variables take precedence when both are present.
- If neither source supplies a complete set, `assembleFossRelease` still succeeds but produces an **unsigned APK that cannot be installed on a device** — the build intentionally does not fail, so anyone can still reproduce and inspect the artifact. Gradle prints an explicit warning at build time: `[jenny] WARNING: release signing credentials not found — the APK will be UNSIGNED and cannot be installed on a device.`
- These variables never affect `./gradlew app:installFossDebug`, the normal day-to-day build/deploy command — debug builds use the Android debug keystore automatically and are unaffected by any of this.
- Signing uses v2 and v3 scheme only (v1/JAR signing is deliberately disabled — it only matters below API 24, and Jenny's `minSdk` is 26).

See also: [Build from source](../contribute/build-from-source.md), [Install the APK](../start/install.md).
