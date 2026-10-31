# Security Policy

Jenny is a personal AI agent that runs on your own Android device with your own
API key. It has real power on that device: it reads and writes files, executes
Python, fetches from the network, and can act on a schedule without you
watching. This document describes the actual trust boundaries — not an
aspirational list — so you can decide what you are comfortable running.

> Jenny is a fork of [nanobot](https://github.com/HKUDS/nanobot). This policy
> covers the fork.

## Reporting a vulnerability

**Do not open a public issue for a security bug.**

Use GitHub's private vulnerability reporting on this repository
(*Security → Report a vulnerability*). Include what you did, what happened,
what you expected, and the impact you think it has. A proof of concept helps a
lot.

We aim to acknowledge reports within 72 hours. Jenny is maintained by one
person, so please allow reasonable time for a fix before disclosing.

## Supported versions

Security fixes go into the latest 2.x release only. Releases before 1.0 get none: the update
check inside the app offers the newest release, and that is the way to get a fix.

## Trust model

Two sentences that matter more than the rest of this file:

1. **The model is inside the trust boundary, the device sandbox is the boundary.**
   Jenny does not defend against the model it is running. It defends the
   *device* against the agent's mistakes, and defends the agent against
   *hostile input from the network*.
2. **`python_exec` is not a sandbox.** See below.

If you run a model you do not trust, or point Jenny at untrusted content with
tools enabled, assume the agent can do anything your Android app UID can do.

## Code execution: `python_exec` is not a sandbox

`python_exec` runs arbitrary Python **in-process**, on the same Chaquopy
interpreter as the gateway. Not a subprocess. Not a container. There is no
interpreter-level containment, and the module allow/block lists are a
**usability guardrail** — they stop the model from casually reaching for
`subprocess`, they do not stop a determined attempt.

The real containment lives outside the interpreter:

- the **Android app sandbox** (the app's own UID and permissions);
- the **workspace path policy** for filesystem writes, enforced for the builtin
  `open` / `io.open` / `pathlib` paths as well as the registered helpers and
  `os.open` (`jenny/security/workspace_policy.py`);
- the **SSRF policy** for outbound network.

`httpx` is deliberately **not** in the default allowlist: outbound network from
executed code goes through the `http_get` / `http_post` builtins, which validate
targets. Raw `httpx` can be re-enabled in config, accepting the risk.

**If you do not trust the model, disable the tool:** `tools.python_exec.enable = false`.
That is the honest answer to "there is no sandbox" — in-process hardening is not.

## Filesystem access

Filesystem tools (`read_file`, `write_file`, `edit_file`, `list_dir`,
`apply_patch`) resolve paths through the workspace path resolver, which enforces
containment under the active workspace when `restrict_to_workspace` is enabled.

Extra roots are capability-specific by design, and they are not user
configuration: they are constructor arguments of the filesystem tool classes
(`extra_read_allowed_dirs` for read-only roots, `extra_write_allowed_dirs` only
where a write-capable tool is intentionally allowed, and exact-file allowlists
where only specific files may change), set in code where a tool is built. The
only user-facing switch is `restrict_to_workspace`: keep it on unless you have a
concrete reason.

## Network access (SSRF)

Outbound requests from agent tools pass through `validate_url_target`
(`jenny/security/network.py`), which blocks loopback, RFC1918 private ranges,
CGNAT, link-local, and cloud metadata endpoints including `169.254.169.254`.
The only escape hatch is `security.ssrf_whitelist`.

**Jenny Apps use a second, deliberately more permissive policy.** An app's
`http` action goes through `validate_app_server_target`, which *allows* RFC1918,
IPv6 ULA **and** CGNAT (`100.64.0.0/10`, the range Tailscale assigns) on purpose:
an app server is a LAN or tailnet device the user declared and approved in the
manifest. Loopback, link-local/metadata and `0.0.0.0/8` stay blocked, so an app
manifest cannot use the proxy as a bridge to the gateway's own API, and
redirects are never followed.

## App updates and self-install

Jenny checks for a new release **every 24 hours by default** (`updates.enabled`,
`updates.checkIntervalH`) and can install one on request. This is the one
outbound connection the user did not switch on, so it belongs in this list.

- **What goes out**: a plain `GET` of the release's `latest.json`. No
  identifier, no version, no headers of Jenny's own, no query string. Redirects
  are validated per hop like every other outbound path, the response is
  size-capped, and the version comparison happens on the device. Nothing about
  the user or the device is transmitted.
- **What GitHub can infer** is what any HTTP server infers from a request: an IP
  address and a timestamp. A daily request from the same address is a weak
  signal that the app is installed. `updates.enabled: false` stops the periodic
  check; the **Check now** button in Settings makes a single request when the
  user presses it, whatever that switch says.
- **Installing** needs three manifest permissions — `REQUEST_INSTALL_PACKAGES`,
  `UPDATE_PACKAGES_WITHOUT_USER_ACTION`, `REQUEST_DELETE_PACKAGES`. None of them
  bypasses the user: the per-app "Install unknown apps" switch is still yours to
  grant, `UPDATE_PACKAGES_WITHOUT_USER_ACTION` only covers packages Jenny is
  already the installer of (itself), and on Android 14+ the system answers with
  its own confirmation prompt as the normal path. `QUERY_ALL_PACKAGES` and
  `INSTALL_PACKAGES` are deliberately **not** declared.
- The agent can call `install_update` itself. It is destructive by nature —
  Android replaces the app and kills the process mid-turn — which is why the
  tool's own description says so and the turn cannot report back.

## SSH

The SSH tools are in a scope of their own (`remote`) that **no agent loads by
default**; only the `sysadmin` subagent type asks for it. Host targets go
through `validate_ssh_target`, a third policy distinct from the two above: it
allows RFC1918, IPv6 ULA **and** CGNAT (`100.64.0.0/10`), blocking only
`0.0.0.0/8`, loopback and link-local/metadata. Jenny Apps' server policy opens
CGNAT the same way. It is opened in these two policies rather than through
`security.ssrf_whitelist` on purpose — the whitelist is global, so widening it
for a Tailscale host would also open CGNAT to `web_fetch` and the other tools
whose targets the model picks. What backs the extra room is not that SSH is safer: it
is that an SSH host is typed by the user in Settings and host-key pinned before
any connection.

## WebUI

The SPA is served from the gateway to a local WebView. Defenses, in order of
what they stop:

- **Content-Security-Policy** enforced on the shell documents (`index.html`,
  `workshop.html` and `onboarding.html`):
  `default-src 'self'; script-src 'self'; object-src 'none'; base-uri 'none'`,
  plus `style-src 'self' 'unsafe-inline'`, `img-src 'self' data: blob:`,
  `font-src 'self'`, `connect-src 'self'` (which covers the WebSocket to the
  same host and port, and nothing else) and `frame-src 'self' http://127.0.0.1:*`
  (for a Jenny App's external view, served by the local proxy on an ephemeral
  port). No inline scripts.
- **Model output is sanitized** with DOMPurify before it reaches `innerHTML`,
  and it **fails safe**: if the sanitizer did not load, the markdown is rendered
  as escaped plain text rather than injected as HTML.
- **Jenny Apps run in `sandbox="allow-scripts"` iframes with an opaque origin.**
  An app cannot read the SPA's DOM, its storage, or its API token; it talks to
  the gateway only through the SDK's `postMessage` bridge.

## API and WebSocket authentication

The HTTP API and WebSocket are **token-gated, and loopback is not treated as
trustworthy**. This is deliberate: Android does not isolate loopback TCP
sockets between apps, so any other app on the device could otherwise mint a
fully privileged API token.

A per-install `websocket.token_issue_secret` is generated on first run and
persisted in `workspace/config.json` (private to the app's UID). The gateway
binds `127.0.0.1` by default. If you bind `0.0.0.0`, a token issue secret is
required — the configuration refuses to validate without one, so the gateway
never starts unauthenticated on all interfaces.

## Telegram channel

The paired Telegram bot follows a **no-oracle rule**: outside a pairing window
it never replies to non-owner chats, so it does not reveal that it exists or
what its pairing state is.

While a pairing code is active, service replies are capped per chat and the
attempt table is bounded fail-closed. The cap is a brute-force defence on the
6-digit code, not just a reply throttle: a chat at the cap becomes ineligible to
pair even with the correct code. Known accepted limits: the pairing window is
not time-bounded, and in-memory counters reset on gateway restart with the same
persisted code.

## Data at rest

- **API keys are stored in plain text** in `workspace/config.json`, protected by
  the Android app sandbox and file permissions. There is no keystore-backed
  encryption for provider keys.
- **Conversations, memory, wiki and app data live in the app's private
  storage** on the device.
- **Android Auto Backup is enabled** (`allowBackup="true"`). This is a
  deliberate choice so that users do not lose their agent's memory when they
  change device — but it means that, if you have Google backup turned on, the
  workspace *including `config.json` with your API keys* is copied to your
  Google account's backup. If you do not want that, turn off backup for this app
  in Android settings, or use the app's own encrypted backup/restore instead.
- **Logs may contain sensitive content.** Redaction is applied to known secret
  fields, not to arbitrary conversation text.
- **Your LLM provider sees your prompts.** That is the one unavoidable outbound
  flow; review your provider's privacy policy. Jenny sends no telemetry of its
  own (the update check described above only fetches a public file). When
  using OpenRouter, Jenny sends the `HTTP-Referer`, `X-OpenRouter-Title` and
  `X-OpenRouter-Categories` attribution headers, which identify the app (not you)
  to that provider.

## Operational advice

- Set spending limits on your provider account. An agent in a loop costs money.
- Keep `restrict_to_workspace` enabled.
- Do not grant broad Android storage permissions.
- Review what the agent did: the transcript records tool calls.
- Treat content fetched from the web as hostile input, not as instructions. Be
  especially careful about asking Jenny to "read this URL and do what it says" —
  that is handing an untrusted party the agent's tools.

## Known limitations

Stated plainly, because they are design trade-offs rather than oversights:

1. **`python_exec` has no interpreter-level sandbox** (see above).
2. **Provider API keys are stored in plain text**, protected only by the app
   sandbox.
3. **No rate limiting on inbound messages.** A local attacker with a valid
   token can drive the agent as fast as the provider allows.
4. **Prompt injection is not solved.** No agent's is. Tool-level guards
   (workspace containment, SSRF) limit the blast radius; they do not prevent a
   model from being talked into a bad call.
5. **No audit trail beyond the transcript and logs.** There is no tamper-evident
   security event log.
6. **Auto Backup carries the workspace off-device** when Google backup is on
   (see *Data at rest*).

## What we do not claim

Jenny is not a multi-tenant service, not a hardened sandbox for untrusted code,
and not audited by a third party. It is a personal agent for your own device,
built so that you can read the source and decide for yourself — which is why
the source is public.
