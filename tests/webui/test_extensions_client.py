"""Le estensioni nelle shell (``shared/extensions.js``) e le loro traduzioni.

Una build senza estensioni manda un elenco vuoto e non succede niente. Con
un'estensione: le traduzioni si registrano **prima** di ``i18n.load`` (la prima
pagina disegnata le ha già), i fogli di stile si agganciano, e il modulo si
monta con i ganci della shell. Un'estensione rotta non ferma la shell.
"""

from __future__ import annotations

from pathlib import Path

from support.js_harness import function, member, requires_node, run_js

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
EXT_JS = (ASSETS / "shared" / "extensions.js").read_text(encoding="utf-8")
I18N_JS = (ASSETS / "shared" / "i18n.js").read_text(encoding="utf-8")

pytestmark = requires_node


def _body(name: str) -> str:
    return function(EXT_JS, name)


def test_translations_from_an_extension_are_merged_over_the_core() -> None:
    run_js(f"""
      import assert from 'node:assert/strict';
      const FILES = {{
        '/assets/i18n/it.json': {{ home: {{ backup: {{ title: 'Backup', more: {{ fileSub: 'core' }} }} }} }},
        '/ext/it.json': {{ home: {{ backup: {{ more: {{ fileSub: 'estensione' }} }} }}, drive: {{ title: 'Drive' }} }},
      }};
      globalThis.fetch = async (url) => ({{ ok: url in FILES, status: url in FILES ? 200 : 404, json: async () => structuredClone(FILES[url]) }});
      Object.defineProperty(globalThis, 'navigator', {{ value: {{ language: 'it-IT' }}, configurable: true }});
      {I18N_JS.replace('export class', 'class').replace('export const i18n', 'const i18n')}
      i18n.addSource({{ it: '/ext/it.json', en: '/ext/en.json' }});
      await i18n.load('it');
      assert.equal(i18n.t('home.backup.title'), 'Backup', 'il ramo del core e stato sostituito');
      assert.equal(i18n.t('home.backup.more.fileSub'), 'estensione');
      assert.equal(i18n.t('drive.title'), 'Drive');
      /* Una lingua che l'estensione non ha: resta il core. */
      FILES['/assets/i18n/en.json'] = {{ home: {{ backup: {{ title: 'Backup' }} }} }};
      await i18n.load('en');
      assert.equal(i18n.translations.en.home.backup.title, 'Backup');
    """)


def test_extensions_prepare_then_mount_with_the_shell_hooks() -> None:
    run_js(f"""
      import assert from 'node:assert/strict';
      const head = [];
      globalThis.document = {{
        createElement: () => ({{}}),
        head: {{ appendChild: (el) => head.push(el) }},
      }};
      const sources = [];
      const i18n = {{ addSource: (s) => sources.push(s) }};
      const logs = [];
      const EXT = [
        {{ name: 'uno', entry: 'data:text/javascript,export function mount(ctx) {{ globalThis.mounted = ctx; }}',
          css: ['/html-mobile/assets/ext/uno/uno.css'], i18n: {{ it: '/x/it.json' }} }},
        {{ name: 'rotta', entry: 'data:text/javascript,throw new Error("rotta")', css: [], i18n: {{}} }},
      ];
      const api = {{ extensions: () => EXT, clientLog: (...a) => logs.push(a) }};
      let _prepared = false;
      {_body("prepareExtensions")}
      {_body("mountExtensions")}
      prepareExtensions();
      prepareExtensions();
      assert.deepEqual(sources, [{{ it: '/x/it.json' }}, {{}}], 'traduzioni registrate due volte o mai');
      assert.equal(head.length, 1);
      assert.equal(head[0].href, '/html-mobile/assets/ext/uno/uno.css');
      assert.equal(head[0].rel, 'stylesheet');

      const hook = {{}};
      await mountExtensions('home', {{ homeBackup: hook }});
      assert.equal(globalThis.mounted.shell, 'home');
      assert.equal(globalThis.mounted.homeBackup, hook);
      assert.equal(logs.length, 1, 'un estensione rotta non si dice');
      assert.equal(logs[0][1], 'extension.rotta');
    """)


def test_without_extensions_nothing_happens() -> None:
    run_js(f"""
      import assert from 'node:assert/strict';
      globalThis.document = {{ createElement: () => assert.fail('foglio'), head: {{}} }};
      const i18n = {{ addSource: () => assert.fail('traduzioni') }};
      const api = {{ extensions: () => [] }};
      let _prepared = false;
      {_body("prepareExtensions")}
      {_body("mountExtensions")}
      prepareExtensions();
      await mountExtensions('home', {{}});
    """)


def test_the_shells_prepare_before_loading_the_translations() -> None:
    """Le traduzioni dell'estensione devono esserci al primo disegno."""
    for shell in ("home-app.js", "onboarding-app.js"):
        source = (ASSETS / shell).read_text(encoding="utf-8")
        assert source.index("prepareExtensions();") < source.index("await i18n.load(i18n.locale);"), shell
        assert "mountExtensions(" in source, shell


def test_the_api_lists_the_extensions_from_the_bootstrap() -> None:
    api = (ASSETS / "shared" / "api-client.js").read_text(encoding="utf-8")
    body = member(api, "extensions")
    run_js(f"""
      import assert from 'node:assert/strict';
      class Api {{ {body} }}
      const a = new Api();
      assert.deepEqual(a.extensions(), []);
      a._bootstrapInfo = {{ extensions: [{{ name: 'x' }}] }};
      assert.deepEqual(a.extensions(), [{{ name: 'x' }}]);
      a._bootstrapInfo = {{ extensions: 'rotto' }};
      assert.deepEqual(a.extensions(), []);
    """)


def test_the_onboarding_welcome_step_runs_the_extension_hooks() -> None:
    wizard = (ASSETS / "onboarding-wizard.js").read_text(encoding="utf-8")
    assert 'id="onboarding-ext-welcome"' in member(wizard, "_renderStep0")
    run_js(f"""
      import assert from 'node:assert/strict';
      class Wizard {{
        constructor() {{ this._welcomeHooks = []; this.step = 0; this.contentEl = null; }}
        _renderStep0() {{ this.rendered = (this.rendered || 0) + 1; }}
        {member(wizard, "addWelcomeExtension")}
      }}
      const w = new Wizard();
      const hook = () => {{}};
      w.addWelcomeExtension(hook);
      assert.deepEqual(w._welcomeHooks, [hook]);
      assert.equal(w.rendered, undefined, 'ridisegna un passo che non c e ancora');
    """)
