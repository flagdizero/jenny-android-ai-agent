"""«Backup» in casa: la data che prima non esisteva, e due cose che si somigliano.

Il giro — passphrase, cifratura, i due picker SAF, il riavvio — sta in
`shared/backup-flow.js` e lo usano gia' l'officina e l'onboarding. Qui si
misura solo cio' che e' di questa stanza:

**«Mai fatto» si dice.** E' l'informazione piu' utile che quella riga possa
portare, ed e' anche l'unico momento in cui serve davvero leggerla: chi un
backup lo fa ogni settimana non ha bisogno che glielo si ricordi.

**La riga si muove solo su un export riuscito.** Fra il container cifrato e il
file su disco c'e' una schermata di sistema che si puo' annullare: scrivere
«ultimo backup: adesso» dopo un annullamento sarebbe la bugia peggiore di
questa pagina.

**La storia locale non e' un backup**, e somiglia abbastanza da essere
scambiata per uno: vive sullo stesso telefono, quindi di un telefono perso non
salva niente. La stanza lo dice in una frase invece di lasciarlo capire.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"
ROOM_JS = ASSETS / "casa-backup.js"
WHEN_JS = ASSETS / "shared" / "when.js"
I18N_JS = ASSETS / "shared" / "i18n.js"
I18N_DIR = ASSETS / "i18n"

_NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(_NODE is None, reason="node non disponibile")


def _member(source: str, name: str) -> str:
    m = re.search(
        rf"\n  ((?:async |get )?{re.escape(name)}\([^)]*\)\s*\{{.*?)\n  \}}", source, re.S
    )
    assert m, f"{name} non trovato"
    return m.group(1) + "\n  }"


def _function(source: str, name: str) -> str:
    m = re.search(rf"(?ms)^export function {re.escape(name)}\(.*?^\}}$", source)
    assert m, f"function {name} non trovata"
    return m.group(0).replace("export function", "function")


_HARNESS = """
import assert from 'node:assert/strict';

const TRANSLATIONS = __TRANSLATIONS__;
const i18n = { locale: 'it', translations: TRANSLATIONS, __T__ };

function makeEl(tag) {
  const el = {
    tag, className: '', textContent: '', hidden: false, disabled: false,
    attrs: {}, children: [], listeners: {},
    setAttribute(k, v) { el.attrs[k] = v; },
    addEventListener(t, fn) { (el.listeners[t] ||= []).push(fn); },
    appendChild(c) { el.children.push(c); return c; },
    classList: {
      add(n) { if (!el.classList.contains(n)) el.className = (el.className + ' ' + n).trim(); },
      remove(n) {
        el.className = String(el.className).split(' ').filter((x) => x && x !== n).join(' ');
      },
      contains(n) { return String(el.className).split(' ').includes(n); },
      toggle(n, on) { if (on) el.classList.add(n); else el.classList.remove(n); },
    },
  };
  return el;
}

const nodi = {};
const document = { createElement: (t) => makeEl(t), getElementById: (id) => (nodi[id] ||= makeEl('div')) };

/* Il flusso condiviso, finto: quel che conta qui e' l'esito, non il giro. */
let esitoExport = true;
let esitoImport = true;
let nativoPresente = true;
const fatti = [];
function runExportFlow() { fatti.push('export'); return Promise.resolve(esitoExport); }
function runImportFlow() { fatti.push('import'); return Promise.resolve(esitoImport); }
function backupNativeAvailable() { return nativoPresente; }

__WHEN_TEXT__
__BACKUP_VALUE__

class CasaBackup {
  __CTOR__
  __SET_BACKUP__
  __OPEN__
  __VALUE__
  __APPLY_TRANSLATIONS__
  __RUN_EXPORT__
  __RUN_IMPORT__
  __PAINT__
}

const avvisi = [];
const giorno = 86400000;
function stanza(backup) {
  for (const k of Object.keys(nodi)) delete nodi[k];
  esitoExport = true;
  esitoImport = true;
  nativoPresente = true;
  fatti.length = 0;
  avvisi.length = 0;
  const s = new CasaBackup({ onExported: () => avvisi.push('riga riscritta') });
  s.setBackup(backup);
  return s;
}
"""


def _harness() -> str:
    room = ROOM_JS.read_text(encoding="utf-8")
    it = json.loads((I18N_DIR / "it.json").read_text(encoding="utf-8"))
    return (
        _HARNESS.replace("__TRANSLATIONS__", json.dumps({"it": it}, ensure_ascii=False))
        .replace("__T__", _member(I18N_JS.read_text(encoding="utf-8"), "t"))
        .replace("__WHEN_TEXT__", _function(WHEN_JS.read_text(encoding="utf-8"), "whenText"))
        .replace("__BACKUP_VALUE__", _function(room, "backupValue"))
        .replace("__CTOR__", _member(room, "constructor"))
        .replace("__SET_BACKUP__", _member(room, "setBackup"))
        .replace("__OPEN__", _member(room, "open"))
        .replace("__VALUE__", _member(room, "value"))
        .replace("__APPLY_TRANSLATIONS__", _member(room, "applyTranslations"))
        .replace("__RUN_EXPORT__", _member(room, "runExport"))
        .replace("__RUN_IMPORT__", _member(room, "runImport"))
        .replace("__PAINT__", _member(room, "_paint"))
    )


def _run_js(script: str) -> None:
    proc = subprocess.run(
        [str(_NODE), "--input-type=module", "-e", _harness() + "\n" + script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_never_having_made_one_is_said_out_loud() -> None:
    """E' l'informazione piu' utile che quella riga possa portare, ed e' anche
    l'unico momento in cui serve leggerla."""
    _run_js("""
      assert.equal(backupValue(null), i18n.t('casa.backup.never'));
      assert.equal(backupValue({ last_export_at: 0 }), i18n.t('casa.backup.never'));

      const s = stanza(null);
      assert.equal(nodi['casa-backup-when'].textContent, i18n.t('casa.backup.neverLong'));
      assert.ok(nodi['casa-backup-when'].classList.contains('is-warn'),
        'mai fatto non si distingue da un backup di ieri');
    """)


