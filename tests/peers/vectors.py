"""I vettori di prova del protocollo fra due Jenny (``docs/internals/peer-protocol.md``).

Valori fissi passati per le funzioni vere di :mod:`jenny.peers`. Un'altra
implementazione del protocollo (il plugin ``opencode-jenny``, in un repository a
parte) li ricalcola dagli stessi ingressi e deve ottenere gli stessi byte: è
così che le due restano compatibili senza leggersi il codice a vicenda.

``scripts/peer_vectors.py`` li scrive in ``tests/peers/vectors/peer-v1.json``;
``test_vectors.py`` controlla che il file sia ancora quello che il codice
produce, così un cambio del protocollo non passa senza rigenerarli.
"""

from __future__ import annotations

import asyncio
import base64
from typing import Any

from jenny.peers import auth, files, model, questions
from jenny.peers.link import ACK_TIMEOUT_S, DONE_TIMEOUT_S, HANDSHAKE_TIMEOUT_S
from jenny.peers.rpc import pair_link
from jenny.peers.store import INBOX_KEEP_MS, RETRY_DELAYS_S, SECRET_BYTES
from jenny.snapshot.crypto import encrypt_container
from jenny.snapshot.crypto_backends.dev import DevAesGcmBackend

# A mostra il codice (e ascolta), B lo scrive (e chiama). Nella sessione dopo
# l'abbinamento chiama B (D, «dialer») e ascolta A (L, «listener»).
A_ID = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"
B_ID = "00112233445566778899aabbccddeeff"
CODE = "K7Q4MZP2"
PAIR_SALT = "000102030405060708090a0b0c0d0e0f"
NONCE_A = "a0a1a2a3a4a5a6a7a8a9aaabacadaeaf"
NONCE_B = "b0b1b2b3b4b5b6b7b8b9babbbcbdbebf"
SECRET = bytes(range(0x40, 0x40 + SECRET_BYTES))
# Il sale e il nonce del contenitore che sigilla il segreto: in produzione sono
# casuali (``encrypt_container`` li genera), qui fissi per avere byte ripetibili.
SEAL_SALT = bytes(range(0x10, 0x20))
SEAL_NONCE = bytes(range(0x20, 0x2C))
NONCE_D = "d0d1d2d3d4d5d6d7d8d9dadbdcdddedf"
NONCE_L = "c0c1c2c3c4c5c6c7c8c9cacbcccdcecf"
MSG_ID = "11111111111111111111111111111111"
QID = "22222222222222222222222222222222"
ANSWER_ID = "33333333333333333333333333333333"
CREATED_MS = 1795000000000

B_HOST = "phone.tail0000.ts.net"
B_IP = "100.64.0.2"
A_HOST = "mac.tail0000.ts.net"
A_IP = "100.64.0.1"
PORT = 18790


def _seal(key: bytes) -> bytes:
    return asyncio.run(encrypt_container(
        key.hex(), SECRET, iterations=1, backend=DevAesGcmBackend(),
        _salt=SEAL_SALT, _nonce=SEAL_NONCE,
    ))


def _frame(frame: dict[str, Any]) -> dict[str, Any]:
    """Il frame e la sua forma sul filo, come la scrive :func:`model.encode`."""
    return {"frame": frame, "wire": model.encode(frame)}


