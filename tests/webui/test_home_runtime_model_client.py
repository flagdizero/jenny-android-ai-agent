"""La casa segue `runtime_model_updated`, come l'officina.

Un ``/model`` cambia il modello che risponde a runtime senza scrivere
``config.json``: ``/api/settings`` continua a dire quello di prima. Prima solo
l'officina ascoltava il frame (``mobile-chat.js``), e nella casa «Chi risponde»
restava sul modello del file. ``HomeApp._readRuntimeModel`` lo tiene da parte e
ridisegna da quel che sa gia'; ``_withRuntimeModel`` lo posa sopra a ogni
ridisegno delle Impostazioni, anche dopo una rilettura del server.

I metodi si ritagliano dal sorgente e girano in node, come negli altri banchi
della casa (v. ``test_home_settings_cache_client.py``).
"""

from __future__ import annotations

from support.js_harness import ASSETS, member, requires_node, run_js

APP_JS = ASSETS / "home-app.js"

pytestmark = requires_node


def _run(script: str) -> None:
    src = APP_JS.read_text(encoding="utf-8")
    harness = f"""
import assert from 'node:assert/strict';
let reads = 0;
const config = () => ({{
  providers: [{{ name: 'anthropic' }}, {{ name: 'opencode_go' }}],
  default_provider: 'anthropic',
  agent: {{ model: 'claude-file', bot_name: 'Jenny' }},
}});
const api = {{
  getSettings: async () => {{ reads += 1; return config(); }},
}};
console.warn = () => {{}};
/* La stanza di «Chi risponde», ridotta a quel che il guscio le passa: la riga
   di «Tu e Jenny» scrive il suo `value()`. */
const seen = [];
const modelRoom = {{
  data: null,
  setSettings(d) {{ this.data = d; seen.push(d); }},
  value() {{ return this.data ? `${{this.data.default_provider}}/${{this.data.agent?.model}}` : ''; }},
}};
const rows = [];
const room = () => ({{ value: () => '', setFloating() {{}}, setName() {{}}, setVersion() {{}}, setBackup() {{}} }});
class Home {{
  constructor() {{
    this._settings = null;
    this._settingsGen = 0;
    this._runtimeModel = null;
    this.you = {{ open() {{}}, sayJenny() {{}}, sayModel(v) {{ rows.push(v); }}, sayUpdates() {{}}, sayBackup() {{}} }};
    this.jennyRoom = room();
    this.modelRoom = modelRoom;
    this.updatesRoom = room();
    this.backupRoom = room();
  }}
  _applyBotName() {{}}
  {member(src, "_readRuntimeModel", prefixes=())}
  {member(src, "_withRuntimeModel", prefixes=())}
  {member(src, "_openSettings", prefixes=("async ",))}
  {member(src, "_paintSettings", prefixes=())}
  {member(src, "_askSettings", prefixes=())}
}}
const home = new Home();
const tick = () => new Promise((r) => setTimeout(r, 0));
"""
    run_js(harness + script)


def test_the_frame_repaints_who_answers_from_what_is_known() -> None:
    _run("""
      await home._openSettings();
      assert.equal(rows.at(-1), 'anthropic/claude-file');
      const readsBefore = reads;
      home._readRuntimeModel({ event: 'runtime_model_updated', model_name: 'claude-fast', provider: 'anthropic' });
      await tick();
      assert.equal(rows.at(-1), 'anthropic/claude-fast', 'la riga e\\u2019 rimasta sul modello del file');
      assert.equal(modelRoom.data.agent.model, 'claude-fast', 'la stanza segna ancora il modello di prima');
      assert.equal(reads, readsBefore, 'il frame non deve richiedere le impostazioni');
      const cached = await home._askSettings();
      assert.equal(cached.agent.model, 'claude-file', 'la cache e\\u2019 quel che dice il server: non si riscrive');
    """)


def test_a_reread_of_the_settings_does_not_bring_the_file_model_back() -> None:
    """``/api/settings`` legge la config, che un preset non tocca: riaprire le
    Impostazioni rimetteva a schermo il modello di prima del ``/model``."""
    _run("""
      await home._openSettings();
      home._readRuntimeModel({ event: 'runtime_model_updated', model_name: 'claude-fast', provider: 'anthropic' });
      await tick();
      await home._openSettings();
      assert.equal(rows.at(-1), 'anthropic/claude-fast');
      assert.equal(modelRoom.data.agent.model, 'claude-fast');
    """)


def test_a_frame_before_any_reading_waits_for_the_first_one() -> None:
    _run("""
      home._readRuntimeModel({ event: 'runtime_model_updated', model_name: 'kimi-k2', provider: 'opencode_go' });
      await tick();
      assert.deepEqual(rows, [], 'senza impostazioni lette non c\\u2019e\\u2019 niente da ridisegnare');
      await home._openSettings();
      assert.equal(rows.at(-1), 'opencode_go/kimi-k2');
    """)


def test_a_frame_without_the_provider_keeps_the_one_it_had() -> None:
    _run("""
      await home._openSettings();
      home._readRuntimeModel({ event: 'runtime_model_updated', model_name: 'kimi-k2', provider: 'opencode_go' });
      home._readRuntimeModel({ event: 'runtime_model_updated', model_name: 'glm-5' });
      await tick();
      assert.equal(rows.at(-1), 'opencode_go/glm-5');
    """)


def test_a_provider_the_config_does_not_know_is_not_laid_over() -> None:
    """Un modello suo sotto la marca di un altro direbbe una cosa che non esiste."""
    _run("""
      await home._openSettings();
      home._readRuntimeModel({ event: 'runtime_model_updated', model_name: 'gpt-x', provider: 'sparito' });
      await tick();
      assert.equal(rows.at(-1), 'anthropic/claude-file');
    """)


def test_other_frames_and_an_empty_model_change_nothing() -> None:
    _run("""
      await home._openSettings();
      const before = rows.length;
      home._readRuntimeModel({ event: 'turn_end', model_name: 'x' });
      home._readRuntimeModel({ event: 'runtime_model_updated', model_name: '   ' });
      home._readRuntimeModel(null);
      await tick();
      assert.equal(rows.length, before);
      assert.equal(home._runtimeModel, null);
    """)


def test_the_home_listens_to_the_frame() -> None:
    """Grep, non comportamento: il comportamento sta qui sopra; qui si tiene
    fermo il filo fra l'ascolto della chat e il metodo."""
    src = APP_JS.read_text(encoding="utf-8")
    assert "this._readRuntimeModel(e.detail);" in src
