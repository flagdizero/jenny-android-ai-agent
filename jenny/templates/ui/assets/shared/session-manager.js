/** Session Manager — quale conversazione è aperta: attach + caricamento thread.
 *
 *  La chiave *è* l'indirizzo della conversazione, e ne esistono due forme:
 *  `websocket:default` per la chat personale e `project:<name>` per un progetto.
 *  Il gateway la usa per due cose diverse — la sessione che Jenny rilegge e il
 *  thread che viene disegnato — e le tiene separate da sé.
 */

import { api } from './api-client.js';
import { chatIdOf, wsManager } from './ws-manager.js';

const UNIFIED_KEY = 'websocket:default';

export class SessionManager extends EventTarget {
  constructor() {
    super();
    this.currentKey = UNIFIED_KEY;
    // Ultimo scope *ancora attuale* visto dal backend. Serve a chi lo guarda
    // molto dopo il caricamento (il popover Info sessione), non a chi disegna
    // il thread: quello lo riceve da `loadThread` e non lo legge da qui.
    this.currentScope = null;
    this.runStartedAt = null;
    // Quale cambio di conversazione è quello in corso: v. `switchGeneration`.
    this._switchGen = 0;
    this._initialized = false;
  }

  /** Passa a un'altra conversazione e si iscrive ai suoi messaggi.
   *
   *  Non tocca quel che è a schermo: ridisegnare il thread è di chi possiede la
   *  chat (`mobile-chat`), che sa anche quando è il momento di farlo.
   */
  switchTo(key) {
    const next = key || UNIFIED_KEY;
    if (next === this.currentKey) return false;
    const previous = this.currentKey;
    this.currentKey = next;
    this.currentScope = null;
    this.runStartedAt = null;
    // Da qui in avanti tutto ciò che era in volo per la conversazione lasciata
    // è scaduto: v. `switchGeneration`.
    this._switchGen++;
    // Prima l'attach della nuova, poi la detach della vecchia: mai un istante
    // con zero conversazioni seguite.
    wsManager.attachChat(next);
    wsManager.detachChat(previous);
    /* Il segnale che la conversazione è cambiata. Serve a chi tiene stato
       *per turno* e non per messaggio: dal cambio in poi i frame della vecchia
       chat vengono scartati, quindi il `turn_end` che avrebbe chiuso quel turno
       non arriverà mai a chi lo stava seguendo — e chi lo stava seguendo
       resterebbe in attesa per sempre (la mascotte incantata in `think`). */
    this.dispatchEvent(new CustomEvent('chat:switch', {
      detail: { chat_id: chatIdOf(next), from: chatIdOf(previous) },
    }));
    return true;
  }

  /** La chiave della conversazione personale. */
  get personalKey() {
    return UNIFIED_KEY;
  }

  /** Il `chat_id` della conversazione aperta, nella forma che portano i frame.
   *
   *  È il termine di confronto per decidere se un frame appartiene a questa
   *  conversazione: la chiave la conosce solo questo oggetto, e la conversione
   *  è quella di `chatIdOf` — la stessa che usa l'`attach`.
   */
  get currentChatId() {
    return chatIdOf(this.currentKey);
  }

  /** Numero d'ordine del cambio di conversazione in corso.
   *
   *  Sale a ogni `switchTo` che va a effetto, e serve a una domanda sola:
   *  *«questa cosa che ho iniziato prima di un'attesa vale ancora?»*. Aprire
   *  una conversazione dura — un bootstrap, la fetch del thread — e le attese
   *  non sono in fila: due tocchi ravvicinati (`patreon`, poi `bordi` dopo
   *  200 ms) lasciano vincere **chi risponde per ultimo**, che non è chi è
   *  stato toccato per ultimo. Il risultato è la cosa peggiore che questa
   *  vista possa fare: il chip dice `patreon`, la chiave è `bordi`, e il fatto
   *  che l'utente enuncia finisce nel diario dell'altro progetto — dove il
   *  gardener lo promuove in pagina, cioè in modo durevole e non ritirabile.
   *
   *  Chi inizia qualcosa di lungo prende nota della generazione *prima*
   *  dell'attesa e, al ritorno, se non combacia più si ritira senza toccare
   *  nulla: né schermo, né stato condiviso.
   */
  get switchGeneration() {
    return this._switchGen;
  }

  init() {
    if (this._initialized) return;
    this._initialized = true;
    this.ensureAttached();
  }

  /** Attach the shared chat (no-op if already attached; re-attach on reconnect is automatic). */
  ensureAttached() {
    wsManager.attachChat(this.currentKey);
  }

  /** Una pagina di thread, **con lo scope che le appartiene**.
   *
   *  Lo scope torna al chiamante e non si legge da `currentScope`: quel campo
   *  lo scrive chiunque carichi un thread e lo vince l'ultimo che risponde,
   *  quindi la risposta a una conversazione già abbandonata ci lasciava dentro
   *  lo scope sbagliato — ed è da lì che il chip prendeva il nome da mostrare.
   *  Il valore di ritorno appartiene a *questa* chiamata e nessun altro lo può
   *  sovrascrivere.
   *
   *  `stale` dice che nel frattempo la conversazione aperta è cambiata (v.
   *  `switchGeneration`): quel che torna è la storia di un'altra chat e il
   *  chiamante deve buttarla.
   */
  async loadThread(key, limit = 160, before = null) {
    const generation = this._switchGen;
    const data = await api.fetchWebuiThread(key, { limit, before });
    const known = !!data && typeof data === 'object';
    const scope = known ? (data.workspace_scope || null) : null;
    const runStartedAt = known ? (data.run_started_at || null) : null;
    const stale = generation !== this._switchGen;
    // Lo stato condiviso lo aggiorna solo la risposta ancora attuale.
    if (known && !stale) {
      this.currentScope = scope;
      this.runStartedAt = runStartedAt;
    }
    return { thread: data, scope, runStartedAt, stale, generation };
  }
}

export const sessionManager = new SessionManager();
