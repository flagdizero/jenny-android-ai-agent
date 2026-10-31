"""Posare un'app su una bacheca: una polaroid che **e'** l'app.

L'app non entra nel cassetto di un'altra scheda: ha la sua polaroid, col suo
valore al posto della foto, e il tocco apre il suo schermo. Se c'entra con una
scheda che c'e' gia' (il portafoglio con «ETF»), le due si legano con un filo.

Un solo posto per le strade che lo fanno — l'RPC ``board.card.add``
dell'interfaccia e il tool ``app`` di Jenny quando l'utente glielo chiede —
cosi' la scheda nasce uguale chiunque la chieda. Scrive come ``user``: una
posa la decide l'utente (anche quando la chiede a Jenny, e allora la scheda
porta ``appBy: "jenny-on-request"``). Di sua iniziativa Jenny non posa niente:
propone dalla busta (``add_app_card``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jenny.board import model
from jenny.board.store import mutate_board, read_reminders


def find_card(board: dict[str, Any], ref: str) -> dict[str, Any] | None:
    """Una scheda per id o per titolo (senza badare a maiuscole e accenti)."""
    card = model.card_by_id(board, ref)
    if card is not None:
        return card
    want = model.slugify(ref)
    return next((c for c in board["cards"] if model.slugify(c["title"]) == want), None)


def tie(board: dict[str, Any], a: str, b: str, by: str = model.USER) -> bool:
    """Un filo fra *a* e *b*, se non c'e' gia' e la bacheca ne ha posto."""
    if a == b or any({c["a"], c["b"]} == {a, b} for c in board["connections"]):
        return False
    if len(board["connections"]) >= model.MAX_CONNECTIONS:
        return False
    board["connections"].append({"a": a, "b": b, "label": "", "by": by})
    return True


def app_size(workspace: Path | None, slug: str) -> str | None:
    """La taglia della striscia di *slug* (S/M/L/XL), o ``None`` se l'app non ne ha:
    allora la sua scheda e' una polaroid con l'icona."""
    from jenny.apps.manifest import find_app

    if workspace is None:
        return None
    app = find_app(workspace, slug)
    section = app.manifest.section if app is not None and app.manifest is not None else None
    return section.size if section is not None and section.blocks and section.size else None


def app_title(workspace: Path | None, slug: str) -> str:
    from jenny.apps.manifest import find_app

    if workspace is not None:
        app = find_app(workspace, slug)
        if app is not None and app.manifest is not None:
            return app.manifest.name
    return slug


def place_app(root: Path, slug: str, *, params: dict[str, Any] | None = None,
              link: Any = None, near: tuple[float, float] | None = None,
              title: str | None = None, by: str | None = None,
              workspace: Path | None = None) -> str:
    """Posa *slug* sul quaderno *root*. Torna dove, in parole.

    Nasce una scheda che **e'** l'app, col suo valore sulla polaroid. Con
    *link* (id o titolo di una scheda) la nuova scheda si lega a quella con un
    filo, e nasce accanto. ``ValueError`` con un messaggio che si legge se non
    si puo'.
    """
    from jenny.config.paths import get_workspace_path

    try:
        ws = workspace or get_workspace_path()
    except Exception:  # noqa: BLE001
        ws = None
    params = dict(params or {})
    name = title or app_title(ws, slug)
    size = app_size(ws, slug)
    box = model.card_box({"size": size})
    reminders = read_reminders(root)
    done: dict[str, Any] = {}

    def apply(board: dict[str, Any]) -> None:
        nonlocal near
        other = None
        if link:
            other = find_card(board, str(link))
            if other is None:
                titles = ", ".join(f"«{c['title']}»" for c in board["cards"]) or "none"
                raise ValueError(f"No card {link!r} on this board (cards: {titles}).")
        if model.is_full(board, reminders):
            raise ValueError("The board is full: the user has to remove a card first.")
        taken = {c["id"] for c in board["cards"]} | model.removed_card_ids(board)
        card_id = model.unique_id(model.slugify(name), taken)
        if near is None and other is not None:
            ow, oh = model.card_box(other)
            near = (other["pos"]["x"] + ow + box[0] / 2, other["pos"]["y"] + oh / 2)
        target = (near[0] - box[0] / 2, near[1] - box[1] / 2) if near is not None else None
        x, y = model.free_slot(board, reminders, box=box, near=target)
        card = model.normalize_board({"cards": [{
            "id": card_id, "title": name, "titleBy": model.USER, "pages": [],
            "pos": {"x": x, "y": y, "by": model.USER}, "glyph": "",
            "userSections": [], "created": model.now_iso(),
            "app": {"slug": slug, "params": params},
            **({"size": size} if size else {}),
        }]})["cards"][0]
        if by:
            card["appBy"] = by
        board["cards"].append(card)
        where = f"as the card «{name}»"
        if other is not None:
            where += (f", tied to «{other['title']}»" if tie(board, card_id, other["id"])
                      else f" (no thread to «{other['title']}»: the board has no room for one)")
        done.update(where=where, card=card_id)

    mutate_board(root, apply)
    return str(done["where"])


def refresh_app_cards(workspace: Path, slug: str | None = None) -> int:
    """Riallinea la taglia delle schede-app al manifest della loro app. Torna quante.

    La taglia sta sulla scheda (il posto libero la usa senza leggere le app), ma
    viene dal manifest: quando Jenny cambia un'app, o all'avvio dopo un
    aggiornamento, le schede che la mostrano prendono la taglia nuova. Senza
    *slug*, tutte le app di tutti i quaderni.
    """
    from jenny.board.placements import _wikis_dirs
    from jenny.utils.wiki_paths import discover_wiki_roots

    sizes: dict[str, str | None] = {}
    changed = 0
    for wikis in _wikis_dirs(workspace):
        for _name, root in sorted(discover_wiki_roots(wikis).items()):
            from jenny.board.store import read_board

            board = read_board(root)
            if board is None:
                continue
            todo = []
            for card in board["cards"]:
                app = card.get("app")
                if not isinstance(app, dict) or (slug is not None and app.get("slug") != slug):
                    continue
                want = sizes.setdefault(app["slug"], app_size(workspace, app["slug"]))
                if card.get("size") != want:
                    todo.append((card["id"], want))
            if not todo:
                continue

            def apply(b: dict[str, Any], todo=todo) -> None:
                for card_id, want in todo:
                    card = model.card_by_id(b, card_id)
                    if card is None:
                        continue
                    if want:
                        card["size"] = want
                    else:
                        card.pop("size", None)

            mutate_board(root, apply)
            changed += len(todo)
    return changed
