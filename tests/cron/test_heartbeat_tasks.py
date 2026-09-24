"""Le parti pure di B13: come si legge ``HEARTBEAT.md`` e come si nomina un task.

``HEARTBEAT.md`` è un file libero, scritto a mano dall'utente: non ha id, non ha
uno schema, e non gliene vogliamo imporre uno. Tutto quello che questo modulo
deve garantire è che lo stesso task, fra un run e l'altro, si riconosca — e che
quando *non* si riconosce sbagli dalla parte giusta (sequenza che riparte da
zero, mai una sequenza ereditata dal task sbagliato).
"""

from __future__ import annotations

from jenny.cron.could_not_check import (
    ESCALATE_AFTER_FAILURES,
    ESCALATION_ASK_LIMIT,
    CouldNotCheckMark,
    could_not_check_reason,
    is_only_markers,
    parse_could_not_check_marks,
    parse_delegated_marks,
    parse_ok_marks,
    parse_warned_marks,
)
from jenny.cron.heartbeat_tasks import (
    HeartbeatTask,
    already_warned_block,
    attribute_marks,
    escalation_block,
    followup_block,
    parse_heartbeat_tasks,
    pending_tasks,
    record_followup_outcomes,
    record_task_outcomes,
    resolve_pending_delegations,
    task_index_block,
    tasks_already_warned,
    tasks_due_for_escalation,
)
from jenny.cron.types import CronJobState, CronTaskCheckState

_WATERBOT = (
    "- Ogni ciclo, controlla l'umidità delle piante e avvisami solo sotto il 15%. "
    "Se hps è irraggiungibile salta il ciclo in silenzio."
)
_VITAMINE = "- Alle 9 ricordami le vitamine."


def _file(*tasks: str) -> str:
    body = "\n".join(tasks)
    return f"# Heartbeat Tasks\n\n<!-- un commento -->\n\n## Active Tasks\n\n{body}\n"


class TestReadingTheFile:
    def test_each_bullet_is_one_task(self) -> None:
        tasks = parse_heartbeat_tasks(_file(_WATERBOT, _VITAMINE))

        assert [t.index for t in tasks] == [1, 2]
        assert tasks[0].label.startswith("Ogni ciclo, controlla l'umidità")
        assert tasks[1].label == "Alle 9 ricordami le vitamine."

    def test_an_indented_line_continues_the_task_above_it(self) -> None:
        """Un sotto-punto è parte del task, non un task suo."""
        tasks = parse_heartbeat_tasks(_file(_WATERBOT, "  - solo se il sole è alto"))

        assert len(tasks) == 1
        assert "solo se il sole è alto" in tasks[0].text

    def test_a_prose_paragraph_is_a_task_too(self) -> None:
        """Non tutti scrivono elenchi puntati: un paragrafo vale come task."""
        tasks = parse_heartbeat_tasks(
            _file("Controlla la posta\ne dimmi se c'è qualcosa di urgente.", "", "Poi taci.")
        )

        assert [t.label for t in tasks] == ["Controlla la posta", "Poi taci."]

    def test_headers_comments_and_other_sections_are_not_tasks(self) -> None:
        content = (
            "# Heartbeat Tasks\n\n"
            "<!--\nquesta è una spiegazione\nsu più righe\n-->\n\n"
            "## Done\n\n- roba vecchia\n\n"
            "## Active Tasks\n\n"
            f"{_VITAMINE}\n"
        )

        assert [t.label for t in parse_heartbeat_tasks(content)] == [
            "Alle 9 ricordami le vitamine."
        ]

    def test_a_file_with_only_headers_has_no_tasks(self) -> None:
        assert parse_heartbeat_tasks("# Heartbeat Tasks\n\n## Active Tasks\n\n") == []


class TestTaskIdentity:
    def test_the_same_task_keeps_its_id_across_reads(self) -> None:
        first = parse_heartbeat_tasks(_file(_WATERBOT))[0]
        second = parse_heartbeat_tasks(_file(_WATERBOT))[0]

        assert first.id == second.id

    def test_moving_a_task_in_the_file_does_not_change_its_id(self) -> None:
        """Riordinare l'elenco è la modifica più frequente: non deve contare."""
        before = parse_heartbeat_tasks(_file(_WATERBOT, _VITAMINE))
        after = parse_heartbeat_tasks(_file(_VITAMINE, _WATERBOT))

        assert before[0].id == after[1].id
        assert after[1].index == 2

    def test_reformatting_without_changing_the_words_keeps_the_id(self) -> None:
        """Il marcatore di lista, il checkbox e gli spazi non sono il task."""
        plain = parse_heartbeat_tasks(_file("- Alle 9 ricordami le vitamine."))[0]
        starred = parse_heartbeat_tasks(_file("* [ ]   Alle 9  ricordami le vitamine."))[0]

        assert plain.id == starred.id

    def test_rewording_a_task_gives_it_a_new_id(self) -> None:
        """Il modo di sbagliare che abbiamo scelto: chi riscrive un task riparte
        da zero. Meglio un avviso in ritardo di K cicli che un avviso che parla
        di un controllo che non esiste più."""
        before = parse_heartbeat_tasks(_file("- Alle 9 ricordami le vitamine."))[0]
        after = parse_heartbeat_tasks(_file("- Alle 9 ricordami le vitamine e l'acqua."))[0]

        assert before.id != after.id

    def test_a_very_long_task_gets_a_short_label(self) -> None:
        task = parse_heartbeat_tasks(_file("- " + "parola " * 60))[0]

        assert len(task.label) <= 90
        assert task.label.endswith("…")


