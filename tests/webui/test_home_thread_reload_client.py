"""Rileggere il filo della casa mentre succede qualcosa: frame vivi, un'altra
rilettura, una lettura che non arriva.

Il filo si rilegge in quattro occasioni — l'avvio, un cambio di conversazione,
una riconnessione, un ``session_boundary`` — e in tutte la lettura e' una
fetch che dura: nel frattempo il socket continua a portare frame. I difetti
misurati qui stanno tutti in quell'attesa:

- la bolla viva che arriva durante la lettura finiva *sopra* tutta la
  storia, perche' la storia si appendeva dopo di lei;
- due riletture della stessa conversazione insieme disegnavano il
  filo due volte;
- la riga di un rifiuto restava nella conversazione dopo, perche'
  la ricarica toglieva i messaggi e i confini ma non le note;
- una lettura fallita all'avvio non si riprovava piu', una
  fallita al resync lasciava il filo vuoto senza dirlo, e quella del
  ``session_boundary`` era un rifiuto di promessa che nessuno prendeva.

Si guida la casa intera (``support.home_dom``): il legame fra la chat, il
guscio e il socket e' proprio quel che una classe finta riscriverebbe.
"""

from __future__ import annotations

from support.home_dom import requires_jsdom, run_home

pytestmark = requires_jsdom

_HEAD = """
import assert from 'node:assert/strict';
import {
  boot, tick, until, frame, threads, hooks, failed, thread, unhandled, FakeWS, locales, $,
} from './boot.mjs';
threads['websocket:default'] = { messages: [
  { role: 'user', text: 'hello' },
  { role: 'assistant', text: 'hello to you', turnId: 'h' },
] };
threads['project:orto'] = { messages: [
  { role: 'user', text: 'Q1' },
  { role: 'assistant', text: 'A1', turnId: 'a' },
  { role: 'user', text: 'Q2 in progress' },
] };
/* La storia arriva tardi: il tempo in cui il socket porta i frame vivi. */
const slowThread = (ms) => {
  hooks.fetch = async (u) => { if (u.pathname.endsWith('/webui-thread')) await tick(ms); };
};
/* Come `slowThread`, ma ogni lettura della storia resta in volo finche' la
   prova non la libera (`reads[i].open()`). Dove conta che un frame arrivi
   *durante* la lettura, un tempo non lo garantisce: sotto carico un `tick(30)`
   torna dopo i 120 ms della lettura, e la prova misura un'altra corsa. */
const gatedThread = () => {
  const reads = [];
  hooks.fetch = async (u) => {
    if (!u.pathname.endsWith('/webui-thread')) return undefined;
    const read = {};
    const passed = new Promise((r) => { read.open = r; });
    reads.push(read);
    await passed;
    return undefined;
  };
  return reads;
};
const reconnect = async () => {
  FakeWS.last.onclose();
  FakeWS.last.readyState = 3;
  // Lo stesso modulo che usa la casa: la riconnessione del ws-manager, subito.
  const { wsManager } = await import(__WS__);
  wsManager.connectChat();
};
"""


def _run(body: str) -> str:
    from support.home_dom import UI

    ws = (UI / "assets" / "shared" / "ws-manager.js").as_posix()
    return run_home(_HEAD.replace("__WS__", repr(ws)) + body)


def test_a_live_answer_stays_below_the_history_when_you_enter_its_conversation() -> None:
    """Entri nel quaderno mentre Jenny ci sta rispondendo."""
    _run("""
const app = await boot();
const reads = gatedThread();
const opening = app.switchConversation('project:orto');
await until(() => reads.length === 1, { msg: 'il quaderno non ha chiesto la sua storia' });
frame({ event: 'delta', chat_id: 'project:orto', turn_id: 'b', text: 'Here is the answer' });
await tick(40);
frame({ event: 'delta', chat_id: 'project:orto', turn_id: 'b', text: ' to Q2' });
reads[0].open();
await opening;
await until(() => {
  assert.deepEqual(thread(), [
    'you: Q1', 'jenny: A1', 'you: Q2 in progress', 'jenny: Here is the answer to Q2',
  ]);
  return true;
});
""")


