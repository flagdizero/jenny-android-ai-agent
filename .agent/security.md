# Security Boundaries

The agent operates with significant power (file system, code execution, web). The following guards must not be bypassed when modifying related code.

## Workspace Restriction

Filesystem tools (`read_file`, `write_file`, `edit_file`, `list_dir`, `apply_patch`) resolve paths through the workspace path resolver (`agent/tools/filesystem.py` / `agent/tools/path_utils.py`), which enforces that the resolved path must lie under the active workspace when workspace restriction is enabled. The media upload directory is always an internal extra read root while restricted.

Additional filesystem roots must be capability-specific. `extra_allowed_dirs` is a legacy read-only alias. Use `extra_read_allowed_dirs` for read-only roots, `extra_write_allowed_dirs` only when a write-capable tool is intentionally allowed to modify an extra directory, and exact file allowlists when a tool may modify only specific files.

**Rule**: Any new path-handling logic must go through the workspace path resolver or perform an equivalent containment check with explicit read/write capability semantics.

## The project/diary boundary is a key plus a destination

**Rewritten 08/09/2026** — before that date this section read *"a project's words cannot reach the
personal diary"*, and the gate was `MemoryStore.append_history` returning `0` for a `project:`
session key. That gate is gone. Read `.agent/project-memory-plan.md` before changing anything
here; the short version follows.

The gate guarded the right rule on the wrong axis. The declared line is **"who you are travels;
where else you work does not"** — a rule about the *category of the fact* — and the gate asked
about the *origin of the session*. Outbound the two coincide, because only identity goes out.
Inbound they do not: a fact about the person, said inside a project, **is** identity, which is
exactly the class allowed to travel, and it was stopped anyway. Measured on the device: 39 of 72
lines in the real projects' journals were facts about the person, and 18 of 23 sampled were in no
memory file at all.

So a project session now writes to `history.jsonl` **with its own key**, and the boundary is
carried by two things that must both hold:

- **the key** closes the read side. `read_recent_history_for_prompt` returns nothing at all for a
  project key, and a project entry matches no other branch either — not the personal one, not an
  internal job's, not a gardener pass's. No prompt in the installation can show it. This was
  always true; it is now load-bearing, and there is a test per branch;
- **the destination** closes the write side. `MemoryStore.build_dream_tools(scope="project")`
  hands the run `read_file` plus the entry tool restricted to `USER.md`
  (`MemoryEntryTool(allowed_targets={"user"})`), and nothing else: no `write_file`, no
  `edit_file`, no `apply_patch`. `memory/MEMORY.md` is the cross-project inventory — *where else
  you work* — and it is unreachable from a project batch, as is `SOUL.md`. A batch never mixes
  the two kinds (`build_dream_prompt` takes the leading run of one kind and stops), so the scope
  is never ambiguous.

`agent/dream_project.md` states the same rule in prose for the model. **That prose is not the
guarantee** — the reduced toolbox is. Anyone tempted to widen the toolbox and rely on the prompt
is undoing the boundary, whatever the prompt still says.

**The reverse direction has no structural boundary, and that is deliberate.** `SOUL.md` and
`USER.md` are always composed from the installation root (`ContextBuilder._IDENTITY_FILES`), so a
project's turn — and every internal pass that runs on the installation root, the gardener's
included — carries the user's identity. `MemoryRecallTool` likewise captures the install-root
archive at construction and ignores workspace scope, so a project session can `recall` the personal
archive. The declared line is **who you are travels; where else you work does not**: what is gated
on the session is the *cross-project inventory*, not the identity.

So "the diary is kept personal" must not be read as symmetric. Personal is not secret-from-a-project.

Three narrowings on that gated half, all keyed on the session and all about actors whose write
surface is a single project — each one is why the previous is not the whole rule:

- a `project:` conversation gets no `## Wikis` block (the list of every wiki with its scope,
  rendered from `wikis/` at each build) and no `Recent History` block;
