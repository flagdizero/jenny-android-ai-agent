"""Il composition root registra i lavori periodici che la config chiede.

La proprietà è una sola, e vale per tutti e quattro i job condizionali:
**se la config lo accende, ``GatewayContainer.build()`` lo registra nel cron con
l'orario configurato; se la spegne, non lo registra.**

Perché serve un modulo a parte. Ogni pezzo è già provato in isolamento: la
config sa costruire il proprio ``CronSchedule``, il ``CronDispatcher`` sa
smistare un job *chiamato* "gardener". Nessuno provava la **giuntura**, cioè che
il composition root crei davvero quel job — e una giuntura non provata è una
funzione che si può spedire morta. Misurato il 23/08 con una mutazione:
``if gardener_cfg.enabled:`` -> ``if False:`` passava tutta la suite.

Questi test costruiscono il grafo **vero** (``build()``), non un finto: senza
provider configurato ``build()`` prende la strada dell'onboarding — nessun
agente, nessuna rete — ma registra i job come in produzione. È la sola forma in
cui il test misura la giuntura invece di riasserire la config.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from jenny.config import paths as paths_mod
from jenny.config.schema import Config
from jenny.cron.types import CronJob
from jenny.runtime.container import GatewayContainer

_MINUTE_MS = 60_000
_HOUR_MS = 3_600_000


@pytest.fixture
def workspace(tmp_path: Path) -> Iterator[Path]:
    """Workspace isolato per la durata del test, e quello di prima ripristinato.

    ``set_workspace_dir`` è globale di processo (``config.workspace_path`` lo
    legge), e la suite ne ha uno di sessione: lasciarlo spostato farebbe scrivere
    i test successivi qui dentro.
    """
    previous = paths_mod.get_workspace_path()
    root = tmp_path / "workspace"
    root.mkdir()
    paths_mod.set_workspace_dir(str(root))
    try:
        yield root
    finally:
        paths_mod.set_workspace_dir(str(previous) if previous else "")


def _jobs(config: Config) -> dict[str, CronJob]:
    """Costruisce il grafo e ritorna i job registrati, per id."""
    container = GatewayContainer(config)
    container.build()
    return {job.id: job for job in container.cron.list_jobs(include_disabled=True)}


# Ogni riga: id del job, come lo si spegne, come si configura il suo orario,
# e quanti ms quell'orario deve valere. Un solo elenco perché la proprietà è
# una sola — e perché un job condizionale aggiunto senza toccarlo non risulterebbe
# coperto, che è esattamente il difetto che questo modulo chiude.
_CONDITIONAL_JOBS = [
    pytest.param(
        "dream",
        lambda c, on: setattr(c.agents.defaults.dream, "enabled", on),
        lambda c: setattr(c.agents.defaults.dream, "interval_h", 3),
        3 * _HOUR_MS,
        id="dream",
    ),
    pytest.param(
        "gardener",
        lambda c, on: setattr(c.agents.defaults.gardener, "enabled", on),
        lambda c: setattr(c.agents.defaults.gardener, "interval_min", 17),
        17 * _MINUTE_MS,
        id="gardener",
    ),
    pytest.param(
        "heartbeat",
        lambda c, on: setattr(c.gateway.heartbeat, "enabled", on),
        lambda c: setattr(c.gateway.heartbeat, "interval_s", 111),
        111 * 1000,
        id="heartbeat",
    ),
    # L'orologio dei quaderni non ha un intervallo suo: un giro all'ora e'
    # la forma del lavoro (le attese sono a giorno o a mese). Lo accende la wiki.
    pytest.param(
        "reminders",
        lambda c, on: setattr(c.wiki, "enabled", on),
        lambda c: None,
        60 * _MINUTE_MS,
        id="reminders",
    ),
    pytest.param(
        "update_check",
        lambda c, on: setattr(c.updates, "enabled", on),
        lambda c: setattr(c.updates, "check_interval_h", 7),
        7 * _HOUR_MS,
        id="update_check",
    ),
]


@pytest.mark.parametrize(("job_id", "toggle", "set_interval", "every_ms"), _CONDITIONAL_JOBS)
def test_an_enabled_job_is_registered_with_its_configured_schedule(
    workspace: Path, job_id: str, toggle, set_interval, every_ms: int
) -> None:
    """L'orario è quello *della config*, non un default riasserito.

    Il numero è deliberatamente strambo (17 minuti, 9 ore): con il default,
    un ``build_schedule()`` cablato a mano nel container passerebbe comunque.
    """
    config = Config()
    toggle(config, True)
    set_interval(config)

    job = _jobs(config).get(job_id)

    assert job is not None, f"{job_id} acceso in config e non registrato dal container"
    assert job.schedule.kind == "every"
    assert job.schedule.every_ms == every_ms
    assert job.payload.kind == "system_event"


@pytest.mark.parametrize(("job_id", "toggle", "set_interval", "every_ms"), _CONDITIONAL_JOBS)
def test_a_disabled_job_is_not_registered(
    workspace: Path, job_id: str, toggle, set_interval, every_ms: int
) -> None:
    """E gli altri restano: così l'assenza non è "build() non ha fatto niente"."""
    config = Config()
    toggle(config, False)

    jobs = _jobs(config)

    assert job_id not in jobs, f"{job_id} spento in config e registrato comunque"
    others = {p.values[0] for p in _CONDITIONAL_JOBS} - {job_id}
    assert others <= set(jobs), f"spegnere {job_id} ha portato via anche {others - set(jobs)}"


def test_by_default_all_periodic_jobs_are_registered(workspace: Path) -> None:
    """I default di produzione, non solo i valori iniettati.

    Il container non è l'unico modo di spegnere un lavoro: se un domani un
    default passasse a ``False`` per sbaglio, i test sopra — che accendono
    esplicitamente — resterebbero verdi.
    """
    assert set(_jobs(Config())) == {p.values[0] for p in _CONDITIONAL_JOBS}
