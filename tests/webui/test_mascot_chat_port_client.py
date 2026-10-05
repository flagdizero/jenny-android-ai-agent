"""Jenny e' la porta della chat: cosa fanno il tocco e lo swipe, posto per posto.

Dal 05/10/2026 il tocco su di lei apre la chat vera dove la chat non e' a
schermo, la chiude dove e' aperta sopra una pagina, e dove la chat c'e' gia'
(la pagina chat, la Console) lei saluta. Lo swipe verso l'interno e' come il
tocco, quello verso il bordo «vai via». Senza adattatore resta il gesto di
prima, e la minichat — che non lo passa — continua a funzionare.

In node, col modulo vero (``shared/jenny-mascot.js``) e i vicini finti: si
chiamano ``_onTap`` e ``_onGestureOut``, le stesse funzioni che la fisica
(``mascot-drag.js``) chiama a fine gesto.
"""

from __future__ import annotations

import shutil
import tempfile
import textwrap
from pathlib import Path

from support.js_harness import requires_node, run_module

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"

pytestmark = requires_node

_NEIGHBORS = {
    "ws-manager.js": "export const wsManager = new EventTarget();\n",
    "session-manager.js": (
        "export const sessionManager = Object.assign(new EventTarget(), "
        "{ currentChatId: 'default' });\n"
    ),
    "mascot.js": """
export function mascotVisible() { return true; }
export function applyMascotSize() {}
""",
    "mascot-drag.js": """
export function bindMascotDrag() {}
export function buildFlyLayer() {}
""",
}

_PRELUDE = """
import assert from 'node:assert/strict';
globalThis.window = { matchMedia: () => ({ matches: false }) };
globalThis.performance = globalThis.performance || { now: () => Date.now() };
const { JennyMascot } = await import('./shared/jenny-mascot.js');

function classes(...initial) {
  const s = new Set(initial);
  return {
    has: (c) => s.has(c),
    add: (c) => s.add(c), remove: (...cs) => cs.forEach((c) => s.delete(c)),
    contains: (c) => s.has(c), toggle: (c, on) => (on ? s.add(c) : s.delete(c)),
  };
}
function img() {
  const attrs = {};
  return {
    classList: classes(),
    getAttribute: (k) => attrs[k] ?? null,
    set src(v) { attrs.src = v; }, get src() { return attrs.src; },
  };
}

/* Jenny senza il suo DOM vero: classi, attributi, corpo e faccia finti. */
function jenny({ out = true, chat = null } = {}) {
  const j = Object.create(JennyMascot.prototype);
  const attrs = {};
  Object.assign(j, {
    mode: 'chat', _chat: chat, _agentState: 'idle', _turnActive: false, _pendingTurn: false,
    _streamTurnId: null, _runSeen: false, _lastClosedTurnId: null,
    _mood: null, _moodUntil: 0, _moodTimer: null,
    _motionNow: { lean: null, squash: null }, _motionTimers: { lean: null, squash: null },
    _greetTimer: null, _slideUntil: 0,
    _labelNote: '',
    _talk: { timer: null, animIdx: 0, open: false, lastTextAt: 0, switchAt: 0 },
    el: {
      classList: out ? classes('out') : classes(),
      setAttribute: (k, v) => { attrs[k] = v; },
      getAttribute: (k) => attrs[k] ?? null,
      removeAttribute: (k) => { delete attrs[k]; },
      style: { setProperty: (k, v) => { attrs[k] = v; } },
      offsetWidth: 0,
    },
    img: img(), face: img(),
  });
  j._updateGestureExclusion = () => {};
  return j;
}

/* La porta finta: dice dov'e' la chat e annota cosa le si chiede. */
function door({ isOpen = false, onScreen = false, canOpen } = {}) {
  const calls = [];
  const d = {
    calls,
    isOpen: () => isOpen,
    onScreen: () => onScreen,
    open: (opts) => calls.push(['open', opts.gesture]),
    close: () => calls.push(['close']),
  };
  if (canOpen !== undefined) d.canOpen = () => canOpen;
  return d;
}
const out = (j) => j.el.classList.contains('out');
const squash = (j) => j.el.getAttribute('data-squash');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
"""


def _run(body: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "shared").mkdir()
        shutil.copy(ASSETS / "shared" / "jenny-mascot.js", root / "shared" / "jenny-mascot.js")
        shutil.copy(ASSETS / "shared" / "bot-name.js", root / "shared" / "bot-name.js")
        for name, text in _NEIGHBORS.items():
            (root / "shared" / name).write_text(text, encoding="utf-8")
        entry = root / "prova.mjs"
        entry.write_text(_PRELUDE + textwrap.dedent(body) + "\nprocess.exit(0);\n", encoding="utf-8")
        run_module(entry)


