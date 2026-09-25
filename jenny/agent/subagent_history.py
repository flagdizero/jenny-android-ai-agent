"""Retention Tier-2 dei subagent: la conversazione, per poterla continuare.

Il Tier-1 (:mod:`jenny.agent.subagent_records`) tiene la *spec* rieseguibile e
l'esito. Qui vive l'altra meta: i messaggi accumulati dal subagent, cioe cio che
serve per rispondere a "no, cambia il titolo" senza ri-specificare il lavoro da
zero. ``AgentRunResult.messages`` porta gia l'intera conversazione, quindi
riprendere e meccanicamente banale: si risalva e si rilancia con
``initial_messages = history + [user(follow-up)]``.

Non c'e un layer di persistenza nuovo: la storia e una sessione sotto la chiave
``subagent:<lineage_id>`` gestita dal :class:`~jenny.session.manager.SessionManager`
del gateway, cosi scritture atomiche, riparazione di un file troncato e
``enforce_file_cap`` arrivano gratis.

**La retention e volutamente cortissima: 3 lineage terminati per session key di
origine, TTL 6 ore.** Non e un compromesso: un resume rimanda al provider TUTTA
la storia del subagent, quindi un researcher con dodici web fetch rende caro ogni
follow-up. Una finestra corta spinge verso il percorso economico (rilancio dalla
spec), e oltre la finestra il degrado costa quasi nulla — l'output vero e
l'artefatto su disco, non il transcript.

Il cache del SessionManager e per-istanza: ogni chiave scritta qui viene
**invalidata subito** (vedi :meth:`SubagentHistoryStore.save`), altrimenti su un
telefono ogni conversazione di subagent resterebbe residente in RAM per la vita
del processo.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from jenny.session.keys import SUBAGENT_SESSION_PREFIX, subagent_session_key

if TYPE_CHECKING:  # pragma: no cover - solo per i type checker
    from jenny.session.manager import SessionManager

# TTL della storia riprendibile. Vedi il docstring del modulo: e una scelta di
# costo, non una limitazione tecnica.
HISTORY_TTL_S = 6 * 60 * 60

# Quante storie terminate restano per session key di origine.
MAX_HISTORY_PER_ORIGIN = 3

# Guardia sul numero di messaggi salvati: oltre questo tetto la storia viene
# potata dalla coda (``Session.enforce_file_cap``). Molto sopra a cio che un
# subagent produce davvero; serve solo a rendere impossibile il file patologico.
_MAX_HISTORY_MESSAGES = 600

__all__ = [
    "HISTORY_TTL_S",
    "MAX_HISTORY_PER_ORIGIN",
    "SubagentHistoryStore",
]


@dataclass(slots=True)
class _Entry:
    """Voce dell'indice in RAM: a quale origine appartiene una storia e da quando."""

    lineage_id: str
    origin_key: str
    saved_at: float


