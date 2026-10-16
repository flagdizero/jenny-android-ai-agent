"""``CloudBackupService``: accendere, spedire, verificare, con una destinazione finta.

La destinazione è una ``MemorySink`` condivisa (come il Drive dell'utente fra
un giro e l'altro); il provider in config è una variabile. Il servizio di
snapshot e lo store sono veri.
"""

from __future__ import annotations

import asyncio
import json
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from jenny.config.schema import SnapshotConfig
from jenny.snapshot.engine import SnapshotEngine
from jenny.snapshot.service import SnapshotService
from jenny_extensions.drive.cloud import CloudRepository, generate_master_key, unseal_key
from jenny_extensions.drive.cloud_drive import DriveConsentError
from jenny_extensions.drive.cloud_service import (
    PROVIDER_DRIVE,
    CloudBackupService,
    CloudUnavailableError,
)
from jenny_extensions.drive.cloud_sink import MemorySink

pytest.importorskip("cryptography")

from jenny.snapshot.crypto_backends.dev import DevAesGcmBackend  # noqa: E402

_BACKEND = DevAesGcmBackend()
# La password dell'utente nei test, e pochi giri di PBKDF2: la lentezza vera
# (600.000) è del telefono, qui costerebbe mezzo secondo a ogni accensione.
PW = "una password lunga"
_ROUNDS = 1000


def _env(tmp_path: Path, *, sink: MemorySink | None = None, available: bool = True):
    runtime = tmp_path / "data"
    ws = runtime / "workspace"
    (ws / "memory").mkdir(parents=True)
    (ws / "USER.md").write_text("l'utente", encoding="utf-8")
    (ws / "memory" / "MEMORY.md").write_text("# memoria", encoding="utf-8")
    snapshots = SnapshotService(SnapshotEngine(ws, runtime / "snapshots"), SnapshotConfig())
    shared = sink if sink is not None else MemorySink()
    provider = {"value": ""}

    async def factory():
        return shared

    async def set_provider(value: str) -> None:
        provider["value"] = value

    service = CloudBackupService(
        snapshots,
        state_dir=runtime / "cloud_backup",
        sink_factory=factory if available else None,
        get_provider=lambda: provider["value"],
        set_provider=set_provider,
        backend=_BACKEND,
        password_iterations=_ROUNDS,
    )
    return SimpleNamespace(
        service=service, sink=shared, provider=provider,
        ws=ws, runtime=runtime, snapshots=snapshots,
    )


async def test_enable_creates_a_private_key_and_the_repository(tmp_path: Path) -> None:
    env = _env(tmp_path)
    assert env.service.status()["has_key"] is False

    result = await env.service.enable(PW)

    assert result["ok"] and result["provider"] == PROVIDER_DRIVE
    assert result["password_set_ms"] > 0
    key = env.service.key_path.read_bytes()
    assert len(key) == 32
    assert stat.S_IMODE(env.service.key_path.stat().st_mode) == 0o600
    assert "repo.json" in env.sink.files
    # Su Drive la chiave c'è solo chiusa con la password, e la password apre lei.
    assert key not in env.sink.files["key.jbk"] and PW.encode() not in env.sink.files["key.jbk"]
    assert await unseal_key(env.sink, PW, backend=_BACKEND) == key
    # La chiave non sta nel workspace: non finisce negli snapshot né nel backup.
    assert not env.service.key_path.is_relative_to(env.ws)


async def test_enable_again_keeps_the_same_key(tmp_path: Path) -> None:
    env = _env(tmp_path)
    await env.service.enable(PW)
    key = env.service.key_path.read_bytes()

    await env.service.disable()
    assert env.provider["value"] == ""
    # Riaccendere una copia che ha già la password: non la si richiede.
    again = await env.service.enable()

    assert again["ok"] and again["password_set_ms"] > 0
    assert env.service.key_path.read_bytes() == key


async def test_a_new_key_is_never_born_without_its_password(tmp_path: Path) -> None:
    env = _env(tmp_path)

    assert await env.service.enable() == {"ok": False, "error": "password_needed"}
    assert await env.service.enable("corta") == {"ok": False, "error": "weak_password"}
    assert not env.service.key_path.exists()
    assert env.sink.files == {} and env.provider["value"] == ""


