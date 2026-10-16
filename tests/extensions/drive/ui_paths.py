"""Dove stanno i file WebUI dell'estensione Drive, e le traduzioni come le vede
la pagina: quelle del core con sopra le sue (``shared/i18n.js``, ``addSource``)."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CORE_ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"
EXT_UI = ROOT / "jenny_extensions" / "drive" / "ui"


def _merge(base: dict, extra: dict) -> dict:
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def locale(name: str) -> dict:
    """Il dizionario *name* che la pagina usa con l'estensione caricata."""
    core = json.loads((CORE_ASSETS / "i18n" / f"{name}.json").read_text(encoding="utf-8"))
    ext = json.loads((EXT_UI / "i18n" / f"{name}.json").read_text(encoding="utf-8"))
    return _merge(core, ext)
