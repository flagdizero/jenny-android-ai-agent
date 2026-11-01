"""Il pannello Subagent dell'officina è della conversazione aperta.

Lo alimentavano due strade che non si accordavano: il frame ``subagent_status``,
già filtrato sulla sessione che ha lanciato i subagent, e un poll ogni 5 s su
``GET /api/subagents`` **senza** ``session_key`` — cioè i subagent di tutte le
sessioni, cron, Dream, giardiniere e gli altri quaderni compresi. Il pannello
oscillava fra le due viste, e un subagent estraneo che finiva lasciava il suo
blocco «cosa ha fatto davvero» nella chat aperta, dentro una bolla vuota creata
apposta (il ripiego di ``_appendSubagentDigest``).

Qui si eseguono in node i metodi veri, ritagliati dal sorgente, su un ``this``
finto: ``_refreshSubagents`` (quale chiave chiede, e cosa fa di una risposta
arrivata dopo un cambio di chat) e ``_resetSubagentPanel`` (cosa resta del
pannello quando la conversazione cambia). ``subagentUrl`` di ``api-client.js``
è la funzione che trasforma la chiave in query string per tutte e cinque le
chiamate.
"""

from __future__ import annotations

import re

from support.js_harness import ASSETS, function, member, requires_node, run_js

pytestmark = requires_node

CHAT_JS = ASSETS / "mobile-chat.js"
API_JS = ASSETS / "shared" / "api-client.js"


def _chat() -> str:
    # mobile-chat.js contiene byte che un decoder stretto rifiuta (v.
    # test_workshop_open_file_from_chat_client.py): i metodi che servono qui no.
    return CHAT_JS.read_text(encoding="utf-8", errors="replace")


def _harness() -> str:
    chat = _chat()
    return f"""
import assert from 'node:assert/strict';

// Il session manager: chiave aperta e generazione dei cambi, che i test muovono.
const sessionManager = {{ currentKey: 'project:piante', switchGeneration: 0 }};

// Il gateway finto: ogni getSubagents resta in sospeso finché il test non lo
// risolve, così l'ordine fra risposta e cambio di chat lo decide il test.
const calls = [];
const api = {{
  getSubagents(opts) {{
    return new Promise((resolve) => calls.push({{ opts, resolve }}));
  }},
}};

const closed = [];
const document = {{
  getElementById(id) {{
    return id === 'oc-detail-dialog' ? {{ close() {{ closed.push(id); }} }} : null;
  }},
}};

function makePanel() {{
  return {{
    rendered: [],
    _saStream: null,
    _subagentLiveIds: new Set(['old1']),
    _saDigestSeen: new Set(['old1']),
    _lastStalledIds: 'old1',
    _renderSubagents(snapshot) {{ this.rendered.push(snapshot); }},
    {member(chat, "_refreshSubagents")},
    {member(chat, "_resetSubagentPanel")},
  }};
}}

const tick = () => new Promise((r) => setTimeout(r, 0));
"""


def _run(script: str) -> None:
    run_js(_harness() + script)


def test_the_poll_asks_only_for_the_open_conversation() -> None:
    _run("""
      const panel = makePanel();
      panel._refreshSubagents();
      assert.equal(calls.length, 1);
      assert.deepEqual(calls[0].opts, { sessionKey: 'project:piante' });

      sessionManager.currentKey = 'websocket:default';
      panel._refreshSubagents();
      assert.deepEqual(calls[1].opts, { sessionKey: 'websocket:default' });
    """)


def test_a_snapshot_that_returns_after_a_switch_is_dropped() -> None:
    """La risposta è della chat lasciata: dipingerla qui rimetterebbe nel
    pannello del quaderno nuovo i subagent di quello vecchio."""
    _run("""
      const panel = makePanel();
      panel._refreshSubagents();
      sessionManager.switchGeneration++;
      calls[0].resolve({ running: [{ task_id: 'foreign' }], recent: [] });
      await tick();
      assert.deepEqual(panel.rendered, []);

      panel._refreshSubagents();
      calls[1].resolve({ running: [{ task_id: 'mine' }], recent: [] });
      await tick();
      assert.deepEqual(panel.rendered, [{ running: [{ task_id: 'mine' }], recent: [] }]);
    """)


