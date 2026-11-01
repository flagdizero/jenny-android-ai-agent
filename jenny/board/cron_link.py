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


def notebook_delivery(notebook: str) -> dict[str, str]:
    """Dove gira e consegna un job del quaderno: la sua chat, da qualunque chat sia nato.

    Il «dove» decide solo il recinto e la chat di consegna: un promemoria chiesto
    dalla chat personale per un quaderno suona nel quaderno, come uno chiesto lì.
    """
    from jenny.session.keys import project_session_key

    key = project_session_key(notebook)
    return {"session_key": key, "origin_channel": "websocket", "origin_chat_id": key}


def add_notebook_job(
    cron: Any,
    notebook: str,
    scope: str,
    *,
    name: str,
    message: str,
    schedule: Any,
    mode: str = "reminder",
    why: str | None = None,
    condition: dict[str, Any] | None = None,
    origin_metadata: dict[str, Any] | None = None,
) -> Any:
    """Un job nuovo del quaderno *notebook*, su una scheda (*scope*) o sul quaderno intero.

    L'unica porta per crearne uno: il tool ``cron``, la bacheca (RPC), il tool
    ``board`` e il giardiniere passano tutti di qui. Solleva ``ValueError`` come
    ``CronService.add_job``. Va chiamata sul loop.
    """
    return cron.add_job(
        name=name,
        schedule=schedule,
        message=message,
        mode=mode,
        board=make_link(notebook, scope),
        why=why,
        condition=condition,
        origin_metadata=origin_metadata or {},
        **notebook_delivery(notebook),
    )


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


def result_of(results: Any, job_id: str, link: dict[str, Any] | None = None) -> dict[str, Any]:
    """L'ultimo esito di un job in ``board.cronResults``, o ``{}``.

    Dal 20/10/2026 la chiave è l'id del job; un legame scritto prima lo teneva
    sotto ``carta/sezione``, e lo si legge lì finché il prossimo giro non lo sposta.
    """
    if not isinstance(results, dict):
        return {}
    entry = results.get(job_id)
    if not isinstance(entry, dict) and link is not None:
        legacy = legacy_result_key(link)
        entry = results.get(legacy) if legacy else None
    return entry if isinstance(entry, dict) else {}


def record_result(
    link: dict[str, Any], *, job_id: str, status: str, text: str, job_name: str,
) -> None:
    """L'esito di un giro sulla bacheca: ``cronResults`` e, se ha parlato, una riga.

    Scritto come Jenny, fuori dalle cose dell'utente (come ``appStates``): non
    tocca né le sezioni né le posizioni, solo lo stato che le accompagna. La
    chiave vecchia ``carta/sezione``, se c'è, se ne va nello stesso giro.
    """
    from jenny.board.store import mutate_board

    root = notebook_root(str(link.get("notebook")))
    if root is None:
        return
    legacy = legacy_result_key(link)
    clean = " ".join((text or "").split())[:MAX_RESULT]
    scope = scope_of(link)

    def apply(board: dict[str, Any]) -> None:
        results = dict(board.get("cronResults") or {})
        before = result_of(results, job_id, link)
        if legacy:
            results.pop(legacy, None)
        entry = {"at": model.now_iso(), "status": status}
        if clean:
            entry["text"] = clean
            entry["textAt"] = entry["at"]
        elif before.get("text"):
            # Un giro senza niente da dire non cancella l'ultimo esito che diceva qualcosa.
            entry["text"] = before["text"]
            entry["textAt"] = str(before.get("textAt") or before.get("at") or entry["at"])
        results[job_id] = entry
        board["cronResults"] = results
        if clean and status == "ok":
            if scope == model.NOTEBOOK:
                model.add_since(board, "cron_result_notebook", app=job_name)
            else:
                card = model.card_by_id(board, scope)
                model.add_since(board, "cron_result", card=scope,
                                title=card["title"] if card else "", app=job_name)

    try:
        mutate_board(root, apply, actor=model.JENNY)
    except Exception as exc:  # noqa: BLE001 — l'esito sulla bacheca e' una cortesia
        logger.warning("cron: result of {} not recorded in {}: {}", job_name, root.name, exc)


