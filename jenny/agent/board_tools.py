"""Gli strumenti della passata della bacheca: una cassetta chiusa.

La passata della bacheca (``jenny/agent/board_pass.py``) riceve questi e i tool
di file del giardiniere, e niente altro. Nessuno di questi strumenti ha un modo
di spostare una scheda, di toglierla o di toccare una sezione dell'utente: è il
principio del giardiniere, **una porta nella cassetta è una via d'uscita**. E
dove una regola conta — il tetto delle proposte, un «no» già detto, una bacheca
piena — la fa valere lo strumento, non il prompt.

Ogni scrittura passa dall'imbuto come ``jenny``, quindi un errore di
programmazione che provasse lo stesso a toccare la forma dell'utente sarebbe
rifiutato da :func:`jenny.board.model.ownership_violations`.

Gli strumenti non sono registrati nel loader: li costruisce a mano la passata,
uno stato (:class:`BoardPassState`) condiviso, per un quaderno solo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jenny.agent.tools.base import Tool, tool_parameters
from jenny.agent.tools.schema import (
    ArraySchema,
    ObjectSchema,
    StringSchema,
    tool_parameters_schema,
)
from jenny.board import model
from jenny.board.drawers import DrawerError, as_dict, as_list, page_text, write_drawer
from jenny.board.store import (
    BoardOwnershipError,
    mutate_board,
    mutate_reminders,
    read_board,
    read_card_summary,
    read_reminders,
)


@dataclass
class BoardPassState:
    """Quel che la passata sa e quel che ha fatto, per un quaderno.

    *stale* sono le schede il cui cassetto va riscritto (il codice l'ha deciso
    dalle impronte); *orphans* le pagine fuori bacheca; *remove_candidates* le
    schede ferme da abbastanza da poterne proporre la messa via. I contatori in
    fondo li legge il codice per scrivere «dall'ultima volta».
    """

    root: Path
    stale: set[str] = field(default_factory=set)
    orphans: set[str] = field(default_factory=set)
    remove_candidates: set[str] = field(default_factory=set)
    apps_with_section: dict[str, dict[str, Any]] = field(default_factory=dict)
    fingerprints: dict[str, str] = field(default_factory=dict)
    migration: bool = False
    proposed_cards: dict[str, list[str]] = field(default_factory=dict)
    proposed_skeleton: list[str] = field(default_factory=list)
    # I cassetti ancora da scrivere, in ordine: ``(id, titolo, pagine)``. Si
    # scrivono **uno alla volta** — v. :func:`next_drawer`.
    queue: list[tuple[str, str, list[str]]] = field(default_factory=list)

    assigned: list[tuple[str, str]] = field(default_factory=list)
    summaries: dict[str, int] = field(default_factory=dict)
    reminders: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    resolved: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)

    def refuse(self, text: str) -> str:
        self.refused.append(text)
        return f"Refused: {text}"


class _BoardTool(Tool):
    _scopes: set[str] = set()

    def __init__(self, state: BoardPassState) -> None:
        self.state = state

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return False

    @property
    def read_only(self) -> bool:
        return False

    @property
    def concurrency_safe(self) -> bool:
        return False


@tool_parameters(tool_parameters_schema(
    page=StringSchema("The page path relative to wiki/, exactly as listed (e.g. `travaso-acero.md`)."),
    card=StringSchema("The id of the card whose drawer the page belongs in."),
    required=["page", "card"],
))
class BoardAssignPageTool(_BoardTool):
    """Mette una pagina fuori bacheca nel cassetto di una scheda."""

    @property
    def name(self) -> str:
        return "board_assign_page"

    @property
    def description(self) -> str:
        return (
            "Put a page that is in no drawer into the drawer of an existing card, when the page "
            "is clearly about that card's subject. A page belongs to one card at most; a page "
            "already in a drawer cannot be moved. Leave a page off the board when it does not "
            "clearly belong anywhere."
        )

    async def execute(self, page: str = "", card: str = "", **_: Any) -> str:
        st = self.state
        if page not in st.orphans:
            return st.refuse(f"`{page}` is not a page off the board")

        def apply(board: dict[str, Any]) -> None:
            target = model.card_by_id(board, card)
            if target is None:
                raise LookupError(card)
            if page in model.page_owners(board):
                raise ValueError(page)
            target["pages"].append(page)

        try:
            mutate_board(st.root, apply, actor=model.JENNY)
        except LookupError:
            return st.refuse(f"there is no card `{card}`")
        except ValueError:
            return st.refuse(f"`{page}` is already in a drawer")
        st.orphans.discard(page)
        st.stale.add(card)
        st.assigned.append((page, card))
        return f"`{page}` is now in the drawer of `{card}`; that drawer will be rewritten."


_SECTION = ObjectSchema(
    title=StringSchema("One of the notebook's skeleton sections, spelled exactly as given."),
    text=StringSchema("At most two short sentences: what the pages say about this, now."
                      " Plain prose, no [[links]]: the drawer lists the pages under it."),
    sources=ArraySchema(StringSchema("A page of this card, relative to wiki/."),
                        description="The pages this section comes from, most relevant first."),
    required=["title", "text", "sources"],
)


@tool_parameters(tool_parameters_schema(
    card=StringSchema("The card id."),
    brief=StringSchema("One or two sentences: the state of this subject right now, in plain "
                       "prose."),
    sections=ArraySchema(_SECTION, description="The skeleton sections that have material."),
    required=["card", "brief", "sections"],
))
class BoardWriteSummaryTool(_BoardTool):
    """Scrive il cassetto di una scheda: «in breve» e le sezioni dello scheletro."""

    @property
    def name(self) -> str:
        return "board_write_summary"

    @property
    def description(self) -> str:
        return (
            "Write the drawer of a card listed as needing it: a brief, and one entry per skeleton "
            "section that its pages have material for — two short sentences each, with the pages "
            "it comes from. Leave out a section with no material rather than inventing one. "
            "Copy a section unchanged when its pages did not change what it says."
        )

    async def execute(self, card: str = "", brief: str = "", sections: Any = None,
                      **_: Any) -> str:
        st = self.state
        if card not in st.stale and card not in st.proposed_cards:
            return st.refuse(f"the drawer of `{card}` does not need rewriting")
        board = read_board(st.root) or model.empty_board()
        existing = model.card_by_id(board, card)
        pages = existing["pages"] if existing else st.proposed_cards.get(card, [])
        try:
            out, rewritten, problems = write_drawer(
                st.root, card, pages=pages, skeleton=board["skeleton"] or st.proposed_skeleton,
                brief=brief, sections=sections, fingerprint=st.fingerprints.get(card),
            )
        except DrawerError as exc:
            return st.refuse(str(exc))
        st.stale.discard(card)
        st.summaries[card] = rewritten
        note = f" Skipped: {'; '.join(problems)}." if problems else ""
        done = f"Drawer of `{card}` written: {len(out)} sections, {rewritten} changed.{note}"
        queued = any(q[0] == card for q in st.queue)
        st.queue = [q for q in st.queue if q[0] != card]
        if not queued:
            return done
        following = next_drawer(st)
        return f"{done}\n\n{following}" if following else f"{done} That was the last drawer."


@tool_parameters(tool_parameters_schema(
    card=StringSchema("The card id the reminder is about."),
    text=StringSchema("What to do, in a few words, in the user's language."),
    date=StringSchema("When: YYYY-MM-DD. For something like 'around March', the first of the month."),
    precision=StringSchema("`day` for a precise date, `month` for 'in March'.", enum=["day", "month"]),
    why=StringSchema("The wiki page (relative to wiki/) that explains why, if there is one."),
    source=StringSchema("Where it was said: the journal line (`raw/journal/YYYYMMDD.md#HH:MM`) or page."),
    required=["card", "text", "date"],
))
class BoardAddReminderTool(_BoardTool):
    """Aggiunge un'attesa a data, estratta dal diario o da una pagina."""

    @property
    def name(self) -> str:
        return "board_add_reminder"

    @property
    def description(self) -> str:
        return (
            "Record something to do with a date — a decided repotting 'around March', a "
            "fertiliser 'on the 10th' — as a reminder on a card. Only for things that were "
            "decided or planned and have a date; never for something already done. A reminder "
            "already on the list is not added twice."
        )

    async def execute(self, card: str = "", text: str = "", date: str = "",
                      precision: str = "day", why: str = "", source: str = "", **_: Any) -> str:
        st = self.state
        board = read_board(st.root) or model.empty_board()
        if model.card_by_id(board, card) is None and card not in st.proposed_cards:
            return st.refuse(f"there is no card `{card}`")
        when = model._normalize_when({"kind": "date", "date": date, "precision": precision})
        clean = model.clean_text(text, model.MAX_TITLE * 2)
        if when is None or not clean:
            return st.refuse("a reminder needs a text and a date written YYYY-MM-DD")
        rid_out: list[str] = []
        dup: list[str] = []

        def apply(rems: dict[str, Any]) -> bool | None:
            key = (card, " ".join(clean.lower().split()))
            for rem in rems["reminders"]:
                if (rem["card"], " ".join(rem["text"].lower().split())) == key:
                    dup.append(rem["id"])
                    return False
            rid = model.unique_id(f"r-{model.slugify(clean, fallback='reminder')}"[:40],
                                  [r["id"] for r in rems["reminders"]])
            rems["reminders"].append({
                "id": rid, "card": card, "text": clean, "when": when,
                "why": why if isinstance(why, str) else "",
                "source": source if isinstance(source, str) else "",
                "by": model.JENNY, "state": "open", "created": model.now_iso(),
            })
            rid_out.append(rid)
            return None

        mutate_reminders(st.root, apply, actor=model.JENNY)
        if dup:
            return f"Already on the list (`{dup[0]}`): not added again."
        st.reminders.append(rid_out[0])
        return f"Reminder `{rid_out[0]}` added to `{card}`."


