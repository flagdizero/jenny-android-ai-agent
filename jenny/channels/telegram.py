"""Canale Telegram: bot personale con pairing a codice e long polling.

Contratto duck-typed del dispatcher (come ``WebSocketChannel``): attributi di
gating, ``start()``/``stop()`` e ``send()``. Niente streaming: il canale non
setta ``_wants_stream`` sull'inbound, quindi riceve solo messaggi finali — e
siccome non riceve nemmeno i progress né un ``turn_end``, l'unico segno di vita
durante un turno è l'indicatore "sta scrivendo…", pilotato dai runtime events
(v. ``_TypingHeartbeat``).

Il canale è pura consegna: non conosce il transcript WebUI. La proiezione dei
turni Telegram sulla vista WebUI è responsabilità del runtime (user echo via
``WebuiTurnCoordinator``, finale via dispatcher, ``turn_end`` via runtime
events); qui resta solo il turn-id nei metadata inbound per correlare le righe.
"""

from __future__ import annotations

import asyncio
import hmac
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from loguru import logger

from jenny.bus.events import COORDINATION_FLAGS, InboundMessage, OutboundMessage
from jenny.bus.queue import MessageBus
from jenny.bus.runtime_events import TurnRunStatusChanged
from jenny.channels.non_streaming import NonStreamingChannelMixin
from jenny.channels.telegram_api import TelegramAPI, TelegramAPIError
from jenny.channels.telegram_format import markdown_to_telegram_html, split_message
from jenny.channels.telegram_media import (
    TelegramFile,
    has_unsupported_attachment,
    pick_file,
)
from jenny.config.paths import get_uploads_dir
from jenny.config.schema import TelegramConfig
from jenny.runtime.power import keep_awake
from jenny.utils.media_decode import FileSizeExceeded, save_bytes
from jenny.webui.metadata import WEBUI_TURN_METADATA_KEY

# Limite prudente sul testo grezzo: la conversione HTML può allungare il chunk.
_RAW_CHUNK_LIMIT = 3500
_POLL_BACKOFF_MAX_S = 60.0
_CHUNK_RETRY_DELAYS = (1, 2, 4)

# Eta' oltre la quale un messaggio trovato in coda all'avvio non viene lavorato.
#
# ``get_updates`` senza offset restituisce tutto il backlog che Telegram ancora
# trattiene (~24h di update non confermati), e ``_offset`` riparte da ``None`` a
# ogni costruzione del canale — cioe' a ogni avvio del gateway e a ogni toggle.
# Senza questo filtro, riaccendere il canale dopo giorni fa aprire un turno LLM
# per ogni messaggio in coda, tutti insieme, su roba vecchia.
#
# Scartare *tutto* il backlog sarebbe piu' semplice ed e' sbagliato: su Android
# le ripartenze sono ordinarie (Doze, watchdog, un aggiornamento), e un riavvio
# tre secondi dopo che l'utente ha scritto perderebbe quel messaggio per sempre,
# senza che niente lo dica. Cinque minuti separano i due casi che contano: il
# messaggio scritto a cavallo di una ripartenza passa, il backlog di giorni no.
_BACKLOG_MAX_AGE_S = 300.0

# Scadenza del wakelock che copre la lavorazione di un update ricevuto.
# Generosa rispetto al lavoro che c'è dentro (pairing, reverse-geocoding di una
# posizione, una risposta di servizio con i suoi retry), ma finita: qui non gira
# mai un turno LLM: quello parte dal bus e si prende il proprio lock "turn".
#
# Il long-poll di ``getUpdates`` NON è coperto, ed è una scelta: l'attesa è
# inattiva per costruzione e dura fino a ``poll_timeout_s`` (50s di default),
# quindi un lock che la copra sarebbe tenuto ~sempre — cioè la modalità
# "always" travestita da "turns". Il costo è che a schermo spento un messaggio
# Telegram può restare in coda finché il device non si sveglia da solo; il
# rimedio è il risveglio programmato, non il wakelock.
_TELEGRAM_WAKELOCK_TIMEOUT_S = 180.0

# Estensioni inviabili come foto (anteprima nativa Telegram); tutto il resto
# (SVG, PDF, ecc.) va come documento. Cap prudente sotto il limite Bot API.
_RASTER_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_TG_MEDIA_MAX_BYTES = 10 * 1024 * 1024

