"""La cache delle impostazioni della casa non rimette indietro la finestra flottante.

``HomeApp._askSettings`` chiede ``/api/settings`` una volta e ne tiene la
promessa: ogni apertura delle Impostazioni la rilegge e passa ``floating`` a
``HomeJenny.setFloating``. Toccato l'interruttore, la stanza sapeva il valore
nuovo e la cache quello vecchio, e alla riapertura vinceva la cache:
l'interruttore si ridisegnava spento con la finestra accesa (visto sul telefono
il 25/09). ``_keepFloating`` scrive nella cache com'e' finita.

I metodi si ritagliano dal sorgente e girano in node, come negli altri banchi
della casa.
"""

from __future__ import annotations

from pathlib import Path

from support.js_harness import member, requires_node, run_js

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
APP_JS = ASSETS / "home-app.js"

pytestmark = requires_node


def _member(source: str, name: str) -> str:
    return member(source, name, prefixes=())


def _run_js(script: str) -> None:
    src = APP_JS.read_text(encoding="utf-8")
    harness = f"""
import assert from 'node:assert/strict';
let reads = 0;
const api = {{
  getSettings: async () => {{
    reads += 1;
    return {{ floating: {{ available: true, enabled: false, active: false }} }};
  }},
}};
class Home {{
  constructor() {{ this._settings = null; }}
  {_member(src, "_askSettings")}
  {_member(src, "_keepFloating")}
}}
const home = new Home();
const tick = () => new Promise((r) => setTimeout(r, 0));
"""
    run_js(harness + script)


def test_reopening_settings_reads_the_switch_as_it_ended() -> None:
    _run_js("""
      const before = await home._askSettings();
      assert.equal(before.floating.enabled, false);
      home._keepFloating({ available: true, enabled: true, active: true });
      await tick();
      const after = await home._askSettings();
      assert.equal(after.floating.enabled, true, 'la cache ha rimesso lo stato di prima');
      assert.equal(reads, 1, 'resta una lettura sola: la cache non si butta');
    """)


def test_nothing_to_keep_when_nothing_was_read() -> None:
    """Senza cache non c'e' niente da correggere: la prossima apertura legge
    dal server, che sa gia' il valore nuovo."""
    _run_js("""
      home._keepFloating({ enabled: true });
      assert.equal(home._settings, null);
      await home._askSettings();
      assert.equal(reads, 1);
    """)


def test_the_room_is_wired_to_the_cache() -> None:
    """Grep, non comportamento: il comportamento dei due capi sta qui sopra e in
    ``test_home_jenny_client.py``; qui si tiene fermo il filo fra i due."""
    src = APP_JS.read_text(encoding="utf-8")
    assert "onFloating: (floating) => this._keepFloating(floating)" in src


# ── Ogni apertura delle Impostazioni rilegge il server ──────────────────────
#
# La casa e' il launcher e vive per giorni; la cache era quella dell'avvio, per
# tutta la vita della pagina. Un aggiornamento trovato dal controllo periodico
# non compariva mai, e «ultimo controllo» invecchiava. Adesso l'apertura mostra
# subito quel che si sa e ridisegna quando arriva la risposta nuova.


def _run_open(script: str) -> None:
    src = APP_JS.read_text(encoding="utf-8")
    harness = f"""
import assert from 'node:assert/strict';
let reads = 0;
let version = 'v1';
let failNext = false;
let hold = null;
const api = {{
  getSettings: async () => {{
    reads += 1;
    if (hold) await hold;
    if (failNext) {{ failNext = false; throw new Error('rete'); }}
    return {{ version: {{ current: version }}, agent: {{ bot_name: 'Jenny' }} }};
  }},
}};
console.warn = () => {{}};
const painted = [];
const room = (name) => ({{
  value: () => name,
  setFloating() {{}}, setName() {{}}, setSettings() {{}}, setBackup() {{}},
  setVersion(v) {{ painted.push(v ? v.current : null); }},
}});
class Home {{
  constructor() {{
    this._settings = null;
    this.you = {{ open() {{}}, sayJenny() {{}}, sayModel() {{}}, sayUpdates() {{}}, sayBackup() {{}} }};
    this.jennyRoom = room('j');
    this.modelRoom = room('m');
    this.updatesRoom = room('u');
    this.backupRoom = room('b');
  }}
  _applyBotName() {{}}
  {member(src, "_openSettings", prefixes=("async ",))}
  {_member(src, "_paintSettings")}
  {_member(src, "_askSettings")}
}}
const home = new Home();
"""
    run_js(harness + script)


def test_opening_settings_again_reads_the_server_again() -> None:
    _run_open("""
      await home._openSettings();
      assert.deepEqual(painted, ['v1']);
      version = 'v2';               // il controllo periodico ha trovato un aggiornamento
      await home._openSettings();
      assert.equal(reads, 2, "la seconda apertura non ha chiesto niente al server");
      assert.equal(painted.at(-1), 'v2', 'la versione nuova non compare');
    """)


def test_the_cached_value_is_shown_while_the_new_one_arrives() -> None:
    _run_open("""
      await home._openSettings();
      version = 'v2';
      let release;
      hold = new Promise((r) => { release = r; });
      painted.length = 0;
      const opening = home._openSettings();
      await new Promise((r) => setTimeout(r, 0));
      assert.deepEqual(painted, ['v1'], "durante la lettura la pagina deve mostrare quel che sa");
      release();
      await opening;
      assert.deepEqual(painted, ['v1', 'v2']);
    """)


def test_a_failed_reread_keeps_what_was_known() -> None:
    _run_open("""
      await home._openSettings();
      painted.length = 0;
      failNext = true;
      await home._openSettings();
      assert.deepEqual(painted, ['v1'], 'una rilettura fallita ha cancellato la versione');
      const cached = await home._askSettings();
      assert.equal(cached.version.current, 'v1', 'la cache si e\\u2019 persa col fallimento');
    """)
