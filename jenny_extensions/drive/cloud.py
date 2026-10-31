"""Backup nel cloud: lo store degli snapshot spedito a pezzi cifrati.

Il backup remoto non rilegge il workspace: spedisce lo store content-addressed
che ``engine.py`` mantiene già, e solo i blob che la destinazione non ha. Il
primo giro porta tutto, i successivi quel che è cambiato.

Layout sulla destinazione (vedi ``cloud_sink.py`` per le chiavi)::

    LEGGIMI.txt                     in chiaro: cos'è, e di non toccarlo
    repo.json                       in chiaro: formato, versione, id del repository
    keycheck.jbk                    l'id del repository cifrato: verifica la chiave
    key.jbk                         la chiave, chiusa con la password dell'utente
    packs/<sha256>.jpk              blob dello store raggruppati e cifrati
    index/<sha256 del pack>.jix     cosa c'è in quel pack, compresso e cifrato
    snapshots/<ms>-<id>.jsn         il manifest di uno snapshot, compresso e cifrato

**Pacchi.** Drive soffre migliaia di file piccoli, quindi i blob (così come
stanno nello store: zlib) viaggiano concatenati in pacchi da qualche MB. In
coda al pacco, prima della cifratura, c'è il suo stesso elenco (JSON + 4 byte
di lunghezza): se un indice andasse perso si ricostruisce dai pacchi. Il nome
del pacco è lo sha256 del file cifrato, così chi lo scarica sa se è intero.

**Ordine di scrittura = garanzia.** Pacco, poi il suo indice, poi — solo alla
fine, con tutti i blob a posto — il manifest. Un giro interrotto lascia al
massimo un pacco orfano, mai uno snapshot remoto che rimanda a blob mancanti.
Il giro dopo riparte dagli indici: quel che è su, è su.

**Chiave e password.** I dati si cifrano con una chiave master casuale da 32
byte, che l'utente non vede e che resta sul telefono: è lei che permette la
copia notturna senza chiedere niente a nessuno. Il container ``.jbk`` deriva la
chiave da una passphrase con PBKDF2; per i dati la "passphrase" è la chiave in
esadecimale e le iterazioni sono 1: con 256 bit di entropia la lentezza di
PBKDF2 non protegge niente, e a 600.000 iterazioni ogni pacco costerebbe secondi
di CPU sul telefono. Il salt casuale per file resta, e con lui una chiave
diversa per ogni file.

La password la sceglie l'utente, e serve solo a un telefono nuovo: chiude la
chiave master in ``key.jbk`` (stesso container, PBKDF2 a 600.000 giri come il
file esportato — qui la lentezza conta, perché una password non ha 256 bit).
Cambiarla riscrive solo ``key.jbk``: i dati non si toccano e le copie vecchie
restano leggibili. La password non si salva da nessuna parte; chi la perde,
col telefono vecchio ancora in mano, ne sceglie un'altra.

**Stato locale.** Nella cartella di stato (fuori dal workspace, nella runtime
root: sopravvive a un ripristino) c'è solo una cache dell'indice remoto e
l'esito dell'ultimo giro. La verità è la destinazione: una cache persa o di un
altro repository si ricostruisce leggendo ``index/``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import struct
import time
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.snapshot.crypto import (
    DEFAULT_KDF_ITERATIONS,
    decrypt_container,
    decrypt_file,
    encrypt_container,
    encrypt_file,
)
from jenny.snapshot.crypto_backends.base import CryptoAuthError, CryptoBackend
from jenny.snapshot.engine import SnapshotEngine
from jenny.snapshot.store import BlobCorruptError, hash_content, object_path
from jenny.snapshot.types import SnapshotManifest, is_snapshot_id, unsafe_entry_reason
from jenny.utils.path import atomic_write

from .cloud_sink import RemoteNotFoundError, RemoteSink

FORMAT_NAME = "jenny-cloud-backup"
FORMAT_VERSION = 1
MASTER_KEY_LEN = 32
DEFAULT_PACK_TARGET_BYTES = 8 << 20
# Vedi la docstring del modulo: la chiave è casuale, non una passphrase.
_KDF_ITERATIONS = 1

# La password piu' corta che si accetta. Chi entra nel Drive puo' provarne a
# raffica sul suo computer, e a frenarlo ci sono solo questi caratteri e i giri
# di PBKDF2: sei (il minimo di WhatsApp) bastano a chi ha un caveau che si
# blocca dopo dieci tentativi, non a noi.
PASSWORD_MIN_LEN = 8

REPO_MARKER_KEY = "repo.json"
KEYCHECK_KEY = "keycheck.jbk"
SEALED_KEY_KEY = "key.jbk"
README_KEY = "LEGGIMI.txt"
PACKS_DIR = "packs"
INDEX_DIR = "index"
SNAPSHOTS_DIR = "snapshots"

_STATE_FILE = "state.json"
_CACHE_FILE = "index_cache.json"
_TMP_DIR = "tmp"

# Il file in chiaro per chi apre la cartella su Drive. Non passa dall'i18n
# perché non lo legge la WebUI: sta nel Drive dell'utente, in entrambe le lingue.
_README = """\
Questa cartella è la copia di sicurezza di Jenny. È cifrata: senza la
password che hai scelto non si può leggere. Non spostare né
modificare i file: Jenny la aggiorna da sola. Per riaverla su un telefono
nuovo, installa Jenny e rispondi «Sì» a «Ci conosciamo già?».

