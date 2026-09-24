"""Le manopole dei due lavoratori periodici, per la schermata Impostazioni.

Dream e il giardiniere sono i due job di sistema registrati all'avvio
(``runtime/container.py``). Le loro manopole vivono in ``config.json`` sotto
``agents.defaults.{dream,gardener}`` piu'
``agents.defaults.compact_projects_when_idle``, e fino a questo modulo la loro
copertura era a macchia di leopardo: Dream aveva i tetti dentro ``/dream
budget``, il giardiniere tutto dentro ``/gardener settings``, e un terzo
lavoratore, poi ritirato, niente: per spegnerlo si editava ``config.json`` a
mano — che e' precisamente l'incidente da cui il blocco del giardiniere era
nato (un ``sed -i`` che ha rotto l'etichetta SELinux del file).

Un comando e' un verbo: fa qualcosa adesso, in questa conversazione. Una
manopola e' una preferenza che sopravvive al turno, e sta dove stanno le altre
diciotto. Restano comandi ``/dream`` e ``/gardener`` — cioe' i due verbi, che
sono la ragione per cui i lavoratori sono collaudabili senza aspettare i loro
orologi.

**Sta su ``/api/`` e non sull'RPC** perche' non porta contenuto: numeri e
booleani stanno in una query string (v. la docstring di ``webui/commands.py``
per il confine). E sta in un modulo suo e non in ``settings_api.py``, che e'
gia' oltre 1.200 righe fra provider, onboarding, update e diagnostica
energetica.

Due cose che questo modulo deve fare e che sono facili da perdere:

- **I range non si riscrivono qui.** Si leggono dallo schema
  (:func:`_bounds`): un range scritto due volte diventa due range appena uno
  dei due si muove, ed e' proprio il numero che si racconta all'utente nel
  rifiuto.
- **Le letture non sollevano mai.** Aprono quattro file dell'utente, e una
  schermata Impostazioni che non si apre perche' ``SOUL.md`` e' illeggibile
  sarebbe un guasto peggiore di quello che sta segnalando — oltre a essere la
  schermata da cui si spengono i lavoratori.
"""

from __future__ import annotations

import os
from typing import Any

from loguru import logger

from jenny.channels.http_utils import QueryParams, parse_flag
from jenny.config import store
from jenny.config.loader import load_config
from jenny.config.schema import Config, DreamConfig, GardenerConfig
from jenny.webui.settings_api import (
    WebUISettingsError,
    _apply_bool,
    _apply_int,
    _bounds,
    _parse_int,
    settings_payload,
)

# Il pavimento operativo della cadenza di review. **Non e' nello schema**
# (``review_every_runs`` resta ``ge=1``) perche' un ``config.json`` restaurato
# deve poter riportare qualunque valore storico senza far fallire il parse: il
# pavimento e' una regola di questa superficie, non della struttura del file.
#
# Dodici perche' sotto quella soglia le passate di review si incontrano: la
# seconda atterra su file che la prima ha gia' potato e continua a cercare cose
# da togliere. Misurato sul device: due passate consecutive hanno portato
# ``USER.md`` da 3.524 a 1.626 caratteri, il 31% sulla sola seconda, e una
# passata forzata ha rimosso cinque voci vere — due domande aperte, un piano, un
# dettaglio biografico e un'osservazione. Era il pavimento di ``/dream budget
# review``, con una frase di conferma da ribattere; qui la conferma e' il flag
# ``confirm_back_to_back``, che il client alza solo dopo un dialogo.
REVIEW_CADENCE_FLOOR = 12




# ── Lettura ──────────────────────────────────────────────────────────────────




def _number(model: type, attr: str, value: Any) -> dict[str, Any]:
    """Un numero col suo range, cosi' l'``<input type=number>`` non inventa i propri."""
    low, high = _bounds(model, attr)
    return {"value": int(value), "min": low, "max": high}


