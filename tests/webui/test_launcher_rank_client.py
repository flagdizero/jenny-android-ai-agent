"""Il motore di ricerca e ordinamento del cassetto, eseguito davvero sotto node.

``shared/launcher-rank.js`` è puro di proposito — niente DOM, niente
``window``, niente i18n — proprio perché questa parte del cassetto si possa
provare senza un telefono. Stesso idioma di ``test_wiki_search_client.py``:
il modulo si importa in node, e le asserzioni girano contro il codice vero.

La casella che dà il nome al file è **3.6**: due voci con lo stesso nome in
spazi di nomi diversi non si sovrascrivono nel ranking. È la classe di difetto
più silenziosa di tutta la funzionalità — una skill "notes" e una Jenny App
"notes" che si contano a vicenda non rompono niente di visibile: l'ordine è
semplicemente sbagliato, e nessuno saprebbe dire rispetto a cosa.
"""

from __future__ import annotations

import json
from pathlib import Path

from support.js_harness import requires_node, run_js

RANK_JS = (
    Path(__file__).resolve().parents[2]
    / "jenny" / "templates" / "ui" / "assets" / "shared" / "launcher-rank.js"
)


pytestmark = requires_node

# Storage finto: `UsageRanking` prende lo storage dal costruttore proprio per
# questo — sotto node `localStorage` non esiste, e stubbarlo globalmente
# nasconderebbe il fatto che il modulo non lo tocca da sé.
_FAKE_STORAGE = """
function fakeStorage(initial) {
  const data = new Map(Object.entries(initial || {}));
  return {
    getItem: (k) => (data.has(k) ? data.get(k) : null),
    setItem: (k, v) => { data.set(k, String(v)); },
    dump: () => Object.fromEntries(data),
  };
}
"""


def _run_js(script: str) -> str:
    source = (
        RANK_JS.read_text(encoding="utf-8")
        + "\nimport assert from 'node:assert/strict';\n"
        + _FAKE_STORAGE
        + script
    )
    return run_js(source)


def _entries_js(entries: list[dict]) -> str:
    return json.dumps(entries)


_THREE_NAMESPACES = [
    {"key": "skill:notes", "name": "notes", "description": "prende appunti"},
    {"key": "jenny:notes", "name": "notes", "description": "bacheca di note"},
    {"key": "android:com.example.notes", "name": "Notes", "description": "com.example.notes"},
]


# ── 3.6 — gli spazi di nomi non si sovrascrivono ────────────────────────────

def test_same_name_in_two_namespaces_keeps_two_separate_counters() -> None:
    """Aprire la skill "notes" non deve far salire la Jenny App "notes"."""
    out = _run_js(
        """
const usage = new UsageRanking(fakeStorage());
usage.record('skill:notes', 1000);
usage.record('skill:notes', 2000);
assert.equal(usage.get('skill:notes').count, 2);
assert.equal(usage.get('jenny:notes').count, 0, 'la Jenny App omonima è stata contaminata');
assert.equal(usage.get('android:com.example.notes').count, 0);
assert.equal(usage.size, 1, 'una sola chiave ricordata');
console.log(JSON.stringify({ok: true}));
"""
    )
    assert json.loads(out)["ok"] is True


def test_ranking_puts_the_used_namespace_first_and_leaves_the_twin_behind() -> None:
    """Il conteggio separato deve *vedersi* nell'ordine, non solo nella mappa:
    è lì che una sovrascrittura si manifesterebbe."""
    out = _run_js(
        f"""
const entries = {_entries_js(_THREE_NAMESPACES)};
const usage = new UsageRanking(fakeStorage());
usage.record('jenny:notes', 5000);
const order = rankEntries(entries, '', usage, 'it').map(e => e.key);
assert.equal(order[0], 'jenny:notes', 'la voce aperta non è in cima: ' + order);
assert.deepEqual(order.slice(1).sort(), ['android:com.example.notes', 'skill:notes']);
console.log(JSON.stringify(order));
"""
    )
    assert json.loads(out)[0] == "jenny:notes"