This folder is Jenny's backup. It is encrypted: it cannot be read without the
password you chose. Do not move or edit these files: Jenny keeps
it up to date by herself. To get it back on a new phone, install Jenny and
answer "Yes" to "Have we met before?".
"""


class CloudBackupError(Exception):
    """Il repository remoto non è utilizzabile per il giro richiesto."""


class RepoNotFoundError(CloudBackupError):
    """Sulla destinazione non c'è un repository di Jenny."""


class WrongKeyError(CloudBackupError):
    """La chiave non apre questo repository."""


class WrongPasswordError(CloudBackupError):
    """La password non apre la chiave della copia."""


class NoSealedKeyError(CloudBackupError):
    """La copia non ha una password: ``key.jbk`` non c'e'."""


@dataclass(frozen=True)
class RemoteSnapshot:
    """Uno snapshot presente sulla destinazione, letto dal solo nome del file."""

    key: str
    id: str
    created_at_ms: int


@dataclass(frozen=True)
class BackupResult:
    """Esito di un giro: cosa è stato spedito per *snapshot_id*."""

    snapshot_id: str
    already_remote: bool
    blobs_uploaded: int
    packs_uploaded: int
    bytes_uploaded: int
    file_count: int = 0


def generate_master_key() -> bytes:
    return secrets.token_bytes(MASTER_KEY_LEN)


async def seal_key(
    sink: RemoteSink,
    master_key: bytes,
    password: str,
    *,
    iterations: int = DEFAULT_KDF_ITERATIONS,
    backend: CryptoBackend | None = None,
) -> None:
    """Chiude *master_key* con *password* in ``key.jbk``, al posto di quel che c'era.

    Chi chiama ha gia' controllato che *master_key* apra la copia: sigillare la
    chiave sbagliata renderebbe la copia illeggibile a chi sa la password.
    """
    if len(password) < PASSWORD_MIN_LEN:
        raise ValueError("password too short")
    sealed = await encrypt_container(
        password, master_key, iterations=iterations, backend=backend
    )
    await sink.put_bytes(SEALED_KEY_KEY, sealed)


async def unseal_key(
    sink: RemoteSink, password: str, *, backend: CryptoBackend | None = None
) -> bytes:
    """La chiave master che *password* apre in ``key.jbk``.

    Raises:
        NoSealedKeyError: la copia non ha una password.
        WrongPasswordError: la password non e' questa.
    """
    try:
        sealed = await sink.get_bytes(SEALED_KEY_KEY)
    except RemoteNotFoundError:
        raise NoSealedKeyError("this backup has no password") from None
    try:
        key = await decrypt_container(password, sealed, backend=backend)
    except CryptoAuthError:
        raise WrongPasswordError("this password does not open the backup") from None
    if len(key) != MASTER_KEY_LEN:
        raise WrongPasswordError("this password does not open the backup")
    return key