class TestTheMarker:
    def test_a_numbered_marker_carries_the_task_and_the_reason(self) -> None:
        assert parse_could_not_check_marks("CHECK_FAILED 2: hps unreachable") == [
            CouldNotCheckMark("2", "hps unreachable")
        ]

    def test_one_turn_can_declare_several_failed_tasks(self) -> None:
        text = "CHECK_FAILED 1: import rotto\nCHECK_FAILED 3: timeout"

        assert [(m.ref, m.reason) for m in parse_could_not_check_marks(text)] == [
            ("1", "import rotto"),
            ("3", "timeout"),
        ]

    def test_the_shapes_a_model_writes_without_thinking_are_all_read(self) -> None:
        for line in ("CHECK_FAILED #2: x", "CHECK_FAILED [2]: x", "CHECK_FAILED 2. x",
                     "**CHECK_FAILED 2: x**", "- CHECK_FAILED 2 - x"):
            assert parse_could_not_check_marks(line) == [CouldNotCheckMark("2", "x")], line

    def test_the_monitor_form_without_a_number_still_works(self) -> None:
        """B8 non si tocca: un monitor ha un controllo solo e non numera niente."""
        assert parse_could_not_check_marks("CHECK_FAILED: hps down") == [
            CouldNotCheckMark(None, "hps down")
        ]
        assert could_not_check_reason("CHECK_FAILED: hps down") == "hps down"
        assert could_not_check_reason("tutto a posto") is None

    def test_a_reason_that_starts_with_a_number_is_not_a_task_number(self) -> None:
        assert could_not_check_reason("CHECK_FAILED: 3 letture su 3 fallite") == (
            "3 letture su 3 fallite"
        )


class TestAttributingAMarkToATask:
    def test_with_one_task_a_marker_without_a_number_is_unambiguous(self) -> None:
        tasks = parse_heartbeat_tasks(_file(_WATERBOT))

        reasons, unattributed = attribute_marks(tasks, [CouldNotCheckMark(None, "hps down")])

        assert reasons == {tasks[0].id: "hps down"}
        assert unattributed == []

    def test_with_two_tasks_a_marker_without_a_number_blames_nobody(self) -> None:
        """Incolpare il task sbagliato produrrebbe un avviso su un controllo sano."""
        tasks = parse_heartbeat_tasks(_file(_WATERBOT, _VITAMINE))

        reasons, unattributed = attribute_marks(tasks, [CouldNotCheckMark(None, "boh")])

        assert reasons == {}
        assert unattributed == ["boh"]

    def test_a_number_that_does_not_exist_blames_nobody(self) -> None:
        tasks = parse_heartbeat_tasks(_file(_WATERBOT, _VITAMINE))

        reasons, unattributed = attribute_marks(tasks, [CouldNotCheckMark("7", "boh")])

        assert reasons == {}
        assert unattributed == ["boh"]


class TestTheStateSelfHeals:
    def _state_with(self, task_id: str, count: int) -> CronJobState:
        return CronJobState(
            task_checks={task_id: CronTaskCheckState(consecutive_could_not_check=count)}
        )

    def test_a_task_that_runs_again_loses_its_entry(self) -> None:
        tasks = parse_heartbeat_tasks(_file(_WATERBOT))
        state = self._state_with(tasks[0].id, 2)

        outcome = record_task_outcomes(
            state, tasks, [], now_ms=1, escalating=[]
        )

        assert outcome.any_failure is False
        assert state.task_checks == {}

    def test_a_task_deleted_from_the_file_is_pruned(self) -> None:
        """Altrimenti lo store cresce a ogni task che l'utente cancella."""
        gone = parse_heartbeat_tasks(_file("- un task che non c'è più"))[0]
        state = self._state_with(gone.id, 2)
        tasks = parse_heartbeat_tasks(_file(_WATERBOT))

        record_task_outcomes(state, tasks, [], now_ms=1, escalating=[])

        assert state.task_checks == {}

    def test_the_streak_of_the_broken_task_grows_alone(self) -> None:
        tasks = parse_heartbeat_tasks(_file(_WATERBOT, _VITAMINE))
        state = CronJobState()

        for _ in range(3):
            record_task_outcomes(
                state,
                tasks,
                [CouldNotCheckMark("1", "hps down")],
                now_ms=7,
                escalating=[],
            )

        assert list(state.task_checks) == [tasks[0].id]
        entry = state.task_checks[tasks[0].id]
        assert entry.consecutive_could_not_check == 3
        assert entry.since_ms == 7
        assert entry.label.startswith("Ogni ciclo")

    def test_escalation_is_due_one_run_before_the_threshold(self) -> None:
        tasks = parse_heartbeat_tasks(_file(_WATERBOT))
        state = self._state_with(tasks[0].id, 1)
        assert tasks_due_for_escalation(state, tasks) == []

        state = self._state_with(tasks[0].id, 2)
        assert tasks_due_for_escalation(state, tasks) == [tasks[0]]

    def test_a_task_already_escalated_is_not_due_again(self) -> None:
        tasks = parse_heartbeat_tasks(_file(_WATERBOT))
        state = CronJobState(
            task_checks={
                tasks[0].id: CronTaskCheckState(
                    consecutive_could_not_check=9, escalated=True
                )
            }
        )

        assert tasks_due_for_escalation(state, tasks) == []


