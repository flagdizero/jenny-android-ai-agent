/** L'host di `onboarding.html`: il wizard del primo avvio e nient'altro.
 *
 *  Il wizard vero e' `onboarding-wizard.js`; qui c'e' quel che un documento
 *  della WebUI deve fare per stare in piedi da solo — credenziale, lingua,
 *  altezza del viewport — e il contratto col guscio Android.
 *
 *  **Il contratto col guscio nativo e' di sei metodi** (v. l'intestazione di
 *  `home-app.js`): `onNativeReady`, `goHome`, `onPackageChanged`,
 *  `handleHardwareBack`, `openChat`, `isChatOnScreen`. Ci sono tutti anche se
 *  qui quasi nessuno fa qualcosa: `MainActivity` chiama `goHome()` senza
 *  guardare se esiste, e un metodo mancante sarebbe un TypeError dentro la sua
 *  `evaluateJavascript`. */

import { api } from './shared/api-client.js';
import { i18n } from './shared/i18n.js';
import { isFirstRun } from './shared/first-run.js';
import { OnboardingController } from './onboarding-wizard.js';
import { prepareExtensions, mountExtensions } from './shared/extensions.js';

/* Oltre il fade del loading nativo (400 ms in `MainActivity.hideLoading`).
   Serve perche' `onNativeReady` arriva una volta sola per WebView: al primo
   `onPageFinished`. Quando ci si arriva dalla casa — il caso normale — quel
   momento e' gia' passato, o passa mentre questo documento sta ancora
   caricando, e la caduta della mini Jenny aspetterebbe per sempre. */
const SHELL_READY_FALLBACK_MS = 500;

class OnboardingApp {
  constructor() {
    this._shellReady = false;
    this._shellReadyCbs = [];
    this.controller = null;
    window.mobileApp = this;
    this._setupViewportHeight();
    this.init();
  }

  async init() {
    try {
      await api.bootstrap();
    } catch (err) {
      console.error('Bootstrap failed:', err);
      api.clientLog('error', 'onboarding.bootstrap', String(err && err.stack || err));
    }

    /* Chi ha gia' i suoi provider qui non ha niente da fare: ci arriva solo
       ricaricando una pagina rimasta aperta, e `onboarding.save` li
       sostituirebbe. Su un «non lo so» si resta — la casa ha appena visto il
       primo avvio, e rimandarla indietro su una lettura fallita farebbe
       rimbalzare i due documenti fra loro. */
    if (await isFirstRun(() => api.getSettings()) === false) {
      api.navigate('/html-mobile/', { replace: true });
      return;
    }

    prepareExtensions();
    await i18n.load(i18n.locale);

    this.controller = new OnboardingController();
    /* Le estensioni prima del primo disegno: il loro gancio e' sul primo passo. */
    await mountExtensions('onboarding', { onboarding: this.controller });
    this.controller.activate();
  }

  /** L'altezza del viewport visibile, in `--vv-height`: con la tastiera su il
   *  form si restringe invece di finirci sotto. Come `setupViewportHeight`
   *  dell'officina, senza la parte sullo scroll della chat. */
  _setupViewportHeight() {
    const root = document.documentElement;
    const setH = () => {
      const h = window.visualViewport?.height;
      // Una pagina caricata a vista nascosta misura 0: scriverlo azzererebbe
      // il documento, e il primo resize vero arriva comunque.
      if (!h) return;
      root.style.setProperty('--vv-height', h + 'px');
      window.scrollTo(0, 0);
    };
    window.visualViewport?.addEventListener('resize', setH);
    setH();
  }

  // ── Il contratto col guscio nativo ──────────────────────────────────

  /** Il loading nativo e' sparito: parte chi aspettava (la mini Jenny). */
  onNativeReady() {
    if (this._shellReady) return;
    this._shellReady = true;
    this._shellReadyCbs.splice(0).forEach((cb) => cb());
  }

  /** Esegue `cb` a pagina visibile. Senza `JennyNative` (un browser normale)
   *  al frame dopo; nel guscio al primo fra `onNativeReady` e il timer. */
  whenShellReady(cb) {
    if (this._shellReady) return cb();
    this._shellReadyCbs.push(cb);
    if (!window.JennyNative) {
      requestAnimationFrame(() => this.onNativeReady());
    } else {
      setTimeout(() => this.onNativeReady(), SHELL_READY_FALLBACK_MS);
    }
  }

  /** Indietro chiude prima il dialog aperto piu' in alto (la passphrase del
   *  ripristino), poi risale di un passo; dal wizard non si esce (v.
   *  `handleBack`). Senza il primo gradino, col dialog aperto la pressione
   *  cambiava il passo sotto di lui. Il dialog si congeda con la semantica di
   *  Esc, come nell'officina (`_dismissTopDialog`): un `cancel` annullabile, e
   *  chi lo rifiuta — il «Restart now» dopo il ripristino — resta aperto. */
  handleHardwareBack() {
    const dialogs = document.querySelectorAll('dialog[open]');
    if (dialogs.length) {
      const top = dialogs[dialogs.length - 1];
      if (top.dispatchEvent(new Event('cancel', { cancelable: true }))) top.close();
      return true;
    }
    return this.controller ? this.controller.handleBack() : true;
  }

  /** Home del launcher: durante il primo avvio non c'e' un altrove. */
  goHome() {
    // Niente da fare: v. sopra.
  }

  /** Il tocco su un avviso: non c'e' ancora una chat, si resta qui. `true` e'
   *  «gestito», cosi' il guscio non ricarica per cercarne una. */
  openChat() {
    return true;
  }

  isChatOnScreen() {
    return false;
  }

  /** Un'app installata o tolta: qui non c'e' un cassetto da aggiornare. */
  onPackageChanged() {
    // Niente da fare: v. sopra.
  }
}

new OnboardingApp();
