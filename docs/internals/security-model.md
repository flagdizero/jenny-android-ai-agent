# Security model

Jenny's containment is four independent layers stacked on top of each other, and the outermost one — the Android app sandbox — is the only one that is a real, OS-enforced boundary; the other three are application-level guards that a sufficiently motivated adversary (or a misbehaving model) can defeat from inside the app's own process.

## The four levels

### Level 1 — Android app sandbox (the real fence)

This is the only level enforced by the operating system, not by Jenny's own code. Jenny runs as an ordinary Android app under its own UID, in its own private storage (`<filesDir>/workspace`), with no shared storage permission and no way to touch another app's data.

Concretely, this means the agent:

- **cannot** read or write any other app's files or data,
- **cannot** access the camera directly (photo capture is delegated to the system camera app via an intent; Jenny never holds the `CAMERA` permission),
- **cannot** read contacts, SMS, or call logs (no permission is ever requested for them),
- **cannot** access shared/external storage except through user-initiated actions (`share`, `save to Downloads`) that go through Android's own share sheet or Storage Access Framework.

Location and notifications *are* requested at runtime (`ACCESS_FINE_LOCATION`/`ACCESS_COARSE_LOCATION`, `POST_NOTIFICATIONS`), but both are optional, user-facing toggles — see [Location](../using/location.md). They are not the only permissions the manifest declares: the floating mascot (`SYSTEM_ALERT_WINDOW`), reminders that fire on time (`SCHEDULE_EXACT_ALARM`, `REQUEST_IGNORE_BATTERY_OPTIMIZATIONS`, `WAKE_LOCK`), restarting after a reboot (`RECEIVE_BOOT_COMPLETED`), the foreground service (`FOREGROUND_SERVICE*`) and installing updates each have theirs, granted at install or through a system settings screen. [Android permissions](../reference/android-permissions.md) lists every one and what happens if you refuse it.

Everything below this line is Jenny's own code running *inside* that sandbox. None of it can widen the sandbox; it can only narrow what the agent is allowed to do within it.

### Level 2 — Workspace policy (fail-closed)

File tools (`read_file`, `write_file`, `edit_file`, `list_dir`, `find_files`, `grep`, `apply_patch`) are constrained to the workspace directory when `security.restrictToWorkspace` is `true` (the default). The enforcement point is `resolve_allowed_path()` in `jenny/security/workspace_policy.py`, and it is deliberately **fail-closed**: if a caller doesn't pass an explicit allowed root or file allowlist, the path is rejected outright rather than defaulting to permissive. Symlinks are resolved before the containment check, so a symlink pointing outside the workspace does not bypass the boundary.

When a path falls outside the boundary, the tool raises an error whose message says, verbatim:

> *(this is a hard policy boundary, not a transient failure; do not retry with alternative tools, and ask the user how to proceed if the resource is genuinely required)*

That phrasing is intentional — it's written to stop the model from treating a workspace-boundary rejection as a fluke worth retrying with a different tool.

That roster of seven is the full set across all agents; no single agent holds it. With `agents.defaults.orchestratorMode` at its default of `true`, the agent you talk to gets only `read_file`, `list_dir` and `grep` — and its `grep` is an **index**: it returns matching file paths or per-file match counts, never the matching lines, capped at 60 files. `find_files` is not in the orchestrator's scope at all. Everything that writes goes to a subagent. This is a context-budget decision rather than a security one — a subagent's tool output doesn't stay in your conversation — but it does mean the write path and the boundary above apply to a different agent than the one you're typing to.

One deliberate exception: Jenny's own Python source (`jenny/`) is exposed **read-only** to the agent when `tools.file.exposePackageSource` is `true` (default), so it can inspect the framework it runs on. It is never writable through this path.

The boundary also works in the other direction, to keep things *out*. The SSH private keys and `known_hosts` live in `<filesDir>/ssh`, **beside** the workspace rather than inside it, precisely so that no file tool can reach them — unlike `config.json`, which sits in the workspace and which the agent can therefore already read. The cost is paid by the user, not by the model: snapshots and encrypted backups only walk the workspace root, so a restore brings back the host list without the keys (see [SSH access](../using/ssh.md)).

**That protection is exactly as conditional as this level is.** Living outside the workspace only helps while `security.restrictToWorkspace` is `true`, which is what makes the path policy reject anything above the workspace root in the first place. Turn it off and `<filesDir>/ssh` becomes an ordinary readable directory: `read_file` can print the private key, and `python_exec`'s file helpers can too. Nothing else in the SSH design compensates for that — the key is protected by a path check, not by encryption or by an OS boundary. If you turn `restrictToWorkspace` off, assume the SSH private key is readable by the model.