- a **gardener** pass (`gardener:` key) gets neither either (T7.8). Its toolbox reads inside one
  project and writes only in `wikis/<name>/wiki/`, and `agent/gardener.md` tells it to work only
  from the journal, the map and the page inventory. Before this, the pass with no user to talk to
  was shown the personal conversation's queue while the project conversation it maintains was shown
  none of it;
- **neither gets `MEMORY.md`** (24/08). This one *moved* a file across the line rather than gating
  a block that was already on the wrong side of it, so the argument is recorded below.

### `MEMORY.md` is inventory, not identity

Until 24/08 this file sat on the identity side and was injected for every session kind. That was a
classification, never a measurement, and the measurement contradicts it: counted one by one, its
entries each serve **one** project — a server to one wiki, an internal agent to another, the repo to
a third — plus a residue that serves none. That is literally *where else you work*. And a fact that
serves one project already has a home: that project's wiki, or its `AGENTS.md`. Pushing it into
**every** project is a broadcast, and it cost more than the space it took — measured on one project
chat, zero useful entries out of eleven and two harmful ones, of which one was false (a project
declared closed while the conversation was reopening it) and one made the turn invent a connection
the user had never mentioned.

**The line did not move; the file did.** `SOUL.md` and `USER.md` are untouched, so the caution in
`_load_bootstrap_files` — threading session kind through the most shared prompt path, and leaving
the one actor with no identity to write pages the user reads — still holds and is still respected.

**For the gardener there is a second argument that does not depend on that measurement**: its four
read tools are built with `allowed_dir = wikis/<name>` (`GardenerStore.build_tools`), so its own
toolbox *refuses* that path. The prompt was pushing in what the confinement forbids it to open.

A `project:` conversation, which *can* read it, gets a one-line pointer instead
(`MemoryStore.get_memory_pointer_context`): removing the block without one would make the file
unreachable in practice, which is the same defect `get_archive_context` exists to avoid — a file the
model does not know exists is, from where it stands, deleted. `recall` does not cover the gap: it
reads `memory/archive/`, the cold tier, not the live file. The pointer also says the content is not
project material, for the same reason the archive line says nothing is written there: naming the
path in front of `agent/project.md`'s capture rule would otherwise reopen, from the write side, the
boundary the gate closes on the read side. **The gardener gets no pointer** — a path its tools
refuse, plus an invitation to open it, is worse than the absence.

**Rule**: when adding an internal actor whose writable surface is one project, gate
`_get_wikis_context`, `read_recent_history_for_prompt` and the `MEMORY.md` block on it — and, if
it can write memory, give it a `build_dream_tools` scope rather than a paragraph — do not
"fix" the identity path (`_IDENTITY_FILES`) to match, and do not widen the gardener's read root (its
`build_tools` comment, *"Lettura: dentro il progetto. Non l'intera installazione come Dream"*, is a
boundary somebody chose: T4.5 records a proposed fix that would have silently undone it).

**Closed since 25/08, and the residue is narrower.** `agent/identity.md`'s workspace listing named
`memory/MEMORY.md` and `memory/history.jsonl` to every session, the gardener included — two paths its
toolbox refuses. Those two are now absent from a gardener pass's prompt entirely: the listing is
gated (`installation_files`), and the other block that named `MEMORY.md` —
`agent/tool_contract.md`'s `## Which File a Fact Belongs In`, which told the pass that "what was
decided, what is still open" belongs in `MEMORY.md` — no longer reaches it either. That block was
already gated on "am I inside a project folder?", which for an internal turn answers a different
question: its scope is the installation, its writable surface is `wikis/<name>/wiki/`. The gate now
reads the union with the session kind (`context.py`, look for `is_gardener_pass`).

What remains: with a skill installed, the skills index names `skills/<name>/SKILL.md` to the pass,
and its toolbox refuses that too. Same class, one path, not closed — the pass arguably should not
receive the skills index at all, which is a third narrowing and a separate decision.

## SSRF Protection

All outbound HTTP requests from agent tools must pass through `validate_url_target` (`security/network.py`). By default it blocks loopback, RFC1918 private addresses, CGNAT ranges, link-local ranges, and cloud metadata endpoints (including `169.254.169.254`).

