"""Le scritture della bacheca dall'interfaccia: i comandi ``board.*``.

Sono comandi RPC e non rotte per la ragione di ``webui/commands.py``: una nota o
una correzione sono testo libero, e una foto sono byte. Tutte le scritture di
``board.json`` e ``reminders.json`` passano dall'imbuto
(:func:`jenny.board.store.mutate_board`) come ``user``: è l'utente che sposta,
risponde alla busta, aggiunge e toglie sezioni.

Ogni comando prende ``wiki`` (il nome del quaderno) e risolve la cartella lato
server; nessun percorso arriva dal client, tranne il nome di una pagina che si
confronta con le pagine che esistono.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.board import model
from jenny.board.store import (
    delete_card_summary,
    mutate_board,
    mutate_reminders,
    read_board,
    read_card_summary,
)
from jenny.webui.commands import (
    CommandContext,
    CommandError,
    _require_wiki_enabled,
    _wikis_dir,
)

# Una foto per una sezione: il client la riduce prima di mandarla (lato lungo
# 1600 px, JPEG), quindi questo tetto è una rete, non un limite che si incontra.
MAX_PHOTO_BYTES = 6_000_000
_PHOTO_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


def _project(ctx: CommandContext, params: Mapping[str, Any]) -> Path:
    from jenny.utils.wiki_paths import discover_wiki_roots

    _require_wiki_enabled(fail_closed=True)
    name = params.get("wiki")
    roots = discover_wiki_roots(_wikis_dir(ctx))
    if not isinstance(name, str) or name not in roots:
        raise CommandError("bad_request", "wiki required")
    return roots[name]


def _card_id(params: Mapping[str, Any], key: str = "card") -> str:
    value = params.get(key)
    if not model.is_valid_id(value):
        raise CommandError("bad_request", f"invalid {key}")
    return str(value)


def _number(params: Mapping[str, Any], key: str) -> float:
    value = params.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CommandError("bad_request", f"{key} must be a number")
    return float(value)


def _need_card(board: dict[str, Any], card_id: str) -> dict[str, Any]:
    card = model.card_by_id(board, card_id)
    if card is None:
        raise CommandError("not_found", "card not found")
    return card


async def _run(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Su disco, quindi fuori dal loop; un ``CommandError`` torna com'è."""
    return await asyncio.to_thread(fn, *args, **kwargs)


def _timezone() -> str | None:
    try:
        from jenny.config.loader import load_config

        return load_config().agents.defaults.timezone or None
    except Exception:  # noqa: BLE001 — l'ora locale del sistema è il ripiego
        return None


# ── lastSeen / lastOpened ────────────────────────────────────────────────────


