"""I cron del quaderno: il legame fra un job dello scheduler e la bacheca.

Un cron chiesto nella chat di un quaderno appartiene a **una scheda o a tutto il
quaderno**, e lo decide Jenny. Il job porta ``payload.board = {notebook, card,
section}``, e questo modulo è l'unico che ne conosce la forma su disco (v.
:func:`make_link`). Il turno gira col recinto del quaderno (la sessione
``cron:nb:<quaderno>/<id>``, v. ``jenny.session.keys``), consegna nella chat del
quaderno (e con la notifica) quando ha qualcosa da dire, e lascia qui il suo
esito: l'ultimo giro sull'etichetta e una riga in «Dall'ultima volta».

**Il quaderno si scrive con un segnaposto, mai con la stringa vuota.**
``CronService._load_board_link`` trasforma un campo vuoto in «cron personale», e
un monitor di quaderno girerebbe senza il recinto del quaderno. Col segnaposto,
anche una versione vecchia vede un link vivo: ``link_alive`` non trova la scheda
e il job va in errore invece di girare senza recinto. Fuori da qui l'ambito del
quaderno è ``model.NOTEBOOK``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from jenny.board import model

MAX_RESULT = 600

# Il segnaposto del quaderno nel campo ``card`` (come ``model.APP_CARD``), e quello
# del campo ``section`` dei legami nuovi: dal 20/10/2026 un cron non scrive più una
# sezione nel cassetto, la bacheca lo deriva dal job.
NOTEBOOK_CARD = "@notebook"
NO_SECTION = "@auto"


def make_link(notebook: str, scope: str) -> dict[str, str]:
    """Il ``payload.board`` di un cron nuovo del quaderno *notebook*, con ambito *scope*."""
    card = NOTEBOOK_CARD if scope == model.NOTEBOOK else scope
    return {"notebook": notebook, "card": card, "section": NO_SECTION}


def scope_of(link: dict[str, Any]) -> str:
    """L'ambito di un legame: l'id della scheda, o ``model.NOTEBOOK``."""
    card = link.get("card")
    if card == NOTEBOOK_CARD:
        return model.NOTEBOOK
    return card if isinstance(card, str) else model.NOTEBOOK


def legacy_section(link: dict[str, Any]) -> str | None:
    """La sezione del cassetto di un legame scritto prima del 20/10/2026, o ``None``."""
    section = link.get("section")
    if scope_of(link) == model.NOTEBOOK or not isinstance(section, str) or section == NO_SECTION:
        return None
    return section


def legacy_result_key(link: dict[str, Any]) -> str | None:
    """La chiave ``carta/sezione`` con cui i legami vecchi scrivevano l'esito."""
    section = legacy_section(link)
    return f"{link.get('card')}/{section}" if section else None


def notebook_root(name: str) -> Path | None:
    """La cartella del quaderno *name*, o ``None`` se non c'e' (piu')."""
    from jenny.config.paths import get_workspace_path
    from jenny.utils.wiki_paths import is_wiki_root

    try:
        from jenny.config.loader import load_config

        wikis = load_config().wiki.wikis_dir or "wikis"
    except Exception:  # noqa: BLE001 — il default e' quello dello scaffold
        wikis = "wikis"
    root = get_workspace_path() / wikis / name
    return root if is_wiki_root(root) else None


def link_alive(link: dict[str, Any]) -> bool:
    """Se la casa di un cron c'è ancora: il quaderno e, se ne ha una, la sua scheda.

    Una scheda nel cestino non è una casa: il suo cron non gira (lo mette in
    pausa ``edit._pause_trashed``, e questo è il controllo di riserva).
    """
    from jenny.board.store import read_board

    root = notebook_root(str(link.get("notebook")))
    if root is None:
        return False
    board = read_board(root)
    if board is None:
        return False
    return model.scope_state(board, scope_of(link)) != model.SCOPE_HIDDEN


def record_result(link: dict[str, Any], *, status: str, text: str, job_name: str) -> None:
    """L'esito di un giro nel cassetto: ``cronResults`` e, se ha parlato, una riga.

    Scritto come Jenny, fuori dalle cose dell'utente (come ``appStates``): non
    tocca le sezioni, solo lo stato che le accompagna.
    """
    from jenny.board.store import mutate_board

    root = notebook_root(str(link.get("notebook")))
    if root is None:
        return
    key = f"{link.get('card')}/{link.get('section')}"
    clean = " ".join((text or "").split())[:MAX_RESULT]

    def apply(board: dict[str, Any]) -> None:
        results = dict(board.get("cronResults") or {})
        entry = {"at": model.now_iso(), "status": status}
        if clean:
            entry["text"] = clean
        elif isinstance(results.get(key), dict) and results[key].get("text"):
            # Un giro senza niente da dire non cancella l'ultimo esito che diceva qualcosa.
            entry["text"] = results[key]["text"]
            entry["textAt"] = results[key].get("textAt") or results[key].get("at")
        if clean:
            entry["textAt"] = entry["at"]
        results[key] = entry
        board["cronResults"] = results
        if clean and status == "ok":
            card = model.card_by_id(board, str(link.get("card")))
            model.add_since(board, "cron_result", card=link.get("card"),
                            title=card["title"] if card else "", app=job_name)

    try:
        mutate_board(root, apply, actor=model.JENNY)
    except Exception as exc:  # noqa: BLE001 — l'esito nel cassetto e' una cortesia
        logger.warning("cron: result of {} not recorded in {}: {}", job_name, root.name, exc)


def last_message_text(session: Any) -> str:
    """Il testo dell'ultimo ``message`` mandato in una sessione (il monitor che ha parlato)."""
    for msg in reversed(getattr(session, "messages", None) or []):
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        for call in reversed(msg.get("tool_calls") or []):
            fn = (call or {}).get("function") or {}
            if fn.get("name") != "message":
                continue
            import json

            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                continue
            if isinstance(args, dict) and isinstance(args.get("content"), str):
                return args["content"]
    return ""


def job_snapshot(job: Any) -> dict[str, Any]:
    """Quel che il cassetto mostra di un job: nome, quando, prossimo giro, stato."""
    from jenny.cron.types import job_display_name

    sched = job.schedule
    # La forma dello ``schedule`` dell'officina (``webui/cron_api._schedule_payload``):
    # il cassetto lo racconta con lo stesso ``describeSchedule``.
    when: dict[str, Any] = {"kind": sched.kind}
    if sched.kind == "every":
        when["every_ms"] = sched.every_ms
    elif sched.kind == "cron":
        when.update({"expr": sched.expr, "tz": sched.tz})
    elif sched.kind == "at":
        when["at_ms"] = sched.at_ms
    return {
        "id": job.id,
        # Mai vuoto per il cassetto (v. ``cron.types.job_display_name``).
        "name": job_display_name(job),
        "message": (job.payload.message or "")[:300],
        "mode": job.payload.mode,
        "schedule": when,
        "nextRunAtMs": job.state.next_run_at_ms,
        "lastRunAtMs": job.state.last_run_at_ms,
        "lastStatus": job.state.last_status,
        "paused": job.paused_at_ms is not None,
        "enabled": job.enabled,
    }