The only escape hatch is `configure_ssrf_whitelist(cidrs)`, which reads from `config.security.ssrf_whitelist` at load time (`config/loader.py`); `config.tools.ssrf_whitelist` no longer exists, so writing it is silently dropped.

**La regola dice «dai tool dell'agente», e due percorsi non sono tool.** I
provider LLM chiamano l'endpoint di chat, e la rotta `/api/settings/provider/models`
chiama `<apiBase>/models`, entrambi **senza** `validate_url_target`. È voluto:
`apiBase` è un valore che l'utente ha scritto in Impostazioni per parlare col suo
provider, e su questo dispositivo può essere un `llama.cpp` in loopback o un
server di modelli in LAN — cioè precisamente ciò che l'SSRF blocca. Bloccare la
lista modelli lasciando passare la chat non proteggerebbe da niente e romperebbe
i modelli locali. Quello che delimita la deroga: token sull'`/api/`, nessun
redirect seguito, timeout stretto, e della risposta si legge solo l'elenco dei
nomi.

**Rule**: Do not add direct `httpx.get` / `requests.get` calls in tools. Route through the existing web fetch utilities or replicate the `validate_url_target` check.

### Jenny Apps server SSRF policy (intentionally more permissive)

Jenny App `http` actions use a **distinct, deliberately more permissive** policy, `validate_app_server_target` (`security/network.py`), backed by `_APP_SERVER_BLOCKED_NETWORKS`. Unlike `validate_url_target`, it **allows RFC1918 private ranges** (`10/8`, `172.16/12`, `192.168/16`), IPv6 ULA (`fc00::/7`) **and CGNAT (`100.64.0.0/10`)** **by design**: an app server is a user-declared LAN *or tailnet* device, reachable at a `server.baseUrl` that the user sees and approves in the manifest. Loopback (`127.0.0.0/8`, `::1`), link-local / cloud metadata (`169.254.0.0/16`) and `0.0.0.0/8` remain blocked. Redirects are never followed, so a server cannot bounce the proxy to a blocked address.

**CGNAT was blocked until Sept 2026, and the re-evaluation the Rule below asks for is this.** The old justification — quoted verbatim — was that keeping it blocked meant "an app manifest cannot use the proxy as an authenticated bridge to the gateway's own API". That is the argument for blocking **loopback**, which still is blocked; a CGNAT address does not reach the gateway. The two had been flattened into one sentence, so the range was carrying a reason that was not about it.

Measured consequence of the old state: a Jenny App could never reach the user's own Tailscale server. The only documented escape hatch was `security.ssrfWhitelist`, which is **global** — using it to let one app talk to one server would have opened CGNAT to `web_fetch` and to every target the model picks. That is a strictly worse trade than a narrow permission in the one policy that needs it, and it is the same trade already made for SSH (see below).

The criterion that actually separates the three policies on CGNAT is **who chooses the address**: an SSH host is typed by the user in Settings, a `server.baseUrl` is declared by the user in a manifest they can read, and in `web_fetch` the address is chosen by the model. The first two get CGNAT; the third does not. Fixed by `test_tailscale_allowed_where_the_user_names_the_target_never_where_the_model_does` (`tests/security/test_ssrf_extended.py`), which asserts all three with an **empty** whitelist — so a future regression to the global-whitelist shortcut fails the test.

`server.auth` is **fail-closed twice over**: `_parse_manifest` (`apps/manifest.py`) rejects a manifest declaring it, so the app loads as broken with the remedy named, and `execute_http_action` (`apps/http.py`) still refuses with 501 if such a manifest reaches it by another route. Before Sept 2026 only the 501 existed, and the app-creator skill actively *instructed* writing `"auth": {"secretRef": ...}` — so an app could validate clean and ship with every http action dead. That advice is withdrawn in `skills/app-creator/SKILL.md`.

