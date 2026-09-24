"""La mascotte flottante si veste del tema scelto nell'app.

Nella finestra flottante non c'è CSS — sono `View` in px — quindi i colori
stavano scritti nel Kotlin: sette costanti esadecimali che erano la palette
`chanel`. Con Synthwave la barra restava avorio sopra un'app rosa, e nessun
compilatore aveva niente da dire.

La regola scelta è la stessa della taglia (v.
``test_mascot_size_contract.py``): la fonte di verità resta la WebUI, che
**spinge** i token calcolati del tema attivo, e il Kotlin non ne ha di propri da
tenere allineati. Qui si prova il giro intero, che nessun compilatore vede:

* la conversione dei colori, eseguita davvero sotto node — è il punto in cui il
  giro si rompe in silenzio, perché ``Color.parseColor`` non legge ``rgba(...)``
  e tre temi su sette scrivono così i bordi;
* i tre anelli del ponte: la chiamata in ``shared/theme.js``, il metodo
  ``@JavascriptInterface`` che la riceve, il metodo del controller che la applica;
* il ripiego del Kotlin, che dev'essere `chanel` e non una palette inventata.
"""

from __future__ import annotations

import re
from pathlib import Path

from support.js_harness import requires_node, run_js

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"
THEME_JS = ASSETS / "shared" / "theme.js"
SPA_CSS = ASSETS / "mobile-style.css"
ANDROID = ROOT / "android/app/src/main/java/com/flagdizero/jenny"


# I sei token che vestono la finestra, nell'ordine in cui viaggiano sul ponte.
TOKENS = ["--surface", "--border-strong", "--text", "--text-faint", "--accent", "--on-accent"]

# Il minimo per importare il modulo fuori dalla WebView: `theme.js` al caricamento
# allinea già barre di sistema e palette, e senza questi finirebbe in errore
# prima di arrivare alle asserzioni.
_SHELL = """
const AppState = {};
globalThis.localStorage = { getItem: () => null, setItem: () => {} };
globalThis.CustomEvent = class { constructor(type, opts) { this.type = type; Object.assign(this, opts || {}); } };
globalThis.document = {
  documentElement: { getAttribute: () => 'chanel', setAttribute: () => {} },
  getElementById: () => null,
};
globalThis.sent = [];
globalThis.window = {
  dispatchEvent: () => {},
  JennyNative: { setFloatingPalette: (...args) => { globalThis.sent.push(args); } },
};
globalThis.getComputedStyle = () => ({
  getPropertyValue: (token) => (globalThis.tokens || {})[token] || '',
});
"""


def _run_js(script: str) -> str:
    source = (
        _SHELL
        + THEME_JS.read_text(encoding="utf-8").replace(
            "import { AppState } from './state.js';", ""
        )
        + "\nimport assert from 'node:assert/strict';\n"
        + script
    )
    return run_js(source)


@requires_node
class TestLaConversione:
    """``argbHex``: da come lo scrive il CSS a come lo legge Android."""

    def test_le_forme_che_i_temi_usano_davvero(self):
        _run_js("""
        // Esadecimale pieno: il caso di --surface in tutti e sette i temi.
        assert.equal(argbHex('#1e1e1e'), '#ff1e1e1e');
        // La forma funzionale: --border-strong di chanel. È il caso che
        // farebbe sollevare Color.parseColor se passasse così com'è.
        assert.equal(argbHex('rgba(244, 241, 234, 0.28)'), '#47f4f1ea');
        assert.equal(argbHex('rgba(244, 241, 234, 0.32)'), '#52f4f1ea');
        assert.equal(argbHex('rgb(20, 20, 20)'), '#ff141414');
        // Forme che il CSS ammette e nessun tema usa oggi: costano due righe.
        assert.equal(argbHex('#abc'), '#ffaabbcc');
        assert.equal(argbHex('#12345678'), '#78123456');
        console.log('ok');
        """)

    def test_un_valore_che_non_si_legge_non_diventa_un_colore(self):
        """Meglio niente che un nero involontario: chi chiama salta la spinta."""
        _run_js("""
        for (const bad of ['', '  ', 'var(--surface)', 'color-mix(in srgb, red, blue)',
                           'rgba(1, 2)', 'papayawhip']) {
          assert.equal(argbHex(bad), '', `'${bad}' non doveva convertirsi`);
        }
        console.log('ok');
        """)

    def test_un_cambio_tema_spinge_sei_colori(self):
        out = _run_js("""
        globalThis.tokens = {
          '--surface': '#2a2139', '--border-strong': '#4d3d6b', '--text': '#f2ecff',
          '--text-faint': '#6a6798', '--accent': '#f92aad', '--on-accent': '#ffffff',
        };
        setTheme('synthwave');
        const last = globalThis.sent[globalThis.sent.length - 1];
        assert.equal(last.length, 6, 'il ponte vuole sei colori');
        assert.ok(last.every(c => /^#[0-9a-f]{8}$/.test(c)), last.join(' '));
        console.log(last.join(' '));
        """)
        assert out.strip().split() == [
            "#ff2a2139", "#ff4d3d6b", "#fff2ecff", "#ff6a6798", "#fff92aad", "#ffffffff"
        ]

    def test_un_token_mancante_non_spinge_una_palette_a_metà(self):
        """Cinque colori nuovi e uno vecchio sono peggio di sei vecchi."""
        _run_js("""
        globalThis.tokens = { '--surface': '#2a2139' };  // gli altri cinque vuoti
        setTheme('synthwave');
        assert.equal(globalThis.sent.length, 0);
        console.log('ok');
        """)


