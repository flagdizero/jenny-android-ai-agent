/** Politica di rendering del pannello subagent — funzioni pure, zero DOM.
 *
 * Sta fuori da mobile-chat.js per due motivi. Primo: è *policy*, non markup —
 * quali card esistono e quali bottoni portano non dipendono da come sono
 * disegnate. Secondo: senza DOM è eseguibile così com'è, ed è quello che fa
 * tests/webui/test_subagent_panel_policy.py, che è l'unico posto dove queste
 * regole sono verificate invece che descritte.
 *
 * Due metà, con lo stesso vincolo: le card del pannello (sotto) e lo stream di
 * attività della modale (in fondo). La seconda è arrivata dopo, ma è policy per
 * la stessa ragione: append-o-rimpiazza, cursore, buco dichiarato e
 * accoppiamento start/end sono *regole del filo*, non disegno — e sbagliarne una
 * produce una vista che sembra viva e non lo è.
 */

/* Stati terminali: il subagent non è più in gioco. Il pannello mostra il LAVORO
   VIVO, quindi una card terminale non è storia — è la transizione, che va vista
   una volta. Resta per il turno corrente e sparisce a `turn_end`, o comunque
   scade da sé dopo `SA_LINGER_MS` se quel frame non arriva mai; lo storico vero
   è nella chat (l'orchestratore riassume ogni esito) e nei record su disco. */
export const SA_TERMINAL_STATES = ['done', 'failed', 'budget', 'cancelled'];

/* Quanto vive una card terminale se `turn_end` non arriva mai. `turn_end` resta
   il modo normale in cui una card sparisce, ma è un frame **live**: nessuno lo
   ritrasmette a chi non era connesso. Su questo telefono non essere connessi è
   la norma — Doze, schermo spento, WebView strozzata — e un turno di cron
   consegnato in quella finestra lascia il pannello senza il proprio sweeper: il
   client si riattacca, la cronologia ridipinge i messaggi, `liveIds` sopravvive
   in memoria e il primo poll ripesca il terminato da `recent`. Misurato sul
   Titan 2: la card di un subagent finito alle 07:00 stava ancora sopra il
   composer alle 10:18, con un turn_end (08:00) passato nel frattempo.

   L'altra metà dello stesso buco è per costruzione: un subagent ancora vivo a
   `turn_end` si re-iscrive (v. saVisibleCards), quindi terminerà *dopo* il solo
   evento che l'avrebbe spazzato — e su un install guidato dai cron il turno
   successivo può essere a ore di distanza.

   Il tetto è di orologio di parete e non di turni, perché è la freschezza che
   giustifica la card: la transizione va vista, e dopo un minuto non c'è più
   niente da vedere. */
export const SA_LINGER_MS = 60_000;

/* `ended_at` assente o non numerico = freschezza ignota, quindi scaduta: una
   card che non sa dimostrare di essere appena nata non merita lo spazio sopra il
   composer. In pratica non capita — ``ended_at`` è ``time.time()`` su ogni
   record terminale — ma il default deve cadere dalla parte del pannello vuoto. */
function saEndedAtMs(entry) {
  const ended = Number(entry?.ended_at);
  return Number.isFinite(ended) && ended > 0 ? ended * 1000 : NaN;
}

/** Millisecondi rimasti a una card terminale, 0 se è già scaduta. */
export function saLingerLeftMs(entry, now = Date.now()) {
  const endedAt = saEndedAtMs(entry);
  if (!Number.isFinite(endedAt)) return 0;
  return Math.max(0, endedAt + SA_LINGER_MS - now);
}

