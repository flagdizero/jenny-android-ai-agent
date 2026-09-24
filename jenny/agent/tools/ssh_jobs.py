"""Registro dei comandi remoti lunghi, staccati dalla connessione SSH.

Perché esiste, e perché non basta alzare il timeout: il wakelock
(``jenny/runtime/power.py``) tiene la CPU accesa durante il comando, ma non fa
niente per la rete. Un passaggio wifi→dati mobili cambia indirizzo e uccide il
TCP, e con esso la sessione SSH; il gateway può anche essere ucciso o riavviato
nel frattempo. Un ``apt upgrade`` atteso su un canale SSH aperto non fallisce in
modo pulito — fallisce a metà, e nessuno sa a che punto era. Il wakelock ha
risolto la sospensione della CPU, non questo.

Qui quindi i comandi lunghi non si aspettano. Si lanciano con ``nohup``
scrivendo su un log lato server, e si seguono **a delta**: ogni poll legge solo
la parte nuova del file. La connessione può cadere quante volte vuole, il
processo remoto non se ne accorge.

Il CURSORE lo tiene questo registro, mai il modello. Chiedere a un LLM di
ricordare un offset in byte fra un turno e l'altro è il modo più affidabile di
perdere output: basta una compattazione del contesto e il delta successivo
riparte da un punto inventato.

Sul quoting: il comando dell'utente/modello finisce dentro ``sh -c`` via
:func:`shlex.quote`, quindi non può rompere il wrapper né toccare i path del
log. I path del log NON derivano mai da input del modello: il ``job_id`` è
generato qui, il modello può solo *nominarne* uno già registrato, e il path
viene riletto dal record — non ricostruito dall'id che ha passato.
"""

from __future__ import annotations

import asyncio
import json
import shlex
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.agent.tools.ssh_backends.base import SshError, SshTarget
from jenny.config.paths import get_runtime_subdir
from jenny.runtime.power import keep_awake
from jenny.security.wire_ids import WIRE_ID_RE
from jenny.utils.path import atomic_write

# Margine fra la scadenza del wakelock e il timeout del comando di poll: il lock
# deve sopravvivere al comando che protegge. Stesso valore e stesso motivo di
# ``ssh._SSH_WAKELOCK_MARGIN_S``, tenuto qui perché il registro non importa il
# modulo dei tool (sarebbe un ciclo).
_POLL_WAKELOCK_MARGIN_S = 60.0

# Stati di un job. "lost" è distinto da "finished": il processo non c'è più ma
# non ha lasciato il codice di uscita, quindi è stato ucciso (OOM killer, reboot
# del server) e dirlo "finito" nasconderebbe un fallimento.
STATUS_RUNNING = "running"
STATUS_FINISHED = "finished"
STATUS_STOPPED = "stopped"
STATUS_LOST = "lost"

_TERMINAL_STATUSES = frozenset({STATUS_FINISHED, STATUS_STOPPED, STATUS_LOST})

# Separa l'intestazione (dimensione/vivo/exit code) dal delta di log in un
# unico comando remoto: tre round trip SSH per un poll sarebbero tre occasioni
# di cadere invece di una.
_MARKER = "__JENNY_SSH_JOB__"

# Solo caratteri sicuri in un path: gli id li generiamo noi, questo è il
# controllo che lo *dimostra* al lettore prima di interpolarli in un comando.
_SAFE_ID_RE = WIRE_ID_RE

# Tetto sui record conservati: senza, il file cresce per sempre e ogni avvio
# rilegge job di sei mesi fa. Si potano i terminati, mai i vivi.
_MAX_JOBS = 100

# Il tetto sopra da solo NON basta, ed è un caso reale e non teorico: pota solo
# i job terminati, quindi cento job rimasti ``running`` — un server sparito e
# mai più interrogato — non ne fanno cadere nessuno e il file cresce comunque.
# Oltre questa età un record si scarta a prescindere dallo stato: un job
# "in corso" da un mese non è in corso, è un record che non possiamo più
# verificare. Tenerlo darebbe al modello un elenco di lavori vivi che non
# esistono, che è peggio del non saperlo.
_MAX_JOB_AGE_DAYS = 30