def test_a_live_answer_stays_below_the_history_across_a_resync() -> None:
    """Il resync a meta' turno: i delta arrivati durante la rilettura
    stanno sotto la storia riletta."""
    _run("""
const app = await boot();
assert.deepEqual(thread(), ['you: hello', 'jenny: hello to you']);
threads['websocket:default'].messages.push({ role: 'user', text: 'and now?' });
const reads = gatedThread();
await reconnect();
await until(() => reads.length, { msg: 'il resync non ha riletto la storia' });
frame({ event: 'delta', chat_id: 'default', turn_id: 'n', text: 'now this' });
reads.forEach((r) => r.open());
await until(() => {
  assert.deepEqual(thread(),
    ['you: hello', 'jenny: hello to you', 'you: and now?', 'jenny: now this']);
  return true;
});
""")


def test_two_reloads_of_the_same_conversation_draw_it_once() -> None:
    """Due riletture insieme, e il filo resta uno."""
    _run("""
const app = await boot();
slowThread(40);
await Promise.all([app.chat.reload(), app.chat.reload()]);
assert.deepEqual(thread(), ['you: hello', 'jenny: hello to you']);
""")


def test_a_reconnect_during_a_switch_does_not_duplicate_the_thread() -> None:
    """Due riletture, nella forma che capita davvero: si cambia conversazione e il socket
    si riapre mentre la lettura e' in volo."""
    _run("""
const app = await boot();
await app.showConversation('project:orto');
await tick(20);
slowThread(120);
const back = app.showConversation('websocket:default');
await tick(20);
await reconnect();
await back;
await tick(300);
assert.deepEqual(thread(), ['you: hello', 'jenny: hello to you']);
""")


def test_a_refusal_note_does_not_follow_you_into_another_conversation() -> None:
    """La riga di rifiuto e' della conversazione in cui e' nata."""
    _run("""
const app = await boot();
await app.showConversation('project:orto');
await tick(20);
app.chat.noteRefusal('too_many_images');
assert.ok(thread().some((row) => row.startsWith('note: ')), 'la nota non e\\u2019 comparsa');
await app.showConversation('websocket:default');
await tick(20);
assert.deepEqual(thread(), ['you: hello', 'jenny: hello to you']);
""")


def test_a_history_that_failed_at_startup_is_read_again() -> None:
    """La prima lettura fallisce; la visibilita', Home e l'apertura della
    chat la riprovano, e finche' non riesce lo si dice."""
    _run("""
let reads = 0;
let down = true;
hooks.fetch = async (u) => {
  if (!u.pathname.endsWith('/webui-thread')) return undefined;
  reads += 1;
  return down ? failed(503) : undefined;
};
const app = await boot();
await tick(50);
const errorText = app.emptyText.textContent;
assert.ok(reads >= 1);
assert.equal($('home-empty').hidden, false, 'la chat irraggiungibile sembra vuota');
assert.ok([locales.it.home.threadError, locales.en.home.threadError].includes(errorText), errorText);
let before = reads;

// Ancora giu': la visibilita' riprova, e l'avviso resta.
document.dispatchEvent(new window.Event('visibilitychange'));
await tick(50);
assert.equal(reads, before + 1, 'tornare a guardare non riprova');
assert.equal(app.emptyText.textContent, errorText);

// Home riprova.
before = reads;
app.goHome();
await tick(50);
assert.equal(reads, before + 1, 'Home non riprova');

// L'apertura della chat da un avviso riprova, e stavolta arriva.
down = false;
before = reads;
app.openChat();
await tick(80);
assert.equal(reads, before + 1, 'aprire la chat non riprova');
assert.deepEqual(thread(), ['you: hello', 'jenny: hello to you']);
assert.equal($('home-empty').hidden, true);
""")


def test_a_failed_resync_keeps_the_thread_and_says_so() -> None:
    """La rilettura dopo una riconnessione fallisce. Il filo non si
    svuota in silenzio: quel che c'era resta, e una riga dice che non e'
    stato riletto; la volta dopo si riprova."""
    _run("""
const app = await boot();
let down = true;
hooks.fetch = async (u) => (down && u.pathname.endsWith('/webui-thread') ? failed(503) : undefined);
await reconnect();
await tick(80);
const rows = thread();
assert.deepEqual(rows.slice(0, 2), ['you: hello', 'jenny: hello to you'], 'il filo si e\\u2019 svuotato');
assert.equal(rows.length, 3);
assert.ok(rows[2].startsWith('note: '), 'il fallimento non si vede');
down = false;
document.dispatchEvent(new window.Event('visibilitychange'));
await tick(80);
assert.deepEqual(thread(), ['you: hello', 'jenny: hello to you']);
assert.deepEqual(unhandled, []);
""")


