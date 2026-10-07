"""Quanto sbaglia la stima dei token, misurato sui numeri veri del provider.

La stima a caratteri (``helpers.estimate_prompt_tokens``) conta quattro
caratteri per token. Sul Titan, con ``deepseek-v4.1-flash``, il rapporto vero
e' 3,7: la stima resta indietro dell'8%, e con un italiano pieno di JSON e
codice puo' sbagliare di piu'. Il provider pero' ci dice, a ogni chiamata,
quanti token di prompt ha contato davvero: qui si tiene il rapporto fra i due,
per modello, e la stima lo applica (``helpers.estimate_prompt_tokens_chain``).

Si tiene anche l'ultimo numero vero di ogni sessione, per chi vuole mostrare
quanto e' grande la conversazione adesso (``/status``).

Stato di processo, in memoria: il rapporto e' una proprieta' del tokenizer, non
della conversazione, e dopo un riavvio si rifa' alla prima chiamata.
Leaf-level: solo stdlib.
"""

from __future__ import annotations

import threading

# Quanto pesa una misura nuova rispetto alla media (media mobile esponenziale).
_ALPHA = 0.3
# Sotto questa stima la misura e' rumore (un prompt di prova, un ping).
_MIN_ESTIMATE = 2_000
# Un rapporto fuori da qui e' un errore di misura, non un tokenizer.
_MIN_FACTOR, _MAX_FACTOR = 0.75, 2.0

_lock = threading.Lock()
_factors: dict[str, float] = {}
_last_real: dict[str, int] = {}


def record(model: str | None, *, real: int, estimated: int,
           session_key: str | None = None) -> None:
    """Una chiamata andata: *real* token secondo il provider, *estimated* secondo noi."""
    if real <= 0:
        return
    with _lock:
        if session_key:
            _last_real[session_key] = int(real)
        if not model or estimated < _MIN_ESTIMATE:
            return
        sample = min(_MAX_FACTOR, max(_MIN_FACTOR, real / estimated))
        old = _factors.get(model)
        _factors[model] = sample if old is None else old + _ALPHA * (sample - old)


def factor(model: str | None) -> float:
    """Il rapporto vero/stima di *model*; 1.0 finche' non c'e' una misura."""
    if not model:
        return 1.0
    with _lock:
        return _factors.get(model, 1.0)


def last_real(session_key: str | None) -> int | None:
    """I token di prompt veri dell'ultima chiamata di *session_key*, se ce n'e' stata una."""
    if not session_key:
        return None
    with _lock:
        return _last_real.get(session_key)


def reset() -> None:
    """Dimentica tutto (per i test)."""
    with _lock:
        _factors.clear()
        _last_real.clear()
