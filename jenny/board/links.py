"""Il lettore a tre link: a quale cassetto appartengono le pagine linkate.

I file della wiki non cambiano: i ``[[link]]`` restano come Jenny li ha scritti.
È il lettore che li mostra attraverso la bacheca, e questo modulo gli dà quel
che serve: per la pagina aperta e per ogni pagina che linka, il cassetto in cui
sta — o nessuno, cioè fuori bacheca.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from jenny.board import model
from jenny.board.store import read_board

_HREF_RE = re.compile(r'href="/\?wiki=([^&"]+)&(?:amp;)?page=([^"#]+)')


def page_membership(
    project_root: Path, notebook: str, page: str, html: str
) -> dict[str, Any] | None:
    """``{card, cardTitle, links: {pagina: {card, title} | None}}``, o ``None`` senza bacheca."""
    board = read_board(project_root)
    if board is None:
        return None
    owners = model.page_owners(board)
    titles = {c["id"]: c["title"] for c in board["cards"]}
    links: dict[str, Any] = {}
    for wiki, target in _HREF_RE.findall(html):
        if unquote(wiki) != notebook:
            continue
        rel = unquote(target)
        if rel in links:
            continue
        card = owners.get(rel)
        links[rel] = {"card": card, "title": titles[card]} if card else None
    own = owners.get(page)
    return {"card": own, "cardTitle": titles.get(own or "", ""), "links": links}
