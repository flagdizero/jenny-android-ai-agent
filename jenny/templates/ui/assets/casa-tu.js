/** La casa — «Tu e Jenny».
 *
 *  Le impostazioni di chi la usa, non di chi la costruisce: la tavola
 *  `TuEJenny.dc.html`. Quel che serve a osservare, regolare e riparare resta
 *  in officina, e da qui ci si arriva — dalla scheda in fondo, o tenendo
 *  premuto l'avatar.
 *
 *  **La lingua non c'e', ed e' una decisione presa con una misura.** Quella
 *  riga cambierebbe le scritte dei bottoni, non la lingua in cui Jenny
 *  risponde: quella viene da `SOUL.md`, da `USER.md` e dal modello, e
 *  `config.agents.defaults.language` la scrive l'onboarding una volta sola per
 *  tre messaggi del backend. La sceglie il sistema, che e' cio' che
 *  `i18n.detectLocale()` fa gia' quando nessuno ha scelto. Se in officina
 *  qualcuno ha scelto, quella scelta vale: e' una app sola, non due.
 *  Misure e ragioni in `.agent/casa-tu-e-jenny-plan.md`.
 */

import { i18n } from './shared/i18n.js';
import { THEMES, currentTheme, setTheme } from './shared/theme.js';

/** Le tre tinte di un tema, in un quadrato solo.
 *
 *  Non basta l'accento: fra `chanel` e `pietra` quello e' quasi lo stesso, ed
 *  e' lo sfondo a dire di che tema si tratta. `swatch` porta i tre colori gia'
 *  scelti (fondo, superficie, accento) e qui si mettono in fila.
 */
export function swatchGradient(swatch) {
  const [a, b, c] = swatch;
  return `linear-gradient(135deg, ${a} 0 34%, ${b} 34% 67%, ${c} 67% 100%)`;
}

/** Il nome del tema, per una pastiglia larga 56 px.
 *
 *  Sei nomi su sette cominciano per «Jenny», e in una fila di sette pastiglie
 *  quella parola non distingue niente: occupa il posto di quella che
 *  distingue. Misurato aprendo la stanza — «Jenny Ky…», «Jenny Sti…», «Jenny
 *  Fu…», tre pastiglie diverse che a schermo dicono la stessa cosa.
 *
 *  L'anno di «Synthwave '84» cade per la stessa ragione: e' la coda del nome,
 *  non il nome. Altrove — nelle schede larghe dell'officina — i nomi restano
 *  interi, perche' li' ci stanno.
 */
