"""Le scritture dell'utente sulla bacheca, chiunque le faccia per lui.

Le fanno due strade: l'interfaccia (i comandi ``board.*`` di
``jenny/webui/board_commands.py``) e l'editore dei quaderni, il subagent che
Jenny lancia quando l'utente le chiede di cambiare un quaderno (il tool
``board``, ``jenny/agent/tools/board.py``). Fino al 09/10/2026 la logica stava
dentro i comandi RPC, e l'editore non avrebbe avuto altro modo di riusarla che
copiarla: qui c'è una volta sola.

Tutto scrive come ``user``: è l'utente che decide, anche quando lo chiede a
Jenny. Per questo il giardiniere, che scrive come ``jenny``, non può disfarlo
(:func:`jenny.board.model.ownership_violations`).

Quasi tutte sono funzioni sincrone (I/O su disco, da chiamare fuori dal loop).
Fanno eccezione quelle che fermano, riprendono o tolgono quel che una scheda fa
da sé — :func:`remove_card`, :func:`restore_card`, :func:`remove_section`,
:func:`respond_suggestion` — e quelle dei promemoria, che sono job dello
scheduler (:func:`add_reminder` e le sue sorelle): sono ``async``: il servizio cron riarma il suo
timer sul loop, e chiamato da un thread solleva «no running event loop». Il
09/10/2026, sull'emulatore, una «Togli» col dito vero ha messo in pausa il cron e
poi è fallita: la scheda era nel cestino, l'interfaccia diceva di riprovare.
Queste fanno il lavoro sui file in un thread e chiamano il cron sul loop.

Un errore che l'utente deve vedere è un :class:`BoardEditError`, che i comandi
traducono in ``CommandError`` e il tool in una frase.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.board import model, removed
from jenny.board.place import app_size, app_title, find_card, tie
from jenny.board.store import (
    board_dir,
    delete_card_summary,
    mutate_board,
    read_board,
    read_card_summary,
)


class BoardEditError(Exception):
    """Una scrittura rifiutata: *code* è quello dei comandi RPC, *message* la ragione."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _notes(root: Path) -> list[dict[str, Any]]:
    """Le righe dei promemoria del quaderno, dal file dei job: occupano un post-it."""
    from jenny.board.cron_link import notes_on_disk

    return notes_on_disk(root.name)


def need_card(board: Mapping[str, Any], ref: str) -> dict[str, Any]:
    """Una scheda per id o per titolo, o un ``not_found`` che elenca quelle che ci sono."""
    card = find_card(dict(board), ref)
    if card is None:
        titles = ", ".join(f"«{c['title']}» ({c['id']})" for c in board.get("cards") or [])
        raise BoardEditError("not_found", f"card not found: {ref}. On the board: {titles or 'none'}")
    return card


def _title(value: Any) -> str:
    title = model.clean_text(value, model.MAX_TITLE)
    if not title:
        raise BoardEditError("bad_request", "title required")
    return title


def _wiki_pages(root: Path) -> set[str]:
    from jenny.board.view import wiki_titles

    return set(wiki_titles(root))


def _check_pages(root: Path, pages: Iterable[str]) -> list[str]:
    """Le pagine chieste, in ordine e senza doppioni, se esistono tutte in ``wiki/``."""
    wanted = list(dict.fromkeys(p for p in pages if isinstance(p, str) and p))
    known = _wiki_pages(root)
    missing = [p for p in wanted if p not in known]
    if missing:
        raise BoardEditError(
            "not_found",
            f"no such page under wiki/: {', '.join(missing)} — pages are named by their path "
            "relative to wiki/ (entities/Acero.md)",
        )
    return wanted


def _take_pages(board: dict[str, Any], pages: list[str], to_card: str) -> list[str]:
    """Toglie *pages* alle schede che le avevano; restituisce gli id di quelle schede."""
    moved = set(pages)
    sources = []
    for card in board["cards"]:
        if card["id"] == to_card:
            continue
        keep = [p for p in card["pages"] if p not in moved]
        if len(keep) != len(card["pages"]):
            card["pages"] = keep
            sources.append(card["id"])
    return sources


def _drop_empty_drawers(root: Path, board: Mapping[str, Any], card_ids: Iterable[str]) -> None:
    """Un cassetto rimasto senza pagine non racconta più niente di vero: si cancella."""
    for cid in card_ids:
        card = model.card_by_id(board, cid)
        if card is not None and not card["pages"]:
            delete_card_summary(root, cid)


# ── Le schede ────────────────────────────────────────────────────────────────


def beside(card: Mapping[str, Any]) -> tuple[float, float]:
    """Il posto da cercare per una scheda «accanto a» *card*: alla sua destra.

    Con il punto di *card* stesso, a parità di distanza vinceva la casella più a
    sinistra, e una fila di schede chieste una accanto all'altra scivolava fuori
    dal primo schermo (09/10/2026, sull'emulatore: x = 2, −18, −38).
    """
    width = model.card_box(card)[0]
    return (float(card["pos"]["x"]) + width + 2, float(card["pos"]["y"]))



