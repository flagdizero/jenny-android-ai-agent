# Backup and restore

Jenny keeps two independent safety nets for your workspace: automatic local snapshots and an encrypted backup file you export yourself. They solve different problems, and only one of them protects you against uninstalling the app.

## Two different things, don't confuse them

| | Local snapshots | Encrypted backup (`.jbk`) |
|---|---|---|
| What it is | Automatic version history of your workspace | A single encrypted file you export and store yourself |
| Where it lives | Inside app storage, alongside the workspace | Wherever you save it (Google Drive, SD card, another device) |
| Survives uninstalling the app | **No** | Yes — that's the whole point |
| Survives a phone swap | No | Yes |
| Needs a passphrase | No | Yes, and it's unrecoverable if lost |
| Created automatically | Yes | No, you trigger it manually |

If you only care about undoing something Jenny (or you) did to a file, snapshots already cover you. If you want to survive an uninstall, a phone upgrade, or a debug-to-release signature change, you need to export a `.jbk` backup and move it off the device.

Exporting and restoring from a file are on the home's **Settings → Backup** page. The local snapshots can be browsed and restored right there, under **Local history**, and also in the workshop, under **Memory → Local history**, which is where they are turned on or off and created by hand. See the [Settings reference](../reference/settings.md#backup).

## Encrypted backup (.jbk)

This is disaster recovery: a single file containing your whole workspace — memory, conversation history, settings, API keys, mini-apps, wiki content — plus the full local snapshot history, all encrypted. It's meant to leave the device.

**One thing it does not contain: your SSH keys.** The private key for each registered host, and the `known_hosts` file holding the fingerprints you accepted, live in `<filesDir>/ssh` — *beside* the workspace, not inside it, so that the agent's file tools cannot reach them. Export and snapshot both walk the workspace root only, so neither ever sees them. The host list itself is in `config.json`, which *is* in the workspace, so a restore brings your hosts back looking complete — with no key behind them. See [After a restore, SSH is not restored](#after-a-restore-ssh-is-not-restored) below.

### Exporting

The **Backup** row on the Settings page says when you last exported one ("never" until you do), and the page it opens says it in full: "Last backup: …" or "You haven't exported a backup yet."

1. Open **Settings → Backup** and tap **Export a backup**.
2. Choose a passphrase and type it twice to confirm.
3. Jenny takes a `pre-export` snapshot, encrypts everything, and hands the file to Android's Storage Access Framework (SAF) "save as" picker — you can save it to Google Drive, an SD card, or any location the picker offers. No storage permission is requested; SAF handles it.
4. The suggested filename is `jenny-backup-YYYYMMDD-HHMMSS.jbk`.

Format details, if you care: the container is AES-256-GCM with a key derived via PBKDF2-HMAC-SHA256 at 600,000 iterations by default (configurable between 100,000 and 10,000,000 via `snapshots.pbkdf2_iterations`). Inside the encrypted envelope is a plain, readable zip archive (a file tree plus the snapshot store) — so in a real emergency you can decrypt the container and open the zip even without Jenny installed. This is a deliberate design choice: your backup isn't locked to this app.

The payload is encrypted in 1 MiB segments rather than in one piece (format version 2), so a workspace full of chat photos doesn't have to fit in the phone's memory to be exported. Photos, audio and video go into the zip as they are, without recompression. Backups in the older single-piece format (version 1) still import. The layout is documented at the top of `jenny/snapshot/crypto.py`. A segmented file is standard AES-GCM, just applied once per segment, so a dozen lines of Python with the `cryptography` package decrypt it:

```python
import hashlib, struct, sys
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

data, passphrase = open(sys.argv[1], "rb").read(), sys.argv[2].encode()
header = data[:41]                     # JNBK | 2 | iterations | salt | nonce | segment size
iterations, salt, nonce = struct.unpack(">I", header[5:9])[0], header[9:25], header[25:37]
size = struct.unpack(">I", header[37:41])[0] + 16
key = AESGCM(hashlib.pbkdf2_hmac("sha256", passphrase, salt, iterations, 32))
body = data[41:]
segments = [body[i:i + size] for i in range(0, len(body), size)]
with open("backup.zip", "wb") as out:
    for i, seg in enumerate(segments):
        seg_nonce = nonce[:4] + (int.from_bytes(nonce[4:], "big") ^ i).to_bytes(8, "big")
        aad = header + struct.pack(">QB", i, i == len(segments) - 1)
        out.write(key.decrypt(seg_nonce, seg, aad))
```