@tool_parameters(tool_parameters_schema(
    kind=StringSchema(
        "What to propose.",
        enum=["add_card", "remove_card", "add_connection", "attach_page", "add_app_card"],
    ),
    text=StringSchema("The proposal as you would say it to the user, in their language: one or two sentences."),
    subject=StringSchema("add_card: the title of the new card."),
    glyph=StringSchema("add_card: an icon name from the list in the instructions."),
    pages=ArraySchema(StringSchema(""), description="add_card: the pages off the board it would hold."),
    card=StringSchema("remove_card / attach_page: the card id. add_app_card (optional): the "
                      "card the app belongs with — the two get tied by a thread."),
    a=StringSchema("add_connection: one card id."),
    b=StringSchema("add_connection: the other card id."),
    label=StringSchema("add_connection: why they are tied, in a few words."),
    page=StringSchema("attach_page: the page relative to wiki/."),
    app=StringSchema("add_app_card: the app slug — a card that IS the app, its value on the "
                     "polaroid, a tap opens it."),
    params=ObjectSchema(description="add_app_card: the parameters the app's summary asks for.",
                        additional_properties=True),
    required=["kind", "text"],
))
class BoardSuggestTool(_BoardTool):
    """Mette una proposta nella busta. Le regole le fa valere lo strumento."""

    @property
    def name(self) -> str:
        return "board_suggest"

    @property
    def description(self) -> str:
        return (
            f"Put a proposal in the envelope: the user decides. At most "
            f"{model.MAX_SUGGESTIONS_PER_PASS} per pass; nothing the user already declined; "
            "nothing already waiting. When the board is full, propose putting something away "
            "before proposing to add."
        )

    async def execute(self, kind: str = "", text: str = "", **kw: Any) -> str:
        st = self.state
        if len(st.suggestions) >= model.MAX_SUGGESTIONS_PER_PASS:
            return st.refuse(f"already {model.MAX_SUGGESTIONS_PER_PASS} proposals this pass")
        board = read_board(st.root)
        if board is None:
            return st.refuse("this notebook has no board yet")
        reminders = read_reminders(st.root)
        if len(board["suggestions"]) >= model.MAX_PENDING_SUGGESTIONS:
            return st.refuse("the envelope is already full")
        clean_text = model.clean_block(text, model.MAX_TEXT)
        if not clean_text:
            return st.refuse("a proposal needs its sentence")
        owners = model.page_owners(board)
        spec: dict[str, Any] = {"kind": kind, "text": clean_text, "created": model.now_iso()}
        if kind == "add_card":
            subject = model.clean_text(kw.get("subject"), model.MAX_TITLE)
            pages = [p for p in as_list(kw.get("pages")) if isinstance(p, str)]
            if model.is_full(board, reminders):
                return st.refuse("the board is full: propose putting a card away first")
            if not subject or len(pages) < model.NEW_CARD_MIN_PAGES:
                return st.refuse(f"a new card needs a subject and at least "
                                 f"{model.NEW_CARD_MIN_PAGES} pages off the board")
            if any(p not in st.orphans or p in owners for p in pages):
                return st.refuse("every page of a new card must be off the board")
            glyph = kw.get("glyph") if kw.get("glyph") in model.GLYPHS else ""
            spec.update(subject=subject, pages=pages, glyph=glyph)
        elif kind == "remove_card":
            card = model.card_by_id(board, str(kw.get("card") or ""))
            if card is None or card["id"] not in st.remove_candidates:
                return st.refuse("only a card listed as still for long can be proposed for putting away")
            spec.update(subject=card["title"], card=card["id"], pages=list(card["pages"]))
        elif kind == "add_connection":
            a, b = str(kw.get("a") or ""), str(kw.get("b") or "")
            ca, cb = model.card_by_id(board, a), model.card_by_id(board, b)
            if ca is None or cb is None or a == b:
                return st.refuse("a thread ties two different cards on the board")
            if len(board["connections"]) >= model.MAX_CONNECTIONS:
                return st.refuse("the board already has as many threads as it can show")
            if any({c["a"], c["b"]} == {a, b} for c in board["connections"]):
                return st.refuse("those two cards are already tied")
            label = model.clean_text(kw.get("label"), model.MAX_LABEL)
            if not label:
                return st.refuse("a thread needs its reason, in a few words")
            spec.update(subject=f"{ca['title']} — {cb['title']}", a=a, b=b, label=label)
        elif kind == "attach_page":
            card = model.card_by_id(board, str(kw.get("card") or ""))
            page = str(kw.get("page") or "")
            if card is None or page not in st.orphans:
                return st.refuse("attach a page off the board to an existing card")
            spec.update(subject=card["title"], card=card["id"], page=page, pages=[page])
        elif kind == "add_app_card":
            app = str(kw.get("app") or "")
            if app not in st.apps_with_section:
                return st.refuse("an app card needs an approved app with a summary")
            if model.is_full(board, reminders):
                return st.refuse("the board is full")
            if any((c.get("app") or {}).get("slug") == app for c in board["cards"]):
                return st.refuse("that app is already a card on this board")
            params = {k: v for k, v in as_dict(kw.get("params")).items()
                      if isinstance(v, (str, int, float, bool))}
            spec.update(subject=str(kw.get("subject") or (st.apps_with_section[app] or {}).get("name")
                                    or app),
                        app=app, params=params)
            if kw.get("card"):
                card = model.card_by_id(board, str(kw["card"]))
                if card is None:
                    return st.refuse("the card to tie the app to is not on the board")
                spec.update(card=card["id"])
        else:
            return st.refuse(f"unknown kind `{kind}`")
        subject_key = (kind, spec.get("subject", "").lower())
        for pending in board["suggestions"]:
            if (pending["kind"], pending.get("subject", "").lower()) == subject_key:
                return st.refuse("the same proposal is already in the envelope")
        for no in board["declined"]:
            if (no.get("kind"), str(no.get("subject", "")).lower()) != subject_key:
                continue
            grown = len(spec.get("pages") or []) >= model.DECLINE_REGROWTH * max(
                1, int(no.get("pagesAtDecline") or 0))
            if not (kind == "add_card" and grown):
                return st.refuse("the user already said no to this")
        for later in board["later"]:
            if (later.get("kind"), str(later.get("subject", "")).lower()) == subject_key and \
                    int(later.get("untilPass") or 0) > board["passes"]:
                return st.refuse("the user asked to hear about this later, not yet")
        sid_holder: list[str] = []

        def apply(b: dict[str, Any]) -> None:
            sid = model.unique_id(f"sg-{model.slugify(spec.get('subject') or kind)}"[:40],
                                  [s["id"] for s in b["suggestions"]])
            sid_holder.append(sid)
            b["suggestions"].append({"id": sid, **spec})

        try:
            mutate_board(st.root, apply, actor=model.JENNY)
        except BoardOwnershipError as exc:  # pragma: no cover — difesa in profondita'
            return st.refuse(str(exc))
        st.suggestions.append(sid_holder[0])
        return f"Proposal `{sid_holder[0]}` is in the envelope."


