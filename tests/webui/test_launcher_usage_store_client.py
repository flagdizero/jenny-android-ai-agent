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

from pathlib import Path

from support.js_harness import requires_node, run_js

STORE_JS = (
    Path(__file__).resolve().parents[2]
    / "jenny" / "templates" / "ui" / "assets" / "shared" / "launcher-usage-store.js"
)
RANK_JS = STORE_JS.parent / "launcher-rank.js"


pytestmark = requires_node

# Un `localStorage` finto (con `removeItem`, che la migrazione usa) e un ponte
# nativo finto che si puo' rompere a comando — i due guasti che contano sono
# «il ponte solleva» e «il ponte accetta e poi non rilegge».
_FAKES = """
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
    source += "\nimport assert from 'node:assert/strict';\n" + _FAKES + script
    return run_js(source)


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
const middle = { getLauncherUsage: () => '' };   // manca il setter
assert.equal(nativeUsable(middle), false);
assert.equal(usageStore({ native: middle, local }), local);
console.log('ok');
""")
    assert "ok" in out


# ── La migrazione ───────────────────────────────────────────────────────────


def test_the_old_value_moves_over_once_and_the_copy_is_removed() -> None:
    """Prima la verita' nuova, poi si toglie la vecchia. E il posto resta uno."""
    out = _run_js("""
const local = fakeLocal({ 'launcher-usage': '{"android:a":[3,10]}' });
const native = fakeNative('');
assert.equal(await migrateUsage(native, local), 'migrated');
assert.equal(native.peek(), '{"android:a":[3,10]}', 'il ponte deve avere il valore');
assert.equal(local.getItem('launcher-usage'), null, 'la copia vecchia va tolta');
// Un secondo giro non deve rifare niente.
assert.equal(await migrateUsage(native, local), 'native-has-data');
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
  assert.equal(await migrateUsage(native, local), 'failed', JSON.stringify(opts));
  assert.equal(
    local.getItem('launcher-usage'), '{"android:a":[3,10]}',
    'il valore vecchio deve sopravvivere a ' + JSON.stringify(opts),
  );
  // E il cassetto deve continuare a funzionare, sul posto vecchio.
  const store = usageStore({ native, local });
  await store.ready;
  assert.equal(store.getItem('launcher-usage'), '{"android:a":[3,10]}');
  store.setItem('launcher-usage', '{"android:b":[1,1]}');
  assert.equal(local.getItem('launcher-usage'), '{"android:b":[1,1]}', 'scrive sul posto vecchio');
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
const local = fakeLocal({ 'launcher-usage': '{"android:old":[99,1]}' });
const native = fakeNative('{"android:fresh":[2,500]}');
assert.equal(await migrateUsage(native, local), 'native-has-data');
assert.equal(native.peek(), '{"android:fresh":[2,500]}');
console.log('ok');
""")
    assert "ok" in out


def test_nothing_to_move_is_not_a_failure() -> None:
    """Prima installazione: niente di qua, niente di la'. Si usa il ponte."""
    out = _run_js("""
const local = fakeLocal({});
const native = fakeNative('');
assert.equal(await migrateUsage(native, local), 'nothing-to-move');
const store = usageStore({ native, local });
assert.notEqual(store, local, 'deve comunque scegliere il ponte');
await store.ready;
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
await store.ready;
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
await store.ready;
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
await store.ready;
await null;   // `_adoptLoaded` gira nel microtask dopo `ready`
usage.record('android:com.example.a', 1000);
usage.record('android:com.example.a', 2000);
usage.record('jenny:note', 1500);

// Un nuovo processo: stesso ponte, istanza nuova.
const rebornStore = nativeStore(native);
const reborn = new UsageRanking(rebornStore);
await rebornStore.ready;
await null;
assert.equal(reborn.get('android:com.example.a').count, 2);
assert.equal(reborn.get('android:com.example.a').last, 2000);
assert.equal(reborn.get('jenny:note').count, 1);
console.log('ok');
""", with_rank=True)
    assert "ok" in out


def test_opens_before_the_bridge_answers_are_added_not_lost() -> None:
    """**La casella del ponte in differita.**

    Il valore vero arriva dopo la costruzione di `UsageRanking`. Un'app aperta
    in quella finestra non deve né sparire né schiacciare mesi d'uso: prima
    della risposta lo storage non scrive, e alla risposta i due si sommano.
    """
    out = _run_js("""
let release;
const gate = new Promise((r) => { release = r; });
let value = '{"android:a":[5,100],"android:b":[2,50]}';
const writes = [];
const slow = {
  getLauncherUsage: async () => { await gate; return value; },
  setLauncherUsage: (v) => { writes.push(v); value = String(v); },
};
const store = nativeStore(slow);
const usage = new UsageRanking(store);
assert.equal(usage.size, 0, 'prima della risposta non si sa niente');
usage.record('android:a', 200);
usage.record('android:c', 300);
assert.deepEqual(writes, [], 'prima della risposta non si scrive: si schiaccerebbe il vero');
release();
await store.ready;
await null;
assert.equal(usage.get('android:a').count, 6, '5 veri + 1 di questo avvio');
assert.equal(usage.get('android:a').last, 200);
assert.equal(usage.get('android:b').count, 2);
assert.equal(usage.get('android:c').count, 1);
assert.equal(writes.length, 1, 'una scrittura, con la somma');
assert.equal(JSON.parse(writes[0])['android:a'][0], 6);
console.log('ok');
""", with_rank=True)
    assert "ok" in out
