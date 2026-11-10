"""Tipo di risultato tool strutturato.

Prima, il successo/fallimento di un tool era "stringly-typed": il registry
decideva con ``result.startswith("Error")``, mis-classificando qualsiasi output
legittimo che iniziasse per "Error". ``ToolResult`` rende successo ed errore
strutturalmente distinti.

**Sul filo l'errore ha una forma sola: un testo che comincia per ``Error``.**
È quella che il modello legge e quella che resta nella cronologia, dove un
``ToolResult`` non arriva: chi rilegge una sessione salvata ha solo il testo.
Quindi i tool possono rispondere in due modi — la stringa ``"Error: …"``, o un
``ToolResult.failure`` — ma chi *decide* se un esito è un errore passa da due
funzioni sole, qui: :func:`render_for_model` porta un ``ToolResult`` alla forma
del filo (un fallimento esce sempre col prefisso), e :func:`is_error_text` la
riconosce. Un confronto di prefisso scritto altrove è una seconda regola.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

ERROR_PREFIX = "Error"


def is_error_text(result: Any) -> bool:
    """*result*, com'è sul filo, è un errore di tool.

    Non toglie gli spazi in testa: chi legge un testo che può averne (una riga
    di cronologia) li toglie prima di chiedere.
    """
    return isinstance(result, str) and result.startswith(ERROR_PREFIX)


@dataclass
class ToolError:
    """Errore tool machine-readable."""

    code: str
    message: str


@dataclass
class ToolResult:
    """Esito di un tool: successo (con contenuto) o errore (strutturato).

    ``content`` è ciò che va nello stream verso l'LLM: una stringa o una lista
    di blocchi di contenuto (stesso contratto di ``Tool.execute``).
    """

    ok: bool
    content: Any = ""
    error: ToolError | None = None

    @classmethod
    def success(cls, content: Any) -> "ToolResult":
        return cls(ok=True, content=content)

    @classmethod
    def failure(
        cls,
        message: str,
        *,
        code: str = "error",
        content: Any = None,
    ) -> "ToolResult":
        return cls(
            ok=False,
            content=content if content is not None else message,
            error=ToolError(code=code, message=message),
        )

    def render(self) -> Any:
        """Ritorna il contenuto visto dall'LLM (stringa o blocchi)."""
        return self.content


def render_for_model(result: ToolResult) -> Any:
    """*result* nella forma del filo: il contenuto, o il testo d'errore col prefisso.

    Un fallimento esce sempre come testo che :func:`is_error_text` riconosce,
    anche quando il tool ha scritto un messaggio senza prefisso o dei blocchi:
    senza, l'esito strutturato diceva «errore» e chi guarda il testo no.
    """
    rendered = result.render()
    if result.ok:
        return rendered
    message = rendered if isinstance(rendered, str) else (
        result.error.message if result.error else "tool failed"
    )
    return message if is_error_text(message) else f"{ERROR_PREFIX}: {message}"