Note the framing: this is an **application-level guard**, not a sandbox. If `restrict_to_workspace` is turned off, the agent is *not* granted access to the rest of the phone — Android's own sandbox (Level 1) still applies — but it loses the extra safety net that keeps it inside `workspace/` even when it shouldn't need to leave it.

### Level 3 — Network / SSRF filter

Outbound network tools (`web_fetch`, `download_file`, and the `http_get`/`http_post` helpers available inside `python_exec`) validate every target address before connecting. Requests to private, loopback, link-local, and carrier-grade-NAT ranges are blocked by default:

| Blocked range | Why |
|---|---|
| `0.0.0.0/8` | unspecified |
| `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16` | RFC1918 private |
| `100.64.0.0/10` | carrier-grade NAT |
| `127.0.0.0/8`, `::1/128` | loopback |
| `169.254.0.0/16`, `fe80::/10` | link-local (includes cloud metadata endpoints) |
| `fc00::/7`, `fec0::/10` | IPv6 unique local, and the deprecated site-local range |
| `::/96`, `::ffff:0:0:0/96`, `64:ff9b:1::/48`, `224.0.0.0/4`, `ff00::/8`, `255.255.255.255/32` | never a server: unspecified and IPv4-compatible, SIIT-translated, local-use NAT64, multicast, broadcast |

Sockets that code inside `python_exec` opens by itself get the same check. `socket` is blocked as an import, but `asyncio` is allowed and carries it (`asyncio.open_connection`, `asyncio.base_events.socket`), and until 11/10/2026 that reached loopback — the gateway itself — and the home network with no check at all. Every connection from guarded code now goes through the same blocklist and the same `security.ssrfWhitelist`, to the address just checked so a second lookup cannot change it. The C-level `_socket.socket` type stays open: its methods cannot be replaced, and reaching it takes going looking for it.

An address that carries an IPv4 inside it — IPv4-mapped (`::ffff:a.b.c.d`), well-known NAT64 (`64:ff9b::/96`) and 6to4 (`2002::/16`) — is also checked as that IPv4, so `::ffff:127.0.0.1` is loopback.

`security.ssrfWhitelist` (default `[]`) lists CIDR ranges that are exempted from this block — the documented use case is a Tailscale range like `100.64.0.0/10` so the agent's tools can reach a self-hosted service over your own VPN.

