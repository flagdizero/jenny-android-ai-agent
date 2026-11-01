"""Cosa porta dentro il prompt una passata del giardiniere, e cosa no. **T7.8.**

Il confine fra un progetto e il diario personale vale in **un verso solo**, ed è
voluto: un progetto non entra nel diario (l'imbuto di ``append_history``), ma
l'identità esce sempre — ``SOUL.md`` e ``USER.md`` li compone ``ContextBuilder``
dalla radice dell'installazione per ogni tipo di sessione. La
riga è «chi sei viaggia, dove altro lavori no» (T7.1), e questo file la misura
sul solo attore in cui non c'è nessun utente a scegliere: la passata del
giardiniere, la cui unica cartella scrivibile è ``wikis/<nome>/wiki/``.

Misurato il 23/08 prima del fix, sul prompt di sistema vero di una chiave
``gardener:``: arrivavano **tutti e cinque** — i tre file di identità, l'elenco
delle wiki (allora una rubrica compilata, con ogni wiki, persona e pianta) e il blocco
``Recent History`` con la coda **della conversazione personale**. E l'ultimo era
il verso rovesciato: la *conversazione* di quel progetto non prende niente da
quella coda (``read_recent_history_for_prompt``, primo ramo), mentre la passata di
manutenzione — che non ha nemmeno un utente con cui parlare — si prendeva la metà
personale. Da lì un fatto personale può entrare in un progetto per una strada che
il template stesso apre: la regola del controllo incrociato dice «cerca un fatto
stabile che l'utente ha detto e che il diario non ha registrato» e la risposta è
un ``journal_append``, cioè l'ingresso della passata dopo.

**La decisione, e la sua metà negativa.** I **due** file di identità
(``SOUL.md``, ``USER.md``) **restano**: il turno di quel progetto li ha per
progetto dichiarato (T7.1), la passata promuove le righe che quel turno ha
scritto, e toglierli vorrebbe dire filare la specie di sessione dentro
``_get_identity``/``_load_bootstrap_files``, cioè il percorso di prompt più
condiviso del repo, per lasciare l'unico attore senza identità a scrivere pagine
che l'utente legge. Si chiudono i **tre** blocchi che non sono identità: la
rubrica fra progetti, la coda di qualcun altro, e ``MEMORY.md``. Sono tre
restringimenti — nessuna lettura si allarga.

**``MEMORY.md`` è il terzo, e ci è arrivato dopo (24/08).** Stava sul lato
identità per classificazione, non per misura: contate una per una, le sue voci
servono ognuna a **un** progetto — un server a una wiki, un agente interno a
un'altra, il repo a una terza — cioè sono «dove altro lavori». Per il giardiniere
c'è un argomento in più che non dipende da quella misura: i suoi quattro tool di
lettura hanno ``allowed_dir = wikis/<nome>`` (``GardenerStore.build_tools``),
quindi la sua cassetta quel file lo **rifiuta** — il prompt gli spingeva dentro
ciò che il confinamento gli vieta di aprire, mentre ``agent/gardener.md`` gli dice
«work only from those». La conversazione di progetto, che invece può leggerlo,
riceve al suo posto una riga che dice dov'è (``get_memory_pointer_context``); la
passata no, perché a lei quella riga indicherebbe un file che non può aprire *e*
la inviterebbe ad aprirlo.

**Cosa provano questi test.** I primi quattro eseguono il codice vero:
costruiscono il prompt di sistema che il loop costruirebbe per quella chiave
(canale interno, nessuno scope legato, quindi radice dell'installazione) e ci
cercano dentro dei marcatori piantati nei file. Il quinto e il sesto girano sulla
regola della coda direttamente, che è dove sta la decisione. Nessuno di questi è
un grep su prosa di template.
"""

from __future__ import annotations

import pathlib

from jenny.agent.context import ContextBuilder
from jenny.agent.gardener import GardenerStore
from jenny.agent.memory import MemoryStore, is_gardener_session_key

WIKIS = "## Wikis"
RECENT_HISTORY = "# Recent History"

PERSONAL = "unified:default"


