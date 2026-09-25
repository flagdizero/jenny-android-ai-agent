/** La disposizione della mappa di ogni quaderno: dove sta, e chi la tocca.
 *
 *  Gli spilli li scrive e li legge la mappa (`home-map.js`), un trascinamento
 *  alla volta. Ma la chiave e' il **nome del quaderno**, e un nome cambia: fino
 *  al 26/09/2026 ne' il rinomino ne' la cancellazione di un quaderno toccavano
 *  il file. Rinominato, il quaderno perdeva la sua disposizione; cancellato, la
 *  lasciava li', e un quaderno nuovo con lo stesso nome ereditava gli spilli di
 *  pagine che non aveva. Qui ci sono le due mosse che mancavano, e il nome del
 *  file, che cosi' sta in un posto solo.
 *
 *  **Tutte e due sono di cortesia.** Un rinomino riuscito non deve diventare
 *  un rinomino fallito perche' non si e' potuta spostare una disposizione: ogni
 *  errore qui si scrive nel log e basta. E nessuna scrive se non ha letto
 *  davvero il file — la stessa regola di `_loadPins`: riscrivere da una lettura
 *  fallita cancellerebbe le disposizioni di tutti gli altri quaderni.
 *
 *  **Non `localStorage`**, ed e' una misura del repo e non un gusto: il
 *  commento di `MainActivity.kt` che `shared/launcher-usage-store.js` cita dice
 *  che «il localStorage della WebView non sopravvive al kill (persistenza
 *  asincrona di Chromium)». Jenny e' il launcher del telefono e il sistema la
 *  uccide di routine: una disposizione tenuta li' si sbriciolerebbe da sola,
 *  poco alla volta, e una mappa che ogni tanto dimentica non sembra rotta —
 *  sembra che il salvataggio non funzioni, che e' peggio.
 *
 *  Il ponte nativo sarebbe durevole ma ha **un cassetto solo**, ed e' del
 *  cassetto delle app: prendergli la chiave non e' roba nostra.
 *
 *  Quindi nel workspace, che e' dove stanno i quaderni: sopravvive al kill,
 *  alla reinstallazione, e se lo porta dietro un backup. Un file solo per tutti
 *  i quaderni invece di uno dentro `wikis/<name>/`, perche' quella cartella la
 *  decide la config (`wiki.wikis_dir`) e il client non la conosce — cercarla
 *  vorrebbe dire indovinarla. Sotto `.jenny/` perche' e' stato dell'interfaccia
 *  e non roba dell'utente: il gestore file lo nasconde da se' (i pattern
 *  `internal`), quindi non compare fra i suoi file.
 */

import { api } from './api-client.js';
import { rpc } from './rpc-client.js';

export const MAP_LAYOUT_FILE = '.jenny/map-layout.json';

/* Il file com'e' su disco. `null` se non c'e' (nessuna mappa mai toccata:
   niente da spostare); un'eccezione per ogni altro inciampo, che chi chiama
   tratta come «non so, non scrivo». */
async function readLayout() {
  let r;
  try {
    r = await api.readWorkspaceFile(MAP_LAYOUT_FILE);
  } catch (err) {
    if (err?.status === 404) return null;
    throw err;
  }
  const data = JSON.parse(r?.content || '{}');
  if (!data || typeof data !== 'object' || Array.isArray(data)) {
    throw new Error('map layout is not an object');
  }
  return data;
}

/* Riscrive il file con *change* applicata, se cambia qualcosa. */
async function rewrite(change, what) {
  try {
    const data = await readLayout();
    if (!data) return false;
    const next = change({ ...data });
    if (!next) return false;
    await rpc.writeWorkspaceFile(MAP_LAYOUT_FILE, JSON.stringify(next));
    return true;
  } catch (err) {
    console.warn(`map layout: ${what} failed`, err);
    return false;
  }
}

/** Un quaderno rinominato si porta dietro la sua disposizione.
 *
 *  Se sotto il nome nuovo c'erano gia' degli spilli — lasciati da un quaderno
 *  omonimo cancellato prima che la cancellazione li togliesse — non sono suoi:
 *  vince quel che c'era sotto il nome vecchio, o niente. */
export function moveLayoutKey(oldName, newName) {
  if (!oldName || !newName || oldName === newName) return Promise.resolve(false);
  return rewrite((data) => {
    const had = Object.hasOwn(data, oldName);
    if (!had && !Object.hasOwn(data, newName)) return null;
    const pins = data[oldName];
    delete data[oldName];
    if (had) data[newName] = pins;
    else delete data[newName];
    return data;
  }, 'rename');
}

/** Un quaderno cancellato non lascia la sua disposizione a chi verra' dopo. */
export function dropLayoutKey(name) {
  if (!name) return Promise.resolve(false);
  return rewrite((data) => {
    if (!Object.hasOwn(data, name)) return null;
    delete data[name];
    return data;
  }, 'drop');
}
