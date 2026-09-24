"""Contratto di filo dell'attività di un subagent: tetti, forma, registro dei watcher.

Modulo foglia (nessuno stato globale, nessun import verso il canale né verso
``jenny/agent``) condiviso dai **due** trasporti che portano la stessa forma:

* il frame WebSocket ``subagent_activity`` (``channels/ws_sender.py``), che è il
  percorso live;
* ``GET /api/subagents/{id}/activity`` (``webui/subagent_routes.py``), che è il
  percorso di risincronizzazione dopo un reload o una WebView uccisa da Android.

Una sola forma, due trasporti — la stessa disciplina già usata per lo snapshot
dei subagent. I nomi dei campi sono quelli di ``ActivityWindow.to_dict()``:
``events``, ``since_seq``, ``first_seq``, ``last_seq``, ``latest_seq``,
``dropped``, ``gap``.

**Perché ``gap`` viene riderivato qui e non copiato.** La regola è una sola —
il primo ``seq`` consegnato dovrebbe essere ``since_seq + 1``, e se è maggiore
manca qualcosa — e copre sia lo sfratto dal ring sia il troncamento ai tetti di
questo modulo. Riderivarla al confine del filo significa che un tetto applicato
*qui* non può produrre una finestra che si dichiara integra: il client non può
mai fidarsi di uno stream bucato senza accorgersene, che è l'unico modo in cui
una telemetria è peggio di nessuna telemetria.

Il modulo non importa ``jenny.agent``: il log e lo store arrivano come
dipendenze opache (duck-typing su ``tail_window`` / ``digest`` / ``load``) e i
tetti sono ridefiniti qui con il loro perché. È lo stesso vincolo di layering
già documentato in ``webui/subagent_routes.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from jenny.security.wire_ids import WIRE_ID_RE

__all__ = [
    "ACTIVITY_FRAME_EVENT",
    "ACTIVITY_PUMP_INTERVAL_S",
    "MAX_DIGEST_WIRE_EVENTS",
    "MAX_FRAME_EVENTS",
    "MAX_HTTP_EVENTS",
    "MAX_WATCHES_PER_CONNECTION",
    "SubagentWatchRegistry",
    "UNWATCH_REASON_CLIENT",
    "UNWATCH_REASON_LIMIT",
    "activity_frame",
    "digest_payload",
    "empty_window_payload",
    "normalize_since",
    "normalize_task_id",
    "slice_for_cursor",
    "window_payload",
]

# Nome dell'evento sul filo. Costante perché la scrive il canale e la legge la
# WebUI: un letterale duplicato diventa un frame ignorato al primo refuso.
ACTIVITY_FRAME_EVENT = "subagent_activity"

# Ack di fine osservazione. Due ragioni distinte, perché il client deve poter
# distinguere "ho chiuso io il modal" da "il gateway mi ha sfrattato": nel
# secondo caso la sua vista è ferma e va riaperta, non solo svuotata.
UNWATCH_REASON_CLIENT = "client"
UNWATCH_REASON_LIMIT = "watch_limit"

# Periodo del pump. È il numero che decide sia la reattività sia il costo:
#
# * 0.4 s è sotto la soglia in cui un umano legge il pannello come "fermo"
#   (il poll che si sostituisce era 5 s, e il modal sembrava statico);
# * a regime un subagent produce qualche evento al secondo, quindi un tick
#   coalizza il burst in **un** frame invece di uno per evento — e il costo per
#   tick è una scansione in RAM di un deque da ≤200 elementi, non I/O;
# * il tick esiste solo mentre almeno una connessione sta guardando: con il
#   modal chiuso non c'è task, quindi il costo aggiunto è esattamente zero
#   anche con tre subagent al lavoro.
ACTIVITY_PUMP_INTERVAL_S = 0.4

# Tetto di eventi in **un** frame WS. Un evento pesa ~250 B sul filo (summary
# capato a 160 caratteri più i campi scalari), quindi 40 sono ~10 KB nel caso
# peggiore: il frame più grande che il pump può produrre resta di un ordine di
# grandezza sotto ``max_message_bytes``, e a 2.5 tick/s il tetto teorico è ~25
# KB/s per il solo task guardato. Un burst più fitto non allarga il frame: fa
# scattare ``gap``, e il client risincronizza via HTTP (che ha un tetto più
# alto perché è on-demand e non ricorrente).
MAX_FRAME_EVENTS = 40

# Tetto di eventi in una risposta HTTP di risync. Pari alla capienza del ring
# lato produttore (``RING_CAPACITY``, non importabile da qui per layering):
# una risync deve poter restituire *tutto* ciò che il ring ha ancora, altrimenti
# introdurrebbe un buco proprio nel percorso che serve a chiuderne uno.
MAX_HTTP_EVENTS = 200

# Tetto di eventi di un digest servito. Pari a ``MAX_DIGEST_EVENTS`` lato
# produttore: qui è una guardia contro un file scritto a mano o da una versione
# futura, non una potatura attesa (un digest nasce da un ring da 200 collassato).
MAX_DIGEST_WIRE_EVENTS = 300

# Quanti task una singola connessione può guardare insieme. Il pool di subagent
# è piccolo (default 3) e il modal è aperto su uno: 3 dà margine per tenere il
# watch precedente mentre se ne apre un altro, e nega la via per cui un client
# (o un client ostile) fa crescere il registro del gateway registrando task id
# arbitrari. Oltre il tetto si sfratta il watch più vecchio, non si rifiuta il
# nuovo: l'ultimo che l'utente ha chiesto è quello che sta guardando.
MAX_WATCHES_PER_CONNECTION = 3

# Charset chiuso del task id: diventa una chiave di dizionario lato gateway e un
# nome di file lato digest, quindi passa da una whitelist invece che da una
# sanitizzazione per sottrazione. Identico al ``_ID_RE`` delle route.
_TASK_ID_RE = WIRE_ID_RE

# Cursore massimo accettato da un client. Un ``since`` fuori scala viene da un
# bug, non da un ring: ammetterlo significherebbe tenere per sempre un cursore
# che nessun evento potrà mai superare.
_MAX_SINCE = 1_000_000_000


# ---------------------------------------------------------------------------
# Validazione degli input del client
# ---------------------------------------------------------------------------


def normalize_task_id(value: Any) -> str | None:
    """Task id valido, o ``None``. Nessuna eccezione: l'input viene dal filo."""
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    return candidate if _TASK_ID_RE.match(candidate) else None


