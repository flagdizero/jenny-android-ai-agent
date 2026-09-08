# Themes and mascot

Jenny's look — color theme, the on-screen mascot, and the interface language — is entirely personal to the phone you're using: none of it is backed up or synced.

## Themes

Open **Settings → Personalization** and you'll find a strip of theme cards under the label "Theme." Each card is not a swatch — it's a live mini-conversation dressed in that theme's actual colors, so what you see is exactly what you get. Tap a card to switch instantly; the app doesn't ask for confirmation and there's nothing to save.

There are seven named themes:

| Theme | Scheme | Feel |
|---|---|---|
| Chanel | Dark (default) | Black and white couture, a thread of gold only where it counts |
| Synthwave '84 | Dark | Neon pink on violet night |
| Jenny Kyoto | Dark | Earth, rust, hand-rounded edges |
| Jenny Sticker | Dark | Cut-out sticker look, white borders, hard shadows |
| Jenny Fumetto | Light | Ink on paper, comic-panel replies |
| Jenny Y2K | Light | Glossy gradients, bubblegum gloss |
| Jenny Pietra | Light | Travertine, bronze, Roman serifs |

Chanel is what a fresh install starts with. Whichever theme you pick, the app applies it before the very first paint on later launches, so there's no flash of the wrong colors.

On Android, the status bar and navigation bar follow your theme automatically — their background color and icon color (light or dark) are kept in sync with whichever theme is active, so a light theme like Jenny Pietra gets dark system-bar icons and a dark theme like Synthwave '84 gets light ones. This only happens inside the Jenny app itself; if you ever open the WebUI in a regular desktop or mobile browser instead of the Android app, the system bars obviously stay whatever your browser or OS already uses.

Your theme choice lives in the WebView's local storage on this specific device, not in `config.json`. That has two consequences worth knowing up front:

- It does **not** travel with an [encrypted backup](backup.md) — a `.jbk` file restores your conversations, memory, and settings, but not which theme card you last tapped.
- Reinstalling the app, or clearing the app's storage, resets the theme back to Chanel. This is expected, not a bug.

## Mascot

A small companion (Jenny, styled as "✿") lives docked at the edge of every screen except the main chat, where she's just present in the corner without any extra interaction — the real conversation is already open there. Tap her, or swipe her inward from the edge, and she pops out with a one-turn minichat: a text field ("Ask here…") and a speech bubble for the reply.

A few things about that minichat are worth knowing before you rely on it:

- Replies are capped at **280 characters** and stripped down to plain text — no markdown, no code blocks, no formatting survives.
- It talks over the **same conversation** as your main chat. Anything you ask the mascot lands in the same history the main chat sees, so a question you type into the minichat can come back up later if you ask Jenny to recall the conversation.
- The mascot gets **no automatic awareness of which screen you're on** — nothing about the current view is silently attached to what you type to her. Jenny does have a pull-based tool that can fetch the HTML of whatever's open (chat, Wiki, Workspace, apps, settings) when she decides it's relevant, but that tool works the same way regardless of whether you asked through the mascot's minichat or the main chat box — so talking to her isn't any blinder than talking in chat, it's just never handed screen context for free.

While waiting for a reply she switches between a "thinking" pose and, once text starts streaming back, a "talking" pose with an animated mouth. If you leave her alone reading a long reply she quiets back down into "thinking" after about a second of no new text.

Once a reply is done she reacts to it for a few seconds: **happy, sad or angry**. That reaction comes from a very small extra request made after the turn is over — the model is asked, in Jenny's own voice, how she feels about what she just wrote, and answers with a single letter. Nothing is added to your conversation or to Jenny's instructions, replies that end in an error or are only a few words long cost nothing, and a "nothing in particular" verdict shows no face at all. Errors make her sad on their own, without asking anyone. She reacts *after* speaking, never while: the question can only be asked once the reply exists. Two `config.json` keys govern it — `agents.defaults.mascotMood` turns it off, `agents.defaults.mascotMoodModelPreset` points the question at a cheaper model — see [Configuration](../reference/configuration.md).

Her face and her body are two separate drawings stacked on each other, which is why an expression can ride on top of any pose rather than being a pose of its own. It only works when she's out in the open: from the docked edge only one eye is visible, so there she wears the single drawn-in face she has always had.

Drag her instead of tapping and she takes flight: she hangs from your finger with a bit of pendulum physics, and on release falls, bounces, gets up, and walks back home to her docked position. It's a pure fidget interaction with no functional effect — dragging her doesn't send anything or change any setting.

Two preferences control her, both in **Settings → Personalization → Mascot**:

| Setting | Options | Default |
|---|---|---|
| Show mascot | on / off | On |
| Mascot size | Small / Medium / Large | Small |

Size is the side of the square she occupies — 120, 160 or 210 px; she starts small. The rest of her geometry follows from it, including where the minichat bubble sits relative to her head, so she stays coherent at every size rather than growing out of her own speech balloon.

Which edge she docks on is not a setting: it's where you last left her. She starts on the left, and after a throw (below) she lands on whichever edge is nearer and stays there.

The size control below the toggle stays on screen when she's switched off, greyed out and inert rather than removed. It reads oddly at first — a control you can see but not use — and it's on purpose: turning her off is exactly the moment you'd go looking for a way to keep her but calm her down, and hiding the option at that moment hid the answer to the question. Greyed-out is a promise about what you get back if you switch her on again.

She comes in one look, in color. There used to be a black-and-white switch here, drawn from a second set of line-art artwork; it was retired when her expressions were drawn, because keeping both meant drawing every expression twice.

Like the theme, these two preferences — and the edge she last landed on — are stored in this device's local storage — not in `config.json`, not in your encrypted backup. A reinstall brings her back showing, small, on the left.

If your phone has "reduce motion" turned on at the OS level, Jenny respects it: the animated mouth-flap while she talks is skipped in favor of a static pose. The drag-to-fly gesture itself is a direct manipulation you control with your finger, so it still works if you choose to use it.

## UI language

The interface exists in two languages: Italian and English. On first launch, before you've made any choice, Jenny reads the phone's system language and picks Italian or English if either matches (checking an exact match first, then just the language prefix); if neither matches, it falls back to English.

You can change it anytime from **Settings → Personalization → Language**, with a two-button segmented control (Italiano / English). Switching is instant — the whole interface re-renders in the new language with no reload and no restart.

This is worth separating clearly from a similarly named setting:

- **UI language** (what you just changed) lives in this device's local storage. It only controls what the buttons, labels, and toasts in the WebUI say.
- **`agents.defaults.language`** is a `config.json` field, and the *only* place that writes it is the onboarding wizard — it captures whichever UI language was active at the time you set the phone up, and uses it for a handful of backend-generated strings (like the initial welcome message).

Because of that split, changing the language toggle in Settings later does **not** touch `agents.defaults.language`. Any backend-generated text that depends on that config field keeps using whichever language you had during onboarding until you edit `config.json` directly — see [Configuration](../reference/configuration.md). Note also that several backend error messages (settings validation, model-list fetch failures) are hardcoded in English regardless of either setting.

None of this affects what language Jenny actually *replies* to you in during a normal conversation — that depends on the language model you're using and how you write to it, not on any toggle in this app.

## See also

- [Settings](../reference/settings.md) — the full accordion this page's controls live in.
- [Chat basics](chat.md) — how the main conversation renders.
- [Backup and restore](backup.md) — what does and doesn't travel in a `.jbk` file.
- [Configuration](../reference/configuration.md) — `config.json` reference, including `agents.defaults.language`.
