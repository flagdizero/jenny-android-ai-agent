# Configuration (config.json)

Every key Jenny reads from `config.json`, with the default value that actually ships in the code and what changing it does.

Most people never need this page: the [Settings screen](./settings.md) covers the common choices, and everything it writes ends up here anyway. Come here for the settings that have no UI — Dream, heartbeat, timezone, tool toggles, snapshot retention, model presets — and for exact defaults and ranges.

## Where the file lives

On Android the file is `<data_dir>/workspace/config.json`, inside the app's private storage (`<filesDir>/workspace/`). It is created on first boot with a minimal skeleton — a `gateway.host` and a per-install `websocket.token_issue_secret` — and then filled in by the onboarding wizard.

The Workspace file browser **hides `config.json` by default**, along with its backup and any quarantined copy (see below) — they carry the same secrets. That is deliberate: the file holds your API keys and the WebUI bootstrap secret. Turn on **Developer mode** in Settings → System to see them.

Three things to know before you hand-edit it:

- **Changes need an app restart.** The only hot-reload path in the app is the WebUI settings screen, which reloads model and provider on the fly after it writes. Nothing watches `config.json` for external edits.
- **Broken JSON no longer blocks boot.** Jenny keeps the last good copy as `config.json.bak` and refreshes it before every successful save. If the live file cannot be read at startup, the backup is used and promoted; if there is no usable backup either, the unreadable file is set aside as `config.corrupt-<timestamp>.json` and Jenny starts on defaults — which means the API key has to be set up again. Either way the gateway comes online, and Settings shows a notice saying what happened and where the broken file went. Keep your own copy anyway before editing by hand.
- **Prefer Settings when the setting exists there.** The UI validates ranges, serialises concurrent writes, and writes atomically (temp file + rename + fsync), so a save interrupted by the OS killing the app cannot leave a half-written file.

## Key naming

Jenny writes camelCase (`apiKey`, `maxTokens`, `intervalS`), and this page uses camelCase throughout. snake_case is accepted everywhere on read (`api_key`, `max_tokens`, `interval_s`), so a hand-written config in either style loads fine — but a save from the UI rewrites the whole file in camelCase.

Two more parsing rules worth knowing:

- **Unknown keys are kept, not applied.** A typo in a key name does not raise, and the setting never applies — but the key survives in the file (a save from the UI no longer erases it) and startup logs a warning listing every key this version does not recognise. If an edit seems to do nothing, check that warning, then the spelling. The same rule is what lets a config written by a newer Jenny survive a downgrade. One limit: unknown keys *inside array items* — an extra field on a single provider entry, say — are not preserved, because merging list items would need a notion of which entry is which.
- **`${VAR_NAME}` in any string value is resolved from the environment at startup**, in memory only. Resolved values are never written back, so editing through the WebUI preserves the placeholder. A referenced variable that is not set aborts startup with `Environment variable 'NAME' referenced in config is not set`. On Android there is no practical way to set environment variables for the app process, so this is a desktop/testing feature — on the phone, secrets live in the file (see [Security model](../internals/security-model.md)).

## providers

The list of LLM endpoints you configured, plus which one is active. There is no built-in provider catalog: Jenny never infers an endpoint from a model name or key prefix.

```json
{
  "providers": {
    "providers": [
      {
        "name": "deepseek",
        "format": "openai_compat",
        "apiKey": "sk-...",
        "apiBase": "https://api.deepseek.com/v1"
      }
    ],
    "default": "deepseek"
  }
}
```

| Key | Type | Default | Effect |
|---|---|---|---|
| `providers.providers[]` | array | `[]` | The configured endpoints. Empty means no agent: the gateway still starts and serves the WebUI, but every turn fails with `No provider configured. Add one in settings or config.json.` |
| `providers.providers[].name` | string | required | Free-form identifier, referenced by `providers.default` and by `modelPresets.<preset>.provider`. |
| `providers.providers[].format` | `"openai_compat"` \| `"anthropic"` | required | Selects the wire format. The only field that decides which client is built. |
| `providers.providers[].apiKey` | string \| null | `null` | Credential, stored in clear text. Local servers that ignore auth still usually want a placeholder such as `"EMPTY"`. |
| `providers.providers[].apiBase` | string \| null | `null` | Full base URL including the version path. When unset: `https://api.openai.com/v1` for `openai_compat`, `https://api.anthropic.com` for `anthropic`. |
| `providers.providers[].caBundle` | string \| null | `null` | Path to a PEM certificate to trust **in addition to** the bundled default roots, for a server whose certificate is signed by your own CA. A relative path is resolved against the workspace. If the file is missing or unreadable the provider refuses to be built — Jenny never falls back to the default bundle silently. Android's own certificate store is not consulted: the Python runtime inside the APK carries its own. |
| `providers.providers[].apiType` | `"auto"` \| `"chat_completions"` \| `"responses"` | `"auto"` | `openai_compat` only, and only against a direct `api.openai.com` base. `auto` uses Chat Completions and probes the Responses API when a reasoning effort is requested or the model is a known OpenAI reasoning model, with a circuit breaker that stops probing after repeated failures. |
| `providers.providers[].extraHeaders` | object \| null | `null` | Headers merged into every request. |
| `providers.providers[].extraBody` | object \| null | `null` | Body fields deep-merged into every request. |
| `providers.providers[].extraQuery` | object \| null | `null` | Query parameters merged into every request. |
| `providers.default` | string \| null | `null` | Name of the active provider. When unset or unmatched, the **first** entry in the list is used. |

The onboarding wizard **replaces the entire provider list** with the single provider you enter. Details, error strings, and prompt-caching behavior: [Providers and models](./providers.md).

## agents.defaults

Everything about how the agent talks to the model and manages its own context.

### Model and generation