def normalize_since(value: Any) -> int:
    """Cursore ``since`` del client, degradato a 0 quando non è sensato.

    Accetta anche la forma stringa (``"12"``): il client la scrive in una query
    HTTP e in un envelope JSON, e due regole diverse per lo stesso numero
    sarebbero due bug diversi.
    """
    if isinstance(value, bool) or value is None:
        return 0
    if isinstance(value, str):
        try:
            value = int(value.strip() or 0)
        except ValueError:
            return 0
    if not isinstance(value, int):
        return 0
    return value if 0 <= value <= _MAX_SINCE else 0


# ---------------------------------------------------------------------------
# Forma della finestra sul filo
# ---------------------------------------------------------------------------


def _as_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    try:
        number = int(value)
    except (OverflowError, ValueError):
        return 0
    return number if number >= 0 else 0


def _event_seq(event: Any) -> int | None:
    """``seq`` di un evento, o ``None`` se l'evento non è utilizzabile.

    Un evento senza ``seq`` intero non è recapitabile: il ``seq`` è ciò che
    rende lo stream verificabile, e consegnarne uno senza romperebbe il
    cursore di chi lo riceve.
    """
    if not isinstance(event, Mapping):
        return None
    seq = event.get("seq")
    if isinstance(seq, bool) or not isinstance(seq, int) or seq <= 0:
        return None
    return seq


def _clean_events(raw: Any, *, limit: int) -> list[dict[str, Any]]:
    """Eventi utilizzabili, i **più recenti** entro *limit*, come dict piatti."""
    if not isinstance(raw, (list, tuple)):
        return []
    events = [dict(e) for e in raw if _event_seq(e) is not None]
    if limit >= 0 and len(events) > limit:
        events = events[-limit:]
    return events


def window_payload(window: Any, *, limit: int = MAX_HTTP_EVENTS) -> dict[str, Any] | None:
    """Normalizza una ``ActivityWindow`` (o il suo dict) nella forma di filo.

    Ritorna ``None`` solo quando l'oggetto non è una finestra affatto: è il
    segnale per il chiamante di servire una finestra vuota invece di inventarne
    una. Per un input ben formato entro *limit* la funzione è l'**identità**
    sulla forma (stesse chiavi, stessi valori) — è ciò che rende sensato dire
    che la route serve la finestra verbatim.

    ``gap`` viene riderivato dalla regola unica (vedi il docstring del modulo),
    quindi un troncamento applicato qui non può passare per finestra integra.
    """
    source: Any = window
    to_dict = getattr(window, "to_dict", None)
    if callable(to_dict):
        try:
            source = to_dict()
        except Exception:  # noqa: BLE001 — un doppio rotto non deve dare 500
            return None
    if not isinstance(source, Mapping):
        return None
    events = _clean_events(source.get("events"), limit=limit)
    since = _as_int(source.get("since_seq"))
    first = events[0]["seq"] if events else 0
    last = events[-1]["seq"] if events else 0
    return {
        "events": events,
        "since_seq": since,
        "first_seq": first,
        "last_seq": last,
        # ``latest_seq`` sopravvive allo sfratto: è ciò che distingue "non è
        # ancora successo niente" (0) da "ti sei perso l'inizio" (gap).
        "latest_seq": _as_int(source.get("latest_seq")),
        "dropped": _as_int(source.get("dropped")),
        "gap": first > since + 1,
    }