_JOBS_FILE = "jobs.json"


class SshJobError(SshError):
    """Errore del registro job (id sconosciuto, avvio non riuscito).

    Sottoclasse di :class:`SshError` di proposito: il livello tool ha già un
    solo ``except SshError`` che traduce tutto in messaggi per il modello, e un
    ramo in più non aggiungerebbe nulla se non un modo di dimenticarsene.
    """


class SshJobNotFoundError(SshJobError):
    """Job id sconosciuto. Il messaggio elenca quelli validi, come per gli alias."""

    def __init__(self, job_id: object, known: list[str]) -> None:
        self.job_id = job_id
        listed = ", ".join(known) if known else "none"
        super().__init__(f"unknown ssh job {job_id!r}: known jobs are {listed}")


@dataclass(slots=True)
class SshJob:
    """Un comando remoto in corso (o concluso) e il punto a cui l'abbiamo letto."""

    job_id: str
    alias: str
    command: str
    pid: int
    log_path: str
    rc_path: str
    started_at: str
    # Offset in BYTE già consegnati al modello. In byte e non in caratteri
    # perché è quello che ``tail -c`` capisce: contarli in caratteri decodificati
    # farebbe scivolare il cursore al primo output non ASCII.
    cursor: int = 0
    status: str = STATUS_RUNNING
    exit_code: int | None = None

    @property
    def running(self) -> bool:
        return self.status not in _TERMINAL_STATUSES


@dataclass(frozen=True, slots=True)
class SshJobPoll:
    """Esito di un poll: il delta letto e lo stato del processo remoto."""

    job: SshJob
    output: str
    log_size: int
    # Byte già scritti sul log ma non ancora consegnati: dice al modello se
    # richiamare subito il poll o se può aspettare.
    pending_bytes: int


# -- costruzione dei comandi remoti ------------------------------------------


def _check_job_id(job_id: str) -> str:
    if not _SAFE_ID_RE.match(job_id or ""):
        raise SshJobError(f"refusing to use an unsafe job id: {job_id!r}")
    return job_id


def _new_job_id(alias: str) -> str:
    """Id generato qui, mai fornito dal chiamante.

    L'alias contribuisce solo con i suoi caratteri sicuri: serve a rendere
    leggibili i nomi dei file di log sul server, non a identificare l'host.
    """
    prefix = "".join(c for c in alias if c.isalnum() or c in "-_")[:24] or "job"
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _start_command(*, log_dir: str, log_path: str, rc_path: str, command: str) -> str:
    """``nohup`` del comando, con il codice di uscita salvato accanto al log.

    Il codice di uscita va scritto su file perché al poll il processo non
    esiste più: ``kill -0`` sa solo dire "vivo/morto", e senza questo file un
    backup fallito e uno riuscito sarebbero indistinguibili.

    Il comando gira in una **subshell** ``( … )``, non in linea. Senza, un
    comando che finisce con ``exit N`` — cosa normalissima in uno script — fa
    uscire il wrapper prima della riga che salva il codice, e il job resterebbe
    per sempre senza esito: né "finished" né vivo. Con la subshell ``exit``
    chiude solo quella, e ``$?`` è esattamente il codice che ha passato.
    """
    inner = f"(\n{command}\n)\nprintf '%s' \"$?\" > {shlex.quote(rc_path)}\n"
    return (
        f"mkdir -p {shlex.quote(log_dir)} 2>/dev/null; "
        # ``< /dev/null`` evita che nohup si lamenti dello stdin e che un comando
        # distratto resti in attesa su un prompt che nessuno leggerà mai.
        f"nohup sh -c {shlex.quote(inner)} < /dev/null > {shlex.quote(log_path)} 2>&1 & "
        f"echo $!"
    )


