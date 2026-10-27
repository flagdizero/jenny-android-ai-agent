"""Ogni chiave che la WebUI chiede per nome esiste, in tutte e due le lingue.

Una chiave che manca non fa errore: ``i18n.t()`` ritorna la chiave grezza, e
l'utente si ritrova ``home.plan.stopSheet.title`` stampato sullo schermo.
``test_i18n_parity`` prova che ``it.json`` ed ``en.json`` hanno le stesse
chiavi; qui si prova l'altra meta': che le chiavi **usate** ci siano. Prima la
copriva una trentina di test, ognuno con l'elenco delle chiavi di una funzione
scritto a mano, e una chiave nuova non la guardava nessuno.

Si leggono le chiamate letterali ``i18n.t('a.b')`` e gli attributi
``data-i18n*="a.b"``. Una chiave composta a runtime (``'home.plan.' + k``) non
si vede da qui: per quelle restano i test della funzione che la compone.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "jenny" / "templates" / "ui"
EXTENSIONS = ROOT / "jenny_extensions"
LOCALES = ("it", "en")

_JS_KEY = re.compile(r"""\bi18n\.t\(\s*['"]([A-Za-z][\w-]*(?:\.[\w-]+)+)['"]""")
_HTML_KEY = re.compile(r"""\bdata-i18n(?:-[a-z]+)?=["']([A-Za-z][\w-]*(?:\.[\w-]+)+)["']""")


def _locale(folder: Path, lang: str) -> dict:
    return json.loads((folder / f"{lang}.json").read_text(encoding="utf-8"))


def _has(table: dict, key: str) -> bool:
    """La chiave c'e' e dice qualcosa: un valore vuoto a schermo e' un buco
    quanto la chiave grezza."""
    node: object = table
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return not isinstance(node, str) or bool(node.strip())


def _used_keys(folder: Path) -> dict[str, set[str]]:
    used: dict[str, set[str]] = {}
    for path in sorted(folder.rglob("*")):
        if path.suffix not in {".js", ".html"} or "vendor" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in (_JS_KEY, _HTML_KEY):
            for found in pattern.finditer(text):
                used.setdefault(found.group(1), set()).add(path.name)
    return used


def _missing(used: dict[str, set[str]], tables: list[dict]) -> list[str]:
    return sorted(
        f"{key} ({', '.join(sorted(files))})"
        for key, files in used.items()
        if not any(_has(table, key) for table in tables)
    )


def test_every_key_the_webui_names_exists_in_both_locales() -> None:
    used = _used_keys(UI)
    assert len(used) > 500, f"letture sospette: solo {len(used)} chiavi trovate"
    for lang in LOCALES:
        missing = _missing(used, [_locale(UI / "assets" / "i18n", lang)])
        assert not missing, f"{lang}.json non ha:\n  " + "\n  ".join(missing)


def test_every_key_an_extension_names_exists_in_both_locales() -> None:
    """Un'estensione porta il suo ``i18n/``, che si aggiunge a quello del core."""
    for ui in sorted(EXTENSIONS.glob("*/ui")):
        used = _used_keys(ui)
        for lang in LOCALES:
            tables = [_locale(UI / "assets" / "i18n", lang)]
            if (ui / "i18n" / f"{lang}.json").is_file():
                tables.append(_locale(ui / "i18n", lang))
            missing = _missing(used, tables)
            assert not missing, f"{ui.parent.name} {lang}:\n  " + "\n  ".join(missing)


def test_the_check_sees_a_missing_key(tmp_path: Path) -> None:
    (tmp_path / "a.js").write_text("i18n.t('home.nope.never'); i18n.t('common.ok');\n")
    (tmp_path / "b.html").write_text('<b data-i18n-aria="home.also.missing"></b>\n')
    used = _used_keys(tmp_path)
    table = {"common": {"ok": "OK"}, "home": {"nope": {"never": "  "}}}
    assert _missing(used, [table]) == ["home.also.missing (b.html)", "home.nope.never (a.js)"]
