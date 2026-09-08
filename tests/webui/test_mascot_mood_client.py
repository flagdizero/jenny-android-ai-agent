"""L'arte della mascotte lato client, eseguita davvero, e il contratto con il backend.

Due cose si misurano qui.

**I livelli.** A mascotte intera il disegno è due immagini impilate — corpo e
faccia — e l'espressione esce da una precedenza stretta: il volo copre tutto,
dal bordo l'arte è cotta e la faccia si spegne, il pensa batte l'umore (è uno
stato, non un sentimento), l'umore batte il normale. Nel parlato la bocca
sbatte sulla faccia e il corpo cambia gesto su un timer più lento.

**L'umore.** Il frame ``mascot_mood`` arriva **dopo** il ``turn_end``, cioè
quando la mascotte è tornata ``idle`` e niente di ciò che le guardie di
``_handleWsMessage`` guardano è ancora "a schermo": va trattato prima di quelle
guardie, e in entrambe le viste. Da lì: si scarta se è la reazione a un turno
che non è più l'ultimo o se un altro turno è in corso, scade da sola, e un
turno nuovo la azzera.

I metodi si estraggono dal sorgente e girano in node su un ``this`` finto con
una ``classList`` e due ``img`` minime: non toccano il DOM oltre a quelle. Il
contratto in coda tiene allineate le etichette Python (``MOODS``) e la lista JS
(``MOOD_FACES``), e pretende che ogni faccia e ogni corpo siano un file vero e
nel manifest Android.

**Un apostrofo nei messaggi di assert va scritto ``\\'``, non ``\'``.** Lo
script JS sta in una stringa Python normale, che ``\'`` lo consuma: node
riceve un apice non protetto e muore di ``SyntaxError`` — e il test fallisce
sul ``returncode``, indicando questa riga invece di quella vera. Costato due
volte l'08/09/2026.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from jenny.session.mascot_mood import MOODS, NEUTRAL_MOOD
from jenny.utils.android_assets import _UI_MANIFEST

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
JENNY_JS = ASSETS / "mobile-jenny.js"

_NODE = shutil.which("node")
node = pytest.mark.skipif(_NODE is None, reason="node non disponibile")

_METHODS = (
    "_onMoodFrame",
    "_acceptMood",
    "_noteTurnClosed",
    "_applyMood",
    "_clearMood",
    "_moodFace",
    "_layered",
    "_setBody",
    "_setFace",
    "_faceKey",
    "_syncArt",
    "_talkTick",
    "_noteTalkActivity",
    "_stopTalk",
    "_setAgentState",
)
_CONSTS = (
    "ART",
    "SIDE_TALK_ANIM",
    "BODY",
    "FACE",
    "TALK_BODIES",
    "MOOD_FACES",
    "MOOD_HOLD_MS",
    "MOUTH_FRAME_MS",
    "TALK_ANIM_SWITCH_MS",
    "TALK_QUIET_TO_THINK_MS",
)


def _method(source: str, name: str) -> str:
    # ``(.*?)`` e non ``([^)]*)``: i parametri possono avere parentesi dentro
    # (``now = performance.now()``), e la firma finisce alla prima ``) {``.
    body = re.search(rf"\n  (?:async )?{name}\((.*?)\)\s*\{{(.*?)\n  \}}", source, re.S)
    assert body, f"{name} non trovato"
    return f"{name}({body.group(1)}) {{{body.group(2)}\n  }}"


def _const(source: str, name: str) -> str:
    # Il ``;`` puo' essere seguito da un commento di riga: senza ammetterlo la
    # cattura correrebbe fino al ``;`` della costante successiva.
    m = re.search(rf"^const {name} = (.*?);[ \t]*(?://.*)?$", source, re.S | re.M)
    assert m, f"{name} non trovata"
    return m.group(1)


def _dict_const(source: str, name: str) -> dict[str, str]:
    return dict(re.findall(r"(\w+):\s*'([^']+)'", _const(source, name)))


def _harness() -> str:
    jenny = JENNY_JS.read_text(encoding="utf-8")
    consts = "\n".join(f"const {name} = {_const(jenny, name)};" for name in _CONSTS)
    methods = "\n".join(_method(jenny, name) + "," for name in _METHODS)
    return f"""
import assert from 'node:assert/strict';

{consts}

function classList(...names) {{
  const set = new Set(names);
  return {{
    contains: (n) => set.has(n),
    add: (n) => set.add(n),
    remove: (n) => set.delete(n),
  }};
}}

/* Una <img> ridotta all'osso: i setter dell'arte leggono getAttribute('src')
   prima di scrivere, e la faccia si spegne con una classe. */