def test_persisted_shape_keys_by_namespace_not_by_name() -> None:
    """Ciò che finisce in `localStorage` deve portare il prefisso: se il ranking
    si salvasse per nome, la separazione si perderebbe al riavvio anche con la
    mappa in memoria corretta — e sarebbe visibile solo dopo un force-stop."""
    out = _run_js(
        """
const storage = fakeStorage();
const usage = new UsageRanking(storage);
usage.record('skill:notes', 1000);
usage.record('android:com.example.notes', 2000);
const raw = JSON.parse(storage.dump()['launcher-usage']);
assert.deepEqual(Object.keys(raw).sort(), ['android:com.example.notes', 'skill:notes']);
// Riletto da zero: il conteggio sopravvive al giro su disco separato per spazio.
const reborn = new UsageRanking(storage);
assert.equal(reborn.get('skill:notes').count, 1);
assert.equal(reborn.get('jenny:notes').count, 0);
console.log(JSON.stringify(raw));
"""
    )
    assert set(json.loads(out)) == {"skill:notes", "android:com.example.notes"}


# ── 3.1 — ricerca su nome *e* descrizione, sui tre spazi insieme ────────────

def test_search_matches_name_and_description_across_namespaces() -> None:
    out = _run_js(
        f"""
const entries = {_entries_js([
    {"key": "skill:cron", "name": "cron", "description": "pianifica lavori ricorrenti"},
    {"key": "jenny:spesa", "name": "Lista spesa", "description": "appunti del supermercato"},
    {"key": "android:com.android.chrome", "name": "Chrome", "description": "com.android.chrome"},
])};
const usage = new UsageRanking(null);
// Solo dalla descrizione, e in uno spazio di nomi diverso da quello del nome.
assert.deepEqual(rankEntries(entries, 'supermercato', usage, 'it').map(e => e.key),
                 ['jenny:spesa']);
// Dal nome del pacchetto, che per una app Android è il testo secondario.
assert.deepEqual(rankEntries(entries, 'com.android', usage, 'it').map(e => e.key),
                 ['android:com.android.chrome']);
// Un termine che non c'è da nessuna parte non porta via mezzo elenco.
assert.deepEqual(rankEntries(entries, 'zzz', usage, 'it'), []);
// Due termini sono in AND, e possono cadere in campi diversi.
assert.deepEqual(rankEntries(entries, 'lista supermercato', usage, 'it').map(e => e.key),
                 ['jenny:spesa']);
assert.deepEqual(rankEntries(entries, 'lista cron', usage, 'it'), []);
console.log(JSON.stringify({{ok: true}}));
"""
    )
    assert json.loads(out)["ok"] is True


def test_search_ignores_accents_and_case() -> None:
    """Sul Titan 2 si scrive da tastiera fisica, dove l'accento costa una combo:
    "però" e "pero" devono cercare la stessa cosa."""
    out = _run_js(
        f"""
const entries = {_entries_js([
    {"key": "jenny:caffe", "name": "Caffè", "description": "conta le tazzine"},
])};
const usage = new UsageRanking(null);
for (const q of ['caffe', 'CAFFÈ', 'Caffe', 'caffè']) {{
  assert.equal(rankEntries(entries, q, usage, 'it').length, 1, 'query fallita: ' + q);
}}
console.log(JSON.stringify({{ok: true}}));
"""
    )
    assert json.loads(out)["ok"] is True


# ── 3.3 — pertinenza, poi frequenza, poi recenza ────────────────────────────

def test_name_beats_description_and_prefix_beats_substring() -> None:
    """L'ordine di pertinenza è la ragione per cui cercare batte scorrere: chi
    scrive "tel" vuole *Telefono*, non l'app la cui descrizione dice
    "controlla il tuo telefono"."""
    out = _run_js(
        f"""
const entries = {_entries_js([
    {"key": "android:a", "name": "Telefono", "description": "com.android.dialer"},
    {"key": "android:b", "name": "Impostazioni", "description": "controlla il telefono"},
    {"key": "android:c", "name": "Voicetel", "description": "com.x.voicetel"},
])};
const usage = new UsageRanking(null);
assert.deepEqual(rankEntries(entries, 'tel', usage, 'it').map(e => e.key),
                 ['android:a', 'android:c', 'android:b']);
console.log(JSON.stringify({{ok: true}}));
"""
    )
    assert json.loads(out)["ok"] is True


