# Skills

A skill teaches Jenny how to do something in chat — reminders, self-diagnostics, building an app for you — without any screen of its own.

## Skills vs. mini-apps

If a capability needs its own screen, it's a [mini-app](mini-apps.md). If it only ever lives inside the conversation, it's a skill. Setting a reminder, learning a new procedure, or building a new Jenny App are all skill-driven; none of them need a UI beyond chat.

## How skills are loaded

Each skill is a folder at `workspace/skills/<name>/SKILL.md` — a markdown file with a frontmatter header plus instructions. Jenny loads skills progressively: only a short summary (name, description, path) sits in the model's context at all times, and Jenny reads the full `SKILL.md` on demand when a task calls for it. This keeps the context small while still giving Jenny access to dozens of skills.

To see what's currently enabled, type `/skill` in chat. It lists every enabled skill with its description — this is the fastest way to check whether a given capability is actually turned on.

## Seeing and switching skills in Hands

Skills live in the workshop's **Hands** drawer — the one that answers *what can she do* — in their own group, just before the jobs that start by themselves. They are not in the launcher drawer: a skill isn't something you launch, so it isn't offered as one.

In the drawer a skill group is a single summary row, **What she knows · 5 built in, 2 yours**. Tapping it opens a panel with two blocks:

- **Yours** — skills you (or Jenny, on your behalf) created. Each row shows the name, one line of description, and a **toggle switch**. Turning it off writes `disabled: true` into the skill's frontmatter; from the next turn Jenny no longer sees that skill in her context, until you turn it back on. The choice survives a restart.
- **Built in** — the skills that ship with the app. They carry a **lock** instead of a switch, and a short description written for you rather than for the model.

Tapping a row's text shows the rest of its description when it's longer than two lines. The panel reads the list again every time it opens, so a skill Jenny wrote a minute ago is already there.

Switched off and unavailable are two different things, and the panel shows them separately:

- **Switched off is a decision** — yours, reversible on the spot, and it's the switch.
- **Unavailable is an impediment** — the skill *cannot* run, because it is missing a tool, a key or a file. It takes the line under the name, in a warning colour and with the actual reason in words, because the reason is the only thing you can act on. Switching the skill on would not make it work.

A skill can be both at once, and then it shows both.

There is deliberately **no editing, deleting or creating** in the panel. To teach Jenny something new, or to change a skill, ask her in chat. If you have no skills of your own yet, the panel offers **Ask Jenny**, which writes `I want to teach you a new skill. Use the "skill-creator" skill and guide me step by step.` into the message box, without sending it, so you can finish the sentence.

## Three kinds of skill

A skill's frontmatter and origin put it in one of three groups:

| Kind | Where it shows | Examples |
|---|---|---|
| **Yours** | Listed under *Yours*, with a switch | Skills you or Jenny create |
| **Built in** | Listed under *Built in*, with a lock | `cron`, `app-creator`, `skill-creator`, `llm-wiki`, `ssh` |
| **Internal** | Not listed, only counted: *"Plus 5 internal ones Jenny uses on her own."* | `memory`, `my`, `http-client`, `data-processing`, `long-goal` |

Built-in skills are core parts of how Jenny works (scheduling, building apps and skills, the wiki, remote machines over SSH). Internal skills are plumbing you're unlikely to ever need to touch directly (self-awareness bookkeeping, low-level HTTP/data helpers). A skill of your own whose frontmatter says `locked: true` is listed under *Yours*, but with a lock instead of a switch.

## Why built-in skills have a lock

**The skills that ship with Jenny are re-extracted from the APK every time the app starts**, overwriting whatever is in their folder under `workspace/skills/`. A switch on one of them would work only until the next restart, then be silently undone. So there is no switch, and the gateway refuses to change or delete a built-in skill (it answers `403`).

Your own skills live in the same folder but are never touched by the start-up extraction, so turning one off, or asking Jenny to change or delete it, sticks.

If you genuinely don't want a built-in capability used, ask Jenny not to use it.

## See also

- [Mini-apps](mini-apps.md) — when a capability needs a screen instead.
- [Slash commands](slash-commands.md) — `/skill` and other chat commands.
- [Tool reference](../reference/tools.md) — the built-in tools that back skills like `cron`, `my`, and the file/python tools.
