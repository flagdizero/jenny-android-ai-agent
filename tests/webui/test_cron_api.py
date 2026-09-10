"""Il payload della programmazione: cosa dice, e cosa si rifiuta di dire.

Le asserzioni che contano non sono «il dizionario ha le chiavi giuste»: sono i
casi in cui lo store da solo racconterebbe una bugia con la formattazione giusta
— un job disabilitato che sparisce, un lavoratore spento che risulta sano, un
heartbeat che non controlla niente e registra ``ok`` a ogni giro.

I promemoria delle fixture sono **inventati**. Il repo e' pubblico e un test e'
un documento come un altro: nessun testo vero copiato dal telefono.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from jenny.cron.service import CronService
from jenny.cron.types import CronJob, CronPayload, CronSchedule, CronTaskCheckState
from jenny.webui.cron_api import webui_cron_payload

_WATERING = "- Ogni ciclo guarda l'umidita' del vaso e avvisami solo sotto il 15%."
_PILLS = "- Alle 9 ricordami le gocce."


def _heartbeat_file(*tasks: str) -> str:
    body = "\n".join(tasks)
    return f"# Heartbeat Tasks\n\n<!-- nota -->\n\n## Active Tasks\n\n{body}\n"


def _config(tmp_path, **flags):
    """Un ``Config`` finto con i quattro interruttori ai percorsi veri."""
    on = {"dream": True, "gardener": True, "heartbeat": True, "updates": True, **flags}
    return SimpleNamespace(
        workspace_path=tmp_path,
        agents=SimpleNamespace(
            defaults=SimpleNamespace(
                timezone="Europe/Rome",
                dream=SimpleNamespace(enabled=on["dream"]),
                gardener=SimpleNamespace(enabled=on["gardener"]),
            )
        ),
        gateway=SimpleNamespace(heartbeat=SimpleNamespace(enabled=on["heartbeat"])),
        updates=SimpleNamespace(enabled=on["updates"]),
    )


@pytest.fixture
def cron(tmp_path):
    return CronService(tmp_path / "cron" / "jobs.json")


def _system(job_id: str, *, every_ms: int = 1_800_000) -> CronJob:
    return CronJob(
        id=job_id,
        name=job_id,
        schedule=CronSchedule(kind="every", every_ms=every_ms),
        payload=CronPayload(kind="system_event"),
    )


# ── il caso in cui il servizio non c'e' ancora ──────────────────────────────

def test_no_service_yet_is_a_state_not_an_error(tmp_path):
    """Durante l'onboarding la WebUI e' servita e il cron non esiste."""
    payload = webui_cron_payload(None, config=_config(tmp_path))

    assert payload["available"] is False
    assert payload["jobs"] == []
    assert payload["now_ms"] > 0


# ── §5.1: i disabilitati non devono sparire ─────────────────────────────────

def test_a_disabled_job_is_listed_not_hidden(cron, tmp_path):
    """``list_jobs()`` filtra per default, e il filtro qui e' una bugia.

    Senza questo, «l'ho disabilitato o l'ho cancellato?» resta senza risposta —
    che e' una delle domande per cui il pannello esiste.
    """
    job = cron.add_job("gocce", CronSchedule(kind="every", every_ms=60_000), _PILLS)
    cron.enable_job(job.id, False)

    payload = webui_cron_payload(cron, config=_config(tmp_path))

    ids = [j["id"] for j in payload["jobs"]]
    assert job.id in ids
    row = next(j for j in payload["jobs"] if j["id"] == job.id)
    assert row["enabled"] is False
    assert row["effective"] == "disabled"
    assert payload["counts"]["total"] == 1
    assert payload["counts"]["enabled"] == 0


# ── §5.8: armato e inerte ───────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("job_id", "flag"),
    [("dream", "dream"), ("gardener", "gardener"), ("update_check", "updates")],
)
def test_a_worker_switched_off_in_config_is_inert_not_healthy(cron, tmp_path, job_id, flag):
    """Il job resta armato nello store e il gestore esce con ``return None``.

    Cioe' registra ``ok`` senza fare niente: qui e' l'unico posto in cui la
    differenza puo' diventare visibile.
    """
    cron.register_system_job(_system(job_id))
    job = cron.get_job(job_id)
    job.state.last_status = "ok"
    cron.persist_job_state()

    payload = webui_cron_payload(cron, config=_config(tmp_path, **{flag: False}))
    row = next(j for j in payload["jobs"] if j["id"] == job_id)

    assert row["enabled"] is True, "nello store e' ancora armato: e' il punto"
    assert row["last_status"] == "ok", "e continua a registrare ok"
    assert row["effective"] == "inert"
    assert payload["counts"]["inert"] == 1


def test_a_worker_switched_on_is_active(cron, tmp_path):
    cron.register_system_job(_system("gardener"))

    payload = webui_cron_payload(cron, config=_config(tmp_path))

    assert next(j for j in payload["jobs"] if j["id"] == "gardener")["effective"] == "active"
    assert payload["counts"]["inert"] == 0


def test_an_unresolvable_flag_path_does_not_invent_inert(cron, tmp_path):
    """Un attributo che manca vuol dire «non lo so», non «spento».

    Un pannello che dichiara "inerte" perche' non ha trovato un attributo mente
    con piu' sicurezza di uno che non dice niente.
    """
    cron.register_system_job(_system("dream"))
    broken = SimpleNamespace(workspace_path=tmp_path)  # nessun ``agents``

    row = webui_cron_payload(cron, config=broken)["jobs"][0]

    assert row["effective"] == "active"


# ── §4.2: l'heartbeat e i suoi task ─────────────────────────────────────────

def test_a_heartbeat_with_no_task_file_is_checking_nothing(cron, tmp_path):
    """Il caso peggiore: gira ogni mezz'ora, registra ``ok``, non controlla niente."""
    cron.register_system_job(_system("heartbeat"))
    job = cron.get_job("heartbeat")
    job.state.last_status = "ok"
    cron.persist_job_state()

    row = webui_cron_payload(cron, config=_config(tmp_path))["jobs"][0]

    assert row["last_status"] == "ok"
    assert row["heartbeat"]["file_present"] is False
    assert row["heartbeat"]["active_task_count"] == 0
    assert row["effective"] == "inert"


