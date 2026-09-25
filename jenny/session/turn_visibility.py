"""Visibilita di un turno: se puo' raggiungere l'utente, e come.

Prima di questo modulo la consegna all'utente era una proprieta della coppia
``(channel, chat_id)`` trasportata sul messaggio: qualunque turno con
``chat_id`` = la chat WebUI parlava all'utente, indipendentemente dal fatto che
appartenesse a lavoro interno. Da qui i tre meccanismi incompatibili incollati
sopra — il gate LLM post-run dell'heartbeat, ``suppress_response`` per i cron
monitor, e *niente* per il turno di annuncio di un subagent, che e' proprio
quello che finiva in chat.

L'invariante e' una sola:

    Un turno **VISIBLE** consegna implicitamente la propria risposta finale.
    Un turno **SILENT** non raggiunge l'utente in alcun modo — ne risposta, ne
    progress, ne reasoning, ne spinner, ne marcatore di fine turno — a meno che
    l'agente non chiami esplicitamente il tool ``message``. Non avere nulla da
    dire e' un esito riuscito, non un fallimento.

La visibilita si risolve **una volta**, al confine del turno, e viene marchiata
nei metadata del messaggio quando e' SILENT: i consumatori a valle che hanno
solo i metadata (tool ``message``, callback di progress, ramo d'errore) leggono
:func:`is_silent_turn`, quelli che hanno anche canale e session key usano
:func:`resolve_turn_visibility`.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping, MutableMapping

from jenny.bus.events import INTERNAL_CHANNEL
from jenny.session.keys import is_internal_session_key

# Unica chiave canonica. Marchiata solo per i turni SILENT: un turno visibile e'
# il caso normale e non deve trascinarsi un flag fino al client (i metadata
# inbound vengono copiati nell'outbound da ``_assemble_outbound``).
TURN_VISIBILITY_META = "_turn_visibility"


async def silent_progress(*_args: Any, **_kwargs: Any) -> None:
    """``on_progress`` muto: un run interno non ha nessuno a cui riferire.

    L'unica copia: Dream, il suo review pass, il giardiniere e i job di sistema
    del cron ne avevano ognuno una propria.
    """


class TurnVisibility(str, Enum):
    """Se un turno puo' raggiungere l'utente da se'."""

    VISIBLE = "visible"
    SILENT = "silent"

    @property
    def silent(self) -> bool:
        return self is TurnVisibility.SILENT


def is_silent_turn(metadata: Mapping[str, Any] | None) -> bool:
    """True se il turno e' stato marchiato SILENT al proprio confine.

    Lettura pura dei metadata, per i consumatori che non hanno (e non devono
    avere) canale e session key nella propria firma.
    """
    return (metadata or {}).get(TURN_VISIBILITY_META) == TurnVisibility.SILENT.value


def resolve_turn_visibility(
    metadata: Mapping[str, Any] | None,
    *,
    channel: str,
    session_key: str,
) -> TurnVisibility:
    """Risolve la visibilita di un turno da marchio esplicito o provenienza.

    Il marchio esplicito vince sempre (lo scrivono i cron monitor e l'heartbeat,
    che sanno di essere lavoro interno). In sua assenza vale la regola
    strutturale: **lavoro interno che gira su un canale utente e' silenzioso**.
    E' cio che rende muto il turno di annuncio di un subagent nato dentro
    l'heartbeat — la sua ``session_key`` e' quella interna d'origine — senza
    bisogno di un caso speciale.

    Un turno sul canale interno resta VISIBLE: non c'e nessun utente da
    raggiungere, e il suo outbound e' il valore di ritorno con cui Dream e il giardiniere
    leggono l'esito del proprio run.
    """
    marked = (metadata or {}).get(TURN_VISIBILITY_META)
    if isinstance(marked, str):
        try:
            return TurnVisibility(marked)
        except ValueError:
            pass
    if channel != INTERNAL_CHANNEL and is_internal_session_key(session_key):
        return TurnVisibility.SILENT
    return TurnVisibility.VISIBLE


def mark_silent_turn(metadata: MutableMapping[str, Any]) -> None:
    """Marchia *metadata* come turno silenzioso (idempotente)."""
    metadata[TURN_VISIBILITY_META] = TurnVisibility.SILENT.value


def silent_turn_metadata(metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Copia di *metadata* marchiata SILENT, per chi costruisce un dict nuovo."""
    out = dict(metadata or {})
    mark_silent_turn(out)
    return out
