/** Confirm Dialog — reusable modal with backdrop. */

import { i18n } from './i18n.js';

/** Chiude *dialog* e risolve **quando il suo evento `close` e' stato consegnato**.
 *
 * I tre modali di questo file condividono un solo elemento `<dialog>` ciascuno,
 * e `close()` non consegna il proprio evento `close` all'istante: su alcuni
 * motori — la WebView di Android fra questi — arriva come task separato.
 * Risolvere prima che quel task sia stato eseguito lascia che il chiamante apra
 * la *domanda successiva*; a quel punto l'evento e' sentito dai listener del
 * nuovo prompt, che lo leggono come «chiuso dall'utente» e lo annullano.
 *
 * Visto sul telefono il 22/08, e non era un caso di bordo: creare un progetto
 * fa due domande di seguito sullo stesso elemento (nome, poi riga di scope), e
 * la seconda si chiudeva da se' prima di comparire. Dalla UI **non si poteva
 * creare nessun progetto** — sempre e solo il toast «serve una riga».
 *
 * Qui c'era un `setTimeout(…, 0)`, che quell'evento lo consumava a vuoto *se*
 * il task del `close` veniva eseguito prima del timer. Ma sono due task source
 * diverse (DOM manipulation e timers) e l'ordine fra due source HTML non lo
 * fissa: dove il timer vince, il chiamante riapre il dialogo e l'evento
 * arretrato finisce nei listener nuovi, cioe' esattamente il guasto di sopra.
 * Non era una cintura, era una scommessa che su Chromium si vince sempre.
 *
 * Risolvere **dall'evento stesso** toglie la scommessa: il chiamante riprende
 * quando il `close` e' gia' passato, e passa mentre nessuno e' iscritto —
 * `cleanup` ha appena rimosso i propri listener e nessun altro prompt e' aperto.
 */
function closeThenResolve(dialog, resolve, value) {
  // Gia' chiuso: ci siamo arrivati dal suo stesso `close` (`close()` azzera
  // `open` di sincrono e accoda l'evento). Un altro non ne arrivera', e
  // aspettarlo vorrebbe dire non risolvere mai — cioe' il chiamante appeso, che
  // e' peggio del guasto che stiamo togliendo.
  if (!dialog.open) {
    resolve(value);
    return;
  }
  dialog.addEventListener('close', () => resolve(value), { once: true });
  dialog.close();
}

/** Il markup dei tre modali.
 *
 *  **Sta qui e non nel documento** perché i documenti sono due: l'officina e la
 *  casa. Finché a usarli era un guscio solo, viverci dentro andava bene; dal
 *  momento in cui anche la casa crea un quaderno — due prompt, una conferma e a
 *  volte un dettaglio a tre uscite — la seconda copia sarebbe stata una seconda
 *  cosa da tenere allineata per disegnare la stessa finestra.
 *
 *  Gli `id` restano quelli: sono l'interfaccia che le tre funzioni qui sotto
 *  cercano, e un modulo che monta il proprio markup e poi lo ritrova per `id`
 *  non è più involuto di uno che se lo tiene in una variabile — è però
 *  compatibile con chi quegli `id` li conosce già (il pannello subagent, i
 *  fogli di stile, i banchi).
 *
 *  I `data-i18n` servono al giro di traduzioni dell'officina, che cammina sul
 *  documento: per questo il montaggio è **all'import** e non alla prima
 *  apertura — un nodo che compare dopo quel giro resterebbe nella lingua
 *  sbagliata fino al cambio di lingua successivo.
 */
