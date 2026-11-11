"""Il registro dei job remoti, contro una shell VERA.

Il server SSH di questi test non è un mini-interprete come quello di
``test_ssh_backend_dev.py``: passa il comando a ``/bin/sh``. È deliberato,
perché quasi tutto ciò che può rompersi qui è testo che deve sopravvivere a una
shell — il quoting del comando dentro ``sh -c``, ``tail -c +N``, ``wc -c``,
``kill -0``, il file col codice di uscita. Contro un finto esecutore
proverebbero solo che il finto esecutore fa quello che gli abbiamo detto.

Come in ``test_ssh_backend_dev.py`` l'ambiente si monta con un
``asynccontextmanager`` e non con una fixture async generator: con pytest 9 +
pytest-asyncio 1.4 quella combinazione si pianta invece di fallire.
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from jenny.agent.tools.ssh_backends.base import SshExecResult, SshTarget
from jenny.agent.tools.ssh_backends.dev import DevSshBackend
from jenny.agent.tools.ssh_jobs import (
    STATUS_FINISHED,
    STATUS_RUNNING,
    STATUS_STOPPED,
    SshJob,
    SshJobError,
    SshJobNotFoundError,
    SshJobStore,
    _check_job_id,
    _new_job_id,
    _parse_pid,
    _start_command,
    get_job_store,
    reset_job_store,
)

# ``asyncssh`` non e una dipendenza runtime: su Android il client SSH e jsch
# via bridge nativo, e il pacchetto non entra mai nei requirements del device
# (stesso trattamento di ``cryptography``). Serve solo qui, per alzare un
# server SSH vero in-process. Senza importorskip questi moduli fallivano in
# *collection* su una macchina che non ce l'ha — come il runner della CI.
asyncssh = pytest.importorskip("asyncssh")

TEST_USER = "jenny"
POLL_BYTES = 4096


async def _handle_shell(process: asyncssh.SSHServerProcess) -> None:
    """Esegue il comando in una shell reale, come farebbe un server vero."""
    proc = await asyncio.create_subprocess_shell(
        process.command or "",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.DEVNULL,
    )
    out, err = await proc.communicate()
    process.stdout.write(out.decode(errors="replace"))
    process.stderr.write(err.decode(errors="replace"))
    process.exit(proc.returncode or 0)


@dataclass(slots=True)
class _Env:
    backend: DevSshBackend
    target: SshTarget
    store: SshJobStore
    log_dir: str
    tmp: Path


@asynccontextmanager
async def shell_env(tmp_path: Path):
    """Server SSH su loopback che esegue davvero i comandi, più un registro pulito."""
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    host_key_path = tmp_path / "host_key"
    host_key.write_private_key(str(host_key_path))

    client_key = asyncssh.generate_private_key("ssh-ed25519")
    client_key_path = tmp_path / "id_ed25519"
    client_key.write_private_key(str(client_key_path))
    os.chmod(client_key_path, 0o600)

    authorized = tmp_path / "authorized_keys"
    authorized.write_bytes(client_key.export_public_key("openssh"))

    server = await asyncssh.listen(
        "127.0.0.1",
        0,
        server_host_keys=[str(host_key_path)],
        authorized_client_keys=str(authorized),
        process_factory=_handle_shell,
    )
    port = server.sockets[0].getsockname()[1]

    known_hosts = tmp_path / "known_hosts"
    line = " ".join(host_key.export_public_key("openssh").decode().split()[:2])
    known_hosts.write_text(f"[127.0.0.1]:{port} {line}\n")

    backend = DevSshBackend()
    target = SshTarget(
        host="127.0.0.1",
        port=port,
        username=TEST_USER,
        key_path=client_key_path,
        known_hosts_path=known_hosts,
        connect_timeout_s=5.0,
        keepalive_interval_s=0,
    )
    log_dir = tmp_path / "jobs"
    try:
        yield _Env(
            backend=backend,
            target=target,
            store=SshJobStore(path=tmp_path / "registry.json"),
            log_dir=str(log_dir),
            tmp=tmp_path,
        )
    finally:
        await backend.close_all()
        server.close()
        await server.wait_closed()


async def _start(env: _Env, command: str) -> SshJob:
    return await env.store.start(
        backend=env.backend,
        target=env.target,
        alias="lab",
        command=command,
        log_dir=env.log_dir,
        timeout_s=10,
    )


async def _poll(env: _Env, job_id: str):
    return await env.store.poll(
        job_id,
        backend=env.backend,
        target=env.target,
        max_bytes=POLL_BYTES,
        timeout_s=10,
    )


async def _drain(env: _Env, job_id: str, *, until, deadline: float = 10.0) -> str:
    """Poll ripetuti finché ``until(accumulato, job)``. Ritorna solo i delta letti."""
    accumulated = ""
    started = time.monotonic()
    while time.monotonic() - started < deadline:
        poll = await _poll(env, job_id)
        accumulated += poll.output
        if until(accumulated, poll.job):
            return accumulated
        await asyncio.sleep(0.05)
    raise AssertionError(f"timed out; read so far: {accumulated!r}")


# -- avvio -------------------------------------------------------------------


async def test_start_registers_a_job_with_a_real_remote_pid(tmp_path):
    async with shell_env(tmp_path) as env:
        job = await _start(env, "sleep 5")

        assert job.pid > 0
        assert job.status == STATUS_RUNNING
        assert env.store.get(job.job_id) is job
        # Il log lo crea il redirect, quindi esiste subito anche se vuoto.
        assert Path(job.log_path).exists()
        assert Path(job.log_path).parent == Path(env.log_dir)

        await env.store.stop(
            job.job_id, backend=env.backend, target=env.target, timeout_s=10
        )


async def test_start_refuses_an_empty_command(tmp_path):
    async with shell_env(tmp_path) as env:
        with pytest.raises(SshJobError):
            await _start(env, "   ")


# -- poll --------------------------------------------------------------------


async def test_poll_returns_only_the_new_output_then_the_exit_code(tmp_path):
    """Il cuore della cosa: ogni poll consegna il delta, mai da capo."""
    async with shell_env(tmp_path) as env:
        gate = tmp_path / "gate"
        job = await _start(
            env,
            f"printf 'alpha\\n'; while [ ! -f {gate} ]; do sleep 0.05; done; "
            "printf 'beta\\n'; exit 7",
        )

        first = await _drain(env, job.job_id, until=lambda text, _j: "alpha" in text)
        assert "beta" not in first
        # Consegnato vuol dire contato: il delta si ferma alla dimensione letta
        # prima di lui (v. ``_poll_command``).
        assert env.store.get(job.job_id).cursor == len(b"alpha\n")

        gate.write_text("go")
        second = await _drain(
            env,
            job.job_id,
            until=lambda text, j: "beta" in text and j.status == STATUS_FINISHED,
        )
        # La riga già consegnata NON ricompare: è il cursore a garantirlo.
        assert "alpha" not in second

        finished = env.store.get(job.job_id)
        assert finished is not None
        assert finished.status == STATUS_FINISHED
        assert finished.exit_code == 7


async def test_poll_reports_pending_bytes_when_the_delta_is_capped(tmp_path):
    """Il cap è in byte lato server: il modello deve sapere che manca del resto."""
    async with shell_env(tmp_path) as env:
        job = await _start(env, "printf 'x%.0s' $(seq 1 5000)")
        await _drain(env, job.job_id, until=lambda _t, j: j.status == STATUS_FINISHED)

        env.store.get(job.job_id).cursor = 0  # rileggi dall'inizio
        poll = await _poll(env, job.job_id)

        assert len(poll.output) == POLL_BYTES
        assert poll.log_size == 5000
        assert poll.pending_bytes == 5000 - POLL_BYTES


async def test_the_job_survives_the_connection_dropping(tmp_path):
    """La ragione per cui il tool esiste: il processo non è figlio della sessione."""
    async with shell_env(tmp_path) as env:
        gate = tmp_path / "gate"
        job = await _start(
            env,
            f"printf 'before\\n'; while [ ! -f {gate} ]; do sleep 0.05; done; "
            "printf 'after\\n'",
        )
        await _drain(env, job.job_id, until=lambda text, _j: "before" in text)
        # Consegnato vuol dire contato (v. ``_poll_command``). Fino all'11/11/2026
        # un poll poteva dare «before» un giro prima di contarlo: la connessione
        # cadeva col cursore a zero, «before» tornava per quella ragione e non
        # per la connessione, e la prova era rossa a caso sotto ``pytest -n``.
        counted = env.store.get(job.job_id).cursor
        assert counted == len(b"before\n")

        await env.backend.close_all()  # come un passaggio wifi -> dati
        assert env.store.get(job.job_id).cursor == counted

        gate.write_text("go")
        rest = await _drain(
            env, job.job_id, until=lambda text, j: "after" in text and j.status == STATUS_FINISHED
        )
        assert "before" not in rest  # il cursore non si è azzerato con la connessione


# -- il delta è esattamente quello contato ----------------------------------


async def test_a_job_that_keeps_writing_is_delivered_once_byte_for_byte(tmp_path):
    """Un log che cresce mentre lo si legge: la somma dei delta è il file.

    Il poll legge la dimensione e poi il delta. Un job che scrive in mezzo
    faceva consegnare quei byte in questo poll senza contarli, e il poll dopo li
    ridava: a ogni confine fra due poll un pezzo doppio, che il modello legge
    come output vero.
    """
    async with shell_env(tmp_path) as env:
        stop = tmp_path / "stop"
        job = await _start(
            env,
            f"i=0; while [ ! -f {stop} ]; do printf 'line %06d\\n' $i; i=$((i+1)); "
            "sleep 0.01; done",
        )
        delivered = ""
        with_output = 0
        while with_output < 12:
            poll = await _poll(env, job.job_id)
            delivered += poll.output
            with_output += bool(poll.output)

        stop.write_text("stop")
        delivered += await _drain(
            env, job.job_id, until=lambda _t, j: j.status == STATUS_FINISHED
        )
        written = Path(job.log_path).read_text()
        assert written.count("\n") > 12
        assert delivered == written


@dataclass(slots=True)
class _LocalShell:
    """Un backend che esegue il comando in una shell di questa macchina.

    Come ``sshd``, che passa il comando alla shell di login dell'utente con
    ``-c``: qui la shell la sceglie il test, perché sul server di un utente può
    essere ``dash``, ``bash``, ``zsh``, ``ksh`` o quella di busybox. ``env``
    serve a mettere davanti nel ``PATH`` un comando finto.
    """

    argv: list[str]
    env: dict[str, str] | None = None

    async def exec(self, target, command, *, timeout_s, max_output_chars):
        proc = await asyncio.create_subprocess_exec(
            *self.argv,
            "-c",
            command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self.env,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout_s)
        return SshExecResult(
            exit_code=proc.returncode or 0,
            stdout=out.decode(errors="replace"),
            stderr=err.decode(errors="replace"),
        )


def _shells() -> list:
    """Le shell che un server può avere, ognuna saltata se qui non c'è."""
    params = []
    for name, argv in (
        ("sh", ["sh"]),
        ("dash", ["dash"]),
        ("bash", ["bash"]),
        ("zsh", ["zsh"]),
        ("ksh", ["ksh"]),
        ("busybox", ["busybox", "sh"]),
    ):
        found = shutil.which(argv[0])
        params.append(
            pytest.param(
                [found, *argv[1:]] if found else [],
                id=name,
                marks=pytest.mark.skipif(not found, reason=f"{argv[0]} is not installed"),
            )
        )
    return params


