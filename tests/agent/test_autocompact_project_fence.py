"""I progetti non si archiviano per inattività — e la lunghezza li raggiunge.

Passo **8** di ``roadmap/progetti-passi.md``.

Fino al passo 8 i progetti erano salvi **per accidente**: ``check_expired`` aveva
``UNIFIED_SESSION_KEY`` cablato dentro, quindi guardava una sessione sola e le
altre non le vedeva nemmeno. La riga del piano dice che quel giro *potrà* girare
su tutte le sessioni — e che quando lo farà deve lasciare stare i progetti.
Questo file è il recinto messo prima, così quel giorno non serve ricordarselo.

**Da T6.5 questo file ha una seconda metà: la manopola.** Il recinto non è stato
demolito — ``compact_projects_when_idle`` (spenta di default) lo apre, e ogni test
qui sopra continua a descrivere il comportamento con la manopola spenta. È quel
che rende accendere P4 una prova reversibile invece di una scommessa.

**Le due metà vanno lette insieme.** Da sola, la prima si legge come «i progetti
non si compattano», che è falso e sarebbe una brutta sorpresa al primo progetto
da duecento turni: la compattazione per **lunghezza** li raggiunge come tutti, a
ogni turno. Il recinto è sul tempo passato, non sulla dimensione.

**E da T2.6 una terza: il cancello che regge la premessa della manopola.**
«Archiviare non butta via nulla perché la verità sta nelle pagine» era una
premessa che nessuno verificava — e che i due orologi rendevano di norma falsa,
perché ``idleCompactAfterMinutes`` scade prima che il giardiniere abbia potuto
promuovere. Ora la compattazione per inattività di un progetto chiede che il
delta di diario sia vuoto **e** che esista almeno una pagina.

Perché archiviare per tempo un progetto è sbagliato, e non solo diverso: un
progetto può stare fermo tre settimane e riprendere dove era — è il suo mestiere.
Comprimerlo perché è stato zitto butta la sola cosa che una sessione di progetto
ha in più della sua cartella.

**E una quarta metà, dall'08/09/2026: sulla stessa sessione scaduta passa ora un
secondo lavoro, che non è questo.** ``_harvest_project_diary`` legge i messaggi
nuovi, li riassume nella coda del diario e **non toglie un messaggio** (v.
``.agent/project-memory-plan.md``). Il recinto qui descritto non lo riguarda: non
difende la sessione dall'essere *letta*, difende i suoi messaggi dall'essere
*buttati*. Perciò i test di pianificazione qui sotto non chiedono più «non è
stato schedulato niente» — che confonderebbe i due lavori e farebbe fallire il
recinto per il motivo sbagliato — ma «non è stato schedulato un ``_archive``».
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from loguru import logger

from jenny.agent.autocompact import AutoCompact
from jenny.agent.gardener import GardenerStore
from jenny.agent.gardener_state import GardenerState, write_state
from jenny.session.manager import SessionManager

PROJECT = "project:patreon"
PERSONAL = "unified:default"


def _archives(scheduled: list) -> list:
    """I soli lavori che *accorciano* una sessione, fra quelli pianificati.

    Distinguere per nome della coroutine e non contarle: sulla stessa sessione
    scaduta passano due lavori con due mestieri opposti, e un conteggio che li
    confonde non prova piu' il recinto.
    """
    return [c for c in scheduled if getattr(c, "__name__", "") == "_archive"]



@pytest.fixture
def autocompact(tmp_path: Path) -> AutoCompact:
    consolidator = MagicMock()
    consolidator.compact_idle_session = AsyncMock(return_value="riassunto")
    return AutoCompact(
        sessions=SessionManager(tmp_path), consolidator=consolidator, session_ttl_minutes=30
    )


def _project_folder(autocompact: AutoCompact, name: str = "patreon") -> Path:
    """La cartella del progetto, dedotta come la deduce il codice.

    ``SessionManager.workspace`` è la radice del workspace, quindi il progetto
    sta in ``<workspace>/wikis/<nome>``: la stessa risoluzione di
    ``WorkspaceScopeResolver.for_project`` e di ``GardenerStore.for_project``.
    """
    folder = autocompact.sessions.workspace / "wikis" / name
    (folder / "wiki").mkdir(parents=True, exist_ok=True)
    return folder


def _promoted(autocompact: AutoCompact, name: str = "patreon") -> Path:
    """Un progetto con almeno una pagina e nessuna riga di diario da leggere."""
    folder = _project_folder(autocompact, name)
    (folder / "wiki" / "canone.md").write_text(
        "# Il canone\n\nQuel che la conversazione ha detto, promosso.\n", encoding="utf-8"
    )
    return folder


def _unread_journal(autocompact: AutoCompact, name: str = "patreon") -> Path:
    """Un progetto con una voce di diario che il giardiniere non ha ancora letto."""
    folder = _promoted(autocompact, name)
    journal = folder / "raw" / "journal"
    journal.mkdir(parents=True, exist_ok=True)
    (journal / "20260823.md").write_text(
        "# 2026-08-23\n\n- 09:00 — l'utente ha detto una cosa che non è ancora una pagina\n",
        encoding="utf-8",
    )
    return folder


def _stale(autocompact: AutoCompact, key: str) -> None:
    """Una sessione ferma da molto più del TTL, salvata su disco."""
    session = autocompact.sessions.get_or_create(key)
    session.messages.append({"role": "user", "content": "ciao"})
    session.updated_at = datetime.now() - timedelta(hours=6)
    autocompact.sessions.save(session)


# ── Il recinto ───────────────────────────────────────────────────────────


def test_a_project_is_never_archived_for_idleness(autocompact: AutoCompact) -> None:
    assert autocompact._may_archive_for_idleness(PROJECT) is False


@pytest.mark.parametrize(
    "key",
    [PERSONAL, "websocket:default", "cron:update_check", "dream:20260822-120000"],
    ids=["personale", "websocket", "cron", "dream"],
)
def test_everything_else_may_still_be(autocompact: AutoCompact, key: str) -> None:
    """La regola è «non un progetto», non «solo la personale».

    Una whitelist stretta bloccherebbe la generalizzazione per cui il passo 8
    esiste: la riga del piano dice che quel giro *può* girare su tutte le
    sessioni, purché non trascini il lavoro interno nel diario.
    """
    assert autocompact._may_archive_for_idleness(key) is True


async def test_the_write_is_guarded_too_not_just_the_entry(autocompact: AutoCompact) -> None:
    """``_archive`` è una coroutine che qualcuno pianifica: è raggiungibile da sola.

    Il controllo in ``check_expired`` protegge l'ingresso; questo protegge la
    riscrittura della sessione, che è l'ultimo istante utile.
    """
    _stale(autocompact, PROJECT)
    await autocompact._archive(PROJECT)
    autocompact.consolidator.compact_idle_session.assert_not_awaited()


async def test_and_the_personal_one_does_get_archived(autocompact: AutoCompact) -> None:
    """Controprova: se non passasse nemmeno questa, il recinto sarebbe un muro."""
    _stale(autocompact, PERSONAL)
    await autocompact._archive(PERSONAL)
    autocompact.consolidator.compact_idle_session.assert_awaited_once()


def test_the_candidate_list_filters_a_project_out(autocompact: AutoCompact) -> None:
    """La guardia all'ingresso è sull'elenco dei candidati, e per questo si prova.

    Con ``UNIFIED_SESSION_KEY`` cablato e un ``if`` sotto, togliere quell'``if``
    non faceva cadere niente: la sola chiave che ci arrivava era comunque
    ammessa. Qui invece l'elenco si può allargare — che è anche quel che farà la
    generalizzazione — e il filtro si vede lavorare.
    """
    # Si allarga l'**elenco**, non il metodo: sovrascrivere ``_idle_candidates``
    # proverebbe la copia scritta qui e non il filtro vero — è così che la prima
    # versione di questo test passava anche col filtro rimosso.
    autocompact._IDLE_CANDIDATE_KEYS = (PERSONAL, PROJECT)
    assert autocompact._idle_candidates() == (PERSONAL,)


def test_a_stale_project_is_never_scheduled_even_if_listed(
    autocompact: AutoCompact,
) -> None:
    """E la stessa porta, guardata dal lato della pianificazione."""
    _stale(autocompact, PROJECT)
    _stale(autocompact, PERSONAL)

    autocompact._IDLE_CANDIDATE_KEYS = (PERSONAL, PROJECT)
    scheduled: list = []
    autocompact.check_expired(scheduled.append)
    assert len(_archives(scheduled)) == 1, "solo la personale, anche con il progetto in elenco"
    for coro in scheduled:
        coro.close()


# ── La controprova: la lunghezza li raggiunge ────────────────────────────


def test_length_based_compaction_runs_for_every_session_including_projects() -> None:
    """Il recinto toglie il tempo, non la dimensione.

    ``maybe_consolidate_by_tokens`` è chiamata sul percorso di *ogni* turno, senza
    guardare la chiave: è quello che impedisce a una sessione di progetto di
    crescere per sempre ora che l'archiviazione per inattività non la tocca. Se
    quella chiamata diventasse condizionale sulla chiave, questo test è il posto
    in cui accorgersene.
    """
    src = Path("jenny/agent/loop.py").read_text(encoding="utf-8")
    # **Dopo** `prepare_session`, non la prima del file: la prima occorrenza sta
    # in `_on_context_overflow`, cioè *prima* nel testo, e cercarla da lì dava una
    # finestra vuota — un test che passava qualunque cosa ci si mettesse dentro
    # (scoperto per mutazione il 22/08).
    start = src.index("self.auto_compact.prepare_session(session, key)")
    call = src.index("await self.consolidator.maybe_consolidate_by_tokens(", start)
    assert call > start
    # Nessun ramo sulla chiave fra le due: la finestra è corta apposta, ed è dove
    # un filtro verrebbe aggiunto.
    window = src[start:call]
    for sospetto in ("is_project_session_key", "project:", "session_kind"):
        assert sospetto not in window, (
            f"la compattazione per lunghezza è diventata condizionale ({sospetto}): "
            "i progetti non hanno più niente che li contenga"
        )

# ── L'interruttore di P4 ─────────────────────────────────────────────────
#
# Accendere ``compact_projects_when_idle`` è l'ultimo gradino: da quel momento la
# conversazione di un progetto non è più l'unico depositario di niente — la
# verità sta nelle pagine, che entrano in contesto d'ufficio (T3 e T6.4) — quindi
# archiviarla non butta via nulla.
#
# Nota su cosa si perde comunque: il transcript **visibile** (``.jenny/webui/``)
# non viene toccato dalla compattazione, che riscrive ``sessions/``. L'amnesia è
# dell'agente, non del registro: una persona può ancora rileggere.


@pytest.fixture
def switched_on(tmp_path: Path) -> AutoCompact:
    consolidator = MagicMock()
    consolidator.compact_idle_session = AsyncMock(return_value="riassunto")
    return AutoCompact(
        sessions=SessionManager(tmp_path),
        consolidator=consolidator,
        session_ttl_minutes=30,
        compact_projects=True,
    )


def test_the_switch_opens_the_fence(switched_on: AutoCompact) -> None:
    assert switched_on._may_archive_for_idleness(PROJECT) is True


def test_opening_the_fence_alone_would_do_nothing(switched_on: AutoCompact) -> None:
    """**Il test che conta.** Il recinto e l'elenco dei candidati sono due cose
    diverse: l'elenco ne conteneva **una sola** (la conversazione personale),
    quindi allargare il solo filtro avrebbe lasciato i progetti fuori dal giro
    senza che nulla lo dicesse. La manopola deve fare entrambe le cose.
    """
    _stale(switched_on, PROJECT)

    candidates = switched_on._idle_candidates()

    assert PROJECT in candidates
    assert PERSONAL in candidates


def test_with_the_switch_off_the_project_is_not_even_looked_at(
    autocompact: AutoCompact,
) -> None:
    _stale(autocompact, PROJECT)

    assert autocompact._idle_candidates() == (PERSONAL,)


def test_a_project_with_no_conversation_is_not_a_candidate(switched_on: AutoCompact) -> None:
    """Si guardano i **file di sessione**, non le wiki: un progetto con cui non si
    è mai parlato non ha niente da compattare."""
    assert PROJECT not in switched_on._idle_candidates()


def test_a_project_name_with_an_underscore_survives_the_round_trip(
    switched_on: AutoCompact,
) -> None:
    """``project:mia_wiki`` diventa il file ``project_mia_wiki.jsonl``, e torna
    chiave sostituendo **solo il primo** underscore. Sbagliare qui produce una
    chiave che non corrisponde a nessuna sessione, cioè un progetto che non si
    compatta mai — in silenzio."""
    _stale(switched_on, "project:mia_wiki")

    assert "project:mia_wiki" in switched_on._idle_candidates()


def test_the_transcript_files_are_not_mistaken_for_sessions(
    switched_on: AutoCompact,
) -> None:
    """Accanto a ``project_x.jsonl`` vive ``websocket_project_x.jsonl``, che è
    un'altra cosa. Il glob è ancorato all'inizio del nome, e questo test lo
    tiene tale.

    **Si nega la forma, non una chiave.** La prima stesura negava esattamente
    ``"websocket:project:patreon"`` — e con il glob allargato il transcript entra
    come ``websocket:project_patreon``, che è una chiave *diversa*: l'asserzione
    passava e la mutazione sopravviveva. Quel che va escluso è qualunque
    candidato che non sia una sessione-progetto.
    """
    _stale(switched_on, "websocket:project:patreon")
    _stale(switched_on, PROJECT)

    candidates = switched_on._idle_candidates()

    assert PROJECT in candidates
    assert not [k for k in candidates if "websocket" in k], candidates


@pytest.mark.asyncio
async def test_the_switch_lets_an_idle_project_be_archived(switched_on: AutoCompact) -> None:
    _stale(switched_on, PROJECT)
    # Un progetto le cui pagine portano già quel che si è detto: il secondo
    # cancello (T2.6, sotto) chiede questo, e senza non si compatta.
    _promoted(switched_on)
    scheduled: list[object] = []

    switched_on.check_expired(scheduled.append)

    assert len(scheduled) == 1
    await scheduled[0]
    switched_on.consolidator.compact_idle_session.assert_awaited_once()
    assert switched_on.consolidator.compact_idle_session.await_args[0][0] == PROJECT


@pytest.mark.asyncio
async def test_internal_work_stays_out_even_with_the_switch_on(
    switched_on: AutoCompact,
) -> None:
    """La seconda guardia di ``_archive`` non è ridondante e non è coperta dalla
    manopola: un run di Dream non è una conversazione, e archiviarlo
    gli toglierebbe la coda di lavoro con cui si ricorda dei propri run."""
    _stale(switched_on, "dream:20260823-120000")

    await switched_on._archive("dream:20260823-120000")

    switched_on.consolidator.compact_idle_session.assert_not_awaited()


def test_the_knob_reaches_autocompact_from_the_config() -> None:
    """Il knob è inutile se non arriva: ``AgentDefaults`` →
    ``AgentLoop`` → ``AutoCompact``. Senza questo test la manopola si potrebbe
    accendere in ``config.json`` senza che cambi niente."""
    import inspect

    from jenny.agent.loop import AgentLoop
    from jenny.config.schema import AgentDefaults

    assert AgentDefaults().compact_projects_when_idle is False
    source = inspect.getsource(AgentLoop)
    assert "compact_projects=compact_projects_when_idle" in source
    assert "compact_projects_when_idle=defaults.compact_projects_when_idle" in source


# ── T2.6: le pagine prima della compattazione ────────────────────────────
#
# Il recinto aperto poggia su una premessa — «la verità sta nelle pagine» — che
# fino a T2.6 nessuno verificava, e che i due orologi rendevano di norma
# **falsa**: ``idleCompactAfterMinutes`` sta a 15 minuti, mentre il giardiniere
# vuole mezz'ora di quiete più fino a sei ore di distanza più un tick da
# mezz'ora. Su un progetto nuovo l'ordine normale era *compatta, poi promuovi*.
#
# Cosa **non** è questo cancello: una difesa dalla perdita della coda. Quella è
# T1.1 — se ``archive()` fallisce, i messaggi rimossi finiscono in
# ``raw/compacted/`` e, se nemmeno quella copia si scrive, non si tronca niente.
# Qui la perdita è di un altro genere: una conversazione compattata **bene**, il
# cui contenuto non è mai diventato pagine.


@contextmanager
def _log_lines(level: str = "INFO") -> Iterator[list[str]]:
    """Le righe di loguru emesse dentro il blocco.

    Il log è materia del test: un progetto che non si compatta mai si spiega
    **solo** da lì — la sessione resta intatta, e "intatta" non ha una voce.
    """
    seen: list[str] = []
    handler = logger.add(lambda message: seen.append(str(message)), level=level)
    try:
        yield seen
    finally:
        logger.remove(handler)


# (a) diario non letto ⇒ non si compatta


def test_a_project_with_unread_journal_lines_is_not_compacted(
    switched_on: AutoCompact,
) -> None:
    _stale(switched_on, PROJECT)
    _unread_journal(switched_on)

    scheduled: list[object] = []
    switched_on.check_expired(scheduled.append)

    assert scheduled == [], "compattata prima che il giardiniere avesse letto il diario"


@pytest.mark.asyncio
async def test_and_the_write_is_gated_too_not_just_the_scheduling(
    switched_on: AutoCompact,
) -> None:
    """``_archive`` è pianificabile da sola: il cancello vale anche là.

    È la stessa asimmetria del recinto — la prima guardia protegge l'ingresso,
    questa la riscrittura della sessione, che è l'ultimo istante utile.
    """
    _stale(switched_on, PROJECT)
    _unread_journal(switched_on)

    await switched_on._archive(PROJECT)

    switched_on.consolidator.compact_idle_session.assert_not_awaited()


def test_the_deferral_is_logged_with_its_reason(switched_on: AutoCompact) -> None:
    _stale(switched_on, PROJECT)
    _unread_journal(switched_on)

    with _log_lines() as lines:
        switched_on.check_expired(lambda coro: coro.close())

    detail = "\n".join(lines)
    assert PROJECT in detail
    assert "journal lines are not promoted yet" in detail, detail


def test_the_same_deferral_is_not_logged_every_minute(switched_on: AutoCompact) -> None:
    """Il giro TTL passa ogni sessanta secondi: a INFO la prima volta, poi zitto.

    Un cancello che parla una volta si legge; sessanta volte l'ora seppellisce
    il resto del log — cioè rende il progetto *meno* spiegabile, non più.
    """
    _stale(switched_on, PROJECT)
    _unread_journal(switched_on)

    with _log_lines() as lines:
        for _ in range(3):
            switched_on.check_expired(lambda coro: coro.close())

    deferrals = [line for line in lines if "deferring idle compaction" in line]
    assert len(deferrals) == 1, deferrals


# (b) delta vuoto ⇒ si compatta come prima


@pytest.mark.asyncio
async def test_an_empty_delta_compacts_exactly_as_before(switched_on: AutoCompact) -> None:
    _stale(switched_on, PROJECT)
    _promoted(switched_on)

    scheduled: list = []
    switched_on.check_expired(scheduled.append)

    assert len(scheduled) == 1
    await scheduled[0]
    switched_on.consolidator.compact_idle_session.assert_awaited_once()


def test_a_journal_already_read_to_the_end_does_not_block(switched_on: AutoCompact) -> None:
    """Il caso vero: il diario **esiste** e il giardiniere l'ha finito.

    Con il solo ``_promoted`` (nessun file di diario) il cancello passerebbe
    anche se leggesse il cursore alla rovescia: qui il file c'è, il cursore lo
    copre, e il delta è vuoto perché *è stato letto*.
    """
    folder = _unread_journal(switched_on)
    state = GardenerState().advanced(
        GardenerStore(folder, switched_on.sessions.workspace).read_delta()
    )
    write_state(folder, state)

    assert switched_on._pages_carry_the_project(PROJECT) is True


# La decisione in più: **anche** almeno una pagina


def test_a_project_with_no_pages_yet_is_not_compacted(switched_on: AutoCompact) -> None:
    """Delta vuoto ha due significati che da fuori si assomigliano.

    «Il giardiniere ha promosso tutto» e «in ``raw/journal/`` non c'è mai finito
    niente» danno lo stesso delta vuoto — ``read_journal_delta`` restituisce un
    delta vuoto quando la cartella del diario non esiste — e sono l'opposto: nel
    secondo caso la conversazione è l'unico depositario esistente, che è
    esattamente il caso da fermare. Il conto del rinvio di troppo è basso e
    limitato: la compattazione per **lunghezza** gira comunque a ogni turno.
    """
    _stale(switched_on, PROJECT)
    _project_folder(switched_on)  # cartella di progetto, zero pagine

    scheduled: list[object] = []
    with _log_lines() as lines:
        switched_on.check_expired(scheduled.append)

    assert scheduled == []
    assert "no pages yet" in "\n".join(lines)


def test_the_shape_measured_on_the_device_is_the_shape_that_defers(
    switched_on: AutoCompact,
) -> None:
    """T9.7, misurato sul telefono il 23/08 e replicato qui riga per riga.

    Il progetto piu' piccolo di quelli veri era la prova che il solo cancello
    del delta non basta, e le due meta' del suo albero lo dicono da sole:

    * ``raw/journal/`` **esiste ed e' vuota** — non manca. Il delta e' vuoto in
      entrambi i casi, quindi il cancello (a) passa: se la decisione si fermasse
      li', questa conversazione — l'unico depositario esistente — verrebbe
      riassunta.
    * l'unico ``.md`` sotto ``wiki/`` e' ``index.md``, cioe' **la mappa**. Un
      conteggio ingenuo (``rglob("*.md")`` senza l'esclusione dell'indice di
      :func:`iter_wiki_pages`) troverebbe una pagina e compatterebbe: la mappa
      dice quali pagine esistono, non e' una di esse, e qui non ne nomina
      nessuna. Le sottocartelle vuote del formato di ricerca ci sono per la
      stessa ragione — sono cartelle, non contenuto.

    Il pavimento sulle pagine e' quindi quel che regge la premessa di P4 su
    questo progetto, non il delta.
    """
    _stale(switched_on, PROJECT)
    folder = _project_folder(switched_on)
    (folder / "wiki" / "index.md").write_text(
        "# Il progetto\n\nAncora nessuna pagina.\n", encoding="utf-8"
    )
    for sub in ("concepts", "entities", "summaries"):
        (folder / "wiki" / sub).mkdir()
    (folder / "raw" / "journal").mkdir(parents=True)

    # Il cancello (a) da solo direbbe si': e' il punto della misura.
    store = GardenerStore.for_project(switched_on.sessions.workspace, "patreon")
    assert store is not None
    assert store.read_delta().is_empty is True

    scheduled: list[object] = []
    with _log_lines() as lines:
        switched_on.check_expired(scheduled.append)

    assert scheduled == []
    assert "no pages yet" in "\n".join(lines)


def test_a_missing_project_folder_defers_instead_of_compacting(
    switched_on: AutoCompact,
) -> None:
    """Nessuna cartella, nessuna pagina: il verso sicuro è il rinvio.

    È anche il modo in cui una ``wiki.wikisDir`` non di serie si presenta
    finché ``AgentLoop`` non passa ``projects_subdir`` qui — rumorosa, non muta.
    """
    _stale(switched_on, PROJECT)

    scheduled: list[object] = []
    with _log_lines() as lines:
        switched_on.check_expired(scheduled.append)

    assert scheduled == []
    assert "no project folder at wikis/patreon" in "\n".join(lines)


def test_the_projects_subdir_is_configurable_and_not_hardcoded(tmp_path: Path) -> None:
    """La cartella dei progetti è ``config.wiki.wikis_dir``, non ``"wikis"``.

    ``Consolidator`` la riceve già come ``projects_subdir``; questo cancello
    guarda la stessa cartella, quindi la prende dalla stessa manopola.
    """
    consolidator = MagicMock()
    consolidator.compact_idle_session = AsyncMock(return_value="riassunto")
    autocompact = AutoCompact(
        sessions=SessionManager(tmp_path),
        consolidator=consolidator,
        session_ttl_minutes=30,
        compact_projects=True,
        projects_subdir="progetti",
    )
    folder = tmp_path / "progetti" / "patreon"
    (folder / "wiki").mkdir(parents=True)
    (folder / "wiki" / "canone.md").write_text("# Canone\n", encoding="utf-8")

    assert autocompact._pages_carry_the_project(PROJECT) is True


def test_the_gate_counts_the_pages_without_opening_them(
    switched_on: AutoCompact, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T3.16. Al cancello serve solo *quante* pagine ci sono, e il titolo di
    ognuna costa una lettura: sul progetto vero più grande (``main``, 52 pagine)
    erano 52 ``read_text`` e 1,30 ms, ora zero e 0,47 ms — a ogni giro TTL, cioè
    ogni minuto, per ogni progetto scaduto.

    L'asserzione guarda i file aperti, non il tempo, e tiene ferma la risposta:
    il cancello passa ancora. Un cancello più veloce che risponde diverso non
    sarebbe un'ottimizzazione ma un difetto.
    """
    folder = _project_folder(switched_on)
    for i in range(5):
        (folder / "wiki" / f"pagina{i}.md").write_text(
            f"# Pagina {i}\n\nTesto.\n", encoding="utf-8"
        )
    opened: list[Path] = []
    real_read_text = Path.read_text

    def spy(self: Path, *args, **kwargs):
        opened.append(self)
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", spy)

    assert switched_on._pages_carry_the_project(PROJECT) is True
    assert [p.name for p in opened if p.name.startswith("pagina")] == []