/* Matrice delle azioni, per stato e per superficie. Tabella e non if/else
   perché è la regola, e il test la rilegge da qui.

   Il perché di ogni riga:
   - running   → solo Stop. Un rilancio alla cieca rigioca la stessa spec: su un
                 job che sta andando non ha senso, e il tap in più costerebbe una
                 colonna di card.
   - stalled   → Stop + Rilancia. È l'unico caso per cui il rilancio esiste:
                 nessun progresso e l'orchestratore non è ancora intervenuto.
                 Resta sulla card, un tap, senza passare dalla modale.
   - failed    → nessuna azione sulla card, Rilancia solo nella modale. Rigiocare
                 la stessa spec su un job fallito rifallisce quasi sempre allo
                 stesso modo; quello che recupera i fallimenti è il riavvio
                 *informato* dell'orchestratore, con una nota correttiva. Il
                 rilancio cieco resta raggiungibile, ma non a un tap.
   - done      → niente: rifare un lavoro riuscito non è mai giusto.
   - cancelled → niente: l'ha fermato l'utente, un bottone che lo riavvia
                 accanto alla scritta "annullato" è una trappola. */
export const SA_ACTIONS = {
  running: { card: ['stop'], modal: ['stop'] },
  stalled: { card: ['stop', 'restart'], modal: ['stop', 'restart'] },
  failed: { card: [], modal: ['restart'] },
  /* budget: fermata perché ha finito le chiamate. Come `failed` qui; il modo
     giusto di riprenderla (continuare con la storia) sta nella casa. */
  budget: { card: [], modal: ['restart'] },
  done: { card: [], modal: [] },
  cancelled: { card: [], modal: [] },
};

/** Azioni ammesse per stato su una superficie ('card' | 'modal').
 *
 * `entry` (facoltativa) e' la voce dello snapshot. Serve a un caso solo: una
 * fermata al budget con la storia ancora intera (`resumable`) non offre il
 * rilancio da zero, che butterebbe quel che ha letto e scritto; si riprende
 * dalla casa («Falla continuare») o da Jenny con `subagent_send`. */
export function saActions(state, surface, entry = null) {
  const key = String(state || '');
  const row = SA_ACTIONS[key];
  if (!row) return [];
  if (key === 'budget' && entry?.resumable) return [];
  return row[surface] || [];
}

export function saIsTerminal(state) {
  return SA_TERMINAL_STATES.includes(String(state || ''));
}

/** Card da rendere, a partire da uno snapshot e dagli id visti vivi nel turno.
 *
 * `liveIds` è l'insieme dei task id che questo client ha visto in `running`
 * durante il turno corrente. È il filtro che tiene il pannello sul lavoro vivo:
 * `snapshot.recent` è una coda che il server serve sempre (la consumano il tool
 * `subagent_status` e GET /api/subagents), e dopo un reload contiene job di turni
 * passati. Renderizzare una voce terminale solo se la sua transizione è stata
 * *osservata* qui è ciò che rende un reload a turno finito un pannello vuoto.
 *
 * Ritorna anche `liveIds` aggiornato: i vivi di adesso restano ammessi a
 * lingerare quando termineranno, anche se il turno intanto è finito (un
 * subagent può sopravvivere al turno che l'ha lanciato). Un terminato scaduto
 * (v. SA_LINGER_MS) invece *esce* da `liveIds`: senza questo il poll successivo
 * lo ripescherebbe da `recent`, e la scadenza sarebbe un filtro che dimentica.
 *
 * `nextExpiryMs` è quanto manca alla prima scadenza fra le card mostrate, o
 * `null` se non ce ne sono. Serve al chiamante per programmare l'ultimo
 * ri-render: a zero running non c'è nessun poll che lo faccia da sé, e una
 * scadenza che nessuno guarda non fa sparire niente.
 */