| Key | Type | Default | Effect |
|---|---|---|---|
| `agents.defaults.model` | string | `""` | Model ID sent to the provider. Empty until onboarding sets it. |
| `agents.defaults.maxTokens` | int | `16384` | Max output tokens per request. On a reasoning model the thinking is charged to this same budget, which is why the default is no longer the old `8192`: a turn that planned at length could exhaust it before emitting any answer. |
| `agents.defaults.temperature` | float | `0.1` | Sampling temperature. Ignored on models where the provider rejects it (Anthropic thinking modes force `1.0`; a few Anthropic models omit it entirely). |
| `agents.defaults.reasoningEffort` | string \| null | `"medium"` | `null` lets the model decide, which is no longer the default: left to itself a reasoning model will spend the whole output budget thinking about an open-ended task. Accepted: `low`, `medium`, `high` — plus values the Settings dropdown does not offer: `none` (explicitly disable thinking), `minimal` (and `minimum`, a DashScope alias normalized to `minimal`), and `adaptive` (Anthropic adaptive thinking, which also enables interleaved thinking between tool calls). Of these, only `adaptive` is unreachable from the settings endpoint and must be written here. |
| `agents.defaults.contextWindowTokens` | int | `65536` | Context budget used for prompt building and consolidation decisions. The settings endpoint accepts **only `65536` or `262144`** and rejects anything else; a hand-edited file can hold other values. If a request still overflows the model's real context window, the runtime retries (up to 2 times per turn) with a reduced window for the rest of that turn — using the limit parsed from the provider's error message when it can, otherwise shrinking the window by 50% as a heuristic. |
| `agents.defaults.toolChoice` | `"auto"` \| `"any"` \| `"none"` \| `"required"` | `"auto"` | How aggressively the model is pushed to call a tool. |
| `agents.defaults.modelPreset` | string \| null | `null` | Name of the preset applied at startup. When null, the direct fields above are used. |

