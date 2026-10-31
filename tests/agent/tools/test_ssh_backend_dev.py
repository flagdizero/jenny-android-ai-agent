"""Il backend dev contro un server SSH VERO, alzato in-process.

Non ci sono mock qui, ed è il punto: il contratto in ``ssh_backends/base.py``
descrive un comportamento (troncamento, timeout, rifiuto per host key
sconosciuta, cap sui trasferimenti) che deve valere identico anche per il
bridge jsch sul device. Misurarlo contro un finto trasporto proverebbe solo che
il finto trasporto fa quello che gli abbiamo detto di fare.

NB sull'infrastruttura: l'ambiente si monta con un ``asynccontextmanager`` e non
con una fixture async. Nessun altro test del repo usa fixture async generator, e
con pytest 9 + pytest-asyncio 1.4 quella combinazione si pianta invece di
fallire — un `async with` esplicito costa due righe e non ha quel problema.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from support.aio import wait_until

from jenny.agent.tools.ssh_backends.base import (
    SshAuthError,
    SshHostKeyError,
    SshTarget,
    SshTimeoutError,
    SshTransportError,
)
from jenny.agent.tools.ssh_backends.dev import DevSshBackend, _known_hosts_entry

# ``asyncssh`` non e una dipendenza runtime: su Android il client SSH e jsch
# via bridge nativo, e il pacchetto non entra mai nei requirements del device
# (stesso trattamento di ``cryptography``). Serve solo qui, per alzare un
# server SSH vero in-process. Senza importorskip questi moduli fallivano in
# *collection* su una macchina che non ce l'ha — come il runner della CI.
asyncssh = pytest.importorskip("asyncssh")

TEST_USER = "jenny"
TEST_PASSWORD = "s3gr3t0-di-prova"


async def _handle_process(process: asyncssh.SSHServerProcess) -> None:
    """Mini interprete: giusto i comportamenti che i test devono osservare."""
    command = process.command or ""
    if command.startswith("echo "):
        process.stdout.write(command[5:] + "\n")
        process.exit(0)
    elif command == "fail":
        process.stderr.write("something broke\n")
        process.exit(3)
    elif command.startswith("big "):
        process.stdout.write("x" * int(command[4:]))
        process.exit(0)
    elif command.startswith("sleep "):
        await asyncio.sleep(float(command[6:]))
        process.exit(0)
    else:
        process.stderr.write(f"unknown command: {command}\n")
        process.exit(127)


@dataclass(slots=True)
class _Env:
    backend: DevSshBackend
    target: SshTarget
    port: int
    key_path: Path
    known_hosts: Path
    host_key_line: str
    tmp: Path


@asynccontextmanager
async def ssh_env(tmp_path: Path, *, sftp_factory: Any = True):
    """Server SSH su loopback con chiave host e chiave client generate al volo.

    *sftp_factory* è quella di ``asyncssh.listen``: ``True`` è il server SFTP
    di serie, una sottoclasse di ``SFTPServer`` ne cambia un comportamento.
    """
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
        process_factory=_handle_process,
        sftp_factory=sftp_factory,
    )
    port = server.sockets[0].getsockname()[1]

    host_key_line = " ".join(host_key.export_public_key("openssh").decode().split()[:2])
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text(f"[127.0.0.1]:{port} {host_key_line}\n")

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
    try:
        yield _Env(
            backend=backend,
            target=target,
            port=port,
            key_path=client_key_path,
            known_hosts=known_hosts,
            host_key_line=host_key_line,
            tmp=tmp_path,
        )
    finally:
        await backend.close_all()
        server.close()
        await server.wait_closed()


class _PasswordServer(asyncssh.SSHServer):
    """Server che accetta SOLO la password, come quelli su cui l'utente entra.

    ``begin_auth`` che ritorna ``True`` significa "l'autenticazione serve":
    ritornare ``False`` aprirebbe a chiunque e i test sulla password sbagliata
    passerebbero senza provare nulla.
    """

    def begin_auth(self, username: str) -> bool:
        return True

    def password_auth_supported(self) -> bool:
        return True

    def validate_password(self, username: str, password: str) -> bool:
        return username == TEST_USER and password == TEST_PASSWORD


@asynccontextmanager
async def password_ssh_env(tmp_path: Path):
    """Come ``ssh_env``, ma il server vuole la password e nessuna chiave esiste.

    Il ``key_path`` del target punta apposta a un file che NON c'è: è la
    situazione reale di un host a password, e serve a verificare che il backend
    non provi comunque ad aprirlo.
    """
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    host_key_path = tmp_path / "pw_host_key"
    host_key.write_private_key(str(host_key_path))

    server = await asyncssh.listen(
        "127.0.0.1",
        0,
        server_host_keys=[str(host_key_path)],
        server_factory=_PasswordServer,
        process_factory=_handle_process,
        sftp_factory=True,
    )
    port = server.sockets[0].getsockname()[1]

    host_key_line = " ".join(host_key.export_public_key("openssh").decode().split()[:2])
    known_hosts = tmp_path / "pw_known_hosts"
    known_hosts.write_text(f"[127.0.0.1]:{port} {host_key_line}\n")

    backend = DevSshBackend()
    target = SshTarget(
        host="127.0.0.1",
        port=port,
        username=TEST_USER,
        key_path=tmp_path / "there_is_no_key_here",
        known_hosts_path=known_hosts,
        password=TEST_PASSWORD,
        connect_timeout_s=5.0,
        keepalive_interval_s=0,
    )
    try:
        yield _Env(
            backend=backend,
            target=target,
            port=port,
            key_path=target.key_path,
            known_hosts=known_hosts,
            host_key_line=host_key_line,
            tmp=tmp_path,
        )
    finally:
        await backend.close_all()
        server.close()
        await server.wait_closed()


# -- esecuzione comandi ------------------------------------------------------

async def test_exec_returns_stdout_and_zero_exit(tmp_path):
    async with ssh_env(tmp_path) as env:
        result = await env.backend.exec(
            env.target, "echo ciao", timeout_s=5, max_output_chars=1000
        )
    assert result.exit_code == 0
    assert result.stdout.strip() == "ciao"
    assert result.truncated_chars == 0


async def test_exec_reports_nonzero_exit_without_raising(tmp_path):
    """Un comando fallito è un RISULTATO, non un'eccezione.

    L'agente deve poter leggere exit code e stderr e decidere; sollevare
    renderebbe indistinguibile "il comando è andato male" da "la connessione è
    caduta", che richiedono reazioni opposte.
    """
    async with ssh_env(tmp_path) as env:
        result = await env.backend.exec(
            env.target, "fail", timeout_s=5, max_output_chars=1000
        )
    assert result.exit_code == 3
    assert "something broke" in result.stderr


async def test_exec_truncates_and_counts_discarded_chars(tmp_path):
    async with ssh_env(tmp_path) as env:
        result = await env.backend.exec(
            env.target, "big 5000", timeout_s=5, max_output_chars=100
        )
    assert len(result.stdout) == 100
    assert result.truncated_chars == 4900


async def test_exec_times_out(tmp_path):
    async with ssh_env(tmp_path) as env:
        with pytest.raises(SshTimeoutError):
            await env.backend.exec(
                env.target, "sleep 5", timeout_s=0.5, max_output_chars=1000
            )


# -- sicurezza ---------------------------------------------------------------

async def test_unknown_host_key_is_refused(tmp_path):
    """Nessun TOFU: un host non presente in known_hosts non si contatta."""
    async with ssh_env(tmp_path) as env:
        empty = tmp_path / "empty_known_hosts"
        empty.write_text("")
        blind = SshTarget(
            host="127.0.0.1",
            port=env.port,
            username=TEST_USER,
            key_path=env.key_path,
            known_hosts_path=empty,
            connect_timeout_s=5.0,
        )
        with pytest.raises(SshHostKeyError):
            await env.backend.exec(blind, "echo ciao", timeout_s=5, max_output_chars=1000)


async def test_password_auth_runs_a_command(tmp_path):
    """Il caso vero: nessuna chiave da nessuna parte, solo la password."""
    async with password_ssh_env(tmp_path) as env:
        result = await env.backend.exec(
            env.target, "echo ciao", timeout_s=5, max_output_chars=1000
        )
    assert result.exit_code == 0
    assert result.stdout.strip() == "ciao"


async def test_password_auth_does_not_open_the_key_file(tmp_path):
    """``client_keys=[]``: publickey non va nemmeno tentato, e il file non esiste."""
    async with password_ssh_env(tmp_path) as env:
        assert not env.key_path.exists()
        await env.backend.exec(env.target, "echo ok", timeout_s=5, max_output_chars=100)


async def test_wrong_password_is_rejected(tmp_path):
    async with password_ssh_env(tmp_path) as env:
        bad = SshTarget(
            host="127.0.0.1",
            port=env.port,
            username=TEST_USER,
            key_path=env.key_path,
            known_hosts_path=env.known_hosts,
            password="non e questa",
            connect_timeout_s=5.0,
        )
        with pytest.raises(SshAuthError) as excinfo:
            await env.backend.exec(bad, "echo ciao", timeout_s=5, max_output_chars=1000)
    # L'errore risale fino al risultato di un tool, cioe nel contesto del
    # modello: la credenziale tentata non deve comparirci.
    assert "non e questa" not in str(excinfo.value)


async def test_unknown_host_key_is_refused_with_password_auth_too(tmp_path):
    """Nessuna scorciatoia al pinning quando c'e una password — anzi, il contrario.

    Senza impronta verificata la password verrebbe consegnata a chiunque
    risponda a quell'indirizzo, e da li e riusabile ovunque l'utente l'abbia
    riciclata. Il rifiuto deve arrivare PRIMA dell'autenticazione.
    """
    async with password_ssh_env(tmp_path) as env:
        empty = tmp_path / "empty_known_hosts_pw"
        empty.write_text("")
        blind = SshTarget(
            host="127.0.0.1",
            port=env.port,
            username=TEST_USER,
            key_path=env.key_path,
            known_hosts_path=empty,
            password=TEST_PASSWORD,
            connect_timeout_s=5.0,
        )
        with pytest.raises(SshHostKeyError):
            await env.backend.exec(blind, "echo ciao", timeout_s=5, max_output_chars=1000)


async def test_password_auth_transfers_files(tmp_path):
    """SFTP passa dalla stessa sessione: se l'auth e a password deve valere anche li."""
    async with password_ssh_env(tmp_path) as env:
        source = tmp_path / "up_pw.txt"
        source.write_text("contenuto")
        remote = str(tmp_path / "remote_pw.txt")

        assert await env.backend.put(env.target, source, remote) == source.stat().st_size
        assert Path(remote).read_text() == "contenuto"