def _poll_command(job: SshJob, *, limit: int) -> str:
    """Intestazione + delta di log in un solo comando.

    L'ordine dei campi conta: la dimensione si legge PRIMA del delta, così il
    cursore avanza al massimo di quanto era davvero disponibile. Se il file
    cresce nel frattempo il peggio che succede è rileggere qualche byte al poll
    successivo — duplicare output è recuperabile, perderlo no.
    """
    log = shlex.quote(job.log_path)
    rc = shlex.quote(job.rc_path)
    return (
        f"s=$(wc -c < {log} 2>/dev/null) || s=0; "
        f"if kill -0 {job.pid} 2>/dev/null; then a=1; else a=0; fi; "
        f"r=$(cat {rc} 2>/dev/null); "
        f'printf "%s\\n%s\\n%s\\n{_MARKER}\\n" "$s" "$a" "$r"; '
        # ``tail -c +N`` è 1-based: il cursore è un conteggio di byte consumati.
        f"tail -c +{job.cursor + 1} {log} 2>/dev/null | head -c {limit}"
    )


def _stop_command(pid: int) -> str:
    """SIGTERM ai figli e poi al wrapper.

    Prima i figli: uccidere ``sh`` per primo li farebbe riparentare a init e
    sopravvivere alla richiesta di stop. Resta best-effort — un albero di
    processi profondo, o un figlio che ignora SIGTERM, sopravvive comunque; è il
    poll successivo a dire com'è andata, non il valore di ritorno di ``kill``.
    """
    return f"pkill -P {pid} 2>/dev/null; kill {pid} 2>/dev/null; exit 0"


def _parse_pid(stdout: str) -> int:
    """Ultima riga non vuota dello stdout di start: il pid stampato da ``echo $!``."""
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if not lines or not lines[-1].isdigit():
        raise SshJobError(
            f"could not read the remote pid from the launch output: {stdout.strip()[:200]!r}"
        )
    return int(lines[-1])


def _as_int(text: str) -> int | None:
    """Campo numerico dell'intestazione di poll, o ``None`` se non lo è."""
    stripped = (text or "").strip()
    if not stripped.lstrip("-").isdigit():
        return None
    return int(stripped)


# -- registro ----------------------------------------------------------------


