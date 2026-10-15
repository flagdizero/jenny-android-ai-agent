# Privacy

There is no Jenny backend and no telemetry: what Jenny sends off the device is either a direct, traceable consequence of something you asked it to do, going to a service you configured, or the one daily update check against GitHub Releases, which carries nothing about you and has its own switch.

## No telemetry, no Jenny-operated backend

Jenny does not phone home in the telemetry sense (the daily update check, covered below, only asks GitHub for a public file). There is no analytics SDK, no crash reporter, and no Jenny-operated server anywhere in the stack — the codebase has been checked for common telemetry/crash-reporting libraries (Firebase, Crashlytics, Sentry, generic "analytics" SDKs) and none are present. The one thing in the code that uses the word "telemetry" internally is the token-usage counter, and that counter is purely local bookkeeping (visible in the workshop, under **Brain → System → Token Usage**): it is never transmitted anywhere.

The WebUI itself is served entirely from `127.0.0.1` — no page, font, or script it loads comes from the internet.

## The six data recipients

Data only leaves the phone through one of these six paths, each gated by a condition you control.

| Recipient | What it receives | Condition |
|---|---|---|
| **Your configured LLM provider** | Chat messages, session history, tool results, and the content of any file the agent reads from the workspace. **Also your device's last-known location, on every single turn**, if location sharing is on and the Android permission is granted — not only when you explicitly ask "where am I". | Always, for any turn — this is the provider you added in onboarding/Settings. Location is additionally gated by `tools.location.enable` (default `true`) **and** the Android location permission. |
| **Bing** | Your `web_search` queries. | Only when the agent actually calls `web_search` (`tools.androidWeb.enable`, default `true`; it is the only switch, and also gates `web_fetch` and the `browser_*` tools). The search engine is currently fixed to Bing — there's no picker. |
| **Sites visited by `web_fetch` / `browser_open` / `download_file`** | Whatever a normal browser visit to that site would reveal: the site sees the request coming from a real, hidden Android WebView, with the phone's own IP address, user-agent, and WebView cookies — not an anonymized fetch. | Only when the agent calls `web_fetch`, `browser_open` or `download_file` on a URL. |
| **`api.telegram.org`** | Messages, if you've paired a Telegram bot: your conversation transits Telegram's servers under Telegram's terms, not Jenny's. | Only if `telegram.enabled` is `true` (default `false` — off until you explicitly connect a bot). |
| **GitHub Releases** (`github.com`) | A plain `GET` of the release's `latest.json`, to find out whether a newer version exists. No identifier, no version, no query string: GitHub sees an IP address and a timestamp, like any HTTP server. Nothing about you or your data is in it. | Every 24 hours by default (`updates.enabled`, default `true`; `updates.checkIntervalH`, default `24`). `updates.enabled: false` stops the periodic check; the **Check now** button in Settings still makes one request when you press it. |
| **The SSH hosts you registered** | The commands the agent runs on that machine, and — through `ssh_transfer` — the content of any workspace file it uploads there. Files fetched with `ssh_transfer` travel the other way, from the server into the workspace. | Only if `tools.ssh.enable` is `true` (default `false`), only for an alias a person registered in the workshop (**Hands → SSH**) whose host key you accepted by hand, and only through a `sysadmin` subagent. The agent can never name an address, only one of your aliases. |

