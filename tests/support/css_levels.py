"""I livelli (`z-index`) dei fogli della WebUI, letti come li legge il browser.

Serve ai banchi di «Jenny sempre sopra» (D3, 25/09/2026): la casa carica
**due** fogli, `mobile-style.css` e `home-style.css`, e un livello che la copre
puo' arrivare da tutti e due. Il banco di prima contava i `z-index` di
`home-style.css` soltanto, e intanto mini-app e lightbox — regole
dell'officina, costruite dal JS condiviso — le passavano davanti.

- :func:`rules`: le regole di un foglio, con le at-rule che le contengono;
  i commenti non contano (un `z-index` citato in un commento non e' un livello).
- :func:`levels`: le coppie (selettore, livello) di un foglio, un selettore per
  voce anche quando la regola ne raggruppa piu' d'uno.
- :func:`key_names`: classi e id dell'ultimo composto di un selettore, cioe'
  quelli che l'elemento colpito deve portare addosso.
- :func:`home_vocabulary`: ogni parola che puo' finire nel DOM della casa —
  `index.html` piu' i moduli che `home-app.js` importa, per chiusura. E' una
  stima **per eccesso** (conta anche le parole che non sono classi), che e' il
  lato sicuro: una regola in piu' da controllare, mai una in meno.
"""

from __future__ import annotations

import re
from pathlib import Path

UI = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui"
ASSETS = UI / "assets"

_IMPORT = re.compile(
    r"""(?:import|export)\s[^;'"]*?from\s*['"](\.[^'"]+)['"]"""
    r"""|import\s*['"](\.[^'"]+)['"]"""
    r"""|import\(\s*['"](\.[^'"]+)['"]"""
)


def rules(css: str) -> list[tuple[str, str, tuple[str, ...]]]:
    """(selettori, corpo, at-rule che la contengono) per ogni regola."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out: list[tuple[str, str, tuple[str, ...]]] = []
    stack: list[str] = []
    start = 0
    i = 0
    while i < len(css):
        c = css[i]
        if c == "{":
            prelude = css[start:i].strip()
            if prelude.startswith("@"):
                stack.append(prelude)
                start = i + 1
            else:
                end = css.index("}", i)
                out.append((prelude, css[i + 1 : end], tuple(stack)))
                i = end
                start = end + 1
        elif c == "}":
            if stack:
                stack.pop()
            start = i + 1
        i += 1
    return out


def levels(css: str) -> list[tuple[str, int]]:
    """(selettore, z-index) per ogni selettore di ogni regola che ne dichiara uno."""
    out = []
    for selectors, body, context in rules(css):
        if any(at.startswith("@keyframes") for at in context):
            continue
        m = re.search(r"(?:^|;)\s*z-index:\s*(-?\d+)", body)
        if not m:
            continue
        for s in selectors.split(","):
            out.append((" ".join(s.split()), int(m.group(1))))
    return out


def key_names(selector: str) -> list[str]:
    """Classi e id dell'ultimo composto (`:root.x .a.b > .c` -> ``['c']``)."""
    last = re.split(r"[\s>+~]+", selector.strip())[-1]
    last = re.sub(r":[\w-]+\([^)]*\)", "", last)
    return re.findall(r"[.#]([\w-]+)", last)


def _closure(entry: Path) -> set[Path]:
    seen: set[Path] = set()
    todo = [entry.resolve()]
    while todo:
        f = todo.pop()
        if f in seen or not f.exists():
            continue
        seen.add(f)
        for m in _IMPORT.finditer(f.read_text(encoding="utf-8")):
            rel = next(g for g in m.groups() if g)
            todo.append((f.parent / rel).resolve())
    return seen


def home_vocabulary() -> set[str]:
    """Le parole che il DOM della casa puo' contenere (stima per eccesso)."""
    modules = _closure(ASSETS / "home-app.js")
    assert len(modules) > 20, f"la chiusura degli import della casa non morde piu' ({len(modules)})"
    text = (UI / "index.html").read_text(encoding="utf-8")
    text += "".join(p.read_text(encoding="utf-8") for p in modules)
    return set(re.findall(r"[A-Za-z][\w-]*", text))