async def test_wrong_client_key_is_rejected(tmp_path):
    async with ssh_env(tmp_path) as env:
        stranger = asyncssh.generate_private_key("ssh-ed25519")
        stranger_path = tmp_path / "stranger"
        stranger.write_private_key(str(stranger_path))
        os.chmod(stranger_path, 0o600)

        bad = SshTarget(
            host="127.0.0.1",
            port=env.port,
            username=TEST_USER,
            key_path=stranger_path,
            known_hosts_path=env.known_hosts,
            connect_timeout_s=5.0,
        )
        with pytest.raises(SshAuthError):
            await env.backend.exec(bad, "echo ciao", timeout_s=5, max_output_chars=1000)


# -- trasferimenti -----------------------------------------------------------

async def test_sftp_put_then_get_roundtrip(tmp_path):
    async with ssh_env(tmp_path) as env:
        source = tmp_path / "up.txt"
        source.write_text("contenuto da caricare")
        remote = str(tmp_path / "remote_copy.txt")

        written = await env.backend.put(env.target, source, remote)
        assert written == source.stat().st_size
        assert Path(remote).read_text() == "contenuto da caricare"

        back = tmp_path / "down.txt"
        read = await env.backend.get(env.target, remote, back, max_bytes=1_000_000)
        assert back.read_text() == "contenuto da caricare"
        assert read == source.stat().st_size


