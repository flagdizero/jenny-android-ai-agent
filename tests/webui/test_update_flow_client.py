"""L'aggiornamento dell'app: la macchina a stati, fatta girare davvero.

Questa macchina e' vissuta dentro `mobile-settings.js` **senza un banco che la
esercitasse**: c'erano le rotte lato Python (`test_settings_update_api.py`) e
due controlli sul sorgente, ma le fasi, il polling e i due casi che ingannano
non li misurava niente. Estrarla in `shared/update-flow.js` — perche' la casa
ne e' diventata la seconda vista — e' anche cio' che la rende misurabile, ed e'
meta' della ragione per cui si estrae invece di ricopiare.

I due casi che ingannano, e sono i due che valgono il banco:

**La connessione che cade *perche'* l'app si sta riavviando.** Nel percorso
silenzioso il sistema uccide il processo mentre la risposta e' ancora in volo.
Chiamarlo errore sarebbe una bugia proprio nel caso normale — ma solo se il
polling ha gia' visto muoversi qualcosa: se la fase e' ancora «inattiva», un
fallimento di partenza e' un fallimento di partenza.

**Il rifiuto «niente da installare»**, che lato server non sporca la fase: il
motivo sta tutto nel `detail` della risposta, e un giro di polling lo
cancellerebbe rileggendo una fase ancora «inattiva». Per questo il polling si
ferma **prima** che lo stato venga scritto — un ordine fra due righe, cioe'
esattamente il genere di cosa che si rompe in silenzio.

I timer sono finti e si fanno scattare a mano: il polling vero e' ogni 1,5 s e
il tetto e' 400 giri, che a tempo reale sarebbero dieci minuti di banco.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from support.js_harness import function, member, requires_node, run_js

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"
FLOW_JS = ASSETS / "shared" / "update-flow.js"
WHEN_JS = ASSETS / "shared" / "when.js"
I18N_JS = ASSETS / "shared" / "i18n.js"
I18N_DIR = ASSETS / "i18n"


pytestmark = requires_node


def _const(source: str, name: str) -> str:
    m = re.search(rf"(?m)^export const {re.escape(name)} = (.+);$", source)
    assert m, f"const {name} non trovata"
    return f"const {name} = {m.group(1)};"


_HARNESS = """
import assert from 'node:assert/strict';

const TRANSLATIONS = __TRANSLATIONS__;
const i18n = { locale: 'it', translations: TRANSLATIONS, __T__ };

/* Timer finti: il polling vero e' ogni 1,5 s con un tetto di 400 giri, e a
   tempo reale questo banco durerebbe dieci minuti. Si tiene la coda e la si
   fa scattare a mano, che e' anche l'unico modo di misurare **quando** il
   polling viene fermato. */
let nextId = 1;
const queued = new Map();
const setTimeout = (fn, ms) => { const id = nextId++; queued.set(id, { fn, ms }); return id; };
const clearTimeout = (id) => { queued.delete(id); };
function pending() { return queued.size; }
async function heartbeat() {
  const ready = [...queued.values()];
  queued.clear();
  for (const t of ready) await t.fn();
  await new Promise((r) => setImmediate(r));
}

/* Le tre rotte. Ogni voce e' o una risposta, o un `Error` da sollevare. */
const replies = { check: null, install: null, status: null };
const calls = [];
const api = {
  _fetch(url) {
    const broken = url.split('/').pop();
    calls.push(broken);
    const r = replies[broken];
    if (r instanceof Error) return Promise.reject(r);
    if (r && r.http && !r.ok) return Promise.resolve({ ok: false, status: r.status || 500 });
    return Promise.resolve({ ok: true, json: () => Promise.resolve(r) });
  },
};

const toasts = [];
const versions = [];
let generation = 0;

__STALE_MS__
__POLL_MS__
__POLL_MAX__
__PHASE_KEY__
__WHEN_TEXT__
__CHECK_LINES__

class UpdateFlow {
  __CTOR__
  __BUSY__
  __IDLE_TIMER__
  __STALE__
  __CHANGED__
  __CHECK__
  __START__
  __STOP__
  __RESUME__
  __SETTLE__
  __FAIL__
  __SCHEDULE__
  __POLL__
}

