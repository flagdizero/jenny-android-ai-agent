# Writing a Tool

A tool is a Python class in `jenny/agent/tools/` that the LLM can call; this page walks through registering one, its anatomy, and what a PR needs before it can merge.

## Where tools live

Every built-in tool is a `Tool` subclass (`jenny/agent/tools/base.py`) living in a module under `jenny/agent/tools/`. There is no plugin directory scanned at runtime and no entry-point discovery: the set of tool modules is a fixed, hardcoded list, and each module explicitly declares which classes it exports as tools.

This is a deliberate fork boundary (see [`FORK_BOUNDARY.md`](../../FORK_BOUNDARY.md)): upstream nanobot used implicit `dir()` reflection to find tool classes, which is order-sensitive and lets a typo silently drop a tool. Jenny replaced that with two explicit lists that fail loudly instead.

## Explicit registration

`jenny/agent/tools/loader.py` defines `_HARDCODED_TOOL_MODULES`, the list of module names under `jenny.agent.tools` that get imported at startup:

```python
_HARDCODED_TOOL_MODULES = [
    "filesystem", "python_exec", "android_web", "download", "location",
    "long_task", "spawn", "cron", "self", "search", "message",
    "apply_patch", "exec_session", "introspect", "diagnostics", "ui_view",
]
```

To add a new tool:

1. Write your `Tool` subclass in a new module (or add it to an existing one if it's closely related, e.g. another filesystem operation).
2. Add a module-level `TOOLS = [YourToolClass, ...]` list at the bottom of the file — every module `ToolLoader` imports **must** declare this, or startup raises `ToolLoadError: Tool module '<name>' does not declare a TOOLS list`. This is the single point the loader reads instead of scanning the module's attributes.
3. If it's a brand-new module (not an addition to an existing one), add its name to `_HARDCODED_TOOL_MODULES` in `loader.py`.

One module can declare more than one tool in its `TOOLS` list — `filesystem.py`, `search.py`, and `exec_session.py` each export several.

### Name collisions abort startup

`ToolLoader.load()` registers each discovered tool by `tool.name`. If two tools resolve to the same name, registration raises and the gateway does not boot:

```
ToolLoadError: Tool name collision: '<name>' from <ClassName> conflicts with an already-registered tool.
```

There is no silent overwrite and no skip-and-log — this used to be a warning that clobbered the earlier registration, which is exactly the kind of bug that's invisible until the wrong tool runs. Pick a name that doesn't already exist in another module. The obvious grep is not enough — most tools declare `name` as a `@property`, so `grep -rn '    name = "'` finds only about a third of them. This catches both shapes:

```bash
grep -rnE '^ +(name = "|def name)' jenny/agent/tools/
```

`ToolLoadError` (a `RuntimeError` subclass, defined in `loader.py`) marks the failures the loader treats as **fatal**, and it is never caught by the loader:

- a name collision;
- a module without a `TOOLS` list;
- a `TOOLS` entry that isn't a `Tool` subclass.

These are programming errors: deterministic, reproducible on every boot, and catchable in a test. Failing loudly is cheaper than shipping a gateway that's quietly missing a tool.

**Tolerated failures** are the other half of the contract, and the distinction is explicit in `load()`. If your `enabled()` or `create()` raises, that depends on the runtime environment (config shape, an Android service that isn't there), and the gateway is the only way the user has to fix it — so that single tool is dropped for the run instead of taking the process down. It is not silent:

- the exception is logged at `ERROR` with its traceback (`Tool <ClassName> disabled: create() raised`), which also lands in the `get_recent_logs` ring buffer;
- one summary `ERROR` line lists everything dropped;
- the failures stay inspectable as `ToolLoadFailure(tool, stage, error)` entries on `ToolLoader.failures`.

After adding a tool, check `loader.failures` (or the startup log) rather than assuming "no crash" means "registered".

Jenny App actions (`<slug>_<action>`, see [Write a mini-app](write-a-mini-app.md)) are re-synced into the same registry every turn and follow a *different* rule: a name colliding with a built-in tool is skipped with a warning rather than raising, so a malformed or malicious app manifest can't crash the gateway or shadow a core tool.

## Anatomy of a tool

`GetLocationTool` (`jenny/agent/tools/location.py`) is a compact, complete example — it shows every piece a tool typically needs:

```python
@tool_parameters(
    tool_parameters_schema(
        precise=BooleanSchema(
            description="Force a fresh GPS fix (turns the radio on, costs battery)...",
            default=False,
        ),
        required=[],
    )
)
class GetLocationTool(Tool):
    _scopes = {"core", "subagent"}
    name = "get_location"
    description = "Get the user's current location (reverse-geocoded place plus latitude/longitude)..."
    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return (
            bool(ctx.android_context)
            and getattr(ctx.config, "location", None) is not None
            and ctx.config.location.enable
        )

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(config=ctx.config.location)

    def __init__(self, config: Any = None):
        self.config = config

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, precise: bool = False, **kwargs: Any) -> str:
        ...
```

Piece by piece:

| Piece | Purpose |
|---|---|
| `name`, `description` | Required abstract properties on `Tool`; `description` is what the LLM reads to decide when to call it — write it for the model, not for a human changelog. |
| `@tool_parameters(...)` | Class decorator (`jenny/agent/tools/base.py`) that attaches a JSON Schema and synthesizes the `parameters` property for you, instead of hand-writing `@property def parameters(self): return {...}`. Build the schema with the helpers in `jenny/agent/tools/schema.py` (`StringSchema`, `BooleanSchema`, `tool_parameters_schema`, etc.). |
| `_scopes` | Which execution contexts register this tool — see below. Defaults to `{"core"}` if omitted. |
| `enabled(ctx)` classmethod | Gate that decides whether this tool registers at all for a given `ToolContext` — return `False` and the tool simply doesn't exist in that run (no error, no stub). Defaults to always `True` on `Tool`. |
| `create(ctx)` classmethod | Builds the tool instance from a `ToolContext` (workspace path, config, bus, subagent manager, cron service, ...). Defaults to `cls()` (no-arg construction) — override it whenever your tool needs config or workspace access. |
| `read_only` property | Defaults to `False`. Set it to `True` when the tool has no side effects — the runner uses this to decide what can run concurrently with other read-only tools (see `concurrency_safe` / `exclusive` on `Tool`). |
| `async def execute(self, **kwargs)` | The actual work. Return a string (or a list of content blocks); raise nothing you don't want surfaced verbatim to the model — tools are expected to catch their own failures and return an `"Error: ..."` string rather than let an exception escape. |

`DownloadFileTool` (`jenny/agent/tools/download.py`) is a second useful reference: it shows a tool that owns an injectable `httpx.AsyncClient` (`__init__(self, workspace, client=None)`) purely so tests can pass an `httpx.MockTransport` instead of hitting the network — a pattern worth copying for any tool that talks to the outside world.

## Scopes: there are four, not two

`_scopes` on the class controls which registries a tool ends up in. **Getting this wrong is the most common way to ship a tool that appears to work and is invisible to the agent the user talks to**, so it is worth reading before you copy a neighbour's value.

- **`"orchestrator"`** — the main agent loop **as it actually runs**. `AgentLoop.tool_scope` returns `"orchestrator"` whenever `agents.defaults.orchestratorMode` is true, which is the default. This is the scope the conversation you are having loads.
- **`"core"`** — the same main loop with `orchestratorMode` set to `false`: the historical, wider scope. It is not the default any more.
- **`"subagent"`** — spawned subagents (`spawn`, `jenny/agent/subagent.py`), which run with a *separate* `ToolRegistry` built via `ToolLoader().load(ctx, registry, scope="subagent")`, narrowed further by the agent type's `allow` list.
- **`"remote"`** — the four SSH tools, and nothing else. **No agent loads it by default**; only the `sysadmin` subagent type asks for it.

So a tool with `_scopes = {"core", "subagent"}` — the common case for the write-side file tools, `python_exec`, search, download, exec sessions and the `browser_*` family — is **not** available to the default orchestrator: the work reaches it by delegation instead. If you want the agent the user talks to to have your tool, `"orchestrator"` has to be in the set.

`Tool._scopes` defaults to `{"core"}` (`jenny/agent/tools/base.py`), which with the shipped default config means **no agent at all** — so leaving it unset is almost never what you want.

Subagents are blind to the main conversation history and get their own scoped `ToolsConfig` — the scoped load is `loader.load(ctx, registry, scope="subagent", allow=…)` in `jenny/agent/subagent.py`. Keep that in mind if your tool's `enabled()`/`create()` assumes the full config shape.

## The config toggle pattern

Tool-specific settings are Pydantic-compat `Base` dataclasses in `jenny/config/tool_schemas.py` — deliberately a *light* module (only imports `Base` + `Field`, no tool code) so `config/schema.py` and the tool modules can both import from it without an import cycle:

```python
class LocationConfig(Base):
    enable: bool = True
    telegram_ttl_s: int = Field(default=3600, ge=60)      # 1 h
    fresh_timeout_s: int = Field(default=15, ge=1, le=60)
```

To wire a new toggle:

1. Add a `<Thing>Config(Base)` class to `tool_schemas.py` with your fields and defaults (use `Field(..., ge=..., le=...)` for anything that needs range validation).
2. Add a field for it on `ToolsConfig` in `jenny/config/schema.py` (`location: LocationConfig = Field(default_factory=LocationConfig)` is the existing example). `ToolContext.config` is set to the app's `ToolsConfig` instance directly (see `AgentLoop._register_default_tools` in `jenny/agent/loop.py`), so inside a tool it's reached as `ctx.config.location`, not `ctx.config.tools.location`; from the top-level `Config` object elsewhere in the codebase it's `config.tools.location`.
3. In your tool, use `enabled(ctx)` to check the toggle (`ctx.config.location.enable`) and `create(ctx)` to hand the config object to the instance. Those two classmethods are the whole binding — there is no separate attribute naming the config section.

`camelCase` aliases (e.g. a JSON key like `freshTimeoutS`) are handled by the `Base`/`Field` layer the same way as the rest of the config — see [Configuration reference](../reference/configuration.md) for how the alias system works; you don't need anything tool-specific for it.

## Testing your tool

Tests mirror the package layout: `jenny/agent/tools/download.py` → `tests/agent/tools/test_download.py`. When you add a tool, add its test file at the matching path under `tests/agent/tools/`.

What existing tests check, worth copying:

- **Unit-test `execute()` directly**, constructing the tool with `Tool.create`-equivalent arguments rather than going through the full `AgentRunner`.
- **Mock outbound I/O.** `test_download.py` builds an `httpx.MockTransport` and monkeypatches `validate_url_target` to bypass DNS/SSRF resolution in tests (`monkeypatch.setattr(download_mod, "validate_url_target", lambda url: (True, None))`) — don't let a tool's test suite make real network calls.
- **Test the registration surface too**, not just `execute()`: `tests/agent/tools/test_tool_loader.py` checks the defaults every `Tool` subclass gets (`enabled(None) is True`, `create(None)` returning an instance) and exercises `ToolContext`'s required fields — useful as a checklist for anything you override.
- **The loader's failure policy is under test** — `tests/agent/tools/test_tool_loader.py` asserts that a collision and a module without `TOOLS` raise `ToolLoadError` out of `load()`, that a failing `enabled()`/`create()` is recorded in `ToolLoader.failures` instead, and that the shipped tool set loads with `failures == []`. If you touch that code path, extend those tests rather than relying on it only failing at gateway startup.

## Before opening a PR

Run the full check from [Code style](code-style.md):

```bash
ruff check jenny/ tests/
npx pyright jenny/bus jenny/command jenny/runtime jenny/session
pytest -q
```

A new tool module isn't in the blocking pyright subset (`jenny/bus`, `jenny/command`, `jenny/runtime`, `jenny/session`) unless you're touching one of those packages directly, but run the full, non-blocking `npx pyright || true` too and don't introduce new errors in `jenny/agent/tools/`.

## See also

- [Tool reference](../reference/tools.md) — every built-in tool, its parameters, and its config toggle.
- [Write a mini-app](write-a-mini-app.md) — the other way to give the agent a new capability, without touching Python.
- [Testing](testing.md) / [Code style](code-style.md) — the checks a tool PR has to pass.