class TestIlPonte:
    """I tre anelli che tengono su il giro, e che nessun compilatore vede."""

    def test_la_webui_spinge_i_sei_token(self):
        source = THEME_JS.read_text(encoding="utf-8")
        block = re.search(r"const FLOATING_TOKENS = \[(.*?)\];", source, re.S)
        assert block, "FLOATING_TOKENS non è più leggibile in shared/theme.js"
        assert re.findall(r"'(--[\w-]+)'", block.group(1)) == TOKENS
        assert "native.setFloatingPalette(" in source
        assert "syncFloatingPalette();" in source

    def test_i_token_spinti_esistono_nel_css(self):
        """Un token rinominato nel CSS non farebbe rumore: `getPropertyValue`
        di una custom property assente torna la stringa vuota."""
        root = re.search(r":root \{(.*?)\n\}", SPA_CSS.read_text(encoding="utf-8"), re.S)
        assert root, "il blocco :root non è più leggibile"
        declared = set(re.findall(r"^\s*(--[\w-]+):", root.group(1), re.M))
        assert set(TOKENS) <= declared, f"token spariti da :root: {set(TOKENS) - declared}"

    def test_il_guscio_riceve_e_il_controller_applica(self):
        main_activity = (ANDROID / "MainActivity.kt").read_text(encoding="utf-8")
        assert "fun setFloatingPalette(" in main_activity
        assert "FloatingOverlayController.setPalette(" in main_activity

        controller = (ANDROID / "FloatingOverlayController.kt").read_text(encoding="utf-8")
        assert "fun setPalette(" in controller
        assert "private fun applyPalette()" in controller

    def test_il_ripiego_del_kotlin_e_il_tema_di_default(self):
        """Il ripiego serve al primo montaggio, prima che la SPA abbia caricato.
        Dev'essere `chanel` **preso dal CSS**, non una palette scelta lì: se
        diverge, la finestra si vede in un modo e un istante dopo in un altro.
        """
        controller = (ANDROID / "FloatingOverlayController.kt").read_text(encoding="utf-8")
        block = re.search(r"private val CHANEL = Palette\((.*?)\n    \)", controller, re.S)
        assert block, "la palette di riserva non è più leggibile"
        kotlin = dict(re.findall(r"(\w+) = 0x([0-9A-Fa-f]{8})", block.group(1)))

        root = re.search(r":root \{(.*?)\n\}", SPA_CSS.read_text(encoding="utf-8"), re.S)
        assert root
        css = dict(re.findall(r"^\s*(--[\w-]+):\s*([^;]+);", root.group(1), re.M))

        for field, token in zip(
            ["surface", "border", "text", "hint", "accent", "onAccent"], TOKENS
        ):
            assert kotlin[field].lower() == _argb(css[token].strip()), (
                f"{field} del ripiego non è più {token} di :root ({css[token].strip()})"
            )


def _argb(value: str) -> str:
    """Il gemello Python di ``argbHex``, per le sole forme che il CSS usa qui."""
    if value.startswith("#"):
        digits = value[1:]
        if len(digits) == 3:
            digits = "".join(c * 2 for c in digits)
        return f"ff{digits}".lower()
    numbers = re.findall(r"[\d.]+", value)
    assert len(numbers) >= 3, f"colore non riconosciuto: {value}"
    alpha = round(float(numbers[3]) * 255) if len(numbers) > 3 else 255
    return f"{alpha:02x}" + "".join(f"{int(float(n)):02x}" for n in numbers[:3])
