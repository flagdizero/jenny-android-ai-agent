"""Runtime-specific helper functions and constants."""

from __future__ import annotations

import re
from typing import Any

from loguru import logger

from jenny.security.workspace_policy import _safe_expanduser
from jenny.session.history_meta import (
    INJECTED_EVENT_META,
    LENGTH_RECOVERY_EVENT,
)
from jenny.utils.helpers import stringify_text_blocks

_MAX_REPEAT_EXTERNAL_LOOKUPS = 2

# Third same-target workspace violation in a turn escalates to "stop retrying".
_MAX_REPEAT_WORKSPACE_VIOLATIONS = 2

EMPTY_FINAL_RESPONSE_MESSAGE = (
    "I completed the tool steps but couldn't produce a final answer. "
    "Please try again or narrow the task."
)

# Distinto da EMPTY_FINAL_RESPONSE_MESSAGE: qui il modello ha prodotto output, ma
# lo ha consumato tutto prima di dire qualcosa di utile (tipicamente reasoning
# che riempie l'intero budget). Dirlo esplicitamente rende il caso
# autodiagnosticabile: il messaggio generico "non ho prodotto una risposta"
# nasconde la causa e manda a caccia del bug sbagliato.
OUTPUT_TRUNCATED_MESSAGE = (
    "I hit the per-response output token limit before producing an answer. "
    "Raise the model's max output tokens, lower its reasoning effort, or ask for "
    "a smaller piece of the task."
)

# Il provider ha rifiutato di rispondere (``content_filter``/``refusal``), anche
# dopo il tentativo senza l'ultimo risultato d'aiutante. Distinto dalla risposta
# vuota per la stessa ragione di OUTPUT_TRUNCATED_MESSAGE: «non ho prodotto una
# risposta» manda a cercare un difetto di Jenny, e qui ha detto no il provider.
PROVIDER_REFUSED_MESSAGE = (
    "The model provider refused to answer this request (content filter), so I "
    "could not reply. Please try again or rephrase the request."
)

# Prende il posto del risultato d'aiutante dopo il quale il provider ha
# rifiutato di rispondere, nella sola copia mandata al modello: la storia tiene
# il risultato com'era (``context_governor.mask_refused_results``).
PROVIDER_REFUSED_RESULT_PLACEHOLDER = (
    "[The provider refused to process this helper result, so it is not shown here. "
    "The files the helper wrote are in the workspace or the notebook: read them if "
    "you need them.]"
)

FINALIZATION_RETRY_PROMPT = (
    "Please provide your response to the user based on the conversation above."
)

BUDGET_EXHAUSTED_FINALIZATION_PROMPT = (
    "The tool-call budget for this turn is exhausted. Based only on the "
    "conversation and tool results above, provide a concise final response to "
    "the user. Do not call or request tools. Do not claim the task is complete "
    "unless the evidence above clearly shows it is complete. State what was "
    "done, what remains, and the best next step if anything is incomplete."
)

# Il prompt di sopra parla «all'utente», e un subagent un utente non ce l'ha:
# risponde all'orchestratore, che deve decidere se farlo continuare. Quello che
# gli serve e cio che il resoconto di un aiutante fermato non portava (prova del
# 24/10/2026: solo gli ultimi tre tool): fatto, file scritti, cosa resta, come
# riprendere. La ripresa e reale — ``subagent_send`` riparte dalla sua storia —
# quindi il prompt lo dice, e il resoconto non deve ricominciare da capo.
SUBAGENT_BUDGET_FINALIZATION_PROMPT = (
    "Your model-call budget for this job is exhausted: no more tools can run. "
    "Write your report to the orchestrator (the main agent that started you), "
    "based only on the conversation and tool results above: what is done, "
    "which files you wrote or changed (their paths), what remains, and how to "
    "continue. The orchestrator can resume you with this conversation intact, "
    "so name the next concrete step. Do not claim the job is complete unless "
    "the evidence above shows it is. Do not call or request tools."
)

# Avviso che un subagent riceve verso la fine del budget
# (``AgentRunSpec.iteration_notice``): una volta, dopo i tool, prima della
# chiamata successiva. ``{left}`` e il numero di chiamate che restano.
SUBAGENT_BUDGET_NOTICE_TEMPLATE = (
    "You have {left} model calls left for this job. Write down what you have "
    "now (save your work to its files), then finish with a report of what is "
    "done, which files you wrote, and what remains."
)

#: Marcatore ``injected_event`` dell'avviso di budget: dice da dove arriva una
#: riga ``user`` che nessuno ha scritto. Vive solo nella conversazione di un
#: subagent, che non finisce mai nella storia di una sessione dell'utente.
BUDGET_NOTICE_EVENT = "budget_notice"

LENGTH_RECOVERY_PROMPT = (
    "Output limit reached. Continue exactly where you left off "
    "— no recap, no apology. Break remaining work into smaller steps if needed."
)

def empty_tool_result_message(tool_name: str) -> str:
    """Short prompt-safe marker for tools that completed without visible output."""
    return f"({tool_name} completed with no output)"


def ensure_nonempty_tool_result(tool_name: str, content: Any) -> Any:
    """Replace semantically empty tool results with a short marker string."""
    if content is None:
        return empty_tool_result_message(tool_name)
    if isinstance(content, str) and not content.strip():
        return empty_tool_result_message(tool_name)
    if isinstance(content, list):
        if not content:
            return empty_tool_result_message(tool_name)
        text_payload = stringify_text_blocks(content)
        if text_payload is not None and not text_payload.strip():
            return empty_tool_result_message(tool_name)
    return content


