"""Dove finisce il conteggio d'uso del cassetto, provato davvero sotto node.

``shared/launcher-usage-store.js`` esiste per un difetto che non si vede:
il ricordo di quel che apri di piu' stava nel ``localStorage`` della WebView,
che Chromium persiste in modo asincrono e che **non sopravvive a un kill del
processo** — mentre le SharedPreferences si' (lo dice gia' un commento di
``MainActivity.kt``, scritto per tutt'altro). Jenny e' il launcher del telefono
e il sistema la uccide di routine: l'ordine «piu' usate» si sbriciolava da se',
poco alla volta, e un cassetto in ordine sbagliato non sembra rotto — sembra
solo che il ranking non serva a niente.

Il modulo prende ponte e storage **come argomenti** proprio per questo file:
sotto node non esistono ne' ``window`` ne' ``localStorage``, e stubbarli
globalmente nasconderebbe il fatto che il modulo non li tocca da se'.

La casella che conta piu' di tutte e' la **migrazione**: gira una volta su dati
d'utente veri, e se sbaglia l'ordine delle due scritture cancella l'unica copia
rimasta.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

STORE_JS = (
    Path(__file__).resolve().parents[2]
    / "jenny" / "templates" / "ui" / "assets" / "shared" / "launcher-usage-store.js"
)
RANK_JS = STORE_JS.parent / "launcher-rank.js"

_NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(_NODE is None, reason="node non disponibile")

# Un `localStorage` finto (con `removeItem`, che la migrazione usa) e un ponte
# nativo finto che si puo' rompere a comando — i due guasti che contano sono
# «il ponte solleva» e «il ponte accetta e poi non rilegge».
_FINTI = """
function fakeLocal(initial) {
  const data = new Map(Object.entries(initial || {}));
  return {
    getItem: (k) => (data.has(k) ? data.get(k) : null),
    setItem: (k, v) => { data.set(k, String(v)); },
    removeItem: (k) => { data.delete(k); },
    dump: () => Object.fromEntries(data),
  };
}
function fakeNative(initial, opts) {
  const o = opts || {};
  let value = initial || '';
  return {
    getLauncherUsage: () => { if (o.throwOnGet) throw new Error('ponte giu'); return value; },
    setLauncherUsage: (v) => {
      if (o.throwOnSet) throw new Error('ponte giu');
      if (o.swallowWrites) return;          // accetta e non conserva
      value = String(v);
    },
    peek: () => value,
  };
}
"""


def _run_js(script: str, *, with_rank: bool = False) -> str:
    source = STORE_JS.read_text(encoding="utf-8")
    if with_rank:
        source += "\n" + RANK_JS.read_text(encoding="utf-8")
    source += "\nimport assert from 'node:assert/strict';\n" + _FINTI + script
    proc = subprocess.run(
        [str(_NODE), "--input-type=module", "-e", source],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return proc.stdout


# ── La scelta del posto ─────────────────────────────────────────────────────


def test_without_the_bridge_it_stays_on_local_storage() -> None:
    """Fuori dall'APK non c'e' nessun kill da temere: si resta dov'era."""
    out = _run_js("""
const local = fakeLocal({ 'launcher-usage': '{"android:a":[3,10]}' });
const store = usageStore({ native: null, local });
assert.equal(store, local, 'senza ponte deve tornare proprio il localStorage');
assert.equal(store.getItem('launcher-usage'), '{"android:a":[3,10]}');
console.log('ok');
""")
    assert "ok" in out


def test_a_bridge_too_old_to_write_is_not_used_at_all() -> None:
    """Metà ponte è peggio di nessun ponte: leggeresti un giro e perderesti tutto.

    Un APK vecchio espone `getLauncherUsage` e non `setLauncherUsage`. Se il
    modulo guardasse solo la prima, ogni salvataggio cadrebbe nel vuoto e il
    conteggio resterebbe fermo per sempre — senza un sintomo.
    """
    out = _run_js("""
const local = fakeLocal({ 'launcher-usage': '{"android:a":[3,10]}' });
const mezzo = { getLauncherUsage: () => '' };   // manca il setter
assert.equal(nativeUsable(mezzo), false);
assert.equal(usageStore({ native: mezzo, local }), local);
console.log('ok');
""")
    assert "ok" in out


# ── La migrazione ───────────────────────────────────────────────────────────


def test_the_old_value_moves_over_once_and_the_copy_is_removed() -> None:
    """Prima la verita' nuova, poi si toglie la vecchia. E il posto resta uno."""
    out = _run_js("""
const local = fakeLocal({ 'launcher-usage': '{"android:a":[3,10]}' });
const native = fakeNative('');
assert.equal(migrateUsage(native, local), 'migrated');
assert.equal(native.peek(), '{"android:a":[3,10]}', 'il ponte deve avere il valore');
assert.equal(local.getItem('launcher-usage'), null, 'la copia vecchia va tolta');
// Un secondo giro non deve rifare niente.
assert.equal(migrateUsage(native, local), 'native-has-data');
console.log('ok');
""")
    assert "ok" in out