# Onboarding in finestra di pairing: budget di risposte di servizio per chat e
# bound fail-closed sul numero di chat tracciate. Superato il cap (o pieno il
# dict) la chat diventa INELEGGIBILE al pairing anche col codice giusto: è
# questo che rende il throttle una difesa reale contro il brute-force del
# codice a 6 cifre (budget totale ≈ cap × bound su 10^6 per vita del canale).
_MAX_PAIR_ATTEMPTS = 5
_MAX_TRACKED_CHATS = 512

# Posizione e venue hanno il loro ramo (``_maybe_handle_location``). Queste
# chiavi restano qui per il caso in cui quel ramo declini — toggle posizione
# spento, coordinate malformate — perché anche allora l'utente ha mandato
# qualcosa e merita una risposta invece del silenzio.
_LOCATION_KEYS = ("location", "venue")

# Contenuto sintetico quando un allegato arriva senza didascalia, o quando il
# modello rischia di credere di poterlo percepire.
#
# **Le immagini non ne hanno uno, ed è una scelta.** Una foto arriva davvero
# come blocco vision: annunciarla al modello non aggiunge niente, e il costo si
# vede dall'altra parte — l'eco del turno sulla vista WebUI usa questo testo,
# quindi il marcatore comparirebbe in chat come se l'utente se lo fosse scritto
# da solo, per giunta in inglese. Senza, una foto senza didascalia è esattamente
# ciò che è quando la si allega dalla WebUI: una bolla con dentro l'immagine.
#
# Per audio e video il marcatore resta l'unica cosa che impedisce a Jenny di
# rispondere come se avesse ascoltato o guardato, e in chat si legge bene.
_MEDIA_TURN_MARKERS: dict[str, str] = {
    "audio": (
        "📎 [The user sent a voice note or audio file. It is saved and referenced by "
        "path, but nothing transcribed it: you cannot hear its contents.]"
    ),
    "video": (
        "📎 [The user sent a video. It is saved and referenced by path, but you cannot "
        "watch it.]"
    ),
    "document": "📎 [The user sent a file, referenced by path.]",
}

# Azione mostrata in chat mentre scarichiamo l'allegato: dire "sta scrivendo"
# durante un download sarebbe la cosa sbagliata detta bene.
_UPLOAD_ACTIONS: dict[str, str] = {
    "image": "upload_photo",
    "audio": "upload_voice",
    "video": "upload_video",
    "document": "upload_document",
}

# Cadenza del battito "sta scrivendo…": l'azione scade lato Telegram dopo ~5s,
# quindi 4 lascia margine senza sprecare richieste.
_TYPING_INTERVAL_S = 4.0

# Tetto duro del battito. Esiste per il caso che non si vede in un test: un
# evento di fine turno perso (crash del loop, handler sganciato a metà) che
# lascerebbe il bot a "sta scrivendo…" per sempre. Cinque minuti sono oltre
# qualunque turno onesto e molto sotto "per sempre".
_TYPING_MAX_S = 300.0

# Contenuto sintetico (LLM-facing, non mostrato all'utente) di un turno
# innescato da una posizione condivisa: la posizione vera arriva nel runtime
# context come "User location (shared via Telegram): …".
_LOCATION_TURN_MARKER = "📍 [The user just shared their current location via Telegram.]"