async def test_enable_does_not_overwrite_someone_elses_backup(tmp_path: Path) -> None:
    # Il telefono nuovo che dice «accendi» invece di «ripristina».
    sink = MemorySink()
    await CloudRepository.open(
        sink, generate_master_key(), tmp_path / "old", create=True, backend=_BACKEND
    )
    before = dict(sink.files)
    env = _env(tmp_path, sink=sink)

    result = await env.service.enable(PW)

    assert result == {"ok": False, "error": "existing_backup"}
    assert await env.service.enable() == {"ok": False, "error": "existing_backup"}
    assert sink.files == before
    assert not env.service.key_path.exists()
    assert env.provider["value"] == ""


async def test_run_sends_a_fresh_snapshot(tmp_path: Path) -> None:
    env = _env(tmp_path)
    assert (await env.service.run())["error"] == "disabled"
    await env.service.enable(PW)

    result = await env.service.run()

    assert result["ok"] and result["uploaded_bytes"] > 0
    assert result["last_success_ms"] > 0 and result["last_error"] == ""
    assert result["running"] is False  # il giro è finito: lo stato lo dice
    head = env.snapshots.engine.list_snapshots(limit=1)[0]
    assert head["trigger"] == "pre_cloud"
    assert any(k.startswith("snapshots/") and head["id"] in k for k in env.sink.files)


async def test_a_run_without_changes_sends_nothing_new(tmp_path: Path) -> None:
    env = _env(tmp_path)
    await env.service.enable(PW)
    await env.service.run()
    puts = len(env.sink.puts)

    result = await env.service.run()

    assert result["ok"] and result["uploaded_bytes"] == 0
    assert len(env.sink.puts) == puts


async def test_a_changed_file_goes_up_on_the_next_run(tmp_path: Path) -> None:
    env = _env(tmp_path)
    await env.service.enable(PW)
    await env.service.run()

    (env.ws / "USER.md").write_text("l'utente, aggiornato", encoding="utf-8")
    result = await env.service.run()

    assert result["ok"] and result["uploaded_bytes"] > 0
    assert len([k for k in env.sink.files if k.startswith("snapshots/")]) == 2


class _RevokedSink(MemorySink):
    async def get_bytes(self, key: str) -> bytes:
        raise DriveConsentError("revoked")


async def test_a_revoked_permission_is_reported_not_raised(tmp_path: Path) -> None:
    env = _env(tmp_path)
    await env.service.enable(PW)
    env.sink.__class__ = _RevokedSink

    result = await env.service.run()

    assert result["ok"] is False and result["error"] == "consent_needed"
    status = env.service.status()
    assert status["last_error"] == "consent_needed" and status["last_failure_ms"] > 0


async def test_a_lost_key_is_reported(tmp_path: Path) -> None:
    env = _env(tmp_path)
    await env.service.enable(PW)
    env.service.key_path.unlink()

    assert (await env.service.run())["error"] == "missing_key"


async def test_verify_rebuilds_the_last_copy_and_cleans_up(tmp_path: Path) -> None:
    env = _env(tmp_path)
    await env.service.enable(PW)
    assert (await env.service.verify())["error"] == "nothing_to_verify"
    await env.service.run()

    result = await env.service.verify()

    assert result["ok"] and result["files"] == 2
    assert env.service.status()["last_verified_ms"] > 0
    assert not (env.runtime / "cloud_backup" / "verify").exists()


async def test_two_runs_at_once_do_not_overlap(tmp_path: Path) -> None:
    env = _env(tmp_path)
    await env.service.enable(PW)

    first, second = await asyncio.gather(env.service.run(), env.service.run())

    assert sorted([first["ok"], second["ok"]]) == [False, True]
    assert "already_running" in (first.get("error"), second.get("error"))


async def test_without_google_there_is_nothing_to_enable(tmp_path: Path) -> None:
    env = _env(tmp_path, available=False)

    assert env.service.status()["available"] is False
    with pytest.raises(CloudUnavailableError):
        await env.service.enable(PW)


async def test_the_status_file_stays_out_of_the_workspace(tmp_path: Path) -> None:
    env = _env(tmp_path)
    await env.service.enable(PW)
    await env.service.run()

    state = env.runtime / "cloud_backup"
    assert json.loads((state / "service.json").read_text())["last_success_ms"] > 0
    assert not any(p.name in {"service.json", "master.key"} for p in env.ws.rglob("*"))


# -- il giro orario --------------------------------------------------------------

_H = 3600 * 1000


