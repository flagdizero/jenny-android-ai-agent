"""Due domande di seguito sullo stesso `<dialog>`, in entrambi gli ordini.

`close()` non consegna il suo evento `close` all'istante: su alcuni motori — la
WebView di Android fra questi — arriva come task separato. Se la Promise del
prompt si risolve **prima** che quel task sia stato eseguito, il chiamante apre
la domanda successiva e l'evento arretrato finisce nei listener del *nuovo*
prompt, che lo leggono come «chiuso dall'utente». Visto sul telefono il 22/08:
creare un progetto fa due domande di seguito (nome, poi riga di scope) e la
seconda si chiudeva da sé prima di comparire — dalla UI **non si poteva creare
nessun progetto**.

La cintura era un `setTimeout(…, 0)`, che quell'evento lo consuma a vuoto *se* il
task del `close` viene eseguito prima del timer. Ma sono due task source diverse
(DOM manipulation e timers) e l'ordine fra source HTML non lo fissa: dove il
timer vince, il guasto è identico a quello di sopra. Non era una cintura, era una
scommessa vinta su Chromium.

Qui i due ordini si scelgono, invece di sperarli: `FakeDialog` consegna il
proprio `close` dopo un ritardo che il test decide. Con 0 vince il `close` (è
l'ordine di Chromium, e ci passava anche il timer); con 5 vince il timer, ed è il
caso che la vecchia versione perdeva. `closeThenResolve` e `promptDialog` si
estraggono dal sorgente e girano in node, come in
``test_chat_switch_race_client.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

from support.js_harness import function, requires_node, run_js

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
DIALOG_JS = ASSETS / "shared" / "dialog.js"


pytestmark = requires_node


def _source() -> str:
    return DIALOG_JS.read_text(encoding="utf-8")


_HARNESS = """
import assert from 'node:assert/strict';

const i18n = { t: (key) => 'i18n:' + key };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* Un elemento ridotto a quel che questi tre modali toccano: listener, testo,
   valore. `dispatch` consegna l'evento come lo consegnerebbe il motore, ai soli
   listener iscritti *in quel momento* — che è il punto di tutto il test. */
class FakeEl {
  constructor(id) {
    this.id = id;
    this.listeners = new Map();
    this.textContent = '';
    this.placeholder = '';
    this.value = '';
    this.focused = 0;
  }
  addEventListener(type, fn, opts) {
    const list = this.listeners.get(type) || [];
    list.push({ fn, once: Boolean(opts && opts.once) });
    this.listeners.set(type, list);
  }
  removeEventListener(type, fn) {
    const list = this.listeners.get(type) || [];
    this.listeners.set(type, list.filter((e) => e.fn !== fn));
  }
  dispatch(type) {
    const list = (this.listeners.get(type) || []).slice();
    this.listeners.set(type, list.filter((e) => !e.once));
    for (const entry of list) entry.fn({ type, target: this });
  }
  focus() { this.focused++; }
}

/* Il `<dialog>`: `close()` azzera `open` di sincrono e **accoda** l'evento, con
   il ritardo che il test decide. `deliverMs = 0` = l'ordine di Chromium;
   `deliverMs = 5` = il `close` arriva dopo un timer a 0, cioè l'ordine che HTML
   permette e che la vecchia versione perdeva. */
class FakeDialog extends FakeEl {
  constructor(id, deliverMs) {
    super(id);
    this.open = false;
    this.deliverMs = deliverMs;
    this.opens = 0;
  }
  showModal() { this.open = true; this.opens++; }
  close() {
    if (!this.open) return;          // come il vero: su un dialog chiuso è un no-op
    this.open = false;
    setTimeout(() => this.dispatch('close'), this.deliverMs);
  }
}

let els = {};
const document = { getElementById: (id) => els[id] || null };

function mountPrompt(deliverMs) {
  const dialog = new FakeDialog('oc-prompt-dialog', deliverMs);
  els = {
    'oc-prompt-dialog': dialog,
    'oc-prompt-message': new FakeEl('oc-prompt-message'),
    'oc-prompt-input': new FakeEl('oc-prompt-input'),
    'oc-prompt-ok': new FakeEl('oc-prompt-ok'),
    'oc-prompt-cancel': new FakeEl('oc-prompt-cancel'),
  };
  return els;
}

/* Il tocco su OK con il testo dentro. Non aspetta: chi chiama decide *quando*
   l'utente tocca, ed è lì che il difetto vive. */
function answer(text) {
  els['oc-prompt-input'].value = text;
  els['oc-prompt-ok'].dispatch('click');
}

/* Una Promise che non si risolve è il guasto peggiore di tutti: il chiamante
   resta appeso e nessun dialogo è aperto. Qui si misura. */
function within(ms, promise) {
  return Promise.race([
    promise,
    sleep(ms).then(() => { throw new Error('promise mai risolta entro ' + ms + 'ms'); }),
  ]);
}