_LOCAL_TARGET = SshTarget(
    host="127.0.0.1",
    port=22,
    username=TEST_USER,
    key_path=Path("/nonexistent/id"),
    known_hosts_path=Path("/nonexistent/known_hosts"),
)


def _finished_job(
    tmp_path: Path, content: bytes | None, *, cursor: int = 0
) -> tuple[SshJobStore, SshJob]:
    """Un job già finito (c'è il codice di uscita) con *content* nel log, o senza log."""
    log = tmp_path / "job.log"
    rc = tmp_path / "job.rc"
    if content is not None:
        log.write_bytes(content)
    rc.write_text("0")
    store = SshJobStore(path=tmp_path / "registry.json")
    job = SshJob(
        job_id="lab-0000cafe",
        alias="lab",
        command="true",
        pid=999_999,
        log_path=str(log),
        rc_path=str(rc),
        started_at=datetime.now().isoformat(),
        cursor=cursor,
    )
    store._load()[job.job_id] = job
    return store, job


async def _local_poll(store: SshJobStore, job: SshJob, shell: _LocalShell, max_bytes: int):
    return await store.poll(
        job.job_id, backend=shell, target=_LOCAL_TARGET, max_bytes=max_bytes, timeout_s=10
    )


def _shell_with_wc_then(tmp_path: Path, argv: list[str], then: str) -> tuple[_LocalShell, Path]:
    """Una shell il cui ``wc`` misura e poi, una volta sola, esegue *then*.

    È la finestra fra la dimensione e quel che il poll fa dopo, aperta a
    comando: niente fortuna, niente tempi. Ritorna la shell e il file che arma
    il colpo, che sparisce quando è partito.
    """
    trigger = tmp_path / "armed"
    trigger.write_text("armed")
    real_wc = shutil.which("wc")
    assert real_wc
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_wc = bin_dir / "wc"
    fake_wc.write_text(
        "#!/bin/sh\n"
        f'{shlex.quote(real_wc)} "$@"\n'
        "rc=$?\n"
        f"if [ -f {shlex.quote(str(trigger))} ]; then\n"
        f"  rm -f {shlex.quote(str(trigger))}\n"
        f"  {then}\n"
        "fi\n"
        "exit $rc\n"
    )
    fake_wc.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    return _LocalShell(argv, env=env), trigger