class SshJobStore:
    """Job persistiti sotto ``.jenny/ssh_jobs/jobs.json``.

    Persistenti e non in memoria perché su Android il gateway viene ucciso di
    routine: un job lanciato ieri deve restare seguibile oggi, cursore compreso.
    """

    def __init__(self, path: Path | None = None) -> None:
        # Path risolto pigramente: al momento dell'import il workspace non è
        # ancora configurato.
        self._path = path
        self._jobs: dict[str, SshJob] | None = None
        # Un solo lock per tutto il registro, tenuto anche durante l'exec SSH.
        # Serializza i poll: due poll concorrenti sullo stesso job leggerebbero
        # lo stesso cursore e ne avanzerebbero uno solo, perdendo un delta. Le
        # operazioni sono tutte brevi e con timeout, quindi il costo è nullo.
        self._lock = asyncio.Lock()

    @property
    def path(self) -> Path:
        if self._path is None:
            self._path = get_runtime_subdir("ssh_jobs") / _JOBS_FILE
        return self._path

    # -- persistenza ---------------------------------------------------------

    def _load(self) -> dict[str, SshJob]:
        if self._jobs is not None:
            return self._jobs
        jobs: dict[str, SshJob] = {}
        try:
            raw = json.loads(self.path.read_text("utf-8")) if self.path.exists() else []
        except (OSError, json.JSONDecodeError) as exc:
            # Il registro non è la verità: la verità sono i processi sul server.
            # Meglio ripartire vuoti che rifiutarsi di lanciare nuovi job.
            logger.error("ssh job store unreadable, starting empty: {}", exc)
            raw = []
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict):
                continue
            try:
                job = SshJob(**{k: item[k] for k in item if k in SshJob.__slots__})
            except TypeError:
                continue
            if _SAFE_ID_RE.match(job.job_id or ""):
                jobs[job.job_id] = job
        self._jobs = jobs
        return jobs

    def _save(self) -> None:
        jobs = self._load()
        self._prune(jobs)
        try:
            atomic_write(self.path, json.dumps([asdict(j) for j in jobs.values()], indent=2))
        except OSError as exc:
            logger.error("could not persist ssh jobs: {}", exc)

    @staticmethod
    def _prune(jobs: dict[str, SshJob]) -> None:
        # 1) Per età, senza guardare lo stato (vedi ``_MAX_JOB_AGE_DAYS``).
        cutoff = datetime.now() - timedelta(days=_MAX_JOB_AGE_DAYS)
        for job in list(jobs.values()):
            try:
                started = datetime.fromisoformat(job.started_at)
            except (TypeError, ValueError):
                # Timestamp illeggibile: si tiene. Un record scritto da una
                # versione futura non deve sparire per un problema di formato.
                continue
            if started < cutoff:
                jobs.pop(job.job_id, None)

        # 2) Per numero, e qui solo i terminati: un job vivo e recente non si
        # butta nemmeno se il tetto è superato — sarebbe l'unico modo che il
        # modello ha di ritrovare un comando ancora in esecuzione.
        if len(jobs) <= _MAX_JOBS:
            return
        terminated = sorted(
            (j for j in jobs.values() if not j.running), key=lambda j: j.started_at
        )
        for job in terminated[: len(jobs) - _MAX_JOBS]:
            jobs.pop(job.job_id, None)

    # -- lettura -------------------------------------------------------------

    def jobs(self, *, alias: str | None = None) -> list[SshJob]:
        values = list(self._load().values())
        if alias is not None:
            values = [j for j in values if j.alias == alias]
        return sorted(values, key=lambda j: j.started_at, reverse=True)

    def get(self, job_id: str) -> SshJob | None:
        return self._load().get(job_id)

    def _require(self, job_id: str, *, alias: str | None = None) -> SshJob:
        jobs = self._load()
        job = jobs.get(job_id)
        if job is None or (alias is not None and job.alias != alias):
            known = [j.job_id for j in self.jobs(alias=alias)]
            raise SshJobNotFoundError(job_id, known)
        return job

    # -- operazioni ----------------------------------------------------------

    async def start(
        self,
        *,
        backend: Any,
        target: SshTarget,
        alias: str,
        command: str,
        log_dir: str,
        timeout_s: float,
    ) -> SshJob:
        """Lancia ``command`` staccato dalla connessione e ne registra il pid."""
        command = command.strip()
        if not command:
            raise SshJobError("refusing to start an empty command")

        async with self._lock:
            job_id = _check_job_id(_new_job_id(alias))
            log_path = f"{log_dir.rstrip('/')}/{job_id}.log"
            rc_path = f"{log_dir.rstrip('/')}/{job_id}.rc"
            result = await backend.exec(
                target,
                _start_command(
                    log_dir=log_dir, log_path=log_path, rc_path=rc_path, command=command
                ),
                timeout_s=timeout_s,
                max_output_chars=2_000,
            )
            if result.exit_code != 0:
                raise SshJobError(
                    f"could not launch the job (exit {result.exit_code}): "
                    f"{(result.stderr or result.stdout).strip()[:300]}"
                )
            job = SshJob(
                job_id=job_id,
                alias=alias,
                command=command,
                pid=_parse_pid(result.stdout),
                log_path=log_path,
                rc_path=rc_path,
                started_at=datetime.now().isoformat(),
            )
            self._load()[job_id] = job
            self._save()
            logger.info("ssh job {} started on {} (pid {})", job_id, alias, job.pid)
            return job

    async def poll(
        self,
        job_id: str,
        *,
        backend: Any,
        target: SshTarget,
        alias: str | None = None,
        max_bytes: int,
        timeout_s: float,
    ) -> SshJobPoll:
        """Legge la sola parte nuova del log e aggiorna stato e cursore."""
        async with self._lock:
            job = self._require(job_id, alias=alias)
            # Un poll è un round trip breve ma è quasi sempre l'ULTIMA cosa che
            # succede in un turno, e spesso a schermo spento: se la CPU si
            # sospende mentre aspetta, il comando scade e il cursore non avanza —
            # il delta non va perso (lo si rilegge), ma il modello riceve un
            # errore di rete al posto dell'output che stava seguendo.
            async with keep_awake("ssh", timeout_s=timeout_s + _POLL_WAKELOCK_MARGIN_S):
                result = await backend.exec(
                    target,
                    _poll_command(job, limit=max_bytes),
                    timeout_s=timeout_s,
                    # Spazio per l'intestazione oltre al delta: se il marker venisse
                    # troncato via non sapremmo più dove finisce l'una e inizia l'altro.
                    max_output_chars=max_bytes + 512,
                )
            head, marker, chunk = result.stdout.partition(f"{_MARKER}\n")
            if not marker:
                raise SshJobError(
                    "unreadable poll output from the remote host "
                    f"(exit {result.exit_code}): {(result.stderr or head).strip()[:300]}"
                )
            fields = head.splitlines()
            size = _as_int(fields[0] if len(fields) > 0 else "") or 0
            alive = (fields[1].strip() if len(fields) > 1 else "") == "1"
            exit_code = _as_int(fields[2] if len(fields) > 2 else "")

            if size < job.cursor:
                # Log troncato o ruotato sotto di noi: ripartire da zero è
                # l'unica lettura onesta, il delta appena chiesto era vuoto.
                job.cursor = 0
                chunk = ""
            else:
                job.cursor += max(0, min(max_bytes, size - job.cursor))

            if exit_code is not None:
                # Il file del codice di uscita ha la precedenza su ``kill -0``:
                # su un server occupato il pid può essere già stato riciclato da
                # un altro processo, che risulterebbe "vivo".
                job.exit_code = exit_code
                # Un job fermato da noi resta "stopped" anche se ha fatto in
                # tempo a registrare il 143 di SIGTERM: "finished" suggerirebbe
                # che il lavoro sia arrivato in fondo, che è l'opposto.
                if job.status != STATUS_STOPPED:
                    job.status = STATUS_FINISHED
            elif alive:
                job.status = STATUS_RUNNING
            elif job.status != STATUS_STOPPED:
                job.status = STATUS_LOST
            self._save()
            return SshJobPoll(
                job=job,
                output=chunk,
                log_size=size,
                pending_bytes=max(0, size - job.cursor),
            )

    async def stop(
        self,
        job_id: str,
        *,
        backend: Any,
        target: SshTarget,
        alias: str | None = None,
        timeout_s: float,
    ) -> SshJob:
        """Manda SIGTERM al processo remoto e marca il job come fermato."""
        async with self._lock:
            job = self._require(job_id, alias=alias)
            await backend.exec(
                target,
                _stop_command(job.pid),
                timeout_s=timeout_s,
                max_output_chars=1_000,
            )
            if job.status == STATUS_RUNNING:
                job.status = STATUS_STOPPED
            self._save()
            logger.info("ssh job {} signalled (pid {})", job.job_id, job.pid)
            return job


# Singleton: il cursore è stato condiviso, e due istanze che scrivono lo stesso
# file si sovrascriverebbero a vicenda i delta.
_store: SshJobStore | None = None


def get_job_store() -> SshJobStore:
    global _store
    if _store is None:
        _store = SshJobStore()
    return _store


def reset_job_store() -> None:
    """Scorda il registro cachato (startup del gateway e test)."""
    global _store
    _store = None
