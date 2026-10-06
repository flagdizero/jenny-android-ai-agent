"""La prova delle app: niente gira finché l'utente non tocca «Tienila».

Fino al 06/10/2026 un'app esisteva appena Jenny ne scriveva i file: i suoi tool
comparivano al turno dopo, lo schermo si apriva. Le micro app invece nascevano
**in prova**, e giravano solo col codice che l'utente aveva approvato. Adesso è
così per tutte: la linea di fiducia non è più il posto da cui la chiedi (la
chat personale o quella di un quaderno), ma **la tua approvazione**.

Le regole:

- **L'impronta** (:func:`code_digest`) è quella del codice: ``app.json``,
  ``run.py`` e tutto ``app/``. I dati (``data/``) e le note per Jenny
  (``AGENT.md``) no: cambiano da soli, e non sono codice.
- **Il contratto** (:func:`contract_atoms`) è cosa l'app tocca e raggiunge, in
  atomi: un host, una collezione che scrive, un'azione con la sua forma, il
  riassunto. Una modifica i cui atomi stanno dentro quelli approvati va subito
  (è una correzione); una che ne aggiunge torna in prova.
- **Le approvazioni** stanno fuori dal workspace (accanto a quelle delle micro
  app e alla chiave SSH), dove gli strumenti di Jenny non arrivano. Le scrive
  solo l'RPC ``apps.keep``, che preme una persona.
- **Le bozze** stanno in ``apps/.drafts/<slug>/``: la cartella col punto non la
  vede ``scan_apps``, quindi una bozza non ha tool e non si apre come app.

Il mondo di prima: finché il file delle approvazioni non esiste, ogni app è
viva. All'avvio il gateway lo crea approvando le app che ci sono già
(:func:`grandfather`): l'aggiornamento non ferma niente di quello che usavi.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from jenny.apps.manifest import SLUG_RE, AppManifest, LoadedApp, load_app, scan_apps

DRAFTS_DIRNAME = ".drafts"
APPROVALS_FILE = "apps.json"
# I file che fanno il codice di un'app; il resto (dati, note) no.
CODE_FILES = ("app.json", "run.py")
CODE_DIRS = ("app",)
_MUTATING = {"append", "set", "update", "delete"}

_lock = threading.Lock()


# ── dove sta ────────────────────────────────────────────────────────────────


def approvals_path(workspace: Path) -> Path:
    """Fuori dal workspace, accanto alle approvazioni delle micro app.

    Il nome porta quello del workspace quando non e' ``workspace`` (sul
    telefono lo e' sempre): due workspace fratelli — i banchi di prova, che
    ne fanno uno per test nella stessa cartella — non devono condividere le
    approvazioni, ne' accendersi la prova a vicenda.
    """
    from jenny.board.micro import approvals_path as micro_approvals

    name = Path(workspace).resolve().name
    file = APPROVALS_FILE if name == "workspace" else f"apps-{name}.json"
    return micro_approvals(workspace).with_name(file)


def app_dir(workspace: Path, slug: str) -> Path:
    return Path(workspace) / "apps" / slug


def draft_dir(workspace: Path, slug: str) -> Path:
    return Path(workspace) / "apps" / DRAFTS_DIRNAME / slug


def _check_slug(slug: str) -> None:
    if not slug or not SLUG_RE.match(slug) or len(slug) > 32:
        raise ValueError(f"invalid app slug {slug!r}")


# ── l'impronta e il contratto ───────────────────────────────────────────────


def _code_files(d: Path) -> list[Path]:
    files = [d / name for name in CODE_FILES if (d / name).is_file()]
    for sub in CODE_DIRS:
        root = d / sub
        if root.is_dir():
            files.extend(p for p in sorted(root.rglob("*")) if p.is_file())
    return files


def code_digest(d: Path) -> str:
    """L'impronta del codice di un'app (o di una bozza)."""
    h = hashlib.sha256()
    for path in _code_files(d):
        rel = path.relative_to(d).as_posix()
        h.update(rel.encode("utf-8") + b"\0")
        try:
            h.update(path.read_bytes())
        except OSError:
            h.update(b"<unreadable>")
        h.update(b"\0")
    return "sha256:" + h.hexdigest()


def _sha(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


def contract_atoms(manifest: AppManifest) -> set[str]:
    """Cosa l'app tocca e raggiunge, in atomi confrontabili."""
    atoms: set[str] = set()
    if manifest.server_base_url:
        atoms.add(f"host:{(urlparse(manifest.server_base_url).hostname or '').lower()}")
    if manifest.view_kind == "external":
        atoms.add("screen:external")
    for action in manifest.actions:
        if action.kind == "storage" and action.op in _MUTATING:
            atoms.add(f"write:{action.collection}")
        atoms.add("action:{}:{}".format(action.name, _sha({
            "kind": action.kind, "op": action.op, "collection": action.collection,
            "method": action.method, "path": action.path, "params": action.params,
            "required": action.required,
        })))
    reader = manifest.reader
    if reader is not None:
        for host in reader["net"]:
            atoms.add(f"net:{host}")
        for skill in reader["uses"]:
            atoms.add(f"skill:{skill}")
        atoms.add("reader:" + _sha({
            k: {"type": v["type"], "unit": v.get("unit", "")} for k, v in reader["fields"].items()
        }))
    section = manifest.section
    if section is not None:
        atoms.add("summary:" + _sha({
            "view": section.view, "field": section.field, "collection": section.collection,
            "aggregate": section.aggregate, "params": sorted(section.params),
            "unit": section.unit,
        }))
    return atoms


