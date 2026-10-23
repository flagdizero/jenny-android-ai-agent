# Testing

How to run Jenny's test suite, lint, and type checks locally, and what CI enforces on every pull request.

## Running the tests

```bash
pytest -q
```

The suite uses `pytest-asyncio` in **auto mode** (`asyncio_mode = "auto"` in `pyproject.toml`'s `[tool.pytest.ini_options]`) — async test functions run without needing an explicit `@pytest.mark.asyncio` decorator on each one. `testpaths` is set to `tests/`, so a bare `pytest` from the repo root already scopes correctly.

Tests mirror the `jenny/` package structure directory-for-directory: `tests/agent/` covers `jenny/agent/`, `tests/webui/` covers `jenny/webui/`, `tests/config/` covers `jenny/config/`, and so on. When you add a module under `jenny/`, its tests belong in the matching relative path under `tests/`, not wherever is convenient. The one exception is `jenny/pydantic_compat/`, which is tested by the single file `tests/test_pydantic_compat.py`.

### Running a subset

Scoping to one directory is the fast inner loop while working on a single subsystem, for example:

```bash
pytest tests/config/ -v
```

### Coverage

CI runs plain `pytest -q`. Coverage was **removed** from it, and `pytest-cov` is not even installed there: the `python_exec` sandbox intercepts every path operation for the duration of an `execute` and refuses the ones outside the workspace, the coverage tracer's own path reads fall inside that window, and the tests asserting a clean record fail. Widening the sandbox's exemption to accommodate the tracer would weaken a security boundary on a platform we do not ship, so the measurement went instead — it had kept CI red for three releases. `[tool.coverage.*]` stays in `pyproject.toml` for a local `pytest -q --cov=jenny`, where you can see those failures and know to ignore them.

## Linting

```bash
ruff check jenny/ tests/
```

Configured in `pyproject.toml`'s `[tool.ruff]` / `[tool.ruff.lint]`: line length 100, target `py311`, rule set `E, F, I, N, W` selected, with `E501` (line-too-long) explicitly ignored — line length is a soft target, not an enforced one.

## Type checking

Jenny uses `pyright` in `basic` mode (config in `pyrightconfig.json`, `include: ["jenny"]`, excluding `jenny/templates` and `jenny/skills`). It's a static check only — zero runtime impact — but it runs in two tiers with different consequences:

```bash
# BLOCKING subset — must stay green, this is what CI fails the build on:
npx pyright jenny/bus jenny/command jenny/plan jenny/runtime jenny/session jenny/snapshot jenny/gateway_runtime.py

# Full-perimeter visibility — informational, does not fail the build:
npx pyright || true
```

The blocking subset (`jenny/bus`, `jenny/command`, `jenny/runtime`, `jenny/session`, `jenny/snapshot`, plus the single module `jenny/gateway_runtime.py`) is already error-clean today and is expected to stay that way — a PR that reintroduces a type error there fails CI. Running `pyright` against the whole `jenny/` perimeter (the second command) surfaces residual errors elsewhere in the codebase without blocking anything; that's the honest state of a codebase being tightened incrementally rather than one pretending to be fully typed already. `reportMissingImports` is disabled project-wide since Android/Chaquopy-only dependencies aren't installed in a plain CI environment, and `jenny/pydantic_compat` (the homemade, stdlib-only `BaseModel` reimplementation — see `FORK_BOUNDARY.md`) gets relaxed `reportGeneralTypeIssues`/`reportAttributeAccessIssue` settings since it leans on dynamic metaprogramming that pyright can't fully follow.

## The full CI-equivalent check

One command, straight from `AGENTS.md`, runs everything CI runs before a PR is considered:

```bash
ruff check jenny/ tests/ && npx pyright jenny/bus jenny/command jenny/plan jenny/runtime jenny/session jenny/snapshot jenny/gateway_runtime.py && pytest -q
```

Run this before committing or opening a PR. It's exactly the lint + blocking-type-check + test sequence, without the non-blocking full-perimeter pyright pass (visibility-only in CI, not a gate).

## What CI actually runs

`.github/workflows/ci.yml` runs on every PR to `main` and on every push to any branch, with three jobs:

| Job | What it does |
|---|---|
| `dco` | Verifies every commit carries a `Signed-off-by:` trailer matching its author (`scripts/check_dco.sh`, which reads git's own trailer block, not any line of the message). On a PR it checks the PR's commits; on a push to a branch other than `main`, the commits the branch adds on top of `main`. See [`CONTRIBUTING.md`](../../CONTRIBUTING.md) for the sign-off requirement — a PR with unsigned commits cannot merge. |
| `lint` | `ruff check jenny/ tests/`, then the blocking pyright subset, then the non-blocking full-perimeter pyright pass (`\|\| true`). |
| `test` | `pytest -q`, run twice as a matrix across Python 3.11 and 3.12. The `dev` extra (`pip install -e ".[dev]"`) brings pytest, pytest-asyncio and ruff at exact versions, plus the test-only backends `cryptography`, `asyncssh` and `pillow` (never installed on Android). It also installs node and `jsdom` (`npm install --no-save jsdom@30.1.1`, found through `NODE_PATH`): the WebUI suites that execute the real JS skip without them, and `tests/webui/test_node_is_available.py` fails instead of skipping when `CI` is set and node, jsdom or Pillow is missing. |

To reproduce the `test` job locally, including the jsdom suites:

```bash
pip install -e ".[dev]"
npm install --no-save jsdom@30.1.1
NODE_PATH=$PWD/node_modules pytest -q
```

`node_modules/` is not in `.gitignore`: delete it afterwards rather than committing it.

CI validates the Android code path on a plain host runner (installing `jenny` with `pip install -e ".[dev]"`) rather than inside an actual Android build — Android is the only supported runtime target for the shipped app, but the Python side of the codebase is what CI exercises directly.

## Language and style conventions that tests should also follow

- Docstrings and comments in **Italian** for new code; code inherited from upstream keeps its existing English — don't translate text that's already there.
- Identifiers, log messages, and commit-facing strings (commit messages, PR descriptions) stay in **English** regardless of the above.
- User-facing WebUI strings are localized via the i18n JSON files (`jenny/templates/ui/assets/i18n/{it,en}.json`) — never hardcode UI copy directly in JS/HTML.

See [Code style](code-style.md) for the complete set of conventions beyond testing.

## See also

- [Build from source](build-from-source.md) — getting a device build running
- [Development](development.md) — repository layout and where subsystems live
- [Code style](code-style.md) — the full style guide
- [Write a tool](write-a-tool.md) / [Write a mini-app](write-a-mini-app.md) — adding functionality, with its own test expectations