def test_a_failed_write_never_deletes_the_only_copy() -> None:
    """**La casella che vale il file.**

    Se la scrittura sul ponte fallisce e il modulo avesse gia' cancellato il
    `localStorage`, il conteggio di mesi d'uso sparirebbe in silenzio. Due
    guasti diversi, stessa pretesa: il valore vecchio e' ancora li'.
    """
    out = _run_js("""
for (const opts of [{ throwOnSet: true }, { swallowWrites: true }]) {
  const local = fakeLocal({ 'launcher-usage': '{"android:a":[3,10]}' });
  const native = fakeNative('', opts);
  assert.equal(migrateUsage(native, local), 'failed', JSON.stringify(opts));
  assert.equal(
    local.getItem('launcher-usage'), '{"android:a":[3,10]}',
    'il valore vecchio deve sopravvivere a ' + JSON.stringify(opts),
  );
  // E il cassetto deve continuare a funzionare, sul posto vecchio.
  assert.equal(usageStore({ native, local }), local);
}
console.log('ok');
""")
    assert "ok" in out


def test_fresh_data_on_the_bridge_is_never_overwritten_by_a_stale_copy() -> None:
    """Gli avvii di oggi battono una copia rimasta indietro.

    Vale se la rimozione dal `localStorage` era fallita al giro prima: la copia
    stantia e' ancora li', e riportarla sopra butterebbe via il vero.
    """
    out = _run_js("""
const local = fakeLocal({ 'launcher-usage': '{"android:vecchio":[99,1]}' });
const native = fakeNative('{"android:nuovo":[2,500]}');
assert.equal(migrateUsage(native, local), 'native-has-data');
assert.equal(native.peek(), '{"android:nuovo":[2,500]}');
console.log('ok');
""")
    assert "ok" in out


def test_nothing_to_move_is_not_a_failure() -> None:
    """Prima installazione: niente di qua, niente di la'. Si usa il ponte."""
    out = _run_js("""
const local = fakeLocal({});
const native = fakeNative('');
assert.equal(migrateUsage(native, local), 'nothing-to-move');
const store = usageStore({ native, local });
assert.notEqual(store, local, 'deve comunque scegliere il ponte');
store.setItem('launcher-usage', '{"android:a":[1,7]}');
assert.equal(native.peek(), '{"android:a":[1,7]}');
console.log('ok');
""")
    assert "ok" in out


# ── Lo storage sul ponte ────────────────────────────────────────────────────


def test_the_bridge_store_has_one_drawer_and_says_so() -> None:
    """Una chiave che non e' la sua non si inventa: `null`, che chi legge gestisce."""
    out = _run_js("""
const native = fakeNative('{"android:a":[1,2]}');
const store = nativeStore(native);
assert.equal(store.getItem('launcher-usage'), '{"android:a":[1,2]}');
assert.equal(store.getItem('altro'), null);
store.setItem('altro', 'x');
assert.equal(native.peek(), '{"android:a":[1,2]}', 'una chiave estranea non scrive');
console.log('ok');
""")
    assert "ok" in out


def test_a_bridge_that_throws_degrades_to_an_alphabetical_drawer() -> None:
    """Mai un cassetto che non si apre: e' la regola di `UsageRanking`, e vale qui."""
    out = _run_js("""
const store = nativeStore(fakeNative('x', { throwOnGet: true, throwOnSet: true }));
assert.equal(store.getItem('launcher-usage'), null);
store.setItem('launcher-usage', '{"a":[1,2]}');   // non deve sollevare
console.log('ok');
""")
    assert "ok" in out


# ── I due moduli insieme ────────────────────────────────────────────────────


def test_usage_ranking_reads_and_writes_through_the_bridge() -> None:
    """La prova che i pezzi combaciano: `UsageRanking` non sa dove sta scrivendo.

    E' il contratto che tiene: se `nativeStore` non offrisse esattamente
    `getItem`/`setItem`, qui il conteggio tornerebbe a zero a ogni riavvio e
    nessun altro banco se ne accorgerebbe.
    """
    out = _run_js("""
const native = fakeNative('');
const store = nativeStore(native);

const usage = new UsageRanking(store);
usage.record('android:com.example.a', 1000);
usage.record('android:com.example.a', 2000);
usage.record('jenny:note', 1500);

// Un nuovo processo: stesso ponte, istanza nuova.
const rinato = new UsageRanking(nativeStore(native));
assert.equal(rinato.get('android:com.example.a').count, 2);
assert.equal(rinato.get('android:com.example.a').last, 2000);
assert.equal(rinato.get('jenny:note').count, 1);
console.log('ok');
""", with_rank=True)
    assert "ok" in out