class TestADelegatedTaskWaitsForItsVerdict:
    """Il turno che delega non ha l'esito: `spawn` ritorna subito."""

    def test_the_two_markers_do_not_read_each_other(self) -> None:
        text = "CHECK_DELEGATED 1: leggi hps\nCHECK_FAILED 2: sveglia non impostata"

        assert [(m.ref, m.reason) for m in parse_delegated_marks(text)] == [
            ("1", "leggi hps")
        ]
        assert [(m.ref, m.reason) for m in parse_could_not_check_marks(text)] == [
            ("2", "sveglia non impostata")
        ]

    def test_a_delegated_task_keeps_its_entry_instead_of_being_pruned(self) -> None:
        """Senza questo, ogni giro azzererebbe la sequenza prima del verdetto."""
        tasks = parse_heartbeat_tasks(_file(_WATERBOT, _VITAMINE))
        state = CronJobState(
            task_checks={tasks[0].id: CronTaskCheckState(consecutive_could_not_check=2)}
        )

        outcome = record_task_outcomes(
            state,
            tasks,
            [],
            now_ms=11,
            escalating=[],
            delegated=[CouldNotCheckMark("1", "leggi hps")],
        )

        assert outcome.any_failure is False
        assert outcome.pending == [tasks[0]]
        entry = state.task_checks[tasks[0].id]
        assert entry.consecutive_could_not_check == 2
        assert entry.pending_since_ms == 11

    def test_the_verdict_from_the_announce_turn_counts_a_failure(self) -> None:
        tasks = parse_heartbeat_tasks(_file(_WATERBOT, _VITAMINE))
        state = CronJobState(
            task_checks={tasks[0].id: CronTaskCheckState(pending_since_ms=11)}
        )

        outcome = record_followup_outcomes(
            state,
            tasks,
            [CouldNotCheckMark(None, "import di wb_probe fallito")],
            now_ms=12,
            escalating=[],
        )

        assert outcome.failed == [tasks[0]]
        entry = state.task_checks[tasks[0].id]
        assert entry.consecutive_could_not_check == 1
        assert entry.pending_since_ms is None

    def test_the_announce_turn_never_prunes(self) -> None:
        """Ha in mano un risultato, non il file: dedurre da qui che gli altri
        task sono sani cancellerebbe sequenze che nessuno ha smentito."""
        tasks = parse_heartbeat_tasks(_file(_WATERBOT, _VITAMINE))
        state = CronJobState(
            task_checks={
                tasks[0].id: CronTaskCheckState(pending_since_ms=11),
                tasks[1].id: CronTaskCheckState(consecutive_could_not_check=2),
            }
        )

        record_followup_outcomes(
            state, tasks, [], now_ms=12, escalating=[]
        )

        assert state.task_checks[tasks[1].id].consecutive_could_not_check == 2

    def test_a_verdict_that_never_arrives_is_resolved_in_favour_of_the_task(self) -> None:
        tasks = parse_heartbeat_tasks(_file(_WATERBOT))
        state = CronJobState(
            task_checks={
                tasks[0].id: CronTaskCheckState(
                    consecutive_could_not_check=2, pending_since_ms=11, label="Ogni ciclo"
                )
            }
        )

        assert resolve_pending_delegations(state) == ["Ogni ciclo"]
        assert state.task_checks == {}
        assert pending_tasks(state, tasks) == []

    def test_the_announce_block_names_only_the_pending_checks(self) -> None:
        tasks = parse_heartbeat_tasks(_file(_WATERBOT, _VITAMINE))

        block = followup_block([tasks[0]], [])

        assert "1. Ogni ciclo" in block
        assert "vitamine" not in block
        assert "CHECK_FAILED <number>:" in block
        assert "EXACTLY ONCE" not in block

    def test_the_announce_block_carries_the_escalation_when_it_is_due(self) -> None:
        tasks = parse_heartbeat_tasks(_file(_WATERBOT))

        block = followup_block([tasks[0]], [tasks[0]])

        assert "EXACTLY ONCE" in block