def describe_atoms(atoms: set[str] | list[str]) -> dict[str, list[str]]:
    """Gli atomi in tre righe che si leggono: cosa guarda, cosa tocca, dove va."""
    out: dict[str, list[str]] = {"reaches": [], "writes": [], "skills": [], "actions": []}
    for atom in sorted(atoms):
        kind, _, rest = atom.partition(":")
        if kind in ("host", "net"):
            out["reaches"].append(rest)
        elif kind == "write":
            out["writes"].append(rest)
        elif kind == "skill":
            out["skills"].append(rest)
        elif kind == "action":
            out["actions"].append(rest.split(":", 1)[0])
    return out


# ── le approvazioni ─────────────────────────────────────────────────────────


def trust_on(workspace: Path) -> bool:
    """Vero da quando il file delle approvazioni esiste (v. :func:`grandfather`)."""
    return approvals_path(workspace).is_file()


def read_approvals(workspace: Path) -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(approvals_path(workspace).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_approvals(workspace: Path, data: dict[str, Any]) -> None:
    path = approvals_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)
    _cache.clear()


def approve(workspace: Path, slug: str, *, grandfathered: bool = False) -> None:
    """Approva il codice che l'app ha adesso, con il suo contratto."""
    d = app_dir(workspace, slug)
    loaded = load_app(d)
    atoms = sorted(contract_atoms(loaded.manifest)) if loaded.manifest else []
    with _lock:
        data = read_approvals(workspace)
        entry: dict[str, Any] = {
            "codeHash": code_digest(d), "atoms": atoms,
            "approvedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        if grandfathered:
            entry["grandfathered"] = True
        data[slug] = entry
        _write_approvals(workspace, data)


def drop_approval(workspace: Path, slug: str) -> None:
    with _lock:
        data = read_approvals(workspace)
        if data.pop(slug, None) is not None:
            _write_approvals(workspace, data)


def grandfather(workspace: Path) -> int:
    """La prima volta: approva le app che ci sono già. Torna quante.

    Chiamata all'avvio, prima che l'agente esista. Se il file c'è già non fa
    niente: un'app comparsa dopo, fuori da Jenny, resta da approvare.
    """
    if trust_on(workspace):
        return 0
    n = 0
    with _lock:
        data: dict[str, Any] = {}
        for app in scan_apps(workspace):
            if app.broken or app.manifest is None:
                continue
            data[app.slug] = {
                "codeHash": code_digest(app.dir),
                "atoms": sorted(contract_atoms(app.manifest)),
                "approvedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "grandfathered": True,
            }
            n += 1
        _write_approvals(workspace, data)
    return n


# La risposta di :func:`is_live`, per slug, finché né il codice né le
# approvazioni cambiano: le rotte statiche la chiedono a ogni file servito.
_cache: dict[tuple[str, str], tuple[tuple[int, ...], bool]] = {}


def _stamp(workspace: Path, d: Path) -> tuple[int, ...]:
    stamps: list[int] = []
    for path in [approvals_path(workspace), *_code_files(d)]:
        try:
            st = path.stat()
            stamps.extend((st.st_mtime_ns, st.st_size))
        except OSError:
            stamps.append(-1)
    return tuple(stamps)


def is_live(workspace: Path, slug: str) -> bool:
    """L'app gira: il suo codice è quello approvato (o la prova non c'è ancora)."""
    if not trust_on(workspace):
        return True
    d = app_dir(workspace, slug)
    key = (str(workspace), slug)
    stamp = _stamp(workspace, d)
    hit = _cache.get(key)
    if hit is not None and hit[0] == stamp:
        return hit[1]
    entry = read_approvals(workspace).get(slug)
    live = bool(entry) and entry.get("codeHash") == code_digest(d)
    _cache[key] = (stamp, live)
    return live


def approved_atoms(workspace: Path, slug: str) -> set[str] | None:
    entry = read_approvals(workspace).get(slug)
    return set(entry.get("atoms") or []) if entry else None


# ── le bozze ────────────────────────────────────────────────────────────────


@dataclass
class Draft:
    slug: str
    dir: Path
    app: LoadedApp


def load_draft(workspace: Path, slug: str) -> Draft | None:
    d = draft_dir(workspace, slug)
    if not (d / "app.json").is_file():
        return None
    return Draft(slug=slug, dir=d, app=load_app(d))


def list_drafts(workspace: Path) -> list[str]:
    root = Path(workspace) / "apps" / DRAFTS_DIRNAME
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and SLUG_RE.match(p.name))


