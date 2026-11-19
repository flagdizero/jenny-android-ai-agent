"""Alert di sistema Android per i messaggi consegnati all'utente.

Principio di design: la notifica non è un tool dell'agente ma una proprietà
del canale di consegna — ogni messaggio user-visible che attraversa il canale
WebSocket viene offerto al bridge Kotlin (``NotifierBridge``), che lo posta
come notifica di sistema solo se l'utente non lo sta già vedendo. La policy "se
squillare" vive quindi in Kotlin (unica fonte di verità sulla visibilità
dell'app); qui si decide il *contenuto* (titolo/corpo/tag) e se il messaggio è
una risposta o un avviso (``is_app_reply``), che Kotlin zittisce diversamente.

Un messaggio consegnato nella chat di un quaderno porta il quaderno **nel tag**
(``project:<nome>|<tag>``, v. ``alert_conversation``): Kotlin lo rilegge da lì
(``AlertConversation.ofTag``) per portare il tocco in quel quaderno, e
ogni quaderno ha così la sua notifica invece di coalizzare con la chat personale.

Segue il pattern lazy-bridge di ``jenny.webui.android_apps_api``: la classe
Kotlin è risolta via Chaquopy alla prima chiamata; senza contesto Android
(desktop/test) ogni chiamata è un no-op silenzioso.

**Le notifiche dell'altra Jenny** (``post_peer_confirm``, ``post_peer_done``,
``clear_peer_notice``) passano dallo stesso bridge ma non sono alert: hanno
pulsanti («Consenti»/«No», «Annulla») e non tacciono con la chat a schermo,
perché sono una decisione da prendere o un'azione da poter disfare, non un
messaggio da leggere. Il tocco su un pulsante torna da
``jenny.runtime.native_input.on_native_peer_action``.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from loguru import logger

from jenny.bus.events import WEBSOCKET_CHANNEL, WEBUI_MESSAGE_SOURCE_METADATA_KEY
from jenny.runtime.chaquopy_bridge import BridgeCache
from jenny.runtime.context import get_android_context
from jenny.runtime.user_text import current_bot_name, say
from jenny.session.keys import (
    is_project_session_key,
    project_name,
    session_key_for_channel,
)
from jenny.utils.helpers import clip
from jenny.utils.plain_text import plain_text

_BRIDGE = BridgeCache("com.flagdizero.jenny.NotifierBridge")

_ALERT_TIMEOUT_S = 10
_BODY_MAX_CHARS = 200

# Separa il quaderno dal resto del tag: ``project:<nome>|message``,
# ``project:<nome>|cron:<label>``. Valore di protocollo condiviso con
# ``NotifierBridge.CONVERSATION_TAG_SEPARATOR``; un nome di quaderno non può
# contenerlo (``is_valid_project_name``), quindi la prima occorrenza è il confine.
CONVERSATION_TAG_SEPARATOR = "|"

# I task fire-and-forget vanno tenuti referenziati fino al completamento,
# altrimenti il GC può cancellarli a metà (asyncio tiene solo weakref).
_TASKS: set[asyncio.Task[Any]] = set()


def reset_notifier_state() -> None:
    """Drop the cached NotifierBridge so a fresh gateway start can't inherit
    a stale bridge from a previous crashed loop.

    Simmetrico a ``android_apps_api.reset_installed_apps_state``; chiamato da
    ``android_entry.run_gateway``.
    """
    _BRIDGE.reset()


def _resolve_bridge_class() -> Any:
    """Resolve the Kotlin NotifierBridge class via Chaquopy.

    Stays a module-level function rather than a direct call to
    ``_BRIDGE.resolve_class``: it is the seam the tests replace to stand
    in for the bridge off-device.
    """
    return _BRIDGE.resolve_class()


async def _get_bridge(context: Any) -> Any:
    """Build or return a cached NotifierBridge instance (thread-safe)."""
    return await _BRIDGE.get(context, resolve=_resolve_bridge_class)


def alert_conversation(chat_id: str | None) -> str | None:
    """Il quaderno in cui è stato consegnato il messaggio (``project:<nome>``), o ``None``.

    Passa da ``session_key_for_channel`` apposta: un ``chat_id`` che non nomina
    un quaderno valido (``project:../fuori``) cade sulla chat personale per la
    stessa regola che vale per la sessione, e la notifica lo segue.
    """
    if not chat_id:
        return None
    key = session_key_for_channel(WEBSOCKET_CHANNEL, chat_id)
    return key if is_project_session_key(key) else None


def alert_fields(
    content: str,
    metadata: dict[str, Any] | None,
    *,
    conversation: str | None = None,
) -> tuple[str, str, str]:
    """Deriva ``(title, body, tag)`` dell'alert dal messaggio outbound.

    Il titolo è il nome che l'utente ha dato all'agente (``bot_name``, di
    default «Jenny») seguito dal tipo dell'avviso nella sua lingua
    (``user_text``: «⏰ Promemoria», «Aggiornamento»), e per un
    cron dall'etichetta del job quando ne ha una — un nome per l'utente, mai
    l'istruzione inglese che il job esegue (v. ``jenny/agent/tools/cron.py``).
    Il tipo viene dal metadata di sorgente proattiva (``_webui_message_source``:
    cron/update); il tag coalizza gli alert della stessa sorgente
    (stesso tag → il nuovo sostituisce il vecchio).

    Il corpo è testo semplice: il markdown della risposta (asterischi, URL dei
    link, titoli) si toglie **prima** di troncare, così i 200 caratteri vanno a
    quel che si legge.

    *conversation* è il quaderno in cui il messaggio è stato consegnato (v.
    ``alert_conversation``). Va **davanti** al tag, qualunque sia il tipo — un
    cron del quaderno consegna nel quaderno, e il suo tocco deve portare lì — e
    un messaggio semplice prende il nome del quaderno nel titolo: senza, due
    quaderni in tendina sarebbero due «Jenny» indistinguibili.
    """
    source = (metadata or {}).get(WEBUI_MESSAGE_SOURCE_METADATA_KEY)
    kind = source.get("kind") if isinstance(source, dict) else None
    raw_label = source.get("label") if isinstance(source, dict) else None
    label = raw_label.strip() if isinstance(raw_label, str) and raw_label.strip() else None

    bot = current_bot_name()
    if kind == "cron":
        title = f"{bot} · {say('notify.reminder')}"
        if label:
            title = f"{title} · {label}"
        tag = f"cron:{label}" if label else "cron"
    elif kind == "update":
        # Tag dedicato: l'annuncio in chat e l'alert esplicito di un update
        # critico partono entrambi da qui e devono coalizzare, non sommarsi.
        title = f"{bot} · {say('notify.update')}"
        tag = "update"
    else:
        title = bot
        if conversation:
            title = f"{bot} · {project_name(conversation)}"
        tag = "message"
    if conversation:
        tag = f"{conversation}{CONVERSATION_TAG_SEPARATOR}{tag}"

    body = clip(" ".join(plain_text(content).split()), _BODY_MAX_CHARS)
    if not body:
        body = say("notify.new_message")
    return title, body, tag


def is_app_reply(metadata: dict[str, Any] | None) -> bool:
    """Il messaggio risponde a un turno che l'utente ha scritto nella WebUI?

    ``webui: True`` lo mette il canale WebSocket sul messaggio in ingresso
    (``WebSocketChannel``), e la risposta finale e gli invii del tool
    ``message`` sullo stesso target ereditano i metadata del turno. Un avviso
    proattivo (cron, Dream, update) nasce su una sessione interna e
    non lo porta.

    La distinzione decide chi zittisce l'alert (``NotifierBridge``): una
    risposta tace con l'app davanti, perché chi ha scritto la sta aspettando
    dovunque sia — anche dentro un quaderno, dove la chat personale non è a
    schermo; un avviso tace solo con la sua chat a schermo — la personale, o
    il quaderno per un cron del quaderno — perché Jenny è il launcher e sta
    davanti quasi sempre.
    """
    return (metadata or {}).get("webui") is True


async def post_alert(
    content: str,
    metadata: dict[str, Any] | None,
    *,
    thread: str | None = None,
    chat_id: str | None = None,
) -> bool:
    """Posta l'alert via bridge. Ritorna False se soppresso (l'utente lo sta vedendo),
    senza contesto Android, o su qualunque errore — mai un'eccezione: la
    consegna in chat è già avvenuta e non deve risentirne.

    *thread* scavalca il tag derivato da ``alert_fields``. Serve a un caso solo,
    ed è il canale della tendina (``channels/notification.py``): le risposte
    dell'agente devono coalizzare tutte su un thread unico invece di impilarsi
    una per messaggio, mentre gli avvisi proattivi tengono i loro tag distinti
    (``cron:<label>``, ``update``) perché sono avvisi diversi. Il
    titolo resta quello che ``alert_fields`` deriva dai metadata: il tag dice
    *dove* va la notifica, il titolo dice *di cosa parla*.

    *chat_id* è la chat WebUI in cui il messaggio è stato consegnato: se è un
    quaderno, il quaderno entra nel tag (v. ``alert_conversation``).
    """
    context = get_android_context()
    if context is None:
        return False
    title, body, tag = alert_fields(
        content, metadata, conversation=alert_conversation(chat_id)
    )
    if thread:
        tag = thread
    # La risposta a un messaggio scritto nella WebUI tace con l'app davanti,
    # un avviso solo con la sua chat a schermo: v. ``is_app_reply``.
    method = "postReply" if is_app_reply(metadata) else "postAlert"
    return bool(await _BRIDGE.call(_get_bridge, context, method, title, body, tag,
                                   timeout=_ALERT_TIMEOUT_S, default=False))


def notify_delivery(
    content: str,
    metadata: dict[str, Any] | None,
    *,
    chat_id: str | None = None,
) -> None:
    """Pianifica (fire-and-forget) l'alert per un messaggio appena consegnato.

    Non blocca il percorso di invio WS: il bridge viene chiamato in un task
    separato. No-op fuori da Android o senza event loop attivo. *chat_id* è la
    chat WebUI di consegna (v. ``post_alert``); senza, l'alert è della chat
    personale, che è il caso di ogni avviso nato fuori da una chat.
    """
    if get_android_context() is None:
        return
    try:
        task = asyncio.get_running_loop().create_task(
            post_alert(content, metadata, chat_id=chat_id)
        )
    except RuntimeError:
        return
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


def notify_alarm(text: str, label: str) -> None:
    """Un allarme a zero token: una notifica scritta dal codice, senza turno.

    Per i guasti che devono arrivare proprio quando il modello non risponde (il
    giardiniere fermo, Dream fermo, un controllo che fallisce in silenzio, la
    copia che non parte). *label* è l'etichetta che l'utente legge **e** il tag
    che accorpa gli allarmi (``cron:<label>``): lo stesso allarme deve portare
    sempre la stessa, e due allarmi diversi mai.
    """
    notify_delivery(
        text, {WEBUI_MESSAGE_SOURCE_METADATA_KEY: {"kind": "cron", "label": label}}
    )


# --- Notifiche dell'altra Jenny ------------------------------------------------

# I due tipi di notifica con pulsanti, e il nome con cui Kotlin ne deriva il tag
# (``NotifierBridge.peerTag``). Valori di protocollo: un tipo che diverge
# lascerebbe in tendina una notifica che ``clear_peer_notice`` non trova.
PEER_NOTICE_KINDS = frozenset({"confirm", "done"})

# ``cid``/``qid``: 32 cifre esadecimali minuscole, la stessa forma che
# ``native_input.on_native_peer_action`` esige al ritorno. Si controlla anche
# qui perché l'identificativo finisce nel tag e negli extra del PendingIntent.
_PEER_IDENT = re.compile(r"[0-9a-f]{32}")

_PEER_TITLE_MAX_CHARS = 120
# Più lungo del corpo di un alert: chi deve dire «Consenti» deve poter leggere
# che cosa esce dal telefono (l'indirizzo intero, non i suoi primi 200 caratteri).
_PEER_BODY_MAX_CHARS = 600


async def _peer_bridge_call(method: str, *args: Any) -> None:
    """La chiamata al bridge, sul loop. ``BridgeCache.call`` non solleva mai."""
    ok = await _BRIDGE.call(_get_bridge, get_android_context(), method, *args,
                            timeout=_ALERT_TIMEOUT_S, default=False)
    if not ok:
        logger.info("NotifierBridge.{} did not post or clear the peer notice", method)


def _peer_fire(method: str, *args: Any) -> None:
    """Pianifica la chiamata (fire-and-forget), come ``notify_delivery``.

    No-op fuori da Android e senza un loop in corsa: queste funzioni le chiama
    codice sul loop del gateway, e da un thread qualunque non c'è dove
    accodarle.
    """
    if get_android_context() is None:
        return
    try:
        task = asyncio.get_running_loop().create_task(_peer_bridge_call(method, *args))
    except RuntimeError:
        logger.info("Peer notice {} skipped: no running event loop", method)
        return
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


def _peer_ident_ok(ident: Any) -> bool:
    return isinstance(ident, str) and _PEER_IDENT.fullmatch(ident) is not None


def _peer_text(title: str, body: str) -> tuple[str, str]:
    """Titolo su una riga e corpo, ciascuno col suo tetto. Il corpo resta com'è
    dentro (a capo compresi): lo scrive il codice, e l'indirizzo va letto intero."""
    clean_title = clip(" ".join(str(title or "").split()), _PEER_TITLE_MAX_CHARS)
    clean_body = clip(str(body or "").strip(), _PEER_BODY_MAX_CHARS)
    return clean_title or current_bot_name(), clean_body


