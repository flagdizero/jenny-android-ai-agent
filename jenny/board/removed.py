"""Le schede tolte dall'utente: quel che si ferma con loro, e quando se ne vanno.

«Togli dalla bacheca» ha un Annulla, e un Annulla vale solo se rimette
**tutto**. Quindi togliere una scheda non butta niente: la scheda, i suoi fili e
il suo posto vanno in ``removedCards`` (v. :data:`model.REMOVED_KEEP_DAYS`), e
quel che faceva da sé — i cron del suo cassetto — si mette in pausa. Le RPC stanno in ``webui/board_commands.py``; la scadenza la fa
l'orologio dei quaderni, che non deve dipendere dalla WebUI: per questo i pezzi
comuni stanno qui.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jenny.board import model
from jenny.board.store import delete_card_summary, mutate_board, mutate_reminders, read_board


def card_crons(card: Mapping[str, Any]) -> list[str]:
    return [str(s["job"]) for s in card.get("userSections") or [] if s.get("kind") == "cron"]


def purge_removed_cards(root: Path, cron: Any = None, now: Any = None) -> int:
    """Butta davvero le schede tolte da più di una settimana. Lo chiama l'orologio.

    Scrive come ``user``: non è Jenny che decide qualcosa sulla bacheca, è la
    scadenza dell'Annulla che l'utente ha avuto (la regola la dice
    :data:`model.REMOVED_KEEP_DAYS`). Quel che la scheda faceva da sé se ne va
    con lei: i cron del cassetto, le attese aperte (lasciate, non cancellate),
    il riassunto derivato.
    """
    board = read_board(root)
    if board is None:
        return 0
    expired = model.expired_removed(board, now)
    if not expired:
        return 0
    ids = {e["card"]["id"] for e in expired}

    def apply(b: dict[str, Any]) -> None:
        b["removedCards"] = [r for r in b["removedCards"] if r["card"]["id"] not in ids]

    mutate_board(root, apply)

    def drop(rems: dict[str, Any]) -> None:
        for rem in rems["reminders"]:
            if rem.get("card") in ids and rem["state"] == "open":
                rem["state"] = "dropped"
                rem["closedAt"] = model.now_iso()

    mutate_reminders(root, drop)
    for entry in expired:
        card = entry["card"]
        if cron is not None:
            for job_id in card_crons(card):
                cron.remove_job(job_id)
        delete_card_summary(root, card["id"])
    return len(expired)
