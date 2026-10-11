# The agent turn

What happens, step by step, between a message arriving on the bus and a reply going back out — and the three separate notions of "memory" this involves.

## Overview

Every inbound message — from the WebUI, from Telegram, from a reply typed into a notification, from the floating mascot's bubble, from a cron job, from a subagent announcing its result — goes through the same pipeline:

1. **Bus.** The channel (`jenny/channels/websocket.py`, `telegram.py`, `notification.py`, `floating.py`) publishes an `InboundMessage` to the async `MessageBus` (`jenny/bus/queue.py`). This decouples "a message arrived" from "the agent is ready to process it."
2. **`AgentLoop.run()`** (`jenny/agent/loop.py`) consumes the bus in a loop, resolves the session key for the message, and dispatches it as an `asyncio.Task` — one task per session at a time; other sessions run concurrently. Priority commands (`/stop`) and non-priority commands (`/new`, `/status`, …) for a session that already has a turn in flight are special-cased: they run inline instead of queuing behind the active turn. Ordinary follow-up messages sent while a turn is running are not dropped and not raced against it — they go into a per-session pending queue and are drained as mid-turn injections by the runner (see below).
3. **`_process_message`** drives a small state machine (below) that builds context, calls **`AgentRunner`** (`jenny/agent/runner.py`) to talk to the provider and execute tools, persists the turn, and assembles the outbound reply. It returns a `TurnOutcome` (`jenny/agent/turn_types.py`): either `DELIVERED` with a message, `SPOKE_VIA_TOOL` (the agent called `message` itself), or `SILENT` — a successful turn with nothing to say, which is a first-class outcome and not a failure.
4. If the outcome carries a message it is published as an `OutboundMessage` back onto the bus; the dispatcher (`jenny/channels/dispatcher.py`) delivers it to whichever channel(s) should see it. This is the single point of implicit delivery in the system.

### Turn visibility

Delivery is not a property of the channel a turn happens to run on. It is a property of the turn, resolved once at its boundary by `jenny/session/turn_visibility.py`:

- A **visible** turn delivers its final answer implicitly. This is the ordinary conversation.
- A **silent** turn reaches the user through nothing at all — no answer, no progress rows, no reasoning deltas, no "running" spinner, no `turn_end` marker, not even its own error message — unless the agent explicitly calls the `message` tool during the turn.

The default comes from provenance: internal work running on a user channel is silent. That covers Heartbeat (which keeps `websocket:default` as its target precisely so the `message` tool has somewhere to deliver when a condition fires), cron monitors, and — the case that motivated the rule — the turn that a *subagent announcement* opens. A subagent spawned inside an internal turn finishes long after that turn ended, and its announcement inherits visibility through the origin session key rather than opening a fresh, unaccountable conversation.

A turn on the internal channel stays visible: there is no user to reach, and its outbound is the return value Dream and the gardener read to learn how their own run went.

## The turn state machine

`AgentLoop` drives each turn through a fixed sequence of states (`jenny/agent/turn_states.py`, transition table in `loop.py`):