def test_a_task_file_with_only_headings_is_also_checking_nothing(cron, tmp_path):
    (tmp_path / "HEARTBEAT.md").write_text(_heartbeat_file(""), encoding="utf-8")
    cron.register_system_job(_system("heartbeat"))

    row = webui_cron_payload(cron, config=_config(tmp_path))["jobs"][0]

    assert row["heartbeat"]["file_present"] is True
    assert row["heartbeat"]["file_readable"] is True
    assert row["heartbeat"]["active_task_count"] == 0
    assert row["effective"] == "inert"


def test_an_unreadable_task_file_is_a_third_state(cron, tmp_path, monkeypatch):
    """Il file che c'e' e non si legge e' un guasto, non un file mai creato."""
    path = tmp_path / "HEARTBEAT.md"
    path.write_text(_heartbeat_file(_WATERING), encoding="utf-8")
    original = type(path).read_text

    def _boom(self, *a, **kw):
        if self.name == "HEARTBEAT.md":
            raise OSError("permessi")
        return original(self, *a, **kw)

    monkeypatch.setattr(type(path), "read_text", _boom)
    cron.register_system_job(_system("heartbeat"))

    row = webui_cron_payload(cron, config=_config(tmp_path))["jobs"][0]

    assert row["heartbeat"]["file_present"] is True
    assert row["heartbeat"]["file_readable"] is False


def test_healthy_tasks_are_listed_with_no_check_entries(cron, tmp_path):
    (tmp_path / "HEARTBEAT.md").write_text(
        _heartbeat_file(_WATERING, _PILLS), encoding="utf-8"
    )
    cron.register_system_job(_system("heartbeat"))

    row = webui_cron_payload(cron, config=_config(tmp_path))["jobs"][0]

    assert row["heartbeat"]["active_task_count"] == 2
    assert [t["state"] for t in row["heartbeat"]["tasks"]] == ["ok", "ok"]
    assert row["heartbeat"]["orphan_checks"] == []
    assert row["effective"] == "active"


def test_the_join_marks_broken_and_pending_and_leaves_the_rest_ok(cron, tmp_path):
    """Le tre specie in una volta: il file dice quali esistono, lo store quali no."""
    from jenny.cron.heartbeat_tasks import parse_heartbeat_tasks

    content = _heartbeat_file(_WATERING, _PILLS)
    (tmp_path / "HEARTBEAT.md").write_text(content, encoding="utf-8")
    tasks = parse_heartbeat_tasks(content)

    cron.register_system_job(_system("heartbeat"))
    job = cron.get_job("heartbeat")
    job.state.task_checks[tasks[0].id] = CronTaskCheckState(
        consecutive_could_not_check=3, since_ms=1_000, label=tasks[0].label, escalated=True,
        escalated_at_ms=2_000,
    )
    job.state.task_checks[tasks[1].id] = CronTaskCheckState(
        pending_since_ms=3_000, label=tasks[1].label
    )
    cron.persist_job_state()

    hb = webui_cron_payload(cron, config=_config(tmp_path))["jobs"][0]["heartbeat"]
    by_id = {t["id"]: t for t in hb["tasks"]}

    assert by_id[tasks[0].id]["state"] == "broken"
    assert by_id[tasks[0].id]["consecutive"] == 3
    assert by_id[tasks[0].id]["escalated"] is True
    assert by_id[tasks[0].id]["escalated_at_ms"] == 2_000
    assert by_id[tasks[1].id]["state"] == "pending"
    assert by_id[tasks[1].id]["consecutive"] == 0
    assert hb["orphan_checks"] == []