async def board_seen(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """«L'ultima volta»: la bacheca aperta, o un cassetto aperto."""
    root = _project(ctx, params)
    card_id = params.get("card")
    stamp = model.now_iso()

    def apply(board: dict[str, Any]) -> bool | None:
        if card_id is None:
            board["lastSeen"] = stamp
            return None
        card = model.card_by_id(board, str(card_id))
        if card is None:
            return False
        card["lastOpened"] = stamp
        return None

    if card_id is not None:
        _card_id(params)
    if not (root / "board" / "board.json").exists():
        return {"ok": True}
    await _run(mutate_board, root, apply)
    return {"ok": True, "at": stamp}


# ── La forma: spostare ───────────────────────────────────────────────────────


async def board_move(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """Trascina = sposta: la posizione diventa dell'utente, e Jenny non la tocca più."""
    root = _project(ctx, params)
    kind = params.get("kind", "card")
    item = _card_id(params, "id")
    pos = {"x": _number(params, "x"), "y": _number(params, "y"), "by": model.USER}

    if kind == "card":
        def apply(board: dict[str, Any]) -> None:
            _need_card(board, item)["pos"] = pos

        await _run(mutate_board, root, apply)
    elif kind == "note":
        def apply_rem(rems: dict[str, Any]) -> None:
            for rem in rems["reminders"]:
                if rem["id"] == item:
                    rem["pos"] = pos
                    return
            raise CommandError("not_found", "reminder not found")

        await _run(mutate_reminders, root, apply_rem)
    else:
        raise CommandError("bad_request", "kind must be card or note")
    return {"ok": True}


# ── La busta ─────────────────────────────────────────────────────────────────


def _near(params: Mapping[str, Any]) -> tuple[float, float] | None:
    """Il punto del muro che l'utente sta guardando, se il client lo manda."""
    raw = params.get("near")
    if not isinstance(raw, Mapping):
        return None
    x, y = raw.get("x"), raw.get("y")
    if isinstance(x, bool) or isinstance(y, bool) or not isinstance(x, (int, float)) \
            or not isinstance(y, (int, float)):
        return None
    return (max(-model.WALL_MAX, min(model.WALL_MAX, float(x))),
            max(-model.WALL_MAX, min(model.WALL_MAX, float(y))))


def _accept(board: dict[str, Any], suggestion: dict[str, Any], reminders: dict[str, Any],
            near: tuple[float, float] | None = None) -> None:
    """Applica una proposta accettata. Il posto lo sceglie il codice, non il modello.

    Con *near* — il centro di dove l'utente sta guardando il muro — le schede
    nuove nascono li' vicino, e non nello schermo dell'origine.
    """
    kind = suggestion["kind"]
    owners = model.page_owners(board)
    taken = {c["id"] for c in board["cards"]}
    stamp = model.now_iso()

    def new_card(title: str, glyph: str, pages: list[str], wanted: str = "") -> dict[str, Any]:
        # L'id proposto, se c'e' ed e' libero: la passata che propone la prima
        # bacheca scrive gia' i cassetti con quegli id, e accettare non deve
        # lasciarli orfani.
        base = wanted if model.is_valid_id(wanted) else model.slugify(title)
        card_id = model.unique_id(base, taken)
        taken.add(card_id)
        target = (near[0] - model.CARD_BOX[0] / 2, near[1] - model.CARD_BOX[1] / 2) \
            if near is not None else None
        x, y = model.free_slot(board, reminders, near=target)
        card = {
            "id": card_id, "title": model.clean_text(title, model.MAX_TITLE) or card_id,
            "titleBy": model.JENNY,
            "pages": [p for p in pages if p not in owners],
            "pos": {"x": x, "y": y, "by": model.JENNY},
            "glyph": glyph, "userSections": [], "created": stamp,
        }
        owners.update({p: card_id for p in card["pages"]})
        board["cards"].append(card)
        return card

    if kind == "initial_board":
        if not board["skeleton"]:
            board["skeleton"] = [
                s for s in (model.clean_text(x, model.MAX_TITLE)
                            for x in suggestion.get("skeleton") or []) if s
            ][:model.MAX_SKELETON]
        for spec in (suggestion.get("cards") or [])[:model.MAX_INITIAL_CARDS]:
            if isinstance(spec, Mapping) and model.items_on_board(board, reminders) < model.MAX_ITEMS:
                new_card(str(spec.get("title") or ""), str(spec.get("glyph") or ""),
                         [p for p in spec.get("pages") or [] if isinstance(p, str)],
                         str(spec.get("id") or ""))
        model.add_since(board, "board_created", cards=len(board["cards"]))
    elif kind == "add_card":
        if model.is_full(board, reminders):
            raise CommandError("conflict", "the board is full")
        card = new_card(suggestion.get("subject") or "", str(suggestion.get("glyph") or ""),
                        list(suggestion.get("pages") or []))
        model.add_since(board, "card_added", card=card["id"], title=card["title"])
    elif kind == "remove_card":
        card_id = str(suggestion.get("card") or "")
        card = model.card_by_id(board, card_id)
        if card is not None:
            board["cards"].remove(card)
            board["connections"] = [
                c for c in board["connections"] if card_id not in (c["a"], c["b"])
            ]
            model.add_since(board, "card_removed", title=card["title"])
    elif kind == "add_connection":
        a, b = str(suggestion.get("a") or ""), str(suggestion.get("b") or "")
        if (
            model.card_by_id(board, a) and model.card_by_id(board, b)
            and len(board["connections"]) < model.MAX_CONNECTIONS
        ):
            board["connections"].append({
                "a": a, "b": b, "label": suggestion.get("label") or "", "by": model.JENNY,
            })
            model.add_since(board, "connection_added", a=a, b=b)
    elif kind == "attach_page":
        card = model.card_by_id(board, str(suggestion.get("card") or ""))
        page = str(suggestion.get("page") or "")
        if card is not None and page and page not in owners:
            card["pages"].append(page)
            model.add_since(board, "page_attached", card=card["id"], page=page)
        board["offboardWants"] = [w for w in board["offboardWants"] if w.get("page") != page]
    elif kind == "add_section":
        card = model.card_by_id(board, str(suggestion.get("card") or ""))
        app = suggestion.get("app")
        if card is not None and model.is_valid_id(app):
            sid = model.unique_id("s-app", [s["id"] for s in card["userSections"]])
            card["userSections"].append({
                "id": sid, "kind": "app", "app": app,
                "params": dict(suggestion.get("params") or {}), "created": stamp,
            })
            model.add_since(board, "section_added", card=card["id"], app=app)


async def board_suggestion_respond(
    ctx: CommandContext, params: Mapping[str, Any]
) -> dict[str, Any]:
    """Sì, no o più avanti a una proposta della busta.

    Un «no» si ricorda (per soggetto e tipo), e torna solo se il soggetto cresce
    di molto; «più avanti» la rimanda di qualche passata.
    """
    from jenny.board.store import read_reminders

    root = _project(ctx, params)
    sid = _card_id(params, "id")
    answer = params.get("answer")
    if answer not in ("yes", "no", "later"):
        raise CommandError("bad_request", "answer must be yes, no or later")
    reminders = await _run(read_reminders, root)
    removed: list[str] = []

    def apply(board: dict[str, Any]) -> None:
        suggestion = next((s for s in board["suggestions"] if s["id"] == sid), None)
        if suggestion is None:
            raise CommandError("not_found", "suggestion not found")
        if answer == "yes":
            before = {c["id"] for c in board["cards"]}
            _accept(board, suggestion, reminders, _near(params))
            removed.extend(before - {c["id"] for c in board["cards"]})
        elif answer == "no":
            board["declined"].append({
                "kind": suggestion["kind"], "subject": suggestion.get("subject", ""),
                "at": model.now_iso(), "pagesAtDecline": len(suggestion.get("pages") or []),
            })
        else:
            board["later"].append({
                "kind": suggestion["kind"], "subject": suggestion.get("subject", ""),
                "untilPass": board["passes"] + model.LATER_PASSES,
            })
        board["suggestions"] = [s for s in board["suggestions"] if s["id"] != sid]

    await _run(mutate_board, root, apply)
    for card_id in removed:
        await _run(delete_card_summary, root, card_id)
    return {"ok": True}


async def board_connection_remove(
    ctx: CommandContext, params: Mapping[str, Any]
) -> dict[str, Any]:
    """Togliere un filo: l'utente sempre, Jenny mai (propone)."""
    root = _project(ctx, params)
    pair = {_card_id(params, "a"), _card_id(params, "b")}

    def apply(board: dict[str, Any]) -> bool | None:
        kept = [c for c in board["connections"] if {c["a"], c["b"]} != pair]
        if len(kept) == len(board["connections"]):
            return False
        board["connections"] = kept
        # Togliere un filo e' un «no» a quel filo: la passata non lo ripropone.
        # Nei due versi, perche' la proposta lo scrive nell'ordine in cui la fa.
        titles = [model.card_by_id(board, cid) for cid in sorted(pair)]
        if all(titles):
            a, b = (t["title"] for t in titles if t)
            for subject in (f"{a} — {b}", f"{b} — {a}"):
                board["declined"].append({"kind": "add_connection", "subject": subject,
                                          "at": model.now_iso(), "pagesAtDecline": 0})
        return None

    await _run(mutate_board, root, apply)
    return {"ok": True}


async def board_offboard_want(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """«Voglio vederla in bacheca»: una proposta al contrario, per la passata dopo."""
    root = _project(ctx, params)
    # Il lettore ha in mano il bersaglio del link com'e' scritto
    # (``[[dormienza]]``, senza ``.md``): si risolve come lo risolve
    # ``/api/page``, e si registra la pagina vera.
    page = await _resolve_page(root, params.get("page"))
    origin = params.get("from")

    def apply(board: dict[str, Any]) -> bool | None:
        if any(w.get("page") == page for w in board["offboardWants"]):
            return False
        board["offboardWants"].append({
            "page": page, "from": origin if model.is_valid_id(origin) else "",
            "at": model.now_iso(),
        })
        return None

    await _run(mutate_board, root, apply)
    return {"ok": True}


async def board_preview(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """Conta un'anteprima di una pagina fuori bacheca.

    Le pagine aperte spesso in anteprima sono proprio quelle che la bacheca sta
    trascurando: il conto lo legge la passata della bacheca (e il lint), non
    l'interfaccia.
    """
    root = _project(ctx, params)
    page = await _resolve_page(root, params.get("page"))

    def apply(board: dict[str, Any]) -> None:
        previews = dict(board.get("previews") or {})
        previews[page] = int(previews.get(page) or 0) + 1
        board["previews"] = previews

    if (root / "board" / "board.json").exists():
        await _run(mutate_board, root, apply)
    return {"ok": True}


async def _resolve_page(root: Path, page: Any) -> str:
    """Il percorso vero di una pagina, anche da un bersaglio di link senza ``.md``."""
    from jenny.board.view import wiki_titles

    titles = await _run(wiki_titles, root)
    if isinstance(page, str) and page not in titles and ".." not in page:
        from jenny.webui.wiki import resolve_wikilink

        resolved = await _run(resolve_wikilink, root, page)
        if resolved is not None:
            page = resolved.relative_to(root / "wiki").as_posix()
    if not isinstance(page, str) or page not in titles:
        raise CommandError("not_found", "page not found")
    return page


# ── Le correzioni ────────────────────────────────────────────────────────────


async def board_fix(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """«Correggi» su una sezione: una segnalazione sulla pagina da cui viene."""
    from jenny.board.corrections import create_correction

    root = _project(ctx, params)
    card_id = _card_id(params)
    section_title = params.get("section")
    text = params.get("text")
    if not isinstance(section_title, str) or not section_title.strip():
        raise CommandError("bad_request", "section required")
    if not isinstance(text, str) or not text.strip():
        raise CommandError("bad_request", "text required")
    if len(text.encode("utf-8")) > 8000:
        raise CommandError("too_large", "correction too long")
    board = await _run(read_board, root)
    card = model.card_by_id(board or {}, card_id)
    summary = await _run(read_card_summary, root, card_id)
    if card is None or summary is None:
        raise CommandError("not_found", "card not found")
    section = next((s for s in summary["sections"] if s["title"] == section_title), None)
    if section is None:
        raise CommandError("not_found", "section not found")
    try:
        result = await _run(
            create_correction, root, card_id=card_id, card_title=card["title"],
            section=section_title, sources=section["sources"], old_text=section["text"],
            correction=text,
        )
    except FileNotFoundError as exc:
        raise CommandError("not_found", str(exc)) from exc
    return {"ok": True, **result}


# ── Le sezioni dell'utente ───────────────────────────────────────────────────


def _parse_when(raw: Any) -> dict[str, Any]:
    when = model._normalize_when(raw)
    if when is None:
        raise CommandError("bad_request", "invalid when")
    return when


async def board_section_add(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """Aggiungi una sezione a un cassetto: nota, promemoria, mini app o foto."""
    from jenny.board.sections import PHOTOS_DIR, journal_note

    root = _project(ctx, params)
    card_id = _card_id(params)
    kind = params.get("kind")
    if kind not in model.SECTION_KINDS:
        raise CommandError("bad_request", "invalid kind")
    if kind in ("micro", "cron"):
        # Una micro app e un cron nascono da Jenny, su richiesta, con la loro
        # prova o il loro job: dal cassetto si chiedono a parole.
        raise CommandError("bad_request", f"a {kind} section is made by Jenny, not added")
    board = await _run(read_board, root)
    if board is None or model.card_by_id(board, card_id) is None:
        raise CommandError("not_found", "card not found")
    stamp = model.now_iso()
    section: dict[str, Any] = {"kind": kind, "created": stamp}

    if kind == "note":
        text = model.clean_block(params.get("text"), model.MAX_NOTE)
        if not text:
            raise CommandError("bad_request", "text required")
        section["text"] = text
        # La riga [said] prima della sezione: se il diario non si scrive, la nota
        # resta comunque, ma senza la promessa di arrivare nella wiki.
        try:
            section["journalLine"] = await _run(journal_note, root, text,
                                                timezone_name=_timezone())
        except OSError as exc:
            logger.warning("board: note not journaled in {}: {}", root.name, exc)
    elif kind == "reminder":
        text = model.clean_text(params.get("text"), model.MAX_TITLE * 2)
        if not text:
            raise CommandError("bad_request", "text required")
        when = _parse_when(params.get("when"))
        rid_holder: list[str] = []

        def add_rem(rems: dict[str, Any]) -> None:
            rid = model.unique_id(f"r-{model.slugify(text, fallback='reminder')}"[:40],
                                  [r["id"] for r in rems["reminders"]])
            rid_holder.append(rid)
            rems["reminders"].append({
                "id": rid, "card": card_id, "text": text, "when": when, "by": model.USER,
                "state": "open", "created": stamp, "source": "board",
            })

        await _run(mutate_reminders, root, add_rem)
        section["reminder"] = rid_holder[0]
    elif kind == "app":
        app = params.get("app")
        if not model.is_valid_id(app):
            raise CommandError("bad_request", "invalid app")
        section["app"] = app
        raw_params = params.get("params") or {}
        if not isinstance(raw_params, Mapping):
            raise CommandError("bad_request", "params must be an object")
        section["params"] = {str(k): v for k, v in raw_params.items()
                             if isinstance(v, (str, int, float, bool))}
    elif kind == "photo":
        mime = params.get("mime")
        data = params.get("data")
        if mime not in _PHOTO_EXT or not isinstance(data, str):
            raise CommandError("bad_request", "photo must be jpeg, png or webp")
        try:
            raw = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise CommandError("bad_request", "invalid photo data") from exc
        if len(raw) > MAX_PHOTO_BYTES:
            raise CommandError("too_large", "photo too large")
        from jenny.utils.path import atomic_write

        name = f"{card_id}-{model.now_iso()[:19].replace(':', '').replace('-', '')}"
        rel = f"{PHOTOS_DIR}/{name}{_PHOTO_EXT[mime]}"
        await _run(atomic_write, root / rel, raw)
        section["path"] = rel
        section["caption"] = model.clean_text(params.get("caption"), model.MAX_TEXT)

    def apply(board: dict[str, Any]) -> None:
        card = _need_card(board, card_id)
        prefix = {"note": "s-note", "reminder": "s-rem", "app": "s-app", "photo": "s-photo"}[kind]
        section["id"] = model.unique_id(prefix, [s["id"] for s in card["userSections"]])
        card["userSections"].append(section)

    await _run(mutate_board, root, apply)
    return {"ok": True, "id": section["id"]}


async def board_section_update(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """Modifica una nota o un promemoria dell'utente.

    Una nota modificata è una riga di diario **nuova**: il diario è append-only,
    e la riga di prima resta la verità di quando è stata detta.
    """
    from jenny.board.sections import journal_note

    root = _project(ctx, params)
    card_id = _card_id(params)
    sid = _card_id(params, "id")
    board = await _run(read_board, root)
    card = model.card_by_id(board or {}, card_id)
    if card is None:
        raise CommandError("not_found", "card not found")
    section = next((s for s in card["userSections"] if s["id"] == sid), None)
    if section is None:
        raise CommandError("not_found", "section not found")
    if section["kind"] == "note":
        text = model.clean_block(params.get("text"), model.MAX_NOTE)
        if not text:
            raise CommandError("bad_request", "text required")
        try:
            ref = await _run(journal_note, root, text, timezone_name=_timezone())
        except OSError:
            ref = None

        def apply(board: dict[str, Any]) -> None:
            for s in _need_card(board, card_id)["userSections"]:
                if s["id"] == sid:
                    s["text"] = text
                    if ref:
                        s["journalLine"] = ref

        await _run(mutate_board, root, apply)
    elif section["kind"] == "reminder":
        text = model.clean_text(params.get("text"), model.MAX_TITLE * 2)
        when = _parse_when(params.get("when")) if params.get("when") is not None else None

        def apply_rem(rems: dict[str, Any]) -> None:
            for rem in rems["reminders"]:
                if rem["id"] == section["reminder"]:
                    if text:
                        rem["text"] = text
                    if when:
                        rem["when"] = when
                        rem.pop("notifiedAt", None)
                    rem["by"] = model.USER
                    return
            raise CommandError("not_found", "reminder not found")

        await _run(mutate_reminders, root, apply_rem)
    else:
        raise CommandError("bad_request", "only notes and reminders can be edited")
    return {"ok": True}


async def board_section_remove(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """Togli una sezione. La wiki non cambia: togliere è una scelta di forma."""
    root = _project(ctx, params)
    card_id = _card_id(params)
    sid = _card_id(params, "id")
    gone: list[dict[str, Any]] = []

    def apply(board: dict[str, Any]) -> bool | None:
        card = _need_card(board, card_id)
        keep = [s for s in card["userSections"] if s["id"] != sid]
        gone.extend(s for s in card["userSections"] if s["id"] == sid)
        if len(keep) == len(card["userSections"]):
            return False
        card["userSections"] = keep
        return None

    await _run(mutate_board, root, apply)
    for section in gone:
        if section.get("kind") == "micro":
            from jenny.board import micro as mc

            await _run(mc.remove, ctx.get_workspace_root(), root, str(section.get("micro")))
        if section.get("kind") == "cron":
            # Togliere il cron dal cassetto toglie il job: nessun cron orfano.
            cron = ctx.get_cron_service() if ctx.get_cron_service is not None else None
            if cron is not None:
                cron.remove_job(str(section.get("job")))
        if section.get("kind") == "reminder":
            def drop(rems: dict[str, Any], rid: str = section["reminder"]) -> None:
                for rem in rems["reminders"]:
                    if rem["id"] == rid and rem["state"] == "open":
                        rem["state"] = "dropped"
                        rem["closedAt"] = model.now_iso()

            await _run(mutate_reminders, root, drop)
    return {"ok": True}


async def board_section_reorder(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """L'ordine delle sezioni aggiunte è dell'utente."""
    root = _project(ctx, params)
    card_id = _card_id(params)
    order = params.get("order")
    if not isinstance(order, list) or not all(isinstance(x, str) for x in order):
        raise CommandError("bad_request", "order must be a list of ids")

    def apply(board: dict[str, Any]) -> None:
        card = _need_card(board, card_id)
        rank = {sid: i for i, sid in enumerate(order)}
        card["userSections"].sort(key=lambda s: rank.get(s["id"], len(rank)))

    await _run(mutate_board, root, apply)
    return {"ok": True}


async def board_reminder_done(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """Un'attesa fatta: non si cancella, diventa un fatto (una riga di diario) e lascia la bacheca."""
    from jenny.board.sections import journal_note

    root = _project(ctx, params)
    rid = _card_id(params, "id")
    done: list[dict[str, Any]] = []

    def apply(rems: dict[str, Any]) -> None:
        for rem in rems["reminders"]:
            if rem["id"] == rid and rem["state"] == "open":
                rem["state"] = "done"
                rem["closedAt"] = model.now_iso()
                rem["by"] = model.USER
                done.append(rem)
                return
        raise CommandError("not_found", "reminder not found")

    await _run(mutate_reminders, root, apply)
    try:
        await _run(journal_note, root, f"Done: {done[0]['text']}", timezone_name=_timezone())
    except OSError as exc:
        logger.warning("board: done reminder not journaled in {}: {}", root.name, exc)
    return {"ok": True}


async def board_setup(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """La nascita guidata: lo scheletro dei cassetti, e cosa ricordare.

    Le due risposte che seguono «di cosa parla questo quaderno» quando lo crei.
    Lo scheletro scelto dall'utente e' suo: la prima passata della bacheca lo
    usa invece di proporne uno. Una risposta vuota lascia decidere a Jenny.
    """
    root = _project(ctx, params)
    raw = params.get("skeleton") or []
    if isinstance(raw, str):
        raw = [part for part in raw.replace(";", ",").split(",")]
    if not isinstance(raw, list):
        raise CommandError("bad_request", "skeleton must be a list")
    skeleton = [s for s in (model.clean_text(x, model.MAX_TITLE) for x in raw) if s]
    skeleton = list(dict.fromkeys(skeleton))[:model.MAX_SKELETON]
    remind = model.clean_text(params.get("remind"), model.MAX_TEXT)

    def apply(board: dict[str, Any]) -> bool | None:
        if not skeleton and not remind:
            return False
        if skeleton and not board["cards"]:
            board["skeleton"] = skeleton
        if remind:
            board["remindHint"] = remind
        return None

    await _run(mutate_board, root, apply)
    return {"ok": True, "skeleton": skeleton, "remind": remind}


def _micro_id(params: Mapping[str, Any]) -> str:
    return _card_id(params, "micro")


async def board_micro_keep(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """[Tienilo]: la micro app in prova diventa viva, e il suo codice approvato.

    E' **questo tocco** ad approvare il codice: l'impronta finisce fuori dal
    workspace (``micro.approvals_path``), e da li' in poi l'orologio lo esegue.
    Vale anche come [Riapprova] per un codice cambiato fuori da Jenny.
    """
    from jenny.board import micro as mc

    root = _project(ctx, params)
    mid = _micro_id(params)
    kept = await _run(mc.keep, ctx.get_workspace_root(), root, mid)
    if kept is None:
        raise CommandError("not_found", "micro app not found")

    def since(board: dict[str, Any]) -> None:
        found = mc.section_of(board, mid)
        card = found[0] if found else {"id": "", "title": ""}
        model.add_since(board, "micro_kept", card=card["id"], title=card["title"],
                        app=kept.spec["title"])

    await _run(mutate_board, root, since)
    # La prima lettura vera da viva, subito: il cassetto non resta su quella di prova.
    await mc.run_micro(ctx.get_workspace_root(), root, mid, force=True)
    return {"ok": True}


async def board_micro_discard(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """[Scarta]: via la bozza; se non c'era una versione viva, via anche la sezione."""
    from jenny.board import micro as mc

    root = _project(ctx, params)
    mid = _micro_id(params)
    await _run(mc.discard_draft, root, mid)
    if mc.load_micro(root, mid) is None:
        await _run(mc.drop_section, root, mid)
    else:
        board = await _run(read_board, root)
        found = mc.section_of(board, mid) if board else None
        if found is not None:
            await _run(mc.set_section, root, found[0]["id"], mid, pending=False)
    return {"ok": True}


async def board_micro_run(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """⟳: rileggi adesso."""
    from jenny.board import micro as mc

    root = _project(ctx, params)
    state = await mc.run_micro(ctx.get_workspace_root(), root, _micro_id(params), force=True)
    if state is None:
        raise CommandError("conflict", "micro app not approved or missing")
    return {"ok": True, "status": state.get("status")}


async def board_micro_pause(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """Pausa e ripresa: ``paused: true|false``."""
    from jenny.board import micro as mc

    root = _project(ctx, params)
    micro = mc.load_micro(root, _micro_id(params))
    if micro is None:
        raise CommandError("not_found", "micro app not found")
    state = mc.read_state(micro.dir)
    if params.get("paused") is True:
        state["status"] = "paused"
    elif state.get("status") == "paused":
        state["status"] = "ok"
        state.pop("lastRunAt", None)
    mc.write_state(micro.dir, state)
    return {"ok": True}


async def board_cron_pause(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """Pausa e ripresa di un cron del quaderno, dal suo cassetto: ``paused: true|false``.

    Solo un job legato a questo quaderno: il cassetto non mette in pausa la
    sveglia della chat personale.
    """
    root = _project(ctx, params)
    job_id = _card_id(params, "job")
    cron = ctx.get_cron_service() if ctx.get_cron_service is not None else None
    if cron is None:
        raise CommandError("unavailable", "scheduler not running")
    job = cron.get_job(job_id)
    if job is None or (job.payload.board or {}).get("notebook") != root.name:
        raise CommandError("not_found", "job not found in this notebook")
    result = cron.set_paused(job_id, params.get("paused") is True)
    return {"ok": True, "result": result}


BOARD_COMMANDS = {
    "board.cron.pause": board_cron_pause,
    "board.micro.keep": board_micro_keep,
    "board.micro.discard": board_micro_discard,
    "board.micro.run": board_micro_run,
    "board.micro.pause": board_micro_pause,
    "board.setup": board_setup,
    "board.seen": board_seen,
    "board.move": board_move,
    "board.suggestion.respond": board_suggestion_respond,
    "board.connection.remove": board_connection_remove,
    "board.offboard.want": board_offboard_want,
    "board.preview": board_preview,
    "board.fix": board_fix,
    "board.section.add": board_section_add,
    "board.section.update": board_section_update,
    "board.section.remove": board_section_remove,
    "board.section.reorder": board_section_reorder,
    "board.reminder.done": board_reminder_done,
}