async def detach_job(cron: Any, job_id: str) -> str:
    """Toglie un job e, se era di un quaderno, quel che ne resta sulla bacheca.

    L'unico smontaggio di un cron: lo usano il tool ``cron``, l'officina e la
    bacheca. Restituisce l'esito di ``CronService.remove_job`` (``removed``,
    ``protected``, ``not_found``). Va chiamata sul loop, come ogni gesto sullo
    scheduler.
    """
    job = cron.get_job(job_id)
    link = dict(job.payload.board) if job is not None and job.payload.board else None
    result = cron.remove_job(job_id)
    if result == "removed" and link is not None:
        await forget_job(link, job_id)
    return result


async def forget_job(link: dict[str, Any], job_id: str) -> None:
    """Dopo che un job del quaderno se n'è andato: via l'esito e l'eventuale sezione vecchia.

    Scritto come ``user``: toglierlo è stato un gesto dell'utente, o di Jenny su
    sua richiesta. La posizione della sua etichetta resta: appartiene all'ambito,
    e la ritrova il prossimo cron dello stesso ambito.
    """
    from jenny.board.notify import board_changed
    from jenny.board.store import mutate_board

    notebook = str(link.get("notebook"))
    root = notebook_root(notebook)
    if root is None:
        return
    legacy_key = legacy_result_key(link)
    scope = scope_of(link)

    def apply(board: dict[str, Any]) -> bool | None:
        changed = False
        results = dict(board.get("cronResults") or {})
        for key in (job_id, legacy_key):
            if key and key in results:
                results.pop(key)
                changed = True
        board["cronResults"] = results
        card = model.card_by_id(board, scope) if scope != model.NOTEBOOK else None
        if card is not None:
            sections = card.get("userSections") or []
            keep = [x for x in sections if not (x.get("kind") == "cron" and x.get("job") == job_id)]
            if len(keep) != len(sections):
                card["userSections"] = keep
                changed = True
        return None if changed else False

    try:
        mutate_board(root, apply)
    except Exception as exc:  # noqa: BLE001 — il job è già andato, la bacheca è contorno
        logger.warning("cron: board of {} not cleaned after {}: {}", notebook, job_id, exc)
    await board_changed(notebook)


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


def notebook_jobs(cron: Any, notebook: str) -> dict[str, dict[str, Any]]:
    """``{id: istantanea}`` dei job legati al quaderno *notebook*.

    Va chiamata **sul loop**: ``list_jobs`` legge lo store dello scheduler, che un
    thread potrebbe trovare a metà di una scrittura (v. ``webui/cron_routes.py``).
    """
    if cron is None:
        return {}
    try:
        jobs = cron.list_jobs(include_disabled=True)
    except Exception:  # noqa: BLE001 — una bacheca senza job resta una bacheca
        return {}
    return {j.id: job_snapshot(j) for j in jobs
            if (j.payload.board or {}).get("notebook") == notebook}


def follow_renamed_notebook(cron: Any, old: str, new: str) -> list[str]:
    """I cron del quaderno *old*, rinominato in *new*, lo seguono; ritorna gli id.

    L'unico punto da cui passano le tre strade di un rinomino: il comando della
    casa, la cartella rinominata a mano che la chat insegue al turno dopo, e la
    ripresa all'avvio di un rinomino interrotto. Gli esiti dei giri non si
    toccano: stanno nel ``board.json`` del quaderno, indicizzati per id del job,
    e sono già partiti con la cartella.

    **Sul loop**, come ogni gesto sullo scheduler. Non solleva: il quaderno è già
    rinominato, e un job rimasto indietro fallisce a ogni giro con «notebook
    gone» invece di girare senza recinto — si toglie a mano.
    """
    from jenny.session.keys import project_session_key

    if cron is None:
        return []
    try:
        return cron.move_notebook(old, new, old_session=project_session_key(old),
                                  new_session=project_session_key(new))
    except Exception:  # noqa: BLE001 — il rinomino è già riuscito
        logger.opt(exception=True).warning("Cron jobs of notebook {} did not follow to {}",
                                           old, new)
        return []