def _install(root: pathlib.Path) -> None:
    """Un'installazione con i cinque posti da cui il personale può viaggiare."""
    (root / "memory").mkdir(parents=True, exist_ok=True)
    (root / "SOUL.md").write_text("# Jenny\n\nTono asciutto. SOULMARK\n", encoding="utf-8")
    (root / "USER.md").write_text(
        "# Chi sei\n\n- Corso di nuoto: giovedì alle 18. USERMARK\n", encoding="utf-8"
    )
    (root / "memory" / "MEMORY.md").write_text(
        "# Long-term\n\n- MEMMARK: i piani di stipendio\n", encoding="utf-8"
    )
    # Un'altra wiki, con uno scope riconoscibile: e' quel che il blocco elenca.
    balcone = root / "wikis" / "balcone"
    (balcone / "wiki").mkdir(parents=True, exist_ok=True)
    (balcone / "AGENTS.md").write_text(
        "---\nsummary: le piante di casa, il Ficus in testa\n---\n\n# balcone\n",
        encoding="utf-8",
    )


def _project(root: pathlib.Path, name: str = "casa") -> pathlib.Path:
    project = root / "wikis" / name
    (project / "wiki").mkdir(parents=True, exist_ok=True)
    (project / "wiki" / "index.md").write_text("# Casa\n\n- [[furgone]]\n", encoding="utf-8")
    return project


def _gardener_key(root: pathlib.Path, name: str = "casa") -> str:
    """La chiave vera della passata, dallo store — non una stringa scritta a mano.

    Scritta a mano il test resterebbe verde il giorno in cui il prefisso cambia,
    provando una cosa su una chiave che non esiste più.
    """
    store = GardenerStore.for_project(root, name)
    assert store is not None
    return store.session_key()


def _gardener_system_prompt(root: pathlib.Path, name: str = "casa") -> str:
    """Il prompt di sistema che il loop costruisce per una passata.

    ``channel`` interno e nessun ``workspace``: è quel che fa
    ``AgentLoop._build_initial_messages``, dove
    ``WorkspaceScopeResolver.for_turn`` ripiega su ``default()`` per ogni canale
    che non è quello scopato — quindi la radice è l'installazione, e il blocco di
    progetto non si rende affatto.

    **E i tool della passata, che qui mancavano.** Fino al 25/08 questo helper
    chiamava ``build_system_prompt`` senza ``available_tools``, cioè lasciava il
    context builder nello stato «non so quali tool ha questo turno» — dove per
    contratto ogni gate per-tool si apre. In produzione quei nomi arrivano sempre
    (``_build_initial_messages`` passa ``turn_tools.tool_names``, e la passata
    porta il proprio registry), quindi il prompt misurato qui era **6.348
    caratteri più grosso** di quello vero: fra l'altro si prendeva
    ``agent/scheduling.md``, che in una passata non c'è mai stato. Un file che
    dice di misurare «il prompt di sistema vero di una chiave ``gardener:``» non
    può costruirlo con meno di quel che il loop gli dà: le asserzioni negative
    diventano più larghe del vero (provano l'assenza da un prompt che nessuno
    riceve) e quelle positive misurano prosa che il modello non legge.

    La cassetta viene dallo store, non da un elenco scritto a mano, per la stessa
    ragione di ``_gardener_key``.
    """
    store = GardenerStore.for_project(root, name)
    assert store is not None
    return ContextBuilder(root).build_system_prompt(
        channel="internal",
        session_key=store.session_key(),
        available_tools=sorted(store.build_tools().tool_names),
    )


# ── quel che resta, e la ragione per cui resta ───────────────────────────────


def test_the_identity_still_travels_into_a_gardener_pass(tmp_path) -> None:
    """**Decisione: i due file di identità restano.** Non è un difetto non chiuso.

    Il turno del progetto li ha per progetto dichiarato (T7.1: «il profilo
    personale non è segreto per un progetto»), e la passata promuove le righe che
    quel turno ha scritto — vedrebbe *meno* di chi le ha scritte. Il caso in cui
    la decisione conta: ``gardener.md`` chiede di recuperare una riga «in their
    terms», e i referenti di quelle parole stanno qui.

    Separato dal test su ``MEMORY.md`` qui sotto di proposito: sono due decisioni
    diverse con due argomenti diversi, e una che cambia non deve poter trascinare
    l'altra dentro la stessa asserzione.
    """
    root = tmp_path
    _install(root)
    _project(root)

    prompt = _gardener_system_prompt(root)

    assert "SOULMARK" in prompt
    assert "USERMARK" in prompt


