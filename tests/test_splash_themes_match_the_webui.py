"""Lo splash di sistema ha lo sfondo del tema scelto nella WebUI.

Da Android 13 ``MainActivity.syncSplashTheme`` sceglie lo splash del lancio
successivo confrontando il ``--bg`` del tema attivo con gli sfondi degli stili
di ``values-v31/themes.xml``. Quei colori sono quindi una copia del CSS, e una
copia si disallinea in silenzio: il ``--bg`` cambia, nessuno stile combacia, e
lo splash torna a quello di default senza che niente si rompa a vista. Qui si
tiene ferma la copia — un tema per stile, stesso colore, stesso schema.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from support.js_harness import ASSETS
from support.kotlin_source import read_code

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "android/app/src/main/res"
SPLASH_XML = RES / "values-v31/themes.xml"
CSS = ASSETS / "mobile-style.css"
THEME_JS = ASSETS / "shared/theme.js"
MAIN_ACTIVITY = ROOT / "android/app/src/main/java/com/flagdizero/jenny/MainActivity.kt"
PREFIX = "Theme.Jenny.Splash."


def _css_block(selector: str) -> str:
    css = CSS.read_text(encoding="utf-8")
    m = re.search(re.escape(selector) + r"\s*\{(.*?)\}", css, re.S)
    assert m, f"blocco {selector} non trovato in mobile-style.css"
    return m.group(1)


def _css_var(block: str, name: str) -> str | None:
    m = re.search(rf"{re.escape(name)}\s*:\s*([^;]+);", block)
    return m.group(1).strip().lower() if m else None


def _webui_themes() -> dict[str, tuple[str, str]]:
    """``{id: (bg, scheme)}`` per ogni tema di ``shared/theme.js``.

    Un tema senza ``--bg`` proprio eredita quello di ``:root`` (è il caso di
    ``chanel``, il default)."""
    root_bg = _css_var(_css_block(":root"), "--bg")
    js = THEME_JS.read_text(encoding="utf-8")
    themes = {}
    for theme_id, scheme in re.findall(r"\{ id: '([a-z0-9]+)',.*?scheme: '(dark|light)'", js, re.S):
        bg = _css_var(_css_block(f'[data-theme="{theme_id}"]'), "--bg") or root_bg
        themes[theme_id] = (bg, scheme)
    return themes


def _splash_styles() -> dict[str, dict[str, str]]:
    tree = ET.parse(SPLASH_XML)
    styles = {}
    for style in tree.getroot().iter("style"):
        name = style.get("name", "")
        if name.startswith(PREFIX):
            styles[name[len(PREFIX):]] = {
                item.get("name", "").removeprefix("android:"): (item.text or "").strip().lower()
                for item in style.iter("item")
            }
    return styles


@pytest.fixture(scope="module")
def themes() -> dict[str, tuple[str, str]]:
    found = _webui_themes()
    assert len(found) >= 7, found
    return found


def test_every_webui_theme_has_a_splash_with_its_background(themes) -> None:
    splashes = _splash_styles()
    assert {t.lower() for t in splashes} == set(themes), (
        f"splash {sorted(splashes)} contro temi {sorted(themes)}"
    )
    for name, items in splashes.items():
        bg, scheme = themes[name.lower()]
        assert items.get("windowSplashScreenBackground") == bg, (
            f"lo splash {name} ha {items.get('windowSplashScreenBackground')}, il tema {bg}"
        )
        light = scheme == "light"
        assert (items.get("windowLightStatusBar") == "true") == light, name
        assert (items.get("windowLightNavigationBar") == "true") == light, name


def test_the_splash_backgrounds_are_all_different() -> None:
    # Il nativo sceglie lo splash dal colore: due temi con lo stesso sfondo
    # prenderebbero sempre il primo dei due.
    backgrounds = [s["windowSplashScreenBackground"] for s in _splash_styles().values()]
    assert len(backgrounds) == len(set(backgrounds)), backgrounds


def test_the_default_splash_is_the_default_theme(themes) -> None:
    tree = ET.parse(SPLASH_XML)
    base = next(s for s in tree.getroot().iter("style") if s.get("name") == "Theme.Jenny")
    items = {i.get("name"): (i.text or "").strip().lower() for i in base.iter("item")}
    default_id = re.search(r"DEFAULT_THEME = '([a-z0-9]+)'", THEME_JS.read_text()).group(1)
    assert items["android:windowSplashScreenBackground"] == themes[default_id][0]
    # Stesso colore per la starting window sotto Android 12 e per il ripiego
    # della schermata di caricamento.
    base_xml = (RES / "values/themes.xml").read_text(encoding="utf-8")
    assert f'name="android:windowBackground">{themes[default_id][0]}<' in base_xml
    default_bg = re.search(r"DEFAULT_BG = 0x([0-9A-Fa-f]{8})", read_code(MAIN_ACTIVITY))
    assert default_bg and default_bg.group(1).lower() == "ff" + themes[default_id][0][1:]


def test_main_activity_offers_every_splash_style() -> None:
    kotlin = read_code(MAIN_ACTIVITY)
    listed = set(re.findall(r"R\.style\.Theme_Jenny_Splash_(\w+)", kotlin))
    assert listed == set(_splash_styles()), listed