One more, smaller case: if your configured provider is OpenRouter, Jenny adds fixed attribution headers to every request (`HTTP-Referer` pointing at Jenny's GitHub repo, `X-OpenRouter-Title: Jenny`, `X-OpenRouter-Categories: android-agent,personal-agent`) so OpenRouter can attribute traffic to the app. This doesn't add a new recipient — OpenRouter is already your chosen LLM provider — but it does add identifying metadata to that traffic.

## What stays local

Everything else lives in the app's private storage (`<filesDir>/workspace` and nearby), inaccessible to other apps and never transmitted:

- `config.json` — including provider API keys, stored in plaintext (see the caveat below).
- Chat history and consolidated long-term memory (`memory/history.jsonl`, `MEMORY.md`, `USER.md`).
- Uploaded attachments (`workspace/uploads/`) and agent downloads (`downloads/` under the turn's root: `workspace/downloads/`, or `<project>/downloads/` inside a notebook).
- Media (images, previews).
- Workspace snapshots (the local "time machine" backups — see [Backup and restore](../using/backup.md)).
- Token usage counts.

**Wiki content is not on this list, and used to be.** The personal chat's system prompt carries the name and one-line scope of every wiki under `workspace/wikis/` (the `summary:` of each `AGENTS.md`), so those names and lines go to your LLM provider on every turn. Page content does not, unless the agent opens a page during a turn — which was always possible under the workspace-to-provider chain below. Versions before 0.10.0 also compiled a directory of the entities in your wikis with a periodic job (Atlas), sending the page inventory to the provider to do it; that job no longer exists, and no derived copy of wiki content is kept in `memory/`.

**A [notebook](../using/projects.md) sends more, and it sends it every turn.** Inside a notebook conversation, that notebook's board (one line per card, up to 2,000 characters) — or, without a board, its map (`wiki/index.md`, up to 2,000 characters) — and up to 6,000 characters of its pages are part of the system prompt on every message, and a message that names a card adds that card's drawer — so page content goes to your provider whether or not the current question touches it. Two further things are worth knowing: what you say in a notebook is captured into a journal file *inside* that folder before Jenny answers, so the sentence is written to disk as well as sent; and a [gardener](../using/gardener.md) pass sends the unread journal lines, the map, the list of page names and your recent messages in that notebook to the provider on its own schedule, with no message from you — and its board pass sends the board, the drawers to rewrite with their pages, and your corrections. The hourly notebook clock sends a due reminder and the page that explains it, to write the announcement. The gardener is switched off in the workshop, under **Memory → Gardener**; the per-turn injection is a property of working in a notebook, and the way not to pay it is to use the personal chat. What a notebook does *not* send is the list of your other wikis — that block is withheld from notebook turns and from gardener passes.

## Android's own backup

The manifest keeps `android:allowBackup="true"`, but with exclusion rules (`res/xml/data_extraction_rules.xml`, and `backup_rules.xml` for Android 11 and older): Google's automatic **cloud** backup takes nothing from Jenny. Before those rules it was eligible to take everything, `config.json` with plaintext API keys included — in practice it failed, because Jenny's private storage is far above the 25 MB quota, but that was luck, not design. To keep a copy off the phone, export Jenny's encrypted backup (see [Backup and restore](../using/backup.md)).

The **phone-to-phone transfer** Android offers when you set up a new phone (cable or Wi-Fi) is allowed and carries everything except caches — API keys included. It goes from one of your phones to another, never through a server.

## The workspace-to-provider chain

It's worth stating this relationship plainly, because it's easy to underestimate: **any file placed inside the workspace is readable by the agent, and anything the agent reads can end up in the context sent to your LLM provider.** There's no separate "private files" area inside the workspace — the workspace boundary (see [Security model](./security-model.md)) controls what the agent can reach on disk, not what it's willing to send upstream once it has read something. If you drop a sensitive document into `workspace/`, expect that its content can travel to whichever provider you've configured, the moment the agent has a reason to read it.

## How to shrink the surface

None of the six recipients above are mandatory except your LLM provider (Jenny can't function without one). To reduce what leaves the device:

- **Turn off update checks** (`updates.enabled: false`) if you don't want the daily request to GitHub; you can still look for a new version by hand with **Check now**.
- **Turn off location sharing** in the workshop (**Hands → Location**) if you don't want your last-known location included in every turn sent to the provider.
- **Leave SSH off** unless you actually want Jenny reaching a server — it is off by default, and every registered host is a machine that receives commands and can receive workspace files.
- **Don't enable Telegram** unless you actually want a second channel — it's off by default, and enabling it means your conversation also flows through Telegram's servers.
- **Pick your LLM provider deliberately.** Since messages, history, and file contents all go to whichever provider you configure, your provider's own privacy policy and data-retention practice matters as much as anything Jenny does.
- Avoid putting anything you wouldn't want reaching your LLM provider inside `workspace/`, given the chain described above.

## Related pages

- [Security model](./security-model.md) — the containment layers and the honest can/cannot lists.
- [Location](../using/location.md) — the location toggle and Android permission.
- [Telegram bridge](../using/telegram.md) — what Telegram adds and when it's active.
- [SSH access](../using/ssh.md) — registering a host, and what the agent can send to it.
- [Configuration reference](../reference/configuration.md) — `tools.location`, `telegram.enabled`, and related keys.
