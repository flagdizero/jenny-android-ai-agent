"""La tabella dei comandi: quali esistono, come si dicono, e **dove** valgono.

Sta in un modulo suo e non in ``builtin.py`` perche' ha tre consumatori che non
sono gli handler — ``/help``, la rotta ``/api/webui/commands`` che alimenta la
tendina del composer, e :mod:`jenny.command.scope`, che decide la disponibilita'
— e perche' il modulo degli handler e' il posto in cui la tabella si perde.

Chi decide *quali* comandi esistono e' questa tabella; chi decide *come si
dicono* nell'interfaccia sono i file i18n (``assets/i18n/{it,en}.json``), che la
tendina consulta con l'inglese di qui come ripiego.
"""

from __future__ import annotations

from dataclasses import dataclass

# Le tre risposte possibili alla domanda "dove vale questo comando". Insieme
# chiuso: un valore fuori elenco nasconderebbe il comando in ogni scope, o lo
# mostrerebbe in tutti (v. ``tests/webui/test_command_specs.py``).
SCOPES: tuple[str, ...] = ("any", "personal", "project")


@dataclass(frozen=True)
class BuiltinCommandSpec:
    """Una voce del menu comandi, piu' la riga di ``/help``.

    ``icon`` e' un nome **Tabler** senza il prefisso ``ti-``: e' il font che la
    WebUI impacchetta, e la tendina lo rende come ``ti-<icon>``. Non e' un
    dettaglio da indovinare — fino alla 0.9.0 cinque di questi nomi erano di
    Lucide (``square-pen``, ``sprout``, ``brush-cleaning``, ``file-pen``,
    ``circle-help``), e nessuno se n'era accorto perche' il campo non era mai
    stato disegnato da nessuno. Un nome che in Tabler non c'e' rende un quadrato
    vuoto, quindi ``tests/webui/test_command_specs.py`` li confronta con il font
    vero: aggiungendo un comando, prendere il nome da la'.

    ``scope`` dice **dove** il comando fa qualcosa, ed e' una delle tre parole di
    :data:`SCOPES`. Il criterio e' il **soggetto**:

    - ``project`` — agisce su *questo* progetto, che prende dalla chiave di
      sessione (``/gardener``, ``/tidy``, ``/init``);
    - ``personal`` — agisce sulla memoria personale o sull'installazione
      (``/dream``, ``/model``, ``/skill``);
    - ``any`` — agisce su *questa conversazione*, qualunque sia (``/new``,
      ``/stop``, ``/status``, ``/history``, ``/goal``, ``/help``).

    Non e' solo cosmetica della tendina: :mod:`jenny.command.scope` lo applica
    **nel dispatch**, perche' non esiste autocomplete sullo ``/`` e un filtro che
    vive solo nel client e' un consiglio, non una regola.

    ``scope_note`` e' la riga che il rifiuto aggiunge quando il comando arriva
    nel posto sbagliato: dice *cosa* fa quel comando, che e' l'informazione con
    cui si capisce perche' qui non ha senso. Sta accanto al comando e non nella
    funzione che compone il rifiuto, cosi' la forma e' una sola e il contenuto e'
    di chi lo conosce.
    """

    command: str
    title: str
    description: str
    icon: str
    arg_hint: str = ""
    scope: str = "any"
    scope_note: str = ""

    @property
    def help_key(self) -> str:
        """La chiave di ``user_text`` della riga di ``/help``.

        L'inglese di quella chiave e' ``description``, parola per parola: lo fissa
        un test, perche' la tendina mostra questo campo come ripiego e ``/help`` la
        chiave, e due testi inglesi diversi per lo stesso comando sarebbero un
        difetto.
        """
        return f"cmd.help.{self.command.lstrip('/')}"

    @property
    def scope_note_key(self) -> str:
        """La chiave di ``user_text`` di ``scope_note`` (stessa regola di :attr:`help_key`)."""
        return f"cmd.scope_note.{self.command.lstrip('/')}"

    def as_dict(self) -> dict[str, str]:
        return {
            "command": self.command,
            "title": self.title,
            "description": self.description,
            "icon": self.icon,
            "arg_hint": self.arg_hint,
            "scope": self.scope,
        }


BUILTIN_COMMAND_SPECS: tuple[BuiltinCommandSpec, ...] = (
    BuiltinCommandSpec(
        "/new",
        "New chat",
        "Stop the current task and start a fresh conversation.",
        "message-plus",
    ),
    BuiltinCommandSpec(
        "/stop",
        "Stop current task",
        (
            "Cancel the active agent turn for this chat. Mini-mes working in the "
            "background keep going: stop each from its own bubble, or send /stop all "
            "to stop everything."
        ),
        "square",
    ),
    BuiltinCommandSpec(
        "/status",
        "Show status",
        "Display runtime, provider, and channel status.",
        "activity",
    ),
    BuiltinCommandSpec(
        "/model",
        "Switch model preset",
        "Show or switch the active model preset.",
        "brain",
        "[preset]",
        scope="personal",
        scope_note=(
            "It switches the preset for the whole installation, not for one project."
        ),
    ),
    BuiltinCommandSpec(
        "/history",
        "Show conversation history",
        "Print the last N persisted conversation messages.",
        "history",
        "[n]",
    ),
    BuiltinCommandSpec(
        "/goal",
        "Start long-running goal",
        "Tell the agent to treat the request as a long-running goal.",
        "activity",
        "<goal>",
    ),
    BuiltinCommandSpec(
        "/dream",
        "Run Dream",
        (
            "Manually trigger memory consolidation now. The budgets and the review cadence "
            "live in Settings → Workshop → Memory."
        ),
        "sparkles",
        scope="personal",
        scope_note=(
            "It consolidates the personal memory. A project conversation feeds it only with "
            "what it says about you, and only into USER.md: the rest stays in that "
            "project's pages."
        ),
    ),
    BuiltinCommandSpec(
        "/gardener",
        "Run the gardener",
        (
            "Inside a project: turn its new journal lines into pages and update its map — or, "
            "with nothing new to promote, bring an oversized map back under its ceiling. The "
            "periodic pass is set in Settings → Workshop → Memory."
        ),
        "seeding",
        scope="project",
        scope_note=(
            "The periodic pass is set in Settings → Workshop → Memory."
        ),
    ),
    BuiltinCommandSpec(
        "/skill",
        "List skills",
        "List enabled skills and their descriptions.",
        "puzzle",
        scope="personal",
        scope_note="It lists what the installation has, not what this project holds.",
    ),
    BuiltinCommandSpec(
        "/tidy",
        "Tidy this project's wiki",
        (
            "Inside a project: restructure its wiki now — split what has outgrown the per-turn "
            "budget, move prose out of an oversized map, realign the page list. Runs in this "
            "conversation, with its pages and your answers in hand; the gardener's periodic pass "
            "cannot do that."
        ),
        "wand",
        scope="project",
    ),
    BuiltinCommandSpec(
        "/init",
        "Write this project's instructions",
        "Inside a project: read the wiki and write its AGENTS.md.",
        "file-pencil",
        scope="project",
    ),
    BuiltinCommandSpec(
        "/help",
        "Show help",
        "List available slash commands.",
        "help-circle",
    ),
)
