"""Il banco di lavoro del tool ``app``: dove un'app si prepara prima di esistere.

Un'app nasce e cambia **solo se funziona**. Il tool ``app`` scrive i file in
``apps/.drafts/<slug>/`` (la cartella col punto non la vede ``scan_apps``: una
bozza non ha tool e non compare nella pagina App), la valida, gira il lettore
una volta, e se tutto va la **promuove** subito: diventa l'app, o la sua
versione nuova. Se no la bozza resta lì, per correggerla con ``edit``, e l'app
di prima — se c'era — continua a girare com'era.

Non c'è nessuna prova da approvare (tolta il 06/10/2026, decisione
dell'utente): un'app che non piace si butta, e il cestino la tiene.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from jenny.apps.manifest import SLUG_RE, LoadedApp, load_app

DRAFTS_DIRNAME = ".drafts"
# I file che fanno il codice di un'app; il resto (dati, note) no.
CODE_FILES = ("app.json", "run.py")
CODE_DIRS = ("app",)


def app_dir(workspace: Path, slug: str) -> Path:
    return Path(workspace) / "apps" / slug


def draft_dir(workspace: Path, slug: str) -> Path:
    return Path(workspace) / "apps" / DRAFTS_DIRNAME / slug


def _check_slug(slug: str) -> None:
    if not slug or not SLUG_RE.match(slug) or len(slug) > 32:
        raise ValueError(f"invalid app slug {slug!r}")


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


def promote_draft(workspace: Path, slug: str) -> Path:
    """La bozza diventa l'app. Il codice si sostituisce; i dati restano.

    Un'app nuova prende tutta la cartella della bozza, dati compresi (la prima
    lettura vera). Una che c'era tiene i suoi ``data/``: una correzione non
    deve cancellare quel che hai scritto.
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


def promote_valid_drafts(workspace: Path) -> list[str]:
    """All'avvio: le bozze valide diventano app. Torna quali.

    Fino al 06/10/2026 un'app nuova restava **in prova** finché l'utente non
    toccava «Tienila»: chi aggiorna con una prova in sospeso la ritrova app
    vera. Una bozza rotta resta dov'è (è un lavoro a metà di Jenny).
    """
    done = []
    for slug in list_drafts(workspace):
        draft = load_draft(workspace, slug)
        if draft is None or draft.app.broken or draft.app.manifest is None:
            continue
        try:
            promote_draft(workspace, slug)
        except OSError:
            continue
        done.append(slug)
    return done


def _copy_bytes(src: str, dst: str) -> str:
    # Niente ``copy2``: su Android gli xattr SELinux danno EACCES dopo la copia.
    Path(dst).write_bytes(Path(src).read_bytes())
    return dst
