"""Shared session key constants and helpers."""

from __future__ import annotations

import re

from loguru import logger

__all__ = [
    "CRON_SESSION_PREFIX",
    "DREAM_SESSION_PREFIX",
    "GARDENER_SESSION_PREFIX",
    "HEARTBEAT_SESSION_KEY",
    "INTERNAL_SESSION_PREFIX",
    "PROJECT_SESSION_PREFIX",
    "SUBAGENT_SESSION_PREFIX",
    "UNIFIED_SESSION_KEY",
    "WEBUI_CHANNEL",
    "internal_session_kind",
    "is_internal_session_key",
    "is_personal_session_key",
    "is_project_session_key",
    "is_valid_project_name",
    "normalize_user_session_key",
    "project_session_key",
    "session_key_for_channel",
    "session_kind",
    "subagent_session_key",
]

UNIFIED_SESSION_KEY = "unified:default"

# Il canale della WebUI. E' l'unico su cui un ``chat_id`` puo' nominare un
# progetto: v. :func:`session_key_for_channel`. Il nome e' quello che
# ``WebSocketChannel.name`` mette negli ``InboundMessage``.
WEBUI_CHANNEL = "websocket"

# Prefisso di una sessione-progetto (``project:<id>``): una conversazione con
# l'utente, legata a una cartella, che **non** alimenta la memoria di lungo
# periodo. E' la terza categoria, e la ragione per cui questo modulo ha una
# classificazione ternaria invece di un booleano: v. :func:`session_kind`.
#
# L'id e' stabile ai rinomini della cartella (la cartella e' l'indirizzo, l'id
# e' l'identita'), quindi non e' derivabile dal path: lo assegna chi crea il
# legame.
PROJECT_SESSION_PREFIX = "project:"

# La forma di un nome di progetto, che e' un nome di cartella dentro ``wikis/``:
# niente separatori, niente punto iniziale (sarebbe nascosta), niente ``..``.
#
# **Sta qui e non dove viene chiesto all'utente.** Il nome arriva da un client —
# nel dialogo del chip e, appena dopo, in ogni ``chat_id`` — e i due punti devono
# rispondere alla stessa domanda: il controllo nel dialogo e' cortesia, questi
# sono i caratteri che possono diventare una sessione e una cartella.
_PROJECT_NAME_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


def is_valid_project_name(name: str) -> bool:
    """True se *name* puo' essere il nome di un progetto."""
    return bool(_PROJECT_NAME_RE.match(name)) and ".." not in name

# Prefisso delle sessioni Tier-2 dei subagent (``subagent:<lineage_id>``).
# Sono storia di lavoro interno, non conversazioni: non devono comparire in
# nessun elenco user-facing ne essere leggibili dalle route HTTP della WebUI.
SUBAGENT_SESSION_PREFIX = "subagent:"

# Prefisso dei run di un job cron (``cron:<job_id>``, v. ``cron/session_turns``).
CRON_SESSION_PREFIX = "cron:"

# Prefisso dei run di Dream (``dream:<timestamp>``, v. ``agent/memory``).
DREAM_SESSION_PREFIX = "dream:"

# Prefisso di una passata del giardiniere
# (``gardener:<progetto>-<timestamp>``, v. ``agent/gardener``). Il nome del
# progetto sta nella chiave perche' le passate di due progetti sono lavori
# distinti; il timestamp perche' **ogni passata parte da zero**: il giardiniere
# non ha memoria dei propri giri, la sua memoria sono le pagine che ha scritto e
# il cursore.
GARDENER_SESSION_PREFIX = "gardener:"

# Prefisso del turno interno generico (``internal:direct``): e' il default di
# ``AgentLoop.process_direct``, che oggi nessun chiamante di produzione lascia
# scoperto — tutti passano una chiave esplicita.
INTERNAL_SESSION_PREFIX = "internal:"

# Sessione dell'heartbeat: chiave *nuda*, senza suffisso, perche ce n'e una sola
# (``cron_dispatch._run_heartbeat``). Sta qui e non inline nel dispatcher perche
# e' anche il discriminante di :func:`is_internal_session_key`.
HEARTBEAT_SESSION_KEY = "heartbeat"

