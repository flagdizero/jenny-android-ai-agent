"""Nella Console una foto senza didascalia parte anche col tocco sul tasto.

`sendMessage` accettava già un messaggio di soli allegati, e la casa lo
mandava; ma il tasto manda della Console si accendeva solo col testo, quindi
lì una foto muta partiva con Invio da tastiera e mai col dito. Dal controllo
della documentazione del 30/09/2026, che prometteva il contrario.

Si misura lo stato del tasto, cioè quel che il dito trova: acceso o spento.
"""

from __future__ import annotations

from support.js_harness import ASSETS, member, requires_node, run_js

CHAT_JS = ASSETS / "mobile-chat.js"

pytestmark = requires_node


def _run(script: str) -> None:
    src = CHAT_JS.read_text(encoding="utf-8")
    run_js(
        "import assert from 'node:assert/strict';\n"
        "function makeChat({ text = '', media = [] } = {}) {\n"
        "  const classes = new Set();\n"
        "  return {\n"
        "    input: { value: text },\n"
        "    imageHandler: { getImages: () => media },\n"
        "    sendBtn: { disabled: true, classList: {\n"
        "      add: (c) => classes.add(c), remove: (c) => classes.delete(c),\n"
        "      contains: (c) => classes.has(c) } },\n"
        f"    {member(src, '_updateSendState')},\n"
        "  };\n"
        "}\n" + script
    )


def test_an_attachment_alone_lights_the_send_button() -> None:
    _run("""
      const chat = makeChat({ media: [{ data_url: 'data:image/png;base64,AA', name: 'a.png' }] });
      chat._updateSendState();
      assert.equal(chat.sendBtn.disabled, false);
      assert.ok(chat.sendBtn.classList.contains('enabled'));
    """)


def test_text_alone_still_lights_it() -> None:
    _run("""
      const chat = makeChat({ text: 'ciao' });
      chat._updateSendState();
      assert.equal(chat.sendBtn.disabled, false);
    """)


def test_nothing_to_send_keeps_it_off() -> None:
    _run("""
      const chat = makeChat({ text: '   ' });
      chat._updateSendState();
      assert.equal(chat.sendBtn.disabled, true);
      assert.ok(!chat.sendBtn.classList.contains('enabled'));
    """)


def test_adding_or_removing_an_attachment_recomputes_it() -> None:
    """Senza questo il tasto resta com'era finché non tocchi il campo."""
    src = CHAT_JS.read_text(encoding="utf-8")
    start = src.index("this.imageHandler.onChange =")
    handler = src[start:src.index("};", start)]
    assert "this._updateSendState()" in handler