export function saVisibleCards(snapshot, liveIds, now = Date.now()) {
  const running = Array.isArray(snapshot?.running) ? snapshot.running : [];
  const recent = Array.isArray(snapshot?.recent) ? snapshot.recent : [];
  const live = new Set(liveIds || []);
  const aliveNow = new Set();
  for (const entry of running) {
    if (entry && entry.task_id) {
      live.add(String(entry.task_id));
      aliveNow.add(String(entry.task_id));
    }
  }
  const lingering = [];
  let nextExpiryMs = null;
  for (const entry of recent) {
    const taskId = entry && entry.task_id ? String(entry.task_id) : '';
    if (!taskId || !live.has(taskId)) continue;
    // Uno stesso id vivo *e* in `recent` non capita, ma se capitasse la card
    // viva è quella che conta: la scadenza non deve poterla sfrattare.
    if (aliveNow.has(taskId)) continue;
    const left = saLingerLeftMs(entry, now);
    if (left <= 0) {
      live.delete(taskId);
      continue;
    }
    lingering.push(entry);
    if (nextExpiryMs === null || left < nextExpiryMs) nextExpiryMs = left;
  }
  return { running, lingering, liveIds: live, nextExpiryMs };
}

/* ══════════════════════════════════════════════════════════════════════════
   Stream di attività (modale)

   Il gateway spinge frame `subagent_activity` solo a chi guarda, e ogni frame
   porta il proprio cursore (`since_seq`, `first_seq`, `last_seq`, `latest_seq`,
   `gap`). Tutto ciò che riguarda *quali eventi lo stato contiene* vive qui,
   perché sono tre regole che si rompono in silenzio:

   1. `initial: true` rimpiazza, tutto il resto appende. Sbagliata la prima, una
      riapertura della modale duplica ciò che si aveva già; sbagliata la seconda,
      un frame di coda cancella la testa.
   2. Il cursore è monotono e l'append scarta i `seq` già visti. È ciò che rende
      un reconnect (ri-watch dallo stesso cursore) e una risync HTTP idempotenti:
      lo stesso evento può arrivare due volte per due strade, e non deve mai
      comparire due volte.
   3. Un buco si dice. `gap` arriva dichiarato dal server (unica regola, riderivata
      al confine del filo) e qui diventa una richiesta di risync più un marcatore
      sulla riga: uno stream bucato che si presenta integro è peggio di un
      pannello statico, perché si guadagna una fiducia che non merita.
   ══════════════════════════════════════════════════════════════════════════ */

/* Quanti eventi lo stato tiene. Il ring lato server ne tiene 200 per task, la
   risync HTTP ne restituisce altrettanti: 400 copre due finestre piene senza far
   crescere per sempre né la memoria né la lista da rendere su un telefono. Il
   taglio è visibile (`trimmed`), come ogni altra perdita. */
export const SA_ACTIVITY_KEEP = 400;

/* Kind che il *digest* aggiunge all'enum vivo: una coppia start/end già
   collassata dal server. Il renderer la tratta come una riga tool risolta. */
const SA_KIND_TOOL = 'tool';
const SA_STATUS_INCOMPLETE = 'incomplete';

function saInt(value) {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : 0;
}

function saText(value) {
  return typeof value === 'string' ? value : '';
}

/** Evento normalizzato, o `null` se inutilizzabile.
 *
 * Senza `seq` intero e positivo l'evento non è collocabile: il `seq` è ciò che
 * rende lo stream verificabile, e tenerne uno senza romperebbe il cursore.
 * `summary` viene copiato e mai ricostruito — è già curato e capato a 160
 * caratteri dal server, e riderivarlo da `name`/`status` significherebbe
 * mostrare qualcosa che il produttore non ha autorizzato.
 */
function saEvent(raw) {
  if (!raw || typeof raw !== 'object') return null;
  const seq = saInt(raw.seq);
  if (!seq) return null;
  // `duration_ms` è opzionale e arriva `null` sugli eventi che non ne hanno uno
  // (fase, iterazione, messaggio in ingresso): `Number(null)` è 0, quindi senza
  // questo controllo ogni riga senza durata ne mostrava una da "0ms".
  const rawDuration = raw.duration_ms;
  const duration = rawDuration === null || rawDuration === undefined
    ? NaN
    : Number(rawDuration);
  return {
    seq,
    ts: Number(raw.ts) || 0,
    kind: saText(raw.kind) || 'phase',
    name: saText(raw.name),
    callId: saText(raw.call_id),
    status: saText(raw.status),
    summary: saText(raw.summary),
    durationMs: Number.isFinite(duration) && duration >= 0 ? Math.floor(duration) : null,
  };
}

