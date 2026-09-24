"""La politica di rendering del pannello subagent, eseguita davvero.

``assets/shared/subagent-policy.js`` è di proposito senza DOM e senza import: è
la sola forma in cui queste regole si possono *eseguire* da qui invece che
descriverle con una regex. Il modulo viene valutato da node (già richiesto dal
type check con ``npx pyright``) e le asserzioni sono in JS, sullo stesso oggetto
che la WebUI usa a runtime.

Le quattro regole che questi test difendono, in ordine di quanto costa perderle:

1. Nulla di un turno passato viene mai renderizzato. Il server serve sempre
   ``recent`` (lo consumano il tool ``subagent_status`` e GET /api/subagents), e
   dopo un reload quella coda contiene i job dei turni precedenti: se il pannello
   li ripescasse, l'utente ritroverebbe card morte sopra il composer per sempre.
2. Una card terminale lingera per il turno corrente e sparisce a ``turn_end``.
3. ...e comunque scade da sé dopo ``SA_LINGER_MS``. ``turn_end`` è un frame
   live: chi era in Doze non lo riceve mai, e senza scadenza la card resta
   sopra il composer per ore (misurato sul Titan 2: una card delle 07:00 ancora
   lì alle 10:18).
4. La matrice delle azioni per stato: Rilancia non deve comparire su un job
   riuscito né a un tap su un job fallito.
"""

from __future__ import annotations

from pathlib import Path

from support.js_harness import requires_node, run_js

POLICY_JS = (
    Path(__file__).resolve().parents[2]
    / "jenny" / "templates" / "ui" / "assets" / "shared" / "subagent-policy.js"
)


pytestmark = requires_node


def _run_js(script: str) -> str:
    """Valuta il modulo di policy seguito da ``script``, con ``assert`` di node."""
    source = (
        POLICY_JS.read_text(encoding="utf-8")
        + "\nimport assert from 'node:assert/strict';\n"
        + script
    )
    return run_js(source)


def test_module_is_pure_no_dom_and_no_imports() -> None:
    """Se il modulo prende dipendenze, questi test smettono di poterlo eseguire."""
    source = POLICY_JS.read_text(encoding="utf-8")
    assert "import " not in source, "la policy deve restare senza dipendenze"
    assert "document" not in source and "window" not in source, "la policy non tocca il DOM"


def test_a_reload_with_nothing_running_renders_an_empty_panel() -> None:
    """Il caso che ha motivato la regola: reload a turno finito.

    ``liveIds`` è vuoto (client appena avviato), ``running`` è vuoto e il server
    serve comunque i terminati dei turni passati: zero card, quindi pannello
    assente.
    """
    _run_js("""
      const snapshot = {
        running: [],
        recent: [
          { task_id: 'old1', state: 'done' },
          { task_id: 'old2', state: 'failed' },
        ],
      };
      const view = saVisibleCards(snapshot, new Set());
      assert.deepEqual(view.running, []);
      assert.deepEqual(view.lingering, []);
      assert.equal(view.running.length + view.lingering.length, 0);
    """)


def test_a_terminated_card_lingers_only_for_the_current_turn() -> None:
    """Vivo → terminato (lingera) → turn_end (sparisce)."""
    _run_js("""
      const NOW = 1700000000000;
      let live = new Set();

      // 1. Il subagent gira: una card viva, nessuna terminale.
      let view = saVisibleCards({ running: [{ task_id: 't1', state: 'running' }], recent: [] }, live, NOW);
      live = view.liveIds;
      assert.equal(view.running.length, 1);
      assert.deepEqual(view.lingering, []);

      // 2. Termina: il server lo sposta in `recent`, la card lingera perché la
      //    transizione è stata osservata qui.
      const terminated = {
        running: [],
        recent: [{ task_id: 't1', state: 'done', ended_at: NOW / 1000 - 1 }],
      };
      view = saVisibleCards(terminated, live, NOW);
      live = view.liveIds;
      assert.deepEqual(view.lingering.map(e => e.task_id), ['t1']);

      // 3. turn_end azzera l'insieme dei vivi: la card non c'è più, e non torna
      //    nemmeno al poll successivo che riporta lo stesso `recent`.
      live = new Set();
      view = saVisibleCards(terminated, live, NOW);
      assert.deepEqual(view.lingering, []);
      view = saVisibleCards(terminated, view.liveIds, NOW);
      assert.deepEqual(view.lingering, []);
    """)


def test_a_terminated_card_expires_without_any_turn_end() -> None:
    """Il buco misurato: nessun ``turn_end``, e la card resta sopra il composer.

    ``turn_end`` è un frame live — un turno di cron consegnato mentre il telefono
    è in Doze non lo consegna a nessuno. Senza scadenza la card sopravvive a
    tutto: al poll, al reconnect, e anche ai turn_end successivi che il client
    non ha visto.
    """
    _run_js("""
      const NOW = 1700000000000;
      const snapshot = {
        running: [],
        recent: [{ task_id: 't1', state: 'done', ended_at: NOW / 1000 }],
      };
      // Appena terminato: la transizione si deve vedere.
      let view = saVisibleCards(snapshot, new Set(['t1']), NOW + 1000);
      assert.deepEqual(view.lingering.map(e => e.task_id), ['t1']);
      assert.ok(view.nextExpiryMs > 0 && view.nextExpiryMs <= SA_LINGER_MS);

      // Un istante dopo la scadenza: pannello vuoto, senza che sia arrivato
      // niente dal gateway.
      view = saVisibleCards(snapshot, new Set(['t1']), NOW + SA_LINGER_MS + 1);
      assert.deepEqual(view.lingering, []);
      assert.equal(view.nextExpiryMs, null);
      // E l'id esce dai vivi: il poll successivo non lo ripesca da `recent`.
      assert.ok(!view.liveIds.has('t1'), 'una scadenza che dimentica non è una scadenza');
    """)