@pytest.mark.parametrize("argv", _shells())
async def test_a_write_between_the_size_and_the_delta_is_delivered_once(tmp_path, argv):
    """Il caso sopra con la scrittura esattamente fra ``wc`` e ``tail``."""
    store, job = _finished_job(tmp_path, b"first\n")
    shell, trigger = _shell_with_wc_then(
        tmp_path, argv, f"printf 'late\\n' >> {shlex.quote(job.log_path)}"
    )

    first = await _local_poll(store, job, shell, POLL_BYTES)
    assert not trigger.exists(), "il wc finto non è stato chiamato"
    assert first.output == "first\n"
    assert first.job.cursor == len(b"first\n")

    second = await _local_poll(store, job, shell, POLL_BYTES)
    assert second.output == "late\n"
    assert first.output + second.output == Path(job.log_path).read_text()


@pytest.mark.parametrize("argv", _shells())
async def test_the_poll_that_says_finished_has_delivered_everything(tmp_path, argv):
    """Un job che scrive l'ultima riga ed esce mentre lo si interroga.

    Il modello smette di chiedere quando legge «finito» senza byte in attesa:
    se quel poll avesse misurato il log prima che il job finisse, l'ultima riga
    resterebbe oltre la fine annunciata, e nessuno la leggerebbe mai.
    """
    store, job = _finished_job(tmp_path, b"first\n")
    Path(job.rc_path).unlink()
    job.pid = os.getpid()  # vivo, finché il colpo non scrive il codice di uscita
    shell, trigger = _shell_with_wc_then(
        tmp_path,
        argv,
        f"printf 'last\\n' >> {shlex.quote(job.log_path)}; "
        f"printf 0 > {shlex.quote(job.rc_path)}",
    )

    delivered = ""
    for _ in range(5):
        poll = await _local_poll(store, job, shell, POLL_BYTES)
        delivered += poll.output
        if poll.job.status == STATUS_FINISHED and not poll.pending_bytes:
            break
    assert not trigger.exists(), "il wc finto non è stato chiamato"
    assert poll.job.status == STATUS_FINISHED
    assert delivered == "first\nlast\n"


