"""Modifica una pagina dal lettore: il salvataggio, e il conflitto con Jenny.

Queste pagine le scrive **anche lei**, con gli strumenti file di sempre. Fra il
momento in cui l'editor si apre e quello in cui si salva, il file puo' essere
cambiato sotto: il client manda il testo da cui e' partito (`base`) e il server
risponde `conflict` invece di scrivere.

Il banco vive attorno a tre domande, e sono tre perche' ognuna ha un modo
diverso di far sparire del lavoro senza che nessuno se ne accorga:

1. `base` e' il **sorgente caricato**, non quel che c'e' nella textarea. Se
   fossero la stessa cosa il confronto sarebbe sempre vero e il conflitto non
   scatterebbe mai.
2. Un errore **non chiude l'editor**: dentro c'e' l'unica copia di quel testo.
3. La conferma di scarto sta in un posto solo, `casa-app.js::_setView`, che e'
   il collo di bottiglia da cui passano tutte le uscite.
"""

from __future__ import annotations

import re
from pathlib import Path

from support.js_harness import member, requires_node, run_js

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
READER_JS = ASSETS / "casa-reader.js"
APP_JS = ASSETS / "casa-app.js"


pytestmark = requires_node

_MEMBERS = ("isDirty", "startEdit", "cancelEdit", "blurEditor", "askCancel", "save", "_onConflict")


def _member(source: str, name: str) -> str:
    return member(source, name, prefixes=("async ",))


_HARNESS = """
import assert from 'node:assert/strict';

/* Il giornale di bordo del banco: cosa e' stato spedito, cosa e' stato detto. */
const spedite = [];
const toast = [];
let risposta = true;          // cosa risponde chi guarda la modale
let esito = null;             // null = va bene; altrimenti l'errore da lanciare

const rpc = {
  writePage(wiki, page, content, base) {
    spedite.push({ wiki, page, content, base });
    return esito ? Promise.reject(esito) : Promise.resolve({});
  },
};
const i18n = { t: (k) => k };
function showToast(m, t) { toast.push([m, t]); }
function confirmDialog() { return Promise.resolve(risposta); }
function elemento() {
  return {
    value: '', hidden: true, scrollTop: 999, caret: null,
    focus() { this.caret = [this.value.length, this.value.length]; },
    blur() {},
    setSelectionRange(a, b) { this.caret = [a, b]; },
  };
}

class Lettore {
  constructor() {
    this.bodyEl = elemento();
    this.editEl = elemento();
    this.barEl = elemento();
    this.notebook = 'orto';
    this.path = 'index.md';
    this.title = 'Orto';
    this.raw = '# Orto\\n';
    this.editing = false;
    this.ricariche = 0;
    this.bodyEl.hidden = false;
  }
  /* `save` ricarica dal server: qui si conta e basta. */
  async load() { this.ricariche += 1; return this.title; }
  __MEMBERS__
}
"""


def _run(script: str) -> None:
    src = READER_JS.read_text(encoding="utf-8")
    body = "\n\n  ".join(_member(src, n) for n in _MEMBERS)
    harness = _HARNESS.replace("__MEMBERS__", body)
    run_js(harness + "\n" + script)


def test_the_base_sent_is_the_loaded_source_not_the_edited_text() -> None:
    """La prova che regge tutto il resto.

    Se `base` fosse `editEl.value` il confronto lato server sarebbe vero per
    costruzione, il `conflict` non scatterebbe mai, e una pagina riscritta da
    Jenny verrebbe cancellata in silenzio a ogni salvataggio.
    """
    _run("""
      const r = new Lettore();
      r.startEdit();
      r.editEl.value = '# Orto\\n\\nlegare a maggio\\n';
      await r.save();
      assert.equal(spedite.length, 1);
      assert.equal(spedite[0].base, '# Orto\\n');
      assert.equal(spedite[0].content, '# Orto\\n\\nlegare a maggio\\n');
      assert.equal(spedite[0].wiki, 'orto');
      assert.equal(spedite[0].page, 'index.md');
    """)


def test_the_editor_opens_at_the_top_not_at_the_end() -> None:
    """`focus()` porta il cursore in fondo, e ci trascina la vista.

    Su una pagina di trenta righe aprire la modifica ti lasciava in coda,
    lontano dal punto che stavi leggendo — e il pulsante lo premi proprio
    perche' stai guardando qualcosa. L'inizio e' dove atterra anche il reso.
    Visto sul telefono, non da qui: questo banco tiene il risultato.
    """
    _run("""
      const r = new Lettore();
      r.raw = 'un testo lungo abbastanza da poter scorrere';
      r.startEdit();
      assert.deepEqual(r.editEl.caret, [0, 0]);
      assert.equal(r.editEl.scrollTop, 0);
    """)