/** Stato iniziale dello stream di un task. */
export function saActivityInit(taskId) {
  return {
    taskId: String(taskId || ''),
    events: [],
    // Ultimo `seq` consegnato: è ciò che si rimanda in `subagent_watch` dopo un
    // reconnect, e il filtro dell'append.
    cursor: 0,
    // Massimo `seq` mai esistito per il task (sopravvive allo sfratto dal ring):
    // distingue "non è ancora successo niente" da "ti sei perso l'inizio".
    latestSeq: 0,
    dropped: 0,
    trimmed: false,
  };
}

/** Unione di due sequenze ordinate per `seq`, senza duplicati. */
function saMerge(current, incoming) {
  if (!current.length) return incoming.slice();
  if (!incoming.length) return current.slice();
  const out = [];
  let i = 0;
  let j = 0;
  while (i < current.length && j < incoming.length) {
    if (current[i].seq === incoming[j].seq) { out.push(current[i]); i++; j++; }
    else if (current[i].seq < incoming[j].seq) out.push(current[i++]);
    else out.push(incoming[j++]);
  }
  while (i < current.length) out.push(current[i++]);
  while (j < incoming.length) out.push(incoming[j++]);
  return out;
}

/** Applica una finestra (frame WS o risposta HTTP) allo stato.
 *
 * `replace` vale solo per la risposta immediata a un watch (`initial: true`):
 * quella è una ripartenza, e rimpiazzare è ciò che impedisce a una riapertura
 * della modale di duplicare la lista.
 *
 * Ritorna anche `resyncFrom`: il cursore da cui rileggere via HTTP quando il
 * server ha dichiarato un buco, oppure `null`. Non è un dettaglio del
 * chiamante — il *da dove* è il cursore di prima di questa finestra, non quello
 * di dopo, altrimenti la risync salterebbe proprio gli eventi mancanti.
 */
export function saActivityIngest(state, payload, { replace = false } = {}) {
  const next = saActivityInit(state?.taskId);
  const priorCursor = saInt(state?.cursor);
  const incoming = (Array.isArray(payload?.events) ? payload.events : [])
    .map(saEvent)
    .filter(Boolean)
    .sort((a, b) => a.seq - b.seq);
  const since = saInt(payload?.since_seq);
  const last = saInt(payload?.last_seq);

  // La deduplica è nell'unione per `seq`, non in un filtro sul cursore: dopo un
  // buco la risync rilegge da *prima* del cursore attuale, e un filtro
  // "> cursore" scarterebbe esattamente gli eventi che serve recuperare.
  let events = replace
    ? incoming
    : saMerge(Array.isArray(state?.events) ? state.events : [], incoming);
  next.trimmed = replace ? false : !!state?.trimmed;
  if (events.length > SA_ACTIVITY_KEEP) {
    events = events.slice(-SA_ACTIVITY_KEEP);
    next.trimmed = true;
  }
  next.events = events;
  next.cursor = Math.max(replace ? since : priorCursor, last);
  next.latestSeq = Math.max(
    replace ? 0 : saInt(state?.latestSeq), saInt(payload?.latest_seq), next.cursor,
  );
  next.dropped = Math.max(replace ? 0 : saInt(state?.dropped), saInt(payload?.dropped));
  return {
    state: next,
    resyncFrom: payload?.gap === true ? (replace ? since : priorCursor) : null,
  };
}

/** Applica un frame WS, ignorando quello di un task che non stiamo guardando.
 *
 * Il filtro sul `task_id` non è paranoia: la modale si può chiudere e riaprire su
 * un altro subagent mentre un frame del precedente è ancora in volo, e quel
 * frame appartiene a una lista che non esiste più.
 *
 * `initial: true` rimpiazza **solo se il watch partiva da zero**. Dopo un
 * reconnect si ri-watcha dal cursore che si ha già (`since = cursor`), e quella
 * risposta iniziale contiene per costruzione solo ciò che manca: rimpiazzare
 * butterebbe via tutto lo stream letto fin lì, che è l'unica cosa che il
 * reconnect deve *non* fare. Appenderlo non può duplicare nulla, perché
 * l'unione è per `seq`.
 */