__CLOSE_THEN_RESOLVE__
__PROMPT_DIALOG__
"""


def _harness() -> str:
    src = _source()
    return (
        _HARNESS.replace("__CLOSE_THEN_RESOLVE__", function(src, "closeThenResolve"))
        .replace("__PROMPT_DIALOG__", function(src, "promptDialog"))
    )


def _run_js(script: str) -> None:
    source = _harness() + "\n" + script
    run_js(source)


# ── Le due domande di `_createProject`, nei due ordini ─────────────────────

_TWO_PROMPTS = """
  const dialog = mountPrompt(__DELIVER__)['oc-prompt-dialog'];

  const first = promptDialog('nome');
  answer('bordi');
  assert.equal(await within(200, first), 'bordi');

  // La seconda domanda: si apre, e l'utente ci pensa un attimo prima di
  // rispondere. È in quell'attimo che un `close` arretrato la annullava.
  const second = promptDialog('riga di scope');
  await sleep(30);
  assert.equal(dialog.opens, 2, 'la seconda domanda non è nemmeno stata aperta');
  answer('i bordi delle stampe');
  assert.equal(await within(200, second), 'i bordi delle stampe',
               'la seconda domanda si è chiusa da sé: nessun progetto si può creare');
"""


def test_the_two_back_to_back_prompts_work_when_close_arrives_first() -> None:
    """L'ordine di Chromium: il task del `close` prima del timer."""
    _run_js(_TWO_PROMPTS.replace("__DELIVER__", "0"))


def test_the_two_back_to_back_prompts_work_when_the_close_arrives_late() -> None:
    """L'ordine che HTML permette, e che il `setTimeout(…, 0)` perdeva.

    È il difetto originale per intero: la Promise si risolveva senza che l'evento
    fosse stato consegnato, il chiamante apriva il secondo prompt, e l'evento
    arretrato lo annullava — «serve una riga» e nessun progetto creato.
    """
    _run_js(_TWO_PROMPTS.replace("__DELIVER__", "5"))


# ── Ogni via d'uscita risolve, e una volta sola ────────────────────────────


def test_cancelling_resolves_null_in_both_orders() -> None:
    _run_js("""
      for (const deliverMs of [0, 5]) {
        const nodes = mountPrompt(deliverMs);
        const p = promptDialog('nome');
        nodes['oc-prompt-cancel'].dispatch('click');
        assert.equal(await within(200, p), null, 'ritardo ' + deliverMs);
        assert.equal(nodes['oc-prompt-dialog'].open, false);
      }
    """)


def test_a_close_that_comes_from_outside_still_settles() -> None:
    """Esc, gesto Indietro, backdrop: il `close` arriva **prima** del `cleanup`.

    Qui `closeThenResolve` trova il dialog già chiuso, e un secondo evento non
    arriverà mai: se aspettasse quello, il chiamante resterebbe appeso — che è
    peggio del guasto che stiamo togliendo, perché nessun dialogo è più aperto.
    """
    _run_js("""
      const dialog = mountPrompt(0)['oc-prompt-dialog'];
      const p = promptDialog('nome');
      // Il motore ha chiuso lui: `open` è già false quando l'evento arriva.
      dialog.open = false;
      dialog.dispatch('close');
      assert.equal(await within(200, p), null);
    """)


def test_the_cancel_event_does_not_resolve_twice() -> None:
    """`cancel` chiude e poi il `close` arriva comunque: una risposta sola."""
    _run_js("""
      const dialog = mountPrompt(5)['oc-prompt-dialog'];
      let settled = 0;
      const p = promptDialog('nome').then((v) => { settled++; return v; });
      dialog.dispatch('cancel');
      assert.equal(await within(200, p), null);
      await sleep(30);
      assert.equal(settled, 1);
      // E il dialog è tornato disponibile per la domanda dopo.
      assert.equal(dialog.open, false);
      const next = promptDialog('seconda');
      assert.equal(dialog.opens, 2, 'il dialog è rimasto inservibile dopo un annullamento');
      answer('bordi');
      assert.equal(await within(200, next), 'bordi');
    """)


def test_a_double_tap_resolves_as_cancelled_instead_of_throwing() -> None:
    """La guardia `if (dialog.open) return`: `showModal()` su un dialog aperto solleva."""
    _run_js("""
      const dialog = mountPrompt(0)['oc-prompt-dialog'];
      const first = promptDialog('nome');
      const second = await within(200, promptDialog('nome'));
      assert.equal(second, null, 'la seconda apertura deve valere come annullata');
      assert.equal(dialog.opens, 1);
      answer('bordi');
      assert.equal(await within(200, first), 'bordi');
    """)


# ── Guardia sul testo del sorgente ────────────────────────────────────────
#
# Debole per costruzione: prova che una riga c'è, non che faccia effetto.


def test_no_modal_resolves_on_a_timer() -> None:
    """La scommessa non torni da un'altra porta: nessun `setTimeout` risolutore.

    Il `setTimeout` che resta è quello del `focus()` sull'input, che non risolve
    niente.
    """
    src = _source()
    body = function(src, "closeThenResolve")
    assert "setTimeout" not in body, "la risoluzione torna a dipendere dall'ordine di due task source"
    assert "addEventListener('close'" in body and "{ once: true }" in body
    timers = re.findall(r"setTimeout\(\(\) => ([^,]+),", src)
    assert timers == ["inputEl.focus()"], f"timer inattesi: {timers}"
