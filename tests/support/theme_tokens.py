"""I token dei sette temi di `mobile-style.css`, risolti come li risolve il browser.

Serve ai banchi di contrasto: il foglio dichiara
i token in `:root`, li riscrive per tema in `[data-theme="…"]`, e qualche regola
di gruppo vale per piu' temi insieme — guardarne solo l'ultima direbbe che agli
altri quel valore non arriva. Qui le regole si leggono **in ordine**, l'ultima
che parla vince, e le `var(--x)` si risolvono fino in fondo.

- :func:`themes`: i nomi dei temi (`chanel`, … `stone`).
- :func:`tokens`: i token di un tema, gia' risolti.
- :func:`rgba`: un colore CSS (`#rgb`, `#rrggbb`, `rgb[a](…)`) come quattro numeri.
- :func:`contrast`: il rapporto WCAG fra un colore e il suo fondo; un colore
  semitrasparente si compone prima sul fondo, e il fondo semitrasparente su
  `--bg`, come lo vede l'occhio.
- :func:`layered`: lo stesso, su una pila di fondi traslucidi (il velo, il
  vetro).
- :func:`stops`: i colori di un fondo, uno solo o le fermate di un gradiente.
"""

from __future__ import annotations

import re
from pathlib import Path

THEMES_CSS = (
    Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets" / "mobile-style.css"
)


def _rules(css: str) -> list[tuple[set[str], str]]:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return [
        ({" ".join(s.split()) for s in selectors.split(",") if s.strip()}, body)
        for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css)
    ]


def themes(css: str | None = None) -> list[str]:
    css = css if css is not None else THEMES_CSS.read_text(encoding="utf-8")
    found = sorted({m.group(1) for m in re.finditer(r'\[data-theme="([^"]+)"\]', css)})
    assert len(found) >= 7, f"i temi trovati sono {len(found)}, non i sette che esistono"
    return found


def tokens(theme: str, css: str | None = None) -> dict[str, str]:
    css = css if css is not None else THEMES_CSS.read_text(encoding="utf-8")
    applies = {":root", f'[data-theme="{theme}"]'}
    values: dict[str, str] = {}
    for selectors, body in _rules(css):  # in ordine: l'ultima che parla vince
        if not (selectors & applies):
            continue
        for name, value in re.findall(r"--([\w-]+):\s*([^;]+);", body):
            values[name] = value.strip()

    def resolve(value: str, depth: int = 0) -> str:
        assert depth < 10, f"{theme}: `var()` circolare in {value}"
        return re.sub(
            r"var\(--([\w-]+)(?:,\s*([^)]+))?\)",
            lambda m: resolve(values.get(m.group(1)) or (m.group(2) or ""), depth + 1),
            value,
        )

    return {name: resolve(value) for name, value in values.items()}


def rgba(color: str) -> tuple[float, float, float, float]:
    color = color.strip()
    # `color-mix(in srgb, X P%, transparent)` e' X con opacita' P: la miscela
    # col trasparente si fa premoltiplicata (il velo e il vetro della chat).
    mix = re.fullmatch(r"color-mix\(in srgb,\s*(\S+)\s+([\d.]+)%,\s*transparent\)", color)
    if mix:
        r, g, b, a = rgba(mix.group(1))
        return (r, g, b, a * float(mix.group(2)) / 100)
    if color.startswith("#"):
        h = color[1:]
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 1.0)
    m = re.fullmatch(r"rgba?\(([^)]*)\)", color)
    if m:
        p = [float(x) for x in re.split(r"[\s,/]+", m.group(1).strip()) if x]
        return (p[0], p[1], p[2], p[3] if len(p) > 3 else 1.0)
    if color == "transparent":
        return (0.0, 0.0, 0.0, 0.0)
    if color == "white":
        return (255.0, 255.0, 255.0, 1.0)
    raise ValueError(f"colore non riconosciuto: {color!r}")


def stops(background: str) -> list[str]:
    """Le tinte di un fondo: il colore, o le fermate di un `linear-gradient`."""
    if "gradient(" in background:
        return re.findall(r"#[0-9a-fA-F]{3,6}\b|rgba?\([^)]*\)", background)
    return [background]


def _over(fg: tuple, bg: tuple) -> tuple:
    a = fg[3]
    return tuple(fg[i] * a + bg[i] * (1 - a) for i in range(3)) + (1.0,)


def _luminance(c: tuple) -> float:
    def ch(v: float) -> float:
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    return 0.2126 * ch(c[0]) + 0.7152 * ch(c[1]) + 0.0722 * ch(c[2])


def layered(fg: str, *grounds: str) -> float:
    """Il contrasto di `fg` sull'ultimo di una pila di fondi, dal piu' basso al
    piu' alto: ognuno traslucido si compone su quello sotto, come lo vede
    l'occhio. Il primo deve essere opaco."""
    ground = rgba(grounds[0])
    assert ground[3] == 1, f"il fondo piu' basso e' traslucido: {grounds[0]}"
    for layer in grounds[1:]:
        ground = _over(rgba(layer), ground)
    ink = rgba(fg)
    if ink[3] < 1:
        ink = _over(ink, ground)
    hi, lo = sorted((_luminance(ink), _luminance(ground)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def contrast(fg: str, bg: str, page: str) -> float:
    """Il contrasto di `fg` su `bg`, con `bg` composto su `page` se traslucido."""
    ground = rgba(bg)
    if ground[3] < 1:
        ground = _over(ground, rgba(page))
    ink = rgba(fg)
    if ink[3] < 1:
        ink = _over(ink, ground)
    hi, lo = sorted((_luminance(ink), _luminance(ground)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)
