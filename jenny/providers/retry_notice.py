"""Le righe d'attesa del retry: scritte in inglese, riconosciute, tradotte.

Il provider le scrive in inglese (vanno anche nei log e non conosce la lingua di
chi legge); arrivano all'utente come riga di progresso nella WebUI, dove un
inglese in mezzo a un'interfaccia italiana stonava. Il dispatcher, che conosce
la lingua dell'agente, le traduce con :func:`localize` prima di consegnarle.
Le frasi, nelle due lingue, sono le chiavi ``retry.*`` di ``runtime/user_text``;
qui restano i modelli che riconoscono l'inglese, e un test prova che una frase
cambiata non smetta in silenzio di essere riconosciuta.
"""

from __future__ import annotations

import re

from jenny.runtime.user_text import say

_PATTERNS = (
    ("waiting", re.compile(
        r"Model request failed, (?P<kind>retry|persistent retry) in (?P<seconds>\d+)s "
        r"\(attempt (?P<attempt>\d+)\)\.",
    )),
    ("stopped", re.compile(r"Persistent retry stopped after (?P<count>\d+) identical errors\.")),
    ("gave_up", re.compile(r"Model request failed after (?P<attempts>\d+) attempts, giving up\.")),
)

def waiting(seconds: int, attempt: int, *, persistent: bool) -> str:
    """«Riprovo fra N secondi»: la riga che scorre durante l'attesa."""
    key = "retry.waiting_persistent" if persistent else "retry.waiting"
    return say(key, "en", seconds=seconds, attempt=attempt)


def stopped(count: int) -> str:
    """Il retry a oltranza si ferma dopo *count* errori identici."""
    return say("retry.stopped", "en", count=count)


def gave_up(attempts: int) -> str:
    """Il retry standard ha finito i tentativi."""
    return say("retry.gave_up", "en", attempts=attempts)


def localize(text: str, language: str | None) -> str:
    """*text* nella lingua *language*, se è una riga d'attesa.

    Qualunque altro testo torna com'è; una lingua senza traduzione dà l'inglese,
    cioè il testo stesso.
    """
    parsed = parse(text)
    if parsed is None:
        return text
    name, fields = parsed
    if name == "waiting":
        name = "waiting_persistent" if fields.pop("kind") == "persistent retry" else "waiting"
    return say(f"retry.{name}", (language or "").strip().lower() or "en", **fields)


def parse(text: str) -> tuple[str, dict[str, str]] | None:
    """Quale riga d'attesa e' *text* (``waiting``/``stopped``/``gave_up``) e i suoi campi.

    ``None`` per qualunque altro testo. Serve a chi la riga non la mostra ma la
    legge: la bolla di lavoro della home (``jenny/runtime/work_status.py``) ne
    tiene solo il tentativo. Gli stessi modelli di :func:`localize`, cosi' una
    frase cambiata non smette di essere riconosciuta da uno dei due.
    """
    for name, pattern in _PATTERNS:
        match = pattern.fullmatch(text.strip())
        if match is not None:
            return name, match.groupdict()
    return None