def empty_window_payload(since: int = 0) -> dict[str, Any]:
    """Finestra vuota nella forma di filo (task ignoto, o nessun agente ancora).

    ``latest_seq == 0`` con ``gap == False`` è la risposta onesta a "non è
    ancora successo niente": il client mostra un'attesa, non un buco.
    """
    return {
        "events": [],
        "since_seq": normalize_since(since),
        "first_seq": 0,
        "last_seq": 0,
        "latest_seq": 0,
        "dropped": 0,
        "gap": False,
    }


def slice_for_cursor(payload: Mapping[str, Any], cursor: int) -> dict[str, Any] | None:
    """Sotto-finestra per un watcher con il **suo** cursore.

    Il pump legge il ring una volta per task (col cursore minimo fra i suoi
    watcher) e da quella lettura ricava la fetta di ognuno: due schede aperte
    sullo stesso subagent, con cursori diversi, costano una lettura sola.

    ``None`` quando per quel cursore non c'è niente di nuovo — che è il caso in
    cui non si deve mandare alcun frame.
    """
    cursor = normalize_since(cursor)
    raw = payload.get("events")
    candidates = raw if isinstance(raw, (list, tuple)) else ()
    events = [
        dict(event)
        for event in candidates
        if (seq := _event_seq(event)) is not None and seq > cursor
    ]
    if not events:
        return None
    first = events[0]["seq"]
    last = events[-1]["seq"]
    return {
        "events": events,
        "since_seq": cursor,
        "first_seq": first,
        "last_seq": last,
        "latest_seq": _as_int(payload.get("latest_seq")),
        "dropped": _as_int(payload.get("dropped")),
        "gap": first > cursor + 1,
    }


def activity_frame(
    task_id: str,
    chat_id: str,
    payload: Mapping[str, Any],
    *,
    initial: bool = False,
) -> dict[str, Any]:
    """Frame WS ``subagent_activity``: envelope + finestra, piatto.

    ``initial`` compare **solo** sulla risposta immediata a un watch: dice al
    client di rimpiazzare la sua lista invece di appenderci, così una riapertura
    del modal non duplica ciò che aveva già.
    """
    frame: dict[str, Any] = {
        "event": ACTIVITY_FRAME_EVENT,
        "chat_id": chat_id,
        "task_id": task_id,
    }
    frame.update(payload)
    if initial:
        frame["initial"] = True
    return frame


def digest_payload(events: Any, source: str) -> dict[str, Any]:
    """Forma di filo del digest: lista piatta più la sua provenienza.

    ``source`` distingue il digest **persistito** (``"digest"``, scritto alla
    transizione terminale) da quello ricavato dal ring vivo (``"live"``, un
    subagent ancora al lavoro) e dall'assenza (``"none"``): il primo è completo,
    il secondo è un'anteprima che cambierà.
    """
    cleaned = _clean_events(events, limit=MAX_DIGEST_WIRE_EVENTS)
    return {"events": cleaned, "count": len(cleaned), "source": source if cleaned else "none"}


# ---------------------------------------------------------------------------
# Registro dei watcher
# ---------------------------------------------------------------------------