class TestOneFaultIsOneWarning:
    """Il ciclo completo: parlare una volta, ricordarselo, e tornare a parlare
    quando è un guasto nuovo.

    Il difetto che tutto questo chiude: ``escalated`` viveva nella stessa voce
    che la risoluzione ottimistica cancellava, quindi bastava un turno d'annuncio
    senza marcatore — un ciclo solo — perché il ricordo di aver parlato sparisse.
    Tre cicli dopo la sequenza era di nuovo a 3, ``escalated`` di nuovo ``False``,
    e l'utente riceveva lo stesso avviso. Misurato sul device: quattro avvisi in
    una notte per un guasto solo.
    """

    def _broken_and_announced(self, task_id: str) -> CronJobState:
        """Lo stato subito dopo l'avviso: sequenza alta, utente informato, delega in corso."""
        return CronJobState(
            task_checks={
                task_id: CronTaskCheckState(
                    consecutive_could_not_check=4,
                    since_ms=10,
                    escalated=True,
                    label="Ogni ciclo",
                    pending_since_ms=11,
                )
            }
        )

    def test_a_markerless_followup_does_not_erase_the_memory_of_having_spoken(self) -> None:
        tasks = parse_heartbeat_tasks(_file(_WATERBOT))
        state = self._broken_and_announced(tasks[0].id)

        assert resolve_pending_delegations(state) == ["Ogni ciclo"]

        entry = state.task_checks[tasks[0].id]
        assert entry.escalated is True
        assert entry.consecutive_could_not_check == 0
        assert entry.since_ms is None
        assert entry.pending_since_ms is None

    def test_the_streak_still_restarts_from_zero(self) -> None:
        """La direzione dell'errore non cambia: da uno stato vecchio non nasce un allarme."""
        tasks = parse_heartbeat_tasks(_file(_WATERBOT))
        state = self._broken_and_announced(tasks[0].id)

        resolve_pending_delegations(state)
        record_task_outcomes(
            state,
            tasks,
            [CouldNotCheckMark("1", "hps irraggiungibile")],
            now_ms=12,
            escalating=[],
        )

        assert state.task_checks[tasks[0].id].consecutive_could_not_check == 1
        assert tasks_due_for_escalation(state, tasks) == []

    def test_a_pending_entry_with_nothing_to_remember_is_still_dropped(self) -> None:
        """Senza un avviso già dato non c'è niente da conservare, e lo store non deve crescere."""
        tasks = parse_heartbeat_tasks(_file(_WATERBOT))
        state = CronJobState(
            task_checks={
                tasks[0].id: CronTaskCheckState(
                    consecutive_could_not_check=2, pending_since_ms=11, label="Ogni ciclo"
                )
            }
        )

        resolve_pending_delegations(state)

        assert state.task_checks == {}

    def test_a_declared_success_closes_the_entry(self) -> None:
        tasks = parse_heartbeat_tasks(_file(_WATERBOT))
        state = self._broken_and_announced(tasks[0].id)

        outcome = record_followup_outcomes(
            state, tasks, [], now_ms=12, escalating=[],
            ok=[CouldNotCheckMark("1", "")],
        )

        assert outcome.recovered == [tasks[0]]
        assert state.task_checks == {}

    def test_an_anonymous_success_with_two_pending_closes_nothing(self) -> None:
        """Non toccare è la direzione sicura: al massimo si aspetta un ciclo in più."""
        tasks = parse_heartbeat_tasks(_file(_WATERBOT, _VITAMINE))
        state = CronJobState(
            task_checks={
                tasks[0].id: CronTaskCheckState(escalated=True, pending_since_ms=11),
                tasks[1].id: CronTaskCheckState(escalated=True, pending_since_ms=11),
            }
        )

        outcome = record_followup_outcomes(
            state, tasks, [], now_ms=12, escalating=[],
            ok=[CouldNotCheckMark(None, "")],
        )

        assert outcome.recovered == []
        assert len(state.task_checks) == 2

    def test_a_success_declared_for_a_task_nobody_delegated_is_ignored(self) -> None:
        """Un turno d'annuncio non può azzerare la sequenza di un controllo che non ha guardato."""
        tasks = parse_heartbeat_tasks(_file(_WATERBOT, _VITAMINE))
        state = CronJobState(
            task_checks={
                tasks[0].id: CronTaskCheckState(pending_since_ms=11),
                tasks[1].id: CronTaskCheckState(consecutive_could_not_check=2),
            }
        )

        record_followup_outcomes(
            state, tasks, [], now_ms=12, escalating=[],
            ok=[CouldNotCheckMark("2", "")],
        )

        assert state.task_checks[tasks[1].id].consecutive_could_not_check == 2

    def test_a_declared_failure_wins_over_a_declared_success(self) -> None:
        tasks = parse_heartbeat_tasks(_file(_WATERBOT))
        state = CronJobState(
            task_checks={tasks[0].id: CronTaskCheckState(pending_since_ms=11)}
        )

        outcome = record_followup_outcomes(
            state, tasks, [CouldNotCheckMark("1", "import fallito")],
            now_ms=12, escalating=[],
            ok=[CouldNotCheckMark("1", "")],
        )

        assert outcome.failed == [tasks[0]]
        assert outcome.recovered == []
        assert state.task_checks[tasks[0].id].consecutive_could_not_check == 1

    def test_an_unrequested_warning_still_counts_as_having_spoken(self) -> None:
        """Misurato sul device il 2026-08-16. Al secondo ciclo di guasto, senza
        che il prompt lo chiedesse, il modello ha chiamato ``message``. Se quel
        messaggio non viene registrato, un ciclo dopo la soglia scatta e
        l'utente riceve lo stesso avviso una seconda volta — e nessuna riga di
        prompt lo impedisce, visto che lì il modello stava già ignorando sia la
        nostra istruzione sia quella scritta dall'utente.

        Ciò che è cambiato è **come** lo si sa: il modello lo dichiara con
        ``CHECK_WARNED``, e il preambolo dell'heartbeat gli chiede quella riga
        anche per un avviso di propria iniziativa. Prima si deduceva da
        ``spoke``, che era vero anche per un messaggio su altro."""
        tasks = parse_heartbeat_tasks(_file(_WATERBOT))
        state = CronJobState(
            task_checks={tasks[0].id: CronTaskCheckState(pending_since_ms=11)}
        )

        outcome = record_followup_outcomes(
            state, tasks, [CouldNotCheckMark("1", "hps irraggiungibile")],
            now_ms=12,
            escalating=[],  # nessuno gli aveva chiesto di parlare
            warned=[CouldNotCheckMark("1", "")],
        )

        assert state.task_checks[tasks[0].id].escalated is True
        # Ma non è l'escalation richiesta, che è quel che finisce nella run record.
        assert outcome.escalated is False
        # E al ciclo dopo non si riparla.
        assert tasks_due_for_escalation(state, tasks) == []
        assert tasks_already_warned(state, tasks) == [tasks[0]]

    def test_a_warning_about_one_task_does_not_silence_the_other(self) -> None:
        """Il messaggio è di UN task, e questa era la trappola.

        Con due controlli rotti nello stesso turno e un avviso spontaneo,
        timbrare ``escalated`` su entrambi zittisce per sempre quello di cui il
        modello NON ha parlato: rotto da sei cicli, mai annunciato, e mai più
        annunciabile. È l'errore peggiore dei due — lo stesso che rende
        inaccettabile la fix minima da cui è partito tutto questo.

        Con ``CHECK_WARNED`` il soggetto è scritto, quindi non c'è più niente da
        indovinare: si timbra il task nominato e nessun altro.
        """
        tasks = parse_heartbeat_tasks(_file(_WATERBOT, _VITAMINE))
        state = CronJobState()

        record_task_outcomes(
            state,
            tasks,
            [CouldNotCheckMark("1", "hps giù"), CouldNotCheckMark("2", "backup giù")],
            now_ms=10,
            escalating=[],  # nessuno ha chiesto di parlare
            warned=[CouldNotCheckMark("1", "")],  # ma il modello ha avvisato del 1
        )

        assert [e.escalated for e in state.task_checks.values()] == [True, False]

        # E il 2, di cui non ha parlato, resta annunciabile quando la soglia arriva.
        for run in range(1, ESCALATE_AFTER_FAILURES):
            record_task_outcomes(
                state, tasks,
                [CouldNotCheckMark("1", "hps giù"), CouldNotCheckMark("2", "backup giù")],
                now_ms=10 + run, escalating=[],
            )
        assert tasks_due_for_escalation(state, tasks) == [tasks[1]]

    def test_an_anonymous_warning_with_two_candidates_stamps_nobody(self) -> None:
        """Un ``CHECK_WARNED`` senza numero, e due guasti in gioco.

        Con due candidati non si sa di quale il modello abbia parlato, ed è la
        stessa regola che ``attribute_marks`` applica a un marcatore di guasto
        anonimo. La direzione dell'errore è quella accettata: nessun timbro
        significa che l'avviso verrà richiesto di nuovo — rumore recuperabile —
        mentre timbrarli entrambi zittirebbe un guasto reale per sempre.
        """
        tasks = parse_heartbeat_tasks(_file(_WATERBOT, _VITAMINE))
        state = CronJobState()

        record_task_outcomes(
            state,
            tasks,
            [CouldNotCheckMark("1", "hps giù"), CouldNotCheckMark("2", "backup giù")],
            now_ms=10,
            escalating=[],
            warned=[CouldNotCheckMark(None, "")],
        )

        assert [e.escalated for e in state.task_checks.values()] == [False, False]

    def test_an_anonymous_warning_with_one_candidate_lands_on_it(self) -> None:
        """Con un candidato solo il marcatore nudo non è ambiguo: è quello.

        Il file può avere N task — quello delle vitamine qui gira benissimo — e
        chiedere un numero dove non c'è niente da distinguere sarebbe solo
        un'occasione di sbagliarlo. È la stessa regola, e lo stesso parametro
        ``default`` di :func:`attribute_marks`, che vale per i guasti.
        """
        tasks = parse_heartbeat_tasks(_file(_WATERBOT, _VITAMINE))
        state = CronJobState()

        record_task_outcomes(
            state,
            tasks,
            [CouldNotCheckMark("1", "hps giù")],
            now_ms=10,
            escalating=[],
            warned=[CouldNotCheckMark(None, "")],
        )

        assert state.task_checks[tasks[0].id].escalated is True

    def test_a_message_without_the_marker_is_not_a_recorded_warning(self) -> None:
        """Il prezzo di questo meccanismo, dichiarato.

        Un modello che manda l'avviso e si dimentica la riga non lascia traccia,
        quindi al giro dopo gli verrà chiesto di nuovo e l'utente sentirà la
        stessa cosa due volte. È il costo scelto: rumore recuperabile invece di
        un guasto zittito per sempre, ed è anche la direzione che
        ``jenny/cron/silence_watchdog.py`` presidia dall'altro lato.
        """
        tasks = parse_heartbeat_tasks(_file(_WATERBOT))
        state = CronJobState(
            task_checks={tasks[0].id: CronTaskCheckState(consecutive_could_not_check=2)}
        )

        record_task_outcomes(
            state,
            tasks,
            [CouldNotCheckMark("1", "hps giù")],
            now_ms=10,
            escalating=tasks,  # gli è stato chiesto di parlare
            warned=[],  # ha parlato (o no), ma non l'ha dichiarato
        )

        assert state.task_checks[tasks[0].id].escalated is False
        assert tasks_due_for_escalation(state, tasks) == [tasks[0]]

    def test_a_warning_about_the_already_warned_task_does_not_stamp_the_new_one(self) -> None:
        """La forma davvero raggiungibile della stessa trappola, e la peggiore.

        Il task 1 è già stato annunciato, è ancora rotto ed è nel blocco
        ``already_warned``. Quel blocco esiste proprio perché il modello, con il
        guasto ancora davanti, ``message`` lo chiama lo stesso: è il
        comportamento misurato sul device che la sua docstring registra. In
        quello stesso turno il task 2 si rompe per la **prima** volta ed è
        l'unica riga ``CHECK_FAILED``.

        Se "unico guasto dichiarato" bastasse ad attribuire il messaggio, il
        task 2 verrebbe timbrato ``escalated`` alla prima mancanza: da lì entra
        nel blocco ``already_warned``, non è più dovuto per escalation, e
        all'utente non arriva mai. Un guasto reale zittito per sempre.

        Il task 1 qui non scrive il suo ``CHECK_FAILED`` — succede, ed è il 02:31
        del logcat — quindi la sua voce viene potata in questo stesso turno; ma
        il suo ``CHECK_WARNED`` dice di quale controllo il messaggio parlava, e
        quello basta.
        """
        tasks = parse_heartbeat_tasks(_file(_WATERBOT, _VITAMINE))
        state = CronJobState(
            task_checks={
                tasks[0].id: CronTaskCheckState(
                    consecutive_could_not_check=5, escalated=True, label="Ogni ciclo"
                )
            }
        )

        record_task_outcomes(
            state,
            tasks,
            [CouldNotCheckMark("2", "sveglia non impostata")],
            now_ms=10,
            escalating=[],  # nessuno ha chiesto di parlare: il 1 è già annunciato
            warned=[CouldNotCheckMark("1", "")],  # ha riparlato del 1, non del 2
        )

        assert state.task_checks[tasks[1].id].escalated is False

        # E il 2 resta annunciabile quando la sua soglia arriva.
        for run in range(1, ESCALATE_AFTER_FAILURES):
            record_task_outcomes(
                state, tasks, [CouldNotCheckMark("2", "sveglia non impostata")],
                now_ms=10 + run, escalating=[],
            )
        assert tasks_due_for_escalation(state, tasks) == [tasks[1]]

    def test_the_same_shape_on_the_announce_turn(self) -> None:
        """Il turno d'annuncio porta gli stessi due blocchi condizionali del run,
        quindi può parlare del task già avvisato esattamente allo stesso modo."""
        tasks = parse_heartbeat_tasks(_file(_WATERBOT, _VITAMINE))
        state = CronJobState(
            task_checks={
                tasks[0].id: CronTaskCheckState(
                    consecutive_could_not_check=5, escalated=True, pending_since_ms=11
                ),
                tasks[1].id: CronTaskCheckState(pending_since_ms=11),
            }
        )

        record_followup_outcomes(
            state, tasks, [CouldNotCheckMark("2", "sveglia non impostata")],
            now_ms=12, escalating=[], warned=[CouldNotCheckMark("1", "")],
        )

        assert state.task_checks[tasks[1].id].escalated is False

    def test_an_explicit_escalation_still_covers_every_task_it_named(self) -> None:
        """Il blocco di escalation chiede **un** messaggio per tutti i task che
        elenca, e una riga ``CHECK_WARNED`` per ciascuno di quelli che quel
        messaggio ha davvero nominato: un solo ``message`` li copre tutti."""
        tasks = parse_heartbeat_tasks(_file(_WATERBOT, _VITAMINE))
        state = CronJobState()

        record_task_outcomes(
            state, tasks,
            [CouldNotCheckMark("1", "hps giù"), CouldNotCheckMark("2", "backup giù")],
            now_ms=10, escalating=tasks,
            warned=[CouldNotCheckMark("1", ""), CouldNotCheckMark("2", "")],
        )

        assert [e.escalated for e in state.task_checks.values()] == [True, True]

    def test_a_second_fault_after_a_recovery_warns_again(self) -> None:
        """Il test che vale tutti gli altri: il ciclo intero, dall'avviso al successivo."""
        tasks = parse_heartbeat_tasks(_file(_WATERBOT))
        state = self._broken_and_announced(tasks[0].id)

        # Il controllo torna a funzionare e lo dichiara.
        record_followup_outcomes(
            state, tasks, [], now_ms=12, escalating=[],
            ok=[CouldNotCheckMark("1", "")],
        )
        assert state.task_checks == {}

        # Settimane dopo si rompe di nuovo: tre cicli, e al terzo si parla.
        for run in range(3):
            assert (tasks_due_for_escalation(state, tasks) != []) is (run == 2)
            record_task_outcomes(
                state,
                tasks,
                [CouldNotCheckMark("1", "hps irraggiungibile")],
                now_ms=100 + run,
                escalating=tasks_due_for_escalation(state, tasks),
                warned=[CouldNotCheckMark("1", "")] if run == 2 else [],
            )

        assert state.task_checks[tasks[0].id].escalated is True

    def test_a_task_that_runs_again_in_a_run_forgets_the_announcement(self) -> None:
        """La seconda via d'uscita, per un task che smette di essere delegato:
        nessun marcatore in un run e la voce sparisce con la sua regola di sempre."""
        tasks = parse_heartbeat_tasks(_file(_WATERBOT))
        state = CronJobState(
            task_checks={tasks[0].id: CronTaskCheckState(escalated=True, label="Ogni ciclo")}
        )

        record_task_outcomes(state, tasks, [], now_ms=12, escalating=[])

        assert state.task_checks == {}


