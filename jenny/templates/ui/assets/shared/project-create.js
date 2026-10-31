/** Creare una conversazione nuova: due domande, e cosa fare di ogni risposta.
 *
 *  L'officina la chiama «progetto», la casa la chiama «quaderno», ed è la stessa
 *  cosa: una cartella dentro `wikis/` con una sessione sua. Le regole di questo
 *  giro sono costate care e non possono esistere in due copie — la prima volta
 *  che una delle due venisse corretta, l'altra comincerebbe a mentire.
 *
 *  **Le parole invece sono di chi chiama.** Questo modulo non sa come si chiama
 *  quel che sta creando: riceve `words` (le chiavi i18n da usare) e `t` (il
 *  traduttore), come `ago()` in `conversation-list.js`. È anche la ragione per
 *  cui non importa `i18n`.
 *
 *  Le cinque regole, in ordine di quando mordono:
 *
 *  1. **Due domande, in quest'ordine**: come si chiama, e di cosa si occupa.
 *     La seconda non è un extra. Un progetto senza una riga di scope lascia il
 *     primo turno senza niente su cui appoggiarsi, e uno scope indovinato
 *     dall'agente è peggio di nessuno scope, perché tutto quel che viene
 *     archiviato dopo lo eredita. Annullarla annulla la creazione, e **niente
 *     viene creato su disco prima che entrambe le risposte ci siano**.
 *  2. **Il nome si controlla con la regola del server**, non con una più
 *     stretta: v. `isOpenableProjectName`. Qui è un avviso, non un secondo
 *     cancello.
 *  3. **Un nome già in elenco è un avviso, non un rifiuto.** La stessa cartella
 *     può essere un albero rimasto a metà, che il server *completa*: fermarsi
 *     qui renderebbe irreparabile proprio il caso in cui questo dialogo serve a
 *     riparare. E l'elenco può essere vecchio, perché una lettura fallita non lo
 *     butta via.
 *  4. **`conversation_exists` è un bivio a tre uscite, non una conferma.** Le
 *     tracce di una conversazione stanno fuori da `wikis/`, quindi un nome che
 *     il picker non elenca può portarsi dietro la chat di un progetto
 *     cancellato. «L'avevo cancellato per sbaglio» e «riparto pulito» sono
 *     entrambe legittime, e chiudere senza scegliere non crea niente — che è la
 *     terza, e la più facile da dare per sbaglio se le uscite fossero due.
 *  5. **Cos'è andato storto lo dice il codice, non il messaggio.** `err.message`
 *     viene da un `CommandError` ed è in inglese: va in console. A schermo va la
 *     chiave che corrisponde al codice; un codice sconosciuto vale come un
 *     guasto del gateway. Senza codice l'errore non viene dal server ma dal
 *     trasporto, e quei messaggi sono già localizzati dove nascono — solo quelli
 *     si possono mostrare così come sono.
 */

import { rpc } from './rpc-client.js';
import { escapeHtml, showToast } from './utils.js';
import { confirmDialog, detailDialog, promptDialog } from './dialog.js';
import { isOpenableProjectName } from './conversation-list.js';

/** I codici che `jenny/webui/commands.py::project_create` può produrre.
 *
 *  `bad_request` ne copre più di uno (nome, riga di scope, cartella di mezzo,
 *  scaffolder), ma nome e riga li ha già filtrati il dialogo: quel che resta è
 *  la cartella, e la stringa lo dice.
 */
export const CREATE_ERROR_KEYS = {
  bad_request: 'rejected',
  too_large: 'seedTooLong',
  unavailable: 'wikiOff',
  internal: 'internal',
};

