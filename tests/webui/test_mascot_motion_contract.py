"""I gesti della mascotte: mai in diagonale, una durata sola.

Dal 05/10/2026 ogni animazione di Jenny e' squash and stretch (dai mockup «Il
saluto di Jenny» e «Jenny in movimento»). Due difetti li ha gia' fatti il primo
mockup, e qui non devono poter tornare:

- **la diagonale**: una scala fuori dagli assi del disegno — uno `skew`, o un
  `rotate(a) scale() rotate(-a)` — su uno sprite piatto lo storce, e la testa
  scivola di lato rispetto ai piedi. Lo stesso succede in silenzio quando due
  fotogrammi di un keyframe hanno liste di funzioni diverse: il browser li
  interpola per matrice, e la matrice puo' introdurre il taglio;
- **due durate**: il timer che toglie il gesto e l'animazione che lo disegna
  scritti in due posti divergono. La durata sta in `MOTIONS`
  (``shared/mascot-motion.js``) e il CSS la legge da `--jenny-<canale>-ms`.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"
CSS = ASSETS / "mobile-style.css"
MOTION = ASSETS / "shared" / "mascot-motion.js"
DRAG = ASSETS / "shared" / "mascot-drag.js"

CHANNEL_LIST = ["translate", "rotate", "scale"]


def _keyframes(css: str) -> dict[str, str]:
    """`@keyframes jenny-*` -> corpo, con le graffe annidate."""
    out: dict[str, str] = {}
    for m in re.finditer(r"@keyframes (jenny-[\w-]+)\s*\{", css):
        depth, i = 1, m.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(css[i], 0)
            i += 1
        out[m.group(1)] = css[m.end() : i - 1]
    return out


def _transforms(body: str) -> list[list[str]]:
    """La lista di funzioni di ogni `transform:` del keyframe."""
    return [
        re.findall(r"([a-zA-Z0-9]+)\(", value)
        for value in re.findall(r"transform:\s*([^;}]+)", body)
    ]


def _motions() -> dict[str, list[str]]:
    src = MOTION.read_text(encoding="utf-8")
    block = re.search(r"export const MOTIONS = \{(.*?)\n\};", src, re.S)
    assert block, "MOTIONS non trovato"
    channels: dict[str, list[str]] = {}
    for m in re.finditer(r"\n  (\w+): \{(.*?)\},", block.group(1), re.S):
        body = re.sub(r"//[^\n]*", "", m.group(2))
        channels[m.group(1)] = re.findall(r"'?([\w-]+)'?: \d+", body)
    return channels


def _gesture_rules(css: str) -> list[tuple[str, str, str, str]]:
    """(selettore, canale, gesto, dichiarazioni) per ogni regola `[data-<canale>="…"]`."""
    rules = []
    for selectors, decls in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        for m in re.finditer(r'\[data-(lean|squash)="([\w-]+)"\]', selectors):
            rules.append((selectors.strip(), m.group(1), m.group(2), decls))
    return rules


def test_no_mascot_keyframe_shears_her() -> None:
    frames = _keyframes(CSS.read_text(encoding="utf-8"))
    assert "jenny-greet" in frames, "il saluto non ha piu' il suo keyframe"
    for name, body in frames.items():
        assert "skew" not in body, f"{name}: uno skew storce il disegno"
        lists = _transforms(body)
        if not lists:
            continue
        assert all(lst == lists[0] for lst in lists), (
            f"{name}: fotogrammi con liste diverse, interpolati per matrice: {lists}"
        )


def test_every_gesture_moves_on_the_three_axes_in_order() -> None:
    """I keyframe dei canali scrivono sempre translate, rotate, scale."""
    css = CSS.read_text(encoding="utf-8")
    frames = _keyframes(css)
    used = {
        anim
        for _sel, _ch, _name, decls in _gesture_rules(css)
        for anim in re.findall(r"animation:\s*(jenny-[\w-]+)", decls)
    }
    for name in used:
        for lst in _transforms(frames[name]):
            assert lst == CHANNEL_LIST, f"{name}: {lst} invece di {CHANNEL_LIST}"


def test_a_gesture_lasts_what_motions_says() -> None:
    """Nessuna durata letterale: ogni animazione di un gesto legge la variabile
    del suo canale, che `_motion` scrive da MOTIONS."""
    rules = _gesture_rules(CSS.read_text(encoding="utf-8"))
    assert rules
    for sel, channel, _name, decls in rules:
        for anim in re.findall(r"animation:\s*([^;]+)", decls):
            if anim.strip() == "none":
                continue
            assert f"var(--jenny-{channel}-ms)" in anim, f"{sel}: durata scritta a mano ({anim})"
            assert not re.search(r"\b\d+(\.\d+)?m?s\b", anim), f"{sel}: durata scritta a mano ({anim})"


def test_motions_and_css_name_the_same_gestures() -> None:
    motions = _motions()
    assert set(motions) == {"lean", "squash"}
    in_css = {(ch, name) for _s, ch, name, _d in _gesture_rules(CSS.read_text(encoding="utf-8"))}
    in_js = {(ch, name) for ch, names in motions.items() for name in names}
    assert in_js - in_css == set(), f"gesti senza CSS: {sorted(in_js - in_css)}"
    assert in_css - in_js == set(), f"CSS di gesti che il JS non accende: {sorted(in_css - in_js)}"


def test_the_flight_never_scales_between_two_rotations() -> None:
    """La deformazione del volo resta sugli assi del disegno: niente scala
    stretta fra due rotazioni (il «nel verso del moto» del primo mockup)."""
    src = re.sub(r"//[^\n]*|/\*.*?\*/", "", DRAG.read_text(encoding="utf-8"), flags=re.S)
    assert not re.search(r"rotate\([^`]*\)\s*scale\([^`]*\)\s*rotate\(", src)
    assert "skew" not in src