@pytest.mark.parametrize("argv", _shells())
@pytest.mark.parametrize(
    ("content", "cursor", "max_bytes", "output", "new_cursor"),
    [
        pytest.param(b"0123456789", 0, POLL_BYTES, "0123456789", 10, id="all"),
        pytest.param(b"0123456789", 4, POLL_BYTES, "456789", 10, id="from-the-cursor"),
        pytest.param(b"0123456789", 2, 3, "234", 5, id="capped"),
        pytest.param(b"0123456789", 10, POLL_BYTES, "", 10, id="nothing-new"),
        # Log troncato o ruotato: il delta chiesto è vuoto e si riparte da zero.
        pytest.param(b"0123", 10, POLL_BYTES, "", 0, id="truncated"),
        pytest.param(None, 0, POLL_BYTES, "", 0, id="no-log-yet"),
    ],
)
async def test_the_poll_command_reads_exactly_what_it_counts_in_every_shell(
    tmp_path, argv, content, cursor, max_bytes, output, new_cursor
):
    store, job = _finished_job(tmp_path, content, cursor=cursor)
    poll = await _local_poll(store, job, _LocalShell(argv), max_bytes)
    assert poll.output == output
    assert poll.job.cursor == new_cursor
    assert poll.job.status == STATUS_FINISHED


