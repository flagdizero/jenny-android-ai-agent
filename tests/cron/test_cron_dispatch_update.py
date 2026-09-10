"""Il ramo ``update_check`` del ``CronDispatcher``.

Il requisito centrale non è annunciare: è **smettere**. Un job che gira ogni
ventiquattro ore e che ogni volta ricorda la stessa versione non è una notifica,
è un assillo — e l'utente che ha detto "dopo" la prima volta non ha modo di
farlo tacere. Chi decide è ``notified_code`` nello stato dell'updater.

Il resto ricalca il contratto dell'heartbeat: turno silenzioso, chat WebUI come
indirizzo, consegna solo tramite il tool ``message`` dentro il turno.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from jenny.config.loader import get_config_path, save_config
from jenny.config.schema import Config
from jenny.runtime import cron_dispatch
from jenny.runtime.cron_dispatch import UPDATE_SESSION_KEY, CronDispatcher
from jenny.runtime.update_check import UpdateInfo
from jenny.session.turn_visibility import TurnVisibility
from jenny.webui.metadata import WEBUI_MESSAGE_SOURCE_METADATA_KEY

_UPDATE_JOB = SimpleNamespace(name="update_check", id="update_check")

_INFO = UpdateInfo(
    version_code=9,
    version_name="0.7.0",
    apk_url="https://example.com/jenny-0.7.0.apk",
    sha256="a" * 64,
    size=48210944,
    notes_url="https://example.com/notes",
    summary="Aggiornamenti in-app e meno consumo a schermo spento.",
    critical=False,
)


class _FakeSession:
    def __init__(self) -> None:
        self.retained: list[int] = []

    def retain_recent_legal_suffix(self, keep: int) -> None:
        self.retained.append(keep)


class _FakeSessions:
    def __init__(self) -> None:
        self.session = _FakeSession()
        self.saved = 0

    def get_or_create(self, _key: str) -> _FakeSession:
        return self.session

    def save(self, _session: _FakeSession) -> None:
        self.saved += 1


class _FakeAgent:
    def __init__(self) -> None:
        self.sessions = _FakeSessions()
        self.calls: list[dict] = []

    async def process_direct(self, prompt: str, **kwargs: Any):
        self.calls.append({"prompt": prompt, **kwargs})
        return None

    def evict_pruned_sessions(self, keys) -> None:  # pragma: no cover - non usato qui
        pass


class _Updater:
    """Doppio dello stato dell'updater: cosa c'è da proporre, cosa è già detto."""

    def __init__(self, info: UpdateInfo | None) -> None:
        self.info = info
        self.notified: int | None = None
        self.checks = 0
        self.alerts: list[tuple[str, dict]] = []

    async def check_for_update(self, _config: Any) -> UpdateInfo | None:
        self.checks += 1
        return self.info

    def notified_version_code(self) -> int | None:
        return self.notified

    def mark_notified(self, version_code: int) -> None:
        self.notified = version_code

    async def post_alert(self, content: str, metadata: dict) -> bool:
        self.alerts.append((content, metadata))
        return True


@pytest.fixture
def config_file(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Scrive ``config.json`` dentro ``tmp_path`` e ci punta il runtime.

    Il ramo ``update_check`` rilegge la config **da disco** a ogni tick, come
    Dream, il giardiniere e l'heartbeat: la sezione la si spegne nel file, non
    nell'oggetto che il dispatcher tiene in mano. L'override del ``config_path``
    è obbligatorio — senza, questi test leggerebbero il ``config.json`` di chi
    esegue la suite.
    """
    from jenny.config import paths
    from jenny.runtime.context import get_runtime_context

    previous = paths.get_workspace_path()
    paths.set_workspace_dir(str(tmp_path))
    monkeypatch.setattr(get_runtime_context(), "config_path", tmp_path / "config.json")

    def write(*, enabled: bool = True, notify_in_chat: bool = True) -> Config:
        config = Config()
        config.updates.enabled = enabled
        config.updates.notify_in_chat = notify_in_chat
        save_config(config, get_config_path())
        return config

    yield write
    paths.set_workspace_dir(str(previous))


