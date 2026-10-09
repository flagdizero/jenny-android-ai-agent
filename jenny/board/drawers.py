"""Il cassetto di una scheda: come si compone, e quando è da riscrivere.

Lo scrivono in due: il giardiniere, nella passata della bacheca, quando le
pagine di una scheda cambiano (``board_write_summary``,
``jenny/agent/board_tools.py``); e l'editore dei quaderni, subito, quando su
richiesta dell'utente cambia le pagine di una scheda (il tool ``board``,
``jenny/agent/tools/board.py``). Le regole sono le stesse e stanno qui una volta
sola: le sezioni sono quelle dello scheletro, citano solo pagine della scheda,
stanno in due righe, e una sezione che non cambia testo tiene la sua data.

E una sola è l'impronta (:func:`pages_fingerprint`): fino al 09/10/2026 la
passata la calcolava sulle pagine che esistono e lo strumento, quando non
l'aveva in mano, su tutte — una pagina sparita lasciava la scheda «da
riscrivere» per sempre.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from jenny.board import model
from jenny.board.store import read_card_summary, write_card_summary

# Due righe per sezione: la sezione e' un indice, non la pagina.
MAX_SECTION_CHARS = 320
MAX_BRIEF_CHARS = 240


class DrawerError(ValueError):
    """Un cassetto rifiutato per intero: nessuna sezione valida."""


def page_text(root: Path, page: str) -> str:
    try:
        return (root / "wiki" / page).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def pages_fingerprint(pages: Iterable[str], titles: Iterable[str],
                      read: Callable[[str], str]) -> str:
    """L'impronta delle pagine di un cassetto che esistono (*titles*: quelle della wiki)."""
    known = set(titles)
    return model.fingerprint((p, read(p)) for p in pages if p in known)


def as_list(value: Any) -> list[Any]:
    """Una lista, anche se il modello l'ha mandata come stringa JSON."""
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip().startswith("["):
        try:
            parsed = json.loads(value)
        except ValueError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def as_dict(value: Any) -> dict[str, Any]:
    """Un oggetto, anche se il modello l'ha mandato come stringa JSON."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip().startswith("{"):
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def write_drawer(
    root: Path,
    card_id: str,
    *,
    pages: list[str],
    skeleton: list[str],
    brief: Any,
    sections: Any,
    fingerprint: str | None = None,
) -> tuple[list[dict[str, Any]], int, list[str]]:
    """Valida e scrive il cassetto di *card_id*.

    Restituisce le sezioni scritte, quante sono cambiate, e gli scarti.

    Una sezione fuori scheletro, o che cita pagine che non sono della scheda, si
    scarta con la sua ragione; se non ne resta nessuna valida il cassetto non si
    scrive (:class:`DrawerError`). Senza *fingerprint* si calcola qui.
    """
    old = read_card_summary(root, card_id) or {}
    old_by_title = {s["title"]: s for s in old.get("sections") or []}
    stamp = model.now_iso()
    out: list[dict[str, Any]] = []
    problems: list[str] = []
    for raw in as_list(sections):
        item = as_dict(raw)
        title = model.clean_text(item.get("title"), model.MAX_TITLE)
        text = model.clean_block(item.get("text"), MAX_SECTION_CHARS)
        sources = [p for p in as_list(item.get("sources")) if isinstance(p, str)]
        if skeleton and title not in skeleton:
            problems.append(f"`{title}` is not a skeleton section")
            continue
        bad = [p for p in sources if p not in pages]
        if bad or not sources:
            problems.append(f"`{title}` cites pages that are not in this drawer: {bad or '(none)'}")
            continue
        if not text:
            continue
        before = old_by_title.get(title)
        changed = before["changed"] if before and before.get("text") == text and \
            before.get("changed") else stamp
        out.append({"title": title, "text": text, "sources": sources[:8], "changed": changed})
    if problems and not out:
        raise DrawerError("; ".join(problems))
    if fingerprint is None:
        from jenny.board.view import wiki_titles

        fingerprint = pages_fingerprint(pages, wiki_titles(root), lambda p: page_text(root, p))
    write_card_summary(root, card_id, {
        "generated": stamp,
        "fingerprint": fingerprint,
        "brief": model.clean_block(brief, MAX_BRIEF_CHARS),
        "sections": out,
    })
    return out, sum(1 for sec in out if sec["changed"] == stamp), problems

