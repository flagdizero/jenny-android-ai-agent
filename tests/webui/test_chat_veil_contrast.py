"""Il velo sotto la chat aperta sopra una pagina: leggibile in ogni tema.

Dal 05/10/2026 Jenny e' la porta della chat: toccata dove la chat non c'e', la
chat vera sale a tutto schermo sopra la pagina (`#home-over` in casa,
`:root.chat-over` nell'officina), e la pagina resta visibile sotto un velo
(`--chat-veil`) ed e' sfocata appena (`--chat-veil-blur`). Il velo deve lasciar
leggere la chat sopra **anche** dove sotto passa il testo della pagina.

Il caso peggiore contato qui: sotto il testo della chat c'e' un glifo della
pagina, del colore del testo del tema, **non sfocato**. La sfocatura spalma il
glifo sul suo fondo e lo attenua, quindi nel telefono il contrasto e' piu' alto
di questo: e' margine, non e' il conto.
"""

from __future__ import annotations

import re
from pathlib import Path

from support import theme_tokens

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
MOBILE_CSS = ASSETS / "mobile-style.css"
HOME_CSS = ASSETS / "home-style.css"

#: WCAG AA per il testo normale.
AA = 4.5


def _veil(value: str) -> str:
    """Il velo come `rgba(…)`: `color-mix(in srgb, X P%, transparent)` e' X
    con opacita' P (la miscela col trasparente si fa premoltiplicata)."""
    m = re.fullmatch(r"color-mix\(in srgb,\s*(\S+)\s+([\d.]+)%,\s*transparent\)", value.strip())
    if not m:
        return value
    r, g, b, _ = theme_tokens.rgba(m.group(1))
    return f"rgba({r}, {g}, {b}, {float(m.group(2)) / 100})"


def test_the_chat_reads_over_the_veil_in_every_theme() -> None:
    low = []
    for theme in theme_tokens.themes():
        v = theme_tokens.tokens(theme)
        veil = _veil(v["chat-veil"])
        # Il velo composto su un glifo della pagina sotto, poi il testo sopra.
        ratio = theme_tokens.contrast(v["text"], veil, v["text"])
        if ratio < AA:
            low.append(f"{theme}: {ratio:.2f}")
    assert not low, f"la chat sopra il velo non si legge: {low}"


def test_the_veil_still_lets_the_page_show_through() -> None:
    """Il velo non e' un muro: e' la pagina di cui stai parlando."""
    for theme in theme_tokens.themes():
        alpha = theme_tokens.rgba(_veil(theme_tokens.tokens(theme)["chat-veil"]))[3]
        assert alpha <= 0.8, f"{theme}: il velo al {alpha:.0%} copre la pagina sotto"


def test_the_blur_is_light_and_on_the_page_not_on_the_veil() -> None:
    mobile = MOBILE_CSS.read_text(encoding="utf-8")
    home = HOME_CSS.read_text(encoding="utf-8")
    blur = re.search(r"--chat-veil-blur:\s*([\d.]+)px;", mobile)
    assert blur, "la sfocatura sotto il velo non c'e'"
    # Leggera: oltre i 4 px la pagina diventa una macchia, e si perde il
    # motivo per cui e' lasciata a vista.
    assert 0 < float(blur.group(1)) <= 4, blur.group(0)
    # Sfocata la pagina, ferma, e non il velo: un `backdrop-filter` sul velo
    # si ricalcola a ogni scorrere della chat, e nell'officina starebbe sullo
    # scroller dei manici di selezione.
    css = re.sub(r"/\*.*?\*/", "", home + mobile, flags=re.S)
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        if re.search(r"home-over|chat-over|#view-chat", selectors):
            assert "backdrop-filter" not in body, f"{selectors.strip()}: v. la docstring"
    assert re.search(
        r":root\.chat-over \.view-beneath,\s*:root\.chat-over-app \.app-frame-overlay \{\s*"
        r"filter: blur\(var\(--chat-veil-blur\)\);",
        mobile,
    ), "nell'officina la vista sotto la chat non e' sfocata"
    assert re.search(r"\.home-beneath \{\s*filter: blur\(var\(--chat-veil-blur", home), (
        "in casa la pagina sotto la chat non e' sfocata"
    )
