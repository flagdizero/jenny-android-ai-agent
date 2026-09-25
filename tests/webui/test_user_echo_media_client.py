"""L'eco di un messaggio da un altro canale porta i suoi allegati in chat.

Il difetto misurato il 18/09/2026 con una foto da Telegram: l'immagine finiva
nel transcript (e alla riapertura si vedeva) ma la bolla live restava di solo
testo. Erano due metà dello stesso buco — il server mandava sul filo i path del
filesystem invece delle URL firmate, e il client non passava comunque gli
allegati al renderer. Qui si blocca la metà client.

Il metodo si estrae dal sorgente e gira in node su un ``this`` finto: non tocca
il DOM da sé, delega tutto a ``addCompletedMessage``, che è il punto in cui la
bolla live e il ripristino da history si incontrano.
"""

from __future__ import annotations

import re
from pathlib import Path

from support.js_harness import requires_node, run_js

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
CHAT_JS = ASSETS / "mobile-chat.js"


pytestmark = requires_node


def _method(source: str, name: str) -> str:
    body = re.search(rf"\n  (?:async )?{name}\(([^)]*)\)\s*\{{(.*?)\n  \}}", source, re.S)
    assert body, f"{name} non trovato"
    return f"{name}({body.group(1)}) {{{body.group(2)}\n  }}"


def _run_js(script: str) -> None:
    chat = CHAT_JS.read_text(encoding="utf-8")
    harness = f"""
import assert from 'node:assert/strict';

function makeChat() {{
  return {{
    calls: [],
    resets: 0,
    unread: 0,
    _resetStreamState() {{ this.resets++; }},
    _bumpUnread() {{ this.unread++; }},
    scrollToBottom() {{}},
    addCompletedMessage(text, role, origin, media) {{
      this.calls.push({{ text, role, origin, media }});
    }},
    {_method(chat, "_handleExternalUser")},
  }};
}}
"""
    run_js(harness + script)


def test_attachments_reach_the_bubble() -> None:
    """Il quarto argomento è l'unico modo in cui l'allegato entra nella bolla."""
    _run_js("""
      const chat = makeChat();
      const media = [{ url: '/api/media/sig/payload', name: 'photo.jpg', kind: 'image' }];
      chat._handleExternalUser({ event: 'user', text: 'guarda qui',
                                 origin: 'telegram', media_urls: media });
      assert.equal(chat.calls.length, 1, 'nessuna bolla');
      const call = chat.calls[0];
      assert.equal(call.text, 'guarda qui');
      assert.equal(call.role, 'user');
      assert.equal(call.origin, 'telegram');
      assert.deepEqual(call.media, media, 'la foto non e\\' arrived al renderer');
    """)


def test_a_photo_without_a_caption_still_makes_a_bubble() -> None:
    """Il guard sul solo testo scartava il messaggio: la bolla è l'immagine."""
    _run_js("""
      const chat = makeChat();
      const media = [{ url: '/api/media/sig/payload', name: 'photo.jpg', kind: 'image' }];
      chat._handleExternalUser({ event: 'user', text: '', origin: 'telegram',
                                 media_urls: media });
      assert.equal(chat.calls.length, 1, 'una foto muta non ha prodotto bolla');
      assert.deepEqual(chat.calls[0].media, media);
      assert.equal(chat.unread, 1);
    """)


def test_an_empty_echo_is_still_ignored() -> None:
    _run_js("""
      const chat = makeChat();
      chat._handleExternalUser({ event: 'user', text: '   ', origin: 'telegram' });
      chat._handleExternalUser({ event: 'user', origin: 'telegram', media_urls: [] });
      assert.equal(chat.calls.length, 0);
      assert.equal(chat.resets, 0, 'uno stream e\\' state reset per nothing');
    """)
