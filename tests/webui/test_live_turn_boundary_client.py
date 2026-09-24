"""Il confine di turno sui frame live, eseguito davvero.

Ogni frame live porta il proprio ``turn_id`` — il recorder lo stampa sul payload
prima che parta sul filo, quindi ``delta``, ``message``, ``stream_end`` e
``turn_end`` ce l'hanno tutti — ma il client lo leggeva **solo** nella
cronologia (``_buildTurns``). Nel live tutto finiva nella bolla corrente, e un
turno può atterrare dentro un altro: un avviso proattivo (heartbeat, cron,
Dream) è un turno a sé e non aspetta che la risposta in corso finisca. Da lì i
due sintomi con una causa sola — l'avviso riusava la bolla della risposta
sovrascrivendone il testo, e il suo ``turn_end`` chiudeva un turno che non era
il suo (mascotte a ``idle`` a metà risposta, o incantata in ``think`` per
sempre quando invece il ``turn_end`` non arrivava proprio).

Le due regole sono **deliberatamente diverse**, ed è la cosa che questo modulo
esiste per bloccare: la chat rende tutti i turni, quindi a ogni cambio di id
apre una bolla nuova; la mascotte ne anima uno solo, quindi resta sul proprio e
ignora le chiusure altrui. Scambiarle rimette in piedi il difetto — la mascotte
che adotta l'id dell'avviso non vedrebbe mai più chiudersi la risposta.

I metodi si estraggono dal sorgente e si eseguono in node su un ``this`` finto:
la WebUI non ha un runner con DOM, ma queste due funzioni non lo toccano.
"""

from __future__ import annotations

import re
from pathlib import Path

from support.js_harness import requires_node, run_js

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
CHAT_JS = ASSETS / "mobile-chat.js"
JENNY_JS = ASSETS / "shared" / "jenny-mascot.js"


pytestmark = requires_node


def _method(source: str, name: str) -> str:
    body = re.search(rf"\n  (?:async )?{name}\(([^)]*)\)\s*\{{(.*?)\n  \}}", source, re.S)
    assert body, f"{name} non trovato"
    return f"{name}({body.group(1)}) {{{body.group(2)}\n  }}"


def _turn_scoped_events(source: str) -> str:
    m = re.search(r"static TURN_SCOPED_EVENTS = (new Set\(\[.*?\]\));", source, re.S)
    assert m, "TURN_SCOPED_EVENTS non trovato"
    return m.group(1)


def _harness() -> str:
    chat = CHAT_JS.read_text(encoding="utf-8")
    jenny = JENNY_JS.read_text(encoding="utf-8")
    return f"""
import assert from 'node:assert/strict';

const ChatController = {{ TURN_SCOPED_EVENTS: {_turn_scoped_events(chat)} }};

function makeChat() {{
  return {{
    _currentTurnId: null,
    resets: 0,
    _resetStreamState() {{ this.resets++; this._currentTurnId = null; }},
    {_method(chat, "_applyTurnBoundary")},
  }};
}}

function makeMascot() {{
  return {{
    _streamTurnId: null,
    {_method(jenny, "_trackedTurnMatches")},
  }};
}}

function frame(event, turn_id) {{
  return turn_id === undefined ? {{ event }} : {{ event, turn_id }};
}}
"""


def _run_js(script: str) -> None:
    source = _harness() + script
    run_js(source)


# ── Chat: ogni turno la sua bolla ───────────────────────────────────────────


def test_a_normal_turn_never_splits() -> None:
    """Tutte le parti di un turno vero portano lo stesso id: qui non si spezza
    niente che fosse legittimamente unito."""
    _run_js("""
      const chat = makeChat();
      for (const ev of ['delta', 'reasoning_delta', 'delta', 'stream_end', 'message']) {
        assert.equal(chat._applyTurnBoundary(frame(ev, 'webui:A')), true);
      }
      assert.equal(chat.resets, 0, 'la risposta è stata spezzata in più bolle');
      assert.equal(chat._applyTurnBoundary(frame('turn_end', 'webui:A')), true);
    """)


def test_a_frame_without_an_id_belongs_to_the_current_turn() -> None:
    """Non è un caso teorico: il retry di una consegna parziale ricostruisce il
    payload con ``skip_persist``, che salta l'annotazione — il ``turn_end``
    ritrasmesso è ``{event, chat_id}`` e basta."""
    _run_js("""
      const chat = makeChat();
      chat._applyTurnBoundary(frame('delta', 'webui:A'));
      assert.equal(chat._applyTurnBoundary(frame('delta')), true);
      assert.equal(chat._applyTurnBoundary(frame('turn_end')), true);
      assert.equal(chat.resets, 0);
      assert.equal(chat._currentTurnId, 'webui:A', "un frame senza id non deve azzerare il turno");
    """)


def test_a_proactive_alert_opens_its_own_bubble_instead_of_overwriting() -> None:
    """Il difetto misurato: quattro avvisi consecutivi, una bolla sola con
    dentro l'ultimo."""
    _run_js("""
      const chat = makeChat();
      chat._applyTurnBoundary(frame('message', 'proactive:1'));
      assert.equal(chat.resets, 0, 'il primo avviso non apre nessun turno da chiudere');
      assert.equal(chat._applyTurnBoundary(frame('turn_end', 'proactive:1')), true);

      // Il turno si è chiuso: il secondo avviso riparte pulito.
      chat._currentTurnId = null;
      chat._applyTurnBoundary(frame('message', 'proactive:2'));
      assert.equal(chat._currentTurnId, 'proactive:2');

      // E anche senza la chiusura di mezzo, un id nuovo apre una bolla nuova:
      // è ciò che salva la cronologia già scritta e i turni non annotati.
      chat._applyTurnBoundary(frame('message', 'proactive:3'));
      assert.equal(chat.resets, 1, "il secondo avviso ha riusato la bolla del primo");
      assert.equal(chat._currentTurnId, 'proactive:3');
    """)