function imgStub(...classes) {{
  let value = null;
  const cls = classList(...classes);
  return {{
    classList: cls,
    get src() {{ return value; }},
    set src(v) {{ value = v; }},
    getAttribute(name) {{ return name === 'src' ? value : null; }},
    get off() {{ return cls.contains('off'); }},
  }};
}}

function makeMascot(...classes) {{
  const m = {{
    mode: 'chat',
    el: {{ classList: classList(...classes) }},
    img: imgStub(),
    face: imgStub('off'),  // nasce spenta, come in _buildDom
    _mood: null, _moodUntil: 0, _moodTimer: null,
    _lastClosedTurnId: null, _streamTurnId: null,
    _turnActive: false, _pendingTurn: false, _agentState: 'idle',
    _reducedMotion: false,
    _talk: {{ timer: null, animIdx: 0, open: false, lastTextAt: 0, switchAt: 0 }},
    states: [],
    {methods}
  }};
  return m;
}}

/* Conta i ridisegni senza sostituire _syncArt: quello vero serve intero. */
/* _setAgentState vero, con la traccia degli stati per chi la guarda. */
function traceStates(m) {{
  const real = m._setAgentState.bind(m);
  m.states = [];
  m._setAgentState = (state) => {{ m.states.push(state); real(state); }};
  return m;
}}

