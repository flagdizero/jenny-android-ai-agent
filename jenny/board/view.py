"""Quel che la bacheca e un cassetto mostrano, calcolato dal server.

Il client non legge mai ``board.json``: riceve una vista già risolta — i
segnali di ogni scheda, i post-it delle attese aperte, le righe di «dall'ultima
volta» successive all'ultima apertura, quante pagine stanno fuori bacheca. Così
le regole (un segnale per scheda, e in quale ordine) stanno in un posto solo, e
la WebView disegna e basta.

Le funzioni qui leggono file e non scrivono niente.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any

from jenny.board import model
from jenny.board.store import read_board, read_card_summary, read_reminders
from jenny.utils.wiki_paths import iter_wiki_pages

# Una sezione mini app che dice «allarme» o «dato vecchio» per una scheda.
# Iniettata: il calcolo legge i dati delle app (``jenny/board/sections.py``), e
# la vista della bacheca non deve sapere come.
AlertProbe = Callable[[Mapping[str, Any]], bool]

_EPOCH = datetime(1970, 1, 1).astimezone()


def wiki_titles(project_root: Path) -> dict[str, str]:
    """``{percorso relativo a wiki/: titolo}`` di ogni pagina del quaderno."""
    return dict(iter_wiki_pages(project_root / "wiki"))


def offboard_pages(board: Mapping[str, Any], titles: Mapping[str, str]) -> list[str]:
    """Le pagine che non stanno in nessun cassetto, in ordine di percorso."""
    owners = model.page_owners(board)
    return sorted(p for p in titles if p not in owners)


def _stamp(value: Any) -> datetime:
    return model.parse_iso(value) or _EPOCH


def card_last_change(card: Mapping[str, Any], summary: Mapping[str, Any] | None) -> datetime:
    """L'ultima volta che il cassetto è cambiato: un riassunto riscritto, o la nascita."""
    moments = [_stamp(card.get("created"))]
    for section in (summary or {}).get("sections") or []:
        moments.append(_stamp(section.get("changed")))
    return max(moments)


def card_signal(
    card: Mapping[str, Any],
    summary: Mapping[str, Any] | None,
    reminders: Mapping[str, Any],
    *,
    today: date | None = None,
    alert: bool = False,
) -> str | None:
    """L'unico segnale di una scheda, nell'ordine di priorità del disegno.

    1. ``alert`` — un problema (una sezione mini app in allarme o ferma);
    2. ``soon`` — un'attesa a data vicina, o già passata e ancora aperta;
    3. ``new`` — il cassetto è cambiato dopo l'ultima volta che l'hai aperto.

    Nessun segnale vuol dire «tutto tranquillo». Uno solo, al massimo: se
    qualcosa si accende, deve voler dire che conta.
    """
    if alert:
        return "alert"
    nxt = model.next_reminder(reminders, card["id"])
    if nxt is not None and model.reminder_is_soon(nxt, today):
        return "soon"
    if summary and card_last_change(card, summary) > _stamp(card.get("lastOpened")):
        return "new"
    return None


def _note_pos(
    board: Mapping[str, Any],
    reminders: Mapping[str, Any],
    card: Mapping[str, Any],
    placed: list[tuple[float, float, float, float]],
) -> dict[str, Any]:
    """Il posto di un post-it che non ne ha uno: il posto libero piu' vicino al suo soggetto.

    Calcolato e non salvato: le attese nate senza un posto (quelle della
    passata, quelle dell'utente) compaiono vicino alla loro scheda senza coprire
    niente, e restano dove sono finché qualcuno non le sposta.
    """
    near = (float(card["pos"]["x"]) + 12, float(card["pos"]["y"]) + 30)
    x, y = model.free_slot(board, reminders, box=model.NOTE_BOX, near=near, extra=placed)
    placed.append((x, y, x + model.NOTE_BOX[0], y + model.NOTE_BOX[1]))
    return {"x": x, "y": y, "by": model.JENNY}


