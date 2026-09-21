"""«I file veri»: la scheda di Memoria che legge davvero il workspace.

Era una voce della tavola che nel prodotto non esisteva. Al suo posto c'era una
riga in fondo al giardiniere che portava al gestore file — un collegamento, non
un contenuto — e il commento in cima a ``mobile-settings.js`` lo diceva:
«mostrarlo anche qui vuole una lettura che questa schermata non fa». Dal
21/09/2026 quella lettura c'e'.

Tre cose non si misurano leggendo il sorgente, e girano qui in node su un DOM
finto:

* **I file di servizio non si elencano.** Il flag ``internal`` lo mette il
  server file per file; il filtro qui non ha piu' nessuna condizione davanti da
  quando la modalita' sviluppatore e' sparita.
* **Il tetto di righe tiene.** Alla radice di un workspace vissuto ci sono
  decine di voci, e una scheda che le elenca tutte rifa' il difetto che questo
  giro ha appena tolto dalla storia locale.
* **Una risposta che non arriva lo dice.** Un contenitore che resta sul
  segnaposto «Caricamento…» per sempre e' il modo in cui un guasto di rete si
  traveste da lentezza.
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
SETTINGS_JS = ASSETS / "mobile-settings.js"
I18N_JS = ASSETS / "shared" / "i18n.js"
I18N_DIR = ASSETS / "i18n"

_NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(_NODE is None, reason="node non disponibile")


def _member(source: str, name: str) -> str:
    m = re.search(
        rf"\n  ((?:async |get )?{re.escape(name)}\([^)]*\)\s*\{{.*?)\n  \}}",
        source,
        re.S,
    )
    assert m, f"{name} non trovato"
    return m.group(1) + "\n  }"


def _const(source: str, name: str) -> str:
    m = re.search(rf"(?m)^export const {re.escape(name)} = .+?;$", source)
    assert m, f"const {name} non trovata"
    return m.group(0).removeprefix("export ")


_HARNESS = """
import assert from 'node:assert/strict';

const TRANSLATIONS = __TRANSLATIONS__;
const i18n = { locale: 'it', translations: TRANSLATIONS, __T__ };

const escapeHtml = (s) => String(s)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;');

__TETTO__

/* Quel che il server risponde a `/api/workspace/list`, deciso dal banco. */
let risposta = null;
let errore = null;
const chiamate = [];
const api = {
  listWorkspace(path) {
    chiamate.push(path);
    return errore ? Promise.reject(errore) : Promise.resolve(risposta);
  },
};

/* Un contenitore solo: la scheda scrive in `#settings-file-lista` e basta. */
function contenitore(presente) {
  const box = { innerHTML: '' };
  return { querySelector: (sel) => (presente && sel === '#settings-file-lista' ? box : null), box };
}

class SettingsController {
  constructor(contentEl) { this.contentEl = contentEl; }
  __RENDER_FILE__
  __CARICA_FILE__
  __PESO_FILE__
}

function voce(name, type, size, internal) {
  return { name, type, size: size ?? null, internal: !!internal };
}

async function scheda(items, { rotto = false, senzaBox = false } = {}) {
  risposta = items === null ? null : { items, path: '' };
  errore = rotto ? new Error('gateway giu') : null;
  chiamate.length = 0;
  const el = contenitore(!senzaBox);
  const c = new SettingsController(el);
  await c._caricaFile();
  return { html: el.box.innerHTML, c };
}