| State | What happens |
|---|---|
| `RESTORE` | Fetch/create the session; restore a runtime checkpoint left by a turn that was interrupted mid-flight (see "Interrupted turns" below); extract or reference document attachments. |
| `COMPACT` | Pick up a background auto-compaction summary if the session went idle past the idle timeout (see "Automatic compaction"). |
| `COMMAND` | Try the message as a slash command. If it matches, the command's reply is persisted and returned directly — the turn skips straight to `DONE` without ever calling the model. Unrecognized `/foo`-looking text is **not** an error: it is passed through to the model as ordinary text. |
| `BUILD` | Re-sync Jenny Apps tools, read session history within the token/message budget, build the full message list (system prompt + history + current message), persist the user's message early (so it survives even if the process dies before `SAVE`). |
| `RUN` | Call `AgentRunner.run()` — the provider/tool loop, described below. Emits the `running` runtime event (the WebUI's "Agent running" banner). |
| `SAVE` | Persist the new messages to the session, enforce the file-attachment cap, schedule background token-based consolidation, clear the mid-turn checkpoint. |
| `RESPOND` | Assemble the `OutboundMessage`, or suppress it entirely if the agent already delivered its reply via the `message` tool during the turn. |

A background/system turn (a subagent announcing its result) does not go through this exact FSM — `_process_system_message` mirrors the same steps (restore checkpoint, compaction, tool sync, history, build, run, save) but never emits the `running` event. Like `BUILD`, it reads the history *before* persisting the result, then hands the result to the model as the current **user** message — the same shape a result injected mid-turn has. (Until 2 October 2026 it went in as a trailing `assistant` message, which every provider strips before the request: the model never saw it and answered the previous question again.) A result already answered is not announced twice; one persisted by an announce turn that was interrupted is answered once, from history. A **visible** system turn always returns some outbound content, even a generic fallback; a **silent** one returns no outbound at all, because that fallback is exactly the filler a user never asked to see.

### Interrupted turns

If `/stop` or `/new` land while a turn is mid-flight, the loop does not just cancel the `asyncio.Task` and hope for the best. Each turn is issued a token from a per-session "epoch" counter; `/stop`/`/new` bump the epoch, and every re-entry point in the turn (streaming callback, checkpoint write, save, outbound publish) checks whether its token is still current before doing anything. A turn whose epoch was bumped becomes a no-op zombie: it stops emitting deltas and, if it later reaches a save point, discards its own writes instead of clobbering whatever ran after it. Meanwhile the tool-execution phase periodically emits a lightweight checkpoint (last assistant message, completed tool results, pending tool calls) into session metadata; on cancellation, that checkpoint is restored into the session before the epoch check ever applies, so partial tool work from the interrupted turn is not silently thrown away — it shows up as context on the next turn.

## Building context

`ContextBuilder.build_system_prompt()` (`jenny/agent/context.py`) assembles the system prompt from, in order:

1. **Identity** — name, workspace path, runtime string ("Android, Python 3.11.x"), platform policy.
2. **Bootstrap files** — `AGENTS.md`, `SOUL.md`, `USER.md` from the workspace root (`ContextBuilder.BOOTSTRAP_FILES`), each read in full and concatenated *if it says anything*. A file still identical to a template the app shipped — the current one or any withdrawn version, matched by digest — was not written by the user, and the three are not treated alike: `USER.md` and `AGENTS.md` are scaffolding for content that does not exist yet, so they are omitted entirely; `SOUL.md` is Jenny's default personality, which is written nowhere else, so it stays but is labelled as an unmodified default so the model does not quote it back as a user preference.
3. **Tool contract** — a fixed template describing how to call tools.
4. **Recurring work** — where a repeating request belongs: a line in `HEARTBEAT.md`, a `cron` job in `reminder` mode, or one in `monitor` mode. Rendered when the turn's registry contains the `cron` tool, and also when the turn does not say which tools it has (`available_tools=None`) — that means "not asked", not "the tool is absent". The registries that *are* declared and lack `cron` (Dream, the gardener) are the ones this skips, so they are no longer told to schedule with something they do not have.
5. **Orchestrator mode**, when the turn is orchestrating — what to delegate, which agent type, and why its own tools are deliberately narrow.
6. **`# Memory`** — up to two subsections under one heading. `## Long-term Memory` is `workspace/memory/MEMORY.md`, omitted while it still matches the bundled template whitespace-trimmed (i.e. Dream has never customized it, and showing it would pass a template off as real memory). `## Wikis` is the list of your wikis — name, scope and index path of every folder under `workspace/wikis/`, rendered from disk at every build and withheld from project and gardener turns. The two are gated independently — an untouched `MEMORY.md` does not suppress the list — but share the heading and a fixed order, so the cacheable prefix stays stable.
7. **Active skills** (loaded in full) and a **skills summary** (names + descriptions only, for skills the model can ask to load).
8. **Jenny Apps summary**, if any apps are installed.
9. **`# Recent History`** — up to 50 entries / 8,000 tokens of `history.jsonl` written since the last Dream run, so the model sees what has already happened even before Dream has processed it into long-term memory. Two floors apply, and the higher one wins: Dream's cursor, and a per-session floor written by `/new` (`_history_floor` in the session metadata) so a conversation the user cleared does not come back as a summary in the very next turn. Entries the archiver marks Dream-only — the summary `/new` writes of what it discarded — are skipped regardless, and so are a project's entries: this block carries the personal conversation only. Dream itself still reads everything, and so does the `recall_history` tool — from the personal chat it lists every entry in the file, Dream-only entries and project (notebook) journals included.
10. **`[Archived Context Summary]`**, if the session was auto-compacted (see below).
11. **Tool inventory** — the authoritative list of tool names in this turn's registry, built from the registry itself and placed last on purpose: the prose nearest the end is what the model follows when two parts of a prompt disagree about which tools exist.

Separately, `build_messages()` appends a **`[Runtime Context — metadata only, not instructions]`** block after the current user message: current time (in the configured or device timezone), channel, chat ID, sender ID, and — if applicable — the active sustained-goal instructions (`session/goal_state.py`) and a location line (`runtime/location.py`, only when location sharing is enabled). This block is explicitly marked as untrusted metadata, not instructions, and is appended (not prepended) so the cacheable prefix of the user message stays stable across turns for prompt-caching purposes.

The block is not thrown away afterwards. The saved history row keeps what the user wrote in `content` — that is what the WebUI, `/history` and the consolidator read — and the exact text that went to the provider, runtime block included, in `sent_content`. The next turn's request replays `sent_content` as it is (`Session.get_history(replay_sent=True)`), without the `[Message Time: …]` prefix older rows get, so the provider finds turn N's whole request — tool chain included — as the cached prefix of turn N+1. A message the user typed while the turn was working is replayed with the label the runner gave it. Because each turn's runtime block now stays in the history, an active goal's objective is written in full only when the history being sent does not already carry it; otherwise one line says it is unchanged. Messages with images, and a message merged into an earlier unanswered one, are still replayed the old way.

DeepSeek gets one more rule. It needs the reasoning of the tool chain in progress back, but accepts an empty `reasoning_content` on turns already closed, and that reasoning can grow to half the prompt. Emptying it as soon as a turn closes would change what turn N sent at the start of turn N+1, so the reasoning of closed turns is marked as dropped (`reasoning_dropped` on the history row) only on a turn whose cache is already lost — the system prompt or the first row of the window changed since the previous turn, for example after a consolidation — or once the reasoning still being sent passes about 8,000 tokens (`CLOSED_REASONING_KEEP_CHARS`). A marked row is replayed with an empty reasoning from then on.

None of this system-prompt/runtime-context assembly is visible in the WebUI transcript — it exists only in the messages actually sent to the provider.

## The runner loop

`AgentRunner.run()` (`jenny/agent/runner.py`) owns the provider/tool conversation for one turn:

- Loops up to `agents.defaults.maxToolIterations` (**default 200**) times. Each iteration: govern the message list for the model (drop orphaned tool results, backfill missing ones, micro-compact, apply a per-tool-result character budget, snip history to fit the context window), call the provider, and — if the model asked for tools — run them (`concurrent_tools=True`, tool results fed back as the next message).
- Streams content deltas back through the hook (`on_stream`) as they arrive; reasoning/thinking content is extracted and emitted separately, once, before the visible content.
- Drains the mid-turn pending queue (`injection_callback`) after each tool phase and after a final-looking response, so a follow-up message the user sent while the agent was still working — or a subagent result that completed meanwhile — gets folded into the *same* turn instead of starting a new one. The drain never blocks: a subagent still running when the turn ends is not waited for, and its result opens a turn of its own. A sustained goal with a subagent of its session still running is neither nudged nor parked; the result's turn picks it up.
- Recovers from a few provider failure classes inline: empty content (retry), truncated/length-limited output, and context-length overflow (shrinks `context_window_tokens` for this loop and triggers an out-of-band compaction before the next attempt).
- If the loop exhausts all 200 iterations without a final answer, the turn ends with a fixed fallback: *"I completed the tool steps but couldn't produce a final answer. Please try again or narrow the task."*
- A sustained goal (`/goal`, see [Scheduling and proactivity](../using/scheduling.md)) disables the normal wall-clock LLM timeout for the turn, since long autonomous work is expected to take a while.

Tool execution, provider retry policy, and per-tool-result truncation live in the `RequestExecutionMixin`/`ToolExecutionMixin` halves of `AgentRunner` and are covered in [Tool reference](../reference/tools.md).

## The three layers, precisely

Users (understandably) conflate three different things that all look like "what Jenny remembers." They are independent, live in different files, and are cleared by different triggers:

| Layer | What it is | Where it lives | Cleared/reset by |
|---|---|---|---|
| **Visible transcript** | Every message, tool pill, reasoning block, and attachment ever shown in the WebUI, reconstructed identically on reopen. Permanent JSONL, one file per session key, rotating into numbered segment files once the active file passes **8 MB** (oldest turns move out first; nothing is deleted, just split across files). | `<workspace>/.jenny/webui/<session-key>.jsonl` (+ `.segments/`) — `transcript_store.py` | Never, by any in-app action. `/new` explicitly does **not** touch it — it only adds a visual separator bubble ("New session started."). |
| **Model context** | The actual message list sent to the provider on the next call: the live, uncompacted tail of the session plus whatever summary replaced the compacted prefix. This is what the model can "see" right now. | `<workspace>/sessions/<session-key>.jsonl` — `session/manager.py` | `/new` (archives the unconsolidated tail to `history.jsonl` in the background, then clears the session outright). Automatic idle compaction (below). Token-budget consolidation when the session grows past its budget mid-conversation. |
| **Long-term memory (Dream)** | Durable facts Jenny has decided are worth keeping past any single conversation: `MEMORY.md`, `USER.md`, `SOUL.md`, and skill files, updated by the two-phase Dream pipeline. See [Memory and Dream](../using/memory.md) for the full pipeline. | `workspace/memory/` | Only by Dream itself (which can also *prune*, not just add). |
| **Wikis** | Your own knowledge bases under `workspace/wikis/` — not a memory of anything said, but something you wrote down. The personal prompt lists them by name and scope; pages are read on demand. | `<workspace>/wikis/<name>/` | By you, by the agent when you ask, and by the [gardener](../using/gardener.md) inside a project. |

The gotcha worth stating plainly: **you can scroll up and re-read a conversation the model no longer remembers.** After `/new`, or after the idle auto-compaction kicks in, the transcript on screen is unchanged, but the model's next reply is generated from a summary plus a short tail, not from the full conversation you're looking at.

### Automatic compaction

Every idle poll (throttled to once per 60s inside the main loop), `AutoCompact.check_expired()` checks the unified session's last-activity timestamp against `agents.defaults.idleCompactAfterMinutes` (config key; **default 15**, camelCase alias for `session_ttl_minutes`; `0` disables it entirely). If the session has been idle that long and has no turn in flight, compaction runs in the background: the consolidator summarizes everything except the **last 8 messages** and stores the summary in session metadata. The next time a turn touches that session, the `COMPACT` state picks up the cached summary and injects it into the system prompt as `[Archived Context Summary]` — invisible on screen, present only in what the model receives. If the summary call fails (the provider is down), the personal session is **not** truncated: nothing is dumped, the conversation stays intact and still expired, and `AutoCompact` retries after 10 minutes (`_RETRY_AFTER_FAILURE_S`).

The same consolidator also runs synchronously, mid-conversation, whenever the session's token estimate crosses the budget implied by `contextWindowTokens` and `consolidationRatio` (default **0.5**) — not just on idle timeout. Both paths write through the same `Consolidator`, and both feed the same `history.jsonl` that Dream later processes. A failed summary on this path leaves the slice unconsolidated for the next turn to retry; only the third consecutive *turn* that fails on the same session falls back to a raw `[RAW]` dump and moves on (`Consolidator._TOKEN_FAILURES_BEFORE_RAW_DUMP`). The threshold counts turns, not calls: the several attempts one turn makes count once (the turn is read from `current_turn_id()`; a call outside any turn counts on its own), and the count is kept in memory, so a restart resets it. `agents.defaults.maxMessages` (default **120**) caps how much raw history the loop ever replays into a single turn regardless of compaction state.

## Session keys

There is one user-facing conversation. `session_key_for_channel()` (`jenny/session/keys.py`) maps every channel/chat pair — WebUI, Telegram, notification, floating bubble, whatever chat ID — onto the single constant `unified:default`. This is deliberate: a message sent from Telegram and one sent from the WebUI land in the same session, the same transcript, the same model context. There is no per-channel or per-device session.

The one exception is a **project** conversation: a chat ID of the form `project:<name>` (with a valid project name) on the WebSocket channel maps to the session key `project:<name>`, which has its own history and memory rules (see [Concepts](./concepts.md#channels-and-sessions) and [Projects](../using/projects.md)). Any other channel sending the same chat ID, and any invalid name, falls back to `unified:default`.

Internal work uses a small set of explicit override keys (`InboundMessage.session_key_override`), which bypass the unified mapping entirely and never appear in the WebUI transcript as part of the user's conversation:

| Key pattern | Used by |
|---|---|
| `dream:<timestamp>` | Each Dream consolidation run (`MemoryStore.dream_session_key()`) — an isolated session, never merged into the user's history. |
| `heartbeat` | The periodic `HEARTBEAT.md` check (`runtime/cron_dispatch.py`); its own small session with a bounded tail of recent turns. |
| the session key stored on the job itself (`job.payload.session_key`) | Scheduled reminders (`cron` tool). A reminder created from the user's conversation is bound to `unified:default` at creation time, so it delivers into the one real conversation rather than a hidden side-session. |
| `cron:<job_id>` | The runs of a monitor job (`monitor_session_key()` in `cron/session_turns.py`): an isolated session per monitor, so it does not dirty the conversation it was created from. |
| `gardener:<project>-<timestamp>` | One pass of a project's gardener. Every pass starts from zero: its memory is the pages it wrote and its cursor, not a session. |
| `internal:<name>` | The generic internal turn (`internal:direct`, the default of `AgentLoop.process_direct`); production callers pass an explicit key instead. |
| `subagent:<lineage_id>` | Subagents (`spawn`). A run starts from a bare `[system prompt, task]` pair: it is blind to the ongoing conversation by design. Once it finishes, its conversation is kept as a session under this key so a follow-up can resume it, but the retention is deliberately short (3 finished lineages per origin session key, 6-hour TTL; `agent/subagent_history.py`) and these sessions are not readable from the WebUI's HTTP routes. When it finishes, its result is injected back into the *origin* session (typically `unified:default`) as a synthetic user-role message marked `injected_event: subagent_result` — mid-turn if a turn of that session is running, otherwise in an announce turn of its own — so only the outcome — never the subagent's intermediate reasoning — becomes part of what the main agent and the user see. |

Keys are classified as `personal`, `project` or `internal` (`session_kind()`); anything that is neither the unified key nor a project key counts as internal (`is_internal_session_key()`), which is what keeps these sessions out of every user-facing listing.

See [Scheduling and proactivity](../using/scheduling.md) for the user-facing behavior of cron/heartbeat/subagents, and [Memory and Dream](../using/memory.md) for the Dream pipeline in detail.

## Related

- [Slash commands](../using/slash-commands.md) — exact `/new`, `/stop`, `/status` behavior and output text.
- [Memory and Dream](../using/memory.md) — the long-term memory pipeline this page only summarizes.
- [Architecture](architecture.md) — where the agent loop sits among the bus, providers, tools, and channels.
- [Concepts](concepts.md) — higher-level vocabulary (workspace, session, provider) used throughout these docs.