def is_blank_text(content: str | None) -> bool:
    """True when *content* is missing or only whitespace."""
    return content is None or not content.strip()


def build_finalization_retry_message() -> dict[str, str]:
    """A short no-tools-allowed prompt for final answer recovery.

    Senza marcatore ``injected_event``, a differenza di
    ``build_length_recovery_message``: questo prompt e quello di budget esaurito
    finiscono in una *copia* della lista (``_finalization_retry_messages``,
    ``_budget_exhausted_finalization_messages``) che serve solo alla richiesta e al
    conteggio token, e non arriva mai alla storia di sessione. Marcarli direbbe il
    falso su dove vanno a finire.
    """
    return {"role": "user", "content": FINALIZATION_RETRY_PROMPT}


def build_budget_exhausted_finalization_message(prompt: str | None = None) -> dict[str, str]:
    """Prompt the model for a no-tools final response after budget exhaustion.

    *prompt* sostituisce il testo di default: un subagent riferisce
    all'orchestratore, non all'utente (``SUBAGENT_BUDGET_FINALIZATION_PROMPT``).
    """
    return {"role": "user", "content": prompt or BUDGET_EXHAUSTED_FINALIZATION_PROMPT}


def build_budget_notice_message(text: str) -> dict[str, str]:
    """L'avviso di budget come riga ``user`` marcata (``BUDGET_NOTICE_EVENT``)."""
    return {"role": "user", "content": text, INJECTED_EVENT_META: BUDGET_NOTICE_EVENT}


def build_length_recovery_message() -> dict[str, str]:
    """Prompt the model to continue after hitting output token limit."""
    return {
        "role": "user",
        "content": LENGTH_RECOVERY_PROMPT,
        INJECTED_EVENT_META: LENGTH_RECOVERY_EVENT,
    }


def external_lookup_signature(tool_name: str, arguments: Any) -> str | None:
    """Stable signature for repeated external lookups we want to throttle."""
    if not isinstance(arguments, dict):
        return None
    if tool_name == "web_fetch":
        url = str(arguments.get("url") or "").strip()
        if url:
            return f"web_fetch:{url.lower()}"
    if tool_name == "web_search":
        query = str(arguments.get("query") or arguments.get("search_term") or "").strip()
        if query:
            return f"web_search:{query.lower()}"
    return None


def repeated_external_lookup_error(
    tool_name: str,
    arguments: Any,
    seen_counts: dict[str, int],
) -> str | None:
    """Block repeated external lookups after a small retry budget."""
    signature = external_lookup_signature(tool_name, arguments)
    if signature is None:
        return None
    count = seen_counts.get(signature, 0) + 1
    seen_counts[signature] = count
    if count <= _MAX_REPEAT_EXTERNAL_LOOKUPS:
        return None
    logger.warning(
        "Blocking repeated external lookup {} on attempt {}",
        signature[:160],
        count,
    )
    return (
        "Error: repeated external lookup blocked. "
        "Use the results you already have to answer, or try a meaningfully different source."
    )


# Workspace-boundary violations are soft errors, with per-target throttling.

_OUTSIDE_PATH_PATTERN = re.compile(r"(?:^|[\s|>'\"])((?:/[^\s\"'>;|<]+)|(?:~[^\s\"'>;|<]+))")


def workspace_violation_signature(
    tool_name: str,
    arguments: Any,
) -> str | None:
    """Return a stable cross-tool signature for the outside-workspace target."""
    if not isinstance(arguments, dict):
        return None
    for key in ("path", "file_path", "target", "source", "destination"):
        val = arguments.get(key)
        if isinstance(val, str) and val.strip():
            return _normalize_violation_target(val.strip())

    if tool_name == "python_exec":
        code = str(arguments.get("code") or "").strip()
        if code:
            match = _OUTSIDE_PATH_PATTERN.search(code)
            if match:
                return _normalize_violation_target(match.group(1))
        cwd = str(arguments.get("working_dir") or "").strip()
        if cwd:
            return _normalize_violation_target(cwd)

    return None


def _normalize_violation_target(raw: str) -> str:
    """Normalize *raw* path so that equivalent spellings collide on the same key."""
    try:
        normalized = _safe_expanduser(raw).resolve().as_posix()
    except Exception:
        normalized = raw.replace("\\", "/")
    return f"violation:{normalized}".lower()


def repeated_workspace_violation_error(
    tool_name: str,
    arguments: Any,
    seen_counts: dict[str, int],
) -> str | None:
    """Return an escalated error after repeated bypass attempts."""
    signature = workspace_violation_signature(tool_name, arguments)
    if signature is None:
        return None
    count = seen_counts.get(signature, 0) + 1
    seen_counts[signature] = count
    if count <= _MAX_REPEAT_WORKSPACE_VIOLATIONS:
        return None
    logger.warning(
        "Escalating repeated workspace bypass attempt {} (attempt {})",
        signature[:160],
        count,
    )
    target = signature.split("violation:", 1)[1] if "violation:" in signature else signature
    return (
        "Error: refusing repeated workspace-bypass attempts.\n"
        f"You have tried to access '{target}' (or an equivalent path) "
        f"{count} times in this turn. This is a hard policy boundary -- "
        "switching tools, working_dir overrides, symlinks, "
        "or base64 piping will NOT change the answer. Stop retrying. "
        "If the user genuinely needs this resource, tell them you cannot "
        "access it and ask how they want to proceed (e.g. copy the file "
        "into the workspace, or disable restrict_to_workspace for this run)."
    )
