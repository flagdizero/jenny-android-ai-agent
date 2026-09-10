"""Un lavoratore periodico spento non fa niente al tick successivo.

È una **classe** di difetti e non un caso singolo. ``GatewayContainer.build``
registra ogni job di sistema *solo se acceso*, ma ``register_system_job`` non ha
una controparte che deregistri e ``remove_job`` protegge i ``system_event``:
quindi il job scritto da un avvio in cui il lavoratore era acceso resta nello
store del cron e scatta per sempre, riavvii compresi. L'unica cosa che lo ferma
è un cancello su ``enabled`` **dentro il gestore**, su config riletta da disco.

Dream non ce l'aveva. Spegnerlo dalle impostazioni non lo spegneva: il ciclo
continuava a girare ogni due ore — snapshot, turno LLM, riscritture di memoria,
potatura delle sessioni — e sopravviveva al riavvio. Il difetto è del 09/09/2026
e il gemello per il giardiniere esisteva già; scritto quel giorno, questo file
sarebbe stato **rosso** su Dream e sull'heartbeat.

Il caso del giardiniere resta dov'è nato, in
``test_gardener_settings_reach_the_dispatcher.py``, insieme all'altra metà che
qui non c'entra (l'intervallo, che non vive nel ``Config`` al momento del tick ma
nello ``schedule`` del job e ha bisogno di ``refresh_system_job``).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from jenny.agent import dream_cycle
from jenny.config.loader import get_config_path, save_config
from jenny.config.schema import Config
from jenny.cron.types import CronJob, CronPayload
from jenny.runtime.cron_dispatch import CronDispatcher

_DREAM_JOB = SimpleNamespace(name="dream", id="dream")


def _heartbeat_job() -> CronJob:
    """Il job vero e non un doppio: il ramo heartbeat legge e scrive
    ``job.state``, quindi un ``SimpleNamespace`` senza stato darebbe un rosso
    che parla d'altro il giorno in cui il cancello sparisse."""
    return CronJob(
        id="heartbeat", name="heartbeat", payload=CronPayload(kind="system_event")
    )

_HEARTBEAT_MD = """# Heartbeat

## Active Tasks

- Ogni ciclo, controlla l'umidità del suolo e avvertimi solo sotto il 15%.
"""


@pytest.fixture(autouse=True)
def _workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Workspace e ``config.json`` tutti dentro ``tmp_path``.

    Il ``config_path`` va fissato esplicitamente: i cancelli leggono da disco, e
    un test senza override andrebbe a leggere il ``config.json`` di chi esegue la
    suite — cioè passerebbe o fallirebbe a seconda di come quella persona ha
    lasciato le proprie impostazioni.
    """
    from jenny.config import paths
    from jenny.runtime.context import get_runtime_context

    previous = paths.get_workspace_path()
    paths.set_workspace_dir(str(tmp_path))
    monkeypatch.setattr(get_runtime_context(), "config_path", tmp_path / "config.json")
    yield
    paths.set_workspace_dir(str(previous))


def _write(**sections) -> Config:
    """Scrive ``config.json``: è ciò che fa l'interruttore in Impostazioni."""
    config = Config()
    for dotted, value in sections.items():
        target = config
        *path, attr = dotted.split("__")
        for step in path:
            target = getattr(target, step)
        setattr(target, attr, value)
    save_config(config, get_config_path())
    return config


# -- Dream ---------------------------------------------------------------------


class _FakeDreamAgent:
    def __init__(self) -> None:
        self.context = SimpleNamespace(memory=None)

    def evict_pruned_sessions(self, keys) -> None:  # pragma: no cover - non raggiunto
        pass