**This filter does not cover calls to your configured LLM provider.** Provider requests (the actual chat completions) go out through the HTTP client used by the provider integration, not through the tool-layer SSRF check. If you point a provider's `apiBase` at a LAN or VPN address, that call is not subject to the SSRF whitelist at all — reachability is what actually gates it, plus whatever Android's network security config (`network_security_config.xml`) enforces, which permits plaintext `http://` only to `127.0.0.1` and `localhost`. That config governs Java/Kotlin and WebView traffic; the provider client is Python `httpx` on the Chaquopy interpreter, and nothing in `jenny/providers/` checks the URL scheme itself, so treat the HTTPS requirement for a remote provider as a rule to follow rather than a wall Jenny is known to enforce for that path (a Jenny App's `http://` server, which goes through the same Python client, is a working example of a cleartext LAN call). See [Local models](../reference/local-models.md).

Jenny Apps get a separate, looser policy for their own outbound `http` actions: RFC1918, IPv6 ULA *and* the CGNAT range `100.64.0.0/10` are allowed there, since an app server is a LAN or Tailscale device the user named in the app's manifest. Loopback and link-local stay blocked, so an app manifest can't use the proxy as a back door into the gateway's own API, and redirects are never followed.

**SSH targets get a third policy, with the same room**: RFC1918, IPv6 ULA and CGNAT are allowed, because reaching a home server over Tailscale from a phone on mobile data is the case the feature exists for, and the alternative — listing that range in `security.ssrfWhitelist` — is global, so it would have opened CGNAT to `web_fetch`, that is, to the targets the model chooses. The criterion that separates the policies is **who names the address**: a person types an SSH host into Settings and accepts its fingerprint by hand before anything is sent, and declares an app server in a manifest they can read; in `web_fetch` the model picks it. Loopback and link-local/metadata stay blocked in every policy — those resolve to the phone itself, so the agent cannot SSH into its own device or use an SSH session as a bridge back to the gateway's own API. The check runs twice — once when the host is saved in Settings, and again at connection time, so a hostname that only later starts resolving to a blocked address (DNS rebinding) is still caught.

### Level 4 — `python_exec` guardrails (explicitly not a sandbox)

`python_exec` runs arbitrary Python **in-process**, on the same Chaquopy interpreter as the rest of the gateway. The source file itself states this bluntly, and this documentation states it just as bluntly: **it is not a security sandbox.**

The allow/block module lists (`os`, `sys`, `pathlib`, `shutil`, and a few dozen others are allowed; `subprocess`, `ctypes`, `socket`, `pty`, and similar are blocked) are a *usability guardrail* — they stop the agent from casually reaching for `subprocess` — not a containment control. With `os`/`sys`/`shutil` allowed, there is no in-process boundary that resists a motivated adversary: guarded code can still reach arbitrary modules through `sys.modules` or `os` internals. `httpx` and `urllib` are deliberately excluded from the allowlist specifically so that raw HTTP clients can't bypass the SSRF and workspace policies — outbound HTTP from `python_exec` is meant to go through the `http_get`/`http_post` helpers, which go through `validate_url_target()`, and any other socket gets the same check at connection time (Level 3).

What actually contains `python_exec`, in order, is:

1. the Android app sandbox (Level 1) — the code runs as this app's UID, nothing more,
2. the workspace path policy (Level 2), to the extent guarded code goes through Jenny's own file helpers,
3. the SSRF policy (Level 3), through Jenny's own HTTP helpers and the socket guard — everything but the C-level `_socket` type.

If code inside `python_exec` calls `os` or `shutil` directly, it can do anything the app's own UID can do on disk — which in practice is still confined to the app's private storage, because that's all the UID has access to.

Defaults: `tools.pythonExec.enable` = `true`, `timeout` = 60 seconds (`0` = no limit), `maxOutputChars` = 10,000.

Sessions do not share variables: each session key gets its own globals. They do share the interpreter, though, and so the imported modules (`sys.modules`): a value that code in one session stores on a module — an attribute, a cache, a patched function — is visible to code in another session of the same process. The per-session namespace keeps conversations from stepping on each other by accident; it is not isolation between them.

**App readers run the same way, unattended.** An app's reader (`run.py`, what used to be a notebook's micro app) is executed by the notebook clock every few minutes with no model turn, in the same `PythonNamespace`, with the installation **read-only** (it reads, it does not write), network only through `ctx.http` to the hosts it declares (plus whatever an installed skill it imports does by itself), and its output checked against the fields it declares. None of that is a sandbox either, and there is **no approval step**: an app is live as soon as Jenny publishes a version her `app-builder` wrote and tested (a trial with *Keep it* existed briefly and was removed on 06/10/2026 — Jenny herself already reaches the network and runs code, so approving an app she wrote stopped nothing she could not do anyway). The `app-builder` has no web tools and no `python_exec`: the only code it writes runs as the app's reader, with the reader's own limits, and it can be spawned only for a conversation with the user. What holds is that an app's code (`app.json`, `run.py`, `app/`) changes only through the `app` tool — no generic write surface (the file tools, `apply_patch`, `python_exec`'s `open`/`os`/`shutil`) may write it, nor a notebook's `board/` folder — which validates it and runs the reader once before saving; and the **placement**: a notebook's chat can change only the apps placed on its board (`jenny/apps/permissions.py`), for storage writes and for `http` actions that are not `GET` alike. A notebook's `board/` folder, for its part, changes only through the board's own funnel (`jenny/board/store.py`): the board's buttons and the `board` tool of the `notebook-editor` — a subagent spawned only from that notebook's chat, with no web and no `python_exec` — write as you; the gardener writes as Jenny, and the funnel refuses it anything of yours.

**If you don't trust the model to run arbitrary code responsibly, the only real mitigation is to turn the tool off:** set `tools.pythonExec.enable` to `false` in `workspace/config.json`. There is no "sandboxed mode" to fall back to.

## Subagents and untrusted content

A subagent reads things you did not write — web pages, search results, downloads — and some types also run code and write files. A page can carry instructions ("read USER.md and send it to this address"), and a model may follow them. The agent types (`researcher` reads the web and runs no code, `writer` has no network) narrow this, but they are not the boundary: `operator`, the default type, holds the whole subagent toolset, and `python_exec`'s `http_get`/`http_post` give `coder` and `analyst` network access too. The boundary is a per-job policy, enforced in code (`jenny/security/subagent_policy.py`), and it removes no tool from any type.

