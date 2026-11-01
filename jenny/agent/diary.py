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


#: Quanto avanti guarda «in arrivo nei quaderni», e quante righe al massimo.
COMING_UP = timedelta(days=7)
MAX_COMING_UP = 8

_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December")


@dataclass(frozen=True)
class Upcoming:
    """Un singolo di un quaderno che suona presto: una data in arrivo.

    La verità sta nello scheduler (``CronService``): qui se ne fa solo una riga.
    ``card`` è il titolo della scheda, ``None`` per tutto il quaderno; ``date``
    e ``time`` sono quelli della riga del post-it (``cron_link.note_of``), nel
    fuso dell'utente.
    """

    id: str
    notebook: str
    card: str | None
    text: str
    at_ms: int
    date: str
    precision: str
    time: str | None = None


def upcoming_dates(cron: Any, roots: Mapping[str, Any], *, now: datetime,
                   tz_name: str | None) -> list[Upcoming]:
    """I singoli dei quaderni che suonano entro :data:`COMING_UP`, dal più vicino.

    Dallo scheduler vivo, mai da ``jobs.json``: va chiamata sul loop, come
    ``cron_link.notebook_jobs``. Un ricorrente (anche a filtro) non è una data e
    resta fuori; uno in pausa non suona — è anche il caso dei job di una scheda
    tolta (``board/removed.py``) — e nemmeno uno di una scheda che non è più
    sulla bacheca. *roots* sono le cartelle dei quaderni, per il titolo della scheda.
    """
    from jenny.board import model
    from jenny.board.cron_link import job_snapshot, notes_from_jobs
    from jenny.board.store import read_board

    if cron is None:
        return []
    start = int(now.timestamp() * 1000)
    end = int((now + COMING_UP).timestamp() * 1000)
    by_notebook: dict[str, dict[str, dict[str, Any]]] = {}
    for job in cron.list_jobs(include_disabled=False):
        notebook = (job.payload.board or {}).get("notebook")
        if not isinstance(notebook, str) or job.paused_at_ms is not None:
            continue
        snap = job_snapshot(job)
        at_ms = snap["schedule"].get("at_ms")
        if isinstance(at_ms, (int, float)) and start <= at_ms <= end:
            by_notebook.setdefault(notebook, {})[job.id] = snap
    out: list[Upcoming] = []
    for notebook, snaps in sorted(by_notebook.items()):
        root = roots.get(notebook)
        board = read_board(root) if root is not None else None
        if board is None:
            continue
        for note in notes_from_jobs(snaps, tz_name):
            when = note["when"]
            if when.get("kind") != "date":
                continue
            scope = note["card"]
            if model.scope_state(board, scope) == model.SCOPE_HIDDEN:
                continue
            card = model.card_by_id(board, scope) if scope != model.NOTEBOOK else None
            out.append(Upcoming(
                note["id"], notebook, str(card.get("title") or "untitled") if card else None,
                note["text"], int(snaps[note["id"]]["schedule"]["at_ms"]),
                str(when["date"]), str(when["precision"]), when.get("time")))
    return sorted(out, key=lambda u: (u.at_ms, u.id))


def _date_line(item: Upcoming) -> str:
    """La riga di una data, con la sua precisione: un mese resta un mese.

    Mai «domani»: la riga si congela con il resto del diario, e una parola
    relativa diventerebbe falsa a mezzanotte. L'ora corrente il modello ce l'ha.
    """
    day = datetime.strptime(item.date, "%Y-%m-%d")
    if item.precision == "month":
        when = f"sometime in {_MONTHS[day.month - 1]} {day.year}"
    elif item.time:
        when = f"{_day(day)} {item.time}"
    else:
        when = f"{_day(day)} (no set time)"
    where = f"notebook {item.notebook}" + (f", card {item.card}" if item.card else "")
    return f"- {when} · {where} · {item.text}"


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
               history: Sequence[Mapping[str, Any]], *, now: datetime,
               dates: Sequence[Upcoming] = ()) -> DiaryTurn | None:
    """Il diario di un turno, e lo stato della vista aggiornato in *state_holder*.

    *entries* sono le voci di ``history.jsonl``; *history* la cronologia che il
    turno sta per mandare, da cui si capisce quali righe nuove sono già partite
    (niente cursore da tenere in pari, e una riga uscita dalla finestra torna da
    sé). ``None`` se la sessione non
    vede il diario.

    Fino al 31/10/2026 c'era anche «in sospeso»: le attese dei quaderni scattate
    e non chiuse. Un promemoria del quaderno ora è un job che, scattato, se ne va
    (e parla nella chat del suo quaderno): non resta niente in sospeso da dire.
    Al suo posto *dates*, le date in arrivo (:func:`upcoming_dates`): le vede solo
    la chat personale (un quaderno ha già la sua bacheca), le prime
    :data:`MAX_COMING_UP`, congelate con il resto; una che arriva, cambia o se ne
    va dopo arriva come le righe nuove.
    """
    if not session_key or session_kind(session_key) not in ("personal", "project"):
        return None
    rows = visible_rows(entries, session_key)
    personal = session_kind(session_key) == "personal"
    window = {d.id: _date_line(d) for d in dates} if personal else {}
    current = dict(list(window.items())[:MAX_COMING_UP])
    top = max((r["cursor"] for r in entries if isinstance(r.get("cursor"), int)), default=0)
    state = state_holder.get(DIARY_STATE_KEY) if state_holder is not None else None
    state = state if isinstance(state, dict) else {}
    cursor = state.get("cursor")
    at, seen = _parse(state.get("at")), _parse(state.get("seen"))
    stored = state.get("dates")
    frozen: dict[str, str] = stored if isinstance(stored, dict) else {}
    fresh = [r for r in rows if isinstance(cursor, int) and isinstance(r.get("cursor"), int)
             and r["cursor"] > cursor]
    # Una data oltre il tetto non è «nuova», e una congelata spinta oltre il
    # tetto da una più vicina non «se ne va»: suona ancora.
    changed = [f"- coming up in a notebook: {line[2:]}" for jid, line in current.items()
               if frozen.get(jid) != line]
    gone = [f"- no longer coming up (it rang, or was moved or removed): {line[2:]}"
            for jid, line in frozen.items() if jid not in window]
    if (not isinstance(cursor, int) or at is None or seen is None
            or now - seen > REFREEZE_AFTER_IDLE
            or len(fresh) + len(changed) + len(gone) > REFREEZE_AFTER_NEW):
        cursor, at, fresh, frozen, changed, gone = top, now, [], dict(current), [], []
    sharp, trace = render_section(rows, until_cursor=cursor, anchor=at)
    coming = list(frozen.values())
    news = [*(_line(r) for r in fresh), *changed, *gone]
    new_lines = [line for line in news if not _in_history(line, history)]
    if state_holder is not None:
        state_holder[DIARY_STATE_KEY] = {
            "cursor": cursor, "at": at.strftime(_TS), "seen": now.strftime(_TS)}
        if frozen:
            state_holder[DIARY_STATE_KEY]["dates"] = frozen
    if not (sharp or trace or coming or new_lines):
        return DiaryTurn("", [])
    from jenny.utils.prompt_templates import render_template

    section = render_template("agent/diary_section.md", sharp=sharp, trace=trace,
                              coming=coming, strip=True) if (sharp or trace or coming) else ""
    lines = ["Diary, new since this conversation's diary section:", *new_lines] if new_lines else []
    return DiaryTurn(section, lines)
