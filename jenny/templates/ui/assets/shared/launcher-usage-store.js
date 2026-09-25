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
 *  Una chiave diversa da `USAGE_KEY` non è roba nostra e non la si inventa: il
 *  ponte ha un cassetto solo. Tornare `null` è la risposta onesta, ed è anche
 *  quella che `UsageRanking._read` già sa gestire.
 */
export function nativeStore(native) {
  return {
    getItem(key) {
      if (key !== USAGE_KEY) return null;
      try {
        return native.getLauncherUsage() || null;
      } catch {
        return null;
      }
    },
    setItem(key, value) {
      if (key !== USAGE_KEY) return;
      try {
        native.setLauncherUsage(String(value));
      } catch {
        /* Come in `UsageRanking._write`: meglio un ordine che non si ricorda di
           questo avvio che un lancio fallito. Qui ci si arriva **dopo** che la
           voce è stata aperta. */
      }
    },
  };
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
 *  @returns {'migrated'|'native-has-data'|'nothing-to-move'|'failed'}
 */
export function migrateUsage(native, local) {
  let existing = null;
  try {
    existing = native.getLauncherUsage() || null;
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
    native.setLauncherUsage(String(old));
    if (native.getLauncherUsage() !== String(old)) return 'failed';
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
  if (migrateUsage(native, local) === 'failed') {
    /* Il ponte c'è ma non risponde. Restare su `localStorage` conserva la
       funzione — male, come prima, ma non peggio di prima. */
    return local || null;
  }
  return nativeStore(native);
}