export function saActivityFrame(state, frame) {
  const taskId = String(frame?.task_id || '');
  if (!state || !taskId || taskId !== state.taskId) {
    return { state, resyncFrom: null, applied: false };
  }
  const fresh = frame?.initial === true && saInt(frame?.since_seq) === 0;
  const out = saActivityIngest(state, frame, { replace: fresh });
  return { state: out.state, resyncFrom: out.resyncFrom, applied: true };
}

/* Etichetta di un segnale continuo ("thinking: ...", "writing: ..."): serve solo
   a NON fondere un run di ragionamento con uno di scrittura, che sono due cose
   diverse anche se condividono il kind. Il summary resta quello del server. */
function saSignalLabel(summary) {
  const text = saText(summary);
  const idx = text.indexOf(':');
  if (idx <= 0 || idx > 16) return '';
  const head = text.slice(0, idx);
  return /^[a-z]+$/.test(head) ? head : '';
}

function saRow(event, extra) {
  return {
    key: `s${event.seq}`,
    seq: event.seq,
    lastSeq: event.seq,
    kind: event.kind,
    name: event.name,
    status: event.status,
    summary: event.summary,
    outcome: '',
    durationMs: event.durationMs,
    ts: event.ts,
    pending: false,
    repeats: 1,
    label: '',
    // Quanti `seq` mancano *prima* di questa riga: 0 se contigua. Ricalcolato a
    // ogni giro, quindi una risync che tappa il buco lo fa sparire da sé.
    missing: 0,
    ...extra,
  };
}

/** Righe da rendere, derivate dagli eventi. Nessuno stato, nessun DOM.
 *
 * Tre collassi, tutti per la stessa ragione — su un telefono la riga è la
 * risorsa scarsa, e "cosa sta facendo adesso" si legge solo se non è sepolto:
 *
 * - `tool_start` + `tool_end` diventano UNA riga (azione → esito, durata,
 *   status). L'accoppiamento usa il `call_id` del provider e degrada a FIFO per
 *   nome: con tre `web_fetch` in volo nello stesso batch il nome accoppierebbe a
 *   caso. Finché l'end non arriva la riga resta `pending`, ed è quella la
 *   risposta a "cosa sta facendo adesso".
 * - un run di `thinking` consecutivi diventa UNA riga che si aggiorna: il server
 *   ne emette uno ogni 0.4s *anche a testo invariato* (cambia `duration_ms`), che
 *   è ciò che fa ticchettare "thinking · 12s" senza tenere un orologio qui, ma
 *   come righe distinte seppellirebbe tutto il resto in dieci secondi.
 * - il kind `tool` del digest (start/end già collassati dal server) entra nella
 *   stessa forma di riga, così la chat e la modale hanno un renderer solo.
 */