def test_a_tap_where_the_chat_is_not_opens_it() -> None:
    _run("""
        const d = door();
        const j = jenny({ out: false, chat: d });
        j._onTap();
        assert.deepEqual(d.calls, [['open', 'tap']]);
    """)


def test_a_tap_on_the_open_chat_closes_it() -> None:
    _run("""
        const d = door({ isOpen: true });
        const j = jenny({ chat: d });
        j._onTap();
        assert.deepEqual(d.calls, [['close']]);
    """)


def test_a_tap_where_the_chat_is_on_screen_says_hello() -> None:
    """Fuori all'angolo: il saluto vero, niente da aprire. L'arte del saluto la
    accende il CSS nel suo livello: corpo e faccia di riposo non cambiano."""
    _run("""
        const d = door({ onScreen: true });
        const j = jenny({ chat: d });
        j._onTap();
        assert.deepEqual(d.calls, []);
        assert.equal(squash(j), 'greet');
        assert.equal(j.el.getAttribute('--jenny-squash-ms'), '1500ms', 'la durata la scrive il JS');
        j._syncArt();
        assert.match(j.face.src, /face-front-normal/);
        assert.match(j.img.src, /body-front-idle/);
    """)


def test_a_tap_brings_her_out_from_the_edge_before_the_hello() -> None:
    """Il saluto aspetta la fine dello scivolamento: un braccio alzato mentre e'
    ancora mezza dietro il bordo non si vedrebbe."""
    _run("""
        const d = door({ onScreen: true });
        const j = jenny({ out: false, chat: d });
        j._onTap();
        assert.ok(out(j), 'al bordo non si vedrebbe');
        assert.equal(squash(j), null, 'ha salutato mentre usciva');
        await sleep(340);
        assert.equal(squash(j), 'greet');
    """)


def test_the_hello_does_not_beat_thinking() -> None:
    """Pensa e parla hanno la precedenza: niente braccio alzato in mezzo a un
    turno, resta il saltello."""
    _run("""
        const d = door({ onScreen: true });
        const j = jenny({ chat: d });
        j._agentState = 'thinking';
        j._onTap();
        j._syncArt();
        assert.match(j.face.src, /face-front-thinking/);
        assert.match(j.img.src, /body-front-think/);
        assert.equal(squash(j), 'hop', 'il saltello resta');
    """)


def test_at_the_edge_the_hello_is_a_hop() -> None:
    """Dove la chat non si apre lo swipe la rimanda al bordo, e li' risponde:
    di lato un braccio alzato non c'e', il saltello si'."""
    _run("""
        const d = door({ canOpen: false });
        const j = jenny({ out: false, chat: d });
        j._onGestureOut(true);
        assert.equal(out(j), false);
        assert.equal(squash(j), 'hop');
    """)


def test_the_hello_ends() -> None:
    _run("""
        const d = door({ onScreen: true });
        const j = jenny({ chat: d });
        j._onTap();
        await sleep(1560);
        assert.equal(squash(j), null, 'tolto a tempo, anche senza animationend');
    """)


def test_a_turn_that_starts_stops_the_hello() -> None:
    """Un messaggio inviato a meta' saluto: il braccio non resta alzato sopra
    il pensa."""
    _run("""
        const d = door({ onScreen: true });
        const j = jenny({ chat: d });
        j._onTap();
        j._setAgentState('thinking');
        assert.equal(squash(j), null);
    """)


def test_going_back_to_the_edge_stops_the_hello() -> None:
    _run("""
        const d = door({ onScreen: true });
        const j = jenny({ chat: d });
        j._onTap();
        j._onGestureOut(false);
        assert.equal(squash(j), null);
    """)


def test_a_new_gesture_replaces_the_one_on_its_channel() -> None:
    _run("""
        const j = jenny();
        j.hello();
        j.nudge();
        assert.equal(squash(j), 'nudge');
        await sleep(560);
        assert.equal(squash(j), null, "il timer del saluto non ha tolto il sobbalzo, l'ha tolto il suo");
    """)


def test_where_the_chat_cannot_open_she_only_says_hello() -> None:
    _run("""
        const d = door({ canOpen: false });
        const j = jenny({ chat: d });
        j._onTap();
        assert.deepEqual(d.calls, []);
        assert.equal(squash(j), 'greet');
    """)


