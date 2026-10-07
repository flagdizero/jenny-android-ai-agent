"""Righe di storia che portano ``role: "user"`` senza essere parole dell'utente.

Una riga ``role: "user"`` nel JSONL di sessione è l'unico segno che Jenny ha di
«ha parlato l'utente», e **tre** cose la scrivono senza essere l'utente:

- un turno di cron, che si persiste come un messaggio qualsiasi
  (``jenny/cron/session_turns.py::cron_history_overrides``);
- il rientro di un subagent: iniettato a metà turno, che
  ``AgentLoop._drain_pending`` normalizza a ``{"role": "user", ...}`` per darlo
  al modello, o arrivato a turno chiuso, che il turno d'annuncio scrive con lo
  stesso ruolo (``TurnPersistenceMixin._persist_subagent_followup``);
- la continuazione sintetica di un sustained goal
  (``jenny/utils/runtime.py::build_goal_continue_message``).

Chi legge la storia per rispondere a una domanda **sull'utente** — quale bolla
mostrare in chat, come titolare la conversazione, «si è fatto vivo dopo che gli
abbiamo scritto?» — deve saltarle tutte e tre. Prima ne esisteva il marcatore
per una sola (cron), e le altre due passavano: da qui la bolla con dentro il
prompt di un subagent, un promemoria che si spacciava per una risposta e un
riarmo di heartbeat davanti a uno schermo vuoto. Questo modulo è il posto unico
dove si dice quali righe sono, così i tre lettori non divergono.

Il marcatore vive **solo** nel JSONL: ``SessionManager.get_history`` ricostruisce
i messaggi per il modello con una whitelist di chiavi, e i provider ne fanno
un'altra prima del filo (``LLMProvider._sanitize_request_messages``). Quel che si
scrive qui non raggiunge mai l'API.
"""

from __future__ import annotations

from typing import Any, Mapping

#: Chiave che nomina l'evento interno da cui una riga di storia è nata.
INJECTED_EVENT_META = "injected_event"

#: Rientro di un subagent. Lo scrivono entrambi i rami d'annuncio: l'iniezione
#: a metà turno (``AgentLoop._drain_pending``) e il turno d'annuncio autonomo
#: (``TurnPersistenceMixin._persist_subagent_followup``).
SUBAGENT_RESULT_EVENT = "subagent_result"

#: Sprone sintetico a un sustained goal ancora attivo.
GOAL_CONTINUE_EVENT = "goal_continue"

#: Ripresa dopo un output troncato dal tetto di token.
LENGTH_RECOVERY_EVENT = "length_recovery"

#: Un messaggio che l'utente ha scritto mentre il turno lavorava, iniettato a
#: meta' giro. **Non** e' una riga sintetica (l'ha scritta l'utente): il segno
#: serve solo al governatore del runner, che nella richiesta lo presenta come
#: tale (``context_governor.label_mid_turn_messages``).
MID_TURN_META = "mid_turn"

_INJECTED_EVENTS = frozenset({
    SUBAGENT_RESULT_EVENT,
    GOAL_CONTINUE_EVENT,
    LENGTH_RECOVERY_EVENT,
})


def is_synthetic_history_row(message: Mapping[str, Any] | None) -> bool:
    """True per una riga di storia che l'utente non ha scritto.

    Vale anche su una riga ``role: "assistant"`` — la forma con cui il turno
    d'annuncio autonomo persisteva il rientro di subagent fino al 02/10/2026, e
    che le sessioni esistenti portano ancora — e i chiamanti la vogliono saltare
    comunque: nessuna delle tre domande di cui sopra la riguarda.
    """
    if not message:
        return False
    # Import dentro la funzione, e non in testa: ``jenny.cron.session_turns``
    # importa ``jenny.session.keys``, che esegue ``jenny/session/__init__.py``,
    # che carica ``manager``, che importa questo modulo — e tornerebbe su
    # ``session_turns`` ancora a metà inizializzazione. Misurato il 2026-08-17
    # su ``manager.py``, che portava questo stesso import in testa: a freddo
    # ``import jenny.cron.session_turns`` sollevava, e la suite era verde perché
    # una raccolta completa carica ``jenny.session`` per prima. La guardia che
    # lo misura è ``tests/session/test_cold_imports.py``, che importa ogni
    # modulo di ``jenny/`` in un interprete nuovo.
    #
    # L'alternativa era rendere pigro ``jenny/session/__init__.py``, e costa più
    # di quanto sembri: una ``__getattr__`` di modulo fa diventare ``Any`` ogni
    # attributo sconosciuto del package — misurato, ``jenny.session.SessionManagr``
    # smette di essere un errore — e ``jenny/session`` sta nel sottoinsieme
    # **bloccante** di pyright. Si perderebbe il controllo dei nomi su tutto il
    # package per chiudere un ciclo che si chiude qui in una riga.
    from jenny.cron.session_turns import CRON_HISTORY_META

    if message.get(CRON_HISTORY_META) is True:
        return True
    return message.get(INJECTED_EVENT_META) in _INJECTED_EVENTS