const steps = [];
function flow() {
  replies.check = null;
  replies.install = null;
  replies.status = null;
  calls.length = 0;
  toasts.length = 0;
  versions.length = 0;
  steps.length = 0;
  queued.clear();
  generation = 0;
  return new UpdateFlow({
    generation: () => generation,
    onToast: (text, type) => toasts.push([text, type]),
    onVersion: (v) => versions.push(v),
    onChange: (s) => steps.push(s && { ...s }),
  });
}
"""


def _harness() -> str:
    src = FLOW_JS.read_text(encoding="utf-8")
    it = json.loads((I18N_DIR / "it.json").read_text(encoding="utf-8"))
    return (
        _HARNESS.replace("__TRANSLATIONS__", json.dumps({"it": it}, ensure_ascii=False))
        .replace("__T__", member(I18N_JS.read_text(encoding="utf-8"), "t"))
        .replace("__STALE_MS__", _const(src, "STALE_MS"))
        .replace("__POLL_MS__", _const(src, "POLL_MS"))
        .replace("__POLL_MAX__", _const(src, "POLL_MAX"))
        .replace("__PHASE_KEY__", function(src, "phaseKey"))
        .replace("__WHEN_TEXT__", function(WHEN_JS.read_text(encoding="utf-8"), "whenText"))
        .replace("__CHECK_LINES__", function(src, "checkLines"))
        .replace("__CTOR__", member(src, "constructor"))
        .replace("__BUSY__", member(src, "busy"))
        .replace("__IDLE_TIMER__", member(src, "idleTimer"))
        .replace("__STALE__", member(src, "_stale"))
        .replace("__CHANGED__", member(src, "_changed"))
        .replace("__CHECK__", member(src, "check"))
        .replace("__START__", member(src, "start"))
        .replace("__STOP__", member(src, "stop"))
        .replace("__RESUME__", member(src, "resume"))
        .replace("__SETTLE__", member(src, "_settleAtPrompt"))
        .replace("__FAIL__", member(src, "_fail"))
        .replace("__SCHEDULE__", member(src, "_schedulePoll"))
        .replace("__POLL__", member(src, "_poll"))
    )


def _run_js(script: str) -> None:
    run_js(_harness() + "\n" + script)


# ── I due casi che ingannano ────────────────────────────────────────────────


def test_a_connection_lost_while_installing_is_the_restart_and_not_a_failure() -> None:
    """Nel percorso silenzioso il sistema uccide il processo mentre la risposta
    e' ancora in volo: e' il caso **normale**, e chiamarlo errore sarebbe una
    bugia proprio li'."""
    _run_js("""
      const f = flow();
      /* La richiesta d'installazione non risponde: il processo muore mentre
         la risposta e' in volo. Si tiene sospesa e si fa cadere **dopo** che
         il polling ha visto muoversi qualcosa, che e' l'ordine vero. */
      let failItNow;
      const real = api._fetch;
      api._fetch = (url) => (url.endsWith('install')
        ? new Promise((_, no) => { failItNow = () => no(new Error('socket chiuso')); })
        : real(url));
      replies.status = { phase: 'downloading', progress: 20, detail: '10 MB' };

      const startup = f.start();
      /* Il polling e' partito **prima** della risposta: e' l'unico modo di
         sapere che l'installazione era gia' in moto. */
      assert.equal(pending(), 1, 'il polling non parte prima della risposta');
      await heartbeat();
      failItNow();
      await startup;
      api._fetch = real;

      assert.equal(f.state.phase, 'downloading');
      assert.equal(f.state.noteKey, 'settings.update.restarting');
      assert.deepEqual(toasts, [], 'ha annunciato un guasto che non c e');
    """)


def test_a_connection_lost_before_anything_moved_is_a_real_failure() -> None:
    """L'altra meta' della stessa regola: se il polling non ha ancora visto
    muoversi niente, la richiesta non e' mai partita davvero."""
    _run_js("""
      const f = flow();
      replies.install = new Error('connessione rifiutata');
      await f.start();

      assert.equal(f.state.phase, 'error');
      assert.equal(f.state.busy, false);
      assert.equal(toasts.length, 1);
      assert.equal(toasts[0][1], 'error');
      assert.equal(pending(), 0, 'il polling e rimasto vivo dopo un errore');
    """)