def build() -> dict[str, Any]:
    key = auth.code_key(CODE, PAIR_SALT)
    sealed = _seal(key)

    mac_pair_b = auth.mac(key, "pair-b", B_ID, A_ID, NONCE_B, NONCE_A)
    mac_pair_a = auth.mac(key, "pair-a", A_ID, B_ID, NONCE_A, NONCE_B)
    mac_pair_done = auth.mac(SECRET, "pair-done", B_ID, A_ID, NONCE_B, NONCE_A)
    mac_l = auth.mac(SECRET, "l", A_ID, B_ID, NONCE_D, NONCE_L)
    mac_d = auth.mac(SECRET, "d", B_ID, A_ID, NONCE_D, NONCE_L)

    ask_body = {"qid": QID, "text": "Is the CI of PR 85 green?", "mode": "read"}
    answer_body = {"qid": QID, "status": "ok", "text": "Yes: all 4 checks passed.", "files": []}

    return {
        "version": model.PROTOCOL_VERSION,
        "constants": {
            "path": model.PEER_PATH,
            "max_frame_bytes": model.MAX_FRAME_BYTES,
            "id_hex_chars": 32,
            "pair_code_len": auth.PAIR_CODE_LEN,
            "pair_alphabet": auth.PAIR_ALPHABET,
            "pair_ttl_ms": auth.PAIR_TTL_MS,
            "pair_max_attempts": auth.PAIR_MAX_ATTEMPTS,
            "pair_kdf_iterations": auth._PAIR_KDF_ITERATIONS,
            "salt_bytes": auth.SALT_BYTES,
            "nonce_bytes": auth.NONCE_BYTES,
            "secret_bytes": SECRET_BYTES,
            "seal_kdf_iterations": 1,
            "handshake_timeout_s": HANDSHAKE_TIMEOUT_S,
            "ack_timeout_s": ACK_TIMEOUT_S,
            "done_timeout_s": DONE_TIMEOUT_S,
            "retry_delays_s": list(RETRY_DELAYS_S),
            "inbox_keep_ms": INBOX_KEEP_MS,
            "max_question_chars": questions.MAX_QUESTION_CHARS,
            "max_answer_chars": questions.MAX_ANSWER_CHARS,
            "max_answer_files": questions.MAX_FILES,
            "answer_statuses": list(questions.STATUSES),
            "ask_modes": list(questions.MODES),
            "file_part_bytes": files.PART_BYTES,
            "max_file_bytes": files.MAX_FILE_BYTES,
        },
        "pair_code": {
            "code": CODE,
            "shown": auth.format_pair_code(CODE),
            "normalize": [
                {"input": raw, "output": auth.normalize_pair_code(raw)}
                for raw in ("k7q4-mzp2", " K7Q4 MZP2 ", "K7Q4MZP2", "K7Q4MZP0", "K7Q4MZP", "")
            ],
            "link": pair_link(host=A_HOST, ip=A_IP, port=PORT, code=CODE),
        },
        "mac": {
            "doc": "HMAC-SHA256(key, utf8(label + '\\x1f' + part1 + '\\x1f' + ...)), lowercase hex",
            "example": {
                "key_hex": SECRET.hex(),
                "label": "l",
                "parts": [A_ID, B_ID, NONCE_D, NONCE_L],
                "message_utf8": "\x1f".join(("l", A_ID, B_ID, NONCE_D, NONCE_L)),
                "mac": mac_l,
            },
        },
        "container": {
            "doc": "JNBK v1: 'JNBK' | 0x01 | uint32 BE iterations | salt[16] | nonce[12] | "
                   "AES-256-GCM(key=PBKDF2-SHA256(utf8(passphrase), salt, iterations, 32), "
                   "nonce, plaintext, aad=header) as ciphertext||tag[16]",
            "passphrase": key.hex(),
            "iterations": 1,
            "salt_hex": SEAL_SALT.hex(),
            "nonce_hex": SEAL_NONCE.hex(),
            "plaintext_hex": SECRET.hex(),
            "container_b64": base64.b64encode(sealed).decode("ascii"),
        },
        "pairing": {
            "shower_id": A_ID,
            "joiner_id": B_ID,
            "code": CODE,
            "salt": PAIR_SALT,
            "code_key_hex": key.hex(),
            "secret_hex": SECRET.hex(),
            "frames": [
                _frame({
                    "type": "pair", "v": model.PROTOCOL_VERSION, "from": B_ID, "nonce": NONCE_B,
                    "name": "phone", "host": B_HOST, "ip": B_IP, "port": PORT,
                }),
                _frame({"type": "pair_challenge", "from": A_ID, "nonce": NONCE_A,
                        "salt": PAIR_SALT}),
                _frame({"type": "pair_proof", "mac": mac_pair_b}),
                _frame({"type": "pair_ok", "mac": mac_pair_a,
                        "sealed": base64.b64encode(sealed).decode("ascii")}),
                _frame({"type": "pair_done", "mac": mac_pair_done}),
            ],
        },
        "session": {
            "dialer_id": B_ID,
            "listener_id": A_ID,
            "secret_hex": SECRET.hex(),
            "frames": [
                _frame({"type": "hello", "v": model.PROTOCOL_VERSION, "from": B_ID, "to": A_ID,
                        "nonce": NONCE_D}),
                _frame({"type": "challenge", "nonce": NONCE_L, "mac": mac_l}),
                _frame({"type": "proof", "mac": mac_d}),
                _frame({"type": "ready"}),
            ],
        },
        "exchange": {
            "frames": [
                _frame({"type": "msg", "id": MSG_ID, "kind": "ask", "body": ask_body,
                        "created_ms": CREATED_MS}),
                _frame({"type": "ack", "id": MSG_ID}),
                _frame({"type": "done"}),
                _frame({"type": "msg", "id": ANSWER_ID, "kind": "answer", "body": answer_body,
                        "created_ms": CREATED_MS + 4000}),
                _frame({"type": "nack", "id": ANSWER_ID, "reason": "unsupported"}),
                _frame({"type": "done"}),
            ],
        },
    }