# Vocabolario delle sessioni interne (lavoro del sistema, non conversazione con
# l'utente): prefisso -> *kind*. Elencare le sessioni e per definizione
# un'operazione user-facing: chi lo fa deve filtrare con
# :func:`is_internal_session_key`.
#
# Questo modulo e' la porta d'ingresso del vocabolario: chi ha bisogno di
# distinguere le sessioni interne importa da qui invece di ricopiarsi la lista
# dei prefissi, cosi il lato scrittura e il lato lettura non possono divergere.
_INTERNAL_KIND_BY_PREFIX: tuple[tuple[str, str], ...] = (
    (SUBAGENT_SESSION_PREFIX, "subagent"),
    (CRON_SESSION_PREFIX, "cron"),
    (DREAM_SESSION_PREFIX, "dream"),
    (GARDENER_SESSION_PREFIX, "gardener"),
    (INTERNAL_SESSION_PREFIX, "internal"),
)

# Chiavi interne senza suffisso: vanno confrontate per uguaglianza, non per
# prefisso, altrimenti non matchano (era il caso di ``heartbeat``, che il
# prefisso ``"heartbeat:"`` non ha mai intercettato).
_INTERNAL_KIND_BY_KEY: dict[str, str] = {HEARTBEAT_SESSION_KEY: "heartbeat"}

# Chiavi utente nella forma vecchia ``<canale>:<chat_id>``. Non esistono piu' come
# sessioni: le scriveva ``CronTool.set_context`` nei payload dei job, quindi si
# incontrano solo rileggendo un ``jobs.json`` scritto prima della sessione unica —
# **e le voci di ``history.jsonl``** scritte allora, che a Dream servono ancora.
#
# **Elenco chiuso, e non un pattern.** Un pattern "``<parola>:<parola>``"
# prenderebbe anche ``project:<id>``, che e' una sessione vera: collassarla sulla
# conversazione personale farebbe girare un job di progetto nella chat personale,
# cioe' esattamente la confusione che le sessioni-progetto esistono per evitare.
_LEGACY_CHANNEL_KEY_PREFIXES: tuple[str, ...] = ("websocket:", "telegram:")

# **La whitelist dei personali**, cioe' l'elenco di chi puo' alimentare
# ``MEMORY.md``. Un solo membro vivo — la conversazione unica — piu' i prefissi
# legacy qui sopra, che sessioni non sono piu' ma sono la conversazione con
# l'utente scritta nelle voci di history di prima della sessione unica: tenerle
# fuori renderebbe invisibile a Dream la storia gia' sul disco.
_PERSONAL_SESSION_KEYS: frozenset[str] = frozenset({UNIFIED_SESSION_KEY})

# Prefissi per cui si e' gia' avvisato. Solo deduplica del log — non entra nella
# classificazione — e sta qui perche' :func:`session_kind` viene chiamata *per
# voce* su ``history.jsonl`` (v. ``MemoryStore.build_dream_prompt``): un warning
# per riga trasformerebbe un file da mille voci in mille righe di log. Il tetto
# e' contro una chiave ostile: le session key arrivano anche da disco.
_UNCLASSIFIED_WARNED: set[str] = set()
_UNCLASSIFIED_WARN_CAP = 32


def _warn_unclassified(key: str) -> None:
    """Dice a voce che una chiave non e' in nessun vocabolario, una volta per prefisso."""
    prefix = key.split(":", 1)[0]
    if prefix in _UNCLASSIFIED_WARNED:
        return
    if len(_UNCLASSIFIED_WARNED) < _UNCLASSIFIED_WARN_CAP:
        _UNCLASSIFIED_WARNED.add(prefix)
    logger.warning(
        "session key {!r} is in no vocabulary of jenny.session.keys: classified as "
        "'internal' (does not feed MEMORY.md, does not appear in user-facing "
        "lists). If it is a new category, register it here.",
        key,
    )