def _age(env, **ages_h: float) -> int:
    """Riscrive lo stato come se le date fossero *ages_h* ore fa; torna «adesso»."""
    import time

    now = int(time.time() * 1000)
    path = env.runtime / "cloud_backup" / "service.json"
    status = json.loads(path.read_text())
    for field, hours in ages_h.items():
        status[field] = now - int(hours * _H) if hours is not None else 0
    path.write_text(json.dumps(status))
    return now


async def test_tick_does_nothing_when_off(tmp_path: Path) -> None:
    env = _env(tmp_path)
    assert await env.service.tick(charging=True, unmetered=True) == {"action": "off", "alert": None}
    assert env.sink.puts == []


async def test_tick_waits_until_twenty_hours_have_passed(tmp_path: Path) -> None:
    env = _env(tmp_path)
    await env.service.enable(PW)

    result = await env.service.tick(charging=True, unmetered=True)

    assert result == {"action": "wait", "alert": None}
    assert not any(k.startswith("snapshots/") for k in env.sink.files)


async def test_tick_runs_on_charge_and_wifi_and_verifies(tmp_path: Path) -> None:
    env = _env(tmp_path)
    await env.service.enable(PW)
    now = _age(env, enabled_at_ms=21)

    result = await env.service.tick(charging=True, unmetered=True, now_ms=now)

    assert result["action"] == "ran" and result["alert"] is None
    status = env.service.status()
    assert status["last_success_ms"] > 0
    assert status["last_verified_ms"] > 0  # prima prova di ripristino, subito


async def test_tick_waits_for_the_charger_until_three_days(tmp_path: Path) -> None:
    env = _env(tmp_path)
    await env.service.enable(PW)

    now = _age(env, enabled_at_ms=21)
    assert (await env.service.tick(charging=False, unmetered=True, now_ms=now))["action"] == "wait"
    now = _age(env, enabled_at_ms=73)
    assert (await env.service.tick(charging=False, unmetered=True, now_ms=now))["action"] == "ran"


async def test_tick_never_uses_mobile_data_and_alerts_once(tmp_path: Path) -> None:
    env = _env(tmp_path)
    await env.service.enable(PW)
    now = _age(env, enabled_at_ms=80)

    first = await env.service.tick(charging=True, unmetered=False, now_ms=now)
    second = await env.service.tick(charging=True, unmetered=False, now_ms=now + _H)

    assert first["action"] == "wait"
    assert first["alert"] == {"days": 3, "error": "not_charging_or_no_wifi", "never": True}
    assert second["alert"] is None  # una volta per serie
    assert not any(k.startswith("snapshots/") for k in env.sink.files)


async def test_a_new_streak_alerts_again(tmp_path: Path) -> None:
    env = _env(tmp_path)
    await env.service.enable(PW)
    now = _age(env, enabled_at_ms=80)
    await env.service.run()  # una copia riuscita chiude la serie...
    now = _age(env, last_success_ms=75, alerted_ms=90)

    result = await env.service.tick(charging=False, unmetered=False, now_ms=now)

    assert result["alert"] is not None and result["alert"]["never"] is False


async def test_tick_does_not_verify_again_within_a_week(tmp_path: Path) -> None:
    env = _env(tmp_path)
    await env.service.enable(PW)
    await env.service.run()
    await env.service.verify()
    verified = env.service.status()["last_verified_ms"]
    now = _age(env, last_success_ms=21)

    await env.service.tick(charging=True, unmetered=True, now_ms=now)

    assert env.service.status()["last_verified_ms"] == verified


# -- telefono nuovo ----------------------------------------------------------------


async def _old_phone_backup(tmp_path: Path) -> tuple[MemorySink, bytes, str]:
    """Il telefono di prima: acceso, una copia su Drive. Torna destinazione, chiave, id."""
    old = _env(tmp_path / "old")
    await old.service.enable(PW)
    await old.service.run()
    snapshot_id = old.snapshots.engine.head_id()
    return old.sink, old.service.key_path.read_bytes(), snapshot_id