def test_a_gardener_pass_does_not_see_the_long_term_memory(tmp_path) -> None:
    """``MEMORY.md`` non entra, e per la passata l'argomento è duplice.

    Non è identità — misurato voce per voce, ognuna serve a un progetto solo,
    cioè è «dove altro lavori» — e in più la cassetta della passata quel file lo
    rifiuta comunque (``allowed_dir = wikis/<nome>``), quindi il prompt le
    spingeva dentro ciò che il confinamento le vieta di aprire.

    E **nemmeno il puntatore** che la conversazione di progetto riceve al suo
    posto: indica un percorso che i tool della passata non aprono, e sopra a
    quello *invita* ad aprirlo.

    Nota sul confine di questa asserzione, perché altrimenti prova più di quel che
    può: il percorso ``memory/MEMORY.md`` **è già** nel prompt della passata, dal
    listato dei file di ``agent/identity.md``, insieme a ``memory/history.jsonl``
    — due path che la sua cassetta rifiuta. È un'incoerenza che precede questo
    cancello e che non si chiude con lo stesso booleano (per Dream quella riga è
    giusta): è registrata a parte. Quindi
    qui il marcatore negativo è **il testo del puntatore preso dal codice**, non
    il path: un letterale scritto a mano diventerebbe verde da solo il giorno che
    la frase cambia, cioè proverebbe zero.
    """
    root = tmp_path
    _install(root)
    _project(root)

    pointer = MemoryStore(root).get_memory_pointer_context()
    assert pointer, "il puntatore deve esistere, altrimenti l'asserzione sotto è vuota"

    prompt = _gardener_system_prompt(root)

    assert "MEMMARK" not in prompt
    assert pointer not in prompt


def test_a_gardener_pass_is_not_shown_paths_its_toolbox_refuses(tmp_path) -> None:
    """I tre file dell'installazione non le vengono nominati. **D8.**

    Non nasce da un difetto osservato ma dal test di un'altra correzione: il
    listato di `agent/identity.md` (blocco `## Workspace`, nelle prime dieci righe
    di *ogni* prompt) nomina `memory/MEMORY.md`, `memory/history.jsonl` e
    `skills/<nome>/SKILL.md`, mentre i quattro tool di lettura della passata hanno
    `allowed_dir = wikis/<nome>` — quindi sono tre indirizzi giusti verso porte
    chiuse, davanti a un attore il cui prompt le dice «work only from those».

    **La riga `Your workspace is at:` resta, e l'asserzione la fissa** perché è la
    metà che non va tolta: per la passata quella radice è *vera*, è la base su cui
    si risolvono i percorsi relativi che il suo prompt le insegna a scrivere come
    `wikis/<nome>/...`. Toglierla romperebbe la scrittura invece di stringere una
    lettura — ed è l'errore facile da fare qui.

    **L'asserzione guarda il blocco `## Workspace`**, e la prima versione
    sbagliava proprio lì: i due percorsi comparivano anche altrove, quindi un
    `not in prompt` bocciava per prosa che non è un elenco indirizzato a *te*.

    **Dal 25/08 i due `memory/` non ci sono più in nessun punto, e l'asserzione lo
    dice.** L'altra occorrenza era `## Which File a Fact Belongs In`
    (`agent/tool_contract.md`), che a una passata non arriva più (**D11**, il test
    qui sotto); quella di `agent/scheduling.md` non c'era mai stata — la vedeva
    solo questo file, che costruiva il prompt senza i tool del turno (v.
    `_gardener_system_prompt`). Le due forme restano entrambe perché provano cose
    diverse: il blocco che la riga di radice resta **senza** di loro, il prompt
    intero che nessun altro blocco li rinomina.

    `SKILL.md` resta misurato sul solo blocco, e non è una dimenticanza: con una
    skill installata l'indice delle skill quel percorso lo nomina — stessa classe
    (un indirizzo verso una porta che la cassetta rifiuta), residuo già registrato
    accanto a D8, non allargato qui di soppiatto.
    """
    root = tmp_path
    _install(root)
    _project(root)

    prompt = _gardener_system_prompt(root)
    block = prompt.split("## Workspace", 1)[1].split("\n## ", 1)[0]

    assert "SKILL.md" not in block
    assert "Your workspace is at:" in block, "la radice dei percorsi relativi resta"
    assert "memory/MEMORY.md" not in prompt
    assert "memory/history.jsonl" not in prompt


