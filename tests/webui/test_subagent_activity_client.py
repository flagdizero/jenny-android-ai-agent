"""Le regole di filo dello stream di attività, eseguite davvero.

Compagno di ``test_subagent_panel_policy.py``: stesso modulo
(``assets/shared/subagent-policy.js``, senza DOM e senza dipendenze), stesso
motivo per cui quel modulo esiste — queste regole si possono *eseguire* da qui
invece che descriverle con una regex.

Le cinque che costa di più perdere, in ordine:

1. ``initial: true`` **rimpiazza** solo quando il watch partiva da zero. Dopo un
   reconnect si ri-watcha dal cursore che si ha già, e rimpiazzare butterebbe
   tutto lo stream letto fin lì — cioè l'unica cosa che un reconnect deve *non*
   fare.
2. Tutto il resto **appende**, e lo stesso evento consegnato due volte (pump +
   risync) compare una volta sola.
3. Un ``gap`` dichiarato chiede una risync **dal cursore di prima** della
   finestra bucata: da quello di dopo salterebbe esattamente ciò che manca.
4. Il buco si vede finché non è tappato, e sparisce da sé quando la risync lo
   tappa: uno stream bucato che si presenta integro è peggio di un pannello
   statico.
5. ``tool_start``/``tool_end`` si accoppiano per ``call_id`` — con tre chiamate
   dello stesso tool in volo il nome accoppierebbe a caso — e la riga resta *in
   corso* finché la fine non arriva, che è la sola risposta a "cosa sta facendo
   adesso".
"""

from __future__ import annotations

from pathlib import Path

from support.js_harness import requires_node, run_js

POLICY_JS = (
    Path(__file__).resolve().parents[2]
    / "jenny" / "templates" / "ui" / "assets" / "shared" / "subagent-policy.js"
)


pytestmark = requires_node

# Helper JS condivisi dai casi: la forma del frame è quella di
# ``channels/subagent_activity_wire.py::activity_frame`` (envelope + finestra,
# piatto), quindi un refuso qui è un refuso sul contratto.
_PRELUDE = """
import assert from 'node:assert/strict';

let nextSeq = 1;
function event(kind, extra = {}) {
  return {
    seq: nextSeq++, ts: 1785841300 + nextSeq, kind, name: null, call_id: null,
    status: null, summary: `${kind} line`, duration_ms: null, ...extra,
  };
}
function windowOf(events, extra = {}) {
  return {
    events,
    since_seq: events.length ? events[0].seq - 1 : 0,
    first_seq: events.length ? events[0].seq : 0,
    last_seq: events.length ? events[events.length - 1].seq : 0,
    latest_seq: events.length ? events[events.length - 1].seq : 0,
    dropped: 0, gap: false, ...extra,
  };
}
function frame(events, extra = {}) {
  return { event: 'subagent_activity', chat_id: 'default', task_id: 't1',
           ...windowOf(events, extra) };
}
function seqs(state) { return state.events.map(e => e.seq); }
"""


def _run_js(script: str) -> str:
    source = POLICY_JS.read_text(encoding="utf-8") + _PRELUDE + script
    return run_js(source)


# ---------------------------------------------------------------------------
# Append vs replace, cursore, reconnect
# ---------------------------------------------------------------------------


def test_the_initial_frame_replaces_and_the_others_append() -> None:
    _run_js("""
      let state = saActivityInit('t1');
      // Risposta immediata al watch: rimpiazza (una riapertura del modal non
      // deve duplicare ciò che si aveva già).
      state = saActivityFrame(state, frame([event('phase'), event('iteration')],
                                           { initial: true })).state;
      assert.deepEqual(seqs(state), [1, 2]);
      assert.equal(state.cursor, 2);

      // Delta del pump: appende.
      state = saActivityFrame(state, frame([event('thinking')])).state;
      assert.deepEqual(seqs(state), [1, 2, 3]);
      assert.equal(state.cursor, 3);

      // Un secondo `initial` da zero (modal riaperto) ricomincia.
      nextSeq = 1;
      state = saActivityFrame(state, frame([event('phase')], { initial: true })).state;
      assert.deepEqual(seqs(state), [1]);
    """)