**Rule**: Keep the three policies separate, and argue any widening from *who names the target*, not from how a range feels. Letting the app-server policy follow redirects, or routing general agent web fetches through `validate_app_server_target`, both still require a fresh threat-model pass.

### Jenny App external-view proxy (loopback listener)

An app declaring `view: {"kind": "external"}` gets its screen from its own server instead of
`app/index.html`, served through `apps/proxy.py::AppViewProxy`: an `asyncio` listener on
`127.0.0.1:<ephemeral>` that forwards to `server.baseUrl` at byte level. It exists because the
APK's `network_security_config.xml` permits cleartext only to loopback, so a WebView framing a
non-loopback `http://` gets `ERR_CLEARTEXT_NOT_PERMITTED` before a socket opens — going through
loopback sidesteps that without widening the policy or baking a personal hostname into the APK.

What bounds the surface:

- **Bind is `127.0.0.1` only**, never an external interface (fixed by `test_binds_only_on_loopback`).
- **One fixed upstream**, validated once at `start()` with `validate_app_server_target`. The proxy
  is not steerable to another address, and `test_start_actually_consults_the_ssrf_gate` exists so
  that stubbing the gate in the transport tests cannot hide its removal.
- **Capability**: the first navigation must present a 128-bit secret in the path; the proxy 302s
  and moves it into a cookie, so the remote page's root-absolute paths keep working. No cookie and
  no prefix → 403. This is load-bearing, because `127.0.0.1:<port>` is reachable by **any** app on
  the phone and an ephemeral port is scannable in seconds.
  - Known wart, deliberately accepted: cookies are per-host and **ignore the port**, so the
    browser sends it to anything on `127.0.0.1`. In practice that is this proxy and the gateway
    (both ours) — and it is why `_rebuild_head` **strips** the cookie from the forwarded request:
    the user's own server must not see it either.
- **Lifetime**: closed by `.../view/close` when the UI closes the view, with an idle timeout as
  the backstop. The listeners live in the gateway process and die with it; there is no container
  shutdown hook.
- Redirects are not resolved by the proxy — they are streamed to the browser, which resolves them
  against the proxy origin, so a redirect cannot move the tunnel to another host.

**The sandbox is wider here, and the reason is the origin, not trust.** A normal app is served
from the *gateway's* origin, so `allow-same-origin` would hand it the SPA's DOM, `localStorage`
and the gateway API with the token — hence `sandbox="allow-scripts"` and nothing else. An external
view sits on `http://127.0.0.1:<ephemeral>`: different port → **different origin**, so
`allow-same-origin` returns only the proxy's own origin and the same-origin policy still keeps it
out of the SPA. Without it the page is effectively dead (opaque origin: no cookies, `localStorage`
throws, its own fetches carry `Origin: null`). This is also why an external view gets its own
overlay rather than being nested inside the app frame: **sandbox flags are inherited by nested
browsing contexts**, so nesting would restore the opaque origin.

**Rule**: The capability check and the loopback bind are the two things holding this up — do not
"simplify" either. Serving an external view from the gateway's own origin, or adding
`allow-same-origin` to the normal app frame, both defeat the isolation entirely.

### SSH target policy (a third one, wider still)

`validate_ssh_target` (`security/network.py`), backed by `_SSH_BLOCKED_NETWORKS`, allows RFC1918, IPv6 ULA **and** CGNAT (`100.64.0.0/10`), blocking only `0.0.0.0/8`, loopback and link-local/metadata. CGNAT is allowed here rather than through `configure_ssrf_whitelist` on purpose: the whitelist is global, so opening it for Tailscale would also open CGNAT to `web_fetch`, where the model picks the address — a narrow permission in each policy that needs it beats a wide one across all three. (The Jenny Apps policy now allows CGNAT on the same grounds, on its own; it did not until Sept 2026.) What backs the extra room is that an SSH host is user-typed in Settings and host-key pinned before any connection, not that SSH is inherently safer.

**Rule**: Loopback stays blocked in all three policies — it is the phone itself, and the gateway's own API lives there.

## Telegram pairing oracle

