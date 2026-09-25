"""I pannelli di un host SSH e di una marca seguono l'oggetto che mostrano.

Tre difetti dello stesso tipo (M17 della revisione profonda): i pannelli si
disegnano all'apertura, e poi nessuno li riallineava.

* Il segno di attesa di «Verifica» cercava il bottone in ``contentEl``, ma il
  bottone vive nel pannello (``#drawer-ssh-host-body``): non si trovava mai.
* Dopo «Genera chiave» il pannello restava su «nessuna chiave ancora».
* Dopo «Elimina» (host o marca) il pannello restava aperto su un oggetto che
  non esisteva piu', coi bottoni ancora attivi.

I metodi veri di ``mobile-settings.js`` girano in node su un DOM finto.
"""

from __future__ import annotations

from support.js_harness import ASSETS, locale, member, requires_node, run_js

pytestmark = requires_node

SRC = (ASSETS / "mobile-settings.js").read_text(encoding="utf-8")

_REAL = (
    "loadSettings",
    "_loadSsh",
    "_openSshHost",
    "_realignPanel",
    "_renderSshPublicKey",
    "_setSshVerifyBusy",
    "_sshGenerateKey",
    "_sshDelete",
    "_openBrand",
    "_deleteProvider",
)


def _script(body: str) -> str:
    methods = "\n".join(member(SRC, name) for name in _REAL)
    return f"""
import assert from 'node:assert/strict';

const i18n = {{ t: (k) => k }};
const escapeHtml = (s) => String(s);
const toasts = [];
const showToast = (t, kind) => toasts.push([t, kind]);
const confirmDialog = async () => true;
globalThis.CSS = {{ escape: (s) => s }};

const nodi = {{}};
const node = (id) => (nodi[id] ||= {{ id, innerHTML: '', textContent: '' }});
const check = {{ disabled: false, textContent: '' }};
globalThis.document = {{
  getElementById: (id) => node(id),
  querySelectorAll: () => [],
  querySelector: (sel) =>
    sel.startsWith('#drawer-ssh-host-body .ssh-verify[data-ssh-alias="nas"]') ? check : null,
}};

const drawer = {{
  activeDrawer: null,
  open(id) {{ this.activeDrawer = id; }},
  close(id) {{ if (this.activeDrawer === id) this.activeDrawer = null; }},
}};
globalThis.window = {{ mobileApp: {{ drawer }} }};

let ssh = {{ enabled: true, hosts: [
  {{ alias: 'nas', username: 'u', host: 'h', port: 22, auth: 'key', has_key: false }},
] }};
let settings = {{ providers: [{{ name: 'a' }}, {{ name: 'b', api_base: 'https://b' }}] }};
const api = {{
  getSsh: async () => JSON.parse(JSON.stringify(ssh)),
  getSettings: async () => JSON.parse(JSON.stringify(settings)),
  generateSshKey: async () => {{
    ssh.hosts[0] = {{ ...ssh.hosts[0], has_key: true, public_key: 'ssh-ed25519 AAAA nas' }};
  }},
  deleteSshHost: async (alias) => {{ ssh.hosts = ssh.hosts.filter((h) => h.alias !== alias); }},
  deleteProvider: async ({{ name }}) => {{
    settings.providers = settings.providers.filter((p) => p.name !== name);
  }},
}};

class Settings {{
  constructor() {{
    this._gen = 0;
    this.contentEl = {{
      querySelector: (sel) => (sel === '#ssh-block' ? node('ssh-block') : null),
    }};
  }}
  _stale(g) {{ return g !== this._gen; }}
  showLoading() {{}}
  hideLoading() {{}}
  render() {{}}
  _renderSshBlock() {{ return ''; }}
  _wireSshBlock() {{}}
  _restoreScrollTop() {{}}
  _wireHostSsh() {{}}
  _editProvider() {{}}
{methods}
}}

const s = new Settings();
const settle = () => new Promise((r) => setTimeout(r, 5));
await s._loadSsh();
await s.loadSettings();

{body}
console.log('ok');
"""


