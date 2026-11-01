# Changelog

What changed in each release of Jenny, written for the person holding the phone. The
version numbers follow [Semantic Versioning](https://semver.org/): from 1.0 on, a change that
breaks something you rely on gets a new major number.

Releases before 1.0 are described only on their
[GitHub release pages](https://github.com/flagdizero/jenny-android-ai-agent/releases).

## [Unreleased]

### Removed

- `/goal` and the long-running goal behind it. Jenny could start one on her own and then keep
  going turn after turn with nothing on screen to say so, and the only way to stop it was
  typing `/stop`. Long work now goes through the two ways you can see: a step-by-step plan in a
  notebook, and helpers, each with its bubble and its Stop. A `/goal` typed now reaches Jenny
  as an ordinary message. On a phone that had it, the old `long-goal` skill is removed at the
  first start, unless you had changed it.

### Fixed

- After undoing a helper with one file, the chat says "1 file put back as it was", not
  "1 files put back as they were"; the same for the file it left alone.
- A one-time reminder that could not ring — the provider failed, or Jenny was not set up
  yet when its time came — is no longer dropped: it is retried every five minutes for up to
  a day past its time, and only then given up, with an error in the log.
- Notebook reminders written before 2.0.0's successor (`board/reminders.json`) are moved
  into the scheduler on the first pass of the notebook clock, so the open ones keep
  ringing; the file is kept beside them as `reminders.json.migrated`.
- The periodic checks written in `HEARTBEAT.md` no longer stop in silence: the first time
  a scheduled job runs after the update, Jenny tells you in chat which of them no longer
  run and asks which ones you want back as recurring checks, and how often. The file is
  kept as `HEARTBEAT.md.retired`.
- A paused reminder is shown as paused on its post-it and in the care calendar, and no
  longer lights a card up as "coming up".
- Reminders written from the board (the notebook editor, the board pass) carry the turn's
  taint like the ones written with the `cron` tool; moving a reminder to another card
  keeps who wrote it.
- The workshop's job list tells a one-time job from its schedule, so one-time jobs from
  an older store no longer show a "next time".

## [2.0.0] — 2026-10-30

Four weeks and about 300 changes since 1.0. A notebook becomes a board you arrange, a big job
in a notebook can run as a plan you approve step by step, and Jenny hands the heavy work to
helpers you can stop and undo. Some things you may rely on changed or went away: they are
listed at the end, and they are why this is 2.0.

### Highlights

- **Every notebook has a board.** A wall that scrolls, grows and zooms with two fingers, with
  a card for each subject. A card opens a drawer with its pages, its apps, its reminders and
  its automations. Notes hang beside a card as a deck of lilac post-its, photos as a deck of
  polaroids that Jenny can see. Reminders for the whole notebook are a free yellow sticky
  note, automations a mint label. The board is yours: Jenny proposes, you move.
- **Plans.** The Plan button in a notebook's chat starts a planning: Jenny reads the
  notebook, asks what she needs, and proposes a list of steps, with what the web will have
  to fill in. Nothing moves until you approve it. Then the steps run one at a time, the
  notebook is photographed before it starts, and you can pause, stop, or put it back as it
  was before the plan. A plan ends with a check and a list of what it could not find.
- **Helpers you can stop and undo.** Jenny's helpers show up in the chat as her mini-mes.
  From a helper's card you can stop it, and put back every file it changed that nobody has
  touched since. A helper interrupted by a restart, by the app being closed or by the
  provider offers to continue from where it was.
- **Jenny from your other devices.** A switch in the workshop (Hands → From other devices)
  lets the Mac or a second phone talk to Jenny over Tailscale while the phone stays in a
  drawer. The phone keeps up with what you write there, and does not ring for it.
- **Jenny reads the screen.** With her accessibility service on (Android 14+), a tap on the
  floating mascot gives her one read-only look at the app underneath, text and picture.
  Apps that block screenshots (banking, password managers, incognito windows) are left out
  entirely. The reading stays in memory for ten minutes, and only a picture she asks for
  is saved among your files.

### Home and chat

- **Jenny is the door to the chat.** Wherever the chat is not on screen, tapping the mascot
  brings the real chat up full screen over what you are looking at, with the page still
  visible beneath, blurred; tap her again or press Back to close it. She can see that page,
  so she can explain a menu, a setting or a mini-app you have in front of you. On the chat
  page and the Console a tap makes her wave. This replaces the one-turn minichat in both
  shells.
- **The field you write in is made of glass**, in every theme and each in its theme's style:
  over a page you see its colours through it. Only your own messages have bubbles; Jenny's
  replies run on the page, with the room they need. In Kyoto your messages now have rounded
  corners like the other themes.
- **A notebook always opens on its board**, from the Notebooks page and from a notebook
  pinned as a page. Its chat is behind Jenny; the Chat | Board switch is gone.
- **A new notebook opens on its chat, and Jenny speaks first**: a short greeting about what
  the notebook is for, ending with a question to get you started. Creating one asks only
  its name and what it is about.
- **A notebook's notification opens that notebook**, on its board with its chat on top,
  instead of the personal chat. Each notebook has its own notification, titled with its
  name, and opening one no longer clears the personal chat's alerts you have not read.
- A **pink dot** on Jenny says a reply arrived while her chat was closed.
- Every message shows when it was sent. Whoever is working shows up as a line in the
  thread, one at a time, and a helper's report reads as one reply, also after a reload.
- Jenny squashes, stretches, walks and waves with her real frames, in the app, in the
  onboarding and floating over other apps.
- The house keeps your place: reading point, drafts, open drawers and doors survive leaving
  the app and a WebView that starts over. An open page reloads itself after an update.
- Alerts ring unless you are reading the chat. A reminder made in the chat rings with Jenny
  in front.
- No blue flash on tap anywhere; every button answers the touch itself. Deletes are red,
  and sheets that are too long scroll.
- The Notebooks page is a bento of covers in your theme's colours, its big tile the
  thickest notebook. The App page is a grid of icons.

### Notebooks

- **A notebook's context is its board**, on every turn. A new notebook is born with two
  questions and an empty board; a research notebook with a journal gets a board proposed.
- **Reminders and automations belong to a card or to the whole notebook**, and Jenny
  chooses which. An automation's rhythm is said in words. A renamed notebook takes its
  scheduled jobs along, and closed reminders become history.
- **The notebook editor.** Changes to a notebook's pages are made by a dedicated helper;
  the gardener only tends it. A notebook keeps every summary of what it archived, and its
  id survives any write.
- The personal chat keeps a diary of what happened in the notebooks, knows what is still
  waiting, and can offer to move a conversation into a notebook.
- When a notebook is not enough, Jenny searches the web, and writes pages for the reader
  with their sources.

### Apps

- **An app exists as soon as it works.** There is no trial and no Keep / Discard: Jenny
  checks the files and runs the app once, and only then replaces the live one.
- **Apps are built by a helper**, and a follow-up goes back to the helper that knows the
  app. An ambiguous request is asked about first.
- **Strips.** An app shows a summary of a few blocks (chips, a progress bar, a list, a
  table, its latest photos) in four sizes, on the board and in the drawer. An offline
  sensor turns its strip red.
- Jenny builds apps from a notebook too, and an app can sign in through a skill.
- A long press on an Android app opens its sheet again.

### Memory and the agent

- **Nothing is deleted from the history any more.** What goes past the cap moves to a
  monthly archive, and `recall` and `recall_history` search it by word, in pages, saying
  what was later corrected or retracted.
- **The conversation is compacted before it outgrows the window**, and a long task
  compacts itself instead of running out of context.
- Jenny speaks to you in your language, slash commands included, and when asked to
  remember or forget something she says when it will happen.
- **Jenny has her own reasoning setting, low by default.** She delegates the thinking to
  helpers, so she answers sooner; the helpers keep the general setting. Change it in the
  workshop's Parameters panel.
- Every helper has its own browser; web pages that need one are read through it, and
  search and fetch give up sooner on a dead engine.
- Claude 4.7 and later get adaptive thinking; Anthropic and DeepSeek requests reuse the
  provider's cache better. OpenCode Go picks the right endpoint for each model by itself.

### Security

- **Content Jenny did not write is tracked.** A helper that reads the web, and whatever it
  writes, is untrusted; what Jenny starts with an untrusted result in view is born
  untrusted. An untrusted helper sends data only to the sites its task names, and its
  changes to files Jenny loads every turn wait as drafts for you to install with one tap.
- Helpers never touch `config.json`, and `python_exec` sockets obey the SSRF policy.
- **Android's own cloud backup takes nothing from Jenny.** It could have taken
  `config.json`, provider keys included; the phone-to-phone transfer at setup still works.

### Backup and builds

- Local history restores right from the home's Backup page.
- **Two builds from the same code.** The GitHub APK (`foss`) contains no Google library.
  The Play Store build adds an encrypted copy on Google Drive.

### Changed in ways you may notice

- **The minichat is gone**, in both shells: the mascot opens the real chat instead.
- **The mascot cannot be hidden.** The setting that switched her off is gone.
- **A notebook has no Chat | Board switch.** It opens on its board, its chat behind Jenny.
- **Apps have no trial.** `apps.keep` and `apps.discard` are gone; an app left on trial by
  1.0 is promoted at the first start.
- **Micro apps became apps.** Every notebook micro app is moved into a general app at the
  first start, and the drawer's micro app section is gone.
- **The WebSocket accepts only typed envelopes.** A plain-text frame, or one with only
  `content`, `text` or `message`, now gets `{"event": "error", "reason": "untyped_frame"}`.
  Send `{"type": "message", "content": "..."}` instead: see
  [WebSocket](docs/reference/websocket.md).
- **The context window defaults to 128K.** An install still at the old 65,536 moves to
  131,072 once, with a warning. A model with a smaller window is still handled.
- **Web search waits 15 seconds, not 30.** An install still at 30 moves to 15.
- **Android's cloud backup is empty for Jenny.** If you counted on it, use Jenny's own
  backup instead.

## [1.0.0] — 2026-10-02

The first stable release. Over 800 changes since 0.11.0. The biggest one is a new home
screen built around the conversation, and the old interface becomes the workshop behind it.

### Highlights

- **A new home.** Jenny opens on a home of swipeable pages: Apps, Jenny (the chat),
  Notebooks and Settings. A row of page names at the top replaces the drawer button and the
  dots. You can pin a Jenny App or a notebook as a page of its own, from where it lives.
- **The workshop.** The previous interface lives on behind Settings, regrouped into four
  drawers: Console, Brain, Hands and Memory.
- **Jenny outside the app.** She can float above other apps and you can talk to her there,
  in a short conversation with rendered Markdown. You can also reply to her straight from the
  notification shade, and the shade shows the exchange as one conversation.
- **Notebooks in the home.** A notebook opens on its own chat, its pages and its map. From
  the reader you can edit a page and report a passage, and the report lands in that
  notebook's chat.

### Home and chat

- Chat text can be selected and copied, and a Copy button sits under every reply.
- Formulas and diagrams render in both shells.
- Older history loads page by page in both shells, and a reply that is streaming stays
  below the history that loads above it.
- A message the gateway refuses comes back to the composer, with the reason in your
  language, instead of disappearing.
- Videos are recognised as videos before they are sent.
- The minichat opens wherever the chat is not on screen, in both shells.
- The status bar and the working line wear Jenny's flower, and the working line stays lit
  through tool runs and subagent waits.
- Jenny's mood comes from the emoji in her reply. The model is not asked for it.
- The assistant carries the name you gave her. The app itself stays Jenny.

### Themes and look

- Synthwave '84 is now near-black and the default theme. Its own bubble is solid pink with
  white text.
- The loading screen and the system splash wear the last theme you chose.
- The theme is picked on Settings, where you can see it.

### Notebooks and wiki

- Creating a notebook asks for its name and then for one line on what it is about. A wrong
  name keeps the dialog open and says the rule first.
- A notebook can be renamed or deleted from its sheet. Both are refused while Jenny is
  still working in it, and the refusal says so in your language.
- The map keeps a dot where you drag it, follows a notebook's rename and leaves with its
  deletion.
- The audit format drops its severity field, in the skill too.

### Workshop

- **Brain** holds the providers as brands with their models. One tap on a model makes it
  answer.
- **Hands** shows what Jenny does for you: skills, scheduled jobs, Telegram and SSH. A
  scheduled job of yours can be paused, resumed or removed from there.
- **Memory** holds the file manager. Delete, rename and copy run as WebSocket commands and
  never overwrite.
- The first-run wizard is now a page of its own.

### Providers and models

- OpenCode Go is supported.
- A provider's base URL is checked before it is saved.
- A provider key or a Telegram token saved from Settings takes effect at once, without a
  restart.
- Streams are more robust:
  - a dropped keep-alive or a reset connection is retried;
  - an interrupted or error-ending stream is reported as an error, not as a complete reply;
  - a retry does not repeat the thinking and tool output already shown;
  - the wait announced by a provider's Retry-After is capped and shown to you.
- The configured temperature reaches the models that accept it. The Anthropic thinking
  budget fits inside the configured max tokens.

### Memory, Dream and the agent

- Dream reads every diary entry whole, keeps up when the chat and a notebook take turns,
  and no longer loses the conversation when a consolidation call fails.
- After `/stop` or a crash the history keeps the whole turn.
- Rules you give Jenny survive the night. Dream no longer erases them or counts them
  against its budget.
- Each conversation has its own `python_exec` globals, and waiting on a `python_exec`
  session no longer freezes the gateway.
- Subagent and consolidation calls count in the token usage.

### Telegram, SSH and the browser

- Attachments sent on Telegram reach Jenny.
- Telegram pairs only from a private chat, and after pairing it listens only to that
  person. Unpairing asks first.
- SSH works with ECDSA and large RSA host keys. Downloads stop at the byte limit and never
  overwrite a partial file.
- The agent's browser blocks the same private and local addresses as the Python network
  guard, including WebSocket, WebRTC and service-worker traffic.

### Backup, updates and settings

- A large backup exports without the 10-second cut and without holding the whole file in
  memory.
- Back closes the backup passphrase dialog instead of the screen beneath it.
- Updates says you have the latest version only after a check that actually worked.
- `config.json` is private from the moment it exists. A value this version does not know
  costs only its own field, and a settings save resolves `${VAR}` references like a start
  does.
- Onboarding sends the first API key over the WebSocket, never in a URL.

### Accessibility

- Every word in every theme reaches a contrast of 4.5:1, and keyboard focus shows in every
  theme.
- Buttons have real accessible names, and small close buttons answer on 24 px or more.
- The workshop dock can be reached from a physical keyboard.

### Changed in ways you may notice

- **The WebUI follows the phone's language.** An old saved language choice is no longer
  remembered.
- **Built-in skills cannot be switched off.** Skills now say whether they ship with the
  app.
- **Cron expressions are evaluated by Jenny itself.** The `croniter` dependency is gone. An
  expression that would never fire is refused when the job is added, and an interval of zero
  or less is refused too.
- **The all-notebooks graph is gone.** The `/api/graph` route now needs a notebook name.
- **Skills can no longer be deleted over HTTP.**
- **Saved home pages and preferences are migrated.** The interface's vocabulary was renamed
  to English, and saved pages, theme ids, mascot keys and workshop drawers carry over
  automatically.