def test_freshness_is_wall_clock_and_unknown_means_expired() -> None:
    """``ended_at`` assente o assurdo = card che non prova di essere fresca."""
    _run_js("""
      const NOW = 1700000000000;
      for (const ended of [undefined, null, 0, -1, 'ieri', NaN]) {
        const view = saVisibleCards(
          { running: [], recent: [{ task_id: 't1', state: 'done', ended_at: ended }] },
          new Set(['t1']),
          NOW,
        );
        assert.deepEqual(view.lingering, [], `ended_at=${String(ended)} non deve lingerare`);
      }
      // Un `ended_at` nel futuro (orologio del telefono spostato) non pinna la
      // card per sempre: al massimo vale una finestra intera.
      const ahead = saLingerLeftMs({ ended_at: NOW / 1000 + 3600 }, NOW);
      assert.ok(ahead > 0, 'una card dal futuro resta visibile');
    """)


def test_turn_end_never_drops_a_subagent_that_outlived_the_turn() -> None:
    """Un job può sopravvivere al turno che l'ha lanciato: resta, e potrà lingerare."""
    _run_js("""
      const alive = { running: [{ task_id: 't1', state: 'running' }], recent: [] };
      // turn_end: `liveIds` azzerato, poi ri-render sullo stesso snapshot.
      const view = saVisibleCards(alive, new Set());
      assert.equal(view.running.length, 1);
      assert.ok(view.liveIds.has('t1'), 'un vivo si re-iscrive da sé');
      // E quando poi terminerà, la sua card lingera come le altre.
      const now = Date.now();
      const after = saVisibleCards(
        { running: [], recent: [{ task_id: 't1', state: 'failed', ended_at: now / 1000 }] },
        view.liveIds,
        now,
      );
      assert.deepEqual(after.lingering.map(e => e.task_id), ['t1']);
    """)


def test_only_the_witnessed_terminations_linger() -> None:
    """Fra i terminati di `recent` passa solo quello visto vivo in questo turno."""
    _run_js("""
      const now = Date.now();
      const view = saVisibleCards({
        running: [],
        recent: [
          { task_id: 'seen', state: 'done', ended_at: now / 1000 },
          { task_id: 'from-an-old-turn', state: 'done', ended_at: now / 1000 },
        ],
      }, new Set(['seen']), now);
      assert.deepEqual(view.lingering.map(e => e.task_id), ['seen']);
    """)


def test_action_matrix_per_state_and_surface() -> None:
    """Chi porta cosa, e dove. Il perché di ogni riga sta nel modulo."""
    _run_js("""
      // running: solo Stop sulla card, un tap, nessuna deviazione dalla modale.
      assert.deepEqual(saActions('running', 'card'), ['stop']);
      assert.deepEqual(saActions('running', 'modal'), ['stop']);

      // stalled: è l'unico caso per cui Rilancia esiste, e resta prominente.
      assert.deepEqual(saActions('stalled', 'card'), ['stop', 'restart']);
      assert.deepEqual(saActions('stalled', 'modal'), ['stop', 'restart']);

      // failed: niente sulla card, Rilancia raggiungibile solo nella modale.
      assert.deepEqual(saActions('failed', 'card'), []);
      assert.deepEqual(saActions('failed', 'modal'), ['restart']);

      // done/cancelled: nessuna azione da nessuna parte.
      for (const state of ['done', 'cancelled']) {
        assert.deepEqual(saActions(state, 'card'), [], state);
        assert.deepEqual(saActions(state, 'modal'), [], state);
      }

      // Uno stato che il backend guadagnasse domani non inventa bottoni.
      assert.deepEqual(saActions('teleporting', 'card'), []);
      assert.deepEqual(saActions('teleporting', 'modal'), []);
      assert.deepEqual(saActions(undefined, 'card'), []);
    """)


def test_relaunch_is_never_offered_on_a_successful_job() -> None:
    _run_js("""
      for (const surface of ['card', 'modal']) {
        assert.ok(!saActions('done', surface).includes('restart'), surface);
      }
      assert.ok(!saActions('failed', 'card').includes('restart'));
    """)


def test_terminal_states_match_the_backend_lifecycle() -> None:
    _run_js("""
      assert.deepEqual([...SA_TERMINAL_STATES].sort(), ['cancelled', 'done', 'failed']);
      for (const state of SA_TERMINAL_STATES) assert.ok(saIsTerminal(state), state);
      for (const state of ['running', 'stalled']) assert.ok(!saIsTerminal(state), state);
    """)


def test_a_malformed_snapshot_never_throws() -> None:
    """Lo snapshot arriva da un frame WS: una forma inattesa non deve rompere la vista."""
    _run_js("""
      for (const snapshot of [null, undefined, {}, { running: 'nope', recent: 3 },
                              { running: [null], recent: [null, { }] }]) {
        const view = saVisibleCards(snapshot, null);
        assert.ok(Array.isArray(view.running));
        assert.ok(Array.isArray(view.lingering));
      }
    """)
