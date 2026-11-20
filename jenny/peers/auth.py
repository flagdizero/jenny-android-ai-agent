"""Chi è dall'altra parte: il codice d'abbinamento e la prova di ogni connessione.

**Abbinamento.** La Jenny A mostra un codice monouso (8 caratteri, 10 minuti,
5 tentativi). La Jenny B lo riceve dall'utente (QR o a mano) e si collega:

1. B → A ``pair {from, nonce}``
2. A → B ``pair_challenge {from, nonce, salt}``
3. B → A ``pair_proof {mac}`` — HMAC con la chiave derivata dal codice
4. A → B ``pair_ok {mac, sealed}`` — A prova di conoscere lo stesso codice e
   manda il segreto nuovo, chiuso con quella chiave
5. B → A ``pair_done {mac}`` — B prova di aver aperto il segreto

Il codice non viaggia mai: viaggiano solo HMAC. La connessione è già dentro
WireGuard, quindi nessuno li vede passare; la derivazione PBKDF2 è la seconda
cintura, per chi riuscisse a vederli.

**Ogni connessione dopo.** Chi chiama (D) e chi ascolta (L) si provano a vicenda
il segreto con due HMAC su due nonce freschi:

1. D → L ``hello {from, to, nonce}``
2. L → D ``challenge {nonce, mac}``
3. D → L ``proof {mac}``
4. L → D ``ready``

Ogni MAC porta un'etichetta diversa per ruolo, così nessuno dei due può
rispedire all'altro la sua stessa prova.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import string
from typing import TypeGuard

PAIR_CODE_LEN = 8
# Senza 0/O, 1/I/L: si leggono e si dettano senza ambiguità.
PAIR_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
PAIR_TTL_MS = 10 * 60 * 1000
PAIR_MAX_ATTEMPTS = 5
_PAIR_KDF_ITERATIONS = 200_000
SALT_BYTES = 16
NONCE_BYTES = 16


def new_pair_code() -> str:
    return "".join(secrets.choice(PAIR_ALPHABET) for _ in range(PAIR_CODE_LEN))


def normalize_pair_code(raw: str) -> str:
    """Il codice come l'ha scritto l'utente, ricondotto alla forma canonica.

    Toglie spazi e trattini, porta in maiuscolo; ``""`` se non è un codice.
    """
    cleaned = "".join(ch for ch in (raw or "").upper() if ch not in string.whitespace + "-")
    if len(cleaned) != PAIR_CODE_LEN or any(ch not in PAIR_ALPHABET for ch in cleaned):
        return ""
    return cleaned


def format_pair_code(code: str) -> str:
    """``K7Q4MZP2`` → ``K7Q4-MZP2``: come lo si mostra."""
    half = len(code) // 2
    return f"{code[:half]}-{code[half:]}"


def new_nonce() -> str:
    return secrets.token_hex(NONCE_BYTES)


def new_salt() -> str:
    return secrets.token_hex(SALT_BYTES)


def code_key(code: str, salt_hex: str) -> bytes:
    """La chiave che il codice e il sale danno. Lenta di proposito: va in un thread."""
    return hashlib.pbkdf2_hmac(
        "sha256", code.encode("ascii"), bytes.fromhex(salt_hex), _PAIR_KDF_ITERATIONS
    )


def mac(key: bytes, label: str, *parts: str) -> str:
    message = "\x1f".join((label, *parts)).encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def mac_ok(key: bytes, supplied: object, label: str, *parts: str) -> bool:
    if not isinstance(supplied, str):
        return False
    return hmac.compare_digest(supplied, mac(key, label, *parts))


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def unb64(text: object) -> bytes | None:
    if not isinstance(text, str):
        return None
    try:
        return base64.b64decode(text, validate=True)
    except ValueError:
        return None


def is_hex(value: object, size: int) -> TypeGuard[str]:
    if not isinstance(value, str) or len(value) != size * 2:
        return False
    try:
        bytes.fromhex(value)
    except ValueError:
        return False
    return True
