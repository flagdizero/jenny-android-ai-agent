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
    from jenny.webui.wiki import resolve_wikilink

    pages_dir = project_root / "wiki"
    links: dict[str, Any] = {}
    for wiki, target in _HREF_RE.findall(html):
        if unquote(wiki) != notebook:
            continue
        # La chiave e' il bersaglio **come sta nel link** (e' quel che il lettore
        # ha in mano); l'appartenenza si cerca sulla pagina vera, perche' un
        # ``[[vasi-settembre]]`` non risolto dal renderer arriva senza ``.md``.
        key = unquote(target)
        if key in links:
            continue
        resolved = resolve_wikilink(project_root, key)
        rel = resolved.relative_to(pages_dir).as_posix() if resolved else key
        card = owners.get(rel)
        links[key] = {"card": card, "title": titles[card]} if card else None
    own = owners.get(page)
    return {"card": own, "cardTitle": titles.get(own or "", ""), "links": links}
