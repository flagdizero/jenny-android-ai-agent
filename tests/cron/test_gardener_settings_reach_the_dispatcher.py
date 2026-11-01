"""Una manopola del giardiniere cambiata a caldo arriva al cron, senza riavvio.

È la metà mancante di T2.8. Uno slash command che scrive ``config.json`` e un job
periodico che legge il ``Config`` catturato all'avvio del container fanno una
manopola che *sembra* funzionare: la chat conferma il numero nuovo, e il
consumatore vero — il tick ogni mezz'ora — continua col vecchio finché qualcuno
non riavvia il gateway. È il modo peggiore in cui un'impostazione può non
funzionare, e cade proprio sull'interruttore ``enabled``, cioè su quello che si
usa per **fermare** una cosa.

Le due metà sono due perché i valori vivono in due posti:

* ``enabled``, ``idle_min``, ``min_hours_between_passes`` stanno nel ``Config``, e
  ``CronDispatcher._run_gardener`` li rilegge da disco a ogni tick (stessa strada
  di ``_run_dream``, stessa ragione);
* ``interval_min`` **non** sta più nel ``Config`` al momento del tick: è diventato
  lo ``schedule`` del ``CronJob`` nello store del cron, e nessuna rilettura di
  ``config.json`` lo tocca. Lì serve ``refresh_system_job`` con ``GARDENER_JOB_ID``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from jenny.config.loader import get_config_path, save_config
from jenny.config.schema import Config
from jenny.cron.service import CronService
from jenny.runtime.cron_dispatch import (
    GARDENER_JOB_ID,
    CronDispatcher,
    refresh_system_job,
)

_JOB = SimpleNamespace(
    name="gardener", id="gardener", payload=SimpleNamespace(kind="system_event")
)


class _FakeAgent:
    def __init__(self, sessions_dir: Path) -> None:
        self.context = SimpleNamespace(memory=None, timezone=None)
        self.sessions = SimpleNamespace(
            sessions_dir=sessions_dir,
            read_session_metadata=lambda key: None,
        )

    def active_session_keys(self) -> tuple[str, ...]:
        return ()

    def evict_pruned_sessions(self, keys) -> None:
        pass


@pytest.fixture(autouse=True)
def _workspace(tmp_path, monkeypatch):
    """Workspace e ``config.json`` tutti dentro ``tmp_path``.

    Il ``config_path`` va fissato esplicitamente: da quando ``_run_gardener``
    rilegge da disco, un test senza override andrebbe a leggere il `config.json`
    di chi esegue la suite.
    """
    from jenny.config import paths
    from jenny.runtime.context import get_runtime_context

    previous = paths.get_workspace_path()
    paths.set_workspace_dir(str(tmp_path))
    monkeypatch.setattr(get_runtime_context(), "config_path", tmp_path / "config.json")
    yield
    paths.set_workspace_dir(str(previous))


def _write(**knobs) -> Config:
    """Scrive ``config.json`` con quei knob: è ciò che fa ``/gardener``."""
    config = Config()
    for key, value in knobs.items():
        setattr(config.agents.defaults.gardener, key, value)
    save_config(config, get_config_path())
    return config


def _project(tmp_path: Path, name: str = "viaggio") -> None:
    root = tmp_path / "wikis" / name
    (root / "wiki").mkdir(parents=True)
    (root / "raw" / "journal").mkdir(parents=True)
    (root / "raw" / "journal" / "20260823.md").write_text("- 09:00 — x\n", encoding="utf-8")


def _dispatcher(agent, startup: Config) -> CronDispatcher:
    return CronDispatcher(
        get_agent=lambda: agent,
        config=startup,
        cron=MagicMock(),
    )


# -- la metà che si rilegge da disco ------------------------------------------


async def test_off_written_after_startup_stops_the_next_tick(tmp_path, monkeypatch):
    """L'``off`` che l'utente ha appena scritto vale al tick dopo, non dopo un riavvio.

    Il dispatcher nasce con il giardiniere **acceso**, come su un telefono che ha
    avviato il gateway prima del comando; poi ``config.json`` dice ``false``. Se il
    tick guardasse ancora il ``Config`` di avvio, spegnere non spegnerebbe.
    """
    called: list[str] = []

    async def _fake_run(agent, store):
        called.append(store.name)

    monkeypatch.setattr("jenny.agent.gardener.run_gardener", _fake_run)
    _project(tmp_path)
    startup = _write(enabled=True)
    dispatcher = _dispatcher(_FakeAgent(tmp_path), startup)

    _write(enabled=False)
    await dispatcher._dispatch(_JOB)

    assert called == []
    # E il ``Config`` di avvio è rimasto quello che era: non lo stiamo mutando, lo
    # stiamo scavalcando con la lettura da disco. Senza questa riga il test
    # resterebbe verde anche se qualcuno "risolvesse" mutando l'oggetto catturato,
    # che è esattamente il rimedio sbagliato (due sorgenti di verità).
    assert startup.agents.defaults.gardener.enabled is True


async def test_the_two_clocks_written_after_startup_reach_the_next_tick(
    tmp_path, monkeypatch
):
    """``idle_min`` e ``min_hours_between_passes`` sono l'altra metà del freno.

    Non basta che ``enabled`` sia vivo: sono questi due a decidere *se entrare* in
    un progetto, e un utente che li allarga per farsi lasciare in pace deve essere
    lasciato in pace subito. Si guarda cosa arriva a ``pick_project``, perché è
    l'unico consumatore e il resto sarebbe una prova indiretta.
    """
    seen: dict[str, int] = {}

    def _fake_pick(workspace, **kwargs):
        seen.update(kwargs)
        return None

    monkeypatch.setattr("jenny.agent.gardener_schedule.pick_project", _fake_pick)
    _project(tmp_path)
    dispatcher = _dispatcher(_FakeAgent(tmp_path), _write(idle_min=30, min_hours_between_passes=6))

    _write(idle_min=600, min_hours_between_passes=48)
    await dispatcher._dispatch(_JOB)

    assert seen["idle_min"] == 600
    assert seen["min_hours_between_passes"] == 48


# -- la metà che vive nello store del cron ------------------------------------


def _cron(tmp_path: Path) -> CronService:
    return CronService(store_path=tmp_path / "cron" / "jobs.json")


def _gardener_job(cron: CronService):
    store = cron._load_store()
    return next((j for j in store.jobs if j.id == GARDENER_JOB_ID), None)


def test_a_new_interval_arms_the_job_without_a_restart(tmp_path):
    """``interval_min`` non è nel ``Config`` al momento del tick: è lo ``schedule``.

    La rilettura da disco non lo raggiunge per costruzione — il job è già scritto
    nello store del cron — quindi senza questa ri-registrazione ``/gardener
    interval 120`` confermerebbe un numero che nessuno applica.
    """
    cron = _cron(tmp_path)
    refresh_system_job(cron, GARDENER_JOB_ID, config=_write(interval_min=30))
    assert _gardener_job(cron).schedule.every_ms == 30 * 60_000

    described = refresh_system_job(cron, GARDENER_JOB_ID, config=_write(interval_min=120))

    assert _gardener_job(cron).schedule.every_ms == 120 * 60_000
    assert described is not None and "120min" in described


def test_turning_it_on_registers_the_job_a_disabled_startup_never_created(tmp_path):
    """Il caso che rende questa funzione necessaria e non comoda.

    ``GatewayContainer.build`` registra il job **solo se acceso**. Su un gateway
    partito col giardiniere spento non c'è nessun job da riarmare, quindi
    ``/gardener on`` scriverebbe un ``enabled=True`` che nessuno va a leggere: la
    riaccensione sarebbe il solo comando a pretendere un riavvio.
    """
    cron = _cron(tmp_path)
    assert _gardener_job(cron) is None

    refresh_system_job(cron, GARDENER_JOB_ID, config=_write(enabled=True, interval_min=45))

    job = _gardener_job(cron)
    assert job is not None and job.schedule.every_ms == 45 * 60_000


def test_a_disabled_gardener_is_not_registered_and_is_not_an_error(tmp_path):
    """Spegnere non deregistra niente: ``register_system_job`` non ha controparte, e
    il cancello è quello di dispatch. Qui si verifica solo che il refresh non armi
    una pianificazione per una sezione spenta, e che lo dica con ``None``."""
    cron = _cron(tmp_path)

    assert refresh_system_job(cron, GARDENER_JOB_ID, config=_write(enabled=False)) is None
    assert _gardener_job(cron) is None


def test_re_arming_the_same_interval_does_not_push_the_next_run_away(tmp_path):
    """Il conto alla rovescia non deve arretrare a ogni comando.

    ``register_system_job`` riparte da zero solo se la pianificazione è cambiata;
    se un domani perdesse quella proprietà, ``/gardener idle 45`` ripetuto qualche
    volta rimanderebbe la passata all'infinito senza che niente lo dica.
    """
    cron = _cron(tmp_path)
    refresh_system_job(cron, GARDENER_JOB_ID, config=_write(interval_min=30))
    first = _gardener_job(cron).state.next_run_at_ms

    refresh_system_job(cron, GARDENER_JOB_ID, config=_write(interval_min=30))

    assert _gardener_job(cron).state.next_run_at_ms == first


# -- e gli altri due lavoratori ----------------------------------------------


@pytest.mark.parametrize(
    ("worker", "attr", "value", "expected_ms"),
    [
        ("dream", "interval_h", 4, 4 * 3_600_000),
        ("gardener", "interval_min", 45, 45 * 60_000),
    ],
)
def test_every_periodic_worker_can_be_re_armed(tmp_path, worker, attr, value, expected_ms):
    """Dal 31/08/2026 la controparte vale per tutti e due, non solo per il giardiniere.

    Prima esisteva solo la versione del giardiniere, perche' era l'unico con un
    interruttore raggiungibile: le manopole di Dream stavano in ``/dream budget``,
    che non ne aveva uno. Portandole in
    Impostazioni, spegnere Dream o cambiarne l'intervallo ha avuto bisogno
    dello stesso ri-armo — altrimenti sarebbero due manopole che chiedono un
    riavvio senza dirlo.
    """
    cron = _cron(tmp_path)
    config = Config()
    setattr(getattr(config.agents.defaults, worker), attr, value)
    save_config(config, get_config_path())

    described = refresh_system_job(cron, worker, config=config)

    job = next((j for j in cron._load_store().jobs if j.id == worker), None)
    assert job is not None and job.schedule.every_ms == expected_ms
    assert described


@pytest.mark.parametrize("worker", ["dream", "gardener"])
def test_a_worker_that_is_off_is_not_registered_and_is_not_an_error(tmp_path, worker):
    cron = _cron(tmp_path)
    config = Config()
    getattr(config.agents.defaults, worker).enabled = False
    save_config(config, get_config_path())

    assert refresh_system_job(cron, worker, config=config) is None
    assert not [j for j in cron._load_store().jobs if j.id == worker]


def test_a_worker_nobody_registered_is_a_programming_error(tmp_path):
    """Elenco chiuso e non un ``getattr`` sul nome: un refuso in una route
    scriverebbe altrimenti un job di sistema che nessun dispatch sa smistare."""
    with pytest.raises(ValueError, match="unknown system worker"):
        refresh_system_job(_cron(tmp_path), "heartbeat")
