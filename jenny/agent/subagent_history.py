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

**La retention e volutamente corta: 6 lineage terminati per session key di
origine, TTL 6 ore.** Non e un compromesso: un resume rimanda al provider TUTTA
la storia del subagent, quindi un researcher con dodici web fetch rende caro ogni
follow-up. Una finestra corta spinge verso il percorso economico (rilancio dalla
spec), e oltre la finestra il degrado costa quasi nulla — l'output vero e
l'artefatto su disco, non il transcript. Dal 24/10/2026 un lavoro finito bene con
una storia lunga riprende *leggero* (:func:`light_resume_messages`): brief,
resoconto e file scritti, non tutto cio' che aveva letto.

Il cache del SessionManager e per-istanza: ogni chiave scritta qui viene
**invalidata subito** (vedi :meth:`SubagentHistoryStore.save`), altrimenti su un
telefono ogni conversazione di subagent resterebbe residente in RAM per la vita
del processo.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from jenny.session.history_meta import INJECTED_EVENT_META
from jenny.session.keys import SUBAGENT_SESSION_PREFIX, subagent_session_key
from jenny.utils.helpers import merge_message_content

if TYPE_CHECKING:  # pragma: no cover - solo per i type checker
    from jenny.session.manager import SessionManager

# TTL della storia riprendibile. Vedi il docstring del modulo: e una scelta di
# costo, non una limitazione tecnica.
HISTORY_TTL_S = 6 * 60 * 60

# Quante storie terminate restano per session key di origine. Erano 3, ma un
# passo di piano fa girare fino a 5 aiutanti normali piu uno breve
# (``subagent.PLAN_HELPER_SLOTS``) nella stessa sessione: con 3, i primi due
# fermati al budget perdevano la storia prima che Jenny potesse farli
# continuare. ``PLAN_HELPER_SLOTS + 1``, scritto qui come numero perche
# ``subagent`` importa questo modulo; un test li tiene allineati.
MAX_HISTORY_PER_ORIGIN = 6

#: Il tipo di fine che ha prodotto una storia, per la potatura: oltre il tetto
#: escono prima le storie di lavori finiti bene, poi quelle fermate al budget —
#: un lavoro finito si ritocca di rado, uno fermato a meta aspetta di continuare.
KIND_DONE = "done"
KIND_BUDGET = "budget"
#: Fotografia presa *mentre* l'aiutante lavora (``SubagentManager``, al piu una
#: ogni ``HISTORY_CHECKPOINT_INTERVAL_S``): se Android uccide il processo a meta
#: lavoro, e cio che permette di riprenderlo invece di rifarlo. La fine normale
#: del run la sostituisce con la sua (stessa chiave). Per la potatura vale quanto
#: una ``budget``: entrambe sono lavoro non finito.
KIND_INTERRUPTED = "interrupted"
_KINDS = (KIND_DONE, KIND_BUDGET, KIND_INTERRUPTED)

# Guardia sul numero di messaggi salvati: oltre questo tetto la storia viene
# potata dalla coda (``Session.enforce_file_cap``). Molto sopra a cio che un
# subagent produce davvero; serve solo a rendere impossibile il file patologico.
_MAX_HISTORY_MESSAGES = 600

__all__ = [
    "HISTORY_TTL_S",
    "KIND_BUDGET",
    "KIND_DONE",
    "KIND_INTERRUPTED",
    "MAX_HISTORY_PER_ORIGIN",
    "SubagentHistoryStore",
    "history_chars",
    "light_resume_messages",
]