class SubagentWatchRegistry:
    """Chi sta guardando cosa, per connessione, con il cursore di ognuno.

    **Costo proporzionale a ciò che si guarda.** Il registro è vuoto finché un
    client non apre un modal, e il pump del canale esiste solo mentre il
    registro non è vuoto (:attr:`active`): tre subagent al lavoro con il
    pannello chiuso non producono un solo frame né un solo tick.

    Tre invarianti, tutte imposte qui:

    1. *Niente sopravvive alla connessione.* :meth:`forget` è chiamata da
       ``WebSocketChannel._cleanup_connection``, che è l'unico punto di uscita
       comune a disconnessione pulita, ``ConnectionClosed`` a metà invio e drop
       per backpressure (app in background su Android). Un client che sparisce
       senza ``subagent_unwatch`` non lascia nulla.
    2. *Il registro non può crescere per la vita del processo.* Ogni
       connessione ne tiene al massimo :data:`MAX_WATCHES_PER_CONNECTION`, e il
       task id passa da :func:`normalize_task_id` prima di arrivare qui.
    3. *Il cursore è monotono.* :meth:`advance` non torna indietro, così un
       frame consegnato non può essere rimandato da un tick successivo.

    Nessun lock: tutte le chiamate arrivano dal loop del canale (dispatch degli
    envelope e pump), che è single-thread. Lo si dice qui perché il log a valle
    invece è thread-safe, e la differenza è deliberata.
    """

    def __init__(self, *, max_per_connection: int = MAX_WATCHES_PER_CONNECTION) -> None:
        self._max = max(1, max_per_connection)
        # connessione -> {task_id: cursore}. L'ordine di inserimento del dict è
        # l'ordine di sfratto: il watch più vecchio è quello che l'utente ha
        # smesso di guardare per primo.
        self._by_conn: dict[Any, dict[str, int]] = {}
        # task_id -> connessioni che lo guardano (lookup inverso del pump).
        self._by_task: dict[str, set[Any]] = {}

    @property
    def active(self) -> bool:
        """``True`` se qualcuno sta guardando qualcosa (gate del pump)."""
        return bool(self._by_task)

    def watch(self, connection: Any, task_id: str, *, cursor: int = 0) -> list[str]:
        """Registra (o riallinea) un watch; ritorna i task sfrattati dal tetto.

        Idempotente sullo stesso ``(connessione, task_id)``: una seconda watch —
        due schede, un modal riaperto, un reconnect — aggiorna il cursore invece
        di duplicare l'iscrizione.
        """
        watches = self._by_conn.setdefault(connection, {})
        watches[task_id] = normalize_since(cursor)
        self._by_task.setdefault(task_id, set()).add(connection)
        evicted: list[str] = []
        while len(watches) > self._max:
            oldest = next(iter(watches))
            if oldest == task_id:
                break
            watches.pop(oldest, None)
            self._detach(connection, oldest)
            evicted.append(oldest)
        return evicted

    def unwatch(self, connection: Any, task_id: str) -> bool:
        """Smette di guardare un task. ``True`` se c'era davvero un watch."""
        watches = self._by_conn.get(connection)
        if watches is None or watches.pop(task_id, None) is None:
            return False
        if not watches:
            self._by_conn.pop(connection, None)
        self._detach(connection, task_id)
        return True

    def forget(self, connection: Any) -> list[str]:
        """Dimentica ogni watch di una connessione. Ritorna i task lasciati."""
        watches = self._by_conn.pop(connection, None)
        if not watches:
            return []
        for task_id in watches:
            self._detach(connection, task_id)
        return list(watches)

    def clear(self) -> None:
        """Svuota il registro (shutdown del canale)."""
        self._by_conn.clear()
        self._by_task.clear()

    def _detach(self, connection: Any, task_id: str) -> None:
        watchers = self._by_task.get(task_id)
        if watchers is None:
            return
        watchers.discard(connection)
        if not watchers:
            # Il task esce dal giro del pump appena nessuno lo guarda più: è
            # questa riga che rende ``active`` un gate onesto.
            self._by_task.pop(task_id, None)

    # -- letture del pump ----------------------------------------------------

    def tasks(self) -> list[str]:
        """Task guardati da almeno una connessione (copia: il pump await-a)."""
        return list(self._by_task)

    def cursors(self, task_id: str) -> list[tuple[Any, int]]:
        """``(connessione, cursore)`` di ogni watcher del task (copia)."""
        watchers = self._by_task.get(task_id)
        if not watchers:
            return []
        out: list[tuple[Any, int]] = []
        for connection in watchers:
            watches = self._by_conn.get(connection)
            if watches is not None and task_id in watches:
                out.append((connection, watches[task_id]))
        return out

    def min_cursor(self, task_id: str) -> int:
        """Cursore minimo fra i watcher: una lettura del ring serve tutti."""
        cursors = [cursor for _conn, cursor in self.cursors(task_id)]
        return min(cursors) if cursors else 0

    def advance(self, connection: Any, task_id: str, cursor: int) -> None:
        """Porta avanti il cursore di un watcher (mai indietro)."""
        watches = self._by_conn.get(connection)
        if watches is None or task_id not in watches:
            return
        watches[task_id] = max(watches[task_id], normalize_since(cursor))

    def is_watching(self, connection: Any, task_id: str) -> bool:
        return task_id in self._by_conn.get(connection, {})

    def watch_count(self) -> int:
        """Watch totali registrati (diagnostica e test)."""
        return sum(len(watches) for watches in self._by_conn.values())