def test_the_verify_button_shows_it_is_busy() -> None:
    out = run_js(
        _script(
            """
drawer.open('ssh-host');
s._openSshHost('nas');
s._setSshVerifyBusy('nas', true);
assert.equal(check.disabled, true, 'il bottone del pannello non si e\\' off');
assert.equal(check.textContent, 'settings.ssh.verifying');
s._setSshVerifyBusy('nas', false);
assert.equal(check.disabled, false);
"""
        )
    )
    assert out.strip() == "ok"


def test_generating_a_key_redraws_the_open_panel() -> None:
    out = run_js(
        _script(
            """
drawer.open('ssh-host');
s._openSshHost('nas');
assert.match(nodi['drawer-ssh-host-body'].innerHTML, /noKeyYet/);
await s._sshGenerateKey('nas', false);
await settle();
const body = nodi['drawer-ssh-host-body'].innerHTML;
assert.match(body, /ssh-ed25519 AAAA nas/, 'il pannello mostra ancora «nessuna chiave»');
assert.doesNotMatch(body, /noKeyYet/);
assert.equal(drawer.activeDrawer, 'ssh-host');
"""
        )
    )
    assert out.strip() == "ok"


def test_deleting_a_host_closes_its_panel() -> None:
    out = run_js(
        _script(
            """
drawer.open('ssh-host');
s._openSshHost('nas');
await s._sshDelete('nas');
await settle();
assert.equal(drawer.activeDrawer, null, "il pannello e' rimasto su un host cancellato");
"""
        )
    )
    assert out.strip() == "ok"


def test_deleting_a_brand_closes_its_panel() -> None:
    out = run_js(
        _script(
            """
drawer.open('brand');
s._openBrand('b');
await s._deleteProvider('b');
await settle();
assert.equal(drawer.activeDrawer, null, "il pannello e' rimasto su una marca cancellata");
"""
        )
    )
    assert out.strip() == "ok"


def test_another_drawer_is_left_alone() -> None:
    """Il riallineamento tocca solo il pannello di cui si parla."""
    out = run_js(
        _script(
            """
s._openSshHost('nas');
drawer.open('tetti');
await s._sshDelete('nas');
await settle();
assert.equal(drawer.activeDrawer, 'tetti');
"""
        )
    )
    assert out.strip() == "ok"


def _labels(html_var: str) -> str:
    """JS che estrae le coppie (etichetta, valore) delle righe di un pannello."""
    return (
        "[...nodi['" + html_var + "'].innerHTML.matchAll("
        "/settings-label\">([^<]*)<\\/span>\\s*<span class=\"settings-summary-value\">([^<]*)</g"
        ")].map((m) => [m[1], m[2]])"
    )


def test_each_panel_row_has_a_label_that_names_it() -> None:
    """M18: la marca etichettava l'indirizzo con «(predefinito)» e la chiave con
    «(nessuna chiave)» — cioe' coi due valori di ripiego — e l'host metteva
    «impronta» davanti a ``user@host:22``."""
    out = run_js(
        _script(
            f"""
settings.providers[1].api_key_hint = 'sk-…abcd';
await s.loadSettings();
s._openBrand('b');
assert.deepEqual({_labels('drawer-brand-body')}, [
  ['settings.brandAddress', 'https://b'],
  ['settings.brandKey', 'sk-…abcd'],
]);
s._openBrand('a');
assert.deepEqual({_labels('drawer-brand-body')}, [
  ['settings.brandAddress', 'settings.defaultUrl'],
  ['settings.brandKey', 'settings.noKey'],
]);
s._openSshHost('nas');
assert.deepEqual({_labels('drawer-ssh-host-body')}, [['settings.ssh.where', 'u@h:22']]);
"""
        )
    )
    assert out.strip() == "ok"


def test_the_new_labels_exist_in_both_languages() -> None:
    for language in ("it", "en"):
        s = locale(language)["settings"]
        assert s["brandAddress"] and s["brandKey"] and s["ssh"]["where"]
