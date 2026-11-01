"""Message tool for sending messages to users."""

from contextvars import ContextVar
from pathlib import Path
from typing import Any, Awaitable, Callable

from loguru import logger

from jenny.agent.tools.base import Tool, tool_parameters
from jenny.agent.tools.context import ContextAware, RequestContext
from jenny.agent.tools.path_utils import resolve_workspace_path
from jenny.agent.tools.schema import ArraySchema, StringSchema, tool_parameters_schema
from jenny.bus.events import OutboundMessage
from jenny.config.paths import get_workspace_path
from jenny.cron.could_not_check import is_only_markers
from jenny.security.subagent_policy import is_config_file
from jenny.security.workspace_access import current_tool_workspace
from jenny.security.workspace_policy import _safe_expanduser
from jenny.session.turn_visibility import is_silent_turn, mark_silent_turn

# Sentinel di default per i flag per-turno: condiviso, mai mutato. Un turno vero
# riceve il suo dict fresco da ``start_turn()``.
_NO_TURN_FLAGS: dict[str, bool] = {}


# Parole che non sono un avviso in nessuna lingua e in nessun contesto: sono
# meta, parlano del turno e non di quello che è successo. La lista è corta e
# chiusa di proposito — v. :func:`_unusable_silent_alert` sul perché qui NON ci
# va un'euristica.
_NOT_AN_ALERT = frozenset(
    {"silent", "test", "ok", "boh", "nothing", "none", "null", "n/a", "na", "nulla"}
)


def _unusable_silent_alert(content: str, *, has_media: bool) -> str | None:
    """Dice perché *content* non è un avviso, o ``None`` se lo è.

    Un turno silenzioso non consegna una risposta: consegna un **avviso**, e un
    avviso è una frase rivolta a una persona. Tre forme che non lo sono hanno
    raggiunto la chat davvero, tutte da qui — misurate il 2026-08-24 sul
    transcript del dispositivo, 11 consegne su 32 fra il 16 e il 24 agosto:

    - il **marcatore di protocollo** (``CHECK_OK 1`` il 16, 17 e 23 agosto,
      ``CHECK_OK`` il 23). È il caso peggiore dei tre, perché sbaglia due volte:
      l'utente riceve una parola d'ordine interna, e il verdetto non viene
      registrato — ``parse_ok_marks`` legge il testo finale del turno, non
      l'argomento di un tool, quindi la voce del task resta in sospeso come se
      il modello non si fosse pronunciato;
    - la **parola nuda** (``silent`` il 24, ``x`` il 17 e il 22, ``boh`` il 16,
      ``test`` il 17). Il modello usa la chat come blocco note;
    - il **testo vuoto** (il 16, il 18, due volte il 23), che in chat è una
      bolla vuota.

    Perché il prompt non basta: il contratto dell'annuncio di un subagent dice
    "l'unico modo di raggiungere l'utente è il tool ``message``" e quello
    del seguito di un monitor delegato dice "chiudi la risposta con ``CHECK_OK``". Le due
    istruzioni collidono, e un modello che vuole far *contare* il proprio
    verdetto lo manda col tool. Stessa scelta del tetto di un avviso per ciclo
    qui sotto: il prompt lo vieta, questo lo rende impossibile.

    **Il terzo criterio è una lista, non un'euristica, e la scelta è
    asimmetrica.** Il primo tentativo era "una parola sola e nessuna cifra": una
    regola che sembra ragionevole e che il test del tetto per ciclo ha bocciato
    subito, perché rifiutava ``"primo"``. Il punto non è quel test — è che una
    forma è un indizio pessimo di ciò che una parola *dice*, e i due errori non
    si pagano allo stesso prezzo: una parola nuda che passa è rumore in chat,
    un avviso vero che viene rifiutato è un monitor che tace proprio quando
    aveva qualcosa da dire. Quindi qui si blocca solo ciò che non può essere un
    avviso in nessun caso — un carattere solo (``x``, ``.``, ``🙄``) e le parole
    meta di :data:`_NOT_AN_ALERT` — e tutto il resto passa. Copre le cinque
    forme osservate; una sesta le sfuggirà, ed è il prezzo giusto.

    **La sesta è arrivata, e cinque volte.** Fra il 26 agosto e il 3 settembre:
    ``noop``, un «tutto a posto» in italiano, un ``Silenzio: … ok.``, ``silent-skip``,
    ``placeholder`` — nessuna intercettabile da una lista, tutte consegnate. La
    denylist non è stata allargata, perché allargarla è la gara che non si vince:
    è stata invece tolta di mezzo la causa. Un turno silenzioso ha ora
    ``nothing_to_report``, cioè un'azione da compiere quando non c'è niente da
    dire, e i tre rifiuti qui sotto la indicano. Prima chiedevano al modello di
    *non fare* qualcosa — un'istruzione che, misurata, non è eseguibile.

    Rifiutare — invece di riscrivere il testo — è la forma giusta perché la
    quota del ciclo non viene consumata (``_sent_in_turn`` si alza solo su una
    consegna riuscita): il modello ha un altro tentativo, e la stringa di
    ritorno gli dice dove va davvero il marcatore. Riscrivere sarebbe peggio:
    consegnerebbe all'utente un testo che il modello non ha scritto.
    """
    text = content.strip()
    if not text:
        if has_media:
            # Un allegato senza didascalia è un invio legittimo: il file È il
            # messaggio.
            return None
        return (
            "Error: nothing was delivered — the message text was empty. "
            "A silent check reaches the user only with real user-facing text. "
            "If there is nothing to report, call `nothing_to_report` instead."
        )
    if is_only_markers(text):
        return (
            "Error: not delivered — CHECK_OK / CHECK_FAILED / CHECK_DELEGATED / "
            "CHECK_WARNED are not messages. Those lines belong in your answer text, "
            "which is where they are read and recorded; sending one here reaches the "
            "user with an internal marker and records nothing. Write the line in your "
            "answer instead — or call `nothing_to_report`, which records the same "
            "verdict as an action — and call this tool only with user-facing text."
        )
    if len(text) == 1 or text.casefold() in _NOT_AN_ALERT:
        return (
            f"Error: {text!r} was not delivered. A silent check sends an alert, and an "
            "alert is a sentence naming what happened and what the user should do. "
            "Either write that text, or call `nothing_to_report` instead."
        )
    return None