class TestTheSilenceInstruction:
    def test_a_healthy_run_has_no_silence_block(self) -> None:
        """Il prompt di un run sano resta byte-identico: niente voci, niente blocco."""
        tasks = parse_heartbeat_tasks(_file(_WATERBOT, _VITAMINE))

        assert tasks_already_warned(CronJobState(), tasks) == []

    def test_only_the_tasks_already_announced_are_listed(self) -> None:
        tasks = parse_heartbeat_tasks(_file(_WATERBOT, _VITAMINE))
        state = CronJobState(
            task_checks={
                tasks[0].id: CronTaskCheckState(escalated=True, consecutive_could_not_check=5),
                tasks[1].id: CronTaskCheckState(consecutive_could_not_check=1),
            }
        )

        warned = tasks_already_warned(state, tasks)

        assert warned == [tasks[0]]
        block = already_warned_block(warned)
        assert "1. Ogni ciclo" in block
        assert "vitamine" not in block
        assert "Do not tell them again" in block

    def test_a_task_is_never_both_due_and_already_warned(self) -> None:
        """I due blocchi si contraddirebbero. Sono disgiunti per costruzione."""
        tasks = parse_heartbeat_tasks(_file(_WATERBOT))
        state = CronJobState(
            task_checks={
                tasks[0].id: CronTaskCheckState(consecutive_could_not_check=9, escalated=True)
            }
        )

        assert tasks_already_warned(state, tasks) == [tasks[0]]
        assert tasks_due_for_escalation(state, tasks) == []

    def test_the_announce_block_carries_the_silence_instruction_too(self) -> None:
        tasks = parse_heartbeat_tasks(_file(_WATERBOT))

        block = followup_block([tasks[0]], [], [tasks[0]])

        assert "Do not tell them again" in block
        assert "EXACTLY ONCE" not in block


