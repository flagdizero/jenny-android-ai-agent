"""La frase fra un passo e l'altro, dal vivo, lascia il posto alla successiva.

Il testo che Jenny scrive prima di uno strumento («ok, la cerco e te la metto in
un file») la rilettura lo fa diventare un pensiero (``transcript_replay.py``,
``demote_interrupted_assistant``), e la casa non lo mostra. Dal vivo invece
restava: sul s22 il 28/10/2026 una ricerca lasciava quattro frasi che dicevano
la stessa cosa, e riaprendo l'app ne restava una. Ora la frase resta a schermo
mentre lo strumento gira, e se ne va quando arriva il seguito o il turno finisce.
"""

from __future__ import annotations

from support.home_dom import requires_jsdom, run_home

pytestmark = requires_jsdom

_HEAD = """
import assert from 'node:assert/strict';
import { boot, tick, frame, thread } from './boot.mjs';
const app = await boot();
const say = (turn, text) => {
  frame({ event: 'delta', chat_id: 'default', turn_id: turn, text });
  frame({ event: 'stream_end', chat_id: 'default', turn_id: turn });
};
const step = (turn, name) => frame({ event: 'message', chat_id: 'default', turn_id: turn,
  kind: 'progress', text: '', tool_events: [{ phase: 'start', name }] });
"""


def test_the_line_before_a_step_stays_until_the_next_one() -> None:
    run_home(_HEAD + """
say('t1', 'ok, vedo come si cura');
step('t1', 'read_file');
await tick(10);
assert.deepEqual(thread(), ['jenny: ok, vedo come si cura'], 'mentre lo strumento gira si vede');
say('t1', 'ok, la cerco');
step('t1', 'spawn');
await tick(10);
assert.deepEqual(thread(), ['jenny: ok, la cerco']);
say('t1', "fatto, ci sta lavorando l'aiutante");
frame({ event: 'turn_end', chat_id: 'default', turn_id: 't1' });
await tick(10);
assert.deepEqual(thread(), ["jenny: fatto, ci sta lavorando l'aiutante"]);
""")


def test_a_progress_text_gives_way_to_the_answer() -> None:
    """Il rientro di un aiutante: «L'aiutante ha finito…» non resta sopra il riassunto."""
    run_home(_HEAD + """
frame({ event: 'message', chat_id: 'default', kind: 'progress', text: "L'aiutante ha finito, ti rispondo." });
await tick(10);
assert.deepEqual(thread(), ["jenny: L'aiutante ha finito, ti rispondo."]);
frame({ event: 'message', chat_id: 'default', text: 'acqua una volta al mese' });
frame({ event: 'turn_end', chat_id: 'default' });
await tick(10);
assert.deepEqual(thread(), ['jenny: acqua una volta al mese']);
""")


def test_a_turn_of_only_steps_leaves_no_bubble() -> None:
    run_home(_HEAD + """
say('t1', 'controllo un attimo');
step('t1', 'read_file');
frame({ event: 'turn_end', chat_id: 'default', turn_id: 't1' });
await tick(10);
assert.deepEqual(thread(), []);
""")


def test_text_with_no_step_in_between_stays_whole() -> None:
    """Due pezzi di risposta senza uno strumento in mezzo sono la risposta."""
    run_home(_HEAD + """
say('t1', 'prima parte');
say('t1', 'seconda parte');
frame({ event: 'turn_end', chat_id: 'default', turn_id: 't1' });
await tick(10);
assert.deepEqual(thread(), ['jenny: prima parte seconda parte']);
""")


def test_copy_no_longer_carries_the_passing_line() -> None:
    run_home(_HEAD + """
say('t1', 'ok, la cerco');
step('t1', 'spawn');
say('t1', 'fatto');
frame({ event: 'turn_end', chat_id: 'default', turn_id: 't1' });
await tick(10);
const bubble = [...document.querySelectorAll('.home-msg-jenny')].pop();
assert.equal(app.chat._textOf(bubble), 'fatto');
""")


def test_a_file_on_its_own_keeps_the_line_before_it() -> None:
    """Il riassunto scritto prima di ``message`` col solo file e' la didascalia del file."""
    run_home(_HEAD + """
say('t1', 'ecco la ricerca: tre fonti, tutte concordi');
step('t1', 'message');
frame({ event: 'message', chat_id: 'default', turn_id: 't1',
  media_urls: [{ url: '/api/media/x/ricerca.md', name: 'ricerca.md' }] });
frame({ event: 'turn_end', chat_id: 'default', turn_id: 't1' });
await tick(10);
assert.deepEqual(thread(), ['jenny: ecco la ricerca: tre fonti, tutte concordi']);
assert.equal(document.querySelectorAll('.home-msg-jenny').length, 1);
""")


def test_an_error_notice_keeps_the_answer_before_it() -> None:
    """Risposta, strumento, poi niente: l'avviso del turno vuoto non prende il suo posto."""
    run_home(_HEAD + """
say('t1', 'si annaffia una volta a settimana');
step('t1', 'journal_append');
frame({ event: 'message', chat_id: 'default', turn_id: 't1',
  text: 'Ho finito i passaggi ma non sono riuscita a scrivere una risposta.',
  error: { kind: 'empty' } });
frame({ event: 'turn_end', chat_id: 'default', turn_id: 't1' });
await tick(10);
assert.deepEqual(thread(), [
  'jenny: si annaffia una volta a settimana Ho finito i passaggi ma non sono riuscita a scrivere una risposta.',
]);
""")