def test_relevance_outranks_usage_but_usage_breaks_the_tie() -> None:
    """Un pari merito di pertinenza lo decide la frequenza, e un pari merito di
    frequenza la recenza. Una voce molto usata **non** deve però scavalcare un
    riscontro migliore: sarebbe un cassetto che ignora quel che si è scritto."""
    out = _run_js(
        f"""
const entries = {_entries_js([
    {"key": "jenny:alfa", "name": "Alfa note", "description": ""},
    {"key": "jenny:beta", "name": "Beta note", "description": ""},
    {"key": "jenny:noteria", "name": "Noteria", "description": ""},
])};
const storage = fakeStorage();
const usage = new UsageRanking(storage);
usage.record('jenny:beta', 9000);   // usata, ma con un riscontro peggiore
usage.record('jenny:beta', 9500);
// "note" attacca il nome di Noteria e sta a metà parola negli altri due:
// la pertinenza vince sulla frequenza.
assert.deepEqual(rankEntries(entries, 'note', usage, 'it').map(e => e.key),
                 ['jenny:noteria', 'jenny:beta', 'jenny:alfa']);
// A pari pertinenza (campo vuoto) comanda la frequenza, poi l'alfabetico.
assert.deepEqual(rankEntries(entries, '', usage, 'it').map(e => e.key),
                 ['jenny:beta', 'jenny:alfa', 'jenny:noteria']);
console.log(JSON.stringify({{ok: true}}));
"""
    )
    assert json.loads(out)["ok"] is True


def test_recency_breaks_a_frequency_tie() -> None:
    out = _run_js(
        f"""
const entries = {_entries_js([
    {"key": "jenny:vecchia", "name": "Vecchia", "description": ""},
    {"key": "jenny:nuova", "name": "Nuova", "description": ""},
])};
const usage = new UsageRanking(fakeStorage());
usage.record('jenny:vecchia', 1000);
usage.record('jenny:nuova', 9000);
assert.deepEqual(rankEntries(entries, '', usage, 'it').map(e => e.key),
                 ['jenny:nuova', 'jenny:vecchia']);
console.log(JSON.stringify({{ok: true}}));
"""
    )
    assert json.loads(out)["ok"] is True


def test_never_used_entries_stay_in_the_list_in_alphabetical_order() -> None:
    """A campo vuoto e senza storia il cassetto è ancora un cassetto: le voci
    ci sono tutte, in un ordine che non cambia da un'apertura all'altra."""
    out = _run_js(
        f"""
const entries = {_entries_js([
    {"key": "jenny:c", "name": "Zebra", "description": ""},
    {"key": "jenny:a", "name": "ananas", "description": ""},
    {"key": "jenny:b", "name": "Órso", "description": ""},
])};
const usage = new UsageRanking(null);
const first = rankEntries(entries, '', usage, 'it').map(e => e.name);
assert.deepEqual(first, ['ananas', 'Órso', 'Zebra']);
assert.deepEqual(rankEntries(entries, '', usage, 'it').map(e => e.name), first);
console.log(JSON.stringify(first));
"""
    )
    assert json.loads(out) == ["ananas", "Órso", "Zebra"]


# ── D9 — lo storage è un dettaglio che può mancare ──────────────────────────

def test_a_broken_or_missing_storage_degrades_to_alphabetical() -> None:
    """Il ranking non è un dato prezioso (D9): senza storage, o con dentro
    spazzatura, il cassetto deve restare un cassetto — mai non aprirsi."""
    out = _run_js(
        f"""
const entries = {_entries_js([
    {"key": "jenny:b", "name": "Beta", "description": ""},
    {"key": "jenny:a", "name": "Alfa", "description": ""},
])};
const exploding = {{
  getItem() {{ throw new Error('SecurityError'); }},
  setItem() {{ throw new Error('QuotaExceeded'); }},
}};
for (const storage of [null, exploding, fakeStorage({{'launcher-usage': 'non-json'}}),
                       fakeStorage({{'launcher-usage': '{{"jenny:a": "boh"}}'}})]) {{
  const usage = new UsageRanking(storage);
  usage.record('jenny:b', 1);   // non deve propagare l'eccezione
  const order = rankEntries(entries, '', usage, 'it').map(e => e.key);
  assert.equal(order.length, 2, 'voci perse con storage ' + String(storage));
}}
console.log(JSON.stringify({{ok: true}}));
"""
    )
    assert json.loads(out)["ok"] is True


def test_the_remembered_keys_are_capped_by_recency() -> None:
    """Un telefono con trecento app e un utente curioso non deve gonfiare la
    riga di localStorage all'infinito: si tengono le più recenti."""
    out = _run_js(
        """
const storage = fakeStorage();
const usage = new UsageRanking(storage, { limit: 3 });
for (let i = 0; i < 10; i++) usage.record('android:pkg' + i, 1000 + i);
const raw = JSON.parse(storage.dump()['launcher-usage']);
assert.deepEqual(Object.keys(raw).sort(), ['android:pkg7', 'android:pkg8', 'android:pkg9']);
console.log(JSON.stringify(Object.keys(raw)));
"""
    )
    assert len(json.loads(out)) == 3