def test_a_refusal_keeps_its_reason_because_polling_stops_first() -> None:
    """«Niente da installare» non sporca la fase lato server: il motivo sta
    tutto nel `detail` della risposta. Se il polling restasse acceso, il giro
    successivo lo cancellerebbe rileggendo una fase ancora «inattiva» — ed e'
    un ordine fra due righe, cioe' il genere di cosa che si rompe in
    silenzio."""
    _run_js("""
      const f = flow();
      replies.install = { ok: false, detail: 'niente da installare' };
      replies.status = { phase: 'idle', progress: 0, detail: '' };
      await f.start();

      assert.equal(f.state.phase, 'error');
      assert.equal(f.state.detail, 'niente da installare');
      assert.equal(pending(), 0, 'il polling e sopravvissuto al rifiuto');

      /* E se anche un giro fosse rimasto in volo, non deve poter cancellare
         il motivo: lo si fa scattare a mano. */
      await heartbeat();
      assert.equal(f.state.detail, 'niente da installare',
        'un giro di polling ha cancellato il motivo del rifiuto');
    """)


# ── `prompt` e' terminale ───────────────────────────────────────────────────


def test_the_prompt_settles_from_the_reply() -> None:
    """Su Android 14+ la conferma di sistema e' *la* strada normale. Trattarla
    come «lavoro in corso» vuol dire dieci minuti di polling col bottone
    disabilitato, e un'uscita-e-rientro che ne fa ripartire altri dieci."""
    _run_js("""
      const f = flow();
      replies.install = { ok: true, state: 'prompt', detail: 'conferma richiesta' };
      await f.start();

      assert.equal(f.state.phase, 'prompt');
      assert.equal(f.state.busy, false, 'il bottone resta disabilitato');
      assert.equal(f.state.noteKey, 'settings.update.promptNote');
      assert.equal(pending(), 0, 'continua a interrogare una fase che non si muove');
    """)


def test_the_prompt_settles_from_the_polling_too() -> None:
    """La stessa fase puo' arrivare dall'altra strada: la risposta
    dell'installazione dice «silent», e a scoprirla e' il polling."""
    _run_js("""
      const f = flow();
      replies.install = { ok: true, state: 'silent' };
      replies.status = { phase: 'prompt', progress: 0, detail: 'conferma richiesta' };
      await f.start();
      assert.equal(f.state.noteKey, 'settings.update.restarting');

      await heartbeat();
      assert.equal(f.state.phase, 'prompt');
      assert.equal(f.state.busy, false);
      assert.equal(pending(), 0);
    """)


# ── Il polling ──────────────────────────────────────────────────────────────


def test_a_poll_that_fails_does_not_change_what_the_user_reads() -> None:
    """Durante l'installazione il gateway **sparisce**, ed e' il caso normale
    e non l'eccezione: un polling che fallisce non e' un'installazione
    fallita."""
    _run_js("""
      const f = flow();
      replies.install = { ok: true, state: 'silent' };
      replies.status = { phase: 'installing', progress: 60, detail: 'scrittura' };
      await f.start();
      await heartbeat();
      const visto = { ...f.state };
      assert.equal(visto.phase, 'installing');

      replies.status = new Error('gateway sparito');
      await heartbeat();
      assert.deepEqual({ ...f.state }, visto, 'un polling caduto ha riscritto lo stato');
      assert.equal(pending(), 1, 'ha smesso di riprovare');
    """)


def test_done_promises_the_restart_and_stops() -> None:
    """«done» lato server vuol dire «sessione committata», non «installato»:
    subito dopo Android sostituisce l'app e il processo muore."""
    _run_js("""
      const f = flow();
      replies.install = { ok: true, state: 'silent' };
      replies.status = { phase: 'done', progress: 100, detail: '' };
      await f.start();
      await heartbeat();

      assert.equal(f.state.phase, 'done');
      assert.equal(f.state.busy, false);
      assert.equal(f.state.noteKey, 'settings.update.restarting');
      assert.equal(pending(), 0);

      /* E la promessa vale **anche se la risposta della richiesta non arriva
         mai**: nel percorso silenzioso il processo muore prima di rispondere,
         quindi la nota se l'e' messa il polling e nessun altro. Senza, resta
         a schermo «Avvio dell'installazione…» mentre l'app si sta gia'
         sostituendo. */
      const g = flow();
      api._fetch = (url) => (url.endsWith('install')
        ? new Promise(() => {})            // non risponde mai
        : Promise.resolve({ ok: true, json: () => Promise.resolve(replies.status) }));
      replies.status = { phase: 'done', progress: 100, detail: '' };
      g.start();
      await new Promise((r) => setImmediate(r));
      assert.equal(g.state.noteKey, 'settings.update.starting');
      await heartbeat();
      assert.equal(g.state.noteKey, 'settings.update.restarting',
        'la nota sul riavvio non arriva quando la risposta non arriva');
      assert.equal(g.state.busy, false);
    """)