def test_everyone_else_still_gets_them(tmp_path) -> None:
    """Il ragionamento è **per attore**, non per specie di sessione.

    Dream monta `allowed_dir=workspace` più `skills/`, un subagent ne ha la radice di lettura (T4.5) e una conversazione di
    progetto legge ovunque per contratto di `agent/project.md`. Per tutti quelli i
    tre percorsi si aprono — e nella chat personale quel listato è l'**unico** posto
    in cui `history.jsonl` viene nominato. Un cancello sul solo «è interno?» li
    avrebbe presi tutti.
    """
    root = tmp_path
    _install(root)
    project = _project(root)

    for key, workspace in (
        (PERSONAL, None),
        ("project:casa", project),
        ("dream:20260825-120537", None),
        ("internal:direct", None),
    ):
        prompt = ContextBuilder(root).build_system_prompt(
            channel="internal", session_key=key, workspace=workspace
        )
        block = prompt.split("## Workspace", 1)[1].split("\n## ", 1)[0]
        assert "memory/history.jsonl" in block, key


_FACT_ROUTING = "## Which File a Fact Belongs In"
_OUTPUT_CONVENTIONS = "## Where Produced Files Go"


def test_a_gardener_pass_is_not_told_where_facts_go_in_the_installation(tmp_path) -> None:
    """I due blocchi che parlano della radice come cartella di lavoro. **D11.**

    `agent/tool_contract.md` li chiude già con un flag — quello nato quando un
    subagent scrisse il file di prova in `wikis/<nome>/output/` obbedendo alla
    lettera a un prompt che gli dava le convenzioni del workspace dentro una
    cartella di progetto. Il flag è `is_wiki_root(root)`, cioè una domanda sulla
    **cartella**, e per una passata quella domanda risponde a un'altra: un turno
    interno non ha scope legato, quindi la radice è l'installazione e il flag è
    falso — mentre la sua superficie di scrittura è `wikis/<nome>/wiki/` e
    nient'altro.

    **Il costo non era il contesto sprecato.** `## Which File a Fact Belongs In`
    dice «`memory/MEMORY.md` — project context: what is going on, what was
    decided, what is still open», e lo diceva all'unico attore il cui mestiere è
    *produrre* esattamente quello: gli nominava come casa dei fatti decisi di un
    progetto il file da cui il cancello di Fase 1 li ha appena tolti, e che la sua
    cassetta rifiuta comunque.

    **Il difetto registrato come D11 era un altro blocco** — `agent/scheduling.md`
    — ed era già chiuso da un gate per-tool: nella cassetta della passata `cron`
    non c'è. Si vedeva solo dal fixture di questo file, che costruiva il prompt
    senza i tool del turno. È la ragione per cui il test qui sotto asserisce anche
    la metà positiva: un flag che si spegne per tutti chiuderebbe la stessa riga
    senza che nessuna asserzione se ne accorga.
    """
    root = tmp_path
    _install(root)
    _project(root)

    prompt = _gardener_system_prompt(root)

    assert _FACT_ROUTING not in prompt
    assert _OUTPUT_CONVENTIONS not in prompt