def test_a_reconnect_watch_does_not_wipe_what_we_already_read() -> None:
    """`initial` con `since > 0` è una ripresa, non una ripartenza."""
    _run_js("""
      let state = saActivityInit('t1');
      state = saActivityFrame(state, frame([event('phase'), event('iteration'),
                                            event('thinking')], { initial: true })).state;
      assert.deepEqual(seqs(state), [1, 2, 3]);

      // Socket caduta: il gateway ha dimenticato il watch. Si ri-watcha da
      // `cursor`, e la risposta iniziale contiene solo il nuovo.
      const resumed = saActivityFrame(state, frame([event('tool_start')], {
        initial: true, since_seq: 3,
      }));
      assert.deepEqual(seqs(resumed.state), [1, 2, 3, 4], 'la ripresa ha cancellato lo stream');
      assert.equal(resumed.state.cursor, 4);
    """)


def test_the_same_event_delivered_twice_appears_once() -> None:
    """Pump e risync possono portare lo stesso `seq`: la lista non lo duplica."""
    _run_js("""
      let state = saActivityInit('t1');
      const events = [event('tool_start'), event('tool_end')];
      state = saActivityFrame(state, frame(events, { initial: true })).state;
      state = saActivityFrame(state, frame(events)).state;
      state = saActivityIngest(state, windowOf(events)).state;
      assert.deepEqual(seqs(state), [1, 2]);
    """)


def test_the_cursor_never_goes_backwards() -> None:
    """Un frame vecchio (o una risync da un cursore basso) non riapre il passato."""
    _run_js("""
      let state = saActivityInit('t1');
      state = saActivityFrame(state, frame([event('phase'), event('phase'),
                                            event('phase')], { initial: true })).state;
      assert.equal(state.cursor, 3);
      state = saActivityIngest(state, windowOf([{ seq: 1, kind: 'phase', summary: 'x' }],
                                               { since_seq: 0, last_seq: 1 })).state;
      assert.equal(state.cursor, 3, 'il cursore è tornato indietro');
    """)


def test_a_frame_for_another_task_is_ignored() -> None:
    """Il modal si può chiudere e riaprire su un altro subagent: il frame in volo
    appartiene a una lista che non esiste più."""
    _run_js("""
      let state = saActivityInit('t1');
      const other = saActivityFrame(state, { ...frame([event('phase')]), task_id: 't2' });
      assert.equal(other.applied, false);
      assert.equal(other.state.events.length, 0);
      assert.equal(other.resyncFrom, null);
      // Nemmeno un frame senza task_id.
      assert.equal(saActivityFrame(state, { events: [] }).applied, false);
    """)


# ---------------------------------------------------------------------------
# Buchi e risincronizzazione
# ---------------------------------------------------------------------------


def test_a_declared_gap_asks_for_a_resync_from_the_old_cursor() -> None:
    _run_js("""
      let state = saActivityInit('t1');
      state = saActivityFrame(state, frame([event('phase')], { initial: true })).state;
      assert.equal(state.cursor, 1);

      // Il pump ha dovuto troncare: seq 2..4 non arrivano.
      nextSeq = 5;
      const applied = saActivityFrame(state, frame([event('iteration')], {
        since_seq: 1, first_seq: 5, gap: true,
      }));
      // Dal cursore di PRIMA: da quello di dopo la risync salterebbe il buco.
      assert.equal(applied.resyncFrom, 1);
      assert.equal(applied.state.cursor, 5);
    """)