def test_an_inward_swipe_opens_the_chat_like_a_tap() -> None:
    _run("""
        const d = door();
        const j = jenny({ out: false, chat: d });
        j._onGestureOut(true);
        assert.deepEqual(d.calls, [['open', 'swipe']]);
    """)


def test_a_swipe_to_the_edge_closes_the_open_chat() -> None:
    _run("""
        const d = door({ isOpen: true });
        const j = jenny({ chat: d });
        j._onGestureOut(false);
        assert.deepEqual(d.calls, [['close']]);
    """)


def test_on_the_chat_page_the_swipes_only_move_her() -> None:
    _run("""
        const d = door({ onScreen: true });
        const j = jenny({ chat: d });
        j._onGestureOut(false);
        assert.equal(out(j), false, 'messa via');
        j._onGestureOut(true);
        assert.equal(out(j), true, 'tornata fuori');
        assert.deepEqual(d.calls, []);
    """)


def test_without_a_door_the_tap_still_tucks_her_away() -> None:
    """Il gesto di prima, quello della minichat: il tocco gira `out`."""
    _run("""
        const j = jenny();
        j._onTap();
        assert.equal(out(j), false);
        j._onTap();
        assert.equal(out(j), true);
        j._onGestureOut(false);
        assert.equal(out(j), false);
    """)


def test_the_badge_shows_and_is_said() -> None:
    _run("""
        const j = jenny();
        j.setBadge(true, 'risposta nuova');
        assert.ok(j.el.classList.has('has-dot'));
        assert.match(j.el.getAttribute('aria-label'), / · risposta nuova$/);
        j.setBadge(false, 'risposta nuova');
        assert.ok(!j.el.classList.has('has-dot'));
        assert.doesNotMatch(j.el.getAttribute('aria-label'), /risposta/);
    """)


def test_the_nudge_comes_and_goes() -> None:
    _run("""
        const j = jenny();
        j.nudge();
        assert.equal(squash(j), 'nudge');
        await sleep(560);
        assert.equal(squash(j), null, 'tolto a tempo, anche senza animationend');
    """)


def test_away_from_the_chat_she_ignores_sends_but_not_frames() -> None:
    """`setChatOnScreen(false)`: niente pensa per gli invii, ma la risposta la fa parlare."""
    _run("""
        const j = jenny({ out: false });
        j._noteTalkActivity = () => {};
        j.setChatOnScreen(false);
        j._handleChatSent({ chat_id: 'default' });
        assert.equal(j._turnActive, false);
        j._handleWsMessage({ event: 'delta', text: 'ciao' });
        assert.equal(j._agentState, 'talking');
        j.setChatOnScreen(true);
        assert.equal(j.mode, 'chat');
    """)


def test_she_leans_into_the_slide_but_not_after_a_flight() -> None:
    """Uscendo dal bordo si sporge verso l'interno, tornando verso il bordo. A
    fine volo e' gia' dove deve stare: niente inclinazione."""
    _run("""
        const j = jenny({ out: false });
        j.setOut(true);
        assert.equal(j.motion('lean'), 'go-in');
        j.setOut(false);
        assert.equal(j.motion('lean'), 'go-edge');
        j._stopMotion('lean');
        j.el.classList.add('flying');
        j.setOut(true);
        assert.equal(j.motion('lean'), null, "si e' sporta atterrando");
        j.setOut(true);
        assert.equal(j.motion('lean'), null);
    """)


def test_a_mood_arrives_with_its_reaction() -> None:
    _run("""
        const j = jenny();
        j._applyMood('angry');
        assert.equal(squash(j), 'angry');
        j._applyMood('nope');
        assert.equal(squash(j), 'angry', 'un umore che non esiste non reagisce');
    """)


def test_the_badge_makes_her_notice_once() -> None:
    _run("""
        const j = jenny();
        j.setBadge(true, 'x');
        assert.equal(squash(j), 'perk');
        j._stopMotion('squash');
        j.setBadge(true, 'x');
        assert.equal(squash(j), null, 'il pallino era gia acceso');
    """)


def test_talking_stretches_on_each_syllable_and_snaps_the_gesture() -> None:
    _run("""
        const j = jenny();
        const now = performance.now();
        Object.assign(j._talk, { timer: 1, lastTextAt: now, switchAt: now + 10_000, open: false });
        j._talkTick();
        assert.equal(squash(j), 'syl', 'bocca aperta, nessuna sillaba');
        assert.equal(j.motion('lean'), null, 'il gesto non e cambiato');
        j._talk.switchAt = 0;
        j._talkTick();
        assert.equal(j.motion('lean'), 'gest');
    """)
