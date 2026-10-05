/** La casa — il velo: la chat sopra la pagina.
 *
 *  Dal 05/10/2026 Jenny e' la porta della conversazione. Toccata dove la chat
 *  non c'e' — le App, una mini-app, le Impostazioni, la bacheca di un quaderno
 *  — la chat vera sale a tutto schermo, e la pagina sotto resta visibile in
 *  trasparenza: si parla di quel che si sta guardando. Prima c'era la
 *  minichat, una domanda a un turno con la risposta in un fumetto.
 *
 *  Questo modulo e' solo il velo: si mostra, rende intoccabile quel che sta
 *  sotto, e dice quando lo si tocca. Spostarci la chat e' del trasloco
 *  (`home-move.js`), decidere quale conversazione e' del guscio.
 *
 *  **Intoccabile, non nascosto.** Sotto c'e' la pagina di cui stai parlando, e
 *  deve restare li' da guardare; ma un tocco che la attraversasse aprirebbe
 *  una scheda sotto la chat. `inert` toglie tocchi, fuoco e lettore di
 *  schermo in un colpo, e si toglie solo dove lo si e' messo: un elemento gia'
 *  inerte per conto suo (una foto della chat, una pagina spenta) resta com'era.
 */

export class ChatOver {
  /** @param shell      `.home-shell`: i suoi figli sono quel che sta sotto.
   *  @param host       `#home-over`, dove la chat sale.
   *  @param onVeilTap  un tocco sul fondo del velo, non su un messaggio ne' su
   *                    un comando: Jenny sobbalza («chiudi da me»), e il velo
   *                    resta. Chiuderlo con un tocco qualunque vorrebbe dire
   *                    perdere la chat a ogni dito che manca il campo. */
  constructor({ shell, host, onVeilTap } = {}) {
    this.shell = shell || null;
    this.host = host || null;
    this._onVeilTap = onVeilTap || null;
    this._open = false;
    /** Gli elementi resi inerti da qui, e solo quelli. */
    this._inerted = [];
    this.host?.addEventListener('click', (e) => {
      if (this._open && this._isVeil(e.target)) this._onVeilTap?.();
    });
  }

  get isOpen() {
    return this._open;
  }

  /* Il fondo: il velo stesso, o il contenitore della chat e il filo dove non
     c'e' un messaggio sotto il dito. */
  _isVeil(target) {
    if (!target) return false;
    if (target === this.host) return true;
    return target.id === 'home-chat' || target.id === 'home-thread';
  }

  show() {
    if (this._open || !this.host) return;
    this._open = true;
    this.host.hidden = false;
    this.shell?.setAttribute('data-over', '');
    const under = Array.from(this.shell?.children || [])
      .filter((el) => el !== this.host && !el.classList?.contains('jenny-duo'));
    /* La mini-app aperta a tutto schermo sta fuori dalla casa, sul `body`: e'
       sotto anche lei. */
    const app = document.querySelector('.app-frame-overlay');
    if (app) under.push(app);
    for (const el of under) {
      if (el.inert) continue;
      el.inert = true;
      this._inerted.push(el);
    }
  }

  hide() {
    if (!this._open) return;
    this._open = false;
    for (const el of this._inerted) el.inert = false;
    this._inerted = [];
    if (this.host) this.host.hidden = true;
    this.shell?.removeAttribute('data-over');
  }
}