function countSyncs(m) {{
  const real = m._syncArt.bind(m);
  m.syncs = 0;
  m._syncArt = () => {{ m.syncs++; real(); }};
  return m;
}}
"""


def _run_js(script: str) -> None:
    proc = subprocess.run(
        [str(_NODE), "--input-type=module", "-e", _harness() + script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


# ── I due livelli ─────────────────────────────────────────────────────────────


@node
def test_from_the_edge_the_art_is_baked_and_the_face_is_off() -> None:
    """Da docked la faccia non si legge: si spegne, e sotto resta la posa cotta."""
    _run_js("""
      const m = makeMascot();
      m._syncArt();
      assert.equal(m.img.src, ART.side);
      assert.equal(m.face.off, true, 'la faccia deve essere spenta al bordo');
    """)


@node
def test_out_and_idle_is_the_resting_body_with_the_normal_face() -> None:
    _run_js("""
      const m = makeMascot('out');
      m._syncArt();
      assert.equal(m.img.src, BODY.idle);
      assert.equal(m.face.src, FACE.normal);
      assert.equal(m.face.off, false);
    """)


@node
def test_waiting_for_a_reply_has_its_own_body_and_face() -> None:
    _run_js("""
      const m = makeMascot('out', 'thinking');
      m._agentState = 'thinking';
      m._syncArt();
      assert.equal(m.img.src, BODY.think);
      assert.equal(m.face.src, FACE.thinking);
    """)


@node
def test_the_thinking_face_beats_a_live_mood() -> None:
    """Aspettare è uno stato: una faccia felice mentre pensa direbbe una cosa falsa."""
    _run_js("""
      const m = makeMascot('out');
      m._applyMood('happy');
      assert.equal(m._faceKey(), 'happy');
      m._agentState = 'thinking';
      assert.equal(m._faceKey(), 'thinking');
      m._agentState = 'idle';
      assert.equal(m._faceKey(), 'happy', 'finito il pensa la faccia torna');
      m._clearMood();
      assert.equal(m._faceKey(), 'normal');
    """)


@node
def test_a_mood_only_changes_the_face_never_the_body() -> None:
    _run_js("""
      const m = makeMascot('out');
      m._applyMood('sad');
      m._syncArt();
      assert.equal(m.img.src, BODY.idle, 'il corpo non è affare dell\\'umore');
      assert.equal(m.face.src, FACE.sad);
      m._clearMood();
    """)


@node
def test_talking_flaps_the_face_and_walks_the_body_on_a_slower_clock() -> None:
    _run_js("""
      const m = traceStates(makeMascot('out'));
      const t0 = 10000;
      m._talk.lastTextAt = t0;
      m._talk.switchAt = t0 + TALK_ANIM_SWITCH_MS;
      // Quattro battute di bocca: apre, chiude, apre, chiude.
      const mouths = [];
      for (let i = 0; i < 4; i++) {
        m._talk.lastTextAt = performance.now();
        m._talkTick();
        mouths.push(m.face.src);
      }
      assert.deepEqual(mouths, [FACE.talk, FACE.normal, FACE.talk, FACE.normal]);
      assert.equal(m.img.src, TALK_BODIES[0], 'il gesto non cambia a ogni bocca');
      assert.deepEqual(m.states, [], 'con testo che arriva non torna al pensa');

      // Passato il tempo del gesto, il corpo cambia (la bocca continua).
      m._talk.switchAt = performance.now() - 1;
      m._talk.lastTextAt = performance.now();
      m._talkTick();
      assert.equal(m.img.src, TALK_BODIES[1]);
    """)


@node
def test_talking_from_the_edge_stays_on_the_baked_pair() -> None:
    _run_js("""
      const m = makeMascot();
      m._talk.lastTextAt = performance.now();
      m._talkTick();
      assert.equal(m.img.src, SIDE_TALK_ANIM[1]);
      assert.equal(m.face.off, true, 'al bordo la faccia resta spenta anche parlando');
      m._talk.lastTextAt = performance.now();
      m._talkTick();
      assert.equal(m.img.src, SIDE_TALK_ANIM[0]);

      // E la spegne anche se arriva accesa: trascinata al bordo *mentre* parla,
      // _syncArt esce subito perché il frame è dell'animatore.
      m.face.classList.remove('off');
      m._talk.lastTextAt = performance.now();
      m._talkTick();
      assert.equal(m.face.off, true, 'una faccia accesa al bordo la spegne l\\'animatore');
    """)


@node
def test_every_talking_signal_keeps_the_mouth_alive() -> None:
    """Un flusso lungo manda 'talking' a ogni delta, non solo al primo.

    Se solo il primo contasse, dopo ``TALK_QUIET_TO_THINK_MS`` l'animatore
    tornerebbe al pensa in mezzo alla frase e — ripartendo — rimetterebbe
    ``animIdx`` a zero: il gesto del parlato non cambierebbe **mai**, e
    ``BODY.hand`` sarebbe un asset che nessuno può vedere. Misurato sul
    telefono l'08/09/2026: 23 scatti su 9 secondi di parlato, sempre a braccia
    giù.
    """
    _run_js("""
      const m = makeMascot('out');
      m._setAgentState('talking');
      const first = m._talk.lastTextAt;
      assert.ok(m._talk.timer, 'l\\'animatore non è partito');
      const spin = performance.now() + 5;
      while (performance.now() < spin) { /* fa passare il tempo */ }
      m._setAgentState('talking');   // stesso stato, nuovo testo
      assert.ok(m._talk.lastTextAt > first,
                'un delta a stato invariato non ha tenuto viva la bocca');
      m._stopTalk();
    """)


@node
def test_the_talking_gesture_changes_on_its_own_clock() -> None:
    """Il corpo alterna i due gesti; la bocca no, ha il suo passo."""
    _run_js("""
      const m = makeMascot('out');
      m._setAgentState('talking');
      const seen = new Set([m.img.src]);
      // Finge il passare del tempo del gesto senza aspettarlo davvero.
      for (let i = 0; i < 4; i++) {
        m._talk.switchAt = performance.now() - 1;
        m._talk.lastTextAt = performance.now();
        m._talkTick();
        seen.add(m.img.src);
      }
      assert.deepEqual([...seen].sort(), [...TALK_BODIES].sort());
      m._stopTalk();
    """)


@node
def test_silence_in_the_stream_goes_back_to_waiting() -> None:
    _run_js("""
      const m = traceStates(makeMascot('out'));
      m._talk.lastTextAt = performance.now() - TALK_QUIET_TO_THINK_MS - 1;
      m._talkTick();
      assert.deepEqual(m.states, ['thinking']);
      m._stopTalk();
    """)


# ── L'umore ───────────────────────────────────────────────────────────────────


@node
def test_a_mood_frame_after_the_closed_turn_is_shown_when_out_and_idle() -> None:
    _run_js("""
      const m = countSyncs(makeMascot('out'));
      m._noteTurnClosed({ event: 'turn_end', turn_id: 'webui:A' });
      m._onMoodFrame({ event: 'mascot_mood', mood: 'happy', turn_id: 'webui:A' });
      assert.equal(m._mood, 'happy');
      assert.equal(m._moodFace(), 'happy');
      assert.equal(m.face.src, FACE.happy);
      assert.ok(m.syncs >= 1, 'la faccia non è stata ridisegnata');
      m._clearMood();
    """)


@node
def test_a_frame_without_turn_id_is_accepted_and_neutral_or_unknown_is_not() -> None:
    _run_js("""
      const m = makeMascot('out');
      m._noteTurnClosed({ event: 'turn_end', turn_id: 'webui:A' });
      assert.equal(m._acceptMood({ mood: 'sad' }), true, 'senza id vale per il turno corrente');
      assert.equal(m._acceptMood({ mood: 'neutral' }), false);
      assert.equal(m._acceptMood({ mood: 'ecstatic' }), false);
      assert.equal(m._acceptMood({ mood: 'thinking' }), false, 'una faccia di stato non è un umore');
      assert.equal(m._acceptMood({ mood: 'normal' }), false);
      assert.equal(m._acceptMood(null), false);
    """)


@node
def test_a_reaction_to_a_turn_that_is_no_longer_the_latest_is_dropped() -> None:
    _run_js("""
      const m = makeMascot('out');
      m._noteTurnClosed({ event: 'turn_end', turn_id: 'webui:A' });
      m._noteTurnClosed({ event: 'turn_end', turn_id: 'webui:B' });
      assert.equal(m._acceptMood({ mood: 'happy', turn_id: 'webui:A' }), false);
      assert.equal(m._acceptMood({ mood: 'happy', turn_id: 'webui:B' }), true);
    """)


@node
def test_a_closing_frame_without_id_remembers_the_followed_turn() -> None:
    """Il retry di una consegna parziale arriva senza annotazione: vale il turno seguito."""
    _run_js("""
      const m = makeMascot('out');
      m._streamTurnId = 'webui:C';
      m._noteTurnClosed({ event: 'turn_end' });
      assert.equal(m._lastClosedTurnId, 'webui:C');
    """)


@node
def test_a_frame_while_another_turn_is_in_flight_is_dropped() -> None:
    _run_js("""
      const m = makeMascot('out');
      m._turnActive = true;
      assert.equal(m._acceptMood({ mood: 'happy' }), false);
      m._turnActive = false; m._pendingTurn = true;
      assert.equal(m._acceptMood({ mood: 'happy' }), false);
    """)


@node
def test_a_mood_arriving_at_the_edge_waits_to_be_called_out() -> None:
    _run_js("""
      const m = makeMascot();
      m._applyMood('happy');
      m._syncArt();
      assert.equal(m.face.off, true, 'al bordo non si mostra');
      m.el.classList.add('out');
      m._syncArt();
      assert.equal(m.face.src, FACE.happy, 'richiamata entro il tempo, la faccia c\\'è');
      m._clearMood();
    """)


@node
def test_the_face_expires_on_its_own() -> None:
    _run_js("""
      const m = makeMascot('out');
      const t0 = 1000;
      m._applyMood('sad', t0);
      assert.equal(m._moodFace(t0 + MOOD_HOLD_MS - 1), 'sad');
      assert.equal(m._moodFace(t0 + MOOD_HOLD_MS), null);
      m._clearMood();
    """)


@node
def test_clearing_redraws_once_and_is_idempotent() -> None:
    _run_js("""
      const m = countSyncs(makeMascot('out'));
      m._applyMood('sad');
      const before = m.syncs;
      m._clearMood();
      assert.equal(m._mood, null);
      assert.equal(m.syncs, before + 1);
      m._clearMood();
      assert.equal(m.syncs, before + 1, 'senza umore non c\\'è niente da ridisegnare');
    """)


# ── Il contratto con il backend e con gli asset ────────────────────────────────


def test_mood_faces_cover_every_backend_label_except_neutral() -> None:
    faces = set(re.findall(r"'(\w+)'", _const(JENNY_JS.read_text(encoding="utf-8"), "MOOD_FACES")))
    expected = {m for m in MOODS if m != NEUTRAL_MOOD}
    assert faces == expected, (
        f"MOOD_FACES (JS) e MOODS (Python) divergono: {sorted(faces)} vs {sorted(expected)}"
    )


def test_every_mood_has_a_drawn_face() -> None:
    source = JENNY_JS.read_text(encoding="utf-8")
    faces = set(re.findall(r"'(\w+)'", _const(source, "MOOD_FACES")))
    assert faces <= set(_dict_const(source, "FACE")), "un umore senza faccia in FACE"


def test_every_layer_the_client_names_exists_and_ships() -> None:
    """Corpo senza faccia è una Jenny senza volto: qui si controlla file per file."""
    source = JENNY_JS.read_text(encoding="utf-8")
    urls = {**_dict_const(source, "BODY"), **_dict_const(source, "FACE")}
    assert len(urls) == 9, sorted(urls)
    manifest = set(_UI_MANIFEST)
    for key, url in sorted(urls.items()):
        rel = url.removeprefix("/html-mobile/")
        assert rel in manifest, f"{key}: {rel} non è in _UI_MANIFEST"
        assert (ASSETS.parent / rel).is_file(), f"{key}: {rel} non esiste"