def _memory_files() -> list[dict[str, Any]] | None:
    """Le tre misure che ``/dream budget`` stampava, o ``None`` se non misurabili.

    ``None`` e non una lista vuota: "non ho potuto misurare" e "tre file da zero
    caratteri" sono due cose diverse, e la seconda va detta con dei numeri.

    L'ordine e' quello di ``budget_report`` (MEMORY, USER, SOUL) ed e' contratto.

    ``exists`` e ``readable`` stanno accanto a ``chars`` perche' ``count_chars``
    ritorna ``0`` in tre casi che non si somigliano: file vuoto, file mai
    scritto, file che non si riesce ad aprire. Il terzo, misurato sul telefono il
    31/08/2026 con un ``chmod 000``, leggeva «0 caratteri su 3.000» per un file
    da 2.407 byte: la schermata sopravviveva — che era il punto — ma quel numero
    era una bugia. Con ``readable`` la UI puo' dire "non misurabile" invece di
    inventare uno zero.
    """
    try:
        from jenny.agent.memory import MemoryStore
        from jenny.agent.memory_budget import budget_report

        config = load_config()
        dream = config.agents.defaults.dream
        memory = MemoryStore(config.workspace_path)
        report = budget_report(
            memory,
            memory_chars=dream.memory_budget_chars,
            user_chars=dream.user_budget_chars,
            soul_chars=dream.soul_budget_chars,
        )
    except Exception as exc:  # noqa: BLE001 — la schermata si apre comunque
        logger.warning("could not measure the memory files for settings: {}", exc)
        return None
    return [
        {
            "label": entry.label,
            "chars": entry.chars,
            "budget": entry.budget,
            "exists": entry.path.exists(),
            "readable": os.access(entry.path, os.R_OK),
        }
        for entry in report
    ]


def _review_state() -> dict[str, int] | None:
    """Stato del review pass: passate dall'ultima, e i due contatori di stallo."""
    try:
        from jenny.agent.memory import MemoryStore

        memory = MemoryStore(load_config().workspace_path)
        runs_since_review, stuck_runs = memory.get_review_state()
        return {
            "runs_since_review": runs_since_review,
            "stuck_runs": stuck_runs,
            "nothing_new_runs": memory.get_nothing_new_runs(),
        }
    except Exception as exc:  # noqa: BLE001 — v. _memory_files
        logger.warning("could not read the Dream review state: {}", exc)
        return None


def memory_settings_payload(config: Config | None = None) -> dict[str, Any]:
    """Dream e i tetti dei file di memoria lunga."""
    cfg = (config or load_config()).agents.defaults.dream
    return {
        "enabled": cfg.enabled,
        "interval_h": _number(DreamConfig, "interval_h", cfg.interval_h),
        "schedule": cfg.describe_schedule(),
        "memory_budget_chars": _number(
            DreamConfig, "memory_budget_chars", cfg.memory_budget_chars
        ),
        "user_budget_chars": _number(DreamConfig, "user_budget_chars", cfg.user_budget_chars),
        "soul_budget_chars": _number(DreamConfig, "soul_budget_chars", cfg.soul_budget_chars),
        "review_every_runs": _number(DreamConfig, "review_every_runs", cfg.review_every_runs),
        # Il pavimento viaggia col payload: il dialogo di conferma lo fa il
        # client, e senza il numero dovrebbe tenerne una copia sua.
        "review_floor": REVIEW_CADENCE_FLOOR,
        "files": _memory_files(),
        "review_state": _review_state(),
    }


def worker_settings_payload(config: Config | None = None) -> dict[str, Any]:
    """Il giardiniere e la compattazione delle chat di progetto."""
    defaults = (config or load_config()).agents.defaults
    gardener = defaults.gardener
    return {
        "gardener": {
            "enabled": gardener.enabled,
            "interval_min": _number(GardenerConfig, "interval_min", gardener.interval_min),
            "idle_min": _number(GardenerConfig, "idle_min", gardener.idle_min),
            "min_hours_between_passes": _number(
                GardenerConfig,
                "min_hours_between_passes",
                gardener.min_hours_between_passes,
            ),
            "schedule": gardener.describe_schedule(),
        },
        # Non e' del giardiniere (sta in ``agents.defaults``) ma e' la stessa
        # decisione vista dall'altro lato, e vale dal prossimo avvio: v.
        # ``update_worker_settings``.
        "compact_projects_when_idle": defaults.compact_projects_when_idle,
    }


# ── Scrittura ────────────────────────────────────────────────────────────────


def _first(query: QueryParams, *names: str) -> str | None:
    """Il primo valore fra piu' nomi accettati (snake_case e camelCase)."""
    for name in names:
        values = query.get(name)
        if values:
            return values[0]
    return None


def _flag(query: QueryParams, *names: str) -> bool:
    """Un flag di conferma: vero solo se dichiarato esplicitamente vero."""
    return parse_flag(_first(query, *names))