def test_the_hole_is_visible_until_the_resync_fills_it() -> None:
    _run_js("""
      let state = saActivityInit('t1');
      state = saActivityFrame(state, frame([event('phase')], { initial: true })).state;
      nextSeq = 5;
      const gapped = saActivityFrame(state, frame([event('iteration')], {
        since_seq: 1, first_seq: 5, gap: true,
      }));
      state = gapped.state;

      // Il buco si dice: la riga che lo segue porta quanti eventi mancano.
      let view = saActivityRows(state);
      assert.deepEqual(view.rows.map(r => r.missing), [0, 3]);

      // Risync via HTTP dal cursore indicato: il ring ha ancora tutto.
      const healed = [{ seq: 2, kind: 'phase', summary: 'a' },
                      { seq: 3, kind: 'phase', summary: 'b' },
                      { seq: 4, kind: 'phase', summary: 'c' },
                      { seq: 5, kind: 'iteration', summary: 'iteration 1' }];
      state = saActivityIngest(state, windowOf(healed, { since_seq: gapped.resyncFrom })).state;
      assert.deepEqual(seqs(state), [1, 2, 3, 4, 5]);
      view = saActivityRows(state);
      assert.ok(view.rows.every(r => r.missing === 0), 'il marcatore non è sparito');
    """)


def test_an_unrecoverable_hole_stays_visible() -> None:
    """Se il ring ha già sfrattato quegli eventi, il buco resta segnato: è la
    cosa onesta, e l'unica alternativa è mentire."""
    _run_js("""
      let state = saActivityInit('t1');
      state = saActivityFrame(state, frame([event('phase')], { initial: true })).state;
      nextSeq = 5;
      state = saActivityFrame(state, frame([event('iteration')], {
        since_seq: 1, first_seq: 5, gap: true,
      })).state;
      // La risync riporta solo ciò che c'è ancora (nulla di nuovo).
      state = saActivityIngest(state, windowOf(
        [{ seq: 5, kind: 'iteration', summary: 'iteration 1' }],
        { since_seq: 1, first_seq: 5, gap: true, dropped: 3 },
      )).state;
      const view = saActivityRows(state);
      assert.equal(view.rows[1].missing, 3);
      assert.equal(view.dropped, 3);
    """)


def test_an_empty_window_is_a_wait_not_a_gap() -> None:
    """`latest_seq == 0` con zero eventi = non è ancora successo niente."""
    _run_js("""
      let state = saActivityInit('t1');
      const applied = saActivityFrame(state, {
        event: 'subagent_activity', task_id: 't1', initial: true, events: [],
        since_seq: 0, first_seq: 0, last_seq: 0, latest_seq: 0, dropped: 0, gap: false,
      });
      assert.equal(applied.applied, true);
      assert.equal(applied.resyncFrom, null, 'una finestra vuota non è un buco');
      const view = saActivityRows(applied.state);
      assert.equal(view.waiting, true);
      assert.deepEqual(view.rows, []);
    """)


def test_events_older_than_the_head_are_reported_once_at_the_top() -> None:
    """Sfratto dal ring: la prima riga porta il conteggio di ciò che precede."""
    _run_js("""
      let state = saActivityInit('t1');
      nextSeq = 43;
      state = saActivityFrame(state, frame([event('phase'), event('iteration')], {
        initial: true, since_seq: 0, first_seq: 43, gap: true, dropped: 42,
      })).state;
      const view = saActivityRows(state);
      assert.equal(view.headMissing, 42);
      assert.equal(view.rows[1].missing, 0);
    """)


def test_the_retained_events_are_capped() -> None:
    _run_js("""
      let state = saActivityInit('t1');
      const many = [];
      for (let i = 0; i < SA_ACTIVITY_KEEP + 50; i++) many.push(event('phase'));
      state = saActivityIngest(state, windowOf(many), { replace: true }).state;
      assert.equal(state.events.length, SA_ACTIVITY_KEEP);
      assert.equal(state.trimmed, true);
      // Si tengono i PIÙ RECENTI: la coda è ciò che dice cosa sta facendo adesso.
      assert.equal(state.events[state.events.length - 1].seq, many[many.length - 1].seq);
    """)


