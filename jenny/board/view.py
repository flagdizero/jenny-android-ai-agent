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


def _place_near(
    board: Mapping[str, Any],
    reminders: Mapping[str, Any],
    near: tuple[float, float],
    box: tuple[float, float],
    placed: list[tuple[float, float, float, float]],
) -> dict[str, Any]:
    """Il posto libero più vicino a *near* per un oggetto di ingombro *box*.

    Calcolato e non salvato: l'oggetto nasce accanto alla sua scheda senza
    coprire niente (né le schede né gli altri oggetti già messi in questo giro),
    e resta lì finché qualcuno non lo porta altrove.
    """
    x, y = model.free_slot(board, reminders, box=box, near=near, extra=placed)
    placed.append((x, y, x + box[0], y + box[1]))
    return {"x": x, "y": y, "by": model.JENNY}


def _postit_pos(
    board: Mapping[str, Any],
    reminders: Mapping[str, Any],
    card: Mapping[str, Any],
    placed: list[tuple[float, float, float, float]],
) -> dict[str, Any]:
    """Il posto del post-it delle attese finché l'utente non lo sposta: vicino
    all'angolo in basso a destra della sua polaroid."""
    w, h = model.card_box(card)
    near = (float(card["pos"]["x"]) + w * 0.6, float(card["pos"]["y"]) + h * 0.75)
    return _place_near(board, reminders, near, model.POSTIT_BOX, placed)


