# Building from source

How to get a working build of Jenny on a device from a clean clone, plus a desktop-only shortcut for iterating on the Python side without a phone.

Jenny ships as an Android APK with an embedded Python 3.11 runtime (via [Chaquopy](https://chaquo.com/chaquopy/)) — there is no separate desktop distribution. Most users install the signed APK from GitHub Releases instead of building; see [Install the APK](../start/install.md) for that path. Building from source and installing on a device or emulator is the contributor path — the one this page covers — and it's also the way to get Jenny running without trusting a prebuilt binary. This page adds what a contributor typically also needs: a faster inner loop, release signing, and a way to exercise the Python gateway without a device at all.

## Prerequisites

| Requirement | Notes |
|---|---|
| The repository cloned | `git clone` of this repo |
| JDK 17 | Runs the Gradle 8.9 build (`android/gradle/wrapper/gradle-wrapper.properties` pins Gradle 8.9) |
| Android SDK Platform 34 | `compileSdk`/`targetSdk` are both 34; install via Android Studio or a standalone `cmdline-tools` setup |
| `adb` | Ships with the SDK's `platform-tools` |
| A device or emulator running Android 8.0 (API 26) or newer | `minSdk` is 26 |
| USB debugging enabled and the device authorized to your machine | Settings → About phone → tap "Build number" seven times → Developer options → USB debugging |
| Network access on the first build | Gradle/Chaquopy download the Python 3.11 runtime and every wheel pinned in `requirements-android.lock.txt` the first time; subsequent builds reuse the cache |

Nothing needs to be installed on the device itself beyond the APK — Chaquopy bundles the entire Python interpreter and dependency set into the app package.

## Build and install on a device

Confirm the device is visible first, then build straight to it:

```bash
adb devices                 # must show your device as "device", not "unauthorized"
cd android
./gradlew app:installFossDebug
```

`app:installFossDebug` is the canonical command (also the one used in `AGENTS.md`): it compiles the debug variant of the `foss` flavor and pushes it to the connected device over `adb` in one step, no separate `adb install` needed. The first build is slow — Gradle has to fetch the Chaquopy Python runtime and every pinned wheel, then Chaquopy compiles them per target ABI (`arm64-v8a`, `armeabi-v7a`, `x86_64`, `x86`). Later builds are much faster since the toolchain and wheels are cached.

### Two flavors

The same code builds two flavors: `foss` (GitHub, F-Droid-ready: no Google library) and `play`
(the Play Store build, which adds the encrypted copy on Google Drive). `installPlayDebug`
builds and installs the second. The two are the same app signed by different keys in release,
so on a device you switch between them by uninstalling.

What a flavor adds lives outside the core, so the `foss` build doesn't contain its code at all:

| | Core (every build) | Only in `play` |
|---|---|---|
| Python and WebUI | `jenny/` | `jenny_extensions/` — packaged by Chaquopy only in `play` (`build.gradle.kts`) |
| Kotlin | `android/app/src/main/` | `android/app/src/play/` |
| Gradle dependencies | `implementation(...)` | `playImplementation(...)` |

The core reaches an extension only through generic hooks — `jenny/extensions.py` on the Python
side, `shared/extensions.js` in the WebUI, `NativeExtension.kt` in the shell — and
`tests/test_extensions.py` fails if the core names anything of the Drive. To check a built APK,
run `python3 scripts/check_foss_apk.py <apk>` (and `--play` for the opposite check).

If `adb devices` lists nothing, or the device shows as `unauthorized`, USB debugging isn't enabled yet, or you haven't accepted the RSA key fingerprint prompt on the device screen.

Opening `android/` in Android Studio and hitting Run does the same build through the IDE if you prefer that over the command line.

### Redeploying UI-only changes

Changes under `jenny/templates/ui/` (the WebUI's HTML/CSS/JS) are bundled into the APK's assets and re-extracted into the workspace at gateway startup — they do **not** show up just by restarting the app. You need to rebuild and reinstall (`./gradlew app:installFossDebug`) for the new UI to reach the device, every time.

## Release builds and signing

`./gradlew app:assembleFossRelease` also works from a clean clone, but by default it produces an **unsigned APK that Android refuses to install**. There is no keystore in this repository — release signing is left to whoever is building, via one of two sources (checked in this order):

- Environment variables: `JENNY_KEYSTORE_PATH`, `JENNY_KEYSTORE_PASSWORD`, `JENNY_KEY_ALIAS`, `JENNY_KEY_PASSWORD`
- A gitignored `android/keystore.properties` file with `storeFile`, `storePassword`, `keyAlias`, `keyPassword`

If neither source supplies a complete set of four values, `app/build.gradle.kts` deliberately leaves the release build **unsigned rather than failing** — so `assembleFossRelease` keeps working for anyone who just wants to reproduce and inspect the artifact, even without a signing key. Gradle prints an explicit warning at build time in that case. When signing does apply, it's schemes v2 and v3 (v1/JAR signing is intentionally disabled — the app's `minSdk` of 26 makes it dead weight).

There's no reason to build a release variant for day-to-day development; `installFossDebug` is the right command unless you're distributing the APK yourself.

## Verifying the device

Two quick sanity checks after a fresh install:

- `adb devices` should show the device as `device` (not `unauthorized` or `offline`) both before and after the build.
- On first launch, the app shows a loading screen while Python/Chaquopy extracts on-device — this is normal and can take a while on the very first run after install. See [First run](../start/first-run.md) for what happens next.

## Appendix: local desktop gateway (dev-only shortcut)

For iterating on the Python side (agent loop, tools, providers, config) without rebuilding and reinstalling an APK every time, you can run the same gateway entry point Android uses, directly on your desktop machine:

```bash
pip install -e .
python -c "from jenny.android_entry import run_gateway; run_gateway('/path/to/some/dir')"
```

This is **not** a supported deployment target — Android is the only runtime target for Jenny (see `AGENTS.md`) — it's purely a way to exercise the gateway's Python side faster while you develop. `run_gateway()` starts the same async gateway the Android runtime calls from Kotlin via Chaquopy, listening on `127.0.0.1:18790` by default, with the WebSocket and HTTP (including the WebUI's static assets) served on that same port.

### The trap: the argument is `data_dir`, not the workspace

`run_gateway(data_dir)` treats its argument as a **data directory**, not the workspace itself — it creates the actual workspace at `<data_dir>/workspace` underneath it. This is easy to get backwards:

```python
# Wrong: you already have a workspace dir and pass it directly —
# Jenny creates ANOTHER workspace/ inside it.
run_gateway("/path/to/some/dir/workspace")   # → workspace lives at
                                              #   /path/to/some/dir/workspace/workspace

# Right: pass the parent directory; Jenny creates workspace/ under it.
run_gateway("/path/to/some/dir")             # → workspace lives at
                                              #   /path/to/some/dir/workspace
```

If your gateway seems to ignore a `config.json` you carefully placed by hand, check whether you handed `run_gateway` the workspace path instead of its parent — this is the single most common way to end up editing the wrong file.

### Config is auto-generated — you don't need to write it by hand

You don't need to create `config.json` yourself before starting the gateway. `ensure_minimal_config()` runs on every `run_gateway()` call and, if no config exists yet, writes a minimal one at `<data_dir>/workspace/config.json` with `gateway.host` set to `127.0.0.1` and a freshly generated `websocket.token_issue_secret` (used to authenticate the WebUI over HTTP/WebSocket). This is idempotent — it never overwrites an existing config.

The gateway starts fine even with **no LLM provider configured at all** — it logs a warning and waits for one to be added. To actually get responses out of it, add a `providers.providers` entry and a `modelPresets` entry to the generated `config.json` afterward (see [Providers and models](../reference/providers.md) for the field-by-field format), then restart the process — config changes made by hand to the file (as opposed to through the WebUI, from the home's Settings or the workshop) require a restart to take effect.

### Talking to the local gateway

There's no separate desktop UI: the gateway serves the same bundled WebUI assets and WebSocket protocol it serves to the Android WebView. Pointing a desktop browser at `http://127.0.0.1:18790/` will load the same SPA, but authenticating it manually means walking the same bootstrap handshake the Kotlin WebView normally does for you (exchanging the `token_issue_secret` from `config.json` for a session token at `/webui/bootstrap`). For quick iteration it's often less friction to talk to the gateway directly with a raw WebSocket client using that secret than to fight the bootstrap flow in a browser — see [WebSocket protocol](../reference/websocket.md) for the message format.

## See also

- [Install the APK](../start/install.md) — the same build, framed for an end user
- [Development](development.md) — repository layout and where subsystems live
- [Testing](testing.md) — running the test suite and lint/type checks
- [Providers and models](../reference/providers.md) — configuring a provider once the gateway is up
- [WebSocket protocol](../reference/websocket.md) — the wire format for talking to a running gateway directly
