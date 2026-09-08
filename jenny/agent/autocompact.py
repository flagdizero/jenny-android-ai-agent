"""Auto compact: proactive compression of idle sessions to reduce token cost and latency."""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Coroutine

from loguru import logger

from jenny.security.workspace_access import WorkspaceScopeResolver
from jenny.session.keys import (
    DREAM_SESSION_PREFIX,
    PROJECT_SESSION_PREFIX,
    UNIFIED_SESSION_KEY,
    is_project_session_key,
)
from jenny.session.manager import Session, SessionManager

# La sottocartella dei progetti quando nessuno la passa. Dalla stessa costante
# che usa il ``Consolidator``, non da un letterale: ``config.wiki.wikis_dir`` e'
# configurabile, e due default scritti a mano divergono al primo che cambia.
_PROJECTS_SUBDIR = WorkspaceScopeResolver.projects_subdir

if TYPE_CHECKING:
    from jenny.agent.memory import Consolidator


class AutoCompact:
    _RECENT_SUFFIX_MESSAGES = 8
    # Sottoinsieme *deliberatamente stretto* del vocabolario di
    # :mod:`jenny.session.keys`: qui "interna" non vuol dire "non e' l'utente",
    # vuol dire "non deve avere ne archiviazione per inattivita ne reiniezione
    # di ``_last_summary``". Allargarlo a ``cron:``/``heartbeat``/``internal:``/
    # ``subagent:`` toglierebbe a quelle sessioni il summary che oggi
    # ``prepare_session`` ricarica quando superano il budget di token: sarebbe
    # una regressione, non un allineamento. Se l'insieme debba coincidere con
    # ``is_internal_session_key`` e' una decisione aperta, da prendere a parte.
    # I prefissi arrivano comunque dalle costanti condivise, cosi la *forma*
    # della chiave non puo divergere dal lato che la scrive.
    _INTERNAL_SESSION_PREFIXES = (DREAM_SESSION_PREFIX,)

    # Le sessioni che il giro per inattivita' prende in considerazione, **prima**
    # del recinto di :meth:`_may_archive_for_idleness`. Oggi una sola.
    #
    # E' un attributo e non una costante dentro ``_idle_candidates`` per una
    # ragione precisa: cosi' un test puo' allargare l'*elenco* lasciando in
    # piedi il filtro vero. Con l'elenco cablato nel metodo, l'unico modo di
    # provare il filtro era sovrascrivere il metodo — e un test che
    # sovrascrive il metodo prova la propria copia, non il codice (misurato per
    # mutazione il 22/08: togliere il filtro non faceva cadere niente).
    #
    # E' anche il punto che una generalizzazione allargherebbe.
    _IDLE_CANDIDATE_KEYS: tuple[str, ...] = (UNIFIED_SESSION_KEY,)

    # Quanti messaggi nuovi servono perche' valga la pena di raccogliere il
    # diario di un progetto (v. :meth:`_harvest_project_diary`). Due, cioe' uno
    # scambio: una domanda e una risposta possono gia' contenere un fatto sulla
    # persona, e la soglia serve solo a non spendere una chiamata LLM su una
    # sessione che non ha detto niente di nuovo.
    _DIARY_HARVEST_MIN_MESSAGES = 2

    # Dove la sessione si annota fin dove il diario e' stato raccolto. Un indice
    # nei messaggi, non un timestamp: i messaggi sono la cosa che si conta, e un
    # orologio andrebbe confrontato con quello di chi scrive.
    _DIARY_HARVEST_KEY = "_diary_harvested"

    def __init__(self, sessions: SessionManager, consolidator: Consolidator,
                 session_ttl_minutes: int = 0,
                 compact_projects: bool = False,
                 projects_subdir: str = _PROJECTS_SUBDIR):
        self.sessions = sessions
        self.consolidator = consolidator
        self._ttl = session_ttl_minutes
        # L'interruttore di P4 (``config.agents.defaults.compact_projects_when_idle``).
        # Spento, il recinto sotto vale come prima; acceso, la conversazione di un
        # progetto si compatta come quella personale — perche' la verita' non sta
        # piu' li', sta nelle pagine.
        self._compact_projects = compact_projects
        # Dove stanno i progetti. **Non un letterale ``"wikis"``**: la cartella
        # e' ``config.wiki.wikis_dir``, e chi costruisce questo oggetto la ha
        # (``AgentLoop.__init__`` la riceve come ``projects_subdir`` e la passa
        # gia' al ``Consolidator``). Finche' quel punto non la passa anche qui, il
        # default vale — corretto per la configurazione di serie, e per una
        # ``wikis_dir`` diversa la cartella non si trova e il recinto **rinvia**
        # dicendolo, invece di compattare sulla fede.
        self._projects_subdir = projects_subdir
        self._archiving: set[str] = set()
        # Separato da ``_archiving``: sono due lavori diversi sulla stessa
        # sessione — uno la accorcia, l'altro la legge e basta — e un insieme
        # solo farebbe rinviare l'uno per colpa dell'altro.
        self._harvesting: set[str] = set()
        self._summaries: dict[str, tuple[str, datetime]] = {}
        # Il motivo dell'ultimo rinvio, per progetto: serve solo a non ripetere
        # la stessa riga di log ogni minuto (il giro TTL gira a 60s). La
        # decisione non e' memorizzata — si rifa ogni volta.
        self._deferred: dict[str, str] = {}

    @property
    def _workspace(self) -> Path | None:
        """La radice del workspace, dedotta da chi tiene le sessioni.

        ``SessionManager.workspace`` **e'** ``config.workspace_path``, in
        ``GatewayContainer`` come in ``AgentLoop`` (che passa la stessa
        ``workspace``): non c'e' un secondo dato da tenere allineato, quindi
        nessun argomento nuovo per una cosa che il chiamante ha gia' dato una
        volta.
        """
        directory = getattr(self.sessions, "workspace", None)
        return directory if isinstance(directory, Path) else None

    def _is_expired(self, ts: datetime | str | None,
                    now: datetime | None = None) -> bool:
        if self._ttl <= 0 or not ts:
            return False
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        return ((now or datetime.now()) - ts).total_seconds() >= self._ttl * 60

    @staticmethod
    def _format_summary(text: str, last_active: datetime) -> str:
        return f"Previous conversation summary (last active {last_active.isoformat()}):\n{text}"

    @classmethod
    def _is_internal_session(cls, key: str) -> bool:
        return key.startswith(cls._INTERNAL_SESSION_PREFIXES)

    def _may_archive_for_idleness(self, key: str) -> bool:
        """Se questa sessione puo' essere archiviata perche' e' stata zitta.

        La regola e' **«non un progetto»**, e la classificazione arriva dal
        predicato canonico di :mod:`jenny.session.keys`: la tassonomia delle
        sessioni vive la' e non si riscrive il confronto qui.

        Perche' i progetti no: archiviare per *tempo passato* la storia lunga di
        un argomento butta esattamente la cosa che rende un progetto utile. Un
        progetto puo' stare fermo tre settimane e riprendere dove era: e' il suo
        mestiere. Quel che non perde e' la compattazione per **lunghezza**, che
        gira a ogni turno di ogni sessione
        (``Consolidator.maybe_consolidate_by_tokens``, v. ``loop.py``): il
        recinto e' sul tempo, non sulla dimensione.

        **E perche' non una whitelist**, che sarebbe la forma piu' prudente e che
        qui e' stata provata e scartata. Due ragioni. La prima: una whitelist
        "solo la personale" bloccherebbe la generalizzazione per cui questo passo
        esiste — la riga del piano dice che l'archiviazione *puo'* girare su tutte
        le sessioni, purche' non trascini il lavoro interno nel diario e lasci
        stare i progetti. La seconda: la prudenza che una whitelist compra qui
        non c'e' comunque, perche' :func:`jenny.session.keys.session_kind` chiude
        la tassonomia a tre etichette e fa cadere **su "personal"** tutto quel
        che non riconosce. Una quarta forma di chiave risulterebbe personale in
        entrambe le forme; fingere il contrario sarebbe una falsa sicurezza.

        Fino al passo 8 questa regola non era scritta: :meth:`check_expired`
        aveva ``UNIFIED_SESSION_KEY`` cablato dentro, quindi i progetti erano
        salvi **per accidente** e non per decisione. Sta qui e non nel chiamante
        perche' chi generalizzera' questa funzione toccherebbe lei.

        **E da T6.5 c'e' una manopola** (``compact_projects_when_idle``, spenta di
        default). Accenderla e' l'ultimo gradino di P4: da quel momento la
        conversazione di un progetto non e' piu' l'unico depositario di niente,
        quindi archiviarla non butta via nulla — la verita' sta nelle pagine, che
        entrano in contesto d'ufficio (T3 e T6.4). Il recinto resta la posizione
        di partenza, e resta reversibile: e' quel che rende accendere P4 una prova
        invece di una scommessa.

        **Aperto il recinto, la manopola non basta da sola** (T2.6): "la verita'
        sta nelle pagine" e' una premessa, non un fatto, e chi la verifica e'
        :meth:`_pages_carry_the_project`. Quella verifica non sta qui perche'
        questo metodo gira su **tutti** i candidati a ogni giro TTL (60 secondi)
        mentre quella legge il diario da disco: pagarla per un progetto che non e'
        nemmeno scaduto sarebbe I/O al minuto per progetto, sul telefono, per
        niente. Sta ai due punti in cui la decisione si prende davvero.
        """
        if self._compact_projects:
            return True
        return not is_project_session_key(key)

    def _pages_carry_the_project(self, key: str) -> bool:
        """Se quel che questa conversazione ha detto e' **gia' diventato pagine**.

        Il secondo cancello di P4, e quello che regge la premessa del primo.
        Compattare la conversazione di un progetto e' innocuo *solo* se il
        giardiniere ha gia' promosso quel che vi si e' detto; senza questo
        controllo i due orologi non sono nemmeno nell'ordine giusto —
        ``idleCompactAfterMinutes`` sta a 15 minuti di default, mentre il
        giardiniere vuole 30 minuti di quiete **piu'** fino a sei ore di distanza
        **piu'** un tick da mezz'ora. Su un progetto nuovo l'ordine normale
        sarebbe stato *compatta, poi promuovi*: cioe' l'esatto contrario della
        premessa. Legare la compattazione al **delta di diario vuoto** trasforma
        una corsa fra due orologi che non si parlano in un invariante.

        Non basta che il diario sia letto: si chiede **anche che esista almeno
        una pagina**. Il delta e' vuoto in due situazioni che si assomigliano da
        fuori e non hanno niente in comune — «il giardiniere ha promosso tutto» e
        «in ``raw/journal/`` non c'e' mai finito niente» (cattura mai avvenuta,
        cartella appena creata: :func:`read_journal_delta` restituisce un delta
        vuoto quando la cartella del diario non c'e'). Nel secondo caso la
        conversazione e' l'unico depositario esistente, ed e' esattamente il caso
        che questo passo deve fermare. Il conto del rinvio di troppo e' invece
        basso e limitato: la compattazione per **lunghezza**
        (``maybe_consolidate_by_tokens``) gira a ogni turno di ogni sessione, per
        cui un progetto che rinvia per sempre non cresce per sempre — perde solo
        la compressione *anticipata*. Asimmetria decisiva: qui si sbaglia dalla
        parte che costa token, non dalla parte che costa memoria.

        Vale **solo** per i progetti: qualunque altra chiave passa senza toccare
        il disco. La conversazione personale ha Dream, non il giardiniere, e
        legarla a un diario che non ha spegnerebbe la compattazione dove funziona.
        """
        if not is_project_session_key(key):
            return True
        # Import locale: ``gardener`` non serve quando la manopola e' spenta —
        # cioe' quasi sempre — e questo modulo lo carica ``AgentLoop`` all'avvio.
        from jenny.agent.gardener import GardenerStore
        from jenny.utils.wiki_paths import iter_wiki_pages

        workspace = self._workspace
        if workspace is None:
            return self._defer(key, "the workspace root is unknown")
        name = key[len(PROJECT_SESSION_PREFIX):]
        # Stessa risoluzione chiave -> cartella del giardiniere e di
        # ``WorkspaceScopeResolver.for_project``, guardia sui ``..`` compresa. E
        # la sottocartella e' quella configurata, non ``"wikis"`` cablata.
        store = GardenerStore.for_project(
            workspace, name, wikis_dir_name=self._projects_subdir,
        )
        if store is None:
            return self._defer(
                key, f"no project folder at {self._projects_subdir}/{name}",
            )
        try:
            delta = store.read_delta()
            # ``titles=False``: qui serve solo il numero, e col default i titoli
            # costerebbero una lettura per pagina — per un cancello che gira a
            # ogni giro TTL, cioe' ogni minuto, per ogni progetto scaduto (T3.16).
            pages = len(iter_wiki_pages(store.root / "wiki", titles=False))
        except Exception as exc:
            # Largo di proposito, e per due ragioni. Un diario illeggibile non e'
            # un permesso a compattare, quindi l'esito giusto e' il rinvio; e
            # ``check_expired`` viene chiamata **dentro** un ``except
            # asyncio.TimeoutError`` in ``AgentLoop.run``, dove un'eccezione non
            # e' ripresa da quel ``try`` — la farebbe uscire dal ciclo
            # dell'agente. Un cancello prudente non deve poter spegnere il loop.
            logger.debug("Auto-compact: journal check failed for {}: {!r}", key, exc)
            return self._defer(key, f"the journal could not be read ({exc})")
        if not delta.is_empty:
            return self._defer(
                key, f"{delta.line_count} journal lines are not promoted yet",
            )
        if not pages:
            return self._defer(key, "the project has no pages yet")
        if self._deferred.pop(key, None) is not None:
            logger.info(
                "Auto-compact: {} is no longer deferred ({} pages, journal fully read)",
                key, pages,
            )
        return True

    def _defer(self, key: str, reason: str) -> bool:
        """Registra il rinvio e lo dice. Sempre ``False``, per scriverlo in linea.

        Il log e' la meta' che rende questo cancello utile invece di misterioso:
        un progetto che non si compatta mai deve poter essere **spiegato**. A
        ``INFO`` la prima volta e a ogni cambio di motivo, a ``DEBUG`` sulle
        ripetizioni: il giro TTL passa ogni minuto, e la stessa riga sessanta
        volte l'ora seppellirebbe il resto del log invece di dirlo.
        """
        first_time = self._deferred.get(key) != reason
        self._deferred[key] = reason
        log = logger.info if first_time else logger.debug
        log("Auto-compact: deferring idle compaction of {} — {}", key, reason)
        return False

    def _idle_candidates(self) -> tuple[str, ...]:
        """Le sessioni che questo giro puo' guardare.

        Oggi una sola — la conversazione personale — ma passa dal filtro invece
        di essere una costante, e la differenza non e' cosmetica: **una guardia
        che non puo' scattare non e' provabile**, e una guardia non provabile
        non e' una guardia. Prima questa funzione aveva
        ``UNIFIED_SESSION_KEY`` cablato e un ``if`` subito sotto: togliere
        quell'``if`` non faceva cadere nessun test, perche' la sola chiave che ci
        arrivava era comunque ammessa (misurato per mutazione il 22/08).

        E' anche la riga che una generalizzazione allargherebbe: estendere
        l'elenco qui fa passare le sessioni nuove dal recinto senza doverselo
        ricordare.
        """
        keys = list(self._IDLE_CANDIDATE_KEYS)
        if self._compact_projects:
            # **Aprire il recinto non basta.** L'elenco dei candidati contiene
            # una chiave sola, quindi con il solo filtro allargato nessun
            # progetto verrebbe mai *guardato*: il recinto e la lista sono due
            # cose diverse, e la seconda e' quella che decide chi entra nel giro.
            keys.extend(self._project_session_keys())
        return tuple(key for key in keys if self._may_archive_for_idleness(key))

    def _project_session_keys(self) -> tuple[str, ...]:
        """Le sessioni-progetto che esistono su disco.

        Si guardano i **file**, non le wiki: un progetto senza conversazione non
        ha niente da compattare, e un progetto la cui cartella e' stata rinominata
        conserva la propria sessione (passo 7). Lo stesso mestiere che
        ``MemoryStore.prune_internal_sessions`` fa per i run interni, con la
        stessa traduzione nome-file -> chiave.
        """
        directory = getattr(self.sessions, "sessions_dir", None)
        if directory is None:
            return ()
        prefix = PROJECT_SESSION_PREFIX[:-1]  # "project", senza i due punti
        try:
            files = sorted(directory.glob(f"{prefix}_*.jsonl"))
        except OSError:
            return ()
        return tuple(path.stem.replace("_", ":", 1) for path in files)

    def check_expired(self, schedule_background: Callable[[Coroutine], None],
                      active_session_keys: Collection[str] = ()) -> None:
        """Schedule archival of idle sessions, unless a task is in flight."""
        for key in self._idle_candidates():
            if key in self._archiving or key in active_session_keys:
                continue
            info = self.sessions.read_session_metadata(key)
            if info is None:
                continue
            if not self._is_expired(info.get("updated_at")):
                continue
            # **Dopo** la scadenza, non prima: il secondo cancello legge il
            # diario da disco, e cosi' lo paga solo un progetto che sta davvero
            # per essere compattato.
            if not self._pages_carry_the_project(key):
                continue
            self._archiving.add(key)
            schedule_background(self._archive(key))
        for key in self._diary_candidates():
            if key in self._harvesting or key in active_session_keys:
                continue
            info = self.sessions.read_session_metadata(key)
            if info is None or not self._is_expired(info.get("updated_at")):
                continue
            self._harvesting.add(key)
            schedule_background(self._harvest_project_diary(key))

    def _diary_candidates(self) -> tuple[str, ...]:
        """I progetti la cui conversazione va **letta** per il diario personale.

        Sono le sessioni-progetto che questo giro *non* sta gia' compattando. La
        compattazione, quando e' accesa, riassume gia' e quel riassunto finisce
        nella coda: raccogliere anche qui vorrebbe dire riassumere due volte la
        stessa materia. Con la manopola spenta — il default — i due insiemi non
        si toccano e questo e' l'unico che gira sui progetti.
        """
        compacting = set(self._idle_candidates())
        return tuple(
            key for key in self._project_session_keys() if key not in compacting
        )

    async def _harvest_project_diary(self, key: str) -> None:
        """Riassume quel che un progetto ha detto di nuovo, **senza toccarlo**.

        **La fase che rende la corsia di diario non vuota** (08/09/2026, v.
        ``.agent/project-memory-plan.md``). Aperta la scrittura in
        ``history.jsonl``, il trasporto restava quello della compattazione: un
        riassunto lo produce solo chi compatta. Misurato sul telefono lo stesso
        giorno, **3 sessioni di progetto su 9** erano mai state compattate — fra
        le sei escluse c'erano quelle da 54, 64, 78 e 80 messaggi, cioe' proprio
        le conversazioni piu' ricche. La corsia sarebbe esistita e non avrebbe
        trasportato quasi niente.

        **Riassume e non compatta, ed e' tutta la differenza.** La sessione non
        perde un messaggio, ``_last_summary`` non viene toccato, il giardiniere
        non c'entra: l'unico effetto e' una voce in piu' nella coda, con la
        chiave del progetto. Percio' qui non serve nessuno dei due cancelli che
        proteggono la compattazione di un progetto — ne' ``non un progetto``, ne'
        ``_pages_carry_the_project``. Quelli difendono il *contenuto* del
        progetto da una troncatura; qui non si tronca niente, e legare la lettura
        all'orologio del giardiniere sarebbe la corsa fra orologi che quel metodo
        esiste per evitare.

        L'indice avanza **anche quando la chiamata LLM fallisce**, e non e' una
        svista: in quel caso ``archive`` scrive il dump grezzo nella coda con la
        stessa chiave, quindi la materia e' arrivata lo stesso e riassumerla di
        nuovo produrrebbe una seconda voce sullo stesso contenuto.
        """
        try:
            session = self.sessions.get_or_create(key)
            messages = list(session.messages)
            # ``min``: se qualcuno ha compattato in mezzo, l'indice puo' essere
            # oltre la fine. Ripartire da li' invece che da zero — rileggere
            # tutto produrrebbe un doppione di quel che e' gia' nella coda.
            start = min(int(session.metadata.get(self._DIARY_HARVEST_KEY, 0)), len(messages))
            fresh = messages[start:]
            if len(fresh) < self._DIARY_HARVEST_MIN_MESSAGES:
                return
            await self.consolidator.archive(fresh, session_key=key)
            session = self.sessions.get_or_create(key)
            session.metadata[self._DIARY_HARVEST_KEY] = len(messages)
            self.sessions.save(session)
            logger.info(
                "Diary harvest for {}: {} new messages read into the queue", key, len(fresh),
            )
        except Exception:
            logger.exception("Diary harvest failed for {}", key)
        finally:
            self._harvesting.discard(key)

    async def _archive(self, key: str) -> None:
        # Secondo controllo, e non e' ridondante: ``_archive`` e' una coroutine
        # che qualcuno pianifica, quindi e' raggiungibile senza passare da
        # ``check_expired`` — ed e' l'ultimo punto prima di riscrivere la
        # sessione. Il primo guardia l'ingresso, questo la scrittura.
        if (
            not self._may_archive_for_idleness(key)
            or self._is_internal_session(key)
            or not self._pages_carry_the_project(key)
        ):
            self._archiving.discard(key)
            return
        try:
            summary = await self.consolidator.compact_idle_session(
                key, self._RECENT_SUFFIX_MESSAGES,
            )
            if summary and summary != "(nothing)":
                session = self.sessions.get_or_create(key)
                meta = session.metadata.get("_last_summary")
                if isinstance(meta, dict):
                    self._summaries[key] = (
                        meta["text"],
                        datetime.fromisoformat(meta["last_active"]),
                    )
        except Exception:
            logger.exception("Auto-compact: failed for {}", key)
        finally:
            self._archiving.discard(key)

    def prepare_session(self, session: Session, key: str) -> tuple[Session, str | None]:
        if self._is_internal_session(key):
            self._archiving.discard(key)
            self._summaries.pop(key, None)
            return session, None
        if key in self._archiving or self._is_expired(session.updated_at):
            # Il log sta *dentro* il confronto e non prima della chiamata: se la
            # sessione e' gia in ``SessionManager._cache`` — il caso normale, ed
            # e' l'unico che l'heartbeat incontra a ogni giro — ``get_or_create``
            # restituisce lo stesso oggetto e non ha ricaricato niente. Loggare
            # "reloading" li faceva credere a un ricaricamento a ogni battito.
            reloaded = self.sessions.get_or_create(key)
            if reloaded is not session:
                logger.info(
                    "Auto-compact: reloaded session {} (archiving={})",
                    key, key in self._archiving,
                )
            session = reloaded
        # Hot path: summary from in-memory dict (process hasn't restarted).
        entry = self._summaries.pop(key, None)
        if entry:
            return session, self._format_summary(entry[0], entry[1])
        # Cold path: summary persisted in session metadata (process restarted).
        meta = session.metadata.get("_last_summary")
        if isinstance(meta, dict):
            return session, self._format_summary(meta["text"], datetime.fromisoformat(meta["last_active"]))
        return session, None
