/* Dov'è il fuoco, detto al guscio Android.
 *
 * La pagina sa quale campo ha il fuoco; la tastiera del telefono sa se sta
 * scrivendo in un campo. Li tiene d'accordo Chromium, e a volte perde il colpo:
 * il campo ha il fuoco, si scrive lo stesso, ma la tastiera crede di non avere
 * un campo davanti e spegne i suoi strumenti — la lettera accentata a pressione
 * lunga di Pastiera, sul Titan 2, per prima. Il guscio controlla e rilega
 * (v. `ImeBinding.kt`); questo script gli dice soltanto dov'è il fuoco.
 *
 * Un ascolto solo, sul documento, per ogni campo di ogni guscio (casa, officina,
 * onboarding): non c'è un sito di `focus()` da ricordarsi di avvisare, né oggi
 * né domani.
 *
 * Tre risposte:
 * - `text`: un campo dove si scrive (textarea, input testuale, contenteditable);
 * - `frame`: un iframe — una Jenny App. Lì dentro non si vede, e il guscio non
 *   rilega alla cieca;
 * - `none`: tutto il resto.
 *
 * Si dice **a fine task**, non a ogni evento: un `blur()` seguito da un
 * `focus()` nello stesso giro — il type-ahead, il ritorno da una pagina — è un
 * fuoco solo. E ogni fuoco su un campo si dice di nuovo, anche sullo stesso
 * campo: per il guscio è un fuoco nuovo, con la sua rilegatura a disposizione.
 *
 * E una cura, `window.JennyEditableFocus.refocus()`, che il guscio chiama
 * quando il campo ha il fuoco e la tastiera no: toglie e ridà il fuoco al campo
 * nello stesso giro, col cursore dov'era. È quello che scioglie il disaccordo
 * dentro Chromium (dopo il tasto Home, per esempio: misurato il 10/10/2026, un
 * `restartInput` dal guscio da solo tornava «nessun campo»). Il suo
 * `blur`/`focus` non si dice al guscio: non è un fuoco nuovo, e contarlo
 * ridarebbe al guscio le sue cure, in tondo.
 *
 * Script classico, dopo `native-bridge.js`: legge `window.JennyNative` come lui
 * lo lascia. Fuori dal guscio (browser, banco) non fa niente.
 */
(function (win, doc) {
  'use strict';

  const native = win.JennyNative;
  if (!native || typeof native.setEditableFocus !== 'function') return;

  /* I tipi di input in cui si scrive del testo. Gli altri (checkbox, range,
     colori, date con il loro selettore) non chiamano la tastiera. */
  const TEXT_INPUTS = new Set([
    '', 'text', 'search', 'email', 'url', 'tel', 'password', 'number',
  ]);

  function stateOf(el) {
    if (!el || el === doc.body || el === doc.documentElement) return 'none';
    if (el.tagName === 'IFRAME') return 'frame';
    if (el.isContentEditable) return 'text';
    if (el.disabled || el.readOnly) return 'none';
    if (el.tagName === 'TEXTAREA') return 'text';
    if (el.tagName === 'INPUT') {
      return TEXT_INPUTS.has((el.getAttribute('type') || '').toLowerCase()) ? 'text' : 'none';
    }
    return 'none';
  }

  let last = null;
  let focusedField = false;  // un campo ha preso il fuoco da quando si è detto l'ultimo stato
  let pending = false;

  function flush() {
    pending = false;
    const now = stateOf(doc.activeElement);
    const fresh = now === 'text' && focusedField;
    focusedField = false;
    if (now === last && !fresh) return;
    last = now;
    try { native.setEditableFocus(now); } catch { /* il guscio non risponde: niente da fare */ }
  }

  function schedule() {
    if (pending) return;
    pending = true;
    win.setTimeout(flush, 0);
  }

  let healing = false;  // dentro `refocus`: il suo giro di fuoco non si conta

  doc.addEventListener('focusin', (e) => {
    if (!healing && stateOf(e.target) === 'text') focusedField = true;
    schedule();
  }, true);
  doc.addEventListener('focusout', schedule, true);

  /* Il cursore di un campo di testo, o la selezione di un contenteditable:
     un `blur` li perde, e la cura non deve spostarti. */
  function keepPlace(el) {
    if (typeof el.selectionStart === 'number') {
      const { selectionStart: start, selectionEnd: end, selectionDirection: dir } = el;
      return () => { try { el.setSelectionRange(start, end, dir || 'none'); } catch { /* niente selezione */ } };
    }
    const sel = win.getSelection ? win.getSelection() : null;
    const ranges = [];
    for (let i = 0; sel && i < sel.rangeCount; i++) ranges.push(sel.getRangeAt(i).cloneRange());
    return () => {
      if (!sel) return;
      sel.removeAllRanges();
      for (const r of ranges) sel.addRange(r);
    };
  }

  function refocus() {
    const el = doc.activeElement;
    if (stateOf(el) !== 'text') return false;
    const back = keepPlace(el);
    healing = true;
    try {
      el.blur();
      el.focus({ preventScroll: true });
      back();
    } finally {
      healing = false;
    }
    return true;
  }

  win.JennyEditableFocus = Object.freeze({ refocus });
})(window, document);
