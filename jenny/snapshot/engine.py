"""Motore di snapshot del workspace: scan, commit, restore, retention, gc.

Il motore è sincrono e puro stdlib; i chiamanti async lo eseguono via
``asyncio.to_thread``. Un file letto durante lo scan viene hashato dai byte
letti (mai ri-letto), quindi ogni voce del manifest è internamente coerente
anche se il workspace cambia a metà scan.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Iterable, Iterator

from loguru import logger

from jenny.snapshot.store import get_blob, iter_blob_hashes, object_path, put_blob
from jenny.snapshot.types import (
    FileEntry,
    SnapshotManifest,
    is_snapshot_id,
    unsafe_entry_reason,
)
from jenny.utils.path import atomic_write

# Esclusioni di default (path POSIX relativi alla radice del workspace).
# Le directory runtime reali (store snapshot, staging) vengono escluse anche
# per prefisso calcolato in ``__init__`` — necessario perché il runtime dir
# può ancora chiamarsi ``.minijenny`` su installazioni esistenti.
DEFAULT_EXCLUDE_GLOBS: tuple[str, ...] = (
    "ui/**",
    "logs/**",
    ".jenny/logs/**",
    ".jenny/snapshots/**",
    ".jenny/backup_staging/**",
    # Registro dei job SSH remoti. Escluso per due ragioni distinte. La prima è
    # che è stato operativo effimero: id, cursori e pid di processi su una
    # macchina che non è questa, privi di senso dopo un restore. La seconda
    # conta di più — contiene i comandi INTERI e i path dei log lato server,
    # cioè l'unica traccia SSH che altrimenti lascerebbe il dispositivo dentro
    # un ``.jbk`` esportato, mentre la chiave privata (fuori dal workspace) no.
    ".jenny/ssh_jobs/**",
    "**/__pycache__/**",
    "*.tmp",
    "*.tmp.*",
)

# ``output/`` NON è in quella lista, ed è una scelta, non una dimenticanza.
#
# È la cartella dove il system prompt manda ogni file che l'agente produce, e
# nessuno la spazza: non esiste sweep, da nessuna parte (``OUTPUT_SUBDIR`` è
# citato solo da ``config/paths.py`` e da ``sync_workspace_templates``). Quindi
# cresce e basta — che è esattamente l'argomento a favore di escluderla, e il
# motivo per cui va misurato invece che intuito.
#
# Il costo vero. I blob sono content-addressed: un deliverable che non cambia
# viene pagato una volta sola e poi condiviso da tutti gli snapshot successivi.
# A ripetersi per snapshot è la sola voce di manifest — path + sha256 esadecimale
# + size + mtime_ns, circa 150 byte serializzati. Con N file in ``output/``,
# ``output/`` costa ~150·N byte per manifest conservato, più i byte dei
# contenuti *distinti* una volta ciascuno. È un termine lineare in quanto
# l'utente ha effettivamente prodotto, non nel tempo di accensione del device:
# la differenza con ``logs/`` o ``__pycache__``, che crescono da soli e sono la
# ragione per cui questa lista esiste.
#
# Il costo dell'esclusione, invece. Questa stessa lista governa anche l'export
# cifrato: ``backup.py::_build_zip`` costruisce l'albero del ``.jbk`` da
# ``iter_tracked_files()``. Escludere ``output/`` non toglierebbe solo la storia
# locale — toglierebbe il lavoro finito dell'utente dall'unico artefatto che
# lascia il telefono. Un backup che contiene i file di sistema e non i risultati
# è il rovescio esatto di quello che serve.
#
# Se un giorno la storia pesasse troppo, la leva è ``retention_max_age_days``
# (default 0 = per sempre), che accorcia la storia lasciando intatto lo stato
# corrente. Perdere snapshot vecchi si sopporta; scoprire a restore fatto che
# l'unica copia di un deliverable non c'era, no.

# Quanto prima dello snapshot precedente un file deve essere stato scritto per
# riusarne l'hash (v. ``SnapshotEngine._reusable_entries``).
_RACY_MARGIN_MS = 2000


class SnapshotEngine:
    """Storia di snapshot content-addressed di una directory radice."""

    def __init__(
        self,
        root: Path,
        snapshots_dir: Path,
        *,
        exclude_globs: Iterable[str] = DEFAULT_EXCLUDE_GLOBS,
        exclude_dirs: Iterable[Path] = (),
    ) -> None:
        self.root = Path(root).resolve()
        self.snapshots_dir = Path(snapshots_dir)
        self.objects_dir = self.snapshots_dir / "objects"
        self.manifests_dir = self.snapshots_dir / "manifests"
        self.index_path = self.snapshots_dir / "index.json"
        # Serializza le scritture dell'indice tra thread concorrenti (writer via
        # to_thread + eventuali chiamanti sync): senza questo due writer possono
        # sovrapporsi su index.json. RLock perché apply_retention/create_snapshot
        # possono annidare la presa attraverso helper.
        self._index_lock = threading.RLock()
        self._globs = tuple(exclude_globs)
        # Prefissi esclusi derivati dai path reali: proteggono lo store dallo
        # auto-includersi qualunque sia il nome del runtime dir.
        self._exclude_prefixes: set[str] = set()
        for extra in (self.snapshots_dir, *exclude_dirs):
            rel = self._rel_or_none(Path(extra))
            if rel is not None:
                self._exclude_prefixes.add(rel)

    # -- scan ---------------------------------------------------------------

    def _rel_or_none(self, path: Path) -> str | None:
        try:
            return path.resolve().relative_to(self.root).as_posix()
        except (ValueError, OSError):
            return None

    def _is_excluded_dir(self, rel: str, name: str) -> bool:
        for prefix in self._exclude_prefixes:
            if rel == prefix or rel.startswith(prefix + "/"):
                return True
        for glob in self._globs:
            if not glob.endswith("/**"):
                continue
            base = glob[:-3]
            if base.startswith("**/"):
                if name == base[3:]:
                    return True
            elif rel == base or rel.startswith(base + "/"):
                return True
        return False

    def _is_excluded_file(self, rel: str, name: str) -> bool:
        for prefix in self._exclude_prefixes:
            if rel.startswith(prefix + "/"):
                return True
        for glob in self._globs:
            if glob.endswith("/**"):
                continue
            if fnmatch(name, glob) or fnmatch(rel, glob):
                return True
        return False

    def iter_tracked_files(self) -> Iterator[tuple[str, Path]]:
        """Itera ``(path_relativo_posix, path_assoluto)`` dei file tracciati.

        Symlink (file e directory) sono sempre saltati, fail-closed: un link
        non può far uscire lo snapshot dal workspace né gonfiarlo.
        """
        for dirpath, dirnames, filenames in os.walk(self.root, followlinks=False):
            dir_rel = Path(dirpath).relative_to(self.root).as_posix()
            if dir_rel == ".":
                dir_rel = ""
            kept: list[str] = []
            for dname in sorted(dirnames):
                rel = f"{dir_rel}/{dname}" if dir_rel else dname
                if (Path(dirpath) / dname).is_symlink():
                    continue
                if self._is_excluded_dir(rel, dname):
                    continue
                kept.append(dname)
            dirnames[:] = kept
            for fname in sorted(filenames):
                full = Path(dirpath) / fname
                if full.is_symlink():
                    continue
                rel = f"{dir_rel}/{fname}" if dir_rel else fname
                if self._is_excluded_file(rel, fname):
                    continue
                yield rel, full

    def fingerprint(self) -> dict[str, tuple[int, int]]:
        """Impronta economica ``{path: (size, mtime_ns)}`` senza hashing.

        Usata dal servizio per il debounce: rileva i cambiamenti a costo di
        una stat per file.
        """
        result: dict[str, tuple[int, int]] = {}
        for rel, full in self.iter_tracked_files():
            try:
                st = full.stat()
            except OSError:
                continue
            result[rel] = (st.st_size, st.st_mtime_ns)
        return result

    # -- snapshot -----------------------------------------------------------

    def create_snapshot(
        self,
        *,
        trigger: str,
        label: str | None = None,
        now_ms: int | None = None,
        reuse_unchanged: bool = False,
    ) -> SnapshotManifest | None:
        """Crea uno snapshot; ritorna None se nulla è cambiato dal precedente.

        Con *reuse_unchanged* un file con la stessa ``(size, mtime_ns)`` della
        sua voce nello snapshot precedente non viene riletto: se ne riusa
        l'hash. Spento di default, perché lo snapshot del workspace è la rete
        di sicurezza del restore e lì una lettura in più non costa niente
        rispetto a un hash sbagliato. Lo accendono le foto dei piani, che ne
        fanno una per passo: senza, ogni foto rilegge tutto il quaderno.
        """
        created_at_ms = now_ms if now_ms is not None else int(time.time() * 1000)
        parent = self.head_id()
        parent_manifest: SnapshotManifest | None = None
        if parent is not None:
            try:
                parent_manifest = self.load_manifest(parent)
            except (OSError, ValueError, KeyError):
                parent_manifest = None
        known = self._reusable_entries(parent_manifest) if reuse_unchanged else {}

        entries: list[FileEntry] = []
        for rel, full in self.iter_tracked_files():
            previous = known.get(rel)
            if previous is not None:
                try:
                    st = full.stat()
                except OSError:
                    continue
                if (st.st_size, st.st_mtime_ns) == (previous.size, previous.mtime_ns):
                    entries.append(previous)
                    continue
            try:
                content = full.read_bytes()
                st = full.stat()
            except OSError:
                # File sparito o illeggibile durante lo scan: lo si salta,
                # verrà catturato dallo snapshot successivo.
                continue
            blob_hash = put_blob(self.objects_dir, content)
            entries.append(
                FileEntry(path=rel, hash=blob_hash, size=len(content), mtime_ns=st.st_mtime_ns)
            )

        if parent_manifest is not None:
            same = {e.path: e.hash for e in parent_manifest.files} == {
                e.path: e.hash for e in entries
            }
            if same:
                return None

        snapshot_id = self._compute_id(parent, trigger, created_at_ms, entries)
        manifest = SnapshotManifest(
            id=snapshot_id,
            created_at_ms=created_at_ms,
            trigger=trigger,
            label=label,
            parent=parent,
            files=entries,
        )
        # L'indice va caricato PRIMA di scrivere il manifest: il rebuild
        # automatico (conteggio disallineato) includerebbe già il manifest
        # nuovo e l'append lo duplicherebbe.
        index = self._load_index()
        atomic_write(
            self.manifests_dir / f"{snapshot_id}.json",
            json.dumps(manifest.to_dict(), ensure_ascii=False),
        )
        index.append(manifest.summary())
        self._write_index(index)
        logger.info(
            "Snapshot {} created (trigger={}, files={}, bytes={})",
            snapshot_id[:12],
            trigger,
            manifest.file_count,
            manifest.total_bytes,
        )
        return manifest

    def _reusable_entries(self, parent: SnapshotManifest | None) -> dict[str, FileEntry]:
        """Le voci di *parent* il cui hash si può riusare senza rileggere il file.

        Si esclude chi è «a rischio di corsa» (il *racy git* di git): un file
        scritto poco prima dello snapshot precedente può essere stato riscritto
        subito dopo con la stessa dimensione **nello stesso tick dell'orologio
        del filesystem** — su alcuni kernel Android lo ``mtime`` avanza a
        jiffy, millisecondi — e la sua ``(size, mtime_ns)`` sarebbe identica a
        un contenuto diverso. Quei file si rileggono: sono pochi, quelli appena
        toccati. Il margine copre anche i filesystem a due secondi (FAT).

        E si esclude chi non ha più il blob: un hash riusato deve poter essere
        materializzato, o il ritorno indietro lo scoprirebbe troppo tardi.
        """
        if parent is None:
            return {}
        racy_from_ns = (parent.created_at_ms - _RACY_MARGIN_MS) * 1_000_000
        return {
            entry.path: entry
            for entry in parent.files
            if entry.mtime_ns < racy_from_ns and object_path(self.objects_dir, entry.hash).is_file()
        }

    @staticmethod
    def _compute_id(
        parent: str | None, trigger: str, created_at_ms: int, entries: list[FileEntry]
    ) -> str:
        payload = json.dumps(
            {
                "parent": parent,
                "trigger": trigger,
                "created_at_ms": created_at_ms,
                "files": sorted((e.path, e.hash) for e in entries),
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    # -- lettura ------------------------------------------------------------

    def list_snapshots(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Sommari degli snapshot, dal più recente al più vecchio."""
        index = sorted(self._load_index(), key=lambda s: s["created_at_ms"], reverse=True)
        return index[:limit] if limit else index

    def head_id(self) -> str | None:
        index = self.list_snapshots(limit=1)
        return index[0]["id"] if index else None

    def load_manifest(self, snapshot_id: str) -> SnapshotManifest:
        """Il manifest di *snapshot_id*.

        L'id arriva anche dalla WebUI (``stage_snapshot_restore``) e finisce in
        un percorso: uno che non ne ha la forma non e' uno snapshot
        (``FileNotFoundError``, come uno che manca) invece di un ``../`` che
        legge un JSON qualunque fuori dallo store. Un file che dentro dice di
        essere un altro snapshot non e' sano: ``ValueError``.
        """
        if not is_snapshot_id(snapshot_id):
            raise FileNotFoundError(f"no snapshot {snapshot_id[:80]!r}")
        data = json.loads((self.manifests_dir / f"{snapshot_id}.json").read_text("utf-8"))
        manifest = SnapshotManifest.from_dict(data)
        if manifest.id != snapshot_id:
            raise ValueError(
                f"snapshot id {manifest.id[:80]!r} does not match its file {snapshot_id[:12]}"
            )
        return manifest

    # -- restore ------------------------------------------------------------

    def restore_snapshot(self, snapshot_id: str, dest_dir: Path) -> SnapshotManifest:
        """Materializza uno snapshot dentro ``dest_dir`` (che viene creata).

        Non tocca mai ``self.root``: il ripristino del workspace vivo passa
        dal protocollo restore-marker al boot, non da qui.
        """
        manifest = self.load_manifest(snapshot_id)
        dest_dir = Path(dest_dir)
        # Tutte le voci prima di scrivere la prima: un manifest importato da un
        # ``.jbk`` e' dato non fidato, e un ``..`` usciva dallo staging. Il
        # confronto sul percorso risolto copre anche un link simbolico che
        # nessun controllo sulla stringa vedrebbe.
        root = dest_dir.resolve()
        for entry in manifest.files:
            reason = unsafe_entry_reason(entry.path, entry.hash)
            if reason is None:
                resolved = (root / Path(*entry.path.split("/"))).resolve()
                if not resolved.is_relative_to(root):
                    reason = f"unsafe path {entry.path!r} leaves the destination"
            if reason is not None:
                raise ValueError(f"snapshot {snapshot_id[:12]}: {reason}")
        dest_dir.mkdir(parents=True, exist_ok=True)
        for entry in manifest.files:
            content = get_blob(self.objects_dir, entry.hash)
            target = dest_dir / Path(*entry.path.split("/"))
            atomic_write(target, content, fsync_dir=False)
        return manifest

    # -- retention / gc -----------------------------------------------------

    def apply_retention(
        self,
        *,
        keep_recent: int,
        thin_after_days: int,
        max_age_days: int | None = None,
        now_ms: int | None = None,
    ) -> list[str]:
        """Applica la retention e ritorna gli id rimossi.

        Politica: i ``keep_recent`` più recenti sono intoccabili; gli snapshot
        più vecchi di ``thin_after_days`` giorni vengono assottigliati a uno
        al giorno (si tiene il più recente di ogni giornata); quelli oltre
        ``max_age_days`` giorni (se impostato; 0/None = per sempre) vengono
        eliminati del tutto.
        """
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        snapshots = self.list_snapshots()
        keep_ids = {s["id"] for s in snapshots[:keep_recent]}
        threshold_ms = now - thin_after_days * 86_400_000
        max_age_ms = max_age_days * 86_400_000 if max_age_days else None

        seen_days: set[int] = set()
        removed: list[str] = []
        for summary in snapshots[keep_recent:]:
            if max_age_ms is not None and summary["created_at_ms"] < now - max_age_ms:
                removed.append(summary["id"])
                continue
            if summary["created_at_ms"] >= threshold_ms:
                keep_ids.add(summary["id"])
                continue
            day = summary["created_at_ms"] // 86_400_000
            if day in seen_days:
                removed.append(summary["id"])
            else:
                seen_days.add(day)
                keep_ids.add(summary["id"])

        if not removed:
            return []
        for snapshot_id in removed:
            # Ultima guardia prima di un ``unlink``: l'id finisce in un percorso.
            if is_snapshot_id(snapshot_id):
                (self.manifests_dir / f"{snapshot_id}.json").unlink(missing_ok=True)
        self._write_index([s for s in self._load_index() if s["id"] in keep_ids])
        logger.info("Snapshot retention removed {} snapshot(s)", len(removed))
        return removed

    def gc(self) -> int:
        """Elimina i blob non referenziati da alcun manifest. Ritorna il conteggio."""
        referenced: set[str] = set()
        if self.manifests_dir.is_dir():
            for manifest_path in self.manifests_dir.glob("*.json"):
                try:
                    data = json.loads(manifest_path.read_text("utf-8"))
                except (OSError, ValueError):
                    continue
                for entry in data.get("files", []):
                    referenced.add(str(entry.get("hash", "")))

        removed = 0
        for blob_hash in list(iter_blob_hashes(self.objects_dir)):
            if blob_hash not in referenced:
                object_path(self.objects_dir, blob_hash).unlink(missing_ok=True)
                removed += 1
        if removed:
            logger.info("Snapshot gc removed {} orphan blob(s)", removed)
        return removed

    # -- index --------------------------------------------------------------

    def _load_index(self) -> list[dict[str, Any]]:
        """Carica l'indice; se corrotto o disallineato lo ricostruisce dai manifest.

        Percorso di SOLA lettura e privo di side-effect: su disallineamento o
        corruzione le summary vengono ricostruite in memoria dai manifest, senza
        mai scrivere ``index.json``. La persistenza dell'indice avviene solo nei
        percorsi writer (``create_snapshot``/``apply_retention``) sotto lock,
        così un lettore concorrente non può entrare in gara con un writer sul
        file temporaneo.
        """
        summaries: list[dict[str, Any]] | None = None
        try:
            data = json.loads(self.index_path.read_text("utf-8"))
            raw = data.get("snapshots", [])
            if isinstance(raw, list):
                # Una riga con un id che non e' un id non viene da questo motore:
                # fuori, prima che la retention la trasformi in un percorso.
                summaries = [
                    s for s in raw if isinstance(s, dict) and is_snapshot_id(s.get("id"))
                ]
        except FileNotFoundError:
            summaries = []
        except (OSError, ValueError):
            summaries = None

        manifest_count = (
            sum(1 for _ in self.manifests_dir.glob("*.json")) if self.manifests_dir.is_dir() else 0
        )
        if summaries is None or len(summaries) != manifest_count:
            summaries = self._summaries_from_manifests()
        return summaries

    def _summaries_from_manifests(self) -> list[dict[str, Any]]:
        """Ricostruisce le summary leggendo tutti i manifest (fonte di verità).

        Helper puro: non scrive nulla. I chiamanti writer decidono se persistere
        il risultato via ``_write_index``.
        """
        summaries: list[dict[str, Any]] = []
        if self.manifests_dir.is_dir():
            for manifest_path in self.manifests_dir.glob("*.json"):
                try:
                    manifest = SnapshotManifest.from_dict(
                        json.loads(manifest_path.read_text("utf-8"))
                    )
                except (OSError, ValueError, KeyError):
                    logger.warning("Skipping unreadable snapshot manifest {}", manifest_path.name)
                    continue
                # L'id e' il nome del file, o il manifest non entra nella storia:
                # la retention cancellerebbe il percorso che l'id scrive.
                if not is_snapshot_id(manifest.id) or manifest_path.stem != manifest.id:
                    logger.warning(
                        "Skipping snapshot manifest {}: its id is not its file name",
                        manifest_path.name,
                    )
                    continue
                summaries.append(manifest.summary())
        summaries.sort(key=lambda s: s["created_at_ms"])
        return summaries

    def _write_index(self, summaries: list[dict[str, Any]]) -> None:
        # Sotto lock: serializza i writer concorrenti sull'indice.
        with self._index_lock:
            atomic_write(
                self.index_path,
                json.dumps({"version": 1, "snapshots": summaries}, ensure_ascii=False),
                fsync_dir=False,
            )