def test_the_row_says_when_like_a_person_would() -> None:
    """La stessa frase dell'ultimo controllo aggiornamenti: e' la stessa
    domanda, e la risposta la scrive un posto solo (`shared/when.js`)."""
    _run_js("""
      const adesso = Date.now() / 1000;
      assert.ok(backupValue({ last_export_at: adesso }).startsWith('oggi alle '),
        backupValue({ last_export_at: adesso }));

      /* I secondi epoch del config diventano millisecondi: sbagliare la
         conversione darebbe «gennaio 1970», che a schermo sembra un guasto. */
      const treGiorni = (Date.now() - 3 * giorno) / 1000;
      assert.ok(!backupValue({ last_export_at: treGiorni }).includes('1970'),
        backupValue({ last_export_at: treGiorni }));

      const s = stanza({ last_export_at: adesso });
      assert.ok(nodi['casa-backup-when'].textContent.includes('oggi alle '));
      assert.equal(nodi['casa-backup-when'].classList.contains('is-warn'), false);
    """)


def test_a_cancelled_export_does_not_move_the_row() -> None:
    """Fra il container cifrato e il file su disco c'e' una schermata di
    sistema che si puo' annullare."""
    _run_js("""
      const s = stanza(null);
      esitoExport = false;
      await s.runExport();

      assert.deepEqual(fatti, ['export']);
      assert.equal(s.value(), i18n.t('casa.backup.never'), 'la riga si e mossa su un annullamento');
      assert.deepEqual(avvisi, [], 'ha avvisato «Tu e Jenny» di un backup che non c e');
      assert.equal(nodi['casa-backup-export'].disabled, false, 'il bottone e rimasto spento');
    """)


def test_a_finished_export_moves_the_row_at_once() -> None:
    """Senza, la data comparirebbe solo alla prossima apertura della pagina —
    cioe' proprio dopo il gesto con cui l'hai fatta."""
    _run_js("""
      const s = stanza(null);
      await s.runExport();

      assert.notEqual(s.value(), i18n.t('casa.backup.never'));
      assert.ok(s.value().startsWith('oggi alle '), s.value());
      assert.deepEqual(avvisi, ['riga riscritta']);
    """)


def test_a_second_tap_while_exporting_does_nothing() -> None:
    """Il flusso condiviso ha gia' la sua mutua esclusione; qui il bottone non
    deve nemmeno sembrare premibile, o si preme due volte e si aprono due
    dialoghi della passphrase."""
    _run_js("""
      const s = stanza(null);
      const primo = s.runExport();               // non atteso: e' ancora in volo
      assert.equal(nodi['casa-backup-export'].disabled, true, 'si puo premere di nuovo');
      await s.runExport();                       // il secondo tocco
      await primo;
      assert.deepEqual(fatti, ['export'], 'due giri di export insieme: ' + fatti.join(','));
      assert.equal(nodi['casa-backup-export'].disabled, false, 'il bottone e rimasto spento');
    """)


def test_without_the_native_bridge_the_buttons_are_not_there() -> None:
    """Fuori dall'APK i due picker non esistono, e un bottone che non fa niente
    e' peggio di un bottone che manca: si dice perche'."""
    _run_js("""
      for (const k of Object.keys(nodi)) delete nodi[k];
      nativoPresente = false;
      const s = new CasaBackup({});
      s.setBackup(null);
      assert.equal(nodi['casa-backup-export'].hidden, true);
      assert.equal(nodi['casa-backup-import'].hidden, true);
      /* Una nota senza il suo bottone promette un gesto che non c'e': la
         scheda del ripristino sparisce tutta (visto sul rig). */
      assert.equal(nodi['casa-backup-import-card'].hidden, true);
      const nota = nodi['casa-backup-export-note'].textContent;
      assert.ok(nota.includes(i18n.t('backup.androidOnly')), 'non dice perche non si puo');
      assert.ok(nota.includes(i18n.t('casa.backup.exportHint')),
        'il motivo ha mangiato la spiegazione di cosa sia un backup');
    """)


def test_the_local_history_is_told_apart_from_a_backup() -> None:
    """Somiglia abbastanza da essere scambiata per un backup: e' automatica, e
    rimette a posto una cosa cancellata per sbaglio. Ma vive su questo
    telefono, quindi di un telefono perso non salva niente."""
    _run_js("""
      stanza({ snapshots_enabled: true });
      const accesa = nodi['casa-backup-snapshots'].textContent;
      assert.equal(accesa, i18n.t('casa.backup.snapshots'));
      assert.ok(accesa.length > 40, 'la frase non spiega niente');

      stanza({ snapshots_enabled: false });
      assert.equal(nodi['casa-backup-snapshots'].textContent,
                   i18n.t('casa.backup.snapshotsOff'));
      assert.notEqual(i18n.t('casa.backup.snapshots'), i18n.t('casa.backup.snapshotsOff'),
        'spenta e accesa si leggono uguali');
    """)