export function saActivityRows(state) {
  const events = Array.isArray(state?.events) ? state.events : [];
  const rows = [];
  const byCall = new Map();
  const byName = new Map();
  const closed = new Set();
  let signalRow = null;
  let expected = 1;

  const closeSlot = (event) => {
    if (event.callId && byCall.has(event.callId)) {
      const slot = byCall.get(event.callId);
      byCall.delete(event.callId);
      if (!closed.has(slot)) { closed.add(slot); return slot; }
    }
    const queue = byName.get(event.name || '');
    while (queue && queue.length) {
      const slot = queue.shift();
      if (!closed.has(slot)) { closed.add(slot); return slot; }
    }
    return null;
  };

  for (const event of events) {
    const missing = event.seq > expected ? event.seq - expected : 0;
    expected = event.seq + 1;

    if (event.kind === 'thinking') {
      const label = saSignalLabel(event.summary);
      if (signalRow && !missing && signalRow.label === label) {
        // Stesso run: la riga si aggiorna sul posto. `lastSeq` sposta la chiave
        // di firma del renderer, non la chiave di identità.
        signalRow.summary = event.summary;
        signalRow.durationMs = event.durationMs;
        signalRow.ts = event.ts;
        signalRow.lastSeq = event.seq;
        signalRow.repeats += 1;
        continue;
      }
      signalRow = saRow(event, { missing, label });
      rows.push(signalRow);
      continue;
    }
    // Qualunque altro evento chiude il run: il ragionamento è finito lì.
    signalRow = null;

    if (event.kind === 'tool_start') {
      const slot = rows.length;
      rows.push(saRow(event, { kind: SA_KIND_TOOL, missing, pending: true }));
      if (event.callId) byCall.set(event.callId, slot);
      if (!byName.has(event.name || '')) byName.set(event.name || '', []);
      byName.get(event.name || '').push(slot);
      continue;
    }

    if (event.kind === 'tool_end') {
      const slot = closeSlot(event);
      if (slot === null) {
        // End senza start: lo start è stato sfrattato dal ring o tagliato. La
        // riga resta, perché l'esito è comunque informazione.
        rows.push(saRow(event, {
          kind: SA_KIND_TOOL, missing, outcome: event.summary, summary: '',
        }));
        continue;
      }
      const row = rows[slot];
      row.pending = false;
      row.outcome = event.summary;
      row.status = event.status;
      row.lastSeq = event.seq;
      row.ts = event.ts;
      row.durationMs = event.durationMs;
      if (event.name) row.name = event.name;
      continue;
    }

    rows.push(saRow(event, {
      missing,
      // Il digest porta gli incompleti (subagent morto a metà chiamata): sono
      // "non lo sappiamo", non "fallito", e la riga lo dice restando pending.
      pending: event.kind === SA_KIND_TOOL && event.status === SA_STATUS_INCOMPLETE,
    }));
  }

  // Un run di ragionamento è in corso solo se è l'ultima cosa accaduta: dopo di
  // esso un qualunque evento lo ha già chiuso.
  const tail = rows[rows.length - 1];
  if (tail && tail.kind === 'thinking') tail.pending = true;

  const first = rows[0];
  return {
    rows,
    // Eventi mai arrivati in testa alla lista: sfrattati dal ring (`dropped`),
    // tagliati da SA_ACTIVITY_KEEP (`trimmed`) o persi prima del primo watch.
    // Per chi guarda sono la stessa cosa — "prima di qui non c'è" — e si dicono
    // una volta sola, in cima.
    headMissing: first ? first.missing : 0,
    pending: rows.filter(r => r.pending).length,
    count: events.length,
    latestSeq: saInt(state?.latestSeq),
    dropped: saInt(state?.dropped),
    trimmed: !!state?.trimmed,
    // Nessun evento *e* nessun seq mai assegnato: non è un buco, è un'attesa.
    waiting: events.length === 0 && !saInt(state?.latestSeq),
  };
}

/** Vista del digest ("cosa ha fatto davvero") a partire dal payload HTTP.
 *
 * `show: false` è il caso `source: "none"` — nessun blocco da espandere, non un
 * blocco vuoto: un accordion che si apre sul nulla è peggio della sua assenza.
 * `live` dice che la condensa viene dal ring di un subagent ancora al lavoro,
 * quindi cambierà: dirlo è l'unica differenza fra un'anteprima e una bugia.
 */
export function saDigestView(payload) {
  const source = String(payload?.source || 'none');
  const events = (Array.isArray(payload?.events) ? payload.events : [])
    .map(saEvent)
    .filter(Boolean)
    .sort((a, b) => a.seq - b.seq);
  if (!events.length || source === 'none') {
    return { show: false, source: 'none', live: false, rows: [], count: 0 };
  }
  const view = saActivityRows({ events, latestSeq: events[events.length - 1].seq });
  return { show: true, source, live: source === 'live', rows: view.rows, count: view.rows.length };
}
