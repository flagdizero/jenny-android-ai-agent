# Android Build Guide

## Requirements
- Android Studio
- Chaquopy plugin
- Python 3.11+

## Setup
1. Open `android/` in Android Studio
2. Chaquopy is configured in `app/build.gradle.kts`, which installs the exact pins of
   `requirements-android.lock.txt` (`pip { install("-r", ...) }`).
3. Pick a flavor: `play` (adds the copy on Google Drive) or `foss` (no Google library).
   From the command line: `./gradlew app:installPlayDebug` or `./gradlew app:installFossDebug`.
4. Build and run

## Runtime
The Python gateway runs in a background thread started from Kotlin.
The WebSocket and HTTP surfaces share the same port (default `18790`),
so the WebView loads `http://127.0.0.1:18790/html-mobile/` and connects
to the WebSocket on the same origin.

## Troubleshooting

Trouble with `web_search`/`web_fetch` (the WebView bridge)? See
[Troubleshooting](../docs/using/troubleshooting.md).
