/** Ricerca e ordinamento del cassetto delle app (passo 3, caselle 3.1/3.3/3.4).
 *
 *  Modulo puro **di proposito**: niente DOM, niente `window`, niente i18n. Sta
 *  in `shared/` e non dentro `mobile-launcher.js` perché è l'unica parte del
 *  cassetto che si può provare senza un telefono — e la casella 3.6 chiede
 *  esattamente questo (v. `tests/webui/test_launcher_rank_client.py`, che lo
 *  esegue sotto node come già fa `wiki-search.js`).
 *
 *  Le due responsabilità sono separate perché hanno vite diverse: il punteggio
 *  dipende solo da (voce, query) e si ricalcola a ogni tasto; l'uso è un dato
 *  che sopravvive ai riavvii e cambia solo quando si apre qualcosa.
 */

/* Pesi. Il nome batte sempre la descrizione, e un attacco batte sempre una
   sottostringa in mezzo: chi digita "tel" vuole "Telefono", non "Impostazioni"
   perché la sua descrizione contiene "controlla il tuo telefono". I valori
   contano solo l'uno rispetto all'altro, e la distanza fra i due gruppi è
   volutamente larga: con due termini, due riscontri deboli nella descrizione
   non devono superare un attacco sul nome. */
const NAME_EXACT = 100;
const NAME_PREFIX = 60;
const NAME_WORD = 40;
const NAME_SUB = 20;
const DESC_PREFIX = 10;
const DESC_WORD = 7;
const DESC_SUB = 4;

/** Minuscolo e senza diacritici: "però" e "pero" cercano la stessa cosa, e chi
 *  scrive da una tastiera fisica senza accenti deve trovare comunque.
 *
 *  Stessa regola di `foldText` in `wiki-search.js` — NFKD, via i segni
 *  combinanti, minuscolo — ma riscritta qui invece di importata: quel modulo si
 *  porta dietro tutto lo smontaggio dell'indice della wiki (`atob`, inflate),
 *  e il cassetto lo pagherebbe all'avvio per quattro righe. */
export function normalizeText(value) {
  return String(value ?? '')
    .normalize('NFKD')
    .replace(/\p{M}/gu, '')
    .toLowerCase();
}

/** La query in termini. Più termini sono in **AND**: "gm mail" trova "Gmail"
 *  solo se entrambi hanno un riscontro, ciascuno anche in un campo diverso. */
export function splitTerms(query) {
  return normalizeText(query).split(/\s+/).filter(Boolean);
}

/** Il termine attacca una parola del campo? Cioè: è all'inizio, oppure subito
 *  dopo un carattere che non sia lettera o cifra. Serve a far vincere "play" su
 *  "Google Play Store" contro un riscontro a metà parola. */
function wordStartIndex(haystack, term) {
  let from = 0;
  for (;;) {
    const at = haystack.indexOf(term, from);
    if (at < 0) return -1;
    if (at === 0 || !/[0-9a-z]/.test(haystack[at - 1])) return at;
    from = at + 1;
  }
}

function fieldScore(haystack, term, exact, prefix, word, sub) {
  if (!haystack) return 0;
  if (haystack === term) return exact;
  if (haystack.startsWith(term)) return prefix;
  if (wordStartIndex(haystack, term) >= 0) return word;
  return haystack.includes(term) ? sub : 0;
}

/** Punteggio di una voce contro i termini già normalizzati.
 *
 *  Ritorna `0` quando **anche un solo** termine non ha riscontro: il chiamante
 *  legge lo zero come "fuori dai risultati". Con nessun termine (campo vuoto)
 *  vale 0 per tutti, e l'ordine lo decidono frequenza e recenza.
 *
 *  @param {{name?: string, description?: string, searchText?: string}} entry
 */
export function scoreEntry(entry, terms) {
  if (!terms.length) return 0;
  const name = normalizeText(entry.name);
  /* `searchText` è il testo secondario cercabile della voce, che non coincide
     sempre con quello mostrato: per una app Android è il nome del pacchetto,
     per una Jenny App rotta è l'errore. Chi costruisce la voce decide; qui si
     cerca e basta. */
  const extra = normalizeText(entry.searchText ?? entry.description);
  let total = 0;
  for (const term of terms) {
    const best = Math.max(
      fieldScore(name, term, NAME_EXACT, NAME_PREFIX, NAME_WORD, NAME_SUB),
      fieldScore(extra, term, DESC_PREFIX, DESC_PREFIX, DESC_WORD, DESC_SUB),
    );
    if (best === 0) return 0;
    total += best;
  }
  return total;
}

/** Chiavi d'uso in `localStorage` (D9).
 *
 *  Il dato non è prezioso: se si perde, l'ordine si riforma in qualche giorno
 *  d'uso. Per questo ogni accesso allo storage è avvolto — una `localStorage`
 *  che esplode (quota piena, modalità privata di certi WebView) deve degradare
 *  a "cassetto in ordine alfabetico", mai a un cassetto che non si apre.
 *
 *  Formato compatto `{ "<key>": [conteggio, ultimoMs] }`: con qualche
 *  centinaio di voci un oggetto per riga costerebbe il triplo dei byte per
 *  informazione identica, e questo valore si riscrive interamente a ogni avvio.
 */
