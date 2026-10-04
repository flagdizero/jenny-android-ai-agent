"""I file che una versione precedente scriveva e questa non legge piu'.

Un lavoratore periodico ritirato lascia dietro di se' piu' del suo job cron
(``CronService.retire_system_job``): i file che produceva. Nessuno li rilegge,
nessuno li pota — chi li potava era il lavoratore — e uno di loro e' visibile
all'utente nel file browser sotto ``memory/``, dove uno **schermo che dice il
falso** e' esattamente il tipo di residuo che poi si scambia per un difetto.

Gira **a ogni avvio**, come l'estrazione dei template e la migrazione delle
wiki, e per la stessa ragione: e' l'unico modo in cui arriva su un telefono
installato da mesi. A regime costa uno ``stat`` per voce e zero scritture.

L'elenco e' chiuso e nomina i file uno per uno: qui la parola «atlas» e' un
**nome di file sul disco dell'utente**, non un concetto del codice, ed e' il
motivo per cui compare. Quel
che l'utente ha scritto di suo — ``memory/WIKI_POLICY.md`` — non e' in elenco.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

# Percorsi esatti, relativi al workspace.
_RETIRED_FILES: tuple[str, ...] = (
    "memory/WIKI.md",  # la rubrica compilata da Atlas
    "memory/.atlas_state.json",  # il suo fingerprint
    # Gli spilli della mappa a grafo dei quaderni, uscita dalla casa con la
    # bacheca (ottobre 2026): la bacheca tiene le sue posizioni dentro la
    # cartella del quaderno, e questo file non lo legge piu' nessuno.
    ".jenny/map-layout.json",
)

# Pattern ``glob`` relativi al workspace, per i file coniati per esecuzione.
_RETIRED_GLOBS: tuple[str, ...] = (
    "sessions/atlas_*.jsonl",  # una sessione per run, che Atlas potava da se'
)


def sweep_retired_artifacts(workspace: Path) -> list[Path]:
    """Toglie i file ritirati che ci sono. Ritorna quelli tolti; non solleva.

    Idempotente per costruzione: la seconda passata non trova niente e non
    scrive una riga di log — su un telefono il gateway riparte spesso, e un
    avviso a ogni avvio per un lavoro finito e' rumore.
    """
    removed: list[Path] = []
    candidates = [workspace / rel for rel in _RETIRED_FILES]
    for pattern in _RETIRED_GLOBS:
        candidates.extend(sorted(workspace.glob(pattern)))
    for path in candidates:
        try:
            if not path.is_file():
                continue
            path.unlink()
        except OSError as exc:
            logger.warning("could not remove the retired file {}: {}", path, exc)
            continue
        removed.append(path)
        logger.info("removed {}: written by a worker this version no longer runs",
                    path.relative_to(workspace).as_posix())
    return removed
