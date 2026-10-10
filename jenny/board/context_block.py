"""La bacheca nel contesto di Jenny, a ogni turno di un quaderno.

Fino alla bacheca il blocco di progetto iniettava ``wiki/index.md`` (la mappa),
entro duemila caratteri; oltre, il modello vedeva la testa della mappa e basta.
Con la bacheca il blocco dice **cosa conta adesso**: una riga per scheda —
titolo, «in breve», l'attesa più vicina, il segnale — e quante pagine stanno
fuori bacheca. Sta nel tetto anche con dodici schede, perché la bacheca ha un
tetto suo.

Il **cassetto completo** di una scheda entra solo quando la conversazione la
nomina, e non nel prompt di sistema ma nel contesto del turno (accanto all'ora):
il prompt di sistema è il prefisso in cache, e una sezione che dipendesse dal
messaggio lo romperebbe a ogni turno.

``wiki/index.md`` resta: per la ricerca, per l'ordine in cui le pagine entrano
nel blocco, e per i quaderni che una bacheca non ce l'hanno (le wiki di ricerca).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from jenny.board import model
from jenny.board.store import read_board, read_card_summary, read_reminders
from jenny.utils.wiki_paths import is_research_wiki, iter_wiki_pages

# Il tetto del blocco. È lo stesso numero della mappa che sostituisce
# (``_PROJECT_MAP_MAX_CHARS`` in ``jenny/agent/context.py``): il blocco si paga a
# ogni turno, e la bacheca non deve costare più di quel che costava la mappa.
BOARD_BLOCK_MAX_CHARS = 2000

# Il tetto di un cassetto nel contesto del turno.
DRAWER_MAX_CHARS = 2500

# Quanti cassetti al più entrano in un turno, se il messaggio nomina più schede.
MAX_DRAWERS_PER_TURN = 2


def _when(rem: Mapping[str, Any]) -> str:
    when = rem.get("when") or {}
    if when.get("kind") == "date":
        day = str(when.get("date", ""))
        return day[:7] if when.get("precision") == "month" else day
    return f"when {when.get('field')} {when.get('op')} {when.get('value')}"


# Le intestazioni della sezione «aperto» della mappa, nelle lingue in cui il
# giardiniere la scrive: e' li' che mette le contraddizioni che non deve
# risolvere da solo, e con la bacheca la mappa non entra piu' a ogni turno.
_OPEN_HEADING = re.compile(
    r"^#{1,4}\s*(open|open questions|aperto|aperti|aperta|aperte|da decidere|"
    r"questioni aperte|domande aperte)\b",
    re.IGNORECASE,
)
OPEN_MAX_CHARS = 400


def open_questions(root: Path) -> str:
    """La sezione «Open» di ``wiki/index.md``, corta, o ``""``."""
    try:
        text = (root / "wiki" / "index.md").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    out: list[str] = []
    inside = False
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            if inside:
                break
            inside = bool(_OPEN_HEADING.match(line.strip()))
            continue
        if inside and line.strip():
            out.append(line.strip())
    joined = " ".join(out)
    return joined if len(joined) <= OPEN_MAX_CHARS else joined[: OPEN_MAX_CHARS - 1] + "…"


def board_absence(root: Path) -> str:
    """Perche' il quaderno *root* non ha una bacheca da mostrare, o ``""`` se ce l'ha.

    ``"research"`` (un archivio di ricerca: niente bacheca finche' non nasce un
    diario), ``"no_pages"``, ``"proposed"``/``"declined"``/``"later"`` (v.
    :func:`model.first_board_hold`), ``"next_pass"`` (niente la trattiene: la
    propone il giardiniere).

    **Esiste perche' senza bacheca il prompt taceva.** La sezione sulla bacheca
    entrava solo con una bacheca con delle schede, quindi l'unica volta in cui
    Jenny doveva spiegarne l'assenza era l'unica in cui non sapeva cosa fosse. Il
    05/10 su un archivio di ricerca se n'e' inventata la causa — «manca il
    frontmatter delle pagine» — e ha mandato due subagent a scriverlo su 33
    pagine. Le domande sono quelle di ``BoardPass.reason``, nello stesso ordine,
    e senza leggere i titoli delle pagine: si paga a ogni turno.
    """
    board = read_board(root)
    if board is not None and board["cards"]:
        return ""
    if is_research_wiki(root):
        return "research"
    if not iter_wiki_pages(root / "wiki", titles=False):
        return "no_pages"
    if board is not None:
        hold = model.first_board_hold(board)
        if hold is not None:
            return hold
    return "next_pass"


def board_block(
    root: Path, *, alert: Callable[[Mapping[str, Any]], bool] | None = None
) -> str:
    """Il blocco della bacheca per il prompt, o ``""`` se il quaderno non ne ha una con schede."""
    board = read_board(root)
    if board is None or not board["cards"]:
        return ""
    reminders = read_reminders(root)
    titles = dict(iter_wiki_pages(root / "wiki"))
    owners = model.page_owners(board)
    offboard = sum(1 for p in titles if p not in owners)
    lines: list[tuple[str, str, str]] = []
    for card in board["cards"]:
        summary = read_card_summary(root, card["id"]) or {}
        tags: list[str] = []
        problem = alert is not None and any(
            alert(s) for s in [*(card.get("userSections") or []), *_app_sections(card)[:1]]
            if s.get("kind") == "app"
        )
        if problem:
            tags.append("[PROBLEM: an app in its drawer is in alarm, stalled or broken]")
        auto = _auto_tag(root, board, card)
        if auto:
            tags.append(auto)
        nxt = model.next_reminder(reminders, card["id"])
        if nxt is not None:
            soon = " — coming up" if model.reminder_is_soon(nxt) else ""
            tags.append(f"[next: {nxt['text']}, {_when(nxt)}{soon}]")
        brief = summary.get("brief") or ""
        lines.append((card["title"], " ".join(brief.split()), " ".join(tags)))
    footer = (
        f"Off the board: {offboard} page(s) — they exist under `wiki/`, and grep finds them."
        if offboard else ""
    )
    pending = open_questions(root)
    if pending:
        footer = (footer + "\n" if footer else "") + f"Open in the map: {pending}"
    waiting = _envelope(board)
    if waiting:
        footer = (footer + "\n" if footer else "") + waiting
    # Il muro tiene fino a 40 schede, il blocco resta di 2.000 caratteri: prima si
    # accorciano i riassunti, poi le schede che non ci stanno diventano «e altre N»
    # (il cassetto di una scheda entra comunque quando la si nomina). Il piede —
    # le pagine fuori bacheca, le domande aperte — non si taglia mai.
    budget = BOARD_BLOCK_MAX_CHARS - len(footer) - 2
    heads = [f"- **{t}**" + (f" {tag}" if tag else "") for t, _b, tag in lines]
    shown = len(lines)
    more = ""
    while shown and sum(len(h) + 1 for h in heads[:shown]) + len(more) > budget:
        shown -= 1
        more = f"- … and {len(lines) - shown} more card(s): their drawers come in when named."
    room = max(0, budget - sum(len(h) + 4 for h in heads[:shown]) - len(more))
    share = room // max(1, shown)
    rows = []
    for title, brief, tag in lines[:shown]:
        cut = brief if len(brief) <= share else brief[: max(0, share - 1)].rstrip() + "…"
        if share < 8:
            cut = ""
        text = f"- **{title}**" + (f" — {cut}" if cut else "") + (f" {tag}" if tag else "")
        rows.append(text)
    if more:
        rows.append(more)
    out = "\n".join(rows)[:budget]
    if footer:
        out += "\n" + footer
    return out[:BOARD_BLOCK_MAX_CHARS]


def _envelope(board: Mapping[str, Any]) -> str:
    """Le proposte in busta, una riga: la chat le deve conoscere per rispondere.

    Il 09/10/2026 sull'emulatore, a «accetta la proposta nella busta», Jenny ha
    speso sette chiamate a cercarla — un ``grep`` su «envelope», il template del
    giardiniere, ``board.json`` letto a mano — perché il blocco non la diceva.
    Sono al più :data:`model.MAX_PENDING_SUGGESTIONS`, e cambiano solo a una
    passata del giardiniere: il prefisso in cache non ne soffre.
    """
    items = [f"«{s.get('subject') or s['kind']}» ({s['kind']})"
             for s in board.get("suggestions") or []]
    return f"In the envelope, waiting for the user: {'; '.join(items)}." if items else ""


def _auto_tag(root: Path, board: Mapping[str, Any], card: Mapping[str, Any]) -> str:
    """``[auto: Acerello · cron Meteo]``: cio' che nel cassetto gira da solo.

    **Solo i nomi.** Fino al 07/10/2026 c'era anche l'ultimo valore
    («Portafoglio 34390.17€»), cioe' un dato che cambia a ogni giro di un
    lettore in mezzo al prompt di sistema: rompeva la cache del provider da li'
    in giu', e nello stesso prompt il modello trovava quattro totali diversi del
    portafoglio. Il valore vero arriva col cassetto, nel contesto del turno,
    quando la scheda viene nominata.
    """
    bits: list[str] = []
    for section in card.get("userSections") or []:
        if section.get("kind") == "cron":
            bits.append(f"cron {section.get('name') or section.get('job')}")
    for view in _app_views(root, card):
        bits.append(str(view.get("name")))
    return f"[auto: {' · '.join(bits)}]" if bits else ""


def _app_sections(card: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Le app posate su una scheda: la scheda-app stessa, e le sezioni ``app``."""
    out = []
    if isinstance(card.get("app"), dict):
        out.append({"id": model.APP_CARD, "kind": "app", "app": card["app"]["slug"],
                    "params": card["app"].get("params") or {}})
    out.extend(s for s in card.get("userSections") or [] if s.get("kind") == "app")
    return out


