"""Righe di storia che portano ``role: "user"`` senza essere parole dell'utente.

Una riga ``role: "user"`` nel JSONL di sessione è l'unico segno che Jenny ha di
«ha parlato l'utente», e **quattro** cose la scrivono senza essere l'utente:

- un turno di cron, che si persiste come un messaggio qualsiasi
  (``jenny/cron/session_turns.py::cron_history_overrides``);
- il rientro di un subagent: iniettato a metà turno, che
  ``AgentLoop._drain_pending`` normalizza a ``{"role": "user", ...}`` per darlo
  al modello, o arrivato a turno chiuso, che il turno d'annuncio scrive con lo
  stesso ruolo (``TurnPersistenceMixin._persist_subagent_followup``);
- la continuazione sintetica di un sustained goal
  (``jenny/utils/runtime.py::build_goal_continue_message``);
- il turno con cui il driver di un piano apre un passo, o lo sollecita
  (``jenny/plan/driver.py``).

Chi legge la storia per rispondere a una domanda **sull'utente** — quale bolla
mostrare in chat, come titolare la conversazione, «si è fatto vivo dopo che gli
abbiamo scritto?» — deve saltarle tutte. Prima ne esisteva il marcatore
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

#: Fonti non fidate di un rientro di subagent (``security.subagent_policy``):
#: presente solo se il lavoro ha letto contenuto che non viene dall'utente.
#: Finché la riga sta nella parte non consolidata della sessione, i lavori che
#: Jenny avvia nascono contaminati.
SUBAGENT_TAINT_META = "subagent_taint_sources"

#: Fonti non fidate che un giro di tool di Jenny stessa ha portato nel turno
#: (un ``web_fetch``, un ``http_get`` dentro ``python_exec``): sta sui risultati
#: di quel giro. Come ``SUBAGENT_TAINT_META``, finché la riga resta nella parte
#: non consolidata, i lavori che Jenny avvia nascono contaminati — anche nei
#: turni dopo, non solo in quello del tool.
TOOL_TAINT_META = "tool_taint_sources"

#: Sprone sintetico a un sustained goal ancora attivo.
GOAL_CONTINUE_EVENT = "goal_continue"

#: Ripresa dopo un output troncato dal tetto di token.
LENGTH_RECOVERY_EVENT = "length_recovery"

#: Un messaggio che l'utente ha scritto mentre il turno lavorava, iniettato a
#: meta' giro. **Non** e' una riga sintetica (l'ha scritta l'utente): il segno
#: serve solo al governatore del runner, che nella richiesta lo presenta come
#: tale (``context_governor.label_mid_turn_messages``).
MID_TURN_META = "mid_turn"

#: Il testo di una riga ``user`` com'e' andato al provider: col Runtime Context
#: in coda, l'etichetta di un messaggio arrivato a meta' turno. ``content`` resta
#: quello che l'utente ha scritto, per chi legge da umano (WebUI, ``/history``,
#: consolidatore); questo lo rimanda solo ``Session.get_history(replay_sent=True)``,
#: cioe' la richiesta del turno dopo. La cache dei provider vale solo su un
#: prefisso identico: misurato l'11/10/2026, rimandare una riga diversa da come
#: era partita faceva ripagare per intero la catena di tool del turno prima
#: (9,8k token su DeepSeek, 14k su Haiku 5.5).
SENT_CONTENT_META = "sent_content"

#: Su una riga ``assistant``: il suo ragionamento non torna piu' al provider
#: (``reasoning_content: ""``, solo DeepSeek). Lo mette
#: ``context_governor.settle_closed_reasoning`` nei soli turni in cui la cache
#: e' gia' persa, e da li' in poi la riga si rimanda sempre vuota: azzerarlo
#: al turno dopo, come si faceva, rompeva il prefisso alla prima assistant con
#: tool del turno prima (9,8k token fuori cache su DeepSeek, 11/10/2026).
REASONING_DROPPED_META = "reasoning_dropped"

#: Su una riga di un turno lungo: e' stata sostituita dal checkpoint della
#: compattazione a meta' turno (``agent/inflight_compaction.py``). Resta nella
#: sessione — il consolidatore e Dream la leggono — ma non torna piu' al modello:
#: ``Session.get_history`` la salta.
COMPACTED_META = "compacted"

#: Sulla prima riga tenuta alla lettera dopo una compattazione a meta' turno: il
#: testo del checkpoint. ``Session.get_history`` lo rimette in coda alla
#: richiesta del turno, come stava nell'ultima richiesta del turno: cosi' il turno
#: dopo riparte dalla vista compattata, e col prefisso che il provider ha in
#: cache. Senza, il turno dopo rileggeva la catena intera e lo snip la buttava
#: quasi tutta (misurato il 25/10/2026: 26 messaggi su 27, e il modello non
#: sapeva piu' cosa aveva letto).
CHECKPOINT_META = "turn_checkpoint"

#: Il turno con cui il driver di un piano a lista apre un passo
#: (``jenny/plan/driver.py``): la lista, il passo e le regole, scritti dal
#: codice. Come il rientro di un subagent, porta il ruolo ``user`` per il modello
#: e non e' una bolla dell'utente.
PLAN_STEP_EVENT = "plan_step"

#: L'unico promemoria che il driver manda a un passo rimasto aperto senza
#: aiutanti in volo, prima di dichiararlo bloccato.
PLAN_REMINDER_EVENT = "plan_reminder"

#: I due eventi del piano, per chi deve riconoscere un turno aperto dal driver.
PLAN_TURN_EVENTS = frozenset({PLAN_STEP_EVENT, PLAN_REMINDER_EVENT})

_INJECTED_EVENTS = frozenset({
    SUBAGENT_RESULT_EVENT,
    GOAL_CONTINUE_EVENT,
    LENGTH_RECOVERY_EVENT,
    PLAN_STEP_EVENT,
    PLAN_REMINDER_EVENT,
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
