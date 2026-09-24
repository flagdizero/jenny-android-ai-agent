# Jenny documentation

Jenny is a local-first AI agent that runs as an Android APK. These docs are written in English; the app's own UI is bilingual (Italian/English) and switches with your device language.

## Using Jenny

Start here if you just want to install and use the app.

### Start here

| Page | Description |
|---|---|
| [Introduction](start/introduction.md) | What Jenny is — a local Android agent, BYOK, one conversation thread, persistent memory, proactivity — and the prerequisites before you install it. |
| [Install the APK](start/install.md) | Downloading and verifying the signed APK from GitHub Releases (or building it from source instead), the permissions it asks for, and what it means that it also registers as a HOME launcher. |
| [First run](start/first-run.md) | What happens on first boot, what gets created on disk, and the 4-step onboarding wizard that connects your provider. |
| [Set it as your launcher](start/launcher-setup.md) | Setting Jenny as your Android home screen (or undoing it), and the honest take on using it as a daily launcher. |
| [Is Jenny for you?](start/is-jenny-for-you.md) | Who this project is built for, and who it probably isn't. |

### Using Jenny

| Page | Description |
|---|---|
| [Tour of the WebUI](using/webui-tour.md) | The 5-tab dock, swipe navigation between views, Android back-button behavior, and the session info popover. |
| [Chat basics](using/chat.md) | Sending messages, streaming replies, tool call pills, the reasoning block, the changed-files pill, and inline file previews. |
| [Files and attachments](using/attachments.md) | Sending images, files, and camera captures from chat; the exact size limits and what the model actually gets to see. |
| [Memory and Dream](using/memory.md) | How session history, idle compaction, and the two-phase Dream consolidation build Jenny's long-term memory — and how the list of your wikis reaches every prompt. |
| [Projects](using/projects.md) | Project conversations: a chat bound to one folder that remembers by writing pages instead of by feeding Jenny's personal memory — the scope chip, the Writes/Read-only switch, capture, and the map. |
| [The gardener](using/gardener.md) | The background pass that turns a project's journal lines into pages and keeps its map true: when it runs, what it refuses to touch, and how to turn it off. |
| [Scheduling and proactivity](using/scheduling.md) | Reminders (one-shot, recurring, cron), the heartbeat loop, goals/long tasks, and subagents — and what silently breaks when the app is killed. |
| [Mini-apps (Jenny Apps)](using/mini-apps.md) | Chat-authored mini web apps backed by native tools, how they differ from skills, and their sandboxing limits. |
| [Skills](using/skills.md) | Markdown-based skill folders that extend agent behavior in chat, where to see and switch them, and why built-in ones can't be switched off. |
| [Themes and mascot](using/themes-mascot.md) | The 7 UI themes, the mascot's interactions and preferences, and UI language vs. agent language. |
| [SSH access](using/ssh.md) | Registering your own remote machines, generating the on-device key, pinning host fingerprints, short commands vs detached jobs — and why a restore doesn't bring SSH access back. |
| [Telegram bridge](using/telegram.md) | Pairing and using the optional Telegram bridge alongside the WebUI, and what does and doesn't work over it. |
| [Location](using/location.md) | How device location is shared with the model as context, the two-gate permission model, and the privacy trade-off. |
| [Backup and restore](using/backup.md) | Encrypted `.jbk` backups (for disaster recovery) vs. local workspace snapshots (a time machine), and how to restore either. |
| [Phone app launcher](using/app-launcher.md) | The search drawer that opens your phone's apps and mini-apps, and the card a long press on a row opens, for app info, uninstalling, editing and deleting. |
| [Wiki](using/wiki.md) | The knowledge base Jenny compiles on request: its pages and map, editing a page, and reporting one. |
| [Slash commands](using/slash-commands.md) | Built-in commands like `/new` and `/stop`, where each one works (this conversation, the personal chat, or inside a project), the Commands chip that lists them, and how unrecognized commands fall through to the LLM. |
| [Troubleshooting](using/troubleshooting.md) | Diagnosing common on-device symptoms — offline dot, silent errors, missed reminders, silent Telegram, blocked URLs. |

### Reference

| Page | Description |
|---|---|
| [Settings](reference/settings.md) | Every field in the in-app Settings screen, section by section, with its effect and default. |
| [Configuration (config.json)](reference/configuration.md) | Full reference for `config.json` keys, including the ones with no UI control at all. |
| [Providers and models](reference/providers.md) | Configuring an Anthropic or OpenAI-compatible provider, switching between them, and reading provider errors. |
| [Local models](reference/local-models.md) | Using a self-hosted model (Ollama, LM Studio) reachable from the phone instead of a hosted API. |
| [Tool reference](reference/tools.md) | Full reference for every built-in tool the agent can call, grouped by capability. |
| [Environment variables](reference/environment-variables.md) | The `JENNY_*` runtime environment knobs and the release keystore variables. |
| [Android permissions](reference/android-permissions.md) | Every Android permission Jenny requests, why, and what happens if you deny it. |

## Under the hood & contributing

For readers who want to understand how Jenny works internally or contribute code.

### Internals

| Page | Description |
|---|---|
| [Architecture](internals/architecture.md) | The system's components and how a message flows from the WebUI through the agent loop to the LLM and back. |
| [Core concepts](internals/concepts.md) | Core vocabulary: sessions, turns, tools, channels, and the workspace. |
| [The agent turn](internals/agent-turn.md) | What happens inside one agent turn, and the three memory layers (transcript, model context, Dream) users conflate. |
| [Security model](internals/security-model.md) | The four security boundaries, from the Android app sandbox down to the `python_exec` guardrails. |
| [Privacy](internals/privacy.md) | What data leaves the device, who it goes to, and what stays local. |
| [WebSocket protocol](reference/websocket.md) | The WebSocket wire protocol used between the WebUI and the gateway, for anyone building their own client. |

### Contribute

| Page | Description |
|---|---|
| [Build from source](contribute/build-from-source.md) | Building and installing the Android app, and running the gateway standalone for development. |
| [Development setup](contribute/development.md) | Setting up a development environment and finding your way around the project layout. |
| [Testing and CI](contribute/testing.md) | Running the test suite, linting, and type checks, and what the CI pipeline blocks on. |
| [Write a tool](contribute/write-a-tool.md) | Adding a new built-in tool to the agent's tool registry. |
| [Write a mini-app](contribute/write-a-mini-app.md) | Building a Jenny App from scratch: manifest, agent instructions, and storage. |
| [Add a provider](contribute/add-a-provider.md) | Implementing support for a new LLM provider. |
| [Code style](contribute/code-style.md) | Style conventions for Python, docstrings, and commit messages. |
| [Publishing a release](contribute/publish-a-release.md) | Cutting a release with `scripts/release.py`: version bump, signed APK, the `latest.json` update manifest, gradual rollout and the kill switch. |
