"""L'ora sotto un messaggio, come si scrive (``shared/chat-time.js``).

Dal 06/10/2026 sotto ogni messaggio, in casa e in officina, c'e' l'ora: solo
l'ora se e' di oggi, data e ora se e' di prima, e l'anno se e' di un altro.
Qui gira il modulo vero in node, nelle due lingue dell'app, con «adesso»
fissato: il resto dei banchi lo finge, e misura solo dove l'ora si posa.
"""

from __future__ import annotations

from pathlib import Path

from support.js_harness import requires_node, run_js

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
CHAT_TIME_JS = ASSETS / "shared" / "chat-time.js"

pytestmark = requires_node

_HEAD = """
import assert from 'node:assert/strict';
const { chatTime, chatTimeIso } = await import('__URL__');
// «Adesso»: martedi' 6 ottobre 2026, 11:40, ora locale.
const now = new Date(2026, 9, 6, 11, 40).getTime();
const at = (...parts) => new Date(...parts).getTime();
""".replace("__URL__", CHAT_TIME_JS.as_uri())


def _run(script: str) -> None:
    run_js(_HEAD + script)


def test_today_is_only_the_time() -> None:
    _run("""
      assert.equal(chatTime(at(2026, 9, 6, 9, 5), { now, locale: 'it' }), '09:05');
      assert.equal(chatTime(at(2026, 9, 6, 9, 5), { now, locale: 'en' }), '09:05');
      // Appena dopo mezzanotte e' ancora oggi.
      assert.equal(chatTime(at(2026, 9, 6, 0, 3), { now, locale: 'it' }), '00:03');
    """)


def test_before_today_is_date_and_time() -> None:
    """Niente «ieri»: sotto un messaggio si legge quando, non quanto fa."""
    _run("""
      assert.equal(chatTime(at(2026, 9, 5, 18, 4), { now, locale: 'it' }), '5 ott, 18:04');
      assert.equal(chatTime(at(2026, 9, 5, 18, 4), { now, locale: 'en' }), 'Oct 5, 18:04');
      assert.equal(chatTime(at(2026, 9, 5, 23, 59), { now, locale: 'it' }), '5 ott, 23:59');
    """)


def test_another_year_carries_the_year() -> None:
    _run("""
      assert.equal(chatTime(at(2025, 9, 5, 18, 4), { now, locale: 'it' }), '5 ott 2025, 18:04');
      assert.equal(chatTime(at(2025, 9, 5, 18, 4), { now, locale: 'en' }), 'Oct 5, 2025, 18:04');
    """)


def test_always_24_hours() -> None:
    """«6:04 PM» sotto una bolla e' largo il doppio, e l'inglese lo darebbe."""
    _run("""
      assert.equal(chatTime(at(2026, 9, 6, 18, 4), { now: at(2026, 9, 6, 23, 0), locale: 'en' }), '18:04');
    """)


def test_no_time_is_no_text() -> None:
    _run("""
      for (const v of [null, undefined, 0, -5, NaN, 'ieri']) {
        assert.equal(chatTime(v, { now, locale: 'it' }), '', String(v));
        assert.equal(chatTimeIso(v), '', String(v));
      }
      assert.equal(chatTimeIso(1791200000000), new Date(1791200000000).toISOString());
    """)
