import contextlib
import json
import secrets
from pathlib import Path

from jenny.utils.path import atomic_write, read_json

_TOKEN_ISSUE_SECRET_KEYS = ("token_issue_secret", "tokenIssueSecret")


def ensure_minimal_config(workspace_path: Path) -> None:
    """Create a minimal gateway config in the workspace if none exists.

    Also backfills a per-install ``websocket.token_issue_secret`` when none is
    configured. Without it, ``/webui/bootstrap`` falls back to a loopback-only
    check — but Android does not isolate loopback TCP sockets between apps,
    so any app on the device could mint a fully privileged API token. The
    secret is generated once and persisted into ``config.json`` (workspace
    storage, private to this app's Android UID), so it survives restarts and
    is never sent back out over the network by this function.

    Resta fuori dal funnel di :mod:`jenny.config.store`: gira **prima**
    dell'event loop del gateway, quando nessun altro scrittore esiste, quindi
    non serve il lock asincrono. Le scritture sono comunque atomiche, e
    lavorano sul JSON grezzo — così non toccano le chiavi che lo schema non
    conosce.
    """
    config_path = workspace_path / "config.json"

    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        minimal = {
            "gateway": {"host": "127.0.0.1"},
            "websocket": {
                "enabled": True,
                "token_issue_secret": secrets.token_urlsafe(32),
            },
        }
        write_private_file(config_path, json.dumps(minimal, indent=2))
        return

    restrict_config_permissions(config_path)
    _backfill_token_issue_secret(config_path)


def _backfill_token_issue_secret(config_path: Path) -> None:
    """Add a generated ``websocket.token_issue_secret`` to an existing config.

    No-ops if the config already has a non-empty ``token`` or
    ``token_issue_secret`` — an explicit operator choice is never overwritten.
    """
    # Un file illeggibile (anche un byte non UTF-8) lo lascia a ``config.loader``.
    data = read_json(config_path)
    if not isinstance(data, dict):
        return

    websocket = data.get("websocket")
    if not isinstance(websocket, dict):
        websocket = {}

    existing_secret = ""
    for key in _TOKEN_ISSUE_SECRET_KEYS:
        existing_secret = str(websocket.get(key) or "").strip()
        if existing_secret:
            break
    existing_token = str(websocket.get("token") or "").strip()
    if existing_secret or existing_token:
        return

    websocket["token_issue_secret"] = secrets.token_urlsafe(32)
    data["websocket"] = websocket
    try:
        write_private_file(config_path, json.dumps(data, indent=2))
    except OSError:
        return


def restrict_config_permissions(config_path: Path) -> None:
    """Best-effort: keep config.json (holds the bootstrap secret) unreadable
    by other local users. A no-op quirk on some filesystems (e.g. FAT); the
    real isolation boundary on Android is the per-app UID sandbox.

    Va richiamata dopo *ogni* scrittura del file: ``atomic_write`` sostituisce
    l'inode, quindi i permessi del file precedente non si conservano da soli.
    Vale anche per il backup, che porta gli stessi segreti."""
    with contextlib.suppress(OSError):
        config_path.chmod(0o600)


def write_private_file(path: Path, content: str, *, fsync_dir: bool = True) -> None:
    """Scrive *path* atomicamente, gia' in ``600`` quando compare col suo nome.

    ``atomic_write`` e poi :func:`restrict_config_permissions` lasciavano una
    finestra in cui ``config.json`` (o il suo ``.bak``), segreti compresi,
    esisteva con i permessi di default: il ``chmod`` ora si fa sul temporaneo,
    prima della rename. Resta best-effort come prima: su un filesystem che
    rifiuta ``chmod`` (FAT) si riscrive senza, e i permessi si tentano dopo.
    """
    try:
        atomic_write(path, content, fsync_dir=fsync_dir, chmod=0o600)
    except PermissionError:
        atomic_write(path, content, fsync_dir=fsync_dir)
        restrict_config_permissions(path)