def post_peer_confirm(cid: str, title: str, body: str) -> None:
    """Posta la domanda «Consenti»/«No» per la conferma *cid*. Mai solleva.

    Il tocco torna da ``native_input.on_native_peer_action("allow"|"deny", cid)``.
    Ripostare con lo stesso *cid* sostituisce la notifica.
    """
    try:
        if not _peer_ident_ok(cid):
            logger.warning("Peer confirm notice not posted: malformed cid")
            return
        title, body = _peer_text(title, body)
        _peer_fire("postPeerConfirm", cid, title, body)
    except Exception:  # noqa: BLE001 — mai un'eccezione al chiamante
        logger.opt(exception=True).warning("Peer confirm notice failed")


def post_peer_done(qid: str, title: str, body: str) -> None:
    """Posta «fatto», col pulsante «Annulla», per la richiesta *qid*. Mai solleva.

    Il tocco torna da ``native_input.on_native_peer_action("undo", qid)``.
    Ripostare con lo stesso *qid* sostituisce la notifica.
    """
    try:
        if not _peer_ident_ok(qid):
            logger.warning("Peer done notice not posted: malformed qid")
            return
        title, body = _peer_text(title, body)
        _peer_fire("postPeerDone", qid, title, body)
    except Exception:  # noqa: BLE001 — mai un'eccezione al chiamante
        logger.opt(exception=True).warning("Peer done notice failed")


def clear_peer_notice(kind: str, ident: str) -> None:
    """Toglie la notifica postata da ``post_peer_confirm`` (*kind* ``"confirm"``)
    o da ``post_peer_done`` (``"done"``) per *ident*. Mai solleva."""
    try:
        if kind not in PEER_NOTICE_KINDS or not _peer_ident_ok(ident):
            logger.warning("Peer notice not cleared: bad kind {!r} or ident", kind)
            return
        _peer_fire("clearPeerNotice", kind, ident)
    except Exception:  # noqa: BLE001 — mai un'eccezione al chiamante
        logger.opt(exception=True).warning("Peer notice clear failed")
