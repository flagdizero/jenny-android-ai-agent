"""Classificazione dell'esito di una risposta del modello.

Il loop dell'``AgentRunner`` aveva tre recovery indipendenti, ognuno con la
propria condizione ad hoc (``blank``, ``finish_reason == "length" and not
blank``, ``error`` + marker di contesto). Lo spazio
``(finish_reason x content x tool_calls x usage)`` non era partizionato, quindi
alcune combinazioni cadevano in un ramo sbagliato in silenzio — in particolare
il troncamento che avviene *dentro* il thinking, che produce
``finish_reason == "length"`` con contenuto vuoto e finiva nel ramo
"risposta vuota", dove il recovery è un retry identico che non può riuscire.

Qui l'esito diventa un valore nominale: il dispatch nel runner è uno switch su
questo enum, e una combinazione non gestita è un caso visibile invece di un
fallthrough. Funzioni pure: nessun I/O, nessuno stato: testabili sull'intera
matrice a costo zero.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

from jenny.agent.context_governor import is_context_length_error
from jenny.utils.runtime import is_blank_text

if TYPE_CHECKING:
    from jenny.providers.base import LLMResponse


# Scarto tollerato sotto il tetto per considerare il budget esaurito. Non è
# prudenza teorica: su un tetto di 8192 il gateway ha riportato ``completion=8191``
# su un troncamento reale (e 8192 su un altro), quindi un confronto esatto rende
# il signal sull'usage muto per un token. La banda è troppo stretta per creare
# falsi positivi su una risposta che si è fermata da sé, e il costo di un falso
# positivo è un tentativo in più, non un errore.
_NEAR_CAP_SLACK = 16

#: I ``finish_reason`` con cui il provider dice «non rispondo»: ``content_filter``
#: dagli endpoint OpenAI-compatibili, ``refusal`` da Anthropic (``stop_map`` di
#: ``anthropic_provider.py``).
REFUSAL_FINISH_REASONS = frozenset({"content_filter", "refusal"})


class ResponseOutcome(str, Enum):
    """Esito di una risposta del modello, dopo la fase tool."""

    OK = "ok"
    """Contenuto utile: risposta finale."""

    TRUNCATED_WITH_TEXT = "truncated_with_text"
    """Budget di output esaurito con testo parziale: continuabile."""

    TRUNCATED_BLANK = "truncated_blank"
    """Budget di output esaurito senza testo utile.

    Tipicamente un reasoning model che consuma l'intero budget in
    ``reasoning_content``, oppure un troncamento a metà dei tool call (che
    ``should_execute_tools`` scarta giustamente, lasciando la risposta vuota).
    Non c'è niente da continuare: va ritentato con un budget diverso.
    """

    EMPTY = "empty"
    """Risposta genuinamente vuota, senza troncamento: il retry è legittimo."""

    REFUSED = "refused"
    """Il provider ha rifiutato di rispondere (``content_filter``/``refusal``), a vuoto.

    Non è una risposta vuota: dipende dal contesto, e una richiesta identica
    riceve lo stesso rifiuto (misurato sul telefono: DeepSeek, 0 token di
    completion in meno di un secondo, due volte di fila). Il recovery è un solo
    tentativo col contesto cambiato, non i retry ciechi di ``EMPTY``.
    """

    CONTEXT_OVERFLOW = "context_overflow"
    """Errore di context length: la finestra va ridotta."""

    ERROR = "error"
    """Altro errore del provider."""


def reported_completion_tokens(response: "LLMResponse") -> int | None:
    """Token di completion *riportati dal provider*, o ``None`` se assenti.

    Deliberatamente legge ``response.usage`` grezzo e non il dizionario di
    ``usage_or_estimate``: quello, quando il provider non riporta nulla, stima
    l'usage dal contenuto della risposta. Su una risposta vuota la stima è ~0,
    quindi userebbe il *sintomo* per misurare la *causa* e il confronto con il
    tetto non scatterebbe mai. Tutti i provider normalizzano su
    ``completion_tokens`` (anche Anthropic, da ``output_tokens``).
    """
    usage = response.usage
    if not usage:
        return None
    raw = usage.get("completion_tokens")
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def output_budget_exhausted(
    response: "LLMResponse", max_tokens: int | None
) -> bool:
    """True se la risposta ha consumato tutto il budget di output.

    Due signal indipendenti, perché il primo da solo non è affidabile: non tutti
    i gateway OpenAI-compatibili riportano ``finish_reason == "length"`` sul
    troncamento, e Jenny non lo logga da nessuna parte, quindi non è verificabile
    a posteriori quale dei due sia arrivato. Il confronto con l'usage riportato
    copre il caso in cui il provider dichiari ``stop`` pur essendo andato al
    tetto.

    Il confronto tollera uno scarto minimo sotto il tetto (``_NEAR_CAP_SLACK``):
    i provider non contano tutti allo stesso modo l'ultimo token, e un confronto
    esatto perde i troncamenti riportati a ``tetto - 1``. Su tetti molto piccoli
    la tolleranza si annulla, così non finisce per coprire una frazione
    significativa dell'intervallo.
    """
    if response.finish_reason == "length":
        return True
    if not max_tokens or max_tokens <= 0:
        return False
    reported = reported_completion_tokens(response)
    if reported is None:
        return False
    slack = min(_NEAR_CAP_SLACK, max_tokens // 64)
    return reported >= max_tokens - slack


def classify_response(
    response: "LLMResponse",
    clean_content: str | None,
    *,
    max_tokens: int | None,
) -> ResponseOutcome:
    """Classifica una risposta già ripulita dal reasoning.

    Da chiamare *dopo* la fase tool: quando ``should_execute_tools`` è vero il
    runner ha già preso il suo ramo, quindi qui i tool call residui sono solo
    quelli che il provider ha emesso sotto un ``finish_reason`` che non li rende
    eseguibili (e che il runner logga come ignorati).
    """
    if response.finish_reason == "error":
        return (
            ResponseOutcome.CONTEXT_OVERFLOW
            if is_context_length_error(response)
            else ResponseOutcome.ERROR
        )
    blank = is_blank_text(clean_content)
    if blank and response.finish_reason in REFUSAL_FINISH_REASONS:
        # Prima del troncamento: un rifiuto non consuma il budget, e un usage
        # strano non deve mandarlo nel recovery che alza i token.
        return ResponseOutcome.REFUSED
    if output_budget_exhausted(response, max_tokens):
        return (
            ResponseOutcome.TRUNCATED_BLANK if blank
            else ResponseOutcome.TRUNCATED_WITH_TEXT
        )
    if blank:
        return ResponseOutcome.EMPTY
    return ResponseOutcome.OK
