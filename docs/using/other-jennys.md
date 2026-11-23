# Your other Jenny

If you have two phones, say a work phone and a personal one, each can run its own Jenny, and the two can talk to each other. Your personal Jenny can ask the work one "did the confirmation from the printer arrive?", get the answer back in a few seconds, and, if you allow it, ask it to do something there: "set me a reminder to call the supplier tomorrow at 9".

They talk directly, phone to phone, over [Tailscale](https://tailscale.com). There is no server in between, Jenny's or anyone else's, and nothing goes through Google Drive or a shared folder. Both build flavours (Play Store and GitHub) have it.

Each Jenny stays herself: her own model, memory, notebooks and reminders. Neither one sees the other's things. She *asks*, and the other one decides what to answer.

There are two ways to reach the other one. You can have your Jenny **ask** her something and bring back the answer, or you can have her **pass you the line** and talk to the other Jenny yourself, from the phone you are holding ([Talking to her directly](#talking-to-her-directly)).

## What you need

- **Tailscale on both phones**, signed in to the same tailnet and connected.
- **Both phones on at the same time** for a message to go through. If one is off or out of reach, what you send waits on your phone and leaves when the other is back. See [When the other phone is off](#when-the-other-phone-is-off).

**Android keeps only one VPN on at a time.** If your work phone has a company VPN that must always be on, Tailscale can't run next to it, and the two Jennys can't reach each other. Check this before anything else.

## Linking the two

Linking is done once, and needs both phones in your hands. The steps below use the workshop panel. The same thing is in the house, under **Settings → Other Jennys → Link another Jenny**: show the QR or type the other one's code, then name her and choose the two permissions that matter most.

1. On the first phone, open the workshop: **Hands → Other Jennys**. Turn on **Link with other Jennys**, then tap **Show the QR**.
2. On the second phone, scan the QR with the camera. Android opens Jenny with the form already filled in: in **Settings → Other Jennys** of the house, or in the workshop panel if that is what is open. Tap **Link**, then give the other Jenny a name ("Work", "Personal"). If linking is off on this phone, **Link** turns it on.
3. Back on the first phone, Jenny asks what to call the one that just linked. Until you answer, she goes by her Tailscale name.

Without a camera, use **Or type on the other Jenny** instead: on the second phone, tap **I have the other's code** and type the address (the Tailscale name, or the `100.x.y.z` address) and the code shown under the QR.

The code is 8 characters, works once, and expires after 10 minutes or five wrong tries. It never travels over the network: the two phones prove to each other that they know it, and then agree on a secret of their own. The QR link fills in the form and nothing else. Linking still takes your tap.

Each linked Jenny is a row in the panel, with its state (*Last contact*, *Not reachable*, *N waiting*), a **Test** button that sends it a ping, **Rename** and **Unlink**. Unlinking tells the other Jenny too if she is reachable. Linking again needs a new code.

## Asking a question

In the personal chat, ask in your own words: "ask Work whether the confirmation from the printer arrived". Jenny sends the question and tells you she asked. The answer arrives as a card of its own in the chat, with the question it answers, usually within seconds. If the other phone is unreachable it arrives later, by itself, with a notification, like a helper's result.

The other Jenny answers in a **read-only turn**: she looks at her own chats, notebooks, diary and reminders, and answers. She can't write, send, schedule or start helpers while answering, whatever the question says. The question can't make her leak your data somewhere else, because in that turn she has no tool that sends anything anywhere except the answer.

What she can see is what passed through *her*: her chats, her notebooks, her reminders and files in her workspace. She does not read the work phone's email, WhatsApp or other apps.

The answer becomes part of the personal chat. Your personal Jenny sees it in later turns ("so the printer confirmed, what next?") and it goes into her memory with the rest of the conversation, as something Work said. She treats it as she would a page she read: while it is fresh in the chat, the helpers she starts are born untrusted.

A question nobody answers within 24 hours expires, and the chat says so.

### Files in the answer

If the answer is a document ("send me the quote you saved this morning"), the other Jenny can attach it, up to three files of 20 MB each, both to an answer and to the reply to a request to do something. This is off by default. It is the point where a document leaves the work phone, so it is decided on that phone: **Answers can carry files**, per linked Jenny. With it off, the answer says the file exists but can't be sent.

A file arrives in pieces, each checked. One that arrives half or damaged is not shown.

## Asking it to do things

Asking is one thing; doing is another. With **Can ask me to do things** on, your personal Jenny can ask the work one to act on the work phone: "put a reminder there", "save this in the Clients notebook", "have a helper compare these three suppliers".

It is set on the phone that acts, per linked Jenny, at one of three levels:

- **No** (the default): requests to do things are refused. Questions work as above.
- **Yes, but ask me before sending anything out**: she does the work, but anything that would leave the work phone toward an address the model picks waits for you to tap **Allow** on a notification: opening a page, downloading a file, using the browser, network access from code, a command on one of your servers over SSH. The notification says what she wants and where ("Personal wants to open example.com"). **No**, or no answer within 10 minutes, means no, and she carries on without it and says so. If the work phone is asleep when the 10 minutes run out, the no comes when it next wakes, at the latest at its 15-minute check. Within one turn she asks once per site, and your answer holds for that site whatever tool she tries next; a helper working on the request asks for itself.
- **Yes, everything**: she does whatever she's asked, network included, without asking.

At every level she can't install an app update on request, and she never reads Jenny's own settings file (where the provider keys and tokens are) while answering or working for the other Jenny. A web search and a notification to you are not "sending out" here: a search only reaches the search engine, and a notification only reaches you. Jenny has no tool to email or message other people, so "email the supplier" can't be done at any level.

The work is done the way Jenny does any work: often by her helpers, who follow the same rules as the request. A reminder she sets on request rings in the work phone's personal chat, and when it rings it follows the rules of the level you have *then*.

### What came back, and undo

The card in the personal chat shows what she did on the other phone, listed by the app rather than written by the model: files changed, reminders added, pages opened. If her helpers are still at it, the card says so, and a second one follows when they are done.

Everything that changed on the work phone can be undone. **Undo** puts the files back as they were and removes the reminders the request created. It is in three places:

- the **done** notification on the work phone;
- the request's row in **Hands → Other Jennys** on the work phone;
- **Ask to undo** on the card in the personal chat, which works only while that phone still accepts requests to act.

A file that changed again after the request is left as it is, and the result says how many. Opened pages and anything already sent can't be called back.

## Talking to her directly

Asking is a relay: your Jenny puts the question, the other one answers, your Jenny tells you. Sometimes you want the other one yourself. Say **"pass me Work"** in the personal chat, or tap the **⇄** key next to the Plan key, and the line is hers: from then on what you type in that chat goes straight to the work Jenny, as if you were typing on the work phone. Your personal Jenny doesn't read it and doesn't answer.

You can tell the line is passed by three things. The row of pages at the top is replaced by the other Jenny's name with a back arrow; the field and the send key change colour; and the ⇄ key carries her name. The Plan key and attachments are gone while the line is passed: it carries text only.

**Her notebooks.** The ⇄ key opens **Who you're talking to**: your Jennys in a row at the top, and under the one that is lit, her chats — the main chat and her notebooks, by name. Tap one to talk there. In words it works too: once you are through to Work, "let's move to the suppliers notebook" moves you, and the head becomes *WORK › SUPPLIERS*. You see the conversation; her boards and pages stay on her phone. The same sheet, with your own Jenny lit, opens the chats of the notebooks on this phone.

**Coming back.** The back arrow, Android's Back, or `/torna` (or `/back`) typed as a message. They are recognised by the app itself, since your Jenny isn't reading. Choosing one of this phone's chats in the sheet comes back too.

**What stays here.** The conversation lives on the other phone, in her chat, where each of your messages is marked with where it came from. On this phone it leaves a marker — *You talked to Work · 10:02–10:15* — and, under it, two lines the work Jenny wrote about what was said and done. Your personal Jenny keeps those, so later she can tell you "you talked about that with Work" and ask her for the details. The two lines are the only part of the conversation that leaves the work phone; **Leaves a two-line summary**, on that phone, turns them off. Tapping the marker opens the line again.

**When she asks before using an app.** If what you ask needs one of the apps on her phone, the question — "to do it, Work has to use Mail on her phone" — appears in the conversation with its keys, as well as in a notification. It is written by the app, not by her model.

**What doesn't pass.** A step-by-step plan is started on the phone it runs on: over the line she can tell you how far it has got, not show its card. And from a passed line you can't be passed on to a third Jenny, or have her ask one: go back first.

**When she doesn't answer.** Your message waits, the head says *not answering*, and a strip above the field offers **Retry** and **Back to Jenny**. What you wrote leaves when she is back.

**The permission.** It is off until you turn it on, on the phone that *receives*: **Settings → Other Jennys → Can pass the line here** for that Jenny. Whoever holds the other phone can then do on this one what you could do by typing here, so turn it on only between phones that are both yours. Turning it off closes a line that is open.

The line can also be passed from Telegram: the other Jenny's replies arrive with her name in front, and `/torna` brings you back.

## A daily report

There is no separate report feature, because a reminder does it. In the personal chat: "every weekday at 7pm, ask Work how the day went and what's left to do". Every evening the question goes out and the answer arrives as a card, and your personal Jenny knows about the day without you asking.

The card arrives even on a quiet day. A monitor ("only tell me if Work has news") still shows Work's card when there's nothing new, because the answer doesn't go through the monitor's check.

## Your Mac, with opencode

The other side doesn't have to be a phone. If you use [opencode](https://opencode.ai) on your Mac, the `opencode-jenny` plugin links it the same way, so your Jenny can ask questions about your code: "is the CI of PR 85 green?", "what changed on main today?". opencode does the work on the Mac; Jenny stays the one you talk to.

- **Installing.** The plugin is not released yet: there is no package to install today, so don't run one found under that name. Its installer, on a Mac with opencode and Tailscale already there, asks which folder opencode may read and which model answers, installs the plugin, keeps opencode running in the background, and prints a QR in the Terminal. Scan it with the phone's camera and tap **Link**, as above; the Terminal then asks what you call the phone.
- **What it can read.** By default, one folder you choose when installing (by default `~/Documents/GitHub`), and only to read: `git status`, `log`, `diff`, and GitHub's PRs, checks and runs through `gh`. It can't edit files, run other commands or open web pages, and it refuses a request to do something. Each question is a session you can open in opencode to see what it did.
- **Letting it do things.** On the Mac, `opencode-jenny act free` gives it full access: whatever you ask Jenny to have the Mac do ("run `pmset -g batt` and tell me", "build the project", "commit and push"), opencode does, with any command or file and without asking anyone, and the answer says what it did. Nothing stands between what Jenny sends and the Mac: if she misunderstands, or something she read talks her into a request, the Mac does it. `opencode-jenny act off` goes back to read only. It is set on the Mac only, never from the phone, and nothing done there can be undone from the card.
- **Regular checks** are reminders, set in words: "every hour, ask the Mac whether the CI of PR 85 is green, and tell me only if it changes". Nothing to set up on the Mac.

The Mac is listed with the Jennys in **Who you're talking to**, with a laptop for its icon and a single chat under it: it has no notebooks. Passing it the line needs a version of the plugin that knows how; until then the line closes at once and says the Mac can't do it yet.

In the panel the Mac is marked *Mac · opencode*. It doesn't ask your phone anything, so it has no permission rows; and if something on the Mac did ask, your phone would refuse without looking at anything. The details of the link are in the [Peer protocol](../internals/peer-protocol.md).

## Permissions are decided by whoever receives

Each phone decides what the other may do *to it*: whether the other can ask questions (**Can ask me questions**, on by default), whether answers to it can carry files, whether it can ask for things to be done, and whether it can pass the line here. Nothing set on the personal phone opens anything on the work phone.

Both directions work the same way. The work Jenny can ask the personal one too, under the personal phone's rules.

Two more limits hold everywhere:

- **No ping-pong.** A turn answering the other Jenny, or doing something she asked, can't ask her anything in turn. A reminder created on request can't either.
- **A cap per hour.** Each Jenny answers at most 30 questions an hour from each linked one. Past that she says "too many" without running a turn.

## When the other phone is off

Every message waits in a queue on the phone that sends it, and is removed only when the other phone confirms it arrived, so nothing arrives twice. The phone retries after 30 seconds, then at growing intervals up to every 30 minutes, and checks the queue every 15 minutes even while the phone sleeps. **Test** in the panel tries again at once. The panel shows how many are waiting.

## After a restore

The secret the two phones share is kept outside the workspace. It is not in snapshots or in the encrypted backup, so a backup restored on a new phone, or after reinstalling Jenny, shows the other Jenny as **Link again**, and you link them with a new code. A restore on the same phone, with Jenny still installed, keeps the link. Android's phone-to-phone transfer, on the other hand, carries it like everything else.

When you link again, the other phone replaces its old row for this Jenny and keeps the name and permissions you had given it, as long as this phone is the same device on your tailnet. If the restore went to a different phone, the other phone keeps the old row too, which stays *Not reachable*: unlink it there.

## Security, in short

- The two Jennys talk on the Tailscale listener of [Jenny from your other devices](remote-access.md), on a path of their own that only accepts other Jennys, and only from your tailnet. A browser can't use that path, and another Jenny can't use the browser's.
- Every connection is proved both ways with the shared secret before anything is said.
- What arrives from the other Jenny is treated as data, not as orders, on both sides. The turn that answers starts marked as untrusted, and so does the answer once it lands: the same limits as a page read from the web.

The details: [Security model](../internals/security-model.md#other-jennys). The config keys: [`peers`](../reference/configuration.md#peers).

## Using the other phone

A request to act can include using the apps on that phone — "ask Work to read me today's mail". The acting phone still asks before using an app, and by default the question waits there. Turn on **She can confirm phone use from her phone** for a Jenny, on the phone that acts, and the question reaches you where you are instead. Details, and what a locked phone does, in [Using the phone](phone-use.md#from-another-jenny).

## See also

- [Settings](../reference/settings.md#other-jennys): the panel, field by field.
- [Scheduling and proactivity](scheduling.md): reminders and monitors.
- [Helpers: stop, undo, continue](helpers.md): the undo that requests to act use too.
