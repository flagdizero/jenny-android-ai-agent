"""Compatibilità dello store cron rispetto al campo ``payload.mode``.

``mode`` è nato dopo lo store: ogni ``jobs.json`` già sul telefono di un utente
è stato scritto **senza** quella chiave. Questi test sono la rete di sicurezza
contro una migrazione sbagliata: un job vecchio deve continuare a comportarsi
come si comportava (``reminder``, cioè parla sempre) e nessun altro campo deve
perdersi nel giro load → save.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from jenny.cron.service import CronService
from jenny.cron.types import CronSchedule

# Job scritto dalla versione precedente: struttura identica a quella emessa da
# ``_save_store``, tranne che per ``payload.mode``, che allora non esisteva.
_LEGACY_JOB: dict[str, Any] = {
    "id": "legacy-1",
    "name": "Daily Loving Message",
    "enabled": True,
    "schedule": {
        "kind": "cron",
        "atMs": None,
        "everyMs": None,
        "expr": "0 10 * * *",
        "tz": "Asia/Kuwait",
    },
    "payload": {
        "kind": "agent_turn",
        "message": "say something nice",
        "sessionKey": "unified:default",
        "originChannel": "websocket",
        "originChatId": "chat-1",
        "originMetadata": {"webui": True, "thread": {"id": "42"}},
    },
    "state": {
        "nextRunAtMs": 4_102_444_800_000,
        "lastRunAtMs": 1_700_000_000_000,
        "lastStatus": "ok",
        "lastError": None,
        "runHistory": [
            {"runAtMs": 1_700_000_000_000, "status": "ok", "durationMs": 1234, "error": None},
        ],
    },
    "createdAtMs": 1_699_000_000_000,
    "updatedAtMs": 1_700_000_000_000,
    "deleteAfterRun": False,
}


# Chiavi di stato nate dopo ``_LEGACY_JOB``, insieme al terzo esito dei monitor
# (``could_not_check``). Come ``payload.mode``, un salvataggio le aggiunge coi
# loro default; qui servono a dire "questo è tutto ciò che il giro load → save ha
# aggiunto", tenendo il resto dell'asserzione un confronto esatto.
_COULD_NOT_CHECK_DEFAULTS: dict[str, Any] = {
    "consecutiveCouldNotCheck": 0,
    "couldNotCheckSinceMs": None,
    "couldNotCheckEscalated": False,
    # Lo stesso terzo esito per singolo task dell'heartbeat: vuoto per ogni job
    # che non sia l'heartbeat, e vuoto anche per un heartbeat sano.
    "taskChecks": {},
}


def _as_saved(job: dict[str, Any], *, mode: str) -> dict[str, Any]:
    """Il job come lo riscrive ``_save_store``: stessi dati, campi nuovi ai default."""
    expected = copy.deepcopy(job)
    expected["payload"]["mode"] = mode
    expected["state"] = {**expected["state"], **_COULD_NOT_CHECK_DEFAULTS}
    # La pausa dall'officina (26/09/2026): un job scritto prima non e' in pausa.
    expected["pausedAtMs"] = None
    return expected


def _write_store(tmp_path: Path, job: dict[str, Any]) -> Path:
    store_path = tmp_path / "cron" / "jobs.json"
    store_path.parent.mkdir(parents=True)
    store_path.write_text(
        json.dumps({"version": 1, "jobs": [job]}, ensure_ascii=False), encoding="utf-8"
    )
    return store_path


def test_a_job_written_before_the_mode_field_existed_still_speaks(tmp_path: Path) -> None:
    """Store senza ``mode``: il job si ricarica come ``reminder``.

    È il default che conserva il comportamento che l'utente vedeva prima: un
    promemoria che tace dopo un aggiornamento sarebbe indistinguibile da un
    promemoria rotto.
    """
    store_path = _write_store(tmp_path, _LEGACY_JOB)

    job = CronService(store_path).get_job("legacy-1")

    assert job is not None
    assert job.payload.mode == "reminder"


def test_reloading_a_legacy_job_loses_nothing_but_gains_the_default_mode(
    tmp_path: Path,
) -> None:
    """Load + save su uno store vecchio: solo ``mode`` in più, niente in meno."""
    store_path = _write_store(tmp_path, _LEGACY_JOB)

    service = CronService(store_path)
    assert service._load_store() is not None
    service._save_store()

    saved = json.loads(store_path.read_text(encoding="utf-8"))

    assert saved == {"version": 1, "jobs": [_as_saved(_LEGACY_JOB, mode="reminder")]}


def test_a_mode_this_version_cannot_execute_falls_back_instead_of_crashing(
    tmp_path: Path,
) -> None:
    """Store scritto da una versione futura (o a mano): si ripiega su ``reminder``.

    Il file può arrivare da un downgrade o da un'app che non conosciamo: un
    valore ignoto non deve impedire l'avvio né far sparire il job.
    """
    exotic = copy.deepcopy(_LEGACY_JOB)
    exotic["payload"]["mode"] = "telepathy"
    store_path = _write_store(tmp_path, exotic)

    job = CronService(store_path).get_job("legacy-1")

    assert job is not None
    assert job.payload.mode == "reminder"


def test_an_unknown_mode_is_rewritten_as_reminder_on_the_next_save(tmp_path: Path) -> None:
    """Caratterizzazione: il ripiego non è solo in memoria, finisce anche su disco.

    Il valore ignoto viene normalizzato al primo salvataggio, quindi un
    downgrade non è reversibile: chi torna alla versione futura ritrova
    ``reminder``. Comportamento attuale, fissato qui perché cambiarlo sia una
    scelta e non un incidente.
    """
    exotic = copy.deepcopy(_LEGACY_JOB)
    exotic["payload"]["mode"] = "telepathy"
    store_path = _write_store(tmp_path, exotic)

    service = CronService(store_path)
    assert service._load_store() is not None
    service._save_store()

    saved = json.loads(store_path.read_text(encoding="utf-8"))
    assert saved["jobs"][0]["payload"]["mode"] == "reminder"


def test_a_monitor_created_while_the_service_is_stopped_survives_the_action_log(
    tmp_path: Path,
) -> None:
    """``add_job`` a servizio fermo passa da ``action.jsonl``, non da ``jobs.json``.

    È un secondo percorso di serializzazione (``asdict`` → ``CronJob.from_dict``)
    che non condivide una riga con ``_parse_jobs``: se ``mode`` si perdesse qui,
    ogni monitor creato prima dell'avvio tornerebbe a parlare.
    """
    store_path = tmp_path / "cron" / "jobs.json"
    created = CronService(store_path).add_job(
        name="watcher",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="controlla la posta",
        mode="monitor",
        session_key="unified:default",
        origin_channel="websocket",
        origin_chat_id="chat-1",
    )
    assert (store_path.parent / "action.jsonl").exists()

    merged = CronService(store_path).get_job(created.id)

    assert merged is not None
    assert merged.payload.mode == "monitor"


# Il journal è la seconda porta d'ingresso dallo storage, accanto a jobs.json:
# senza la normalizzazione in _merge_action un modo ignoto entrava verbatim nel
# modello in memoria, violando il Literal di CronPayload.mode, e _save_store lo
# riscriveva tale e quale su disco.
def test_an_unknown_mode_in_the_action_log_falls_back_like_it_does_on_disk(
    tmp_path: Path,
) -> None:
    store_path = tmp_path / "cron" / "jobs.json"
    action_path = store_path.parent / "action.jsonl"
    action_path.parent.mkdir(parents=True)
    action_path.write_text(
        json.dumps(
            {
                "action": "add",
                "params": {
                    "id": "weird-1",
                    "name": "watcher",
                    "enabled": True,
                    "schedule": {"kind": "every", "at_ms": None, "every_ms": 60_000},
                    "payload": {
                        "kind": "agent_turn",
                        "mode": "telepathy",
                        "message": "controlla",
                        "session_key": "unified:default",
                        "origin_channel": "websocket",
                        "origin_chat_id": "chat-1",
                        "origin_metadata": {},
                    },
                    "state": {},
                    "created_at_ms": 1,
                    "updated_at_ms": 1,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    job = CronService(store_path).get_job("weird-1")

    assert job is not None
    assert job.payload.mode == "reminder"


def test_a_monitor_job_survives_a_full_save_load_cycle(tmp_path: Path) -> None:
    """Il caso simmetrico: ``monitor`` non deve degradare a ``reminder``.

    È il guasto peggiore possibile per questa feature — il job torna a parlare
    a ogni ciclo e nulla nello store dice perché.
    """
    monitor = copy.deepcopy(_LEGACY_JOB)
    monitor["payload"]["mode"] = "monitor"
    store_path = _write_store(tmp_path, monitor)

    service = CronService(store_path)
    assert service._load_store() is not None
    service._save_store()

    reloaded = CronService(store_path).get_job("legacy-1")
    assert reloaded is not None
    assert reloaded.payload.mode == "monitor"
    assert json.loads(store_path.read_text(encoding="utf-8")) == {
        "version": 1,
        "jobs": [_as_saved(monitor, mode="monitor")],
    }
