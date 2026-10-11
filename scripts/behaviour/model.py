"""Cosa si osserva di un giro, e come uno scenario lo giudica.

Niente qui parla col dispositivo: le osservazioni si costruiscono dalle righe
della sessione e dai file letti dopo il giro, cosi' i controlli si provano
offline su osservazioni registrate (``tests/scripts/test_behaviour_checks.py``).
"""

from __future__ import annotations

import json
import random
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

_SYLLABLES = ("ka", "zo", "bri", "nel", "fu", "dra", "mo", "ves", "tal", "qui", "ron", "pe",
              "lis", "gau", "tre", "vo", "zan", "mer")


def make_nonce(rng: random.Random) -> str:
    """Una parola inventata, nuova a ogni giro: nessun giro ritrova quella di un altro.

    Il giro precedente lascia la sua in ``history.jsonl`` (``/new`` archivia la
    conversazione buttata): una parola fissa la farebbe ritrovare al giro dopo.
    """
    return "".join(rng.choice(_SYLLABLES) for _ in range(3)) + str(rng.randint(10, 99))


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any]
    result: str = ""

    def arg_text(self) -> str:
        return json.dumps(self.args, ensure_ascii=False)


@dataclass
class Observation:
    """Un giro di uno scenario, com'e' andato."""

    replies: list[str]
    tool_calls: list[ToolCall]
    files: dict[str, str | None] = field(default_factory=dict)
    timed_out: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def reply(self) -> str:
        return self.replies[-1] if self.replies else ""

    def called(self, name: str) -> list[ToolCall]:
        return [c for c in self.tool_calls if c.name == name]

    def last_turn_called(self, name: str) -> list[ToolCall]:
        """Le chiamate dell'ultimo turno dell'utente soltanto."""
        calls = self.extra.get("last_turn_calls")
        pool = calls if isinstance(calls, list) else self.tool_calls
        return [c for c in pool if c.name == name]


@dataclass
class Check:
    """Una proprieta' di un giro: passa o no, e perche'."""

    name: str
    ok: bool
    detail: str = ""


@dataclass
class Edit:
    """Una modifica dal vivo di un file dell'app, per la taratura."""

    path: str
    old: str
    new: str = ""


@dataclass
class Scenario:
    name: str
    stage: int
    area: str
    #: Cosa misura, in una riga: finisce nel report.
    promise: str
    #: I messaggi dell'utente, in ordine. ``{n}``, ``{m}``... diventano nonce.
    turns: list[str]
    check: Callable[[Observation, Mapping[str, str]], list[Check]]
    #: File di prova da scrivere prima del giro (percorso relativo al workspace).
    files: dict[str, str] = field(default_factory=dict)
    #: Righe da aggiungere in coda a file esistenti (es. ``history.jsonl``).
    append: dict[str, str] = field(default_factory=dict)
    #: ``personal`` (la chat unificata, sessione nuova) o ``notebook`` (un
    #: quaderno usa e getta, creato e cancellato attorno al giro).
    chat: str = "personal"
    #: File da leggere dopo il giro.
    read_after: tuple[str, ...] = ()
    #: Togliere la garanzia: il tasso deve crollare, o la sonda non misura niente.
    calibration: list[Edit] = field(default_factory=list)
    #: ``/new`` dopo i turni e lettura della voce d'archivio (sonda del consolidatore).
    archive_after: bool = False
    #: ``/dream`` dopo i turni.
    dream_after: bool = False
    #: Un giro dell'heartbeat forzato dopo i file di prova (fino a ~6 minuti).
    heartbeat_after: bool = False
    #: ``guard``: una promessa con una taratura che ha fatto crollare il tasso, o
    #: una che vive in Python e si tara con una build (``calibration_note``).
    #: ``probe``: un comportamento misurato senza una frase da togliere — dice
    #: com'e', non se e' cambiato per colpa di un prompt.
    #: Tra un turno e l'altro si aspetta che i subagent finiscano e che il loro
    #: annuncio sia arrivato, invece di fermarli.
    wait_subagents: bool = False
    #: Dopo l'ultimo turno si aspettano i subagent, e la risposta che conta e'
    #: l'ultima dell'assistente in sessione (quella del turno d'annuncio).
    final_reply_after_subagents: bool = False
    kind: str = "guard"
    calibration_note: str = ""
    nonces: tuple[str, ...] = ("n",)

    def render(self, text: str, nonces: Mapping[str, str]) -> str:
        for key, value in nonces.items():
            text = text.replace("{" + key + "}", value)
        return text


def observation_from_rows(rows: list[dict[str, Any]], replies: list[str], *,
                          files: dict[str, str | None] | None = None,
                          timed_out: bool = False) -> Observation:
    """Le chiamate di tool dalle righe di una sessione, con i loro risultati."""
    results = {
        str(r.get("tool_call_id")): str(r.get("content") or "")
        for r in rows if r.get("role") == "tool"
    }
    calls: list[ToolCall] = []
    for row in rows:
        if row.get("role") != "assistant":
            continue
        for tc in row.get("tool_calls") or []:
            fn = tc.get("function") or {}
            raw = fn.get("arguments")
            try:
                args = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
            except (json.JSONDecodeError, TypeError, ValueError):
                args = {"_raw": raw}
            calls.append(ToolCall(str(fn.get("name") or tc.get("name") or ""), args,
                                  results.get(str(tc.get("id")), "")))
    return Observation(replies, calls, files or {}, timed_out)


# -- controlli riusabili ---------------------------------------------------------

_UNKNOWN_TOOL = re.compile(r"^Error: Tool '([^']+)' not found")


def no_unknown_tool(obs: Observation) -> Check:
    """Trasversale: nessuna chiamata a un tool che il turno non ha."""
    bad = [c.name for c in obs.tool_calls if _UNKNOWN_TOOL.match(c.result)]
    return Check("no_unknown_tool", not bad, ", ".join(bad))


def finished(obs: Observation) -> Check:
    return Check("turn_finished", not obs.timed_out, "timed out" if obs.timed_out else "")


_IT_WORDS = {"il", "la", "che", "di", "non", "per", "una", "sono", "con", "del", "della", "è",
             "questo", "anche", "nel", "gli", "le", "ho", "ti", "puoi"}
_EN_WORDS = {"the", "and", "is", "of", "to", "it", "that", "you", "with", "for", "this", "are",
             "not", "your", "can", "have", "in", "on", "was", "about"}


def language_of(text: str) -> str:
    """``it``/``en`` dal conto delle parole funzione: grossolano, ma senza un modello."""
    words = re.findall(r"[a-zàèéìòù']+", text.lower())
    it = sum(w in _IT_WORDS for w in words)
    en = sum(w in _EN_WORDS for w in words)
    if it == en:
        return "?"
    return "it" if it > en else "en"
