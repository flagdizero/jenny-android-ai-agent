"""`shared/turns.js`: come la storia si raggruppa in turni, e dove un turno dal
vivo finisce — una spina dorsale per casa e officina.

Le due chat ne avevano una copia ciascuna, gia' divergenti. Qui le regole
comuni; quel che ogni chat tiene di un turno ha i suoi banchi
(`test_home_history_client.py`, `test_message_bubble_client.py`, ...).
"""

from __future__ import annotations

from support.js_harness import ASSETS, requires_node, run_js

TURNS_JS = (ASSETS / "shared" / "turns.js").read_text(encoding="utf-8")


def test_both_chats_use_it() -> None:
    for name, boundary in (("home-chat.js", "_crossesTurn"), ("mobile-chat.js", "_applyTurnBoundary")):
        src = (ASSETS / name).read_text(encoding="utf-8")
        assert "groupTurns(messages" in src, name
        assert "turnBoundary(" in src, name
        assert "currentTurn.turnId !== turnId" not in src and "current.turnId !== turnId" not in src, (
            f"{name} raggruppa ancora per conto suo"
        )
        assert boundary in src


@requires_node
def test_the_rules() -> None:
    run_js(
        "import assert from 'node:assert/strict';\n"
        + TURNS_JS.replace("export ", "")
        + """
const absorb = (t, m) => { if (m.text) t.content += (t.content ? '|' : '') + m.text; };
const turns = groupTurns([
  { role: 'assistant', text: 'a', turn_id: 't1', sentAt: 1 },
  { role: 'assistant', text: 'b', turnId: 't1', sentAt: 2, latencyMs: 900 },
  { role: 'assistant', text: 'notte 1' },
  { role: 'assistant', text: 'notte 2' },
  { session_boundary: true, text: '/new' },
  { role: 'user', text: 'ciao', origin: 'telegram', device: 'mac', sentAt: 3 },
  { role: 'assistant', text: 'x', turn_id: 't2', error: { kind: 'overloaded' } },
  { role: 'assistant', text: 'annuncio', workStart: true },
], { absorb, skip: (m) => m.workStart });
assert.deepEqual(turns.map((t) => t.boundary ? 'B' : t.user ? 'U:' + t.text : t.content),
  ['a|b', 'notte 1', 'notte 2', 'B', 'U:ciao', 'x'], 'senza id non si accorpa, mai');
assert.equal(turns[0].at, 2, 'l\\u2019ora e\\u2019 quella dell\\u2019ultimo pezzo');
assert.equal(turns[0].latencyMs, 900);
assert.equal(turns[4].device, 'mac');
assert.deepEqual(turns[5].error, { kind: 'overloaded' });

const scoped = new Set(['delta', 'turn_end', 'message']);
assert.equal(turnBoundary(null, { event: 'delta', turn_id: 'a' }, scoped), 'adopt');
assert.equal(turnBoundary('a', { event: 'delta', turn_id: 'a' }, scoped), 'same');
assert.equal(turnBoundary('a', { event: 'turn_end' }, scoped), 'same', 'senza id vale il turno aperto');
assert.equal(turnBoundary('a', { event: 'turn_end', turn_id: 'b' }, scoped), 'ignore');
assert.equal(turnBoundary('a', { event: 'message', turnId: 'b' }, scoped), 'new');
assert.equal(turnBoundary('a', { event: 'goal_status', turn_id: 'b' }, scoped), 'same');
"""
    )