def notebook_jobs_on_disk(notebook: str, workspace: Path | None = None) -> list[dict[str, Any]]:
    """I cron del quaderno letti da ``cron/jobs.json``, per chi scrive un prompt.

    Sola lettura e fuori dal loop: chi costruisce il prompt di un turno o della
    passata del giardiniere non ha lo scheduler in mano, e una lettura del file
    non gli tocca lo stato (v. :func:`notebook_jobs` per chi lo ha). Restano i
    job vivi, accesi o in pausa; un job ``at`` già scattato è spento e non conta.
    """
    import json

    from jenny.config.paths import get_workspace_path

    path = (workspace or get_workspace_path()) / "cron" / "jobs.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for job in raw.get("jobs") or [] if isinstance(raw, dict) else []:
        if not isinstance(job, dict):
            continue
        payload = job.get("payload") or {}
        link = payload.get("board") if isinstance(payload, dict) else None
        if not isinstance(link, dict) or link.get("notebook") != notebook:
            continue
        paused = job.get("pausedAtMs") is not None
        if not job.get("enabled", True) and not paused:
            continue
        state = job.get("state") or {}
        out.append({
            "id": str(job.get("id") or ""),
            "name": str(job.get("name") or ""),
            "message": " ".join(str(payload.get("message") or "").split()),
            "kind": (job.get("schedule") or {}).get("kind"),
            "atMs": (job.get("schedule") or {}).get("atMs"),
            "everyMs": (job.get("schedule") or {}).get("everyMs"),
            "precision": (job.get("schedule") or {}).get("precision"),
            "mode": payload.get("mode") or "reminder",
            "why": payload.get("why"),
            "condition": payload.get("condition"),
            "scope": scope_of(link),
            "nextRunAtMs": state.get("nextRunAtMs") if isinstance(state, dict) else None,
            "paused": paused,
            "link": link,
        })
    return out


def job_snapshot(job: Any) -> dict[str, Any]:
    """Quel che la bacheca mostra di un job: nome, quando, prossimo giro, stato, ambito."""
    from jenny.cron.types import job_display_name

    sched = job.schedule
    # La forma dello ``schedule`` dell'officina (``webui/cron_api._schedule_payload``):
    # il cassetto lo racconta con lo stesso ``describeSchedule``.
    when: dict[str, Any] = {"kind": sched.kind}
    if sched.kind == "every":
        when["every_ms"] = sched.every_ms
    elif sched.kind == "cron":
        from jenny.cron.cronexpr import shape

        when.update({"expr": sched.expr, "tz": sched.tz, "shape": shape(sched.expr or "")})
    elif sched.kind == "at":
        when["at_ms"] = sched.at_ms
        if sched.precision:
            when["precision"] = sched.precision
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
        # Quante volte di fila un monitor non è riuscito a guardare: l'etichetta lo dice.
        "couldNotCheck": job.state.consecutive_could_not_check,
        "scope": scope_of(job.payload.board) if job.payload.board else None,
        # Solo per ritrovare l'esito scritto da un legame vecchio (v. ``result_of``).
        "legacySection": legacy_section(job.payload.board) if job.payload.board else None,
        "why": job.payload.why,
        "condition": job.payload.condition,
    }


