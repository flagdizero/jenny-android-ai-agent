"""L'allarme su un controllo morto che non dipende dal modello.

Ogni altro avviso di questo sottosistema finisce nel tool ``message``, quindi
presuppone che il modello faccia la sua parte. Questi test difendono il caso in
cui non la fa — misurato, non ipotetico: 19 run consecutivi con il controllo
morto e zero avvisi, perché il timbro ``escalated`` era dedotto da un booleano di
turno senza soggetto e una volta messo il prompt diceva "non ripeterlo".

Cosa NON viene testato qui, e va detto per non credere il contrario: che
l'utente veda qualcosa. ``notify_delivery`` posta una notifica di sistema, e
solo con l'app in background; qui si verifica che parta, con che testo e quante
volte.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jenny.cron.could_not_check import ESCALATE_AFTER_FAILURES
from jenny.cron.service import CronService
from jenny.cron.silence_watchdog import (
    WATCHDOG_AFTER_FAILURES,
    WATCHDOG_QUIET_MS,
    alert_silently_broken_checks,
    silently_broken_checks,
)
from jenny.cron.types import (
    CronJobState,
    CronMonitorCouldNotCheckError,
    CronSchedule,
    CronTaskCheckState,
)
from jenny.webui.metadata import WEBUI_MESSAGE_SOURCE_METADATA_KEY

_NOW = 1_760_000_000_000


def _spy(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any]]:
    sent: list[tuple[str, Any]] = []
    monkeypatch.setattr(
        "jenny.runtime.notifier.notify_delivery",
        lambda content, metadata: sent.append((content, metadata)),
    )
    return sent


class TestTheThresholdIsAboveTheModelsOwn:
    """Sotto il doppio della soglia di escalation non si dice niente.

    Il motivo non è la prudenza: sotto quel punto non si sa ancora distinguere
    "il modello non avvisa" da "il modello avviserà al prossimo giro", e un
    allarme che parte insieme al suo sarebbe il doppione che tutto questo lavoro
    esiste per togliere.
    """

    def test_the_watchdog_waits_longer_than_the_prompt_does(self) -> None:
        assert WATCHDOG_AFTER_FAILURES > ESCALATE_AFTER_FAILURES

    def test_a_check_that_just_started_failing_is_not_an_alarm(self) -> None:
        state = CronJobState(consecutive_could_not_check=ESCALATE_AFTER_FAILURES)

        assert silently_broken_checks(state, now_ms=_NOW) == []

    def test_a_check_dead_past_the_threshold_and_never_reported_is(self) -> None:
        state = CronJobState(consecutive_could_not_check=WATCHDOG_AFTER_FAILURES)

        assert silently_broken_checks(state, now_ms=_NOW) == [("", WATCHDOG_AFTER_FAILURES)]


class TestAStampDoesNotBuySilenceForever:
    """``escalated`` dice che l'avviso è uscito una volta, non che sia bastato."""

    def _entry(self, *, escalated_at_ms: int | None) -> CronJobState:
        return CronJobState(
            task_checks={
                "abc": CronTaskCheckState(
                    consecutive_could_not_check=WATCHDOG_AFTER_FAILURES,
                    escalated=True,
                    escalated_at_ms=escalated_at_ms,
                    label="WaterBot: umidità piante",
                )
            }
        )

    def test_inside_the_quiet_window_nothing_is_repeated(self) -> None:
        state = self._entry(escalated_at_ms=_NOW - WATCHDOG_QUIET_MS + 1)

        assert silently_broken_checks(state, now_ms=_NOW) == []

    def test_a_check_still_dead_a_day_later_is_news_again(self) -> None:
        state = self._entry(escalated_at_ms=_NOW - WATCHDOG_QUIET_MS)

        assert silently_broken_checks(state, now_ms=_NOW) == [
            ("WaterBot: umidità piante", WATCHDOG_AFTER_FAILURES)
        ]

    def test_a_stamp_without_a_date_does_not_buy_anything(self) -> None:
        """Voce scritta prima che ``escalated_at_ms`` esistesse.

        ``rearm_after_user_message`` la considera apposta non riarmabile, per non
        avvisare di un guasto vecchio di ore nel momento in cui l'aggiornamento
        atterra. Qui la scelta è l'opposta e non è una contraddizione: un
        controllo morto da sei run con un timbro di data ignota è esattamente il
        silenzio che questo modulo esiste per rompere.
        """
        state = self._entry(escalated_at_ms=None)

        assert silently_broken_checks(state, now_ms=_NOW) != []