# Risposte lato bot, localizzate come i WELCOME_TEMPLATES dell'onboarding.
_BOT_STRINGS: dict[str, dict[str, str]] = {
    "it": {
        "paired": "✅ Collegato! Da ora puoi parlare con Jenny da questa chat.",
        "welcome": (
            "Scrivimi come in una chat normale e ti risponde Jenny.\n\n"
            "• /new — inizia una nuova conversazione\n"
            "• 📎 Foto, file, vocali e video: arrivano a Jenny. Le foto le vede;\n"
            "  gli altri li riceve come file, senza ascoltarli o guardarli."
        ),
        "start_prompt": (
            "Per collegarti, inviami il codice a 6 cifre che vedi nella WebUI di Jenny."
        ),
        "wrong_code": (
            "Codice non valido. Controlla il codice a 6 cifre nella WebUI di Jenny e riprova."
        ),
        "media_unsupported": "🤷 Questo tipo di messaggio non so ancora gestirlo.",
        "media_too_big": "📦 Allegato troppo grande: non riesco a scaricarlo.",
        "media_failed": "⚠️ Non sono riuscita a scaricare l'allegato. Riprova.",
    },
    "en": {
        "paired": "✅ Paired! You can now talk to Jenny from this chat.",
        "welcome": (
            "Message me like a normal chat and Jenny replies.\n\n"
            "• /new — start a new conversation\n"
            "• 📎 Photos, files, voice notes and videos reach Jenny. She can see\n"
            "  photos; the rest arrive as files she cannot listen to or watch."
        ),
        "start_prompt": (
            "To pair, send me the 6-digit code shown in Jenny's WebUI."
        ),
        "wrong_code": (
            "Invalid code. Check the 6-digit code in Jenny's WebUI and try again."
        ),
        "media_unsupported": "🤷 I can't handle this kind of message yet.",
        "media_too_big": "📦 That attachment is too big for me to download.",
        "media_failed": "⚠️ I couldn't download that attachment. Try again.",
    },
}


class _TypingHeartbeat:
    """Tiene acceso "sta scrivendo…" finché qualcuno lo tiene acceso.

    L'azione di Telegram scade da sola dopo ~5 secondi: non è uno stato che si
    accende e si spegne, è un battito. Da qui le tre proprietà che contano:

    - **idempotente**: ``start`` su un battito già acceso lo sostituisce invece
      di affiancarne un secondo. C'è una sola chat accoppiata, quindi un solo
      battito per canale;
    - **silenzioso**: un errore su ``sendChatAction`` non risale mai. È un
      indicatore cosmetico; se il turno vero fallisce lo dirà il turno;
    - **mortale**: si spegne da sé dopo ``_TYPING_MAX_S`` anche senza segnale di
      fine, perché l'evento che lo spegne può non arrivare mai e un bot che
      scrive da mezz'ora è peggio di uno che non scrive.
    """

    def __init__(
        self,
        api: Any,
        *,
        interval_s: float = _TYPING_INTERVAL_S,
        max_s: float = _TYPING_MAX_S,
    ) -> None:
        self._api = api
        self._interval_s = interval_s
        self._max_s = max_s
        self._task: asyncio.Task | None = None

    async def start(self, chat_id: str, action: str = "typing") -> None:
        await self.stop()
        self._task = asyncio.create_task(self._beat(chat_id, action))

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None or task.done():
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _beat(self, chat_id: str, action: str) -> None:
        deadline = time.monotonic() + self._max_s
        while time.monotonic() < deadline:
            with suppress(Exception):
                await self._api.send_chat_action(chat_id, action)
            await asyncio.sleep(self._interval_s)
        logger.debug("Telegram: typing heartbeat expired on its own cap")


