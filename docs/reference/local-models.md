# Local models

Running Jenny against a self-hosted model server — Ollama, LM Studio, vLLM, llama.cpp, or anything else that speaks the OpenAI Chat Completions API — instead of a hosted provider.

There's nothing Jenny-specific about self-hosted inference: you configure it exactly like any other `openai_compat` provider (see [Providers and models](./providers.md)). What's different is the network path, because Jenny runs on the phone, not on the same machine as your model server.

## The offline loop, and why it isn't quite "offline"

Jenny itself needs no internet access to run — the gateway, the WebUI, and the agent loop are entirely local to the phone. But the *provider call* still has to reach wherever your model server lives, over the network, from the phone. If that server is your own laptop or a box on your LAN, the phone has to be able to open a connection to it — same as any other app making an HTTP request. There is no special local-inference mode that keeps traffic off the network; a self-hosted `openai_compat` endpoint is called the exact same way a hosted one would be.

Concretely, the endpoint must be **reachable from the phone itself** — not from a desktop browser, not from the machine running the model server. If your phone and your model server aren't on the same network (or connected through a VPN), the request will simply fail to connect, the same as pointing a browser at an address it can't route to.

## Use HTTPS for anything that is not on the phone

Jenny's Android network security config only allows plaintext (unencrypted) `http://` traffic to `127.0.0.1` and `localhost`. That config governs the app's Java/Kotlin and WebView traffic. The provider client is Python (`httpx`) running under Chaquopy, and Jenny does not check the URL scheme itself, so it has not been established that a plain-`http://` provider on a LAN or Tailscale address is refused. Treat HTTPS as the rule to follow for every address other than the phone itself: a request that goes out in cleartext can be read by anyone on the path, API key included.

Practically, this means:

| Endpoint location | Plain `http://` | What you need |
|---|---|---|
| `http://127.0.0.1:PORT` or `http://localhost:PORT` | Allowed | Only reachable if the model server runs *on the phone itself* — not a typical setup. |
| `http://192.168.x.x:PORT` (LAN) | **Not recommended** | Put a TLS-terminating reverse proxy in front of the model server, or otherwise serve it over `https://`. A self-signed certificate works, but installing the CA on the phone does **not** make Jenny trust it — see [Self-signed certificates](./providers.md#self-signed-certificates) for the field that does. |
| `http://<tailscale-ip>:PORT` | **Not recommended** | Same advice: HTTPS. Tailscale itself doesn't change the cleartext rule — it just changes the routing. |
| `https://anything` | n/a (already HTTPS) | No special handling needed beyond a TLS chain Jenny trusts — the bundled default roots, plus whatever the provider's `caBundle` adds. |

If you're used to running Ollama or LM Studio with their default plain-HTTP listener and pointing a desktop app at it directly, don't point Jenny at it unmodified from the phone — put HTTPS in front of it.

## The Tailscale note: SSRF whitelist does not apply here

Jenny has an SSRF (server-side request forgery) protection layer that blocks its own tools (`web_fetch`, `download_file`, `python_exec`'s HTTP helpers) from reaching private/loopback/CGNAT address ranges, with `security.ssrfWhitelist` as the escape hatch (commonly used for a Tailscale range like `100.64.0.0/10`).

**That whitelist has nothing to do with provider calls.** LLM provider requests go through a completely separate code path from the agent's tools and are never checked against the SSRF filter at all — private-range and CGNAT addresses are not blocked for provider traffic in the first place. So if you're setting up a Tailscale-reachable Ollama box as your provider, you do not need to touch `ssrfWhitelist` for the provider connection itself; that setting only matters if you also want the agent's own tools (not the model call) to reach addresses in that range. The thing that *does* gate a Tailscale-hosted provider is the HTTPS requirement above — Tailscale gives you private, authenticated routing, but Android's network security config still refuses plaintext HTTP to it.

## `10.0.2.2` is emulator-only

If you've seen `10.0.2.2` in older examples pointing at a host machine's Ollama or vLLM instance, that address only means anything inside the Android emulator — it's the emulator's special alias for "the machine running the emulator." On a real phone, `10.0.2.2` is just an unreachable address like any other; it resolves to nothing on your actual network. On a real device you need the model server's actual LAN IP (or a Tailscale/VPN address, or a public hostname), reachable, and served over HTTPS as advised above.

## Tool calling and grammars

Jenny sends its full tool set on every request, and some self-hosted servers constrain the model's output to those schemas with a generated grammar rather than trusting the model to emit valid JSON. llama.cpp does this whenever tools are present; Ollama, LM Studio and vLLM take other routes.

That conversion has limits Jenny has to stay inside. llama.cpp expands string and array length bounds into literal repetition rules, and guards them twice against `MAX_REPETITION_THRESHOLD` (2000, in `src/llama-grammar.cpp`): once on the repetition count itself, and once on `n_prev_rules * total_rules` — the count multiplied by the complexity of the rule being repeated. The second guard is the one that bites, because it makes the usable ceiling a fraction of 2000 rather than 2000, and the fraction depends on the rest of the grammar. Either way you get:

```
parse: error parsing grammar: number of repetitions exceeds sane defaults, please reduce the number of repetitions
```

surfacing to Jenny as `HTTP 400: Failed to initialize samplers: failed to parse grammar`. The failure is all-or-nothing: llama-server compiles **one** grammar from the union of every tool schema, so a single out-of-range field breaks every request that carries tools, including a bare "hi". It is not a symptom of a bad model, a bad prompt, or a missing `--jinja`.

Measured against llama-server b10210 with Qwen2.5-3B-Instruct: the two long free-text fields Jenny had then (`long_task.goal` and `complete_goal.recap`, tools since removed) failed even when lowered to 2000, and passed at 1000. Rather than pick a number that happens to fit today, both dropped their schema-level bound entirely and check the length in `execute` instead — the limit the model sees is unchanged, and it costs nothing on the wire. With that in place the full tool set as it was then (22 tools; there are more now, see the [tool reference](tools.md)) compiled and answered normally. A test in `tests/agent/tools/test_schema_wire_limits.py` fails the build if any schema drifts back over the cap.

Re-checked on-device against b10229 built from source in Termux, same model, `llama-server -m … --host 127.0.0.1 --port 8080`: the pre-fix schemas return the grammar error, the same schemas capped at 2000/1500 still return it, and the current ones answered HTTP 200 with all 22 tools of that time present.

If you write your own tool ([Write a tool](../contribute/write-a-tool.md)), the same applies: keep length and item bounds small, or leave them out of the schema and validate inside `execute`.

One thing that has nothing to do with grammars but shows up as the same HTTP 400: Jenny's tool schemas alone are around 5,800 tokens, before the system prompt. Start `llama-server` with a context well above that (`-c 16384` or more) or every request fails with `exceeds the available context size` — which reads like a Jenny bug and is not one.

## The first token takes as long as the prompt

A hosted provider answers in a second or two. A model server on the phone has to read the whole prompt first, and while it does the connection stays completely silent — no tokens, no keep-alives. With Jenny's tool set that prompt is ~5,850 tokens: on a Titan 2 (MediaTek mt6878) running Qwen2.5-3B Q4_K_M that measured **217 s at ~27 tok/s**, thread count and batch size making no difference — it's memory-bound, not core-bound. Once the first token lands, generation is steady, and a second turn on the same prefix is near-instant because the server keeps the KV cache.

So the wait *before* the first token and a gap *inside* a running stream are different things, and Jenny times them differently:

| | Loopback endpoint | Everything else |
|---|---|---|
| Wait for the model's first output | 600 s | 300 s |
| Gap after the first output (stall) | 90 s | 90 s |
| HTTP connect / write / pool timeout | 600 s | 120 s |
| HTTP read timeout | 610 s | 310 s |

Two consequences worth knowing. The first-output wait is measured by the stream itself and is **not** capped by the HTTP request timeout: the read timeout is never lower than the longer of the first-output and idle budgets plus 10 seconds (so a silent model is cut by the stream budget, with a readable message, not by an anonymous `ReadTimeout`), which is where 610 s and 310 s come from. A self-hosted server that is *not* on loopback — a LAN or Tailscale box behind HTTPS — is treated like any other remote endpoint, so its first-token budget is 300 s; if it needs longer, `JENNY_STREAM_FIRST_OUTPUT_TIMEOUT_S` has to be raised. Only connecting, writing and the connection pool stay on the 600 s / 120 s request timeout, since a connection that will not open has nothing to do with a model that is thinking. And the knobs (`JENNY_STREAM_FIRST_OUTPUT_TIMEOUT_S`, `JENNY_STREAM_IDLE_TIMEOUT_S`, `JENNY_OPENAI_COMPAT_TIMEOUT_S`) are read from the environment, which the Android runtime has no way to set — on a phone the defaults in that table are what you get.

Before this split, every path shared the 90 s stall timeout, so a local 3B never survived its own first turn: the grammar compiled, the server started working, and Jenny gave up with `stream stalled for more than 90 seconds` at 0 tokens received.

## Configuring it

Same provider entry shape as any other `openai_compat` provider — via **Add provider** in the workshop's **Brain → Who thinks** group (Format *OpenAI Compatible*), or directly in `config.json`:

```json
{
  "providers": {
    "providers": [
      {
        "name": "home-ollama",
        "format": "openai_compat",
        "apiKey": "EMPTY",
        "apiBase": "https://ollama.example-tailnet.ts.net:11434/v1"
      }
    ],
    "default": "home-ollama"
  }
}
```

A few notes specific to self-hosted servers:

- **`apiKey` is still required.** Most self-hosted servers don't check it, but Jenny's provider factory refuses to start a provider with no key at all — use a placeholder like `"EMPTY"`.
- **Model IDs must match exactly what the server serves.** Ollama and LM Studio use their own model-tag naming (e.g. `llama3.1:8b`); copy the exact tag the server reports, not a generic model name.
- **`apiType`** defaults to `"auto"`, which for a non-`api.openai.com` base always means Chat Completions — the Responses API auto-detection in "auto" mode only ever triggers for `api.openai.com` directly, so it's irrelevant here regardless of setting.
- Prompt caching's explicit `cache_control` markers only apply on OpenRouter with Claude-named models (see [Providers and models](./providers.md#prompt-caching-whats-actually-happening)) — a local server gets none of that; any caching your server does is its own business, not something Jenny requests.

## Honest end-to-end status

The pieces above — network security config behavior, the SSRF/provider-path separation, and `10.0.2.2` being emulator-only — are all verified against the current code. What hasn't been verified end-to-end on a real device is the full loop of a phone reaching a self-hosted server over LAN or Tailscale with a real TLS certificate in place and getting a working chat response back. Treat this page as "how it's built to work," and expect to troubleshoot certificate trust on first setup.

## See also

- [Providers and models](./providers.md) — provider fields, formats, and the fields you'd set for any endpoint (including self-hosted ones).
- [Security model](../internals/security-model.md) — the SSRF filter and what it does and doesn't cover.
- [Troubleshooting](../using/troubleshooting.md) — "URL blocked" / SSRF errors and connection-refused symptoms.