# ── I promemoria: le righe dei post-it ───────────────────────────────────────
#
# Dal 31/10/2026 un promemoria del quaderno è un job come gli altri: un singolo a
# data (sparisce quando scatta) o un ricorrente con un filtro ``condition`` (la
# cadenza è dell'utente). Sulla bacheca è una riga del post-it giallo della sua
# scheda, o di quello libero del quaderno, e nel calendario di cura del cassetto;
# un ricorrente **senza** filtro è invece una riga dell'etichetta menta. Qui
# c'è l'unica traduzione da job a riga, per la vista (dall'istantanea, sul loop)
# e per chi scrive un prompt (dal file, fuori dal loop).


def user_timezone() -> str | None:
    """Il fuso dell'utente (``agents.defaults.timezone``), o ``None`` per quello di sistema."""
    try:
        from jenny.config.loader import load_config

        return load_config().agents.defaults.timezone or None
    except Exception:  # noqa: BLE001 — l'ora locale del sistema è il ripiego
        return None


def is_note(kind: Any, condition: Any) -> bool:
    """Se un job è una riga del post-it: un singolo, o un ricorrente con un filtro."""
    return kind == "at" or (isinstance(condition, dict) and bool(condition))


def note_of(
    *,
    job_id: str,
    scope: str,
    text: str,
    kind: Any,
    at_ms: Any = None,
    precision: Any = None,
    condition: Any = None,
    every_ms: Any = None,
    tz_name: str | None = None,
    paused: bool = False,
) -> dict[str, Any] | None:
    """La riga ``{id, card, text, when}`` di un job, o ``None`` se non è un promemoria.

    ``when`` è ``{kind: "date", date, precision}`` (con ``time`` per un singolo
    a un'ora esatta) o ``{kind: "condition", app, params, field, op, value,
    every_ms}``. ``date`` è sempre ``AAAA-MM-GG`` nel fuso dell'utente, anche per
    un mese (il primo): il client la mette in un ``<input type=date>``. Un job
    in pausa resta una riga (l'utente l'ha fermato, non tolto) e porta
    ``paused: true``: non è «il prossimo» di nessuno e non si accende «a breve»
    (``model.next_note``, ``model.note_is_soon``), come già non entra nel diario.
    """
    from datetime import datetime

    if isinstance(condition, dict) and condition:
        when: dict[str, Any] = {"kind": "condition", "app": condition.get("app"),
                                "params": dict(condition.get("params") or {}),
                                "field": condition.get("field"), "op": condition.get("op"),
                                "value": condition.get("value"), "every_ms": every_ms}
    elif kind == "at" and isinstance(at_ms, (int, float)) and at_ms:
        from jenny.utils.helpers import safe_zoneinfo

        tz = safe_zoneinfo(tz_name) if tz_name else None
        at = (datetime.fromtimestamp(at_ms / 1000, tz=tz) if tz
              else datetime.fromtimestamp(at_ms / 1000).astimezone())
        when = {"kind": "date", "date": at.strftime("%Y-%m-%d"),
                "precision": "month" if precision == "month" else "day"}
        if precision not in ("day", "month"):
            # Un singolo a un'ora esatta (dal tool ``cron``) la porta: «21 ott, 09:00».
            when["time"] = at.strftime("%H:%M")
    else:
        return None
    note = {"id": job_id, "card": scope, "text": text, "when": when}
    if paused:
        note["paused"] = True
    return note


def notes_from_jobs(cron_jobs: Any, tz_name: str | None = None) -> list[dict[str, Any]]:
    """Le righe dei promemoria dalle istantanee di :func:`notebook_jobs`, dalla più vicina.

    Solo i job vivi: un singolo già scattato non c'è più, e uno spento resta
    fuori. Uno in pausa c'è, segnato (v. :func:`note_of`).
    """
    out = []
    for job_id, snap in (cron_jobs or {}).items():
        if not (snap.get("enabled") or snap.get("paused")):
            continue
        sched = snap.get("schedule") or {}
        note = note_of(job_id=job_id, scope=snap.get("scope") or model.NOTEBOOK,
                       text=snap.get("name") or "", kind=sched.get("kind"),
                       at_ms=sched.get("at_ms"), precision=sched.get("precision"),
                       condition=snap.get("condition"), every_ms=sched.get("every_ms"),
                       tz_name=tz_name, paused=bool(snap.get("paused")))
        if note is not None:
            out.append(note)
    return model.sorted_notes(out)