class TestRecognisingAMarkerFromOutside:
    """``is_only_markers`` sta qui perché qui vive la grammatica dei marcatori,
    ma il consumatore è il tool ``message``: deve poter riconoscere un marcatore
    *prima* di consegnarlo all'utente come se fosse un avviso."""

    def test_a_marker_line_is_recognised_with_its_markdown_noise(self) -> None:
        assert is_only_markers("CHECK_OK 1")
        assert is_only_markers("- CHECK_OK 1")
        assert is_only_markers("**CHECK_OK 1**")
        assert is_only_markers("CHECK_OK 1\nCHECK_FAILED 2: rotto")

    def test_all_four_words_are_recognised(self) -> None:
        for marker in ("CHECK_OK", "CHECK_FAILED", "CHECK_DELEGATED", "CHECK_WARNED"):
            assert is_only_markers(f"{marker} 1"), marker

    def test_a_real_sentence_is_not_a_marker(self) -> None:
        assert not is_only_markers("Acerello è al 9%, dagli acqua")
        assert not is_only_markers("Acerello è al 9%\nCHECK_WARNED 1")

    def test_empty_is_not_a_marker(self) -> None:
        """Vuoto è vuoto, e chi chiama lo distingue prima: un ``True`` qui
        darebbe al modello la spiegazione sbagliata."""
        assert not is_only_markers("")
        assert not is_only_markers("   \n  ")
        assert not is_only_markers(None)


