"""Dove sono posate le app: le schede-app e le sezioni ``app`` dei cassetti.

Un'app è sempre generale (vive in ``workspace/apps``); sulla bacheca di un
quaderno si **posa**, in due modi:

- come **scheda**: la polaroid è l'app (``card["app"] = {slug, params}``);
- come **sezione** nel cassetto di una scheda (``{kind: "app", app, params}``).

Le pose sono viste della stessa app: toglierne una non tocca l'app. E una posa
è anche un **permesso**: la chat di un quaderno può usare (scrivere) le app che
sono posate sulla sua bacheca, e solo quelle (``jenny/apps/permissions.py``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jenny.board.store import read_board


def placements(root: Path) -> list[dict[str, Any]]:
    """Le pose di un quaderno: ``[{app, params, card, section}]``.

    ``section`` è ``None`` per una scheda-app. Le schede tolte dalla bacheca
    (in attesa dell'Annulla) non contano: la loro app non è posata.
    """
    board = read_board(root)
    if board is None:
        return []
    out: list[dict[str, Any]] = []
    for card in board.get("cards") or []:
        app = card.get("app")
        if isinstance(app, dict) and isinstance(app.get("slug"), str):
            out.append({"app": app["slug"], "params": dict(app.get("params") or {}),
                        "card": card["id"], "section": None})
        for section in card.get("userSections") or []:
            if section.get("kind") == "app" and isinstance(section.get("app"), str):
                out.append({"app": section["app"], "params": dict(section.get("params") or {}),
                            "card": card["id"], "section": section["id"]})
    return out


def placed_slugs(root: Path) -> set[str]:
    """Le app posate sulla bacheca del quaderno *root*."""
    return {p["app"] for p in placements(root)}


def all_placements(workspace: Path) -> list[dict[str, Any]]:
    """Le pose di tutti i quaderni, ciascuna con il nome del suo quaderno."""
    from jenny.utils.wiki_paths import discover_wiki_roots

    out: list[dict[str, Any]] = []
    for wikis in _wikis_dirs(workspace):
        for name, root in sorted(discover_wiki_roots(wikis).items()):
            for place in placements(root):
                out.append({**place, "notebook": name})
    return out


def _wikis_dirs(workspace: Path) -> list[Path]:
    try:
        from jenny.config.loader import load_config

        name = getattr(load_config().wiki, "wikis_dir", "wikis") or "wikis"
    except Exception:  # noqa: BLE001 — senza config, il nome di serie
        name = "wikis"
    return [Path(workspace) / name]
