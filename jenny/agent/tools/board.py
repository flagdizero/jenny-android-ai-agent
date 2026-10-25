"""Il tool ``board``: l'editore dei quaderni cambia la bacheca come l'ha chiesto l'utente.

Esiste solo per il subagent ``notebook-editor`` (scope ``notebook``): né la
chat né ``operator`` lo ereditano. Dal 09/10/2026 un quaderno si cambia tutto
su richiesta — pagine, mappa e bacheca — e lo cambia l'editore che Jenny lancia
dalla chat del quaderno. Il giardiniere resta l'altra metà, e non si toccano:
lui lavora di sua iniziativa, propone e basta (``jenny/agent/board_tools.py``).

Ogni scrittura passa da :mod:`jenny.board.edit`, la stessa strada
dell'interfaccia, e vale come dell'utente: il giardiniere non può disfarla
(:func:`jenny.board.model.ownership_violations`). Il cassetto di una scheda le
cui pagine cambiano lo riscrive l'editore stesso, con le regole della passata
(:mod:`jenny.board.drawers`).

Lavora per la conversazione del quaderno da cui è partito l'editore, mai per un
cron o per il giardiniere, e mai in un turno di sola lettura.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from jenny.agent.tools.base import Tool, tool_parameters
from jenny.agent.tools.context import current_request_session_key
from jenny.agent.tools.schema import (
    ArraySchema,
    BooleanSchema,
    ObjectSchema,
    StringSchema,
    tool_parameters_schema,
)
from jenny.board import edit, model
from jenny.board.cron_link import notebook_jobs_on_disk
from jenny.board.drawers import DrawerError, as_list, page_text, pages_fingerprint, write_drawer
from jenny.board.store import read_board, read_card_summary, read_reminders
from jenny.security.workspace_access import (
    READONLY_TOOL_REFUSAL,
    current_turn_is_readonly,
    current_workspace_scope,
)

_WRITES = (
    "add_card", "rename_card", "move_card", "remove_card", "restore_card", "move_pages",
    "remove_pages", "connect", "disconnect", "add_note", "add_reminder", "attach_reminder",
    "update_reminder", "close_reminder", "remove_section",
    "set_skeleton", "answer_proposal", "write_drawer",
)
_ACTIONS = ("show", *_WRITES)
# Quelle che fermano, riprendono o tolgono un cron: il servizio cron vuole il loop
# (v. :mod:`jenny.board.edit`), quindi non passano da ``asyncio.to_thread``.
_ON_THE_LOOP = ("remove_card", "restore_card", "remove_section", "answer_proposal")

_SECTION = ObjectSchema(
    title=StringSchema("One of the board's skeleton sections, spelled exactly as `show` lists it."),
    text=StringSchema("At most two short sentences: what the pages say about this, now."
                      " Plain prose, no [[links]]: the drawer lists the pages under it."),
    sources=ArraySchema(StringSchema("A page of this card, relative to wiki/."),
                        description="The pages this section comes from, most relevant first."),
    required=["title", "text", "sources"],
)


@tool_parameters(tool_parameters_schema(
    action=StringSchema(
        "show: the board as the user sees it — read it first. "
        "add_card (title, pages?, glyph?, near?): a new card; pages already on another card "
        "move to it. rename_card (card, title). move_card (card, near): put it next to another "
        "card. remove_card (card): into the trash, restorable; for a drawer with no card, "
        "deletes it. restore_card (card). "
        "move_pages (pages, card): onto that card, from another card or from off the board. "
        "remove_pages (pages, card?): off that card, or off every card when card is empty; "
        "also for pages that no longer exist. The files stay in the wiki (to delete them, "
        "`notebook action=remove_pages`). "
        "connect (card, other, label?) / disconnect (card, other): a thread. "
        "add_note (card, text). add_reminder (card?, text, date, precision?): tie it to the "
        "card it is about, or leave card empty for the whole notebook — you decide. "
        "attach_reminder (card?, id): put an existing open reminder on that card (empty: the "
        "whole notebook); a reminder sits on one card only, so this moves it. "
        "update_reminder (id, text?, date?, precision?, why?, clear_why?): correct an open "
        "reminder; why is the wiki page that explains it, clear_why=true removes it. "
        "close_reminder (id): done. remove_section (card, id). "
        "set_skeleton (skeleton): the drawer sections, only while the board has no cards. "
        "answer_proposal (id, answer): yes / no / later to a proposal in the envelope. "
        "write_drawer (card, brief, sections): rewrite a drawer from its pages.",
        enum=list(_ACTIONS),
    ),
    card=StringSchema("A card's id or title. For move_pages, the card the pages go to."),
    other=StringSchema("connect / disconnect: the other card."),
    near=StringSchema("add_card / move_card: the card to put it next to."),
    title=StringSchema("add_card / rename_card: the card's name, in the user's words."),
    glyph=StringSchema("add_card: an icon name, e.g. leaf, tree, trees, flower, book, coin."),
    pages=ArraySchema(StringSchema("A page path relative to wiki/, as `show` lists it."),
                      description="add_card / move_pages / remove_pages: the pages."),
    label=StringSchema("connect: why the two belong together, in a few words."),
    text=StringSchema("add_note / add_reminder / update_reminder: the text, in the user's "
                      "language."),
    date=StringSchema("add_reminder / update_reminder: YYYY-MM-DD. For 'in March', the first "
                      "of the month."),
    precision=StringSchema("add_reminder / update_reminder: `day`, or `month` for 'in March'.",
                           enum=["day", "month"]),
    why=StringSchema("update_reminder: the page that explains the reminder, relative to wiki/ "
                     "(it must exist). Omitted or empty: unchanged."),
    clear_why=BooleanSchema(description="update_reminder: true removes the reminder's page."),
    id=StringSchema("attach_reminder / close_reminder / update_reminder: the reminder id. "
                    "remove_section: the section id. answer_proposal: the proposal id."),
    answer=StringSchema("answer_proposal.", enum=list(edit.ANSWERS)),
    skeleton=ArraySchema(StringSchema("A section title."),
                         description="set_skeleton: the drawer sections, in order."),
    brief=StringSchema("write_drawer: one or two sentences — the state of this subject now."),
    sections=ArraySchema(_SECTION, description="write_drawer: the skeleton sections that "
                                               "have material in the card's pages."),
    required=["action"],
))
class BoardTool(Tool):
    """La bacheca di un quaderno, cambiata come l'ha chiesto l'utente."""

    _scopes = {"notebook"}

    def __init__(self, origin_session_key: str | None = None, cron: Any = None,
                 timezone: str | None = None) -> None:
        # La conversazione per cui si lavora: dentro il giro dell'editore la
        # sessione di richiesta e' la sua, non quella del quaderno.
        self._origin = origin_session_key
        self._cron = cron
        self._timezone = timezone

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return True

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(origin_session_key=getattr(ctx, "origin_session_key", None),
                   cron=getattr(ctx, "cron_service", None),
                   timezone=getattr(ctx, "timezone", None))

    @property
    def name(self) -> str:
        return "board"

    @property
    def description(self) -> str:
        return (
            "Change this notebook's board exactly as the user asked: cards, which pages each "
            "holds, threads, notes, reminders, drawers, the envelope. Every write counts as the "
            "user's own. Start with action=show. When a card's pages change, the reply lists "
            "the drawers to rewrite: rewrite each with action=write_drawer, from its pages."
        )

    def _root(self) -> Path | None:
        from jenny.session.keys import is_project_session_key
        from jenny.utils.wiki_paths import is_wiki_root

        key = self._origin or current_request_session_key() or ""
        if not is_project_session_key(key):
            return None
        scope = current_workspace_scope()
        root = scope.write_root() if scope is not None else None
        return root if root is not None and is_wiki_root(root) else None

    async def execute(self, action: str = "", **kw: Any) -> str:
        root = self._root()
        if root is None:
            return ("Not from here: a board is changed only for a notebook's chat. "
                    "Nothing was written.")
        if action not in _ACTIONS:
            return f"Unknown action: use {', '.join(_ACTIONS)}."
        if action == "show":
            return await asyncio.to_thread(show, root)
        if current_turn_is_readonly():
            return READONLY_TOOL_REFUSAL
        try:
            if action in _ON_THE_LOOP:
                out = await self._write_on_the_loop(root, action, kw)
            else:
                out = await asyncio.to_thread(self._write, root, action, kw)
        except edit.BoardEditError as exc:
            return f"Not done: {exc.message}"
        except DrawerError as exc:
            return f"Drawer not written: {exc}"
        from jenny.board.notify import board_changed

        await board_changed(root.name)
        return out

    def _write(self, root: Path, action: str, kw: dict[str, Any]) -> str:
        card = str(kw.get("card") or "")
        if action == "add_card":
            made, redraw = edit.add_card(root, kw.get("title"), glyph=kw.get("glyph") or "",
                                         pages=_strings(kw.get("pages")),
                                         near_card=str(kw.get("near") or "") or None)
            return f"Card `{made['id']}` «{made['title']}» added.{_redraw(root, redraw)}"
        if action == "rename_card":
            edit.rename_card(root, card, kw.get("title"))
            return "Renamed."
        if action == "move_card":
            edit.move_card(root, card, str(kw.get("near") or ""))
            return "Moved."
        if action == "move_pages":
            redraw = edit.move_pages(root, _strings(kw.get("pages")), card)
            return f"Pages moved.{_redraw(root, redraw)}"
        if action == "remove_pages":
            taken, redraw = edit.remove_pages(root, _strings(kw.get("pages")), card or None)
            return _removed(root, taken, redraw, one_card=bool(card))
        if action == "connect":
            edit.connect(root, card, str(kw.get("other") or ""), kw.get("label"))
            return "Tied."
        if action == "disconnect":
            if not edit.disconnect(root, card, str(kw.get("other") or "")):
                return "Those two were not tied: nothing changed."
            return "Thread removed."
        if action == "add_note":
            sid = edit.add_note(root, card, kw.get("text"), timezone_name=self._timezone)
            return f"Note `{sid}` added (it is also a [said] line in the journal)."
        if action == "add_reminder":
            when = {"kind": "date", "date": kw.get("date"),
                    "precision": kw.get("precision") or "day"}
            rid = edit.add_reminder(root, card, kw.get("text"), when)
            if not card:
                return (f"Reminder `{rid}` added for the whole notebook: a line on the board's "
                        "free sticky note.")
            return (f"Reminder `{rid}` added: a line on the card's sticky note and in its care "
                    "calendar.")
        if action == "attach_reminder":
            return _attach_reminder(root, card, str(kw.get("id") or ""))
        if action == "update_reminder":
            return _update_reminder(root, str(kw.get("id") or ""), kw)
        if action == "close_reminder":
            text = edit.close_reminder(root, str(kw.get("id") or ""),
                                       timezone_name=self._timezone)
            return f"Done: «{text}» (it is now a line in the journal)."
        if action == "set_skeleton":
            board = read_board(root)
            if board is not None and board["cards"]:
                raise edit.BoardEditError(
                    "conflict", "the skeleton is set while the board has no cards")
            skeleton = edit.set_skeleton(root, as_list(kw.get("skeleton")))
            return f"Skeleton: {' · '.join(skeleton) or '(empty)'}."
        return self._write_drawer(root, card, kw)

    async def _write_on_the_loop(self, root: Path, action: str, kw: dict[str, Any]) -> str:
        card = str(kw.get("card") or "")
        if action == "remove_card":
            entry = await edit.remove_card(root, card, cron=self._cron)
            if entry.get("orphan"):
                cid = entry["card"]["id"]
                return (f"No card `{cid}` on the board: its leftover drawer "
                        f"board/cards/{cid}.json is deleted. Nothing to restore.")
            stopped = _jobs_note(entry["paused"], "paused until restore_card")
            return (f"«{entry['card']['title']}» is in the trash (restore_card brings it back). "
                    f"Its pages stay in the wiki, now off the board.{stopped}")
        if action == "restore_card":
            back = await edit.restore_card(root, card, cron=self._cron)
            title, resumed = back["card"]["title"], _jobs_note(back, "running again")
            if not back["card"]["pages"]:
                return (f"«{title}» is back on the board, with no pages: they are on other cards "
                        f"now. It has no drawer until it gets pages (move_pages).{resumed}")
            redraw = await asyncio.to_thread(_redraw_if_stale, root, back["card"])
            return f"«{title}» is back on the board.{resumed}{redraw}"
        if action == "remove_section":
            if not await edit.remove_section(root, card, str(kw.get("id") or ""),
                                             cron=self._cron):
                return "No such section on that card: nothing changed."
            return "Section removed."
        done = await edit.respond_suggestion(root, str(kw.get("id") or ""),
                                             str(kw.get("answer") or ""), cron=self._cron,
                                             workspace=_workspace())
        return f"Answered {kw.get('answer')} to «{done.get('subject') or done['kind']}»."

    @staticmethod
    def _write_drawer(root: Path, ref: str, kw: dict[str, Any]) -> str:
        board = read_board(root) or model.empty_board()
        card = edit.need_card(board, ref)
        if not card["pages"]:
            raise edit.BoardEditError("conflict", f"«{card['title']}» has no pages to write from")
        sections, changed, problems = write_drawer(
            root, card["id"], pages=card["pages"], skeleton=board["skeleton"],
            brief=kw.get("brief"), sections=kw.get("sections"),
        )
        note = f" Skipped: {'; '.join(problems)}." if problems else ""
        return f"Drawer of «{card['title']}» written: {len(sections)} sections, {changed} changed.{note}"


def _update_reminder(root: Path, rid: str, kw: dict[str, Any]) -> str:
    """Testo, data e ``why`` di un'attesa aperta, in una scrittura sola.

    ``why`` assente o vuoto lascia la pagina com'è: un campo vuoto non deve
    staccarla in silenzio. Per toglierla c'è ``clear_why``.
    """
    text, date = kw.get("text"), kw.get("date")
    why = str(kw.get("why") or "").strip()
    clear = kw.get("clear_why") is True
    if not (text or date or why or clear):
        raise edit.BoardEditError("bad_request", "give text, date, why or clear_why to change")
    when = ({"kind": "date", "date": date, "precision": kw.get("precision") or "day"}
            if date else None)
    rem = edit.update_reminder_fields(root, rid, text=text, when=when, why=why or None,
                                      clear_why=clear and not why)
    if why:
        return f"Reminder `{rid}` updated; why: {rem['why']}."
    if clear:
        return f"Reminder `{rid}` updated; it no longer points to a page."
    return f"Reminder `{rid}` updated."


def _attach_reminder(root: Path, card: str, rid: str) -> str:
    """La risposta di ``attach_reminder``: dove sta ora, e da dove è venuta."""
    done = edit.attach_reminder(root, card, rid)
    rem = done["reminder"]
    where = _where(rem["card"])
    if not done["changed"]:
        return f"Reminder `{rem['id']}` is already {where}: nothing changed."
    tail = ("a line on the board's free sticky note" if rem["card"] == model.NOTEBOOK
            else "a line on the card's sticky note and in its care calendar")
    return (f"Reminder `{rem['id']}` «{rem['text']}» is now {where} "
            f"(it was {_where(done['from'])}): {tail}.")


def _jobs_note(what: dict[str, Any], state: str) -> str:
    """«Its 1 cron job: paused…» — quel che la scheda fa da sé."""
    jobs = len(what.get("jobs") or [])
    if not jobs:
        return ""
    return f" What it did by itself — {jobs} cron job(s) — is {state}."


def _strings(value: Any) -> list[str]:
    return [p for p in as_list(value) if isinstance(p, str)]


def _workspace() -> Path | None:
    try:
        from jenny.config.paths import get_workspace_path

        return get_workspace_path()
    except Exception:  # noqa: BLE001 — serve solo alle proposte di un'app
        return None


def _removed(root: Path, taken: dict[str, list[str]], redraw: list[str], *,
             one_card: bool) -> str:
    """La risposta di ``remove_pages``: da dove sono uscite, e i cassetti da riscrivere."""
    if not taken:
        where = "that card" if one_card else "any card"
        return f"None of those pages was on {where}: nothing changed."
    where = "; ".join(f"`{cid}`: {', '.join(pages)}" for cid, pages in taken.items())
    return f"Taken off the board — {where}. The wiki is untouched.{_redraw(root, redraw)}"


def _redraw(root: Path, cards: list[str]) -> str:
    """La coda della risposta: i cassetti da riscrivere, con le pagine di ognuno."""
    if not cards:
        return ""
    board = read_board(root) or model.empty_board()
    lines = []
    for cid in cards:
        card = model.card_by_id(board, cid)
        if card is not None:
            lines.append(f"- `{cid}` «{card['title']}»: {', '.join(card['pages'])}")
    return "\nDrawers to rewrite (write_drawer, from these pages):\n" + "\n".join(lines)


def _redraw_if_stale(root: Path, card: dict[str, Any]) -> str:
    """Dopo un ripristino: il cassetto da riscrivere solo se non è aggiornato."""
    from jenny.board.view import wiki_titles

    if _drawer_state(root, card, wiki_titles(root)) == "up to date":
        return ""
    return _redraw(root, [card["id"]])


def _section_words(section: dict) -> str:
    """Una sezione dell'utente nella vista della bacheca; una foto con percorso e
    nota, perché chi lavora sulla scheda sappia cos'è e dove aprirla."""
    words = f"`{section['id']}` {section['kind']}"
    if section["kind"] == "photo":
        caption = section.get("caption") or ""
        words += f" {section.get('path')}" + (f" «{caption}»" if caption else "")
    return words