@tool_parameters(tool_parameters_schema(
    id=StringSchema("The correction's id, as listed."),
    resolution=StringSchema("One sentence for the record: what you changed, and in which page."),
    required=["id", "resolution"],
))
class BoardResolveCorrectionTool(_BoardTool):
    """Chiude una correzione dalla bacheca, dopo averla applicata alla wiki."""

    @property
    def name(self) -> str:
        return "board_resolve_correction"

    @property
    def description(self) -> str:
        return (
            "Close a correction the user made from the board, once you have applied it to the "
            "wiki page(s). Call it only after the page says what the user said."
        )

    async def execute(self, id: str = "", resolution: str = "", **_: Any) -> str:  # noqa: A002
        from jenny.board.corrections import resolve_correction

        st = self.state
        if not resolve_correction(st.root, id, resolution):
            return st.refuse(f"no open correction `{id}`")
        st.resolved.append(id)
        return f"Correction `{id}` closed."


_PROPOSED_CARD = ObjectSchema(
    title=StringSchema("The subject, as the user would name it."),
    glyph=StringSchema("An icon name from the list in the instructions."),
    pages=ArraySchema(StringSchema(""), description="The pages (relative to wiki/) of its drawer."),
    required=["title", "pages"],
)


@tool_parameters(tool_parameters_schema(
    skeleton=ArraySchema(StringSchema(""), description="The drawer sections, in order.",
                         max_items=model.MAX_SKELETON),
    cards=ArraySchema(_PROPOSED_CARD, description="The first cards.",
                      max_items=model.MAX_INITIAL_CARDS),
    text=StringSchema("One or two sentences to the user, in their language, presenting the board."),
    required=["skeleton", "cards", "text"],
))
class BoardProposeBoardTool(_BoardTool):
    """Propone la prima bacheca di un quaderno che non ne ha una."""

    @property
    def name(self) -> str:
        return "board_propose_board"

    @property
    def description(self) -> str:
        return (
            "Propose this notebook's first board: the skeleton of drawer sections (the same for "
            "every card, in order) and the first cards, each with the pages of its drawer. It "
            "arrives as one proposal the user accepts or not. Returns the id each card will have."
        )

    async def execute(self, skeleton: Any = None, cards: Any = None, text: str = "",
                      **_: Any) -> str:
        st = self.state
        if not st.migration or st.proposed_cards:
            return st.refuse("this notebook already has a board or a proposal for one")
        sk = [s for s in (model.clean_text(x, model.MAX_TITLE) for x in as_list(skeleton)) if s]
        sk = list(dict.fromkeys(sk))[:model.MAX_SKELETON]
        # Lo scheletro che l'utente ha chiesto a Jenny (``set_skeleton``, a
        # bacheca ancora vuota) vince: e' suo.
        current = read_board(st.root)
        if current and current["skeleton"]:
            sk = list(current["skeleton"])
        specs: list[dict[str, Any]] = []
        used: set[str] = set()
        taken_ids: set[str] = set()
        for raw in as_list(cards)[:model.MAX_INITIAL_CARDS]:
            item = as_dict(raw)
            title = model.clean_text(item.get("title"), model.MAX_TITLE)
            pages = [p for p in as_list(item.get("pages"))
                     if isinstance(p, str) and p in st.orphans and p not in used]
            if not title or not pages:
                continue
            used.update(pages)
            cid = model.unique_id(model.slugify(title), taken_ids)
            taken_ids.add(cid)
            glyph = item.get("glyph") if item.get("glyph") in model.GLYPHS else ""
            specs.append({"id": cid, "title": title, "glyph": glyph, "pages": pages})
        if not sk or not specs:
            return st.refuse("a board needs a skeleton and at least one card with its pages")
        stamp = model.now_iso()

        def apply(board: dict[str, Any]) -> None:
            board["suggestions"].append({
                "id": "sg-board", "kind": "initial_board", "subject": "",
                "text": model.clean_block(text, model.MAX_TEXT), "created": stamp,
                "skeleton": sk, "cards": specs,
            })

        mutate_board(st.root, apply, actor=model.JENNY)
        st.proposed_skeleton = sk
        st.proposed_cards = {s["id"]: s["pages"] for s in specs}
        st.queue = [(s["id"], s["title"], list(s["pages"])) for s in specs]
        st.suggestions.append("sg-board")
        ids = ", ".join(f"`{s['id']}` ({s['title']})" for s in specs)
        return (f"Board proposed with {len(specs)} cards: {ids}. Now write the drawers **one at "
                "a time**, in this order, with board_write_summary: each drawer you write hands "
                "you the pages of the next one. Write each from the pages shown with it, as soon "
                "as you have them.\n\n" + next_drawer(st))


