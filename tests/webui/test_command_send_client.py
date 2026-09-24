"""Un comando non è un messaggio: non porta allegati e non si prende la bozza.

Il pulsante «Nuova chat» sta accanto alla graffetta, e la tendina dei comandi
sopra il composer: nei due casi il comando parte da uno schermo dove qualcuno
può aver già scritto mezza domanda, o messo in coda una foto. Prima partivano
insieme al comando, e con `/new` non era solo spreco: la riga utente di un
comando **sopravvive** nel transcript quando porta media
(`transcript_recorder.append_user_message` la salta solo se `not media_paths`),
e siccome sta nello stesso turno del confine, la chat appena azzerata si
riapriva con `/new` e le sue immagini appesi in cima — cioè con il difetto che
il confine esiste per non avere.

Il testo che si stava scrivendo, invece, torna dov'era: la chat nuova è
esattamente il posto in cui mandarlo.
"""

from __future__ import annotations

import re
from pathlib import Path

from support.js_harness import requires_node, run_js

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
CHAT_JS = ASSETS / "mobile-chat.js"


pytestmark = requires_node


def _member(source: str, name: str) -> str:
    m = re.search(
        rf"\n  ((?:async |get )?{re.escape(name)}\([^)]*\)\s*\{{.*?)\n  \}}",
        source,
        re.S,
    )
    assert m, f"{name} non trovato"
    return m.group(1) + "\n  }"


_HARNESS = """
import assert from 'node:assert/strict';

const i18n = { t: (key) => 'i18n:' + key };
const sessionManager = { currentKey: 'unified:default', ensureAttached() {} };
const sent = [];
const wsManager = {
  sendToChat(key, text, media) { sent.push({ key, text, media }); return true; },
};
const document = {
  createElement() {
    const el = { className: '', textContent: '', children: [] };
    el.appendChild = (c) => { el.children.push(c); return c; };
    return el;
  },
};

function makeChat({ draft = '', images = [] } = {}) {
  const cleared = [];
  const chat = {
    input: {
      value: draft,
      style: {},
      focused: 0,
      focus() { chat.input.focused++; },
    },
    chatArea: { children: [], appendChild(node) { chat.chatArea.children.push(node); } },
    imageHandler: {
      get count() { return images.length; },
      getImages: () => images.map((name) => ({ data_url: 'data:,' + name, name })),
      getAttachmentEntries: () => images.map((name) => ({ url: 'data:,' + name, name })),
      clear() { cleared.push(true); images = []; },
    },
    rendered: [],
    resized: 0,
    _renderMediaAttachments(_msg, entries) { chat.rendered.push(entries); },
    scrollToBottom() {},
    // La bolla dell'eco registra il proprio sorgente e si appende la riga di
    // azioni: entrambi fuori misura qui.
    _setMessageSource() {},
    _appendMsgActions() {},
    _resetStreamState() {},
    _updateSendState() {},
    _updateActions() {},
    _autoResize() { chat.resized++; },
    __SEND__,
    __SEND_COMMAND__,
  };
  chat.cleared = cleared;
  return chat;
}
"""


def _harness() -> str:
    chat = CHAT_JS.read_text(encoding="utf-8")
    return (
        _HARNESS.replace("__SEND__", _member(chat, "sendMessage"))
        .replace("__SEND_COMMAND__", _member(chat, "_sendCommandLine"))
    )


def _run_js(script: str) -> None:
    run_js(_harness() + "\n" + script)


# ── Gli allegati restano nel composer ────────────────────────────────────────


def test_a_command_does_not_carry_the_staged_attachments() -> None:
    _run_js("""
      const chat = makeChat({ images: ['foto.jpg'] });
      await chat._sendCommandLine('/new');
      assert.equal(sent.length, 1);
      assert.equal(sent[0].text, '/new');
      assert.deepEqual(sent[0].media, [], "il comando si è portato dietro la foto");
      assert.deepEqual(chat.cleared, [], "la foto è sparita dal composer");
      assert.deepEqual(chat.rendered, [], 'la bolla del comando ha disegnato gli allegati');
    """)


def test_an_ordinary_message_still_carries_them() -> None:
    """Il default non cambia: è la strada di ogni invio normale."""
    _run_js("""
      const chat = makeChat({ draft: 'guarda qua', images: ['foto.jpg'] });
      await chat.sendMessage();
      assert.equal(sent.length, 1);
      assert.equal(sent[0].media.length, 1);
      assert.equal(chat.rendered.length, 1, 'la bolla non mostra quel che sta partendo');
      assert.deepEqual(chat.cleared, [true], "gli allegati restano in coda dopo l'invio");
    """)


def test_a_command_alone_is_still_sent_with_no_attachments() -> None:
    """La guardia «né testo né immagini» non deve fermare un comando."""
    _run_js("""
      const chat = makeChat();
      await chat._sendCommandLine('/status');
      assert.deepEqual(sent.map((s) => s.text), ['/status']);
    """)


# ── La bozza torna dov'era ──────────────────────────────────────────────────


def test_the_draft_comes_back_after_the_command() -> None:
    _run_js("""
      const chat = makeChat({ draft: 'mezza domanda' });
      await chat._sendCommandLine('/new');
      assert.deepEqual(sent.map((s) => s.text), ['/new'],
                       "è partita la bozza invece del comando");
      assert.equal(chat.input.value, 'mezza domanda',
                   "la bozza è stata mangiata dal comando");
      assert.equal(chat.resized, 1, 'la textarea resta alta una riga sopra un testo lungo');
    """)


def test_an_empty_composer_stays_empty() -> None:
    """Nessuna bozza, nessun ripristino: il composer resta pulito dopo l'invio."""
    _run_js("""
      const chat = makeChat();
      await chat._sendCommandLine('/new');
      assert.equal(chat.input.value, '');
      assert.equal(chat.resized, 0);
    """)


def test_whitespace_is_not_a_draft() -> None:
    """Un a capo lasciato nel composer non è roba da rimettere a posto."""
    _run_js("""
      const chat = makeChat({ draft: '   ' });
      await chat._sendCommandLine('/new');
      assert.equal(chat.input.value, '');
    """)