> **The three "Advanced parameters" fields in Settings — Max Tokens, Temperature, Reasoning Effort — save from the UI**, and saving them rebuilds the provider so the value applies to the next message rather than at the next restart. In 0.3.x they did not save at all: the UI posted them and showed "Saved!" while the backend handler ignored them. Editing `config.json` or defining a [model preset](#modelpresets) remains the way to set values the UI can't express — notably `reasoningEffort: "adaptive"`, which the settings endpoint rejects.

### Context, memory and sessions

| Key | Type | Default | Effect |
|---|---|---|---|
| `agents.defaults.idleCompactAfterMinutes` | int ≥ 0 | `15` | Minutes of idle time before the session context is proactively compacted (summary + the most recent messages, rewritten into the session file). `0` disables it, leaving only the token-driven consolidation that never rewrites the file. Accepted under its legacy name `sessionTtlMinutes` too. |
| `agents.defaults.maxMessages` | int ≥ 0 | `120` | Upper bound on live messages kept in the session before consolidation kicks in. |
| `agents.defaults.consolidationRatio` | float 0.1–0.95 | `0.5` | Fraction of the live context consolidated when a consolidation runs. |
| `agents.defaults.dream.enabled` | bool | `true` | Registers the periodic Dream memory-consolidation job at startup. |
| `agents.defaults.dream.intervalH` | int ≥ 1 | `2` | Hours between Dream runs. The deadline survives an app restart; a run missed while the app was down happens at the next tick. |
| `agents.defaults.gardener.enabled` | bool | `true` | Registers the periodic [gardener](../using/gardener.md) job, which turns a project's journal lines into wiki pages. With no projects, or with no unread journal lines, a tick exits before reaching the provider. The switch in Settings → Wiki and projects writes this. |
| `agents.defaults.gardener.intervalMin` | int 1–1440 | `30` | Minutes between ticks — how often it *looks* for a project to garden. Past a day the pass has stopped being periodic; `enabled: false` is the way to say never. |
| `agents.defaults.gardener.idleMin` | int 0–1440 | `30` | How long that project's conversation must have been silent before a pass starts. `0` lets a pass begin while you are talking in it (it can promote half a conversation, and rewrite the map while you read it). A project with a turn actually in flight is skipped regardless. |
| `agents.defaults.gardener.minHoursBetweenPasses` | int 0–8760 | `6` | Minimum gap before returning to the *same* project, counted from the last **attempt** rather than the last success. `0` lets it come straight back, which is the measured Dream degradation written as a number. |
| `agents.defaults.compactProjectsWhenIdle` | bool | `false` | Whether a project's conversation is archived once it goes idle, like the personal one. Off by default: a project can sit for three weeks and pick up where it was. Read when the agent starts, so a change needs a gateway restart. Even when on, a project is not compacted while journal lines are still unpromoted, or while it has no pages at all. |
| `agents.defaults.maxToolIterations` | int | `200` | Hard ceiling on tool calls in a single turn. |
| `agents.defaults.maxToolResultChars` | int | `16000` | Tool output above this is truncated before it reaches the model. |
| `agents.defaults.contextBlockLimit` | int \| null | `null` | Optional cap on context blocks; unset means no extra limit. |

`dream` has **six** fields — `enabled`, `intervalH`, and the four budget/cadence knobs documented in [Memory and Dream](../using/memory.md): `memoryBudgetChars`, `userBudgetChars`, `soulBudgetChars`, `reviewEveryRuns`. All six are settable from Settings → Memory. Older docs mentioned `cron`, `modelOverride` and `maxBatchSize`; none of them exist. See [Memory and Dream](../using/memory.md).

**Both periodic workers are settable from the app**, and this is the only block on this page where that is true: Dream and the memory budgets in **Settings → Memory**, the gardener — plus `compactProjectsWhenIdle` — in **Settings → Wiki and projects**. The ranges above are the ones those screens carry and the server enforces; a value outside one is refused with the range named.

What applies when: `enabled` and the intervals re-arm the periodic job immediately, so neither turning a worker off nor changing its schedule needs a restart — including turning one back on after the gateway started with it off, which is the case a plain config edit cannot fix. `idleMin`, `minHoursBetweenPasses` and the Dream budgets are read on each run. `compactProjectsWhenIdle` takes effect at the next gateway start, and the screen says so.

A number outside its range in a file written by an older version is **clamped to the bound** rather than rejected — a stricter schema would fail to parse the file, and the recovery path for an unparseable `config.json` starts from defaults, taking your provider and API key with it. That clemency is also what lets a worker be switched off from such a file. See [The gardener](../using/gardener.md).

### Behavior and identity

| Key | Type | Default | Effect |
|---|---|---|---|
| `agents.defaults.timezone` | string | `""` | **Empty means auto**: the device timezone detected at startup, falling back to `UTC` only when detection fails. Resolved once per config load, and written back as `""` when it still matches the device — so it keeps following the phone. Set an IANA name (`"Europe/Rome"`) to pin it. Drives runtime time context, cron schedules without an explicit `tz`, and one-shot `at` times without an offset. |
| `agents.defaults.botName` | string | `"Jenny"` | Assistant name in chat and in the welcome message. Requires a restart to fully apply. |
| `agents.defaults.botIcon` | string | `"✿"` | Emoji shown next to the name. No UI field; restart to apply. |
| `agents.defaults.mascotMood` | bool | `true` | After each WebUI turn, a small background request asks the model how Jenny feels about the reply she just gave (one letter, a few hundred input tokens), and the mascot wears the matching face for a few seconds — happy, sad or angry. Nothing is added to the conversation or to the agent's prompt; turns that end in an error, command turns and very short replies cost nothing. Off means no request at all. Read per turn: a change applies to the next turn without a restart. |
| `agents.defaults.mascotMoodModelPreset` | string \| null | `null` | Name of a `modelPresets` entry whose `model` answers the mood request instead of the turn's own model — the place to point at a cheaper model. Only the preset's `model` is used; the provider stays the turn's. An unknown preset falls back to the turn's model with a warning in the log. |
| `agents.defaults.language` | string | `"it"` | Language for backend-generated text (welcome message and similar). Written once by onboarding from the UI locale. **Not** the UI language — that lives in the device's `localStorage`. |
| `agents.defaults.orchestratorMode` | bool | `true` | The main agent runs as an orchestrator: it keeps `spawn`, the subagent-control tools, cron, `message`, `ui_view`, `long_task`, introspection, logs, location and **read-only** file access (`read_file`, `list_dir`), and loses the tools whose output bloats your conversation — `python_exec`, `write_file`/`edit_file`, `apply_patch`, `download_file`, the web tools, exec sessions and search. That work goes to subagents instead. Set it to `false` to give the main agent the full toolset back (the pre-0.5 behaviour); restart to apply. |
| `agents.defaults.maxConcurrentSubagents` | int ≥ 1 | `3` | How many `spawn`ed subagents may run at once. One slot is reserved for short jobs: an ordinary spawn may take at most `limit - 1` slots (no reservation when the limit is `1`, which therefore serialises every fan-out). Beyond that, `spawn` returns an error so the agent can wait or reorder its work. Each slot is a live LLM request from a phone, so raising this hits your provider's rate limit and the battery well before it hits the CPU. Installations created before 0.5 carry the old default of `1` in their file and are moved to `3` once, with a warning in the log — see `configVersion` below. |
| `agents.defaults.subagentStallThresholdSeconds` | int ≥ 10 | `180` | How long a subagent may go without observable progress before it is flagged `stalled`. Flagging only — nothing is cancelled; relaunching stays a decision for you or the agent. A stalled subagent that resumes goes back to `running`. |
| `agents.defaults.subagentToolErrorBudget` | int ≥ 0 | `3` | How many recoverable tool errors a subagent may make before it gives up. It gets the same retry hint the main agent gets and carries on, instead of dying on the first one — a single mis-guessed `read_file` offset used to throw away a job that had already finished its real work. `0` restores that old behaviour. Security-boundary refusals (SSRF, workspace violations) are counted separately and are not spent from this budget. |
| `agents.defaults.toolHintMaxLength` | int 20–500 | `40` | Truncation length of the short tool-call hints shown while the agent works. |
| `agents.defaults.disabledSkills` | string[] | `[]` | Skill directory names excluded from the agent's skill summary, always-on injection, and subagent summaries. Applies to built-in and workspace skills alike. |
| `agents.defaults.providerRetryMode` | `"standard"` \| `"persistent"` | `"standard"` | Retry policy for provider errors. `persistent` keeps retrying transient failures longer. |

## Top-level keys

| Key | Type | Default | Effect |
|---|---|---|---|
| `extractDocumentText` | bool | **`false`** | When false (the default), non-image attachments are saved under `workspace/uploads/` and only referenced by path — the agent reads them on demand with its file tools. Setting it to `true` restores the legacy behavior of extracting and inlining document text into the prompt on every turn. See [Files and attachments](../using/attachments.md). |
| `configVersion` | int ≥ 0 | current | Schema version of *this file*, not of the app. It exists so a value in the file can be told apart from an old default that was merely written into it: the config is always saved with every field, so a file written when some default was X keeps X forever, and raising the default in a new release would otherwise reach nobody who upgrades. A file with a lower version (or none at all) gets the one-off migrations for the versions in between, then the number is stamped forward and persisted with the next ordinary save. From that point your values are treated as deliberate and are never rewritten. You should not need to edit this. |

## gateway

| Key | Type | Default | Effect |
|---|---|---|---|
| `gateway.host` | string | `"127.0.0.1"` | Bind address. |
| `gateway.port` | int | `18790` | HTTP port. |
| `gateway.heartbeat.enabled` | bool | `true` | Registers the built-in heartbeat cron job at startup. It reads `workspace/HEARTBEAT.md` and acts only on the `## Active Tasks` section. |
| `gateway.heartbeat.intervalS` | int ≥ 1 | `1800` | Seconds between heartbeat checks (30 minutes). Every cycle that finds a task costs an LLM call. |
| `gateway.heartbeat.keepRecentMessages` | int | `8` | Messages retained in the internal heartbeat session after each run. |

**On the phone, `host` and `port` are imposed by the Android runtime.** The service calls the gateway entry point with `127.0.0.1:18790` explicitly, which overwrites whatever the file says — both for the HTTP API and for the WebSocket, which share that single port. Editing them in `config.json` changes nothing on-device; they only matter when running the gateway yourself for local testing.

The heartbeat job is stored like any other cron job (`<workspace>/cron/jobs.json`) and appears in `cron(action="list")` as `heartbeat`, but it is system-managed and cannot be removed with the `cron` tool — disable it here and restart. See [Scheduling and proactivity](../using/scheduling.md).

## websocket

The channel the WebUI talks over. On-device, the runtime forces `host` and `port` onto this section too, so the WebView reaches chat and the HTTP API from one origin.

| Key | Type | Default | Effect |
|---|---|---|---|
| `websocket.enabled` | bool | `false` in the schema, `true` as written on-device | Enables the WebSocket channel. **An explicit `false` is honoured and does disable it** — `channels/dispatcher.py` reads it and returns, with a log line saying so. The Android runtime writes `true` via `setdefault`, which does not overwrite a `false` you put there yourself. |
| `websocket.host` | string | `"127.0.0.1"` | Bind address. Forced to the runtime value on-device. |
| `websocket.port` | int | `8765` | Off-device default. On the phone this is always overwritten with `18790`. |
| `websocket.path` | string | `"/"` | WebSocket path. Must start with `/`. |
| `websocket.tokenIssueSecret` | string | `""` | The install's shared secret. Generated once at first boot (32 random URL-safe bytes) and persisted here with `chmod 600`. It authenticates both the WebSocket handshake (`?token=…`) and the HTTP API (`Authorization: Bearer …` or `X-Jenny-Auth:`). Never regenerate it casually — the WebUI receives it through the bootstrap route. |
| `websocket.websocketRequiresToken` | bool | `true` | Requires the token in the handshake. |
| `websocket.allowFrom` | string[] | `["*"]` | Client-ID allowlist for connections. This is the real key — there is no `channels.*.allowFrom` anywhere in the codebase. |
| `websocket.streaming` | bool | `true` | Stream assistant text as it is generated. |
| `websocket.sendProgress` | bool | `true` | Send progress events to the WebUI. |
| `websocket.sendToolHints` | bool | `false` | Send the short `tool(args…)` hints as progress events. |
| `websocket.showReasoning` | bool | `true` | Config-only. Delivers the model's reasoning stream to the WebUI. **Turning it off also stops it being recorded**: the transcript append happens inside the same send path the dispatcher gates on this flag, so with it off the reasoning is absent from replay and history too, not just from the live view. Telegram never receives reasoning regardless. |
| `websocket.sendMaxRetries` | int 0–10 | `3` | Delivery attempts per outbound message, including the first send. Backoff 1 s, 2 s, 4 s, then capped at 4 s. |
| `websocket.maxMessageBytes` | int 1024–41943040 | `37748736` | Max inbound frame size (36 MiB), sized for four ~6 MB images after client-side normalization plus base64 overhead. |
| `websocket.pingIntervalS` | float 5–300 | `20.0` | Keepalive ping interval. |
| `websocket.pingTimeoutS` | float 5–300 | `20.0` | Keepalive ping timeout. |
| `websocket.sslCertfile` / `websocket.sslKeyfile` | string | `""` | Optional TLS material for off-device deployments. |

One validation to be aware of: setting `host` to `0.0.0.0` or `::` **with an empty `tokenIssueSecret` is rejected at load** — the config raises rather than exposing an unauthenticated agent on every interface. Wire-level details for integrators: [WebSocket protocol](./websocket.md).

## telegram

| Key | Type | Default | Effect |
|---|---|---|---|
| `telegram.enabled` | bool | `false` | Master switch. The channel starts only when this is true **and** a bot token is present. |
| `telegram.botToken` | string \| null | `null` | BotFather token, stored in clear text. The API never returns it in full — the settings payload carries only a `first4...last4` hint. |
| `telegram.botUsername` | string \| null | `null` | Cached bot username from `getMe`. |
| `telegram.pairedChatId` | string \| null | `null` | The paired chat. Its presence *is* the "paired" state — there is no stored enum. |
| `telegram.pairedUsername` | string \| null | `null` | Display name of the paired user. |
| `telegram.pairingCode` | string \| null | `null` | The 6-digit code, persisted so pairing survives the frequent process restarts on Android, and cleared once pairing succeeds. |
| `telegram.pollTimeoutS` | int 1–300 | `50` | Long-poll timeout against the Telegram API. |

Pairing, the throttle, and the asymmetric view between Telegram and the WebUI: [Telegram bridge](../using/telegram.md).

## tools

Toggles for the built-in tool groups. Only web search and location have UI controls; everything else here is config-only. Full behavior of each tool: [Tool reference](./tools.md).

### tools.file

| Key | Type | Default | Effect |
|---|---|---|---|
| `tools.file.enable` | bool | `true` | Registers the filesystem tools (`read_file`, `write_file`, `edit_file`, `apply_patch`, `list_dir`, `find_files`, `grep`). Off means the agent cannot touch files at all. |
| `tools.file.exposePackageSource` | bool | `true` | Adds Jenny's own extracted Python source as an extra **read-only** root, so the agent can inspect the framework it runs on. Never writable. |

### tools.pythonExec

| Key | Type | Default | Effect |
|---|---|---|---|
| `tools.pythonExec.enable` | bool | `true` | Registers `python_exec` and the exec-session tools. |
| `tools.pythonExec.timeout` | int ≥ 0 | `60` | Seconds per execution. `0` means no limit. |
| `tools.pythonExec.maxOutputChars` | int 1000–50000 | `10000` | Output truncation threshold. |
| `tools.pythonExec.allowedModules` | string[] | see below | Import allowlist. |
| `tools.pythonExec.blockedModules` | string[] | see below | Import denylist. |

Defaults — allowed: `os`, `sys`, `pathlib`, `json`, `re`, `math`, `datetime`, `collections`, `itertools`, `functools`, `typing`, `io`, `shutil`, `glob`, `hashlib`, `base64`, `asyncio`, `csv`, `platform`, `time`, `struct`, `textwrap`, `unicodedata`, `html`, `xml`, `dataclasses`, `enum`, `uuid`. Blocked: `subprocess`, `pty`, `shlex`, `multiprocessing`, `ctypes`, `socket`, `signal`, `termios`, `tty`, `grp`, `pwd`, `resource`, `syslog`, `curses`, `readline`, `_thread`, `fcntl`.

**`httpx` and `urllib` are deliberately absent from the allowlist.** A raw HTTP client inside executed code could reach loopback, link-local and RFC1918 targets (SSRF) or read local files through `file://`, bypassing both the SSRF policy and the workspace policy. Outbound HTTP stays available through the `http_get` / `http_post` helpers, which run every URL through the SSRF check. Adding `"httpx"` back to `allowedModules` opts out of that protection knowingly. And note what the code itself says: the allowlist is a guardrail, **not a sandbox**.

### tools.androidWeb

| Key | Type | Default | Effect |
|---|---|---|---|
| `tools.androidWeb.enable` | bool | `true` | Registers `web_search`, `web_fetch` **and the five `browser_*` tools**. It is the only switch: there is no per-half toggle — `search.enable` / `fetch.enable` do not exist in the schema, so writing them changes nothing. |
| `tools.androidWeb.search.searchEngine` | string | `"bing"` | The only supported value; the settings endpoint rejects anything else. |
| `tools.androidWeb.search.maxResults` | int | `5` | Results per search. The UI validates 1–10. |
| `tools.androidWeb.search.timeout` | int | `30` | Seconds per search. The UI validates 1–120. Also used as the fetch timeout. |
| `tools.androidWeb.fetch.maxChars` | int | `50000` | Max characters extracted per page. The UI validates 1000–200000. |
| `tools.androidWeb.browser.timeout` | int | `30` | Seconds per browser action. |
| `tools.androidWeb.browser.maxSnapshotChars` | int | `2500` | Cap on a `browser_snapshot` payload. |
| `tools.androidWeb.browser.maxReadChars` | int | `4000` | Cap on a `browser_read` payload. |
| `tools.androidWeb.browser.idleCloseS` | int | `300` | Seconds of inactivity after which an open session is reaped on its own — an open browser costs ~100 MB of RAM. |

### tools.location

| Key | Type | Default | Effect |
|---|---|---|---|
| `tools.location.enable` | bool | `true` | The user-facing toggle. With it on **and** the Android permission granted, a last-known position line is injected into the context on every turn — which means it reaches your LLM provider on every turn. |
| `tools.location.telegramTtlS` | int ≥ 60 | `3600` | How long a location shared through Telegram overrides the GPS reading, for that channel only. Kept in memory, lost on restart. |
| `tools.location.freshTimeoutS` | int 1–60 | `15` | How long `get_location precise=true` waits for a fresh GPS fix. |

See [Location](../using/location.md).

### tools.ssh

Access to remote machines. Both gates are closed by default and **both are necessary**: this is the only capability that acts on a computer other than the phone.

```json
{
  "tools": {
    "ssh": {
      "enable": true,
      "hosts": [
        {
          "alias": "nas",
          "host": "nas.home.lan",
          "port": 22,
          "username": "jenny",
          "description": "The home NAS",
          "auth": "key",
          "jobLogDir": "/tmp/jenny-jobs"
        }
      ]
    }
  }
}
```

| Key | Type | Default | Effect |
|---|---|---|---|
| `tools.ssh.enable` | bool | **`false`** | Master switch. **Asymmetric**: turning it off is checked on every single call, so it applies instantly (it is the emergency stop); turning it on only registers the tools at the next gateway start. |
| `tools.ssh.hosts[]` | array | `[]` | The registered machines. An empty list means no SSH tools even with `enable: true` — the agent can only name an alias from here, never an address. |
| `tools.ssh.connectTimeoutS` | float 1–60 | `15.0` | Connection timeout. |
| `tools.ssh.commandTimeoutS` | int 1–300 | `60` | Ceiling for a single `ssh_exec`, and for the short launch/poll/stop commands `ssh_job` issues. Low on purpose, and the wake lock added in 0.6.6 is not a reason to raise it: a wake lock keeps the CPU awake, not the connection. A long command waited for on an open SSH channel still dies when the phone walks from wifi to mobile data, or when the gateway restarts. Long work belongs to `ssh_job`, which detaches it. A `timeout_s` passed by the model can only lower this, never raise it. |
| `tools.ssh.maxOutputChars` | int 1000–50000 | `10000` | Truncation threshold for command output and for each `ssh_job` poll. The result reports how many characters were dropped. |
| `tools.ssh.keepaliveIntervalS` | int 0–300 | `30` | Server-alive interval on the SSH session; `0` disables it. |
| `tools.ssh.idleCloseS` | int ≥ 30 | `300` | How long a pooled connection may sit unused before it is closed. Enforced by a reaper in both backends (the Android `SshBridge` and the dev backend), which wakes at most once a minute and drops any session idle past this. It exists because a phone's connection is not free to keep alive: a forgotten session survives a wifi-to-mobile switch as a socket that will fail on the next command anyway. The floor of 30 is enforced by the schema, so there is no way to disable the reaper from config. |
| `tools.ssh.maxTransferBytes` | int ≥ 1024 | `52428800` | Cap for `ssh_transfer` (50 MB), in both directions. On a download it is checked with a remote `stat` before the local file is opened, so an oversize transfer leaves nothing behind. |

Per host:

| Key | Type | Default | Effect |
|---|---|---|---|
| `alias` | string | required | The identity of the host and the **only** thing the model ever passes. Also the name of the key file on disk, so the Settings UI restricts it to 1–32 chars of `A–Za–z0–9_-` starting alphanumeric. Not renameable. |
| `host` | string | required | Hostname or IP. Validated against the network policy when saved **and** again at connection time, so a name that later starts resolving to a blocked address is caught. RFC1918, IPv6 ULA and CGNAT are allowed (a home server on the LAN or over Tailscale is the point); loopback, link-local/metadata and `0.0.0.0/8` are not — those are the phone itself. |
| `port` | int 1–65535 | `22` | |
| `username` | string | required | Login account. |
| `description` | string | `""` | Shown **to the model** by `ssh_hosts`, so it can pick between machines and tell you which one it acted on. |
| `hostKeyFingerprint` | string \| null | `null` | **Display only.** The enforcement is the `known_hosts` file next to the private key; without a matching line there, the connection is refused no matter what this says. Required in both `auth` modes. |
| `auth` | `"key"` \| `"password"` | `"key"` | How Jenny logs in. The default is unchanged, so hosts registered before this option existed keep behaving exactly as they did. |
| `password` | string \| null | `null` | Only read when `auth` is `"password"`, where it is mandatory — Settings refuses to save a password host without one. **Stored in clear text in `config.json`**, like `telegram.botToken` and `providers[].apiKey`. Never returned by the settings API (the payload carries a `has_password` boolean instead), never in a tool argument, never in a tool result, and kept out of `repr()` so it can't fall into a log line. Switching a host back to `auth: "key"` through Settings clears it. |
| `jobLogDir` | string | `"/tmp/jenny-jobs"` | Where `ssh_job` writes its per-job log and exit-code files **on the server**. No field in Settings — config-only. Nothing cleans these up, and `/tmp` is wiped on reboot on most systems, so point it somewhere durable if you want old job output to survive. |

The private key (`<alias>_ed25519`, one per host) and `known_hosts` live in `<filesDir>/ssh` — **outside** the workspace, alongside it. That is why the agent's file tools cannot read them, and also why they are absent from snapshots and from an exported `.jbk`: a restore brings back this host list but no keys.

A `password` does **not** get that protection, and the difference is worth stating plainly rather than discovering later: `config.json` is inside the workspace, so the file holding it is readable by the agent's own file tools and is included in snapshots and in an exported `.jbk` (encrypted there with your backup passphrase). The practical consequence is that a restore reactivates a password host immediately while a key host has to be set up again — convenient in one direction, and the reason a dedicated key is still the better default in the other: a key can be revoked from `authorized_keys` without changing the password you use to log in yourself. See [SSH access](../using/ssh.md).

### tools.my, introspect, diagnostics

| Key | Type | Default | Effect |
|---|---|---|---|
| `tools.my.enable` | bool | `true` | Registers the `my` self-inspection tool. |
| `tools.my.allowSet` | bool | **`false`** | The only restrictive default in the whole tools section. When false, `my` is read-only and a write attempt returns `Error: set is disabled (tools.my.allow_set is false)`. |
| `tools.introspect.enable` | bool | `true` | Registers `get_source` — read-only access to Jenny's own package source. |
| `tools.diagnostics.enable` | bool | `true` | Registers `get_recent_logs` (in-memory buffer, ~500 lines, cleared on restart). It is the first stop for troubleshooting: ask Jenny to check her own logs. |

`tools.restrictToWorkspace` also appears under `tools` — it is a **mirror**, not a setting. See below.

## security

The canonical home for the two policy switches.

| Key | Type | Default | Effect |
|---|---|---|---|
| `security.restrictToWorkspace` | bool | **`true`** | Keeps workspace-aware tools inside the workspace (plus `skills/`, the media directory, and the read-only package source). This is an application-level, fail-closed policy boundary, not an OS sandbox. |
| `security.ssrfWhitelist` | string[] | `[]` | CIDR ranges exempted from the SSRF guard, e.g. `["100.64.0.0/10"]` for Tailscale. Keep entries as narrow as possible (`192.168.1.50/32`). |

Two things people get wrong here:

- **The old location still loads, but is not where you edit.** A legacy config carrying `tools.restrictToWorkspace` / `tools.ssrfWhitelist` and no `security` block is migrated into `security` automatically by a validator, and `tools.restrictToWorkspace` is then kept in sync as a mirror the tool layer reads. Write to `security`.
- **The SSRF whitelist covers agent tools, not provider calls.** It gates `web_fetch`, `download_file`, the `python_exec` HTTP helpers, media ingestion, and — through a looser blocklist that permits private LAN ranges — Jenny App servers and `tools.ssh` targets. Requests to your LLM endpoint do not go through it — a self-hosted model on a private address works without whitelisting anything (see [Local models](./local-models.md)).

Full threat model: [Security model](../internals/security-model.md).

## power

Anti-doze: the wake lock, the scheduled wake-ups, and the outage log behind **Settings → Background activity**.

The problem this section exists for is worth stating plainly, because it is not obvious: **a foreground service keeps the *process* alive, not the *processor*.** With the screen off the phone suspends, the agent's own timers stop advancing, and anything waiting on one waits with them. A job that fires late isn't late because the code was slow — it's late because the clock it was sleeping on was frozen. Only a `PARTIAL_WAKE_LOCK` prevents the CPU suspending, and only an alarm registered with Android can wake it up again at a known moment. These keys decide how much of each Jenny asks for.

| Key | Type | Default | Effect |
|---|---|---|---|
| `power.keepAwake` | `"off"` \| `"turns"` \| `"always"` | **`"turns"`** | How much of the time Jenny holds a wake lock. `turns` takes one around real work — an agent turn, a cron/Dream/heartbeat job, an SSH command, a Telegram update being processed — and releases it immediately after. `always` holds one for the entire life of the gateway service: nothing drifts, and it costs real battery, so it's the setting for a phone that lives on a charger. `off` is the pre-0.6.6 behaviour, kept as an escape hatch if the lock misbehaves on some device. A value that isn't one of the three is a typo, not a reason to refuse to boot: it's logged and treated as `turns`. **This is the one key here with a UI control** (Settings → Background activity), and it takes effect at the next gateway restart — the service-lifetime lock is taken once, at startup. |
| `power.wakelockRotateMin` | int 0–240 | `50` | Minutes after which the service-lifetime lock (`keepAwake: "always"` only) is released and immediately re-acquired. `0` disables rotation. This is not hygiene for its own sake: PowerGenie, the battery manager on Honor/Huawei, kills an app that has held a wake lock for more than 60 minutes, so the default sits deliberately under that line. Per-turn locks are short-lived and never rotated. |
| `power.watchdogEnabled` | bool | `true` | A self-chaining alarm that checks whether the gateway is still alive and starts it again if it isn't. It exists because the gateway can be killed without anything noticing — nothing in the app is in a position to report its own death. Setting this to `false` is also how you dismantle a chain armed by an earlier run: the alarms live in Android's `AlarmManager`, not in Jenny's process, so nothing disarms them on their own. |
| `power.watchdogIntervalMin` | int 5–120 | `15` | Base interval between watchdog checks. The interval adapts rather than holding steady: ×2 with the screen off, ×4 in deep Doze. Spacing them out there is not battery thrift — an app that wakes the system on a fixed beat while it should be idle is exactly what OEM battery managers flag and then kill. The gateway is considered dead once its heartbeat is three (worst-case) periods stale; a false positive costs one no-op start, a false negative leaves the agent down until you notice. |
| `power.alarmDrivenCron` | bool | `true` | Arms an OS alarm for the scheduler's next real deadline, alongside the ordinary in-process timer. The timer sleeps on a clock that stops while the SoC is suspended; the alarm doesn't. The alarm targets the true next deadline, not the scheduler's shorter internal poll, so an idle phone isn't woken every few minutes for nothing. |
| `power.alarmClockFallback` | bool | `true` | An 8-hourly wake-up registered as an *alarm clock* — the one alarm category no ROM dares suppress. It is the last net under everything else, but only where it can actually register as one: measured on-device, `setAlarmClock` still needs the exact-alarm permission, and without it this net degrades to the same inexact alarm as the rest rather than outranking them. It has a flag of its own for a cosmetic reason that is nonetheless real: on many ROMs a pending alarm-clock lights the alarm icon in the status bar. Three wake-ups a day, rather than one every quarter hour, is what keeps it under any "this app wakes the system too much" heuristic. Switching it off *cancels* the queued alarm rather than merely not re-arming it — otherwise the icon you wanted gone would linger for up to eight hours. |
| `power.gapWarningMin` | int ≥ 5 | `60` | How long a stretch of downtime has to be before it's recorded as an outage and shown in Settings → Background activity. The measurement is taken across the gateway's own death, on the wall clock, because that's the only clock that survives both the process and a reboot; implausible values (a clock that jumped, anything over a month) are discarded rather than reported as a ten-year outage. At most 20 outages are kept, in `<workspace>/state/power_gaps.json`. |

Two things worth being clear about:

- **`keepAwake` is the only one of these with a switch in the app.** The rest are calibration, not decisions you can make well without reading the paragraphs above, so they stay config-only and need a restart.
- **None of this stops an OEM battery manager killing the app.** No application code can. The watchdog, the periodic worker and the alarm-clock net are about coming *back* quickly and about the outage being visible afterwards rather than silent — see [Troubleshooting](../using/troubleshooting.md#reminders-and-periodic-checks-arent-firing).

The defaults above are reasoned from Android's documented behaviour and from what OEM battery managers are known to punish; they have not yet been tuned against measurements on a real device under real Doze. See [Scheduling and proactivity](../using/scheduling.md) for what this does and does not guarantee a reminder, and [Android permissions](./android-permissions.md) for `WAKE_LOCK` and `SCHEDULE_EXACT_ALARM`.

## workspace

These govern the **WebUI Workspace tab**, not the agent's file tools — the agent is bounded by `security.restrictToWorkspace` instead.

| Key | Type | Default | Effect |
|---|---|---|---|
| `workspace.enabled` | bool | `true` | Off makes every `/api/workspace/*` route answer `503 workspace is disabled` — the Workspace tab stops working. |
| `workspace.maxFileSize` | int | `1000000` | Max bytes the file viewer will read (1 MB). |
| `workspace.allowWrite` | bool | `true` | Off makes write, mkdir, rename and copy answer `403 workspace writes are disabled`. |
| `workspace.allowDelete` | bool | `true` | Off makes delete answer `403 workspace deletes are disabled`. |

## snapshots

Local versioning of the workspace, plus the key derivation used by encrypted backup export. Snapshots are created by the runtime without involving the LLM.

| Key | Type | Default | Effect |
|---|---|---|---|
| `snapshots.enabled` | bool | `true` | Master switch for automatic snapshots. |
| `snapshots.scanIntervalMinutes` | int ≥ 1 | `5` | How often the workspace is scanned for changes. |
| `snapshots.quietMinutes` | int ≥ 1 | `10` | Quiet period after the last change before a snapshot is taken. |
| `snapshots.dailySafetySnapshot` | bool | `true` | Takes one snapshot a day even without qualifying changes. |
| `snapshots.retentionRecent` | int ≥ 1 | `20` | The most recent N snapshots are always protected from pruning, including from the age horizon. |
| `snapshots.retentionThinAfterDays` | int ≥ 1 | `30` | Beyond this age, history is thinned to roughly one snapshot per day. |
| `snapshots.retentionMaxAgeDays` | int ≥ 0 | `0` | Age horizon in days; `0` means keep forever. The Settings selector maps to 7 / 30 / 365 / 0. **Changing retention prunes immediately.** |
| `snapshots.pbkdf2Iterations` | int 100000–10000000 | `600000` | PBKDF2 iterations for the exported `.jbk` backup key. The ceiling mirrors the container format's own limit. |
| `snapshots.excludeGlobs` | string[] | see below | Paths never captured. |

Default excludes: `ui/**`, `logs/**`, `.jenny/logs/**`, `.jenny/snapshots/**`, `.jenny/backup_staging/**`, `**/__pycache__/**`, `*.tmp`, `*.tmp.*`. See [Backup and restore](../using/backup.md).

## apps

| Key | Type | Default | Effect |
|---|---|---|---|
| `apps.enabled` | bool | `true` | Enables the Jenny Apps runtime and the dynamic `<slug>_<action>` tools it registers. |
| `apps.httpTimeoutS` | float 1–120 | `20.0` | Timeout for an app's outbound HTTP proxy calls. |
| `apps.maxCollectionBytes` | int | `5000000` | Per-collection storage ceiling (5 MB). Writes past it fail with `413`. |

See [Mini-apps](../using/mini-apps.md).

## updates

The in-app update check. It is the one outbound connection you did not switch on, so it is documented here rather than left to a contributor page — see [Privacy and security](https://github.com/flagdizero/jenny-android-ai-agent#privacy-and-security) in the README for what it does and does not send.

| Key | Type | Default | Effect |
|---|---|---|---|
| `updates.enabled` | bool | `true` | Registers the periodic `update_check` job at startup **and** gates every run of it: a job left in the cron store by an earlier boot still exits before touching the network when this is `false`. |
| `updates.manifestUrl` | string | the release's `latest.json` on GitHub | Where the version manifest is fetched from. A plain `GET`, redirects validated per hop, response size-capped. |
| `updates.checkIntervalH` | int 1–168 | `24` | Hours between checks. The default is not a network compromise — it is how often it makes sense to *interrupt*, since every positive result is an interruption. |
| `updates.notifyInChat` | bool | `true` | Whether a newer version opens a chat message, or stays visible only where you go looking for it. |

Turning `enabled` off stops the check; the `install_update` tool remains available for when you ask for it explicitly. See also [Android permissions](android-permissions.md) for the three permissions the install half needs.

## wiki

| Key | Type | Default | Effect |
|---|---|---|---|
| `wiki.enabled` | bool | `true` | Off makes every wiki route answer `503`. |
| `wiki.wikisDir` | string | `"wikis"` | Directory holding the wikis, relative to the workspace. |
| `wiki.extensions` | string[] | `["fenced_code", "tables", "toc", "wikilinks", "mermaid"]` | Python-Markdown extensions used to render wiki pages. Mermaid renders here and only here — not in chat. |

See [Wiki](../using/wiki.md).

`wiki.wikisDir` is also where [projects](../using/projects.md) live — a project is a wiki — so `wiki.enabled: false` disables project creation too, and the create dialog says so. Renaming this directory moves every project with it; the gardener and the project-scope resolver both read the configured name rather than a hardcoded `wikis`.

## modelPresets

Named bundles of model settings you can switch between at runtime with `/model <name>`. `modelPresets` is a top-level object; its keys are your own preset names.

```json
{
  "modelPresets": {
    "fast": {
      "label": "Fast",
      "provider": "openai",
      "model": "gpt-4.1-mini",
      "maxTokens": 4096,
      "contextWindowTokens": 65536,
      "temperature": 0.2,
      "reasoningEffort": "low"
    },
    "deep": {
      "label": "Deep",
      "provider": "anthropic",
      "model": "claude-opus-4-5",
      "maxTokens": 8192,
      "reasoningEffort": "adaptive"
    }
  },
  "agents": { "defaults": { "modelPreset": "fast" } }
}
```

| Field | Type | Default | Effect |
|---|---|---|---|
| `label` | string \| null | `null` | Display name. |
| `provider` | string \| null | `null` | Name of a configured provider entry. |
| `model` | string \| null | `null` | Model ID. Falls back to the current model when null. |
| `maxTokens` | int \| null | `null` | Overrides output tokens while the preset is active. |
| `contextWindowTokens` | int \| null | `null` | Overrides the context budget. Falls back to the current value when null. |
| `temperature` | float \| null | `null` | Overrides the sampling temperature. |
| `reasoningEffort` | string \| null | `null` | Per-preset reasoning effort — including `none` and `adaptive`. This is the tidiest way to keep several thinking levels one command apart. |

How switching behaves:

- `/model` with no argument prints the current model, the active preset (`(none)` when unset), and the available preset names.
- `/model <name>` applies the preset **for future turns** — model, context window, and generation settings on the active provider. An unknown name returns `Could not switch model preset: …` followed by the available names.
- A preset's `provider` field selects request routing where the active provider supports it; provider objects are not swapped mid-run, so a preset pointing at a genuinely different backend needs an app restart (or a provider change from Settings, which does hot-reload).
- Runtime switches are **not written back to `config.json`**. `agents.defaults.modelPreset` decides what you start with after a restart.

## Cross-references

- [Settings](./settings.md) — the UI-first view of the settings that have screens
- [Providers and models](./providers.md) — choosing formats, base URLs, and models
- [Tool reference](./tools.md) — what each tool actually does with these toggles
- [Security model](../internals/security-model.md) — workspace policy, SSRF, and where the real boundaries are
- [Android permissions](./android-permissions.md) — `WAKE_LOCK`, `SCHEDULE_EXACT_ALARM` and the battery exemption behind the `power.*` keys
- [Projects](../using/projects.md) and [The gardener](../using/gardener.md) — the `gardener.*` keys and `compactProjectsWhenIdle` from the user's side
- [Memory and Dream](../using/memory.md), [Scheduling and proactivity](../using/scheduling.md), [SSH access](../using/ssh.md), [Telegram bridge](../using/telegram.md), [Backup and restore](../using/backup.md)
- [Troubleshooting](../using/troubleshooting.md) — what to do when a config change breaks the boot