# Un cassetto alla volta. La proposta, e poi ogni cassetto scritto, consegnano
# le pagine del cassetto **successivo** e di quello solo.
#
# Prima la proposta consegnava le pagine di tutti i cassetti insieme, dentro un
# tetto comune di 30.000 caratteri: su un quaderno vero le pagine oltre il tetto
# arrivavano come «non mostrata», il modello le leggeva una a una, e il contesto
# — che il governor taglia dalla testa — le perdeva mentre leggeva le altre.
# Visto il 04/10/2026 (44 pagine, 130 letture, nessun cassetto) e di nuovo il
# 05/10 sul Titan (9 schede, 107 letture in 4 minuti, nessun cassetto, fermato
# al giro 170 di 200). Con un cassetto per volta il contesto contiene un cassetto
# solo, e quello che il taglio butta e' gia' scritto: non dipende da quanto e'
# grande la finestra.
DRAWER_PAGE_CHARS = 2500
DRAWER_CARD_CHARS = 9000


def drawer_pages(root: Path, pages: list[str]) -> str:
    """Il testo delle pagine di un cassetto, dentro il tetto del cassetto."""
    parts = []
    used = 0
    for page in pages:
        room = min(DRAWER_PAGE_CHARS, DRAWER_CARD_CHARS - used)
        if room <= 200:
            parts.append(f"`{page}` — _(not shown: over the budget of this drawer; read it with "
                         "read_file if you need it)_")
            continue
        body = page_text(root, page)
        cut = body[:room]
        note = "\n_(cut here)_" if len(body) > room else ""
        fence = "`" * max(4, max((len(m) for m in re.findall(r"`+", cut)), default=0) + 1)
        parts.append(f"`{page}`\n\n{fence}markdown\n{cut}\n{fence}{note}")
        used += len(cut)
    return "\n\n".join(parts) or "_(no pages)_"


