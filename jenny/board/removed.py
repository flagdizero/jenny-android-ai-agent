"""Le schede tolte dall'utente: quel che si ferma con loro, e quando se ne vanno.

«Togli dalla bacheca» ha un Annulla, e un Annulla vale solo se rimette
**tutto**. Quindi togliere una scheda non butta niente: la scheda, i suoi fili e
il suo posto vanno in ``removedCards`` (v. :data:`model.REMOVED_KEEP_DAYS`), e
quel che faceva da sé — i suoi cron e i suoi promemoria, job anche loro — si
mette in pausa. Le RPC stanno in ``webui/board_commands.py``; la scadenza la fa
l'orologio dei quaderni, che non deve dipendere dalla WebUI: per questo i pezzi
comuni stanno qui.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jenny.board import model
from jenny.board.store import delete_card_summary, mutate_board, read_board


def card_jobs(cron: Any, notebook: str, card_id: str) -> list[str]:
    """Gli id dei cron della scheda *card_id* del quaderno *notebook*.

    Dai job dello scheduler, non dalle sezioni del cassetto: dal 20/10/2026 un
    cron non ne scrive più, e un legame vecchio porta comunque la sua scheda.
    """
    from jenny.board.cron_link import scope_of

    if cron is None:
        return []
    try:
        jobs = cron.list_jobs(include_disabled=True)
    except Exception:  # noqa: BLE001 — senza scheduler non c'è niente da fermare
        return []
    return [j.id for j in jobs if (j.payload.board or {}).get("notebook") == notebook
            and scope_of(j.payload.board) == card_id]


def purge_removed_cards(root: Path, cron: Any = None, now: Any = None) -> int:
    """Butta davvero le schede tolte da più di una settimana. Lo chiama l'orologio.

    Scrive come ``user``: non è Jenny che decide qualcosa sulla bacheca, è la
    scadenza dell'Annulla che l'utente ha avuto (la regola la dice
    :data:`model.REMOVED_KEEP_DAYS`). Quel che la scheda faceva da sé se ne va
    con lei: i suoi job — cron e promemoria — e il riassunto derivato.
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
    for entry in expired:
        card = entry["card"]
        for job_id in card_jobs(cron, root.name, card["id"]):
            cron.remove_job(job_id)
        delete_card_summary(root, card["id"])
    return len(expired)