def test_the_polling_has_a_ceiling() -> None:
    """Senza il tetto, una fase che non si muove piu' lascerebbe un timer vivo
    per tutta la vita della pagina."""
    _run_js("""
      const f = flow();
      replies.install = { ok: true, state: 'silent' };
      replies.status = { phase: 'downloading', progress: 1, detail: '' };
      await f.start();
      for (let i = 0; i < POLL_MAX + 2; i++) await heartbeat();
      assert.equal(pending(), 0, 'il polling gira ancora oltre il tetto');
    """)


def test_leaving_the_screen_stops_everything() -> None:
    """La generazione della vista: ogni continuazione la cattura prima del
    primo `await` ed esce se e' cambiata — altrimenti scrive nello stato di una
    schermata che non c'e' piu'."""
    _run_js("""
      const f = flow();
      replies.install = { ok: true, state: 'silent' };
      replies.status = { phase: 'downloading', progress: 30, detail: '' };
      await f.start();
      const before = { ...f.state };

      generation += 1;          // si e' cambiata schermata
      await heartbeat();
      assert.deepEqual({ ...f.state }, before, 'ha scritto in una schermata lasciata');
      assert.equal(pending(), 0);

      /* E chi rientra riaggancia: l'installazione va avanti per conto suo. */
      f.resume();
      assert.equal(pending(), 1, 'rientrando, il polling non riparte');
    """)


def test_resume_does_not_double_the_polling() -> None:
    """Riagganciare un polling gia' vivo vorrebbe dire due giri per ogni
    battito, e il doppio delle richieste sul telefono che sta installando."""
    _run_js("""
      const f = flow();
      replies.install = { ok: true, state: 'silent' };
      replies.status = { phase: 'downloading', progress: 30, detail: '' };
      await f.start();
      assert.equal(pending(), 1);
      f.resume();
      f.resume();
      assert.equal(pending(), 1, 'il polling si e sdoppiato');
    """)


# ── Il controllo manuale ────────────────────────────────────────────────────


def test_a_second_tap_while_checking_does_nothing() -> None:
    """Il doppio tocco e' fermato due volte: qui dal flag, e lato server da un
    lock, perche' la rotta fa rete."""
    _run_js("""
      const f = flow();
      replies.check = { status: 'ok', version: { current: '0.11.0', update_available: false } };
      const one = f.check();
      const due = f.check();
      await Promise.all([one, due]);
      assert.deepEqual(calls, ['check'], 'due controlli in volo insieme');
    """)


def test_a_fresh_version_comes_out_before_the_toast() -> None:
    """Senza, una versione appena trovata comparirebbe solo alla prossima
    apertura — cioe' proprio dopo il gesto con cui l'utente l'ha chiesta."""
    _run_js("""
      const f = flow();
      replies.check = { status: 'ok', version: { current: '0.11.0', latest: '0.12.0', update_available: true } };
      await f.check();

      assert.equal(versions.length, 1, 'la versione fresca non esce');
      assert.equal(versions[0].latest, '0.12.0');
      assert.equal(toasts[0][0], i18n.t('settings.update.available', { version: '0.12.0' }));
      assert.equal(f.checking, false);
    """)


def test_a_check_that_could_not_run_says_so_and_unlocks() -> None:
    """Il flag si azzera anche se nel frattempo si e' usciti dalla sezione: e'
    roba del flusso, non del DOM, e lasciarlo acceso bloccherebbe il bottone al
    rientro senza che nulla lo rimetta a posto."""
    _run_js("""
      const f = flow();
      replies.check = new Error('rete assente');
      await f.check();
      assert.equal(f.checking, false, 'il bottone resta bloccato');
      assert.deepEqual(toasts, [[i18n.t('settings.update.checkFailed'), 'error']]);
      assert.deepEqual(versions, [], 'ha annunciato una versione che non ha letto');

      /* E un controllo gia' in corso lato server non e' un guasto: si dice, e
         non si spegne niente. */
      toasts.length = 0;
      replies.check = { status: 'busy' };
      await f.check();
      assert.equal(toasts[0][1], undefined, 'un «occupato» annunciato come errore');
    """)


