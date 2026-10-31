# Jenny from your other devices

Jenny lives on the phone, but you don't have to be holding it to talk to her. With one switch, your computer or a second phone can open the same Jenny in a browser, through [Tailscale](https://tailscale.com): the same chat, the same notebooks, the same apps. The phone stays the server. It can sit in a drawer on its charger while you type on the Mac.

Nothing is copied to the other device and nothing runs there. The browser shows the phone's own WebUI, and every turn still runs on the phone, with the phone's provider, memory and files.

## What you need

- **Tailscale on the phone**, signed in and connected.
- **Tailscale on the other device**, on the same tailnet (in practice, the same account).
- A browser on the other device. Nothing else to install.

Tailscale is the only way in on purpose. The home Wi-Fi never reaches this listener. The link Jenny gives you carries a key, and over Tailscale it travels encrypted end to end, even though the address starts with `http://`.

## Turning it on

In the workshop, open the **Hands** drawer and find **From other devices**. Its row, **Tailscale**, says whether it is on and who is connected. Tap it, then turn on **Reachable over Tailscale**.

When it is on, the panel shows:

- **Open from**: the address the other device should open, the phone's Tailscale name or address on port 18790. The key is not shown on screen.
- **Copy link**: copies the full link, key included. Paste it into the other device's browser and bookmark it.
- **Show QR**: the same link as a QR code, to scan from a second phone.
- **Connected now**: who is connected, since when, and how many windows each one has open. *Nobody, for now.* is the quiet state.

The link carries Jenny's key, and whoever has it gets in. Don't paste it into a chat, and don't send it to anyone. If a link has gone somewhere it shouldn't, tap **Disconnect everyone and change the key**: every other device is disconnected at once and the old link stops working. Nothing changes on the phone.

Turning the switch off keeps the key, so a link you saved works again when you turn it back on. Only changing the key makes old links stop working.

## Using Jenny from the other device

The browser opens the home, and the workshop is behind Settings as usual. What you write there reaches the phone live: the phone's chat shows your message with a small label, *from another device* or the device's name, and Jenny's answer appears on both.

**The phone doesn't ring for it.** A reply to something you wrote from another device makes no notification on the phone, because you are already reading it where you are. Reminders and anything Jenny starts on her own still notify the phone as usual.

Some things need the phone in your hand, and from another device they are missing or refused:

- **Android apps.** The App page shows only your Jenny Apps: launching, uninstalling or opening an Android app's info happens on the phone.
- **The floating mascot, screen reading and the overlay permission.** Those settings are hidden.
- **Installing an update.** You can see that one exists, but installing it needs Android's confirmation on the phone.
- **Jenny Apps that show a website.** They work through a proxy that only the phone can reach; tapping one says so and asks you to open it on the phone.

If you turn remote access off from the other device, Jenny warns you first: that window gets disconnected, and turning it back on takes the phone.

## When Tailscale drops

While the switch is on, Jenny keeps Tailscale on. When the phone reconnects to Tailscale, Android closes Jenny's listener, and Jenny opens it again within about ten seconds. If Tailscale itself goes down for about 20 seconds, she asks it to reconnect. If that doesn't work after two tries, she opens the Tailscale app once.

The panel tells you where things stand:

- *Waiting for Tailscale*, then *Jenny is turning Tailscale back on · attempt N*: she is on it.
- **Tailscale needs you**: she opened Tailscale and it still didn't connect, usually because it wants you to sign in again or to allow the VPN. Tap **Open Tailscale** and check. She stops trying by herself after this. As soon as Tailscale is back, she starts listening again on her own.

Clearing the phone's recent apps can also stop Tailscale. If the other device suddenly can't reach Jenny, the Tailscale row in the workshop is the first place to look.

## Security, in short

- The listener exists only while the switch is on, and only on the phone's Tailscale addresses, never on all interfaces. A connection that doesn't come from your tailnet is refused before anything else.
- The key is separate from the one the app itself uses inside the phone, and each listener accepts only its own key.
- The other device gets exactly what the phone's WebUI can do, with the phone-only exceptions above. It does not get a shell, files outside the WebUI, or the phone's Android permissions.

The details, including why Tailscale is recognised by its IPv6 range rather than its `100.x` addresses: [Security model](../internals/security-model.md). The config keys: [`gateway.remoteAccess`](../reference/configuration.md).

## See also

- [Settings](../reference/settings.md#from-other-devices): the panel, field by field.
- [Telegram bridge](telegram.md): the other way to reach a phone left at home, with notifications that follow you.