def next_drawer(state: BoardPassState) -> str:
    """Il prossimo cassetto da scrivere — com'e' adesso e le sue pagine — o ``""``."""
    if not state.queue:
        return ""
    card, title, pages = state.queue[0]
    after = len(state.queue) - 1
    more = f"{after} more after this one" if after else "the last one"
    old = read_card_summary(state.root, card) or {}
    current = "\n".join(f"- **{s['title']}**: {s['text']} (from {', '.join(s['sources'])})"
                        for s in old.get("sections") or []) or "_(never written)_"
    return (f"### Next drawer: `{card}` — {title} ({more})\n\nThe drawer now:\n{current}\n\n"
            + drawer_pages(state.root, pages))


def build_board_tools(state: BoardPassState) -> list[Tool]:
    # ``parameters`` lo inietta ``@tool_parameters``, e il type checker non lo
    # vede: per lui queste classi restano astratte.
    tools: list[Tool] = [
        BoardAssignPageTool(state),  # pyright: ignore[reportAbstractUsage]
        BoardWriteSummaryTool(state),  # pyright: ignore[reportAbstractUsage]
        BoardAddReminderTool(state),  # pyright: ignore[reportAbstractUsage]
        BoardSuggestTool(state),  # pyright: ignore[reportAbstractUsage]
        BoardResolveCorrectionTool(state),  # pyright: ignore[reportAbstractUsage]
    ]
    if state.migration:
        tools.append(BoardProposeBoardTool(state))  # pyright: ignore[reportAbstractUsage]
    return tools