async def test_new_phone_opens_the_copy_with_the_password_and_restores(tmp_path: Path) -> None:
    from jenny.snapshot.locations import MARKER_FILE_NAME, STAGED_WORKSPACE_DIR_NAME

    sink, key, snapshot_id = await _old_phone_backup(tmp_path)
    new = _env(tmp_path / "new", sink=sink)
    # Senza chiave si sa almeno che una copia c'è: è lì che si chiede la password.
    assert await new.service.remote_snapshots() == {"ok": False, "error": "missing_key"}

    assert await new.service.unlock("non è questa") == {"ok": False, "error": "wrong_password"}
    assert not new.service.key_path.exists()
    assert await new.service.unlock(PW) == {"ok": True}
    assert new.service.key_path.read_bytes() == key
    assert new.service.status()["password_set_ms"] > 0
    remote = await new.service.remote_snapshots()
    assert [s["id"] for s in remote["snapshots"]] == [snapshot_id]

    staged = await new.service.stage_restore(snapshot_id)

    assert staged["ok"] and staged["file_count"] == 2
    tree = new.runtime / STAGED_WORKSPACE_DIR_NAME
    assert (tree / "USER.md").read_text(encoding="utf-8") == "l'utente"
    marker = json.loads((new.runtime / MARKER_FILE_NAME).read_text())
    assert marker["source"] == "cloud" and marker["snapshot_id"] == snapshot_id
    # Lo stato di prima resta raggiungibile dalla storia locale.
    assert new.snapshots.engine.list_snapshots(limit=1)[0]["trigger"] == "pre_restore"


async def test_no_copy_on_drive_is_said_plainly(tmp_path: Path) -> None:
    new = _env(tmp_path / "new")

    assert await new.service.remote_snapshots() == {"ok": False, "error": "no_backup"}
    assert await new.service.unlock(PW) == {"ok": False, "error": "no_backup"}


async def test_a_copy_without_a_password_says_so(tmp_path: Path) -> None:
    # Una copia fatta prima della password (o il cui key.jbk è stato tolto).
    sink, _key, _ = await _old_phone_backup(tmp_path)
    del sink.files["key.jbk"]
    new = _env(tmp_path / "new", sink=sink)

    assert await new.service.unlock(PW) == {"ok": False, "error": "no_password"}
    assert not new.service.key_path.exists()


async def test_changing_the_password_keeps_the_old_copies_readable(tmp_path: Path) -> None:
    old = _env(tmp_path / "old")
    await old.service.enable(PW)
    await old.service.run()
    sink, key = old.sink, old.service.key_path.read_bytes()
    snapshot_id = old.snapshots.engine.head_id()
    packs = {k: v for k, v in sink.files.items() if k != "key.jbk"}

    assert (await old.service.set_password("corta"))["error"] == "weak_password"
    assert (await old.service.set_password("la nuova password"))["ok"]

    # Si è riscritto solo key.jbk: i dati non si toccano.
    assert {k: v for k, v in sink.files.items() if k != "key.jbk"} == packs
    new = _env(tmp_path / "new", sink=sink)
    assert (await new.service.unlock(PW))["error"] == "wrong_password"
    assert await new.service.unlock("la nuova password") == {"ok": True}
    assert new.service.key_path.read_bytes() == key
    assert (await new.service.stage_restore(snapshot_id))["ok"]


async def test_a_password_is_never_sealed_over_someone_elses_copy(tmp_path: Path) -> None:
    # La chiave di qui non apre la copia su Drive (l'ha rifatta un altro
    # telefono): chiuderla con una password renderebbe quella copia illeggibile.
    sink, _key, _ = await _old_phone_backup(tmp_path)
    before = sink.files["key.jbk"]
    other = _env(tmp_path / "other", sink=sink)
    other.service.key_path.parent.mkdir(parents=True)
    other.service.key_path.write_bytes(generate_master_key())

    assert (await other.service.set_password("un'altra password"))["error"] == "wrong_key"
    assert sink.files["key.jbk"] == before


async def test_a_vanished_password_shows_up_on_the_next_run(tmp_path: Path) -> None:
    env = _env(tmp_path)
    await env.service.enable(PW)
    await env.service.run()
    assert env.service.status()["password_set_ms"] > 0

    del env.sink.files["key.jbk"]
    await env.service.run()

    assert env.service.status()["password_set_ms"] == 0
    assert (await env.service.set_password(PW))["ok"]
    assert env.service.status()["password_set_ms"] > 0


async def test_the_password_never_reaches_disk(tmp_path: Path) -> None:
    env = _env(tmp_path)
    await env.service.enable(PW)
    await env.service.set_password("un'altra password lunga")
    await env.service.run()

    for path in env.runtime.rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            assert PW.encode() not in data and b"un'altra password" not in data, path