def _cover(workspace: Path, card: Mapping[str, Any]) -> dict[str, Any]:
    """L'app di una scheda-app e la sua copertina: il valore al posto della foto.

    Solo la scheda che **e'** un'app ha una copertina: una scheda-soggetto
    («Acerello») tiene la sua foto, e un'app che c'entra con lei sta sulla sua
    polaroid, legata da un filo. ``hasScreen`` dice al tocco se aprire lo
    schermo dell'app o il cassetto.
    """
    from jenny.board.sections import cover_of, section_view

    if not isinstance(card.get("app"), dict):
        return {}
    section = {"id": model.APP_CARD, "kind": "app", "app": card["app"]["slug"],
               "params": card["app"].get("params") or {}}
    view = section_view(workspace, None, section) or {}
    # ``live`` si apre; ``trial`` apre la prova (Tienila); ``missing``,
    # ``trashed`` e ``broken`` aprono il cassetto, che dice cosa e' successo.
    if view.get("state") in ("missing", "trashed", "broken"):
        state = view["state"]
    elif view.get("codeStatus") in ("trial", "unapproved"):
        state = "trial"
    else:
        state = "live"
    out: dict[str, Any] = {"app": {
        "slug": card["app"]["slug"], "params": section["params"],
        "hasScreen": bool(view.get("hasScreen")), "state": state,
        "icon": view.get("icon") or "",
    }}
    cover = cover_of(view)
    if cover is not None:
        out["cover"] = cover
    return out


def build_board_view(
    project_root: Path,
    *,
    today: date | None = None,
    alert_probe: AlertProbe | None = None,
    workspace: Path | None = None,
) -> dict[str, Any]:
    """La risposta di ``GET /api/board``.

    Con *workspace*, una scheda che e' un'app porta anche la sua **copertina**
    (il valore dell'app, al posto della foto).
    """
    board = read_board(project_root)
    reminders = read_reminders(project_root)
    titles = wiki_titles(project_root)
    if board is None:
        return {
            "exists": False,
            "cards": [],
            "notes": [],
            "connections": [],
            "suggestions": [],
            "since": [],
            "offboard": len(titles),
            "pages": len(titles),
        }
    cards_out: list[dict[str, Any]] = []
    on_board: dict[str, dict[str, Any]] = {}
    for card in board["cards"]:
        summary = read_card_summary(project_root, card["id"])
        # L'app della scheda-app conta come una sezione: accende l'angolo
        # rosso e il ⟳ come le altre.
        live_sections = list(card.get("userSections") or [])
        if isinstance(card.get("app"), dict):
            live_sections.append({"id": model.APP_CARD, "kind": "app",
                                  "app": card["app"]["slug"],
                                  "params": card["app"].get("params") or {}})
        alert = bool(alert_probe and any(
            alert_probe(section) for section in live_sections
            if section.get("kind") in ("app", "micro")
        ))
        # Il segno ⟳: la scheda ha qualcosa che gira da solo (una micro app
        # tenuta, un cron, un'app col suo lettore). E' un segno, non un
        # segnale: non e' rosso.
        auto = any(
            (sec.get("kind") == "micro" and sec.get("pending") is not True)
            or sec.get("kind") == "cron"
            for sec in card.get("userSections") or []
        )
        on_board[card["id"]] = card
        cards_out.append({
            "id": card["id"],
            "title": card["title"],
            "glyph": card.get("glyph") or "",
            "pos": card["pos"],
            "pinned": card.get("pinned", False),
            "pages": len(card.get("pages") or []),
            "signal": card_signal(card, summary, reminders, today=today, alert=alert),
            "auto": auto,
            **(_cover(workspace, card) if workspace is not None else {}),
        })
    notes: list[dict[str, Any]] = []
    placed: list[tuple[float, float, float, float]] = []
    for rem in model.open_reminders(reminders):
        card = on_board.get(rem.get("card", ""))
        if card is None:
            continue
        notes.append({
            "id": rem["id"],
            "card": card["id"],
            "text": rem["text"],
            "when": rem["when"],
            "pos": rem.get("pos") or _note_pos(board, reminders, card, placed),
        })
    last_seen = _stamp(board.get("lastSeen"))
    since = [row for row in board.get("since") or [] if _stamp(row.get("at")) > last_seen]
    offboard = offboard_pages(board, titles)
    return {
        "exists": True,
        "skeleton": board["skeleton"],
        "cards": cards_out,
        "notes": notes,
        "connections": [
            c for c in board["connections"] if c["a"] in on_board and c["b"] in on_board
        ],
        "suggestions": [
            {k: s.get(k) for k in ("id", "kind", "subject", "text")}
            for s in board["suggestions"]
        ],
        "since": since,
        "offboard": len(offboard),
        "pages": len(titles),
        "lastSeen": board.get("lastSeen") or "",
        # A quale cassetto appartiene ogni pagina: la ricerca lo scrive accanto a
        # ogni risultato («in Acero», «fuori bacheca»).
        "pageOwners": model.page_owners(board),
        "full": model.is_full(board, reminders),
    }


