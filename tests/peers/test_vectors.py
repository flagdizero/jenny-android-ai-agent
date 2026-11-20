"""I vettori di prova del protocollo sono ancora quelli che il codice produce.

Il file ``vectors/peer-v1.json`` è il contratto con le altre implementazioni del
protocollo (il plugin ``opencode-jenny``): se il codice cambia i byte e il file
no, una delle due parti smette di capire l'altra senza che nessun test di qui
se ne accorga. Rigenerarlo è ``python scripts/peer_vectors.py``.
"""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

from jenny.peers import auth, model
from jenny.snapshot.crypto import decrypt_container
from jenny.snapshot.crypto_backends.dev import DevAesGcmBackend
from tests.peers.vectors import build

VECTORS = Path(__file__).parent / "vectors" / "peer-v1.json"


def _load() -> dict:
    return json.loads(VECTORS.read_text(encoding="utf-8"))


def test_the_file_is_what_the_code_produces() -> None:
    assert _load() == build(), "protocol changed: run `python scripts/peer_vectors.py`"


def test_every_frame_is_a_valid_frame_of_its_type() -> None:
    data = _load()
    for section in ("pairing", "session", "exchange"):
        for item in data[section]["frames"]:
            frame = model.decode(item["wire"], item["frame"]["type"])
            assert frame == item["frame"]


def test_the_sealed_secret_opens_with_the_code_key() -> None:
    data = _load()
    pairing = data["pairing"]
    sealed = base64.b64decode(pairing["frames"][3]["frame"]["sealed"])
    secret = asyncio.run(
        decrypt_container(pairing["code_key_hex"], sealed, backend=DevAesGcmBackend())
    )
    assert secret.hex() == pairing["secret_hex"]


def test_each_side_accepts_the_other_sides_macs() -> None:
    data = _load()
    p = data["pairing"]
    key = bytes.fromhex(p["code_key_hex"])
    secret = bytes.fromhex(p["secret_hex"])
    a, b = p["shower_id"], p["joiner_id"]
    nonce_b = p["frames"][0]["frame"]["nonce"]
    nonce_a = p["frames"][1]["frame"]["nonce"]
    assert auth.mac_ok(key, p["frames"][2]["frame"]["mac"], "pair-b", b, a, nonce_b, nonce_a)
    assert auth.mac_ok(key, p["frames"][3]["frame"]["mac"], "pair-a", a, b, nonce_a, nonce_b)
    assert auth.mac_ok(
        secret, p["frames"][4]["frame"]["mac"], "pair-done", b, a, nonce_b, nonce_a
    )
    s = data["session"]
    d, listener = s["dialer_id"], s["listener_id"]
    nonce_d = s["frames"][0]["frame"]["nonce"]
    nonce_l = s["frames"][1]["frame"]["nonce"]
    assert auth.mac_ok(secret, s["frames"][1]["frame"]["mac"], "l", listener, d, nonce_d, nonce_l)
    assert auth.mac_ok(secret, s["frames"][2]["frame"]["mac"], "d", d, listener, nonce_d, nonce_l)
    # Le etichette dei due ruoli non si scambiano: la prova di uno non vale per l'altro.
    assert not auth.mac_ok(
        secret, s["frames"][1]["frame"]["mac"], "d", d, listener, nonce_d, nonce_l
    )


def test_the_code_key_is_pbkdf2_of_the_code() -> None:
    p = _load()["pairing"]
    assert auth.code_key(p["code"], p["salt"]).hex() == p["code_key_hex"]