def _card_notes(card: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Le note dell'utente su una scheda, dalla più recente."""
    notes = [s for s in card.get("userSections") or [] if s.get("kind") == "note"]
    return sorted(notes, key=lambda s: s.get("created") or "", reverse=True)


def _card_photos(project_root: Path, card: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Le foto di una scheda il cui file c'è ancora, dalla più recente."""
    photos = [s for s in card.get("userSections") or []
              if s.get("kind") == "photo" and (project_root / str(s.get("path"))).is_file()]
    return sorted(photos, key=lambda s: s.get("created") or "", reverse=True)


def _app_card(workspace: Path, card: Mapping[str, Any]) -> dict[str, Any]:
    """L'app di una scheda-app e la sua striscia.

    Una scheda che **e'** un'app con dati si mostra come una **striscia**
    (``jenny/apps/slip_view.py``): intestazione e blocchi, della sua taglia; una
    senza dati resta una polaroid con l'icona dell'app. ``hasScreen`` dice al
    tocco se aprire lo schermo dell'app o il cassetto; ``box`` e' l'ingombro della
    taglia vera, cosi' zoom e posto libero misurano quel che si vede.
    """
    from jenny.apps.manifest import find_app
    from jenny.apps.slip_view import slip_view
    from jenny.board.sections import section_view

    if not isinstance(card.get("app"), dict):
        return {}
    slug = card["app"]["slug"]
    params = card["app"].get("params") or {}
    section = {"id": model.APP_CARD, "kind": "app", "app": slug, "params": params}
    view = section_view(workspace, section) or {}
    # ``live`` si apre; ``missing``, ``trashed`` e ``broken`` aprono il
    # cassetto, che dice cosa e' successo.
    state = view["state"] if view.get("state") in ("missing", "trashed", "broken") else "live"
    out: dict[str, Any] = {"app": {
        "slug": slug, "params": params,
        "hasScreen": bool(view.get("hasScreen")), "state": state,
        "icon": view.get("icon") or "",
    }}
    if state == "live":
        app = find_app(workspace, slug)
        slip = slip_view(workspace, app, params) if app is not None else {}
        if slip:
            out["slip"] = slip
            out["box"] = list(model.SLIP_BOX[slip["size"]])
    return out


def build_board_view(
    project_root: Path,
    *,
    today: date | None = None,
    alert_probe: AlertProbe | None = None,
    workspace: Path | None = None,
) -> dict[str, Any]:
    """La risposta di ``GET /api/board``.

    Con *workspace*, una scheda che e' un'app porta anche la sua **striscia**
    (``slip``: la forma compatta dell'app, coi suoi blocchi) e l'ingombro vero.
    """
    board = read_board(project_root)
    reminders = read_reminders(project_root)
    titles = wiki_titles(project_root)
    if board is None:
        return {
            "exists": False,
            "cards": [],
            "notes": [],
            "postits": [],
            "notePostits": [],
            "galleries": [],
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
            if section.get("kind") == "app"
        ))
        # Il segno ⟳: la scheda ha qualcosa che gira da solo (un cron). E' un
        # segno, non un segnale: non e' rosso.
        auto = any(sec.get("kind") == "cron" for sec in card.get("userSections") or [])
        on_board[card["id"]] = card
        cards_out.append({
            "id": card["id"],
            "title": card["title"],
            "glyph": card.get("glyph") or "",
            "pos": card["pos"],
            "box": list(model.card_box(card)),
            "pinned": card.get("pinned", False),
            "pages": len(card.get("pages") or []),
            "signal": card_signal(card, summary, reminders, today=today, alert=alert),
            "auto": auto,
            **(_app_card(workspace, card) if workspace is not None else {}),
        })
    # Le attese aperte, dalla più vicina; e un post-it per ogni scheda che ne
    # ha, con le sue prime righe e un filo verso la scheda.
    notes = [
        {"id": rem["id"], "card": rem["card"], "text": rem["text"], "when": rem["when"]}
        for rem in model.board_reminders(board, reminders)
    ]
    with_notes = {n["card"] for n in notes}
    placed: list[tuple[float, float, float, float]] = []
    postits = [
        {"card": card["id"],
         "pos": card.get("postitPos") or _postit_pos(board, reminders, card, placed)}
        for card in board["cards"] if card["id"] in with_notes
    ]
    # Le note dell'utente, accorpate in un post-it lilla per scheda (sotto la
    # polaroid), e le sue foto in un mazzetto (a destra). Dopo i gialli, così i
    # posti calcolati dei gialli restano quelli di sempre.
    note_postits = []
    for card in board["cards"]:
        jots = _card_notes(card)
        if not jots:
            continue
        h = model.card_box(card)[1]
        near = (float(card["pos"]["x"]), float(card["pos"]["y"]) + h + 1)
        note_postits.append({
            "card": card["id"],
            "pos": card.get("notesPos")
            or _place_near(board, reminders, near, model.NOTES_BOX, placed),
            "count": len(jots),
            "rows": [{"text": n.get("text") or "", "created": n.get("created") or ""}
                     for n in jots[:model.NOTE_ROWS]],
        })
    galleries = []
    for card in board["cards"]:
        photos = _card_photos(project_root, card)
        if not photos:
            continue
        w = model.card_box(card)[0]
        near = (float(card["pos"]["x"]) + w + 3, float(card["pos"]["y"]))
        galleries.append({
            "card": card["id"],
            "pos": card.get("photosPos")
            or _place_near(board, reminders, near, model.GALLERY_BOX, placed),
            "count": len(photos),
            "photos": [{"id": p["id"], "path": p["path"], "caption": p.get("caption") or ""}
                       for p in photos[:model.GALLERY_SHOWN]],
        })
    last_seen = _stamp(board.get("lastSeen"))
    since = [row for row in board.get("since") or [] if _stamp(row.get("at")) > last_seen]
    offboard = offboard_pages(board, titles)
    return {
        "exists": True,
        "skeleton": board["skeleton"],
        "cards": cards_out,
        "notes": notes,
        "postits": postits,
        "postitRows": model.POSTIT_ROWS,
        "notePostits": note_postits,
        "galleries": galleries,
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
    user_sections = []
    notes = []
    photos = []
    # Il cassetto di una scheda che **e'** un'app comincia dall'app: la sua
    # striscia intera (la scheda senza schermo si apre qui, non altrove).
    own = ([{"id": model.APP_CARD, "kind": "app", "app": card["app"]["slug"],
             "params": card["app"].get("params") or {}, "own": True}]
           if isinstance(card.get("app"), dict) else [])
    for section in [*own, *(card.get("userSections") or [])]:
        # Un promemoria aggiunto dal cassetto ha ancora una sua sezione, ma si
        # mostra una volta sola: nel calendario di cura, con quelli di Jenny.
        if section.get("kind") == "reminder":
            continue
        resolved = dict(section)
        if sections_resolver is not None:
            resolved.update(sections_resolver(section, {
                "reminders": reminders,
                "card": card["id"],
                "cronJobs": cron_jobs or {},
                "cronResults": board.get("cronResults") or {},
            }))
        # Le note e le foto dell'utente non sono sezioni fra le altre: sono i
        # due mazzetti sotto il brief, post-it lilla e polaroid
        # (``home-board-notes.js``, ``home-board-photos.js``).
        if section.get("kind") == "note":
            notes.append({key: resolved[key] for key in ("id", "text", "created", "status")
                          if key in resolved})
        elif section.get("kind") == "photo":
            photos.append({key: resolved[key] for key in
                           ("id", "path", "caption", "created", "exists", "status")
                           if key in resolved})
        else:
            user_sections.append(resolved)
    notes.sort(key=lambda n: n.get("created") or "", reverse=True)
    photos.sort(key=lambda p: p.get("created") or "", reverse=True)
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
        # Il calendario di cura: le attese aperte della scheda, dalla più vicina.
        "reminders": model.sorted_reminders(
            r for r in model.open_reminders(reminders) if r.get("card") == card_id
        ),
        # Le note e le foto dell'utente, dalla più recente.
        "notes": notes,
        "photos": photos,
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
