"""Il backup nel cloud visto dal resto di Jenny: accendere, spedire, verificare.

Mette insieme i pezzi — la chiave, la destinazione (Google Drive), il
repository (``cloud.py``) e lo store degli snapshot — dietro poche operazioni
che la WebUI e (in seguito) il giro notturno chiamano senza sapere come sono
fatti dentro.

**La chiave** vive in ``<runtime_root>/cloud_backup/master.key``: fuori dal
workspace, quindi fuori dagli snapshot e dal backup stesso (una copia della
chiave dentro i dati che apre non proteggerebbe niente). Fuori dal telefono
esiste solo chiusa con la password dell'utente, in ``key.jbk`` sulla copia
(v. ``cloud.py``): un telefono nuovo la riapre scrivendo quella password, e
nient'altro la riapre. La password non tocca mai il disco né i log.

**Un giro** = uno snapshot fresco (``pre_cloud``) e la sua spedizione. Lo store
viene letto mentre la retention potrebbe girare: lo snapshot che si spedisce è
il più recente, e la retention protegge sempre gli ultimi; se comunque un blob
sparisse a metà, il giro fallisce e il successivo riparte da quel che c'è.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from jenny.snapshot.crypto import DEFAULT_KDF_ITERATIONS
from jenny.snapshot.locations import runtime_root_for
from jenny.snapshot.store import hash_content
from jenny.utils.path import atomic_write

from .cloud import (
    MASTER_KEY_LEN,
    PASSWORD_MIN_LEN,
    CloudBackupError,
    CloudRepository,
    NoSealedKeyError,
    RepoNotFoundError,
    WrongKeyError,
    WrongPasswordError,
    generate_master_key,
    has_sealed_key,
    repo_exists,
    seal_key,
    unseal_key,
)
from .cloud_drive import DriveConsentError, DriveError, DriveSink
from .cloud_sink import RemoteSink

if TYPE_CHECKING:
    from jenny.snapshot.crypto_backends.base import CryptoBackend
    from jenny.snapshot.service import SnapshotService

PROVIDER_DRIVE = "drive"
# Il nome dell'estensione: chiave in ``config.extensions`` e cartella dei
# temporanei sotto ``ext_tmp/``.
EXTENSION_NAME = "drive"
# Lo stato locale (chiave, cache dell'indice, esito dei giri), accanto al
# workspace e fuori da esso: ``filesDir/cloud_backup/``, lo stesso di prima.
STATE_DIR_NAME = "cloud_backup"
SNAPSHOT_TRIGGER = "pre_cloud"
KEY_FILE = "master.key"
_STATUS_FILE = "service.json"
_VERIFY_DIR = "verify"

# Il ritmo del giro automatico (v. ``tick``). Venti ore e non ventiquattro:
# un telefono messo in carica ogni sera a un'ora un po' diversa deve rientrare
# nella finestra anche la sera in cui lo si attacca prima.
DUE_AFTER_MS = 20 * 3600 * 1000
# Oltre questo, la carica non si aspetta più: meglio una copia a batteria che
# nessuna copia. Il Wi-Fi invece sì, sempre — i dati mobili sono dell'utente.
# Era 72 ore: su un telefono caricato ogni due o tre giorni, a orari sparsi, la
# copia saltava un giorno su due. Una copia normale manda un paio di MB.
OVERDUE_AFTER_MS = 24 * 3600 * 1000
# Tre notti senza una copia riuscita: Jenny lo dice, una volta per serie.
ALERT_AFTER_MS = 72 * 3600 * 1000
VERIFY_EVERY_MS = 7 * 24 * 3600 * 1000
# La storia delle copie per i pallini delle ultime notti: due settimane, con un
# tetto per chi fa «Fai una copia adesso» dieci volte al giorno.
COPIES_KEEP_MS = 14 * 24 * 3600 * 1000
COPIES_KEEP_MAX = 40

SinkFactory = Callable[[], Awaitable[RemoteSink]]


class CloudUnavailableError(Exception):
    """Su questo dispositivo il backup nel cloud non si può fare (niente Google)."""


class CloudBackupService:
    """Le operazioni del backup nel cloud per un workspace."""

    def __init__(
        self,
        snapshot_service: SnapshotService,
        *,
        state_dir: Path,
        sink_factory: SinkFactory | None,
        get_provider: Callable[[], str],
        set_provider: Callable[[str], Awaitable[None]],
        backend: CryptoBackend | None = None,
        password_iterations: int = DEFAULT_KDF_ITERATIONS,
        tmp_dir: Path | None = None,
    ) -> None:
        self._snapshots = snapshot_service
        self._state_dir = Path(state_dir)
        # I temporanei (pacchi a metà, la prova di ripristino) fuori dallo
        # stato, che invece viaggia da un telefono all'altro.
        self._tmp_dir = Path(tmp_dir) if tmp_dir is not None else self._state_dir
        self._sink_factory = sink_factory
        self._get_provider = get_provider
        self._set_provider = set_provider
        self._backend = backend
        # I giri di PBKDF2 per la password: 600.000 come il file esportato. Il
        # parametro c'è per i test, che altrimenti pagherebbero mezzo secondo
        # a ogni accensione; chi apre legge il numero dall'header.
        self._password_iterations = password_iterations
        self._lock = asyncio.Lock()
        # ``(byte fatti, byte totali)`` del giro in corso, ``None`` fuori dai giri.
        self._progress: tuple[int, int] | None = None

    # -- chiave -----------------------------------------------------------------

    @property
    def key_path(self) -> Path:
        return self._state_dir / KEY_FILE

    def _read_key(self) -> bytes | None:
        try:
            key = self.key_path.read_bytes()
        except FileNotFoundError:
            return None
        if len(key) != MASTER_KEY_LEN:
            logger.warning("Cloud backup key file has the wrong size; ignoring it")
            return None
        return key

    def _write_key(self, key: bytes) -> None:
        atomic_write(self.key_path, key, chmod=0o600)

    # -- stato ------------------------------------------------------------------

    def _read_status(self) -> dict[str, Any]:
        try:
            data = json.loads((self._state_dir / _STATUS_FILE).read_text("utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write_status(self, **changes: Any) -> None:
        status = self._read_status()
        status.update(changes)
        atomic_write(self._state_dir / _STATUS_FILE, json.dumps(status), fsync_dir=False)

    def status(self) -> dict[str, Any]:
        """Quel che serve alla riga «Al sicuro · stanotte alle 3:12» e ai suoi guai."""
        stored = self._read_status()
        return {
            "available": self._sink_factory is not None,
            "provider": self._get_provider(),
            "has_key": self._read_key() is not None,
            "running": self._lock.locked(),
            "last_success_ms": stored.get("last_success_ms", 0),
            "last_snapshot_created_at_ms": stored.get("last_snapshot_created_at_ms", 0),
            "last_failure_ms": stored.get("last_failure_ms", 0),
            "last_error": stored.get("last_error", ""),
            "last_bytes_uploaded": stored.get("last_bytes_uploaded", 0),
            "last_verified_ms": stored.get("last_verified_ms", 0),
            "enabled_at_ms": stored.get("enabled_at_ms", 0),
            # Quando l'utente ha scelto (o riscritto) la password. Zero: la
            # copia non ha password, e un telefono nuovo non la riaprirebbe.
            "password_set_ms": stored.get("password_set_ms", 0),
            "alerted_ms": stored.get("alerted_ms", 0),
            "recent_copies_ms": list(stored.get("recent_copies_ms") or []),
            "stored_bytes": stored.get("stored_bytes", 0),
            "file_count": stored.get("file_count", 0),
            "progress": (
                {"done_bytes": self._progress[0], "total_bytes": self._progress[1]}
                if self._progress is not None and self._lock.locked()
                else None
            ),
        }

    # -- operazioni ---------------------------------------------------------------

    async def _open(self, sink: RemoteSink, key: bytes, *, create: bool) -> CloudRepository:
        return await CloudRepository.open(
            sink, key, self._state_dir, create=create, backend=self._backend,
            tmp_dir=self._tmp_dir / "tmp",
        )

    async def _sink(self) -> RemoteSink:
        if self._sink_factory is None:
            raise CloudUnavailableError("no cloud destination on this device")
        return await self._sink_factory()

    async def enable(self, password: str | None = None) -> dict[str, Any]:
        """Accende il backup su Drive: chiave (nuova se manca), repository, password.

        Una chiave nuova vuole la password che la chiude: senza, si torna
        ``password_needed`` prima di scrivere niente, e la pagina la chiede. Se
        sulla destinazione c'è già un backup che questa chiave non apre — il
        telefono nuovo che ha detto «accendi» invece di «ripristina» — non lo
        si sovrascrive: si torna ``existing_backup`` e decide l'utente.
        Riaccendendo una copia che ha già la password, la password non serve.
        """
        if password is not None and len(password) < PASSWORD_MIN_LEN:
            return {"ok": False, "error": "weak_password"}
        async with self._lock:
            sink = await self._sink()
            try:
                key = self._read_key()
                if key is None:
                    if await repo_exists(sink):
                        return {"ok": False, "error": "existing_backup"}
                    if password is None:
                        return {"ok": False, "error": "password_needed"}
                    key = generate_master_key()
                    await asyncio.to_thread(self._write_key, key)
                try:
                    await self._open(sink, key, create=True)
                except WrongKeyError:
                    return {"ok": False, "error": "existing_backup"}
                if password is not None:
                    await seal_key(
                    sink, key, password,
                    iterations=self._password_iterations, backend=self._backend,
                )
                    self._write_status(password_set_ms=int(time.time() * 1000))
                elif not await has_sealed_key(sink):
                    self._write_status(password_set_ms=0)
            except DriveConsentError:
                return {"ok": False, "error": "consent_needed"}
            finally:
                await _close(sink)
            await self._set_provider(PROVIDER_DRIVE)
            # Da qui conta l'attesa della prima copia, e l'avviso se non arriva.
            self._write_status(enabled_at_ms=int(time.time() * 1000), alerted_ms=0)
            logger.info("Cloud backup enabled ({})", PROVIDER_DRIVE)
        return {"ok": True, **self.status()}

    async def disable(self) -> dict[str, Any]:
        """Spegne i giri. La copia su Drive e la chiave restano: si riaccende com'era."""
        await self._set_provider("")
        return {"ok": True, **self.status()}

    async def run(self) -> dict[str, Any]:
        """Un giro: snapshot fresco e spedizione di quel che manca."""
        if self._get_provider() != PROVIDER_DRIVE:
            return {"ok": False, "error": "disabled"}
        if self._lock.locked():
            return {"ok": False, "error": "already_running"}
        async with self._lock:
            outcome = await self._run_locked()
        # Lo stato si legge a lock rilasciato: dentro direbbe «in corso» di un
        # giro già finito.
        return {**outcome, **self.status()}

    async def _run_locked(self) -> dict[str, Any]:
        key = self._read_key()
        if key is None:
            return self._fail("missing_key")
        started = time.monotonic()
        try:
            await self._snapshots.snapshot_now(SNAPSHOT_TRIGGER)
            snapshot_id = await asyncio.to_thread(self._snapshots.engine.head_id)
            if snapshot_id is None:
                return self._fail("no_snapshot")
            sink = await self._sink()
            try:
                repo = await self._open(sink, key, create=False)
                result = await repo.backup(
                    self._snapshots.engine, snapshot_id, progress=self._note_progress,
                    fresh=bool(self._read_status().get("force_fresh")),
                )
                remote = repo.read_state()
                stored_bytes = repo.stored_bytes()
                # La verità sulla password è la copia: se ``key.jbk`` è sparito
                # (cancellato a mano da Drive) la scheda deve dirlo, perché un
                # telefono nuovo non la riaprirebbe più.
                sealed = await has_sealed_key(sink)
            finally:
                self._progress = None
                await _close(sink)
        except DriveConsentError:
            return self._fail("consent_needed")
        except WrongKeyError:
            return self._fail("wrong_key")
        except RepoNotFoundError:
            return self._fail("backup_missing")
        except (DriveError, CloudBackupError, OSError) as exc:
            logger.warning("Cloud backup run failed: {}", exc)
            return self._fail(f"{type(exc).__name__}: {exc}")
        now = int(time.time() * 1000)
        copies = [
            t for t in self._read_status().get("recent_copies_ms") or []
            if isinstance(t, int) and now - t < COPIES_KEEP_MS
        ]
        copies = (copies + [now])[-COPIES_KEEP_MAX:]
        self._write_status(
            last_success_ms=now,
            last_snapshot_created_at_ms=remote.get("last_snapshot_created_at_ms", 0),
            last_bytes_uploaded=result.bytes_uploaded,
            last_error="",
            recent_copies_ms=copies,
            stored_bytes=stored_bytes,
            file_count=result.file_count,
            force_fresh=False,
            password_set_ms=(self._read_status().get("password_set_ms") or now) if sealed else 0,
        )
        logger.info(
            "Cloud backup run done in {:.1f}s ({} bytes)",
            time.monotonic() - started, result.bytes_uploaded,
        )
        return {"ok": True, "uploaded_bytes": result.bytes_uploaded}

    def _note_progress(self, done: int, total: int) -> None:
        self._progress = (done, total)

    async def tick(
        self, *, charging: bool, unmetered: bool, now_ms: int | None = None
    ) -> dict[str, Any]:
        """Il giro automatico: decide se è il momento, lo fa, e dice se avvisare.

        Chiamato ogni ora dal cron di sistema. Le regole, in ordine:

        - spento → niente;
        - una copia riuscita da meno di 20 ore → niente;
        - altrimenti, in carica e su una rete non a consumo → un giro;
        - da più di 24 ore senza copia → un giro anche senza carica, ma mai
          su rete a consumo;
        - dopo un giro riuscito, una prova di ripristino se l'ultima ha più di
          una settimana.

        ``alert`` non è vuoto quando sono passate 72 ore dall'ultima copia
        riuscita (o dall'accensione, se non ce n'è mai stata una) e per questa
        serie non si è ancora avvisato: chi chiama lo porta all'utente.
        """
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        if self._get_provider() != PROVIDER_DRIVE:
            return {"action": "off", "alert": None}
        stored = self._read_status()
        since_ref = stored.get("last_success_ms") or stored.get("enabled_at_ms") or now
        waited = now - since_ref
        action = "wait"
        if waited >= DUE_AFTER_MS and unmetered and (charging or waited >= OVERDUE_AFTER_MS):
            outcome = await self.run()
            action = "ran" if outcome.get("ok") else "failed"
            if outcome.get("ok"):
                stored = self._read_status()
                if now - (stored.get("last_verified_ms") or 0) >= VERIFY_EVERY_MS:
                    checked = await self.verify()
                    if not checked.get("ok"):
                        # Una copia rotta l'ha già segnata ``verify``
                        # (``_mark_damaged``); il resto si rifà al prossimo giro.
                        logger.warning("Cloud backup verify failed: {}", checked.get("error"))
        stored = self._read_status()
        since_ref = stored.get("last_success_ms") or stored.get("enabled_at_ms") or now
        alert = None
        if now - since_ref >= ALERT_AFTER_MS and (stored.get("alerted_ms") or 0) <= since_ref:
            alert = {
                "days": int((now - since_ref) // (24 * 3600 * 1000)),
                "error": stored.get("last_error") or ("no_wifi" if action == "wait" else ""),
                "never": not stored.get("last_success_ms"),
            }
            self._write_status(alerted_ms=now)
        return {"action": action, "alert": alert}

    async def verify(self) -> dict[str, Any]:
        """Prova di ripristino: scarica l'ultima copia remota e la confronta coi suoi hash.

        Un backup che si scrive e non si rilegge non è un backup. Ricostruisce
        l'albero in una cartella di lavoro (fuori dal workspace), controlla
        ogni file contro l'hash del manifest e la butta.
        """
        key = self._read_key()
        if key is None:
            return {"ok": False, "error": "missing_key"}
        async with self._lock:
            work = self._tmp_dir / _VERIFY_DIR
            await asyncio.to_thread(shutil.rmtree, work, True)
            sink = await self._sink()
            try:
                repo = await self._open(sink, key, create=False)
                snapshots = await repo.list_snapshots()
                if not snapshots:
                    return {"ok": False, "error": "nothing_to_verify"}
                manifest = await repo.restore(snapshots[0], work)
                bad = await asyncio.to_thread(_mismatches, work, manifest.files)
            except DriveConsentError:
                return {"ok": False, "error": "consent_needed"}
            except CloudBackupError as exc:
                # La copia c'è ma non si ricostruisce (pacco rovinato, blob che
                # manca o non torna): è rotta, non irraggiungibile.
                logger.warning("Cloud backup verify found a damaged copy: {}", exc)
                self._mark_damaged()
                return {"ok": False, "error": "mismatch"}
            except (DriveError, OSError) as exc:
                # Rete o disco: il controllo non è finito, la copia non risulta
                # rotta. Lo si rifà al prossimo giro.
                return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            finally:
                await _close(sink)
                await asyncio.to_thread(shutil.rmtree, work, True)
            if bad:
                self._mark_damaged()
                return {"ok": False, "error": "mismatch", "files": bad[:20]}
            self._write_status(last_verified_ms=int(time.time() * 1000))
            return {
                "ok": True,
                "snapshot_created_at_ms": snapshots[0].created_at_ms,
                "files": len(manifest.files),
            }

    # -- telefono nuovo: chiave e ripristino ------------------------------------

    async def set_password(self, password: str) -> dict[str, Any]:
        """Sceglie o cambia la password della copia: riscrive solo ``key.jbk``.

        La vecchia non serve — il telefono ha già la chiave, cioè tutto. Prima
        di sigillare si controlla che la chiave di qui apra davvero la copia su
        Drive: chiudere con la password una chiave che non è la sua renderebbe
        la copia illeggibile proprio a chi la sa.
        """
        if len(password) < PASSWORD_MIN_LEN:
            return {"ok": False, "error": "weak_password"}
        key = self._read_key()
        if key is None:
            return {"ok": False, "error": "missing_key"}
        async with self._lock:
            sink = await self._sink()
            try:
                await self._open(sink, key, create=False)
                await seal_key(
                    sink, key, password,
                    iterations=self._password_iterations, backend=self._backend,
                )
            except RepoNotFoundError:
                return {"ok": False, "error": "no_backup"}
            except WrongKeyError:
                return {"ok": False, "error": "wrong_key"}
            except DriveConsentError:
                return {"ok": False, "error": "consent_needed"}
            finally:
                await _close(sink)
            self._write_status(password_set_ms=int(time.time() * 1000))
        logger.info("Cloud backup password set")
        return {"ok": True, **self.status()}

    async def unlock(self, password: str) -> dict[str, Any]:
        """Telefono nuovo (o chiave che manca qui): la password riapre la chiave.

        La chiave che esce da ``key.jbk`` deve anche aprire la copia — un
        ``key.jbk`` d'altri non deve diventare la chiave di questo telefono.
        Tenuta quella, la copia notturna riprende senza chiedere più niente.
        """
        async with self._lock:
            sink = await self._sink()
            try:
                if not await repo_exists(sink):
                    return {"ok": False, "error": "no_backup"}
                key = await unseal_key(sink, password, backend=self._backend)
                await self._open(sink, key, create=False)
            except NoSealedKeyError:
                return {"ok": False, "error": "no_password"}
            except (WrongPasswordError, WrongKeyError):
                return {"ok": False, "error": "wrong_password"}
            except DriveConsentError:
                return {"ok": False, "error": "consent_needed"}
            finally:
                await _close(sink)
            await asyncio.to_thread(self._write_key, key)
            self._write_status(password_set_ms=int(time.time() * 1000))
        logger.info("Cloud backup key unlocked with the password")
        return {"ok": True}

    async def remote_snapshots(self, limit: int = 10) -> dict[str, Any]:
        """Le copie su Drive, dalla più recente: quel che «Ci conosciamo già?» mostra."""
        key = self._read_key()
        sink = await self._sink()
        try:
            if key is None:
                # Senza chiave si sa almeno se una copia c'è: la password si
                # chiede solo a chi ha qualcosa da aprire.
                if not await repo_exists(sink):
                    return {"ok": False, "error": "no_backup"}
                return {"ok": False, "error": "missing_key"}
            repo = await self._open(sink, key, create=False)
            snapshots = await repo.list_snapshots()
        except RepoNotFoundError:
            return {"ok": False, "error": "no_backup"}
        except WrongKeyError:
            return {"ok": False, "error": "wrong_key"}
        except DriveConsentError:
            return {"ok": False, "error": "consent_needed"}
        finally:
            await _close(sink)
        return {
            "ok": True,
            "snapshots": [
                {"id": s.id, "created_at_ms": s.created_at_ms} for s in snapshots[:limit]
            ],
        }

    async def stage_restore(self, snapshot_id: str) -> dict[str, Any]:
        """Prepara il ripristino di una copia su Drive; lo scambio avviene al riavvio.

        Stesso protocollo del ripristino da file o da snapshot locale: albero in
        ``workspace_staged``, conteggio di controllo, marker per ultimo. Lo stato
        di adesso viene fotografato prima (``pre_restore``): anche un ripristino
        dal cloud resta annullabile dalla storia locale.
        """
        from jenny.snapshot.locations import STAGED_WORKSPACE_DIR_NAME, runtime_root_for
        from jenny.snapshot.restore_marker import write_marker, write_staging_sanity

        key = self._read_key()
        if key is None:
            return {"ok": False, "error": "missing_key"}
        runtime_root = runtime_root_for(self._snapshots.engine.root)
        staged = runtime_root / STAGED_WORKSPACE_DIR_NAME
        async with self._lock:
            sink = await self._sink()
            try:
                repo = await self._open(sink, key, create=False)
                match = [s for s in await repo.list_snapshots() if s.id == snapshot_id]
                if not match:
                    return {"ok": False, "error": "no_such_copy"}
                await self._snapshots.snapshot_now("pre_restore")
                await asyncio.to_thread(shutil.rmtree, staged, True)
                manifest = await repo.restore(match[0], staged)
            except DriveConsentError:
                await asyncio.to_thread(shutil.rmtree, staged, True)
                return {"ok": False, "error": "consent_needed"}
            except (DriveError, CloudBackupError, OSError) as exc:
                await asyncio.to_thread(shutil.rmtree, staged, True)
                logger.warning("Cloud restore failed: {}", exc)
                return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            finally:
                await _close(sink)
            await asyncio.to_thread(write_staging_sanity, staged)
            write_marker(runtime_root, source="cloud", snapshot_id=snapshot_id)
        logger.info("Cloud restore staged ({}); restart required", snapshot_id[:12])
        return {
            "ok": True,
            "created_at_ms": manifest.created_at_ms,
            "file_count": manifest.file_count,
        }

    def _mark_damaged(self) -> None:
        """La copia remota è rotta: lo stato lo dice, e la prossima la rifà da capo."""
        self._write_status(
            last_error="verify_mismatch",
            last_failure_ms=int(time.time() * 1000),
            force_fresh=True,
        )

    def _fail(self, error: str) -> dict[str, Any]:
        self._write_status(last_failure_ms=int(time.time() * 1000), last_error=error[:500])
        return {"ok": False, "error": error}


def _mismatches(root: Path, entries: list[Any]) -> list[str]:
    bad = []
    for entry in entries:
        path = root / Path(*entry.path.split("/"))
        try:
            if hash_content(path.read_bytes()) != entry.hash:
                bad.append(entry.path)
        except OSError:
            bad.append(entry.path)
    return bad


async def _close(sink: RemoteSink) -> None:
    aclose = getattr(sink, "aclose", None)
    if aclose is not None:
        await aclose()


# -- costruzione sul telefono -------------------------------------------------------


def _android_sink_factory(context: Any) -> SinkFactory:
    """Una ``DriveSink`` il cui token arriva da ``DriveBridge.kt``."""
    from java import jclass  # importabile solo sotto Chaquopy

    bridge = jclass("com.flagdizero.jenny.DriveBridge")
    last: dict[str, str | None] = {"token": None}

    async def token_provider(force: bool) -> str:
        stale = last["token"] if force else None
        token = await asyncio.to_thread(bridge.getToken, context, stale)
        if not token:
            raise DriveConsentError("Drive needs the user's consent")
        last["token"] = str(token)
        return str(token)

    async def factory() -> RemoteSink:
        return DriveSink(token_provider)

    return factory


def device_conditions() -> tuple[bool, bool]:
    """``(in carica, rete non a consumo)``. Fuori da Android: sempre sì."""
    from jenny.runtime.context import get_android_context

    context = get_android_context()
    if context is None:
        return True, True
    try:
        from java import jclass

        bridge = jclass("com.flagdizero.jenny.DriveBridge")
        return bool(bridge.isCharging(context)), bool(bridge.isUnmetered(context))
    except Exception:
        logger.exception("Could not read charging/network state")
        return False, False


async def cloud_tick(snapshot_service: SnapshotService) -> dict[str, Any]:
    """Il giro orario del cron di sistema, con le condizioni vere del telefono."""
    charging, unmetered = await asyncio.to_thread(device_conditions)
    return await cloud_service_for(snapshot_service).tick(
        charging=charging, unmetered=unmetered
    )


def cloud_service_for(snapshot_service: SnapshotService) -> CloudBackupService:
    """Il servizio (uno per processo) sopra *snapshot_service*.

    Vive appeso allo ``SnapshotService`` perché è l'unico oggetto condiviso fra
    le route della WebUI e il runtime: due istanze vorrebbero dire due lock, e
    due giri insieme sullo stesso repository.
    """
    existing = getattr(snapshot_service, "_cloud_service", None)
    if existing is not None:
        return existing

    from jenny.config.loader import load_config
    from jenny.config.store import mutate
    from jenny.runtime.context import get_android_context

    workspace = snapshot_service.engine.root
    context = get_android_context()
    sink_factory = None
    if context is not None and _drive_available(context):
        sink_factory = _android_sink_factory(context)

    # L'interruttore sta in ``config.json`` sotto ``extensions.drive`` (la
    # sezione generica delle estensioni): viaggia negli snapshot e nella copia
    # stessa, così il telefono nuovo, ripristinato, la ritrova accesa.
    def get_provider() -> str:
        return str(load_config().extensions.get(EXTENSION_NAME, {}).get("provider", ""))

    async def set_provider(value: str) -> None:
        def apply(cfg: Any) -> bool:
            section = dict(cfg.extensions.get(EXTENSION_NAME, {}))
            if section.get("provider", "") == value:
                return False
            section["provider"] = value
            cfg.extensions = {**cfg.extensions, EXTENSION_NAME: section}
            return True

        await mutate(apply)

    root = runtime_root_for(workspace)
    service = CloudBackupService(
        snapshot_service,
        state_dir=root / STATE_DIR_NAME,
        tmp_dir=root / "ext_tmp" / EXTENSION_NAME,
        sink_factory=sink_factory,
        get_provider=get_provider,
        set_provider=set_provider,
    )
    snapshot_service._cloud_service = service  # type: ignore[attr-defined]
    return service


def _drive_available(context: Any) -> bool:
    try:
        from java import jclass

        return bool(jclass("com.flagdizero.jenny.DriveBridge").isAvailable(context))
    except Exception:
        logger.exception("DriveBridge unavailable")
        return False


__all__ = [
    "PROVIDER_DRIVE",
    "CloudBackupService",
    "CloudUnavailableError",
    "cloud_service_for",
    "cloud_tick",
    "device_conditions",
]
