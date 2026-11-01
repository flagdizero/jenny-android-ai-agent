# Telegram bridge

Jenny can also live in Telegram as a personal bot, so you can talk to it from any device without opening the WebUI — but it's a narrower window onto the same conversation, not a second assistant.

## What this actually is

The Telegram channel is a **single-user bot**: your Jenny bot pairs with exactly one Telegram chat — a private chat between you and the bot. It is not a way to give other people access to your agent, and it is not a separate conversation — it shares the same session as the WebUI (see [Shared session](#shared-session-new-resets-both-sides) below). Your phone has to stay on and Jenny's app has to be running for the bot to answer anything; there is no cloud component.

## Setting it up

You'll find this in the workshop under **Hands → Telegram** (a row that opens the pairing panel), and optionally as a step during first-run setup ("Connect Telegram (optional)").

1. Open **@BotFather** on Telegram and send `/newbot`.
2. Pick a name and a username for the bot.
3. Paste the token BotFather gives you into the **Bot token** field in Jenny.

Press **Connect**. Jenny validates the token immediately against Telegram's `getMe` API before saving anything:

- Wrong or malformed token → `telegram token rejected: <reason from Telegram>`, nothing is saved.
- Phone offline / Telegram unreachable → `cannot reach Telegram: <error type>`, nothing is saved.
- Success → the channel is enabled, the bot's command menu is registered (`/start` "Quick guide", `/new` "New conversation" — best-effort; if this sub-step fails, pairing still proceeds), and a 6-digit pairing code is generated.

The token travels from the WebUI to the gateway as a command over the WebUI's authenticated WebSocket (`telegram.save`), not in the URL of an HTTP request, so it never ends up in a request line or an access log. Jenny never shows a saved token in full again — only a masked hint (first 4 and last 4 characters, e.g. `1234...abcd`). Keep the real token somewhere safe (e.g. re-request it from @BotFather) in case you need it again.

## Pairing your chat

Once the token is saved, Jenny shows a 6-digit code and the line "Send this code to your bot @yourbot on Telegram to pair it:", plus a link to your bot. The WebUI polls for pairing status every 2.5 seconds.

Pairing is accepted **only from a private chat** with the bot. A code sent from a group, supergroup or channel is ignored without any reply (and does not count as an attempt): paired to a group, every member of that group would be driving your agent.

Open the private chat with your bot and send the code — either as a plain message, or as the payload of a `/start` deep link (i.e. following a `t.me/yourbot?start=123456` link). A bare `/start` with no code gets the reply "To pair, send me the 6-digit code shown in Jenny's WebUI."; a wrong code gets "Invalid code. Check the 6-digit code in Jenny's WebUI and try again."

On success the bot replies "✅ Paired! You can now talk to Jenny from this chat." followed by its welcome text, the WebUI shows "Paired with @username" (and a toast "Telegram paired!" if you did this during onboarding), and the code is cleared.

The bot's own replies (pairing, welcome, attachment errors) are in Italian or English, following Jenny's language setting; the texts quoted on this page are the English ones.

The pairing code is written to config, so **it survives an app restart** — you don't lose your place mid-setup. It is regenerated whenever you save a new token or unpair (see below).

## The 5-attempt limit — read this before you start guessing

To stop a stranger from brute-forcing the 6-digit code, Jenny caps pairing attempts at **5 per chat** (and tracks at most 512 chats at once; beyond that, further chats are ignored outright). This cap applies to any text message sent from a private chat during the pairing window that isn't the exact code — including a bare `/start`, which also counts as an attempt.

This protection has a sharp edge: **it applies to you too.** If you mistype the code five times, that chat becomes permanently ineligible to pair — even if you then send the *correct* code, the bot goes completely silent (no error message, nothing). The only way out is to make Jenny regenerate the channel: go to **Hands → Telegram** in the workshop and either unpair or save a new token. Both actions issue a fresh pairing code and reset the attempt counter.

The attempt counter is kept **in memory only** — it also resets whenever the Jenny app itself restarts or the channel is reloaded (any Telegram setting saved, the toggle flipped), so those are (side-effect) ways out too, but don't count on them as a fix.

## A pairing made with a group has to be redone

Older versions accepted the pairing code from any chat, groups included. That is no longer enough: in the paired chat Jenny now only answers messages whose **sender is the paired chat itself** — in a private chat the chat id is the id of the person, so that means you. In a group the chat id belongs to the group, not to any member, so a bot paired with a group **stops answering everyone there**, silently (the device log records a warning that a group pairing must be redone from a private chat). Channel posts, which have no sender, are ignored the same way.

The fix is to pair again from a private chat: **Hands → Telegram → Unpair** in the workshop (it asks first), then open a private chat with the bot and send it the new code.

## Unlink vs. switching the channel off

Once you're paired the section shows a **Channel active** toggle and an **Unpair** button. They do different things, and only one of them costs you the pairing:

| Action | What it does | To get back |
|---|---|---|
| **Channel active** (off) | Stops the channel entirely: no more polling, and the bot replies to nothing. Nothing is deleted — the token, the paired chat and its username all stay on record. | Switch the same toggle back on. That is the whole recovery: no new token, no new pairing code, no trip to BotFather. |
| **Unpair** | Asks for confirmation, then clears the paired chat and generates a fresh pairing code. Leaves the channel switched on. | Send the new code to the bot again — no need to touch the token. |

The toggle is also the channel's status, not just a command: if it is off, the section says so plainly. That matters because the two states are otherwise indistinguishable from the outside — a channel that is off looks exactly like a bot that has stopped answering.

Switching it back on does **not** make Jenny answer everything you sent while it was off. Telegram holds undelivered messages for about a day, and a channel that has just started ignores anything older than five minutes rather than replying to a pile of stale messages at once. The five-minute window is there so the opposite case still works: if the app restarts — Doze, an update, the watchdog — a message you sent seconds earlier is still picked up when it comes back.

## Everything hot-reloads

Saving a token, unpairing, and flipping the toggle all take effect immediately — the running channel is stopped and, if still switched on, restarted with the new configuration in the background. You never need to restart the app for a Telegram setting to take effect.

## Battery exemption

Telegram delivery uses long polling — the app keeps an open request to Telegram's servers waiting for new messages. Android's Doze mode can throttle or suspend that when the screen is off and the phone isn't charging, which delays replies. If Jenny detects it isn't already exempt from battery optimization, the paired view still shows a hint ("For instant replies even when the phone is not charging, exempt Jenny from battery optimization.") and an **Exempt from battery** button that opens the Android system prompt directly.

The same request is now offered in two better places — during first-run setup, and in the workshop under **Brain → Background activity**, which also shows whether the exemption is currently in force and re-offers it after a system update silently reset it (Samsung and Xiaomi updates do this). The exemption was never Telegram-specific: reminders, monitors, Dream and the gardener are throttled by Doze in exactly the same way. It lived here only because Telegram was the first place the delay became obvious.

**An inbound Telegram message can still wait for the phone to wake up, and this one is not fixed.** Jenny holds the CPU awake while she *processes* an update, but not while the long-poll sits waiting for one — that wait is idle by construction and lasts up to `telegram.pollTimeoutS` (50 seconds by default), so a wake lock covering it would be held essentially all the time. That isn't the "only while working" mode you chose, it's `always` wearing a disguise. The honest consequence: with the screen off and the phone suspended, a message you send can sit in Telegram's queue until something else wakes the device. Nothing is lost — Telegram holds the update and it's processed when the phone comes back — but "instant" is not a promise anyone can make here. Setting `power.keepAwake` to `always` does remove this wait, at the cost of real battery; that is the trade, and it's the reason the setting exists. You can change it in the workshop under **Brain → Background activity → Keep the CPU awake** (**Never**, **While working**, **Always**), which also shows whether the CPU is held awake right now; it takes effect at the next Jenny restart. See [Configuration](../reference/configuration.md#power).

## What works from Telegram vs. the WebUI

Telegram is a much narrower surface than the WebUI. Be clear-eyed about the gap:

| | Telegram | WebUI |
|---|---|---|
| Text messages in | Yes | Yes |
| Location sharing in | Yes (see [below](#sharing-your-location-from-telegram)) | N/A (uses phone GPS automatically) |
| Photos (and static stickers, and images sent as files) in | Yes — Jenny sees them | Yes |
| Voice notes, audio, video, documents in | Yes, as files — saved and referenced by path, but not transcribed or watched | Yes |
| Live streaming of the reply | **No** — only the finished message | Yes |
| Tool-use / progress indicators | **No** | Yes: a line saying what she is doing in the home, expandable tool pills in the workshop's Console |
| "Show thinking" block | **No** | Only in the workshop's Console, model-dependent |
| Typing indicator while Jenny works | Yes — "typing…" while the turn runs (it gives up by itself after 5 minutes) | N/A |

### Attachments you send

A caption is read as your message text. Each attachment is downloaded into the same `uploads/` folder the WebUI uses:

- **Photos** go to the model as images, so Jenny can actually look at them. A photo sent *as a file* (uncompressed) is recognised by its MIME type and treated the same way. An album arrives as one Telegram update per photo, so it becomes one turn per photo.
- **Voice notes, audio, video and other documents** are saved and passed to Jenny by path, with a note telling her she cannot hear or watch them — nothing transcribes them.
- **Size caps**: 5 MB for an image, 20 MB for anything else (the most the Bot API lets a bot download). Over the cap the bot replies "📦 That attachment is too big for me to download."; a failed download gets "⚠️ I couldn't download that attachment. Try again." In both cases no turn starts.
- **Not handled**: contacts, polls, dice, games, stories and animated or video stickers get "🤷 I can't handle this kind of message yet."

### Commands

- `/start` — from the paired chat, shows the quick-guide welcome text again (does not start an LLM turn).
- `/new` — starts a new conversation. **This affects the WebUI too** — see [below](#shared-session-new-resets-both-sides).
- `/stop` — stops the currently running turn, wherever it was started.

The bot's own command menu (the `/` button in Telegram) lists only `/start` and `/new`, but that is only the menu: every other [slash command](slash-commands.md) goes through the same router as in the WebUI, so typing `/status`, `/model`, `/history`, `/dream` or `/skill` works from Telegram too. `/help` lists what the personal conversation can do — Telegram is always the personal conversation, so the notebook commands (`/gardener`, `/tidy`, `/init`) are neither listed nor available there.

Messages from anyone other than the paired chat are ignored completely and silently — the bot never confirms or denies that it exists to a stranger. Inside the paired chat, a message is accepted only if its sender is the paired person (see [above](#a-pairing-made-with-a-group-has-to-be-redone)).

### Outbound messages: final text, then attachments

Because Telegram only ever gets the finished answer, long replies are split into chunks of at most **3500 characters** of raw text (Telegram's own hard limit is 4096; the smaller number leaves room for HTML formatting overhead), preferring to break on paragraph or line boundaries. Supported formatting is a small HTML subset — bold, italic, inline code, code blocks, and links; headings become bold text. Everything else degrades to plain characters. If Telegram rejects the formatted chunk, Jenny automatically retries as plain text.

Any files Jenny attaches (via the `message` tool, or file responses) follow the text:

- Raster images (`.jpg`, `.jpeg`, `.png`, `.webp`, `.gif`) are sent as photos with a native preview.
- Everything else (PDFs, SVGs, etc.) is sent as a document.
- **Files over 10 MB are silently skipped** — logged on the device but nothing is shown to you in the chat.
- If Telegram rejects an image as a photo, Jenny automatically retries it as a plain document rather than losing it.

## Canonical view: WebUI sees everything, Telegram doesn't

The WebUI is the single source of truth for the conversation. Every message you send from Telegram, and every reply, is mirrored live into the WebUI's chat view, marked as coming from Telegram — so opening the app later shows the full history regardless of where you actually chatted.

The reverse is **not** true: messages you send from the WebUI are never delivered to Telegram. Telegram only ever receives replies to its own messages, plus proactive messages: monitor alerts, a notebook's reminders, and anything else Jenny sends you on her own from background work. A reminder of the personal chat rings in the chat it was asked from, so one you asked for here comes here, while one asked for in the WebUI stays there. Service replies from the bot itself — pairing prompts, the welcome text, the attachment error replies — never appear in the WebUI either; they aren't part of the conversation.

A proactive message that comes from a [notebook](projects.md) — one of its reminders, or its monitor's alert — starts with the notebook's name in brackets, `[plants] The basil is thirsty.`, because in Telegram everything lands in the one chat and the name is the only way to tell it from one of the personal chat's. In the notebook's own chat in the WebUI the same message appears without it, and the copy sent to Telegram is never added to the personal conversation.

## Shared session: `/new` resets both sides

There is only one conversation — Jenny doesn't keep a separate memory or context per channel. WebUI and Telegram are two windows onto the exact same session. This has one consequence you should know before setting up Telegram:

**Sending `/new` from *either* channel resets the conversation for *both*.** If someone has access to your paired Telegram chat, they can wipe the conversation you've been having on your phone in the WebUI (the prior history is archived, not deleted, but the active context is gone). Keep that in mind before pairing a bot you might share access to.

## Sharing your location from Telegram

Sending a location or venue in the Telegram chat (paperclip → Location) is handled specially: it's recorded as a **Telegram-only override** valid for `tools.location.telegramTtlS` (default 3600 seconds / 1 hour, minimum 60), and it immediately triggers an LLM turn so Jenny can react with your shared position already in context. For a venue, Jenny prefers the place's name and address as the location label. Within the TTL, all Telegram replies use that shared position; once it expires (or from the WebUI at any time), Jenny falls back to the phone's live GPS.

This override is **in-memory** — it's lost if the app restarts, even before the hour is up.

If the location toggle (`tools.location.enable`, default on) is switched off, sharing a location from Telegram is **ignored entirely**, and — this is the one genuinely misleading part of the whole feature — the bot replies with the same "🤷 I can't handle this kind of message yet." used for unsupported message types, which has nothing to do with location being off. If your location share from Telegram seems to vanish, check the Location toggle (workshop, **Hands → Location**) first.

Sending your location also spends a real LLM turn (and therefore tokens) even if you didn't type anything.

## Offline behavior: the phone is the server

There is no server other than your phone. If the Jenny app isn't running (or the phone is off), the bot does not answer — full stop.

When the app comes back, it resumes long polling from scratch (Telegram's internal update offset isn't saved across restarts) and Telegram hands over whatever it still holds. Of that startup backlog, **only messages less than five minutes old are answered** — one LLM turn per message, in sequence. Anything older is dropped (logged on the device as a warning) and never answered: a message you sent while the app was closed for longer than that is lost, not queued. Once the first recent message (or an empty queue) has been seen, the filter switches off for the rest of the run. How long Telegram itself holds undelivered updates is set by Telegram's Bot API, not by Jenny.

## See also

- [Chat basics](chat.md) for how the WebUI side of the same conversation behaves.
- [Location](location.md) for the two-tier location model and the privacy implications of leaving it on.
- [Scheduling and proactivity](scheduling.md) for reminders and monitor alerts that also get delivered to Telegram.
- [Troubleshooting](troubleshooting.md) if the bot has gone quiet and you're not sure why.