The export/import picker uses Android's standard document APIs, so Drive should work like any other SAF target, but a full save-to-Drive round trip hasn't been confirmed on-device yet.

**The passphrase cannot be recovered or reset.** There is no "forgot passphrase" flow. If you lose it, the backup file is permanently unreadable — Jenny warns you about this in the passphrase dialog. Write it down somewhere safe, not just in your head.

### Importing

1. From **Settings → Backup**, tap **Restore from a file** (worded as "Restore from backup" during onboarding — see below).
2. Pick the `.jbk` file with Android's file picker. The picker has no MIME filter for `.jbk` (it isn't a registered file type), so it shows up as a generic file — just pick it by name.
3. Enter the passphrase. There is no separate confirmation after this: the warning is the note under the button, *"Replaces everything there is now with what's in the file, and restarts Jenny."* This is not a merge of your current workspace and the backup — it's a full replacement.
4. Jenny decrypts and validates the backup first, **then** takes a `pre-restore` snapshot of your **current** state, and finally shows a non-cancellable **"Restore ready"** dialog with a single **Restart now** button. You cannot back out of this dialog with the Android back button.
5. Tapping Restart now kills and relaunches the app. The actual workspace swap happens at that restart, not before — the app restarts itself to do the swap cleanly.

What actually gets replaced: the whole workspace tree is swapped for the one in the backup — and only the workspace tree, which is why the SSH key directory next to it is neither replaced nor restored. Your current workspace isn't deleted immediately — it's kept as an internal safety copy for 7 days in case something goes wrong, but that copy is not reachable from the UI; it exists purely as an emergency recovery mechanism, not something you can browse or restore from yourself.

**The snapshot history is the one exception to "replace everything."** The snapshot history bundled inside the `.jbk` is merged additively into your local snapshot store — nothing is thrown away. After the restore, you can see both the snapshots that came with the backup and the ones you had locally before, including the `pre-restore` snapshot the import just took. So even after a full restore, you can still step back to the moment right before you imported.

Restore is also offered on the **"Restore from backup"** card during [first-run onboarding](../start/first-run.md) (*"Used Jenny before? Bring everything back from an encrypted backup file"*) for people setting up a new phone. After the restart the rest of the setup wizard is skipped: the app opens straight on the home, on Jenny's page, with the restored provider, name and history. The phone's Back button closes the passphrase dialog; it doesn't close the final **Restart now** dialog.

### Errors and edge cases

