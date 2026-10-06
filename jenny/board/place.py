"""Posare un'app su una bacheca: come scheda, o come sezione di un cassetto.

Un solo posto per le due strade che lo fanno — l'RPC ``board.card.add`` /
``board.section.add`` dell'interfaccia e il tool ``app`` di Jenny quando
l'utente glielo chiede — cosi' la scheda nasce uguale chiunque la chieda.
Scrive come ``user``: una posa la decide l'utente (anche quando la chiede a
Jenny, e allora la sezione porta ``by: "jenny-on-request"``). Di sua
iniziativa Jenny non posa niente: propone dalla busta (``add_app_card``).
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
              card_ref: Any = None, near: tuple[float, float] | None = None,
              title: str | None = None, by: str | None = None,
              workspace: Path | None = None) -> str:
    """Posa *slug* sul quaderno *root*. Torna dove, in parole.

    Con *card_ref* diventa una sezione del cassetto di quella scheda; senza,
    nasce una scheda che **e'** l'app, con la sua copertina. ``ValueError``
    con un messaggio che si legge se non si puo'.
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
        if card_ref:
            card = find_card(board, str(card_ref))
            if card is None:
                titles = ", ".join(f"«{c['title']}»" for c in board["cards"]) or "none"
                raise ValueError(f"No card {card_ref!r} on this board (cards: {titles}).")
            sid = model.unique_id("s-app", [s["id"] for s in card["userSections"]])
            section: dict[str, Any] = {"id": sid, "kind": "app", "app": slug, "params": params,
                                       "created": model.now_iso()}
            if by:
                section["by"] = by
            card["userSections"].append(section)
            done.update(where=f"in the drawer of «{card['title']}»", card=card["id"],
                        section=sid)
            return
        if model.is_full(board, reminders):
            raise ValueError("The board is full: the user has to remove a card first.")
        taken = {c["id"] for c in board["cards"]} | model.removed_card_ids(board)
        card_id = model.unique_id(model.slugify(name), taken)
        target = (near[0] - model.CARD_BOX[0] / 2, near[1] - model.CARD_BOX[1] / 2) \
            if near is not None else None
        x, y = model.free_slot(board, reminders, near=target)
        card = model.normalize_board({"cards": [{
            "id": card_id, "title": name, "titleBy": model.USER, "pages": [],
            "pos": {"x": x, "y": y, "by": model.USER}, "glyph": "",
            "userSections": [], "created": model.now_iso(),
            "app": {"slug": slug, "params": params}, "cover": model.COVER_CARD,
        }]})["cards"][0]
        if by:
            card["appBy"] = by
        board["cards"].append(card)
        done.update(where=f"as the card «{name}»", card=card_id, section=None)

    mutate_board(root, apply)
    return str(done["where"])