@dataclass(slots=True)
class _Entry:
    """Voce dell'indice in RAM: a quale origine appartiene una storia e da quando."""

    lineage_id: str
    origin_key: str
    saved_at: float
    kind: str = KIND_DONE


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
        taint: tuple[str, ...] = (),
        kind: str = KIND_DONE,
    ) -> bool:
        """Salva la conversazione di un lineage. Ritorna ``True`` se persistita.

        Best-effort: un errore di serializzazione o di I/O viene loggato e non
        propaga — la storia e un'ottimizzazione del follow-up, non puo uccidere
        un subagent che ha appena finito il lavoro.

        E :meth:`write` seguita da :meth:`register`: chi non vuole fermare il
        loop sulla scrittura (il checkpoint durante il run) le chiama separate,
        la prima in un thread e la seconda sul loop.
        """
        moment = time.time() if now is None else now
        if not self.write(
            lineage_id, origin_key, messages, saved_at=moment, taint=taint, kind=kind,
        ):
            return False
        self.register(lineage_id, origin_key, saved_at=moment, kind=kind)
        return True

    def write(
        self,
        lineage_id: str,
        origin_key: str,
        messages: list[dict[str, Any]] | None,
        *,
        saved_at: float,
        taint: tuple[str, ...] = (),
        kind: str = KIND_DONE,
    ) -> bool:
        """La meta su disco di :meth:`save`: serializza e scrive il file, nient'altro.

        Non tocca l'indice ne pota, quindi si puo chiamare da un thread
        (``asyncio.to_thread``): del SessionManager usa solo ``save`` e
        ``invalidate``, un inserimento e un pop sul suo dict di cache. Chi la
        chiama a parte deve poi chiamare :meth:`register` sul loop, altrimenti
        la storia esiste su disco ma la potatura e ``has`` non la vedono fino al
        prossimo riavvio. Stesso contratto best-effort di :meth:`save`.
        """
        if self._sessions is None:
            return False
        clean = self._sanitize(messages)
        if not clean:
            return False
        moment = saved_at
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
                    # Fonti non fidate lette in questa conversazione: chi la
                    # riprende nasce contaminato (v. ``taint_of``).
                    "taint_sources": list(taint),
                    # Per la potatura dopo un riavvio (v. ``_reconcile``).
                    "stop_kind": kind,
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
        return True

    def register(
        self, lineage_id: str, origin_key: str, *, saved_at: float, kind: str = KIND_DONE,
    ) -> None:
        """Mette nell'indice una storia appena scritta da :meth:`write`, e pota."""
        if self._sessions is None:
            return
        self._reconcile()
        self._index[lineage_id] = _Entry(lineage_id, origin_key, saved_at, kind)
        self.prune(now=saved_at)

    def has(self, lineage_id: str, *, now: float | None = None) -> bool:
        """C'e una storia di *lineage_id* entro TTL, secondo l'indice in RAM.

        Una domanda da snapshot, che si pubblica ogni pochi secondi: niente
        letture di file. L'indice segue ogni ``save``/``drop``/potatura, e un
        file rotto lo scopre solo :meth:`load` — che e cio che una ripresa fa
        comunque prima di partire.
        """
        if self._sessions is None:
            return False
        self._reconcile()
        entry = self._index.get(lineage_id)
        if entry is None:
            return False
        moment = time.time() if now is None else now
        return self.ttl_s <= 0 or moment - entry.saved_at <= self.ttl_s

    def file_size(self, lineage_id: str) -> int | None:
        """Byte del file della storia di *lineage_id*, o ``None`` se non c'e.

        Un ``stat``, nessuna lettura: il checkpoint lo usa per allungare il
        proprio intervallo quando la storia diventa grande.
        """
        path = self._path_for(subagent_session_key(lineage_id))
        if path is None:
            return None
        try:
            return path.stat().st_size
        except OSError:
            return None

    def entry_of(self, lineage_id: str, *, now: float | None = None) -> tuple[str, float] | None:
        """``(kind, saved_at)`` della storia di *lineage_id* entro TTL, o ``None``.

        Dall'indice in RAM, come :meth:`has`: lo chiede il boot per sapere se un
        aiutante morto col processo ha lasciato una fotografia da cui ripartire.
        """
        if not self.has(lineage_id, now=now):
            return None
        entry = self._index[lineage_id]
        return entry.kind, entry.saved_at

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

    def taint_of(self, lineage_id: str) -> tuple[str, ...]:
        """Fonti non fidate della storia salvata, ``()`` se pulita o assente.

        Una ripresa rimette in contesto tutto ciò che quel lavoro aveva letto:
        se c'erano pagine web, il lavoro ripreso è non fidato quanto il primo.
        """
        if self._sessions is None:
            return ()
        try:
            data = self._sessions.read_session_file(subagent_session_key(lineage_id))
        except Exception:  # noqa: BLE001 — illeggibile: ``load`` la scarta comunque
            return ()
        metadata = data.get("metadata") if isinstance(data, dict) else None
        raw = metadata.get("taint_sources") if isinstance(metadata, dict) else None
        if not isinstance(raw, list):
            return ()
        return tuple(x for x in raw if isinstance(x, str))[:8]

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
            excess = len(entries) - self.max_per_origin
            if excess <= 0:
                continue
            # Prima le storie di lavori finiti bene, poi quelle di lavori non
            # finiti (fermate al budget, o fotografate a meta da un checkpoint:
            # stesso rango); dentro ciascun gruppo, la piu vecchia.
            entries.sort(key=lambda e: (e.kind != KIND_DONE, e.saved_at))
            dropped.extend(e.lineage_id for e in entries[:excess])

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
            kind = payload.get("stop_kind") if isinstance(payload, dict) else None
            if not isinstance(saved_at, (int, float)) or isinstance(saved_at, bool):
                self.drop(lineage_id)
                continue
            self._index[lineage_id] = _Entry(
                lineage_id,
                origin if isinstance(origin, str) else "",
                float(saved_at),
                kind if kind in _KINDS else KIND_DONE,
            )