class TelegramChannel(NonStreamingChannelMixin):
    """Canale bot Telegram con pairing a codice singolo owner."""

    name = "telegram"
    send_progress = False
    send_tool_hints = False
    show_reasoning = False
    # I retry sono gestiti per-chunk internamente: un retry esterno del
    # dispatcher rispedirebbe anche i chunk già consegnati (duplicati).
    send_max_retries = 1

    def __init__(
        self,
        config: TelegramConfig,
        bus: MessageBus,
        *,
        api: TelegramAPI | None = None,
        on_paired: Callable[[str, str | None], Awaitable[None]] | None = None,
        language: str = "en",
        runtime_events: Any | None = None,
    ):
        self.config = config
        self.bus = bus
        self.api = api or TelegramAPI(config.bot_token or "")
        self._on_paired = on_paired
        self._language = language if language in _BOT_STRINGS else "en"
        self._paired_chat_id = config.paired_chat_id
        self._pairing_code = config.pairing_code
        self._offset: int | None = None
        self._poll_task: asyncio.Task | None = None
        # Vero finche' stiamo smaltendo la coda che c'era *prima* di partire.
        # Si spegne al primo update abbastanza recente (o alla prima coda
        # vuota), e da lì non si riaccende: il filtro sull'eta' non deve poter
        # mangiare traffico vivo per il resto della vita del processo — se
        # l'orologio del telefono fosse sballato, un filtro permanente
        # scarterebbe tutto in silenzio per sempre.
        self._draining = True
        # Tentativi di pairing per chat (in-memory: si azzera al reload del
        # canale, che rigenera comunque il codice nei percorsi che contano).
        self._pair_attempts: dict[str, int] = {}
        self._typing = _TypingHeartbeat(self.api)
        # L'unico segnale di fine turno che arriva fin qui. Telegram non riceve
        # un ``_turn_end`` (``WebuiTurnCoordinator.handle_turn_end`` esce subito
        # per i canali diversi da websocket) né i progress (``send_progress`` è
        # falso): resta il runtime event, il cui "idle" è emesso in un
        # ``finally`` e quindi copre anche errori e turni abortiti.
        self._unsubscribe: Callable[[], None] | None = None
        if runtime_events is not None:
            self._unsubscribe = runtime_events.subscribe(
                self._on_run_status, TurnRunStatusChanged
            )

    def _t(self, key: str) -> str:
        return _BOT_STRINGS[self._language][key]

    @property
    def paired_chat_id(self) -> str | None:
        """Chat accoppiata corrente (stato vivo, aggiornato al pairing)."""
        return self._paired_chat_id

    # ------------------------------------------------------------------ #
    # Lifecycle                                                          #
    # ------------------------------------------------------------------ #

    async def start(self) -> None:
        """Avvia il long polling; ritorna quando il task termina (stop)."""
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("Telegram channel started (paired={})", bool(self._paired_chat_id))
        with suppress(asyncio.CancelledError):
            await self._poll_task

    async def stop(self) -> None:
        # L'ordine conta: il canale viene *ricostruito* a ogni reload delle
        # impostazioni Telegram, e un handler lasciato appeso continuerebbe a
        # scrivere su una ``TelegramAPI`` già chiusa a ogni turno.
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        await self._typing.stop()
        if self._poll_task is not None:
            self._poll_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._poll_task
            self._poll_task = None
        await self.api.close()

    async def _poll_loop(self) -> None:
        """Long-poll di ``getUpdates`` con backoff su errori di rete.

        Il backoff cresce fino a 60s e si azzera al primo successo: cadute
        Wi-Fi o doze temporaneo si riassorbono senza intervento.
        """
        backoff = 1.0
        while True:
            try:
                updates = await self.api.get_updates(self._offset, self.config.poll_timeout_s)
                backoff = 1.0
                if self._draining and not updates:
                    # Coda vuota: non c'era backlog, o l'abbiamo finito.
                    self._draining = False
                for update in updates:
                    update_id = update.get("update_id")
                    if isinstance(update_id, int):
                        self._offset = update_id + 1
                    # L'offset e' gia' avanzato, quindi uno scarto qui e'
                    # definitivo: al prossimo giro Telegram non lo ripropone.
                    # Deve stare *dopo* l'avanzamento, altrimenti l'update
                    # scartato tornerebbe a ogni poll, per sempre.
                    if self._draining and self._is_stale_backlog(update):
                        continue
                    self._draining = False
                    try:
                        # Il lock si prende qui, per-update, e non attorno a
                        # ``get_updates``: vedi _TELEGRAM_WAKELOCK_TIMEOUT_S.
                        async with keep_awake(
                            "telegram", timeout_s=_TELEGRAM_WAKELOCK_TIMEOUT_S
                        ):
                            await self._handle_update(update)
                    except Exception:
                        logger.exception("Telegram: error handling update")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(
                    "Telegram poll error ({}), retrying in {:.0f}s", type(e).__name__, backoff
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _POLL_BACKOFF_MAX_S)

    def _is_stale_backlog(self, update: dict[str, Any]) -> bool:
        """Vero se l'update e' troppo vecchio per essere lavorato all'avvio.

        Vale solo mentre ``_draining``, cioe' sul backlog che precede la
        partenza del canale. Lo scarto e' loggato a WARNING con l'eta': e' una
        perdita voluta ma non silenziosa, ed e' l'unica traccia che resta se
        l'orologio del device fosse sballato e il filtro mordesse a torto.
        """
        message = update.get("message")
        date = message.get("date") if isinstance(message, dict) else None
        if not isinstance(date, int | float):
            # Eta' non deducibile: si lavora. Scartare e' irreversibile, quindi
            # il caso ambiguo non lo merita — al massimo si risponde a qualcosa
            # di vecchio, che e' il male minore rispetto a perdere un messaggio.
            return False
        age = time.time() - float(date)
        if age <= _BACKLOG_MAX_AGE_S:
            return False
        logger.warning(
            "Telegram: dropping backlog message from startup queue (age {:.0f}s > {:.0f}s)",
            age,
            _BACKLOG_MAX_AGE_S,
        )
        return True

    # ------------------------------------------------------------------ #
    # Inbound                                                            #
    # ------------------------------------------------------------------ #

    async def _handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message")
        if not isinstance(message, dict):
            return
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        if not chat_id:
            return
        sender = message.get("from") or {}
        text = message.get("text")

        if not self._paired_chat_id:
            await self._maybe_pair(chat_id, sender, text)
            return
        if chat_id != str(self._paired_chat_id):
            # Mittente estraneo: silenzio totale, nessun oracle sull'esistenza
            # del bot o dello stato di pairing.
            logger.info("Telegram: ignoring message from unpaired chat {}", chat_id)
            return
        if isinstance(text, str) and text.strip() and self._parse_start(text) is not None:
            # /start dal proprietario: guida rapida di servizio, non un turno
            # LLM (e niente rumore nella vista WebUI). /new e /stop invece
            # proseguono verso il command router. Va riconosciuto prima di
            # qualunque lavoro sugli allegati: un /start non ne porta.
            await self._send_raw(chat_id, self._t("welcome"))
            return

        # Una didascalia è testo dell'utente quanto un messaggio normale. Prima
        # non veniva nemmeno letta, quindi una foto con didascalia perdeva
        # metà del messaggio — la metà scritta a mano.
        if not isinstance(text, str) or not text.strip():
            caption = message.get("caption")
            text = caption if isinstance(caption, str) and caption.strip() else None

        picked = pick_file(message)

        if picked is None and not text:
            # Niente testo e niente file scaricabile: o è una posizione (che ha
            # il suo turno sintetico e la sua config), o è un tipo che non
            # trattiamo, o non è niente.
            if await self._maybe_handle_location(chat_id, sender, message):
                return
            if has_unsupported_attachment(message) or any(
                key in message for key in _LOCATION_KEYS
            ):
                await self._send_raw(chat_id, self._t("media_unsupported"))
            return

        media: list[str] = []
        if picked is not None:
            saved = await self._ingest_media(chat_id, picked)
            if saved is None:
                # Il fallimento ha già la sua risposta di servizio. Non si
                # prosegue con la sola didascalia: un turno che parla di una
                # foto che non è arrivata è peggio di nessun turno.
                return
            media.append(saved)

        content = self._turn_content(text, picked)

        # Turn-id per correlare le righe della vista WebUI (user echo, finale,
        # turn_end) allo stesso turno: stesso ruolo del turn-id dei client WS.
        metadata: dict[str, Any] = {WEBUI_TURN_METADATA_KEY: str(uuid.uuid4())}
        await self.bus.publish_inbound(
            InboundMessage(
                channel=self.name,
                sender_id=str(sender.get("id", chat_id)),
                chat_id=chat_id,
                content=content,
                media=media,
                metadata=metadata,
            )
        )
        # Il turno è in viaggio ma non è ancora "running": fra il publish e la
        # costruzione del contesto passa tempo vero, e l'utente non deve
        # vedere il vuoto. Da qui in poi lo tiene acceso il runtime event.
        await self._typing.start(chat_id)

    @staticmethod
    def _turn_content(text: str | None, picked: TelegramFile | None) -> str:
        """Compone il testo del turno da didascalia e allegato.

        Il marcatore compare quando l'allegato arriva **senza** didascalia, e
        in più sempre per audio e video: lì è l'unica cosa che impedisce al
        modello di rispondere come se avesse ascoltato o guardato.

        Le immagini non ne hanno uno (v. ``_MEDIA_TURN_MARKERS``): una foto
        senza didascalia produce un contenuto vuoto, e il turno è fatto dalla
        sola immagine — come quando la si allega dalla WebUI.
        """
        if picked is None:
            return text or ""
        marker = _MEDIA_TURN_MARKERS.get(picked.kind)
        if marker is None:
            return text or ""
        if not text:
            return marker
        if picked.kind in ("audio", "video"):
            return f"{text}\n\n{marker}"
        return text

    async def _ingest_media(self, chat_id: str, picked: TelegramFile) -> str | None:
        """Scarica e salva un allegato; ritorna il path o ``None``.

        ``None`` significa "già risposto all'utente": ogni uscita di errore
        manda la sua risposta di servizio. Un allegato che sparisce in silenzio
        è indistinguibile da un bot morto, e qui il silenzio sarebbe pure
        definitivo — l'offset è già avanzato, quindi Telegram non riproporrà
        l'update al giro dopo.
        """
        if picked.size is not None and picked.size > picked.max_bytes:
            # Il rifiuto economico: ``file_size`` arriva già nell'update, quindi
            # un file oltre cap non costa nemmeno la chiamata a getFile.
            logger.info(
                "Telegram: attachment too big ({} bytes > {}), skipping",
                picked.size, picked.max_bytes,
            )
            await self._send_raw(chat_id, self._t("media_too_big"))
            return None

        action = _UPLOAD_ACTIONS.get(picked.kind, "typing")
        try:
            async with self._working(chat_id, action):
                info = await self.api.get_file(picked.file_id)
                declared = info.get("file_size")
                if isinstance(declared, int) and declared > picked.max_bytes:
                    await self._send_raw(chat_id, self._t("media_too_big"))
                    return None
                file_path = info.get("file_path")
                if not isinstance(file_path, str) or not file_path.strip():
                    raise TelegramAPIError(500, "getFile returned no file_path")
                data = await self.api.download_file(
                    file_path, max_bytes=picked.max_bytes
                )
                return await asyncio.to_thread(self._persist_media, data, picked)
        except FileSizeExceeded:
            logger.info("Telegram: attachment exceeded cap mid-download, skipping")
            await self._send_raw(chat_id, self._t("media_too_big"))
            return None
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(
                "Telegram: attachment download failed ({}): {}", picked.kind, type(e).__name__
            )
            await self._send_raw(chat_id, self._t("media_failed"))
            return None

    @staticmethod
    def _persist_media(data: bytes, picked: TelegramFile) -> str:
        """Scrive l'allegato in ``uploads/``. Sincrona: gira in un thread.

        Stessa cartella e stessa convenzione di nome degli allegati della
        WebUI (``save_bytes``), così il file browser e i tool filesystem
        dell'agente ne vedono una sola specie.
        """
        return save_bytes(
            data,
            get_uploads_dir(),
            mime_type=picked.mime,
            original_name=picked.filename,
            max_bytes=picked.max_bytes,
        )

    @asynccontextmanager
    async def _working(self, chat_id: str, action: str) -> AsyncIterator[None]:
        """Mostra l'azione giusta mentre si lavora, e la spegne comunque.

        È l'unico punto in cui il canale accende il battito da sé senza un
        runtime event: qui il turno non esiste ancora, perché il download
        precede il publish.
        """
        await self._typing.start(chat_id, action)
        try:
            yield
        finally:
            await self._typing.stop()

    async def _on_run_status(self, event: TurnRunStatusChanged) -> None:
        """Accende o spegne il battito seguendo lo stato del turno.

        Il filtro sul canale esclude da solo i turni interni (cron, Dream,
        heartbeat: ``channel="internal"``) e quelli della WebUI. Le consegne
        proattive non passano di qui affatto — non hanno un turno Telegram —
        ed è giusto: Jenny che "sta scrivendo" senza che tu abbia scritto
        niente sarebbe inquietante, non utile.
        """
        ctx = event.context
        if ctx.channel != self.name:
            return
        chat_id = str(self._paired_chat_id or ctx.chat_id)
        if not chat_id:
            return
        if event.status == "running":
            await self._typing.start(chat_id)
        else:
            await self._typing.stop()

    async def _maybe_handle_location(
        self, chat_id: str, sender: dict[str, Any], message: dict[str, Any]
    ) -> bool:
        """Gestisce una posizione condivisa via Telegram (``location``/``venue``).

        La registra come override per-canale (usata solo dalle risposte
        Telegram entro il TTL) e innesca un turno LLM così Jenny reagisce, con
        la posizione già iniettata nel runtime context. Ritorna ``False`` se il
        messaggio non è una posizione o se il toggle posizione è off — in quel
        caso il chiamante ricade sulla fallback "media_soon".
        """
        raw_loc = message.get("location")
        venue = message.get("venue") if isinstance(message.get("venue"), dict) else None
        if venue and isinstance(venue.get("location"), dict):
            raw_loc = venue["location"]
        if not isinstance(raw_loc, dict):
            return False
        try:
            lat = float(raw_loc["latitude"])
            lng = float(raw_loc["longitude"])
        except (KeyError, TypeError, ValueError):
            return False

        # Toggle posizione: caricato lazy (le condivisioni sono rare). Se off,
        # non registriamo nulla e lasciamo rispondere la fallback media_soon.
        try:
            from jenny.config.loader import load_config

            cfg = load_config().tools.location
        except Exception:  # noqa: BLE001
            logger.opt(exception=True).debug("Telegram: could not load location config")
            cfg = None
        if cfg is not None and not getattr(cfg, "enable", True):
            return False

        from dataclasses import replace

        from jenny.runtime.location import build_telegram_fix, record_telegram_location

        fix = await build_telegram_fix(cfg, lat, lng)
        # Un venue porta già un nome/indirizzo leggibile: preferiamolo al
        # reverse-geocoding delle coordinate.
        if venue:
            label = ", ".join(
                str(x) for x in (venue.get("title"), venue.get("address")) if x
            )
            if label:
                fix = replace(fix, place=label)
        record_telegram_location(chat_id, fix)

        metadata: dict[str, Any] = {WEBUI_TURN_METADATA_KEY: str(uuid.uuid4())}
        await self.bus.publish_inbound(
            InboundMessage(
                channel=self.name,
                sender_id=str(sender.get("id", chat_id)),
                chat_id=chat_id,
                content=_LOCATION_TURN_MARKER,
                metadata=metadata,
            )
        )
        return True

    @staticmethod
    def _parse_start(text: str) -> str | None:
        """Riconosce un comando ``/start``; ritorna il payload (anche vuoto).

        Case-insensitive sul comando, con l'eventuale suffisso ``@botusername``
        rimosso. Ritorna ``None`` se il testo non è un /start.
        """
        parts = text.strip().split(maxsplit=1)
        if not parts:
            return None
        command = parts[0].split("@", 1)[0]
        if command.lower() != "/start":
            return None
        return parts[1].strip() if len(parts) > 1 else ""

    async def _maybe_pair(self, chat_id: str, sender: dict[str, Any], text: Any) -> None:
        """Onboarding in finestra di pairing: solo il codice esatto accoppia.

        Senza ``pairing_code`` attivo il bot resta muto (regola no-oracle,
        vedi ``.agent/security.md``). In finestra risponde con prompt/feedback
        entro un budget per chat; una chat oltre il cap (o oltre il bound del
        dict, fail-closed) è ineleggibile al pairing anche col codice giusto.
        """
        if not self._pairing_code or not isinstance(text, str):
            return

        start_payload = self._parse_start(text)
        candidate = start_payload if start_payload is not None else text.strip()

        # Eleggibilità PRIMA del confronto col codice: è il blocco del
        # pairing, non solo del feedback, a fermare il brute-force.
        attempts = self._pair_attempts.get(chat_id, 0)
        if attempts >= _MAX_PAIR_ATTEMPTS:
            logger.info("Telegram: chat {} exceeded pairing attempts, ignoring", chat_id)
            return
        if chat_id not in self._pair_attempts and len(self._pair_attempts) >= _MAX_TRACKED_CHATS:
            logger.warning("Telegram: pairing attempt table full, ignoring chat {}", chat_id)
            return

        if candidate and hmac.compare_digest(candidate, self._pairing_code):
            username = sender.get("username")
            self._paired_chat_id = chat_id
            self._pairing_code = None
            self._pair_attempts.clear()
            if self._on_paired is not None:
                try:
                    await self._on_paired(
                        chat_id, username if isinstance(username, str) else None
                    )
                except Exception:
                    logger.exception("Telegram: on_paired callback failed")
            logger.info("Telegram: paired with chat {}", chat_id)
            await self._send_raw(
                chat_id, self._t("paired") + "\n\n" + self._t("welcome")
            )
            return

        self._pair_attempts[chat_id] = attempts + 1
        if start_payload == "":
            # /start nudo: è l'inizio dell'onboarding, chiedi il codice.
            logger.info("Telegram: /start during pairing window from chat {}", chat_id)
            await self._send_raw(chat_id, self._t("start_prompt"))
        else:
            logger.info("Telegram: pairing attempt with wrong code from chat {}", chat_id)
            await self._send_raw(chat_id, self._t("wrong_code"))

    # ------------------------------------------------------------------ #
    # Outbound                                                           #
    # ------------------------------------------------------------------ #

    async def send(
        self,
        msg: OutboundMessage,
        *,
        only_conns: list[Any] | None = None,
        skip_persist: bool = False,
    ) -> list[Any]:
        """Consegna un messaggio finale al chat accoppiato.

        Ritorna sempre ``[]``: non esiste fan-out parziale su Telegram.
        Gli eventi di solo coordinamento WebUI vengono ignorati.
        """
        meta = msg.metadata or {}
        if self._is_webui_only_event(meta):
            return []
        if not self._paired_chat_id:
            logger.warning("Telegram: dropping outbound, no paired chat")
            return []
        content = msg.content or ""
        media = [m for m in (msg.media or []) if isinstance(m, str) and m.strip()]
        if not content.strip() and not media:
            return []

        chat_id = str(self._paired_chat_id)
        if content.strip():
            for chunk in split_message(content, _RAW_CHUNK_LIMIT):
                await self._send_chunk(chat_id, chunk)
        # Gli allegati seguono il testo, ciascuno come foto (raster) o documento
        # (SVG e formati non-foto). Un media non inviabile viene loggato e
        # saltato, senza abbattere la consegna del resto.
        for item in media:
            await self._send_media_item(chat_id, item)
        return []

    async def _send_media_item(self, chat_id: str, path: str) -> None:
        """Invia un singolo allegato come foto o documento, best-effort."""
        is_url = path.startswith(("http://", "https://"))
        ext = Path(urlparse(path).path if is_url else path).suffix.lower()
        as_photo = ext in _RASTER_EXTS
        method = "sendPhoto" if as_photo else "sendDocument"
        field = "photo" if as_photo else "document"
        try:
            if is_url:
                await self.api.send_media_url(
                    chat_id, method=method, field=field, url=path
                )
                return
            p = Path(path)
            if not p.is_file():
                logger.warning("Telegram: media not found, skipping: {}", path)
                return
            data = await asyncio.to_thread(p.read_bytes)
            if len(data) > _TG_MEDIA_MAX_BYTES:
                logger.warning(
                    "Telegram: media too large ({} bytes), skipping: {}", len(data), path
                )
                return
            try:
                await self.api.send_media_file(
                    chat_id, method=method, field=field, filename=p.name, data=data
                )
            except TelegramAPIError as e:
                # Un raster rifiutato dall'elaborazione foto (400) passa come
                # documento: meglio consegnare il file che perderlo.
                if as_photo and e.status_code == 400:
                    await self.api.send_media_file(
                        chat_id, method="sendDocument", field="document",
                        filename=p.name, data=data,
                    )
                else:
                    raise
        except Exception as e:
            logger.error("Telegram: media send failed for {}: {}", path, type(e).__name__)

    @staticmethod
    def _is_webui_only_event(meta: dict[str, Any]) -> bool:
        return any(meta.get(key) for key in COORDINATION_FLAGS)

    async def _send_chunk(self, chat_id: str, chunk: str) -> None:
        """Invia un chunk con retry interno; HTML → fallback plain su 400."""
        last_error: Exception | None = None
        for attempt, delay in enumerate((0, *_CHUNK_RETRY_DELAYS)):
            if delay:
                await asyncio.sleep(delay)
            try:
                try:
                    await self.api.send_message(
                        chat_id, markdown_to_telegram_html(chunk), parse_mode="HTML"
                    )
                except TelegramAPIError as e:
                    if e.status_code != 400:
                        raise
                    # HTML rifiutato (tag spezzati da chunking o markdown
                    # inatteso): il testo grezzo passa sempre.
                    await self.api.send_message(chat_id, chunk)
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:
                last_error = e
                logger.warning(
                    "Telegram send failed (attempt {}): {}", attempt + 1, type(e).__name__
                )
        logger.error("Telegram: giving up on chunk after retries: {}", last_error)

    async def _send_raw(self, chat_id: str, text: str) -> None:
        """Risposta di servizio (pairing/media): best-effort, senza transcript."""
        try:
            await self.api.send_message(chat_id, text)
        except Exception:
            logger.exception("Telegram: service reply failed")