def add_card(
    root: Path,
    title: Any,
    *,
    glyph: Any = "",
    pages: Iterable[str] = (),
    near: tuple[float, float] | None = None,
    near_card: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Una scheda nuova dell'utente, con le sue pagine se ne ha.

    Le pagine che stavano su un'altra scheda **passano** a questa. *near* è un
    punto del muro (il centro della vista, dall'interfaccia); *near_card* una
    scheda accanto a cui metterla. Restituisce la scheda e gli id dei cassetti
    da riscrivere: il suo, se ha pagine, e quelli delle schede che le hanno
    perse.
    """
    title = _title(title)
    glyph = glyph if isinstance(glyph, str) else ""
    wanted = _check_pages(root, pages)
    notes = _notes(root)
    made: dict[str, Any] = {}
    sources: list[str] = []

    def apply(board: dict[str, Any]) -> None:
        if model.is_full(board, notes):
            raise BoardEditError("conflict", "the board is full")
        target = near
        if near_card:
            target = beside(need_card(board, near_card))
        elif near is not None:
            target = (near[0] - model.CARD_BOX[0] / 2, near[1] - model.CARD_BOX[1] / 2)
        taken = {c["id"] for c in board["cards"]} | model.removed_card_ids(board)
        card_id = model.unique_id(model.slugify(title), taken)
        x, y = model.free_slot(board, notes, near=target)
        sources.extend(_take_pages(board, wanted, card_id))
        card = model.normalize_board({"cards": [{
            "id": card_id, "title": title, "titleBy": model.USER, "pages": wanted,
            "pos": {"x": x, "y": y, "by": model.USER}, "glyph": glyph,
            "userSections": [], "created": model.now_iso(),
        }]})["cards"][0]
        board["cards"].append(card)
        made.update(card)

    board = mutate_board(root, apply)
    _drop_empty_drawers(root, board, sources)
    redraw = ([made["id"]] if made["pages"] else []) + sources
    return made, redraw


def rename_card(root: Path, ref: str, title: Any) -> None:
    """Il nome diventa dell'utente: Jenny non lo riscrive più."""
    title = _title(title)

    def apply(board: dict[str, Any]) -> None:
        card = need_card(board, ref)
        card["title"] = title
        card["titleBy"] = model.USER

    mutate_board(root, apply)


def move_card(root: Path, ref: str, near_card: str) -> None:
    """Una scheda accanto a un'altra. Le coordinate esatte restano dell'interfaccia."""
    notes = _notes(root)

    def apply(board: dict[str, Any]) -> None:
        card = need_card(board, ref)
        other = need_card(board, near_card)
        if other["id"] == card["id"]:
            raise BoardEditError("bad_request", "a card cannot go next to itself")
        rest = {**board, "cards": [c for c in board["cards"] if c["id"] != card["id"]]}
        x, y = model.free_slot(rest, notes, box=model.card_box(card), near=beside(other))
        card["pos"] = {"x": x, "y": y, "by": model.USER}

    mutate_board(root, apply)


def move_pages(root: Path, pages: Iterable[str], to_card: str) -> list[str]:
    """Sposta pagine su una scheda, da un'altra o da fuori bacheca.

    Restituisce i cassetti da riscrivere: la scheda d'arrivo e quelle di partenza
    che hanno ancora pagine (un cassetto rimasto vuoto si cancella).
    """
    wanted = _check_pages(root, pages)
    if not wanted:
        raise BoardEditError("bad_request", "pages required")
    target: dict[str, Any] = {}
    sources: list[str] = []

    def apply(board: dict[str, Any]) -> None:
        card = need_card(board, to_card)
        target.update(card)
        sources.extend(_take_pages(board, wanted, card["id"]))
        card["pages"] = [*card["pages"], *(p for p in wanted if p not in card["pages"])]

    board = mutate_board(root, apply)
    _drop_empty_drawers(root, board, sources)
    still = [cid for cid in sources if (model.card_by_id(board, cid) or {}).get("pages")]
    return [target["id"], *still]


def remove_pages(root: Path, pages: Iterable[str], from_card: str | None = None
                 ) -> tuple[dict[str, list[str]], list[str]]:
    """Toglie pagine dalle schede — da *from_card*, o da tutte se manca — anche
    quando il file non c'è più. Restituisce ``(tolte, cassetti da riscrivere)``:
    ``tolte`` è ``{id scheda: [pagine]}``, vuoto se nessuna scheda le aveva.

    **Niente controllo d'esistenza, al contrario di** :func:`move_pages`: il
    caso per cui esiste è proprio il riferimento a una pagina cancellata. In una
    prova dell'ottobre 2026 un riordino ha cancellato e rifatto le pagine di un
    quaderno e ha lasciato 40 riferimenti morti in ``board.json``: l'editore non
    aveva un modo per toglierli.

    **Perché non si potano da soli alla scrittura della bacheca**, anche se un
    riferimento a un file che non c'è è quasi sempre sbagliato:

    - un riferimento morto non si vede (il cassetto mostra solo le pagine vere),
      mentre una potatura non si disfa: se la pagina torna — un ritorno a prima
      di un piano che rimette le pagine e tiene la bacheca, un file rinominato e
      poi rimesso — la scheda la riprende solo se il riferimento c'è ancora;
    - nell'imbuto di :mod:`jenny.board.store` toglierebbe pagine anche nelle
      scritture del giardiniere, e :func:`model.ownership_violations` rifiuta
      ogni sua scrittura che tolga pagine a una scheda: una bacheca con un
      riferimento morto non accetterebbe più niente da lui;
    - e vorrebbe una lettura di ``wiki/`` dentro il lock, dove l'imbuto non fa
      I/O lento.

    Quindi la bacheca li tiene finché qualcuno decide: ``show`` del tool li
    elenca, e l'editore li toglie con questa funzione.
    """
    wanted = list(dict.fromkeys(p for p in pages if isinstance(p, str) and p))
    if not wanted:
        raise BoardEditError("bad_request", "pages required")
    # Fuori dal lock: le pagine che esistono, per gli orfani e per i cassetti.
    known = _wiki_pages(root)
    taken: dict[str, list[str]] = {}

    def apply(board: dict[str, Any]) -> bool | None:
        cards = [need_card(board, from_card)] if from_card else board["cards"]
        for card in cards:
            gone = [p for p in card["pages"] if p in wanted]
            if gone:
                card["pages"] = [p for p in card["pages"] if p not in wanted]
                taken[card["id"]] = gone
        if not taken:
            return False
        # Una pagina vera tolta da ogni scheda diventa orfana, e il giardiniere
        # assegna gli orfani nuovi alla scheda a cui «appartengono chiaramente»:
        # cioè quella da cui l'utente l'ha appena tolta. Segnarla come già vista
        # è quel che fa lui stesso con gli orfani che ha giudicato.
        owners = model.page_owners(board)
        dropped = {p for gone in taken.values() for p in gone
                   if p in known and p not in owners}
        if dropped:
            board["orphansSeen"] = sorted({*board["orphansSeen"], *dropped})
        return None

    board = mutate_board(root, apply)
    _drop_empty_drawers(root, board, taken)
    return taken, _stale_drawers(root, board, taken, known)


def _stale_drawers(root: Path, board: Mapping[str, Any], card_ids: Iterable[str],
                   known: set[str]) -> list[str]:
    """Fra *card_ids*, i cassetti che non dicono più le pagine della loro scheda.

    Si misura con l'impronta salvata, la stessa che usa il giardiniere
    (``BoardPass.stale_cards``): un riferimento morto tolto non la cambia, una
    pagina vera tolta sì, e un cassetto mai scritto va scritto.
    """
    from jenny.board.drawers import page_text, pages_fingerprint

    out = []
    for cid in card_ids:
        card = model.card_by_id(board, cid)
        if card is None or not any(p in known for p in card["pages"]):
            continue
        summary = read_card_summary(root, cid)
        now = pages_fingerprint(card["pages"], known, lambda p: page_text(root, p))
        if summary is None or summary.get("fingerprint") != now:
            out.append(cid)
    return out


async def remove_card(root: Path, ref: str, *, cron: Any = None) -> dict[str, Any]:
    """Togli una scheda, con un Annulla che rimette tutto; restituisce la voce del
    cestino: ``card`` e ``paused`` (i cron fermati).

    La scheda, i suoi fili e il suo posto vanno in ``removedCards`` per
    :data:`model.REMOVED_KEEP_DAYS` giorni. Quel che faceva da sé si ferma — i
    suoi cron e i suoi promemoria (job anche loro) in pausa — e riparte con
    :func:`restore_card`. La wiki non cambia: le pagine restano, e la ricerca le
    trova.

    Una scheda senza pagine, senza cassetto o senza attese si toglie come le
    altre. Un **cassetto orfano** — ``board/cards/<id>.json`` senza la scheda in
    ``board.json`` — non ha niente da mettere nel cestino: si cancella il file, e
    la voce ha ``orphan: True`` (v. :func:`orphan_drawers`).
    """
    def trash() -> dict[str, Any]:
        taken: list[dict[str, Any]] = []
        try:
            mutate_board(root, lambda board: taken.append(_trash(board, need_card(board, ref))))
        except BoardEditError as exc:
            orphan = _drop_orphan_drawer(root, ref) if exc.code == "not_found" else None
            if orphan is None:
                raise
            return orphan
        return taken[0]

    entry = await asyncio.to_thread(trash)
    if entry.get("orphan"):
        return entry
    entry["paused"] = await _pause_trashed(root, entry["card"], cron)
    return entry


def orphan_drawers(root: Path, board: Mapping[str, Any] | None = None) -> list[str]:
    """Gli id dei cassetti (``board/cards/<id>.json``) che nessuna scheda reclama.

    Il caso delle prove del Piano a lista del 23 e 24/10/2026: il cassetto
    ``acqua-e-ciclo-stagionale`` stava su disco senza la sua scheda dal 4/10,
    nessuno strumento lo toglieva (``remove_card`` rispondeva «card not found», i tool
    file non scrivono in ``board/``) e l'editore lo lasciava ogni volta fra i
    «non risolti». Non sono orfani i cassetti di una scheda nel cestino (servono
    al suo Annulla) né quelli di una prima bacheca ancora nella busta: la
    passata che la propone li scrive prima che l'utente risponda.
    """
    folder = board_dir(root) / model.CARDS_DIR
    if not folder.is_dir():
        return []
    if board is None:
        board = read_board(root) or model.empty_board()
    claimed = {c["id"] for c in board.get("cards") or []} | model.removed_card_ids(board)
    for suggestion in board.get("suggestions") or []:
        for spec in suggestion.get("cards") or []:
            if isinstance(spec, Mapping) and isinstance(spec.get("id"), str):
                claimed.add(spec["id"])
    return sorted(p.stem for p in folder.glob("*.json")
                  if p.is_file() and model.is_valid_id(p.stem) and p.stem not in claimed)


def _drop_orphan_drawer(root: Path, ref: str) -> dict[str, Any] | None:
    """Cancella il cassetto orfano *ref* (per id, o per il titolo fatto id); la
    voce da restituire, o ``None`` se *ref* non ne nomina uno.

    Niente cestino: un cassetto è derivato dalle pagine
    (:func:`jenny.board.store.write_card_summary`), e senza la sua scheda non lo
    mostra niente.
    """
    orphans = set(orphan_drawers(root))
    card_id = next((c for c in (ref.strip(), model.slugify(ref, fallback="")) if c in orphans),
                   None)
    if card_id is None:
        return None
    delete_card_summary(root, card_id)
    logger.info("board: orphan drawer {} removed from {}", card_id, root.name)
    return {"card": {"id": card_id, "title": card_id, "pages": []}, "connections": [],
            "orphan": True, "paused": {"jobs": []}}


def _trash(board: dict[str, Any], card: dict[str, Any]) -> dict[str, Any]:
    """Dentro un *apply*: la scheda e i suoi fili passano nel cestino."""
    card_id = card["id"]
    board["cards"].remove(card)
    conns = [c for c in board["connections"] if card_id in (c["a"], c["b"])]
    board["connections"] = [c for c in board["connections"] if c not in conns]
    entry = {"card": card, "connections": conns, "at": model.now_iso(),
             "paused": {"jobs": []}}
    board["removedCards"] = [*board["removedCards"], entry][-model.MAX_REMOVED_CARDS:]
    return entry


async def _pause_trashed(root: Path, card: Mapping[str, Any], cron: Any) -> dict[str, list[str]]:
    """Dopo il cestino: si ferma quel che la scheda faceva da sé, e lo si annota."""
    jobs = []
    for job_id in removed.card_jobs(cron, root.name, card["id"]):
        # Solo quelli che questa «Togli» ha fermato: l'Annulla non deve
        # riprendere un cron che l'utente aveva già messo in pausa lui.
        if cron.set_paused(job_id, True) == "paused":
            jobs.append(job_id)
    paused = {"jobs": jobs}
    if not jobs:
        return paused

    def note(board: dict[str, Any]) -> None:
        for entry in board["removedCards"]:
            if entry["card"]["id"] == card["id"]:
                entry["paused"] = paused

    await asyncio.to_thread(mutate_board, root, note)
    return paused


async def restore_card(root: Path, ref: str, *, cron: Any = None) -> dict[str, Any]:
    """L'Annulla di «Togli»: la scheda torna dov'era, con i suoi fili.

    Le pagine che nel frattempo un'altra scheda ha preso restano a quella; i
    fili verso una scheda che non c'è più non tornano. Restituisce ``card`` e
    quel che riparte: ``jobs``.
    """
    def untrash() -> dict[str, Any]:
        notes = _notes(root)
        back: dict[str, Any] = {}

        def apply(board: dict[str, Any]) -> None:
            want = model.slugify(ref)
            entry = next((r for r in board["removedCards"] if r["card"]["id"] == ref
                          or model.slugify(r["card"]["title"]) == want), None)
            if entry is None:
                raise BoardEditError("not_found", "nothing to restore")
            card = entry["card"]
            if model.card_by_id(board, card["id"]) is not None:
                raise BoardEditError("conflict", "a card with that id is on the board")
            if model.is_full(board, notes):
                raise BoardEditError("conflict", "the board is full")
            owners = model.page_owners(board)
            card["pages"] = [p for p in card["pages"] if p not in owners]
            board["cards"].append(card)
            on_board = {c["id"] for c in board["cards"]}
            for conn in entry["connections"]:
                if conn["a"] in on_board and conn["b"] in on_board \
                        and len(board["connections"]) < model.MAX_CONNECTIONS:
                    board["connections"].append(conn)
            board["removedCards"] = [r for r in board["removedCards"] if r is not entry]
            back.update(card=card, **entry["paused"])

        mutate_board(root, apply)
        return back

    back = await asyncio.to_thread(untrash)
    if cron is not None:
        for job_id in back.get("jobs") or []:
            if cron.get_job(job_id) is not None:
                cron.set_paused(job_id, False)
    return back


# ── I fili ───────────────────────────────────────────────────────────────────


def connect(root: Path, a: str, b: str, label: Any = "") -> None:
    """Un filo dell'utente fra due schede."""
    text = model.clean_text(label, model.MAX_LABEL)

    def apply(board: dict[str, Any]) -> None:
        ca, cb = need_card(board, a), need_card(board, b)
        if not tie(board, ca["id"], cb["id"], by=model.USER):
            raise BoardEditError(
                "conflict", "not tied: same card, already tied, or the board has as many "
                "threads as it can show")
        board["connections"][-1]["label"] = text

    mutate_board(root, apply)


def disconnect(root: Path, a: str, b: str) -> bool:
    """Togliere un filo. È anche un «no» a quel filo: la passata non lo ripropone."""
    found: list[bool] = []

    def apply(board: dict[str, Any]) -> bool | None:
        ca, cb = find_card(board, a), find_card(board, b)
        pair = {ca["id"] if ca else a, cb["id"] if cb else b}
        kept = [c for c in board["connections"] if {c["a"], c["b"]} != pair]
        if len(kept) == len(board["connections"]):
            return False
        board["connections"] = kept
        found.append(True)
        # Nei due versi, perche' la proposta lo scrive nell'ordine in cui la fa.
        titles = [model.card_by_id(board, cid) for cid in sorted(pair)]
        if all(titles):
            ta, tb = (t["title"] for t in titles if t)
            for subject in (f"{ta} — {tb}", f"{tb} — {ta}"):
                board["declined"].append({"kind": "add_connection", "subject": subject,
                                          "at": model.now_iso(), "pagesAtDecline": 0})
        return None

    mutate_board(root, apply)
    return bool(found)


# ── Le sezioni dell'utente: note e attese ────────────────────────────────────


def _append_section(root: Path, ref: str, section: dict[str, Any], prefix: str) -> str:
    def apply(board: dict[str, Any]) -> None:
        card = need_card(board, ref)
        section["id"] = model.unique_id(prefix, [s["id"] for s in card["userSections"]])
        card["userSections"].append(section)

    mutate_board(root, apply)
    return section["id"]


def card_id_of(root: Path, ref: str) -> str:
    """L'id di una scheda che c'è, prima di scrivere altrove (il diario, le attese)."""
    return need_card(read_board(root) or {"cards": []}, ref)["id"]


def add_note(root: Path, ref: str, text: Any, *, timezone_name: str | None = None) -> str:
    """Una nota nel cassetto: è anche una riga ``[said]`` del diario."""
    from jenny.board.sections import journal_note

    body = model.clean_block(text, model.MAX_NOTE)
    if not body:
        raise BoardEditError("bad_request", "text required")
    card_id_of(root, ref)
    section: dict[str, Any] = {"kind": "note", "created": model.now_iso(), "text": body}
    # La riga [said] prima della sezione: se il diario non si scrive, la nota
    # resta comunque, ma senza la promessa di arrivare nella wiki.
    try:
        section["journalLine"] = journal_note(root, body, timezone_name=timezone_name)
    except OSError as exc:
        logger.warning("board: note not journaled in {}: {}", root.name, exc)
    return _append_section(root, ref, section, "s-note")


def _section(root: Path, ref: str, sid: str) -> tuple[str, dict[str, Any]]:
    board = read_board(root) or {"cards": []}
    card = need_card(board, ref)
    section = next((s for s in card["userSections"] if s["id"] == sid), None)
    if section is None:
        raise BoardEditError("not_found", "section not found")
    return card["id"], section


def update_note(root: Path, ref: str, sid: str, text: Any, *,
                timezone_name: str | None = None) -> None:
    """Una nota modificata è una riga di diario **nuova**: la riga di prima resta la
    verità di quando è stata detta."""
    from jenny.board.sections import journal_note

    card_id, section = _section(root, ref, sid)
    if section["kind"] != "note":
        raise BoardEditError("bad_request", "only notes and photos can be edited here")
    body = model.clean_block(text, model.MAX_NOTE)
    if not body:
        raise BoardEditError("bad_request", "text required")
    try:
        line = journal_note(root, body, timezone_name=timezone_name)
    except OSError:
        line = None

    def apply(board: dict[str, Any]) -> None:
        for s in need_card(board, card_id)["userSections"]:
            if s["id"] == sid:
                s["text"] = body
                if line:
                    s["journalLine"] = line

    mutate_board(root, apply)


def update_photo_caption(root: Path, ref: str, sid: str, text: Any, *,
                         timezone_name: str | None = None) -> None:
    """La nota di una foto, corretta: come una nota, una riga di diario **nuova**.

    Svuotata, la foto resta senza nota e non scrive niente: la riga di prima,
    se c'era, resta la verità di quando è stata detta.
    """
    from jenny.board.sections import journal_note

    card_id, section = _section(root, ref, sid)
    if section["kind"] != "photo":
        raise BoardEditError("bad_request", "only notes and photos can be edited here")
    body = model.clean_text(text, model.MAX_TEXT)
    line = None
    if body:
        try:
            line = journal_note(root, body, photo=section["path"], timezone_name=timezone_name)
        except OSError as exc:
            logger.warning("board: photo note not journaled in {}: {}", root.name, exc)

    def apply(board: dict[str, Any]) -> None:
        for s in need_card(board, card_id)["userSections"]:
            if s["id"] == sid:
                s["caption"] = body
                if line:
                    s["journalLine"] = line
                elif not body:
                    s.pop("journalLine", None)

    mutate_board(root, apply)


async def remove_section(root: Path, ref: str, sid: str, *, cron: Any = None) -> bool:
    """Togli una sezione. La wiki non cambia: togliere è una scelta di forma."""
    def drop_section() -> list[dict[str, Any]]:
        gone: list[dict[str, Any]] = []

        def apply(board: dict[str, Any]) -> bool | None:
            card = need_card(board, ref)
            keep = [s for s in card["userSections"] if s["id"] != sid]
            gone.extend(s for s in card["userSections"] if s["id"] == sid)
            if len(keep) == len(card["userSections"]):
                return False
            card["userSections"] = keep
            return None

        mutate_board(root, apply)
        return gone

    gone = await asyncio.to_thread(drop_section)
    for section in gone:
        if section.get("kind") == "cron" and cron is not None:
            # Togliere il cron dal cassetto toglie il job: nessun cron orfano.
            cron.remove_job(str(section.get("job")))
    return bool(gone)


# ── I promemoria: job dello scheduler ────────────────────────────────────────
#
# Dal 31/10/2026 un promemoria del quaderno è un job di ``cron/jobs.json``
# (``cron_link.add_notebook_job``), e ``board/reminders.json`` non c'è più. Un
# promemoria a data è un singolo: quando suona se ne va, quindi non ci sono
# attese «fatte», «tolte» o «scadute» da tenere. Uno a condizione è un
# ricorrente con un filtro (``payload.condition``), alla cadenza che sceglie
# l'utente. Tutto sul loop, come ogni gesto sullo scheduler; l'id di un
# promemoria è quello del suo job.

# La cadenza più fitta di un promemoria a condizione: il passo dell'orologio dei
# quaderni, che fa girare i lettori delle app.
MIN_CONDITION_EVERY_MS = 15 * 60 * 1000


def _need_cron(cron: Any) -> Any:
    if cron is None:
        raise BoardEditError("unavailable", "scheduler not running")
    return cron


def _reminder_text(text: Any) -> str:
    return model.clean_text(text, model.MAX_TITLE * 2)


def _when_schedule(when: Any, every_ms: Any, timezone_name: str | None) -> tuple[Any, Any]:
    """Lo ``schedule`` e la ``condition`` del job di un promemoria, da quel che arriva.

    *when* è ``{kind: "date", date, precision}`` o ``{kind: "condition", app,
    params, field, op, value}``; una condizione vuole *every_ms*, la cadenza
    con cui guardarla, che sceglie l'utente.
    """
    from jenny.cron.dates import date_schedule
    from jenny.cron.types import CronSchedule

    if not isinstance(when, Mapping):
        raise BoardEditError("bad_request", "invalid when")
    if when.get("kind") == "date":
        day = str(when.get("date") or "").strip()
        value = day[:7] if when.get("precision") == "month" else day[:10]
        if len(value) not in (7, 10):
            raise BoardEditError("bad_request", "invalid when: a date is YYYY-MM-DD")
        try:
            return date_schedule(value, timezone_name), None
        except ValueError as exc:
            raise BoardEditError("bad_request", f"invalid when: {exc}") from exc
    if when.get("kind") == "condition":
        condition = {key: when.get(key) for key in ("app", "params", "field", "op", "value")}
        condition["params"] = dict(when.get("params") or {}) \
            if isinstance(when.get("params"), Mapping) else {}
        if (not model.is_valid_id(condition["app"]) or not isinstance(condition["field"], str)
                or not condition["field"] or condition["op"] not in model.CONDITION_OPS
                or isinstance(condition["value"], bool)
                or not isinstance(condition["value"], (int, float, str))):
            raise BoardEditError("bad_request", "invalid when: a condition needs app, field, "
                                                "op and value")
        return CronSchedule(kind="every", every_ms=_every(every_ms)), condition
    raise BoardEditError("bad_request", "invalid when")


def _every(every_ms: Any) -> int:
    if isinstance(every_ms, bool) or not isinstance(every_ms, (int, float)):
        raise BoardEditError("bad_request", "a condition needs every_ms: how often to check it")
    if every_ms < MIN_CONDITION_EVERY_MS:
        raise BoardEditError("bad_request", "every_ms must be at least 15 minutes")
    return int(every_ms)


def _why_page(root: Path, why: Any) -> str | None:
    """La pagina che spiega un promemoria, se c'è davvero sotto ``wiki/``."""
    page = str(why or "").strip().removeprefix("wiki/")
    return _check_pages(root, [page])[0] if page else None


def reminder_job(cron: Any, root: Path, rid: str) -> Any:
    """Il job del promemoria *rid* di questo quaderno, o un ``not_found``.

    Solo un promemoria (un singolo, o un ricorrente con un filtro) legato a
    questo quaderno: la bacheca non tocca la sveglia della chat personale, né
    un cron che si ripete (quello lo cambia il tool ``cron``).
    """
    from jenny.board.cron_link import is_note

    job = _need_cron(cron).get_job(str(rid or "").strip())
    if (job is None or (job.payload.board or {}).get("notebook") != root.name
            or not is_note(job.schedule.kind, job.payload.condition)):
        raise BoardEditError("not_found", f"no reminder `{rid}` in this notebook")
    return job


async def add_reminder(root: Path, ref: str, text: Any, when: Any, *, cron: Any,
                       every_ms: Any = None, why: Any = None,
                       timezone_name: str | None = None) -> str:
    """Un promemoria nuovo: di una scheda (*ref*) o di tutto il quaderno (*ref* vuoto).

    Diventa un job del quaderno (v. :func:`jenny.board.cron_link.add_notebook_job`):
    suona nella chat del quaderno, e sulla bacheca è una riga del post-it della
    sua scheda, o di quello libero. Restituisce l'id del job.
    """
    from jenny.board.cron_link import add_notebook_job

    _need_cron(cron)
    body = _reminder_text(text)
    if not body:
        raise BoardEditError("bad_request", "text required")
    schedule, condition = _when_schedule(when, every_ms, timezone_name)
    scope = (await asyncio.to_thread(card_id_of, root, ref) if str(ref or "").strip()
             else model.NOTEBOOK)
    page = await asyncio.to_thread(_why_page, root, why)
    try:
        job = add_notebook_job(cron, root.name, scope, name=body, message=body,
                               schedule=schedule, why=page, condition=condition)
    except ValueError as exc:
        raise BoardEditError("bad_request", str(exc)) from exc
    return str(job.id)


async def update_reminder(root: Path, rid: str, *, cron: Any, text: Any = None,
                          when: Any = None, every_ms: Any = None, why: Any = None,
                          clear_why: bool = False,
                          timezone_name: str | None = None) -> dict[str, Any]:
    """Testo, data, cadenza e ``why`` di un promemoria, in una scrittura sola.

    Tutto quel che può essere rifiutato si controlla prima di scrivere. Una data
    uguale a quella che ha già non lo sposta: un singolo a un'ora esatta resta a
    quell'ora quando si corregge solo il testo. Una data su un promemoria a
    condizione lo fa diventare un singolo. Restituisce ``{id, text, why}``.
    """
    from jenny.board.cron_link import job_snapshot, note_of

    job = reminder_job(cron, root, rid)
    body = _reminder_text(text) if text else ""
    page = await asyncio.to_thread(_why_page, root, why)
    if not (body or when is not None or every_ms is not None or page or clear_why):
        raise BoardEditError("bad_request", "nothing to change")
    changes: dict[str, Any] = {}
    if body:
        changes.update(name=body, message=body)
    if page:
        changes["why"] = page
    elif clear_why:
        changes["why"] = None
    if when is not None:
        if every_ms is None and job.schedule.kind == "every":
            every_ms = job.schedule.every_ms
        schedule, condition = _when_schedule(when, every_ms, timezone_name)
        snap = job_snapshot(job)
        sched = snap["schedule"]
        now = note_of(job_id=job.id, scope="", text="", kind=sched.get("kind"),
                      at_ms=sched.get("at_ms"), precision=sched.get("precision"),
                      condition=job.payload.condition, every_ms=sched.get("every_ms"),
                      tz_name=timezone_name)
        new = note_of(job_id=job.id, scope="", text="", kind=schedule.kind,
                      at_ms=schedule.at_ms, precision=schedule.precision, condition=condition,
                      every_ms=schedule.every_ms, tz_name=timezone_name)
        same = (now is not None and new is not None
                and {k: v for k, v in now["when"].items() if k != "time"} == new["when"])
        if not same:
            changes.update(schedule=schedule, condition=condition)
    elif every_ms is not None:
        from jenny.cron.types import CronSchedule

        if not job.payload.condition:
            raise BoardEditError("bad_request", "every_ms is only for a condition reminder")
        changes["schedule"] = CronSchedule(kind="every", every_ms=_every(every_ms))
    try:
        done = cron.update_job(job.id, **changes) if changes else job
    except ValueError as exc:
        raise BoardEditError("bad_request", str(exc)) from exc
    if done is None:
        raise BoardEditError("not_found", f"no reminder `{rid}` in this notebook")
    return {"id": done.id, "text": done.name, "why": done.payload.why or ""}


async def close_reminder(root: Path, rid: str, *, cron: Any,
                         timezone_name: str | None = None) -> str:
    """Un promemoria fatto: il job se ne va, e resta un fatto (una riga di diario)."""
    from jenny.board.cron_link import detach_job
    from jenny.board.sections import journal_note

    job = reminder_job(cron, root, rid)
    text = job.name or job.payload.message
    await detach_job(cron, job.id)
    try:
        await asyncio.to_thread(journal_note, root, f"Done: {text}",
                                timezone_name=timezone_name)
    except OSError as exc:
        logger.warning("board: done reminder not journaled in {}: {}", root.name, exc)
    return text


async def drop_reminder(root: Path, rid: str, *, cron: Any) -> None:
    """Togli un promemoria: il job se ne va, e nient'altro."""
    from jenny.board.cron_link import detach_job

    job = reminder_job(cron, root, rid)
    await detach_job(cron, job.id)


async def attach_reminder(root: Path, ref: str, rid: str, *, cron: Any) -> dict[str, Any]:
    """Un promemoria che c'è già, sulla scheda *ref* (o, *ref* vuoto, sul quaderno).

    Un promemoria ha un ambito solo, quindi attaccarlo altrove lo **sposta**, e
    attaccarlo dove sta già non cambia niente. Serve a quello rimasto senza
    post-it perché la sua scheda è nel cestino o non c'è più. Lo scheduler non
    cambia il legame di un job: se ne fa uno nuovo uguale, sul nuovo ambito (e
    acceso: se era fermo per la scheda nel cestino, riparte), e il vecchio se ne
    va. Restituisce ``{"reminder": {id, card, text}, "from", "changed"}``.
    """
    from jenny.board.cron_link import add_notebook_job, detach_job, scope_of

    job = reminder_job(cron, root, rid)
    scope = (await asyncio.to_thread(card_id_of, root, ref) if str(ref or "").strip()
             else model.NOTEBOOK)
    before = scope_of(job.payload.board or {})
    text = job.name or job.payload.message
    if before == scope:
        return {"reminder": {"id": job.id, "card": scope, "text": text}, "from": before,
                "changed": False}
    try:
        moved = add_notebook_job(cron, root.name, scope, name=job.name,
                                 message=job.payload.message, schedule=job.schedule,
                                 mode=job.payload.mode, why=job.payload.why,
                                 condition=job.payload.condition)
    except ValueError as exc:
        raise BoardEditError("bad_request", str(exc)) from exc
    await detach_job(cron, job.id)
    return {"reminder": {"id": moved.id, "card": scope, "text": text}, "from": before,
            "changed": True}


# ── Lo scheletro ─────────────────────────────────────────────────────────────


def set_skeleton(root: Path, raw: Any) -> list[str]:
    """Lo scheletro dei cassetti: solo finché la bacheca non ha schede.

    Con le schede già scritte su uno scheletro, cambiarlo lascerebbe ogni cassetto
    con sezioni che lo scheletro non ha più.
    """
    if isinstance(raw, str):
        raw = [part for part in raw.replace(";", ",").split(",")]
    if not isinstance(raw, list):
        raise BoardEditError("bad_request", "skeleton must be a list")
    skeleton = [s for s in (model.clean_text(x, model.MAX_TITLE) for x in raw) if s]
    skeleton = list(dict.fromkeys(skeleton))[:model.MAX_SKELETON]

    def apply(board: dict[str, Any]) -> bool | None:
        if not skeleton or board["cards"]:
            return False
        board["skeleton"] = skeleton
        return None

    mutate_board(root, apply)
    return skeleton


# ── La busta ─────────────────────────────────────────────────────────────────


def accept_suggestion(board: dict[str, Any], suggestion: Mapping[str, Any],
                      notes: Iterable[Mapping[str, Any]],
                      near: tuple[float, float] | None = None,
                      workspace: Path | None = None) -> list[dict[str, Any]]:
    """Dentro un *apply*: applica una proposta accettata. Il posto lo sceglie il codice.

    Con *near* — il centro di dove l'utente sta guardando il muro — le schede
    nuove nascono li' vicino, e non nello schermo dell'origine. Restituisce le
    voci di cestino delle schede tolte (``remove_card``): chi la chiama, dopo
    aver scritto, ferma quel che facevano da sé (:func:`_pause_trashed`).
    """
    kind = suggestion["kind"]
    owners = model.page_owners(board)
    # Anche gli id delle schede tolte: un Annulla non deve trovare il posto preso.
    taken = {c["id"] for c in board["cards"]} | model.removed_card_ids(board)
    stamp = model.now_iso()
    trashed: list[dict[str, Any]] = []

    def new_card(title: str, glyph: str, pages: list[str], wanted: str = "",
                 size: str | None = None) -> dict[str, Any]:
        # L'id proposto, se c'e' ed e' libero: la passata che propone la prima
        # bacheca scrive gia' i cassetti con quegli id, e accettare non deve
        # lasciarli orfani.
        base = wanted if model.is_valid_id(wanted) else model.slugify(title)
        card_id = model.unique_id(base, taken)
        taken.add(card_id)
        box = model.card_box({"size": size})
        target = (near[0] - box[0] / 2, near[1] - box[1] / 2) if near is not None else None
        x, y = model.free_slot(board, notes, box=box, near=target)
        card = {
            "id": card_id, "title": model.clean_text(title, model.MAX_TITLE) or card_id,
            "titleBy": model.JENNY,
            "pages": [p for p in pages if p not in owners],
            "pos": {"x": x, "y": y, "by": model.JENNY},
            "glyph": glyph, "userSections": [], "created": stamp,
        }
        if size:
            card["size"] = size
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
            if isinstance(spec, Mapping) and model.items_on_board(board, notes) < model.MAX_ITEMS:
                new_card(str(spec.get("title") or ""), str(spec.get("glyph") or ""),
                         [p for p in spec.get("pages") or [] if isinstance(p, str)],
                         str(spec.get("id") or ""))
        model.add_since(board, "board_created", cards=len(board["cards"]))
    elif kind == "add_card":
        if model.is_full(board, notes):
            raise BoardEditError("conflict", "the board is full")
        card = new_card(suggestion.get("subject") or "", str(suggestion.get("glyph") or ""),
                        list(suggestion.get("pages") or []))
        model.add_since(board, "card_added", card=card["id"], title=card["title"])
    elif kind == "remove_card":
        # Come la «Togli» dell'utente, perche' lo e': nel cestino, con l'Annulla.
        # Fino al 09/10/2026 una proposta accettata cancellava la scheda per
        # sempre e lasciava girare i cron del suo cassetto.
        card = model.card_by_id(board, str(suggestion.get("card") or ""))
        if card is not None:
            trashed.append(_trash(board, card))
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
    elif kind == "add_app_card":
        if model.is_full(board, notes):
            raise BoardEditError("conflict", "the board is full")
        app = str(suggestion.get("app") or "")
        if model.is_valid_id(app):
            title = model.clean_text(suggestion.get("subject"), model.MAX_TITLE) or app
            card = new_card(title, "", [], size=app_size(workspace, app))
            card["titleBy"] = model.USER
            card["app"] = {"slug": app, "params": dict(suggestion.get("params") or {})}
            model.add_since(board, "card_added", card=card["id"], title=card["title"])
            # L'app c'entra con una scheda: un filo, non un posto nel suo cassetto.
            other = model.card_by_id(board, str(suggestion.get("card") or ""))
            if other is not None and tie(board, card["id"], other["id"]):
                model.add_since(board, "connection_added", a=card["id"], b=other["id"])
    elif kind == "add_section":
        # Una proposta di prima (un'app nel cassetto di una scheda): diventa
        # la polaroid dell'app, legata a quella scheda.
        if model.is_full(board, notes):
            raise BoardEditError("conflict", "the board is full")
        other = model.card_by_id(board, str(suggestion.get("card") or ""))
        app = str(suggestion.get("app") or "")
        if other is not None and model.is_valid_id(app):
            card = new_card(app_title(workspace, app), "", [], size=app_size(workspace, app))
            card["titleBy"] = model.USER
            card["app"] = {"slug": app, "params": dict(suggestion.get("params") or {})}
            model.add_since(board, "card_added", card=card["id"], title=card["title"])
            if tie(board, card["id"], other["id"]):
                model.add_since(board, "connection_added", a=card["id"], b=other["id"])
    return trashed


ANSWERS = ("yes", "no", "later")


async def respond_suggestion(root: Path, sid: str, answer: str, *,
                             near: tuple[float, float] | None = None,
                             workspace: Path | None = None, cron: Any = None) -> dict[str, Any]:
    """Sì, no o più avanti a una proposta della busta.

    Un «no» si ricorda (per soggetto e tipo), e torna solo se il soggetto cresce
    di molto; «più avanti» la rimanda di qualche passata. Restituisce la proposta.
    """
    if answer not in ANSWERS:
        raise BoardEditError("bad_request", "answer must be yes, no or later")
    trashed: list[dict[str, Any]] = []
    answered: dict[str, Any] = {}

    def respond() -> None:
        notes = _notes(root)

        def apply(board: dict[str, Any]) -> None:
            suggestion = next((s for s in board["suggestions"] if s["id"] == sid), None)
            if suggestion is None:
                raise BoardEditError("not_found", "suggestion not found")
            answered.update(suggestion)
            if answer == "yes":
                trashed.extend(accept_suggestion(board, suggestion, notes, near, workspace))
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

        mutate_board(root, apply)

    await asyncio.to_thread(respond)
    for entry in trashed:
        await _pause_trashed(root, entry["card"], cron)
    return answered