# ---------------------------------------------------------------------------
# Righe: in corso vs finito, accoppiamento, collasso del ragionamento
# ---------------------------------------------------------------------------


def test_a_tool_stays_in_progress_until_its_end_arrives() -> None:
    _run_js("""
      let state = saActivityInit('t1');
      state = saActivityFrame(state, frame([
        event('tool_start', { name: 'grep', call_id: 'c1', summary: 'grepping src for "foo"' }),
      ], { initial: true })).state;
      let view = saActivityRows(state);
      assert.equal(view.rows.length, 1);
      assert.equal(view.rows[0].kind, 'tool');
      assert.equal(view.rows[0].pending, true, 'una chiamata in volo non è finita');
      assert.equal(view.rows[0].outcome, '');
      assert.equal(view.pending, 1);

      state = saActivityFrame(state, frame([
        event('tool_end', { name: 'grep', call_id: 'c1', status: 'ok',
                            summary: '12 matches in 3 files', duration_ms: 210 }),
      ])).state;
      view = saActivityRows(state);
      // Una riga sola: azione -> esito, con durata e status della fine.
      assert.equal(view.rows.length, 1);
      assert.equal(view.rows[0].pending, false);
      assert.equal(view.rows[0].summary, 'grepping src for "foo"');
      assert.equal(view.rows[0].outcome, '12 matches in 3 files');
      assert.equal(view.rows[0].status, 'ok');
      assert.equal(view.rows[0].durationMs, 210);
      assert.equal(view.pending, 0);
    """)


def test_concurrent_calls_of_the_same_tool_pair_by_call_id() -> None:
    """Tre `web_fetch` nello stesso batch sono il caso normale: per nome
    l'accoppiamento sarebbe casuale, e l'esito finirebbe sulla riga sbagliata."""
    _run_js("""
      let state = saActivityInit('t1');
      state = saActivityFrame(state, frame([
        event('tool_start', { name: 'web_fetch', call_id: 'f1', summary: 'opening a.example' }),
        event('tool_start', { name: 'web_fetch', call_id: 'f2', summary: 'opening b.example' }),
        event('tool_start', { name: 'web_fetch', call_id: 'f3', summary: 'opening c.example' }),
      ], { initial: true })).state;
      // Finisce per secondo quello aperto per terzo.
      state = saActivityFrame(state, frame([
        event('tool_end', { name: 'web_fetch', call_id: 'f3', status: 'error',
                            summary: 'timed out after 20s', duration_ms: 20000 }),
      ])).state;
      const view = saActivityRows(state);
      assert.deepEqual(view.rows.map(r => r.summary),
        ['opening a.example', 'opening b.example', 'opening c.example']);
      assert.deepEqual(view.rows.map(r => r.pending), [true, true, false]);
      assert.equal(view.rows[2].outcome, 'timed out after 20s');
      assert.equal(view.rows[2].status, 'error');
    """)


def test_a_tool_end_without_its_start_still_shows_its_outcome() -> None:
    _run_js("""
      let state = saActivityInit('t1');
      nextSeq = 9;
      state = saActivityFrame(state, frame([
        event('tool_end', { name: 'read_file', call_id: 'x', status: 'ok',
                            summary: '120 of 412 lines', duration_ms: 40 }),
      ], { initial: true })).state;
      const view = saActivityRows(state);
      assert.equal(view.rows.length, 1);
      assert.equal(view.rows[0].kind, 'tool');
      assert.equal(view.rows[0].outcome, '120 of 412 lines');
      assert.equal(view.rows[0].pending, false);
    """)


