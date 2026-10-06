"""Il cestino delle app: «Elimina» sposta, e solo «Svuota» cancella.

Fino al 06/10/2026 eliminare un'app faceva ``rmtree`` della sua cartella, dati
compresi, senza annulla: il buco di fiducia più grosso delle app. Adesso
l'app va in ``apps/.trash/<slug>-<UTC>/`` così com'è (codice e ``data/``), e
da lì si rimette o si butta per sempre.

La cartella comincia con un punto, quindi né :func:`jenny.apps.manifest.scan_apps`
né le rotte statiche la vedono: un'app nel cestino non ha tool, non si apre e
non compare nella griglia.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jenny.apps.manifest import SLUG_RE

TRASH_DIRNAME = ".trash"
# ``<slug>-<YYYYMMDDTHHMMSSZ>``: lo slug davanti, cosi' il cestino si legge.
_ENTRY_RE = re.compile(r"\A(?P<slug>[a-z0-9]+(?:-[a-z0-9]+)*)-(?P<at>\d{8}T\d{6}Z)\Z")


def trash_dir(workspace: Path) -> Path:
    return Path(workspace) / "apps" / TRASH_DIRNAME


def _app_dir(workspace: Path, slug: str) -> Path:
    if not slug or SLUG_RE.match(slug) is None:
        raise ValueError("invalid app slug")
    apps_root = (Path(workspace) / "apps").resolve()
    app_dir = (apps_root / slug).resolve()
    if app_dir.parent != apps_root:
        raise ValueError("invalid app slug")
    return app_dir


def trash_app(workspace: Path, slug: str, *, now: datetime | None = None) -> str:
    """Sposta l'app nel cestino; torna l'id della voce (``<slug>-<UTC>``)."""
    app_dir = _app_dir(workspace, slug)
    if not app_dir.is_dir():
        raise FileNotFoundError(f"app '{slug}' not found")
    at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    target_root = trash_dir(workspace)
    target_root.mkdir(parents=True, exist_ok=True)
    # Due eliminazioni dello stesso slug nello stesso secondo: la seconda va un
    # secondo piu' in la', e la voce resta leggibile.
    while (target_root / f"{slug}-{at.strftime('%Y%m%dT%H%M%SZ')}").exists():
        at += timedelta(seconds=1)
    entry = f"{slug}-{at.strftime('%Y%m%dT%H%M%SZ')}"
    os.replace(app_dir, target_root / entry)
    return entry


def _entry_dir(workspace: Path, trash_id: str) -> tuple[Path, str]:
    match = _ENTRY_RE.match(trash_id or "")
    if match is None:
        raise ValueError("invalid trash id")
    path = trash_dir(workspace) / trash_id
    if not path.is_dir():
        raise FileNotFoundError(f"'{trash_id}' is not in the trash")
    return path, match.group("slug")


def restore_app(workspace: Path, trash_id: str) -> str:
    """Rimette un'app dal cestino al suo slug; ``FileExistsError`` se è preso."""
    path, slug = _entry_dir(workspace, trash_id)
    target = _app_dir(workspace, slug)
    if target.exists():
        raise FileExistsError(f"an app called '{slug}' already exists")
    os.replace(path, target)
    return slug


def purge_app(workspace: Path, trash_id: str) -> str:
    """Cancella per sempre una voce del cestino. È l'unico ``rmtree`` delle app."""
    path, slug = _entry_dir(workspace, trash_id)
    shutil.rmtree(path)
    return slug


def _records(path: Path) -> int:
    count = 0
    for coll in (path / "data").glob("*.jsonl"):
        try:
            count += sum(1 for line in coll.read_text(encoding="utf-8").splitlines() if line.strip())
        except OSError:
            continue
    return count


def list_trash(workspace: Path) -> list[dict]:
    """Le voci del cestino, la più recente per prima: ``{id, slug, name, at, records}``."""
    root = trash_dir(workspace)
    if not root.is_dir():
        return []
    out = []
    for path in root.iterdir():
        match = _ENTRY_RE.match(path.name)
        if match is None or not path.is_dir():
            continue
        # Il nome dal manifest letto a mano: ``load_app`` vuole che la cartella
        # sia uno slug, e quella del cestino non lo e'.
        try:
            raw = json.loads((path / "app.json").read_text(encoding="utf-8"))
            name = str(raw.get("name") or "") if isinstance(raw, dict) else ""
        except (OSError, ValueError):
            name = ""
        at = datetime.strptime(match.group("at"), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        out.append({
            "id": path.name, "slug": match.group("slug"), "name": name or match.group("slug"),
            "at": at.isoformat(timespec="seconds"), "records": _records(path),
        })
    out.sort(key=lambda e: e["at"], reverse=True)
    return out


def trashed_entry(workspace: Path, slug: str) -> dict | None:
    """La voce più recente del cestino per *slug*, o ``None``."""
    return next((e for e in list_trash(workspace) if e["slug"] == slug), None)
