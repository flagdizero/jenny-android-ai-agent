"""L'orologio dei quaderni: il job di sistema ``reminders``.

Gira ogni quarto d'ora e fa tre cose, nessuna delle quali è un promemoria:

- fa girare i **lettori delle app** generali (una volta per giro, per tutti i
  quaderni insieme), così i dati che le schede-app mostrano restano freschi;
- guarda le **sezioni mini app** dei cassetti: quando lo stato di una cambia (va
  in allarme, si ferma, torna a posto), il codice scrive **una** riga di diario
  e una di «dall'ultima volta». Le letture non entrano mai nella wiki; gli
  eventi sì;
- butta le **schede tolte** da più di una settimana, coi loro job
  (:func:`jenny.board.removed.purge_removed_cards`).

Fino al 31/10/2026 leggeva anche ``board/reminders.json`` e annunciava le attese
a data e a condizione in un turno silenzioso della chat personale. Quel file non
si scrive più: un promemoria del quaderno è un job dello scheduler (un singolo, o
un ricorrente con un filtro ``condition`` guardato dal codice prima del modello,
v. ``jenny/cron/condition.py``), e suona da solo nella chat del suo quaderno. Un
file rimasto da un'installazione di prima lo converte in job la prima passata
(:mod:`jenny.board.legacy_reminders`). Il nome del job resta ``reminders``.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.board import model
from jenny.board.store import mutate_board, read_board

# Il passo dell'orologio: un quarto d'ora (decisione del 05/10/2026). E' anche
# l'intervallo minimo di un lettore (``micro.MIN_EVERY_MINUTES``).
CLOCK_EVERY_MS = 15 * 60 * 1000


def _local_now(timezone_name: str | None, now: datetime | None) -> datetime:
    if now is not None:
        return now
    from jenny.utils.helpers import safe_zoneinfo

    tz = safe_zoneinfo(timezone_name) if timezone_name else None
    return datetime.now(tz=tz) if tz else datetime.now().astimezone()


def watch_app_sections(workspace: Path, root: Path, *, timezone_name: str | None = None) -> int:
    """Gli eventi delle sezioni mini app: una riga di diario per ogni cambio di stato.

    Lo stato visto l'ultima volta sta in ``board.json`` (``appStates``): la prima
    volta che una sezione si vede, si registra e basta — un'app agganciata
    stamattina non «va in allarme» per il solo fatto di essere nuova.
    """
    from jenny.board.sections import journal_event, section_view

    board = read_board(root)
    if board is None:
        return 0
    known: dict[str, str] = dict(board.get("appStates") or {})
    seen: dict[str, str] = {}
    events: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    for card in board["cards"]:
        sections = list(card.get("userSections") or [])
        if isinstance(card.get("app"), dict):
            sections.append({"id": model.APP_CARD, "kind": "app", "app": card["app"]["slug"],
                             "params": card["app"].get("params") or {}})
        for section in sections:
            view = section_view(workspace, section)
            if view is None:
                continue
            key = f"{card['id']}/{section['id']}"
            state = str(view.get("state"))
            seen[key] = state
            if key in known and known[key] != state:
                events.append((card, section, view))
    if seen == known:
        return 0
    for card, _section, view in events:
        try:
            journal_event(root, card["title"], view, timezone_name=timezone_name)
        except OSError as exc:
            logger.warning("reminders: app event not journaled in {}: {}", root.name, exc)

    def apply(b: dict[str, Any]) -> None:
        b["appStates"] = seen
        for card, _section, view in events:
            model.add_since(b, "app_event", card=card["id"], title=card["title"],
                            app=view.get("name") or view.get("slug"), state=view.get("state"))

    mutate_board(root, apply, actor=model.JENNY)
    return len(events)


async def run_reminders(
    agent: Any,
    workspace: Path,
    *,
    wikis_dir_name: str = "wikis",
    timezone_name: str | None = None,
    now: datetime | None = None,
    cron: Any = None,
) -> str | None:
    """Un giro dell'orologio su tutti i quaderni. Torna una riga per il log del cron.

    I lettori delle app, gli eventi delle sezioni mini app, e le schede tolte da
    più di una settimana. *agent* non serve più (nessun annuncio parte da qui) e
    resta nella firma per chi chiama. *cron* è il servizio dei job: serve a
    buttare i job delle schede tolte (:func:`jenny.board.removed.purge_removed_cards`).
    """
    from jenny.board.legacy_reminders import migrate_legacy_reminders
    from jenny.board.removed import purge_removed_cards
    from jenny.utils.wiki_paths import discover_wiki_roots

    del agent
    current = _local_now(timezone_name, now)
    events = 0
    produced = 0
    migrated = 0
    # **Prima chi produce, poi chi giudica**: i lettori delle app generali girano
    # una volta per giro, per tutti i quaderni insieme.
    try:
        from jenny.apps.reader import run_due_readers

        produced += await run_due_readers(workspace, now=current)
    except Exception:  # noqa: BLE001 — un lettore rotto non ferma l'orologio
        logger.exception("reminders: app readers not run")
    for name, root in sorted(discover_wiki_roots(workspace / wikis_dir_name).items()):
        if read_board(root) is None:
            continue
        try:
            migrated += migrate_legacy_reminders(root, cron, timezone_name=timezone_name,
                                                 now=current)
        except Exception:  # noqa: BLE001 — un quaderno rotto non ferma gli altri
            logger.exception("reminders: legacy reminders of {} not migrated", name)
        try:
            purge_removed_cards(root, cron, now=current)
        except Exception:  # noqa: BLE001 — un quaderno rotto non ferma gli altri
            logger.exception("reminders: removed cards of {} not purged", name)
        try:
            events += watch_app_sections(workspace, root, timezone_name=timezone_name)
        except Exception:  # noqa: BLE001 — un quaderno rotto non ferma gli altri
            logger.exception("reminders: app sections of {} not checked", name)
    if not events and not produced and not migrated:
        return None
    line = f"reminders: {events} app events, {produced} reader runs"
    return line + (f", {migrated} reminders migrated" if migrated else "")