def show(root: Path) -> str:
    """La bacheca in testo: quel che serve all'editore per decidere, e gli id per agire."""
    from jenny.board.view import missing_pages, offboard_pages, wiki_titles

    board = read_board(root)
    titles = wiki_titles(root)
    if board is None:
        pages = ", ".join(sorted(titles)) or "none"
        return (f"This notebook has no board yet. Set the skeleton (set_skeleton), then add cards "
                f"with their pages. Pages: {pages}")
    reminders = read_reminders(root)
    gone = missing_pages(board, titles)
    lines = [
        f"Skeleton: {' · '.join(board['skeleton']) or '(none yet)'}",
        f"Items on the board: {model.items_on_board(board, reminders)} of {model.MAX_ITEMS}",
        "",
        "Cards:",
    ]
    for card in board["cards"]:
        if card.get("app"):
            lines.append(f"- `{card['id']}` «{card['title']}» — the app `{card['app']['slug']}`")
            continue
        # Un cron scritto prima del 20/10/2026 ha ancora una sezione: si elenca una
        # volta sola, fra le automazioni qui sotto.
        sections = ", ".join(_section_words(s) for s in card["userSections"]
                             if s["kind"] != "cron")
        dead = gone.get(card["id"]) or []
        pages = [p for p in card["pages"] if p not in dead]
        lines.append(
            f"- `{card['id']}` «{card['title']}» — pages: {', '.join(pages) or 'none'}"
            + (f"; MISSING (no such file): {', '.join(dead)}" if dead else "")
            + f"; drawer: {_drawer_state(root, card, titles)}"
            + (f"; sections: {sections}" if sections else "")
        )
    if board["connections"]:
        lines += ["", "Threads:"]
        lines += [f"- `{c['a']}` — `{c['b']}`" + (f": {c['label']}" if c["label"] else "")
                  for c in board["connections"]]
    open_rems = model.open_reminders(reminders)
    if open_rems:
        lines += ["", "Open reminders:"]
        lines += [f"- `{r['id']}` {_where(r['card'])}: {r['text']} ({_when(r)})"
                  + _off_board(board, r["card"]) for r in open_rems]
    # I cron li tiene lo scheduler: qui si vedono, li cambia il tool ``cron``.
    jobs = notebook_jobs_on_disk(root.name)
    if jobs:
        lines += ["", "Automations (the cron tool changes them):"]
        lines += [f"- `{j['id']}` {_where(j['scope'])}: {j['name']}"
                  + (" (paused)" if j["paused"] else "") for j in jobs]
    off = offboard_pages(board, titles)
    lines += ["", f"Off the board ({len(off)}): {', '.join(off) or 'none'}"]
    if gone:
        # Nessuno li pota da solo (v. ``edit.remove_pages``): chi legge qui li toglie.
        dead = sum(len(pages) for pages in gone.values())
        lines += ["", f"{dead} page(s) on cards no longer exist under wiki/: take them off with "
                      "remove_pages, or move_pages the page that replaced them."]
    orphans = edit.orphan_drawers(root, board)
    if orphans:
        # Un cassetto senza la sua scheda: lo mostra solo questa vista, e lo
        # toglie ``remove_card`` (v. ``edit.orphan_drawers``).
        lines += ["", "Drawers with no card (remove_card deletes them): "
                      + ", ".join(f"`{cid}`" for cid in orphans)]
    if board["removedCards"]:
        lines += ["", "In the trash (restore_card):"]
        lines += [f"- `{r['card']['id']}` «{r['card']['title']}»" for r in board["removedCards"]]
    if board["suggestions"]:
        lines += ["", "In the envelope (answer_proposal):"]
        lines += [f"- `{s['id']}` {s['kind']} «{s.get('subject') or ''}»: {s['text']}"
                  for s in board["suggestions"]]
    return "\n".join(lines)


