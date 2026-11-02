class KeyboardManager {
  constructor() {
    this.shortcuts = new Map();
    this.init();
  }

  init() {
    document.addEventListener('keydown', (e) => {
      // Don't trigger shortcuts when typing in inputs (except Escape and Mod keys)
      const isInput = e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.isContentEditable;
      const combo = this.getCombo(e);
      const handler = this.shortcuts.get(combo);
      
      if (handler) {
        // Allow Escape and Mod-only shortcuts even in inputs
        if (isInput && e.key !== 'Escape' && !this._isModOnly(combo)) return;
        e.preventDefault();
        handler(e);
      }
    });
  }

  getCombo(e) {
    // Le tastiere fisiche (es. Unihertz Titan via bbkeyboard) possono emettere
    // eventi keydown con `e.key` undefined: senza guard, `.toLowerCase()` lancia
    // e l'handler globale floodava la console con errori non gestiti.
    if (!e.key) return '';
    const parts = [];
    if (e.metaKey || e.ctrlKey) parts.push('mod');
    if (e.shiftKey) parts.push('shift');
    if (e.altKey) parts.push('alt');
    parts.push(e.key.toLowerCase());
    return parts.join('+');
  }

  _isModOnly(combo) {
    return combo === 'mod+,';
  }

  register(combo, handler) {
    this.shortcuts.set(combo, handler);
  }
}

export const keyboard = new KeyboardManager();
