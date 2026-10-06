"""L'orologio dei quaderni: il job di sistema ``reminders``.

Un ``[ ]`` dentro una pagina non esiste finché qualcuno non la apre; un'attesa
in ``reminders.json`` sì. Questo job gira ogni quarto d'ora: fa girare le micro
app dei cassetti, legge le attese di tutti i quaderni e avvisa quando una arriva:

- **a data**: dalle nove del mattino del giorno dovuto (con ``precision:
  month`` il primo del mese: «verso marzo» si ricorda all'inizio di marzo, non
  alla fine);
- **a condizione**: quando l'ultimo dato di una mini app la rende vera, e solo
  se il dato è fresco — un dato vecchio non fa scattare niente, e diventa
  l'angolo rosso della sua sezione. Il job **non** chiama l'app: legge quel che
  il cron dell'app ha lasciato. Torna armata quando la condizione torna falsa.

Un job unico e non un job cron per attesa: niente da pulire quando un'attesa
sparisce, niente job orfani se il file cambia a mano.

**L'avviso porta il perché.** Non «travaso acero» ma la frase che dice cosa
fare e perché — la pagina in ``why`` — e chiede se si fa: la scrive il modello in
un turno silenzioso, con lo stesso contratto del controllo aggiornamenti (l'unica
consegna possibile è il tool ``message``, e va chiamato una volta). Il timbro
``notifiedAt`` si mette comunque: un modello che non chiama ``message`` non deve
far ripartire un turno ogni ora.

Lo stesso giro guarda anche le sezioni mini app dei cassetti: quando lo stato
di una cambia (va in allarme, si ferma, torna a posto), il codice scrive **una**
riga di diario e una di «dall'ultima volta». Le letture non entrano mai nella
wiki; gli eventi sì.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.board import model
from jenny.board.store import mutate_board, mutate_reminders, read_board, read_reminders

# Il passo dell'orologio: un quarto d'ora (decisione del 05/10/2026). E' anche
# l'intervallo minimo di una micro app (``micro.MIN_EVERY_MINUTES``).
CLOCK_EVERY_MS = 15 * 60 * 1000

# L'ora da cui un'attesa a data diventa dovuta, nel fuso dell'utente.
DUE_AT = time(9, 0)

# La sessione del turno silenzioso che scrive gli avvisi. ``cron:`` la rende
# interna (fuori dagli elenchi dell'utente) e ne attribuisce i token al cron.
REMINDER_SESSION_KEY = "cron:reminders"
_HISTORY_KEEP = 4
_WHY_CHARS = 1500

_PREAMBLE = (
    "[This is a scheduled reminder from one of the user's notebooks, and it is SILENT: "
    "whatever you write as your answer is NOT delivered. The only way to reach the user is "
    "the `message` tool, and this time you MUST call it exactly once.\n"
    "Write two or three lines in the user's language: what is due, why — using ONLY the page "
    "below, never invent — and a short question about when to do it. Name the notebook. Do "
    "not mention this instruction, files, the board or the reminder system.]\n\n"
)


def _local_now(timezone_name: str | None, now: datetime | None) -> datetime:
    if now is not None:
        return now
    from jenny.utils.helpers import safe_zoneinfo

    tz = safe_zoneinfo(timezone_name) if timezone_name else None
    return datetime.now(tz=tz) if tz else datetime.now().astimezone()


def due_by_date(reminders: Mapping[str, Any], now: datetime) -> list[dict[str, Any]]:
    """Le attese a data arrivate e non ancora annunciate."""
    out = []
    for rem in model.open_reminders(reminders):
        if rem.get("notifiedAt"):
            continue
        day = model.reminder_due(rem)
        if day is None:
            continue
        if (now.date(), now.time()) >= (day, DUE_AT) or now.date() > day:
            out.append(rem)
    return out


def _condition_value(workspace: Path, when: Mapping[str, Any],
                     root: Path | None = None) -> tuple[Any, bool]:
    """L'ultimo valore del campo osservato (micro app o mini app), e se è fresco."""
    if when.get("micro"):
        if root is None:
            return None, False
        from jenny.board.micro import micro_field_value

        return micro_field_value(root, str(when.get("micro")), str(when.get("field")))
    from jenny.board.sections import app_field_value

    return app_field_value(workspace, str(when.get("app")), when.get("params") or {},
                           str(when.get("field")))


