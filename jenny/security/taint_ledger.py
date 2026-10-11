"""Registro di provenienza: quali file ha scritto un lavoro non fidato.

Il taint di un subagent (``security.subagent_policy``) vive quanto il lavoro.
Un file però resta: un ``researcher`` salva una pagina in ``out/`` e il giorno
dopo un ``coder`` la legge. Senza registro il secondo lavoro risulterebbe
pulito, e il percorso «pagina ostile → codice eseguito» passerebbe da un file.
Qui si ricorda, per path relativo alla radice dell'installazione, chi ha
contaminato un file; leggerlo marca il lavoro che lo legge.

Forma: ``.jenny/taint_ledger.json`` = ``{"v": 1, "entries": {rel: {"t", "src"}}}``.
Una voce può essere una cartella (vale per tutto ciò che contiene). Le scritture
nel registro le fanno solo i lavori non fidati, quindi sono rare; la lettura è
una ricerca in un dict, in memoria dopo il primo accesso.

Chi pulisce una voce: una scrittura **completa** fatta da un lavoro fidato (i
byte ora sono suoi), una scrittura dell'utente dalla WebUI, l'installazione di
una bozza confermata. Una modifica parziale fatta da un lavoro fidato non
pulisce: i byte contaminati restano nel file.

Il file stesso è SECRET per i subagent (``subagent_policy``): un lavoro non
fidato non può cancellare le proprie tracce.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from loguru import logger

from jenny.utils.path import atomic_write

LEDGER_REL = (".jenny", "taint_ledger.json")

# Tetti: il registro cresce solo con le scritture di lavori non fidati, ma un
# lavoro che scrive migliaia di file non deve farne un file da megabyte. Oltre
# ``_COLLAPSE_AT`` voci nella stessa cartella resta una voce per la cartella;
# oltre ``_MAX_ENTRIES`` cadono le più vecchie.
_MAX_ENTRIES = 2000
_COLLAPSE_AT = 50


class TaintLedger:
    """Registro di una installazione. Thread-safe; carica alla prima richiesta."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._path = self._root.joinpath(*LEDGER_REL)
        self._lock = threading.Lock()
        self._entries: dict[str, dict[str, object]] | None = None

    # -- lettura -----------------------------------------------------------

    def _load(self) -> dict[str, dict[str, object]]:
        if self._entries is not None:
            return self._entries
        entries: dict[str, dict[str, object]] = {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("entries"), dict):
                entries = {
                    k: v for k, v in raw["entries"].items()
                    if isinstance(k, str) and isinstance(v, dict)
                }
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            # Un registro illeggibile si ricomincia vuoto: perdere la memoria di
            # un taint è il caso peggiore, ma non deve fermare i tool dei file.
            logger.warning("Taint ledger unreadable, starting empty: {}", exc)
        # Le voci di file spariti non servono più a nessuno.
        dropped = [k for k in entries if not self._root.joinpath(k).exists()]
        for k in dropped:
            entries.pop(k)
        self._entries = entries
        if dropped:
            self._save()
        return entries

    def source_of(self, rel: str) -> str | None:
        """La fonte che ha contaminato *rel* (o una sua cartella), o None."""
        with self._lock:
            entries = self._load()
            if not entries:
                return None
            parts = rel.split("/")
            for i in range(len(parts), 0, -1):
                hit = entries.get("/".join(parts[:i]))
                if hit is not None:
                    return str(hit.get("src") or "unknown")
        return None

    # -- scrittura ---------------------------------------------------------

    def record(self, rel: str, src: str) -> None:
        """Ricorda che *rel* è stato scritto da un lavoro non fidato."""
        with self._lock:
            entries = self._load()
            if self._covered(entries, rel):
                return
            entries[rel] = {"t": int(time.time()), "src": src}
            self._compact(entries, rel)
            self._save()

    def clear(self, rel: str) -> None:
        """Dimentica *rel* (non le sue cartelle: quelle coprono altri file)."""
        with self._lock:
            entries = self._load()
            if entries.pop(rel, None) is not None:
                self._save()

    def move(self, src_rel: str, dst_rel: str) -> None:
        """Una rinomina porta il taint con sé (e non lo lascia indietro)."""
        source = self.source_of(src_rel)
        if source is not None:
            self.record(dst_rel, source)

    @staticmethod
    def _covered(entries: dict[str, dict[str, object]], rel: str) -> bool:
        parts = rel.split("/")
        return any("/".join(parts[:i]) in entries for i in range(len(parts), 0, -1))

    @staticmethod
    def _compact(entries: dict[str, dict[str, object]], rel: str) -> None:
        parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
        if parent:
            prefix = parent + "/"
            siblings = [k for k in entries if k.startswith(prefix)]
            if len(siblings) > _COLLAPSE_AT:
                src = entries[rel].get("src")
                for k in siblings:
                    entries.pop(k)
                entries[parent] = {"t": int(time.time()), "src": src}
        if len(entries) > _MAX_ENTRIES:
            oldest = sorted(entries, key=lambda k: entries[k].get("t") or 0)
            for k in oldest[: len(entries) - _MAX_ENTRIES]:
                entries.pop(k)

    def _save(self) -> None:
        assert self._entries is not None
        try:
            atomic_write(
                self._path,
                json.dumps({"v": 1, "entries": self._entries}, ensure_ascii=False),
                fsync_dir=False,
            )
        except OSError as exc:
            logger.warning("Taint ledger write failed: {}", exc)


_LEDGERS: dict[str, TaintLedger] = {}
_LEDGERS_LOCK = threading.Lock()


def ledger_for(root: Path) -> TaintLedger:
    """Il registro dell'installazione *root* (uno per radice, per processo)."""
    key = str(Path(root).resolve())
    with _LEDGERS_LOCK:
        ledger = _LEDGERS.get(key)
        if ledger is None:
            ledger = _LEDGERS[key] = TaintLedger(Path(key))
        return ledger