@tool_parameters(
    tool_parameters_schema(
        content=StringSchema(
            "Message content for proactive or cross-channel delivery. "
            "Do not use this for a normal reply in the current chat."
        ),
        channel=StringSchema(
            "Optional target channel for cross-channel/proactive delivery. "
            "Do not set this to the current runtime channel for a normal reply."
        ),
        chat_id=StringSchema(
            "Optional target chat/user ID for cross-channel/proactive delivery. "
            "On WebSocket/WebUI turns: omit chat_id to use the server's conversation id "
            "(never pass client_id values like anon-…). "
            "Do not set this to the current runtime chat for a normal reply."
        ),
        media=ArraySchema(
            StringSchema(""),
            description="Optional list of existing file paths to attach.",
        ),
        buttons=ArraySchema(
            ArraySchema(StringSchema("Button label")),
            description="Optional: inline keyboard buttons as list of rows, each row is list of button labels.",
        ),
        required=["content"],
    )
)
class MessageTool(Tool, ContextAware):
    """Tool to send messages to users on chat channels."""

    _scopes = {"core", "orchestrator"}

    def __init__(
        self,
        send_callback: Callable[[OutboundMessage], Awaitable[None]] | None = None,
        default_channel: str = "",
        default_chat_id: str = "",
        default_message_id: str | None = None,
        workspace: str | Path | None = None,
        restrict_to_workspace: bool = False,
    ):
        self._send_callback = send_callback
        self._workspace = (
            _safe_expanduser(workspace) if workspace is not None else get_workspace_path()
        )
        self._restrict_to_workspace = restrict_to_workspace
        self._default_channel: ContextVar[str] = ContextVar(
            "message_default_channel", default=default_channel
        )
        self._default_chat_id: ContextVar[str] = ContextVar(
            "message_default_chat_id", default=default_chat_id
        )
        self._default_message_id: ContextVar[str | None] = ContextVar(
            "message_default_message_id",
            default=default_message_id,
        )
        self._default_metadata: ContextVar[dict[str, Any]] = ContextVar(
            "message_default_metadata",
            default={},
        )
        # I flag per-turno vivono DENTRO un dict mutabile tenuto dalla ContextVar,
        # non come valore della ContextVar stessa. Il tool viene eseguito in un
        # task figlio (``asyncio.wait_for``/``gather`` in tool_execution) che
        # riceve una *copia* del context: un ``ContextVar.set()`` fatto lì dentro
        # non risalirebbe mai al turno, e la soppressione della risposta finale in
        # ``AgentLoop._assemble_outbound`` non scatterebbe (utente = messaggio
        # doppio). Mutando l'oggetto condiviso il turno vede la scrittura, e
        # l'isolamento fra turni concorrenti resta garantito perché ogni turno
        # installa un dict nuovo nel proprio context. Stesso idioma di
        # ``file_state._current_file_states``.
        self._turn_flags: ContextVar[dict[str, bool]] = ContextVar(
            "message_turn_flags",
            default=_NO_TURN_FLAGS,
        )

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        send_callback = ctx.bus.publish_outbound if ctx.bus else None
        return cls(
            send_callback=send_callback,
            workspace=ctx.workspace,
            restrict_to_workspace=ctx.config.restrict_to_workspace,
        )

    def set_context(self, ctx: RequestContext) -> None:
        """Set the current message context."""
        self._default_channel.set(ctx.channel)
        self._default_chat_id.set(ctx.chat_id)
        self._default_message_id.set(ctx.message_id)
        self._default_metadata.set(dict(ctx.metadata or {}))

    def set_send_callback(self, callback: Callable[[OutboundMessage], Awaitable[None]]) -> None:
        """Set the callback for sending messages."""
        self._send_callback = callback

    def start_turn(self) -> None:
        """Reset per-turn send tracking.

        Installa un contenitore nuovo nel context del turno: le scritture dei
        tool (che girano in task figli) mutano *questo* oggetto e restano
        visibili al turno che l'ha creato.
        """
        self._turn_flags.set({"sent_in_turn": False})

    @property
    def _sent_in_turn(self) -> bool:
        return self._turn_flags.get().get("sent_in_turn", False)

    @_sent_in_turn.setter
    def _sent_in_turn(self, value: bool) -> None:
        flags = self._turn_flags.get()
        if flags is _NO_TURN_FLAGS:
            # Nessuno ``start_turn()`` per questo context (uso diretto del tool,
            # fuori da un turno): la scrittura resta locale al context invece di
            # mutare il sentinel condiviso da tutte le istanze.
            flags = {}
            self._turn_flags.set(flags)
        flags["sent_in_turn"] = value

    @property
    def name(self) -> str:
        return "message"

    @property
    def description(self) -> str:
        return (
            "Proactively send a message to a user/channel, optionally with file attachments. "
            "Use this for reminders, cross-channel delivery, or explicit proactive sends. "
            "Do not use this for the normal reply in the current chat: answer naturally instead. "
            "If channel/chat_id would target the current runtime conversation, do not call this tool "
            "unless the user explicitly asked you to proactively send an existing file attachment. "
            "For proactive attachment delivery, use the 'media' parameter with file paths. "
            "Do NOT use read_file to send files — that only reads content for your own analysis."
        )

    def _is_config_file(self, path: Path) -> bool:
        """*path* e' uno dei file di configurazione nella radice del workspace?

        Il security model promette che le chiavi del provider e i segreti non
        escono dal telefono tramite ``openFile``/``shareFile``: un allegato di
        ``message`` verso Telegram e' la stessa uscita, e va chiusa allo stesso
        modo. I nomi stanno in ``security.subagent_policy``, che li nega anche
        ai subagent.
        """
        return is_config_file(path, self._workspace)

    def _resolve_media(self, media: list[str]) -> list[str]:
        """Resolve local media attachments and enforce workspace restriction when enabled.

        Raises ``ValueError`` when an attachment is one of the gateway's own
        configuration files: those never leave the device as an attachment.
        """
        resolved: list[str] = []
        access = current_tool_workspace(
            self._workspace,
            restrict_to_workspace=self._restrict_to_workspace,
        )
        workspace = access.project_path or self._workspace
        for p in media:
            if p.startswith(("http://", "https://")):
                resolved.append(p)
                continue
            if not access.restrict_to_workspace:
                try:
                    path = _safe_expanduser(p)
                except (RuntimeError, OSError):
                    path = Path(p)
                local = p if path.is_absolute() else str(workspace / path)
            else:
                local = str(resolve_workspace_path(p, workspace, access.allowed_root))
            if self._is_config_file(Path(local)):
                raise ValueError(
                    f"'{p}' is a gateway configuration file and cannot be sent as an attachment"
                )
            resolved.append(local)
        return resolved

    async def execute(
        self,
        content: str,
        channel: str | None = None,
        chat_id: str | None = None,
        message_id: str | None = None,
        media: list[str] | None = None,
        buttons: list[list[str]] | None = None,
        **kwargs: Any,
    ) -> str:
        from jenny.utils.helpers import strip_think

        raw_content = content
        content = strip_think(content)
        # Se lo strip ha svuotato un testo che c'era, quel testo era SOLO
        # macchina del template — marker di thinking o un blocco di tool call
        # scritto come prosa (v. ``strip_think``, punti 7-8). Consegnarlo
        # comunque vuol dire una bolla vuota in chat; rifiutare lascia al
        # modello un altro tentativo, ed è la stessa asimmetria di
        # ``_unusable_silent_alert``: un invio perso è recuperabile al giro
        # dopo, un invio di macchina all'utente no. Vale su qualunque turno,
        # silenzioso o visibile, perché qui non si giudica *cosa* dice il
        # messaggio: non è rimasto nulla da dire.
        if raw_content.strip() and not content.strip() and not media:
            logger.info(
                "MessageTool: template-leak-only content suppressed: {!r}", raw_content[:80]
            )
            return (
                "Error: nothing was delivered — after removing template markers "
                "(thinking tokens, tool-call markup) your message had no text left. "
                "Write it as plain user-facing text and send it again."
            )

        if buttons is not None:
            if not isinstance(buttons, list) or any(
                not isinstance(row, list) or any(not isinstance(label, str) for label in row)
                for row in buttons
            ):
                return "Error: buttons must be a list of list of strings"
        default_channel = self._default_channel.get()
        default_chat_id = self._default_chat_id.get()
        channel = channel or default_channel
        explicit_chat_id = chat_id
        if (
            default_channel == "websocket"
            and channel == "websocket"
            and explicit_chat_id is not None
            and str(explicit_chat_id).strip() != ""
            and str(explicit_chat_id).strip() != str(default_chat_id).strip()
        ):
            return (
                "Error: chat_id does not match the active WebSocket conversation. "
                "Omit chat_id (and usually channel) so delivery uses the current "
                "conversation id from context — WebSocket client_id strings "
                "(e.g. anon-…) are not chat ids."
            )
        chat_id = chat_id or default_chat_id
        # Only inherit default message_id when targeting the same channel+chat.
        # Cross-chat sends must not carry the original message_id, because
        # some channels use it to determine the target conversation via their
        # Reply API, which would route the message to the wrong chat entirely.
        same_target = channel == default_channel and chat_id == default_chat_id
        if same_target:
            message_id = message_id or self._default_message_id.get()
        else:
            message_id = None

        if not channel or not chat_id:
            return "Error: No target channel/chat specified"

        # Un ciclo silenzioso manda AL MASSIMO un avviso. Non e' una quota
        # arbitraria: un turno silenzioso non e' una conversazione, e' un avviso —
        # e un avviso e' uno. Misurato sul dispositivo: senza questo tetto un
        # singolo ciclo heartbeat ha consegnato cinque messaggi di fila
        # ("sto aspettando", "ok basta, mi zitto", "🙄"), perche' il modello
        # trattava la chat come un canale di pensiero. Il prompt lo vieta, ma il
        # prompt non garantisce: qui il secondo tentativo non parte, e la stringa
        # di ritorno dice al modello cosa fare invece (accorpare).
        if self._sent_in_turn and is_silent_turn(self._default_metadata.get()):
            logger.info("MessageTool: second send suppressed on a silent turn")
            return (
                "Error: you already sent the one alert this scheduled run is allowed. "
                "A silent check delivers at most one message per run. Do not try again: "
                "if something is missing, it belongs in that single message — say it in "
                "the next run instead."
            )

        # Stesso posto, stessa ragione del tetto qui sopra, su un asse diverso:
        # quello contava i messaggi, questo guarda se ce n'è uno. Dopo il tetto e
        # non prima perché un secondo invio va spiegato come secondo invio.
        if is_silent_turn(self._default_metadata.get()):
            refusal = _unusable_silent_alert(content, has_media=bool(media))
            if refusal is not None:
                logger.info(
                    "MessageTool: unusable silent alert suppressed: {!r}", content[:80]
                )
                return refusal

        if not self._send_callback:
            return "Error: Message sending not configured"

        if media:
            try:
                media = self._resolve_media(media)
            except (OSError, PermissionError, ValueError) as e:
                return f"Error: media path is not allowed: {str(e)}"

        metadata = dict(self._default_metadata.get()) if same_target else {}
        # La visibilità del turno sopravvive al cambio di target. Un invio
        # cross-channel butta via lo stato di *routing* dell'origine (message_id
        # su tutti: instraderebbe la risposta nella chat sbagliata), ma la
        # visibilità non è una proprietà del target: è del turno, risolta una
        # volta al suo confine, e i consumatori a valle la leggono dai metadata
        # (v. ``jenny.session.turn_visibility``). Serve al ChannelDeliverer, che
        # da qui sa se questa consegna è un turno WebUI a sé — e deve quindi
        # chiuderselo da solo — o se vive dentro un turno visibile che il suo
        # ``turn_end`` ce l'ha già.
        if not same_target and is_silent_turn(self._default_metadata.get()):
            mark_silent_turn(metadata)
        if message_id:
            metadata["message_id"] = message_id
        # Un invio proattivo è l'unica cosa che il modello dice all'utente da
        # FUORI la conversazione: gira su una sessione interna (cron,
        # Dream), quindi il suo testo finisce nella history di *quella* sessione
        # e non in quella unificata. Senza questa registrazione l'utente vede
        # l'avviso (transcript WebUI + notifica Android) e al turno dopo il
        # modello non ne ha traccia. Misurato sul dispositivo il 2026-08-12:
        # avviso "il server non è raggiungibile" alle 18:33 dall'heartbeat, "sicura?"
        # alle 18:39 su ``unified:default`` — risposto come se non fosse mai
        # stato detto, perché nel contesto di quel turno non c'era.
        # La registrazione segue l'intento proattivo, che è la ragione vera:
        # prima dipendeva dalla presenza di un allegato (che la attivava per il
        # motivo diverso di conservare la copia dei media in cronologia).
        proactive = not same_target or is_silent_turn(self._default_metadata.get())
        if proactive or media:
            metadata["_record_channel_delivery"] = True
        # Fan-out cross-canale SOLO per un invio davvero proattivo/cross-channel.
        # Un allegato o messaggio nella conversazione corrente (``same_target``)
        # resta sul canale d'origine e non viene diffuso agli altri canali
        # accoppiati (es. Telegram): il ChannelDeliverer diffonde solo con
        # questo flag (o con ``proactive=True`` dai chiamanti cron).
        # Eccezione: un turno SILENZIOSO (cron monitor, lavoro interno
        # in genere). Li ``same_target`` e vero (canale/chat d'origine) ma
        # l'utente NON e in conversazione: sta arrivando un avviso, non una
        # risposta, e deve raggiungere anche i canali accoppiati (WebUI +
        # Telegram). E' la stessa consegna proattiva che prima faceva a mano il
        # cron dispatcher per l'heartbeat. La condizione si legge dai metadata del
        # turno gia propagati in ``set_context``: nessuno stato nuovo sul tool e
        # nessun call site in piu da tenere allineato.
        if proactive:
            metadata["_proactive_fanout"] = True

        msg = OutboundMessage(
            channel=channel,
            chat_id=chat_id,
            content=content,
            media=media or [],
            buttons=buttons or [],
            metadata=metadata,
        )

        try:
            await self._send_callback(msg)
            if channel == default_channel and chat_id == default_chat_id:
                self._sent_in_turn = True
            media_info = f" with {len(media)} attachments" if media else ""
            button_info = f" with {sum(len(row) for row in buttons)} button(s)" if buttons else ""
            return f"Message sent to {channel}:{chat_id}{media_info}{button_info}"
        except Exception as e:
            return f"Error sending message: {str(e)}"


# Registrazione esplicita dei tool di questo modulo (Fase 5.3): il
# ToolLoader legge questa lista invece della reflection dir(). Un nuovo
# tool va aggiunto qui esplicitamente.
TOOLS = [MessageTool]