def test_a_check_without_a_task_becomes_an_orphan_not_a_broken_check(cron, tmp_path):
    """L'utente ha cancellato la riga mentre era rotta: niente ripulisce la voce.

    Mostrarla fra i controlli rotti lo accuserebbe di un controllo che ha tolto;
    nasconderla lascerebbe i contatori del job senza causa visibile.
    """
    (tmp_path / "HEARTBEAT.md").write_text(_heartbeat_file(_WATERING), encoding="utf-8")
    cron.register_system_job(_system("heartbeat"))
    job = cron.get_job("heartbeat")
    job.state.task_checks["un-id-che-non-e-piu-nel-file"] = CronTaskCheckState(
        consecutive_could_not_check=5, since_ms=1_000, label="un controllo tolto"
    )
    cron.persist_job_state()

    hb = webui_cron_payload(cron, config=_config(tmp_path))["jobs"][0]["heartbeat"]

    assert [t["state"] for t in hb["tasks"]] == ["ok"]
    assert len(hb["orphan_checks"]) == 1
    assert hb["orphan_checks"][0]["consecutive"] == 5


def test_only_the_heartbeat_carries_the_heartbeat_block(cron, tmp_path):
    cron.register_system_job(_system("dream"))

    assert webui_cron_payload(cron, config=_config(tmp_path))["jobs"][0]["heartbeat"] is None


# ── identita', vocabolario, fusi ────────────────────────────────────────────

def test_a_system_job_presents_itself_and_cannot_be_removed(cron, tmp_path):
    cron.register_system_job(_system("gardener"))

    row = webui_cron_payload(cron, config=_config(tmp_path))["jobs"][0]

    assert row["kind"] == "system"
    assert row["protected"] is True
    assert row["purpose"] and "Gardener" in row["purpose"]
    assert row["message"] is None and row["message_preview"] is None


def test_a_user_job_carries_a_preview_in_the_list_and_the_whole_text_in_the_detail(
    cron, tmp_path
):
    long_text = "Ricordami " + ("x" * 400)
    cron.add_job("lungo", CronSchedule(kind="every", every_ms=60_000), long_text)

    row = webui_cron_payload(cron, config=_config(tmp_path))["jobs"][0]

    assert row["kind"] == "user"
    assert row["protected"] is False
    assert row["purpose"] is None
    assert len(row["message_preview"]) == 160
    assert row["message"] == long_text


def test_the_schedule_travels_structured_not_as_an_english_sentence(cron, tmp_path):
    """La frase la compone il client: questa UI e' bilingue, il tool no."""
    cron.add_job("orario", CronSchedule(kind="cron", expr="0 9 * * *", tz="Europe/Rome"), _PILLS)

    row = webui_cron_payload(cron, config=_config(tmp_path))["jobs"][0]

    assert row["schedule"] == {
        "kind": "cron", "every_ms": None, "expr": "0 9 * * *",
        "at_ms": None, "tz": "Europe/Rome",
    }
    # Nessun campo pre-formattato: se un giorno ne comparisse uno, il client
    # smetterebbe di essere l'unico a decidere in che lingua si legge.
    assert set(row["schedule"]) == {"kind", "every_ms", "expr", "at_ms", "tz"}


def test_the_display_timezone_is_the_job_s_own_then_the_default(cron, tmp_path):
    """La stessa scelta di ``CronTool._display_timezone``: il pannello e la chat
    devono dire la stessa ora."""
    cron.add_job("con-tz", CronSchedule(kind="cron", expr="0 9 * * *", tz="Asia/Tokyo"), _PILLS)
    cron.add_job("senza-tz", CronSchedule(kind="every", every_ms=60_000), _PILLS)

    rows = {j["name"]: j for j in webui_cron_payload(cron, config=_config(tmp_path))["jobs"]}

    assert rows["con-tz"]["display_timezone"] == "Asia/Tokyo"
    assert rows["senza-tz"]["display_timezone"] == "Europe/Rome"


def test_the_monitor_mode_travels(cron, tmp_path):
    """Un monitor che tace ha funzionato: senza questo bit il client non lo sa."""
    cron.add_job(
        "monitor", CronSchedule(kind="every", every_ms=60_000), _WATERING, mode="monitor"
    )

    assert webui_cron_payload(cron, config=_config(tmp_path))["jobs"][0]["mode"] == "monitor"