class TestTheRunTurnDeclaringItselfFine:
    """Il turno che *delega* può anche dichiarare quel task a posto, e non conta.

    Osservato sul Titan 2 alle 13:26 del 2026-09-03, quarto ciclo dopo
    l'introduzione di ``nothing_to_report``: il turno del run ha chiamato il tool
    con ``task=1`` **e poi** ha delegato lo stesso task 1. Le due cose si
    contraddicono — un controllo appena affidato a un subagent non ha ancora una
    risposta — ma oggi non fanno danno, perché il registratore del run non legge
    affatto ``CHECK_OK``: il verdetto di un controllo delegato lo scrive il turno
    d'annuncio (``record_followup_outcomes``), che il risultato ce l'ha in mano.

    Questo test esiste per tenere ferma quella cecità. Chi un domani passasse
    ``ok=`` anche a ``record_task_outcomes`` per simmetria chiuderebbe come sano,
    nello stesso turno, un controllo che sta appena partendo — e la delega
    sparirebbe dallo stato senza che nessuno abbia guardato il risultato.
    """

    def test_the_run_recorder_takes_no_ok_argument(self) -> None:
        import inspect

        assert "ok" not in inspect.signature(record_task_outcomes).parameters

    def test_a_task_declared_fine_and_delegated_in_one_turn_stays_pending(self) -> None:
        tasks = parse_heartbeat_tasks(
            "## Active Tasks\n\n- controlla l'umidità\n- ricordami le vitamine\n"
        )
        state = CronJobState()

        outcome = record_task_outcomes(
            state,
            tasks,
            [],
            now_ms=1_000,
            escalating=[],
            delegated=[CouldNotCheckMark("1", "letture umidità")],
        )

        assert [t.index for t in outcome.pending] == [1]
        assert state.task_checks[tasks[0].id].pending_since_ms == 1_000


class TestTheModelQuotingItsOwnInstructions:
    """Il preambolo rigurgitato non è un verdetto.

    Misurato sul Titan 2 il 2026-09-03 alle 11:25: il modello ha scritto la sua
    riga vera e poi ha riversato in coda l'intero preambolo — 4.883 caratteri,
    segnaposto compresi. Il parser li ha letti come dichiarazioni e un controllo
    sano è finito registrato come guasto **e** come già segnalato all'utente,
    senza che nessun avviso sia partito. È lo stato "controllo morto in
    silenzio", prodotto da un controllo che stava benissimo.
    """

    # Le tre righe esatte lette dal dispositivo, verbatim.
    ECHOED = (
        "CHECK_DELEGATED 1: reading all plants' soil humidity via the waterbot skill\n"
        "\n"
        "[This is a scheduled background check. It is SILENT by default: whatever you "
        "write as your answer is NOT delivered to the user and nobody reads it.\n"
        "CHECK_FAILED <task number>: <one short line naming what stopped you>\n"
        "CHECK_WARNED <task number>\n"
        "CHECK_DELEGATED <task number>: <what you asked the subagent for>\n"
    )

    def test_an_echoed_failure_specimen_is_not_a_failure(self) -> None:
        assert parse_could_not_check_marks(self.ECHOED) == []

    def test_an_echoed_warning_specimen_does_not_stamp_the_alert(self) -> None:
        """Il peggiore dei tre: ``escalated`` zittisce gli avvisi veri di quel
        controllo, e nessuno se ne accorge finché non serve."""
        assert parse_warned_marks(self.ECHOED) == []

    def test_the_real_line_in_the_same_answer_still_counts(self) -> None:
        """Non si butta via la risposta: si buttano via i segnaposto."""
        marks = parse_delegated_marks(self.ECHOED)
        assert [m.ref for m in marks] == ["1"]

    def test_a_specimen_in_the_reason_position_is_read_too(self) -> None:
        assert parse_could_not_check_marks("CHECK_FAILED: <reason>") == []

    def test_a_real_reason_is_never_mistaken_for_one(self) -> None:
        marks = parse_could_not_check_marks("CHECK_FAILED 2: hps unreachable")
        assert [(m.ref, m.reason) for m in marks] == [("2", "hps unreachable")]


class TestThePositiveMarker:
    def test_the_marker_is_read_with_and_without_a_number(self) -> None:
        assert parse_ok_marks("CHECK_OK 2")[0].ref == "2"
        assert parse_ok_marks("CHECK_OK")[0].ref is None

    def test_the_three_markers_do_not_read_each_other(self) -> None:
        text = "CHECK_OK 1\nCHECK_FAILED 2: rotto\nCHECK_DELEGATED 3: passato a un subagent"

        assert [m.ref for m in parse_ok_marks(text)] == ["1"]
        assert [m.ref for m in parse_could_not_check_marks(text)] == ["2"]
        assert [m.ref for m in parse_delegated_marks(text)] == ["3"]

    def test_the_monitor_reader_ignores_it(self) -> None:
        """Il monitor ha un controllo per turno: là il silenzio è già una prova."""
        assert could_not_check_reason("CHECK_OK") is None

    def test_the_announce_block_asks_for_a_verdict_in_both_directions(self) -> None:
        tasks = parse_heartbeat_tasks(_file(_WATERBOT))

        block = followup_block([tasks[0]], [])

        assert "CHECK_FAILED <number>:" in block
        assert "CHECK_OK <number>" in block

    def test_an_unreachable_target_is_a_failure_even_when_told_to_give_up_quietly(
        self,
    ) -> None:
        """Misurato sul device il 2026-08-16, ed è il motivo per cui questa
        frase esiste. Il task WaterBot dice "se hps è irraggiungibile non
        ritentare, riporta UNREACHABLE e fermati"; il subagent ha riportato
        correttamente ``UNREACHABLE``; e il turno d'annuncio ha scritto
        ``CHECK_OK``. Una versione precedente di questo blocco diceva che un
        controllo saltato per istruzione propria valeva un verdetto positivo —
        una frase innocua finché significava "non scrivere niente", ma che
        trasformata in un'affermazione di salute copriva esattamente il guasto.
        """
        tasks = parse_heartbeat_tasks(_file(_WATERBOT))

        block = followup_block([tasks[0]], [])

        assert "could not reach what it needed did NOT produce an answer" in block
        assert "give up quietly" in block
        # E il dubbio si risolve verso il guasto, non verso la salute.
        assert "If you are unsure which of the two a result is, it is CHECK_FAILED" in block