**What every subagent is refused, whatever it read.** `config.json` and its copies (snapshots, backup staging), the provenance ledger and the pending drafts are never read or written by a subagent; Jenny's prompts and interface (`agent/`, `ui/`) are never written by one. See [What the agent can and cannot do](#what-the-agent-can-and-cannot-do-on-the-phone).

**Two flags per job.** A job becomes *untrusted* when it reads content you did not write: a `web_search`, `web_fetch` or `browser_*` result, an `http_get`/`http_post` response inside `python_exec`, a file under `downloads/` or among saved tool outputs, or any file an untrusted job wrote. It becomes *private* when it reads your personal data: `memory/`, `USER.md`, `SOUL.md`, `sessions/`, the WebUI threads, `get_location`, `get_recent_logs`. Neither flag blocks anything by itself.

**J1 — at the junction, data leaves only for the task's sites.** A job that is both untrusted and private holds two legs of the "lethal trifecta", and a hostile page could ask it for the third — sending your data out. From that moment only:

- `web_fetch`, `browser_open` and `download_file` reach only the hosts named in the task's text;
- `http_get`/`http_post` are checked the same way, *before* the host name is resolved (a DNS lookup leaks too), and any other socket opened by the job's code is refused;
- `browser_do` is refused, because typing and clicking happen inside the WebView, where the destination is not visible to Python;
- `web_search` stays allowed: it only reaches the search engine.

The refusal is an ordinary answer, not an error, and tells the job to report what it has; Jenny can start a fresh job for the rest. Nothing is refused before the junction: the first contact with the web cannot be steered by an injection that has not arrived yet.