def discard_draft(workspace: Path, slug: str) -> bool:
    _check_slug(slug)
    d = draft_dir(workspace, slug)
    if not d.exists():
        return False
    shutil.rmtree(d)
    return True


def promote_draft(workspace: Path, slug: str) -> Path:
    """La bozza diventa l'app. Il codice si sostituisce; i dati restano.

    Un'app nuova prende tutta la cartella della bozza, dati della prova
    compresi (la prima lettura vera). Una che c'era tiene i suoi ``data/``:
    una correzione non deve cancellare quel che hai scritto.
    """
    _check_slug(slug)
    src = draft_dir(workspace, slug)
    if not (src / "app.json").is_file():
        raise FileNotFoundError(f"no draft for '{slug}'")
    dst = app_dir(workspace, slug)
    if not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.replace(src, dst)
        return dst
    for name in (*CODE_FILES, "AGENT.md"):
        target = dst / name
        if (src / name).is_file():
            target.write_bytes((src / name).read_bytes())
        elif name == "run.py" and target.exists():
            target.unlink()
    for sub in CODE_DIRS:
        if (dst / sub).exists():
            shutil.rmtree(dst / sub)
        if (src / sub).is_dir():
            shutil.copytree(src / sub, dst / sub, copy_function=_copy_bytes)
    shutil.rmtree(src)
    return dst


def _copy_bytes(src: str, dst: str) -> str:
    # Niente ``copy2``: su Android gli xattr SELinux danno EACCES dopo la copia.
    Path(dst).write_bytes(Path(src).read_bytes())
    return dst


# ── lo stato per l'interfaccia ──────────────────────────────────────────────


def app_trust_state(workspace: Path, slug: str) -> dict[str, Any]:
    """``state``: ``live``, ``trial`` (solo bozza), ``changed`` (viva, con una
    modifica in prova) o ``unapproved`` (il codice è cambiato fuori da Jenny).
    Con una bozza, ``trial`` dice cosa tocca: ``{reaches, writes, skills,
    actions, new}`` (``new``: gli atomi che la modifica aggiunge)."""
    draft = load_draft(workspace, slug)
    live_exists = (app_dir(workspace, slug) / "app.json").is_file()
    out: dict[str, Any] = {}
    if live_exists:
        out["state"] = "live" if is_live(workspace, slug) else "unapproved"
    if draft is not None:
        out["state"] = "changed" if live_exists else "trial"
        atoms = contract_atoms(draft.app.manifest) if draft.app.manifest else set()
        before = approved_atoms(workspace, slug) or set()
        out["trial"] = {
            **describe_atoms(atoms),
            "new": sorted(atoms - before) if live_exists else [],
            "broken": draft.app.broken, "error": draft.app.error,
            "reading": _first_reading(workspace, draft),
        }
    out.setdefault("state", "missing")
    return out


def _first_reading(workspace: Path, draft: Draft) -> dict[str, Any] | None:
    """La prima lettura vera della prova, per il foglio «in prova»."""
    if draft.app.broken or draft.app.manifest is None or draft.app.manifest.section is None:
        return None
    from jenny.apps.summary_view import summary_view

    view = summary_view(workspace, draft.app, {})
    if view.get("value") is None:
        return None
    return {"value": view["value"], "unit": view.get("unit", ""),
            "label": view.get("label", ""), "source": view.get("source", ""),
            "at": view.get("at", "")}
