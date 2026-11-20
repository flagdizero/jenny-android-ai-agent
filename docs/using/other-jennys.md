# Your other Jenny

If you have two phones, say a work phone and a personal one, each can run its own Jenny, and the two can talk to each other. Your personal Jenny can ask the work one "did the confirmation from the printer arrive?", get the answer back in a few seconds, and, if you allow it, ask it to do something there: "set me a reminder to call the supplier tomorrow at 9".

They talk directly, phone to phone, over [Tailscale](https://tailscale.com). There is no server in between, Jenny's or anyone else's, and nothing goes through Google Drive or a shared folder. Both build flavours (Play Store and GitHub) have it.

Each Jenny stays herself: her own model, memory, notebooks and reminders. Neither one sees the other's things. She *asks*, and the other one decides what to answer.

## What you need

- **Tailscale on both phones**, signed in to the same tailnet and connected.
- **Both phones on at the same time** for a message to go through. If one is off or out of reach, what you send waits on your phone and leaves when the other is back. See [When the other phone is off](#when-the-other-phone-is-off).

**Android keeps only one VPN on at a time.** If your work phone has a company VPN that must always be on, Tailscale can't run next to it, and the two Jennys can't reach each other. Check this before anything else.

## Linking the two

Linking is done once, and needs both phones in your hands.

1. On the first phone, open the workshop: **Hands → Other Jennys**. Turn on **Link with other Jennys**, then tap **Show the QR**.
2. On the second phone, scan the QR with the camera. Android opens Jenny with the form already filled in. Give the other Jenny a name ("Work", "Personal") and tap **Link**.

Without a camera, use **Or type on the other Jenny** instead: on the second phone, tap **I have the other's code** and type the address (the Tailscale name, or the `100.x.y.z` address) and the code shown under the QR.

The code is 8 characters, works once, and expires after 10 minutes or five wrong tries. It never travels over the network: the two phones prove to each other that they know it, and then agree on a secret of their own. The QR link fills in the form and nothing else. Linking still takes your tap.

Each linked Jenny is a row in the panel, with its state (*Last contact*, *Not reachable*, *N waiting*), a **Test** button that sends it a ping, **Rename** and **Unlink**. Unlinking tells the other Jenny too if she is reachable. Linking again needs a new code.

## Asking a question

In the personal chat, ask in your own words: "ask Work whether the confirmation from the printer arrived". Jenny sends the question and tells you she asked. The answer arrives as a card of its own in the chat, with the question it answers, usually within seconds. If the other phone is unreachable it arrives later, by itself, with a notification, like a helper's result.

The other Jenny answers in a **read-only turn**: she looks at her own chats, notebooks, diary and reminders, and answers. She can't write, send, schedule or start helpers while answering, whatever the question says. The question can't make her leak your data somewhere else, because in that turn she has no tool that sends anything anywhere except the answer.

What she can see is what passed through *her*: her chats, her notebooks, her reminders and files in her workspace. She does not read the work phone's email, WhatsApp or other apps.

The answer becomes part of the personal chat. Your personal Jenny sees it in later turns ("so the printer confirmed, what next?") and it goes into her memory with the rest of the conversation, as something Work said.

A question nobody answers within 24 hours expires, and the chat says so.

### Files in the answer

If the answer is a document ("send me the quote you saved this morning"), the other Jenny can attach it, up to three files of 20 MB each. This is off by default. It is the point where a document leaves the work phone, so it is decided on that phone: **Answers can carry files**, per linked Jenny. With it off, the answer says the file exists but can't be sent.

A file arrives in pieces, each checked. One that arrives half or damaged is not shown.

## Asking it to do things

Asking is one thing; doing is another. With **Can ask me to do things** on, your personal Jenny can ask the work one to act on the work phone: "put a reminder there", "save this in the Clients notebook", "have a helper compare these three suppliers".

It is set on the phone that acts, per linked Jenny, at one of three levels:

- **No** (the default): requests to do things are refused. Questions work as above.
- **Yes, but ask me before sending anything out**: she does the work, but anything that would leave the work phone toward an address the model picks waits for you to tap **Allow** on a notification: opening a page, downloading a file, using the browser, network access from code. The notification says what she wants and where ("Personal wants to open example.com"). **No**, or no answer within 10 minutes, means no, and she carries on without it and says so. Within one turn she asks once per site; a helper working on the request asks for itself.
- **Yes, everything**: she does whatever she's asked, network included, without asking.

At every level she can't install an app update on request. A web search and a notification to you are not "sending out" here: a search only reaches the search engine, and a notification only reaches you. Jenny has no tool to email or message other people, so "email the supplier" can't be done at any level.

The work is done the way Jenny does any work: often by her helpers, who follow the same rules as the request. A reminder she sets on request rings in the work phone's personal chat, and when it rings it follows the rules of the level you have *then*.

### What came back, and undo

The card in the personal chat shows what she did on the other phone, listed by the app rather than written by the model: files changed, reminders added, pages opened. If her helpers are still at it, the card says so, and a second one follows when they are done.

Everything that changed on the work phone can be undone. **Undo** puts the files back as they were and removes the reminders the request created. It is in three places:

- the **done** notification on the work phone;
- the request's row in **Hands → Other Jennys** on the work phone;
- **Ask to undo** on the card in the personal chat, which works only while that phone still accepts requests to act.

A file that changed again after the request is left as it is, and the result says how many. Opened pages and anything already sent can't be called back.

## A daily report

There is no separate report feature, because a reminder does it. In the personal chat: "every weekday at 7pm, ask Work how the day went and what's left to do". Every evening the question goes out and the answer arrives as a card, and your personal Jenny knows about the day without you asking.

The card arrives even on a quiet day. A monitor ("only tell me if Work has news") still shows Work's card when there's nothing new, because the answer doesn't go through the monitor's check.

## Permissions are decided by whoever receives

Each phone decides what the other may do *to it*: whether the other can ask questions (**Can ask me questions**, on by default), whether answers to it can carry files, and whether it can ask for things to be done. Nothing set on the personal phone opens anything on the work phone.

Both directions work the same way. The work Jenny can ask the personal one too, under the personal phone's rules.

Two more limits hold everywhere:

- **No ping-pong.** A turn answering the other Jenny, or doing something she asked, can't ask her anything in turn. A reminder created on request can't either.
- **A cap per hour.** Each Jenny answers at most 30 questions an hour from each linked one. Past that she says "too many" without running a turn.

## When the other phone is off

Every message waits in a queue on the phone that sends it, and is removed only when the other phone confirms it arrived, so nothing arrives twice. The phone retries after 30 seconds, then at growing intervals up to every 30 minutes, and checks the queue every 15 minutes even while the phone sleeps. **Test** in the panel tries again at once. The panel shows how many are waiting.

## After a restore

The secret the two phones share is kept outside the workspace. It is not in snapshots or in the encrypted backup, so a phone restored from a backup shows the other Jenny as **Link again**, and you link them with a new code. Android's phone-to-phone transfer, on the other hand, carries it like everything else.

## Security, in short

- The two Jennys talk on the Tailscale listener of [Jenny from your other devices](remote-access.md), on a path of their own that only accepts other Jennys, and only from your tailnet. A browser can't use that path, and another Jenny can't use the browser's.
- Every connection is proved both ways with the shared secret before anything is said.
- What arrives from the other Jenny is treated as data, not as orders. Her turn starts marked as untrusted, which brings in the same limits as a page read from the web.

The details: [Security model](../internals/security-model.md#other-jennys). The config keys: [`peers`](../reference/configuration.md#peers).

## See also

- [Settings](../reference/settings.md#other-jennys): the panel, field by field.
- [Scheduling and proactivity](scheduling.md): reminders and monitors.
- [Helpers: stop, undo, continue](helpers.md): the undo that requests to act use too.
