"""Un frame appartiene a *una* conversazione, e si rende solo in quella.

``mobile-chat.js`` filtrava i frame live per ``turn_id`` e mai per ``chat_id``:
il turno di un'altra conversazione — la risposta data in ``project:patreon``,
l'avviso proattivo consegnato sulla chat personale — si dipingeva nel thread che
in quel momento era a schermo, delta, righe di ``file_edit`` e ``turn_end``
compresi. Finché di conversazioni ce n'era una il filtro assente era un filtro
inutile; le sessioni-progetto sono ciò che lo rende raggiungibile, quindi qui non
c'è nessun comportamento storico da preservare.

Le due dimensioni interagiscono, e l'ordine in cui si guardano è il contratto
che questo modulo esiste per fissare: ``_applyTurnBoundary`` **adotta** il turno
del frame che vede, quindi un frame estraneo che passasse da lì si prenderebbe
``_currentTurnId`` e il ``turn_end`` della risposta vera non combacerebbe più con
niente (la mascotte incantata in ``think``, difetto già misurato su questo
dispositivo da un'altra porta). Filtrando prima, un turno scartato non apre
niente — e quindi non lascia niente di aperto.

I metodi si estraggono dal sorgente e si eseguono in node su un ``this`` finto,
come in ``test_live_turn_boundary_client.py``: la WebUI non ha un runner con DOM,
e queste funzioni non lo toccano. In coda ci sono alcune asserzioni sul solo
testo del sorgente, per le cose che non si possono eseguire (l'ordine dentro
``handleMessage``, chi si iscrive a cosa): sono guardie deboli e dichiarate tali
nei rispettivi docstring.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
CHAT_JS = ASSETS / "mobile-chat.js"
JENNY_JS = ASSETS / "shared" / "jenny-mascot.js"
COMPANION_JS = ASSETS / "mobile-jenny.js"
SESSION_JS = ASSETS / "shared" / "session-manager.js"
WS_JS = ASSETS / "shared" / "ws-manager.js"

_NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(_NODE is None, reason="node non disponibile")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _method(source: str, name: str) -> str:
    body = re.search(rf"\n  (?:async )?{name}\(([^)]*)\)\s*\{{(.*?)\n  \}}", source, re.S)
    assert body, f"{name} non trovato"
    return f"{name}({body.group(1)}) {{{body.group(2)}\n  }}"


def _static_set(source: str, name: str) -> str:
    m = re.search(rf"static {name} = (new Set\(\[.*?\]\));", source, re.S)
    assert m, f"{name} non trovato"
    return m.group(1)


def _harness() -> str:
    chat = _read(CHAT_JS)
    return f"""
import assert from 'node:assert/strict';

const ChatController = {{ TURN_SCOPED_EVENTS: {_static_set(chat, "TURN_SCOPED_EVENTS")} }};
ChatController.CHAT_SCOPED_EVENTS = {_static_set(chat, "CHAT_SCOPED_EVENTS")};

// La conversazione aperta secondo il session manager: nel client è un getter su
// `sessionManager` (`chatIdOf(currentKey)`), qui è un campo che i test muovono.
const sessionManager = {{ currentChatId: 'default' }};

function makeChat() {{
  return {{
    _currentTurnId: null,
    resets: 0,
    _resetStreamState() {{ this.resets++; this._currentTurnId = null; }},
    {_method(chat, "_belongsToOpenChat")},
    {_method(chat, "_applyTurnBoundary")},
  }};
}}

// Il router vero, nell'ordine vero: filtro della chat, poi confine di turno.
// Ritorna true se il frame sarebbe stato reso.
function route(chat, msg) {{
  if (!chat._belongsToOpenChat(msg)) return false;
  if (!chat._applyTurnBoundary(msg)) return false;
  return true;
}}

