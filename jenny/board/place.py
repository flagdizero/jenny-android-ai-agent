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


def app_title(workspace: Path | None, slug: str) -> str:
    from jenny.apps import trust
    from jenny.apps.manifest import find_app

    if workspace is not None:
        app = find_app(workspace, slug)
        if app is None:
            draft = trust.load_draft(workspace, slug)
            app = draft.app if draft is not None else None
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
            near = (other["pos"]["x"] + model.CARD_BOX[0] * 1.5,
                    other["pos"]["y"] + model.CARD_BOX[1] / 2)
        target = (near[0] - model.CARD_BOX[0] / 2, near[1] - model.CARD_BOX[1] / 2) \
            if near is not None else None
        x, y = model.free_slot(board, reminders, near=target)
        card = model.normalize_board({"cards": [{
            "id": card_id, "title": name, "titleBy": model.USER, "pages": [],
            "pos": {"x": x, "y": y, "by": model.USER}, "glyph": "",
            "userSections": [], "created": model.now_iso(),
            "app": {"slug": slug, "params": params},
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
