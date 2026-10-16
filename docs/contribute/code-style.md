# Code Style

The conventions a Jenny pull request is expected to follow — tooling settings, the two-tier type check, and the language rules that decide what gets written in Italian and what stays in English.

## Language and runtime

| Item | Value | Where |
|---|---|---|
| Minimum Python | 3.11 | `requires-python = ">=3.11"` in `pyproject.toml` |
| Ruff target | `py311` | `[tool.ruff]` |
| Pyright target | 3.11 | `pythonVersion` in `pyrightconfig.json` |
| CI test matrix | 3.11 and 3.12 | `.github/workflows/ci.yml` |

The codebase is **asyncio end-to-end**: the message bus, the agent loop, the providers, the tools, and the WebSocket/HTTP layer are all coroutines. New code in those paths is `async def` by default, and anything genuinely blocking (filesystem scans, subprocess work, native Android calls) belongs off the event loop rather than inline in a handler. Logging goes through `loguru`'s `logger` with brace-style placeholders (`logger.warning("Skipped {} corrupt line(s) in {}", n, path)`), not f-strings.

## Formatting and linting

```bash
ruff check jenny/ jenny_extensions/ tests/
```

| Setting | Value |
|---|---|
| `line-length` | 100 |
| `select` | `E`, `F`, `I`, `N`, `W` |
| `ignore` | `E501` |

`I` means imports are sorted and grouped by ruff — an unsorted import block fails CI. `N` enforces PEP 8 naming. `E501` (line too long) is deliberately ignored, so **100 columns is the target, not a hard gate**: keep to it for readability, but don't mangle a URL, a long string constant, or a call signature to fit. There is no auto-formatter step in CI (`ruff format` is not run), so match the layout of the file you're editing instead of reflowing it.

## Type checking

Pyright runs in `basic` mode (`pyrightconfig.json`, `include: ["jenny"]`, excluding `jenny/templates` and `jenny/skills`). It is a static check with zero runtime impact, and it runs in two tiers with very different consequences:

```bash
# BLOCKING subset — must stay error-clean:
npx pyright jenny/bus jenny/command jenny/runtime jenny/session jenny/snapshot jenny/gateway_runtime.py jenny/extensions.py jenny_extensions

# Full perimeter — informational, never fails the build:
npx pyright || true
```

`jenny/bus`, `jenny/command`, `jenny/runtime`, `jenny/session`, `jenny/snapshot` and `jenny/gateway_runtime.py` are at zero errors today and CI fails if a PR reintroduces one. The full-perimeter run surfaces the residual errors elsewhere, which are being tightened directory by directory rather than declared clean prematurely — don't add to that pile, but don't be surprised by it either.

Two config carve-outs are worth knowing:

- `reportMissingImports` is off project-wide, because the Android/Chaquopy-only dependencies aren't installed in a plain environment.
- `jenny/pydantic_compat` gets relaxed `reportGeneralTypeIssues` / `reportAttributeAccessIssue`; it's the homemade stdlib-only `BaseModel` (see [`FORK_BOUNDARY.md`](../../FORK_BOUNDARY.md)) and leans on metaprogramming pyright can't follow.

CI pins the checker version (`npx --yes pyright@1.1.411`); a bare `npx pyright` locally may pick up a newer release and report slightly different results.

## Tests

`pytest` runs with `asyncio_mode = "auto"` and `testpaths = ["tests"]`, so async tests need no `@pytest.mark.asyncio` decorator and a bare `pytest` from the repo root is already scoped correctly.

**Tests mirror the `jenny/` package structure directory for directory.** `jenny/agent/tools/download.py` is tested by `tests/agent/tools/test_download.py`; `jenny/apps/storage.py` by `tests/apps/test_storage.py`. A new module goes with a test file at the matching relative path, not wherever is convenient. The one exception is `jenny/pydantic_compat/`, which is tested by the single file `tests/test_pydantic_compat.py`. See [Testing](./testing.md) for what CI runs and how to scope a subset.

## The full check before a PR

```bash
ruff check jenny/ jenny_extensions/ tests/ && npx pyright jenny/bus jenny/command jenny/runtime jenny/session jenny/snapshot jenny/gateway_runtime.py jenny/extensions.py jenny_extensions && pytest -q
```

Lint, blocking type check, tests — the same sequence CI gates on. Commits also need a DCO `Signed-off-by:` line; see [`CONTRIBUTING.md`](../../CONTRIBUTING.md).

## Which language to write in

Jenny's codebase is bilingual on purpose, and the split is by *audience*, not by file:

| What | Language | Note |
|---|---|---|
| Docstrings and comments in **new** code | Italian | The maintainer reads them; they explain intent, not syntax. |
| Docstrings and comments **inherited from upstream** | English — leave as is | Never translate existing text. A diff that renders an untouched English comment into Italian is noise and will be asked to be reverted. |
| Identifiers (modules, classes, functions, variables) | English | Always, in both cases above. |
| Log messages and exception strings | English | They end up in bug reports and device logs. |
| Commit messages, PR titles and descriptions | English | Commit-facing text is public. |
| Tool `name` / `description` fields | English | The LLM reads them; see [Write a tool](./write-a-tool.md). |
| User-facing WebUI strings | Neither — localized | See below. |

### WebUI strings are never hardcoded

Every string a user sees in the SPA lives in `jenny/templates/ui/assets/i18n/it.json` and `en.json`, and is read through the shared helper:

```js
import { i18n } from './shared/i18n.js';
button.textContent = i18n.t('dialog.confirm');
```

Adding a string means adding the key to **both** JSON files. A literal in JS or HTML is a bug even when the app happens to be running in that language.

### New WebUI files must be declared

The Android build ships UI assets from an explicit manifest, not a directory walk: a new file under `jenny/templates/ui/` has to be added to `_UI_MANIFEST` in `jenny/utils/android_assets.py` (the sibling lists `_TEMPLATES_MANIFEST` and `_SKILLS_MANIFEST` do the same job for prompt templates and skills). Miss it and the file simply won't exist on the device — the desktop gateway will look fine while the APK breaks. Registering a package with no manifest at all raises `ValueError: no static manifest registered for package ...` rather than falling back to a silent walk.

## Where the rules live

`AGENTS.md` at the repo root is the machine-readable source for all of the above and is what AI coding agents load automatically; this page is the same content written for people. If you change a convention, change it there too.

## See also

- [Testing](./testing.md) — running the suite, coverage, what each CI job does
- [Build from source](./build-from-source.md) — getting a device build running
- [Write a tool](./write-a-tool.md) / [Write a mini-app](./write-a-mini-app.md) — adding functionality
- [Add a provider](./add-a-provider.md) — supporting a new LLM wire format