def internal_session_kind(key: str) -> str | None:
    """Il *kind* di lavoro interno a cui appartiene la session key, o ``None``.

    Ritorna una delle etichette del vocabolario (``"subagent"``, ``"cron"``,
    ``"dream"``, ``"gardener"``, ``"internal"``, ``"heartbeat"``) quando la chiave
    e' di una sessione interna, ``None`` quando e' una conversazione utente.

    Serve a chi non ha bisogno solo del si/no di
    :func:`is_internal_session_key` ma anche di *quale* interno sia — per
    esempio per instradare la contabilita dei token su un bucket diverso.
    """
    kind = _INTERNAL_KIND_BY_KEY.get(key)
    if kind is not None:
        return kind
    for prefix, prefix_kind in _INTERNAL_KIND_BY_PREFIX:
        if key.startswith(prefix):
            return prefix_kind
    return None


def session_kind(key: str) -> str:
    """La categoria di una session key: ``"internal"``, ``"project"``, ``"personal"``.

    **La classificazione delle sessioni sta qui e in nessun altro posto.** Le tre
    categorie non sono una tassonomia per bellezza: rispondono a domande diverse
    e vengono trattate diversamente da chi tiene la memoria.

    - ``internal`` — lavoro del sistema (cron, Dream, il giardiniere, subagent,
      heartbeat). Non e' conversazione, non compare negli elenchi user-facing, e
      rilegge le *proprie* voci in coda di lavoro perche' e' cosi che un job si
      ricorda dei suoi run passati.
    - ``project`` — conversazione con l'utente dentro un progetto. Non alimenta
      la memoria di lungo periodo e non condivide niente della contabilita
      personale: ne' la coda, ne' il cursore. La sua continuita vive nella
      propria sessione e nei file che scrive.
    - ``personal`` — la conversazione con l'utente. E' la sola che alimenta il
      diario da cui Dream costruisce ``MEMORY.md``.

    Il predicato che serviva prima era binario, e :func:`is_personal_session_key`
    aveva scritto in docstring che il giorno in cui fosse nata una terza
    categoria la blacklist "non e' interna" sarebbe diventata silenziosamente
    sbagliata. Quel giorno e' questo: una chiave ``project:`` non e' interna, e
    con la vecchia definizione risultava percio' *personale* — cioe' avrebbe
    alimentato ``MEMORY.md``. Le tre etichette qui sono un insieme chiuso; chi ne
    ha bisogno usa i tre predicati sotto e non riscrive il confronto.

    **Il residuo cade su ``internal``, e non su ``personal``** (deciso il 23/08,
    T4.10). Fino a oggi le prime due erano whitelist e la terza era "tutto il
    resto": un *kind* nuovo il cui prefisso qualcuno si dimenticasse di
    registrare qui finiva nel bucket che Dream consuma
    (``MemoryStore.build_dream_prompt`` filtra su
    :func:`is_personal_session_key`), cioe' il suo contenuto entrava in
    ``MEMORY.md``. Quel guasto e' silenzioso e permanente: ``MEMORY.md`` non dice
    da dove viene una riga.

    Il guasto opposto — un *kind* legittimo nuovo trattato come interno — costa
    che i suoi turni non tornino al chiamante e non compaiano negli elenchi
    user-facing. **Si vede al primo giro** (era misurabile: tre test di questa
    suite, che usavano chiavi sintetiche ``api:...`` e ``system``, sono caduti
    subito con ``result is None``), e si ripara con una riga in
    ``_INTERNAL_KIND_BY_PREFIX`` o in ``_PERSONAL_SESSION_KEYS``. Fra un guasto
    che si vede e uno che non si vede, il residuo va su quello che si vede.

    Non solleva, e non e' timidezza: questa funzione gira anche sul campo
    ``session_key`` delle voci di ``history.jsonl``, scritte da versioni
    precedenti e modificabili a mano. Un'eccezione qui trasformerebbe una riga
    vecchia in un crash di Dream e dell'autocompaction, cioe' una domanda di
    classificazione in un guasto di disponibilita' della memoria. Fail-closed
    qui vuol dire "il bucket prudente", non "abortisci".
    """
    if internal_session_kind(key) is not None:
        return "internal"
    if key.startswith(PROJECT_SESSION_PREFIX):
        return "project"
    if key in _PERSONAL_SESSION_KEYS or key.startswith(_LEGACY_CHANNEL_KEY_PREFIXES):
        return "personal"
    # Chiave vuota: e' "nessuna chiave", non un vocabolario mancante. Cade nel
    # bucket prudente come tutto il resto, ma **senza** avvisare — non c'e' niente
    # da registrare, ed e' il default di alcuni ContextVar dei tool
    # (``CronTool._session_key``), quindi avvisare qui sarebbe solo rumore.
    if key:
        _warn_unclassified(key)
    return "internal"