async def test_get_refuses_file_over_cap_without_writing_it(tmp_path):
    """Il cap si applica PRIMA di scrivere: niente file troncato sul telefono."""
    async with ssh_env(tmp_path) as env:
        remote = tmp_path / "huge.bin"
        remote.write_bytes(b"y" * 5000)
        destination = tmp_path / "should_not_exist.bin"

        with pytest.raises(SshTransportError, match="over the"):
            await env.backend.get(
                env.target, str(remote), destination, max_bytes=1000
            )
        assert not destination.exists()


class _UnderstatingSFTPServer(asyncssh.SFTPServer):
    """Un server che dichiara 10 byte per ogni file, qualunque ne sia la misura.

    È il caso che il controllo sulla ``stat`` non vede: un file cresciuto fra
    la ``stat`` e la lettura, o un server che risponde male. Il tetto deve
    valere sui byte che arrivano, non su quelli annunciati.
    """

    def stat(self, path: bytes):  # type: ignore[override]
        attrs = asyncssh.SFTPAttrs.from_local(super().stat(path))
        attrs.size = 10
        return attrs


async def test_get_caps_the_bytes_that_arrive_not_the_declared_size(tmp_path):
    """La ``stat`` dice 10 byte, ne arrivano 5000: la copia si ferma al tetto,
    niente file sul telefono, nemmeno il ``.part``."""
    async with ssh_env(tmp_path, sftp_factory=_UnderstatingSFTPServer) as env:
        remote = tmp_path / "grows.bin"
        remote.write_bytes(b"z" * 5000)
        destination = tmp_path / "downloads" / "grows.bin"
        destination.parent.mkdir()

        with pytest.raises(SshTransportError, match="grew past the 1000 byte limit"):
            await env.backend.get(env.target, str(remote), destination, max_bytes=1000)

        assert list(destination.parent.iterdir()) == []


