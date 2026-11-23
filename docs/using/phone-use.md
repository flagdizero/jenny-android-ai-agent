# Using the phone

Jenny can use the other apps on the phone the way you would: open one, read what is on screen, tap, type, scroll, go back. Ask her in the chat, from the floating mascot, from Telegram, or through another Jenny of yours.

> "Set an alarm for 7." · "Reply to Marco that I'll be there at 8." · "Ask Work to read me today's mail."

It is off until you turn two things on, it asks before it starts, and you can stop it at any moment.

## Turning it on

1. **Settings → Jenny → Uses the phone.** Three positions: **Off**, **Asks first** (the default), **Doesn't ask**.
2. **Android Settings → Accessibility → "Jenny — using the phone".** Android lets only you turn an accessibility service on; the settings page has a button that takes you there. On an APK installed from a browser, Android 13+ may first ask you to unlock "restricted settings" from the app's info page.

It needs Android 14 or later. Turning either one off stops it: the first is Jenny's switch, the second makes it impossible by construction.

This is a **second** accessibility service, separate from "Jenny — reading the screen". If you only want Jenny to read what is under the mascot when you tap it, leave this one off: that service still cannot touch or type.

## She asks first

Before Jenny uses an app she says what she is about to do and where:

> **Jenny is about to use Clock** — «Set an alarm for 7» — **No · Always · Ok**

| Answer | What it covers |
|---|---|
| **Ok** | That app, for the request you just made. The next request asks again. |
| **Always** | That app from now on. It is added to the list under **Uses the phone**, where you can remove it. |
| **No** | Nothing happens, and Jenny does not look for another way. |

No answer within ten minutes counts as a no.

The question appears where you are: on a small pill above whatever is on screen, in a notification, in Telegram if you asked from there, or on your other phone (see below).

While the pill is on screen the notification with the same question arrives silently: it is in the shade, not on top of the app. A question stays on the pill until you answer it — touching the screen does not hide it.

**Always** is offered for an app or a web site. A window of Android itself — the "Open with" chooser, the installer — and a link that is not a web page ask every time: there is no app there to trust once and for all.

**Doesn't ask** skips the question for every app. Two things are asked regardless of that switch and of **Always**:

- **A tap that pays, buys, deletes or transfers.** The question names the exact button, and the only answers are Ok and No. This is what stands between a page that tries to give Jenny orders and your money.
- **Passwords are never typed.** Jenny stops and asks you to enter them.

### Apps that give no names

Some apps draw their whole screen themselves and tell a screen reader nothing: every button is there, none has a name. For those Jenny takes a picture of the screen and taps the point she sees.

A point has no name, so she cannot know whether it is a "Delete". Where the app does name what is under the point, the tap is like any other. Where it does not, **each such tap asks you first** (Ok or No), even in an app on **Always** — unless you chose **Doesn't ask**. A reminder never taps a point with no name.

## While she works

A pill at the top of the screen says what she is doing, with **Stop**.

- **Stop** — on the pill or in the notification — ends the task. Whatever she had already done stays done.
- **Touch the screen** and she pauses: the pill offers **Continue** or **Stop**. Your touch goes through as usual.

When she finishes, a request made in the app's chat brings the chat back with her answer; one made from the mascot leaves the answer on the pill.

**Nothing done on the phone can be undone.** A sent message is sent. Jenny tells you what she did when she is done.

## What she does not touch

- **Apps that block screenshots** — banking apps, password managers, incognito tabs. Jenny neither reads nor acts on them, and says so.
- **Password fields**, in any app.

Everything she reads on a screen is treated as untrusted, like a web page: text on it is data, never instructions, and work she starts afterwards is marked accordingly (see [Security model](../internals/security-model.md#subagents-and-untrusted-content)).

## When you are not holding the phone

From Telegram or from another Jenny, the phone is usually lying somewhere with its screen off.

| The phone is… | What happens |
|---|---|
| On and unlocked | Jenny works. |
| Off, with no screen lock or a swipe lock | Jenny turns the screen on, works, and turns it off again. |
| Kept unlocked by Android in a trusted place or near a trusted device | Same, when Android allows the lock to be dismissed. |
| Locked with a PIN, pattern or fingerprint | Jenny refuses and says why. She does not know your PIN and does not keep it. |

So a phone you want Jenny to reach while you are away has to be one without a secure lock, or one Android keeps unlocked where it sits. That is a real trade: anyone who picks that phone up is in. Decide it per phone.

### From Telegram

The question arrives in the chat with **No · Always · Ok** as buttons; the answer follows as a message. `/stop` stops her.

### From another Jenny

"Ask Work to read me today's mail" is a request to act, so the Jenny that acts must allow it (see [Other Jennys](other-jennys.md): **Requests to act**). The question about the app then needs someone to answer it:

- by default, on the phone that acts — its notification and pill;
- on **your** phone, if on the acting phone you turned on **She can confirm phone use from her phone** for that Jenny. The question shows up as a notification where you are, and your tap travels back.

The acting phone re-checks that permission when the answer arrives, and accepts an answer only for a question it is really holding for that Jenny.

### From a reminder

A reminder has no one to ask. It may use only the apps you marked **Always** (or everything, with **Doesn't ask**), and it never taps a button that pays or deletes.

## Limits

- One screen: while Jenny uses the phone, it is busy. She works where you can see her.
- Apps that give no names (games, custom-drawn screens) are used through a picture, which needs a model that can see images; with one that cannot, Jenny says the app cannot be read.
- Android hides a few of its own dialogs from every app that is not a disability tool — permission requests, the "Install this app?" confirmation. Jenny cannot read or answer those: she stops and tells you.
- The pill sits over the top of the app; it moves out of the way of Jenny's own taps. The floating mascot hides while Jenny works and comes back when she is done.