function nomi(html) {
  return [...html.matchAll(/class="file-riga-nome">([^<]*)</g)].map((m) => m[1]);
}
"""


def _harness() -> str:
    src = SETTINGS_JS.read_text(encoding="utf-8")
    it = json.loads((I18N_DIR / "it.json").read_text(encoding="utf-8"))
    return (
        _HARNESS.replace("__TRANSLATIONS__", json.dumps({"it": it}, ensure_ascii=False))
        .replace("__T__", _member(I18N_JS.read_text(encoding="utf-8"), "t"))
        .replace("__TETTO__", _const(src, "TETTO_FILE"))
        .replace("__RENDER_FILE__", _member(src, "_renderFile"))
        .replace("__CARICA_FILE__", _member(src, "_caricaFile"))
        .replace("__PESO_FILE__", _member(src, "_pesoFile"))
    )


def _run_js(script: str) -> None:
    proc = subprocess.run(
        [str(_NODE), "--input-type=module", "-e", _harness() + "\n" + script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_service_files_are_never_listed() -> None:
    """Il flag lo mette il server; la scheda lo rispetta senza chiedere
    permesso a nessuno — l'interruttore che lo scavalcava non esiste piu'."""
    _run_js("""
      const { html } = await scheda([
        voce('progetti', 'directory'),
        voce('sessions', 'directory', null, true),
        voce('note.md', 'file', 120),
        voce('config.json', 'file', 900, true),
      ]);
      assert.deepEqual(nomi(html), ['progetti', 'note.md']);
    """)


def test_folders_come_first_then_files_each_alphabetical() -> None:
    """E' l'ordine del gestore file: due schermate sugli stessi dati che li
    ordinano diversamente sembrano parlare di due cartelle diverse."""
    _run_js("""
      const { html } = await scheda([
        voce('zeta.md', 'file', 10),
        voce('orto', 'directory'),
        voce('alfa.md', 'file', 10),
        voce('appunti', 'directory'),
      ]);
      assert.deepEqual(nomi(html), ['appunti', 'orto', 'alfa.md', 'zeta.md']);
    """)


def test_the_cap_is_small_enough_to_be_a_cap() -> None:
    """Il banco qui sotto legge `TETTO_FILE` dal sorgente, quindi da solo non
    sa distinguere un tetto da un numero enorme: alzarlo a 99 lo lascerebbe
    verde e rimetterebbe in piedi l'elenco lungo che questo giro ha appena
    tolto alla storia locale. Il valore percio' si guarda anche da fuori.

    Dodici e' largo: sono gia' mezzo schermo di un telefono da 1440 px, e la
    scheda deve poter stare **dentro** un cassetto che si scorre insieme ad
    altri quattro gruppi.
    """
    tetto = int(re.search(r"export const TETTO_FILE = (\d+);", SETTINGS_JS.read_text(encoding="utf-8")).group(1))
    assert 3 <= tetto <= 12, f"TETTO_FILE = {tetto}: non e' piu' un tetto, e' un elenco"


def test_the_row_cap_holds_and_the_rest_is_counted() -> None:
    """Il tetto e' la ragione per cui questa scheda non ridiventa l'elenco che
    il giro ha appena tolto alla storia locale."""
    _run_js("""
      const molte = [];
      for (let i = 0; i < TETTO_FILE + 5; i++) {
        molte.push(voce(`c${String(i).padStart(2, '0')}`, 'directory'));
      }
      const { html } = await scheda(molte);
      assert.equal(nomi(html).length, TETTO_FILE, 'il tetto non tiene');
      assert.ok(html.includes('file-riga-avanzo'), 'quel che avanza non si conta');
      assert.ok(html.includes('5'), 'il conto di quel che avanza e\\u2019 sbagliato: ' + html);
    """)


def test_nothing_left_over_means_no_counting_row() -> None:
    """Una riga «e altre 0 voci» sotto un elenco completo e' peggio di niente."""
    _run_js("""
      const { html } = await scheda([voce('orto', 'directory'), voce('note.md', 'file', 12)]);
      assert.ok(!html.includes('file-riga-avanzo'), 'conta un avanzo che non c\\u2019e\\u2019');
    """)


def test_an_empty_workspace_says_so() -> None:
    """Vuoto e «non l'ho letta» sono due risposte diverse, e la scheda le
    distingue: un contenitore vuoto le confonderebbe."""
    _run_js("""
      const vuoto = await scheda([]);
      assert.ok(vuoto.html.includes(i18n.t('officina.file.vuoto')), vuoto.html);
      const solointerni = await scheda([voce('sessions', 'directory', null, true)]);
      assert.ok(solointerni.html.includes(i18n.t('officina.file.vuoto')), solointerni.html);
    """)


def test_a_read_that_failed_says_so_instead_of_loading_forever() -> None:
    """Il segnaposto e' «Caricamento…»: lasciarcelo per sempre e' il modo in
    cui un guasto di rete si traveste da lentezza."""
    _run_js("""
      const { html } = await scheda(null, { rotto: true });
      assert.ok(html.includes(i18n.t('officina.file.errore')), html);
      assert.ok(!html.includes(i18n.t('settings.loading')), 'e\\u2019 rimasto sul segnaposto');
    """)


def test_outside_memoria_the_read_does_not_happen() -> None:
    """`_caricaFile` gira col cablaggio di ogni cassetto. Negli altri due il
    contenitore non c'e', e una richiesta per una scheda che non e' a schermo
    e' peso pagato per niente."""
    _run_js("""
      await scheda([voce('orto', 'directory')], { senzaBox: true });
      assert.deepEqual(chiamate, [], 'ha letto il workspace da un cassetto che non lo mostra');
    """)


def test_the_card_carries_the_way_into_the_file_manager() -> None:
    """La scheda dice cosa c'e'; ad aprirlo si va dal gestore file. Senza questa
    riga il gestore resta vivo e senza nessuna maniglia — e' la porta che
    `test_officina_cassetti_contract.py` conta."""
    _run_js("""
      const c = new SettingsController(contenitore(true));
      const html = c._renderFile();
      assert.ok(html.includes('data-porta="workspace"'), html);
      assert.ok(html.includes('settings-file-lista'), 'manca il contenitore delle righe');
    """)


def test_a_size_that_the_server_did_not_send_is_not_invented() -> None:
    """Le cartelle arrivano con ``size: null``; «NaN B» accanto a un nome e' il
    modo piu' rapido di far sembrare rotta una schermata che non lo e'."""
    _run_js("""
      const c = new SettingsController(contenitore(true));
      assert.equal(c._pesoFile(null), '');
      assert.equal(c._pesoFile(undefined), '');
      assert.equal(c._pesoFile(0), '0 B');
      assert.equal(c._pesoFile(2400), '2.4 kB');
      assert.equal(c._pesoFile(3_500_000), '3.5 MB');
    """)