# Le chiavi che, se presenti, chiedono un ri-armo del job di quel lavoratore.
# **Presenza e non cambio**: ``register_system_job`` e' idempotente e riparte da
# zero solo se la pianificazione e' cambiata, quindi ri-armare a intervallo
# identico non sposta la prossima scadenza; mentre perdersi la transizione di
# ``enabled`` lascerebbe scritto nel file un valore che nessun job va a leggere
# (su un gateway partito col lavoratore spento il job non e' registrato).
#
# **Questi due elenchi non vanno allungati**, ed e' l'opposto di cio' che si e'
# fatto al gancio del provider, dove un elenco gemello e' stato cancellato
# perche' marciva (v. ``_handle_settings_update`` in ``settings_routes.py``).
# Qui non marcisce, per costruzione: coprono ``enabled`` e l'intervallo, cioe'
# tutto cio' che vive nella *pianificazione* del job, mentre ogni altra
# manopola — i budget di Dream, ``idleMin``, ``minHoursBetweenPasses`` — la
# rilegge il lavoratore da disco a ogni run (v. i commenti gemelli in
# ``runtime/cron_dispatch.py``, ``_run_gardener`` e ``_run_dream``). Un campo
# nuovo qui dentro non e' inerte: vale al giro successivo. Aggiungercelo
# significherebbe solo ri-armare un orologio per un valore che non riguarda
# l'orologio.
MEMORY_REARM_KEYS = ("dream_enabled", "dreamEnabled", "dream_interval_h", "dreamIntervalH")
GARDENER_REARM_KEYS = (
    "gardener_enabled",
    "gardenerEnabled",
    "gardener_interval_min",
    "gardenerIntervalMin",
)


async def update_memory_settings(query: QueryParams) -> dict[str, Any]:
    """Scrive le manopole di Dream e i tre tetti della memoria lunga.

    Patch e non PUT: un campo assente dalla query non si tocca. Un valore
    identico lascia ``config.json`` intatto e non fa ruotare il ``.bak`` — e'
    quel che il comando faceva, ed e' pinnato dai suoi test.
    """
    review_raw = _first(query, "review_every_runs", "reviewEveryRuns")
    if review_raw is not None:
        # Validato **prima** di ``mutate``: un valore rifiutato non deve toccare
        # il file ne' far ruotare il backup.
        review = _parse_int(review_raw, "review_every_runs", DreamConfig, "review_every_runs")
        if review < REVIEW_CADENCE_FLOOR and not _flag(
            query, "confirm_back_to_back", "confirmBackToBack"
        ):
            raise WebUISettingsError(
                f"a review cadence below {REVIEW_CADENCE_FLOOR} runs needs an explicit "
                "confirmation: below it the review passes land on files a previous pass has "
                "already pruned, and keep looking for things to remove"
            )

    def _apply(config: Config) -> bool:
        dream = config.agents.defaults.dream
        changed = _apply_bool(query, dream, "enabled", "dream_enabled", "dreamEnabled")
        changed |= _apply_int(
            query, dream, "interval_h", DreamConfig, "dream_interval_h", "dreamIntervalH"
        )
        for attr, snake, camel in (
            ("memory_budget_chars", "memory_budget_chars", "memoryBudgetChars"),
            ("user_budget_chars", "user_budget_chars", "userBudgetChars"),
            ("soul_budget_chars", "soul_budget_chars", "soulBudgetChars"),
            ("review_every_runs", "review_every_runs", "reviewEveryRuns"),
        ):
            changed |= _apply_int(query, dream, attr, DreamConfig, snake, camel)
        return changed

    await store.mutate(_apply)
    return settings_payload()


async def update_worker_settings(query: QueryParams) -> dict[str, Any]:
    """Scrive le manopole del giardiniere e la compattazione progetti.

    ``compact_projects_when_idle`` e' la sola che non vale subito: la legge
    l'agente quando parte, quindi la risposta alza ``requires_restart`` e la UI
    lo dice sul posto invece che in un toast che scorre via.
    """
    seen: dict[str, bool] = {}

    def _apply(config: Config) -> bool:
        defaults = config.agents.defaults
        gardener = defaults.gardener
        changed = _apply_bool(
            query, gardener, "enabled", "gardener_enabled", "gardenerEnabled"
        )
        for attr, snake, camel in (
            ("interval_min", "gardener_interval_min", "gardenerIntervalMin"),
            ("idle_min", "gardener_idle_min", "gardenerIdleMin"),
            (
                "min_hours_between_passes",
                "gardener_min_hours_between_passes",
                "gardenerMinHoursBetweenPasses",
            ),
        ):
            changed |= _apply_int(query, gardener, attr, GardenerConfig, snake, camel)
        compact = _apply_bool(
            query,
            defaults,
            "compact_projects_when_idle",
            "compact_projects_when_idle",
            "compactProjectsWhenIdle",
        )
        seen["restart"] = compact
        return changed or compact

    await store.mutate(_apply)
    return settings_payload(requires_restart=seen.get("restart", False))