const MARKUP = `
<dialog class="oc-dialog" id="oc-confirm-dialog">
  <div class="oc-dialog-inner">
    <p class="oc-dialog-message" id="oc-confirm-message"></p>
    <div class="oc-dialog-buttons">
      <button class="oc-btn oc-btn-cancel" id="oc-confirm-cancel" data-i18n="dialog.cancel">Cancel</button>
      <button class="oc-btn oc-btn-confirm" id="oc-confirm-ok" data-i18n="dialog.confirm">Confirm</button>
    </div>
  </div>
</dialog>

<dialog class="oc-dialog" id="oc-prompt-dialog">
  <div class="oc-dialog-inner">
    <p class="oc-dialog-message" id="oc-prompt-message"></p>
    <input type="text" class="oc-dialog-input" id="oc-prompt-input" />
    <div class="oc-dialog-buttons">
      <button class="oc-btn oc-btn-cancel" id="oc-prompt-cancel" data-i18n="dialog.cancel">Cancel</button>
      <button class="oc-btn oc-btn-confirm" id="oc-prompt-ok" data-i18n="dialog.confirm">Confirm</button>
    </div>
  </div>
</dialog>

<dialog class="oc-sheet oc-detail" id="oc-detail-dialog">
  <div class="oc-sheet-inner oc-detail-inner">
    <div class="oc-detail-head">
      <h2 class="oc-detail-title" id="oc-detail-title"></h2>
      <button class="oc-detail-close" id="oc-detail-close" type="button" aria-label="Close" data-i18n-aria="common.close"><i class="ti ti-x"></i></button>
    </div>
    <div class="oc-detail-body" id="oc-detail-body"></div>
    <div class="oc-dialog-buttons oc-detail-actions" id="oc-detail-actions"></div>
  </div>
</dialog>`;

/* Una volta sola, e solo se non c'è già: un secondo import non deve duplicare
   nulla, e un documento che se li porta da sé (ce ne fosse uno) vince. */
function mountDialogs() {
  if (typeof document === 'undefined' || !document.body) return;
  if (document.getElementById('oc-confirm-dialog')) return;
  const host = document.createElement('div');
  host.innerHTML = MARKUP;
  while (host.firstElementChild) document.body.appendChild(host.firstElementChild);
}

mountDialogs();

export function confirmDialog(message, okText, cancelText) {
  okText = okText || i18n.t('dialog.confirm');
  cancelText = cancelText || i18n.t('dialog.cancel');
  const dialog = document.getElementById('oc-confirm-dialog');
  if (!dialog) return Promise.resolve(false);
  // Dialog condiviso: una seconda apertura (doppio tap) mentre è già aperto
  // farebbe throware showModal(); si tratta come "annullato".
  if (dialog.open) return Promise.resolve(false);

  const msgEl = document.getElementById('oc-confirm-message');
  const okBtn = document.getElementById('oc-confirm-ok');
  const cancelBtn = document.getElementById('oc-confirm-cancel');

  if (!msgEl || !okBtn || !cancelBtn) return Promise.resolve(false);

  msgEl.textContent = message;
  okBtn.textContent = okText;
  cancelBtn.textContent = cancelText;

  return new Promise((resolve) => {
    let settled = false;
    const cleanup = (value) => {
      if (settled) return;
      settled = true;
      okBtn.removeEventListener('click', onOk);
      cancelBtn.removeEventListener('click', onCancel);
      dialog.removeEventListener('close', onClose);
      dialog.removeEventListener('cancel', onCancel);
      closeThenResolve(dialog, resolve, value);
    };

    const onOk = () => cleanup(true);
    const onCancel = () => cleanup(false);
    const onClose = () => cleanup(false);

    okBtn.addEventListener('click', onOk);
    cancelBtn.addEventListener('click', onCancel);
    dialog.addEventListener('close', onClose);
    dialog.addEventListener('cancel', onCancel);

    dialog.showModal();
  });
}

/** Modale di dettaglio: titolo, corpo scorrevole, azioni in fondo.
 *
 * Risolve con l'`id` dell'azione premuta, o `null` per qualunque forma di
 * chiusura. Chiudersi *sempre* alla scelta è deliberato: su telefono una modale
 * che resta aperta dopo un tap su "Ferma" nasconde proprio il pannello dove
 * l'effetto si vede, e il toast è già la conferma.
 *
 * `bodyHtml` è markup: chi chiama ha già escapato il contenuto (è il pattern del
 * resto della UI, che costruisce le righe come stringhe).
 *
 * Vie d'uscita: bottone X, tap sul backdrop, Esc e gesto Indietro di Android.
 * Le ultime due arrivano gratis dall'evento `cancel` di <dialog> + showModal();
 * il backdrop no — un click sull'elemento dialog che non passa da .oc-dialog-inner
 * è per definizione fuori dal riquadro.
 */