export class UsageRanking {
  /** @param {Storage|null} storage `localStorage`, o un finto nei test.
   *  @param {{storageKey?: string, limit?: number}} [options] */
  constructor(storage, options = {}) {
    this.storage = storage || null;
    this.storageKey = options.storageKey || 'launcher-usage';
    /* Tetto alle chiavi ricordate. Un telefono con 300 app e un utente che le
       apre tutte una volta riempirebbe la riga senza che quel ricordo serva a
       niente: quando si sfora, si buttano le meno recenti. */
    this.limit = options.limit || 300;
    this.data = this._read();
    /* Uno storage che risponde in differita (il ponte nativo, v.
       `shared/launcher-usage-store.js`) espone `ready`. Fino ad allora ha
       detto `null`, quindi `data` contiene solo le aperture di questo avvio:
       quando il valore vero arriva si sommano, invece di perderne uno dei
       due. Con `localStorage` `ready` non c'è e non cambia niente. */
    const ready = this.storage?.ready;
    if (ready && typeof ready.then === 'function') {
      ready.then(() => this._adoptLoaded(), () => {});
    }
  }

  /** Il valore vero è arrivato: rileggilo e sommaci le aperture fatte prima. */
  _adoptLoaded() {
    const early = this.data;
    this.data = this._read();
    if (!early.size) return;
    for (const [key, value] of early) {
      const current = this.get(key);
      this.data.set(key, {
        count: current.count + value.count,
        last: Math.max(current.last, value.last),
      });
    }
    this._write();
  }

  _read() {
    try {
      const raw = this.storage?.getItem(this.storageKey);
      if (!raw) return new Map();
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== 'object') return new Map();
      const map = new Map();
      for (const [key, value] of Object.entries(parsed)) {
        // Il valore ha fatto un giro fuori dal nostro codice (è un file su
        // disco, e i tool dell'agente sanno scrivere): si accetta solo la forma
        // attesa, invece di propagare un NaN dentro il comparatore.
        if (!Array.isArray(value)) continue;
        const count = Number(value[0]);
        const last = Number(value[1]);
        if (!Number.isFinite(count) || !Number.isFinite(last)) continue;
        map.set(key, { count: Math.max(0, Math.trunc(count)), last: Math.max(0, last) });
      }
      return map;
    } catch {
      return new Map();
    }
  }

  _write() {
    if (!this.storage) return;
    if (this.data.size > this.limit) {
      const ordered = [...this.data.entries()].sort((a, b) => b[1].last - a[1].last);
      this.data = new Map(ordered.slice(0, this.limit));
    }
    const plain = {};
    for (const [key, value] of this.data) plain[key] = [value.count, value.last];
    try {
      this.storage.setItem(this.storageKey, JSON.stringify(plain));
    } catch {
      // Meglio un ordine che non si ricorda di questo avvio che un lancio
      // fallito: la voce è già stata aperta quando arriviamo qui.
    }
  }

  /** Uso di una chiave; mai `undefined`, così i comparatori non si difendono. */
  get(key) {
    return this.data.get(key) || { count: 0, last: 0 };
  }

  /** Una voce è stata aperta. La chiave è quella di `launcherEntries()`
   *  (`android:<pkg>` / `jenny:<slug>` / `skill:<name>`): due voci omonime in
   *  spazi diversi hanno chiavi diverse e non si sovrascrivono a vicenda. */
  record(key, now = Date.now()) {
    if (!key) return;
    const current = this.get(key);
    this.data.set(key, { count: current.count + 1, last: now });
    this._write();
  }

  /** Quante voci hanno una storia. Il cassetto lo legge per decidere se il
   *  campo vuoto può davvero intitolarsi "Recenti". */
  get size() {
    return this.data.size;
  }
}

/** Le voci filtrate e ordinate: pertinenza, poi frequenza, poi recenza (3.3).
 *
 *  A campo vuoto la pertinenza è 0 per tutti e l'ordine diventa "quel che usi,
 *  in cima" — con le mai aperte in coda in ordine alfabetico, che è l'unico
 *  ordine sensato per una lista di cui non si sa ancora niente.
 *
 *  Non muta `entries` e non tiene stato: la si può chiamare a ogni tasto.
 *
 *  @param {Array<object>} entries voci da `AppsController.launcherEntries()`
 *  @param {string} query testo grezzo del campo
 *  @param {{get: (key: string) => {count: number, last: number}}} usage
 *  @param {string} [locale] per `localeCompare` (l'ordine alfabetico è di lingua)
 */
export function rankEntries(entries, query, usage, locale) {
  const terms = splitTerms(query);
  const scored = [];
  for (const entry of entries) {
    const score = scoreEntry(entry, terms);
    if (terms.length && score === 0) continue;
    scored.push({ entry, score });
  }
  scored.sort((a, b) => {
    if (a.score !== b.score) return b.score - a.score;
    const ua = usage.get(a.entry.key);
    const ub = usage.get(b.entry.key);
    if (ua.count !== ub.count) return ub.count - ua.count;
    if (ua.last !== ub.last) return ub.last - ua.last;
    const byName = String(a.entry.name).localeCompare(
      String(b.entry.name), locale, { sensitivity: 'base' });
    // Ultimo criterio la chiave: due voci omonime in spazi diversi devono avere
    // un ordine *stabile*, altrimenti si scambiano di posto a ogni ridisegno.
    return byName || String(a.entry.key).localeCompare(String(b.entry.key));
  });
  return scored.map(item => item.entry);
}
