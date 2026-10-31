"""Le correzioni dal cassetto: richieste, non scritture.

«Correggi» su una sezione del cassetto **non scrive il riassunto**: il riassunto
lo scrive Jenny dalla wiki, e la wiki è la fonte di verità. La correzione
diventa una segnalazione (un audit, la stessa cosa di «Segnala» nel lettore)
sulla prima pagina da cui viene la sezione, con ``source: board``. La passata
della bacheca la applica alla wiki e la chiude; poi la sezione si rigenera
dalla pagina, che ora contiene la correzione. Così le fonti di verità restano
una.

**L'ancora è la pagina intera.** Un audit del lettore è ancorato a un punto
preciso del markdown; una riga di riassunto invece è una sintesi, e non
corrisponde a un pezzo esatto di nessuna pagina. ``target_lines`` copre tutta la
pagina, e ``anchor_text`` è la riga del riassunto com'era (una riga sola: il
lint legge il frontmatter riga per riga, e un testo su più righe con dentro
``id:`` o ``target:`` gli sostituirebbe le chiavi).
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.utils.path import atomic_write

_FRONT_RE = re.compile(r"\A---\n([\s\S]*?)\n---\n?")
_RESOLUTION_PLACEHOLDER = "<!-- filled in when the audit is processed -->"


def _first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return " ".join(line.split())[:300]
    return ""


def create_correction(
    project_root: Path,
    *,
    card_id: str,
    card_title: str,
    section: str,
    sources: list[str],
    old_text: str,
    correction: str,
    author: str = "user",
) -> dict[str, Any]:
    """Apre la segnalazione di una correzione fatta dal cassetto.

    *sources* sono le pagine da cui viene la sezione: la segnalazione va sulla
    prima e cita le altre, e decide il giardiniere dove scrivere.
    Solleva ``FileNotFoundError`` se la prima pagina non c'è più.
    """
    from jenny.webui.audit import AuditEntry, filename_for, make_id, to_markdown

    if not sources:
        raise FileNotFoundError("the section has no source page")
    target = sources[0]
    page = project_root / "wiki" / target
    if not page.is_file():
        raise FileNotFoundError(f"target file not found: {target}")
    lines = max(1, page.read_text(encoding="utf-8").count("\n") + 1)
    audit_id = make_id()
    others = "".join(f"\n- `{p}`" for p in sources[1:])
    body = (
        "# Comment\n\n"
        f"A correction made from the board, in the drawer of **{card_title}**, section "
        f"**{section}**. The summary is written from the wiki, so the fix belongs in the "
        "wiki: apply it to the page(s) this section comes from, and the summary will be "
        "rewritten from them.\n\n"
        f"The summary said:\n\n> {' '.join(old_text.split())}\n\n"
        f"The user says:\n\n> {' '.join(correction.split())}\n"
        + (f"\nThe section also draws on:{others}\n" if others else "")
        + f"\n# Resolution\n\n{_RESOLUTION_PLACEHOLDER}\n"
    )
    entry = AuditEntry(
        id=audit_id,
        target=target,
        target_lines=(1, lines),
        anchor_before="",
        anchor_text=_first_line(old_text) or section,
        anchor_after="",
        author=author,
        source="board",
        created=datetime.now().isoformat(timespec="seconds"),
        status="open",
        body=body,
        extra={
            "board_card": card_id,
            "board_section": section,
            "board_text": " ".join(correction.split())[:1000],
            "board_sources": list(sources),
        },
    )
    out = project_root / "audit" / filename_for(audit_id, f"board {card_id} {section}")
    atomic_write(out, to_markdown(entry))
    return {"id": audit_id, "path": out.relative_to(project_root).as_posix(), "target": target}


def _read_front(path: Path) -> dict[str, Any] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    match = _FRONT_RE.match(text)
    if not match:
        return None
    try:
        import yaml

        data = yaml.safe_load(match.group(1))
    except Exception:  # noqa: BLE001 — un audit illeggibile si salta
        return None
    return data if isinstance(data, dict) else None


def iter_board_audits(project_root: Path) -> list[tuple[Path, dict[str, Any]]]:
    """Gli audit nati dalla bacheca, aperti e chiusi, in ordine di file."""
    out: list[tuple[Path, dict[str, Any]]] = []
    for folder in (project_root / "audit", project_root / "audit" / "resolved"):
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.md")):
            front = _read_front(path)
            if front and front.get("source") == "board":
                out.append((path, front))
    return out


def list_corrections(project_root: Path, card_id: str) -> list[dict[str, Any]]:
    """Le correzioni di una scheda: in attesa (graffetta) o integrate (✓)."""
    out = []
    for path, front in iter_board_audits(project_root):
        if front.get("board_card") != card_id:
            continue
        resolved = front.get("status") == "resolved"
        try:
            stamp = datetime.fromtimestamp(path.stat().st_mtime).astimezone()
        except OSError:
            continue
        out.append({
            "id": str(front.get("id", "")),
            "section": str(front.get("board_section", "")),
            "text": str(front.get("board_text", "")),
            "target": str(front.get("target", "")),
            "status": "done" if resolved else "pending",
            "at": stamp.isoformat(timespec="seconds"),
        })
    return out


def open_corrections(project_root: Path) -> list[dict[str, Any]]:
    """Le correzioni ancora aperte del quaderno, per la passata della bacheca."""
    out = []
    for path, front in iter_board_audits(project_root):
        if front.get("status") != "open" or path.parent.name == "resolved":
            continue
        out.append({
            "id": str(front.get("id", "")),
            "card": str(front.get("board_card", "")),
            "section": str(front.get("board_section", "")),
            "text": str(front.get("board_text", "")),
            "target": str(front.get("target", "")),
            "sources": [str(s) for s in front.get("board_sources") or []],
            "file": path.relative_to(project_root).as_posix(),
        })
    return out


def resolve_correction(project_root: Path, audit_id: str, resolution: str) -> bool:
    """Chiude una correzione: stato ``resolved``, risoluzione scritta, file in ``resolved/``.

    Vero se l'ha trovata aperta e chiusa. La chiude il codice, con il testo che
    la passata gli dà: il modello non scrive in ``audit/``.
    """
    for path, front in iter_board_audits(project_root):
        if str(front.get("id")) != audit_id or front.get("status") != "open":
            continue
        if path.parent.name == "resolved":
            continue
        text = path.read_text(encoding="utf-8")
        match = _FRONT_RE.match(text)
        if not match:
            return False
        head = re.sub(r"(?m)^status: open$", "status: resolved", match.group(0), count=1)
        rest = text[match.end():]
        note = " ".join(resolution.split()) or "Applied to the wiki by the board pass."
        if _RESOLUTION_PLACEHOLDER in rest:
            rest = rest.replace(_RESOLUTION_PLACEHOLDER, note, 1)
        else:
            rest = rest.rstrip("\n") + f"\n\n{note}\n"
        target = path.parent / "resolved" / path.name
        atomic_write(target, head + rest)
        try:
            path.unlink()
        except OSError as exc:
            logger.warning("board: could not remove {} after resolving it: {}", path, exc)
        return True
    return False
