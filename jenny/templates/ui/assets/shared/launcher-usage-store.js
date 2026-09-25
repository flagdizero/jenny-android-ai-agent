/** Dove vive il conteggio d'uso del cassetto.
 *
 *  `UsageRanking` (`shared/launcher-rank.js`) prende lo storage dal
 *  costruttore e non sa da dove venga: è puro apposta, e resta puro. Questo
 *  modulo decide **quale** storage passargli, e porta di là il valore vecchio
 *  la prima volta.
 *
 *  **Perché non basta `localStorage`.** Ce l'aveva, ed era il posto sbagliato.
 *  Lo dice un commento di `MainActivity.kt` scritto per tutt'altro motivo — «il
 *  localStorage della WebView non sopravvive al kill (persistenza asincrona di
 *  Chromium), le SharedPreferences sì». Jenny è il launcher del telefono e il
 *  sistema la uccide di routine: il ricordo di quel che apri di più si
 *  sbriciolava da sé, poco alla volta, e un cassetto in ordine sbagliato non
 *  sembra rotto — sembra solo che il ranking non funzioni.
 *
 *  Fuori dall'APK (banco, browser, desktop) il ponte non c'è e si resta su
 *  `localStorage`: lì non c'è nessun kill da temere, e un cassetto che non
 *  ricorda niente sarebbe un peggioramento gratuito.
 *
 *  **Niente `window` a livello di modulo**, per la stessa ragione del pager e
 *  di `wire-error.js`: le dipendenze arrivano come argomenti, così il modulo si
 *  esercita sotto node senza fingere un browser.
 */

/** La chiave, unica e condivisa con `UsageRanking`. */
export const USAGE_KEY = 'launcher-usage';

/** Il ponte c'è e sa fare questo mestiere? Le due funzioni si chiedono
 *  entrambe: un ponte più vecchio dell'app ha la prima e non la seconda, e
 *  scoprirlo al primo salvataggio vorrebbe dire leggere per un giro e poi
 *  perdere tutto. */
export function nativeUsable(native) {
  return typeof native?.getLauncherUsage === 'function'
    && typeof native?.setLauncherUsage === 'function';
}

/** Lo storage appoggiato al ponte nativo, nella forma che `UsageRanking` usa.
 *
 *  **Il ponte risponde in differita.** `getLauncherUsage` sta sulla porta del
 *  nativo che solo la SPA raggiunge (v. `shared/native-bridge.js`): era
 *  sincrono finché il ponte lo vedeva anche ogni iframe — cioè finché una
 *  Jenny App poteva leggere quali app apri e quanto spesso. Ora torna una
 *  Promise, e questo storage la assorbe:
 *
 *  - finché non ha risposto, `getItem` dice `null` e `setItem` non scrive
 *    niente: `UsageRanking` parte vuoto e conta solo gli incrementi;
 *  - quando risponde, `ready` si risolve e `UsageRanking` rilegge e **somma**
 *    gli incrementi al valore vero (`_adoptLoaded`). Scrivere prima avrebbe
 *    sovrascritto mesi d'uso con i due lanci di questo avvio.
 *
 *  Se il ponte non risponde affatto, si resta su `local` (se c'è): male come
 *  prima, non peggio. Una chiave diversa da `USAGE_KEY` non è roba nostra e
 *  non la si inventa: `null`, che `UsageRanking._read` già sa gestire.
 *
 *  @param {any} native il ponte (`getLauncherUsage`/`setLauncherUsage`)
 *  @param {Storage|null} [local] il `localStorage`, per la migrazione e il ripiego
 */
export function nativeStore(native, local = null) {
  let backend = 'loading';   // 'loading' → 'native' | 'local'
  let cache = null;

  const store = {
    ready: null,
    getItem(key) {
      if (key !== USAGE_KEY) return null;
      if (backend === 'local') {
        try { return local?.getItem(key) ?? null; } catch { return null; }
      }
      return cache;
    },
    setItem(key, value) {
      if (key !== USAGE_KEY || backend === 'loading') return;
      if (backend === 'local') {
        try { local?.setItem(key, String(value)); } catch { /* v. sotto */ }
        return;
      }
      cache = String(value);
      try {
        Promise.resolve(native.setLauncherUsage(cache)).catch(() => {});
      } catch {
        /* Come in `UsageRanking._write`: meglio un ordine che non si ricorda di
           questo avvio che un lancio fallito. Qui ci si arriva **dopo** che la
           voce è stata aperta. */
      }
    },
  };

  store.ready = (async () => {
    const outcome = await migrateUsage(native, local);
    if (outcome === 'failed') {
      /* Il ponte c'è ma non risponde. Restare su `localStorage` conserva la
         funzione — male, come prima, ma non peggio di prima. */
      backend = 'local';
      return;
    }
    try {
      cache = (await native.getLauncherUsage()) || null;
      backend = 'native';
    } catch {
      backend = 'local';
    }
  })();

  return store;
}

/** Porta il valore da `localStorage` al ponte, una volta sola.
 *
 *  L'ordine è quello di `save_rules` in `agent/soul_rules.py`: **prima la
 *  verità nuova, poi si toglie la vecchia**, e la seconda mossa solo se la
 *  prima si rilegge. Invertirlo vuol dire che una scrittura fallita cancella
 *  l'unica copia rimasta.
 *
 *  Si toglie davvero, invece di lasciare le due copie a divergere: da qui in
 *  poi il posto è uno. Se il ponte sparisse in futuro, il cassetto ripartirebbe
 *  da zero — che è quel che faceva comunque a ogni kill, cioè il difetto che
 *  questo modulo chiude.
 *
 *  Asincrona perché il ponte lo è. La rilettura dopo la scrittura regge
 *  perché il nativo esegue i comandi in fila su un thread solo: il `set` è
 *  partito prima del `get`, quindi il `get` lo vede.
 *
 *  @returns {Promise<'migrated'|'native-has-data'|'nothing-to-move'|'failed'>}
 */
export async function migrateUsage(native, local) {
  let existing = null;
  try {
    existing = (await native.getLauncherUsage()) || null;
  } catch {
    return 'failed';
  }
  /* Il ponte ha già qualcosa: è lui la verità, e riportarci sopra un
     `localStorage` stantio butterebbe via gli avvii veri di oggi. */
  if (existing) return 'native-has-data';

  let old = null;
  try {
    old = local?.getItem(USAGE_KEY) || null;
  } catch {
    old = null;
  }
  if (!old) return 'nothing-to-move';

  try {
    await native.setLauncherUsage(String(old));
    if ((await native.getLauncherUsage()) !== String(old)) return 'failed';
  } catch {
    return 'failed';
  }
  try {
    local.removeItem(USAGE_KEY);
  } catch {
    /* Il valore buono è già di là. Una copia vecchia che resta è innocua:
       `migrateUsage` non la rileggerà più, perché il ponte adesso ha dei dati. */
  }
  return 'migrated';
}

/** Lo storage da dare a `UsageRanking`, migrazione compresa.
 *
 *  @param {{native?: any, local?: any}} [deps] di suo `window.JennyNative` e
 *         `window.localStorage`; nei banchi, dei finti.
 */
export function usageStore(deps = {}) {
  const native = 'native' in deps
    ? deps.native
    : (typeof window === 'undefined' ? null : window.JennyNative);
  const local = 'local' in deps
    ? deps.local
    : (typeof window === 'undefined' ? null : window.localStorage);

  if (!nativeUsable(native)) return local || null;
  return nativeStore(native, local);
}
