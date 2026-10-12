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

from jenny.board import edit, model
from jenny.board.store import (
    mutate_board,
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
    """Su disco, quindi fuori dal loop; un ``CommandError`` torna com'è, un
    rifiuto di :mod:`jenny.board.edit` diventa il ``CommandError`` col suo codice."""
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except edit.BoardEditError as exc:
        raise CommandError(exc.code, exc.message) from exc


async def _edit(pending: Any) -> Any:
    """Una scrittura async di :mod:`jenny.board.edit` (quelle che toccano i cron):
    il suo rifiuto diventa il ``CommandError`` col suo codice, come in :func:`_run`."""
    try:
        return await pending
    except edit.BoardEditError as exc:
        raise CommandError(exc.code, exc.message) from exc


def _cron(ctx: CommandContext) -> Any:
    return ctx.get_cron_service() if ctx.get_cron_service is not None else None


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
    pos = {"x": _number(params, "x"), "y": _number(params, "y"), "by": model.USER}

    if kind == "card":
        item = _card_id(params, "id")

        def apply(board: dict[str, Any]) -> None:
            _need_card(board, item)["pos"] = pos

        await _run(mutate_board, root, apply)
    elif kind == "postit":
        # Il post-it delle attese di una scheda: l'id e' quello della scheda.
        card_id = _card_id(params, "id")

        def apply_postit(board: dict[str, Any]) -> None:
            _need_card(board, card_id)["postitPos"] = pos

        await _run(mutate_board, root, apply_postit)
    else:
        raise CommandError("bad_request", "kind must be card or postit")
    return {"ok": True}


# ── Le schede dell'utente: crea, rinomina, togli, rimetti ────────────────────
#
# Fino al 06/10/2026 la bacheca cambiava forma solo dalla busta. Adesso è anche
# dell'utente nel senso pieno: una scheda si crea dal «+» o tenendo premuto il
# muro vuoto, si rinomina, si toglie con un Annulla. Jenny continua a non poter
# fare niente di tutto questo da sola (``ownership_violations``): queste
# scritture sono ``user``, e la loro logica sta in ``jenny/board/edit.py``,
# che le condivide con l'editore dei quaderni.


async def board_card_add(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """Una scheda nuova, dove l'utente l'ha chiesta (``near``) o al centro della vista.

    Nasce vuota: niente pagine (le attacca la passata del giardiniere, una
    proposta della busta o l'editore), il titolo e il posto sono dell'utente.
    """
    root = _project(ctx, params)
    app = params.get("app")
    if isinstance(app, Mapping):
        # Una scheda che **e'** un'app: la polaroid porta il suo valore.
        return await _card_from_app(ctx, root, app, params)
    glyph = params.get("glyph") if isinstance(params.get("glyph"), str) else ""
    made, _redraw = await _run(edit.add_card, root, params.get("title"), glyph=glyph,
                               near=_near(params))
    return {"ok": True, "card": {"id": made["id"], "title": made["title"], "pos": made["pos"]}}


def _app_params(workspace: Path, slug: Any, raw: Any) -> tuple[str, dict[str, Any]]:
    """Lo slug di un'app che esiste e i parametri che il suo riassunto
    dichiara: nient'altro passa."""
    from jenny.apps.manifest import SLUG_RE, find_app

    if not isinstance(slug, str) or not SLUG_RE.match(slug):
        raise CommandError("bad_request", "invalid app")
    app = find_app(workspace, slug)
    if app is None or app.manifest is None:
        raise CommandError("not_found", "app not found")
    declared = app.manifest.section.params if app.manifest.section is not None else {}
    given = raw if isinstance(raw, Mapping) else {}
    unknown = sorted(set(given) - set(declared))
    if unknown:
        raise CommandError("bad_request", f"unknown params: {', '.join(unknown)}")
    return slug, {k: v for k, v in given.items() if isinstance(v, (str, int, float, bool))}


async def _card_from_app(ctx: CommandContext, root: Path, app: Mapping[str, Any],
                         params: Mapping[str, Any]) -> dict[str, Any]:
    from jenny.board.place import place_app

    workspace = ctx.get_workspace_root()
    slug, app_params = _app_params(workspace, app.get("slug"), app.get("params"))
    title = model.clean_text(params.get("title"), model.MAX_TITLE) or None
    try:
        where = await _run(place_app, root, slug, params=app_params, near=_near(params),
                           title=title, workspace=workspace, link=params.get("link") or None)
    except ValueError as exc:
        raise CommandError("conflict", str(exc)) from exc
    board = await _run(read_board, root)
    card = next((c for c in reversed((board or {}).get("cards") or [])
                 if (c.get("app") or {}).get("slug") == slug), None)
    return {"ok": True, "where": where,
            "card": {"id": card["id"], "title": card["title"], "pos": card["pos"]} if card
            else None}


async def board_card_rename(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """Il nome di una scheda diventa dell'utente: Jenny non lo riscrive più."""
    root = _project(ctx, params)
    await _run(edit.rename_card, root, _card_id(params, "id"), params.get("title"))
    return {"ok": True}


async def board_card_remove(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """Togli una scheda, con un Annulla: v. :func:`jenny.board.edit.remove_card`."""
    root = _project(ctx, params)
    entry = await _edit(edit.remove_card(root, _card_id(params, "id"), cron=_cron(ctx)))
    return {"ok": True, "title": entry["card"]["title"]}


async def board_card_restore(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """L'Annulla di «Togli»: v. :func:`jenny.board.edit.restore_card`."""
    root = _project(ctx, params)
    await _edit(edit.restore_card(root, _card_id(params, "id"), cron=_cron(ctx)))
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


async def board_suggestion_respond(
    ctx: CommandContext, params: Mapping[str, Any]
) -> dict[str, Any]:
    """Sì, no o più avanti a una proposta della busta: v.
    :func:`jenny.board.edit.respond_suggestion`."""
    root = _project(ctx, params)
    sid = _card_id(params, "id")
    await _edit(edit.respond_suggestion(root, sid, str(params.get("answer")), near=_near(params),
                                        workspace=ctx.get_workspace_root(), cron=_cron(ctx)))
    return {"ok": True}


async def board_connection_remove(
    ctx: CommandContext, params: Mapping[str, Any]
) -> dict[str, Any]:
    """Togliere un filo: l'utente sempre, Jenny mai (propone)."""
    root = _project(ctx, params)
    await _run(edit.disconnect, root, _card_id(params, "a"), _card_id(params, "b"))
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


async def board_section_add(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """Aggiungi una sezione a un cassetto: nota, promemoria o foto."""
    root = _project(ctx, params)
    card_id = _card_id(params)
    kind = params.get("kind")
    if kind not in model.SECTION_KINDS:
        raise CommandError("bad_request", "invalid kind")
    if kind in ("micro", "cron"):
        # Un cron nasce da Jenny, su richiesta, col suo job: dal cassetto si
        # chiede a parole. Una micro app non nasce piu'.
        raise CommandError("bad_request", f"a {kind} section is made by Jenny, not added")
    if kind == "app":
        # Un'app non entra nel cassetto di un'altra scheda: e' una polaroid
        # sua, legata a questa da un filo. Le sezioni ``app`` che ci sono gia'
        # (le micro app migrate) restano e si disegnano.
        raise CommandError("bad_request",
                           "an app is a card of its own: use board.card.add with app and link")
    if kind == "note":
        sid = await _run(edit.add_note, root, card_id, params.get("text"),
                         timezone_name=_timezone())
    elif kind == "reminder":
        sid = await _run(edit.add_reminder, root, card_id, params.get("text"), params.get("when"))
    else:
        sid = await _add_photo(root, card_id, params)
    return {"ok": True, "id": sid}


async def _add_photo(root: Path, card_id: str, params: Mapping[str, Any]) -> str:
    """Una foto nel cassetto: solo dall'interfaccia, che ha i byte."""
    from jenny.board.sections import PHOTOS_DIR
    from jenny.utils.path import atomic_write

    await _run(edit.card_id_of, root, card_id)
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
    name = f"{card_id}-{model.now_iso()[:19].replace(':', '').replace('-', '')}"
    rel = f"{PHOTOS_DIR}/{name}{_PHOTO_EXT[mime]}"
    await _run(atomic_write, root / rel, raw)
    section = {"kind": "photo", "created": model.now_iso(), "path": rel,
               "caption": model.clean_text(params.get("caption"), model.MAX_TEXT)}

    def apply(board: dict[str, Any]) -> None:
        card = edit.need_card(board, card_id)
        section["id"] = model.unique_id("s-photo", [s["id"] for s in card["userSections"]])
        card["userSections"].append(section)

    await _run(mutate_board, root, apply)
    return str(section["id"])


async def board_section_update(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """Modifica una nota o un promemoria dell'utente."""
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
        await _run(edit.update_note, root, card_id, sid, params.get("text"),
                   timezone_name=_timezone())
    elif section["kind"] == "reminder":
        await _run(edit.update_reminder, root, card_id, sid, params.get("text"),
                   params.get("when"))
    else:
        raise CommandError("bad_request", "only notes and reminders can be edited")
    return {"ok": True}


async def board_section_remove(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """Togli una sezione. La wiki non cambia: togliere è una scelta di forma."""
    root = _project(ctx, params)
    await _edit(edit.remove_section(root, _card_id(params), _card_id(params, "id"),
                                    cron=_cron(ctx)))
    return {"ok": True}


async def board_section_reorder(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """L'ordine delle sezioni aggiunte è dell'utente."""
    root = _project(ctx, params)
    card_id = _card_id(params)
    order = params.get("order")
    if not isinstance(order, list) or not all(isinstance(x, str) for x in order):
        raise CommandError("bad_request", "order must be a list of ids")

    def apply(board: dict[str, Any]) -> None:
        card = edit.need_card(board, card_id)
        rank = {sid: i for i, sid in enumerate(order)}
        card["userSections"].sort(key=lambda s: rank.get(s["id"], len(rank)))

    await _run(mutate_board, root, apply)
    return {"ok": True}


async def board_reminder_done(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """Un'attesa fatta: v. :func:`jenny.board.edit.close_reminder`."""
    root = _project(ctx, params)
    await _run(edit.close_reminder, root, _card_id(params, "id"), timezone_name=_timezone())
    return {"ok": True}


async def board_reminder_update(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """Correggi un'attesa dal calendario di cura, per id: anche le attese di Jenny
    non hanno una sezione da cui passare."""
    root = _project(ctx, params)
    await _run(edit.update_reminder_by_id, root, _card_id(params, "id"), params.get("text"),
               params.get("when"))
    return {"ok": True}


async def board_reminder_drop(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """Togli un'attesa dal calendario di cura: v. :func:`jenny.board.edit.drop_reminder`."""
    root = _project(ctx, params)
    await _run(edit.drop_reminder, root, _card_id(params, "id"))
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
    "board.card.add": board_card_add,
    "board.card.rename": board_card_rename,
    "board.card.remove": board_card_remove,
    "board.card.restore": board_card_restore,
    "board.cron.pause": board_cron_pause,
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
    "board.reminder.update": board_reminder_update,
    "board.reminder.drop": board_reminder_drop,
}
