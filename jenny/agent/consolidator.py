"""Consolidator — memoria a due fasi (estratto da memory.py).

Consolida la history di sessione sotto il **lock rientrante per-task**
condiviso (invariante Fase 1: turno e consolidation condividono lo stesso
dominio di lock). Spostato verbatim da ``memory.py``; ``MemoryStore`` e le
costanti d'archivio sono importate da lì. ``memory`` re-esporta ``Consolidator``
in coda per preservare l'API (``from jenny.agent.memory import Consolidator``).
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from loguru import logger

from jenny.agent.memory import (
    _ARCHIVE_SUMMARY_MAX_CHARS,
    _RAW_ARCHIVE_MAX_CHARS,
    MemoryStore,
    iter_fact_lines,
)
from jenny.security.workspace_access import WorkspaceScopeResolver
from jenny.session.keys import PROJECT_SESSION_PREFIX, is_project_session_key
from jenny.session.manager import Session
from jenny.utils.helpers import (
    channel_delivery_aware_user_start,
    estimate_message_tokens,
    estimate_prompt_tokens_chain,
    find_legal_message_start,
    recent_message_start_index,
    truncate_text,
    truncate_text_to_tokens,
)
from jenny.utils.prompt_templates import render_template
from jenny.utils.wiki_paths import discover_wiki_roots

if TYPE_CHECKING:
    from jenny.agent.session_locks import ReentrantSessionLock, SessionLocks
    from jenny.providers.base import LLMProvider
    from jenny.session.manager import SessionManager


# Dove un progetto tiene la copia grezza di quel che la compattazione gli
# rimuove dalla sessione. Sotto ``raw/`` e **accanto** a ``raw/journal/``:
#
# - ``raw/`` perché una conversazione è materiale grezzo per definizione — è la
#   stessa ragione per cui il diario sta lì (v. ``wiki_paths.JOURNAL_DIRNAME``) —
#   e perché è il ramo che nessun sottosistema cammina: albero, grafo, ricerca e
#   impronta guardano solo ``wiki/``, quindi una cartella qui non chiede a
#   nessuno di imparare a non trattarla da pagina.
# - **Accanto** e non *dentro* il diario, che è la scelta vera: una pagina di
#   diario è un fatto per riga, append-only, con un cursore di righe che il
#   giardiniere avanza. Rovesciarci dentro una conversazione intera gli farebbe
#   promuovere un dump a pagina, cioè romperebbe l'unico consumatore del diario
#   per salvare dei byte che non gli servono.
#
# Il lint della wiki non ci passa: chiede un riassunto solo a
# ``raw/articles|papers|notes`` e controlla la forma solo di ``raw/journal/``.
_PROJECT_COMPACTED_SUBDIR = "raw/compacted"

# Il default della sottocartella dei progetti. Letto dal risolutore del turno
# invece di riscritto, perché è la stessa cartella: due letterali ``"wikis"``
# sarebbero due cose da tenere allineate, e la conseguenza di disallinearle è
# che questa copia finisce dove nessuno la cerca. Resta solo un default: chi
# costruisce il Consolidator passa la ``wikis_dir`` configurata, perché su
# un'installazione che l'ha cambiata cercare qui non troverebbe il progetto.
_PROJECTS_SUBDIR = WorkspaceScopeResolver.projects_subdir


def _estimate_tokens(text: str) -> int:
    """Stima in token, con la stessa convenzione di ``truncate_text_to_tokens``.

    Quattro caratteri per token, e conta che sia la *stessa* convenzione del
    troncatore: se qui si stimasse più fine, il budget sottratto e il budget
    applicato divergerebbero, e la differenza si manifesterebbe come una
    richiesta fuori finestra invece che come un troncamento.
    """
    return len(text) // 4


class Consolidator:
    """Lightweight consolidation: summarizes evicted messages into history.jsonl."""

    _MAX_CONSOLIDATION_ROUNDS = 5

    _SAFETY_BUFFER = 1024  # extra headroom for tokenizer estimation drift

    def __init__(
        self,
        store: MemoryStore,
        provider: LLMProvider,
        model: str,
        sessions: SessionManager,
        context_window_tokens: int,
        build_messages: Callable[..., list[dict[str, Any]]],
        get_tool_definitions: Callable[[], list[dict[str, Any]]],
        max_completion_tokens: int = 4096,
        consolidation_ratio: float = 0.5,
        session_locks: "SessionLocks | None" = None,
        projects_subdir: str = _PROJECTS_SUBDIR,
    ):
        self.store = store
        self.provider = provider
        self.model = model
        self.sessions = sessions
        self.context_window_tokens = context_window_tokens
        self.max_completion_tokens = max_completion_tokens
        self.consolidation_ratio = consolidation_ratio
        self._build_messages = build_messages
        self._get_tool_definitions = get_tool_definitions
        # La ``config.wiki.wikis_dir`` viva, non il default: la copia della coda
        # di un progetto si scrive dentro la cartella del progetto, e su
        # un'installazione che ha spostato i progetti il default cercherebbe
        # dove non c'è niente — cioè rifiuterebbe di compattare per sempre.
        self._projects_subdir = projects_subdir
        # Dominio di lock condiviso col turno (AgentLoop). Se non iniettato (es.
        # costruzione diretta nei test) ne crea uno privato: la consolidation
        # resta serializzata con sé stessa, ma perde la mutua esclusione col
        # turno — che in produzione arriva sempre iniettata dall'AgentLoop.
        from jenny.agent.session_locks import SessionLocks

        self._session_locks: SessionLocks = session_locks or SessionLocks()

    def set_provider(
        self,
        provider: LLMProvider,
        model: str,
        context_window_tokens: int,
    ) -> None:
        self.provider = provider
        self.model = model
        self.context_window_tokens = context_window_tokens
        self.max_completion_tokens = provider.generation.max_tokens

    def get_lock(self, session_key: str) -> "ReentrantSessionLock":
        """Return the shared per-session lock (same object the turn uses)."""
        return self._session_locks.get(session_key)

    def pick_consolidation_boundary(
        self,
        session: Session,
        tokens_to_remove: int,
    ) -> tuple[int, int] | None:
        """Pick a user-turn boundary that removes enough old prompt tokens."""
        start = session.last_consolidated
        if start >= len(session.messages) or tokens_to_remove <= 0:
            return None

        removed_tokens = 0
        last_boundary: tuple[int, int] | None = None
        for idx in range(start, len(session.messages)):
            message = session.messages[idx]
            if idx > start and message.get("role") == "user":
                last_boundary = (idx, removed_tokens)
                if removed_tokens >= tokens_to_remove:
                    return last_boundary
            removed_tokens += estimate_message_tokens(message)

        return last_boundary

    @staticmethod
    def _full_unconsolidated_history(session: Session) -> list[dict[str, Any]]:
        """Return the whole unconsolidated tail for consolidation decisions."""
        unconsolidated_count = len(session.messages) - session.last_consolidated
        if unconsolidated_count <= 0:
            return []
        return session.get_history(
            max_messages=unconsolidated_count,
            include_timestamps=True,
        )

    @staticmethod
    def _replay_overflow_boundary(
        session: Session,
        replay_max_messages: int | None,
    ) -> int | None:
        if not replay_max_messages or replay_max_messages <= 0:
            return None
        tail = list(enumerate(session.messages[session.last_consolidated:], session.last_consolidated))
        if len(tail) <= replay_max_messages:
            return None

        tail_messages = [message for _idx, message in tail]
        start_idx = recent_message_start_index(
            tail_messages,
            replay_max_messages,
            extend_to_user=True,
        )
        sliced = tail[start_idx:]
        user_start = channel_delivery_aware_user_start(
            [message for _idx, message in sliced]
        )
        if user_start is not None:
            sliced = sliced[user_start:]

        legal_start = find_legal_message_start([message for _idx, message in sliced])
        if legal_start:
            sliced = sliced[legal_start:]
        if not sliced:
            return len(session.messages)

        first_visible_idx = sliced[0][0]
        if first_visible_idx <= session.last_consolidated:
            return None
        return first_visible_idx

    async def _consolidate_replay_overflow(
        self,
        session: Session,
        replay_max_messages: int | None,
    ) -> str | None:
        """Archive messages that would be hidden by the replay message window."""
        end_idx = self._replay_overflow_boundary(session, replay_max_messages)
        if end_idx is None:
            return None
        chunk = session.messages[session.last_consolidated:end_idx]
        if not chunk:
            return None
        logger.info(
            "Replay-window consolidation for {}: chunk={} msgs, replay_max={}",
            session.key,
            len(chunk),
            replay_max_messages,
        )
        summary = await self.archive(chunk, session_key=session.key)
        session.last_consolidated = end_idx
        self.sessions.save(session)
        return summary

    def _persist_last_summary(self, session: Session, summary: str | None) -> None:
        if summary and summary != "(nothing)":
            session.metadata["_last_summary"] = {
                "text": summary,
                "last_active": session.updated_at.isoformat(),
            }
            self.sessions.save(session)

    def estimate_session_prompt_tokens(
        self,
        session: Session,
    ) -> tuple[int, str]:
        """Estimate prompt size from the full unconsolidated session tail."""
        history = self._full_unconsolidated_history(session)
        channel, chat_id = (session.key.split(":", 1) if ":" in session.key else (None, None))
        # Include archived summary in estimation so the budget accounts for it.
        meta = session.metadata.get("_last_summary")
        summary = meta.get("text") if isinstance(meta, dict) else None
        probe_messages = self._build_messages(
            history=history,
            current_message="[token-probe]",
            channel=channel,
            chat_id=chat_id,
            sender_id=None,
            session_summary=summary,
            session_metadata=session.metadata,
            session_key=session.key,
            workspace=self._probe_workspace(session.key),
        )
        return estimate_prompt_tokens_chain(
            self.provider,
            self.model,
            probe_messages,
            self._get_tool_definitions(),
        )

    @property
    def _workspace_scopes(self) -> WorkspaceScopeResolver:
        """**Il risolutore del turno, non una seconda aritmetica di percorsi.**

        "Quale cartella" ha un solo proprietario:
        ``WorkspaceScopeResolver.for_project``, la stessa chiamata che
        ``AgentLoop`` fa per le sessioni di progetto. Comporre qui un
        ``workspace / wikis_dir / nome`` a mano vorrebbe dire una seconda
        risposta alla stessa domanda, con la sua guardia sui ``..`` da riscrivere
        e da tenere allineata (la lezione di ``WorkspaceScope.write_root``).

        Gli ingressi sono i due che questa classe ha gia', e sono gli **unici
        due** che ``for_project`` legge: la radice dello store e la ``wikis_dir``
        configurata. ``default_restrict_to_workspace`` non entra in quel percorso
        — dentro un progetto lo scope e' ``restricted`` per definizione — quindi
        non c'e' un terzo dato da tenere allineato.

        Proprieta' e non attributo del costruttore, per la stessa ragione per cui
        ``_project_dump_dir`` legge ``self.store.workspace`` al momento dell'uso:
        ``store`` puo' essere un doppio, e costringerlo a esporre ``workspace``
        solo per costruire questo oggetto trasformerebbe una dipendenza di una
        funzione in una dipendenza di **tutta** la classe.
        """
        return WorkspaceScopeResolver(
            default_workspace=self.store.workspace,
            default_restrict_to_workspace=True,
            projects_subdir=self._projects_subdir,
        )

    def _probe_workspace(self, session_key: str) -> Path | None:
        """La radice su cui la sonda costruisce il prompt. **T3.8.**

        Fino a T3.8 la sonda non passava ``workspace``, quindi per una sessione
        ``project:*`` costruiva il prompt sulla radice dell'installazione: il
        blocco di progetto — mappa, pagine, ``AGENTS.md`` del progetto — restava
        **fuori dalla stima**. La conseguenza non era un numero curioso: la
        decisione di autocompattazione e ``/status`` leggevano un valore
        sistematicamente basso proprio sulle sessioni il cui prompt e' cresciuto
        di piu', cioe' la compattazione arrivava tardi dove serviva prima.

        La cartella si chiede al **risolutore del turno**, con la stessa chiamata
        che ``AgentLoop`` usa per le sessioni di progetto
        (``workspace_scopes.for_project``): la sonda deve misurare il prompt che
        il turno costruira', e due strade per la stessa cartella divergono.

        ``for_turn`` non serve e sarebbe sbagliato: vuole il *canale* del
        messaggio, e qui non c'e' un messaggio — il canale che questa classe
        ricava dalla chiave (``"project"``) non e' quello scoped, quindi
        ``for_turn`` ricadrebbe sulla radice di default, cioe' sul difetto.

        ``None`` fuori dai progetti, che e' il default di ``build_messages``:
        sessione personale e sessioni interne (cron, Dream, heartbeat) sono
        misurate esattamente come prima.
        """
        if not is_project_session_key(session_key):
            return None
        return self._workspace_scopes.for_project(session_key).project_path

    @property
    def _input_token_budget(self) -> int:
        """Available input token budget for consolidation LLM."""
        return self.context_window_tokens - self.max_completion_tokens - self._SAFETY_BUFFER

    def _truncate_to_token_budget(self, text: str, *, reserved_tokens: int = 0) -> str:
        """Truncate text so it fits within the consolidation LLM's token budget.

        *reserved_tokens* è lo spazio già speso nel system prompt da qualcosa
        che non è la conversazione — oggi il blocco "già registrato" della
        fase 4. Va sottratto qui e non altrove: il budget è la finestra del
        modello meno la risposta, e un blocco aggiunto al system senza toglierlo
        da questo conto è una richiesta che sfora la finestra, cioè una
        consolidation che fallisce e raw-dumpa la conversazione in history.
        """
        budget = self._input_token_budget - max(0, reserved_tokens)
        if budget <= 0:
            return truncate_text(text, _RAW_ARCHIVE_MAX_CHARS)
        return truncate_text_to_tokens(text, budget)

    async def archive(
        self,
        messages: list[dict],
        *,
        session_key: str | None = None,
        summary_messages: list[dict] | None = None,
        prompt_visible: bool = True,
    ) -> str | None:
        """Summarize messages via LLM and append to history.jsonl.

        ``messages`` are the messages being archived (removed from the live
        session); they are what gets raw-dumped if the LLM call fails.
        ``summary_messages``, when given, lets callers include retained
        messages in the summary without archiving them.

        ``prompt_visible=False`` scrive la voce per Dream e non per i prompt di
        turno: e' ``/new``, che archivia qui una conversazione che l'utente ha
        deciso di non avere piu' davanti (v. ``MemoryStore.append_history``). Il
        default resta ``True``, che e' il caso normale — l'auto-compattazione
        riassume una conversazione **che continua**, e il modello deve
        continuare a vederne la coda.

        Returns the summary text on success, None if nothing to archive.
        """
        if not messages:
            return None
        messages_to_summarize = summary_messages if summary_messages is not None else messages
        try:
            formatted = MemoryStore._format_messages(messages_to_summarize)
            # Il blocco sta nel system e non in coda alla conversazione: è
            # istruzione, non materiale da riassumere, e in fondo al messaggio
            # utente si leggerebbe come l'ultima cosa detta nella chat.
            known = self.store.get_known_facts_context(session_key=session_key)
            system = render_template("agent/consolidator_archive.md", strip=True)
            if known:
                system = f"{system}\n\n{known}"
            formatted = self._truncate_to_token_budget(
                formatted, reserved_tokens=_estimate_tokens(known),
            )
            response = await self.provider.chat_with_retry(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": formatted},
                ],
                tools=None,
                tool_choice=None,
            )
            if response.finish_reason == "error":
                raise RuntimeError(f"LLM returned error: {response.content}")
            summary = response.content or "[no summary]"
            self._log_extraction(summary, known, session_key)
            # L'I/O di MemoryStore è bloccante-by-design (open+write+fsync sotto
            # threading.Lock): girando qui in un metodo async, lo spostiamo fuori
            # dall'event loop con to_thread per non bloccare il loop sul fsync.
            await asyncio.to_thread(
                self.store.append_history,
                summary,
                max_chars=_ARCHIVE_SUMMARY_MAX_CHARS,
                session_key=session_key,
                prompt_visible=prompt_visible,
            )
            return summary
        except Exception:
            logger.warning("Consolidation LLM call failed, raw-dumping to history")
            await asyncio.to_thread(
                self.store.raw_archive,
                messages,
                session_key=session_key,
                prompt_visible=prompt_visible,
            )
            return None

    @staticmethod
    def _log_extraction(summary: str, known: str, session_key: str | None) -> None:
        """La misura della fase 4: quanto di ciò che esce era già dentro.

        ``repeats`` conta le ripetizioni **verbatim**, ed è quindi un limite
        inferiore: un fatto riestratto con altre parole non lo tocca. È scritto
        così di proposito — l'alternativa sarebbe un confronto approssimato, che
        darebbe un numero più alto e meno vero. Serve come segnale, non come
        percentuale: sopra zero vuol dire che il blocco è nel prompt e il
        modello lo sta ignorando, che è l'unico esito di questa fase che nessun
        test locale può vedere.
        """
        from jenny.agent.tools.memory_entries import entry_id, parse_entries

        facts = [fact for mark, fact in iter_fact_lines(summary) if mark != "skip"]
        known_ids = {entry.id for entry in parse_entries(known)} if known else set()
        repeats = sum(1 for fact in facts if entry_id(f"- {fact}") in known_ids)
        logger.info(
            "Consolidation for {}: {} facts extracted, {} already recorded shown, "
            "{} verbatim repeats",
            session_key or "-",
            len(facts),
            len(known_ids),
            repeats,
        )

    async def maybe_consolidate_by_tokens(
        self,
        session: Session,
        *,
        replay_max_messages: int | None = None,
    ) -> None:
        """Loop: archive old messages until prompt fits within safe budget.

        The budget reserves space for completion tokens and a safety buffer
        so the LLM request never exceeds the context window.
        """
        if self.context_window_tokens <= 0:
            return

        lock = self.get_lock(session.key)
        async with lock:
            # Refresh session reference: AutoCompact may have replaced it.
            fresh = self.sessions.get_or_create(session.key)
            if fresh is not session:
                session = fresh
            if not session.messages:
                return

            budget = self._input_token_budget
            target = int(budget * self.consolidation_ratio)
            last_summary = await self._consolidate_replay_overflow(
                session,
                replay_max_messages,
            )
            try:
                estimated, source = self.estimate_session_prompt_tokens(
                    session,
                )
            except Exception:
                logger.exception("Token estimation failed for {}", session.key)
                estimated, source = 0, "error"
            if estimated <= 0:
                self._persist_last_summary(session, last_summary)
                return
            if estimated < budget:
                unconsolidated_count = len(session.messages) - session.last_consolidated
                logger.debug(
                    "Token consolidation idle {}: {}/{} via {}, msgs={}",
                    session.key,
                    estimated,
                    self.context_window_tokens,
                    source,
                    unconsolidated_count,
                )
                self._persist_last_summary(session, last_summary)
                return

            for round_num in range(self._MAX_CONSOLIDATION_ROUNDS):
                if estimated <= target:
                    break

                boundary = self.pick_consolidation_boundary(session, max(1, estimated - target))
                if boundary is None:
                    logger.debug(
                        "Token consolidation: no safe boundary for {} (round {})",
                        session.key,
                        round_num,
                    )
                    break

                end_idx = boundary[0]

                chunk = session.messages[session.last_consolidated:end_idx]
                if not chunk:
                    break

                logger.info(
                    "Token consolidation round {} for {}: {}/{} via {}, chunk={} msgs",
                    round_num,
                    session.key,
                    estimated,
                    self.context_window_tokens,
                    source,
                    len(chunk),
                )
                summary = await self.archive(chunk, session_key=session.key)
                # Advance the cursor either way: on success the chunk was
                # summarized; on failure archive() already raw-archived it as
                # a breadcrumb. Re-archiving the same chunk on the next call
                # would just emit duplicate [RAW] entries.
                if summary:
                    last_summary = summary
                session.last_consolidated = end_idx
                self.sessions.save(session)
                if not summary:
                    # LLM is degraded — stop hammering it this call;
                    # the next invocation can retry a fresh chunk.
                    break

                try:
                    estimated, source = self.estimate_session_prompt_tokens(
                        session,
                    )
                except Exception:
                    logger.exception("Token estimation failed for {}", session.key)
                    estimated, source = 0, "error"
                if estimated <= 0:
                    break

            # Persist the last summary to session metadata so it can be injected
            # into the runtime context on the next prepare_session() call, aligning
            # the summary injection strategy with AutoCompact._archive().
            self._persist_last_summary(session, last_summary)

    async def compact_idle_session(
        self,
        session_key: str,
        max_suffix: int = 8,
    ) -> str | None:
        """Hard-truncate an idle session under the consolidation lock.

        Used by AutoCompact so all session mutation goes through a single
        lock-protected path.  Returns the summary text on success, ``None``
        if the LLM failed (raw_archive fallback), or ``""`` if there was
        nothing to archive.
        """
        lock = self.get_lock(session_key)
        async with lock:
            self.sessions.invalidate(session_key)
            session = self.sessions.get_or_create(session_key)

            messages_to_summarize = list(session.messages[session.last_consolidated:])
            if not messages_to_summarize:
                session.updated_at = datetime.now()
                self.sessions.save(session)
                return ""

            probe = Session(
                key=session.key,
                messages=messages_to_summarize.copy(),
                created_at=session.created_at,
                updated_at=session.updated_at,
                metadata={},
                last_consolidated=0,
            )
            dropped, already_consolidated = probe.retain_recent_legal_suffix(max_suffix, extend_to_user=True)
            messages_to_keep = probe.messages
            messages_to_remove = dropped[already_consolidated:]

            if not messages_to_remove and not messages_to_keep:
                session.updated_at = datetime.now()
                self.sessions.save(session)
                return ""

            last_active = session.updated_at
            summary: str | None = ""
            if messages_to_remove:
                # Summarize the retained suffix too, but only remove/raw-dump
                # the messages that are no longer kept in the live session.
                summary = await self.archive(
                    messages_to_remove,
                    session_key=session_key,
                    summary_messages=messages_to_summarize,
                )

            if messages_to_remove and summary is None and is_project_session_key(session_key):
                # ``archive()`` ha fallito la chiamata LLM e ha raw-dumpato in
                # ``history.jsonl``. **Dall'08/09/2026 quel dump viene scritto
                # anche per un progetto** (``append_history`` non rifiuta più una
                # chiave ``project:``), e la copia qui serve lo stesso: sono due
                # depositi per due lettori diversi. Il dump in history è la coda
                # da cui Dream estrae i fatti *sulla persona*, e da un prompt di
                # progetto non è raggiungibile — il filtro di
                # ``read_recent_history_for_prompt`` lo esclude. La copia invece
                # resta **dentro il progetto**, che è l'unico posto dove quella
                # conversazione può essere ritrovata da chi ci lavora.
                #
                # Quindi qui, e solo qui, la copia va fatta prima di troncare:
                # senza, la troncatura di sotto butterebbe i messaggi dal
                # progetto — e il fatto che una copia esista in una coda che il
                # progetto non può leggere non è un rimedio.
                copy = await asyncio.to_thread(
                    self._copy_removed_into_project, session_key, messages_to_remove
                )
                if copy is None:
                    # **Non si tronca.** Una compattazione mancata costa contesto
                    # alla prossima finestra di inattività, che riprova; una
                    # troncatura senza copia costa la conversazione. La sessione
                    # non viene nemmeno salvata: ``updated_at`` resta vecchio,
                    # quindi resta scaduta e AutoCompact la ripesca.
                    logger.warning(
                        "Idle-session compact for {} aborted: the LLM failed and no copy "
                        "could be written into the project, so the {} messages stay in the "
                        "session; retrying at the next idle window",
                        session_key,
                        len(messages_to_remove),
                    )
                    return None
                logger.info(
                    "Idle-session compact for {}: {} messages copied to {} before truncating",
                    session_key,
                    len(messages_to_remove),
                    copy,
                )

            if summary and summary != "(nothing)":
                session.metadata["_last_summary"] = {
                    "text": summary,
                    "last_active": last_active.isoformat(),
                }

            session.messages = messages_to_keep
            session.last_consolidated = 0
            session.updated_at = datetime.now()
            self.sessions.save(session)

            if messages_to_remove:
                logger.info(
                    "Idle-session compact for {}: archived={}, kept={}, summary={}",
                    session_key,
                    len(messages_to_remove),
                    len(messages_to_keep),
                    bool(summary),
                )

            return summary

    def _project_dump_dir(self, session_key: str) -> Path | None:
        """La cartella delle copie del progetto di *session_key*, o ``None``.

        Il nome del progetto si **cerca** fra le cartelle che esistono davvero
        invece di comporre un percorso: arriva da una chiave di sessione, cioè in
        ultima analisi da un client, e una ricerca per chiave in un dizionario non
        ha nessun ``..`` da validare — non c'è aritmetica di percorsi da
        sbagliare. Che la cartella sia una wiki vera viene gratis dalla stessa
        funzione, e serve: se il progetto è stato cancellato o rinominato non c'è
        posto dove scrivere, e il chiamante deve saperlo invece di inventarne uno
        (la lezione del passo 6, la stessa di ``for_project``).

        La sottocartella dei progetti arriva iniettata (``config.wiki.wikis_dir``),
        non dal default della classe: se la si leggesse dal default, su
        un'installazione che ha spostato i progetti questa ricerca non troverebbe
        mai niente e la compattazione di un progetto verrebbe rifiutata per
        sempre — un difetto silenzioso travestito da prudenza.
        """
        name = session_key[len(PROJECT_SESSION_PREFIX):]
        roots = discover_wiki_roots(self.store.workspace / self._projects_subdir)
        root = roots.get(name)
        return None if root is None else root / _PROJECT_COMPACTED_SUBDIR

    def _copy_removed_into_project(
        self,
        session_key: str,
        messages: list[dict],
    ) -> Path | None:
        """Copia *messages* dentro il progetto. Il percorso scritto, o ``None``.

        **JSONL, un messaggio per riga, e non il testo formattato** di
        ``raw_archive``: questa non è una briciola da rileggere, è la copia che
        rende reversibile la troncatura che le segue. Il formato è quello delle
        righe di un file di sessione (v. ``SessionManager.save``), quindi
        rimetterle dentro è un innesto e non una ricostruzione a mano; e non
        perde i messaggi senza ``content`` — un giro di tool — che
        ``_format_messages`` scarta perché a lui servono da riassumere.

        Niente tetto di lunghezza, per la stessa ragione: i tetti di
        ``raw_archive`` proteggono un prompt, e questo file in nessun prompt
        entra. Mezza copia non renderebbe reversibile mezza troncatura.

        In append e con ``fsync``: append perché due passate nello stesso secondo
        cadono sullo stesso nome e appendere non ne perde nessuna, ``fsync``
        perché subito dopo il chiamante butta gli originali — la copia deve
        essere su disco *prima*, non nella page cache.
        """
        directory = self._project_dump_dir(session_key)
        if directory is None:
            return None
        page = directory / f"{datetime.now():%Y%m%d-%H%M%S}.jsonl"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            with page.open("a", encoding="utf-8") as fh:
                for message in messages:
                    fh.write(json.dumps(message, ensure_ascii=False) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        except (OSError, TypeError, ValueError) as exc:
            # ``TypeError``/``ValueError``: un messaggio non serializzabile in
            # JSON. Non è mai capitato — le sessioni si salvano con lo stesso
            # ``json.dumps`` — ma qui un'eccezione non gestita passerebbe per un
            # fallimento di ``compact_idle_session``, e la conversazione
            # resterebbe intera senza che nessuno dica perché.
            logger.warning("Project copy of the compacted tail failed ({}): {}", page, exc)
            return None
        return page