@pytest.fixture
def dream_spies(monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:
    """Spie sui due gesti che un tick spento non deve fare.

    La presa risponde **no**, cioè "un ciclo è già in corso": il tick acceso si
    ferma una riga dopo il cancello, e ``claims`` diventa la misura esatta di
    *«il cancello ha lasciato passare»* senza che questo file debba tenere in
    piedi un doppio dell'intero ciclo di Dream.

    ``begin_dream_cycle`` resta come rete: se un giorno il ciclo partisse lo
    stesso, il fallimento dice *cosa* è successo invece di morire più in là su un
    doppio incompleto. Gli import in ``_run_dream``/``_dream_cycle`` sono locali
    alla funzione, quindi sostituire l'attributo del modulo basta.
    """
    claims: list[str] = []
    starts: list[str] = []

    def _claim() -> bool:
        claims.append("claim")
        return False

    async def _begin(*_args, **_kwargs):
        starts.append("begin")
        raise AssertionError(
            "il ciclo di Dream è partito con l'interruttore spento"
        )

    monkeypatch.setattr(dream_cycle, "claim_dream_cycle", _claim)
    monkeypatch.setattr(dream_cycle, "release_dream_cycle", lambda: None)
    monkeypatch.setattr(dream_cycle, "begin_dream_cycle", _begin)
    return {"claims": claims, "starts": starts}


def _dream_dispatcher(startup: Config) -> CronDispatcher:
    return CronDispatcher(
        get_agent=lambda: _FakeDreamAgent(),
        config=startup,
        cron=MagicMock(),
        heartbeat_cfg=SimpleNamespace(),
    )


async def test_dream_off_written_after_startup_stops_the_next_tick(dream_spies) -> None:
    """L'``off`` che l'utente ha appena girato vale al tick dopo, non dopo un riavvio.

    Il dispatcher nasce con Dream **acceso**, come su un telefono che ha avviato
    il gateway prima che l'interruttore fosse toccato; poi ``config.json`` dice
    ``false``. Se il tick guardasse ancora il ``Config`` di avvio — o non
    guardasse niente, che è com'era — spegnere non spegnerebbe.
    """
    startup = _write(agents__defaults__dream__enabled=True)
    dispatcher = _dream_dispatcher(startup)

    _write(agents__defaults__dream__enabled=False)
    assert await dispatcher.dispatch(_DREAM_JOB) is None

    assert dream_spies["starts"] == []
    # E il ``Config`` di avvio è rimasto quello che era: non lo stiamo mutando, lo
    # stiamo scavalcando con la lettura da disco. Senza questa riga il test
    # resterebbe verde anche se qualcuno "risolvesse" mutando l'oggetto catturato,
    # che è esattamente il rimedio sbagliato (due sorgenti di verità).
    assert startup.agents.defaults.dream.enabled is True


async def test_a_dream_tick_that_is_off_does_not_even_take_the_claim(dream_spies) -> None:
    """Il cancello sta **prima** della presa, e questa è l'unica riga che lo dice.

    La presa è condivisa con il ``/dream`` battuto a mano, che non è serializzato
    con niente: un tick spento non deve nemmeno contendersela. Spostare il
    controllo dentro ``_dream_cycle`` lascerebbe verde il test qui sopra.
    """
    dispatcher = _dream_dispatcher(_write(agents__defaults__dream__enabled=True))

    _write(agents__defaults__dream__enabled=False)
    await dispatcher.dispatch(_DREAM_JOB)

    assert dream_spies["claims"] == []


async def test_dream_on_reaches_the_claim(dream_spies) -> None:
    """Il controllo negativo: acceso, il tick passa il cancello.

    Senza questa riga un cancello sempre chiuso — un refuso sul nome del campo,
    una negazione di troppo — passerebbe i due test qui sopra a pieni voti. Che
    il tick si fermi subito dopo, sulla presa che risponde "occupato", è il
    doppio e non il comportamento: qui interessa solo che il cancello lo abbia
    lasciato arrivare fin lì.
    """
    dispatcher = _dream_dispatcher(_write(agents__defaults__dream__enabled=True))

    assert await dispatcher.dispatch(_DREAM_JOB) == "dream: already running"

    assert dream_spies["claims"] == ["claim"]
    assert dream_spies["starts"] == []


# -- Heartbeat -----------------------------------------------------------------


class _FakeHeartbeatSession:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    def retain_recent_legal_suffix(self, keep: int) -> None:  # pragma: no cover
        pass


class _FakeHeartbeatAgent:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.sessions = SimpleNamespace(
            get_or_create=lambda _key: _FakeHeartbeatSession(),
            save=lambda _session: None,
        )

    async def process_direct_outcome(self, prompt: str, **kwargs):  # pragma: no cover
        self.calls.append({"prompt": prompt, **kwargs})
        raise AssertionError("il turno dell'heartbeat è partito con la sezione spenta")

    def evict_pruned_sessions(self, keys) -> None:  # pragma: no cover - non raggiunto
        pass


async def test_heartbeat_off_written_after_startup_stops_the_next_tick(
    tmp_path: Path,
) -> None:
    """Stessa storia dell'heartbeat, che il cancello non ce l'aveva affatto.

    Nella WebUI la sezione è oggi in sola lettura, quindi il ``false`` lo si
    scrive nel file — ed è proprio il caso peggiore: il job registrato da un
    avvio precedente sopravvive al riavvio che dovrebbe applicarlo, perché il
    ramo che *non* lo registra non ha nessun modo di toglierlo.

    Il controllo negativo qui non serve scriverlo: ``test_cron_dispatch_heartbeat.py``
    è per intero un heartbeat **acceso** che deve arrivare al turno, e un cancello
    sempre chiuso lo spegnerebbe tutto.
    """
    (tmp_path / "HEARTBEAT.md").write_text(_HEARTBEAT_MD, encoding="utf-8")
    agent = _FakeHeartbeatAgent()
    dispatcher = CronDispatcher(
        get_agent=lambda: agent,
        # ``workspace_path`` è una property su ``Config``: il ramo heartbeat legge
        # solo quello da ``self._config``, quindi un doppio esplicito è più onesto
        # (stessa scelta di ``test_cron_dispatch_heartbeat.py``). Il cancello,
        # invece, legge il ``config.json`` scritto qui sotto.
        config=SimpleNamespace(workspace_path=tmp_path),
        cron=MagicMock(),
        heartbeat_cfg=SimpleNamespace(enabled=True, keep_recent_messages=8),
    )

    _write(gateway__heartbeat__enabled=False)
    assert await dispatcher.dispatch(_heartbeat_job()) is None

    assert agent.calls == []