def test_a_session_boundary_that_cannot_be_read_is_not_an_unhandled_rejection() -> None:
    """Il ``session_boundary`` rilegge il filo; se la lettura fallisce,
    lo si dice e non resta un rifiuto di promessa senza padrone."""
    _run("""
const app = await boot();
hooks.fetch = async (u) => (u.pathname.endsWith('/webui-thread') ? failed(503) : undefined);
frame({ event: 'message', chat_id: 'default', session_boundary: true });
await tick(80);
assert.deepEqual(unhandled, []);
const rows = thread();
assert.ok(rows.at(-1).startsWith('note: '), `nessun avviso: ${JSON.stringify(rows)}`);
""")


_PAGED = """
threads['websocket:default'] = { messages: [
  { role: 'user', text: 'Q3' }, { role: 'assistant', text: 'A3', turnId: 'c' },
], page: { before_cursor: 'c1', has_more_before: true } };
const olderPage = () => ({ ok: true, status: 200, json: async () => ({ messages: [
  { role: 'user', text: 'Q1' }, { role: 'assistant', text: 'A1', turnId: 'a' }],
  page: { before_cursor: null, has_more_before: false } }) });
/* La pagina precedente torna dopo `pageMs`, la storia recente dopo `threadMs`. */
const slowPaging = (pageMs, threadMs) => {
  hooks.fetch = async (u) => {
    if (!u.pathname.endsWith('/webui-thread')) return undefined;
    if (u.searchParams.get('before')) { await tick(pageMs); return olderPage(); }
    await tick(threadMs);
    return undefined;
  };
};
/* Come `slowPaging`, ma l'ordine lo decide la prova: ogni risposta parte quando
   la prova la libera (`open()`), non dopo un tempo. Sotto carico (`-n auto`) un
   `tick(5)` puo' tornare con 80 ms di ritardo, e allora la storia da 100 ms
   arrivava prima della pagina da 20: un'altra corsa, con un'altra risposta
   giusta. `asked` conta le richieste arrivate al cancello. */
const gatedPaging = () => {
  const gate = () => {
    const g = { asked: 0 };
    g.passed = new Promise((r) => { g.open = r; });
    return g;
  };
  const gates = { page: gate(), thread: gate() };
  hooks.fetch = async (u) => {
    if (!u.pathname.endsWith('/webui-thread')) return undefined;
    const g = u.searchParams.get('before') ? gates.page : gates.thread;
    g.asked += 1;
    await g.passed;
    return g === gates.page ? olderPage() : undefined;
  };
  return gates;
};
"""


def test_an_older_page_loaded_during_a_reload_does_not_end_up_below_the_history() -> None:
    """Si scorre in su mentre il filo si sta rileggendo: la pagina piu'
    vecchia entra in cima prima che la storia recente arrivi. La storia non le
    finisce sopra, e il tocco dopo non la riaggiunge una seconda volta."""
    _run(_PAGED + """
const app = await boot();
assert.deepEqual(thread(), ['you: Q3', 'jenny: A3']);
const gates = gatedPaging();
const reading = app.chat.reload();
await until(() => gates.thread.asked, { msg: 'la rilettura non ha chiesto la storia' });
const paging = app.chat.pager.loadMore();
await until(() => gates.page.asked, { msg: 'lo scorrimento non ha chiesto la pagina' });
gates.page.open();
await paging;
assert.deepEqual(thread(), ['you: Q1', 'jenny: A1', 'you: Q3', 'jenny: A3']);
gates.thread.open();
await reading;
await until(() => {
  assert.deepEqual(thread(), ['you: Q3', 'jenny: A3'], 'la storia e\\u2019 entrata sopra la pagina');
  return true;
});
assert.equal(app.chat.pager.cursor, 'c1');
assert.equal(app.chat.pager.hasMore, true);
await app.chat.pager.loadMore();
await until(() => {
  assert.deepEqual(thread(), ['you: Q1', 'jenny: A1', 'you: Q3', 'jenny: A3']);
  return true;
});
""")


