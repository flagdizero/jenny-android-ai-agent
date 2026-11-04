/** I turni di una conversazione: come la storia si raggruppa, e dove un turno
 *  dal vivo finisce.
 *
 *  Casa (`home-chat.js`) e officina (`mobile-chat.js`) avevano ciascuna la sua
 *  copia di tutte e due le regole, e avevano gia' cominciato a divergere: la
 *  casa teneva l'errore del modello e il dispositivo di chi scrive, l'officina
 *  il testo del confine. Qui la spina dorsale, una volta; ognuna ci appende
 *  quel che di un turno le serve (`absorb`). La mascotte segue i turni a modo
 *  suo, e resta a parte (`mobile-jenny.js`).
 *
 *  Le regole sono quelle del server (`jenny/webui/transcript_replay.py`):
 *
 *  - un messaggio dell'utente e un confine di contesto (`session_boundary`,
 *    da /new) chiudono il turno in corso;
 *  - le parti dell'assistente con lo **stesso** `turn_id` sono una bolla;
 *  - **senza id non si accorpa, mai**: con due id assenti `undefined !==
 *    undefined` e' falso, e quattro avvisi proattivi della notte prima
 *    finivano in una bolla sola (13/08/2026). Il server conia un id per ogni
 *    consegna, ma la storia gia' scritta quell'id non l'avra' mai.
 */

/** L'id del turno di un frame o di un messaggio, nelle due grafie del filo. */
export function turnIdOf(msg) {
  return msg?.turn_id || msg?.turnId || null;
}

/** I messaggi persistiti diventano turni.
 *
 *  Un turno dell'utente: `{ user: true, text, origin, device, media, at }`. Un
 *  confine: `{ boundary: true, text }`. Un turno dell'assistente: `{ turnId,
 *  content: '', media, latencyMs, at, error? }`, piu' quel che *absorb*
 *  ci mette. `at` e' l'ora del suo ultimo pezzo, cioe' di quando si e' chiuso.
 *
 *  - *skip(msg)*: vero per un messaggio che non c'e' (l'annuncio di un lavoro
 *    lungo, in casa);
 *  - *absorb(turn, msg)*: una parte dell'assistente entra nel suo turno — il
 *    testo, le tracce degli strumenti, i pensieri: ognuno decide cosa tenerne.
 */
export function groupTurns(messages, { skip = null, absorb }) {
  const turns = [];
  let current = null;
  const flush = () => {
    if (current) turns.push(current);
    current = null;
  };
  for (const msg of messages || []) {
    if (skip?.(msg)) continue;
    if (msg.session_boundary) {
      flush();
      turns.push({ boundary: true, text: msg.text || msg.content || '' });
      continue;
    }
    const role = msg.role || (msg.kind === 'user' ? 'user' : 'assistant');
    if (role === 'user') {
      flush();
      turns.push({
        user: true,
        text: msg.text || msg.content || '',
        origin: msg.origin,
        device: msg.device || null,
        media: Array.isArray(msg.media) ? msg.media : [],
        at: msg.sentAt ?? null,
      });
      continue;
    }
    const turnId = turnIdOf(msg);
    if (!current || !turnId || current.turnId !== turnId) {
      flush();
      current = { turnId, content: '', media: [], latencyMs: null, at: null };
    }
    if (msg.sentAt != null) current.at = msg.sentAt;
    if (Array.isArray(msg.media) && msg.media.length) current.media.push(...msg.media);
    if (msg.latencyMs != null) current.latencyMs = msg.latencyMs;
    /* Il turno e' finito in un errore del modello (`error: {kind}`, dal
       gateway): la bolla avra' il suo «Riprova» anche dopo una ricarica. */
    if (msg.error) current.error = msg.error;
    absorb(current, msg);
  }
  flush();
  return turns;
}

/** Il confine di un turno dal vivo: cosa fare di *msg* con il turno *current*
 *  aperto (il suo id, o `null`). *scoped* sono gli eventi che appartengono a
 *  un turno.
 *
 *  - `'same'`: il frame e' del turno aperto, o non porta un id (il retry di
 *    una consegna parziale arriva senza: vale il turno corrente);
 *  - `'adopt'`: nessun turno aperto, e questo lo apre col suo id;
 *  - `'ignore'`: il `turn_end` di un altro turno non riguarda quello aperto;
 *  - `'new'`: un altro turno comincia, e quello aperto si chiude. La risposta
 *    interrotta a meta' riprende nella propria bolla: due bolle separate
 *    dall'avviso sono l'ordine in cui le cose sono davvero successe. */
export function turnBoundary(current, msg, scoped) {
  if (!scoped.has(msg.event)) return 'same';
  const turnId = turnIdOf(msg);
  if (!turnId || turnId === current) return 'same';
  if (current === null) return 'adopt';
  if (msg.event === 'turn_end') return 'ignore';
  return 'new';
}