function frame(event, chat_id, turn_id) {{
  const f = {{ event }};
  if (chat_id !== undefined) f.chat_id = chat_id;
  if (turn_id !== undefined) f.turn_id = turn_id;
  return f;
}}
"""


def _run_js(script: str) -> None:
    source = _harness() + script
    proc = subprocess.run(
        [str(_NODE), "--input-type=module", "-e", source],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


# ── Il filtro ───────────────────────────────────────────────────────────────


def test_a_turn_of_another_conversation_is_not_rendered() -> None:
    """Il difetto: con la personale a schermo, un turno di ``project:patreon``
    si dipingeva qui — testo, file toccati e chiusura."""
    _run_js("""
      const chat = makeChat();
      for (const ev of ['delta', 'reasoning_delta', 'stream_end', 'message',
                        'file_edit', 'turn_end', 'user', 'goal_status']) {
        assert.equal(route(chat, frame(ev, 'project:patreon', 'p:1')), false,
                     ev + " di un'altra conversazione è stato reso");
      }
    """)


def test_the_open_conversation_is_rendered_whichever_it_is() -> None:
    """La personale e un progetto sono simmetrici: il filtro confronta, non
    privilegia. La chiave `websocket:default` arriva sul filo come `default`."""
    _run_js("""
      const chat = makeChat();
      for (const ev of ['delta', 'message', 'file_edit', 'turn_end']) {
        assert.equal(route(chat, frame(ev, 'default', 'a:1')), true, ev);
      }

      sessionManager.currentChatId = 'project:patreon';
      const other = makeChat();
      for (const ev of ['delta', 'message', 'file_edit', 'turn_end']) {
        assert.equal(route(other, frame(ev, 'project:patreon', 'b:1')), true, ev);
      }
      assert.equal(route(other, frame('delta', 'default', 'c:1')), false,
                   'con un progetto aperto la personale non deve dipingere qui');
      sessionManager.currentChatId = 'default';
    """)


def test_a_frame_without_a_chat_id_is_rendered() -> None:
    """Permissivo su ciò che non sa, come il confine di turno: il retry di una
    consegna parziale ricostruisce il payload con ``skip_persist`` e arriva
    spoglio. Scartarlo perderebbe la chiusura del turno."""
    _run_js("""
      const chat = makeChat();
      assert.equal(route(chat, frame('delta', undefined, 'a:1')), true);
      assert.equal(route(chat, frame('turn_end')), true);
    """)


def test_out_of_band_frames_are_never_filtered_by_chat() -> None:
    """I frame che parlano del runtime, non di una conversazione. Uno per uno il
    motivo per cui filtrarli sarebbe un guasto:

    ``subagent_activity`` porta un ``chat_id`` **inaffidabile** — è mirato a una
    connessione e il campo lo riempie ``ws_sender._chat_id_for`` con
    ``min(chats)``, cioè ``default`` per chiunque sia iscritto anche alla chat
    personale: filtrarlo spegnerebbe la modale dell'attività a chi guarda un
    progetto. ``subagent_status`` porta lo snapshot globale dei subagent sul
    ``chat_id`` di chi li ha avviati. ``runtime_model_updated`` e ``error`` non
    portano ``chat_id`` affatto."""
    _run_js("""
      const chat = makeChat();
      for (const ev of ['subagent_status', 'subagent_activity', 'subagent_unwatched',
                        'runtime_model_updated', 'error', 'app_data_changed',
                        'apps_list_changed', 'ui_query']) {
        assert.equal(chat._belongsToOpenChat(frame(ev, 'project:patreon')), true,
                     ev + ' è stato filtrato per chat: non è di una conversazione');
      }
    """)


# ── L'interazione con il confine di turno ───────────────────────────────────


def test_a_dropped_turn_leaves_no_turn_half_open() -> None:
    """La domanda che conta: cosa resta di un turno scartato a metà volo.

    Niente — perché non ha mai aperto niente. La risposta in corso mantiene il
    proprio ``_currentTurnId``, nessuna bolla viene azzerata, e la *sua*
    chiusura arriva ancora a lei."""
    _run_js("""
      const chat = makeChat();
      assert.equal(route(chat, frame('delta', 'default', 'mio:1')), true);

      // Un intero turno di un'altra conversazione atterra in mezzo.
      for (const ev of ['message', 'file_edit', 'delta', 'stream_end', 'turn_end']) {
        route(chat, frame(ev, 'project:patreon', 'altro:1'));
      }
      assert.equal(chat.resets, 0, 'un turno scartato ha azzerato la bolla in corso');
      assert.equal(chat._currentTurnId, 'mio:1', 'un turno scartato si è preso il turno corrente');

      // La risposta continua e si chiude normalmente.
      assert.equal(route(chat, frame('delta', 'default', 'mio:1')), true);
      assert.equal(route(chat, frame('turn_end', 'default', 'mio:1')), true);
    """)


def test_filtering_after_the_turn_boundary_would_reopen_the_defect() -> None:
    """Perché l'ordine è parte del contratto e non una preferenza.

    ``_applyTurnBoundary`` ha un effetto collaterale: adotta il turno del frame
    che vede. Passandogli un frame estraneo — cioè filtrando dopo — il turno
    corrente diventa quello dell'altra conversazione, e il ``turn_end`` della
    risposta vera viene ignorato: la risposta non si chiude più."""
    _run_js("""
      const chat = makeChat();
      chat._applyTurnBoundary(frame('delta', 'default', 'mio:1'));

      // L'ordine sbagliato: prima il confine di turno.
      chat._applyTurnBoundary(frame('message', 'project:patreon', 'altro:1'));
      assert.equal(chat._currentTurnId, 'altro:1');
      assert.equal(chat._applyTurnBoundary(frame('turn_end', 'default', 'mio:1')), false,
                   'questo test descrive il difetto: se passa, il difetto non c\\'è più ' +
                   'e il test va riscritto');
    """)


# ── Guardie sul testo del sorgente ──────────────────────────────────────────
#
# Deboli per costruzione: leggono il sorgente, non lo eseguono, quindi provano
# che una riga c'è e non che faccia effetto. Coprono le tre cose che non si
# possono eseguire senza un DOM — l'ordine dentro `handleMessage`, chi si
# iscrive a quale stream, e che la regola di conversione della chiave stia in un
# posto solo — e vanno lette come "questo pezzo non è stato rimosso per
# distrazione", non come una verifica del comportamento (quello sta sopra).


def test_handle_message_filters_by_chat_before_the_turn_boundary() -> None:
    chat = _read(CHAT_JS)
    body = re.search(r"\n  handleMessage\(msg\) \{(.*?)\n    switch", chat, re.S)
    assert body, "handleMessage non trovato"
    head = body.group(1)
    i_chat = head.find("_belongsToOpenChat(msg)")
    i_turn = head.find("_applyTurnBoundary(msg)")
    assert i_chat != -1, "handleMessage non filtra per chat_id"
    assert i_turn != -1, "handleMessage non applica più il confine di turno"
    assert i_chat < i_turn, (
        "il filtro sulla chat deve venire prima del confine di turno: "
        "v. test_filtering_after_the_turn_boundary_would_reopen_the_defect"
    )


def test_the_chat_id_rule_lives_in_one_place() -> None:
    """La conversione chiave→``chat_id`` serve in due direzioni (l'``attach`` in
    uscita, il filtro in entrata) e deve rispondere identico: se divergono,
    l'attach segue una chat e il filtro sorveglia un'altra — nessun frame
    passerebbe più. Quindi una funzione sola, e nessuna copia a mano del
    prefisso fuori da lei."""
    ws = _read(WS_JS)
    assert "export function chatIdOf(" in ws
    for path in sorted(ASSETS.rglob("*.js")):
        if path.name in {"ws-manager.js"} or "vendor" in path.parts:
            continue
        src = _read(path)
        assert "replace(/^websocket:/" not in src, (
            f"{path.name} rifà a mano lo strip del prefisso: usa chatIdOf/currentChatId"
        )


def test_switching_conversation_detaches_the_previous_one() -> None:
    session = _read(SESSION_JS)
    body = re.search(r"\n  switchTo\(key\) \{(.*?)\n  \}", session, re.S)
    assert body, "switchTo non trovato"
    head = body.group(1)
    assert "attachChat(next)" in head
    assert "detachChat(previous)" in head, "switchTo non stacca la conversazione lasciata"
    assert head.find("attachChat(next)") < head.find("detachChat(previous)"), (
        "mai un istante con zero conversazioni seguite"
    )
    assert "detachChat(chatId)" in _read(WS_JS)


def test_discarding_the_view_closes_the_turn_in_flight() -> None:
    """``invalidateHistory`` butta il DOM: i riferimenti alle bolle in
    composizione e il banner del goal vanno con lui, o restano a puntare nodi
    staccati. Prima lo rimediava il ``turn_end`` che arrivava comunque; da
    quando i frame dell'altra conversazione vengono scartati, non arriva più."""
    chat = _read(CHAT_JS)
    body = re.search(r"\n  invalidateHistory\(\) \{(.*?)\n  \}", chat, re.S)
    assert body, "invalidateHistory non trovato"
    head = body.group(1)
    assert "_resetStreamState()" in head
    assert "_clearGoalBanner()" in head


