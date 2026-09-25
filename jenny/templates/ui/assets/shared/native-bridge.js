/* Il ponte verso il guscio Android, ricomposto in un solo `window.JennyNative`.
 *
 * **Script classico, non un modulo, e caricato in <head> subito dopo
 * bootstrap.js**: i chiamanti leggono `window.JennyNative` già mentre i moduli
 * si costruiscono (l'inset di gesture del cassetto, `onNativeReady`), quindi
 * l'oggetto deve esistere prima che il primo modulo giri.
 *
 * **Perché il nativo arriva da due porte** (v. `installNativeBridges` in
 * MainActivity.kt). Un oggetto di `addJavascriptInterface` compare in OGNI
 * frame della WebView, qualunque sia la sua origine — e questa WebView ospita
 * le cornici delle Jenny App e la vista esterna, cioè HTML che non è nostro.
 * Quando il ponte era uno solo, una di quelle pagine poteva copiare
 * `config.json`, con le chiavi dei provider, nella cartella Download.
 *
 * - `JennyNativeInfo` (addJavascriptInterface): solo letture innocue, sincrone.
 * - `JennyNativePort` (addWebMessageListener): tutto il resto. Chromium lo
 *   inietta solo nei frame dell'origine del gateway, e il Kotlin accetta in più
 *   solo il frame principale. È asincrono: un comando parte e basta, una
 *   domanda torna come Promise.
 *
 * Fuori dal guscio (browser, banco) non c'è nessuna delle due porte e
 * `window.JennyNative` resta indefinito: è così che i chiamanti sanno di non
 * essere su Android, e non va cambiato.
 *
 * I tre elenchi sono lo specchio di `JennyNativeInfo` e di
 * `NativeCommands.dispatch` nel Kotlin: un test li confronta, perché un nome
 * sbagliato da una parte sola è un metodo che non esiste, in silenzio.
 */
(function (win) {
  'use strict';

  /* Sincroni, da `JennyNativeInfo`. Letture che una qualunque cornice può fare
     senza danno: niente che scriva, apra o legga un dato dell'utente. */
  const READS = [
    'getBottomGestureInset',
    'hasHardwareKeyboard',
    'isBatteryExempt',
    'systemUpdatedSinceLastRun',
    'deviceManufacturer',
  ];

  /* Comandi senza risposta: partono e basta, come prima (i chiamanti non
     leggevano il valore di ritorno). */
  const COMMANDS = [
    'setGestureExclusion',
    'clearGestureExclusion',
    'setLauncherUsage',
    'setMascotSize',
    'setFloatingPalette',
    'chatOpened',
    'setThemeBars',
    'exportBackup',
    'importBackup',
    'restartApp',
    'requestBatteryExemption',
  ];

  /* Domande: tornano una Promise col valore che il metodo nativo restituiva.
     Si rifiuta se il nativo ha fallito. */
  const QUERIES = [
    'getLauncherUsage',
    'openFile',
    'shareFile',
    'saveToDownloads',
    'requestExactAlarmPermission',
    'openBatterySettings',
  ];

  const info = win.JennyNativeInfo;
  const port = win.JennyNativePort;
  if (!info && !port) return;

  const bridge = {};

  for (const name of READS) {
    if (info && typeof info[name] === 'function') {
      bridge[name] = (...args) => info[name](...args);
    }
  }

  if (port && typeof port.postMessage === 'function') {
    let seq = 0;
    const waiting = new Map();

    port.addEventListener('message', (event) => {
      let reply;
      try { reply = JSON.parse(event.data); } catch { return; }
      const pending = reply && waiting.get(reply.id);
      if (!pending) return;
      waiting.delete(reply.id);
      if (reply.ok) pending.resolve(reply.r);
      else pending.reject(new Error('native command failed'));
    });

    const send = (payload) => port.postMessage(JSON.stringify(payload));

    for (const name of COMMANDS) {
      bridge[name] = (...args) => { send({ m: name, a: args }); };
    }
    for (const name of QUERIES) {
      bridge[name] = (...args) => new Promise((resolve, reject) => {
        const id = ++seq;
        waiting.set(id, { resolve, reject });
        try {
          send({ id, m: name, a: args });
        } catch (e) {
          waiting.delete(id);
          reject(e);
        }
      });
    }
  }

  win.JennyNative = Object.freeze(bridge);
})(window);
