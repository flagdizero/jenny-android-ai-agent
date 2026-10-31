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
from jenny.board.cron_link import notebook_jobs_on_disk, result_of
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

# Le foto di un cassetto nel turno: le più recenti, e un tetto alla riga, che
# viene prima delle pagine (le pagine Jenny le ritrova da sola, le foto no).
DRAWER_PHOTOS = 6
PHOTOS_LINE_MAX_CHARS = 700

# Le aggiunte dell'utente alla bacheca dall'ultimo messaggio: al più tre.
MAX_RECENT = 3

# Una parola del titolo aggancia la scheda se è lunga almeno così.
TITLE_WORD_MIN = 4


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


def notebook_line(root: Path) -> str:
    """Le cose di tutto il quaderno, non legate a una scheda, in una riga per il prompt.

    ``[auto: cron Gelo] [next: Porta dentro le piante, 2026-12-01]``, o ``""``. Solo
    i nomi dei cron e l'attesa più vicina, come il blocco della bacheca: un dato
    che cambia a ogni giro romperebbe la cache del provider. C'è anche su una
    bacheca senza schede, dove il blocco della bacheca tace.
    """
    if read_board(root) is None:
        return ""
    tags = []
    names = [job["name"] or job["id"] for job in notebook_jobs_on_disk(root.name)
             if job["scope"] == model.NOTEBOOK]
    if names:
        tags.append(f"[auto: {' · '.join(f'cron {n}' for n in names)}]")
    nxt = model.next_reminder(read_reminders(root), model.NOTEBOOK)
    if nxt is not None:
        soon = " — coming up" if model.reminder_is_soon(nxt) else ""
        tags.append(f"[next: {nxt['text']}, {_when(nxt)}{soon}]")
    return " ".join(tags)


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
        auto = _auto_tag(root, card)
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


def _auto_tag(root: Path, card: Mapping[str, Any]) -> str:
    """``[auto: Acerello · cron Meteo]``: cio' che nel cassetto gira da solo.

    **Solo i nomi.** Fino al 07/10/2026 c'era anche l'ultimo valore
    («Portafoglio 34390.17€»), cioe' un dato che cambia a ogni giro di un
    lettore in mezzo al prompt di sistema: rompeva la cache del provider da li'
    in giu', e nello stesso prompt il modello trovava quattro totali diversi del
    portafoglio. Il valore vero arriva col cassetto, nel contesto del turno,
    quando la scheda viene nominata.
    """
    bits = [f"cron {job['name'] or job['id']}"
            for job in notebook_jobs_on_disk(root.name) if job["scope"] == card["id"]]
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


def _says(text: str, words: str) -> bool:
    return bool(words) and re.search(rf"(?<!\w){re.escape(words)}(?!\w)", text) is not None


def named_cards(root: Path, message: str) -> list[dict[str, Any]]:
    """Le schede che il messaggio nomina, nell'ordine della bacheca.

    Il titolo intero, o se no **una parola del titolo** che sta in quello di una
    scheda sola: «acero» nomina «Acero giapponese». Fino al 14/10/2026 valeva
    solo il titolo intero, e «ho messo una foto dell'acero» non portava il
    cassetto. Una parola che sta in due titoli («vaso») non nomina nessuna delle
    due; una corta (sotto ``TITLE_WORD_MIN``) nemmeno.
    """
    board = read_board(root)
    if board is None or not message:
        return []
    text = message.casefold()
    words: dict[str, set[str]] = {}
    for card in board["cards"]:
        for word in re.findall(r"\w+", card["title"].casefold()):
            if len(word) >= TITLE_WORD_MIN:
                words.setdefault(word, set()).add(card["id"])
    out = []
    for card in board["cards"]:
        title = card["title"].casefold().strip()
        if _says(text, title) or any(
            len(words.get(word, ())) == 1 and _says(text, word)
            for word in re.findall(r"\w+", title)
        ):
            out.append(card)
    return out[:MAX_DRAWERS_PER_TURN]


