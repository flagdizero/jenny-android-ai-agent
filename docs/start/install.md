# Installing Jenny

Jenny is not on the Play Store. The normal way to get it onto a phone is to download the
signed APK from GitHub Releases, verify it, and sideload it. Building from source is the
alternative, described further down, for anyone who'd rather not run someone else's binary.

## Download the APK

Grab it from [**Releases**](https://github.com/flagdizero/jenny-android-ai-agent/releases/latest). It targets Android 8.0 or newer and is
about 72 MB, most of which is the embedded CPython runtime.

Verify what you downloaded before installing it. The expected hash is published on the
release page:

```bash
shasum -a 256 jenny-0.10.0.apk
```

You'll have to allow installation from outside the Play Store; Android will prompt you for
that. The APK is signed with the maintainer's own key (RSA 4096, schemes v2 and v3) — not by
any store, so no store vouches for it. That's the deal with sideloading: verifying the hash
is how you check you got the file the maintainer actually built, not something swapped in
along the way.

Once it's installed, launch it and follow onboarding — see [First run](first-run.md).

## Or build it yourself

Building from source works the same way whether you want to avoid sideloading someone else's
binary, want to inspect exactly what you're running, or are contributing to the project. It
needs a computer, not just the phone.

### What you need

| Requirement | Notes |
|---|---|
| A computer with the repository cloned | `git clone` of the project |
| JDK 17 | Required to run the Gradle 8.9 build |
| Android SDK Platform 34 | Via Android Studio, or a standalone `cmdline-tools` install |
| `adb` | Ships with the Android SDK platform-tools |
| A phone or tablet running Android 8.0 (API 26) or newer | `minSdk` is 26, `targetSdk`/`compileSdk` is 34 |
| USB debugging enabled on the device, and the device visible to `adb` | See below |
| Network access on the first build | Gradle downloads the Chaquopy Python 3.11 runtime and every wheel in `requirements-android.lock.txt` (20 pinned packages, direct and transitive) the first time you build |

You do **not** need anything installed on the phone beyond the APK itself. Jenny bundles a full Python 3.11 interpreter and its entire dependency set inside the app via [Chaquopy](https://chaquo.com/chaquopy/) — there is no separate Python install, no Termux, nothing to `pip install` on-device.

The build targets four Android ABIs (`arm64-v8a`, `armeabi-v7a`, `x86_64`, `x86`), so the same command works whether the target is a real phone or an x86 emulator image.

### Build and install

From the `android/` directory of the cloned repository:

```bash
adb devices          # confirm the device shows up as "device", not "unauthorized"
./gradlew app:installDebug
```

`app:installDebug` compiles the debug variant and installs it straight to the connected device over `adb` — no separate `adb install` step needed. The first run is slow: Gradle has to fetch the Chaquopy runtime and all the pinned Python wheels, and Chaquopy has to compile them for every target ABI. Subsequent builds are much faster.

If `adb devices` doesn't list your phone, USB debugging isn't enabled (Settings → About phone → tap "Build number" seven times to unlock Developer options, then enable USB debugging there), or the device hasn't accepted the RSA key fingerprint prompt yet.

Opening `android/` in Android Studio and hitting Run does the same thing through the IDE, if you prefer that over the command line.

### About release builds

`./gradlew app:assembleRelease` also works, but by default it produces an **unsigned APK that Android will refuse to install**. Release builds are only installable if you supply your own signing key, via one of:

- Environment variables: `JENNY_KEYSTORE_PATH`, `JENNY_KEYSTORE_PASSWORD`, `JENNY_KEY_ALIAS`, `JENNY_KEY_PASSWORD`
- A gitignored `android/keystore.properties` file with `storeFile`, `storePassword`, `keyAlias`, `keyPassword`

Without a complete set of credentials, the build still succeeds (so you can reproduce and inspect the artifact) but prints a warning and leaves the APK unsigned. There is no keystore in the repository — you would be signing with your own key, not the project's.

For day-to-day use on your own device, `installDebug` is the right command; there's no reason to build a release variant unless you're distributing the APK yourself.

## What the app declares about itself

A few things worth knowing before you install, none of them bugs — all deliberate, and all a little surprising the first time:

- **Jenny declares itself as a home-screen launcher**, not just an app. Alongside the normal launcher/app-drawer entry, its manifest also lists the `HOME` intent category. Press the phone's Home button and Android may offer Jenny as a candidate default launcher. This is intentional — the project is designed as much for a spare "phone in a drawer acting as a server" as for a primary phone — but it will surprise you if you install it on your daily driver. See [Set it as your launcher](launcher-setup.md) for what that actually looks like and how to say no. <!-- TODO: verify on-device (O-3): when exactly the HOME chooser prompt appears and how to dismiss/undo it -->
- **The gateway runs as a persistent foreground service**, which means an ongoing, low-priority, silent notification stays in your notification shade the entire time Jenny is running: "Jenny is running", with the body "Ready to reply and to run scheduled tasks". It follows your *phone's* language (Italian or English), not Jenny's own UI language setting, because it's an Android string resource rather than part of the WebUI. **Tap it to open Jenny** — which is the fastest way back into the app if you're not using Jenny as your launcher. Its notification channel is called "Background service" and carries a description explaining that Android requires the notification and that it stays until Jenny is stopped, so the shade itself can answer "what is this and why can't I dismiss it?". The notification can't be swiped away without stopping the service (which also stops the agent), and it deliberately survives you swiping Jenny out of Recents (`stopWithTask=false`) — closing the recent-apps card does not stop Jenny.
- **Jenny restarts itself after a device reboot.** A boot receiver relaunches the gateway service automatically, so after you restart the phone, Jenny comes back on its own without you opening the app — useful for the "server in a drawer" case, but worth knowing if you expect a fresh reboot to leave things off.
- **Cloud backup is on, with a real caveat.** The app has Android's `allowBackup` flag enabled with no exclusion rules configured. In practice this means Google's automatic app-data backup can sweep up Jenny's private storage — including `config.json`, which stores your LLM provider API keys in plain text. If you use Android's device backup/Google One backup, your API keys can end up on Google's servers as a side effect, with no separate encryption of Jenny's making. See [Security model](../internals/security-model.md) and [Privacy](../internals/privacy.md) for the full picture; if this bothers you, disable app backup for Jenny in your phone's backup settings. <!-- TODO: verify on-device (O-9): what a real Android 14 auto-backup actually captures for this app -->

## Permissions

Jenny requests a specific, short list of permissions at install and at first run — see [Android permissions](../reference/android-permissions.md) for the full table of what each one does and what happens if you say no. Notably, it never asks for the camera permission (photo capture is handed off to your phone's own camera app) or any storage permission (file saves and backups go through Android's Storage Access Framework and system pickers instead).

## After installing

The app opens straight into a loading screen the first time — that's normal, and covered in [First run](first-run.md), along with the setup wizard that follows it.

## See also

- [Introduction](introduction.md) — what Jenny is before you install it
- [First run](first-run.md) — what happens the first time you open the app
- [Set it as your launcher](launcher-setup.md) — the HOME-launcher behavior, in detail
- [Android permissions](../reference/android-permissions.md) — every permission, why, and what denial does
- [Backup and restore](../using/backup.md) — protecting your data independently of Android's cloud backup
- [Build from source](../contribute/build-from-source.md) — the same build, with more detail for contributors