# ---------------------------------------------------------------------------
# misura e ripresa leggera
# ---------------------------------------------------------------------------


def history_chars(history: list[dict[str, Any]]) -> int:
    """Quanto pesa una storia salvata, in caratteri del suo JSON.

    Il ragionamento non conta: ``reasoning_content`` non torna al provider in una
    ripresa che lo ha gia' chiuso, e conterlo farebbe sembrare lunga una storia
    che non lo e'. La stessa unita' per il tetto delle riprese
    (``SubagentManager.CONTINUE_MAX_HISTORY_CHARS``) e per la soglia della
    ripresa leggera, cosi' i due numeri si confrontano.
    """
    return sum(
        len(json.dumps({k: v for k, v in m.items() if k != "reasoning_content"},
                       ensure_ascii=False, default=str))
        for m in history
    )


#: Gli strumenti con cui un aiutante scrive, per la nota «cosa hai scritto» della
#: ripresa leggera. Nomi e argomenti veri, non indovinati: ``write_file`` ed
#: ``edit_file`` (``tools/filesystem.py``, argomento ``path``), ``apply_patch``
#: (``edits[].path``), ``notebook action=remove_pages`` (``pages``) e ``board
#: action=write_drawer`` (``card``), gli ultimi due del ``notebook-editor``.
_PATH_WRITE_TOOLS = frozenset({"write_file", "edit_file"})

# Voci per riga della nota: un aiutante di quaderno ne tocca 5-15; oltre, la
# nota diventerebbe lei il contesto che la ripresa leggera vuole togliere.
_MAX_NOTE_ITEMS = 40

_LIGHT_RESUME_NOTE = (
    "[Context note: your earlier work was condensed to save tokens. You still have "
    "your brief and your report; the files you read and the tool calls you made are "
    "no longer here. {files}Before you change a file, read it again: what you remember "
    "of it may be out of date.]"
)


def _text_of(content: Any) -> str:
    """Il testo di un ``content``, stringa o lista di blocchi."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(block.get("text") or "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def _call_arguments(call: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Nome e argomenti di una tool call in forma OpenAI; ``("", {})`` se illeggibile."""
    function = call.get("function")
    if not isinstance(function, dict):
        return "", {}
    raw = function.get("arguments")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            raw = None
    return str(function.get("name") or ""), raw if isinstance(raw, dict) else {}