class TestOneAlarmPerFailureNotTwo:
    def test_the_per_task_map_wins_over_the_job_summary(self) -> None:
        """I contatori del job sono il riassunto della stessa cosa.

        Un heartbeat con un task rotto ha ENTRAMBE le letture sopra soglia:
        sommarle darebbe due allarmi per un guasto, e il secondo non saprebbe
        nemmeno dire di quale task parla.
        """
        state = CronJobState(
            consecutive_could_not_check=WATCHDOG_AFTER_FAILURES,
            task_checks={
                "abc": CronTaskCheckState(
                    consecutive_could_not_check=WATCHDOG_AFTER_FAILURES, label="piante"
                )
            },
        )

        assert silently_broken_checks(state, now_ms=_NOW) == [("piante", WATCHDOG_AFTER_FAILURES)]

    def test_a_healthy_task_next_to_a_broken_one_is_not_named(self) -> None:
        state = CronJobState(
            task_checks={
                "dead": CronTaskCheckState(
                    consecutive_could_not_check=WATCHDOG_AFTER_FAILURES, label="piante"
                ),
                "young": CronTaskCheckState(consecutive_could_not_check=1, label="backup"),
            }
        )

        assert silently_broken_checks(state, now_ms=_NOW) == [("piante", WATCHDOG_AFTER_FAILURES)]

    def test_several_dead_checks_produce_one_alarm_that_counts_them(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sent = _spy(monkeypatch)
        state = CronJobState(
            task_checks={
                "a": CronTaskCheckState(
                    consecutive_could_not_check=WATCHDOG_AFTER_FAILURES, label="piante"
                ),
                "b": CronTaskCheckState(
                    consecutive_could_not_check=WATCHDOG_AFTER_FAILURES + 3, label="backup"
                ),
            }
        )

        alert_silently_broken_checks("heartbeat", state, now_ms=_NOW)

        assert len(sent) == 1
        content, _metadata = sent[0]
        assert "2 checks" in content
        assert str(WATCHDOG_AFTER_FAILURES + 3) in content


class TestTheAlertItself:
    def test_it_names_the_check_and_where_to_look(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Il testo non ripete il *motivo*.

        Quello lo scrive il modello, sta in ``last_error`` e in ``/cron``, e una
        seconda copia qui potrebbe divergere da quella. L'allarme dice quale
        controllo e da quanto — le due cose che decidono se valga la pena
        alzarsi — e dove trovare il resto.
        """
        sent = _spy(monkeypatch)
        state = CronJobState(
            task_checks={
                "abc": CronTaskCheckState(
                    consecutive_could_not_check=WATCHDOG_AFTER_FAILURES,
                    label="WaterBot: umidità piante",
                )
            }
        )

        alert_silently_broken_checks("heartbeat", state, now_ms=_NOW)

        content, _metadata = sent[0]
        assert "WaterBot: umidità piante" in content
        assert str(WATCHDOG_AFTER_FAILURES) in content
        assert "/cron" in content

    def test_the_tag_does_not_collide_with_a_real_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Il tag coalizza gli alert della stessa etichetta.

        Con il nome del job nudo questa notifica sostituirebbe quella di un
        messaggio vero dello stesso job — cioè l'unica che l'utente non deve
        perdere. Il suffisso è ciò che tiene i due tag distinti.
        """
        sent = _spy(monkeypatch)
        state = CronJobState(consecutive_could_not_check=WATCHDOG_AFTER_FAILURES)

        alert_silently_broken_checks("piante", state, now_ms=_NOW)

        _content, metadata = sent[0]
        source = metadata[WEBUI_MESSAGE_SOURCE_METADATA_KEY]
        assert source["kind"] == "cron"
        assert source["label"] != "piante"
        assert "piante" in source["label"]

    def test_nothing_is_posted_when_nothing_is_wrong(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sent = _spy(monkeypatch)

        assert alert_silently_broken_checks("piante", CronJobState(), now_ms=_NOW) == []
        assert sent == []


class TestTheServiceCallsIt:
    """Un solo punto di aggancio, e copre monitor e heartbeat insieme.

    ``CronService._execute_job`` è l'unico posto che vede sia i contatori del job
    appena incrementati sia la mappa per-task che il dispatcher ha già riscritto.
    """

    @staticmethod
    def _service(tmp_path: Path, *, escalated: bool = False) -> tuple[CronService, str]:
        service = CronService(tmp_path / "cron" / "jobs.json")

        async def on_job(job: Any) -> str | None:
            raise CronMonitorCouldNotCheckError(
                "could not check", reason="host unreachable", escalated=escalated
            )

        service.on_job = on_job
        job = service.add_job(
            name="piante",
            schedule=CronSchedule(kind="every", every_ms=1_800_000),
            message="controlla le piante",
            mode="monitor",
            session_key="unified:default",
            origin_channel="websocket",
            origin_chat_id="chat-1",
        )
        return service, job.id

    async def test_the_alarm_fires_once_the_streak_is_long_enough(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sent = _spy(monkeypatch)
        service, job_id = self._service(tmp_path)

        for _ in range(WATCHDOG_AFTER_FAILURES):
            await service.run_job(job_id)

        # Un allarme per ogni run oltre soglia, ed è voluto: il tag coalizza,
        # quindi sul telefono resta una notifica sola sempre aggiornata, e chi
        # l'ha scartata la rivede al giro dopo.
        assert len(sent) == 1
        job = service.get_job(job_id)
        assert job is not None
        assert job.state.consecutive_could_not_check == WATCHDOG_AFTER_FAILURES

    async def test_a_short_streak_stays_quiet(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sent = _spy(monkeypatch)
        service, job_id = self._service(tmp_path)

        for _ in range(WATCHDOG_AFTER_FAILURES - 1):
            await service.run_job(job_id)

        assert sent == []

    async def test_a_successful_check_clears_the_ground(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Il reset della sequenza è già quello di ``_reset_could_not_check``.

        Vale la pena difenderlo da qui: se un run riuscito non azzerasse, questo
        allarme diventerebbe permanente su un controllo guarito.
        """
        sent = _spy(monkeypatch)
        service, job_id = self._service(tmp_path)
        failing = service.on_job
        for _ in range(WATCHDOG_AFTER_FAILURES):
            await service.run_job(job_id)
        assert len(sent) == 1

        async def healthy(job: Any) -> str | None:
            return None

        service.on_job = healthy
        await service.run_job(job_id)

        # La sequenza riparte da zero, quindi il guasto successivo deve
        # ricominciare a contare invece di allarmare subito.
        service.on_job = failing
        await service.run_job(job_id)

        assert len(sent) == 1