# ── Le frasi del meccanismo ─────────────────────────────────────────────────


def test_a_fresh_install_is_not_an_alarm() -> None:
    """Prima del primo tentativo in assoluto non c'e' nessun guasto: c'e'
    un'installazione appena fatta, e darle l'aria dell'allarme sarebbe la prima
    cosa falsa che Jenny dice."""
    _run_js("""
      const rows = checkLines({});
      assert.equal(rows.length, 1);
      assert.equal(rows[0].key, 'settings.update.neverChecked');
      assert.equal(rows[0].warn, false, 'la prima accensione e un allarme');
    """)


def test_tries_without_a_single_success_are_a_warning() -> None:
    _run_js("""
      const rows = checkLines({ last_check: Date.now(), last_success: 0 });
      assert.deepEqual(rows.map((r) => [r.key, r.warn]),
                       [['settings.update.staleNever', true]]);
    """)


def test_a_phone_that_was_off_for_a_week_is_not_a_broken_mechanism() -> None:
    """Il confronto e' fra i **due marcatempo dell'updater**, non con l'ora
    corrente: `last_check` e' scritto a ogni tentativo, `last_success` solo
    quando il manifest e' stato letto davvero, e un telefono spento per una
    settimana li ha vecchi entrambi."""
    _run_js("""
      const off = Date.now() - 20 * 86400000;
      const rows = checkLines({ last_check: off, last_success: off });
      assert.deepEqual(rows.map((r) => r.warn), [false],
        'un telefono spento viene segnalato come meccanismo rotto');

      /* Tentativi che continuano e non arrivano piu': quello si', e sopra la
         soglia di una settimana. */
      const broken = checkLines({ last_check: Date.now(), last_success: off });
      assert.deepEqual(broken.map((r) => [r.key, r.warn]),
        [['settings.update.lastSuccess', false], ['settings.update.stale', true]]);

      /* Sotto soglia si tace: una notte senza rete non e' un guasto. */
      const yesterday = checkLines({ last_check: Date.now(), last_success: Date.now() - 2 * 86400000 });
      assert.deepEqual(yesterday.map((r) => r.warn), [false]);
    """)


def test_when_it_happened_reads_like_a_person_would_say_it() -> None:
    """Relativo finche' resta leggibile, datato dopo: a novanta giorni «90
    giorni fa» non dice piu' niente, una data si'. «Ieri» ha un ramo suo
    perche' «1 giorni fa» si legge male in tutte e due le lingue."""
    _run_js("""
      const day = 86400000;
      assert.ok(whenText(Date.now()).startsWith('oggi alle '), whenText(Date.now()));

      const midnight = new Date(); midnight.setHours(0, 0, 0, 0);
      const yesterday = midnight.getTime() - 3600000;   // un'ora prima di mezzanotte
      assert.ok(whenText(yesterday).startsWith('ieri alle '), whenText(yesterday));

      assert.equal(whenText(midnight.getTime() - 3 * day), '3 giorni fa');
      /* Arrotondato per eccesso: un controllo di tre giorni fa alle 23:00 dista
         due giorni e un'ora dalla mezzanotte di oggi, e troncando diventerebbe
         «2 giorni fa» — cioe' un giorno piu' recente di quel che e'. */
      assert.equal(whenText(midnight.getTime() - 2 * day - 3600000), '3 giorni fa');

      const old = whenText(midnight.getTime() - 90 * day);
      assert.ok(!old.includes('giorni fa'), 'a novanta giorni dice ancora «giorni fa»: ' + old);
      assert.ok(/\\d{4}/.test(old), 'e senza l anno: ' + old);
    """)


def test_idle_has_no_phrase_of_its_own() -> None:
    """Prima di premere il bottone non c'e' niente da raccontare, e dopo un
    errore la fase torna a essere l'ultima cosa detta, non «inattivo»."""
    _run_js("""
      assert.equal(phaseKey('idle'), '');
      assert.equal(phaseKey(undefined), '');
      for (const phase of ['downloading', 'installing', 'prompt', 'error', 'done']) {
        const key = phaseKey(phase);
        assert.ok(key, 'la fase ' + phase + ' non ha parola');
        assert.notEqual(i18n.t(key), key, 'la fase ' + phase + ' non e tradotta');
      }
    """)
