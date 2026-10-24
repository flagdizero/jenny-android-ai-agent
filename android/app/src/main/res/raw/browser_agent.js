// Motore lato pagina della sessione di navigazione (browser_* tools).
//
// Kotlin lo inietta con evaluateJavascript sostituendo il segnaposto in fondo
// al file con un oggetto JSON. La sostituzione e' testuale e globale, quindi
// il segnaposto deve comparire **una volta sola**: nominarlo qui sopra lo
// farebbe sostituire anche dentro questo commento. Vive nella pagina, quindi `window.__jenny` sopravvive fra una chiamata e
// l'altra ma **muore a ogni navigazione**: è esattamente ciò che rende un
// riferimento vecchio un errore invece di un click sull'elemento sbagliato.
//
// Il tetto sui caratteri si applica **qui**: se il muro di testo attraversa il
// bridge il danno è già fatto — nessuno tronca il risultato di un tool a valle
// (context_governor taglia la cronologia, non la singola risposta).
(function (ARGS) {
  var J = (window.__jenny = window.__jenny || { v: 0, n: 0, refs: {}, prev: null });

  // ---------------------------------------------------------------- visibilità

  // checkVisibility risolve display/visibility/opacity **ereditati**, che è il
  // punto in cui una camminata ingenua sbaglia: getComputedStyle(figlio).display
  // non eredita il `none` di un antenato. Misurato il 29/08 su it.wikipedia.org:
  // senza questo, 2.502 elementi "visibili" di cui la gran parte in sezioni
  // chiuse.
  function visible(el) {
    if (el.getAttribute('aria-hidden') === 'true') return false;
    if (typeof el.checkVisibility === 'function') {
      try {
        if (!el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) return false;
      } catch (e) { /* browser vecchio: si ricade sui controlli sotto */ }
    }
    var r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    return !clipped(el, r);
  }

  // Il secondo modo di essere invisibili: dentro un contenitore che ritaglia
  // (`overflow:hidden` + altezza a zero). È come Wikipedia mobile chiude le
  // sezioni: i figli hanno un rettangolo valido, ma non si vedono.
  function clipped(el, r) {
    var p = el.parentElement, hops = 0;
    while (p && hops < 6) {
      var s;
      try { s = getComputedStyle(p); } catch (e) { return false; }
      if (s.overflow === 'hidden' || s.overflow === 'clip' ||
          s.overflowY === 'hidden' || s.overflowY === 'clip') {
        var pr = p.getBoundingClientRect();
        if (pr.height <= 1 || pr.width <= 1) return true;
        if (r.bottom <= pr.top + 1 || r.top >= pr.bottom - 1) return true;
      }
      p = p.parentElement; hops++;
    }
    return false;
  }

  // ---------------------------------------------------------------- ruolo/nome

  // Solo ruoli su cui si può agire, più le intestazioni (che orientano) e i
  // landmark (che dividono). Tutto il resto — presentation, none, rowgroup,
  // document — è rumore: misurato il 29/08, arrivava fino a un terzo delle righe.
  var ACTIONABLE = {
    link: 1, button: 1, textbox: 1, searchbox: 1, checkbox: 1, radio: 1,
    combobox: 1, listbox: 1, option: 1, slider: 1, spinbutton: 1, switch: 1,
    tab: 1, menuitem: 1, password: 1,
  };
  var LANDMARK = {
    navigation: 1, main: 1, search: 1, form: 1, banner: 1,
    contentinfo: 1, complementary: 1, region: 1,
  };

  function isPassword(el) {
    return el.tagName.toLowerCase() === 'input' &&
      (el.getAttribute('type') || '').toLowerCase() === 'password';
  }

  function role(el) {
    // Un campo password resta `password` qualunque `role` dichiari la pagina:
    // `<input type=password role=textbox>` passava per un textbox, e il divieto
    // di scriverci (che guarda il ruolo) non scattava.
    if (isPassword(el)) return 'password';
    var explicit = el.getAttribute('role');
    if (explicit) {
      var first = explicit.split(/\s+/)[0].toLowerCase();
      if (ACTIONABLE[first] || LANDMARK[first] || first === 'heading') return first;
      return null;
    }
    var t = el.tagName.toLowerCase();
    if (t === 'a') return el.hasAttribute('href') ? 'link' : null;
    if (t === 'button') return 'button';
    if (t === 'summary') return 'button';
    if (t === 'input') {
      var ty = (el.getAttribute('type') || 'text').toLowerCase();
      if (ty === 'hidden') return null;
      if (ty === 'checkbox') return 'checkbox';
      if (ty === 'radio') return 'radio';
      if (ty === 'submit' || ty === 'button' || ty === 'reset' || ty === 'image') return 'button';
      if (ty === 'search') return 'searchbox';
      if (ty === 'password') return 'password';
      return 'textbox';
    }
    if (t === 'textarea') return 'textbox';
    if (t === 'select') return 'combobox';
    if (/^h[1-6]$/.test(t)) return 'heading';
    if (t === 'nav') return 'navigation';
    if (t === 'main') return 'main';
    if (t === 'form') return 'form';
    if (t === 'header') return 'banner';
    if (t === 'footer') return 'contentinfo';
    if (t === 'aside') return 'complementary';
    return null;
  }

  function clean(s) {
    return (s || '').replace(/\s+/g, ' ').trim();
  }

  function accessibleName(el, r) {
    var n = clean(el.getAttribute('aria-label'));
    if (n) return n;
    var lb = el.getAttribute('aria-labelledby');
    if (lb) {
      var p = document.getElementById(lb.split(/\s+/)[0]);
      if (p) { n = clean(p.innerText || p.textContent); if (n) return n; }
    }
    var id = el.getAttribute('id');
    if (id) {
      try {
        var l = document.querySelector('label[for="' + id.replace(/["\\]/g, '') + '"]');
        if (l) { n = clean(l.innerText || l.textContent); if (n) return n; }
      } catch (e) { /* id non selezionabile: si prosegue */ }
    }
    if (el.closest) {
      var cl = el.closest('label');
      if (cl && cl !== el) { n = clean(cl.innerText || cl.textContent); if (n) return n; }
    }
    n = clean(el.getAttribute('placeholder')); if (n) return n;
    n = clean(el.getAttribute('alt')); if (n) return n;
    n = clean(el.getAttribute('title')); if (n) return n;
    // Il valore è un nome solo per i controlli che non sono segreti.
    if (r !== 'password' && el.value && typeof el.value === 'string') {
      n = clean(el.value); if (n) return n;
    }
    n = clean(el.innerText || el.textContent); if (n) return n;
    // Ripieghi per gli elementi senza testo (icone, immagini linkate): un nome
    // brutto orienta comunque, una riga `link ""` no.
    var img = el.querySelector && el.querySelector('img[alt]');
    if (img) { n = clean(img.getAttribute('alt')); if (n) return n; }
    var href = el.getAttribute && el.getAttribute('href');
    if (href) {
      var tail = href.split('#')[0].split('?')[0].replace(/\/+$/, '').split('/').pop();
      if (tail) return '/' + decodeURIComponent(tail).slice(0, 40);
    }
    return '';
  }

  // Il nome del bottone che invia il modulo di *el*, o '' se non c'e' un modulo.
  // Serve alla politica su `press Enter`: un Enter in un campo invia il modulo
  // come un click sul suo bottone, e deve passare dallo stesso lessico dei
  // verbi che costano. Senza bottone, il nome del modulo stesso.
  function formLabel(el) {
    var f = el.form;
    if (!f) return '';
    var btn = f.querySelector(
      'button[type="submit"], button:not([type]), input[type="submit"], input[type="image"]');
    var n = btn ? accessibleName(btn, 'button') : '';
    return n || clean(f.getAttribute('aria-label')) || clean(f.getAttribute('name')) || 'form';
  }

  function state(el, r) {
    var bits = [];
    if (el.getAttribute('aria-expanded')) bits.push('expanded=' + el.getAttribute('aria-expanded'));
    if (el.disabled === true || el.getAttribute('aria-disabled') === 'true') bits.push('disabled');
    if (r === 'checkbox' || r === 'radio' || r === 'switch') {
      var c = el.checked !== undefined ? el.checked : el.getAttribute('aria-checked') === 'true';
      bits.push(c ? 'checked' : 'unchecked');
    }
    if (r === 'combobox' && el.options && el.selectedIndex >= 0) {
      var o = el.options[el.selectedIndex];
      if (o) bits.push('value=' + JSON.stringify(clean(o.text).slice(0, 30)));
    }
    if (r === 'textbox' || r === 'searchbox') {
      var v = clean(el.value);
      if (v) bits.push('value=' + JSON.stringify(v.slice(0, 40)));
    }
    if (r === 'password') bits.push(el.value ? 'filled' : 'empty');
    if (r === 'heading') {
      var lv = el.getAttribute('aria-level') || (/^h([1-6])$/.test(el.tagName.toLowerCase())
        ? el.tagName.charAt(1) : '');
      if (lv) bits.push('level=' + lv);
    }
    return bits;
  }

  // ---------------------------------------------------------------- raccolta

  function collect() {
    var out = [];
    var all = document.body ? document.body.querySelectorAll('*') : [];
    var vh = window.innerHeight || 800;
    for (var i = 0; i < all.length; i++) {
      var el = all[i];
      var r = role(el);
      if (!r) continue;
      if (!visible(el)) continue;
      var rect = el.getBoundingClientRect();
      var nm = accessibleName(el, r);
      if (!nm && !ACTIONABLE[r]) continue;          // landmark/heading muti: rumore
      out.push({
        el: el, role: r, name: nm.slice(0, 80),
        state: state(el, r),
        inView: rect.bottom > 0 && rect.top < vh,
        top: rect.top,
        actionable: !!ACTIONABLE[r],
      });
    }
    return out;
  }

  function lineFor(item, ref) {
    var s = '- ' + item.role;
    if (item.name) s += ' "' + item.name + '"';
    var bits = item.state.slice();
    if (ref) bits.unshift('ref=' + ref);
    if (bits.length) s += ' [' + bits.join(' ') + ']';
    return s;
  }

  // L'identita' di un elemento **non comprende il suo ref**: il ref porta il
  // numero di versione, che cambia a ogni snapshot, quindi confrontare le righe
  // gia' composte fa risultare nuova ogni riga di una pagina ferma. Misurato sul
  // telefono il 29/08: "0 invariate, 72 sparite" su due snapshot identici.
  function keyFor(item) {
    return item.role + '|' + item.name + '|' + item.state.join(',');
  }

  // ---------------------------------------------------------------- snapshot

  function snapshot(args) {
    var maxChars = args.maxChars || 2000;
    var filter = clean(args.filter).toLowerCase();
    var version = args.version;
    // La versione e' quella del **documento**, non della fotografia. Guardare
    // una pagina non la cambia: azzerare i ref a ogni snapshot faceva morire
    // riferimenti ancora buoni, e il modello si ritrovava rifiutato per aver
    // guardato due volte. Misurato il 29/08 su it.m.wikipedia.org: uno snapshot
    // di troppo e il browser_read successivo veniva respinto.
    // La protezione resta: un elemento sostituito sotto non e' piu' connesso al
    // documento, e resolve() lo rifiuta lo stesso.
    if (J.v !== version) { J.v = version; J.refs = {}; J.prev = null; J.n = 0; }

    var items = collect();
    var n = 0;
    var lines = [];
    var keys = [];
    var index = {};
    var used = 0;
    var deferred = {};      // ruolo -> quante righe non mostrate
    var truncated = false;

    function push(item) {
      // Il ref si assegna **dopo** il tetto: allocarlo prima lascia buchi nella
      // numerazione e mette nella mappa elementi che il modello non ha mai visto.
      var probe = lineFor(item, item.actionable ? version + ':e' + J.n : null);
      if (used + probe.length + 1 > maxChars) { truncated = true; return false; }
      var ref = null;
      if (item.actionable) {
        ref = version + ':e' + (J.n++);
        n++;
        J.refs[ref] = item.el;
        // Ruolo e nome tornano anche a parte, non solo dentro il testo: la
        // politica su cosa si puo' cliccare e dove si puo' scrivere sta in
        // Python, dove si puo' testare, ma il nome accessibile lo sa solo la
        // pagina. Questo indice non arriva al modello.
        index[ref] = [item.role, item.name, formLabel(item.el)];
      }
      lines.push(lineFor(item, ref)); keys.push(keyFor(item));
      used += probe.length + 1;
      return true;
    }

    // Con un filtro si cerca: contano gli elementi che portano quel testo, ovunque
    // siano. Senza, si guarda: conta quel che si vede adesso, e il resto si conta
    // invece di elencarlo (e' cio' che tiene una pagina vera dentro il tetto).
    for (var i = 0; i < items.length; i++) {
      var it = items[i];
      // Il filtro guarda il nome **e** il ruolo: chiedere filter="heading" e
      // ricevere le intestazioni e' quello che chiunque si aspetta, e senza
      // questo il modello ci prova comunque e trova il vuoto.
      var wanted = filter
        ? (it.name.toLowerCase().indexOf(filter) >= 0 || it.role === filter)
        : it.inView;
      if (!wanted) { deferred[it.role] = (deferred[it.role] || 0) + 1; continue; }
      if (!push(it)) { deferred[it.role] = (deferred[it.role] || 0) + 1; }
    }

    // Il resto si conta, non si elenca. Elencarlo è quello che porta una pagina
    // vera a decine di migliaia di caratteri (misurato: 102.510 su Wikipedia).
    var rest = [];
    for (var k in deferred) if (deferred[k]) rest.push(deferred[k] + ' ' + k);
    var trailer = '';
    if (rest.length) {
      trailer = (filter ? '… without "' + filter + '" in the name: ' : '… off screen: ') +
        rest.sort().join(', ') +
        (filter ? '.' : ' — use filter="<text>" to reach them, or scroll.');
    }
    if (truncated) {
      trailer = (trailer ? trailer + '\n' : '') +
        '… snapshot truncated at ' + maxChars + ' characters.';
    }

    var text = lines.join('\n') + (trailer ? '\n' + trailer : '');

    // Modalità differenza: si mandano solo le righe nuove. Dopo una navigazione
    // J.prev non c'è (documento nuovo) e si ricade sul pieno, che è corretto.
    // Una ricerca non entra nel confronto in nessuna delle due direzioni: né come
    // termine di paragone (v. sotto), né come cosa da confrontare. Misurato sul
    // telefono: uno snapshot filtrato contro la fotografia intera precedente
    // dichiarava "20 sparite", che erano semplicemente le righe non richieste.
    var diff = null;
    if (args.mode === 'diff' && J.prev && !filter) {
      var before = {};
      for (var b = 0; b < J.prev.length; b++) before[J.prev[b]] = 1;
      var added = [];
      for (var c = 0; c < lines.length; c++) if (!before[keys[c]]) added.push(lines[c]);
      var kept = lines.length - added.length;
      var removed = J.prev.length - kept;
      diff = (added.length ? added.join('\n') : '(nothing new on the page)') +
        '\n… ' + kept + ' unchanged, ' + removed + ' gone.' +
        (trailer ? '\n' + trailer : '');
    }
    // Una fotografia filtrata e' una ricerca, non un ritratto della pagina:
    // tenerla come termine di paragone fa dire alla differenza successiva che
    // sono "sparite" tutte le righe che il filtro non aveva chiesto.
    if (!filter) J.prev = keys;

    return {
      url: location.href,
      title: clean(document.title).slice(0, 100),
      version: version,
      refs: n,
      total: items.length,
      chars: text.length,
      index: index,
      text: diff !== null ? diff : text,
      mode: diff !== null ? 'diff' : 'full',
    };
  }

  // ---------------------------------------------------------------- azioni

  function resolve(ref) {
    if (!ref) return { error: 'missing ref' };
    var v = String(ref).split(':')[0];
    if (String(J.v) !== v) {
      return { error: 'ref "' + ref + '" is from version ' + v + ', the current snapshot is ' +
        J.v + ' — the page has changed, run browser_snapshot again' };
    }
    var el = J.refs[ref];
    if (!el) return { error: 'ref "' + ref + '" is unknown in this snapshot' };
    if (!el.isConnected) {
      return { error: 'ref "' + ref + '" is no longer in the document — run browser_snapshot again' };
    }
    return { el: el };
  }

  function fire(el, type) {
    el.dispatchEvent(new Event(type, { bubbles: true }));
  }

  function act(args) {
    var steps = args.steps || [];
    var results = [];
    var navHint = false;
    for (var i = 0; i < steps.length; i++) {
      var st = steps[i];
      var a = (st.action || '').toLowerCase();
      var r = { i: i, action: a, ok: false };
      try {
        if (a === 'wait') {
          r.ok = true;                       // l'attesa vera la fa Kotlin
        } else if (a === 'scroll') {
          // `amount` sono schermate, non pixel. Il modello passa numeri da pixel
          // (visti 300, 1500, 2500), che senza tetto mandano la pagina a fondo
          // corsa e costano tre chiamate per tornare indietro.
          var screens = Math.max(1, Math.min(10, st.amount || 1));
          var amount = screens * (window.innerHeight || 800) * 0.85;
          if ((st.direction || 'down') === 'up') amount = -amount;
          window.scrollBy(0, amount);
          r.ok = true; r.scrollY = window.scrollY;
        } else if (a === 'press') {
          var target = document.activeElement || document.body;
          var key = st.key || 'Enter';
          // `submit: false` lo mette Python quando non sa (o non deve) far
          // partire il modulo: l'invio si fa allora col click sul bottone, che
          // passa dal controllo sui verbi che costano. Il rifiuto viene **prima**
          // dei tasti: un keydown Enter lo legge anche il JavaScript della
          // pagina, e un gestore che paga all'Enter non aspetta il submit.
          if (key === 'Enter' && target.form && st.submit === false) {
            r.error = 'Enter would submit the form "' + formLabel(target) + '": click its ' +
              'button instead, or repeat the step with "confirm": true if the user said yes.';
            results.push(r); break;
          }
          ['keydown', 'keyup'].forEach(function (t) {
            target.dispatchEvent(new KeyboardEvent(t, { key: key, bubbles: true }));
          });
          if (key === 'Enter' && target.form) {
            target.form.requestSubmit ?
              target.form.requestSubmit() : target.form.submit(); navHint = true;
          }
          r.ok = true;
        } else {
          var res = resolve(st.ref);
          if (res.error) { r.error = res.error; results.push(r); break; }
          var el = res.el;
          if (a === 'click') {
            el.scrollIntoView({ block: 'center' });
            if (el.tagName === 'A' && el.getAttribute('href')) navHint = true;
            if (el.type === 'submit' || el.tagName === 'BUTTON') navHint = true;
            el.click();
            r.ok = true;
          } else if (a === 'type') {
            // Un elemento su cui non si puo' scrivere deve **fallire**, non
            // annuire: `el.value = "..."` su un <a> attacca una proprieta'
            // inventata e non cambia niente. Misurato sul telefono il 29/08 —
            // il modello ci ha provato quattro volte di fila, perche' ogni
            // volta gli rispondevamo "ok".
            var tag = el.tagName.toLowerCase();
            // Anche qui, oltre che in Python: l'indice dei ruoli puo' mancare
            // (il ref di un bridge vecchio), il tipo del campo no.
            if (isPassword(el)) {
              r.error = 'I don\'t type into a password field: the user enters credentials.';
              results.push(r); break;
            }
            var typeable = tag === 'input' || tag === 'textarea' || el.isContentEditable;
            if (!typeable) {
              r.error = 'cannot type into this: ' + (role(el) || tag) +
                ' "' + accessibleName(el, role(el)).slice(0, 40) + '". It takes a textbox or a searchbox: ' +
                'look for one in the snapshot, or click this first if it opens a field.';
              results.push(r); break;
            }
            el.focus();
            el.value = st.text || '';
            fire(el, 'input'); fire(el, 'change');
            // Verifica invece di fidarsi: un campo controllato da un framework
            // puo' rimettersi come prima appena lo si tocca.
            if (String(el.value) !== String(st.text || '')) {
              r.error = 'the field did not keep the text (the page rewrites it)';
              results.push(r); break;
            }
            r.ok = true; r.value = String(el.value).slice(0, 60);
          } else if (a === 'select') {
            if (el.tagName.toLowerCase() !== 'select') {
              r.error = 'not a dropdown: ' + (role(el) || el.tagName.toLowerCase());
              results.push(r); break;
            }
            var want = String(st.value == null ? '' : st.value).toLowerCase();
            var picked = -1;
            for (var o = 0; o < (el.options || []).length; o++) {
              var opt = el.options[o];
              if (String(opt.value).toLowerCase() === want ||
                  clean(opt.text).toLowerCase() === want) { picked = o; break; }
            }
            if (picked < 0) {
              r.error = 'no option "' + st.value + '" in this list';
              results.push(r); break;
            }
            el.selectedIndex = picked;
            fire(el, 'input'); fire(el, 'change');
            r.ok = true; r.selected = clean(el.options[picked].text);
          } else {
            r.error = 'unknown action: ' + a;
            results.push(r); break;
          }
        }
      } catch (e) {
        r.error = String(e && e.message ? e.message : e);
        results.push(r); break;
      }
      results.push(r);
    }
    var failed = results.length && !results[results.length - 1].ok;
    return { results: results, failed: failed, navHint: navHint, done: results.length };
  }

  // ---------------------------------------------------------------- lettura

  // Il testo di una regione. ``innerText`` salta cio' che non si vede, e i
  // siti da telefono chiudono le sezioni a fisarmonica: sulla Wikipedia mobile
  // di un articolo restavano l'introduzione e i titoli (1.014 caratteri,
  // misurato sul telefono). Quando il testo nascosto e' molto piu' del
  // visibile si legge anche quello, da una copia senza script, stili, menu e
  // moduli; altrimenti vale il visibile, che non porta con se' il testo dei
  // banner chiusi.
  var NOT_PROSE = 'script,style,noscript,template,svg,nav,aside,header,footer,form,button,' +
    'select,[aria-hidden="true"],[role="navigation"],[role="dialog"]';

  function readableText(el) {
    var visible = clean(el.innerText || '');
    var copy = el.cloneNode(true);
    var junk = copy.querySelectorAll(NOT_PROSE);
    for (var i = 0; i < junk.length; i++) {
      if (junk[i].parentNode) junk[i].parentNode.removeChild(junk[i]);
    }
    var all = clean(copy.textContent || '');
    return all.length > visible.length * 2 ? all : (visible || all);
  }

  function read(args) {
    var maxChars = args.maxChars || 4000;
    var el = document.body;
    if (args.ref) {
      var res = resolve(args.ref);
      if (res.error) return { error: res.error };
      el = res.el;
    } else {
      var main = document.querySelector('main, [role="main"], article');
      if (main) el = main;
    }
    var txt = readableText(el);
    return {
      url: location.href,
      chars: txt.length,
      truncated: txt.length > maxChars,
      text: txt.slice(0, maxChars),
    };
  }

  // ---------------------------------------------------------------- dispatch

  try {
    if (ARGS.op === 'snapshot') return JSON.stringify(snapshot(ARGS));
    if (ARGS.op === 'act') return JSON.stringify(act(ARGS));
    if (ARGS.op === 'read') return JSON.stringify(read(ARGS));
    return JSON.stringify({ error: 'unknown op: ' + ARGS.op });
  } catch (e) {
    return JSON.stringify({ error: 'JS: ' + String(e && e.message ? e.message : e) });
  }
})(__ARGS__);
