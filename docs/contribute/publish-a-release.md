# Publishing a release

How a new version of Jenny gets out of this repository and onto the phones that already have
it installed. This page is for whoever is doing the publishing — it assumes you have the
signing keystore and push access. It does not explain how the in-app updater is coded, but it
does describe [what the person holding the phone ends up seeing](#what-the-user-actually-sees),
because that is what you are deciding when you fill in the manifest.

**The first version that ships the updater still has to be installed by hand.** An app that
does not know how to look for updates will never find one. The whole mechanism described here
starts working from the *next* release onwards: everyone running an older build has to
[sideload the APK](../start/install.md) once, the normal way, and only after that does Jenny
start offering updates to herself.

## The short version

```bash
# 1. bump every place the version lives
python3 scripts/release.py 0.7.0

# 2. build and sign the APK (needs your local keystore)
cd android && ./gradlew app:assembleRelease && cd ..

# 3. hash the signed APK and generate the manifest
python3 scripts/release.py 0.7.0 --manifest-only \
    --apk android/app/build/outputs/apk/release/app-release.apk \
    --summary-it "Aggiornamenti automatici e correzioni varie." \
    --summary-en "Automatic updates and assorted fixes."

# 4. publish — the script printed this command, it never runs it for you
gh release create v0.7.0 dist/release/jenny-0.7.0.apk dist/release/latest.json \
    --repo flagdizero/jenny-android-ai-agent \
    --title "Jenny 0.7.0" --notes "Automatic updates and assorted fixes."
```

Add `--dry-run` to any `release.py` invocation to see exactly what it would do — every file it
would touch, the manifest it would produce, the commands it would print — without writing a
single byte.

## Why there is a script at all

The version number lives in four places that have to agree, and nothing in the build catches
a mismatch until it is already on someone's phone:

| Where | What |
|---|---|
| `pyproject.toml` | `version = "0.7.0"` — the Python package version |
| `jenny/__init__.py` | the hardcoded fallback the app reports on Android, where package metadata does not exist |
| `android/app/build.gradle.kts` | `versionName` (what the user sees) **and** `versionCode` (the integer Android compares) |
| `latest.json` | the update manifest, which has to describe the APK you actually built |

`scripts/release.py` is the only thing that touches all four together. It rewrites the three
source files with anchored patterns and refuses to write anything at all if a pattern matches
zero times or more than once — if someone moves `versionCode` into a build flavor, you get an
error, not a half-edited Gradle file.

Two rules it enforces, both worth knowing before you fight with it:

- **The version has to go up.** `0.7.0` after `0.6.6` is fine; `0.6.6` again, or anything lower,
  is refused. Pre-release suffixes (`0.7.0-dev`) are fine in the working tree but cannot be
  published — and promoting one is the ordinary case, not an exception: with the tree at
  `0.7.0-dev`, releasing `0.7.0` is accepted and drops the suffix everywhere.
- **`versionCode` only ever increments by one.** Android will not install an APK whose
  `versionCode` is lower than the installed one, and there is no way back down. This is the
  number the updater actually compares; `versionName` is decoration for humans.

The script never publishes. It prints the `gh` commands and stops. Uploading an asset is a
deliberate act, not something that happens because you typed a version number.

## Step by step

### Before you start

- The working tree should be clean and on the commit you intend to ship.
- You need the release keystore, either as `android/keystore.properties` or via the
  `JENNY_KEYSTORE_*` environment variables — see
  [Environment variables](../reference/environment-variables.md). CI does not publish releases
  precisely because it does not have the key; the whole thing happens on your machine.
- You need the `gh` CLI, authenticated against the repository.

### 1. Bump the version

```bash
python3 scripts/release.py 0.7.0
```

Read what it printed, then commit the four changed lines. Running `pytest -q` here is worth the
30 seconds: there are tests asserting that the version files agree with each other, and they
will tell you immediately if something was left behind.

Two strings the script does **not** touch, because they describe the published asset rather than
the version: the APK filename and its approximate size in `README.md` and
[`docs/start/install.md`](../start/install.md). Both are instructions a reader copy-pastes, so a
stale one sends them to a file that isn't there. Update them once the signed APK exists and you
know its real size — step 3 prints it.

### 2. Build and sign

```bash
cd android && ./gradlew app:assembleRelease
```

The result lands in `android/app/build/outputs/apk/release/app-release.apk`. If the build prints
a warning about missing signing credentials, stop — an unsigned APK is not installable, and
publishing one means everybody who tries to update gets an error.

**The signing key must be the same one the installed build was signed with.** Android refuses
an in-place update signed by a different key; the user's only way out is uninstalling, which
takes their workspace with it. This is the one mistake in this whole process that cannot be
fixed by publishing a corrected release.

### 3. Generate the manifest

```bash
python3 scripts/release.py 0.7.0 --manifest-only \
    --apk android/app/build/outputs/apk/release/app-release.apk \
    --summary-it "…" --summary-en "…"
```

`--manifest-only` skips the bump (already done in step 1) and reuses the `versionCode` that is
in the tree. The script hashes the APK, writes `dist/release/latest.json`, and stages a copy of
the APK named `jenny-0.7.0.apk`, because GitHub names an asset after the file you upload and the
manifest's `apk_url` has to match it exactly. `dist/` is gitignored, so nothing it stages ends
up in a commit.

The two summaries are one-liners shown inside the app when the update is offered — write them
for the person holding the phone, not for the changelog. Both are required: Jenny picks one
according to **her configured language** (Settings → language, i.e. `agents.defaults.language`),
not the device locale, and falls back to `summary_en` if the matching one is missing. Anything
past 400 characters is truncated, so keep them to a line.

### 4. Publish

Run the `gh release create` command the script printed. It attaches **both** the APK and
`latest.json` to the tag.

The `--notes` it printed already contain the **verification block** — sha256, exact size, and the
`shasum` and `apksigner` commands. Keep it in the body when you expand the notes by hand: the
README tells people to verify the download against the hash *published on the release page*, and
that sentence is only true while the block is there. The script prints it separately too, so you
can paste it into a body you wrote in the GitHub UI.

The manifest must be attached to the release under exactly that name, because the client fetches
it from GitHub's stable redirect:

```
https://github.com/flagdizero/jenny-android-ai-agent/releases/latest/download/latest.json
```

GitHub resolves `/latest/` to whatever the most recent non-draft, non-prerelease release is. That
is the entire discovery mechanism — there is no server, no API key, no endpoint to keep alive. It
also means a release marked as a **pre-release** or left as a **draft** is invisible to the
updater, which is a useful way to stage an APK without offering it to anyone.

### 5. Check it

Fetch the URL above and confirm you get the manifest you just wrote. Then, on a phone still
running the old build, wait for the next update check and confirm it sees the new version.

There is no "check now" button: the check runs on a schedule (`updates.checkIntervalH`, 24 hours
by default) and nowhere else, so on a freshly published release you may be waiting up to a day.
To see it immediately, lower the interval in `config.json` and restart the gateway — the job is
registered at startup, so the new interval does not apply until then.

## What the user actually sees

Publishing a manifest does not install anything. It puts a file where phones look, and from there
three things happen on their own.

**A message in chat, once per version.** The `update_check` cron job runs every
`updates.checkIntervalH` hours (24 by default), and the first time it finds a version this device
can take, it has Jenny say so in her own words, in the conversation — the version number, your
summary, and a question about installing now. It is recorded as announced at that point and never
brought up again for that version, however the user answers. If `updates.notifyInChat` is off, this
step is skipped entirely and nothing is recorded, so turning it back on still gets the
announcement.

**A badge in Settings.** The version row grows a *New* pill (*Security* when the release is
`critical`), a line with your summary, a *What changed* link to `notes_url`, and an *Install now*
button. This is not a second check — it reads the same cached result the scheduled check wrote —
but it is the part that survives a missed message, and it is where a user who said "later" comes
back to.

**Two tools.** The user can ask in chat: `update_status` reports what is available and how far an
installation has got, and `install_update` starts one — only after they have explicitly asked for
it in that conversation.

For a `critical` release there is also a system notification, so it lands even with the app closed.

### What installing looks like

Jenny downloads the APK, checks its SHA-256 and its exact size, and hands it to Android's
`PackageInstaller`. From there, one of two things happens, and **which one is not up to us** —
it is up to the Android version, the ROM and who owns the package:

- **Unattended.** The system accepts the update without asking. The user sees nothing at all: the
  process is killed mid-sentence, the app is replaced, and Jenny comes back up by herself a few
  seconds later. The WebUI says so in advance, because the connection dropping would otherwise
  look like a crash.
- **With a confirmation.** The system refuses to install unattended and returns its own installer
  screen. If the app is in the foreground that screen opens; if it is not — the usual case for a
  phone in a drawer — it is posted as a high-priority notification instead, on its own channel, and
  waits. **Nothing is installed until the user taps Install.** An update can sit in that state
  indefinitely.

The second path is the normal one to expect, not an error branch: an APK you sideloaded is not
installed by us, and unattended self-update is a concession the system may withhold. Neither path
is knowable in advance — the answer only arrives at commit time.

One consequence worth internalising before you read a log or a bug report: **"committed" is not
"installed".** When Jenny reports the unattended path she is saying the system accepted the
session, not that the new APK is running. The only proof of a completed update is the app coming
back on a higher `versionCode`. A release that fails verification after the commit fails after
Jenny has already stopped being able to tell you.

## What each manifest field means

```json
{
  "schema": 1,
  "version_code": 9,
  "version_name": "0.7.0",
  "apk_url": "https://github.com/flagdizero/jenny-android-ai-agent/releases/download/v0.7.0/jenny-0.7.0.apk",
  "sha256": "05592b9d8bc11f615c6217a942854399b9d8db5bbba29d69dfb3163cd7e696fc",
  "size": 2097152,
  "notes_url": "https://github.com/flagdizero/jenny-android-ai-agent/releases/tag/v0.7.0",
  "summary_it": "Aggiornamenti automatici e correzioni varie.",
  "summary_en": "Automatic updates and assorted fixes.",
  "min_supported_code": 0,
  "rollout": 100,
  "critical": false
}
```

| Field | Meaning |
|---|---|
| `schema` | Version of this manifest format. A client that does not recognise the number ignores the manifest rather than guessing. Bump it only on a breaking change to the format, never for a normal release. |
| `version_code` | The integer Android compares. A device updates only if this is **higher** than its own. This is the field that decides whether an update exists. |
| `version_name` | The human-readable version, shown in the update prompt. Purely cosmetic. |
| `apk_url` | Direct download URL for the signed APK asset. Must point at a file actually attached to that tag. |
| `sha256` | Hex digest of the APK. The client verifies the download against it and refuses to install on a mismatch, which is what makes a corrupted or substituted download fail loudly. It is *not* the main defence against a hostile APK — whoever controls the manifest controls the hash written in it. That job belongs to the signature: an in-place update whose certificate does not match the installed package is rejected by Android outright. |
| `size` | APK size in bytes, and a load-bearing one. Before downloading, the client refuses to start unless there is room for twice this plus a 64 MB margin — the staging copy needs its own space, and discovering that halfway through is how you fill a phone's cache and fail silently. It also rejects a mismatched `Content-Length` up front and a truncated file at the end. |
| `notes_url` | Where "what's new" points: the GitHub release page for the tag. |
| `summary_it` / `summary_en` | One-line summary shown in the app, picked by device language. |
| `min_supported_code` | Oldest `versionCode` still allowed to take this update automatically. See below. |
| `rollout` | Percentage of devices offered this update, 0–100. `0` stops everyone, `critical` included. See below. |
| `critical` | Whether the client is allowed to insist. See below. |

Everything except `rollout`, `critical`, `min_supported_code` and the two summaries is computed
from the APK and the version — you do not hand-write a manifest, and you should not hand-edit
the computed fields. The three knobs are the ones you actually make decisions about.

## Gradual rollout

`rollout` is the percentage of devices that are offered the update. It exists so that a bad
release reaches ten people instead of everyone.

Each device falls into a stable bucket, so the population only ever grows as you raise the
number: a device that qualified at 10 still qualifies at 50. Raising the percentage never takes
the update away from someone who has already been offered it.

A cautious release looks like this:

```bash
# publish at 10%
python3 scripts/release.py 0.7.0 --manifest-only --apk … --rollout 10 \
    --summary-it "…" --summary-en "…"
gh release create v0.7.0 dist/release/jenny-0.7.0.apk dist/release/latest.json …

# a day later, nothing on fire — widen it
python3 scripts/release.py 0.7.0 --manifest-only --apk … --rollout 50 \
    --summary-it "…" --summary-en "…"
gh release upload v0.7.0 dist/release/latest.json --clobber

# and finally
python3 scripts/release.py 0.7.0 --manifest-only --apk … --rollout 100 \
    --summary-it "…" --summary-en "…"
gh release upload v0.7.0 dist/release/latest.json --clobber
```

The APK never changes; you are only rewriting the manifest asset in place with `--clobber`. Keep
the summaries identical across the widenings, or different people will see different text for
the same release.

## The kill switch

If a release turns out to be broken, you stop it the same way you widened it — by rewriting
`latest.json` with `rollout` set to `0`:

```bash
python3 scripts/release.py 0.7.0 --manifest-only --apk … --rollout 0 \
    --summary-it "…" --summary-en "…"
gh release upload v0.7.0 dist/release/latest.json --clobber
```

From that moment, no further device is offered the update. It takes effect on each phone's next
update check, so it is fast but not instant.

Zero is the one number `critical` does not override. Everywhere else `critical: true` skips the
wave, but a manifest at `rollout: 0` stops every device, critical or not — otherwise the brake
would be missing on exactly the releases it exists for, since the way you repair a broken build
is to publish the fix as `critical`, and a fix published in a hurry is the one most likely to
need stopping in turn.

Be clear about what this does and does not do:

- **It does not uninstall anything.** Devices that already took the update keep the broken build.
- **It does not roll back.** There is no way to push an older `versionCode`; Android will not
  install it.
- **It does not unsay the announcement.** A device that was already told about `0.7.0` in chat
  has that recorded, permanently, for that version. Withdrawing the manifest makes the Settings
  badge and the *Install now* button disappear at the next check, but nobody gets a retraction —
  and if you later re-widen the same version, those devices will not be told a second time. If you
  need to tell people something, that is a message you send, not a manifest edit.

So the kill switch buys you time, nothing more. The actual fix is a new release with a
*higher* `versionCode`, published at `rollout: 100` and — if the broken build is genuinely
harmful — marked `critical`. Deleting the bad release from GitHub is optional and mostly
cosmetic; what stops the bleeding is the manifest.

## `critical`

`critical: true` tells the client this is not an update it may quietly forget about. Use it for
security fixes and for repairing a build that is broken on the device — not to hurry people along
because you are pleased with a feature. It is the only signal that separates "there is a new
version" from "you really want this one", and it stops meaning anything if you set it every time.

Concretely, it does four things:

- **It skips the rollout wave.** A security fix is not delivered in waves: at any percentage
  between 1 and 100, every device that can take it is offered it immediately. The single
  exception is `rollout: 0`, the [kill switch](#the-kill-switch), which stops critical releases
  too — that is a deliberate stop, not a wave.
- Jenny is told to say plainly that it is a security update, instead of describing a new version.
- The Settings badge reads *Security* instead of *New*, and the notice is styled to match.
- A system notification is posted, so the announcement lands even if nobody had the chat open.

It is still not a forced install. `critical` changes how insistently Jenny asks and who gets
asked — never whether the user can say no. It also has no bearing on whether Android installs
unattended or shows its confirmation screen: that decision belongs to the system and is made at
install time, not in the manifest.

## `min_supported_code`

`min_supported_code` is the oldest `versionCode` still allowed to take the update automatically.
Devices below it are left alone and have to sideload the APK by hand.

Leave it at the default in normal times. Raise it when an in-place upgrade from a very old build
would land the user somewhere worse than a manual reinstall would — a workspace migration the old
version cannot perform, a config format it cannot read. Raising it is a decision to strand a group
of users on an old build until they intervene manually, so it needs a concrete reason.

The script defaults it to the value from the previous manifest in the output directory, or `0` if
there is none, and `--min-supported-code` sets it explicitly. It refuses a value higher than the
new `version_code`, which would lock out literally everyone.

## Reference: `release.py` options

| Option | Effect |
|---|---|
| `VERSION` (positional) | The new version, `X.Y.Z`. Required. |
| `--apk PATH` | Path to the signed APK. Generates the manifest; without it the script only bumps. |
| `--manifest-only` | Skip the bump; the files must already be at `VERSION`. |
| `--summary-it` / `--summary-en` | The in-app one-liners. Required whenever `--apk` is given. |
| `--rollout N` | Percentage of devices offered the update, 0–100. Default `100`. |
| `--critical` | Mark the release critical. Default off. |
| `--min-supported-code N` | Oldest `versionCode` allowed to update. Default: previous manifest, else `0`. |
| `--out PATH` | Output directory, or a path ending in `latest.json`. Default `dist/release/`. |
| `--repo SLUG` | Repository the URLs point at. Default `flagdizero/jenny-android-ai-agent`. |
| `--dry-run` | Print everything, write nothing. |

## Reference: the knobs on the device

These live under `updates` in the device's `config.json`. They are not exposed in Settings — a
user who wants to change them edits the file — but they decide whether anything you publish is
ever looked at, so they belong on this page.

| Key | Effect |
|---|---|
| `enabled` | Whether the `update_check` job is registered at all. Off means the device never fetches the manifest and never learns a new version exists. Default `true`. |
| `manifestUrl` | Where to look. Defaults to the `releases/latest/download/latest.json` URL above; override it to point a device at a staging manifest. |
| `checkIntervalH` | Hours between checks, 1–168. Default `24`. Read once at startup, so a change needs a gateway restart. |
| `notifyInChat` | Whether a new version opens a message in chat. Off leaves the Settings badge as the only signal — the check still runs. Default `true`. |
