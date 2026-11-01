"""Tool ``nothing_to_report``: l'astensione **dichiarata** di un turno silenzioso.

Il difetto che questo modulo chiude. Su un turno silenzioso l'unica azione che
raggiunge l'utente è il tool ``message``, quindi "ho qualcosa da dire" è
un'azione e "non ho niente da dire" è l'**assenza** di un'azione. Un modello
piccolo codifica la seconda come la prima con un contenuto vuoto:
``message("silent")``, ``message("x")``, ``message("placeholder")``,
``message("CHECK_OK 1")``. Misurate sul Titan 2 fra il 21 agosto e il 3
settembre 2026: 14 bolle di riempimento arrivate davvero in chat, e in una
finestra di 11 ore le **uniche due** chiamate a ``message`` erano entrambe
spazzatura. Il guardiano in ``message.py::_unusable_silent_alert`` ne blocca
cinque forme e altrettante gliene sono sfuggite dopo: una denylist non può
vincere contro l'insieme infinito dei modi di dire niente. Qui l'astensione
diventa una cosa che il modello **fa**.

Come si innesta senza toccare nessun lettore a valle. Il turno registra le
dichiarazioni in un dict per-turno; ``AgentLoop`` le trascrive in righe
``CHECK_OK <n>`` accodate al solo ``TurnOutcome.final_text`` — mai a
``ctx.final_content``, quindi la history della sessione resta pulita. Da lì in
poi ``parse_ok_marks`` legge ciò che leggeva prima, e non cambia un byte in
tutto il pipeline dei marcatori.

**Solo con un numero esplicito**, e non è pedanteria. ``CHECK_OK`` non dice
"non ho niente da dire", dice "il controllo ha prodotto la sua risposta"
(v. ``cron/monitor_followup.py``), e su un monitor in attesa di verdetto azzera
la sequenza dei guasti **e** il ricordo di aver già avvisato l'utente.
Sintetizzare un ``CHECK_OK`` nudo su ogni astensione vorrebbe dire chiudere come
sano un controllo delegato di cui questo turno non ha detto niente, e rendere
codice morto il ramo "il verdetto non è arrivato affatto"
(``resolve_pending_delegation``), che quel ricordo lo conserva di proposito.
Con la regola "solo numerato" un'astensione nuda lascia lo stato esattamente
com'era: nessuna regressione possibile.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from loguru import logger

from jenny.agent.tools.base import Tool, tool_parameters
from jenny.agent.tools.context import ContextAware, RequestContext
from jenny.agent.tools.schema import IntegerSchema, tool_parameters_schema
from jenny.cron.could_not_check import OK_MARKER
from jenny.session.turn_visibility import is_silent_turn

# Sentinella condivisa, mai mutata. Stesso idioma — e stessa trappola — di
# ``MessageTool._turn_flags``: mutarla propagherebbe le dichiarazioni di un turno
# a tutti i turni futuri di ogni context, chat comprese. La guardia in
# :meth:`NothingToReportTool._record` è ciò che lo impedisce, e serve davvero: la
# FSM ha un percorso che salta BUILD — ``(COMMAND, "shortcut") -> DONE`` — e con
# esso ``start_turn()``, pur arrivando poi a ``TurnOutcome.of``.
_NO_TURN_STATE: dict[str, Any] = {}

# Tetto difensivo sulle dichiarazioni numerate di un turno: un modello in loop
# non deve poter far crescere ``final_text`` senza limite. Dodici è oltre
# qualunque elenco di task plausibile.
_MAX_DECLARATIONS = 12


@tool_parameters(
    tool_parameters_schema(
        task=IntegerSchema(
            description=(
                "The number of the check this covers, as listed in this run's prompt. "
                "Omit it when the run listed no numbers."
            ),
            minimum=1,
            maximum=999,
            nullable=True,
        ),
    )
)
class NothingToReportTool(Tool, ContextAware):
    """Dichiara che questo turno silenzioso non ha niente per l'utente."""

    # Deliberatamente NON ``subagent``: un subagent non ha il tool ``message``,
    # il suo turno non è silenzioso in questo senso, e il suo esito lo giudica il
    # turno d'annuncio, che è un turno dell'agente principale.
    _scopes = {"core", "orchestrator"}

    def __init__(self) -> None:
        self._metadata: ContextVar[dict[str, Any]] = ContextVar(
            "nothing_to_report_metadata", default={}
        )
        # I tool girano in un task figlio che riceve una *copia* del context: un
        # ``ContextVar.set()`` fatto lì dentro non risalirebbe mai al turno. Si
        # muta quindi l'oggetto tenuto dalla ContextVar, non la ContextVar.
        self._turn_state: ContextVar[dict[str, Any]] = ContextVar(
            "nothing_to_report_turn_state", default=_NO_TURN_STATE
        )

    @property
    def name(self) -> str:
        return "nothing_to_report"

    @property
    def description(self) -> str:
        return (
            "On a silent scheduled run, declare that there is nothing the user needs to "
            "see. Use it instead of sending an empty, one-word or placeholder message: it "
            "delivers nothing and reaches nobody. Not for a normal turn (there, just answer), and not for a "
            "check that could not be carried out — that is a CHECK_FAILED line in your "
            "answer text, which this does not replace."
        )

    def set_context(self, ctx: RequestContext) -> None:
        self._metadata.set(dict(ctx.metadata or {}))

    def start_turn(self) -> None:
        """Azzera le dichiarazioni del turno.

        Installa un contenitore nuovo nel context del turno: le scritture dei
        task figli mutano *questo* oggetto e restano visibili al turno che l'ha
        creato.
        """
        self._turn_state.set({"tasks": [], "bare": 0})

    def declared_tasks(self) -> list[int]:
        """I numeri di task dichiarati in questo turno, in ordine e senza doppioni."""
        return list(self._turn_state.get().get("tasks", ()))

    def _record(self, task: int | None) -> int:
        state = self._turn_state.get()
        if state is _NO_TURN_STATE:
            # Nessuno ``start_turn()`` per questo context (uso diretto del tool,
            # o un percorso di turno che salta BUILD): la scrittura resta locale
            # invece di mutare la sentinella condivisa da tutte le istanze.
            state = {"tasks": [], "bare": 0}
            self._turn_state.set(state)
        tasks: list[int] = state.setdefault("tasks", [])
        if task is None:
            state["bare"] = state.get("bare", 0) + 1
        elif task not in tasks and len(tasks) < _MAX_DECLARATIONS:
            tasks.append(task)
        return len(tasks) + state.get("bare", 0)

    async def execute(self, task: Any = None, **kwargs: Any) -> str:
        # Un turno visibile consegna la propria risposta finale da sé: qui non
        # c'è niente da cui astenersi, e lasciar passare la chiamata insegnerebbe
        # al modello che è un modo di chiudere una conversazione. La stringa NON
        # comincia per "Error": ``tool_execution`` classificherebbe il rifiuto
        # come errore, gli appenderebbe "try a different approach" e lo
        # addebiterebbe al ``ToolErrorBudget`` — cioè inviterebbe a insistere.
        if not is_silent_turn(self._metadata.get()):
            return (
                "Not applicable: this is a normal turn and your answer is delivered to "
                "the user. Just write it. This tool exists only for a silent scheduled "
                "run, where nothing you write is delivered."
            )

        number: int | None = None
        if task is not None:
            try:
                number = int(str(task).strip().lstrip("#[").rstrip("].)"))
            except (TypeError, ValueError):
                return (
                    f"Ignored the value {task!r}: `task` must be the plain number of a "
                    "check as listed in this run's prompt. Nothing was delivered to the "
                    "user either way; end the turn."
                )

        count = self._record(number)
        logger.info(
            "NothingToReportTool: silent run declared nothing to report (task={}, {} so far)",
            number,
            count,
        )
        # Nessuna richiesta di "chiudere con una riga". La prima versione la
        # faceva, e il 2026-09-03 alle 11:25 il modello ha chiuso rigurgitando
        # 4.883 caratteri di preambolo, segnaposto dei marcatori compresi (v.
        # ``could_not_check._is_specimen``). Qui il turno non deve scrivere altro:
        # la risposta finale di un turno silenzioso non la legge nessuno.
        return (
            "Recorded. Nothing was delivered to the user, and that is the right outcome. "
            "End the turn now; nothing more is needed."
        )


def declared_marker_lines(tool: Tool | None) -> str:
    """Le righe di marcatore equivalenti alle astensioni dichiarate nel turno.

    Vive qui e non in ``loop.py`` perché la traduzione fra l'azione e il
    marcatore è una proprietà di questo tool: il loop ne chiama una funzione, e
    la regola "core stays small" del progetto resta rispettata.

    Solo le dichiarazioni **numerate**: v. la nota nel docstring del modulo sul
    perché un ``CHECK_OK`` anonimo non si sintetizza.
    """
    if not isinstance(tool, NothingToReportTool):
        return ""
    return "".join(f"\n{OK_MARKER} {n}" for n in tool.declared_tasks())


# Registrazione esplicita: il ToolLoader legge questa lista. Il modulo va anche
# aggiunto a ``_HARDCODED_TOOL_MODULES`` in ``loader.py``, altrimenti non carica
# niente e non lo dice (v. ``tests/agent/tools/test_loader_list_matches_disk.py``).
TOOLS = [NothingToReportTool]
