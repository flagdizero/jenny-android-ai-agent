"""Le micro app dei cassetti diventano app generali. Una volta, all'avvio.

Fino al 06/10/2026 una micro app viveva nel quaderno
(``<quaderno>/board/micro/<id>/``): un ``run.py`` che l'orologio girava, una
sezione nel cassetto. Adesso è un'app come le altre — con un ``reader`` e un
``summary`` — che vive in ``workspace/apps`` ed è **posata** nel cassetto da
cui veniva. Per l'utente non cambia niente di quel che vede: stessa sezione
(stesso id, quindi stessi eventi e stesse righe «dall'ultima volta»), stesse
letture, stesso stato, stesse attese a condizione.

L'ordine dei passi è quello che rende sicuro ripartire a metà: prima l'app
(cartella e dati), poi la sua approvazione, poi la bacheca e le attese, e solo
alla fine la cartella vecchia va in ``board/micro/.migrated/`` — da lì in poi
:func:`jenny.board.micro.list_micros` non la vede più, e una seconda passata
non trova niente da fare.

L'approvazione passa: l'utente aveva approvato **quel** ``run.py``, e il
manifest intorno lo genera questo codice, non Jenny. Una micro app non
approvata (o ancora in prova) diventa un'app in prova.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.apps.manifest import SLUG_RE

MIGRATED_DIRNAME = ".migrated"


def _taken(workspace: Path) -> set[str]:
    apps = Path(workspace) / "apps"
    names: set[str] = set()
    for sub in (apps, apps / ".drafts"):
        if sub.is_dir():
            names |= {p.name for p in sub.iterdir() if p.is_dir()}
    trash = apps / ".trash"
    if trash.is_dir():
        names |= {p.name.rsplit("-", 1)[0] for p in trash.iterdir()}
    return names


def _slug_for(title: str, taken: set[str]) -> str:
    from jenny.board import model

    base = model.slugify(title, fallback="app")[:32].strip("-") or "app"
    if not SLUG_RE.match(base):
        base = "app"
    slug = base
    n = 2
    while slug in taken:
        suffix = f"-{n}"
        slug = base[:32 - len(suffix)].rstrip("-") + suffix
        n += 1
    taken.add(slug)
    return slug


def manifest_for(spec: dict[str, Any], notebook: str) -> dict[str, Any]:
    """L'``app.json`` di una micro app: il lettore e il riassunto, nient'altro."""
    summary: dict[str, Any] = {"view": spec["view"], "primary": spec["primary"],
                               "staleAfterMinutes": spec["staleAfter"]}
    if spec.get("alarm"):
        summary["alarm"] = spec["alarm"]
    reader: dict[str, Any] = {"every": spec["every"], "fields": spec["fields"]}
    if spec.get("net"):
        reader["net"] = spec["net"]
    if spec.get("uses"):
        reader["uses"] = spec["uses"]
    return {
        "name": spec["title"],
        "description": f"Reads {spec['title']} every {spec['every']} minutes "
                       f"(a micro app of the notebook {notebook} until October 2026).",
        "icon": "ti-activity",
        "reader": reader,
        "summary": summary,
    }


def _move(src: Path, dst: Path) -> None:
    if src.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.replace(src, dst)


def _write_app(target: Path, spec: dict[str, Any], code: str, notebook: str,
               data_from: Path | None) -> None:
    """Scrive la cartella dell'app; se c'e', ci porta dentro i dati della micro app."""
    from jenny.board.micro import LATEST_FILE, READINGS_FILE, STATE_FILE

    (target / "data").mkdir(parents=True, exist_ok=True)
    (target / "app.json").write_text(
        json.dumps(manifest_for(spec, notebook), ensure_ascii=False, indent=2), encoding="utf-8")
    (target / "run.py").write_text(code, encoding="utf-8")
    if data_from is not None:
        _move(data_from / READINGS_FILE, target / "data" / "readings.jsonl")
        _move(data_from / LATEST_FILE, target / "data" / "latest.json")
        _move(data_from / STATE_FILE, target / "data" / "state.json")


