"""Le sezioni che l'utente aggiunge a un cassetto: nota, promemoria, mini app, foto.

Le sezioni dello scheletro (Vaso, Acqua…) le scrive il giardiniere dalla wiki;
queste sono dell'utente, e ognuna ha la sua verità altrove:

- **nota** — resta sulla scheda come l'hai scritta, e diventa anche una riga di
  diario ``[said]``: il giardiniere la promuove come ogni altra riga. Quando il
  suo cursore l'ha passata, la nota è «nella wiki».
- **promemoria** — un rimando a un'attesa di ``reminders.json``.
- **mini app** — un rimando a una Jenny App con la sua vista compatta (il blocco
  ``section`` del manifest). **L'app produce, la bacheca trattiene**: la sezione
  legge l'ultimo valore che l'app ha lasciato nei suoi dati, con la sua firma
  (ora e fonte), e non la chiama mai.
- **foto** — un file in ``raw/photos/``, perché ``raw/`` è il materiale grezzo.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from jenny.board import model

PHOTOS_DIR = "raw/photos"

# Quanti punti al più ha l'andamento di una sezione mini app: uno al giorno.
_TREND_POINTS = 31


def _note_integrated(project_root: Path, ref: str, cursor: Mapping[str, int]) -> bool:
    """Se la riga di diario di una nota è già stata letta dal giardiniere.

    *ref* è ``raw/journal/AAAAMMGG.md:<riga>``, la riga fisica come la conta il
    cursore: passata quella, la nota è entrata nella wiki (o il giardiniere l'ha
    giudicata e lasciata, che per la nota è lo stesso — non è più in attesa).
    """
    path, _, line = ref.rpartition(":")
    try:
        number = int(line)
    except ValueError:
        return False
    return cursor.get(path, 0) >= number


def _gardener_cursor(project_root: Path) -> dict[str, int]:
    try:
        from jenny.agent.gardener_state import read_state

        return dict(read_state(project_root).cursor)
    except Exception:  # noqa: BLE001 — una nota «in attesa» è il ripiego onesto
        return {}


def _record_time(record: Mapping[str, Any]) -> datetime | None:
    stamp = model.parse_iso(record.get("ts"))
    return stamp.astimezone(timezone.utc) if stamp else None


def app_section_view(
    workspace: Path,
    slug: str,
    params: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """La vista compatta di una mini app in un cassetto.

    ``state`` dice come sta: ``ok``, ``alarm`` (il valore ha passato la soglia
    del manifest), ``stale`` (l'ultimo dato è più vecchio di quanto il manifest
    ammette), ``empty`` (nessun dato), ``missing`` (l'app non c'è più),
    ``trashed`` (è nel cestino, si può rimettere), ``broken`` (il manifest è
    rotto) o ``no_section`` (l'app non dichiara una vista compatta). ``alarm``
    e ``stale`` accendono l'angolo rosso della scheda.
    """
    from jenny.apps.manifest import find_app
    from jenny.apps.summary_view import summary_view

    app = find_app(workspace, slug)
    if app is None:
        # Nel cestino non e' sparita: il cassetto offre di rimetterla.
        from jenny.apps.trash import trashed_entry

        entry = trashed_entry(workspace, slug)
        if entry is not None:
            return {"state": "trashed", "slug": slug, "name": entry["name"],
                    "trashId": entry["id"]}
        return {"state": "missing", "slug": slug}
    if app.broken or app.manifest is None:
        return {"state": "broken", "slug": slug, "error": app.error}
    return summary_view(workspace, app, params, now=now)


def records_view(
    records: list[dict[str, Any]],
    *,
    field_name: str,
    latest: str = "ts",
    alarm: Mapping[str, Any] | None = None,
    stale_after_minutes: int | None = None,
    trend_days: int = 0,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Lo stato di una serie di letture: l'ultimo valore, l'allarme, il dato fermo, l'andamento.

    **Un solo motore** per le sezioni mini app (i record di una collezione
    dell'app) e per le micro app (le letture del loro ``readings.jsonl``): stessi
    record ``{id, ts, ...}``, stessi stati, stesso andamento — cosi' la vista, gli
    eventi di diario e le attese a condizione non sanno chi ha prodotto il dato.
    ``record`` e' l'ultima lettura intera, per chi vuole anche gli altri campi.
    """
    if not records:
        return {"state": "empty", "value": None, "at": "", "trend": [], "record": None}

    def order(record: Mapping[str, Any]) -> tuple[int, float, str]:
        return model.instant_order(record.get(latest))

    last = max(records, key=order)
    value = last.get(field_name)
    at = _record_time(last)
    current = now or datetime.now(timezone.utc)
    state = "ok"
    if alarm and model.compare(value, alarm["op"], alarm["value"]):
        state = "alarm"
    if stale_after_minutes and (
        at is None or current - at > timedelta(minutes=stale_after_minutes)
    ):
        state = "stale"
    trend: list[Any] = []
    if trend_days:
        since = current - timedelta(days=trend_days)
        daily: dict[str, Any] = {}
        for record in sorted(records, key=order):
            stamp = _record_time(record)
            if stamp is None or stamp < since:
                continue
            daily[stamp.date().isoformat()] = record.get(field_name)
        trend = [v for v in daily.values() if isinstance(v, (int, float))][-_TREND_POINTS:]
    return {
        "state": state,
        "value": value,
        "at": at.isoformat(timespec="seconds") if at else "",
        "trend": trend,
        "record": last,
    }


def app_field_value(
    workspace: Path,
    slug: str,
    params: Mapping[str, Any],
    field_name: str,
    *,
    now: datetime | None = None,
) -> tuple[Any, bool]:
    """L'ultimo valore di *field_name* nei dati di un'app, e se è fresco.

    La collezione, i filtri e la soglia di freschezza sono quelli della vista
    compatta del manifest: un'attesa a condizione guarda gli stessi record che
    la sezione mostra. Un'app senza vista compatta, o senza dati, dà
    ``(None, False)``: una condizione che non si può valutare non scatta.
    """
    from jenny.apps.manifest import find_app
    from jenny.apps.summary_view import field_value

    app = find_app(workspace, slug)
    if app is None or app.manifest is None or app.manifest.section is None:
        return None, False
    return field_value(app, params, field_name, now=now)


def journal_event(
    project_root: Path, card_title: str, view: Mapping[str, Any], *,
    timezone_name: str | None = None,
) -> str:
    """Una riga di diario per un cambio di stato di una sezione mini app.

    La scrive il codice, ed è ``[inferred]``: è un'osservazione della macchina,
    non qualcosa che l'utente ha detto, quindi non potrà mai giustificare una
    pagina più forte di ``open``.
    """
    from jenny.agent.tools.journal import INFERRED_MARKER, append_journal_entry
    from jenny.utils.helpers import safe_zoneinfo
    from jenny.utils.wiki_paths import journal_page_name, wiki_journal_dir

    tz = safe_zoneinfo(timezone_name) if timezone_name else None
    stamp = datetime.now(tz=tz) if tz else datetime.now().astimezone()
    value = view.get("value")
    reading = f", {view.get('label')} {value}{view.get('unit') or ''}" if value is not None else ""
    since = f", last data {view.get('at')}" if view.get("at") else ""
    text = f"{view.get('name') or view.get('slug')} on {card_title}: {view.get('state')}{reading}{since}"
    page = wiki_journal_dir(project_root) / journal_page_name(stamp.date())
    number = append_journal_entry(page, stamp.date(),
                                  f"- {stamp:%H:%M} — {INFERRED_MARKER} {text[:400]}\n")
    return f"{page.relative_to(project_root).as_posix()}:{number}"


def section_resolver(
    workspace: Path, project_root: Path
) -> Callable[[Mapping[str, Any], Mapping[str, Any]], dict[str, Any]]:
    """Il risolutore che ``build_card_view`` chiama per ogni sezione dell'utente."""
    cursor: dict[str, int] | None = None

    def resolve(section: Mapping[str, Any], ctx: Mapping[str, Any]) -> dict[str, Any]:
        nonlocal cursor
        kind = section.get("kind")
        if kind == "note":
            ref = section.get("journalLine")
            if not isinstance(ref, str) or not ref:
                return {"status": "pending"}
            if cursor is None:
                cursor = _gardener_cursor(project_root)
            return {"status": "integrated" if _note_integrated(project_root, ref, cursor)
                    else "pending"}
        if kind == "reminder":
            reminders = ctx.get("reminders") or {}
            rem = next(
                (r for r in reminders.get("reminders") or [] if r["id"] == section.get("reminder")),
                None,
            )
            return {"reminderData": rem}
        if kind == "app":
            return {"view": app_section_view(
                workspace, str(section.get("app")), section.get("params") or {}
            )}
        if kind == "photo":
            path = project_root / str(section.get("path"))
            return {"exists": path.is_file()}
        if kind == "micro":
            from jenny.board.micro import micro_view

            return {"view": micro_view(workspace, project_root, str(section.get("micro")),
                                       pending=section.get("pending") is True)}
        if kind == "cron":
            jobs = ctx.get("cronJobs") or {}
            results = ctx.get("cronResults") or {}
            return {"job": jobs.get(str(section.get("job"))),
                    "result": results.get(f"{ctx.get('card')}/{section.get('id')}")}
        return {}

    return resolve


# Gli stati di una sezione che accendono l'angolo rosso della sua scheda: il
# dato in allarme o fermo, e una micro app che non va piu'.
ALERT_STATES = ("alarm", "stale", "broken", "unapproved", "quarantined")


def section_view(workspace: Path, project_root: Path | None,
                 section: Mapping[str, Any]) -> dict[str, Any] | None:
    """La vista di una sezione che produce dati (mini app o micro app), o ``None``."""
    kind = section.get("kind")
    if kind == "app":
        return app_section_view(workspace, str(section.get("app")), section.get("params") or {})
    if kind == "micro" and project_root is not None and section.get("pending") is not True:
        from jenny.board.micro import micro_view

        return micro_view(workspace, project_root, str(section.get("micro")))
    return None


def alert_probe(
    workspace: Path, project_root: Path | None = None
) -> Callable[[Mapping[str, Any]], bool]:
    """Se una sezione mini app o micro app deve accendere l'angolo rosso della sua scheda."""

    def probe(section: Mapping[str, Any]) -> bool:
        view = section_view(workspace, project_root, section)
        return bool(view) and view.get("state") in ALERT_STATES

    return probe


def journal_note(project_root: Path, text: str, *, timezone_name: str | None = None) -> str:
    """Scrive la riga ``[said]`` di una nota appena aggiunta; ne restituisce il riferimento.

    La nota resta sulla scheda come l'hai scritta; la riga è quel che il
    giardiniere promuove. Il riferimento (``raw/journal/AAAAMMGG.md:<riga>``)
    dice alla scheda quando la nota è entrata nella wiki.
    """
    from jenny.agent.tools.journal import SAID_MARKER, append_journal_entry
    from jenny.utils.helpers import safe_zoneinfo
    from jenny.utils.wiki_paths import journal_page_name, wiki_journal_dir

    tz = safe_zoneinfo(timezone_name) if timezone_name else None
    stamp = datetime.now(tz=tz) if tz else datetime.now().astimezone()
    day = stamp.date()
    page = wiki_journal_dir(project_root) / journal_page_name(day)
    line = " ".join(text.split())[:500]
    number = append_journal_entry(page, day, f"- {stamp:%H:%M} — {SAID_MARKER} {line}\n")
    return f"{page.relative_to(project_root).as_posix()}:{number}"


def short_value(value: Any, *, limit: int = 7) -> str:
    """Un valore in pochi caratteri, per la copertina: «12,4k», «✓», «38».

    I numeri grandi si abbreviano (k, M), i decimali si tagliano a uno, il
    separatore e' la virgola (la copertina non sa la lingua, e la casa e'
    nata italiana: le cifre sono le stesse). Il testo si tronca con «…».
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "✓" if value else "–"
    if isinstance(value, (int, float)):
        n = float(value)
        for div, suffix in ((1_000_000_000, "G"), (1_000_000, "M"), (10_000, "k")):
            if abs(n) >= div:
                scaled = n / (div if suffix != "k" else 1000)
                text = f"{scaled:.1f}".rstrip("0").rstrip(".") + suffix
                return text.replace(".", ",")
        if n == int(n):
            return str(int(n))
        return f"{n:.1f}".replace(".", ",")
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def cover_of(view: Mapping[str, Any]) -> dict[str, Any] | None:
    """La copertina di una scheda dal riassunto della sua app.

    ``state`` dice come sta (``ok``, ``alarm``, ``stale``, ``empty``…);
    senza valore la copertina non c'e' e resta la foto.
    """
    value = view.get("value")
    if value is None or view.get("state") in ("missing", "broken", "no_section", "trashed"):
        return None
    unit = str(view.get("unit") or "")
    return {
        "short": short_value(value),
        "unit": unit if len(unit) <= 3 else "",
        "state": view.get("state") or "ok",
        "at": view.get("at") or "",
        "trend": [v for v in view.get("trend") or [] if isinstance(v, (int, float))][-14:],
        "icon": view.get("icon") or "",
    }
