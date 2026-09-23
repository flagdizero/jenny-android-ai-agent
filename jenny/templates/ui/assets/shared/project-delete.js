/** Cancellare un progetto: la domanda, la chiamata, l'esito.
 *
 * **Perché è un modulo e non un metodo del file manager.** Un progetto si crea
 * dal chip dello scope, sopra il composer, e fino alla 0.9.x si cancellava solo
 * dal file manager della tab Workspace — dopo aver saputo che i progetti vivono
 * in `wikis/`. Chi ne aveva creato uno per sbaglio non trovava la strada
 * indietro: è la seconda metà della issue #11, ed è la stessa risposta della
 * prima — l'azione va dove sta il suo oggetto. Ora i chiamanti sono due, e il
 * flusso vive in uno solo.
 *
 * **Non importa `scope-chip.js`**, di proposito: uno dei due chiamanti *è* il
 * chip, e importarlo qui chiuderebbe un ciclo. Quel che segue la cancellazione
 * (uscire dallo scope, ridisegnare un elenco, tornare all'explorer) è di chi
 * chiama e cambia da chiamante a chiamante; quel che è comune, e va detto allo
 * stesso modo dovunque, è la domanda: quante conversazioni si porta via.
 */

import { api } from './api-client.js';
import { rpc } from './rpc-client.js';
import { showToast } from './utils.js';
import { confirmDialog } from './dialog.js';
import { i18n } from './i18n.js';

/** Le frasi del giro, come chiavi i18n.
 *
 *  Il giro è uno e non sa come si chiami quel che cancella: in officina è un
 *  *progetto*, in casa un *quaderno*. Stessa soluzione di `PROJECT_WORDS` in
 *  `project-create.js`: chi chiama passa le sue parole, le regole restano qui.
 */
export const PROJECT_DELETE_WORDS = {
  confirm: 'workspace.deleteProjectConfirm',
  confirmWithChat: 'workspace.deleteProjectConfirmWithChat',
  failed: 'workspace.deleteProjectFailed',
};

/** Chiede conferma e cancella *name*. Ritorna `true` solo se è sparito davvero.
 *
 *  `false` copre due casi che al chiamante interessano allo stesso modo — ha
 *  detto di no, oppure il server ha rifiutato — perché in entrambi il progetto
 *  c'è ancora e non va tolto da nessun elenco. L'errore lo dice il toast, qui.
 *
 *  @param words  le chiavi i18n da usare (v. `PROJECT_DELETE_WORDS`).
 */
export async function deleteProjectFlow(name, words = PROJECT_DELETE_WORDS) {
  if (!name) return false;

  let described = null;
  try {
    described = await api.describeProject(name);
  } catch (err) {
    // Non sapere quante conversazioni si porta via non deve impedire di
    // cancellare: si chiede con la domanda breve. Fallire *chiuso* qui vorrebbe
    // dire che un gateway lento rende incancellabile un progetto.
    console.warn('project describe failed:', err);
  }
  const messages = described?.conversation?.messages;
  const question = messages
    ? i18n.t(words.confirmWithChat, { name, count: messages })
    : i18n.t(words.confirm, { name });
  if (!(await confirmDialog(question))) return false;

  try {
    await rpc.deleteProject(name);
  } catch (err) {
    console.warn('project.delete failed:', err?.code || '(no code)', err?.message);
    showToast(i18n.t(words.failed, { name }), 'error');
    return false;
  }
  return true;
}
