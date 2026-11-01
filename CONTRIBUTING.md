# Contributing to Jenny

Thanks for your interest in contributing! Jenny is young and moving fast, so
a few simple rules keep things sane for everyone.

## Before you start

- **Bugs**: open an issue with steps to reproduce, your device/Android
  version, and the AI provider you are using.
- **Features**: check the existing issues first. For anything non-trivial,
  open an issue to discuss it *before*
  writing code — it saves you from building something that can't be merged.
- **Small fixes** (typos, docs, obvious bugs): just send the PR.

## Branches

Pull requests target **`main`**, from a short-lived branch of your own; releases are cut from
`main`. The code builds
two Gradle flavors: `foss` (GitHub and F-Droid, no Google library) and `play` (the Play Store,
which adds the encrypted copy on Google Drive). Code only `play` needs lives outside the core —
`jenny_extensions/` for Python and its WebUI files, `android/app/src/play/` for Kotlin — so the
`foss` build contains none of it. See [Building from source](docs/contribute/build-from-source.md#two-flavors).

## Developer Certificate of Origin (DCO)

All contributions require a DCO sign-off. This is a simple statement that you
have the right to submit the code you are contributing, under the
[Developer Certificate of Origin](https://developercertificate.org/).

To sign off, add the `-s` flag when committing:

```
git commit -s -m "Fix reminder scheduling on Android 15"
```

This appends a line like:

```
Signed-off-by: Your Name <your@email.example>
```

The sign-off has to be a real trailer (in the last paragraph of the message)
matching the commit's own author. Pull requests with unsigned commits fail the
`dco` job in CI (`scripts/check_dco.sh`, run by
[`.github/workflows/ci.yml`](.github/workflows/ci.yml)) and cannot be merged;
the same job also runs on every push to a branch, so you find out before the PR.

You can run the same check yourself before pushing:

```
scripts/check_dco.sh origin/main HEAD
```

If you forgot, fix the last commit with `git commit --amend -s` (or
`git rebase --signoff origin/main` for several commits) and force-push your
branch.

## Licensing of contributions

By contributing, you agree that your contributions are licensed under the
project's license, **AGPL-3.0** (see [LICENSE](LICENSE)). Note that the
project name and logo are covered by a separate
[trademark policy](TRADEMARK.md).

## Code guidelines

- Keep PRs focused: one change per PR.
- Match the existing code style of the file you are touching.
- If your change affects security boundaries (workspace sandbox, network
  guards, credential handling), call it out explicitly in the PR description.
  The boundaries, and the limits already accepted, are described in
  [Security model](docs/internals/security-model.md).
- Test on a real device when possible, and say which one.
- If you change a prompt under `jenny/templates/agent/`, a skill, or a tool
  description, run [`scripts/behaviour_check.py`](scripts/behaviour/README.md)
  against an emulator and paste the rates that moved. Several of Jenny's
  guarantees are sentences the model honours, not code, and no test goes red
  when one stops holding.

## Design rules

- **The core stays small; extend at the edges.** `jenny/agent/loop.py` and
  `runner.py` are the critical path: a feature that can live in a channel, a
  tool or a skill goes there.
- **Duplication over premature abstraction.** Channels and providers may repeat
  similar logic; do not add base classes only to remove it.
- **The HTTP surface is for reads.** `/api/` never reads a request body, so its
  parameters travel in the query string or a header. Anything that writes, or
  carries content or a secret, is a command over the WebSocket
  (`jenny/webui/commands.py`, see [WebSocket protocol](docs/reference/websocket.md#commands-rpc)).
  Do not smuggle a payload into a header.
- **Every `config.json` write goes through `jenny/config/store.py::mutate()`**
  (see [`AGENTS.md`](AGENTS.md)); calling `save_config()` directly loses other
  writers' changes silently.
- **Explicit over magical.** Configuration is declared in
  `jenny/config/schema.py`; bad input raises instead of being corrected. The one
  exception is loading an unusable `config.json`, which falls back loudly (to
  the `.bak`, or to defaults with the broken file kept) because on a phone a
  config the gateway refuses is an app nobody can repair.

## Conduct

Be kind, assume good faith, keep discussions technical. The maintainer's
decision on scope and merging is final — forks are always an option, that's
what the license is for.

The full rules, and how to report a problem privately, are in
[CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md).