@pytest.fixture
def setup(config_file, monkeypatch: pytest.MonkeyPatch):
    def build(
        info: UpdateInfo | None = _INFO,
        *,
        notify_in_chat: bool = True,
        enabled: bool = True,
    ):
        updater = _Updater(info)
        monkeypatch.setattr(
            "jenny.runtime.update_check.check_for_update", updater.check_for_update
        )
        monkeypatch.setattr(
            "jenny.runtime.update_check.notified_version_code",
            updater.notified_version_code,
        )
        monkeypatch.setattr(
            "jenny.runtime.update_check.mark_notified", updater.mark_notified
        )
        monkeypatch.setattr("jenny.runtime.notifier.post_alert", updater.post_alert)
        agent = _FakeAgent()
        config_file(enabled=enabled, notify_in_chat=notify_in_chat)
        dispatcher = CronDispatcher(
            get_agent=lambda: agent,
            # Il ``Config`` del dispatcher è quello **d'avvio**, e resta acceso: è
            # catturato quando il container si costruisce e niente lo aggiorna. I
            # flag che i test chiedono vanno sul file, che è ciò che il ramo legge
            # davvero — così ogni test di questo file è anche una prova che quella
            # lettura è fresca.
            config=Config(),
            cron=MagicMock(),
            heartbeat_cfg=SimpleNamespace(keep_recent_messages=8),
        )
        return dispatcher, agent, updater

    return build


class TestTheAnnouncement:
    async def test_a_new_version_opens_exactly_one_turn(self, setup) -> None:
        dispatcher, agent, _updater = setup()

        assert await dispatcher.dispatch(_UPDATE_JOB) is None
        assert len(agent.calls) == 1

    async def test_the_turn_is_silent_and_addressed_to_the_user_chat(self, setup) -> None:
        dispatcher, agent, _updater = setup()

        await dispatcher.dispatch(_UPDATE_JOB)
        call = agent.calls[0]

        assert call["visibility"] is TurnVisibility.SILENT
        assert call["channel"] == "websocket"
        assert call["chat_id"] == "default"
        assert call["session_key"] == UPDATE_SESSION_KEY
        assert call["metadata"] == {WEBUI_MESSAGE_SOURCE_METADATA_KEY: {"kind": "update"}}

    async def test_the_prompt_carries_version_summary_and_the_question(
        self, setup
    ) -> None:
        dispatcher, agent, _updater = setup()

        await dispatcher.dispatch(_UPDATE_JOB)
        prompt = agent.calls[0]["prompt"]

        assert "0.7.0" in prompt
        assert _INFO.summary in prompt
        assert _INFO.notes_url in prompt
        assert "install it now" in prompt
        assert "`message` tool" in prompt

    async def test_the_session_tail_stays_bounded(self, setup) -> None:
        dispatcher, agent, _updater = setup()

        await dispatcher.dispatch(_UPDATE_JOB)

        assert agent.sessions.session.retained == [cron_dispatch._UPDATE_HISTORY_KEEP]
        assert agent.sessions.saved == 1


class TestItSaysItOnlyOnce:
    async def test_the_announced_version_is_recorded(self, setup) -> None:
        dispatcher, _agent, updater = setup()

        await dispatcher.dispatch(_UPDATE_JOB)

        assert updater.notified == 9

    async def test_the_second_run_stays_quiet(self, setup) -> None:
        """Il caso che questo job esiste per non fare."""
        dispatcher, agent, updater = setup()

        await dispatcher.dispatch(_UPDATE_JOB)
        await dispatcher.dispatch(_UPDATE_JOB)

        assert updater.checks == 2
        assert len(agent.calls) == 1

    async def test_a_further_version_speaks_again(self, setup) -> None:
        dispatcher, agent, updater = setup()

        await dispatcher.dispatch(_UPDATE_JOB)
        updater.info = UpdateInfo(**{**_INFO.__dict__, "version_code": 10,
                                     "version_name": "0.8.0"})
        await dispatcher.dispatch(_UPDATE_JOB)

        assert len(agent.calls) == 2
        assert updater.notified == 10