export function shortThemeName(label) {
  return String(label || '').replace(/^Jenny\s+/, '').replace(/\s+'\d+$/, '');
}

export class CasaTu {
  /** @param onWorkshop  la porta dell'officina: la apre chi sa come si apre.
   *  @param onJenny     la riga che porta da lei.
   *  @param onModel     la riga che porta a chi risponde. */
  constructor({ onWorkshop, onJenny, onModel } = {}) {
    this.el = document.getElementById('casa-tu');
    this.themesEl = document.getElementById('casa-themes');
    this.themeLabel = document.getElementById('casa-theme-label');
    this.themeValue = document.getElementById('casa-theme-value');
    this.themeDesc = document.getElementById('casa-theme-desc');
    this.workshopName = document.getElementById('casa-workshop-name');
    this.workshopHint = document.getElementById('casa-workshop-hint');
    this.versionEl = document.getElementById('casa-version');
    this.jennyLabel = document.getElementById('casa-jenny-label');
    this.jennyValue = document.getElementById('casa-jenny-value');
    this.modelLabel = document.getElementById('casa-model-label');
    this.modelValue = document.getElementById('casa-model-value');
    this._painted = false;

    document.getElementById('casa-workshop')
      ?.addEventListener('click', () => onWorkshop?.());
    document.getElementById('casa-row-jenny')
      ?.addEventListener('click', () => onJenny?.());
    document.getElementById('casa-row-model')
      ?.addEventListener('click', () => onModel?.());
    this.themesEl?.addEventListener('click', (e) => {
      const card = e.target.closest('[data-theme]');
      if (card) this.pickTheme(card.dataset.theme);
    });
  }

  /** La stanza si apre. Quel che sa il server arriva dopo, e da fuori. */
  open() {
    this._paintThemes();
  }

  /** Il numero di versione, quando si sa.
   *
   *  Una versione che non si sa non si scrive: la riga resta vuota, e vuota
   *  non occupa. Non e' un guasto di cui valga la pena parlare a chi sta
   *  scegliendo un tema.
   */
  showVersion(current) {
    if (!current || !this.versionEl) return;
    this.versionEl.textContent = i18n.t('casa.tu.version', { version: current });
    this.versionEl.hidden = false;
  }

  /** Come sta lei, sulla riga che porta da lei: «piccola · flottante». */
  sayJenny(value) {
    if (this.jennyValue) this.jennyValue.textContent = value || '';
  }

  /** Chi risponde, sulla riga che porta a sceglierlo: la marca. */
  sayModel(value) {
    if (this.modelValue) this.modelValue.textContent = value || '';
  }

  applyTranslations() {
    if (this.themeLabel) this.themeLabel.textContent = i18n.t('settings.themeLabel');
    if (this.workshopName) this.workshopName.textContent = i18n.t('casa.workshop');
    if (this.workshopHint) this.workshopHint.textContent = i18n.t('casa.tu.workshopHint');
    if (this.jennyLabel) this.jennyLabel.textContent = i18n.t('casa.jenny.title');
    if (this.modelLabel) this.modelLabel.textContent = i18n.t('casa.model.title');
    /* Il nome del tema non si traduce — «Jenny Kyoto» e' un nome — ma la frase
       che lo racconta si', e cambia con la lingua. */
    this._sayTheme();
  }

  /** Cambia tema, subito. Nessuna conferma: si vede, ed e' la conferma. */
  pickTheme(id) {
    const theme = setTheme(id);
    this._markTheme();
    this._sayTheme();
    return theme;
  }

  /* Le pastiglie si disegnano una volta: i sette temi non cambiano mentre
     guardi, e ridisegnarle a ogni tocco butterebbe via lo scorrimento di lato
     — il tema scelto potrebbe essere il settimo. */
  _paintThemes() {
    if (this._painted || !this.themesEl) {
      this._markTheme();
      this._sayTheme();
      return;
    }
    const frag = document.createDocumentFragment();
    for (const theme of THEMES) frag.appendChild(this._themeCard(theme));
    this.themesEl.appendChild(frag);
    this._painted = true;
    this._markTheme();
    this._sayTheme();
  }

  _themeCard(theme) {
    const el = document.createElement('button');
    el.type = 'button';
    el.className = 'casa-theme';
    el.dataset.theme = theme.id;
    el.setAttribute('role', 'radio');

    const swatch = document.createElement('span');
    swatch.className = 'casa-theme-swatch';
    swatch.style.background = swatchGradient(theme.swatch);
    el.appendChild(swatch);

    const name = document.createElement('span');
    name.className = 'casa-theme-name';
    name.textContent = shortThemeName(theme.label);
    el.appendChild(name);
    return el;
  }

  /* Quale e' acceso. `aria-checked` accanto alla classe, perche' l'anello lo
     vede solo chi guarda. */
  _markTheme() {
    if (!this.themesEl) return;
    const now = currentTheme().id;
    for (const card of this.themesEl.children) {
      const on = card.dataset.theme === now;
      card.classList.toggle('is-on', on);
      card.setAttribute('aria-checked', String(on));
    }
  }

  /* Il nome del tema acceso, e la frase che lo racconta: sono quelle
     dell'officina, gia' tradotte, e dire la stessa cosa con altre parole
     vorrebbe dire tenerne allineate due. */
  _sayTheme() {
    const theme = currentTheme();
    if (this.themeValue) this.themeValue.textContent = theme.label;
    if (this.themeDesc) this.themeDesc.textContent = i18n.t(`themes.${theme.id}.desc`);
  }

}