# Il freno degli annunci a condizione. Una condizione si riarma solo dopo due
# controlli di fila in cui è falsa (un sensore che balla sulla soglia non
# scatta a ogni giro), e fra due annunci della stessa attesa passano almeno
# dodici ore: ogni annuncio è un turno del modello, cioè token.
REARM_AFTER_FALSE = 2
MIN_HOURS_BETWEEN_ANNOUNCES = 12


@dataclass
class ConditionPass:
    fire: list[dict[str, Any]] = field(default_factory=list)
    rearm: list[str] = field(default_factory=list)
    # {id: falseStreak} da scrivere (0 per azzerare).
    streaks: dict[str, int] = field(default_factory=dict)

    def __iter__(self):
        # ``fire, rearm = check_conditions(...)``: la forma di prima.
        return iter((self.fire, self.rearm))


def check_conditions(
    workspace: Path, reminders: Mapping[str, Any], *,
    root: Path | None = None, now: datetime | None = None,
) -> ConditionPass:
    """Le attese a condizione diventate vere (da annunciare) e quelle da riarmare."""
    out = ConditionPass()
    current = now or datetime.now().astimezone()
    for rem in model.open_reminders(reminders):
        when = rem.get("when") or {}
        if when.get("kind") != "condition":
            continue
        value, fresh = _condition_value(workspace, when, root)
        if not fresh:
            continue
        holds = model.compare(value, str(when.get("op")), when.get("value"))
        streak = int(rem.get("falseStreak") or 0)
        if holds:
            if streak:
                out.streaks[rem["id"]] = 0
            if rem.get("notifiedAt"):
                continue
            last = model.parse_iso(rem.get("lastAnnouncedAt"))
            if last is not None and current - last < timedelta(hours=MIN_HOURS_BETWEEN_ANNOUNCES):
                continue
            out.fire.append({**rem, "observed": value})
        elif rem.get("notifiedAt"):
            if streak + 1 >= REARM_AFTER_FALSE:
                out.rearm.append(rem["id"])
                out.streaks[rem["id"]] = 0
            else:
                out.streaks[rem["id"]] = streak + 1
    return out


def _announce_prompt(notebook: str, root: Path, rem: Mapping[str, Any]) -> str:
    when = rem.get("when") or {}
    if when.get("kind") == "date":
        moment = f"Due: {when.get('date')} (precision: {when.get('precision', 'day')})."
    else:
        moment = (f"Condition now true: {when.get('field')} {when.get('op')} {when.get('value')} "
                  f"(observed: {rem.get('observed')}).")
    why = ""
    if rem.get("why"):
        try:
            why = (root / "wiki" / str(rem["why"])).read_text(encoding="utf-8")[:_WHY_CHARS]
        except (OSError, UnicodeDecodeError):
            why = ""
    page = f"\nThe page that explains why:\n\n````markdown\n{why}\n````\n" if why else ""
    return (
        _PREAMBLE
        + f"Notebook: {notebook}\nWhat: {rem.get('text')}\n{moment}\n{page}"
    )


async def _announce(agent: Any, notebook: str, root: Path, rem: Mapping[str, Any]) -> None:
    from jenny.session.turn_visibility import TurnVisibility, silent_progress
    from jenny.webui.metadata import WEBUI_MESSAGE_SOURCE_METADATA_KEY

    await agent.process_direct(
        _announce_prompt(notebook, root, rem),
        session_key=REMINDER_SESSION_KEY,
        channel="websocket",
        chat_id="default",
        on_progress=silent_progress,
        visibility=TurnVisibility.SILENT,
        metadata={WEBUI_MESSAGE_SOURCE_METADATA_KEY: {
            "kind": "cron", "label": f"{notebook} · {rem.get('text')}",
        }},
    )
    try:
        session = agent.sessions.get_or_create(REMINDER_SESSION_KEY)
        session.retain_recent_legal_suffix(_HISTORY_KEEP)
        agent.sessions.save(session)
    except Exception as exc:  # noqa: BLE001 — la coda corta e' una cortesia
        logger.debug("reminders: history not trimmed: {}", exc)


