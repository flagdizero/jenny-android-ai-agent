"""Guardia: il contenuto non torna dentro un header HTTP.

La superficie ``/api/`` del gateway è servita dall'hook di handshake di
``websockets``, che non legge mai il body di una richiesta. Chi doveva spedire
del contenuto ha risposto tre volte con lo stesso trucco — il payload in un
header custom — e in tre dialetti diversi: grezzo (``/api/workspace/write``,
che *non poteva funzionare*: emoji vietate dagli header di ``fetch``, accenti
rotti dai surrogate escape, 8192 byte di tetto per riga), percent-encodato
(``audit.resolve``), base64 (backup).

Ora esiste un solo posto giusto — l'RPC WebSocket (``channels.ws_rpc`` +
``webui.commands``) — e questo test impedisce che il quarto dialetto nasca per
imitazione, come è successo ai primi tre. Stesso ruolo di
``tests/agent/tools/test_asyncssh_is_dev_only.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

from support.js_harness import ASSETS

_REPO = Path(__file__).resolve().parents[2]
_SCANNED = (
    (_REPO / "jenny" / "webui", "*.py"),
    (_REPO / "jenny" / "channels", "*.py"),
    (ASSETS, "*.js"),
)

# Header con un payload dentro: ``X-Jenny-<qualcosa>-Data``.
_PAYLOAD_HEADER_RE = re.compile(r"X-Jenny-[A-Za-z]+-Data", re.IGNORECASE)

# Le uniche coppie (file, header) ammesse. I payload del backup sono piccoli e
# limitati (passphrase, staged_path, snapshot_id, label) e il base64 serve a
# tenere la passphrase fuori dalla query string, dove finirebbe nei log: una
# scelta di sicurezza, non un trucco per la dimensione. Migrabile sull'RPC
# senza design nuovo — quando succede, questa lista va svuotata.
_ALLOWED_PAIRS = {
    ("backup_routes.py", "x-jenny-backup-data"),
    ("api-client.js", "x-jenny-backup-data"),
}


def _violations() -> list[str]:
    found: list[str] = []
    for root, pattern in _SCANNED:
        for path in sorted(root.rglob(pattern)):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for header in _PAYLOAD_HEADER_RE.findall(text):
                pair = (path.name, header.lower())
                if pair not in _ALLOWED_PAIRS:
                    found.append(f"{path.relative_to(_REPO)}: {header}")
    return found


def test_no_payload_travels_in_an_http_header() -> None:
    violations = _violations()
    assert violations == [], (
        "payload in un header HTTP: gli header del gateway stanno in 8192 byte "
        "e in ISO-8859-1. Usa un comando dell'RPC WebSocket (webui/commands.py "
        f"+ channels/ws_rpc.py, lato client rpc-client.js). Trovato: {violations}"
    )


def test_the_backup_exception_is_still_real() -> None:
    """Se il backup migra sull'RPC, ``_ALLOWED_PAIRS`` va svuotata."""
    backup = (_REPO / "jenny" / "webui" / "backup_routes.py").read_text(encoding="utf-8")
    assert _PAYLOAD_HEADER_RE.search(backup), (
        "backup_routes.py non usa più un payload header: togli l'eccezione da "
        "_ALLOWED_PAIRS in questo test"
    )
