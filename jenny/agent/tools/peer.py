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
    "from_peer": "This work was asked for by another Jenny, so it can't ask a Jenny in turn: "
                 "only the user of this phone starts questions.",
}

# Cos'è un peer ``opencode`` (il plugin ``opencode-jenny`` sul Mac dell'utente),
# detto al modello accanto al suo nome. Testo fisso: la descrizione del tool non
# cambia con i peer collegati (la cache del provider parte dalla lista dei tool),
# il nome e il tipo arrivano solo nei risultati.
OPENCODE_WHAT = "opencode on the user's computer: code, git, GitHub, read-only"


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
            "(opencode on their Mac): ask it about code, git and GitHub; it only reads and "
            "can't act. The request is queued and this returns at once: the answer, with "
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
        return (
            f"{verb} {who}. The answer will appear in this chat as its own card when it "
            f"arrives: in seconds if {name} is reachable, otherwise when that {device} is "
            "back online. Tell the user you asked; don't repeat or guess the answer."
        )


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


# Registrazione esplicita: il ToolLoader legge questa lista. Il modulo va anche
# aggiunto a ``_HARDCODED_TOOL_MODULES`` in ``loader.py``.
TOOLS = [PeerAskTool, PeerAttachTool]
