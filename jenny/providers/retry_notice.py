"""Le righe d'attesa del retry: il testo inglese e la sua traduzione, in un posto solo.

Il provider le scrive in inglese (vanno anche nei log e non conosce la lingua di
chi legge); arrivano all'utente come riga di progresso nella WebUI, dove un
inglese in mezzo a un'interfaccia italiana stonava. Il dispatcher, che conosce
la lingua dell'agente, le traduce con :func:`localize` prima di consegnarle.
Modelli e traduzioni stanno qui accanto, così una frase cambiata da una parte
non smette in silenzio di essere riconosciuta dall'altra (lo prova un test).
"""

from __future__ import annotations

import re

_WAITING = "Model request failed, {kind} in {seconds}s (attempt {attempt})."
_STOPPED = "Persistent retry stopped after {count} identical errors."
_GAVE_UP = "Model request failed after {attempts} attempts, giving up."

_PATTERNS = (
    ("waiting", re.compile(
        r"Model request failed, (?P<kind>retry|persistent retry) in (?P<seconds>\d+)s "
        r"\(attempt (?P<attempt>\d+)\)\.",
    )),
    ("stopped", re.compile(r"Persistent retry stopped after (?P<count>\d+) identical errors\.")),
    ("gave_up", re.compile(r"Model request failed after (?P<attempts>\d+) attempts, giving up\.")),
)

_TRANSLATIONS: dict[str, dict[str, str]] = {
    "it": {
        "waiting.retry": "Il modello non risponde: riprovo fra {seconds} s (tentativo {attempt}).",
        "waiting.persistent retry": (
            "Il modello non risponde: riprovo ancora fra {seconds} s (tentativo {attempt})."
        ),
        "stopped": "Smetto di riprovare: {count} errori uguali di fila.",
        "gave_up": "Il modello non ha risposto dopo {attempts} tentativi: mi fermo.",
    },
}


def waiting(seconds: int, attempt: int, *, persistent: bool) -> str:
    """«Riprovo fra N secondi»: la riga che scorre durante l'attesa."""
    kind = "persistent retry" if persistent else "retry"
    return _WAITING.format(kind=kind, seconds=seconds, attempt=attempt)


def stopped(count: int) -> str:
    """Il retry a oltranza si ferma dopo *count* errori identici."""
    return _STOPPED.format(count=count)


def gave_up(attempts: int) -> str:
    """Il retry standard ha finito i tentativi."""
    return _GAVE_UP.format(attempts=attempts)


def localize(text: str, language: str | None) -> str:
    """*text* nella lingua *language*, se è una riga d'attesa e la lingua ha una traduzione.

    Qualunque altro testo, o una lingua senza traduzione, torna com'è.
    """
    table = _TRANSLATIONS.get((language or "").strip().lower())
    if not table:
        return text
    for name, pattern in _PATTERNS:
        match = pattern.fullmatch(text.strip())
        if match is None:
            continue
        fields = match.groupdict()
        key = f"{name}.{fields.pop('kind')}" if name == "waiting" else name
        return table[key].format(**fields)
    return text


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
