"""La bolla della chat flottante è quella della chat dell'app.

Fino al 13/10/2026 la bolla dell'utente nella finestra flottante era
`--accent` con `--on-accent` sopra, e la chat dell'app la veste con
`--bubble-user-*` e `--radius-bubble`: in cinque temi su sette un'altra cosa
(Synthwave: rosa più scuro, testo bianco, alone; Kyoto: trasparente col bordo;
Sticker: bordo bianco e ombra sfalsata; Y2K: una sfumatura). Adesso la WebUI
spinge quei token, tradotti da ``bubbleStyle`` (``shared/theme.js``) in una
forma che il Kotlin legge senza un parser CSS.

Qui si prova:

* la traduzione, eseguita sotto node sui token **veri** dei sette temi;
* che un valore che non si legge non mandi mezza bolla;
* i tre anelli del ponte, che nessun compilatore vede;
* il ripiego del Kotlin, che dev'essere il tema di default preso dal CSS.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from support import theme_tokens
from support.js_harness import requires_node, run_js
from support.kotlin_source import read_source

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"
THEME_JS = ASSETS / "shared" / "theme.js"
BRIDGE_JS = ASSETS / "shared" / "native-bridge.js"
ANDROID = ROOT / "android/app/src/main/java/com/flagdizero/jenny"

TOKENS = [
    "bubble-user-bg", "bubble-user-text", "bubble-user-border",
    "bubble-user-shadow", "radius-bubble",
]

# Il minimo per importare `theme.js` fuori dalla WebView (v.
# `test_floating_palette_contract.py`), più il registro di quel che il ponte riceve.
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
  JennyNative: { setFloatingBubble: (json) => { globalThis.sent.push(json); } },
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


def _styles() -> dict[str, dict | None]:
    """`bubbleStyle` di ciascun tema, sui token risolti come li risolve il browser."""
    themes = {
        theme: {f"--{t}": theme_tokens.tokens(theme)[t] for t in TOKENS}
        for theme in theme_tokens.themes()
    }
    out = _run_js(f"""
    const themes = {json.dumps(themes)};
    const out = {{}};
    for (const [id, tokens] of Object.entries(themes)) out[id] = bubbleStyle(t => tokens[t]);
    console.log(JSON.stringify(out));
    """)
    return json.loads(out)


@requires_node
class TestTheTranslation:
    def test_every_theme_has_a_bubble_the_shell_can_draw(self):
        """Un tema che non si traduce lascerebbe la bolla del tema di prima, in
        silenzio: tutti e sette devono passare."""
        styles = _styles()
        assert len(styles) >= 7
        assert not [theme for theme, style in styles.items() if style is None]

    def test_the_themes_that_differ_from_the_accent(self):
        styles = _styles()
        assert styles["synthwave"] == {
            "fill": ["#ffd61a8f"], "text": "#ffffffff", "stroke": None,
            "shadow": {"dx": 0, "dy": 0, "blur": 16, "color": "#80d61a8f"},
            "radii": [14, 14, 4, 14],
        }
        # Trasparente: il Kotlin ci mette la superficie, il bordo resta.
        assert styles["kyoto"]["fill"] == ["#00000000"]
        assert styles["kyoto"]["stroke"] == {"width": 1, "color": "#ff4a443b"}
        # Bordo bianco e ombra piena sfalsata; un raggio solo per tutti e quattro.
        assert styles["sticker"]["stroke"] == {"width": 2.5, "color": "#ffffffff"}
        assert styles["sticker"]["shadow"] == {"dx": 4, "dy": 4, "blur": 0, "color": "#66000000"}
        assert styles["sticker"]["radii"] == [14, 14, 14, 14]
        # La sfumatura, e l'ombra esterna invece del riflesso `inset`.
        assert styles["y2k"]["fill"] == ["#ffffa7dd", "#fff56ab5"]
        assert styles["y2k"]["shadow"] == {"dx": 0, "dy": 4, "blur": 10, "color": "#59f56ab5"}

    def test_what_it_cannot_draw_it_does_not_send(self):
        """Meglio la bolla di prima, intera, che una mezza."""
        _run_js("""
        const good = {
          '--bubble-user-bg': '#d61a8f', '--bubble-user-text': '#ffffff',
          '--bubble-user-border': 'none', '--bubble-user-shadow': 'none',
          '--radius-bubble': '14px',
        };
        assert.ok(bubbleStyle(t => good[t]));
        for (const [token, bad] of [
          ['--bubble-user-bg', 'radial-gradient(#fff, #000)'],
          ['--bubble-user-bg', 'linear-gradient(90deg, #fff, #000)'],
          ['--bubble-user-border', '2px dashed #fff'],
          ['--bubble-user-shadow', '0 0 1em #fff'],
          ['--radius-bubble', '14px / 8px'],
          ['--bubble-user-text', ''],
        ]) {
          const tokens = { ...good, [token]: bad };
          assert.equal(bubbleStyle(t => tokens[t]), null, `${token}: ${bad}`);
        }
        console.log('ok');
        """)

    def test_a_theme_change_pushes_the_bubble(self):
        out = _run_js("""
        globalThis.tokens = {
          '--bubble-user-bg': '#d61a8f', '--bubble-user-text': '#ffffff',
          '--bubble-user-border': 'none', '--bubble-user-shadow': '0 0 16px rgba(214, 26, 143, 0.5)',
          '--radius-bubble': '14px 14px 4px 14px',
        };
        setTheme('synthwave');
        console.log(globalThis.sent[globalThis.sent.length - 1]);
        """)
        assert json.loads(out)["fill"] == ["#ffd61a8f"]


class TestTheBridge:
    def test_the_webui_sends_it_where_it_sends_the_palette(self):
        source = THEME_JS.read_text(encoding="utf-8")
        assert "native.setFloatingBubble(JSON.stringify(style))" in source
        assert source.count("syncFloatingBubble();") == 2, "al cambio di tema e al caricamento"
        assert "'setFloatingBubble'" in BRIDGE_JS.read_text(encoding="utf-8")

    def test_the_shell_receives_and_the_controller_applies(self):
        main_activity = read_source(ANDROID / "MainActivity.kt")
        assert '"setFloatingBubble" -> FloatingOverlayController.setBubbleStyle(' in main_activity
        controller = read_source(ANDROID / "FloatingOverlayController.kt")
        assert "fun setBubbleStyle(json: String)" in controller
        assert "parseBubble(prefs.getString(PREF_BUBBLE, null))" in controller, (
            "la bolla deve valere dal primo montaggio, come la palette"
        )

    def test_a_new_palette_repaints_the_bubbles_too(self):
        """Le bolle portano superficie, bordo e colori del markdown della
        palette: un cambio di tema che toccasse solo lei le lasciava vecchie."""
        controller = read_source(ANDROID / "FloatingOverlayController.kt")
        body = controller[controller.index("private fun applyPalette()"):]
        body = body[: body.index("\n    }\n")]
        assert "renderHistory()" in body


@requires_node
def test_the_kotlin_fallback_is_the_default_theme() -> None:
    """Il ripiego vale al primo montaggio, prima che la SPA abbia caricato: dev'essere
    la bolla del tema di default **presa dal CSS**, come la palette."""
    controller = read_source(ANDROID / "FloatingOverlayController.kt")
    block = re.search(r"private val DEFAULT_BUBBLE = BubbleStyle\((.*?)\n    \)", controller, re.S)
    assert block, "la bolla di riserva non è più leggibile"
    body = block.group(1)
    default = re.search(r"DEFAULT_THEME = '([a-z0-9]+)'", THEME_JS.read_text(encoding="utf-8"))
    assert default
    css = _styles()[default.group(1)]

    def argb(value: str) -> str:
        return "#" + value.lower()

    fill = re.search(r"fill = listOf\((.*?)\)", body).group(1)
    assert [argb(h) for h in re.findall(r"0x([0-9A-Fa-f]{8})", fill)] == css["fill"]
    text = re.search(r"text = 0x([0-9A-Fa-f]{8})", body).group(1)
    assert argb(text) == css["text"]
    assert ("stroke = null" in body) == (css["stroke"] is None)
    shadow = re.search(r"shadow = Shadow\(([^,]+)f, ([^,]+)f, ([^,]+)f, 0x([0-9A-Fa-f]{8})", body)
    assert shadow and css["shadow"]
    assert [float(shadow.group(i)) for i in (1, 2, 3)] == [
        css["shadow"]["dx"], css["shadow"]["dy"], css["shadow"]["blur"]
    ]
    assert argb(shadow.group(4)) == css["shadow"]["color"]
    radii = re.search(r"radii = listOf\((.*?)\)", body).group(1)
    assert [float(r.rstrip("f")) for r in re.findall(r"[\d.]+f", radii)] == css["radii"]