class TestWhenNothingShouldBeSaid:
    async def test_no_update_means_no_turn(self, setup) -> None:
        dispatcher, agent, updater = setup(info=None)

        assert await dispatcher.dispatch(_UPDATE_JOB) is None
        assert agent.calls == []
        assert updater.notified is None

    async def test_chat_notification_off_keeps_the_announcement_pending(
        self, setup
    ) -> None:
        """Spegnere la notifica non deve *consumarla*: riaccendendola si annuncia."""
        dispatcher, agent, updater = setup(notify_in_chat=False)

        await dispatcher.dispatch(_UPDATE_JOB)

        assert agent.calls == []
        assert updater.notified is None

    async def test_the_section_switched_off_stops_the_job_before_the_network(
        self, setup
    ) -> None:
        """``updates.enabled: false`` deve spegnere davvero: niente rete, niente turno.

        Non basta non registrare il job all'avvio. Il default è acceso, quindi
        al primo boot il job finisce nello store del cron, e da lì non esce più:
        ``register_system_job`` non ha una controparte che deregistri e
        ``remove_job`` protegge i ``system_event``. Dal secondo avvio in poi
        l'unica cosa che può fermarlo è questo controllo.
        """
        dispatcher, agent, updater = setup(enabled=False)

        assert await dispatcher.dispatch(_UPDATE_JOB) is None

        assert updater.checks == 0, "the check reached the network while disabled"
        assert agent.calls == []
        assert updater.notified is None
        assert updater.alerts == []
        # E il ``Config`` d'avvio è rimasto acceso: lo stiamo scavalcando con la
        # lettura da disco, non mutando. Senza questa riga il test resterebbe
        # verde anche col rimedio sbagliato, cioè due sorgenti di verità.
        assert dispatcher._config.updates.enabled is True

    async def test_the_section_switched_back_on_resumes(
        self, setup, config_file
    ) -> None:
        """Lo spegnimento è uno stato, non una cancellazione — e si disfa a caldo."""
        dispatcher, agent, updater = setup(enabled=False)
        await dispatcher.dispatch(_UPDATE_JOB)

        config_file(enabled=True)
        await dispatcher.dispatch(_UPDATE_JOB)

        assert updater.checks == 1
        assert len(agent.calls) == 1


class TestACriticalUpdate:
    async def test_it_also_rings_the_system_alert(self, setup) -> None:
        critical = UpdateInfo(**{**_INFO.__dict__, "critical": True})
        dispatcher, _agent, updater = setup(info=critical)

        await dispatcher.dispatch(_UPDATE_JOB)

        assert len(updater.alerts) == 1
        content, metadata = updater.alerts[0]
        assert "0.7.0" in content
        assert metadata == {WEBUI_MESSAGE_SOURCE_METADATA_KEY: {"kind": "update"}}

    async def test_the_prompt_says_it_is_critical(self, setup) -> None:
        critical = UpdateInfo(**{**_INFO.__dict__, "critical": True})
        dispatcher, agent, _updater = setup(info=critical)

        await dispatcher.dispatch(_UPDATE_JOB)

        assert "critical security update" in agent.calls[0]["prompt"]

    async def test_a_normal_update_does_not_ring_twice(self, setup) -> None:
        """Sul percorso normale l'alert lo fa già la consegna del messaggio."""
        dispatcher, _agent, updater = setup()

        await dispatcher.dispatch(_UPDATE_JOB)

        assert updater.alerts == []