def test_the_job_level_health_has_no_invented_escalated_at(cron, tmp_path):
    """A livello di job quel campo non esiste: ce l'ha la voce di ``task_checks``.

    Una simmetria falsa e' una bugia che il client poi renderizza.
    """
    cron.register_system_job(_system("heartbeat"))
    job = cron.get_job("heartbeat")
    job.state.consecutive_could_not_check = 2
    job.state.could_not_check_since_ms = 1_000
    job.state.could_not_check_escalated = True
    cron.persist_job_state()

    health = webui_cron_payload(cron, config=_config(tmp_path))["jobs"][0]["health"]

    assert health == {
        "consecutive_could_not_check": 2, "since_ms": 1_000, "escalated": True,
    }


def test_the_run_history_comes_back_newest_first(cron, tmp_path):
    from jenny.cron.types import CronRunRecord

    cron.register_system_job(_system("dream"))
    job = cron.get_job("dream")
    job.state.run_history = [
        CronRunRecord(run_at_ms=1_000, status="ok", duration_ms=10),
        CronRunRecord(run_at_ms=2_000, status="silenced", duration_ms=20),
    ]
    cron.persist_job_state()

    runs = webui_cron_payload(cron, config=_config(tmp_path))["jobs"][0]["runs"]

    assert [r["run_at_ms"] for r in runs] == [2_000, 1_000]
    assert runs[0]["status"] == "silenced"


# ── privacy: l'audit interno non passa da qui ───────────────────────────────

def test_the_payload_never_carries_a_rendered_prompt_or_a_response(cron, tmp_path):
    """I record in ``cron/runs/`` sono un audit interno e restano fuori.

    Asserzione sul JSON e non sui campi noti: cosi' regge anche a un campo
    aggiunto domani.
    """
    cron.register_system_job(_system("dream"))
    cron.add_job("promemoria", CronSchedule(kind="every", every_ms=60_000), _PILLS)
    cron.write_run_record(
        "dream:1:abc",
        {"rendered_prompt": "SEGRETO-PROMPT", "response": "SEGRETO-RISPOSTA"},
    )

    blob = json.dumps(webui_cron_payload(cron, config=_config(tmp_path)), ensure_ascii=False)

    assert "rendered_prompt" not in blob
    assert "SEGRETO-PROMPT" not in blob
    assert "SEGRETO-RISPOSTA" not in blob


# ── coerenza col banner di recupero ─────────────────────────────────────────

def test_the_panel_says_the_list_was_rebuilt_at_startup(cron, tmp_path, monkeypatch):
    """Lo stesso stato che le impostazioni mostrano in cima alla schermata.

    Non e' un doppione: il banner sta sopra una pagina lunga, l'elenco piu' in
    basso, e chi lo legge deve sapere **di quell'elenco** che e' stato
    ricostruito.
    """
    ctx = SimpleNamespace(
        cron_recovered_from="empty", cron_quarantine_path=tmp_path / "jobs.json.corrupt-1"
    )
    import jenny.runtime.context as context_mod

    monkeypatch.setattr(context_mod, "get_runtime_context", lambda: ctx)

    payload = webui_cron_payload(cron, config=_config(tmp_path))

    assert payload["recovery"]["restored_from"] == "empty"
    assert payload["recovery"]["broken_file"].endswith("jobs.json.corrupt-1")


def test_a_healthy_start_reports_no_recovery(cron, tmp_path):
    assert webui_cron_payload(cron, config=_config(tmp_path))["recovery"] is None


# ── la config si legge da disco ─────────────────────────────────────────────

def test_without_a_config_it_reads_from_disk(cron, tmp_path, monkeypatch):
    """Il vincolo che la correzione di Dream lascia in eredita'.

    I quattro cancelli chiamano ``load_config()`` e non ``self._config``, perche'
    quello del container e' fotografato alla costruzione. Un pannello che
    guardasse la fotografia direbbe «attivo» di un worker appena spento.
    """
    import jenny.config.loader as loader

    calls: list[int] = []

    def _fake_load_config():
        calls.append(1)
        return _config(tmp_path, dream=False)

    monkeypatch.setattr(loader, "load_config", _fake_load_config)
    cron.register_system_job(_system("dream"))

    row = webui_cron_payload(cron)["jobs"][0]

    assert calls, "la config non e' stata riletta da disco"
    assert row["effective"] == "inert"


# ── stato del servizio ──────────────────────────────────────────────────────

def test_a_stopped_scheduler_is_reported(cron, tmp_path):
    """Un elenco perfetto col servizio fermo e' una bugia con la formattazione
    giusta: le scadenze nello store sono nel passato e nessuno le rimette."""
    cron.register_system_job(_system("dream"))

    payload = webui_cron_payload(cron, config=_config(tmp_path))

    assert payload["service_running"] is False
