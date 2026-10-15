# Providers and models

How Jenny talks to an LLM: the two wire formats it speaks, how to add or change a provider, and what actually happens (and doesn't) when you do.

Jenny does not ship with any model. It is bring-your-own-key: you point it at an LLM endpoint you already have access to, and every request — and every dollar or token it costs — goes through your own account.

## There is no built-in catalog

Jenny does not maintain a list of "supported providers." A provider is just an entry you define yourself: a name you pick, a wire format, a credential, and an endpoint. Jenny never infers anything from the model name, the shape of the API key, or the base URL — whatever you configure is exactly what runs. This is why pasting the right API key against the wrong base URL, or the right base URL with a model ID from a different service, is the single most common first-run failure (see [Quick checks](#quick-checks)).

## The two formats

Every provider entry declares one `format`, and that field alone decides which backend code path handles the request:

| Format | Speaks | Default base URL | Typical services |
|---|---|---|---|
| `anthropic` | Claude via the Anthropic Messages API | `https://api.anthropic.com` | Anthropic direct, or any Anthropic-Messages-compatible proxy |
| `openai_compat` | OpenAI Chat Completions (and optionally the Responses API) | `https://api.openai.com/v1` | OpenAI, Groq, DeepSeek, Ollama, vLLM, OpenRouter, Together, Fireworks, and any other Chat-Completions-shaped endpoint |

These are the exact hints shown in the onboarding wizard's "Choose your provider format" step: **Anthropic Compatible** — "Claude models via Anthropic Messages API" — and **OpenAI Compatible** — "OpenAI, Groq, DeepSeek, Ollama, vLLM, OpenRouter, Together, Fireworks...". The same two choices reappear in the workshop's **Brain → Who thinks** whenever you add or edit a provider.

Both default base URLs apply only when you leave the base URL field empty. Change it whenever the service isn't the default one — Groq, DeepSeek, OpenRouter, Together, Fireworks, or anything self-hosted all need their own `apiBase` under the `openai_compat` format.

## Managing providers

Providers are managed in the workshop, under **Brain → Who thinks** (the same list the onboarding wizard's "Connect your provider" step writes to). Which of them answers, and with which model, is chosen right there — each provider's group lists its models, and a tap on one makes it answer — or on the home's **Settings → Who answers** page, which can also add or replace a provider's key. Each provider has:

- **Name** — a free-form label (e.g. "My Claude"), not a service identifier. It's what other config, like model presets, refers to.
- **Format** — the `anthropic` / `openai_compat` choice above.
- **API Key** — shown masked, as a 4+4 character hint (first four and last four characters) once one is configured, never displayed in full again.
- **Base URL** — shown as `(default)` when left empty.

Actions available there: **Add provider** below the list, and **Edit** and **Delete** in the panel that the settings button beside each provider opens. Adding one has a **Use it now** switch (on by default) that makes it the one that answers as soon as it's saved, and while it's on it asks for its **First model**; with the switch off the field goes away and the model is picked when you switch to that brand. A few things worth knowing:

- Saving shows **"Provider saved"** (or **"Saved, and it answers now"** with **Use it now** on); deleting asks **`Delete provider "{name}"?`** and then confirms **"Provider deleted"**.
- You cannot delete the last remaining provider (**"Cannot delete the last provider"**) — Jenny always needs at least one configured to keep the agent runnable.
- Both **Name** and **API Key** are required to save (**"Name and API Key are required"**).
- **Anything you change in these screens hot-reloads immediately** — which provider is active, the key, the base URL, the format, the CA certificate — with no app restart and no "requires restart" prompt. The gateway rebuilds the provider backend in place and swaps it into the running agent for the next turn. If you've read an older doc (or `providers.md`'s previous revision) claiming a provider switch needs a restart, that claim is false as of the current code.
- Up to and including **0.11.0** that was only true of the active provider, the model and the base URL. Saving a **CA certificate** or an **API key** on its own left the running agent using the client it had built at startup, so a correct CA could load the model list and still fail every chat message with `[SSL: CERTIFICATE_VERIFY_FAILED]` ([#12](https://github.com/flagdizero/jenny-android-ai-agent/issues/12)). On those versions, force-stop the app and reopen it after saving; the certificate on disk is already correct and is picked up at startup.
- `apiType`, and the advanced `extraHeaders` / `extraBody` / `extraQuery` fields described below, are **not exposed in the UI at all** — they only exist if you hand-edit `workspace/config.json`. Hand-editing `config.json` directly *does* require restarting the app for the change to take effect; only changes made in the UI hot-reload.

## Provider fields (`config.json`)

The full field set, only reachable by hand-editing `providers.providers[]` in `config.json`:

| Field | Required | Description |
|---|---|---|
| `name` | yes | Free-form identifier, referenced by `providers.default` and by any `modelPresets.<preset>.provider`. |
| `format` | yes | `"anthropic"` or `"openai_compat"`. The only field that picks the backend. |
| `apiKey` | yes | The gateway refuses to start a provider without one — see the exact error below. Local servers that don't check keys still need a placeholder like `"EMPTY"`. |
| `apiBase` | no | Full HTTP base URL, version path included where the service expects it (e.g. `/v1`). Omit to use the format's default. |
| `caBundle` | no | Path to a PEM certificate to trust on top of the default roots — for a server with a certificate signed by your own CA. Relative paths start at the workspace. Also editable in the workshop. See [Self-signed certificates](#self-signed-certificates). |
| `apiType` | no, `openai_compat` only | `"auto"` (default), `"chat_completions"`, or `"responses"`. See [Chat Completions vs. Responses API](#chat-completions-vs-responses-api-openai_compat-only). Config-only, not in the UI. |
| `extraHeaders` / `extraBody` / `extraQuery` | no | Extra request headers, body fields, and query params merged into every request to this provider. Config-only. |

Keys may be written as camelCase or snake_case in the file; Jenny always writes camelCase back when it saves.

### A base URL without its version

The `apiBase` above should carry the version path, but one common slip is forgiven for `openai_compat` providers. If a request gets a `404` and the base has no version segment (a last path segment like `v1`), Jenny retries **once** on `<base>/v1`. If that answers, it adopts the corrected base for the rest of the run and logs `API base URL auto-corrected`; nothing is written to `config.json`, so fix the field in Settings if you want the log line gone. If the retry also returns `404`, the error names both URLs it tried, so it reads as a wrong base and not a wrong key. A base that already ends in a version segment is never retried.

## Self-signed certificates

If your server's certificate is signed by a CA of your own, installing that CA on the phone
does nothing for Jenny. Her HTTP calls are made by the Python runtime bundled inside the APK,
and that runtime carries **its own** set of trusted roots; Android's system and user
certificate stores are never consulted. The symptom is
`[SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate`.

Name the CA in the provider instead:

1. Get the PEM file into the workspace. Sending it to Jenny as a chat attachment puts it in
   `workspace/uploads/`; asking her to save the text you paste works too.
2. In the workshop, **Brain → Who thinks** → the provider's settings button → **Edit** → **CA certificate**, and enter the path
   (relative paths start at the workspace, so `uploads/ca.pem` is enough).

The trust is **added**, not swapped: the default roots stay in place, so a provider that also
talks to a publicly-signed host keeps working. The same trust is used by the model catalog
probe, so the model list loads too.

If the file is missing, unreadable, or isn't a certificate, the save is refused with a message that says
which path failed — Jenny never quietly falls back to her own bundle, because that would leave
you believing you're using your certificate when you aren't. The same check runs at startup: a
`caBundle` that has gone missing since (a wiped workspace, a restore from backup — the file
lives in the workspace, so it does travel in a backup) stops the provider from being built, and
the gateway starts in its "no provider configured" state with the reason in the log.

Disabling certificate verification is deliberately not offered.

## Choosing the active provider

Jenny picks the active provider in this order:

1. the entry whose `name` matches `providers.default`, if set;
2. otherwise the first entry in the `providers.providers` list;
3. otherwise startup / the next request fails with **"No provider configured"**.

## API keys are stored in plaintext

Every `apiKey` value sits in `workspace/config.json` as plain text. The only thing standing between that file and the rest of the world is Android's normal per-app sandbox — there is no separate encryption, keychain, or OS credential store involved.

Android's own cloud backup is excluded for this app, so the file doesn't travel to Google in plaintext that way. It does travel in one deliberate, encrypted way — a `.jbk` you export — and in clear in one: the phone-to-phone transfer when you set up a new phone. See [Backup and restore](../using/backup.md).

If you export an unencrypted copy of `config.json` yourself (e.g. by pulling it off the device for debugging), you are exporting your API keys in the clear; handle that file accordingly.

## Chat Completions vs. Responses API (`openai_compat` only)

`apiType: "auto"` (the default) mostly means "use Chat Completions." Jenny only considers the OpenAI Responses API at all when *both* of these are true:

- the base URL points directly at `api.openai.com` (not OpenRouter, not any other gateway), **and**
- the request either sets a `reasoningEffort` value, or the model is one of OpenAI's reasoning families (o1/o3/o4, or any `gpt-5*` model).

When both hold, Jenny tries the Responses API — and if it starts failing, a small circuit breaker kicks in: after **3 consecutive failures** for that model and reasoning-effort pair, it stops trying Responses and falls back to Chat Completions for **5 minutes** before probing Responses again (a single "half-open" retry). This is tracked per model *and* reasoning effort (the breaker key is `model:reasoning_effort`, so a failing `high` does not shut out `low`), automatic, and not configurable beyond forcing `apiType` to `"chat_completions"` or `"responses"` explicitly if you want to skip the auto-detection entirely.

For every other endpoint — Groq, DeepSeek, Ollama, OpenRouter, a self-hosted server, anything that isn't `api.openai.com` directly — `auto` always means Chat Completions; the Responses API is never attempted.

That last sentence is about `auto` only. Setting `apiType: "responses"` **explicitly** forces the Responses API on any `openai_compat` base URL, auto-detection included — which is what makes gateways that serve a Responses-shaped endpoint of their own reachable. OpenCode Go is the exception that needs none of this: Jenny picks its endpoint per model by itself (see [OpenCode Go](#opencode-go) below).

## Session affinity header

Every request from the `openai_compat` provider carries an `x-session-affinity` header: a random ID generated once when the provider is built, so it stays the same for the whole run and changes when the gateway restarts. It exists so that a gateway that routes or caches by session can keep a run's requests together; it carries nothing about you. An `extraHeaders` entry with the same name replaces it. The `anthropic` format does not send it, and OpenCode Go adds its own `x-opencode-session` header on top (see below).

## Prompt caching: what's actually happening

Be precise about this, because it differs a lot by format:

- **`anthropic` format**: cache-control markers are always applied to every request. This is unconditional — no setting to flip.
- **`openai_compat` format**: Jenny only emits explicit `cache_control` markers when **both** the base URL is OpenRouter **and** the model name looks like a Claude/Anthropic model (contains "claude" or an `anthropic/` prefix). Every other `openai_compat` endpoint — OpenAI direct, Groq, DeepSeek, a self-hosted server, OpenRouter with a non-Claude model — gets no explicit cache markers from Jenny at all. Whatever caching happens there, if any, is entirely up to that provider's own server-side behavior; Jenny doesn't request or control it.

In short: don't expect Jenny-driven prompt caching outside of Anthropic-format requests and the OpenRouter-Claude combination specifically.

What Jenny does do for every provider is keep the conversation append-only: the next turn resends the previous turn's messages byte for byte, runtime block included (see [The agent turn](../internals/agent-turn.md)). That is what lets a server-side prefix cache — DeepSeek's, OpenAI's — reuse the whole previous turn instead of stopping at its first message.

## OpenRouter attribution headers

When the configured base URL contains `openrouter` (case-insensitive), Jenny automatically attaches attribution headers to every request: an `HTTP-Referer` pointing at the project's GitHub repository and an `X-OpenRouter-Title` of "Jenny" (plus a categories header). This is fixed behavior tied to detecting an OpenRouter base URL — there's no setting to suppress it, and it has no effect on non-OpenRouter endpoints.

## OpenCode Go

[OpenCode Go](https://opencode.ai/docs/go/) is a subscription that fronts a curated set of open coding models behind one API key. Its docs ask third-party clients to identify themselves with their own user agent and to send a stable per-conversation ID in `x-opencode-session`, which the gateway uses for routing and prompt-cache affinity. Jenny does both automatically whenever the configured base URL contains `opencode.ai` — there's no setting to flip, and no effect on any other endpoint.

The conversation ID is a SHA-256 hash of Jenny's internal session key, truncated to 32 characters: opaque, stable for the life of a conversation, and distinct across conversations. The raw session key names a channel and a chat (`telegram:123456789`), so it is never sent as-is. Internal sessions — cron, Dream, heartbeat — each get their own ID, as does the unified user conversation. The model-catalog probe sends the user agent but no session header: a catalog listing is not a conversation.

### One base URL, three endpoints, picked per model

Go serves the same base URL in three different wire formats, and each model accepts exactly one of them:

| Endpoint | Format | Models |
|---|---|---|
| `/chat/completions` | OpenAI Chat Completions | GLM-5.x, Kimi, LongCat, DeepSeek, MiMo, Hy |
| `/responses` | OpenAI Responses | GPT Luna, Grok, Muse Spark |
| `/messages` | Anthropic Messages | Claude Haiku, MiniMax, Qwen3.x |

One provider entry reaches all of them. Set `apiBase` to `https://opencode.ai/zen/go/v1` and pick any `format`: when the base URL is OpenCode, the format only decides which endpoint Jenny tries **first** (`/messages` for `anthropic`, `/responses` for `openai_compat` with `apiType: "responses"`, `/chat/completions` otherwise).

Jenny does not need to be told which endpoint a model wants, and could not ask: Go's `/models` listing carries no protocol information. Instead it learns from Go's own refusal. A model sent to the wrong endpoint is rejected with `HTTP 400 {"error":{"type":"ModelProtocolUnsupported"}}` before it generates anything, so nothing ran and nothing was billed. On that error, and only on that error, Jenny repeats the request on the next endpoint. The one that answers is remembered for that model in `workspace/.jenny/opencode_endpoints.json`, so the next turn goes straight there; in practice the first message to a new model is about a second slower, and that's all.

The rules that keep this from hiding real problems:

- **Any other error goes straight to you.** A 400 about account privacy, a bad key or a bad parameter is not retried on another endpoint, because switching wouldn't fix it and would bury the real cause.
- **If no endpoint accepts the model, you see the first error**, the one from the endpoint your format chose, not the last one tried.
- **Only a successful reply teaches.** An error never records an endpoint for a model.
- **A streamed reply that has already shown text is never repeated.** The refusal arrives before any output, so this only matters for some other, unexpected failure.
- **A remembered endpoint that starts refusing is forgotten**, and the search starts over.

The provider dialog only exposes name, format, API key, base URL and CA certificate. You never need `apiType` for Go; delete `opencode_endpoints.json` to make Jenny relearn every model.

### `name` is stripped from messages

The `/chat/completions` endpoint rejects the optional `name` key on a message instead of ignoring it, answering `messages[N]: "name" is not supported by this endpoint`. Jenny puts that key on tool-result messages, so the failure is late and looks worse than it is: the first request of a turn goes through, and the turn dies as soon as the model calls a tool. Jenny therefore drops `name` from outgoing messages when the base URL is OpenCode, and only then — every other endpoint still receives it. Nothing is lost: `tool_call_id` is what correlates a result with its call, and the local history keeps the name.

### Two things to know before subscribing

- **Privacy.** Most Go models are zero-retention, but `Muse Spark 1.2/1.3 Contributor` are not: their discounted pricing is explicitly in exchange for using your prompts and completions to train future models. For an agent with access to your files and messages, avoid them. `Grok 4.6` and `GPT 5.6 Luna` retain abuse-monitoring logs for 30 days.
- **Traffic shape.** Go is meant for coding agents and comparable clients, and the docs say traffic is monitored for abuse. Jenny's own validated-client peers include general-purpose agents, so its shape is fine, but the terms are OpenCode's to enforce and to change.

## Model IDs must match the endpoint exactly

Jenny sends whatever string you put in the model field straight to the provider. There's no translation or aliasing layer. The most common way a working provider config produces "model not found" is pointing a preset or the onboarding model field at a model ID that belongs to a different service than the one `apiBase`/`apiKey` are configured for — e.g. an OpenRouter-style `anthropic/claude-...` slug sent to a direct Anthropic endpoint, which expects a plain `claude-...` name (or vice versa).

## Quick checks

| Symptom | Likely cause |
|---|---|
| `Provider '<name>': api_key is required.` | The active provider entry has no `apiKey`. Local/self-hosted servers that ignore auth still need a placeholder value. |
| `messages[N]: "name" is not supported by this endpoint` (HTTP 400, after the first tool call) | An OpenCode base URL reached by a code path that skips the provider's message sanitiser. See [OpenCode Go](#name-is-stripped-from-messages). |
| `ModelProtocolUnsupported` (HTTP 400) on an OpenCode base URL | Should not reach you: Jenny retries on Go's other endpoints. If it does, no endpoint accepted that model ID. See [OpenCode Go](#one-base-url-three-endpoints-picked-per-model). |
| `This Go model trains on request data. Allow paid endpoints that train on request data…` (HTTP 400) | Muse Spark on an OpenCode workspace whose Privacy settings do not allow models that train on your data. It's an account setting on opencode.ai, not a Jenny one; see the privacy note under [OpenCode Go](#two-things-to-know-before-subscribing). |
| `No provider configured. Add a provider in Settings or edit workspace/config.json...` | `providers.providers` is empty. Add one from the workshop's **Brain → Who thinks → Add provider**, or by hand-editing `config.json`. |
| 401 / unauthorized | The key is missing, expired, has stray whitespace, or belongs to a different service than the configured base URL. |
| Model not found | The model ID doesn't exist on the endpoint you configured — check it's the exact ID that endpoint serves, not a name copied from a different provider's docs. |
| Connection refused | A local/self-hosted server isn't running, or the base URL has the wrong host, port, or path. See [Local models](./local-models.md) if the endpoint is off-device. |
| Could not fetch models (on **Who answers**: "The list did not arrive") | The model list probe failed. This doesn't block saving a provider; the model in use stays listed, and a model ID can still be entered by hand as a new provider's **First model**, or in `config.json`. |

The model-list probe used by **Who answers** (`GET <apiBase>/models`) is advisory only: a failed probe never blocks you from saving a provider, and a successful one never changes anything in your config beyond what you explicitly choose.

## See also

- [Local models](./local-models.md) — self-hosted endpoints (Ollama, vLLM, LM Studio) reachable from the phone.
- [Configuration](./configuration.md) — full `config.json` reference, including model presets and agent defaults.
- [Settings](./settings.md) — the Settings tour, including **Who answers**, **Who thinks** and **Parameters**.
- [First run](../start/first-run.md) — the onboarding wizard that writes your first provider entry.
- [Privacy](../internals/privacy.md) — what leaves the device and when.
