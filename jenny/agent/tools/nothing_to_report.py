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
dichiarazioni in un dict per-turno; ``AgentLoop`` le trascrive in una riga di
marcatore accodata al solo ``TurnOutcome.final_text`` — mai a
``ctx.final_content``, quindi la history della sessione resta pulita. Da lì in
poi i parser di ``cron/could_not_check.py`` leggono ciò che leggevano prima.

**L'esito è un argomento obbligatorio** (``checked``), dal 01/11/2026. Prima il
tool diceva solo «niente da dire», e un controllo *non eseguito* doveva
ricordarsi di scrivere a parte una riga ``CHECK_FAILED`` nel testo. Il behaviour
check l'ha misurato su DeepSeek: a un monitor il cui testo diceva «se il file
non c'è, non dirmi niente», col file assente, la riga non è mai arrivata — 0 giri
su 3, tutti archiviati come «ha guardato, niente da dire». La domanda ora arriva
nel momento in cui il modello chiude il turno: ``checked=false`` con il motivo
diventa ``CHECK_FAILED: <motivo>``, ``checked=true`` diventa ``CHECK_OK``.

``CHECK_OK`` nudo, e non è più un rischio. Su un monitor in attesa di verdetto
azzera la sequenza dei guasti e il ricordo di aver avvisato (v.
``cron/monitor_followup.py``): finché il tool voleva dire solo «non parlo», un
``CHECK_OK`` sintetizzato da ogni astensione avrebbe chiuso come sano un
controllo di cui il turno non aveva detto niente. Ora il modello lo **afferma**
(«ho fatto ogni passo»). Il turno che delega non chiama questo tool (scrive
``CHECK_DELEGATED``), e se lo facesse ``bound_runner`` legge la delega prima.
Il numero di controllo che il tool accettava (``task``) serviva all'heartbeat,
che ne aveva tanti in un turno: non c'è più.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from loguru import logger

from jenny.agent.tools.base import Tool, tool_parameters
from jenny.agent.tools.context import ContextAware, RequestContext
from jenny.agent.tools.schema import BooleanSchema, StringSchema, tool_parameters_schema
from jenny.cron.could_not_check import COULD_NOT_CHECK_MARKER, OK_MARKER
from jenny.session.turn_visibility import is_silent_turn

# Sentinella condivisa, mai mutata. Stesso idioma — e stessa trappola — di
# ``MessageTool._turn_flags``: mutarla propagherebbe le dichiarazioni di un turno
# a tutti i turni futuri di ogni context, chat comprese. La guardia in
# :meth:`NothingToReportTool._record` è ciò che lo impedisce, e serve davvero: la
# FSM ha un percorso che salta BUILD — ``(COMMAND, "shortcut") -> DONE`` — e con
# esso ``start_turn()``, pur arrivando poi a ``TurnOutcome.of``.
_NO_TURN_STATE: dict[str, Any] = {}

# Il motivo di un controllo non eseguito finisce in ``last_error`` e nell'officina:
# una riga, non un paragrafo.
_REASON_MAX = 200


@tool_parameters(
    tool_parameters_schema(
        checked=BooleanSchema(
            description=(
                "true: you did every step the check asks for, and nothing is worth telling "
                "the user. false: something stopped you — a missing file, a tool you do not "
                "have, a host you cannot reach, a value you could not compute — even when "
                "the check's own text told you to stay quiet in that case."
            ),
        ),
        reason=StringSchema(
            "Only with checked=false: what stopped you, in one short line. It reaches "
            "nobody; it is what the workshop shows next to the failed run.",
            nullable=True,
        ),
        required=["checked"],
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
            "On a silent scheduled run, end it without telling the user anything, and say "
            "how the check went: checked=true if you did every step and there is nothing "
            "worth telling; checked=false with a reason if anything stopped you. It delivers "
            "nothing and reaches nobody. Use it instead of sending an empty, one-word or "
            "placeholder message. Not for a normal turn: there, just answer."
        )

    def set_context(self, ctx: RequestContext) -> None:
        self._metadata.set(dict(ctx.metadata or {}))

    def start_turn(self) -> None:
        """Azzera le dichiarazioni del turno.

        Installa un contenitore nuovo nel context del turno: le scritture dei
        task figli mutano *questo* oggetto e restano visibili al turno che l'ha
        creato.
        """
        self._turn_state.set({"verdicts": []})

    def verdicts(self) -> list[tuple[bool, str]]:
        """Le dichiarazioni del turno, ``(checked, motivo)``, in ordine."""
        return list(self._turn_state.get().get("verdicts", ()))

    def _record(self, checked: bool, reason: str) -> None:
        state = self._turn_state.get()
        if state is _NO_TURN_STATE:
            # Nessuno ``start_turn()`` per questo context (uso diretto del tool,
            # o un percorso di turno che salta BUILD): la scrittura resta locale
            # invece di mutare la sentinella condivisa da tutte le istanze.
            state = {"verdicts": []}
            self._turn_state.set(state)
        state.setdefault("verdicts", []).append((checked, reason))

    async def execute(self, checked: Any = None, reason: Any = None, **kwargs: Any) -> str:
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
        if not isinstance(checked, bool):
            return (
                "Say how the check went: checked=true if you did every step it asks for, "
                "checked=false with a reason if anything stopped you. Nothing was delivered "
                "to the user either way."
            )
        # Il motivo vale solo per un controllo che non ha girato.
        why = "" if checked else (" ".join(str(reason or "").split())[:_REASON_MAX]
                                  or "no reason given")
        self._record(checked, why)
        logger.info("NothingToReportTool: silent run ended, checked={} {}", checked, why)
        # Nessuna richiesta di "chiudere con una riga". La prima versione la
        # faceva, e il 2026-09-03 alle 11:25 il modello ha chiuso rigurgitando
        # 4.883 caratteri di preambolo, segnaposto dei marcatori compresi (v.
        # ``could_not_check._is_specimen``). Qui il turno non deve scrivere altro:
        # la risposta finale di un turno silenzioso non la legge nessuno.
        if checked:
            return (
                "Recorded. Nothing was delivered to the user, and that is the right outcome. "
                "End the turn now; nothing more is needed."
            )
        return (
            "Recorded as a check that could not run. Nothing was delivered to the user. "
            "End the turn now."
        )


def declared_marker_lines(tool: Tool | None) -> str:
    """La riga di marcatore equivalente all'esito dichiarato nel turno.

    Vive qui e non in ``loop.py`` perché la traduzione fra l'azione e il
    marcatore è una proprietà di questo tool: il loop ne chiama una funzione, e
    la regola "core stays small" del progetto resta rispettata.

    Un turno che ha dichiarato un guasto e poi un «tutto bene» non è sano: vince
    il guasto, perché un guasto perso non lo rivede nessuno, un guasto in più si
    smentisce al giro dopo.
    """
    if not isinstance(tool, NothingToReportTool):
        return ""
    verdicts = tool.verdicts()
    failed = [reason for checked, reason in verdicts if not checked]
    if failed:
        return f"\n{COULD_NOT_CHECK_MARKER}: {failed[0]}"
    if verdicts:
        return f"\n{OK_MARKER}"
    return ""


# Registrazione esplicita: il ToolLoader legge questa lista. Il modulo va anche
# aggiunto a ``_HARDCODED_TOOL_MODULES`` in ``loader.py``, altrimenti non carica
# niente e non lo dice (v. ``tests/agent/tools/test_loader_list_matches_disk.py``).
TOOLS = [NothingToReportTool]