async def test_unknown_job_id_lists_the_known_ones(tmp_path):
    async with shell_env(tmp_path) as env:
        job = await _start(env, "true")
        with pytest.raises(SshJobNotFoundError) as excinfo:
            await _poll(env, "made-up-id")
        assert job.job_id in str(excinfo.value)


async def test_poll_refuses_a_job_belonging_to_another_host(tmp_path):
    async with shell_env(tmp_path) as env:
        job = await _start(env, "true")
        with pytest.raises(SshJobNotFoundError):
            await env.store.poll(
                job.job_id,
                backend=env.backend,
                target=env.target,
                alias="another-host",
                max_bytes=POLL_BYTES,
                timeout_s=10,
            )


# -- stop --------------------------------------------------------------------


async def test_stop_kills_the_remote_process(tmp_path):
    async with shell_env(tmp_path) as env:
        job = await _start(env, "sleep 60")

        stopped = await env.store.stop(
            job.job_id, backend=env.backend, target=env.target, timeout_s=10
        )
        assert stopped.status == STATUS_STOPPED

        # Un poll successivo deve vedere il processo sparito e lasciarlo
        # "stopped": né "lost" (sappiamo perché è morto) né "finished" (il
        # lavoro non è arrivato in fondo), anche se il wrapper fa in tempo a
        # registrare il 143 di SIGTERM.
        await _drain(env, job.job_id, until=lambda _t, j: j.status != STATUS_RUNNING)
        assert env.store.get(job.job_id).status == STATUS_STOPPED


# -- quoting -----------------------------------------------------------------


async def test_a_command_full_of_shell_metacharacters_stays_intact(tmp_path):
    """Il comando arriva dal modello: deve finire nel log, non nel wrapper."""
    async with shell_env(tmp_path) as env:
        victim = tmp_path / "victim.txt"
        payload = f"printf '%s\\n' \"it's > {victim}; rm -rf /\""
        job = await _start(env, payload)

        text = await _drain(
            env, job.job_id, until=lambda _t, j: j.status == STATUS_FINISHED
        )
        assert f"it's > {victim}; rm -rf /" in text
        # Nessuna redirezione è davvero avvenuta.
        assert not victim.exists()
        assert env.store.get(job.job_id).exit_code == 0


def test_start_command_quotes_the_log_paths_not_just_the_command():
    command = _start_command(
        log_dir="/tmp/j obs",
        log_path="/tmp/j obs/a.log",
        rc_path="/tmp/j obs/a.rc",
        command="echo hi",
    )
    assert "'/tmp/j obs/a.log'" in command
    assert "'/tmp/j obs/a.rc'" in command
    # Il pid stampato deve essere quello del processo staccato.
    assert command.rstrip().endswith("echo $!")
    # Regressione: senza la subshell un comando che termina con `exit N` esce
    # dal wrapper prima di salvare il codice, e il job non si conclude mai.
    assert "(\necho hi\n)" in command


def test_job_ids_are_generated_and_unsafe_ones_are_refused():
    """Un id non deve mai poter diventare un pezzo di path remoto."""
    assert _new_job_id("prod") != _new_job_id("prod")
    assert _new_job_id("../../etc").startswith("etc-")
    _check_job_id("lab-0123abcd")
    for bad in ("../escape", "a b", "", "x" * 65):
        with pytest.raises(SshJobError):
            _check_job_id(bad)


def test_parse_pid_rejects_noise():
    assert _parse_pid("\n1234\n") == 1234
    with pytest.raises(SshJobError):
        _parse_pid("mkdir: permission denied\n")


# -- persistenza -------------------------------------------------------------


async def test_jobs_and_cursors_survive_a_new_store(tmp_path):
    """Su Android il gateway viene ucciso di routine: il cursore deve restare."""
    async with shell_env(tmp_path) as env:
        job = await _start(env, "printf 'hello\\n'")
        # Si aspetta anche la fine: è lo stato che il registro riletto deve
        # ridare. «hello» può arrivare quando il job non ha ancora scritto il
        # suo codice di uscita.
        await _drain(
            env,
            job.job_id,
            until=lambda text, j: "hello" in text and j.status == STATUS_FINISHED,
        )
        # Consegnato vuol dire contato (v. ``_poll_command``).
        cursor = env.store.get(job.job_id).cursor
        assert cursor == len(b"hello\n")

        reloaded = SshJobStore(path=env.store.path)
        restored = reloaded.get(job.job_id)
        assert restored is not None
        assert restored.cursor == cursor
        assert restored.command == "printf 'hello\\n'"
        assert restored.status == STATUS_FINISHED