def _cron_lines(root: Path, scope: str) -> list[str]:
    """I cron del quaderno con ambito *scope*, una riga ciascuno, col loro ultimo esito."""
    results = (read_board(root) or {}).get("cronResults") or {}
    lines = []
    for job in notebook_jobs_on_disk(root.name):
        if job["scope"] != scope:
            continue
        last = result_of(results, job["id"], job["link"])
        # Una citazione datata, non una frase di Jenny al presente: il testo
        # di un giro vecchio («al prossimo messaggio ci guardo») arrivava
        # come un'istruzione da eseguire adesso.
        said = (f", its last result ({last.get('textAt') or last.get('at')}, its own words, "
                f"may be out of date): «{last['text']}»") if last.get("text") else ""
        lines.append(f"- Notebook cron «{job['name']}» (`{job['id']}`, "
                     f"cron action=list shows it){said}")
    return lines


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
    for section in card.get("userSections") or []:
        if section.get("kind") == "note":
            parts.append(f"- The user's note: {section.get('text')}")
    parts.extend(_cron_lines(root, card["id"]))
    for view in _app_views(root, card):
        parts.append(_app_line(view))
    for rem in model.open_reminders(reminders):
        if rem.get("card") == card["id"]:
            parts.append(f"- Reminder: {rem['text']}, {_when(rem)}")
    photos = _photos_line(root, card)
    if photos:
        parts.append(photos)
    pages = ", ".join(f"[[{Path(p).stem}]]" for p in card.get("pages") or [])
    if pages:
        parts.append(f"Its pages: {pages}")
    return "\n".join(parts)[:DRAWER_MAX_CHARS]


def _day(iso: Any) -> str:
    stamp = model.parse_iso(iso)
    return stamp.strftime("%d %b") if stamp else ""


def _photo_words(section: Mapping[str, Any]) -> str:
    caption = section.get("caption") or ""
    note = f"«{caption}»" if caption else "(no note)"
    return f"{_day(section.get('created'))} {note} {section.get('path')}".strip()


def _photos_line(root: Path, card: Mapping[str, Any]) -> str:
    """Le foto del cassetto, dalla più recente: data, nota e percorso.

    Fino al 14/10/2026 il cassetto nel turno le saltava: «ho messo una foto
    dell'acero» costava otto chiamate (``uploads/``, grep, ``board.json``) prima
    di trovarla.
    """
    photos = sorted(
        (s for s in card.get("userSections") or []
         if s.get("kind") == "photo" and (root / str(s.get("path"))).is_file()),
        key=lambda s: s.get("created") or "", reverse=True,
    )
    if not photos:
        return ""
    shown = "; ".join(_photo_words(s) for s in photos[:DRAWER_PHOTOS])
    more = f"; +{len(photos) - DRAWER_PHOTOS} more" if len(photos) > DRAWER_PHOTOS else ""
    line = (f"- Photos the user pinned ({len(photos)}, newest first): {shown}{more}"
            f" — read_file a path to look at it.")
    return line if len(line) <= PHOTOS_LINE_MAX_CHARS else line[:PHOTOS_LINE_MAX_CHARS - 1] + "…"


def recent_line(root: Path, since: Any) -> str:
    """Quel che l'utente ha appuntato sulla bacheca dopo *since* (l'ultimo
    messaggio della conversazione): foto e note, le più recenti.

    Vale anche se il messaggio non nomina nessuna scheda: «l'hai vista la foto?»
    è proprio il caso in cui Jenny deve sapere dov'è.
    """
    after = model.parse_iso(since)
    board = read_board(root)
    if after is None or board is None:
        return ""
    found = []
    for card in board["cards"]:
        for section in card.get("userSections") or []:
            if section.get("kind") not in ("photo", "note"):
                continue
            if section["kind"] == "photo" and not (root / str(section.get("path"))).is_file():
                continue
            created = model.parse_iso(section.get("created"))
            if created is not None and created > after:
                found.append((created, card, section))
    if not found:
        return ""
    found.sort(key=lambda item: item[0], reverse=True)
    bits = []
    for _created, card, section in found[:MAX_RECENT]:
        if section["kind"] == "photo":
            caption = section.get("caption") or ""
            note = f", their note: «{caption}»" if caption else ", no note"
            bits.append(f"a photo on «{card['title']}» ({section.get('path')}{note})")
        else:
            bits.append(f"a note on «{card['title']}»: «{section.get('text')}»")
    # Le note, e la nota di una foto, sono gia' righe [said] del diario: sul
    # jenny_s22 (14/10/2026) Jenny le ha riscritte tutte e tre con journal_append.
    return ("Since the last message the user pinned on the board: " + "; ".join(bits)
            + ". Their notes are already in the notebook's journal: do not write them there"
            " again.")


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


def turn_lines(root: Path, message: str, *, since: Any = None) -> list[str]:
    """Le righe da aggiungere al contesto del turno: quel che l'utente ha
    appuntato sulla bacheca dall'ultimo messaggio, e i cassetti delle schede
    nominate."""
    recent = recent_line(root, since) if since else ""
    return [*([recent] if recent else []),
            *(drawer_text(root, card) for card in named_cards(root, message))]
