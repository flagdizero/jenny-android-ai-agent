"""Compattazione a meta' turno: un checkpoint al posto dei primi passi di un compito lungo.

Fra un turno e l'altro la sessione la tiene corta il ``Consolidator``; dentro un
turno, finora, c'erano solo il microcompact, il tetto per risultato e lo snip. Lo
snip pero' non taglia mai dentro il turno in corso: tiene la coda da un messaggio
``user``, e se nella coda non ce n'e' si allarga fino alla richiesta del turno,
cioe' a tutto il turno. Un compito lungo con tool che il microcompact non svuota
(``ssh_exec``, il browser) cresceva quindi fino all'errore del provider, e i due
tentativi di recupero dimezzavano una finestra che lo snip non poteva rispettare:
misurato il 25/10/2026, un turno da 40 passi moriva all'undicesimo.

Qui il runner, quando la richiesta che sta per partire supera
:data:`TRIGGER_RATIO` del budget e la catena del turno ne vale almeno
:data:`MIN_CHAIN_RATIO`, fa scrivere al modello un checkpoint (richiesta,
istruzioni dell'utente, fatto, errori, da fare, passo corrente: le sezioni dei
riassunti di Claude Code, Codex e OpenHands) e d'ora in poi manda al modello la
storia prima della richiesta, la richiesta col checkpoint in coda, e la coda
recente alla lettera. I messaggi che l'utente ha scritto a meta' lavoro entrano
nel checkpoint **alla lettera**, non riassunti: e' li' che un riassunto perde i
vincoli.

La vista e' solo per il modello: i messaggi del run restano interi, e il turno si
salva com'e'. La storia prima della richiesta resta allo snip e al
``Consolidator``, come prima: compattarla qui la riassumerebbe a ogni turno senza
che niente la accorci davvero.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from jenny.session.history_meta import CHECKPOINT_META, COMPACTED_META, INJECTED_EVENT_META
from jenny.utils.helpers import estimate_message_tokens, merge_message_content

__all__ = [
    "InFlightCheckpoint",
    "TRIGGER_RATIO",
    "MIN_CHAIN_RATIO",
    "TAIL_RATIO",
    "MAX_SUMMARY_WORDS",
    "LLM_FAILURES_BEFORE_DETERMINISTIC",
    "apply_checkpoint",
    "chain_start",
    "chain_tokens",
    "collect_user_messages",
    "deterministic_summary",
    "find_request_index",
    "format_range",
    "mark_compacted",
    "plan_cut",
]

# Le soglie stanno fra quelle di chi lo fa gia': Claude Code compatta a ~83% della
# finestra, Codex al 90%, Gemini CLI al 50% tenendo il 30% piu' recente. Qui il
# budget e' gia' al netto dell'output riservato, quindi 0,8 del budget e' meno di
# 0,8 della finestra.
TRIGGER_RATIO = 0.80
# Sotto questa quota la catena del turno non libera abbastanza da valere una
# chiamata e una cache persa (il ``clear_at_least`` di Anthropic): il resto lo fa
# lo snip, sulla storia prima della richiesta.
MIN_CHAIN_RATIO = 0.20
# La coda tenuta alla lettera, al massimo: 30% del budget e mai piu' di meta'
# della catena, altrimenti la compattazione non libera niente.
TAIL_RATIO = 0.30
MAX_SUMMARY_WORDS = 800
# Il riassunto puo' non arrivare (provider giu', il modello chiama un tool invece
# di scrivere): dopo questi fallimenti nello stesso run si passa al checkpoint
# senza modello, che non fallisce e tiene comunque la richiesta.
LLM_FAILURES_BEFORE_DETERMINISTIC = 2
SUMMARY_MAX_CHARS = 12_000
VERBATIM_USER_MAX_CHARS = 8_000
VERBATIM_USER_EACH_MAX_CHARS = 2_000

_CHECKPOINT_HEADER = (
    "[Context checkpoint — not a message from the user. The earlier steps of this task "
    "were replaced by the summary below to save space ({count}). Their tool results are "
    "no longer in context: if you need one again, re-read the file or repeat the lookup "
    "rather than guessing.]"
)
_DETERMINISTIC_NOTE = (
    "(No summary could be written, so this is the list of the calls made in the "
    "compacted steps, with the start of each result.)"
)


@dataclass(slots=True)
class InFlightCheckpoint:
    """Lo stato della compattazione di un run.

    ``anchor`` e' l'indice della richiesta del turno nei messaggi del run, ``cut``
    il primo messaggio tenuto alla lettera dopo di lei: quel che sta in mezzo e'
    nel ``summary``. Gli indici bastano perche' i messaggi del run crescono solo
    in coda (un'iniezione puo' fondersi nell'ultimo, mai in uno di prima).
    """

    anchor: int
    cut: int
    summary: str
    user_messages: list[str] = field(default_factory=list)
    compactions: int = 1
    deterministic: bool = False

    def render(self) -> str:
        count = "once" if self.compactions == 1 else f"{self.compactions} times"
        parts = [_CHECKPOINT_HEADER.format(count=count), self.summary.strip()]
        if self.user_messages:
            quoted = "\n".join(f"- {text}" for text in self.user_messages)
            parts.append(
                "Messages the user sent while you worked on this task, verbatim "
                f"(oldest first):\n{quoted}"
            )
        return "\n\n".join(parts)


def find_request_index(initial_messages: list[dict[str, Any]]) -> int | None:
    """L'indice della richiesta del turno: l'ultimo ``user`` dei messaggi iniziali."""
    for idx in range(len(initial_messages) - 1, -1, -1):
        if initial_messages[idx].get("role") == "user":
            return idx
    return None


def chain_start(anchor: int, checkpoint: InFlightCheckpoint | None) -> int:
    """Il primo messaggio della catena ancora alla lettera."""
    return checkpoint.cut if checkpoint is not None else anchor + 1


def apply_checkpoint(
    messages: list[dict[str, Any]],
    checkpoint: InFlightCheckpoint | None,
) -> list[dict[str, Any]]:
    """La vista per il modello: richiesta col checkpoint in coda, poi la coda."""
    if checkpoint is None:
        return messages
    anchor, cut = checkpoint.anchor, checkpoint.cut
    if not (0 <= anchor < cut <= len(messages)) or messages[anchor].get("role") != "user":
        return messages
    request = dict(messages[anchor])
    request["content"] = merge_message_content(request.get("content"), checkpoint.render())
    return [*messages[:anchor], request, *messages[cut:]]


def mark_compacted(messages: list[dict[str, Any]], checkpoint: InFlightCheckpoint) -> None:
    """Segna sulle righe del run quel che il checkpoint ha sostituito.

    Le righe si salvano come sono (``_save_turn`` le copia con le loro chiavi), e
    ``Session.get_history`` ne rifa' la vista: le compattate saltano, e la prima
    tenuta porta il checkpoint, che torna in coda alla richiesta. Scrive sui dict
    del run, che sono del run: la richiesta no, perche' e' gia' salvata a parte
    (``_persist_user_message_early``) e qui sarebbe una copia.
    """
    for message in messages[checkpoint.anchor + 1: checkpoint.cut]:
        message[COMPACTED_META] = True
        message.pop(CHECKPOINT_META, None)
    messages[checkpoint.cut][CHECKPOINT_META] = checkpoint.render()


def _tool_capped_tokens(message: dict[str, Any], max_tool_chars: int) -> int:
    if message.get("role") == "tool" and max_tool_chars > 0:
        content = message.get("content")
        if isinstance(content, str) and len(content) > max_tool_chars:
            message = {**message, "content": content[:max_tool_chars]}
    return estimate_message_tokens(message)


def chain_tokens(
    messages: list[dict[str, Any]],
    start: int,
    *,
    scale: float = 1.0,
    max_tool_chars: int = 0,
) -> int:
    """Quanto pesa la catena da *start* in poi, coi risultati al loro tetto."""
    return int(sum(_tool_capped_tokens(m, max_tool_chars) for m in messages[start:]) * scale)


def plan_cut(
    messages: list[dict[str, Any]],
    start: int,
    tail_budget: int,
    *,
    scale: float = 1.0,
    max_tool_chars: int = 0,
) -> int | None:
    """Il primo messaggio da tenere alla lettera, o ``None`` se non c'e' niente da compattare.

    Il taglio cade sempre su un ``assistant``: la coda comincia con una risposta
    del modello (e i suoi tool result dietro), cosi' dopo la richiesta ``user`` col
    checkpoint i ruoli si alternano e nessun risultato resta orfano. Si tiene la
    coda piu' lunga che sta in *tail_budget*, e almeno l'ultimo giro anche se da
    solo lo sfora.
    """
    candidates = [
        i for i in range(start, len(messages)) if messages[i].get("role") == "assistant"
    ]
    if not candidates:
        return None
    suffix = [0] * (len(messages) + 1)
    for i in range(len(messages) - 1, start - 1, -1):
        suffix[i] = suffix[i + 1] + int(_tool_capped_tokens(messages[i], max_tool_chars) * scale)
    cut = candidates[-1]
    for i in reversed(candidates):
        if suffix[i] > tail_budget:
            break
        cut = i
    return cut if cut > start else None


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


def collect_user_messages(
    previous: list[str],
    messages: list[dict[str, Any]],
) -> list[str]:
    """I messaggi scritti dall'utente in *messages*, in coda a *previous*.

    Fuori le righe sintetiche (sprone del goal, avviso di budget, ripresa dopo un
    troncamento, rientro di un subagent): non sono parole dell'utente, e le prime
    tre ripetute alla lettera sembrerebbero richieste nuove. Si tengono i piu'
    recenti che stanno in :data:`VERBATIM_USER_MAX_CHARS`.
    """
    collected = list(previous)
    for message in messages:
        if message.get("role") != "user":
            continue
        if message.get(INJECTED_EVENT_META):
            continue
        text = " ".join(_text_of(message.get("content")).split())
        if not text:
            continue
        if len(text) > VERBATIM_USER_EACH_MAX_CHARS:
            text = text[:VERBATIM_USER_EACH_MAX_CHARS] + " […]"
        collected.append(text)
    kept: list[str] = []
    total = 0
    for text in reversed(collected):
        if kept and total + len(text) > VERBATIM_USER_MAX_CHARS:
            break
        kept.append(text)
        total += len(text)
    kept.reverse()
    return kept


def _call_line(call: dict[str, Any], max_args: int) -> str:
    function = call.get("function") if isinstance(call, dict) else None
    if not isinstance(function, dict):
        return "?"
    name = function.get("name") or "?"
    args = function.get("arguments")
    if not isinstance(args, str):
        args = json.dumps(args, ensure_ascii=False, default=str)
    args = " ".join(args.split())
    if len(args) > max_args:
        args = args[:max_args] + "…"
    return f"{name}({args})"


def format_range(
    messages: list[dict[str, Any]],
    *,
    request: dict[str, Any] | None = None,
    previous_summary: str | None,
    max_chars: int,
    min_result_chars: int = 1_500,
) -> str:
    """I passi da compattare come trascritto, per il riassunto senza la cache.

    I risultati si dividono quel che *max_chars* lascia, e si tagliano solo se non
    ci stanno interi: un tetto fisso per risultato (era 1.500 caratteri) toglieva
    proprio il dato a meta' di un file, che e' quel che il checkpoint deve tenere —
    visto su jenny_s22 il 26/10/2026, con i codici al carattere 5.000 di file da
    10.000. Sotto *min_result_chars* a testa non si scende: allora cadono i passi
    piu' vecchi (come fa Codex quando la sua compattazione sfora), e la richiesta e
    i messaggi dell'utente sono gia' fuori di qui, alla lettera.
    """
    results = [
        _text_of(m.get("content")).strip() for m in messages if m.get("role") == "tool"
    ]
    other = sum(
        len(_text_of(m.get("content"))) + 200 for m in messages if m.get("role") != "tool"
    )
    room = max_chars - len(previous_summary or "") - other
    result_chars = max(min_result_chars, room // len(results)) if results else 0
    blocks: list[str] = []
    for message in messages:
        role = message.get("role")
        if role == "assistant":
            text = _text_of(message.get("content")).strip()
            calls = [_call_line(c, 400) for c in message.get("tool_calls") or []]
            line = "ASSISTANT:"
            if text:
                line += f" {text}"
            if calls:
                line += "\n  calls: " + "; ".join(calls)
            blocks.append(line)
        elif role == "tool":
            text = _text_of(message.get("content")).strip()
            if len(text) > result_chars:
                text = text[:result_chars] + " […]"
            blocks.append(f"RESULT ({message.get('name') or 'tool'}): {text}")
        elif role == "user":
            blocks.append(f"USER: {_text_of(message.get('content')).strip()}")
    kept: list[str] = []
    total = len(previous_summary or "")
    for block in reversed(blocks):
        if kept and total + len(block) > max_chars:
            kept.append("[older steps left out]")
            break
        kept.append(block)
        total += len(block)
    kept.reverse()
    head = ""
    if request is not None:
        head += f"USER REQUEST (verbatim):\n{_text_of(request.get('content')).strip()}\n\n"
    if previous_summary:
        head += f"EARLIER CHECKPOINT:\n{previous_summary.strip()}\n\n"
    return head + "\n\n".join(kept)


def deterministic_summary(
    messages: list[dict[str, Any]],
    *,
    previous_summary: str | None,
    max_chars: int = 6_000,
) -> str:
    """Il checkpoint senza modello: le chiamate fatte e l'inizio dei loro risultati.

    E' il mascheramento delle osservazioni della «Complexity Trap» (2025) applicato
    ai passi compattati: si sa cosa e' stato fatto e con quali argomenti, e un
    risultato che serve ancora si rilegge.
    """
    results = {
        m.get("tool_call_id"): " ".join(_text_of(m.get("content")).split())[:160]
        for m in messages
        if m.get("role") == "tool"
    }
    lines: list[str] = []
    for message in messages:
        if message.get("role") != "assistant":
            continue
        text = " ".join(_text_of(message.get("content")).split())
        if text:
            lines.append(f"- note: {text[:200]}")
        for call in message.get("tool_calls") or []:
            result = results.get(call.get("id") if isinstance(call, dict) else None, "")
            lines.append(f"- {_call_line(call, 160)} → {result}")
    kept: list[str] = []
    total = 0
    for line in reversed(lines):
        if total + len(line) > max_chars:
            kept.append("- [older calls left out]")
            break
        kept.append(line)
        total += len(line)
    kept.reverse()
    parts = []
    if previous_summary:
        parts.append(previous_summary.strip())
    parts.append(f"## Done so far\n{_DETERMINISTIC_NOTE}\n" + "\n".join(kept))
    return "\n\n".join(parts)