def migrate_notebook(workspace: Path, root: Path) -> int:
    """Migra le micro app di un quaderno. Torna quante."""
    from jenny.apps import trust
    from jenny.board import micro as mc
    from jenny.board import model
    from jenny.board.store import mutate_board, mutate_reminders

    ids = mc.list_micros(root)
    drafts_dir = mc.micros_dir(root) / mc.DRAFTS_DIRNAME
    draft_ids = sorted(p.name for p in drafts_dir.iterdir() if p.is_dir()) \
        if drafts_dir.is_dir() else []
    todo = sorted(set(ids) | set(draft_ids))
    if not todo:
        return 0
    taken = _taken(workspace)
    renamed: dict[str, str] = {}
    for micro_id in todo:
        live = mc.load_micro(root, micro_id)
        draft = mc.load_micro(root, micro_id, draft=True)
        base = live or draft
        if base is None:
            continue
        slug = _slug_for(base.spec["title"], taken)
        if live is not None:
            target = trust.app_dir(workspace, slug)
            _write_app(target, live.spec, live.code, root.name, live.dir)
            if mc.is_approved(workspace, root, live):
                trust.approve(workspace, slug)
        if draft is not None:
            target = trust.draft_dir(workspace, slug)
            _write_app(target, draft.spec, draft.code, root.name, draft.dir)
        renamed[micro_id] = slug

    def apply(board: dict[str, Any]) -> None:
        for card in board["cards"]:
            for i, section in enumerate(card["userSections"]):
                if section.get("kind") == "micro" and section.get("micro") in renamed:
                    card["userSections"][i] = {
                        "id": section["id"], "kind": "app",
                        "app": renamed[section["micro"]], "params": {},
                        "created": section.get("created") or model.now_iso(),
                        "migratedFrom": section["micro"],
                        **({"by": section["by"]} if section.get("by") else {}),
                    }

    mutate_board(root, apply)

    def apply_rems(rems: dict[str, Any]) -> None:
        for rem in rems["reminders"]:
            when = rem.get("when") or {}
            if when.get("kind") == "condition" and when.get("micro") in renamed:
                when["app"] = renamed[when.pop("micro")]
                when.setdefault("params", {})

    mutate_reminders(root, apply_rems)
    gone = mc.micros_dir(root) / MIGRATED_DIRNAME
    gone.mkdir(parents=True, exist_ok=True)
    for micro_id in renamed:
        for src in (mc.micro_dir(root, micro_id), mc.micro_dir(root, micro_id, draft=True)):
            if src.exists():
                dst = gone / (src.name + ("-draft" if src.parent.name == mc.DRAFTS_DIRNAME else ""))
                if dst.exists():
                    shutil.rmtree(dst)
                os.replace(src, dst)
        mc.drop_approval(workspace, root, micro_id)
        logger.info("Micro app {}/{} is now the app '{}'", root.name, micro_id, renamed[micro_id])
    return len(renamed)


def migrate_micro_apps(workspace: Path) -> int:
    """Tutti i quaderni. Idempotente; un quaderno rotto non ferma gli altri."""
    from jenny.apps.trust import grandfather
    from jenny.board.placements import _wikis_dirs
    from jenny.utils.wiki_paths import discover_wiki_roots

    # La prova deve essere gia' accesa: un'approvazione scritta qui creerebbe
    # il file delle approvazioni senza le app che c'erano gia'.
    grandfather(workspace)
    total = 0
    for wikis in _wikis_dirs(workspace):
        for name, root in sorted(discover_wiki_roots(wikis).items()):
            try:
                total += migrate_notebook(workspace, root)
            except Exception:  # noqa: BLE001
                logger.opt(exception=True).error("Micro apps of {} not migrated", name)
    return total
