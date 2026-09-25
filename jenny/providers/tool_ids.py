"""Unicità degli id di tool call, condivisa fra i provider (modulo "leaf").

Casa unica di una regola che vale su ogni wire-format: **due tool call dello
stesso turno non possono condividere un id**. Alcuni provider (Zhipu/GLM in
streaming) riusano lo stesso id per le chiamate parallele, e un id duplicato non
rompe solo la richiesta successiva — corrompe la contabilità interna prima di
arrivare in rete: il transcript scarta l'evento con un ``call_id`` già visto
(``webui/transcript_tool_events.py``) e i risultati grossi collidono sullo stesso
file ``{tool_call_id}.txt`` (``utils/helpers.py::maybe_persist_tool_result``),
dove il secondo si ritrova il payload del primo.

Due fasi, due funzioni:

* :func:`dedupe_tool_ids` — in *parsing*, sulla risposta appena arrivata. È qui
  che si evita il danno a valle.
* :func:`unique_tool_ids_in_history` — in *invio*, sulla history in formato
  interno. Risana le sessioni già avvelenate (senza, l'unica via d'uscita è
  cancellare la conversazione) ed è un no-op idempotente su quelle sane.

Entrambe prendono i generatori di id dal chiamante: la *forma* dell'id è
provider-specifica (``toolu_…`` per Anthropic, 9 caratteri alfanumerici per gli
OpenAI-compat, vincolo Mistral) e non appartiene a questo modulo.

Leaf-level: solo stdlib, nessun import verso i provider.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Sequence
from typing import Any

__all__ = ["dedupe_tool_ids", "unique_tool_ids_in_history"]


def dedupe_tool_ids(
    ids: Sequence[Any],
    *,
    replacement: Callable[[Any, int], str],
) -> list[str]:
    """Rende unici gli id di una singola risposta del modello.

    La prima occorrenza tiene l'id del wire: è l'id che il modello ha appena
    annunciato in streaming e che i consumatori dei delta hanno già visto.
    Rinominare quello, invece del duplicato, li disallineerebbe.

    *replacement* riceve ``(id_grezzo, index)`` e viene chiamata solo per gli id
    vuoti o già visti. L'id che restituisce non viene ri-verificato contro i
    successivi, esattamente come nel percorso OpenAI da cui questa regola viene.
    """
    seen: set[str] = set()
    result: list[str] = []
    for index, raw in enumerate(ids):
        value = raw if isinstance(raw, str) else ""
        if not value or value in seen:
            value = replacement(raw, index)
        seen.add(value)
        result.append(value)
    return result


def unique_tool_ids_in_history(
    messages: Sequence[dict[str, Any]],
    *,
    fresh_id: Callable[[], str],
    derive_id: Callable[[str, int, int], Any],
) -> list[dict[str, Any]]:
    """Disambigua gli id nella history e rimappa i ``tool_call_id`` dei risultati.

    Restituisce copie: i dict originali non vengono mutati.

    L'unicità è imposta **dentro il singolo messaggio assistant**, non su tutta
    la conversazione: è il vincolo che i provider applicano davvero, e allargarlo
    riscriverebbe id già validi.

    I risultati vengono riaccoppiati in FIFO sull'id originale. Regge perché il
    runner accoda i messaggi ``tool`` nello stesso ordine dei ``tool_calls``
    (``agent/runner.py::_run_tool_phase``); se quel fan-out un giorno riordinasse
    gli esiti, l'accoppiamento va rifatto su una chiave esplicita.

    *derive_id* ``(seed, idx, salt) -> str`` produce i sostituti in modo
    **deterministico**: la stessa history deve dare gli stessi id a ogni
    richiesta, altrimenti il prefisso in cache del prompt si invalida a ogni
    retry. *fresh_id* copre il solo caso in cui non esiste un seed (id assente).
    """
    pending: dict[str, deque[str]] = {}
    result: list[dict[str, Any]] = []

    for msg in messages:
        clean = dict(msg)
        calls = clean.get("tool_calls")
        if isinstance(calls, list):
            used: set[str] = set()
            normalized: list[Any] = []
            for idx, tool_call in enumerate(calls):
                if not isinstance(tool_call, dict):
                    normalized.append(tool_call)
                    continue
                tc_clean = dict(tool_call)
                raw_id = tc_clean.get("id")
                mapped_id = _unique_id(
                    raw_id, used, idx, fresh_id=fresh_id, derive_id=derive_id,
                )
                tc_clean["id"] = mapped_id
                used.add(mapped_id)
                if isinstance(raw_id, str) and raw_id:
                    pending.setdefault(raw_id, deque()).append(mapped_id)
                normalized.append(tc_clean)
            clean["tool_calls"] = normalized

        raw_result_id = clean.get("tool_call_id")
        if isinstance(raw_result_id, str) and raw_result_id:
            queue = pending.get(raw_result_id)
            if queue:
                mapped = queue.popleft()
                if not queue:
                    pending.pop(raw_result_id, None)
                clean["tool_call_id"] = mapped
        result.append(clean)

    return result


def _unique_id(
    value: Any,
    used_ids: set[str],
    idx: int,
    *,
    fresh_id: Callable[[], str],
    derive_id: Callable[[str, int, int], Any],
) -> str:
    """Id libero per questa tool call, preferendo quello che ha già."""
    base = value if isinstance(value, str) and value else fresh_id()
    if base not in used_ids:
        return base
    seed = value if isinstance(value, str) and value else base
    salt = 1
    while True:
        candidate = derive_id(seed, idx, salt)
        if isinstance(candidate, str) and candidate not in used_ids:
            return candidate
        salt += 1