# (c) la sessione personale non è toccata dal cancello nuovo


def test_the_personal_session_is_untouched_by_the_new_gate(switched_on: AutoCompact) -> None:
    """La personale ha Dream, non il giardiniere.

    Legarla a un diario che non ha spegnerebbe la compattazione proprio dove
    funziona — e nessun file di progetto esiste in questo test.
    """
    assert switched_on._pages_carry_the_project(PERSONAL) is True


@pytest.mark.asyncio
async def test_the_personal_session_still_compacts_with_the_switch_on(
    switched_on: AutoCompact,
) -> None:
    _stale(switched_on, PERSONAL)

    scheduled: list = []
    switched_on.check_expired(scheduled.append)

    assert len(scheduled) == 1
    await scheduled[0]
    switched_on.consolidator.compact_idle_session.assert_awaited_once()
    assert switched_on.consolidator.compact_idle_session.await_args[0][0] == PERSONAL


@pytest.mark.asyncio
async def test_the_personal_session_compacts_with_the_switch_off_too(
    autocompact: AutoCompact,
) -> None:
    """Controprova sul verso opposto: il cancello nuovo non è un interruttore
    generale mascherato."""
    _stale(autocompact, PERSONAL)

    scheduled: list = []
    autocompact.check_expired(scheduled.append)

    assert len(scheduled) == 1
    await scheduled[0]
    autocompact.consolidator.compact_idle_session.assert_awaited_once()


# (d) manopola spenta ⇒ niente si compatta, come prima


def test_with_the_knob_off_a_promoted_project_still_does_not_compact(
    autocompact: AutoCompact,
) -> None:
    """Anche col diario letto e le pagine al loro posto: la manopola resta la
    prima parola, e il cancello nuovo non è una scorciatoia per accenderla."""
    _stale(autocompact, PROJECT)
    _promoted(autocompact)

    scheduled: list = []
    autocompact.check_expired(scheduled.append)

    assert _archives(scheduled) == []
    assert autocompact._idle_candidates() == (PERSONAL,)
    # E il secondo lavoro c'e', perche' e' l'altro verso dello stesso invariante:
    # la conversazione del progetto resta intera **e** quel che vi si e' detto
    # sulla persona non si perde.
    assert [getattr(c, "__name__", "") for c in scheduled] == ["_harvest_project_diary"]
    for coro in scheduled:
        coro.close()
