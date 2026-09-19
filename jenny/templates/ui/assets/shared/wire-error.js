/** Le parole di un rifiuto del gateway — per tutti e due i gusci.
 *
 *  Un frame `error` porta due campi con due mestieri diversi: `reason` è la
 *  parola per la macchina (un identificatore stabile, sempre presente — v.
 *  `tests/channels/test_error_frame_contract.py`), `detail` è per il log. Le
 *  parole per una persona stanno **qui**, tradotte, e non nel server: il
 *  gateway non sa in che lingua stai leggendo.
 *
 *  Prima non era così e si vedeva: l'officina mostrava `Errore: image_rejected`,
 *  cioè un nome del codice sorgente a chi stava mandando una foto, e la casa non
 *  mostrava niente del tutto.
 *
 *  **Due famiglie, non una.** Alcuni rifiuti vogliono dire «il tuo messaggio non
 *  è entrato», e appartengono al messaggio: la bolla va tolta e quel che avevi
 *  scritto ti torna indietro. Gli altri vogliono dire «qualcosa non ha
 *  funzionato» e sono una riga nel filo. Confonderli significa o buttare via un
 *  messaggio buono, o lasciarti credere che sia partito uno che non è partito.
 *
 *  `t` è un appiglio e non un `import` di `i18n`, per la stessa ragione del
 *  pager: `i18n` costruisce la sua istanza al caricamento e legge
 *  `localStorage`, quindi importarlo renderebbe questo file inesercitabile
 *  fuori da un browser. Le chiavi però le decide questo modulo, ed è quello che
 *  impedisce ai due gusci di chiamare la stessa cosa in due modi.
 */

/* I codici a cui sappiamo dare delle parole. Un codice fuori da questo elenco
   non sparisce: cade sulla frase generica **col codice fra parentesi**, che a
   chi legge dice almeno che non è colpa sua e a chi ripara lascia il filo da
   tirare. */
const KNOWN = new Set([
  'missing_content',
  'malformed',
  'decode',
  'size',
  'too_many_images',
  'too_many_videos',
  'too_many_files',
  'unknown_type',
  'invalid_task_id',
  'invalid_project_name',
]);

/* «Il tuo messaggio non è entrato». */
const SEND_REASONS = new Set([
  'missing_content',
  'malformed',
  'decode',
  'size',
  'too_many_images',
  'too_many_videos',
  'too_many_files',
]);

/* E questo e' il motivo per cui la famiglia non si decide solo dal `reason`:
   ogni rifiuto di un allegato porta `detail: "image_rejected"` **qualunque** sia
   il motivo. Un motivo nuovo inventato domani dal server (un formato che non
   digerisce, un codec) cadrebbe fuori da `SEND_REASONS` e il messaggio non
   tornerebbe indietro — cioe' perderesti quel che avevi scritto per colpa di una
   tabella non aggiornata. Con `detail` la famiglia resta giusta comunque. */
const SEND_DETAILS = new Set(['image_rejected']);

export const WIRE_ERROR_PREFIX = 'common.wireError';

/**
 *  Cosa dire, e a chi appartiene.
 *
 *  @param {{reason?: string, detail?: string}} frame  il frame `error`
 *  @param {(key: string) => string} t                 la traduzione
 *  @returns {{text: string, blocksSend: boolean, code: string}}
 */
export function describeWireError(frame, t) {
  const reason = typeof frame?.reason === 'string' ? frame.reason : '';
  const detail = typeof frame?.detail === 'string' ? frame.detail : '';
  const code = reason || detail;
  const blocksSend = SEND_REASONS.has(reason) || SEND_DETAILS.has(detail);

  if (KNOWN.has(reason)) {
    return { text: t(`${WIRE_ERROR_PREFIX}.${reason}`), blocksSend, code };
  }
  const generic = t(`${WIRE_ERROR_PREFIX}.unknown`);
  return { text: code ? `${generic} (${code})` : generic, blocksSend, code };
}
