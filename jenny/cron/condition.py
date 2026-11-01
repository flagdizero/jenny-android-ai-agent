"""Il filtro di un ricorrente su un dato di un'app: si guarda nel codice, prima del modello.

«Ogni 6 ore guarda se l'umidità va sotto il 15 e avvertimi» non è un terzo
meccanismo accanto al singolo e al ricorrente: è un ricorrente con un filtro, e
la cadenza la decide l'utente (decisione del 31/10/2026). Fino ad allora la
condizione era un'attesa in ``board/reminders.json`` che l'orologio dei quaderni
guardava ogni quarto d'ora per conto suo.

A ogni scadenza del job: si fa girare il lettore dell'app (il dato è di adesso),
si legge l'ultimo valore del campo e lo si confronta. Falsa: il giro finisce
qui, zero token. Vera: il turno parte col valore osservato nel prompt. Un turno
solo finché resta vera (``CronJobState.condition_notified``); una lettura falsa
riarma.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger


@dataclass(frozen=True)
class ConditionReading:
    """Una lettura del filtro: vera o no, il valore visto, o perché non si è potuta fare."""

    holds: bool
    value: Any = None
    error: str | None = None


def describe(condition: dict[str, Any]) -> str:
    """``umidita < 15``: la condizione in una riga, per un prompt o un log."""
    return f"{condition.get('field')} {condition.get('op')} {condition.get('value')}"


async def read_condition(
    condition: dict[str, Any], *, workspace: Path | None = None,
) -> ConditionReading:
    """Fa girare il lettore dell'app e confronta l'ultimo valore. Non solleva."""
    from jenny.apps.reader import run_reader
    from jenny.board.model import compare
    from jenny.board.sections import app_field_value
    from jenny.config.paths import get_workspace_path

    root = workspace or get_workspace_path()
    slug = str(condition.get("app") or "")
    try:
        # ``force``: la cadenza è quella del job, non quella del manifest. Un'app
        # senza lettore (dati scritti a mano) torna ``None`` e si legge com'è.
        await run_reader(root, slug, force=True)
    except Exception as exc:  # noqa: BLE001 — un lettore rotto è un controllo mancato
        logger.warning("cron: reader of {} failed before a condition: {}", slug, exc)
        return ConditionReading(False, error=f"the app {slug} could not be read: {exc}")
    value, fresh = app_field_value(root, slug, condition.get("params") or {},
                                   str(condition.get("field") or ""))
    if not fresh:
        return ConditionReading(False, value, error=f"no fresh value from the app {slug}")
    return ConditionReading(
        compare(value, str(condition.get("op")), condition.get("value")), value,
    )