def test_a_conversation_still_learns_where_facts_go(tmp_path) -> None:
    """La metà positiva, e vale quanto l'altra.

    Quel routing esiste perché prima viveva solo nella scheda di aiuto della
    WebUI, cioè in un posto che nessun modello legge, e a «ricordati questo» si
    scriveva dove capitava. Chiuderlo per la passata non deve chiuderlo per chi ha
    davanti un utente che dice «ricordati questo».
    """
    root = tmp_path
    _install(root)

    prompt = ContextBuilder(root).build_system_prompt(
        channel="webui", session_key=PERSONAL
    )

    assert _FACT_ROUTING in prompt
    assert _OUTPUT_CONVENTIONS in prompt


# ── quel che si chiude ───────────────────────────────────────────────────────


def test_a_gardener_pass_does_not_see_the_other_projects(tmp_path) -> None:
    """La rubrica fra progetti non entra: è «dove altro lavori».

    Formulato sull'effetto e non sul mezzo, come il gemello di
    ``test_a_project_prompt_does_not_name_another_project``: «non contiene
    ``## Wikis``» resterebbe verde il giorno in cui l'elenco arriva da
    un'altra parte.

    Tre ragioni, e la terza è solo sua: la scelta del progetto è già stata fatta
    (dal cron), la vita privata ci viaggia dentro, ed è un elenco di pagine che i
    suoi tool **non possono aprire** — davanti a una passata la cui regola 3 è
    «una pagina che nomina una cosa che ha una pagina sua la linka».
    """
    root = tmp_path
    _install(root)
    _project(root)

    prompt = _gardener_system_prompt(root)

    assert WIKIS not in prompt
    for other in ("balcone", "Ficus"):
        assert other not in prompt, f"il prompt della passata su casa nomina {other}"


def test_a_gardener_pass_does_not_see_the_personal_conversation(tmp_path) -> None:
    """Il verso rovesciato, chiuso: la coda personale non entra nella passata.

    La *conversazione* del progetto non prende niente da questa coda; la passata
    di manutenzione si prendeva la metà personale. Qui si misura sul prompt
    intero, cioè comprese le due strade per cui una voce può entrare (la propria
    chiave e il ramo personale).
    """
    root = tmp_path
    _install(root)
    _project(root)
    MemoryStore(root).append_history("HISTMARK: detto nella chat personale", session_key=PERSONAL)

    prompt = _gardener_system_prompt(root)

    assert "HISTMARK" not in prompt
    assert RECENT_HISTORY not in prompt


def test_the_pass_still_reads_its_own_entries(tmp_path) -> None:
    """**Non è un ``return []``**, ed è la differenza che il ramo interno protegge.

    Un job rilegge i propri run: quel che si toglie è la coda di qualcun altro.
    Oggi la chiave della passata porta l'orologio, quindi in produzione il blocco
    esce comunque vuoto — ma il giorno in cui diventasse stabile un ``return []``
    negherebbe i propri run in silenzio, e questo test cade al posto dell'utente.
    """
    root = tmp_path
    store = MemoryStore(root)
    key = _gardener_key(_with_project(root))
    store.append_history("OWNMARK: la passata di prima", session_key=key)
    store.append_history("HISTMARK: la chat personale", session_key=PERSONAL)

    entries = store.read_recent_history_for_prompt(0, session_key=key)

    assert [e["content"] for e in entries] == ["OWNMARK: la passata di prima"]


def test_the_other_internal_sessions_are_untouched(tmp_path) -> None:
    """Il controllo negativo: cambia **un** ramo, non la regola.

    ``cron`` è il ramo che esiste per la cura dell'amnesia dei job ricorrenti, e la
    metà personale gli serve — gira nella chat personale. Se questo cade insieme
    al test sopra, la modifica ha preso tutti gli interni invece di uno.
    """
    root = tmp_path
    store = MemoryStore(root)
    store.append_history("HISTMARK: la chat personale", session_key=PERSONAL)

    entries = store.read_recent_history_for_prompt(0, session_key="cron:daily")

    assert [e["content"] for e in entries] == ["HISTMARK: la chat personale"]
    assert not is_gardener_session_key("cron:daily")


def _with_project(root: pathlib.Path) -> pathlib.Path:
    """La cartella del progetto, per le prove che non costruiscono il prompt."""
    _project(root)
    return root
