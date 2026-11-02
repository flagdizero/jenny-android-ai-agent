"""URL lunghi e tabelle larghe: la stessa resa nei tre posti.

Misurato in Chrome sulla pagina vera (una pagina di prova con un URL di 130
caratteri e una tabella di sei colonne), prima della correzione:

- **lettore**, 590 e 360 px: 957 px di contenuto, e il lettore intero scorreva
  di lato; a 360 la tabella usciva di 84 px;
- **chat della casa**, 360 px: 9 parole su 28 spezzate a meta' nelle celle
  (`overflow-wrap: anywhere` del messaggio arriva fino alle celle);
- **chat dell'officina**, 360 px: le stesse 9 parole spezzate, e la tabella
  tagliata da `overflow: hidden` senza modo di vederne il resto.

Dopo: la pagina resta ferma, nessuna parola spezzata, e la tabella scorre di
lato da sola. Qui il contratto che tiene in piedi quella misura.
"""

from __future__ import annotations

import re
from pathlib import Path

from support import css_levels
from support.js_harness import ASSETS

HOME = ASSETS / "home-style.css"
SHOP = ASSETS / "mobile-style.css"

_TABLES = ((HOME, ".home-reader-body"), (HOME, ".home-block"), (SHOP, ".chat-content"))


def _body(path: Path, selector: str) -> str:
    out = []
    for selectors, body, context in css_levels.rules(path.read_text(encoding="utf-8")):
        if context:
            continue
        if selector in {" ".join(s.split()) for s in selectors.split(",")}:
            out.append(body)
    return "\n".join(out)


def _decl(body: str, prop: str) -> str | None:
    found = re.findall(rf"(?:^|[;\s]){re.escape(prop)}:\s*([^;]+);", body)
    return found[-1].strip() if found else None


def test_a_wide_table_scrolls_inside_its_own_box() -> None:
    for path, root in _TABLES:
        body = _body(path, f"{root} table")
        assert body, f"{path.name}: manca `{root} table`"
        where = f"{path.name}: `{root} table`"
        assert _decl(body, "display") == "block", f"{where}: senza `display: block` non scorre"
        assert _decl(body, "max-width") == "100%", where
        assert _decl(body, "overflow-x") == "auto", where
        assert _decl(body, "width") == "fit-content", f"{where}: il bordo girerebbe attorno a un vuoto"
        assert _decl(body, "overflow") is None, f"{where}: `overflow` taglia la tabella"


def test_no_cell_breaks_a_word_in_half() -> None:
    for path, root in _TABLES:
        cells = _body(path, f"{root} td")
        assert cells, f"{path.name}: manca `{root} td`"
        assert _decl(cells, "overflow-wrap") == "break-word", (path.name, root)
        assert _decl(cells, "word-break") == "normal", (path.name, root)


def test_the_reader_wraps_what_is_longer_than_a_line() -> None:
    body = _body(HOME, ".home-reader-body")
    assert _decl(body, "overflow-wrap") in {"break-word", "anywhere"}, (
        "senza `overflow-wrap` un URL lungo allarga il lettore, che scorre di lato"
    )