class SubagentHistoryStore:
    """Storia riprendibile dei subagent, come sessioni ``subagent:<lineage_id>``.

    ``sessions=None`` disabilita lo store: ``load`` ritorna sempre ``None`` e un
    ``subagent_send`` degrada al rilancio dalla spec. E deliberato che il default
    sia "disabilitato" invece di costruire un SessionManager: due istanze sulla
    stessa directory sono due cache divergenti sugli stessi file.
    """

    def __init__(
        self,
        sessions: "SessionManager | None" = None,
        *,
        ttl_s: float = HISTORY_TTL_S,
        max_per_origin: int = MAX_HISTORY_PER_ORIGIN,
    ) -> None:
        self._sessions = sessions
        self.ttl_s = float(ttl_s)
        self.max_per_origin = max(1, int(max_per_origin))
        # Indice in RAM lineage -> origine/istante: serve solo alla potatura per
        # origine. Piccolo per costruzione (max_per_origin voci per sessione).
        self._index: dict[str, _Entry] = {}
        self._reconciled = False

    @property
    def enabled(self) -> bool:
        return self._sessions is not None

    # ------------------------------------------------------------------
    # write
    # ------------------------------------------------------------------

    def save(
        self,
        lineage_id: str,
        origin_key: str,
        messages: list[dict[str, Any]] | None,
        *,
        now: float | None = None,
    ) -> bool:
        """Salva la conversazione di un lineage. Ritorna ``True`` se persistita.

        Best-effort: un errore di serializzazione o di I/O viene loggato e non
        propaga — la storia e un'ottimizzazione del follow-up, non puo uccidere
        un subagent che ha appena finito il lavoro.
        """
        if self._sessions is None:
            return False
        clean = self._sanitize(messages)
        if not clean:
            return False
        moment = time.time() if now is None else now
        key = subagent_session_key(lineage_id)
        try:
            from jenny.session.manager import Session

            session = Session(
                key=key,
                messages=clean,
                metadata={
                    "subagent_lineage_id": lineage_id,
                    "origin_session_key": origin_key,
                    "saved_at": moment,
                },
            )
            session.enforce_file_cap(limit=_MAX_HISTORY_MESSAGES)
            self._sessions.save(session)
        except Exception as e:  # noqa: BLE001 — vedi docstring
            logger.warning("Subagent history save failed for lineage {}: {}", lineage_id, e)
            self._invalidate(key)
            return False
        # Invalidazione immediata: lo store scrive e legge da disco, non tiene
        # mai una sessione di subagent viva nel cache del SessionManager.
        self._invalidate(key)
        self._reconcile()
        self._index[lineage_id] = _Entry(lineage_id, origin_key, moment)
        self.prune(now=moment)
        return True

    # ------------------------------------------------------------------
    # read
    # ------------------------------------------------------------------

    def load(self, lineage_id: str, *, now: float | None = None) -> list[dict[str, Any]] | None:
        """Storia riprendibile del lineage, o ``None`` se non c'e/scaduta/corrotta.

        Ogni esito diverso da "storia valida entro TTL" ritorna ``None``: il
        chiamante degrada al rilancio dalla spec, che e sempre possibile. Una
        storia illeggibile viene anche eliminata, per non rileggerla ogni volta.
        """
        if self._sessions is None:
            return None
        key = subagent_session_key(lineage_id)
        try:
            data = self._sessions.read_session_file(key)
        except Exception as e:  # noqa: BLE001 — un file rotto degrada, non solleva
            logger.warning("Subagent history unreadable for lineage {}: {}", lineage_id, e)
            self.drop(lineage_id)
            return None
        if not isinstance(data, dict):
            return None

        metadata = data.get("metadata")
        saved_at = metadata.get("saved_at") if isinstance(metadata, dict) else None
        if not isinstance(saved_at, (int, float)) or isinstance(saved_at, bool):
            # Senza istante di salvataggio non si puo applicare la TTL: la
            # storia e inattendibile, quindi vale come scaduta.
            logger.info("Subagent history for lineage {} has no timestamp; dropping", lineage_id)
            self.drop(lineage_id)
            return None
        moment = time.time() if now is None else now
        if self.ttl_s > 0 and moment - float(saved_at) > self.ttl_s:
            logger.info("Subagent history for lineage {} expired; dropping", lineage_id)
            self.drop(lineage_id)
            return None

        messages = self._sanitize(data.get("messages"))
        if not messages:
            self.drop(lineage_id)
            return None
        return messages

    # ------------------------------------------------------------------
    # delete / prune
    # ------------------------------------------------------------------

    def drop(self, lineage_id: str) -> None:
        """Elimina la storia di un lineage (file + cache + indice)."""
        self._index.pop(lineage_id, None)
        if self._sessions is None:
            return
        key = subagent_session_key(lineage_id)
        path = self._path_for(key)
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError as e:
                logger.warning("Could not delete subagent history {}: {}", path, e)
        self._invalidate(key)

    def prune(self, *, now: float | None = None) -> list[str]:
        """Applica TTL e tetto per origine. Ritorna i lineage eliminati."""
        moment = time.time() if now is None else now
        dropped: list[str] = []

        if self.ttl_s > 0:
            for entry in list(self._index.values()):
                if moment - entry.saved_at > self.ttl_s:
                    dropped.append(entry.lineage_id)

        by_origin: dict[str, list[_Entry]] = {}
        for entry in self._index.values():
            if entry.lineage_id in dropped:
                continue
            by_origin.setdefault(entry.origin_key, []).append(entry)
        for entries in by_origin.values():
            if len(entries) <= self.max_per_origin:
                continue
            entries.sort(key=lambda e: e.saved_at, reverse=True)
            dropped.extend(e.lineage_id for e in entries[self.max_per_origin:])

        for lineage_id in dropped:
            self.drop(lineage_id)
        return dropped

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _sanitize(messages: Any) -> list[dict[str, Any]]:
        """Tiene solo i messaggi con forma utilizzabile come storia LLM.

        Un file ricostruito da una riga troncata puo contenere qualunque cosa:
        se non resta nulla di valido il chiamante vede una storia vuota e
        rilancia dalla spec.
        """
        if not isinstance(messages, list):
            return []
        return [
            m for m in messages
            if isinstance(m, dict) and isinstance(m.get("role"), str) and m["role"]
        ]

    def _sessions_dir(self) -> Path | None:
        sessions = self._sessions
        if sessions is None:
            return None
        try:
            return Path(sessions.sessions_dir)
        except (TypeError, AttributeError):  # doppio di test senza sessions_dir
            return None

    def _path_for(self, key: str) -> Path | None:
        directory = self._sessions_dir()
        if directory is None or self._sessions is None:
            return None
        try:
            return directory / f"{self._sessions.safe_key(key)}.jsonl"
        except (TypeError, AttributeError):
            return None

    def _invalidate(self, key: str) -> None:
        sessions = self._sessions
        if sessions is None:
            return
        try:
            sessions.invalidate(key)
        except Exception:  # noqa: BLE001 — la pulizia del cache non propaga
            logger.debug("Session cache invalidate failed for {}", key)

    def _reconcile(self) -> None:
        """Registra una volta per processo le storie lasciate da un processo morto.

        Su Android il gateway viene ucciso spesso: senza questa passata i file
        ``subagent_*.jsonl`` di ieri non sarebbero nell'indice e nessuna potatura
        li toccherebbe piu. Le storie senza timestamp leggibile vengono eliminate
        subito: sono comunque irriprendibili (vedi :meth:`load`).
        """
        if self._reconciled or self._sessions is None:
            return
        self._reconciled = True
        directory = self._sessions_dir()
        if directory is None:
            return
        try:
            stem_prefix = self._sessions.safe_key(SUBAGENT_SESSION_PREFIX)
        except (TypeError, AttributeError):
            return
        try:
            paths = sorted(directory.glob(f"{stem_prefix}*.jsonl"))
        except OSError as e:
            logger.warning("Subagent history dir unreadable {}: {}", directory, e)
            return
        reader = getattr(self._sessions, "read_session_metadata", None)
        if not callable(reader):
            return
        for path in paths:
            lineage_id = path.stem[len(stem_prefix):]
            if not lineage_id or lineage_id in self._index:
                continue
            try:
                meta = reader(subagent_session_key(lineage_id))
            except Exception:  # noqa: BLE001 — file rotto: si elimina, non si alza
                meta = None
            payload = meta.get("metadata") if isinstance(meta, dict) else None
            saved_at = payload.get("saved_at") if isinstance(payload, dict) else None
            origin = payload.get("origin_session_key") if isinstance(payload, dict) else None
            if not isinstance(saved_at, (int, float)) or isinstance(saved_at, bool):
                self.drop(lineage_id)
                continue
            self._index[lineage_id] = _Entry(
                lineage_id,
                origin if isinstance(origin, str) else "",
                float(saved_at),
            )
