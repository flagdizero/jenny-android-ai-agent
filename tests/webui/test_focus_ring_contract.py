"""Il fuoco da tastiera si vede in ogni tema.

Il Titan 2 ha una tastiera fisica: Tab e le frecce spostano il fuoco davvero.
Due modi in cui il fuoco spariva:

- **il solo colore del bordo.** In Fumetto `--border`, `--border-strong` e
  `--accent` sono lo stesso nero: un campo che al fuoco «diventa del colore
  dell'accento» non cambia di un pixel. Ora il campo cambia colore **e**
  spessore (un pixel d'ombra piena sopra il bordo);
- **`outline: none` senza sostituto.** La ricerca delle pagine toglieva il suo
  anello e nessuno lo rimetteva.

Il colore e' `--focus-ring`, che deve arrivare a 3:1 (la soglia di un oggetto
grafico) su ogni fondo di ogni tema. La misura vera — Tab nella pagina,
in Fumetto, e lo stile calcolato — e' nel rapporto della correzione.
"""

from __future__ import annotations

import re
from pathlib import Path

from support import css_levels, theme_tokens

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
HOME = ASSETS / "home-style.css"
SHOP = ASSETS / "mobile-style.css"

# I campi che dicevano il fuoco col solo colore del bordo, o non lo dicevano.
_FIELDS = {
    HOME: (".home-field:has(:focus-visible)", ".home-board-search-input:focus", ".home-rules:focus",
           ".home-key-input:focus", ".home-audit-comment:focus", ".home-drawer-fix-area:focus"),
    SHOP: (".compose-pill:has(:focus-visible)", ".oc-dialog-input:focus", ".settings-input:focus",
           ".launcher-search:focus", ".onboarding-input:focus"),
}


def _bodies(path: Path, selector: str) -> str:
    out = []
    for selectors, body, _ in css_levels.rules(path.read_text(encoding="utf-8")):
        if selector in {" ".join(s.split()) for s in selectors.split(",")}:
            out.append(body)
    return "\n".join(out)


def test_the_ring_reads_on_every_ground_of_every_theme() -> None:
    problems = []
    for theme in theme_tokens.themes():
        v = theme_tokens.tokens(theme)
        assert v.get("focus-ring"), f"{theme}: `--focus-ring` non arriva"
        for ground in ("bg", "surface", "surface-2"):
            ratio = theme_tokens.contrast(v["focus-ring"], v[ground], v["bg"])
            if ratio < 3:
                problems.append(f"{theme}: --focus-ring su --{ground} = {ratio:.2f}")
    assert not problems, problems


def test_a_focused_field_changes_thickness_not_only_colour() -> None:
    for path, selectors in _FIELDS.items():
        for selector in selectors:
            body = _bodies(path, selector)
            assert body, f"{path.name}: manca `{selector}`"
            assert re.search(r"border-color:\s*var\(--focus-ring\)", body), (path.name, selector)
            assert re.search(r"box-shadow:[^;]*0 0 0 1px var\(--focus-ring\)", body, re.S), (
                f"{path.name}: `{selector}` dice il fuoco col solo colore del bordo, "
                f"che in Fumetto e' gia' quello"
            )


def test_no_ring_is_a_thin_line_of_the_fill_accent() -> None:
    """Un anello da 1px di `--accent`: in Y2K sta a 2,2:1 sul fondo."""
    offenders = []
    for path in (HOME, SHOP):
        for selectors, body, _ in css_levels.rules(path.read_text(encoding="utf-8")):
            if ":focus" not in selectors:
                continue
            if re.search(r"outline:\s*1px", body) or re.search(r"outline:[^;]*var\(--accent\)", body):
                offenders.append(f"{path.name}: {' '.join(selectors.split())}")
    assert not offenders, offenders


def test_every_control_has_a_ring_by_default() -> None:
    """La rete sotto: un controllo che nessuna regola veste ha comunque
    l'anello, senza pesare sulle regole che lo vestono a modo loro."""
    css = SHOP.read_text(encoding="utf-8")
    m = re.search(r":where\(([^)]*(?:\([^)]*\)[^)]*)*)\):focus-visible\s*\{([^}]*)\}", css)
    assert m, "manca l'anello di serie"
    for tag in ("button", "a", "select", '[role="tab"]', '[role="switch"]'):
        assert tag in m.group(1), tag
    assert "outline: 2px solid var(--focus-ring)" in m.group(2)


def test_the_board_search_says_where_the_focus_is() -> None:
    """La lente della bacheca toglie l'anello del browser: lo deve rimettere
    il bordo, o il fuoco e' invisibile."""
    field = _bodies(HOME, ".home-board-search-input:focus")
    assert "outline: none" in field
    assert "var(--focus-ring)" in field


def test_a_finger_on_a_composer_button_does_not_light_the_composer() -> None:
    """La pastiglia del composer contiene dei bottoni (la graffetta, i
    comandi): con ``:focus-within`` un tocco del dito su uno di loro
    accendeva l'anello di tutta la pastiglia. L'anello segue il fuoco che si
    vede: il campo di testo sempre, un bottone solo da tastiera."""
    for path, selector in ((HOME, ".home-field"), (SHOP, ".compose-pill")):
        assert not _bodies(path, f"{selector}:focus-within"), (path.name, selector)
        assert _bodies(path, f"{selector}:has(:focus-visible)"), (path.name, selector)