def test_a_corrupt_registry_starts_empty_instead_of_blocking_new_jobs(tmp_path):
    path = tmp_path / "registry.json"
    path.write_text("{ this is not json")
    assert SshJobStore(path=path).jobs() == []


def test_a_registry_that_is_not_utf8_starts_empty_too(tmp_path):
    path = tmp_path / "registry.json"
    path.write_bytes(b'[{"id": "caf\xe9"}]')
    assert SshJobStore(path=path).jobs() == []


def test_the_registry_is_written_as_a_list_of_objects(tmp_path):
    store = SshJobStore(path=tmp_path / "registry.json")
    store._load()["a-1"] = SshJob(
        job_id="a-1",
        alias="lab",
        command="true",
        pid=1,
        log_path="/tmp/a-1.log",
        rc_path="/tmp/a-1.rc",
        # Timestamp RELATIVO: una data fissa finisce fuori dalla finestra di
        # ritenzione col passare del tempo e il test marcisce in silenzio.
        started_at=datetime.now().isoformat(),
    )
    store._save()
    written = json.loads((tmp_path / "registry.json").read_text())
    assert [j["job_id"] for j in written] == ["a-1"]


def test_pruning_never_drops_a_running_job(tmp_path):
    from jenny.agent.tools import ssh_jobs

    store = SshJobStore(path=tmp_path / "registry.json")
    jobs = store._load()
    for i in range(ssh_jobs._MAX_JOBS + 20):
        jobs[f"j-{i:03d}"] = SshJob(
            job_id=f"j-{i:03d}",
            alias="lab",
            command="true",
            pid=i,
            log_path="/tmp/x.log",
            rc_path="/tmp/x.rc",
            # Recenti e distinti: qui si verifica la potatura per NUMERO,
            # e con date fisse la potatura per eta le toglierebbe tutte.
            started_at=(datetime.now() - timedelta(minutes=i)).isoformat(),
            status=STATUS_RUNNING if i % 10 == 0 else STATUS_FINISHED,
        )
    running = {k for k, j in jobs.items() if j.running}
    store._save()

    kept = {j.job_id for j in store.jobs()}
    assert running <= kept
    assert len(kept) <= ssh_jobs._MAX_JOBS


def test_default_registry_path_lives_in_the_runtime_dir():
    from jenny.config.paths import get_runtime_subdir

    reset_job_store()
    try:
        assert get_job_store().path.parent == get_runtime_subdir("ssh_jobs")
        assert get_job_store() is get_job_store()
    finally:
        reset_job_store()


def test_prune_drops_stale_records_whatever_their_status(tmp_path, monkeypatch):
    """Il tetto per numero da solo non basta.

    Pota solo i terminati, quindi cento job rimasti ``running`` — un server
    sparito e mai piu interrogato — non ne farebbero cadere nessuno. Un job
    "in corso" da un mese non e in corso: e un record non piu verificabile, e
    tenerlo darebbe al modello un elenco di lavori vivi che non esistono.
    """
    from jenny.agent.tools.ssh_jobs import SshJob, SshJobStore

    old = datetime.now() - timedelta(days=45)
    recent = datetime.now() - timedelta(days=1)

    def _job(job_id: str, started: datetime, status: str) -> SshJob:
        return SshJob(
            job_id=job_id,
            alias="lab",
            command="sleep 1",
            pid=1,
            log_path="/tmp/l",
            rc_path="/tmp/rc",
            started_at=started.isoformat(),
            status=status,
        )

    jobs = {
        "stale-running": _job("stale-running", old, "running"),
        "stale-done": _job("stale-done", old, "finished"),
        "fresh-running": _job("fresh-running", recent, "running"),
    }
    SshJobStore._prune(jobs)

    assert set(jobs) == {"fresh-running"}


def test_prune_keeps_records_with_an_unreadable_timestamp(tmp_path):
    """Un record scritto da una versione futura non deve sparire per il formato."""
    from jenny.agent.tools.ssh_jobs import SshJob, SshJobStore

    jobs = {
        "weird": SshJob(
            job_id="weird",
            alias="lab",
            command="sleep 1",
            pid=1,
            log_path="/tmp/l",
            rc_path="/tmp/rc",
            started_at="not-a-timestamp",
        )
    }
    SshJobStore._prune(jobs)
    assert "weird" in jobs
