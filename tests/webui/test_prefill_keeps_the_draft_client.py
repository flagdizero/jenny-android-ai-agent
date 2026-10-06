"""Un testo precompilato nel composer non si porta via la bozza.

«Chiedi a Jenny» dalle altre viste
(``MobileApp.sendInChat``) e i comandi con un argomento (``runCommand`` con
``arg_hint``) **riscrivevano** il composer: la domanda che stavi scrivendo
spariva. ``_sendCommandLine`` invece la rimetteva. Ora i due passano da
``prefillComposer``, che mette la bozza da parte, e ``sendMessage`` la rimette
appena il testo precompilato parte.

Si eseguono in node i metodi veri di ``mobile-chat.js`` e ``mobile-app.js``.
"""

from __future__ import annotations

from support.js_harness import ASSETS, member, requires_node, run_js

pytestmark = requires_node

CHAT_SRC = (ASSETS / "mobile-chat.js").read_text(encoding="utf-8")
APP_SRC = (ASSETS / "mobile-app.js").read_text(encoding="utf-8")

_HARNESS = (
    """
import assert from 'node:assert/strict';
const sent = [];
const wsManager = { sendToChat: (key, text) => { sent.push(text); return true; } };
const sessionManager = { currentKey: 'websocket:default', ensureAttached() {} };
const i18n = { t: (k) => k };
globalThis.document = {
  createElement: () => ({
    className: '', textContent: '', isConnected: false,
    appendChild() {}, remove() { this.isConnected = false; },
  }),
};
class Chat {
  constructor() {
    this.input = { value: '', style: {}, focus() {}, setSelectionRange() {} };
    this.chatArea = { appendChild(node) { node.isConnected = true; } };
    this.imageHandler = { getImages: () => [], getAttachmentEntries: () => [], clear() {} };
    this._draftAfterSend = null;
  }
  _autoResize() {}
  _updateSendState() {}
  _updateActions() {}
  _resetStreamState() {}
  _setMessageSource() {}
  _appendMsgActions() {}
  _appendTime() {}
  _renderMediaAttachments() {}
  scrollToBottom() {}
  startNewChat() {}
"""
    + member(CHAT_SRC, "runCommand")
    + "\n"
    + member(CHAT_SRC, "prefillComposer")
    + "\n"
    + member(CHAT_SRC, "_sendCommandLine")
    + "\n"
    + member(CHAT_SRC, "sendMessage")
    + "\n"
    + member(CHAT_SRC, "_takeBackPendingSend")
    + """
}
const chat = new Chat();
"""
)


def test_a_command_with_an_argument_keeps_the_draft_for_after() -> None:
    run_js(
        _HARNESS
        + """
      chat.input.value = 'una domanda a meta';
      chat.runCommand({ command: '/model', arg_hint: '<nome>' });
      assert.equal(chat.input.value, '/model ');
      chat.input.value = '/model veloce';
      await chat.sendMessage();
      assert.deepEqual(sent, ['/model veloce']);
      assert.equal(chat.input.value, 'una domanda a meta', 'la bozza torna dopo il comando');
      await chat.sendMessage();
      assert.deepEqual(sent, ['/model veloce', 'una domanda a meta']);
      assert.equal(chat.input.value, '', 'e poi non torna piu');
    """
    )


def test_ask_jenny_from_another_view_keeps_the_draft() -> None:
    run_js(
        _HARNESS
        + "class Shell {\n"
        + member(APP_SRC, "sendInChat")
        + """
        switchMode() {}
      }
      const shell = new Shell();
      shell.controllers = { chat };
      chat.input.value = 'stavo scrivendo';
      shell.sendInChat('Insegnami una skill');
      assert.equal(chat.input.value, 'Insegnami una skill');
      // Un secondo precompilato non manda via la bozza vera.
      shell.sendInChat('Un altra richiesta');
      await chat.sendMessage();
      assert.deepEqual(sent, ['Un altra richiesta']);
      assert.equal(chat.input.value, 'stavo scrivendo');
    """
    )


def test_an_empty_composer_has_nothing_to_keep() -> None:
    run_js(
        _HARNESS
        + """
      chat.runCommand({ command: '/model', arg_hint: '<nome>' });
      assert.equal(chat._draftAfterSend, null);
      chat.input.value = '/model x';
      await chat.sendMessage();
      assert.equal(chat.input.value, '');
    """
    )


def test_a_rejected_prefill_gives_its_text_back_and_keeps_the_draft() -> None:
    """Il testo precompilato parte, la bozza torna nel campo, e il gateway lo
    rifiuta: il testo rifiutato torna nel campo, e la bozza torna da parte
    per l'invio dopo."""
    run_js(
        _HARNESS
        + """
      chat.input.value = 'la mia bozza';
      chat.prefillComposer('Insegnami una skill');
      await chat.sendMessage();
      assert.equal(chat.input.value, 'la mia bozza');
      assert.equal(chat._takeBackPendingSend(), true);
      assert.equal(chat.input.value, 'Insegnami una skill', 'il testo rifiutato e\\u2019 perso');
      await chat.sendMessage();
      assert.deepEqual(sent, ['Insegnami una skill', 'Insegnami una skill']);
      assert.equal(chat.input.value, 'la mia bozza', 'la bozza e\\u2019 persa');
    """
    )


def test_a_rejection_does_not_overwrite_what_you_typed_after_the_prefill() -> None:
    run_js(
        _HARNESS
        + """
      chat.input.value = 'la mia bozza';
      chat.prefillComposer('Insegnami una skill');
      await chat.sendMessage();
      chat.input.value = 'la mia bozza, cambiata';
      chat._takeBackPendingSend();
      assert.equal(chat.input.value, 'la mia bozza, cambiata');
      assert.equal(chat._draftAfterSend, null);
    """
    )