def test_an_older_page_asked_before_a_reload_is_not_added_twice() -> None:
    """La stessa corsa, partita dall'altro capo: lo scorrimento chiede la
    pagina, poi arriva la rilettura, e la pagina torna mentre la rilettura e'
    ancora in volo."""
    _run(_PAGED + """
const app = await boot();
slowPaging(60, 100);
const paging = app.chat.pager.loadMore();
await tick(5);
const reading = app.chat.reload();
await paging;
await reading;
await tick(30);
assert.deepEqual(thread(), ['you: Q3', 'jenny: A3']);
await app.chat.pager.loadMore();
await tick(100);
assert.deepEqual(thread(), ['you: Q1', 'jenny: A1', 'you: Q3', 'jenny: A3']);
""")


def test_a_live_answer_survives_two_overlapping_reloads() -> None:
    """La seconda rilettura parte quando la bolla viva c'e' gia': e' nata
    durante la prima, e nessuna delle due la butta. Solo il segno messo sul
    nodo quando nasce la distingue dalla storia che se ne va."""
    _run("""
const app = await boot();
const reads = gatedThread();
const first = app.chat.reload();
await until(() => reads.length === 1);
frame({ event: 'delta', chat_id: 'default', turn_id: 'n', text: 'live' });
await until(() => thread().includes('jenny: live'), { msg: 'la bolla viva non e\\u2019 nata' });
const second = app.chat.reload();
await until(() => reads.length === 2);
reads[0].open();
await first;
reads[1].open();
await second;
await until(() => {
  const rows = thread();
  assert.deepEqual(rows.slice(0, 2), ['you: hello', 'jenny: hello to you']);
  assert.ok(rows.slice(2).includes('jenny: live'), `la bolla viva e\\u2019 sparita: ${JSON.stringify(rows)}`);
  return true;
});
""")


def test_a_superseded_read_that_fails_is_not_reported() -> None:
    """Due letture in volo, la prima fallisce quando la seconda e' gia'
    partita: decide l'ultima, e il suo filo non si segna come illeggibile."""
    _run("""
const app = await boot();
let n = 0;
hooks.fetch = async (u) => {
  if (!u.pathname.endsWith('/webui-thread')) return undefined;
  n += 1;
  if (n === 1) { await tick(100); return failed(503); }
  return undefined;
};
const a = app._readThread();
await tick(10);
const b = app._readThread();
await Promise.all([a, b]);
await tick(30);
assert.equal(app._threadFailed, false, 'una lettura scavalcata segna il filo come fallito');
assert.deepEqual(thread(), ['you: hello', 'jenny: hello to you']);
""")


def test_a_helper_report_reads_back_as_one_bubble() -> None:
    """La frase e il file del rientro di un aiutante, dal transcript alla casa.

    Il rientro e' un turno di sistema: dal 28/10/2026 ha un id (``system:…``),
    e la rilettura accorpa per id (`_buildTurns`, regola 4). Prima ricaricando
    stavano in due bolle, dal vivo in una.
    """
    import json

    from jenny.webui.transcript_replay import replay_transcript_to_ui_messages

    turn = {"turn_id": "system:r1"}
    messages = replay_transcript_to_ui_messages([
        {"event": "user", "chat_id": "default", "text": "cerca la calathea"},
        {"event": "message", "text": "Ci lavora un aiutante.", "turn_id": "u1"},
        {"event": "turn_end", "turn_id": "u1"},
        {"event": "message", "kind": "progress", "text": "ecco la ricerca", **turn},
        {"event": "message", "kind": "progress", "text": "",
         "tool_events": [{"phase": "start", "name": "message"}], **turn},
        {"event": "message", "text": "", **turn,
         "media_urls": [{"url": "/m/r.md", "name": "r.md"}]},
        {"event": "turn_end", **turn},
    ])
    _run(f"""
threads['websocket:default'] = {{ messages: {json.dumps(messages)} }};
await boot();
await tick(80);
assert.deepEqual(thread().slice(-2), ['jenny: Ci lavora un aiutante.', 'jenny: ecco la ricerca']);
const last = [...$('home-thread').querySelectorAll('.home-msg')].at(-1);
assert.match(last.textContent, /r\\.md/);
""")