def test_an_alert_landing_mid_answer_does_not_close_the_answer() -> None:
    """L'interleaving vero: la risposta riprende nella propria bolla, e il
    ``turn_end`` dell'avviso non chiude la risposta."""
    _run_js("""
      const chat = makeChat();
      chat._applyTurnBoundary(frame('delta', 'webui:A'));

      // Avviso proattivo in mezzo: bolla nuova, non sovrascrittura.
      assert.equal(chat._applyTurnBoundary(frame('message', 'proactive:1')), true);
      assert.equal(chat.resets, 1);

      // La sua chiusura riguarda lui.
      assert.equal(chat._applyTurnBoundary(frame('turn_end', 'proactive:1')), true);
      chat._currentTurnId = null;   // _handleTurnEnd -> _resetStreamState

      // La risposta riprende: bolla propria, e la sua chiusura arriva a lei.
      assert.equal(chat._applyTurnBoundary(frame('delta', 'webui:A')), true);
      assert.equal(chat._applyTurnBoundary(frame('turn_end', 'webui:A')), true);
    """)


def test_a_turn_end_of_someone_elses_turn_is_ignored() -> None:
    _run_js("""
      const chat = makeChat();
      chat._applyTurnBoundary(frame('delta', 'webui:A'));
      assert.equal(chat._applyTurnBoundary(frame('turn_end', 'proactive:1')), false,
                   'ha chiuso un turno che non stava rendendo');
      assert.equal(chat.resets, 0);
      assert.equal(chat._currentTurnId, 'webui:A');
    """)


def test_out_of_band_frames_stay_out_of_the_turn_bookkeeping() -> None:
    """Snapshot dei subagent, modello runtime, goal: frame dedicati, non parti
    di un turno. Passarli dal confine li farebbe adottare come turno corrente."""
    _run_js("""
      const chat = makeChat();
      for (const ev of ['subagent_status', 'subagent_activity', 'goal_status',
                        'runtime_model_updated', 'user']) {
        assert.equal(chat._applyTurnBoundary(frame(ev, 'qualunque')), true);
      }
      assert.equal(chat._currentTurnId, null);
      assert.equal(chat.resets, 0);
    """)


# ── Mascotte: un turno alla volta, e resta il suo ───────────────────────────


def test_the_mascot_keeps_following_the_turn_it_started_following() -> None:
    """La regola opposta a quella della chat. Se adottasse l'id dell'avviso, il
    ``turn_end`` della risposta non combacerebbe più con niente e resterebbe
    animata per sempre: lo stesso difetto, da un'altra porta."""
    _run_js("""
      const m = makeMascot();
      assert.equal(m._trackedTurnMatches(frame('delta', 'webui:A')), true);
      assert.equal(m._streamTurnId, 'webui:A');

      // Avviso proattivo in mezzo: lo anima, ma non gli cede il tracciamento.
      assert.equal(m._trackedTurnMatches(frame('message', 'proactive:1')), false);
      assert.equal(m._streamTurnId, 'webui:A', "l'avviso si è preso il turno della mascotte");

      // Quindi la chiusura dell'avviso non la spegne, e quella vera sì.
      assert.equal(m._trackedTurnMatches(frame('turn_end', 'proactive:1')), false);
      assert.equal(m._trackedTurnMatches(frame('turn_end', 'webui:A')), true);
    """)


def test_a_standalone_alert_is_tracked_and_closed() -> None:
    """A turno fermo l'avviso è il turno: deve poter chiudere il proprio."""
    _run_js("""
      const m = makeMascot();
      assert.equal(m._trackedTurnMatches(frame('message', 'proactive:1')), true);
      assert.equal(m._streamTurnId, 'proactive:1');
      assert.equal(m._trackedTurnMatches(frame('turn_end', 'proactive:1')), true);
    """)


def test_a_closing_frame_never_opens_the_tracking() -> None:
    """La minichat chiusa a metà turno scarta i frame intermedi (non c'è niente
    a schermo da dipingere), quindi la mascotte quel turno non l'ha mai visto
    aprirsi. La chiusura resta permissiva — ignorarla lascerebbe ``_pendingTurn``
    alzato per sempre, che è il difetto di N9 — ma non adotta: il turno vero,
    quando arriva, deve poter essere tracciato."""
    _run_js("""
      const m = makeMascot();
      assert.equal(m._trackedTurnMatches(frame('turn_end', 'proactive:1')), true);
      assert.equal(m._streamTurnId, null, 'una chiusura ha aperto un tracciamento');
      assert.equal(m._trackedTurnMatches(frame('delta', 'webui:A')), true);
      assert.equal(m._streamTurnId, 'webui:A');
    """)


def test_the_mascot_is_permissive_without_an_id() -> None:
    _run_js("""
      const m = makeMascot();
      m._trackedTurnMatches(frame('delta', 'webui:A'));
      assert.equal(m._trackedTurnMatches(frame('turn_end')), true,
                   'il turn_end ritrasmesso arriva senza annotazione');
    """)
