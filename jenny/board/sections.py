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

from loguru import logger

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
    ``broken`` (il manifest è rotto) o ``no_section`` (l'app non dichiara una
    vista compatta). ``alarm`` e ``stale`` accendono l'angolo rosso della scheda.
    """
    from jenny.apps.manifest import find_app
    from jenny.apps.storage import StorageError, read_collection

    app = find_app(workspace, slug)
    if app is None:
        return {"state": "missing", "slug": slug}
    if app.broken or app.manifest is None:
        return {"state": "broken", "slug": slug, "error": app.error}
    manifest = app.manifest
    base: dict[str, Any] = {"slug": slug, "name": manifest.name, "icon": manifest.icon}
    section = manifest.section
    if section is None:
        return {**base, "state": "no_section"}
    filters = {
        key: params.get(val[1:]) if isinstance(val, str) and val.startswith("$") else val
        for key, val in section.where.items()
    }
    try:
        records = [
            r for r in read_collection(app.dir, section.collection)
            if all(r.get(k) == v for k, v in filters.items())
        ]
    except (StorageError, OSError) as exc:
        logger.warning("board: app section {} unreadable: {}", slug, exc)
        records = []
    base.update({
        "unit": section.unit,
        "label": section.label or section.field,
        "verbs": section.verbs,
        "params": dict(params),
    })
    if not records:
        return {**base, "state": "empty", "value": None, "at": "", "trend": []}

    def order(record: Mapping[str, Any]) -> str:
        return str(record.get(section.latest, ""))

    latest = max(records, key=order)
    value = latest.get(section.field)
    at = _record_time(latest)
    current = now or datetime.now(timezone.utc)
    state = "ok"
    if section.alarm and model.compare(value, section.alarm["op"], section.alarm["value"]):
        state = "alarm"
    if section.stale_after_minutes and (
        at is None or current - at > timedelta(minutes=section.stale_after_minutes)
    ):
        state = "stale"
    trend: list[Any] = []
    if section.trend_days:
        since = current - timedelta(days=section.trend_days)
        daily: dict[str, Any] = {}
        for record in sorted(records, key=order):
            stamp = _record_time(record)
            if stamp is None or stamp < since:
                continue
            daily[stamp.date().isoformat()] = record.get(section.field)
        trend = [v for v in daily.values() if isinstance(v, (int, float))][-_TREND_POINTS:]
    return {
        **base,
        "state": state,
        "value": value,
        "at": at.isoformat(timespec="seconds") if at else "",
        "trend": trend,
    }


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
        return {}

    return resolve


def alert_probe(workspace: Path) -> Callable[[Mapping[str, Any]], bool]:
    """Se una sezione mini app deve accendere l'angolo rosso della sua scheda."""

    def probe(section: Mapping[str, Any]) -> bool:
        view = app_section_view(workspace, str(section.get("app")), section.get("params") or {})
        return view.get("state") in ("alarm", "stale")

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
