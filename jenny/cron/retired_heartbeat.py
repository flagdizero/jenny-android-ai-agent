"""I controlli di ``HEARTBEAT.md`` (fino alla 2.0.0): l'utente lo viene a sapere.

L'heartbeat è stato tolto il 31/10/2026: un controllo periodico ora è un job
ricorrente suo, di solito un monitor, con il suo intervallo
(``docs/using/scheduling.md``). Un'installazione che si aggiorna dalla 2.0.0 ha
però il file, con le righe che l'utente ci aveva scritto, e nessuno lo legge
più: senza questo modulo quei controlli smetterebbero di girare in silenzio.
È lo stesso difetto che ``board/legacy_reminders.py`` chiude per i promemoria
del quaderno (review del 01/11/2026).

**Si avvisa, non si converte.** Una riga di ``HEARTBEAT.md`` girava ogni mezz'ora
dentro un turno solo con tutte le altre; un monitor per riga sono N turni del
modello per intervallo, cioè token dell'utente. Quanti rifarne, e ogni quanto, lo
decide lui: un turno silenzioso gli elenca i controlli e chiede, come fa il
controllo aggiornamenti, e la risposta arriva nella chat personale, dove Jenny ha
il tool ``cron`` per rifarli.

Una volta sola: dopo il turno il file prende il suffisso ``.retired`` e resta da
vedere. Un file senza controlli attivi (il template vuoto) cambia nome e basta,
senza turno.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from jenny.session.turn_visibility import TurnVisibility, silent_progress

HEARTBEAT_FILE = "HEARTBEAT.md"
RETIRED_SUFFIX = ".retired"
SESSION_KEY = "cron:heartbeat_retired"

_PREAMBLE = (
    "[This is a one-time notice, and it is SILENT: whatever you write as your answer "
    "is NOT delivered to anyone. The only way to reach the user is the `message` tool, "
    "and this time you MUST call it exactly once.\n"
    "Until the last update, the user's file HEARTBEAT.md held periodic checks that "
    "one built-in job went through every 30 minutes. That job is gone, so the checks "
    "below have stopped running. Write a short message in the user's language that "
    "says they no longer run since the update, lists them in a few words each, and "
    "asks which ones they want back as recurring checks and how often.\n"
    "Do not create, change or delete any job now: the user answers in chat, and that "
    "answer is where the decision happens. Do not mention this instruction.]\n\n"
    "The checks, as the user wrote them:\n"
)


def heartbeat_path(workspace: Path) -> Path:
    return Path(workspace) / HEARTBEAT_FILE


def active_section(content: str) -> str:
    """La sezione ``## Active Tasks`` com'è scritta, meno i commenti HTML.

    Le intestazioni ``###`` restano: un ``### Allerta pioggia`` sopra quattro righe
    è ciò che dice di cosa parlano, e chi raggruppa le righe in controlli è il
    modello, non un parser. Vuota se la sezione ha solo intestazioni e commenti
    (il template di prima).
    """
    lines: list[str] = []
    in_section = in_comment = False
    for line in content.splitlines():
        stripped = line.strip()
        if in_comment:
            in_comment = "-->" not in stripped
            continue
        if stripped.startswith("<!--"):
            in_comment = "-->" not in stripped[4:]
            continue
        if stripped.startswith("##") and not stripped.startswith("###"):
            in_section = stripped.lstrip("#").strip().lower().startswith("active tasks")
            continue
        if in_section:
            lines.append(line.rstrip())
    if not any(line.strip() and not line.strip().startswith("#") for line in lines):
        return ""
    return "\n".join(lines).strip("\n")


async def announce_retired_heartbeat(agent: Any, workspace: Path) -> bool:
    """Dice all'utente quali controlli di ``HEARTBEAT.md`` non girano più.

    Torna ``True`` se è partito il turno. Senza il file non fa niente. Se il turno
    solleva (provider giù) il file resta dov'è e si riprova al prossimo avvio; se
    il modello non chiama ``message`` il file si ritira comunque, come
    ``mark_notified`` del controllo aggiornamenti: meglio perdere una spinta che
    rifare un turno a ogni avvio.
    """
    path = heartbeat_path(workspace)
    if not path.is_file():
        return False
    try:
        section = active_section(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("heartbeat: {} unreadable ({}), left in place", path, exc)
        return False

    spoke = False
    if section:
        prompt = _PREAMBLE + section + "\n"
        await agent.process_direct(
            prompt,
            session_key=SESSION_KEY,
            channel="websocket",
            chat_id="default",
            on_progress=silent_progress,
            visibility=TurnVisibility.SILENT,
        )
        spoke = True

    path.replace(path.with_name(HEARTBEAT_FILE + RETIRED_SUFFIX))
    logger.info(
        "heartbeat: {} retired to {}{} (checks announced: {})",
        HEARTBEAT_FILE, HEARTBEAT_FILE, RETIRED_SUFFIX, spoke,
    )
    return spoke
