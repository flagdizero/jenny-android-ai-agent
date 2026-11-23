# Peer protocol

This is the wire protocol two linked Jennys speak ([Your other Jenny](../using/other-jennys.md)), written down so that something other than Jenny can speak it too. The reference implementation is `jenny/peers/`; where this page and the code disagree, the code wins and this page has a bug.

The protocol has a contract file: [`tests/peers/vectors/peer-v1.json`](../../tests/peers/vectors/peer-v1.json). It holds fixed inputs and the exact bytes Jenny produces from them, for every hash, MAC, sealed secret and frame below. Another implementation is compatible when it reproduces that file. `tests/peers/test_vectors.py` fails when Jenny's code stops producing it, and `python scripts/peer_vectors.py` writes it again after a deliberate change.

The security reasoning (who decides what, what stays open) is in [Security model](security-model.md#other-jennys). This page is only the format.

## Transport

- **WebSocket**, plain `ws://`, on the path **`/peer`**. The default port is **18790**; a peer's port is stored with it and travels in the pairing link.
- **Tailscale only.** A listener binds only to the device's own tailnet addresses (`100.64.0.0/10` and `fd7a:115c:a1e0::/48`) and drops a connection from any other source before reading a frame. A dialer refuses to connect to an address outside those ranges. The traffic is already encrypted by WireGuard; the protocol adds authentication, not another layer of encryption.
- **Frames** are text messages holding one JSON object each, UTF-8, at most **262,144 bytes**. Every frame has a `type`. A frame that is not valid JSON, not an object, too large, or not one of the types expected at that point **closes the connection**. There is no error frame: a peer that speaks something else is treated as someone probing, not as a bug to tolerate.
- **Ids** (of a Jenny, a message, a question, a file) are **32 lowercase hex characters**. Each Jenny makes its own id once and keeps it.
- Unknown fields in a known frame are ignored, so a newer side can add optional fields without breaking an older one.

The protocol version is **1**. It travels as `v` in `pair` and `hello`.

## Two primitives

**MAC.** Every proof in the protocol is

```
mac(key, label, part1, part2, …) = lowercase_hex( HMAC-SHA256( key, utf8(label ␟ part1 ␟ part2 ␟ …) ) )
```

where `␟` is the unit separator `\x1f` and the parts are the ids and nonces as hex strings. Each role has its own label, so neither side can send the other its own proof back. Comparisons are constant-time.

**Sealed box.** The pairing secret travels inside the same container Jenny uses for its encrypted backups (`jenny/snapshot/crypto.py`, format v1):

```
"JNBK" | 0x01 | iterations (uint32, big-endian) | salt (16 bytes) | nonce (12 bytes) | ciphertext | tag (16 bytes)
```

The AES-256-GCM key is `PBKDF2-HMAC-SHA256(utf8(passphrase), salt, iterations, 32 bytes)`. The 37-byte header is the additional authenticated data. For pairing, the passphrase is the code key in lowercase hex and `iterations` is **1**: the code key is already slow to derive.

## Pairing

One side **shows** a code (A) and the other **types or scans** it (B). The code is 8 characters from the alphabet `23456789ABCDEFGHJKMNPQRSTUVWXYZ` (no 0/O, no 1/I/L), shown as `K7Q4-MZP2`. It is valid once, for **10 minutes** and **5 wrong tries**, and lives only in A's memory. A user may type it with lowercase letters, spaces or a dash: normalise by removing whitespace and `-` and upper-casing, and reject anything that is not 8 characters of the alphabet.

The QR and the deep link carry everything B needs:

```
jenny://pair?host=<MagicDNS name>&ip=<100.x address>&port=<port>&code=<code without the dash>
```

The link only fills in the form; B's user still confirms. B tries `host` and falls back to `ip`.

B opens a WebSocket to A's `/peer` and the exchange is:

| # | Direction | Frame |
|---|---|---|
| 1 | B → A | `{"type":"pair", "v":1, "from":B, "nonce":nB, "name":…, "host":…, "ip":…, "port":…, "agent":…}` |
| 2 | A → B | `{"type":"pair_challenge", "from":A, "nonce":nA, "salt":s, "agent":…}` |
| 3 | B → A | `{"type":"pair_proof", "mac": mac(K, "pair-b", B, A, nB, nA)}` |
| 4 | A → B | `{"type":"pair_ok", "mac": mac(K, "pair-a", A, B, nA, nB), "sealed": base64(seal(hex(K), S)), "agent":…}` |
| 5 | B → A | `{"type":"pair_done", "mac": mac(S, "pair-done", B, A, nB, nA)}` |

- `nA`, `nB` are fresh 16-byte nonces and `s` a fresh 16-byte salt, all in hex.
- `K = PBKDF2-HMAC-SHA256(ascii(code), bytes(s), 200000, 32)`, where `code` is the normalised code.
- `S` is a fresh **32-byte** secret made by A.
- In frame 1, `name`, `host`, `ip` and `port` are where B can be reached later. A stores them with the new peer, and its user can rename it.

Rules on each side:

- **A** checks frame 3 before anything else. A wrong MAC costs one of the 5 tries and closes the connection. After frame 5 checks out, A forgets the code and stores B. A also refuses a `pair` whose `from` is its own id or not an id.
- **B** stores A only after frame 4's MAC checks out and the box opens to exactly 32 bytes, and after sending frame 5. If no code is open on A, A just closes the connection.

**`agent`** says what is on each side: `"jenny"` or `"opencode"` (the `opencode-jenny` plugin on the user's computer). Each side sends it in its own frames (`pair`, `pair_challenge`, `pair_ok`) and stores what the other said with the new peer: a Jenny shows an opencode peer as a computer, not as another Jenny. A Jenny also saves an opencode peer with every permission off and answers `refused`, without running a turn, to any `ask` it sends: that side only answers. The field is self-declared and not covered by the MACs, so it can only take permissions away, never add any. The field is optional: a missing or unknown value means `"jenny"`, and older Jennys, which don't send it, are read that way. B takes it from `pair_ok`, or from `pair_challenge` if `pair_ok` doesn't carry it.

## Every connection after pairing

The side with something to send dials (D), the other listens (L). The same secret `S` proves both sides:

| # | Direction | Frame |
|---|---|---|
| 1 | D → L | `{"type":"hello", "v":1, "from":D, "to":L, "nonce":nD}` |
| 2 | L → D | `{"type":"challenge", "nonce":nL, "mac": mac(S, "l", L, D, nD, nL)}` |
| 3 | D → L | `{"type":"proof", "mac": mac(S, "d", D, L, nD, nL)}` |
| 4 | L → D | `{"type":"ready"}` |

L closes without a word if `to` is not its own id, if `from` is not a peer it holds a secret for, or if the proof is wrong. D closes if the challenge MAC is wrong. Each handshake step has **15 seconds**.

A listener that sees a linked peer come from a new tailnet address may update the address it keeps for that peer.

## The exchange

After `ready`, **both** sides send their pending messages for the other on the same connection, so one connection empties both queues:

```
{"type":"msg",  "id":<message id>, "kind":<kind>, "body":{…}, "created_ms":<epoch ms>}
{"type":"ack",  "id":<message id>}
{"type":"nack", "id":<message id>, "reason":"unsupported" | "bad" | "failed"}
{"type":"done"}
```

- Messages go **one at a time**: a side sends the next `msg` only after the `ack` or `nack` of the previous one, waiting up to **30 seconds**.
- After its last message a side sends `done`. The dialer closes once it has sent `done` and received the other's, or 30 seconds after its own.
- **`ack`** means "I have it, never send it again". It is sent once the message is handled, and also for a message id already received in the **last 7 days**, which is acknowledged and dropped. Each message therefore takes effect once, even when an `ack` is lost and the sender tries again.
- **`nack`** refuses a message, and its `reason` says whether to try again. `unsupported` (a `kind` this side does not know) and `bad` (a body that is not valid for its kind) mean "remove it from your queue, it will never work": the sender logs it and drops the message. `failed` means the handler could not deal with it *now*: a Jenny keeps the message, waits as after a failed delivery, and sends it again, up to 3 more times before dropping it. A side that drops on every `nack`, as an older Jenny does, is still correct; a side that sends `failed` for everything gets a few retries and nothing worse. Any other reason is treated as final.
- A receiver records a message id as seen only **after** its handler has finished, so the message that got `failed` is handled again when it comes back; handlers must tolerate seeing the same `qid` twice.
- **Queue.** A sender keeps each message until its `ack` or `nack`. A failed delivery is retried after 30 s, 1, 2, 5, 10 and then every 30 minutes, and sooner when something else wakes the queue.

## Message kinds

Bodies are JSON objects. A receiver ignores unknown fields, cuts text to the limits below, and treats every string from the other side as data, never as instructions.

### `ping`

`{}`. Proves the line works; the receiver only logs it.

### `unpair`

`{}`. The other side has unlinked: forget its secret and its entry.

### `ask`

A question, or a request to do something:

```
{"qid": <id>, "text": <at most 4000 chars>, "mode": "read" | "act"}
```

An unknown `mode` counts as `read`. The receiver answers with an `answer` for the same `qid`, later, as its own message.

### `answer`

```
{"qid": <id>, "status": <status>, "text": <at most 8000 chars>, "files": [<file>…],
 "mode": "act", "working": true|false, "done": [<item>…]}
```

- `status` is one of `ok`, `refused` (not allowed), `too_many` (rate limit, 30 an hour per peer in Jenny), `unavailable`, `failed`, `expired`.
- `files` lists at most 3 files sent before the answer as `file_part` messages: `{"fid", "name", "size", "sha256"}`. A file that did not arrive whole is reported as missing, not shown.
- `mode`, `working` and `done` appear only in answers to `mode: "act"`:
  - `working`: helpers are still at it, and a second answer will follow;
  - `done`: a list, written by code, of what was touched: `{"kind": "file" | "job" | "egress", "label": <text>, "undoable": bool, "allowed"?: bool}`, at most 40 items.
- `unlock` is optional, only with `refused`: the command that turns on what was refused, as the user would type it on that side (the `opencode-jenny` plugin sends `… act free` when it is read only). One printable line, at most 200 characters, no backquotes; anything else is ignored. Jenny shows it on the refusal, ready to copy.
- A refusal is sent at once, without running a turn: the asking side waits for the `ack` of its `ask` (at most 2.5 seconds) and then 1.5 seconds more, so its own reply can already say no. Anything slower arrives as a card on its own.

### `file_part`

One piece of a file that goes with an answer:

```
{"fid": <id>, "name": <name>, "size": <bytes>, "sha256": <hex of the whole file>, "i": <index>, "n": <count>, "data": <base64>}
```

- Every piece is **163,840 bytes** except the last, so `n = max(1, ceil(size / 163840))`.
- A file is at most **20 MB**.
- The receiver puts the file together when all `n` pieces are there, and discards it if the size or the SHA-256 does not match.
- The pieces are queued before the `answer` that names them, and the queue keeps its order.

### `undo` and `undone`

`{"qid": <id>}` asks the side that acted on request `qid` to undo it. The reply is:

```
{"qid": <id>, "status": "ok" | "refused" | "still_working" | "not_found" | "failed", "restored": n, "kept": n, "jobs": n}
```

Jenny honours `undo` only while that peer is still allowed to ask it to do things.

### The line: `line_open`, `line_say`, `line_move`, `line_list`, `line_close`

A **line** carries the user's own words from one side to the other: the user, on the passing side, writes to the receiving side as if typing there. The passing side sends these five; the receiving side answers with the four below. Every line has an id, `lid`, chosen by the side that opens it.

```
line_open   {"lid": <id>, "place": ""}            open; place is "" for the main chat, or a notebook's name
line_say    {"lid": <id>, "mid": <id>, "text": …}  one message of the user, up to 8,000 characters
line_move   {"lid": <id>, "place": "Suppliers"}    move the line to another chat of the receiving side
line_list   {"rid": <id>}                          ask for the names of the chats that can be moved to
line_close  {"lid": <id>}                          the user went back
```

The receiving side accepts a line only from a peer it allows to pass one. A `line_say` for a line it does not have open is answered with `line_state` `closed`, never handled.

### `line_state`, `line_text`, `line_chats`, `line_trace`

```
line_state  {"lid": <id>, "state": "open" | "refused" | "closed", "place": "", "reason": "…"}
line_text   {"lid": <id>, "text": …}               a final reply of the receiving side, up to 16,000 characters
line_chats  {"rid": <id>, "ok": true, "chats": ["Suppliers", …]}
line_trace  {"lid": <id>, "text": …}               after a close: a short summary, up to 400 characters
```

`line_state` answers `line_open` and `line_move`, and can arrive by itself when the receiving side closes the line. With `state` `open`, a `reason` of `no_place` means the chat asked for does not exist and the line is where `place` says. Other reasons, all with a state that is not `open`: `not_allowed` (the permission is off, or was turned off), `no_line` (there is no such line). `line_text` carries only finished replies: no partial text, reasoning or progress. Links to the receiving side's pages (`[[notebook/page]]`) travel as plain names, since the other phone cannot open them. `line_trace` is optional and may arrive long after the close, or never.

A side that does not implement the line answers `nack unsupported` to `line_open`, and the passing side closes the line at once and says why.

## Compatibility

- A new **kind** needs no version change: an older side answers `nack unsupported` and the sender drops it.
- A new **optional field** needs no version change.
- Anything that changes a MAC, the box, the order of the handshake or the meaning of an existing field bumps the version, with new vectors in a new file (`peer-v2.json`).

## Related

- [Security model](security-model.md#other-jennys): the rules each side applies on top of this format.
- [Your other Jenny](../using/other-jennys.md): what the user sees.
- [Configuration](../reference/configuration.md#peers): the `peers` keys.
