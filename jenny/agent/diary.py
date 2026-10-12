"""Il diario: quel che è successo di recente, fra la chat e i quaderni, in testa a Jenny.

Una Jenny sola che parla di tutto ha bisogno di sapere cosa è successo *altrove*
senza andarselo a cercare. Il caso che l'ha chiesto (11/10/2026): un film visto
e commentato nel quaderno dei film, scritto nel suo journal alle 18:09; alle 20
la chat personale diceva ancora «devi vederlo». L'informazione c'era due volte
(journal e ``history.jsonl``) ed era filtrata fuori.

Le righe sono quelle di ``history.jsonl`` con un ``kind`` (``said``/``inferred``,
specchiate dal journal dei quaderni da ``journal_append``): brevi, datate,
attribuite. Questo modulo decide **quali** righe vede una sessione e **come**,
senza scrivere niente; ``ContextBuilder`` le mette nel prompt.

**Due gradi, come la memoria vera.** Gli ultimi tre giorni riga per riga; il
resto della settimana come traccia, un giorno per riga con i posti toccati e
quante cose, senza riassunti (un riassunto del modello ogni turno, e riassunti
di riassunti, sono il modo in cui un diario inventa). Oltre la settimana niente:
c'è ``recall_history``.

**La cache.** Il prompt di sistema è il prefisso in cache: una riga nuova lì lo
romperebbe a ogni scrittura, e un confine «tre giorni fa» calcolato su adesso lo
romperebbe anche senza scritture. Quindi la vista è **congelata**: righe fino a
un cursore, gradi contati da un istante, tutti e due salvati nei metadata della
sessione (:data:`DIARY_STATE_KEY`). Le righe arrivate dopo vanno nel contesto del
turno, una volta sola — dal commit che rimanda il messaggio com'era partito,
restano in storia e sono parte del prefisso in cache del turno dopo. Si
ricongela quando la cache è comunque fredda (una pausa lunga) o quando le righe
nuove sono diventate troppe.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from jenny.session.keys import PROJECT_SESSION_PREFIX, session_kind

#: Lo stato della vista congelata, nei metadata della sessione:
#: ``{"cursor": int, "at": "AAAA-MM-GG HH:MM", "seen": "AAAA-MM-GG HH:MM"}``.
DIARY_STATE_KEY = "diary_view"

#: Il primo grado, riga per riga.
SHARP = timedelta(days=3)
#: Il secondo grado, una riga per giorno; oltre, niente.
TRACE = timedelta(days=7)
#: Una pausa più lunga di così e la cache del provider è fredda comunque: è il
#: momento giusto per ricongelare (le cache dei provider durano da 5 minuti a
#: un'ora).
REFREEZE_AFTER_IDLE = timedelta(hours=1)
#: Oltre queste righe nuove si ricongela anche a cache calda: un turno che
#: riparte da zero costa meno di un contesto che cresce senza fine.
REFREEZE_AFTER_NEW = 12
#: Tetto delle righe nitide nel prompt: le più recenti.
MAX_SHARP_LINES = 30

_KIND_LABEL = {"said": "the user said", "inferred": "you noted"}
_TS = "%Y-%m-%d %H:%M"
# I giorni in inglese a mano: ``%a`` segue il locale del processo, e il prompt
# non deve cambiare lingua con il telefono.
_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _day(when: datetime) -> str:
    return f"{_DAYS[when.weekday()]} {when.strftime('%d/%m')}"


@dataclass(frozen=True)
class DiaryTurn:
    """Quel che un turno porta del diario: il blocco in testa e le righe nuove."""

    section: str
    new_lines: list[str]


def _parse(ts: Any) -> datetime | None:
    if not isinstance(ts, str):
        return None
    try:
        return datetime.strptime(ts[:16], _TS)
    except ValueError:
        return None


def _place(row: Mapping[str, Any]) -> str:
    about = row.get("about")
    if isinstance(about, str) and about:
        return f"notebook {about}"
    key = row.get("session_key")
    if isinstance(key, str) and key.startswith(PROJECT_SESSION_PREFIX):
        return f"notebook {key[len(PROJECT_SESSION_PREFIX):]}"
    return "chat"


def visible_rows(entries: Sequence[Mapping[str, Any]], session_key: str) -> list[Mapping[str, Any]]:
    """Le righe del diario che *session_key* vede.

    - la chat personale: tutte, tranne quelle ``untrusted`` nate altrove (il
      riciclo attraverso la memoria: una riga nata con una pagina web in vista
      non deve arrivare come un fatto in un'altra conversazione);
    - un quaderno: solo le righe che parlano di lui (``about``) nate fuori dalla
      sua chat — le sue le ha già nel journal. Mai le righe di un altro quaderno:
      «chi sei viaggia, dove altro lavori no»;
    - tutto il resto (cron, Dream, il giardiniere, i subagent): niente.
    """
    kind = session_kind(session_key)
    if kind not in ("personal", "project"):
        return []
    notebook = session_key[len(PROJECT_SESSION_PREFIX):] if kind == "project" else None
    rows: list[Mapping[str, Any]] = []
    for row in entries:
        if not row.get("kind") or not isinstance(row.get("content"), str):
            continue
        if row.get("prompt_visible") is False:
            continue
        own = row.get("session_key") == session_key
        if row.get("untrusted") and not own:
            continue
        if notebook is not None and (own or row.get("about") != notebook):
            continue
        rows.append(row)
    return rows


def _line(row: Mapping[str, Any]) -> str:
    when = _parse(row.get("timestamp"))
    stamp = f"{_day(when)} {when.strftime('%H:%M')}" if when else str(row.get("timestamp", ""))
    who = _KIND_LABEL.get(str(row.get("kind")), "noted")
    return f"- {stamp} · {_place(row)} · {who}: {row['content']}"


def _trace_lines(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    days: dict[str, Counter[str]] = {}
    labels: dict[str, str] = {}
    for row in rows:
        when = _parse(row.get("timestamp"))
        if when is None:
            continue
        day = when.strftime("%Y-%m-%d")
        labels[day] = _day(when)
        days.setdefault(day, Counter())[_place(row)] += 1
    lines = []
    for day in sorted(days):
        places = ", ".join(f"{place} ({n})" for place, n in sorted(days[day].items()))
        lines.append(f"- {labels[day]} · {places}")
    return lines


def render_section(rows: Sequence[Mapping[str, Any]], *, until_cursor: int,
                   anchor: datetime) -> tuple[list[str], list[str]]:
    """Le righe nitide e la traccia della vista congelata a *until_cursor*, *anchor*."""
    sharp: list[Mapping[str, Any]] = []
    trace: list[Mapping[str, Any]] = []
    for row in rows:
        cursor = row.get("cursor")
        when = _parse(row.get("timestamp"))
        if not isinstance(cursor, int) or cursor > until_cursor or when is None:
            continue
        age = anchor - when
        if age <= SHARP:
            sharp.append(row)
        elif age <= TRACE:
            trace.append(row)
    return [_line(r) for r in sharp[-MAX_SHARP_LINES:]], _trace_lines(trace)


def _in_history(line: str, history: Sequence[Mapping[str, Any]]) -> bool:
    for message in history:
        content = message.get("content")
        if isinstance(content, str) and line in content:
            return True
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and line in str(block.get("text", "")):
                    return True
    return False


def diary_turn(entries: Sequence[Mapping[str, Any]], session_key: str | None,
               state_holder: MutableMapping[str, Any] | None,
               history: Sequence[Mapping[str, Any]], *, now: datetime) -> DiaryTurn | None:
    """Il diario di un turno, e lo stato della vista aggiornato in *state_holder*.

    *entries* sono le voci di ``history.jsonl``; *history* la cronologia che il
    turno sta per mandare, da cui si capisce quali righe nuove sono già partite
    (stesso controllo di ``_goal_lines_once``: niente cursore da tenere in pari,
    e una riga uscita dalla finestra torna da sé). ``None`` se la sessione non
    vede il diario.
    """
    if not session_key or session_kind(session_key) not in ("personal", "project"):
        return None
    rows = visible_rows(entries, session_key)
    top = max((r["cursor"] for r in entries if isinstance(r.get("cursor"), int)), default=0)
    state = state_holder.get(DIARY_STATE_KEY) if state_holder is not None else None
    cursor = state.get("cursor") if isinstance(state, dict) else None
    at = _parse(state.get("at")) if isinstance(state, dict) else None
    seen = _parse(state.get("seen")) if isinstance(state, dict) else None
    fresh = [r for r in rows if isinstance(cursor, int) and isinstance(r.get("cursor"), int)
             and r["cursor"] > cursor]
    if (not isinstance(cursor, int) or at is None or seen is None
            or now - seen > REFREEZE_AFTER_IDLE or len(fresh) > REFREEZE_AFTER_NEW):
        cursor, at, fresh = top, now, []
    sharp, trace = render_section(rows, until_cursor=cursor, anchor=at)
    new_lines = [line for line in (_line(r) for r in fresh) if not _in_history(line, history)]
    if state_holder is not None:
        state_holder[DIARY_STATE_KEY] = {
            "cursor": cursor, "at": at.strftime(_TS), "seen": now.strftime(_TS)}
    if not (sharp or trace or new_lines):
        return DiaryTurn("", [])
    from jenny.utils.prompt_templates import render_template

    section = render_template("agent/diary_section.md", sharp=sharp, trace=trace,
                              strip=True) if (sharp or trace) else ""
    lines = ["Diary, new since this conversation's diary section:", *new_lines] if new_lines else []
    return DiaryTurn(section, lines)