- Wrong passphrase or a corrupted file both produce the same message: **"Wrong passphrase or corrupt file."** There's no way to tell the two apart from the error alone.
- A backup file with an absurd iteration count (corrupted or hostile) is rejected immediately — iterations above 10,000,000 are refused before the expensive key derivation even runs.
- Export and import both require the native Android bridge; on a browser or a non-Android build you'll see **"Backup is only available in the Android app."**
- Deriving the key from your passphrase takes roughly a second on-device (that's the point of 600,000 PBKDF2 iterations) — export and import are not instant.

### After a restore, SSH is not restored

A restore replaces the workspace. Your registered hosts live in `config.json` inside the workspace, so they all come back: aliases, addresses, usernames, descriptions, and the fingerprint strings shown in the workshop. The private keys and `known_hosts` do not — they were never in the backup, because they sit outside the workspace. Nothing in the restore touches `<filesDir>/ssh` either way.

What that means depends on where you restore:

- **Same device, app still installed** (a restore to undo a bad state): the key directory was never removed, so SSH keeps working. Nothing to do.
- **New phone, or after an uninstall/reinstall** (the case backups exist for): the key directory is empty. The workshop shows the full host list, but every SSH call fails.

Since 0.6.6 the **SSH** group in the workshop's **Hands** drawer says so instead of leaving you to work it out: any host whose fingerprint is recorded in `config.json` but no longer pinned in `known_hosts` is named in a notice explaining that keys and fingerprints are not in the backup by design. That combination only happens to a host you had already verified, so a host you simply never finished setting up is not flagged. Export and import themselves still say nothing.

The fix is manual and per host: open **Hands → SSH** in the workshop, tap each host and then **Generate key**, copy the new public line into that server's `~/.ssh/authorized_keys`, and then **Verify fingerprint** again to re-pin the host key. Password hosts are the exception — the password is stored in `config.json`, so it comes back with the workspace and only the fingerprint needs re-accepting.

The remote job registry is also left behind on purpose: `.jenny/ssh_jobs/**` is excluded from snapshots and backups (see below), so pending `ssh_job` entries do not survive a restore.

## Local snapshots

Snapshots are an automatic, content-addressed version history of your workspace. They exist so a bad edit — by you or by Jenny — is never final, and they require no action from you day to day. They live outside the workspace tree itself, which is exactly why restoring from one is always safe to undo: the history isn't wiped out by the restore that reads from it.

### When a snapshot is taken

| Trigger | Condition |
|---|---|
| Change + quiet period | The workspace is scanned every 5 minutes; if something changed, a snapshot is taken after 10 minutes without further changes |
| Daily safety net | If no snapshot has been taken in the last 24 hours, one is taken regardless |
| App shutdown | Every time the app closes |
| Before memory consolidation | Right before each Dream run |
| Before a gardener pass | Right before each [gardener](gardener.md) pass on a notebook (tagged `pre_gardener`, shown as *before the gardener*) |
| Before export / before restore | Automatically, as described above (`pre-export`, `pre-restore`) |
| Manual | Tap **Create snapshot now** in the workshop's **Memory → Local history** panel |

If nothing changed since the last snapshot, no new one is created — you get a **"No changes since the last snapshot"** toast instead of a duplicate entry.

### Retention

- The most recent **20 snapshots are always kept**, no matter how old.
- Beyond 30 days, history thins out to roughly one snapshot per day.
- Beyond that, the default horizon is **forever** — nothing is deleted purely on age. You can change this in the same panel under **Keep history for**: 1 week, 1 month, 1 year, or Forever.

**Changing the retention setting prunes old snapshots immediately, and that pruning is permanent.** Dialing the horizon down from Forever to 1 week doesn't just change future behavior — it deletes everything older than a week right away. There's no undo.

### What's excluded

Snapshots never capture the UI bundle, log files, temporary files, `__pycache__`, or the snapshot store itself. Symlinks are always skipped, unconditionally — if a path in your workspace is a symlink, it simply isn't included in any snapshot or backup.

`.jenny/ssh_jobs/**`, the registry of remote jobs started with `ssh_job`, is excluded too, for two separate reasons. It is throwaway operational state — ids, byte cursors and process ids belonging to a machine that isn't this phone, meaningless after a restore. And it holds the full text of every command sent to a server plus the server-side log paths, which would otherwise be the one SSH trace that leaves the device inside an exported `.jbk`, while the private key (outside the workspace) does not.

And, as covered above, the SSH key directory is outside the workspace entirely, so it is not "excluded" so much as never in scope.

### Restoring from a snapshot

Open **Settings → Backup → Local history** in the home — it opens the list in place, newest first, eight at a time with **Show the others** below — or **Memory → Local history** in the workshop, and tap any entry. You'll be asked to confirm: *"Bring Jenny back to its state from {date}? A snapshot of the current state is saved first, so you can undo this."* Like the `.jbk` import, this applies at the next app restart, and — because the snapshot history lives outside the workspace — a restore from a snapshot is always reversible: you can always step forward again afterward.

## APK updates, uninstalling, and signature mismatches

- **Updating the APK preserves your workspace.** Reinstalling a new build with the same signing key keeps everything — memory, conversations, config, uploads, wiki, mini-apps. The one thing that always gets refreshed on every app start, update or not, is the UI bundle and the built-in skills; those are re-extracted from whatever APK is currently installed, overwriting any local changes to them.
- **Uninstalling deletes everything**, workspace included. This is standard Android behavior for private app storage, and neither snapshots nor anything else in the app protects you from it — snapshots live in the same storage that uninstalling wipes out. The only thing that survives an uninstall is a `.jbk` file you've already exported and moved off the device.
- **A debug build and a release build (or two release builds signed with different keystores) are, to Android, different apps that happen to share a package name.** Installing one over the other is refused; Android forces you to uninstall the old one first, which erases the workspace. If you ever need to switch between a debug and a release build of Jenny — or re-sign a release build with a new keystore — **export a `.jbk` backup first**. There's no way around the uninstall once the signatures don't match.