def test_a_run_of_thinking_collapses_into_one_row_that_ticks() -> None:
    """Il server manda un `thinking` ogni 0.4s **anche a testo invariato**: è ciò
    che fa ticchettare la durata, ma come righe distinte seppellirebbe tutto."""
    _run_js("""
      let state = saActivityInit('t1');
      const updates = [];
      for (let i = 0; i < 20; i++) {
        updates.push(event('thinking', {
          summary: 'thinking: still weighing the two sources', duration_ms: 1000 + i * 400,
        }));
      }
      state = saActivityIngest(state, windowOf(updates), { replace: true }).state;
      const view = saActivityRows(state);
      assert.equal(view.rows.length, 1, 'venti aggiornamenti, una riga');
      assert.equal(view.rows[0].repeats, 20);
      assert.equal(view.rows[0].durationMs, 1000 + 19 * 400, 'la durata è quella ultima');
      assert.equal(view.rows[0].pending, true, 'in coda allo stream: sta pensando adesso');
      assert.equal(view.count, 20, 'il conteggio resta quello degli eventi');
    """)


def test_a_finished_run_of_thinking_is_no_longer_in_progress() -> None:
    _run_js("""
      let state = saActivityInit('t1');
      state = saActivityIngest(state, windowOf([
        event('thinking', { summary: 'thinking: a', duration_ms: 500 }),
        event('thinking', { summary: 'thinking: b', duration_ms: 900 }),
        event('iteration', { summary: 'iteration 1' }),
      ]), { replace: true }).state;
      const view = saActivityRows(state);
      assert.equal(view.rows.length, 2);
      assert.equal(view.rows[0].pending, false, 'un evento dopo chiude il ragionamento');
      assert.equal(view.pending, 0);
    """)


def test_thinking_and_writing_are_two_runs_not_one() -> None:
    """Stesso kind, due attività diverse per chi guarda: il ragionamento e il
    testo della risposta che si sta formando."""
    _run_js("""
      let state = saActivityInit('t1');
      state = saActivityIngest(state, windowOf([
        event('thinking', { summary: 'thinking: which source do I trust', duration_ms: 800 }),
        event('thinking', { summary: 'writing: The Titan 2 secondary display', duration_ms: 300 }),
        event('thinking', { summary: 'writing: The Titan 2 display is', duration_ms: 700 }),
      ]), { replace: true }).state;
      const view = saActivityRows(state);
      assert.equal(view.rows.length, 2);
      assert.deepEqual(view.rows.map(r => r.label), ['thinking', 'writing']);
      assert.equal(view.rows[1].repeats, 2);
    """)


def test_a_hole_inside_a_run_of_thinking_breaks_the_row() -> None:
    """Fondere due aggiornamenti separati da un buco significherebbe nascondere
    il buco dentro una riga."""
    _run_js("""
      let state = saActivityInit('t1');
      state = saActivityIngest(state, windowOf([
        { seq: 1, kind: 'thinking', summary: 'thinking: a', duration_ms: 400 },
        { seq: 7, kind: 'thinking', summary: 'thinking: b', duration_ms: 4000 },
      ]), { replace: true }).state;
      const view = saActivityRows(state);
      assert.equal(view.rows.length, 2);
      assert.equal(view.rows[1].missing, 5);
    """)


def test_an_event_without_a_duration_has_no_duration() -> None:
    """`duration_ms: null` è la norma su fase/iterazione/messaggio: mostrarlo
    come "0ms" era una durata inventata su ogni riga strutturale."""
    _run_js("""
      let state = saActivityInit('t1');
      state = saActivityIngest(state, windowOf([
        event('phase', { summary: 'running tools' }),
        event('tool_end', { name: 'grep', summary: 'no matches', status: 'ok', duration_ms: 0 }),
      ]), { replace: true }).state;
      const view = saActivityRows(state);
      assert.equal(view.rows[0].durationMs, null);
      assert.equal(view.rows[1].durationMs, 0, 'zero misurato resta zero');
    """)


