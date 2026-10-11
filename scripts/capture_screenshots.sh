#!/usr/bin/env bash
#
# Captures the README screenshots from a real device over adb.
#
# Usage:  scripts/capture_screenshots.sh [shot-name ...]
#         (no arguments = walk the whole shot list)
#
# Screenshots for a mobile app have to come from the device: a desktop browser
# pointed at the gateway renders different fonts, a different aspect ratio and
# no Android system bars, so it misrepresents the product.
#
# The script prompts you to put the app in the right state, then grabs the
# framebuffer. Nothing is cropped or retouched — what the phone shows is what
# lands in docs/img/.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="$REPO_ROOT/docs/img"

# name|description of the state to set up before the capture.
# The set the docs use, 1080x1920; see docs/img/. In English: the README and the site are.
SHOTS=(
"hero-chat|Home, Jenny page: a real exchange where she uses a tool on a mini-app she wrote (e.g. log a meal, read the day back), plus one more short turn. This is the shot people judge the project by."
"apps|A Jenny App open from the Apps page, with some data in it."
"themes|Home -> Settings, the theme card at the top."
"notebook-board|Notebooks -> a notebook with a few cards on its board, a thread or two between them."
"notebook-drawer|The same board -> a card held open in its drawer."
"wiki-reader|The same notebook -> a page open in the reader, a passage selected so the Report bar shows."
"onboarding-format|First run (fresh install), step 1: the two format cards."
"onboarding-model|First run, step 3: the model list with one selected. Never capture step 2 once the key is typed."
"app-card|Apps page, long press on a Jenny App: the card with Open / Add as a page / Edit / Delete."
"workshop-console|Settings -> Workshop, Console: a reply with its tool pills and Show thinking, keyboard closed."
)

# Physical id of the display to capture, resolved at runtime rather than hardcoded:
# it differs per device, and the Titan 2 exposes two (a 1436x1440 main panel and a
# small secondary one). Picking the first entry SurfaceFlinger reports is what the
# system itself treats as the primary display.
resolve_display() {
    DISPLAY_ID="$(adb shell dumpsys SurfaceFlinger --display-id 2> /dev/null \
        | sed -n 's/^Display \([0-9]\{1,\}\) .*/\1/p' | head -1)"
    if [ -z "${DISPLAY_ID:-}" ]; then
        echo "error: could not resolve a display id from SurfaceFlinger." >&2
        exit 1
    fi
    echo "capturing display $DISPLAY_ID"
}

require_device() {
    if ! command -v adb > /dev/null 2>&1; then
        echo "error: adb not found in PATH." >&2
        exit 1
    fi
    # An offline/unauthorized device shows up in `adb devices` but cannot be
    # captured, so match the trailing "device" state explicitly.
    if [ "$(adb devices | grep -c -E '\sdevice$')" -eq 0 ]; then
        echo "error: no authorised device attached. Check 'adb devices' —" >&2
        echo "       unlock the phone and accept the USB debugging prompt." >&2
        exit 1
    fi
}

capture() {
    local name="$1" description="$2"
    echo
    echo "── $name ──"
    echo "$description"
    printf 'Set the screen up, then press Enter (or s to skip): '
    read -r answer
    if [ "$answer" = "s" ]; then
        echo "skipped $name"
        return
    fi
    local target="$OUT_DIR/$name.png"
    # Capture to a file on the device, then pull it. `adb exec-out screencap -p`
    # looks cleaner but is unusable on multi-display phones (e.g. the Unihertz
    # Titan 2): screencap prints "[Warning] Multiple displays were found..." on
    # STDOUT, so the warning is prepended to the PNG and the file is corrupt.
    #
    # `-d $DISPLAY_ID` matters for the same reason, and is the subtler half of it:
    # without an id screencap says its choice "is not guaranteed to be consistent
    # across captures", and on the Titan 2 it periodically returns the small
    # secondary panel — which is off, so the grab is a perfectly valid all-black PNG.
    adb shell screencap -d "$DISPLAY_ID" -p /sdcard/_capture.png 2> /dev/null
    adb pull /sdcard/_capture.png "$target" > /dev/null 2>&1
    adb shell rm -f /sdcard/_capture.png 2> /dev/null || true
    if [ ! -s "$target" ]; then
        rm -f "$target"
        echo "error: capture produced no data for $name." >&2
        return 1
    fi
    # A non-empty file is not proof of a valid capture — check the PNG magic
    # bytes so a corrupt grab fails here instead of in the README.
    if [ "$(head -c 4 "$target" | od -An -tx1 | tr -d ' \n')" != "89504e47" ]; then
        rm -f "$target"
        echo "error: $name is not a valid PNG — capture was corrupted." >&2
        return 1
    fi
    # ...and a valid PNG is not proof of a useful one: a capture taken with the
    # screen asleep, or off the wrong display, decodes fine and is entirely black.
    if [ "$(ls -l "$target" | awk '{print $5}')" -lt 20000 ]; then
        echo "warning: $name is only $(du -h "$target" | cut -f1) — a black or blank" >&2
        echo "         capture compresses to almost nothing. Check the screen was on." >&2
    fi
    echo "wrote docs/img/$name.png ($(du -h "$target" | cut -f1))"
}

require_device
resolve_display
mkdir -p "$OUT_DIR"

if [ "$#" -gt 0 ]; then
    for requested in "$@"; do
        found=0
        for entry in "${SHOTS[@]}"; do
            name="${entry%%|*}"
            if [ "$name" = "$requested" ]; then
                capture "$name" "${entry#*|}"
                found=1
                break
            fi
        done
        if [ "$found" -eq 0 ]; then
            echo "unknown shot '$requested'. Known shots:" >&2
            for entry in "${SHOTS[@]}"; do echo "  ${entry%%|*}" >&2; done
            exit 2
        fi
    done
else
    for entry in "${SHOTS[@]}"; do
        capture "${entry%%|*}" "${entry#*|}"
    done
fi

echo
echo "Done. Check the results, then make sure every image referenced in"
echo "README.md exists — a broken image in the README is worse than none."
