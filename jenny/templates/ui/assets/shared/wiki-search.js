/** Motore di ricerca full-text della wiki, lato client.
 *
 *  Riceve l'indice impacchettato che `/api/graph` spedisce insieme al grafo
 *  (v. `jenny/webui/wiki_search.py`) e risponde a ogni carattere digitato senza
 *  toccare la rete. Il vincolo di progetto è uno solo: **il risultato deve
 *  essere già nella forma che serve al disegno**, cioè una `Uint8Array` indicizzata
 *  per posizione del nodo nell'array `nodes` del grafo. Il grafo la legge in un
 *  ciclo O(n) senza costruire `Set` né mappe per keystroke.
 *
 *  Perché è veloce, in ordine di importanza:
 *
 *  1. Gli array numerici arrivano in base64 e diventano TypedArray con una
 *     memcpy, non con centinaia di migliaia di `JSON.parse` di numeri.
 *  2. Unione e intersezione lavorano *sulla maschera*, non su liste ordinate:
 *     un'unione è "scrivi 1 in queste posizioni", un'intersezione è un AND
 *     su n byte. Niente allocazioni, niente merge, niente deduplica.
 *  3. I buffer di lavoro sono due, allocati una volta per indice e riusati a
 *     ogni query: la ricerca non produce spazzatura, quindi non provoca GC
 *     mentre l'utente scrive.
 *
 *  L'ultimo token della query è cercato **per prefisso** (bisezione sul
 *  dizionario ordinato): è ciò che fa accendere i nodi mentre si scrive, senza
 *  aspettare che la parola sia finita.
 */

// Gemello di `_TOKEN_RE` in wiki_search.py. La classe di caratteri è ASCII
// esplicita e non `\w`: in JavaScript `\w` resta ASCII anche col flag `u`,
// mentre in Python è unicode-aware — usarla farebbe divergere in silenzio i due
// tokenizzatori, e il client cercherebbe termini mai scritti nel dizionario.
const TOKEN_RE = /[0-9a-z]+/g;

// Gemello di `_MIN_TOKEN_LEN`. Un token più corto non sta nel dizionario: va
// trattato come "nessun vincolo", non come "zero risultati".
const MIN_TOKEN_LEN = 2;

/** Normalizzazione condivisa col server: NFKD, via i diacritici, minuscolo. */
export function foldText(text) {
  // `\p{M}` (marks) è l'equivalente pratico di `unicodedata.combining()` usato
  // dal server: dopo NFKD è ciò che resta degli accenti, e va via.
  return (text || '')
    .normalize('NFKD')
    .replace(/\p{M}/gu, '')
    .toLowerCase();
}

/** Token indicizzabili di un testo, nell'ordine in cui compaiono.
 *
 *  **Nessun modulo la importa, e non e' morta**: il suo consumatore e' un banco
 *  (`tests/webui/test_wiki_search_client.py::test_the_two_tokenizers_agree`),
 *  che la estrae da qui per confrontarla con `wiki_search.py::tokenize`. Le due
 *  devono spezzare le parole allo stesso modo o la ricerca dal telefono non
 *  trova quel che il server ha indicizzato. Una passata di codice morto che
 *  guardi solo gli `import` la segnala: e' successo il 22/09/2026. */
export function tokenize(text) {
  return foldText(text).match(TOKEN_RE) || [];
}