The Telegram bot follows a **no-oracle rule** (`channels/telegram.py`): outside a pairing
window — no `pairing_code` set, or already paired — the bot NEVER replies to non-owner
chats, so it does not reveal that it exists or its pairing state.

**Accepted trade-off** for onboarding: while a `pairing_code` is active (from token save
until a successful pairing), the bot answers service replies (`/start` prompt, wrong-code
feedback) up to `_MAX_PAIR_ATTEMPTS` per chat, with the attempt table bounded fail-closed
at `_MAX_TRACKED_CHATS` (no eviction). A chat at/over the cap — or a new chat when the
table is full — becomes **ineligible to pair even with the correct code**: the cap is a
brute-force defence on the 6-digit code (total guess budget ≈ cap × bound out of 10^6 per
channel lifetime), not just a reply throttle.

Known limits, accepted by design: the pairing window is not time-bounded (a code persists
after unpair until re-paired), and in-memory counters reset on channel reload/gateway
restart with the same persisted code — the reload paths that matter (token save, unpair)
regenerate the code anyway. Owner lockout recovers via WebUI "Unpair"/"Change token".

**Rule**: any new reply on the unpaired path MUST go through the attempt counter and MUST
NOT fire when `pairing_code` is unset. Never reply to non-owner chats once paired.

## Code Execution

`PythonExecTool` (`agent/tools/python_exec.py`) is the current execution surface. It runs arbitrary Python **in-process** on the single Chaquopy interpreter (in the executor threadpool / dedicated session threads) — **not** in a subprocess, and it is **not a security sandbox**. This matches the honest trust-boundary docstring in `python_exec.py`. The real containment comes from three layers outside the interpreter:

- the **Android app sandbox** (the app's own uid / permissions);
- the **workspace path policy** for filesystem writes — now enforced for the builtin `open` / `io.open` / `pathlib` I/O paths too, not just the registered helpers and `os.open` (`security/workspace_policy.py`);
- the **SSRF policy** for outbound network.

The module allow/block lists are a **usability guardrail** (they stop the model from accidentally reaching for e.g. `subprocess`), **not** a containment control. In particular, `httpx` is **no longer in the default allowlist** (`config/tool_schemas.py`): outbound network is available only through the `http_get` / `http_post` builtins, which validate targets via the SSRF policy. Raw `httpx` can be re-added explicitly in config, accepting the risk.

Deployments that do not trust the model must disable the tool via `tools.python_exec.enable = false` — that is the real answer to "no sandbox", not in-process hardening.

### The read-only turn is on the same side of that boundary

The read-only switch (`WorkspaceScope.writable = False`) is enforced inside `python_exec` by `_refuse_write_if_readonly`, and its gate is thread-local: it fires only on a thread the guard entered. Thread hops made through `asyncio` — `asyncio.to_thread`, `loop.run_in_executor` — carry the whole turn across (`_carry_turn_across_thread`, T4.13: both the path boundary *and* the read-only ContextVar). A **raw thread** reached through an allowed module's internals (`asyncio.base_events.threading.Thread`, `asyncio.futures.concurrent.futures.ThreadPoolExecutor`) carries neither, so it bypasses the path boundary **and** the read-only turn alike — measured 23/08/2026, `restrict_to_workspace` on and off: a raw thread's `open(p, 'w')` writes during a read-only turn. This is an accepted limit (closing it means patching `threading.Thread` process-wide); see `TestKnownRemainingDoors` and the trust-boundary comment in `python_exec.py`.

So read-only is an **instruction backed by tool refusals**, not a containment control, and the two halves fail together rather than one at a time. The prompt block the model reads (`templates/agent/readonly.md`) is written to state intent for exactly this reason: a prompt sentence that promised more than the code keeps would be worse than one that states the intent, because the reader is the model.

**Rule**: Do not describe `python_exec` as a sandbox, and do not describe the read-only turn as one either. Do not introduce shell execution or command wrappers. Any new path-handling or network path added to the execution surface must go through the workspace path policy and the SSRF policy, since those layers (not the interpreter) are the containment boundary.