def test_an_event_without_a_seq_is_not_deliverable() -> None:
    """Il `seq` è ciò che rende lo stream verificabile: tenerne uno senza
    romperebbe il cursore di chi lo legge."""
    _run_js("""
      let state = saActivityInit('t1');
      state = saActivityIngest(state, windowOf([
        { kind: 'phase', summary: 'no seq' },
        { seq: 0, kind: 'phase', summary: 'zero' },
        { seq: 'x', kind: 'phase', summary: 'not a number' },
        { seq: 4, kind: 'phase', summary: 'good' },
      ], { last_seq: 4 }), { replace: true }).state;
      assert.deepEqual(seqs(state), [4]);
    """)


def test_a_malformed_frame_never_throws() -> None:
    """I frame arrivano dal filo: una forma inattesa non deve rompere la vista."""
    _run_js("""
      for (const payload of [null, undefined, {}, { events: 'nope' }, { events: [null, 3] },
                             { events: [{ seq: 1 }], gap: 'yes' }]) {
        const out = saActivityIngest(saActivityInit('t1'), payload);
        assert.ok(Array.isArray(out.state.events));
        const view = saActivityRows(out.state);
        assert.ok(Array.isArray(view.rows));
      }
      for (const frame of [null, undefined, {}, { task_id: 't1' }]) {
        const out = saActivityFrame(saActivityInit('t1'), frame);
        assert.ok(Array.isArray(out.state.events));
      }
      assert.deepEqual(saActivityRows(null).rows, []);
      assert.deepEqual(saActivityRows({ events: 'nope' }).rows, []);
    """)


# ---------------------------------------------------------------------------
# Digest ("cosa ha fatto davvero")
# ---------------------------------------------------------------------------


def test_a_digest_with_nothing_in_it_renders_no_block() -> None:
    """`source: "none"` = niente blocco. Un accordion che si apre sul vuoto è
    peggio della sua assenza."""
    _run_js("""
      for (const payload of [{ events: [], count: 0, source: 'none' },
                             { events: [], count: 0, source: 'digest' },
                             null, {}]) {
        const view = saDigestView(payload);
        assert.equal(view.show, false);
        assert.equal(view.source, 'none');
        assert.deepEqual(view.rows, []);
      }
    """)


def test_a_live_digest_is_flagged_as_a_preview() -> None:
    _run_js("""
      const events = [{ seq: 1, kind: 'tool', name: 'grep', status: 'ok',
                        summary: 'grepping src -> 12 matches', duration_ms: 210 }];
      assert.equal(saDigestView({ events, count: 1, source: 'live' }).live, true);
      assert.equal(saDigestView({ events, count: 1, source: 'digest' }).live, false);
      assert.equal(saDigestView({ events, count: 1, source: 'digest' }).show, true);
    """)


def test_the_digest_kind_tool_renders_like_a_finished_call() -> None:
    """Il digest arriva con start/end già collassati: una forma di riga sola per
    la chat e per il modal."""
    _run_js("""
      const view = saDigestView({ source: 'digest', count: 3, events: [
        { seq: 1, kind: 'thinking', summary: 'thinking: 14 steps, 41.2s total',
          duration_ms: 41200 },
        { seq: 2, kind: 'tool', name: 'web_fetch', status: 'error',
          summary: 'opening a.example -> timed out after 20s', duration_ms: 20010 },
        { seq: 3, kind: 'tool', name: 'write_file', status: 'incomplete',
          summary: 'writing out.md (no result recorded)' },
      ]});
      assert.equal(view.show, true);
      assert.deepEqual(view.rows.map(r => r.kind), ['thinking', 'tool', 'tool']);
      assert.equal(view.rows[1].status, 'error');
      assert.equal(view.rows[1].durationMs, 20010);
      // `incomplete` non è "fallito": è "non lo sappiamo", e resta segnalato.
      assert.equal(view.rows[2].status, 'incomplete');
      assert.equal(view.rows[2].pending, true);
      // Nessun buco: i seq del digest sono rinumerati da 1 e contigui.
      assert.ok(view.rows.every(r => r.missing === 0));
    """)
