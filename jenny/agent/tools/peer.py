"""Gli strumenti delle altre Jenny: ``peer_ask`` per chiedere, ``peer_attach`` per rispondere con un file.

``peer_ask`` è nella chat personale e nei giri dei suoi promemoria, e solo
quando c'è un'altra Jenny abbinata (``AgentLoop._turn_tool_view``): mette la
domanda in coda e torna subito. La risposta non passa da qui: arriva più tardi
come una scheda a sé nella chat (``jenny.peers.questions``). Così anche un
resoconto serale è solo un promemoria che chiede («ogni sera alle 19 chiedi a
Lavoro com'è andata»), non una funzione a parte. Un lavoro nato da una
richiesta dell'altra Jenny non chiede mai: il blocco dei giri.

``peer_attach`` esiste solo nel turno con cui questa Jenny risponde a un'altra
(``ANSWER_TOOLS``): segna un file del workspace da mandare insieme alla risposta,
se l'utente di questo telefono l'ha permesso. Non manda niente da sé.

Tutti e due nascono senza il servizio delle domande, che il composition root
lega dopo (``AgentLoop.attach_peer_questions``): il collegamento vive nel canale
WebSocket, che nasce dopo l'agente.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jenny.agent.tools.base import Tool, tool_parameters
from jenny.agent.tools.context import ToolContext
from jenny.agent.tools.schema import StringSchema, tool_parameters_schema

_REFUSALS = {
    "no_peers": "No other Jenny is linked to this one. The user links one in the workshop "
                "(Hands → Other Jennys).",
    "not_found": "There is no linked Jenny with that name.",
    "bad_question": "Write the question to ask.",
    "disabled": "Linking with other Jennys is turned off.",
    "from_line": "The user is writing to you through another Jenny: from here you can't "
                 "ask a third one. They go back first (the back arrow, or /torna) and ask "
                 "their own Jenny.",
    "from_peer": "This work was asked for by another Jenny, so it can't ask a Jenny in turn: "
                 "only the user of this phone starts questions.",
}

# Cos'è un peer ``opencode`` (il plugin ``opencode-jenny`` sul Mac dell'utente),
# detto al modello accanto al suo nome. Testo fisso: la descrizione del tool non
# cambia con i peer collegati (la cache del provider parte dalla lista dei tool),
# il nome e il tipo arrivano solo nei risultati.
OPENCODE_WHAT = (
    "opencode on the user's computer: code, git, GitHub; does things there only if the "
    "user turned that on on the computer"
)


def _label(name: str, kind: str) -> str:
    """Il nome di un collegato come lo legge il modello, col tipo se non è una Jenny."""
    return f"{name} ({OPENCODE_WHAT})" if kind == "opencode" else name


@tool_parameters(
    tool_parameters_schema(
        peer=StringSchema(
            "The name of the user's other Jenny to ask, as the user calls it (e.g. its "
            "name in the workshop). Empty when only one is linked.",
            nullable=True,
        ),
        question=StringSchema(
            "The question or request, written for that Jenny: self-contained, with the "
            "names, dates and details she needs, since she can't see this conversation."
        ),
        mode=StringSchema(
            "'read' (default): she looks at what she already has and answers; asking her for "
            "information or for a file she has is 'read' (she can attach files). 'act': she "
            "does something on her phone (open or read a web page there, set a reminder, "
            "write a note, save a file, give her helpers a task), if the user allowed "
            "requests to act there. Use 'act' whenever the user wants something done there, "
            "even when they say 'ask her to…': in 'read' she can't do anything.",
            enum=("read", "act"),
            nullable=True,
        ),
        required=["question"],
    )
)
class PeerAskTool(Tool):
    """Fa una domanda a un'altra Jenny dell'utente (sola lettura, risposta in arrivo)."""

    # Anche ``orchestrator``: è la modalità di default della chat personale, e un
    # tool che dichiara solo ``core`` lì il loader non lo carica affatto.
    _scopes = {"core", "orchestrator"}

    def __init__(self) -> None:
        self._questions: Any | None = None

    def bind_questions(self, questions: Any) -> None:
        self._questions = questions

    @property
    def name(self) -> str:
        return "peer_ask"

    @property
    def description(self) -> str:
        return (
            "Ask the user's other Jenny (on another of their phones, e.g. the work phone) "
            "something only she can see: her chats, notebooks, reminders and files. With "
            "mode='act' you ask her to do something on her phone instead; she may refuse "
            "if the user hasn't allowed it there, and the user of that phone may have to "
            "allow anything that leaves it. A linked peer can also be the user's computer "
            "(opencode on their Mac): ask it about code, git and GitHub; with mode='act' it "
            "does things there (commands, files) if the user turned that on on the Mac, and "
            "refuses otherwise. The request is queued and this returns at once: the answer, with "
            "what was done, shows up later in this chat as a separate card, by itself. "
            "Don't wait for it and never guess what it will say."
        )

    @property
    def read_only(self) -> bool:
        return False

    async def execute(
        self, peer: Any = None, question: Any = None, mode: Any = None, **kwargs: Any
    ) -> str:
        from jenny.agent.tools.context import current_request_session_key
        from jenny.peers.gate import current_act
        from jenny.peers.link import PeerError
        from jenny.peers.model import peer_kind
        from jenny.session.keys import CRON_SESSION_PREFIX

        if self._questions is None:
            return _REFUSALS["no_peers"]
        # Il blocco dei giri: un lavoro nato da una richiesta dell'altra Jenny
        # (il suo turno, i suoi aiutanti, un promemoria che ha creato) non
        # rimanda domande indietro da solo. Il cancello è legato in tutti e tre.
        if current_act() is not None:
            return _REFUSALS["from_peer"]
        # Un salto solo: chi scrive attraverso un'altra Jenny non ne interroga
        # una terza da qui (``jenny.peers.line``).
        from jenny.agent.tools.context import current_request_context
        from jenny.peers.line import line_of

        ctx = current_request_context()
        if ctx is not None and line_of(ctx.metadata) is not None:
            return _REFUSALS["from_line"]
        mode = "act" if mode == "act" else "read"
        try:
            asked = await self._questions.ask(str(peer or ""), str(question or ""), mode)
        except PeerError as exc:
            if exc.code == "which_peer":
                names = ", ".join(
                    _label(p.name, peer_kind(p.kind)) for p in await self._questions.peers()
                )
                return f"Say which one to ask: {names}."
            return _REFUSALS.get(exc.code, f"Could not ask: {exc.code}.")
        name = asked["peer"]
        kind = peer_kind(asked.get("kind"))
        # Le frasi di ritorno sono neutre («the answer», «it is back online»):
        # dall'altra parte può esserci un Mac, non solo un'altra Jenny.
        device = "computer" if kind == "opencode" else "phone"
        verb = "Asked" if mode == "read" else "Sent the request to"
        who = _label(name, kind)
        if (current_request_session_key() or "").startswith(CRON_SESSION_PREFIX):
            # Il giro silenzioso di un monitor: la scheda arriva nella chat
            # personale da sola, un ``message`` per dire «ho chiesto» è rumore.
            return (
                f"{verb} {who}. The answer will reach the user's chat by itself, as its own "
                "card, when it arrives. Don't message the user about having asked."
            )
        # Un «no» arriva subito: lo si aspetta un attimo, così Jenny lo dice ora
        # invece di «aspetto la risposta» con il rifiuto già lì sopra.
        early = await self._questions.early_answer(asked["qid"], asked.get("outbox_id", ""))
        if early is not None:
            return _early_text(early, who=who, name=name, kind=kind, mode=mode)
        return (
            f"{verb} {who}. The answer will appear in this chat as its own card when it "
            f"arrives: in seconds if {name} is reachable, otherwise when that {device} is "
            "back online. Tell the user you asked; don't repeat or guess the answer."
        )


def _early_text(early: dict[str, Any], *, who: str, name: str, kind: str, mode: str) -> str:
    """Cosa dire al modello di una risposta arrivata subito: lo stato, in parole del codice.

    Il testo dell'altra non entra qui: lo mostra la scheda, e un testo arrivato
    da un altro dispositivo non diventa un'istruzione nel turno.
    """
    status = early.get("status")
    computer = kind == "opencode"
    if status == "ok":
        if early.get("working"):
            return (
                f"{who} accepted and is working on it: the card just above in this chat says "
                "so, and another card comes when it's done. Tell the user, briefly."
            )
        return (
            f"{who} already answered: the answer is the card just above in this chat. "
            "Don't repeat or guess it; at most say it answered."
        )
    tail = (
        " The card just above in this chat shows it too. Tell the user now, in your words, "
        "that it didn't happen and why; don't say you're waiting for an answer."
    )
    if status == "refused":
        if computer and mode == "act":
            unlock = early.get("unlock") or ""
            how = (
                f" To let it act, the user runs this on that computer: `{unlock}`."
                if unlock else " The user turns that on on that computer."
            )
            return (
                f"{who} refused: it is set to read only, so it runs no commands and changes "
                f"no files.{how}{tail}"
            )
        if computer:
            return f"{who} refused the question.{tail}"
        if mode == "act":
            return (
                f"{who} refused: requests to act from this phone are off there. The user turns "
                f"them on on {name}'s phone: Workshop → Other Jennys.{tail}"
            )
        return (
            f"{who} refused: questions from this phone are off there. The user turns them on "
            f"on {name}'s phone: Workshop → Other Jennys.{tail}"
        )
    if status == "too_many":
        return (
            f"{who} got too many questions in the last hour and didn't take this one: "
            f"try again later.{tail}"
        )
    if status == "unavailable":
        what = "opencode isn't ready there" if computer else "it has no model to answer with yet"
        return f"{who} couldn't take it: {what}.{tail}"
    return f"{who} couldn't answer.{tail}"


@tool_parameters(
    tool_parameters_schema(
        path=StringSchema("The file to send with your answer: its path in your workspace."),
        required=["path"],
    )
)
class PeerAttachTool(Tool):
    """Allega un file alla risposta per l'altra Jenny (solo nel turno di risposta)."""

    # Il turno di risposta usa il registro dell'agente principale, che di default
    # è quello dell'orchestratore: senza questo scope il tool non c'è.
    _scopes = {"core", "orchestrator"}

    def __init__(self, workspace: Path) -> None:
        self._workspace = Path(workspace)

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        return cls(Path(ctx.workspace))

    @property
    def name(self) -> str:
        return "peer_attach"

    @property
    def description(self) -> str:
        return (
            "Attach a file from your workspace to the answer you are giving another of the "
            "user's Jennys. Only while answering her, and only when the user allowed files."
        )

    async def execute(self, path: Any = None, **kwargs: Any) -> str:
        from jenny.peers.files import MAX_FILE_BYTES
        from jenny.peers.questions import MAX_FILES, PEER_TURN
        from jenny.security.subagent_policy import is_config_file

        turn = PEER_TURN.get()
        if turn is None:
            return "Not available here: this is only for answering another Jenny."
        if not turn.allow_files:
            return (
                f"Files can't be sent to {turn.peer_name} from this phone: the user hasn't "
                "allowed it. Say in your answer that the file exists and where."
            )
        if len(turn.attachments) >= MAX_FILES:
            return f"At most {MAX_FILES} files per answer."
        raw = str(path or "").strip()
        if not raw:
            return "Give the path of the file to attach."
        root = self._workspace.resolve()
        candidate = (root / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
        try:
            rel = candidate.relative_to(root)
        except ValueError:
            return "Only files inside your workspace can be attached."
        if rel.parts and rel.parts[0].startswith(".jenny") or is_config_file(candidate, root):
            return "That file can't leave this phone."
        if not candidate.is_file():
            return f"There is no file at {raw}."
        if candidate.stat().st_size > MAX_FILE_BYTES:
            return f"{candidate.name} is too large to send (max {MAX_FILE_BYTES // (1024 * 1024)} MB)."
        if candidate not in turn.attachments:
            turn.attachments.append(candidate)
        return f"Attached {candidate.name}: it goes with your answer. Mention it in your reply."


_LINE_REFUSALS = {
    "no_peers": _REFUSALS["no_peers"],
    "not_found": _REFUSALS["not_found"],
    "disabled": _REFUSALS["disabled"],
    "bad_place": "That is not the name of a notebook.",
    "bad_channel": "The line can't be passed from here: only from the chat in the app or Telegram.",
    "from_line": "The user is already writing to you through another Jenny: from here the "
                 "line can't be passed on to a third one. Tell them to go back first (the "
                 "back arrow, or /torna) and ask their own Jenny.",
}

# Perché l'altra non ha preso la linea (il motivo che torna da `PeerLines.settled`).
_LINE_CLOSED = {
    "not_allowed": "{name} does not take the line from this phone: the user allows it on "
                   "{name}'s phone, in her Settings, under the other Jennys, with the switch "
                   "that lets this phone pass the line there (describe it in the user's "
                   "language; do not quote an English label).",
    "unsupported": "{name} runs an older version that cannot take the line yet: it needs "
                   "an update.",
    "unpaired": "{name} is no longer linked to this phone.",
}


@tool_parameters(
    tool_parameters_schema(
        peer=StringSchema(
            "The name of the user's other Jenny (or linked computer) to hand the chat to, "
            "as the user calls it. Empty when only one is linked.",
            nullable=True,
        ),
        notebook=StringSchema(
            "Only when the user names one of HER notebooks to talk to there ('pass me "
            "Work, in the suppliers notebook'): its name. Otherwise leave it empty: the "
            "line opens on her main chat.",
            nullable=True,
        ),
    )
)
class PeerLineTool(Tool):
    """Passa la chat a un'altra Jenny: da lì in poi l'utente scrive direttamente a lei."""

    _scopes = {"core", "orchestrator"}

    def __init__(self) -> None:
        self._questions: Any | None = None

    def bind_questions(self, questions: Any) -> None:
        self._questions = questions

    @property
    def name(self) -> str:
        return "peer_line"

    @property
    def description(self) -> str:
        return (
            "Hand this chat over to the user's other Jenny (or to their linked computer), "
            "when the user asks to talk to her directly: 'pass me Work', 'let me talk to "
            "the work Jenny', 'put me through to…'. From then on what the user writes here "
            "goes straight to her, and you neither read it nor answer, until they come "
            "back (the back arrow, or /torna). Use it only on such a request. To get one "
            "answer from her while staying here, use peer_ask instead. After calling it, "
            "reply with one short line saying you are passing them over and how to come back."
        )

    @property
    def read_only(self) -> bool:
        return False

    async def execute(self, peer: Any = None, notebook: Any = None, **kwargs: Any) -> str:
        from jenny.agent.tools.context import current_request_context
        from jenny.peers.gate import current_act
        from jenny.peers.line import line_of
        from jenny.peers.link import PeerError
        from jenny.session.keys import is_personal_session_key

        lines = getattr(self._questions, "lines", None)
        if lines is None:
            return _LINE_REFUSALS["no_peers"]
        ctx = current_request_context()
        if ctx is None or not is_personal_session_key(ctx.session_key or ""):
            return "The line is passed from the user's main chat, not from here."
        if line_of(ctx.metadata) is not None:
            return _LINE_REFUSALS["from_line"]
        if current_act() is not None:
            return _REFUSALS["from_peer"]
        try:
            line = await lines.open(
                ctx.channel, ctx.chat_id, str(peer or ""), str(notebook or "").strip(),
            )
        except PeerError as exc:
            if exc.code == "which_peer":
                names = ", ".join(p.name for p in await self._questions.peers())
                return f"Say which one: {names}."
            return _LINE_REFUSALS.get(exc.code, f"Could not pass the line: {exc.code}.")
        name = line["name"]
        outcome = await lines.settled(ctx.channel, ctx.chat_id, line["lid"])
        if outcome == "open":
            asked = str(notebook or "").strip()
            now = await lines.status(ctx.channel, ctx.chat_id) or {}
            if asked and str(now.get("place") or "").casefold() != asked.casefold():
                return (
                    f"The line is passed to {name}, but on her main chat: she has no "
                    f"notebook called '{asked}'. The user's next messages in this chat go to "
                    "her, not to you. Reply now with one short line saying both things, and "
                    "that they come back with the back arrow or by writing /torna."
                )
            return (
                f"The line is passed to {name}: the user's next messages in this chat go "
                "to her, not to you. Reply now with one short line: that you are passing them "
                "over, and that they come back with the back arrow or by writing /torna."
            )
        if outcome == "waiting":
            return (
                f"The line to {name} is open on this side, but {name} has not answered yet "
                "(that phone may be asleep or offline): what the user writes in this chat now "
                "waits and reaches her when she is back; it does not come to you. Reply now "
                "with one short line saying exactly that, and that they come back with the "
                "back arrow or by writing /torna."
            )
        why = _LINE_CLOSED.get(outcome, "{name} closed the line at once.").format(name=name)
        return (
            f"The line was NOT passed: {why} The user is still talking to you. Tell them so "
            "in one short line; do not say you are passing them over."
        )


@tool_parameters(
    tool_parameters_schema(
        notebook=StringSchema(
            "The name of one of your notebooks to move the conversation to. Empty to go "
            "back to your main chat.",
            nullable=True,
        ),
    )
)
class LineMoveTool(Tool):
    """Dentro una linea: sposta la conversazione nella chat di un quaderno di questa Jenny."""

    _scopes = {"core", "orchestrator"}

    def __init__(self) -> None:
        self._questions: Any | None = None

    def bind_questions(self, questions: Any) -> None:
        self._questions = questions

    @property
    def name(self) -> str:
        return "line_move"

    @property
    def description(self) -> str:
        return (
            "Only while the user is writing to you from their other phone, through their "
            "other Jenny (those messages are marked as coming from her): move the "
            "conversation to the chat of one of your notebooks, when they ask ('let's move "
            "to the plants notebook'), or back to your main chat. Their next message "
            "arrives there. Anywhere else this does nothing."
        )

    @property
    def read_only(self) -> bool:
        return False

    async def execute(self, notebook: Any = None, **kwargs: Any) -> str:
        from jenny.agent.tools.context import current_request_context
        from jenny.peers.line import line_of

        lines = getattr(self._questions, "lines", None)
        ctx = current_request_context()
        peer_id = line_of(ctx.metadata) if ctx is not None else None
        if lines is None or peer_id is None:
            return (
                "Not available here: this is only for when the user writes to you through "
                "another Jenny. In the app they open a notebook themselves."
            )
        place = str(notebook or "").strip()
        refusal = await lines.move_here(peer_id, place)
        if refusal == "no_place":
            names = ", ".join(lines.place_names()[:40]) or "none"
            return f"There is no notebook called {place!r}. The notebooks are: {names}."
        if refusal is not None:
            return "The line is no longer open."
        where = f"the {place} notebook" if place else "your main chat"
        return (
            f"Moved: the user's next message arrives in {where}, and is answered there. "
            "Reply now with one short line saying so."
        )


# Registrazione esplicita: il ToolLoader legge questa lista. Il modulo va anche
# aggiunto a ``_HARDCODED_TOOL_MODULES`` in ``loader.py``.
TOOLS = [PeerAskTool, PeerAttachTool, PeerLineTool, LineMoveTool]