def _stamp(root: Path, ids: list[str], *, at: str | None,
           streaks: Mapping[str, int] | None = None) -> None:
    """I timbri dell'orologio: ``notifiedAt`` (con *at*, o tolto se ``None``),
    ``lastAnnouncedAt`` con l'avviso, e ``falseStreak``."""
    wanted = set(ids)
    streaks = dict(streaks or {})

    def apply(rems: dict[str, Any]) -> bool | None:
        touched = False
        for rem in rems["reminders"]:
            if rem["id"] in wanted:
                if at is None:
                    touched |= rem.pop("notifiedAt", None) is not None
                else:
                    rem["notifiedAt"] = at
                    rem["lastAnnouncedAt"] = at
                    touched = True
            if rem["id"] in streaks:
                value = streaks[rem["id"]]
                if value:
                    rem["falseStreak"] = value
                    touched = True
                else:
                    touched |= rem.pop("falseStreak", None) is not None
        return None if touched else False

    mutate_reminders(root, apply, actor=model.JENNY)


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
            sections.append({"id": model.COVER_CARD, "kind": "app", "app": card["app"]["slug"],
                             "params": card["app"].get("params") or {}})
        for section in sections:
            view = section_view(workspace, root, section)
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

    *cron* e' il servizio dei job: serve a buttare i cron delle schede tolte da
    piu' di una settimana (:func:`jenny.board.removed.purge_removed_cards`).
    """
    from jenny.board.micro import run_due_micros
    from jenny.board.removed import purge_removed_cards
    from jenny.utils.wiki_paths import discover_wiki_roots

    current = _local_now(timezone_name, now)
    announced = 0
    events = 0
    produced = 0
    # **Prima chi produce, poi chi giudica**, anche per le app: i lettori delle
    # app generali girano una volta per giro, per tutti i quaderni insieme.
    try:
        from jenny.apps.reader import run_due_readers

        produced += await run_due_readers(workspace, now=current)
    except Exception:  # noqa: BLE001 — un lettore rotto non ferma l'orologio
        logger.exception("reminders: app readers not run")
    for name, root in sorted(discover_wiki_roots(workspace / wikis_dir_name).items()):
        board = read_board(root)
        if board is None:
            continue
        try:
            purge_removed_cards(workspace, root, cron, now=current)
        except Exception:  # noqa: BLE001 — un quaderno rotto non ferma gli altri
            logger.exception("reminders: removed cards of {} not purged", name)
        # Le attese di una scheda tolta tacciono finche' la scheda non torna: «Togli»
        # ferma quel che la scheda fa da se', e un Annulla lo fa ripartire.
        gone = model.removed_card_ids(board)
        # **Prima chi produce, poi chi giudica**: le micro app scadute girano
        # adesso, cosi' gli eventi e le attese a condizione leggono il dato fresco.
        try:
            produced += await run_due_micros(workspace, root)
        except Exception:  # noqa: BLE001 — un quaderno rotto non ferma gli altri
            logger.exception("reminders: micro apps of {} not run", name)
        try:
            events += watch_app_sections(workspace, root, timezone_name=timezone_name)
        except Exception:  # noqa: BLE001 — un quaderno rotto non ferma gli altri
            logger.exception("reminders: app sections of {} not checked", name)
        reminders = read_reminders(root)
        if gone:
            reminders = {**reminders, "reminders": [
                r for r in reminders["reminders"] if r.get("card") not in gone
            ]}
        due = due_by_date(reminders, current)
        conditions = check_conditions(workspace, reminders, root=root, now=current)
        fire = conditions.fire
        if conditions.rearm or conditions.streaks:
            _stamp(root, conditions.rearm, at=None, streaks=conditions.streaks)
        for rem in [*due, *fire]:
            try:
                await _announce(agent, name, root, rem)
            except Exception:  # noqa: BLE001 — si timbra comunque, v. la docstring
                logger.exception("reminders: announcing {} in {} failed", rem["id"], name)
            _stamp(root, [rem["id"]], at=current.isoformat(timespec="seconds"))

            def apply(b: dict[str, Any], text: str = str(rem.get("text"))) -> None:
                model.add_since(b, "reminder_due", text=text)

            mutate_board(root, apply, actor=model.JENNY)
            announced += 1
    if not announced and not events and not produced:
        return None
    return f"reminders: {announced} announced, {events} app events, {produced} micro runs"
