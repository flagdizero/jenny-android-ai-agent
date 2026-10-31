"""I quattro tool SSH: elenco host, comando breve, job lungo, trasferimento file.

Questo modulo è deliberatamente sottile. Chi decide *se* si può parlare con un
host è ``ssh_transport`` (alias, policy di rete, pinning della host key,
presenza della chiave); chi trasporta è il backend; chi tiene i cursori dei job
lunghi è ``ssh_jobs``. Qui restano solo tre responsabilità: leggere la config
fresca, formattare per il modello, e **tradurre le eccezioni** in frasi che gli
dicano cosa fare invece di farlo indovinare.

Tutti e quattro dichiarano ``_scopes = {"remote"}``, che non è nessuno degli
scope esistenti. È voluto: il tipo di subagent ``operator`` è definito come
"tutti i tool dello scope subagent", quindi mettere SSH lì gli darebbe in un
colpo solo web, esecuzione di codice e una shell su una macchina terza. Uno
scope a parte costringe a nominarlo esplicitamente per concederlo.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.agent.tools.base import Tool, tool_parameters
from jenny.agent.tools.schema import IntegerSchema, StringSchema, tool_parameters_schema
from jenny.agent.tools.ssh_backends.base import (
    SshAuthError,
    SshError,
    SshExecResult,
    SshHostKeyError,
    SshTimeoutError,
    SshTransportError,
    SshUnavailableError,
)
from jenny.agent.tools.ssh_jobs import (
    STATUS_LOST,
    STATUS_RUNNING,
    SshJob,
    SshJobError,
    SshJobPoll,
    get_job_store,
)
from jenny.agent.tools.ssh_transport import (
    SshDisabledError,
    SshHostBlockedError,
    SshHostUnknownError,
    SshKeyMissingError,
    configured_hosts,
    get_ssh_backend,
    resolve_target,
)
from jenny.runtime.power import keep_awake
from jenny.security.subagent_policy import journal_touch
from jenny.security.workspace_access import (
    READONLY_TOOL_REFUSAL,
    current_turn_is_readonly,
    current_workspace_scope,
)
from jenny.security.workspace_policy import (
    WorkspaceBoundaryError,
    _safe_expanduser,
    resolve_allowed_path,
)

_HOST_PARAM = "Alias of a registered host. Call `ssh_hosts` if you do not know the aliases."

# Margine fra la scadenza del wakelock e il timeout del comando SSH. Il lock
# deve sopravvivere al comando che protegge, altrimenti la CPU si sospende
# proprio mentre si aspetta la risposta e il timeout scatta per un motivo che
# non ha niente a che vedere col server. Il tetto di ``command_timeout_s`` è 300s
# da schema, quindi il valore risultante resta abbondantemente sotto il
# massimo accettato dal bridge.
_SSH_WAKELOCK_MARGIN_S = 60.0

# Comando mostrato nell'elenco job: abbastanza per riconoscerlo, non tanto da
# riempire il contesto quando i job sono dieci.
_COMMAND_PREVIEW_CHARS = 120


def _ssh_config(config: Any) -> Any:
    return config.tools.ssh


def _describe(exc: BaseException) -> str:
    """Traduce un errore del trasporto in un'istruzione per il modello.

    Le eccezioni non risalgono mai al runner: un traceback nel risultato di un
    tool diventa un esempio che il modello imita, e per metà di questi errori la
    reazione giusta non è "riprova" ma "chiedi all'utente".
    """
    if isinstance(exc, SshHostKeyError):
        return (
            f"Error: {exc} The host key has to be checked and accepted by a person in "
            "Settings → Workshop → Hands → SSH — it cannot be bypassed from here, and you should not try. "
            "Tell the user what to open."
        )
    if isinstance(exc, SshHostUnknownError):
        return f"Error: {exc}. Use one of those aliases, or ask the user to add the host in Settings → Workshop → Hands → SSH."
    if isinstance(exc, SshKeyMissingError):
        return f"Error: {exc}."
    if isinstance(exc, SshHostBlockedError):
        return (
            f"Error: {exc}. The address is refused by the network policy (loopback, "
            "link-local and metadata ranges are never reachable). This is not retryable."
        )
    if isinstance(exc, SshAuthError):
        return (
            f"Error: the server refused the key ({exc}). Either the username in Settings is "
            "wrong, or Jenny's public key is not in that user's ~/.ssh/authorized_keys. "
            "Settings → Workshop → Hands → SSH shows the public key to install."
        )
    if isinstance(exc, SshTimeoutError):
        return (
            f"Error: {exc}. If the command is genuinely slow, do not retry it with "
            "`ssh_exec` — start it with `ssh_job` (action=start) and follow it with "
            "action=poll. If it is waiting on an interactive prompt, re-run it with a "
            "non-interactive flag."
        )
    if isinstance(exc, SshJobError):
        # Sottoclasse di SshError, quindi arriverebbe comunque in fondo: il ramo
        # esiste perché "SSH failed" davanti a "unknown ssh job" manderebbe il
        # modello a diagnosticare la rete invece di rileggere l'elenco.
        return f"Error: {exc}."
    if isinstance(exc, SshDisabledError):
        # Non è un guasto e non va diagnosticato: è una decisione dell'utente.
        return f"Error: {exc}"
    if isinstance(exc, SshUnavailableError):
        return f"Error: no SSH transport is available in this runtime ({exc})."
    if isinstance(exc, SshTransportError):
        return f"Error: the SSH connection failed ({exc}). The host may be down or off this network."
    return f"Error: SSH failed ({exc})."


def _render_exec(result: SshExecResult) -> str:
    parts = [f"exit code: {result.exit_code}"]
    stdout, stderr = result.stdout.rstrip(), result.stderr.rstrip()
    if stdout:
        parts.append(f"stdout:\n{stdout}")
    if stderr:
        parts.append(f"stderr:\n{stderr}")
    if not stdout and not stderr:
        parts.append("(no output)")
    if result.truncated_chars:
        # Il numero di caratteri *scartati*, non la lunghezza finale: il modello
        # deve poter decidere se vale la pena rilanciare con grep/tail.
        parts.append(
            f"[{result.truncated_chars} characters were dropped. Re-run narrowing the "
            "output (grep, tail, head) rather than guessing what is missing.]"
        )
    return "\n".join(parts)


class _SshToolMixin:
    """Gate di attivazione, risoluzione dell'alias e config fresca, condivisi.

    Sta davanti a ``Tool`` nell'MRO di ogni tool: ``Tool`` definisce già
    ``enabled``/``create``, e con l'ordine opposto vincerebbero le sue.
    """

    _scopes = {"remote"}

    def __init__(self, validate: Callable[[str], tuple[bool, str]] | None = None) -> None:
        # Iniettabile per gli stessi motivi documentati in
        # ``ssh_transport.resolve_target``: la policy di rete blocca il loopback,
        # e i test parlano con un server SSH vero su 127.0.0.1.
        self._validate = validate

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        # Due gate, entrambi necessari: il toggle e almeno un host dichiarato da
        # un umano. Volutamente NON si guarda ``ctx.android_context``: il
        # backend dev esiste apposta perché questi tool siano testabili altrove.
        # ``ctx.config`` è già una ``ToolsConfig``, NON la ``Config`` radice:
        # si scrive ``ctx.config.ssh`` come fanno ``ctx.config.location`` e
        # ``ctx.config.android_web``. Passare da ``.tools`` dava sempre ``None``,
        # quindi i tool non si caricavano mai nel runtime vero — e il test non
        # lo vedeva perché costruiva il ctx con una ``Config`` intera, cioè con
        # una forma che in produzione non esiste.
        ssh = getattr(getattr(ctx, "config", None), "ssh", None)
        return bool(ssh is not None and ssh.enable and ssh.hosts)

    @classmethod
    def disabled_reason(cls, ctx: Any) -> str | None:
        """Quale dei due gate e giu — il toggle o l'elenco host.

        I due casi si rimediano in due punti diversi della stessa schermata, e
        confonderli costa all'utente un giro a vuoto. Il subagent ``sysadmin``
        rifiutato mostra questa frase al posto di "il tool non era disponibile".
        """
        ssh = getattr(getattr(ctx, "config", None), "ssh", None)
        if ssh is None or not ssh.enable:
            return "SSH access is off (Settings → Workshop → Hands → SSH)"
        if not ssh.hosts:
            return "no SSH host is registered (Settings → Workshop → Hands → SSH → Add host)"
        return None

    @classmethod
    def create(cls, ctx: Any) -> Any:
        return cls()

    async def _resolve_off_loop(self, alias: str) -> tuple[Any, Any, Any]:
        """:meth:`_resolve` in un thread: rilegge la config e risolve il nome
        dell'host (``validate_ssh_target`` → ``getaddrinfo``), entrambi
        bloccanti. Sul loop un DNS lento fermava tutto il gateway. """
        return await asyncio.to_thread(self._resolve, alias)

    def _resolve(self, alias: str) -> tuple[Any, Any, Any]:
        """``alias`` → ``(config ssh, config host, target)``, da config **fresca**.

        Rileggere la config a ogni chiamata non è pigrizia: i tool si
        costruiscono una volta sola allo startup e le impostazioni non li
        ricostruiscono, quindi un host aggiunto dalla UI sarebbe altrimenti
        invisibile fino al riavvio del gateway.
        """
        from jenny.config.loader import load_config

        config = load_config()
        ssh_cfg = _ssh_config(config)
        # Ricontrollo del toggle a ogni chiamata, in AGGIUNTA a ``enabled()``.
        # L'asimmetria è voluta: ``enabled()`` decide allo startup se questi tool
        # esistono, e riaccendere SSH richiede quindi un riavvio — ma SPEGNERLO
        # deve avere effetto subito, perché quello è l'interruttore di emergenza.
        # Un utente che toglie la spunta mentre un subagent sta lavorando su un
        # server si aspetta che smetta, non che finisca il turno.
        if ssh_cfg is None or not ssh_cfg.enable:
            raise SshDisabledError(
                "SSH is switched off in Settings → Workshop → Hands → SSH. Turning it back on is the "
                "user's call, and it is not something you can do from here."
            )
        host_cfg, target = resolve_target(alias, config=config, validate=self._validate)
        return ssh_cfg, host_cfg, target


# -- ssh_hosts ---------------------------------------------------------------


@tool_parameters(tool_parameters_schema())
class SshHostsTool(_SshToolMixin, Tool):
    """Elenca gli host SSH registrati, leggendoli dalla config corrente."""

    _scopes = {"remote"}

    name = "ssh_hosts"
    description = (
        "List the remote machines the user has registered for SSH: alias, host, username "
        "and what each one is for. Every other SSH tool takes one of these aliases as its "
        "`host` argument — you cannot reach an address that is not in this list. "
        "Call this when you do not know the aliases, or when one was rejected as unknown: "
        "the list is read live, so a host the user just added in Settings shows up here "
        "without restarting anything."
    )

    async def execute(self, **kwargs: Any) -> str:
        try:
            hosts = configured_hosts()
        except Exception as exc:  # config illeggibile: non è un motivo per esplodere
            logger.error("ssh_hosts: could not read the configuration: {}", exc)
            return f"Error: could not read the SSH configuration ({exc})."
        if not hosts:
            # ``configured_hosts`` ritorna vuoto anche a SSH spento: distinguere i
            # due casi evita di far dire al modello "aggiungi un host" a chi gli
            # host ce li ha gia e ha solo tolto la spunta.
            from jenny.config.loader import load_config

            ssh_cfg = _ssh_config(load_config())
            if ssh_cfg is not None and not ssh_cfg.enable:
                return (
                    "SSH is switched off in Settings → Workshop → Hands → SSH. Turning it back on is the "
                    "user's call, and it also needs a gateway restart to take effect."
                )
            return (
                "No SSH hosts are configured. The user has to add one in Settings → Workshop → Hands → SSH "
                "(host, username, then accept the host key fingerprint)."
            )
        lines = [f"{len(hosts)} SSH host(s) registered:"]
        for host in hosts:
            entry = f"- {host.alias}: {host.username}@{host.host}"
            if host.port != 22:
                entry += f":{host.port}"
            if host.description:
                entry += f" — {host.description}"
            lines.append(entry)
        return "\n".join(lines)


# -- ssh_exec ----------------------------------------------------------------


@tool_parameters(
    tool_parameters_schema(
        host=StringSchema(_HOST_PARAM),
        command=StringSchema(
            "Shell command to run. It must finish quickly and on its own: no interactive "
            "prompts, no pagers, no `sudo` asking for a password (there is no TTY and no "
            "stdin). Pass non-interactive flags such as -y."
        ),
        timeout_s=IntegerSchema(
            description="Optional timeout in seconds. Capped by the configured limit.",
            minimum=1,
            maximum=300,
            nullable=True,
        ),
        required=["host", "command"],
    )
)
class SshExecTool(_SshToolMixin, Tool):
    """Comando breve e sincrono su un host registrato."""

    _scopes = {"remote"}

    name = "ssh_exec"
    description = (
        "Run ONE SHORT command on a registered remote machine over SSH and wait for its "
        "result (exit code, stdout, stderr). Use it to inspect and to make quick changes: "
        "check a service, read a config file, restart a container. "
        "Do NOT use it for anything slow — package upgrades, builds, backups, big copies. "
        "Jenny runs on a phone whose CPU can suspend with the screen off and whose network "
        "switches between wifi and mobile data, so a long command dies half-way with no way "
        "to tell how far it got. Start those with `ssh_job` instead. "
        "There is no TTY and no stdin: an interactive command will time out, not prompt."
    )

    async def execute(
        self, host: str, command: str, timeout_s: int | None = None, **kwargs: Any
    ) -> str:
        command = (command or "").strip()
        if not command:
            return "Error: command is empty."
        try:
            ssh_cfg, _host_cfg, target = await self._resolve_off_loop(host)
        except SshError as exc:
            return _describe(exc)

        # Il timeout scelto dal modello può solo ABBASSARE quello di config: il
        # tetto è la ragione per cui esiste ssh_job, e un tool non deve poterlo
        # alzare per comodità.
        limit = float(ssh_cfg.command_timeout_s)
        if timeout_s is not None:
            limit = max(1.0, min(limit, float(timeout_s)))

        try:
            # Il turno tiene già il proprio wakelock, ma con un tag diverso e
            # una scadenza pensata per il turno; questo è il tag che in
            # ``dumpsys power`` dice "sto aspettando una macchina remota".
            async with keep_awake("ssh", timeout_s=limit + _SSH_WAKELOCK_MARGIN_S):
                result = await get_ssh_backend().exec(
                    target,
                    command,
                    timeout_s=limit,
                    max_output_chars=ssh_cfg.max_output_chars,
                )
        except SshError as exc:
            return _describe(exc)
        logger.info("ssh_exec on {}: exit {}", host, result.exit_code)
        return _render_exec(result)


# -- ssh_job -----------------------------------------------------------------


def _render_job_line(job: SshJob) -> str:
    state = job.status
    if job.exit_code is not None:
        state += f" (exit {job.exit_code})"
    preview = job.command.replace("\n", " ")[:_COMMAND_PREVIEW_CHARS]
    return f"- {job.job_id} [{state}] started {job.started_at} — {preview}"


def _render_poll(poll: SshJobPoll) -> str:
    job = poll.job
    header = f"job {job.job_id} on {job.alias}: {job.status}"
    if job.exit_code is not None:
        header += f" (exit code {job.exit_code})"
    parts = [header]
    if poll.output:
        parts.append(f"new output:\n{poll.output.rstrip()}")
    else:
        parts.append("no new output since the last poll.")
    if poll.pending_bytes:
        parts.append(
            f"{poll.pending_bytes} more bytes are already waiting — poll again right away "
            "to read them."
        )
    elif job.status == STATUS_RUNNING:
        parts.append(
            "Still running. Do not poll in a tight loop: answer the user, and poll again "
            "when they ask or when the task next comes up."
        )
    if job.status == STATUS_LOST:
        parts.append(
            "The process disappeared without recording an exit code — it was killed "
            "(out of memory, or the server rebooted). Treat the work as unfinished."
        )
    return "\n".join(parts)


@tool_parameters(
    tool_parameters_schema(
        host=StringSchema(_HOST_PARAM),
        action=StringSchema(
            "start a new detached command, poll it for new output, stop it, or list the "
            "jobs known for this host.",
            enum=["start", "poll", "stop", "list"],
        ),
        command=StringSchema("Command to run. Required for action=start."),
        job_id=StringSchema(
            "Job identifier returned by action=start. Required for poll and stop."
        ),
        required=["host", "action"],
    )
)
class SshJobTool(_SshToolMixin, Tool):
    """Comandi remoti lunghi: lanciati staccati, seguiti a delta."""

    _scopes = {"remote"}

    name = "ssh_job"
    description = (
        "Run a LONG command on a registered remote machine without waiting for it. "
        "`start` launches it detached, writing to a log file on the server, and returns a "
        "job_id; `poll` returns only the output produced since your last poll, plus whether "
        "the process is still alive and its exit code once it ends; `stop` signals it; "
        "`list` shows the jobs known for that host. "
        "Use this for package upgrades, builds, backups, long copies — anything that can "
        "outlive a phone's network. The job survives the connection dropping, the gateway "
        "restarting and the screen going off; you do not have to keep the turn open. "
        "You never track the read position yourself: `poll` always resumes where it left off."
    )

    async def execute(
        self,
        host: str,
        action: str,
        command: str | None = None,
        job_id: str | None = None,
        **kwargs: Any,
    ) -> str:
        action = (action or "").strip().lower()
        if action not in ("start", "poll", "stop", "list"):
            return "Error: action must be one of start, poll, stop, list."
        store = get_job_store()

        if action == "list":
            # Nessuna connessione: l'elenco è locale, e deve restare leggibile
            # anche quando l'host è irraggiungibile o la sua chiave è cambiata —
            # è proprio allora che serve sapere cosa era rimasto in sospeso.
            try:
                known = {h.alias for h in configured_hosts()}
            except Exception as exc:
                return f"Error: could not read the SSH configuration ({exc})."
            if host not in known:
                return _describe(SshHostUnknownError(host, sorted(known)))
            jobs = store.jobs(alias=host)
            if not jobs:
                return f"No jobs recorded for {host}."
            return "\n".join([f"{len(jobs)} job(s) on {host}:"] + [_render_job_line(j) for j in jobs])

        if action == "start" and not (command or "").strip():
            return "Error: action=start needs a command."
        if action in ("poll", "stop") and not (job_id or "").strip():
            return f"Error: action={action} needs the job_id returned by action=start."

        try:
            ssh_cfg, host_cfg, target = await self._resolve_off_loop(host)
        except SshError as exc:
            return _describe(exc)
        backend = get_ssh_backend()
        timeout = float(ssh_cfg.command_timeout_s)

        try:
            if action == "start":
                job = await store.start(
                    backend=backend,
                    target=target,
                    alias=host,
                    command=command or "",
                    log_dir=host_cfg.job_log_dir,
                    timeout_s=timeout,
                )
                return (
                    f"Started job {job.job_id} on {host} (remote pid {job.pid}), logging to "
                    f"{job.log_path}. It keeps running if this connection drops. "
                    f"Read its output with ssh_job action=poll job_id={job.job_id}."
                )
            if action == "poll":
                poll = await store.poll(
                    str(job_id),
                    backend=backend,
                    target=target,
                    alias=host,
                    max_bytes=ssh_cfg.max_output_chars,
                    timeout_s=timeout,
                )
                return _render_poll(poll)
            job = await store.stop(
                str(job_id), backend=backend, target=target, alias=host, timeout_s=timeout
            )
            return (
                f"Sent SIGTERM to job {job.job_id} (remote pid {job.pid}). This is "
                "best-effort: poll the job to confirm it actually stopped."
            )
        except SshError as exc:
            return _describe(exc)


# -- ssh_transfer ------------------------------------------------------------


@tool_parameters(
    tool_parameters_schema(
        host=StringSchema(_HOST_PARAM),
        direction=StringSchema(
            "up sends the local file to the remote host, down brings the remote file to "
            "the phone.",
            enum=["up", "down"],
        ),
        local_path=StringSchema(
            "Path inside the workspace, relative to it. Must stay within the workspace."
        ),
        remote_path=StringSchema("Absolute path of the file on the remote host."),
        required=["host", "direction", "local_path", "remote_path"],
    )
)
class SshTransferTool(_SshToolMixin, Tool):
    """Copia un file fra il workspace e un host registrato, via SFTP."""

    _scopes = {"remote"}

    name = "ssh_transfer"
    description = (
        "Copy ONE file between the workspace on this phone and a registered remote machine "
        "(direction=up to send, direction=down to fetch). Transfers go over SFTP on the same "
        "SSH connection. The local side is always inside the workspace — a path outside it "
        "is refused, and in a project a download can only land in the project's folder — "
        "and the transfer is capped by the configured size limit, checked before anything "
        "is written."
    )

    def __init__(
        self,
        workspace: str | Path,
        validate: Callable[[str], tuple[bool, str]] | None = None,
    ) -> None:
        super().__init__(validate)
        self._workspace = _safe_expanduser(workspace)

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(workspace=ctx.workspace)

    def _turn_root(self) -> Path:
        """La cartella del turno: ``write_root()`` dello scope legato, o il workspace.

        Senza uno scope legato — sessioni interne, test — resta il workspace del
        costruttore, che in quel caso è anche la radice del turno.
        """
        scope = current_workspace_scope()
        return scope.write_root() if scope is not None else self._workspace

    async def execute(
        self,
        host: str,
        direction: str,
        local_path: str,
        remote_path: str,
        **kwargs: Any,
    ) -> str:
        direction = (direction or "").strip().lower()
        if direction not in ("up", "down"):
            return "Error: direction must be 'up' or 'down'."
        # `down` scrive sul telefono, e la destinazione non passa dal cancello
        # dei tool file: in sola lettura si rifiuta qui. `up` scrive sul
        # remoto, come `ssh_exec`, e resta aperto.
        if direction == "down" and current_turn_is_readonly():
            return READONLY_TOOL_REFUSAL
        remote_path = (remote_path or "").strip()
        if not remote_path:
            return "Error: remote_path is empty."

        try:
            # Le radici sono quelle dei tool file, nessuna extra root. La
            # directory SSH (chiave privata, known_hosts) vive fuori dal
            # workspace proprio perché un tool come questo non possa esfiltrarla.
            #
            # `down` scrive sul telefono: il confine è quello di scrittura del
            # turno (`WorkspaceScope.write_root()`, come `download_file` e i tool
            # file), e un percorso relativo parte da lì. Con la radice
            # dell'installazione, da dentro un progetto si sovrascriveva
            # `SOUL.md`. `up` legge, e la lettura resta aperta
            # sull'installazione come per `read_file`; il percorso relativo
            # parte comunque dalla cartella del turno.
            base = self._turn_root()
            local = resolve_allowed_path(
                local_path,
                workspace=base,
                allowed_root=base if direction == "down" else self._workspace,
            )
        except WorkspaceBoundaryError as exc:
            return f"Error: {exc}"

        try:
            ssh_cfg, _host_cfg, target = await self._resolve_off_loop(host)
        except SshError as exc:
            return _describe(exc)
        backend = get_ssh_backend()
        cap = int(ssh_cfg.max_transfer_bytes)

        try:
            if direction == "up":
                if not local.is_file():
                    return f"Error: {local_path} does not exist in the workspace."
                size = local.stat().st_size
                if size > cap:
                    return (
                        f"Error: {local_path} is {size} bytes, over the {cap} byte transfer "
                        "limit. Compress or split it, or ask the user to raise the limit."
                    )
                sent = await backend.put(target, local, remote_path)
                logger.info("ssh_transfer up {} -> {}:{}", local_path, host, remote_path)
                return f"Uploaded {local_path} to {host}:{remote_path} ({sent} bytes)."

            # Dentro un aiutante: il «prima» per l'annulla (``agent.subagent_journal``).
            journal_touch(local)
            local.parent.mkdir(parents=True, exist_ok=True)
            # Il cap sul download lo applica il backend, due volte: sulla
            # dimensione dichiarata dal server, prima di cominciare, e sui byte
            # che arrivano davvero, perché quella dimensione la dice il server.
            # Scrive su un `.part` e rinomina solo a copia completa, così un
            # download fermato dal tetto non lascia un file troncato (v.
            # `SshBackend.get`). Applicarlo qui, a trasferimento finito, sarebbe
            # troppo tardi per entrambe le cose.
            got = await backend.get(target, remote_path, local, max_bytes=cap)
            logger.info("ssh_transfer down {}:{} -> {}", host, remote_path, local_path)
            return f"Downloaded {host}:{remote_path} to {local_path} ({got} bytes)."
        except SshError as exc:
            return _describe(exc)
        except OSError as exc:
            return f"Error: local file error ({exc})."


# Registrazione esplicita dei tool di questo modulo: il ToolLoader legge questa
# lista. Un nuovo tool va aggiunto qui esplicitamente.
TOOLS = [SshHostsTool, SshExecTool, SshJobTool, SshTransferTool]