def test_the_mascot_releases_its_turn_on_a_switch() -> None:
    """La mascotte anima un turno alla volta e resta sul proprio: al cambio di
    chat il ``turn_end`` di quel turno non arriverà mai, e senza questo resta a
    pensare per sempre.

    Vale per tutti e due i gusci: l'ascolto e il rilascio stanno nella mascotte
    condivisa, e l'officina ci aggiunge solo la minichat in volo."""
    jenny = _read(JENNY_JS)
    assert "sessionManager.addEventListener('chat:switch'" in jenny
    assert "_releaseTrackedTurn()" in jenny
    body = re.search(r"\n  _releaseTrackedTurn\(\) \{(.*?)\n  \}", jenny, re.S)
    assert body, "_releaseTrackedTurn non trovato"
    head = body.group(1)
    for field in ("_turnActive = false", "_pendingTurn = false", "_streamTurnId = null"):
        assert field in head, f"_releaseTrackedTurn non azzera {field}"

    companion = _read(COMPANION_JS)
    body = re.search(r"\n  _releaseTrackedTurn\(\) \{(.*?)\n  \}", companion, re.S)
    assert body, "l'officina non dimentica piu' la minichat al cambio di chat"
    assert "awaiting = false" in body.group(1)
    assert "super._releaseTrackedTurn()" in body.group(1), (
        "l'officina ha riscritto il rilascio invece di aggiungerci la minichat"
    )
    assert "chat:switch" in _read(SESSION_JS), "switchTo non annuncia il cambio"
