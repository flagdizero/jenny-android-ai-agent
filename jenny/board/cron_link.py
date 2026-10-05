"""I cron del quaderno: il legame fra un job dello scheduler e il suo cassetto.

Un cron chiesto nella chat di un quaderno nasce **nel cassetto di una scheda**:
il job porta ``payload.board = {notebook, card, section}``, e la sezione
``{kind: cron, job}`` lo mostra. Il turno gira col recinto del quaderno (la
sessione ``cron:nb:<quaderno>/<id>``, v. ``jenny.session.keys``), consegna nella
chat del quaderno (e con la notifica) quando ha qualcosa da dire, e lascia qui il
suo esito: l'ultima riga nel cassetto e una in «Dall'ultima volta».
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from jenny.board import model

MAX_RESULT = 600


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
    """Se il quaderno e la scheda di un cron esistono ancora."""
    from jenny.board.store import read_board

    root = notebook_root(str(link.get("notebook")))
    if root is None:
        return False
    board = read_board(root)
    return board is not None and model.card_by_id(board, str(link.get("card"))) is not None


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
        "name": job.name,
        "message": (job.payload.message or "")[:300],
        "mode": job.payload.mode,
        "schedule": when,
        "nextRunAtMs": job.state.next_run_at_ms,
        "lastRunAtMs": job.state.last_run_at_ms,
        "lastStatus": job.state.last_status,
        "paused": job.paused_at_ms is not None,
        "enabled": job.enabled,
    }