/** Le parole del giro: quel che si crea è un **quaderno**, in casa come in officina. */
export const NOTEBOOK_WORDS = {
  namePrompt: 'scope.newProjectName',
  namePlaceholder: 'scope.newProjectPlaceholder',
  nameHint: 'scope.newProjectHint',
  invalidName: 'scope.invalidName',
  nameTaken: 'scope.nameTaken',
  nameTakenContinue: 'scope.nameTakenContinue',
  seedPrompt: 'scope.newProjectSeed',
  seedPlaceholder: 'scope.newProjectSeedPlaceholder',
  seedRequired: 'scope.seedRequired',
  leftoverTitle: 'scope.leftoverChatTitle',
  leftoverBody: 'scope.leftoverChatBody',
  leftoverBodyNoCount: 'scope.leftoverChatBodyNoCount',
  leftoverKeep: 'scope.leftoverChatKeep',
  leftoverDiscard: 'scope.leftoverChatDiscard',
  created: 'scope.created',
  failed: 'scope.createFailed',
  rejected: 'scope.createRejected',
  seedTooLong: 'scope.createSeedTooLong',
  wikiOff: 'scope.createWikiOff',
  internal: 'scope.createInternal',
};

/** Chiede, crea, e dice com'è andata.
 *
 *  @param words  le chiavi i18n da usare (v. `NOTEBOOK_WORDS`); di norma quelle di default.
 *  @param t      il traduttore: `(key, vars) => string`.
 *  @param known  i nomi già in elenco, per l'avviso della regola 3. Può essere
 *                vecchio o mancante, ed è previsto: non è lui l'ultima parola.
 *  @param initial il nome già scritto nel campo, se c'è (di norma vuoto).
 *  @returns il nome creato, oppure `null` se non è stato creato niente — e
 *           `null` vuol dire *davvero* niente su disco, in tutte le uscite.
 */
export async function createProjectFlow({ words = NOTEBOOK_WORDS, t, known = [], initial = '' }) {
  /* La regola si dice prima (`hint`) e un nome che non la rispetta tiene il
     dialog aperto col testo scritto (`validate`): prima era un toast dopo
     Conferma, e il giro ricominciava da zero. `initial` e' il nome gia'
     scritto — quello cercato nei Quaderni senza trovarlo — e passa dalla
     stessa regola di un nome battuto a mano. */
  const name = await promptDialog(t(words.namePrompt), {
    initial,
    placeholder: t(words.namePlaceholder),
    hint: t(words.nameHint),
    validate: (value) => (isOpenableProjectName(value.trim()) ? null : t(words.invalidName)),
  });
  if (!name) return null;
  const clean = name.trim();

  /* L'avviso **prima** della riga di scope: scriverla per poi vedersi rifiutare
     la creazione è il modo peggiore di scoprirlo. */
  if ((known || []).some((it) => (it?.name ?? it) === clean)) {
    const goOn = await confirmDialog(
      t(words.nameTaken, { name: clean }),
      t(words.nameTakenContinue),
    );
    if (!goOn) return null;
  }

  /* Una riga vuota non annulla piu' la creazione: il dialog resta e lo dice.
     Annullare resta annullare. */
  const seed = await promptDialog(t(words.seedPrompt, { name: clean }), {
    placeholder: t(words.seedPlaceholder),
    validate: (value) => (value.trim() ? null : t(words.seedRequired)),
  });
  if (!seed || !seed.trim()) return null;

  try {
    const first = await rpc.createProject(clean, seed.trim());
    if (first?.status === 'conversation_exists') {
      const count = first?.conversation?.messages;
      const choice = await detailDialog({
        title: t(words.leftoverTitle, { name: clean }),
        bodyHtml: `<p>${escapeHtml(
          count
            ? t(words.leftoverBody, { name: clean, count })
            : t(words.leftoverBodyNoCount, { name: clean }),
        )}</p>`,
        actions: [
          { id: 'keep', label: t(words.leftoverKeep), variant: 'primary' },
          { id: 'discard', label: t(words.leftoverDiscard) },
        ],
      });
      if (!choice) return null;
      await rpc.createProject(clean, seed.trim(), choice);
    }
  } catch (err) {
    console.warn('project.create failed:', err?.code || '(no code)', err?.message);
    const slot = err?.code ? (CREATE_ERROR_KEYS[err.code] || 'internal') : null;
    showToast(
      slot
        ? t(words[slot], { name: clean })
        : t(words.failed, { error: err?.message || '' }),
      'error',
    );
    return null;
  }

  showToast(t(words.created, { name: clean }), 'success');
  return clean;
}