def _app_views(root: Path, card: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Il riassunto delle app posate su *card*, per il blocco e per il cassetto."""
    from jenny.board.sections import app_section_view

    workspace = _workspace_of(root)
    if workspace is None:
        return []
    views = []
    for section in _app_sections(card):
        try:
            view = app_section_view(workspace, str(section["app"]), section.get("params") or {})
        except Exception:  # noqa: BLE001 — un'app rotta non toglie il blocco
            continue
        if view.get("state") not in ("missing", "no_section"):
            views.append({**view, "slug": section["app"]})
    return views


def _workspace_of(root: Path) -> Path | None:
    try:
        from jenny.config.paths import get_workspace_path

        return get_workspace_path()
    except Exception:  # noqa: BLE001 — senza workspace impostato, il quaderno sta in <ws>/wikis/
        return root.parent.parent if root.parent.name else None


def named_cards(root: Path, message: str) -> list[dict[str, Any]]:
    """Le schede che il messaggio nomina, per titolo, nell'ordine della bacheca."""
    board = read_board(root)
    if board is None or not message:
        return []
    text = message.casefold()
    out = []
    for card in board["cards"]:
        title = card["title"].casefold().strip()
        if title and re.search(rf"(?<!\w){re.escape(title)}(?!\w)", text):
            out.append(card)
    return out[:MAX_DRAWERS_PER_TURN]


def drawer_text(root: Path, card: Mapping[str, Any]) -> str:
    """Il cassetto di una scheda come testo per il contesto del turno."""
    summary = read_card_summary(root, card["id"]) or {}
    reminders = read_reminders(root)
    parts = [f"Drawer of the card «{card['title']}» (written from the wiki pages it cites):"]
    if summary.get("brief"):
        parts.append(summary["brief"])
    for section in summary.get("sections") or []:
        sources = ", ".join(f"[[{Path(s).stem}]]" for s in section.get("sources") or [])
        parts.append(f"- {section['title']}: {section['text']} ({sources})")
    results = (read_board(root) or {}).get("cronResults") or {}
    for section in card.get("userSections") or []:
        if section.get("kind") == "note":
            parts.append(f"- The user's note: {section.get('text')}")
        elif section.get("kind") == "cron":
            last = results.get(f"{card['id']}/{section.get('id')}") or {}
            # Una citazione datata, non una frase di Jenny al presente: il testo
            # di un giro vecchio («al prossimo messaggio ci guardo») arrivava
            # come un'istruzione da eseguire adesso.
            said = (f", its last result ({last.get('textAt') or last.get('at')}, its own words, "
                    f"may be out of date): «{last['text']}»") if last.get("text") else ""
            parts.append(f"- Notebook cron «{section.get('name') or ''}» (`{section.get('job')}`, "
                         f"cron action=list shows it){said}")
    for view in _app_views(root, card):
        parts.append(_app_line(view))
    for rem in model.open_reminders(reminders):
        if rem.get("card") == card["id"]:
            parts.append(f"- Reminder: {rem['text']}, {_when(rem)}")
    pages = ", ".join(f"[[{Path(p).stem}]]" for p in card.get("pages") or [])
    if pages:
        parts.append(f"Its pages: {pages}")
    return "\n".join(parts)[:DRAWER_MAX_CHARS]


def _app_line(view: Mapping[str, Any]) -> str:
    """Un'app posata nel cassetto: il valore, lo stato, e come cambiarla."""
    state = str(view.get("state") or "")
    status = {
        "trashed": "in the trash",
        "broken": f"broken: {view.get('error') or view.get('lastError') or ''}",
    }.get(state, state)
    value = view.get("value")
    shown = f"{view.get('label') or view.get('field') or 'value'} {value}{view.get('unit') or ''}" \
        if value is not None else "no value yet"
    extras = ", ".join(f"{x['label']} {x['value']}{x.get('unit') or ''}"
                       for x in view.get("extras") or [] if x.get("value") is not None)
    rows = f", {len(view['rows'])} rows" if view.get("rows") else ""
    params = ", ".join(f"{k}={v}" for k, v in (view.get("params") or {}).items())
    return (f"- App «{view.get('name')}» (`{view.get('slug')}`{', ' + params if params else ''}; "
            f"app tool to change it): {shown}{', ' + extras if extras else ''}{rows} at "
            f"{view.get('at') or '—'}; {status}")


def turn_lines(root: Path, message: str) -> list[str]:
    """Le righe da aggiungere al contesto del turno: i cassetti delle schede nominate."""
    return [drawer_text(root, card) for card in named_cards(root, message)]