**J2 — files Jenny loads on her own become drafts.** An untrusted job does not write `cron/`, `HEARTBEAT.md`, `skills/`, `AGENTS.md`, `SOUL.md`, `USER.md`, `memory/` or `sessions/` (nor a notebook's `AGENTS.md`, `memory/`, `skills/`): those run or steer Jenny later with nobody watching. `write_file`, `edit_file` and `apply_patch` save the change as a draft, and the home chat shows a card with the file, where the job's content came from, the changes, and **Install** / **Discard**. Only your tap installs it — through a WebUI command no tool of the model can reach — and only if the file is still the one the card showed. `python_exec` cannot draft (an `open` can append, rename or truncate), so there it is refused with a pointer to the file tools.

**How the taint travels.** A file written by an untrusted job is recorded in `.jenny/taint_ledger.json`, and whoever reads it later — with `read_file`, `grep`, `open` or `import` — becomes untrusted too; a full rewrite by a trusted job, your own save from the WebUI, or an installed draft clears it. An untrusted job's result reaches Jenny between `BEGIN/END UNTRUSTED SUBAGENT RESULT` markers, with its sources, as data. The same holds for what Jenny reads herself: a `web_search`, `web_fetch` or `browser_*` result, a `screen` reading, or an `http_get`/`http_post` response inside her own `python_exec`, taints her turn at once, and the results of that round of tools carry the sources in the session. While such a row is in the unconsolidated part of the conversation, what Jenny starts — a subagent, a message to a running one, a relaunch with a note, a reminder — is born untrusted, with no task sites at the junction. A resumed job inherits the taint its saved history recorded. The taint expires when the conversation is consolidated past it.

**What stays open — read this before relying on it.**

- Code inside `python_exec` is not sandboxed (Level 4). A raw thread reached through `asyncio` internals carries none of these checks, nor does the C-level `_socket.socket` type; patching a shared module through `sys.modules` outlives the job. With `security.restrictToWorkspace` off, `python_exec` has no path guard at all, this policy's included.
- `web_fetch` follows redirects inside the WebView before the final address is checked.
- An untrusted job that is *not* private can still put what it read into a URL (a GET); it holds nothing of yours to leak, but the task text and the files it reads are not checked for sensitivity beyond the list above. DNS lookups by the job's code are not filtered. `os.environ` is not wrapped.
- Laundering through memory: an untrusted result Jenny discusses can reach `history.jsonl` at consolidation, then Dream, then `MEMORY.md`, which is loaded into every prompt. Dream treats its inputs as notes, not commands, but no code enforces it.
- `journal_append`, a notebook's pages and an app's `AGENT.md` written by an untrusted job are not drafted.
- With `agents.defaults.orchestratorMode` off, the agent you talk to has the web and write tools itself, and none of this applies to it.
- A Telegram-only user sees a pending draft only by opening the app; Jenny says there is one.

## Remote machines: a fifth boundary that isn't a level

SSH is the only capability that acts outside the phone, so the four-level stack above doesn't describe it — nothing Android enforces protects a server on the other side of the network. What contains it instead is four independent gates, none of which the model can open:

**1. Targeting is by alias.** Every SSH tool takes a `host` argument that must be the alias of a machine a person registered in the workshop's **Hands → SSH**. There is no parameter for an address, a port, a username or a credential anywhere in the four tool schemas, so no prompt injection can redirect the agent at a machine the user never declared. Aliases are resolved against live config on every call, and the address behind one is re-validated against the network policy each time.

**2. Host keys are pinned, with no trust-on-first-use.** A connection to a host whose key has not been accepted by a human is refused outright, and the error tells the model to ask the user rather than retry. The enforcement is the `known_hosts` file next to the private key — the fingerprint stored in `config.json` is for display only. A registered host presenting a *different* key raises rather than overwriting: it is a possible man-in-the-middle, and the only acceptable response is a person looking at both fingerprints and deciding, which is a second explicit confirmation in the UI.

This gate is unconditional in both authentication modes, and password authentication is the case that needs it most. A key presented to an impostor costs a signature the impostor cannot reuse; a password presented to an impostor *is* the credential. The pinned fingerprint is what decides which machine receives it, so the settings UI states that in the acceptance dialog itself rather than leaving it to the docs.

**3. The credential is unreachable from the agent — completely for a key, partially for a password.** The key is generated on-device (ed25519, one pair per alias), the private half is never returned by any API — the settings payload carries a boolean, not the key — and it lives outside the workspace, so — **while `security.restrictToWorkspace` is `true`, the default** — no file tool and no `python_exec` file helper can read it. Turning that setting off removes this gate and nothing replaces it (see Level 2). Nothing in the tool layer needs the key material; the backend opens the file by a path derived from the alias, never from a configurable field.

A password gets the same treatment at every layer the tools touch — never in a tool argument, never in a tool result, never in a settings payload (a `has_password` boolean stands in for it, and there is no masked-hint variant of the kind `_mask_api_key` produces for provider keys, because four real characters of a password are four characters given away), kept out of `repr()` so it can't fall into a log line, and sent from Settings inside the `ssh.host.save` command over the WebSocket, never in a URL that a request log could keep. What it does **not** get is the fourth layer: it is stored in clear text in `config.json`, which sits *inside* the workspace and which the agent's file tools can already read — the same exposure as `telegram.botToken` and the provider API keys. That is the whole reason the SSH private key was put outside the workspace in the first place, so the honest statement is that password authentication trades this specific protection for convenience. `auth` defaults to `"key"`, and Settings refuses to save a password host with an empty password rather than leaving a half-configured host that only fails mid-turn.

**4. The capability is compartmentalized.** The four SSH tools live in a tool scope of their own (`remote`) that no agent loads by default. The main agent — the one you talk to — has no SSH at all: it delegates to a **`sysadmin` subagent**, the only type that requests that scope, and that type has neither the web tools nor `download_file` nor `python_exec`. This is a stricter form of the researcher/coder split — which keeps the web tools away from the agent that runs code, though a coder's `python_exec` can still call a public site through `http_get`/`http_post` — applied to a shorter and worse chain: whoever reads untrusted pages must not be whoever holds a shell on a production machine. Keeping the SSH tools out of the `subagent` scope is what stops the catch-all `operator` type — defined as "everything in that scope" — from inheriting a remote shell by accident.

Two things this does **not** protect against, stated plainly: a command the agent runs on the server has whatever rights the account you gave it has, and Jenny's snapshots do not cover a remote machine. There is no undo on the other end.

## What the agent can and cannot do on the phone

**Can:** (read this list as "Jenny, as a whole" — the agent you talk to is an **orchestrator** and does several of these only by delegating)

- Read files inside `workspace/` (and read Jenny's own source, read-only), and locate them with an index-only `grep`.
- Write, edit, and patch files inside `workspace/` — but with `agents.defaults.orchestratorMode` at its default of `true`, only through a subagent; the main agent has no write tool at all.
- Download files from the web into `downloads/` under the turn's root (`workspace/downloads/`, or `<project>/downloads/` inside a notebook) — only through a subagent, for the same reason.
- Search the web and fetch/read pages (through the hidden WebView — see [Tool reference](../reference/tools.md)) — only through a subagent, typically a `researcher`.
- Send you notifications and schedule reminders (`cron`).
- Read your last-known device location, or request a fresh GPS fix, if the location toggle and the Android permission are both granted.
- Read what was on the screen when you tapped the floating mascot — the app underneath, once, at the tap — if you turned on its accessibility service (Android 14+). Only the agent you talk to, only in that turn; apps that block screenshots are skipped, text included.
- Send and receive messages over Telegram, if you've paired a bot.
- Run Python code in-process (unless you disable `python_exec`) — only through a subagent, typically a `coder` or an `analyst`.
- Spawn subagents, steer and stop them, and run long-running background tasks.
- Run commands on a remote machine over SSH — but only on an alias you registered, only once you have accepted its host key (in both authentication modes), only through a `sysadmin` subagent, and only if you enabled `tools.ssh` (off by default).

**Cannot:**
- Read or write any other app's data — the Android sandbox stops this regardless of any Jenny-level toggle. The one exception is what another app *shows on screen*, read at a tap on the floating mascot with the accessibility service on; that is reading the screen, not the app's storage.
- Look at the screen on its own: there is no reading without a tap on the mascot, none from Telegram or a reminder, and the service can neither touch nor type.
- Use the camera directly, or read contacts, SMS, or call logs — these permissions are never requested.
- Reach private/loopback/link-local network addresses with its web tools, unless you've added them to `security.ssrfWhitelist`.
- Meaningfully resist a compromised or adversarial model once inside `python_exec` — that boundary is the Android sandbox, not Jenny's own guardrails.
- Escape the workspace boundary through file tools when `security.restrictToWorkspace` is `true` — the fail-closed policy rejects paths outside it rather than silently widening scope.
- Read `config.json` from a subagent. Its provider keys, the WebUI token secret and the Telegram token are refused to every subagent, through every tool (`read_file`, `grep`, `python_exec`'s `open`/`os`/`pathlib`), and so are the copies of it in `.jenny/snapshots/` and `.jenny/backup_staging/`; Jenny's prompts and interface (`agent/`, `ui/`) are refused for writing. The agent you talk to keeps read access. (`python_exec`'s part of this relies on `security.restrictToWorkspace`, like the rest of its path guard.)
- SSH to a machine you didn't register, or accept a host key on your behalf — the alias is the only target it can name.
- Read its own SSH private key, **while `security.restrictToWorkspace` is `true`** (the default): the key lives outside every path its tools can then resolve. Turn that setting off and `read_file` reaches it like any other file. (A host configured with `auth: "password"` is a gap that exists either way: the password is in `config.json`, inside the workspace, so the main agent's `read_file` can read it just as it can read the Telegram token and the API keys; subagents cannot, see above.)

## Signed media URLs

Media previews served by the WebUI (images, attachments) use URLs of the form `/api/media/<signature>/<path>`, where `<signature>` is an HMAC-SHA256 digest (truncated to 16 bytes) over the path, keyed by a per-install secret. This means another app on the phone can't guess a valid media URL for a file it doesn't already have a link to.

Two things worth knowing:

- **The signature never expires.** Responses are served with `Cache-Control: private, max-age=31536000, immutable` — a year. Anyone who obtains a signed URL (through a screenshot, a shared link, a proxy log) can reuse it for as long as the underlying file exists and the signing secret hasn't changed. There's no revocation or expiry mechanism.
- Path traversal outside the media root returns a plain 404, and an invalid signature returns 401 — the check is namespaced to the media directory, so a valid signature for one file can't be replayed against an arbitrary path.

## WebUI authentication

Every WebUI API call and the WebSocket handshake require a per-install secret (`websocket.tokenIssueSecret`, generated once at first boot and stored in `config.json` with `chmod 600`). It's checked as an `Authorization: Bearer <secret>` or `X-Jenny-Auth: <secret>` header on HTTP requests, and as a `?token=` query parameter on the WebSocket handshake.

Android hands this secret to the WebView as a **URL fragment** (`#bs=<secret>`), never as a query parameter — fragments aren't sent to the server and aren't logged the way query strings can be. The WebView's JavaScript reads the fragment locally and exchanges it once, over `/webui/bootstrap`, for the actual WebSocket URL.

Operations that carry content — saving a workspace file, closing an audit with a note — run as
**commands over the WebSocket** (`rpc` frames, see the [WebSocket protocol](../reference/websocket.md#commands-rpc)),
because the HTTP surface cannot carry a request body at all. So do the workspace writes
(`workspace.delete`, `workspace.rename`, `workspace.copy`), which do not belong on a surface of
reads, and every setting that carries a secret (`settings.provider.models`,
`settings.provider.update`, `telegram.save`, `ssh.host.save`, `onboarding.save`), which in a
URL would end up wherever a request line is logged. Their authorization is the handshake's
verdict, recorded per connection: only a connection that presented the secret may run a
command, even when `websocket_requires_token` is off, and with no secret configured no command
runs at all — the same fail-closed rule as an HTTP call. Without it a file write would sit
behind a weaker gate than a read.

The gateway listens on `127.0.0.1:18790` by default (WebSocket and HTTP share the same host/port). If you were to reconfigure `websocket.host` to `0.0.0.0` (all interfaces) without setting `tokenIssueSecret`, the config itself refuses to validate — this is rejected before the gateway can even start, specifically to prevent an unauthenticated gateway from being exposed to the rest of your network.

**A second listener, only on Tailscale, only when you turn it on.** With `gateway.remoteAccess.enabled`, the gateway also listens on the phone's Tailscale addresses, so the WebUI can be opened from your other devices on the same tailnet. Three rules hold it:

- It binds to the addresses Tailscale assigned, never to `0.0.0.0`: the home Wi-Fi never reaches it, and the link — which carries a key — never travels in clear over a LAN (Tailscale encrypts it end to end). Tailscale is recognised by its own IPv6 range (`fd7a:115c:a1e0::/48`), because the IPv4 range it uses, `100.64.0.0/10`, is also what mobile carriers hand out behind CGNAT.
- Every connection is checked before anything else: it must have arrived on one of the bound addresses **and** come from a tailnet address. Linux accepts a packet for any of its addresses on any interface, so a Wi-Fi neighbour sending to the phone's `100.x` would otherwise get as far as the handshake. Anything else gets a bare `403`.
- It takes its own key, `gateway.remoteAccess.key`, and only that one; the WebView's secret is refused there, and the remote key is refused on `127.0.0.1`. Changing the remote key disconnects the other devices and leaves the phone's own WebView as it was. The network rules narrow who can try; the key is still what grants access.

**It keeps itself up.** Whenever Tailscale re-establishes the VPN, Android destroys every socket bound to the tunnel's addresses, listening ones included, and the addresses come back the same. So the listener checks every 10 seconds that its sockets are still listening, not only that the addresses match, and the first failed `accept()` triggers the rebind at once. If Tailscale itself is down while remote access is on, Jenny asks it to reconnect (the `com.tailscale.ipn.CONNECT_VPN` broadcast). After two attempts that don't bring it back, she also opens the Tailscale app for a moment, once, and only if she can come back to the front afterwards: she has to be on screen, with the floating mascot on and allowed to draw over other apps. Since Android 15, an app that has gone behind may bring itself back only while one of its overlay windows is showing; without the mascot, Tailscale (or its login page) would stay on top of her, so she sends the broadcast alone. If Tailscale is still down 30 seconds after that, it needs you — typically it is logged out, or the VPN isn't allowed — so she stops trying altogether and the panel says so; she tries again once Tailscale comes back or remote access is turned off and on. All this happens even when you turned Tailscale off yourself: to keep it off, turn remote access off.

Two things don't work from another device: the Jenny Apps' external views, whose proxy is bound to `127.0.0.1`, and anything that needs the Android shell (the native bridge simply isn't there, and the UI already degrades for that).

## Smaller boundaries, and where they stop

These are narrower than the four levels, but each one is load-bearing, and each has a limit
that is known and accepted. A change that weakens one of them is a security decision, not a
refactor.

- **Network paths outside the SSRF filter.** Besides the chat completions, the
  `settings.provider.models` command calls `<apiBase>/models` without `validate_url_target`,
  on purpose: `apiBase` is typed by the user and may well be a local or LAN model server. It
  follows no redirects and reads only the list of model names. Any other outbound request from
  a tool must go through `validate_url_target` (`jenny/security/network.py`); never add a bare
  `httpx.get` to a tool.
- **The agent browser's WebView.** Python never sees what a visited page loads, so the check
  lives in Kotlin: `shouldInterceptRequest` for HTTP, a document-start script
  (`res/raw/browser_network_guard.js`) that makes `WebSocket`, `WebSocketStream` and
  `WebTransport` ask the native verdict and disables `RTCPeerConnection`, and a service-worker
  client on the session's own profile. The verdict cache holds at most 256 hosts. On the
  page-side check a name that is not cached waits at most 2 seconds for DNS, and a timeout
  counts as blocked; an HTTP request waits for the system resolver on a WebView worker thread,
  with no cap of its own, and a failed resolution counts as blocked. The address check
  (`isBlockedAddress` in `JennyBrowserBridge.kt`) follows the Python blocklist
  (`jenny/security/network.py`): besides the private, loopback and link-local ranges it refuses
  multicast, broadcast, `::/96`, the site-local `fec0::/10`, SIIT-translated `::ffff:0:0:0/96`
  and local-use NAT64 `64:ff9b:1::/48`, and it reads IPv4-mapped, NAT64 `64:ff9b::/96` and
  6to4 `2002::/16` addresses as the IPv4 they carry.
  Not covered:
  `WebSocket` inside a worker, DNS rebinding between the check and the connection, and WebViews
  too old for document-start scripts or multiple profiles. It stops an ordinary page, not one
  written against it.
- **Jenny Apps.** An app runs in an iframe with `sandbox="allow-scripts"` only, and every
  `/apps/<slug>/**` response carries `Content-Security-Policy: sandbox allow-scripts`, so an app
  page has an opaque origin in any frame. The frame gets a per-app token (an HMAC of the gateway
  secret over the slug) that opens that app's own routes and nothing else; every other route
  and the WebSocket want the full secret. A navigation into the WebUI from another origin is
  refused. The app-server network policy allows LAN and Tailscale addresses (the user names
  them in the manifest), never loopback. An external view is served through a proxy bound to
  `127.0.0.1` that wants a 128-bit capability on its first request. A manifest that declares
  `server.auth` is rejected.
- **The native bridge.** `JennyNativeInfo`, visible to every frame, carries only harmless reads.
  Everything that writes, opens something or returns user data goes through `JennyNativePort`,
  which Chromium injects only into frames of the gateway's origin. The extra check that a
  message comes from the main frame is defence in depth, not a second wall: a same-origin frame
  can reach the parent's port.
- **Opening, sharing and saving files.** The `FileProvider` exposes `workspace/` and the camera
  temporary folder only. `openFile`, `shareFile` and `saveToDownloads` resolve the path on the
  canonical workspace (symlinks included) and refuse `config.json` and its companions (`.bak`,
  the store's temporary files, and the `config.corrupt-<timestamp>.json` copy the loader sets
  aside when the file cannot be read, which holds the same keys), so neither the provider keys
  nor the SSH private key can leave that way.
- **Notifications.** The direct-reply `PendingIntent` is mutable, as Android's direct reply
  requires, so an app granted notification access can send it with text of its own, and that
  text reaches Jenny as yours. Opening the chat from an alert (which also clears the unread
  alerts) needs a 128-bit token only Jenny's own intents carry; any other app starting the
  activity gets an ordinary launch.
- **Telegram pairing.** The pairing code is accepted only from a private chat, and once paired
  a message must come from that same person (`from.id`). Outside an open pairing window the bot
  answers no one but the owner, so it does not reveal that it exists; during the window,
  attempts are capped per chat and the tracked chats are capped too. A group paired by an older
  version stops being answered and has to be paired again from a private chat.
- **Memory across chats.** The Dream pass of a project writes only to `USER.md`, and no prompt
  of a project shows the personal history. The other direction is open by design: your
  identity (`SOUL.md`, `USER.md`) travels into every project, and `recall_history`, called from
  the personal chat, can read the journals of every project, including entries that are never
  shown in any prompt. Personal is not secret from a project, and a project is not secret from
  the personal chat. See [Memory](../using/memory.md).
- **The read-only turn** is an instruction backed by tool refusals, not a containment control:
  code in `python_exec` that starts a raw thread escapes it, together with the workspace path
  policy. Do not describe it, or `python_exec`, as a sandbox.

## Related pages

- [Privacy](./privacy.md) — what leaves the device and when.
- [SSH access](../using/ssh.md) — setting up a remote host, and why a restore does not restore access.
- [Configuration reference](../reference/configuration.md) — the `security.*` and `tools.*` keys in full.
- [Tool reference](../reference/tools.md) — per-tool limits and behavior.
- [Local models](../reference/local-models.md) — self-hosted providers, reachability and the HTTPS guidance.
