"""Instradamento del job ``gardener`` nel ``CronDispatcher``.

Il dispatcher non deve contenere logica del giardiniere: sceglie il progetto con
``pick_project`` e chiama ``run_gardener``. Stesso motivo di Dream — se
un giorno qualcuno reimplementa qui la selezione, questo test resta verde ma il
prossimo cambiamento andrà fatto in due posti.

Il test che vale più degli altri è quello su ``enabled``:
``register_system_job`` non ha una controparte che deregistri, quindi un job
registrato da un avvio precedente **resta nello store del cron** anche dopo che
la sezione è stata spenta. Se il controllo vivesse solo alla registrazione,
spegnere il giardiniere non lo spegnerebbe.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from jenny.config.schema import Config
from jenny.runtime.cron_dispatch import CronDispatcher

_JOB = SimpleNamespace(
    name="gardener", id="gardener", payload=SimpleNamespace(kind="system_event")
)


class _FakeAgent:
    def __init__(self, sessions_dir: Path, *, active: tuple[str, ...] = ()) -> None:
        self.context = SimpleNamespace(memory=None, timezone=None)
        self.sessions = SimpleNamespace(
            sessions_dir=sessions_dir,
            read_session_metadata=lambda key: None,
        )
        self._active = active

    def active_session_keys(self) -> tuple[str, ...]:
        return self._active

    async def process_direct(self, prompt: str, **_kwargs):
        return SimpleNamespace(metadata={"_stop_reason": "completed"}, usage={})

    def evict_pruned_sessions(self, keys) -> None:
        pass


class _BrokenAgent(_FakeAgent):
    """Un agente il cui turno salta: il ``run_gardener`` **vero** ne fa ``failed``.

    Serve a non stubbare ``run_gardener`` nei test sul timbro. Il timbro lo mette
    ``run_gardener`` (per coprire anche ``/gardener``, non solo il cron), quindi un
    test che stubba il runner e poi controlla lo stato su disco misurerebbe una
    cosa che nessuno fa più — verde, e cieco.
    """

    async def process_direct(self, prompt: str, **_kwargs):
        raise RuntimeError("provider giù")

    # Nota: in questo fixture la passata non arriva nemmeno qui — i template dei
    # prompt non sono sincronizzati sotto ``tmp_path``, quindi ``build_prompt``
    # salta prima. Va bene per questi test (l'esito è ``failed`` in entrambi i
    # casi, ed è quello che si sta misurando) e resta valido se un domani il
    # fixture i template li sincronizza.


def _dispatcher(agent, config: Config | None = None) -> CronDispatcher:
    return CronDispatcher(
        get_agent=lambda: agent,
        config=config or Config(),
        cron=MagicMock(),
    )


@pytest.fixture(autouse=True)
def _workspace(tmp_path, monkeypatch):
    """``Config.workspace_path`` viene dal contesto runtime, non dal campo.

    Non è un dettaglio da test: è la stessa strada che ``_run_gardener`` percorre
    (``GardenerStore`` costruito da ``self._config.workspace_path``), quindi
    spostare il workspace qui prova il dispatcher nelle condizioni vere.

    Il ``config_path`` è fissato qui insieme al workspace perché ``_run_gardener``
    rilegge i knob **da disco** a ogni tick (v. ``_config``): senza un percorso
    proprio i test finirebbero a leggere il `config.json` di chi esegue la suite,
    o quello lasciato da un altro file.
    """
    from jenny.config import paths
    from jenny.runtime.context import get_runtime_context

    previous = paths.get_workspace_path()
    paths.set_workspace_dir(str(tmp_path))
    monkeypatch.setattr(get_runtime_context(), "config_path", tmp_path / "config.json")
    yield
    paths.set_workspace_dir(str(previous))


def _config(**knobs) -> Config:
    """La config dei knob, **scritta su disco** e non solo passata al dispatcher.

    ``_run_gardener`` la rilegge da ``config.json`` a ogni tick, per la stessa
    ragione di ``_run_dream``: il ``Config`` catturato dal container non lo
    aggiorna nessuno, quindi ``enabled=False`` scritto da ``/gardener off`` non
    arriverebbe mai al job. Un test che passasse solo l'oggetto misurerebbe una
    strada che il codice non percorre più.
    """
    from jenny.config.loader import get_config_path, save_config

    config = Config()
    for key, value in knobs.items():
        setattr(config.agents.defaults.gardener, key, value)
    save_config(config, get_config_path())
    return config


def _project(tmp_path: Path, name: str = "viaggio") -> Path:
    root = tmp_path / "wikis" / name
    (root / "wiki").mkdir(parents=True)
    (root / "raw" / "journal").mkdir(parents=True)
    (root / "raw" / "journal" / "20260823.md").write_text("- 09:00 — x\n", encoding="utf-8")
    return root


@pytest.mark.asyncio
async def test_the_job_reaches_run_gardener_with_the_picked_project(tmp_path, monkeypatch):
    seen: dict[str, object] = {}

    async def _fake_run(agent, store, **_kw):
        from jenny.agent.gardener import GardenerOutcome

        seen["agent"] = agent
        seen["project"] = store.name
        return GardenerOutcome(status="written", elapsed=0.1, lines=2, writes=1)

    monkeypatch.setattr("jenny.agent.gardener.run_gardener", _fake_run)

    _project(tmp_path)

    agent = _FakeAgent(tmp_path)
    await _dispatcher(agent, _config())._dispatch(_JOB)

    assert seen["project"] == "viaggio"
    assert seen["agent"] is agent


@pytest.mark.asyncio
async def test_a_disabled_gardener_does_not_run_even_if_the_job_fires(tmp_path, monkeypatch):
    called: list[str] = []

    async def _fake_run(agent, store, **_kw):
        called.append(store.name)

    monkeypatch.setattr("jenny.agent.gardener.run_gardener", _fake_run)
    _project(tmp_path)

    await _dispatcher(_FakeAgent(tmp_path), _config(enabled=False))._dispatch(_JOB)

    assert called == []


@pytest.mark.asyncio
async def test_no_project_ready_means_no_call(tmp_path, monkeypatch):
    called: list[str] = []

    async def _fake_run(agent, store, **_kw):
        called.append(store.name)

    monkeypatch.setattr("jenny.agent.gardener.run_gardener", _fake_run)
    (tmp_path / "wikis").mkdir()

    await _dispatcher(_FakeAgent(tmp_path), _config())._dispatch(_JOB)

    assert called == []


@pytest.mark.asyncio
async def test_the_in_flight_sessions_come_from_the_agent(tmp_path, monkeypatch):
    """Il dispatcher deve *chiedere* all'agente chi sta lavorando adesso: se non
    lo passasse, il cancello più importante sarebbe scavalcato da chi lo chiama.
    """
    called: list[str] = []

    async def _fake_run(agent, store, **_kw):
        called.append(store.name)

    monkeypatch.setattr("jenny.agent.gardener.run_gardener", _fake_run)
    _project(tmp_path)

    await _dispatcher(
        _FakeAgent(tmp_path, active=("project:viaggio",)), _config()
    )._dispatch(_JOB)

    assert called == []


# ── Una passata che non registra niente: timbro, esito, allarme ──────────────


def _outcome(status: str, **kw):
    from jenny.agent.gardener import GardenerOutcome

    return GardenerOutcome(status=status, elapsed=0.1, lines=2, **kw)


def _returning(status: str, **kw):
    async def _fake_run(agent, store, **_kw):
        return _outcome(status, **kw)

    return _fake_run


@pytest.mark.asyncio
async def test_a_failed_pass_is_not_reported_as_ok(tmp_path, monkeypatch):
    """Il dispatcher ritornava ``None`` per **ogni** esito, quindi una passata
    fallita e una riuscita erano indistinguibili da fuori: nel record del job
    finiva "ok" e chi andava a guardare vedeva un giardiniere che funziona."""
    monkeypatch.setattr("jenny.agent.gardener.run_gardener", _returning("no_write"))
    _project(tmp_path)

    result = await _dispatcher(_FakeAgent(tmp_path), _config())._dispatch(_JOB)

    assert result is not None and "no_write" in result and "viaggio" in result


@pytest.mark.asyncio
async def test_a_pass_that_wrote_says_nothing(tmp_path, monkeypatch):
    """Il controllo dell'altro verso: senza questo, un ``_run_gardener`` che
    ritornasse sempre una stringa passerebbe il test sopra."""
    monkeypatch.setattr("jenny.agent.gardener.run_gardener", _returning("written", writes=1))
    _project(tmp_path)

    assert await _dispatcher(_FakeAgent(tmp_path), _config())._dispatch(_JOB) is None


@pytest.mark.asyncio
async def test_a_failed_pass_is_stamped_so_the_next_tick_skips_it(tmp_path, monkeypatch):
    """Senza il timbro, il tick di mezz'ora dopo rifà la stessa passata, e così
    ogni mezz'ora. Il runner qui è quello **vero** — il timbro vive dentro
    ``run_gardener`` perché ``/gardener`` deve contare come il cron, e stubbarlo
    renderebbe questo test verde qualunque cosa succeda al timbro."""
    from jenny.agent.gardener_state import read_state

    root = _project(tmp_path)

    await _dispatcher(_BrokenAgent(tmp_path), _config())._dispatch(_JOB)

    state = read_state(root)
    assert state.last_attempt_at is not None and state.failures == 1
    assert state.cursor == {}, "il cursore non deve muoversi su una passata fallita"


@pytest.mark.asyncio
async def test_a_skipped_tick_is_not_counted_as_an_attempt(tmp_path, monkeypatch):
    """``skipped_no_delta`` è il caso normale di un tick, non un insuccesso: non
    ha chiamato nessun provider, e timbrarlo sposterebbe in avanti la distanza di
    una passata che non è mai partita."""
    from jenny.agent.gardener_state import read_state

    monkeypatch.setattr("jenny.agent.gardener.run_gardener", _returning("skipped_no_delta"))
    root = _project(tmp_path)

    result = await _dispatcher(_FakeAgent(tmp_path), _config())._dispatch(_JOB)

    assert result is None
    assert read_state(root).last_attempt_at is None
    assert read_state(root).failures == 0


@pytest.mark.asyncio
async def test_a_tick_stands_down_when_a_pass_is_already_in_flight(tmp_path):
    """Un ``/gardener viaggio`` a mano e il tick del cron nello stesso minuto.

    La mutua esclusione vive in ``run_gardener`` — i chiamanti sono due e la
    guardia deve essere una — quindi qui si prova la sola cosa che è del
    dispatcher: il tick si ritira **senza** timbrare e senza contare un
    insuccesso. Timbrarlo vorrebbe dire spostare in avanti la distanza per una
    passata che il cron non ha nemmeno fatto, e contarlo vorrebbe dire che tre
    ``/gardener`` a mano fanno scattare l'allarme «il diario non diventa pagine».
    """
    from jenny.agent.gardener import _PASSES_IN_FLIGHT
    from jenny.agent.gardener_state import GardenerState, read_state

    root = _project(tmp_path)
    _PASSES_IN_FLIGHT.add("viaggio")
    try:
        result = await _dispatcher(_FakeAgent(tmp_path), _config())._dispatch(_JOB)
    finally:
        _PASSES_IN_FLIGHT.discard("viaggio")

    assert result is None
    assert read_state(root) == GardenerState()


@pytest.mark.asyncio
async def test_the_tick_after_a_failed_pass_does_not_run_it_again(tmp_path, monkeypatch):
    """La ripetizione, vista da dove è stata misurata: due tick di fila, una
    passata sola. Prima erano due — e quarantotto in un giorno."""
    from jenny.agent import gardener as gardener_module

    _project(tmp_path)
    passes: list[str] = []
    real_run = gardener_module.run_gardener

    async def _counting(agent, store, **kw):
        # Avvolge il runner **vero** invece di sostituirlo: il timbro deve
        # avvenire davvero, o il secondo tick non avrebbe ragione di fermarsi e
        # il test misurerebbe soltanto se stesso.
        #
        # ``**kw`` inoltrato e non ingoiato: da T2.5 il dispatcher passa il delta
        # gia' letto, e mangiarlo qui vorrebbe dire misurare una strada che in
        # produzione non esiste (il runner tornerebbe a rileggere i diari).
        passes.append(store.name)
        return await real_run(agent, store, **kw)

    monkeypatch.setattr("jenny.agent.gardener.run_gardener", _counting)

    dispatcher = _dispatcher(_BrokenAgent(tmp_path), _config())
    await dispatcher._dispatch(_JOB)
    await dispatcher._dispatch(_JOB)

    assert passes == ["viaggio"], "il secondo tick ha rifatto la passata"


def _alarms(monkeypatch) -> list[str]:
    """Gli alert di sistema emessi, senza runtime Android."""
    seen: list[str] = []
    monkeypatch.setattr(
        "jenny.runtime.notifier.notify_delivery",
        lambda content, metadata: seen.append(content),
    )
    return seen


@pytest.mark.asyncio
async def test_a_series_of_failures_reaches_a_surface_someone_sees(tmp_path, monkeypatch):
    """L'allarme, perché il log da solo non basta: su Android nessuno legge
    logcat, ed è precisamente lo stato in cui il diario di un progetto smette di
    diventare pagine senza che niente lo dica. Stessa forma di ``_alert_stuck``
    di Dream — nessun token, nessuna dipendenza dal modello."""
    from jenny.agent.gardener_state import GARDENER_FAILURES_ARE_ALARMING

    alerts = _alarms(monkeypatch)
    # La serie la conta ``run_gardener`` e la porta nell'esito; qui si prova la
    # sola decisione che è del cron — se quella serie vale una notifica.
    monkeypatch.setattr(
        "jenny.agent.gardener.run_gardener",
        _returning("failed", failures=GARDENER_FAILURES_ARE_ALARMING),
    )
    _project(tmp_path)

    await _dispatcher(_FakeAgent(tmp_path), _config())._dispatch(_JOB)

    assert len(alerts) == 1
    assert "viaggio" in alerts[0] and str(GARDENER_FAILURES_ARE_ALARMING) in alerts[0]


@pytest.mark.asyncio
async def test_one_failure_below_the_threshold_does_not_ring(tmp_path, monkeypatch):
    """Il controllo dell'altro verso: una passata andata storta è ordinaria — un
    provider giù per un minuto — e un allarme a ogni incidente è un allarme che
    si impara a ignorare."""
    from jenny.agent.gardener_state import GARDENER_FAILURES_ARE_ALARMING

    alerts = _alarms(monkeypatch)
    monkeypatch.setattr(
        "jenny.agent.gardener.run_gardener",
        _returning("failed", failures=GARDENER_FAILURES_ARE_ALARMING - 1),
    )
    _project(tmp_path)

    await _dispatcher(_FakeAgent(tmp_path), _config())._dispatch(_JOB)

    assert alerts == []


# ── T2.5: la selezione e la passata leggono il diario una volta in due ────────


@pytest.mark.asyncio
async def test_one_tick_opens_the_journal_once(tmp_path, monkeypatch):
    """Il tick intero, col runner **vero**: due letture erano due.

    ``pick_project`` apre i diari per decidere e fino al 23/08/2026
    ``run_gardener`` li riapriva da zero un istante dopo —
    ``read_journal_delta`` fa un ``read_text`` intero di ogni
    ``raw/journal/*.md`` prima di guardare il cursore, quindi il costo è per
    file e per giorno di diario, non per riga non letta.

    Il conteggio è **esattamente uno** e non «al massimo uno»: con zero il tick
    non avrebbe nemmeno guardato, e un test che passa a zero non misura niente.
    Il fixture porta una riga non letta proprio per questo.

    Va qui e non in ``tests/agent``: il doppio giro nasce dall'incontro fra la
    selezione e la passata, e il posto dove i due si incontrano è questo
    dispatcher. Un test che passasse il delta a mano proverebbe la firma di
    ``run_gardener`` e non il suo cablaggio.
    """
    _project(tmp_path)
    reads: list[str] = []
    original = Path.read_text

    def _spy(self, *a, **kw):
        if "journal" in self.as_posix():
            reads.append(self.name)
        return original(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", _spy)

    await _dispatcher(_FakeAgent(tmp_path), _config())._dispatch(_JOB)

    assert reads == ["20260823.md"]
