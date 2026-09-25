// Guardia di rete lato pagina della sessione di navigazione (browser_* tools).
//
// Kotlin la registra con WebViewCompat.addDocumentStartJavaScript: gira in ogni
// frame http/https **prima** degli script della pagina. Esiste perché
// shouldInterceptRequest (JennyBrowserBridge.sessionClient) vede le richieste
// HTTP ma non le connessioni WebSocket, WebTransport e WebRTC: una pagina
// visitata dall'agente poteva aprire `ws://192.168.1.1` e parlare coi servizi
// della rete di casa senza passare da nessun filtro.
//
// Cosa fa:
// - WebSocket, WebSocketStream e WebTransport: prima di aprire chiede al nativo
//   (`JennyBrowserGuard.blocked`) lo stesso verdetto che vale per l'HTTP — reti
//   private, loopback, link-local, CGNAT, oppure nome irrisolvibile — e se è
//   bloccato solleva SecurityError invece di connettersi;
// - RTCPeerConnection: non si costruisce affatto. L'agente non ha motivo di
//   usare WebRTC, e i candidati ICE sono proprio il modo di raggiungere (e di
//   scoprire) gli indirizzi della rete locale.
//
// Cosa NON fa, e va detto (v. .agent/security.md): non tocca i Worker — dentro
// un Worker `WebSocket` è quello nativo — né i frame in cui questo script non
// viene iniettato (un about:blank appena creato può restituire i costruttori
// originali). Ferma una pagina qualunque, non una costruita apposta contro
// questa guardia. Il verdetto sul nome è quello, in cache, dell'HTTP: la
// connessione vera risolve il nome di nuovo, quindi un DNS rebinding resta
// possibile come per le richieste HTTP.
(function () {
  'use strict';

  // Presi adesso, prima che giri la pagina: una pagina che riscrivesse
  // `JennyBrowserGuard.blocked` dopo non cambia il metodo che si chiama qui.
  var guard = window.JennyBrowserGuard;
  var blocked = guard && guard.blocked;

  function hostOf(url) {
    var parsed;
    try {
      parsed = new URL(String(url), location.href);
    } catch (e) {
      return null; // URL invalido: lo rifiuterà il costruttore nativo
    }
    return parsed.hostname.replace(/^\[|\]$/g, '').toLowerCase();
  }

  function refused(url) {
    var host = hostOf(url);
    if (host === null) return false;
    if (!host) return true;
    // Senza il nativo non c'è verdetto: nel dubbio si blocca, come fa
    // isBlockedHost con un DNS che non risponde.
    if (typeof blocked !== 'function') return true;
    try {
      return !!blocked.call(guard, host);
    } catch (e) {
      return true;
    }
  }

  function refusal(kind) {
    return new DOMException(
      'Jenny: ' + kind + ' to a private or local address refused', 'SecurityError');
  }

  // Sostituisce window[name] con un costruttore che controlla l'URL, tenendo
  // prototipo e costanti di quello vero: `instanceof` e `WebSocket.OPEN`
  // continuano a funzionare. `prototype.constructor` punta al guardiano, o
  // `WebSocket.prototype.constructor` restituirebbe l'originale a chiunque.
  function guardUrlConstructor(name, constants) {
    var Native = window[name];
    if (typeof Native !== 'function') return;
    var Guarded = function (url) {
      if (!new.target) {
        throw new TypeError("Failed to construct '" + name + "': Please use the 'new' operator");
      }
      if (refused(url)) throw refusal(name);
      return Reflect.construct(Native, arguments, new.target);
    };
    Guarded.prototype = Native.prototype;
    constants.forEach(function (k) {
      if (k in Native) Object.defineProperty(Guarded, k, { value: Native[k] });
    });
    try {
      Object.defineProperty(Native.prototype, 'constructor', {
        value: Guarded, writable: true, configurable: true,
      });
    } catch (e) { /* prototipo bloccato: resta la sostituzione del nome */ }
    Object.defineProperty(window, name, { value: Guarded, writable: true, configurable: true });
  }

  function forbidConstructor(name) {
    var Native = window[name];
    if (typeof Native !== 'function') return;
    var Forbidden = function () {
      throw new DOMException('Jenny: ' + name + ' is disabled in this browser', 'NotSupportedError');
    };
    Forbidden.prototype = Native.prototype;
    try {
      Object.defineProperty(Native.prototype, 'constructor', {
        value: Forbidden, writable: true, configurable: true,
      });
    } catch (e) { /* v. sopra */ }
    Object.defineProperty(window, name, { value: Forbidden, writable: true, configurable: true });
  }

  guardUrlConstructor('WebSocket', ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED']);
  guardUrlConstructor('WebTransport', []);
  guardUrlConstructor('WebSocketStream', []);
  forbidConstructor('RTCPeerConnection');
  forbidConstructor('webkitRTCPeerConnection');
})();
