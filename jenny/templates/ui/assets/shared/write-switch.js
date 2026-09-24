/** Interruttore scrittura / sola lettura, accanto al chip dello scope.
 *
 * Risponde alla seconda metà della stessa domanda del chip — *cosa farà quel
 * che sto per mandare* — e per questo sta nella stessa riga: un messaggio
 * partito credendolo in sola lettura non si ritira, e l'unico momento in cui
 * l'utente può accorgersene è mentre guarda il composer.
 *
 * **Il flag viaggia nel messaggio, non in uno stato sul server.** Il server non
 * lo tiene da nessuna parte: se lo tenesse potrebbe raccontare al client uno
 * stato diverso da quello con cui il messaggio è partito, che è il solo guasto
 * che conta. Qui si tiene solo la *preferenza*, per conversazione, e la si
 * rimanda a ogni invio.
 *
 * Sola lettura vuol dire «non cambia niente sul telefono»: file, download, dati
 * delle mini-app, memoria, promemoria, aggiornamento dell'app. Restano possibili
 * la risposta e l'invio di messaggi — una chat che non può nemmeno avvisarti non
 * è in sola lettura, è muta.
 *
 * **Per questo l'interruttore non aspetta la rete.** Il suo stato è di qui e
 * basta: non c'è niente da leggere dal server, quindi non c'è niente per cui
 * valga la pena restare inerti. `_key` parte già sulla conversazione personale —
 * quella da cui `session-manager.js` riparte sempre — invece di restare `null`
 * fino al primo `syncFromSession`: con `null` il primo tocco cadeva nel vuoto
 * (la preferenza non veniva registrata, l'etichetta non cambiava) proprio nel
 * caso in cui l'utente ha più bisogno di poter dire «non toccare niente», cioè
 * quando il caricamento del thread è appena fallito. E la chiave è quella vera,
 * non un segnaposto: un segnaposto farebbe perdere la preferenza al primo
 * `syncFromSession` riuscito, che è un interruttore che torna indietro da solo.
 */

import { i18n } from './i18n.js';
import { sessionManager } from './session-manager.js';
import { AppState } from './state.js';

export class WriteSwitch {
  constructor() {
    this.el = document.getElementById('write-switch');
    // Chat e onboarding condividono l'index: senza il blocco il modulo non fa
    // nulla invece di sollevare al primo getElementById nullo (come il chip).
    this.enabled = Boolean(this.el);
    // Preferenza per conversazione, in memoria. **Non in localStorage**: la
    // conversazione aperta non sopravvive a un riavvio (`session-manager.js`
    // riparte sempre dalla personale), quindi una preferenza che sopravvive
    // sarebbe una promessa che al riavvio non ha più un soggetto.
    this._byKey = new Map();
    // Mai `null`: v. il docstring. La conversazione aperta al primo disegno è
    // la personale, quindi questa è la chiave giusta anche prima che qualcuno
    // ce lo confermi — e resta giusta se nessuno lo fa mai.
    this._key = sessionManager.personalKey;
    // **Nessun `onChange`.** Ce n'era uno, dichiarato qui e chiamato in
    // `toggle`, che nessuno ha mai montato: il ridisegno che avrebbe servito lo
    // fa già l'iscrizione a `AppState.on('readonlyTurn')` — il chip aggiorna il
    // placeholder da lì, e passare per lo stato invece che per una callback è
    // quel che rende irrilevante l'ordine dei due `syncFromSession`. Un gancio
    // che non serve a nessuno è un secondo modo di far sapere la stessa cosa,
    // cioè un secondo modo di sbagliarla.
  }

  init() {
    if (!this.enabled) return;
    this.el.addEventListener('click', (e) => {
      e.stopPropagation();
      this.toggle();
    });
    this.render();
  }

  /** La conversazione a cui si riferisce l'interruttore da adesso.
   *
   *  Una chiave assente vuol dire la personale, non «nessuna conversazione»:
   *  ripiegare lì tiene l'interruttore operabile in ogni caso.
   */
  syncFromSession(sessionKey) {
    this._key = sessionKey || sessionManager.personalKey;
    this._publish();
    this.render();
  }

  /** `true` se il prossimo messaggio di questa conversazione è in sola lettura. */
  get readonly() {
    return this._byKey.get(this._key) === true;
  }

  toggle() {
    if (!this.enabled) return;
    const next = !this.readonly;
    // Senza guardia: `_key` c'è sempre, e la guardia che c'era qui rendeva il
    // tasto morto proprio quando serviva (v. il docstring).
    this._byKey.set(this._key, next);
    this._publish();
    this.render();
  }

  /** Su `AppState` perché il placeholder del composer lo legge da lì. */
  _publish() {
    if (AppState.readonlyTurn === this.readonly) return;
    AppState.set('readonlyTurn', this.readonly);
  }

  render() {
    if (!this.enabled) return;
    const ro = this.readonly;
    this.el.dataset.mode = ro ? 'readonly' : 'write';
    this.el.setAttribute('aria-pressed', ro ? 'true' : 'false');
    const label = i18n.t(ro ? 'write.readonly' : 'write.write');
    this.el.setAttribute('aria-label', i18n.t('write.change'));
    this.el.setAttribute('title', i18n.t(ro ? 'write.readonlyHint' : 'write.writeHint'));
    const mark = this.el.querySelector('.write-switch-mark');
    if (mark) mark.className = 'write-switch-mark ti ' + (ro ? 'ti-eye' : 'ti-pencil');
    const text = this.el.querySelector('.write-switch-label');
    if (text) text.textContent = label;
  }
}

export const writeSwitch = new WriteSwitch();
