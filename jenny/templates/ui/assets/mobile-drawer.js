/** Mobile Drawer Manager — sliding panels with backdrop and swipe support. */

export class DrawerManager extends EventTarget {
  constructor() {
    super();
    this.activeDrawer = null;
    // Chi aveva il fuoco quando il drawer si è aperto: ci torna alla chiusura.
    this._lastFocus = null;
    // Vero solo durante il passaggio diretto da un drawer all'altro: non è un
    // ritorno alla vista, quindi lo sfondo resta inerte e il fuoco non torna
    // all'invocante per poi rientrare subito nel drawer nuovo.
    this._swapping = false;
    this.backdrop = document.getElementById('backdrop');

    this.backdrop.addEventListener('click', () => this.closeAll());

    document.querySelectorAll('[data-close]').forEach(btn => {
      btn.addEventListener('click', () => this.closeAll());
    });

    this.setupSwipe();
  }

  setupSwipe() {
    let startY = 0;
    let startX = 0;
    let startTime = 0;

    document.querySelectorAll('.drawer').forEach(drawer => {
      drawer.addEventListener('touchstart', (e) => {
        startY = e.touches[0].clientY;
        startX = e.touches[0].clientX;
        startTime = Date.now();
      }, { passive: true });

      drawer.addEventListener('touchend', (e) => {
        const endY = e.changedTouches[0].clientY;
        const endX = e.changedTouches[0].clientX;
        const deltaY = endY - startY;
        const deltaX = endX - startX;
        const deltaTime = Date.now() - startTime;

        // Only close on fast downward swipe with minimal horizontal drift
        if (deltaY > 50 && deltaTime < 300 && Math.abs(deltaX) < 30) {
          this.closeAll();
        }
      }, { passive: true });
    });
  }

  open(id) {
    const drawer = document.getElementById(`drawer-${id}`);
    if (!drawer) return;

    const previous = this.activeDrawer;
    if (previous && previous !== id) {
      this._swapping = true;
      this.close(previous, false);
      this._swapping = false;
    } else if (!previous) {
      this._lastFocus = document.activeElement;
    }

    drawer.classList.add('open');
    this.backdrop.classList.add('open');
    this.activeDrawer = id;
    this._setContentInert(true);
    // Il fuoco entra nel drawer: senza, Tab ripartirebbe da dove si trovava,
    // cioè dentro il contenuto appena reso inerte — quindi da nessuna parte.
    const entry = drawer.querySelector('.drawer-close') || drawer;
    entry.focus?.();
    this.dispatchEvent(new CustomEvent('open', { detail: id }));
  }

  close(id, dispatch = true) {
    const drawer = document.getElementById(`drawer-${id}`);
    if (drawer) drawer.classList.remove('open');

    if (this.activeDrawer === id) {
      this.activeDrawer = null;
      this.backdrop.classList.remove('open');
      if (!this._swapping) this._releaseContent();
      if (dispatch) {
        this.dispatchEvent(new CustomEvent('close', { detail: id }));
      }
    }
  }

  closeAll() {
    document.querySelectorAll('.drawer').forEach(d => d.classList.remove('open'));
    this.backdrop.classList.remove('open');
    const previous = this.activeDrawer;
    this.activeDrawer = null;
    this._releaseContent();
    if (previous) {
      this.dispatchEvent(new CustomEvent('close', { detail: previous }));
    }
  }

  /** Rende inerte ciò che il drawer copre: le viste dentro `.main`, intestazione
   *  compresa (il drawer parte da top:0 e ne prende il 75%, quindi il titolo e i
   *  suoi pulsanti stanno fisicamente sotto). `inert` toglie fuoco, click e
   *  lettura TalkBack in un colpo solo, senza focus trap fatto a mano.
   *
   *  Fuori restano i drawer stessi, il backdrop — che è il modo primario di
   *  richiudere — e il dock, che vive fuori da `.main`, resta visibile sotto il
   *  drawer e continua a funzionare: renderlo inerte disattiverebbe un comando
   *  che si vede. */
  _setContentInert(on) {
    const main = document.querySelector('.main');
    if (!main) return;
    for (const child of main.children) {
      if (child === this.backdrop || child.classList.contains('drawer')) continue;
      /* La vista sotto la chat aperta sopra di lei (05/10/2026) e' inerte per
         conto suo: chiudere un drawer aperto dalla chat non la risveglia. */
      child.inert = on || child.classList.contains('view-beneath');
    }
  }

  /** Toglie l'inerzia allo sfondo e riporta il fuoco sull'invocante, se è
   *  ancora nel documento: la vista sotto può essere stata ridisegnata mentre
   *  il drawer era aperto, e in quel caso non si sposta niente. */
  _releaseContent() {
    this._setContentInert(false);
    const previous = this._lastFocus;
    this._lastFocus = null;
    if (previous && previous.isConnected) previous.focus?.();
  }

  toggle(id) {
    if (this.activeDrawer === id) {
      this.closeAll();
    } else {
      this.open(id);
    }
  }
}