def _written_items(history: list[dict[str, Any]]) -> tuple[list[str], list[str], list[str]]:
    """File scritti, pagine tolte e cassetti riscritti, dalle tool call riuscite.

    Una chiamata il cui risultato comincia con «Error» non ha scritto niente e
    non entra: la nota direbbe all'aiutante di aver cambiato un file che non ha
    toccato. Una chiamata senza risultato (storia potata) conta, nel dubbio.
    """
    failed = {
        str(m.get("tool_call_id"))
        for m in history
        if m.get("role") == "tool"
        and isinstance(m.get("content"), str)
        and m["content"].lstrip().startswith("Error")
    }
    files: list[str] = []
    removed: list[str] = []
    drawers: list[str] = []

    def _add(bucket: list[str], value: Any) -> None:
        if isinstance(value, str) and value.strip() and value.strip() not in bucket:
            bucket.append(value.strip())

    for m in history:
        if m.get("role") != "assistant":
            continue
        for call in m.get("tool_calls") or []:
            if not isinstance(call, dict) or str(call.get("id")) in failed:
                continue
            name, args = _call_arguments(call)
            if name in _PATH_WRITE_TOOLS:
                _add(files, args.get("path"))
            elif name == "apply_patch" and not args.get("dry_run"):
                for edit in args.get("edits") or []:
                    if isinstance(edit, dict):
                        _add(files, edit.get("path"))
            elif name == "notebook" and args.get("action") == "remove_pages":
                for page in args.get("pages") or []:
                    _add(removed, page)
            elif name == "board" and args.get("action") == "write_drawer":
                _add(drawers, args.get("card"))
    return files, removed, drawers


def _note_line(label: str, items: list[str]) -> str:
    shown = items[:_MAX_NOTE_ITEMS]
    more = f" (and {len(items) - len(shown)} more)" if len(items) > len(shown) else ""
    return f"{label}: {', '.join(shown)}{more}. "


def light_resume_messages(
    history: list[dict[str, Any]], message: str,
) -> list[dict[str, Any]] | None:
    """La semina di una ripresa leggera: la storia senza il lavoro di mezzo.

    Resta lo scheletro del dialogo — il system prompt salvato, il brief, ogni
    messaggio di Jenny e ogni resoconto con cui l'aiutante ha chiuso — e sparisce
    cio' che sta fra l'uno e l'altro: le tool call, i loro risultati, i testi
    intermedi e le righe sintetiche del runner (``INJECTED_EVENT_META``). In coda
    una nota con i file che l'aiutante ha scritto, ricavata dalle sue tool call,
    poi il messaggio nuovo. Lo scheletro, e non solo «brief + ultimo resoconto»:
    alla seconda ripresa leggera dello stesso aiutante la prima nota, il primo
    messaggio di Jenny e il primo resoconto sono righe di testo, e restano.

    La forma e' sempre ``[system,] user, assistant, …, user``: nessuna tool call
    senza risultato, nessun risultato senza la sua chiamata, ruoli consecutivi
    uguali fusi in uno. ``None`` se la storia non ha un resoconto da cui
    ripartire: il chiamante riprende allora con la storia intera, come prima.
    """
    seed: list[dict[str, Any]] = []
    rest = history
    if history and history[0].get("role") == "system":
        seed.append(dict(history[0]))
        rest = history[1:]
    turns: list[dict[str, Any]] = []

    def _push(role: str, content: Any) -> None:
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"] = merge_message_content(turns[-1]["content"], content)
        else:
            turns.append({"role": role, "content": content})

    for m in rest:
        role = m.get("role")
        if role == "user":
            if m.get(INJECTED_EVENT_META):
                continue
            content = m.get("content")
            if isinstance(content, list) or _text_of(content).strip():
                _push("user", content)
        elif role == "assistant" and not m.get("tool_calls"):
            text = _text_of(m.get("content")).strip()
            if text:
                _push("assistant", text)
    # Serve un brief in testa e almeno un resoconto: senza, non c'e' nulla da
    # condensare e la storia intera e' l'unica continuazione onesta.
    if not turns or turns[0]["role"] != "user" or not any(
        t["role"] == "assistant" for t in turns
    ):
        return None

    files, removed, drawers = _written_items(history)
    listed = ""
    if files:
        listed += _note_line("Files you wrote or edited", files)
    if removed:
        listed += _note_line("Pages you removed", removed)
    if drawers:
        listed += _note_line("Drawers you rewrote", drawers)
    if not listed:
        listed = "No file writes were found in that work. "
    _push("user", _LIGHT_RESUME_NOTE.format(files=listed) + "\n\n" + message)
    return seed + turns