def notes_on_disk(notebook: str, workspace: Path | None = None,
                  tz_name: str | None = None) -> list[dict[str, Any]]:
    """Le righe dei promemoria del quaderno lette da ``cron/jobs.json``, dalla più vicina.

    Per chi non ha lo scheduler in mano (un prompt, una scrittura della bacheca
    in un thread): come :func:`notebook_jobs_on_disk`. Senza workspace
    configurato non c'è niente da leggere, e l'elenco è vuoto.
    """
    if workspace is None:
        try:
            from jenny.config.paths import get_workspace_path

            workspace = get_workspace_path()
        except RuntimeError:
            return []
    return notes_of_disk_jobs(notebook_jobs_on_disk(notebook, workspace), tz_name)


def notes_of_disk_jobs(jobs: list[dict[str, Any]], tz_name: str | None = None,
                       ) -> list[dict[str, Any]]:
    """Le righe dei promemoria fra i job di :func:`notebook_jobs_on_disk`, dalla più vicina.

    Per chi ha già letto il file una volta e ne vuole sia i cron sia i promemoria
    (il blocco della bacheca nel prompt): una lettura, non una per domanda.
    """
    out = []
    for job in jobs:
        note = note_of(job_id=job["id"], scope=job["scope"],
                       text=job["name"] or job["message"][:60] or job["id"], kind=job["kind"],
                       at_ms=job["atMs"], precision=job["precision"],
                       condition=job["condition"], every_ms=job["everyMs"], tz_name=tz_name,
                       paused=bool(job.get("paused")))
        if note is not None:
            out.append(note)
    return model.sorted_notes(out)


def note_words(note: dict[str, Any]) -> str:
    """Il «quando» di una riga in parole, per un prompt: ``2026-11-12``, ``2026-03``,
    ``2026-10-21 09:00``, o ``when umidita < 15 (checked every 6 h)``."""
    when = note.get("when") or {}
    if when.get("kind") == "date":
        day = str(when.get("date") or "")
        if when.get("precision") == "month":
            return day[:7]
        return f"{day} {when['time']}" if when.get("time") else day
    every = when.get("every_ms")
    cadence = ""
    if isinstance(every, (int, float)) and every > 0:
        hours = every / 3_600_000
        cadence = (f" (checked every {hours:g} h)" if hours < 24
                   else f" (checked every {hours / 24:g} day(s))")
    return f"when {when.get('field')} {when.get('op')} {when.get('value')}{cadence}"


async def clear_job_whys(cron: Any, notebook: str, pages: Any) -> list[dict[str, str]]:
    """Toglie il ``why`` dei job del quaderno che puntavano a *pages* (relative a
    ``wiki/``); restituisce ``[{id, text, why}]`` di quelli toccati.

    La chiama ``notebook remove_pages`` dopo aver cancellato le pagine: un
    ``why`` morto è un promemoria che, quando suona, parla senza la sua ragione.
    Va chiamata sul loop.
    """
    gone = {str(p).removeprefix("wiki/") for p in pages or () if isinstance(p, str) and p}
    touched: list[dict[str, str]] = []
    if cron is None or not gone:
        return touched
    for job_id, snap in notebook_jobs(cron, notebook).items():
        why = str(snap.get("why") or "").removeprefix("wiki/")
        if why in gone and cron.update_job(job_id, why=None) is not None:
            touched.append({"id": job_id, "text": str(snap.get("name") or ""), "why": why})
    return touched