def build_card_view(
    project_root: Path,
    card_id: str,
    *,
    today: date | None = None,
    sections_resolver: Callable[[Mapping[str, Any], Mapping[str, Any]], dict[str, Any]]
    | None = None,
    corrections: list[dict[str, Any]] | None = None,
    cron_jobs: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """La risposta di ``GET /api/board/card``, o ``None`` se la scheda non c'è.

    *cron_jobs* sono i job dello scheduler legati a questo quaderno (``{id:
    istantanea}``): la sezione di un cron mostra il suo prossimo giro e l'esito.
    """
    board = read_board(project_root)
    if board is None:
        return None
    card = model.card_by_id(board, card_id)
    if card is None:
        return None
    reminders = read_reminders(project_root)
    titles = wiki_titles(project_root)
    summary = read_card_summary(project_root, card_id) or {}
    opened = _stamp(card.get("lastOpened"))
    sections = []
    for section in summary.get("sections") or []:
        sources = [
            {"path": p, "title": titles.get(p) or Path(p).stem, "exists": p in titles}
            for p in section.get("sources") or []
        ]
        sections.append({
            "title": section["title"],
            "text": section["text"],
            "sources": sources,
            "fresh": _stamp(section.get("changed")) > opened,
        })
    # Le sezioni nello stesso ordine dello scheletro del quaderno, così l'utente
    # sa sempre dove guardare; quelle che lo scheletro non conosce vanno in coda.
    order = {title: i for i, title in enumerate(board["skeleton"])}
    sections.sort(key=lambda s: order.get(s["title"], len(order)))
    by_id = {c["id"]: c for c in board["cards"]}
    connections = []
    for conn in board["connections"]:
        if card_id not in (conn["a"], conn["b"]):
            continue
        other = conn["b"] if conn["a"] == card_id else conn["a"]
        if other in by_id:
            connections.append({
                "to": other, "title": by_id[other]["title"], "label": conn["label"],
                "by": conn["by"],
            })
    nxt = model.next_reminder(reminders, card_id)
    user_sections = []
    for section in card.get("userSections") or []:
        resolved = dict(section)
        if sections_resolver is not None:
            resolved.update(sections_resolver(section, {
                "reminders": reminders,
                "card": card["id"],
                "cronJobs": cron_jobs or {},
                "cronResults": board.get("cronResults") or {},
            }))
        user_sections.append(resolved)
    return {
        "id": card["id"],
        "title": card["title"],
        "glyph": card.get("glyph") or "",
        "pages": [
            {"path": p, "title": titles.get(p) or Path(p).stem}
            for p in card.get("pages") or [] if p in titles
        ],
        "generated": summary.get("generated") or "",
        "brief": summary.get("brief") or "",
        "sections": sections,
        "next": nxt,
        "reminders": [
            r for r in model.open_reminders(reminders) if r.get("card") == card_id
        ],
        "userSections": user_sections,
        "connections": connections,
        # Una correzione integrata mostra la sua ✓ fino alla prossima apertura
        # del cassetto, poi resta solo il riassunto rigenerato.
        "corrections": [
            c for c in corrections or []
            if c.get("status") == "pending" or _stamp(c.get("at")) > opened
        ],
        "skeleton": board["skeleton"],
    }