async def test_get_never_touches_a_file_already_named_like_the_partial(tmp_path):
    """Il temporaneo ha un nome che prima non c'era: un ``<nome>.part`` dell'utente
    (o di un altro download) accanto alla destinazione resta com'era, sia a copia
    riuscita sia a copia interrotta."""
    async with ssh_env(tmp_path, sftp_factory=_UnderstatingSFTPServer) as env:
        downloads = tmp_path / "downloads"
        downloads.mkdir()
        destination = downloads / "report.bin"
        bystander = downloads / "report.bin.part"
        bystander.write_bytes(b"un file che c'era gia'")

        remote = tmp_path / "report.bin"
        remote.write_bytes(b"r" * 300)
        await env.backend.get(env.target, str(remote), destination, max_bytes=1000)
        assert destination.read_bytes() == b"r" * 300
        assert bystander.read_bytes() == b"un file che c'era gia'"

        remote.write_bytes(b"r" * 5000)
        with pytest.raises(SshTransportError, match="grew past"):
            await env.backend.get(env.target, str(remote), destination, max_bytes=1000)
        assert bystander.read_bytes() == b"un file che c'era gia'"
        assert sorted(p.name for p in downloads.iterdir()) == ["report.bin", "report.bin.part"]


async def test_get_returns_the_bytes_written(tmp_path):
    """Il conto che torna è quello dei byte scritti, non la ``stat``."""
    async with ssh_env(tmp_path, sftp_factory=_UnderstatingSFTPServer) as env:
        remote = tmp_path / "small.bin"
        remote.write_bytes(b"q" * 300)
        destination = tmp_path / "small_copy.bin"

        read = await env.backend.get(env.target, str(remote), destination, max_bytes=1000)

        assert read == 300
        assert destination.read_bytes() == b"q" * 300


# -- chiavi e host key -------------------------------------------------------

async def test_generate_key_pair_writes_private_0600_and_returns_public(tmp_path):
    backend = DevSshBackend()
    key_path = tmp_path / "generated" / "id_ed25519"
    public = await backend.generate_key_pair(key_path)

    assert public.startswith("ssh-ed25519 ")
    assert key_path.exists()
    assert oct(key_path.stat().st_mode)[-3:] == "600"
    # La privata non deve comparire in cio che il metodo restituisce.
    assert "PRIVATE KEY" not in public


async def test_probe_host_key_returns_usable_known_hosts_line(tmp_path):
    async with ssh_env(tmp_path) as env:
        line, fingerprint = await env.backend.probe_host_key("127.0.0.1", env.port)

    assert line == f"[127.0.0.1]:{env.port} {env.host_key_line}"
    assert fingerprint.startswith("SHA256:")


def test_known_hosts_entry_brackets_only_nonstandard_ports():
    """Su porta 22 il nome host va nudo, altrimenti la riga non verra mai trovata."""
    assert _known_hosts_entry("example.com", 22, "ssh-ed25519 AAAA") == (
        "example.com ssh-ed25519 AAAA"
    )
    assert _known_hosts_entry("example.com", 2222, "ssh-ed25519 AAAA") == (
        "[example.com]:2222 ssh-ed25519 AAAA"
    )


# -- pool --------------------------------------------------------------------

async def test_connection_is_reused_across_commands(tmp_path):
    async with ssh_env(tmp_path) as env:
        await env.backend.exec(env.target, "echo uno", timeout_s=5, max_output_chars=1000)
        first = env.backend._connections[env.target.pool_key]
        await env.backend.exec(env.target, "echo due", timeout_s=5, max_output_chars=1000)
        assert env.backend._connections[env.target.pool_key] is first


async def test_close_all_drops_every_connection(tmp_path):
    async with ssh_env(tmp_path) as env:
        await env.backend.exec(env.target, "echo ciao", timeout_s=5, max_output_chars=1000)
        assert env.backend._connections

        await env.backend.close_all()
        assert not env.backend._connections