def test_a_saved_page_is_reread_from_the_server() -> None:
    """Non si fida di quel che ha appena scritto: il reso lo fa il server."""
    _run("""
      const r = new Lettore();
      r.startEdit();
      r.editEl.value = 'nuovo';
      await r.save();
      assert.equal(r.ricariche, 1);
      assert.equal(r.editing, false);
      assert.equal(r.editEl.hidden, true);
      assert.equal(r.bodyEl.hidden, false);
    """)


def test_a_failed_save_keeps_the_editor_open() -> None:
    """Dentro la textarea c'e' l'unica copia di quel testo.

    Chiudere l'editor su un errore vorrebbe dire buttarla via *per via di* un
    guasto — cioe' perdere il lavoro proprio nel momento in cui non si e'
    riusciti a metterlo al sicuro.
    """
    _run("""
      const r = new Lettore();
      r.startEdit();
      r.editEl.value = 'lavoro non salvato';
      esito = Object.assign(new Error('boom'), { code: 'internal' });
      await r.save();
      assert.equal(r.editing, true);
      assert.equal(r.editEl.value, 'lavoro non salvato');
      assert.equal(r.ricariche, 0);
      assert.deepEqual(toast.map((t) => t[1]), ['error']);
    """)


def test_a_conflict_refused_keeps_the_text_and_does_not_reload() -> None:
    """Jenny ha riscritto la pagina, e chi ha scritto dice di no.

    L'editor resta aperto col suo testo dentro: e' l'unica copia rimasta, e
    ricaricare al posto suo la butterebbe via per decidere una cosa che non ci
    spetta.
    """
    _run("""
      const r = new Lettore();
      r.startEdit();
      r.editEl.value = 'il mio testo';
      esito = Object.assign(new Error('conflict'), { code: 'conflict' });
      risposta = false;
      await r.save();
      assert.equal(r.editing, true);
      assert.equal(r.editEl.value, 'il mio testo');
      assert.equal(r.ricariche, 0);
    """)


def test_a_conflict_accepted_reloads_the_page() -> None:
    _run("""
      const r = new Lettore();
      r.startEdit();
      r.editEl.value = 'il mio testo';
      esito = Object.assign(new Error('conflict'), { code: 'conflict' });
      risposta = true;
      await r.save();
      assert.equal(r.editing, false);
      assert.equal(r.ricariche, 1);
    """)


def test_an_untouched_editor_closes_without_asking() -> None:
    """Aperto e richiuso senza scrivere: non c'e' niente da chiedere.

    `risposta = false` e' la parte che conta — se la modale comparisse
    comunque, questa risposta terrebbe aperto l'editor.
    """
    _run("""
      const r = new Lettore();
      r.startEdit();
      risposta = false;
      await r.askCancel();
      assert.equal(r.editing, false);
      assert.equal(r.bodyEl.hidden, false);
    """)


def test_a_touched_editor_asks_before_throwing_the_text_away() -> None:
    _run("""
      const r = new Lettore();
      r.startEdit();
      r.editEl.value = 'scritto a mano';
      risposta = false;
      await r.askCancel();
      assert.equal(r.editing, true, 'un no tiene aperto');
      risposta = true;
      await r.askCancel();
      assert.equal(r.editing, false, 'un si chiude');
    """)


def test_the_leave_guard_lives_in_the_single_chokepoint() -> None:
    """Le uscite dal lettore sono quattro, la guardia e' una.

    L'occhiello, l'Indietro del telefono, «Parlane» e un cambio di
    conversazione passano tutte da `_setView`. Metterla sui bottoni vorrebbe
    dire dimenticarsene sulla quinta strada — che e' esattamente il difetto che
    il gestore file ha avuto, con tre strade e due senza guardia.

    Banco sul sorgente, e non c'e' modo di farlo altrimenti: quel che va
    provato e' *dove* sta il controllo, non cosa calcola.
    """
    src = APP_JS.read_text(encoding="utf-8")
    m = re.search(r"\n  _setView\(name[^)]*\) \{.*?\n  \}", src, re.S)
    assert m, "_setView non trovato"
    assert "isDirty()" in m.group(0), "la guardia sulle modifiche non e' in _setView"
    assert "_confirmLeaveReader" in m.group(0)

    # E nessun altro la chiede: una seconda guardia altrove e' una guardia che
    # puo' divergere da questa.
    assert src.count("isDirty()") == 1, "isDirty chiesto in piu' di un posto"