async def has_sealed_key(sink: RemoteSink) -> bool:
    try:
        await sink.get_bytes(SEALED_KEY_KEY)
    except RemoteNotFoundError:
        return False
    return True


async def repo_exists(sink: RemoteSink) -> bool:
    """C'e' una copia di Jenny sulla destinazione? Non serve la chiave per saperlo."""
    try:
        await sink.get_bytes(REPO_MARKER_KEY)
    except RemoteNotFoundError:
        return False
    return True


def snapshot_key(manifest: SnapshotManifest) -> str:
    # Il tempo nel nome: l'elenco degli snapshot remoti si mostra (e si
    # ordina) senza scaricarne nemmeno uno.
    return f"{SNAPSHOTS_DIR}/{manifest.created_at_ms:013d}-{manifest.id}.jsn"


def parse_snapshot_name(name: str) -> tuple[int, str] | None:
    """``"<ms>-<id>.jsn"`` → ``(ms, id)``; ``None`` per qualunque altra cosa."""
    if not name.endswith(".jsn"):
        return None
    stem = name[: -len(".jsn")]
    ms, sep, snap_id = stem.partition("-")
    if not sep or not ms.isdigit() or not is_snapshot_id(snap_id):
        return None
    return int(ms), snap_id


class CloudRepository:
    """Un repository di backup su una :class:`RemoteSink`, aperto con la sua chiave."""

    def __init__(
        self,
        sink: RemoteSink,
        master_key: bytes,
        state_dir: Path,
        repo_id: str,
        *,
        pack_target_bytes: int = DEFAULT_PACK_TARGET_BYTES,
        backend: CryptoBackend | None = None,
        tmp_dir: Path | None = None,
    ) -> None:
        self._sink = sink
        self._passphrase = master_key.hex()
        self._state_dir = Path(state_dir)
        # I pacchi a metà stanno fuori dallo stato: lo stato viaggia nel
        # trasferimento fra telefoni, i temporanei no (``ext_tmp/`` in
        # ``data_extraction_rules.xml``). Senza, accanto allo stato.
        self._tmp_root = Path(tmp_dir) if tmp_dir is not None else self._state_dir / _TMP_DIR
        self.repo_id = repo_id
        self._pack_target = pack_target_bytes
        self._backend = backend
        # hash del blob → [id del pack, offset, lunghezza] nel pack in chiaro
        self._blobs: dict[str, list[Any]] = {}
        self._seen_indexes: set[str] = set()
        self._load_cache()

    # -- apertura -------------------------------------------------------------

    @classmethod
    async def open(
        cls,
        sink: RemoteSink,
        master_key: bytes,
        state_dir: Path,
        *,
        create: bool = False,
        pack_target_bytes: int = DEFAULT_PACK_TARGET_BYTES,
        backend: CryptoBackend | None = None,
        tmp_dir: Path | None = None,
    ) -> CloudRepository:
        """Apre il repository sulla destinazione, o lo crea se *create*.

        Raises:
            RepoNotFoundError: non c'è e *create* è falso.
            WrongKeyError: c'è, ma la chiave non è la sua.
            CloudBackupError: c'è, ma in un formato che questa versione non legge.
        """
        if len(master_key) != MASTER_KEY_LEN:
            raise ValueError("master key must be 32 bytes")
        passphrase = master_key.hex()
        try:
            marker = json.loads(await sink.get_bytes(REPO_MARKER_KEY))
        except RemoteNotFoundError:
            if not create:
                raise RepoNotFoundError("no Jenny backup on this destination") from None
            repo_id = secrets.token_hex(16)
            keycheck = await encrypt_container(
                passphrase, repo_id.encode(), iterations=_KDF_ITERATIONS, backend=backend
            )
            await sink.put_bytes(README_KEY, _README.encode("utf-8"))
            await sink.put_bytes(KEYCHECK_KEY, keycheck)
            # Il marker per ultimo: un repository senza keycheck non esiste.
            await sink.put_bytes(
                REPO_MARKER_KEY,
                json.dumps(
                    {"format": FORMAT_NAME, "version": FORMAT_VERSION, "repo_id": repo_id}
                ).encode(),
            )
            logger.info("Cloud backup repository {} created", repo_id[:8])
            return cls(
                sink, master_key, state_dir, repo_id,
                pack_target_bytes=pack_target_bytes, backend=backend, tmp_dir=tmp_dir,
            )

        if not isinstance(marker, dict) or marker.get("format") != FORMAT_NAME:
            raise CloudBackupError("destination holds something that is not a Jenny backup")
        if marker.get("version") != FORMAT_VERSION:
            raise CloudBackupError(f"unsupported cloud backup version {marker.get('version')}")
        repo_id = str(marker.get("repo_id") or "")
        try:
            check = await decrypt_container(
                passphrase, await sink.get_bytes(KEYCHECK_KEY), backend=backend
            )
        except (CryptoAuthError, RemoteNotFoundError):
            raise WrongKeyError("this key does not open the backup") from None
        if check.decode("utf-8", "replace") != repo_id:
            raise WrongKeyError("this key does not open the backup")
        return cls(
            sink, master_key, state_dir, repo_id,
            pack_target_bytes=pack_target_bytes, backend=backend, tmp_dir=tmp_dir,
        )

    # -- stato locale -----------------------------------------------------------

    def _load_cache(self) -> None:
        try:
            data = json.loads((self._state_dir / _CACHE_FILE).read_text("utf-8"))
        except (OSError, ValueError):
            return
        # Una cache di un altro repository (l'utente ha cambiato account, o è un
        # ripristino) non vale niente: si ricostruisce da ``index/``.
        if not isinstance(data, dict) or data.get("repo_id") != self.repo_id:
            return
        self._blobs = dict(data.get("blobs") or {})
        self._seen_indexes = set(data.get("indexes") or [])

    def _save_cache(self) -> None:
        atomic_write(
            self._state_dir / _CACHE_FILE,
            json.dumps(
                {
                    "repo_id": self.repo_id,
                    "indexes": sorted(self._seen_indexes),
                    "blobs": self._blobs,
                }
            ),
            fsync_dir=False,
        )

    def read_state(self) -> dict[str, Any]:
        """L'esito degli ultimi giri, per la spia «Al sicuro»."""
        try:
            data = json.loads((self._state_dir / _STATE_FILE).read_text("utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) and data.get("repo_id") == self.repo_id else {}

    def _write_state(self, **changes: Any) -> None:
        state = self.read_state()
        state.update(changes, repo_id=self.repo_id)
        atomic_write(self._state_dir / _STATE_FILE, json.dumps(state), fsync_dir=False)

    def _tmp_dir(self) -> Path:
        path = self._tmp_root
        path.mkdir(parents=True, exist_ok=True)
        return path

    # -- indice remoto ------------------------------------------------------------

    async def refresh_index(self) -> None:
        """Porta la cache al passo con ``index/`` (gli indici scritti da altri giri)."""
        names = await self._sink.list(INDEX_DIR)
        fresh = [n for n in names if n.endswith(".jix") and n not in self._seen_indexes]
        # Un blob può stare in due pacchi: dopo una copia rovinata, quella che
        # la rifà lo ricarica. Vince l'indice **più recente** (``at``), quindi
        # si leggono tutti prima e si applicano in ordine di scrittura.
        loaded: list[tuple[int, str, str, list[Any]]] = []
        for name in fresh:
            pack_id = name[: -len(".jix")]
            data = await decrypt_container(
                self._passphrase,
                await self._sink.get_bytes(f"{INDEX_DIR}/{name}"),
                backend=self._backend,
            )
            try:
                index = json.loads(zlib.decompress(data))
            except (zlib.error, ValueError) as exc:
                raise CloudBackupError(f"index {name} is not readable") from exc
            if not isinstance(index, dict) or index.get("pack") != pack_id:
                raise CloudBackupError(f"index {name} describes another pack")
            loaded.append((int(index.get("at") or 0), name, pack_id, index.get("blobs", [])))
        for _at, name, pack_id, blobs in sorted(loaded):
            for blob_hash, offset, length in blobs:
                self._blobs[blob_hash] = [pack_id, int(offset), int(length)]
            self._seen_indexes.add(name)
        if fresh:
            await asyncio.to_thread(self._save_cache)

    def has_blob(self, blob_hash: str) -> bool:
        return blob_hash in self._blobs

    def stored_bytes(self) -> int:
        """Quanto occupa la copia sulla destinazione: i blob noti all'indice.

        Sono i byte dei blob come stanno nei pacchi (compressi, prima della
        cifratura): il resto — indici, manifest, cifratura — è qualche per cento.
        Per «8,4 MB nel tuo Drive» basta, e non costa una richiesta.
        """
        return sum(int(location[2]) for location in self._blobs.values())

    # -- backup -----------------------------------------------------------------

    async def list_snapshots(self) -> list[RemoteSnapshot]:
        """Gli snapshot sulla destinazione, dal più recente."""
        out = []
        for name in await self._sink.list(SNAPSHOTS_DIR):
            parsed = parse_snapshot_name(name)
            if parsed is not None:
                out.append(RemoteSnapshot(f"{SNAPSHOTS_DIR}/{name}", parsed[1], parsed[0]))
        return sorted(out, key=lambda s: s.created_at_ms, reverse=True)

    async def backup(
        self,
        engine: SnapshotEngine,
        snapshot_id: str,
        *,
        progress: Callable[[int, int], None] | None = None,
        fresh: bool = False,
    ) -> BackupResult:
        """Spedisce lo snapshot *snapshot_id* dello store locale di *engine*.

        *progress* riceve ``(byte fatti, byte totali)`` all'inizio e dopo ogni
        pacco caricato. Sono i byte **dei file** (come nel workspace), non quelli
        compressi che viaggiano: è la misura che una persona riconosce.

        *fresh* ricarica **tutti** i blob dello snapshot, anche quelli che la
        destinazione dice di avere: è la copia che rifà una copia trovata
        rovinata dal controllo, e i suoi indici, più recenti, prendono il posto
        dei vecchi.
        """
        manifest = await asyncio.to_thread(engine.load_manifest, snapshot_id)
        if not fresh and any(s.id == snapshot_id for s in await self.list_snapshots()):
            return BackupResult(snapshot_id, True, 0, 0, 0, manifest.file_count)

        await self.refresh_index()
        needed: list[str] = []
        sizes: dict[str, int] = {}
        for entry in manifest.files:
            if (fresh or entry.hash not in self._blobs) and entry.hash not in sizes:
                needed.append(entry.hash)
                sizes[entry.hash] = entry.size
        total = sum(sizes.values())
        done = 0
        if progress is not None:
            progress(0, total)

        packs = 0
        sent = 0
        writer: _PackWriter | None = None
        in_pack = 0
        try:
            for blob_hash in needed:
                raw = await asyncio.to_thread(_read_verified_raw, engine.objects_dir, blob_hash)
                if writer is None:
                    writer = _PackWriter(self._tmp_dir())
                await asyncio.to_thread(writer.add, blob_hash, raw)
                in_pack += sizes[blob_hash]
                if writer.size >= self._pack_target:
                    sent += await self._upload_pack(
                        writer, self._pack_progress(progress, done, in_pack, total)
                    )
                    packs += 1
                    writer = None
                    done, in_pack = done + in_pack, 0
                    if progress is not None:
                        progress(done, total)
            if writer is not None:
                sent += await self._upload_pack(
                    writer, self._pack_progress(progress, done, in_pack, total)
                )
                packs += 1
                writer = None
                done, in_pack = done + in_pack, 0
                if progress is not None:
                    progress(done, total)
        finally:
            if writer is not None:
                writer.discard()

        # Il manifest compresso: un workspace da duemila file fa 300 kB di JSON,
        # e va su a ogni giro anche quando è cambiato un file solo.
        sealed = await encrypt_container(
            self._passphrase,
            zlib.compress(json.dumps(manifest.to_dict(), ensure_ascii=False).encode("utf-8")),
            iterations=_KDF_ITERATIONS,
            backend=self._backend,
        )
        await self._sink.put_bytes(snapshot_key(manifest), sealed)
        sent += len(sealed)
        self._write_state(
            last_success_ms=int(time.time() * 1000),
            last_snapshot_id=snapshot_id,
            last_snapshot_created_at_ms=manifest.created_at_ms,
        )
        logger.info(
            "Cloud backup of snapshot {}: {} blobs in {} packs, {} bytes",
            snapshot_id[:12], len(needed), packs, sent,
        )
        return BackupResult(snapshot_id, False, len(needed), packs, sent, manifest.file_count)

    @staticmethod
    def _pack_progress(
        progress: Callable[[int, int], None] | None, done: int, in_pack: int, total: int
    ) -> Callable[[int, int], None] | None:
        """L'avanzamento *dentro* un pacco, riportato ai byte dei file.

        Un pacco grande (una foto da 20 MB è un pacco da sola) altrimenti
        farebbe saltare la barra da 0 a 100: Drive lo carica a pezzi, e ogni
        pezzo confermato sposta la barra della sua parte.
        """
        if progress is None:
            return None

        def report(sent: int, size: int) -> None:
            if size > 0:
                progress(done + int(in_pack * min(sent, size) / size), total)

        return report

    async def _upload_pack(
        self, writer: _PackWriter, on_progress: Callable[[int, int], None] | None = None
    ) -> int:
        """Sigilla, cifra e carica un pacco, poi il suo indice. Ritorna i byte spediti."""
        tmp = self._tmp_dir()
        plain = await asyncio.to_thread(writer.seal)
        sealed = tmp / f"{plain.name}.enc"
        try:
            await encrypt_file(
                self._passphrase, plain, sealed,
                iterations=_KDF_ITERATIONS, backend=self._backend,
            )
            pack_id = await asyncio.to_thread(_sha256_file, sealed)
            await self._sink.put_file(f"{PACKS_DIR}/{pack_id}.jpk", sealed, on_progress)
            index = await encrypt_container(
                self._passphrase,
                zlib.compress(json.dumps({
                    "pack": pack_id,
                    "at": int(time.time() * 1000),
                    "blobs": writer.entries,
                }).encode()),
                iterations=_KDF_ITERATIONS,
                backend=self._backend,
            )
            await self._sink.put_bytes(f"{INDEX_DIR}/{pack_id}.jix", index)
            size = sealed.stat().st_size + len(index)
        finally:
            plain.unlink(missing_ok=True)
            sealed.unlink(missing_ok=True)
        for blob_hash, offset, length in writer.entries:
            self._blobs[blob_hash] = [pack_id, offset, length]
        self._seen_indexes.add(f"{pack_id}.jix")
        await asyncio.to_thread(self._save_cache)
        return size

    def record_failure(self, error: str) -> None:
        """Annota un giro fallito: la spia conta da ``last_success_ms``, non da qui."""
        self._write_state(last_failure_ms=int(time.time() * 1000), last_error=error[:500])

    # -- ripristino ---------------------------------------------------------------

    async def load_manifest(self, snapshot: RemoteSnapshot) -> SnapshotManifest:
        data = await decrypt_container(
            self._passphrase, await self._sink.get_bytes(snapshot.key), backend=self._backend
        )
        try:
            manifest = SnapshotManifest.from_dict(json.loads(zlib.decompress(data)))
        except (zlib.error, ValueError, KeyError, TypeError) as exc:
            raise CloudBackupError(f"snapshot {snapshot.key} is not readable") from exc
        if manifest.id != snapshot.id:
            raise CloudBackupError(f"snapshot {snapshot.key} holds another snapshot")
        return manifest

    async def restore(
        self,
        snapshot: RemoteSnapshot,
        dest_dir: Path,
        *,
        progress: Callable[[int, int], None] | None = None,
    ) -> SnapshotManifest:
        """Materializza *snapshot* in *dest_dir*, scaricando solo i pacchi che servono.

        Come ``SnapshotEngine.restore_snapshot``, valida ogni voce prima di
        scrivere la prima: il manifest arriva dalla rete. *progress* riceve
        ``(pacchi fatti, pacchi totali)``.
        """
        manifest = await self.load_manifest(snapshot)
        dest_dir = Path(dest_dir)
        root = dest_dir.resolve()
        for entry in manifest.files:
            reason = unsafe_entry_reason(entry.path, entry.hash)
            if reason is None:
                resolved = (root / Path(*entry.path.split("/"))).resolve()
                if not resolved.is_relative_to(root):
                    reason = f"unsafe path {entry.path!r} leaves the destination"
            if reason is not None:
                raise CloudBackupError(f"snapshot {snapshot.id[:12]}: {reason}")

        await self.refresh_index()
        by_pack: dict[str, dict[str, tuple[int, int]]] = {}
        paths_by_hash: dict[str, list[str]] = {}
        for entry in manifest.files:
            location = self._blobs.get(entry.hash)
            if location is None:
                raise CloudBackupError(f"blob {entry.hash[:12]} of {entry.path!r} is missing")
            pack_id, offset, length = location
            by_pack.setdefault(pack_id, {})[entry.hash] = (int(offset), int(length))
            paths_by_hash.setdefault(entry.hash, []).append(entry.path)

        dest_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._tmp_dir()
        total = len(by_pack)
        for done, (pack_id, blobs) in enumerate(sorted(by_pack.items()), start=1):
            sealed = tmp / f"restore-{pack_id}.jpk"
            plain = tmp / f"restore-{pack_id}.plain"
            try:
                await self._sink.get_file(f"{PACKS_DIR}/{pack_id}.jpk", sealed)
                if await asyncio.to_thread(_sha256_file, sealed) != pack_id:
                    raise CloudBackupError(f"pack {pack_id[:12]} is damaged")
                await decrypt_file(self._passphrase, sealed, plain, backend=self._backend)
                await asyncio.to_thread(
                    _extract_blobs, plain, blobs, paths_by_hash, dest_dir
                )
            finally:
                sealed.unlink(missing_ok=True)
                plain.unlink(missing_ok=True)
            if progress is not None:
                progress(done, total)
        return manifest


# -- helper sincroni (girano in un thread) --------------------------------------


class _PackWriter:
    """Un pacco in costruzione su file: blob concatenati, poi l'elenco in coda."""

    def __init__(self, tmp_dir: Path) -> None:
        self.path = tmp_dir / f"pack-{secrets.token_hex(8)}.plain"
        self._handle = open(self.path, "wb")  # chiuso da seal() o discard()
        self.entries: list[list[Any]] = []
        self.size = 0

    def add(self, blob_hash: str, raw: bytes) -> None:
        self._handle.write(raw)
        self.entries.append([blob_hash, self.size, len(raw)])
        self.size += len(raw)

    def seal(self) -> Path:
        trailer = json.dumps({"blobs": self.entries}).encode()
        self._handle.write(trailer)
        self._handle.write(struct.pack(">I", len(trailer)))
        self._handle.close()
        return self.path

    def discard(self) -> None:
        self._handle.close()
        self.path.unlink(missing_ok=True)


def _read_verified_raw(objects_dir: Path, blob_hash: str) -> bytes:
    """I byte del blob così come sono nello store (zlib), verificati contro l'hash.

    Spedire un blob corrotto vorrebbe dire scoprirlo al ripristino, cioè
    quando non c'è più l'originale.
    """
    raw = object_path(objects_dir, blob_hash).read_bytes()
    try:
        content = zlib.decompress(raw)
    except zlib.error as exc:
        raise BlobCorruptError(f"blob {blob_hash} is not valid zlib data") from exc
    if hash_content(content) != blob_hash:
        raise BlobCorruptError(f"blob {blob_hash} content does not match its hash")
    return raw


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _extract_blobs(
    plain: Path,
    blobs: dict[str, tuple[int, int]],
    paths_by_hash: dict[str, list[str]],
    dest_dir: Path,
) -> None:
    with open(plain, "rb") as handle:
        for blob_hash, (offset, length) in blobs.items():
            handle.seek(offset)
            raw = handle.read(length)
            try:
                content = zlib.decompress(raw)
            except zlib.error as exc:
                raise CloudBackupError(f"blob {blob_hash[:12]} is damaged") from exc
            if hash_content(content) != blob_hash:
                raise CloudBackupError(f"blob {blob_hash[:12]} does not match its hash")
            for rel in paths_by_hash[blob_hash]:
                atomic_write(dest_dir / Path(*rel.split("/")), content, fsync_dir=False)