def test_pool_key_changes_with_connection_params(tmp_path):
    """Cambiare porta o utente deve invalidare la sessione, non riusarla."""
    base = SshTarget(
        host="example.com",
        port=22,
        username="jenny",
        key_path=tmp_path / "id",
        known_hosts_path=tmp_path / "known_hosts",
    )
    other_port = SshTarget(
        host=base.host,
        port=2222,
        username=base.username,
        key_path=base.key_path,
        known_hosts_path=base.known_hosts_path,
    )
    other_user = SshTarget(
        host=base.host,
        port=base.port,
        username="altro",
        key_path=base.key_path,
        known_hosts_path=base.known_hosts_path,
    )
    assert base.pool_key != other_port.pool_key
    assert base.pool_key != other_user.pool_key


def test_pool_key_changes_between_key_and_password_auth(tmp_path):
    """Passare da chiave a password deve invalidare la sessione, non riusarla.

    Riusare quella aperta significherebbe continuare a parlare col server con le
    credenziali vecchie: l'utente ha cambiato il modo di autenticarsi proprio
    perche quelle non vanno piu bene.
    """
    with_key = SshTarget(
        host="example.com",
        port=22,
        username="jenny",
        key_path=tmp_path / "id",
        known_hosts_path=tmp_path / "known_hosts",
    )
    with_password = SshTarget(
        host=with_key.host,
        port=with_key.port,
        username=with_key.username,
        key_path=with_key.key_path,
        known_hosts_path=with_key.known_hosts_path,
        password="hunter2",
    )
    assert with_key.pool_key != with_password.pool_key
    assert with_key.auth_mode == "key"
    assert with_password.auth_mode == "password"


def test_password_is_not_in_the_pool_key_nor_in_the_repr(tmp_path):
    """La chiave del pool viene loggata e spedita a Kotlin; il repr finisce ovunque."""
    target = SshTarget(
        host="example.com",
        port=22,
        username="jenny",
        key_path=tmp_path / "id",
        known_hosts_path=tmp_path / "known_hosts",
        password="s3gr3t0",
    )
    assert "s3gr3t0" not in target.pool_key
    assert "s3gr3t0" not in repr(target)
    assert "s3gr3t0" not in str(target)
    # Il resto del target deve restare leggibile, o il repr non diagnostica piu.
    assert "example.com" in repr(target)


# -- chiusura per inattivita -------------------------------------------------


async def test_idle_connection_is_closed_without_further_calls(tmp_path):
    """Il potatore deve girare anche quando NESSUNO chiama piu nulla.

    E' proprio quello il caso che conta: col keepalive attivo una sessione
    inutilizzata resterebbe a pingare il server all'infinito dopo un singolo
    comando, e su un telefono quello e batteria e traffico. Una potatura pigra
    (solo alla chiamata successiva) non lo risolverebbe affatto.
    """
    async with ssh_env(tmp_path) as env:
        target = SshTarget(
            host=env.target.host,
            port=env.target.port,
            username=env.target.username,
            key_path=env.target.key_path,
            known_hosts_path=env.target.known_hosts_path,
            connect_timeout_s=5.0,
            keepalive_interval_s=0,
            idle_close_s=1,
        )
        await env.backend.exec(target, "echo ciao", timeout_s=5, max_output_chars=1000)
        assert target.pool_key in env.backend._connections

        # Il reaper gira a scatti di un secondo: la connessione se ne va al
        # primo o al secondo. Si aspetta quel momento, non 2,5 s fissi.
        await wait_until(lambda: target.pool_key not in env.backend._connections, timeout=5)


async def test_idle_reaping_can_be_switched_off(tmp_path):
    async with ssh_env(tmp_path) as env:
        target = SshTarget(
            host=env.target.host,
            port=env.target.port,
            username=env.target.username,
            key_path=env.target.key_path,
            known_hosts_path=env.target.known_hosts_path,
            connect_timeout_s=5.0,
            keepalive_interval_s=0,
            idle_close_s=0,
        )
        await env.backend.exec(target, "echo ciao", timeout_s=5, max_output_chars=1000)
        assert env.backend._reaper is None
        await asyncio.sleep(1.5)
        assert target.pool_key in env.backend._connections


async def test_close_all_stops_the_reaper_task(tmp_path):
    """Un task lasciato pendente fa gridare asyncio alla chiusura del loop."""
    async with ssh_env(tmp_path) as env:
        target = SshTarget(
            host=env.target.host,
            port=env.target.port,
            username=env.target.username,
            key_path=env.target.key_path,
            known_hosts_path=env.target.known_hosts_path,
            connect_timeout_s=5.0,
            keepalive_interval_s=0,
            idle_close_s=60,
        )
        await env.backend.exec(target, "echo ciao", timeout_s=5, max_output_chars=1000)
        reaper = env.backend._reaper
        assert reaper is not None

        await env.backend.close_all()
        assert reaper.cancelled() or reaper.done()
        assert env.backend._reaper is None