async def test_restoring_an_unknown_copy_writes_nothing(tmp_path: Path) -> None:
    from jenny.snapshot.locations import MARKER_FILE_NAME

    sink, _key, _ = await _old_phone_backup(tmp_path)
    new = _env(tmp_path / "new", sink=sink)
    await new.service.unlock(PW)

    assert (await new.service.stage_restore("e" * 64))["error"] == "no_such_copy"
    assert not (new.runtime / MARKER_FILE_NAME).exists()


# -- quel che la pagina disegna ------------------------------------------------------


async def test_status_carries_what_the_card_draws(tmp_path: Path) -> None:
    env = _env(tmp_path)
    await env.service.enable(PW)
    await env.service.run()
    await env.service.run()

    status = env.service.status()

    assert len(status["recent_copies_ms"]) == 2
    assert status["stored_bytes"] > 0
    assert status["file_count"] == 2
    assert status["progress"] is None  # fuori dai giri non c'è
    assert "alerted_ms" in status


async def test_old_copies_leave_the_history(tmp_path: Path) -> None:
    env = _env(tmp_path)
    await env.service.enable(PW)
    now = _age(env, enabled_at_ms=1)
    path = env.runtime / "cloud_backup" / "service.json"
    status = json.loads(path.read_text())
    status["recent_copies_ms"] = [now - 20 * 24 * _H, now - 2 * 24 * _H] + [now - 1000] * 45
    path.write_text(json.dumps(status))

    await env.service.run()

    copies = env.service.status()["recent_copies_ms"]
    assert len(copies) == 40
    assert all(now - t < 14 * 24 * _H for t in copies)


async def test_progress_is_visible_while_running(tmp_path: Path) -> None:
    env = _env(tmp_path)
    await env.service.enable(PW)
    seen: list[dict] = []
    original = env.service._note_progress

    def spy(done: int, total: int) -> None:
        original(done, total)
        seen.append(env.service.status()["progress"])

    env.service._note_progress = spy
    await env.service.run()

    assert seen and seen[0]["done_bytes"] == 0 and seen[0]["total_bytes"] > 0
    assert seen[-1]["done_bytes"] == seen[-1]["total_bytes"]
    assert env.service.status()["progress"] is None


async def test_a_damaged_copy_is_found_said_and_redone(tmp_path: Path) -> None:
    """Il caso per cui il controllo esiste: un pacco su Drive si rovina. Il
    controllo lo trova (non lo scambia per «rete giù»), lo stato lo dice, e la
    copia dopo ricarica tutto — anche se nel workspace non è cambiato niente,
    che è proprio il caso in cui una copia normale non manderebbe nulla."""
    from jenny_extensions.drive.cloud import PACKS_DIR

    env = _env(tmp_path)
    await env.service.enable(PW)
    await env.service.run()
    for key in [k for k in env.sink.files if k.startswith(f"{PACKS_DIR}/")]:
        data = bytearray(env.sink.files[key])
        data[len(data) // 2] ^= 0xFF
        env.sink.files[key] = bytes(data)

    checked = await env.service.verify()

    assert checked == {"ok": False, "error": "mismatch"}
    status = env.service.status()
    assert status["last_error"] == "verify_mismatch"
    assert status["last_failure_ms"] >= status["last_success_ms"]

    packs_before = {k for k in env.sink.files if k.startswith(f"{PACKS_DIR}/")}
    redone = await env.service.run()
    packs_after = {k for k in env.sink.files if k.startswith(f"{PACKS_DIR}/")}
    assert redone["ok"] and packs_after - packs_before, "non ha ricaricato niente"
    assert env.service.status()["last_error"] == ""
    assert (await env.service.verify())["ok"], "la copia rifatta non si apre"
    # E un telefono nuovo, che legge tutti gli indici, sceglie i pacchi nuovi.
    other = await CloudRepository.open(
        env.sink, env.service.key_path.read_bytes(), tmp_path / "other", backend=_BACKEND
    )
    snap = (await other.list_snapshots())[0]
    await other.restore(snap, tmp_path / "back")
    assert (tmp_path / "back" / "USER.md").read_text(encoding="utf-8") == "l'utente"


async def test_a_check_that_could_not_finish_is_not_a_damaged_copy(tmp_path: Path) -> None:
    env = _env(tmp_path)
    await env.service.enable(PW)
    await env.service.run()

    class _Down(MemorySink):
        async def get_file(self, key, dest):
            raise OSError("network is unreachable")

    env.sink.__class__ = _Down
    checked = await env.service.verify()

    assert checked["ok"] is False and checked["error"].startswith("OSError")
    assert env.service.status()["last_error"] == ""
