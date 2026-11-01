"""La sonda dei piani a lista: «in questa sessione gira un piano?».

Il piano non vive nei metadati della sessione ma in ``plan.json``, fuori dal
workspace: una sua copia qui sarebbe la seconda verità che diverge. Il driver dei
piani si registra (``GatewayContainer``) e risponde dal suo indice in memoria;
senza driver (i test, l'agente non ancora nato) nessun piano è vivo. Stesso schema
di ``board/notify.py``: un gancio, non un import del driver.

Chi chiede: il contesto del turno, gli aiutanti (gli slot di un piano) e il tetto
della chiamata al modello (:func:`plan_wall_llm_timeout_s`).
"""

from __future__ import annotations

from typing import Protocol


class PlanProbe(Protocol):
    """Quel che questo modulo chiede ai piani a lista (``jenny.plan.driver``)."""

    def plan_running(self, session_key: str) -> bool: ...

    def plan_open(self, session_key: str) -> bool: ...


_plan_probe: PlanProbe | None = None


def set_plan_probe(probe: PlanProbe | None) -> None:
    global _plan_probe
    _plan_probe = probe


def plan_running_in_session(session_key: str | None) -> bool:
    """True se la sessione ha un piano a lista che sta girando (``running``/``pausing``)."""
    if not session_key or _plan_probe is None:
        return False
    try:
        return bool(_plan_probe.plan_running(session_key))
    except Exception:  # noqa: BLE001 — un indice rotto non deve fermare un turno
        return False


def plan_open_in_session(session_key: str | None) -> bool:
    """True se la sessione ha un piano a lista aperto (proposto, in corso, fermo)."""
    if not session_key or _plan_probe is None:
        return False
    try:
        return bool(_plan_probe.plan_open(session_key))
    except Exception:  # noqa: BLE001
        return False


def plan_wall_llm_timeout_s(session_key: str | None) -> float | None:
    """Il tetto della chiamata al modello per un turno della sessione *session_key*.

    ``0.0`` spegne l'``asyncio.wait_for`` attorno alla richiesta quando nella
    sessione gira un piano: un passo può durare a lungo, e il tetto lo romperebbe
    a metà. ``None`` vuol dire «usa ``JENNY_LLM_TIMEOUT_S``». Vale per il turno e
    per gli aiutanti lanciati da quella sessione.
    """
    return 0.0 if plan_running_in_session(session_key) else None