def test_a_switch_empties_the_panel_and_reads_the_new_conversation() -> None:
    _run("""
      const panel = makePanel();
      panel._resetSubagentPanel();
      // Prima il pannello vuoto, subito: niente dalla chat lasciata resta a schermo.
      assert.deepEqual(panel.rendered, [{ running: [], recent: [] }]);
      // Gli insiemi che decidono il blocco «cosa ha fatto davvero» ripartono da
      // zero: un subagent visto vivo altrove non lascia il suo blocco qui.
      assert.equal(panel._subagentLiveIds.size, 0);
      assert.equal(panel._saDigestSeen.size, 0);
      assert.equal(panel._lastStalledIds, '');
      // Poi la lettura per la chat nuova.
      assert.equal(calls.length, 1);
      assert.deepEqual(calls[0].opts, { sessionKey: 'project:piante' });
      // Nessuna modale aperta, quindi niente da chiudere.
      assert.deepEqual(closed, []);
    """)


def test_a_switch_closes_an_open_detail_through_its_own_exit() -> None:
    """La modale si chiude dal suo evento ``close``, la stessa uscita della X:
    è quella che passa da ``_detachSubagentStream`` e toglie il watch."""
    _run("""
      const panel = makePanel();
      panel._saStream = { taskId: 'old1' };
      panel._resetSubagentPanel();
      assert.deepEqual(closed, ['oc-detail-dialog']);
    """)


def test_the_panel_resets_on_a_conversation_switch_not_on_any_invalidation() -> None:
    """Guardia sul testo: il reset è legato a ``chat:switch`` del session
    manager, che copre ogni strada di un cambio vero. ``invalidateHistory``
    scatta anche senza cambio (la minichat che chiude un turno), e lì il
    pannello deve restare com'è."""
    chat = _chat()
    assert "sessionManager.addEventListener('chat:switch', () => this._resetSubagentPanel())" in chat
    invalidate = member(chat, "invalidateHistory", body_only=True)
    assert "_resetSubagentPanel" not in invalidate


def test_every_subagent_call_of_the_workshop_names_its_conversation() -> None:
    """Le cinque chiamate verso ``/api/subagents*``: nessuna parte senza chiave,
    altrimenti il gateway non filtra e non controlla niente."""
    chat = _chat()
    calls = re.findall(
        r"api\.(getSubagents|restartSubagent|cancelSubagent|getSubagentActivity|getSubagentDigest)"
        r"\(([^;]*?)\)\)?;",
        chat,
        re.S,
    )
    names = sorted({name for name, _ in calls})
    assert names == sorted([
        "cancelSubagent", "getSubagentActivity", "getSubagentDigest",
        "getSubagents", "restartSubagent",
    ])
    for name, args in calls:
        assert "sessionKey" in args, f"{name} parte senza la conversazione: {args!r}"


def test_the_session_key_becomes_a_query_parameter() -> None:
    source = API_JS.read_text(encoding="utf-8")
    run_js(f"""
      import assert from 'node:assert/strict';
      {function(source, "subagentUrl")}

      // Senza chiave: byte per byte la chiamata di prima.
      assert.equal(subagentUrl(''), '/api/subagents');
      assert.equal(subagentUrl('/abc/cancel', {{}}), '/api/subagents/abc/cancel');
      assert.equal(subagentUrl('/abc/activity', {{ since: '0' }}),
                   '/api/subagents/abc/activity?since=0');

      // Con la chiave, nelle due forme che i gusci mandano.
      assert.equal(subagentUrl('', {{ sessionKey: 'project:piante' }}),
                   '/api/subagents?session_key=project%3Apiante');
      assert.equal(subagentUrl('/abc/activity', {{ since: '7', sessionKey: 'websocket:default' }}),
                   '/api/subagents/abc/activity?since=7&session_key=websocket%3Adefault');

      // Una chiave vuota non è una chiave.
      assert.equal(subagentUrl('/abc/digest', {{ sessionKey: '' }}), '/api/subagents/abc/digest');
    """)