export function detailDialog({ title = '', bodyHtml = '', actions = [] } = {}) {
  const dialog = document.getElementById('oc-detail-dialog');
  if (!dialog) return Promise.resolve(null);
  if (dialog.open) return Promise.resolve(null);

  const titleEl = document.getElementById('oc-detail-title');
  const bodyEl = document.getElementById('oc-detail-body');
  const actionsEl = document.getElementById('oc-detail-actions');
  const closeBtn = document.getElementById('oc-detail-close');
  if (!titleEl || !bodyEl || !actionsEl || !closeBtn) return Promise.resolve(null);

  titleEl.textContent = title;
  bodyEl.innerHTML = bodyHtml;
  bodyEl.scrollTop = 0;
  actionsEl.innerHTML = '';
  for (const action of actions) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'oc-btn ' + (action.variant === 'primary' ? 'oc-btn-confirm' : 'oc-btn-cancel');
    btn.dataset.actionId = action.id;
    // textContent, non innerHTML: le etichette vengono da i18n, ma il canale
    // resta un canale — nessun markup entra da qui.
    btn.textContent = action.label || action.id;
    actionsEl.appendChild(btn);
  }

  return new Promise((resolve) => {
    let settled = false;
    const cleanup = (value) => {
      if (settled) return;
      settled = true;
      actionsEl.removeEventListener('click', onAction);
      closeBtn.removeEventListener('click', onCancel);
      dialog.removeEventListener('click', onBackdrop);
      dialog.removeEventListener('close', onClose);
      dialog.removeEventListener('cancel', onCancel);
      closeThenResolve(dialog, resolve, value);
    };
    const onAction = (e) => {
      const btn = e.target.closest('[data-action-id]');
      if (btn) cleanup(btn.dataset.actionId);
    };
    const onCancel = () => cleanup(null);
    const onClose = () => cleanup(null);
    const onBackdrop = (e) => { if (e.target === dialog) cleanup(null); };

    actionsEl.addEventListener('click', onAction);
    closeBtn.addEventListener('click', onCancel);
    dialog.addEventListener('click', onBackdrop);
    dialog.addEventListener('close', onClose);
    dialog.addEventListener('cancel', onCancel);

    dialog.showModal();
  });
}

/** Prompt con input testuale. Risolve con la stringa inserita, o null se annullato. */
export function promptDialog(message, { placeholder = '', initial = '', okText, cancelText } = {}) {
  okText = okText || i18n.t('dialog.confirm');
  cancelText = cancelText || i18n.t('dialog.cancel');
  const dialog = document.getElementById('oc-prompt-dialog');
  if (!dialog) return Promise.resolve(null);
  if (dialog.open) return Promise.resolve(null);

  const msgEl = document.getElementById('oc-prompt-message');
  const inputEl = document.getElementById('oc-prompt-input');
  const okBtn = document.getElementById('oc-prompt-ok');
  const cancelBtn = document.getElementById('oc-prompt-cancel');
  if (!msgEl || !inputEl || !okBtn || !cancelBtn) return Promise.resolve(null);

  msgEl.textContent = message;
  inputEl.placeholder = placeholder;
  inputEl.value = initial;
  okBtn.textContent = okText;
  cancelBtn.textContent = cancelText;

  return new Promise((resolve) => {
    let settled = false;
    const cleanup = (val) => {
      if (settled) return;
      settled = true;
      okBtn.removeEventListener('click', onOk);
      cancelBtn.removeEventListener('click', onCancel);
      inputEl.removeEventListener('keydown', onKey);
      dialog.removeEventListener('close', onClose);
      dialog.removeEventListener('cancel', onCancel);
      closeThenResolve(dialog, resolve, val);
    };
    const onOk = () => cleanup(inputEl.value);
    const onCancel = () => cleanup(null);
    const onClose = () => cleanup(null);
    const onKey = (e) => { if (e.key === 'Enter') { e.preventDefault(); onOk(); } };

    okBtn.addEventListener('click', onOk);
    cancelBtn.addEventListener('click', onCancel);
    inputEl.addEventListener('keydown', onKey);
    dialog.addEventListener('close', onClose);
    dialog.addEventListener('cancel', onCancel);

    dialog.showModal();
    setTimeout(() => inputEl.focus(), 30);
  });
}
