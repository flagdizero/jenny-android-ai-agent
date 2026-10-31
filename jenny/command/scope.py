"""Dove vale un comando, e cosa si risponde quando arriva nel posto sbagliato.

**Una regola, un posto.** Prima di questo modulo la domanda «questo comando ha
senso qui?» aveva tre risposte diverse in tre punti: un cancello nel loop per
``/tidy`` e ``/init``, una frase scritta a mano dentro ``cmd_gardener``, e niente
per ``/dream``, ``/model`` e ``/skill`` — che dentro un progetto
partivano. La tendina del composer filtrava su ``spec.scope``, ma **lato client**:
non esiste autocomplete sullo ``/``, quindi quel filtro nascondeva una voce a chi
guardava il menu e non diceva niente a chi digitava.

La classificazione vera sta in :mod:`jenny.session.keys` (``session_kind``), che e'
il vocabolario delle sessioni; qui si traduce nella domanda dei comandi:

- ``project`` — vale **solo** dentro un progetto. Il soggetto lo prende dalla
  chiave di sessione, e fuori non ce l'ha. Nessuno di questi accetta il nome di
  un progetto come argomento: il lavoro su un progetto si fa da dentro il
  progetto, come per ``journal_append``.
- ``personal`` — vale **fuori** da un progetto. Agisce sulla memoria personale o
  sull'installazione, e dentro un progetto sarebbe la cosa che il confine dei
  prompt evita da sempre: *chi sei viaggia, dove altro lavori no*.
- ``any`` — vale in entrambe: agisce su *questa* conversazione.

Il residuo cade su «disponibile», e non e' distrazione: una chiave che non e' ne'
progetto ne' personale e' interna (cron, Dream, heartbeat), e i comandi che
passano da la' sono lavoro del sistema — rifiutarli trasformerebbe una domanda di
classificazione in un job che non gira.
"""

from __future__ import annotations

from jenny.command.specs import BUILTIN_COMMAND_SPECS, BuiltinCommandSpec
from jenny.runtime.user_text import say
from jenny.session.keys import is_project_session_key

__all__ = [
    "available",
    "refusal",
    "refusal_for_line",
    "spec_for_line",
    "visible_specs",
]


def spec_for_line(raw: str) -> BuiltinCommandSpec | None:
    """La spec della riga *raw*, o ``None`` se non nomina un comando noto.

    Confronta la **prima parola**: gli argomenti non cambiano dove un comando
    vale. Il match e' esatto sulla parola, non un prefisso, altrimenti ``/newx``
    passerebbe per ``/new``.
    """
    word = raw.strip().split(maxsplit=1)[0].lower() if raw.strip() else ""
    if not word:
        return None
    for spec in BUILTIN_COMMAND_SPECS:
        if spec.command == word:
            return spec
    return None


def available(spec: BuiltinCommandSpec, session_key: str) -> bool:
    """Se *spec* ha un soggetto nella sessione *session_key*."""
    in_project = is_project_session_key(session_key)
    if spec.scope == "project":
        return in_project
    if spec.scope == "personal":
        return not in_project
    return True


def visible_specs(session_key: str | None) -> tuple[BuiltinCommandSpec, ...]:
    """I comandi da offrire in *session_key*. ``None`` = tutti.

    ``None`` non e' «nessuno scope»: e' «non lo so», e in quel caso si elenca
    tutto invece di indovinare. Lo usano la documentazione e i test; le due
    superfici vere — ``/help`` e la rotta della tendina — passano una chiave.
    """
    if session_key is None:
        return BUILTIN_COMMAND_SPECS
    return tuple(spec for spec in BUILTIN_COMMAND_SPECS if available(spec, session_key))


def refusal(spec: BuiltinCommandSpec) -> str:
    """«Qui non c'e' niente su cui agire», **e dove invece c'e'**.

    Un rifiuto che dice solo di no costa un altro turno: e' la lezione dei
    rifiuti dei progetti, ed e' la forma che hanno gia' ``journal_append`` fuori
    da un progetto e il tool ``cron`` dentro uno. La riga finale e'
    ``spec.scope_note``, che dice *cosa* fa il comando — l'informazione con cui si
    capisce da soli perche' qui non ha senso.

    Il chip sopra il composer e' nominato apposta: e' il gesto con cui si passa
    da una parte all'altra, e senza di lui il consiglio «mandalo dalla chat
    personale» e' un'istruzione senza un dove.
    """
    if spec.scope == "project":
        body = say("cmd.refusal.project", command=spec.command)
    else:
        body = say("cmd.refusal.personal", command=spec.command)
    if not spec.scope_note:
        return body
    return f"{body}\n\n{say(spec.scope_note_key)}"


def refusal_for_line(raw: str, session_key: str) -> str | None:
    """Il rifiuto per la riga *raw* in *session_key*, o ``None`` se va bene.

    E' la funzione che il router monta come cancello: una firma sola, e la
    decisione tutta qui dentro (v. ``CommandRouter.availability``).
    """
    spec = spec_for_line(raw)
    if spec is None or available(spec, session_key):
        return None
    return refusal(spec)