def is_internal_session_key(key: str) -> bool:
    """True se la session key appartiene a lavoro interno, non all'utente.

    Usata come filtro unico per gli elenchi di sessioni e come default della
    visibilita di un turno (:mod:`jenny.session.turn_visibility`): il confine
    sta qui e non replicato in ogni chiamante, cosi aggiungere una sessione
    interna non richiede di ricordarsi di aggiornare N punti.
    """
    return session_kind(key) == "internal"


def is_project_session_key(key: str) -> bool:
    """True se la session key e' la conversazione di un progetto."""
    return session_kind(key) == "project"


def is_personal_session_key(key: str) -> bool:
    """True se la session key e' la conversazione personale con l'utente.

    **Whitelist, non la negazione di :func:`is_internal_session_key`.** Chi la usa
    decide cosa entra nella memoria di lungo periodo, e per quella decisione
    serve l'elenco di chi *puo'* — che oggi ha un solo membro — e non quello di
    chi non puo'. Da quando esiste :func:`session_kind` le due non coincidono
    piu': una chiave ``project:`` non e' interna e non e' personale.

    Dal 23/08 la docstring e' vera anche dell'implementazione: l'elenco e'
    ``_PERSONAL_SESSION_KEYS`` piu' i prefissi legacy, e una chiave che non e' in
    nessun vocabolario **non** e' personale (v. :func:`session_kind`). Prima
    "whitelist" descriveva il chiamante e non il codice: il residuo cadeva qui.
    """
    return session_kind(key) == "personal"


def project_session_key(project_id: str) -> str:
    """La session key di un progetto dal suo id.

    Sta qui accanto al prefisso per la stessa ragione di
    :func:`subagent_session_key`: la chiave la compone chi la definisce, cosi il
    lato che la scrive e quello che la classifica non possono divergere.
    """
    return f"{PROJECT_SESSION_PREFIX}{project_id}"


def normalize_user_session_key(key: str) -> str:
    """La session key persistita, riportata alla forma corrente.

    Serve a un solo punto — il caricamento dello store dei job cron — e vive qui
    perche' la forma delle chiavi la decide questo modulo. Tutto cio' che non e'
    una chiave utente legacy torna identico: le chiavi interne, la conversazione
    unica e qualunque categoria futura passano intatte.
    """
    if key.startswith(_LEGACY_CHANNEL_KEY_PREFIXES):
        return UNIFIED_SESSION_KEY
    return key


def subagent_session_key(lineage_id: str) -> str:
    """Session key della storia Tier-2 di un lineage."""
    return f"{SUBAGENT_SESSION_PREFIX}{lineage_id}"


def session_key_for_channel(channel: str, chat_id: str) -> str:
    """Return the session key for a channel/chat pair.

    Ogni canale e ogni chat cadono sulla **conversazione unica**, con una sola
    eccezione: un ``chat_id`` che nomina un progetto (``project:<nome>``) sulla
    WebUI, che apre la conversazione di quel progetto. Le chiavi interne non
    passano di qui — usano ``session_key_override``.

    **Qualunque altra cosa cade sulla conversazione personale, e non e' un
    dettaglio**: il ``chat_id`` arriva da un client, e senza questo un client
    confuso (o ostile) potrebbe farsi creare una sessione qualsiasi mandando un
    nome inventato. L'elenco delle forme riconosciute e' chiuso, e il nome deve
    superare :func:`is_valid_project_name` — ``project:../fuori`` non e' un
    progetto, e' un tentativo.

    Il canale conta: un messaggio Telegram con dentro ``project:qualcosa`` resta
    la conversazione personale. Un progetto e' una sessione di lavoro alla
    tastiera, e la vita "fuori" di Jenny — Telegram, cron, avvisi — non ci entra.
    """
    if channel == WEBUI_CHANNEL and is_project_session_key(chat_id):
        if is_valid_project_name(chat_id[len(PROJECT_SESSION_PREFIX):]):
            return chat_id
    return UNIFIED_SESSION_KEY