function decodeBase64(b64) {
  const bin = atob(b64 || '');
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

export class WikiSearchIndex {
  /** @param wire il campo `search` della risposta di /api/graph (o null). */
  constructor(wire) {
    this.version = wire.version;
    this.docCount = wire.docs;
    // I termini arrivano come un'unica stringa \n-separata e già ordinata:
    // `split` costa un'allocazione sola all'avvio e in cambio la bisezione per
    // il range di prefisso è un confronto fra stringhe, non fra offset.
    this.terms = wire.terms ? wire.terms.split('\n') : [];
    const offsets = decodeBase64(wire.offsets);
    this.offsets = new Int32Array(offsets.buffer, offsets.byteOffset, offsets.byteLength >> 2);
    const postings = decodeBase64(wire.postings);
    this.postings = wire.bits === 16
      ? new Uint16Array(postings.buffer, postings.byteOffset, postings.byteLength >> 1)
      : new Int32Array(postings.buffer, postings.byteOffset, postings.byteLength >> 2);
    this.weights = decodeBase64(wire.weights);

    // Scratch riusati a ogni query: l'accumulatore e la maschera del token
    // corrente. Allocarli per keystroke significherebbe due Uint8Array da
    // n byte a ogni tasto, cioè GC nel bel mezzo della digitazione.
    this._acc = new Uint8Array(this.docCount);
    this._cur = new Uint8Array(this.docCount);
    this._scores = new Uint16Array(this.docCount);
  }

  static from(wire) {
    return wire ? new WikiSearchIndex(wire) : null;
  }

  /** Primo indice di termine >= *prefix* (bisezione sul dizionario ordinato). */
  _lowerBound(prefix) {
    let lo = 0;
    let hi = this.terms.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (this.terms[mid] < prefix) lo = mid + 1;
      else hi = mid;
    }
    return lo;
  }

  /** Accende in *mask* i nodi dei termini in `[from, to)`, sommando i punteggi.
   *
   *  Scrivere direttamente nella maschera invece di costruire liste ordinate è
   *  ciò che rende innocua l'espansione di un prefisso larghissimo: un "a"
   *  digitato da solo tocca molte postings, ma ognuna è una scrittura di un
   *  byte, senza deduplica né allocazione. */
  _unionRange(from, to, mask, scores) {
    const { offsets, postings, weights } = this;
    for (let t = from; t < to; t++) {
      const end = offsets[t + 1];
      for (let p = offsets[t]; p < end; p++) {
        const node = postings[p];
        mask[node] = 1;
        if (scores) {
          const next = scores[node] + weights[p];
          scores[node] = next > 0xffff ? 0xffff : next;
        }
      }
    }
  }

  /** Un termine senza postings è *universale* (presente quasi ovunque): il
   *  server ne omette la lista perché non restringe niente. Vale per l'intero
   *  range di prefisso solo se **tutti** i termini del range sono così. */
  _rangeIsUniversal(from, to) {
    return this.offsets[to] === this.offsets[from];
  }

  /** Esegue la query e ritorna `{ mask, scores, count }`, o `null` se la query
   *  non impone alcun vincolo (vuota, o solo termini universali/troppo corti).
   *
   *  `mask` e `scores` sono i buffer interni: validi fino alla query successiva.
   *  Chi deve conservarli ne fa una copia.
   *
   *  Semantica: AND fra i token. L'ultimo token è un prefisso — è quello che si
   *  sta ancora scrivendo — a meno che la query finisca con uno spazio, nel
   *  qual caso anche l'ultimo è considerato completo. */
  query(text) {
    const raw = foldText(text);
    const tokens = raw.match(TOKEN_RE) || [];
    if (!tokens.length) return null;
    const lastIsPrefix = !/[^0-9a-z]$/.test(raw);

    const acc = this._acc;
    const cur = this._cur;
    const scores = this._scores;
    scores.fill(0);
    let constrained = false;

    for (let i = 0; i < tokens.length; i++) {
      const token = tokens[i];
      const isPrefix = lastIsPrefix && i === tokens.length - 1;
      if (!isPrefix && token.length < MIN_TOKEN_LEN) continue;  // fuori dizionario

      const from = this._lowerBound(token);
      let to;
      if (isPrefix) {
        to = from;
        while (to < this.terms.length && this.terms[to].startsWith(token)) to++;
      } else {
        to = this.terms[from] === token ? from + 1 : from;
      }

      if (from === to) {
        // Nessun termine corrisponde: la congiunzione è vuota, e con essa la
        // ricerca. Va distinto dal caso "nessun vincolo" — qui l'utente ha
        // scritto qualcosa che nella wiki non esiste, e deve vederlo.
        acc.fill(0);
        return { mask: acc, scores, count: 0 };
      }
      if (this._rangeIsUniversal(from, to)) continue;  // termine ovunque: non filtra

      cur.fill(0);
      this._unionRange(from, to, cur, scores);

      if (!constrained) {
        acc.set(cur);
        constrained = true;
      } else {
        for (let n = 0; n < acc.length; n++) acc[n] &= cur[n];
      }
    }

    if (!constrained) return null;  // solo termini universali: non è un filtro

    let count = 0;
    for (let n = 0; n < acc.length; n++) if (acc[n]) count++;
    return { mask: acc, scores, count };
  }
}
