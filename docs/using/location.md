# Location

Jenny can read your phone's GPS and use it in conversation — here is exactly what it knows, when, and how to turn it off.

## The two-level model

Jenny does not "watch" your location continuously. There are two distinct ways it reaches your position, and they behave very differently:

1. **Last-known context line, injected every turn.** Before each message is sent to the model, Jenny reads whatever position the phone's OS already has cached (a "fused" last-known fix — no GPS radio is turned on for this) and adds a line like `Device location (~3 min ago): Milan, Italy (45.46421, 9.18921)` to the context. The age is always stated, so the model knows how stale it is. This is free: it costs no battery and does not wait on anything. If no fix is cached yet (fresh install, permission just granted) the line is simply omitted.
2. **A precise, on-demand fix via the `get_location` tool.** When Jenny actually needs an accurate position — "where exactly am I?", a distance calculation, a maps link — it can call `get_location` with `precise=true`. This turns the GPS radio on and waits for a fresh fix, up to a 15-second timeout (`freshTimeoutS`, configurable). This costs battery and a few seconds; it is not done automatically on every turn.

The tool also returns coordinates, accuracy in meters, the fix's age, and a Google Maps link — useful when you ask Jenny to share or reason about a specific place.

## The double gate

Two independent switches must both be on for any location data to reach Jenny at all:

| Gate | Where | Default |
|---|---|---|
| In-app toggle | Workshop → **Hands** → **Location** → **Share my location** | ON |
| Android runtime permission | System permission prompt (`ACCESS_FINE_LOCATION` / `ACCESS_COARSE_LOCATION`, precise or approximate — either one is enough) | Requested at first launch |

The toggle is exactly this text, with the hint (with her name, if you renamed her): "Jenny uses your phone's GPS to know where you are: the recent position is injected into context each message, a precise fix only on request. Requires the Android location permission. Locations shared via Telegram apply there only and expire after an hour."

The toggle flips `tools.location.enable` in the backend config; the permission is Android's. It is first asked when the app starts up, right after the notification permission, and Android offers both **Precise** and **Approximate** — either one is enough for Jenny.

If the toggle is on but Android hasn't granted the permission, **Hands → Location** says so under the toggle — *"Android hasn't allowed Jenny to use the location yet"* — with an **Allow location** button. Tapping it, or turning the toggle on while the permission is missing, asks Android again. Once Android stops asking (after you've refused twice, or chose "Don't ask again"), the same button opens Jenny's page in Android's settings instead, where the permission is granted under **Permissions → Location**. The notice disappears as soon as the permission is there.

If you ask Jenny for your location while either gate is off, the `get_location` tool replies with an explicit error rather than failing silently:

> "Location unavailable — the toggle may be off, the Android location permission not granted, or no GPS fix is currently known."

The passive context line, on the other hand, fails silently by design: it is just omitted, with no error and nothing shown in the chat.

Jenny never requests background location access (`ACCESS_BACKGROUND_LOCATION`) — reads only happen while the app's foreground service is alive, which on Android is the same lifetime as the gateway itself (see [Android permissions](../reference/android-permissions.md)).

## Place reminders (zones)

The one case in which Jenny reads the position on her own is a reminder about a place ("when I get to the station…"), described in [Arriving at a place](scheduling.md#arriving-at-a-place). While at least one zone is armed, the code reads the position from time to time — from the network first, the GPS only when needed — and compares it with the zones on the phone: the readings are not sent anywhere and cost no model call. Only an arrival starts a turn, and that turn is told the place you arrived at, not your coordinates. With no zone armed, nothing is read. Both gates above apply: with the toggle off Jenny refuses to arm a zone, and without the permission the zones are on hold.

## Say it plainly: this reaches your LLM provider every turn

If the toggle is on and the permission is granted, **your approximate location is sent to whichever LLM provider you've configured on every single message you send** — not just when you explicitly ask about your location. It rides along in the same context block as the current time and other runtime info. This is the trade-off of the "always available" design: Jenny can answer location-aware questions naturally, but that also means a steady stream of your whereabouts leaves the device on every turn, going wherever your configured provider (and, if applicable, its own subprocessors) sends inference traffic.

If you don't want your location leaving the device at all, turn the toggle off (see below) — that's the only way to guarantee it never gets to the context builder.

## Telegram location override (1 hour, that channel only)

If you share a location (or a venue) from inside the paired Telegram chat, Jenny treats it as a temporary override that applies **only to Telegram**, not to the WebUI:

- The shared coordinates are reverse-geocoded when possible (a venue's name and address are used as the place label) and immediately trigger a turn, so Jenny reacts to it right away.
- For up to `telegramTtlS` (default 3600 seconds = 1 hour, minimum 60), every reply sent through Telegram uses that shared position instead of the phone's GPS. The context line reads `User location (shared via Telegram): <place> (lat, lng)` instead of the usual "Device location" line.
- Once the hour is up, Telegram falls back to the phone's live GPS, exactly like the WebUI already does. The WebUI is never affected by a Telegram location share.
- The override is kept only in memory — it does not survive an app restart, and there is no per-chat persistence beyond that TTL.
- If the location toggle is off, sharing a location in Telegram is not recorded at all, and the bot replies with the generic "🤷 I can't handle this kind of message yet." it uses for unsupported message types — which is misleading here (it's really about location being disabled, not about unsupported media), so don't read that reply as a media limitation in this specific case.

See [Telegram bridge](telegram.md) for the rest of what Telegram can and cannot do.

## Configuration

The workshop only exposes the on/off toggle. The other two fields are config-only today — edit `workspace/config.json` and restart the app to change them.

| Config key (camelCase) | Default | Meaning |
|---|---|---|
| `tools.location.enable` | `true` | Master switch: last-known injection and the `get_location` tool are both gated on this, in addition to the Android permission. |
| `tools.location.telegramTtlS` | `3600` (min `60`) | How long a location shared via Telegram overrides the GPS fix, in seconds, for Telegram replies only. |
| `tools.location.freshTimeoutS` | `15` (range `1`–`60`) | How long `get_location(precise=true)` waits for a fresh GPS fix before giving up. |

See [Configuration reference](../reference/configuration.md) for the snake_case aliases (`telegram_ttl_s`, `fresh_timeout_s`) accepted on read.

## Turning it off

To stop location data from reaching Jenny entirely, do either of these (both work; the toggle is the quicker one):

- **In-app:** in the workshop, **Hands** → **Location** → turn off **Share my location**. The toast confirms "Location disabled". This flips `tools.location.enable` to `false` immediately, no restart needed — both the context line and the `get_location` tool go dark right away.
- **Android permission:** revoke Location for Jenny from the system app settings. This achieves the same result at the OS level regardless of what the in-app toggle says, since the native bridge cannot return anything without it.

Turning either one off does not retroactively remove location lines already sent to your provider in past turns — it only stops new ones going forward.

## Related pages

- [Settings](../reference/settings.md#location) — the Location group in the workshop's Hands drawer, and what else lives there.
- [Telegram bridge](telegram.md) — the location-sharing override in context, and what Telegram can/cannot receive.
- [Android permissions](../reference/android-permissions.md) — the full permission table and what happens if you deny each one.
- [Privacy](../internals/privacy.md) — everywhere your data can leave the device, location included.
- [Configuration reference](../reference/configuration.md) — full key reference with snake_case aliases.