class TestThePromptFragments:
    def test_the_index_block_names_every_task_by_number(self) -> None:
        tasks = parse_heartbeat_tasks(_file(_WATERBOT, _VITAMINE))

        block = task_index_block(tasks)

        assert "1. Ogni ciclo" in block
        assert "2. Alle 9 ricordami le vitamine." in block

    def test_the_index_block_says_the_numbers_are_not_for_the_user(self) -> None:
        block = task_index_block(parse_heartbeat_tasks(_file(_WATERBOT)))

        assert "never in a message to the user" in block

    @staticmethod
    def _due(*streaks: int) -> list[HeartbeatTask]:
        """I task dovuti, con la loro sequenza: come li produce il run vero.

        ``escalation_block`` si costruisce sempre sull'uscita di
        ``tasks_due_for_escalation``, che è la sola a sapere quanti run sono
        mancati davvero — passargli dei task grezzi qui vorrebbe dire misurare
        una chiamata che non esiste.
        """
        tasks = parse_heartbeat_tasks(_file(_WATERBOT, _VITAMINE))
        state = CronJobState(
            task_checks={
                task.id: CronTaskCheckState(consecutive_could_not_check=streak)
                for task, streak in zip(tasks, streaks)
            }
        )
        return tasks_due_for_escalation(state, tasks)

    def test_the_escalation_block_asks_for_one_message_for_all_of_them(self) -> None:
        block = escalation_block(self._due(2, 2))

        assert "EXACTLY ONCE" in block
        assert "never one per task" in block
        assert "no internal file names" in block

    def test_the_escalation_block_lists_only_the_tasks_that_are_due(self) -> None:
        block = escalation_block(self._due(0, 2))

        assert "Alle 9 ricordami le vitamine." in block
        assert "Ogni ciclo" not in block

    def test_the_escalation_block_counts_the_runs_that_have_actually_happened(self) -> None:
        """Il numero è quello vero, non la soglia.

        ``tasks_due_for_escalation`` scatta a ``K - 1``: quando il blocco viene
        costruito i run mancati sono **due**, e il terzo è quello che sta per
        girare. Il modello quel numero lo riferisce all'utente, che si sentiva
        dire "tre" di una cosa successa due volte. Il monitor lo fa già giusto
        (v. ``test_the_prompt_counts_the_runs_that_have_actually_happened`` in
        ``test_cron_monitor_could_not_check.py``); qui la costante era scritta
        a mano nel testo.
        """
        block = escalation_block(self._due(ESCALATE_AFTER_FAILURES - 1))

        assert f"({ESCALATE_AFTER_FAILURES - 1} runs in a row)" in block
        assert f"{ESCALATE_AFTER_FAILURES} times in a row" not in block
        assert f"({ESCALATE_AFTER_FAILURES} runs in a row)" not in block

    def test_each_listed_task_carries_its_own_count(self) -> None:
        """Un blocco solo può nominarne più d'uno, e le sequenze non coincidono.

        Un task dovuto alla soglia e uno di cui il modello aveva già ignorato
        l'istruzione arrivano qui con due numeri diversi: una frase sola per
        entrambi ne direbbe uno sbagliato per uno dei due.

        Il secondo numero sta dentro ``ESCALATION_ASK_LIMIT``, e non è un
        dettaglio del test: oltre quella finestra non si chiede più (v.
        :class:`TestTheAskStopsInsteadOfRepeatingForever`), quindi un task a
        sequenza 5 nel blocco non ci sarebbe.
        """
        due = self._due(2, ESCALATE_AFTER_FAILURES - 1 + ESCALATION_ASK_LIMIT - 1)

        block = escalation_block(due)

        assert f"- 1. {due[0].label} (2 runs in a row)" in block
        assert f"- 2. {due[1].label} ({due[1].failed_runs} runs in a row)" in block


class TestTheAskStopsInsteadOfRepeatingForever:
    """Il costo residuo di ``CHECK_WARNED``, e il suo tetto.

    Il timbro è una riga che il modello deve scrivere, quindi un modello che non
    la scrive mai non fa mai scattare ``already_warned``: senza limite il blocco
    di escalation tornerebbe nel prompt a ogni run per sempre, e con lui un
    messaggio all'utente ogni mezz'ora. Dove la finestra finisce comincia
    ``jenny/cron/silence_watchdog.py``, che non passa dal modello.
    """

    def _state_at(self, task_id: str, streak: int) -> CronJobState:
        return CronJobState(
            task_checks={task_id: CronTaskCheckState(consecutive_could_not_check=streak)}
        )

    def test_the_ask_covers_exactly_the_window(self) -> None:
        tasks = parse_heartbeat_tasks(_file(_WATERBOT))
        first = ESCALATE_AFTER_FAILURES - 1

        asked = [
            tasks_due_for_escalation(self._state_at(tasks[0].id, streak), tasks) != []
            for streak in range(first + ESCALATION_ASK_LIMIT + 2)
        ]

        assert asked == [False] * first + [True] * ESCALATION_ASK_LIMIT + [False, False]

    def test_the_window_hands_over_to_the_watchdog_with_no_gap(self) -> None:
        """Le due finestre sono contigue, e questo test è ciò che le tiene tali."""
        from jenny.cron.silence_watchdog import WATCHDOG_AFTER_FAILURES

        assert ESCALATE_AFTER_FAILURES - 1 + ESCALATION_ASK_LIMIT == WATCHDOG_AFTER_FAILURES - 1