def _off_board(board: dict[str, Any], scope: str) -> str:
    """Un'attesa la cui scheda non è sulla bacheca non ha post-it: lo si dice, e come rimediare."""
    if model.scope_state(board, scope) != model.SCOPE_HIDDEN:
        return ""
    if any(r["card"]["id"] == scope for r in board["removedCards"]):
        return " — NOT ON THE BOARD: its card is in the trash (restore_card, or attach_reminder)"
    return " — NOT ON THE BOARD: no such card (attach_reminder puts it on one)"


def _where(scope: str) -> str:
    """Dove sta un promemoria o un cron: su una scheda, o su tutto il quaderno."""
    return "on the whole notebook" if scope == model.NOTEBOOK else f"on `{scope}`"


def _drawer_state(root: Path, card: dict[str, Any], titles: dict[str, str]) -> str:
    if not card["pages"]:
        return "none (no pages to write it from)"
    summary = read_card_summary(root, card["id"])
    if summary is None:
        return "never written"
    now = pages_fingerprint(card["pages"], titles, lambda p: page_text(root, p))
    return "up to date" if summary.get("fingerprint") == now else "out of date"


def _when(rem: dict[str, Any]) -> str:
    when = rem.get("when") or {}
    if when.get("kind") == "date":
        return str(when.get("date", ""))[:7 if when.get("precision") == "month" else 10]
    return "when a value changes"


TOOLS = [BoardTool]
