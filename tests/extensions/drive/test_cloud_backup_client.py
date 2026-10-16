"""``cloud-backup.js`` dell'estensione Drive: le parole dello stato e il giro del
telefono nuovo.

Il gateway e il nativo sono finti; qui si misura quel che è solo del client:
come uno stato diventa una frase (e quando diventa un allarme), e quando si
chiede la password della copia — solo a chi ha qualcosa da aprire o da
chiudere, e mai una seconda volta per la stessa cosa.
"""

from __future__ import annotations

import json
import re

from support.js_harness import function, member, requires_node, run_js

from .ui_paths import CORE_ASSETS, EXT_UI, locale

CLOUD_JS = (EXT_UI / "cloud-backup.js").read_text(encoding="utf-8")
DIALOGS_JS = (EXT_UI / "cloud-backup-dialogs.js").read_text(encoding="utf-8")
WHEN_JS = (CORE_ASSETS / "shared" / "when.js").read_text(encoding="utf-8")
I18N_JS = (CORE_ASSETS / "shared" / "i18n.js").read_text(encoding="utf-8")
IT = locale("it")

pytestmark = requires_node


def _harness() -> str:
    late = re.search(r"export const CLOUD_LATE_MS = ([^;]+);", CLOUD_JS).group(1)
    return (
        """
import assert from 'node:assert/strict';
const i18n = { locale: 'it', translations: { it: __IT__ }, __T__ };
__WHEN__
const CLOUD_LATE_MS = __LATE__;
const CLOUD_BLOCKING = __BLOCKING__;
const DAY_MS = 86400000;
__PURE__

/* Il gateway e il nativo, finti e scriptabili. */
const calls = [];
const answers = {};
const api = {
  cloudAction: async (name) => { calls.push(name); return answers[name]; },
  cloudWithPassword: async (name, pw) => { calls.push(`${name}:${pw}`); return answers[`${name}:${pw}`] || answers[name]; },
};
/* Il client del modulo, sopra il finto: un test puo' sostituirne un metodo. */
const cloudAction = (...a) => api.cloudAction(...a);
const cloudWithPassword = (...a) => api.cloudWithPassword(...a);
/* La domanda della password, finta: risponde con *typed* in ordine, e come il
   dialogo vero riprova da sola finché la risposta è una che si corregge. */
const RETRY = __RETRY__;
const asked = [];
function askWith(...typed) {
  return async (submit) => {
    asked.push('ask');
    for (const pw of typed) {
      if (pw === null) return null;
      const result = await submit(pw);
      if (result.ok || !RETRY.includes(result.error)) return result;
    }
    return null;
  };
}
const nativeAnswers = {};
function connectDrive() { calls.push('connectDrive'); return Promise.resolve(nativeAnswers.connectDrive); }
function _native(method) { calls.push(method); return Promise.resolve(nativeAnswers[method]); }
let _busy = false;
__EXCLUSIVE__
__ERROR_TEXT__
__LIST__
__FIND__
__UNLOCK__
__TURN_ON__
__CHOOSE__
""".replace("__IT__", json.dumps(IT, ensure_ascii=False))
        .replace("__T__", member(I18N_JS, "t"))
        .replace("__WHEN__", function(WHEN_JS, "whenText"))
        .replace("__LATE__", late)
        .replace("__EXCLUSIVE__", function(CLOUD_JS, "_exclusive"))
        .replace("__ERROR_TEXT__", function(CLOUD_JS, "cloudErrorText"))
        .replace("__BLOCKING__", re.search(r"export const CLOUD_BLOCKING = (\[[^\]]+\]);", CLOUD_JS).group(1))
        .replace("__PURE__", "\n".join(function(CLOUD_JS, n) for n in (
            "_startOfDay", "calendarDays", "reasonKey", "cloudView", "nightStrip", "dayText",
            "sizeText", "progressSizes")))
        .replace("__RETRY__", re.search(r"export const PASSWORD_RETRY = (\[[^\]]+\]);", CLOUD_JS).group(1))
        .replace("__LIST__", function(CLOUD_JS, "_listWithPassword"))
        .replace("__FIND__", function(CLOUD_JS, "findMyCopy"))
        .replace("__UNLOCK__", function(CLOUD_JS, "unlockHere"))
        .replace("__TURN_ON__", function(CLOUD_JS, "turnOnDrive"))
        .replace("__CHOOSE__", function(CLOUD_JS, "choosePassword"))
    )


def _run(script: str) -> None:
    run_js(_harness() + "\n" + script)


def test_which_state_wins() -> None:
    """La priorità della tabella: non disponibile → spenta → in corso → ferma →
    in ritardo → prima copia → al sicuro."""
    _run("""
      const now = Date.parse('2026-10-15T12:00:00');
      const H = 3600 * 1000;
      const on = (x = {}) => ({ available: true, provider: 'drive', enabled_at_ms: now - 30 * 24 * H, ...x });
      const kind = (s) => cloudView(s, now).kind;
      assert.equal(kind(null), 'unavailable');
      assert.equal(kind({ available: false, provider: 'drive' }), 'unavailable');
      assert.equal(kind({ available: true, provider: '' }), 'off');
      assert.equal(kind(on({ running: true, last_error: 'consent_needed', last_failure_ms: now })), 'running');
      assert.equal(kind(on({ last_success_ms: now - 2 * H })), 'safe');
      assert.equal(cloudView(on({ last_success_ms: now - 2 * H }), now).tone, 'ok');

      /* Il confine dei tre giorni, misurato in ore. */
      assert.equal(kind(on({ last_success_ms: now - 71 * H })), 'safe');
      const late = cloudView(on({ last_success_ms: now - 72 * H }), now);
      assert.equal(late.kind, 'late');
      assert.equal(late.reason, 'waiting', 'senza errori il motivo è l attesa');
      assert.equal(late.days, 3);

      /* Il permesso tolto vince sul ritardo, ed è rosso subito. */
      const blocked = cloudView(on({ last_success_ms: now - 80 * H, last_failure_ms: now - H,
                                     last_error: 'consent_needed' }), now);
      assert.deepEqual([blocked.kind, blocked.tone, blocked.action], ['blocked', 'err', 'giveBack']);
      assert.equal(cloudView(on({ last_success_ms: now - 2 * H, last_failure_ms: now - H,
                                  last_error: 'wrong_key' }), now).action, 'unlock');

      /* Senza password: gialla, prima del ritardo, mai sopra a «ferma». */
      const nopass = cloudView(on({ has_key: true, password_set_ms: 0, last_success_ms: now - 2 * H }), now);
      assert.deepEqual([nopass.kind, nopass.tone, nopass.action], ['nopass', 'warn', 'setPassword']);
      assert.equal(kind(on({ has_key: true, password_set_ms: 0, last_success_ms: now - 80 * H })), 'nopass');
      assert.equal(kind(on({ has_key: true, password_set_ms: 0, last_success_ms: now - 2 * H,
                             last_failure_ms: now - H, last_error: 'consent_needed' })), 'blocked');
      assert.equal(kind(on({ has_key: true, password_set_ms: 5, last_success_ms: now - 2 * H })), 'safe');

      /* Un errore più vecchio dell'ultima copia riuscita non conta più. */
      assert.equal(kind(on({ last_success_ms: now - H, last_failure_ms: now - 2 * H,
                             last_error: 'consent_needed' })), 'safe');

      /* Accesa da poco, nessuna copia: «quasi pronta», col motivo se ha già fallito. */
      const first = cloudView(on({ enabled_at_ms: now - H }), now);
      assert.deepEqual([first.kind, first.reason], ['first', '']);
      const firstFailed = cloudView(on({ enabled_at_ms: now - 2 * H, last_failure_ms: now - H,
                                         last_error: 'DriveError: HTTP 500' }), now);
      assert.deepEqual([firstFailed.kind, firstFailed.reason], ['first', 'other']);
      /* Accesa da quattro giorni senza mai una copia: in ritardo. */
      assert.equal(kind(on({ enabled_at_ms: now - 4 * 24 * H })), 'late');

      /* Il controllo ha trovato la copia rovinata: non è più «al sicuro». */
      const damaged = cloudView(on({ last_success_ms: now - 2 * H, last_failure_ms: now - H,
                                     last_error: 'verify_mismatch' }), now);
      assert.deepEqual([damaged.kind, damaged.tone, damaged.action], ['damaged', 'warn', 'copyNow']);
      /* Un controllo solo non finito (rete) non lo è. */
      assert.equal(kind(on({ last_success_ms: now - 2 * H, last_failure_ms: now - H,
                             last_error: 'verify_failed' })), 'safe');
      /* E una copia riuscita dopo la rimette a posto. */
      assert.equal(kind(on({ last_success_ms: now - H, last_failure_ms: now - 2 * H,
                             last_error: 'verify_mismatch' })), 'safe');

      /* «Te l'ho scritto in chat» solo se l'avviso è partito dopo l'ultima copia. */
      assert.equal(cloudView(on({ last_success_ms: now - 80 * H, alerted_ms: now - H }), now).alerted, true);
      assert.equal(cloudView(on({ last_success_ms: now - 80 * H, alerted_ms: now - 90 * H }), now).alerted, false);
    """)


def test_reasons_are_the_ones_the_page_can_explain() -> None:
    _run("""
      assert.equal(reasonKey(''), 'waiting');
      assert.equal(reasonKey('wrong_key'), 'missing_key');
      assert.equal(reasonKey('consent_needed'), 'consent_needed');
      assert.equal(reasonKey('verify_mismatch'), 'verify_mismatch');
      assert.equal(reasonKey('OSError: [Errno 28] No space left'), 'other');
      for (const r of ['waiting', 'missing_key', 'consent_needed', 'verify_mismatch', 'other']) {
        assert.ok(!i18n.t(`cloudBackup.reasons.${r}.why`).startsWith('cloudBackup'), r);
        assert.ok(!i18n.t(`cloudBackup.reasons.${r}.todo`).startsWith('cloudBackup'), r);
      }
    """)


def test_the_seven_nights() -> None:
    _run("""
      const now = Date.parse('2026-10-15T12:00:00');  // giovedì
      const H = 3600 * 1000;
      const at = (iso) => Date.parse(iso);
      const states = (s) => nightStrip(s, now, 'it').cells.map((c) => c.state).join(' ');

      const all = { enabled_at_ms: now - 30 * 24 * H,
                    recent_copies_ms: [9, 10, 11, 12, 13, 14, 15].map((d) => at(`2026-10-${String(d).padStart(2, '0')}T03:12:00`)) };
      assert.equal(states(all), 'ok ok ok ok ok ok ok');
      const strip = nightStrip(all, now, 'it');
      assert.equal(strip.cells[6].label, 'oggi');
      assert.equal(strip.cells[0].label, 'ven', strip.cells[0].label);
      assert.deepEqual([strip.copies, strip.missed], [7, 0]);

      /* Oggi senza copia non è un buco: la notte non è passata. */
      const noToday = { enabled_at_ms: now - 30 * 24 * H,
                        recent_copies_ms: [9, 10, 11, 12, 13, 14].map((d) => at(`2026-10-${String(d).padStart(2, '0')}T03:12:00`)) };
      assert.equal(states(noToday), 'ok ok ok ok ok ok today');

      /* Un buco in mezzo, e due copie nello stesso giorno contano una volta. */
      const gap = { enabled_at_ms: now - 30 * 24 * H, recent_copies_ms: [
        at('2026-10-09T03:00:00'), at('2026-10-11T03:00:00'), at('2026-10-11T18:00:00'), at('2026-10-15T03:00:00')] };
      assert.equal(states(gap), 'ok miss ok miss miss miss ok');

      /* 23:59 e 00:01 sono due giorni diversi. */
      const edge = { enabled_at_ms: now - 30 * 24 * H, recent_copies_ms: [at('2026-10-13T23:59:00'), at('2026-10-14T00:01:00')] };
      assert.equal(states(edge), 'miss miss miss miss ok ok today');

      /* Accesa tre giorni fa: i giorni prima non sono buchi. */
      const fresh = { enabled_at_ms: at('2026-10-12T20:00:00'), recent_copies_ms: [at('2026-10-13T03:00:00')] };
      assert.equal(states(fresh), 'before before before miss ok miss today');

      /* Un telefono ripristinato non sa quando fu accesa: conta dalla copia più vecchia. */
      const restored = { recent_copies_ms: [at('2026-10-14T03:00:00')] };
      assert.equal(states(restored), 'before before before before before ok today');
    """)


def test_the_seven_nights_across_daylight_saving() -> None:
    """Il 25/10/2026 in Europa il giorno dura 25 ore: sette celle restano sette giorni."""
    run_js(_harness() + """
      const now = Date.parse('2026-10-28T12:00:00');
      const copies = [22, 23, 24, 25, 26, 27, 28].map((d) => Date.parse(`2026-10-${d}T03:12:00`));
      const strip = nightStrip({ enabled_at_ms: now - 30 * 86400000, recent_copies_ms: copies }, now, 'it');
      assert.equal(strip.cells.map((c) => c.state).join(' '), 'ok ok ok ok ok ok ok');
    """, env={"TZ": "Europe/Rome"})


def test_dates_like_a_person_says_them() -> None:
    _run("""
      const now = Date.parse('2026-10-15T12:00:00');
      assert.ok(dayText(Date.parse('2026-10-15T03:12:00'), now, 'it').startsWith('oggi alle 3:12'),
        dayText(Date.parse('2026-10-15T03:12:00'), now, 'it'));
      assert.ok(dayText(Date.parse('2026-10-14T23:50:00'), now, 'it').startsWith('ieri alle'));
      assert.ok(dayText(Date.parse('2026-10-11T03:12:00'), now, 'it').startsWith('domenica alle'));
      assert.equal(dayText(Date.parse('2026-10-03T03:12:00'), now, 'it'), '3 ott');
      assert.equal(sizeText(8_400_000, 'it'), '8,4 MB');
      assert.equal(sizeText(1_250_000_000, 'it'), '1,3 GB');
      assert.equal(sizeText(300, 'it'), '1 kB');
      assert.deepEqual(progressSizes(0, 20_000_000, 'it'), { done: '0 MB', total: '20 MB' });
      assert.deepEqual(progressSizes(5_100_000, 8_400_000, 'it'), { done: '5,1 MB', total: '8,4 MB' });
      assert.deepEqual(progressSizes(30, 20, 'it'), { done: '0 kB', total: '0 kB' });
      assert.equal(calendarDays(Date.parse('2026-10-11T03:12:00'), now), 4);
    """)


def test_every_error_is_a_sentence() -> None:
    _run("""
      assert.equal(cloudErrorText('consent_needed'), i18n.t('cloudBackup.errors.consent_needed'));
      /* Gli errori tecnici del gateway arrivano come «Tipo: dettaglio»: diventano
         la frase generica, mai il testo dell'eccezione. */
      assert.equal(cloudErrorText('DriveError: HTTP 500'), i18n.t('cloudBackup.errors.generic'));
      assert.equal(cloudErrorText(''), i18n.t('cloudBackup.errors.generic'));
      assert.equal(cloudErrorText(undefined), i18n.t('cloudBackup.errors.generic'));
    """)


def test_same_phone_lists_the_copies_without_a_password() -> None:
    _run("""
      nativeAnswers.connectDrive = 'granted';
      answers.remote = { ok: true, snapshots: [{ id: 'a', created_at_ms: 1 }] };
      const found = await findMyCopy({ askPassword: askWith('mai chiesta') });
      assert.equal(found.ok, true);
      assert.deepEqual(calls, ['connectDrive', 'remote']);
      assert.equal(asked.length, 0, 'ha chiesto una password che non serviva');
    """)


def test_new_phone_asks_the_password_and_retries_a_wrong_one() -> None:
    _run("""
      nativeAnswers.connectDrive = 'granted';
      let opened = false;
      api.cloudAction = async (name) => {
        calls.push(name);
        if (name === 'remote') return opened ? { ok: true, snapshots: [] } : { ok: false, error: 'missing_key' };
        return answers[name];
      };
      answers['unlock:sbagliata'] = { ok: false, error: 'wrong_password' };
      const realWith = api.cloudWithPassword;
      api.cloudWithPassword = async (name, pw) => {
        const r = await realWith(name, pw);
        if (r?.ok) opened = true;
        return r;
      };
      answers['unlock:giusta lunga'] = { ok: true };
      const found = await findMyCopy({ askPassword: askWith('sbagliata', 'giusta lunga') });
      assert.equal(found.ok, true);
      assert.deepEqual(calls, ['connectDrive', 'remote', 'unlock:sbagliata', 'unlock:giusta lunga', 'remote']);
      assert.equal(asked.length, 1, 'il dialogo si è chiuso e riaperto sulla password sbagliata');
    """)


def test_no_copy_no_password_question() -> None:
    _run("""
      nativeAnswers.connectDrive = 'granted';
      answers.remote = { ok: false, error: 'no_backup' };
      assert.deepEqual(await findMyCopy({ askPassword: askWith('x') }), { ok: false, error: 'no_backup' });
      assert.equal(asked.length, 0);
    """)


def test_giving_up_on_the_password_restores_nothing() -> None:
    _run("""
      nativeAnswers.connectDrive = 'granted';
      answers.remote = { ok: false, error: 'missing_key' };
      assert.deepEqual(await findMyCopy({ askPassword: askWith(null) }), { ok: false, error: 'cancelled' });
      assert.deepEqual(await unlockHere({ askPassword: askWith(null) }), { ok: false, error: 'cancelled' });
      assert.ok(!calls.includes('run'));
    """)


def test_unlocking_here_resumes_the_copy() -> None:
    _run("""
      let opened = false;
      api.cloudAction = async (name) => {
        calls.push(name);
        if (name === 'remote') return opened ? { ok: true, snapshots: [] } : { ok: false, error: 'wrong_key' };
        return { ok: true };
      };
      api.cloudWithPassword = async (name, pw) => { calls.push(`${name}:${pw}`); opened = true; return { ok: true }; };
      const result = await unlockHere({ askPassword: askWith('la password') });
      assert.equal(result.ok, true);
      assert.deepEqual(calls, ['remote', 'unlock:la password', 'remote', 'run']);
    """)


def test_turning_on_asks_the_password_only_for_a_new_key() -> None:
    _run("""
      nativeAnswers.connectDrive = 'granted';
      answers.enable = { ok: false, error: 'password_needed' };
      answers['enable:una password'] = { ok: true, password_set_ms: 1 };
      answers.run = { ok: true };
      const result = await turnOnDrive({ askPassword: askWith('una password') });
      assert.equal(result.ok, true);
      assert.deepEqual(calls, ['connectDrive', 'enable', 'enable:una password', 'run']);

      /* Riaccesa, con la password già sulla copia: nessuna domanda. */
      calls.length = 0; asked.length = 0;
      answers.enable = { ok: true, password_set_ms: 5 };
      assert.equal((await turnOnDrive({ askPassword: askWith('x') })).ok, true);
      assert.deepEqual(calls, ['connectDrive', 'enable', 'run']);
      assert.equal(asked.length, 0);

      /* Riaccesa, ma la copia la password non ce l'ha: la sceglie adesso. */
      calls.length = 0;
      answers.enable = { ok: true, password_set_ms: 0 };
      answers['password:nuova password'] = { ok: true, password_set_ms: 9 };
      assert.equal((await turnOnDrive({ askPassword: askWith('nuova password') })).ok, true);
      assert.deepEqual(calls, ['connectDrive', 'enable', 'password:nuova password', 'run']);
    """)


def test_turning_on_over_someone_elses_copy_asks_nothing() -> None:
    _run("""
      nativeAnswers.connectDrive = 'granted';
      answers.enable = { ok: false, error: 'existing_backup' };
      const result = await turnOnDrive({ askPassword: askWith('x') });
      assert.deepEqual(result, { ok: false, error: 'existing_backup' });
      assert.equal(asked.length, 0);
    """)


def test_choosing_without_typing_is_a_cancel() -> None:
    _run("""
      assert.deepEqual(await choosePassword({ askPassword: askWith(null) }), { ok: false, error: 'cancelled' });
      answers['password:una password'] = { ok: true };
      assert.equal((await choosePassword({ askPassword: askWith('una password') })).ok, true);
    """)


def test_no_consent_no_search() -> None:
    _run("""
      nativeAnswers.connectDrive = 'denied';
      assert.deepEqual(await findMyCopy(), { ok: false, error: 'denied' });
      nativeAnswers.connectDrive = 'unavailable';
      assert.deepEqual(await findMyCopy(), { ok: false, error: 'unavailable' });
      assert.deepEqual(calls, ['connectDrive', 'connectDrive']);
    """)


def test_two_taps_one_search() -> None:
    _run("""
      nativeAnswers.connectDrive = 'granted';
      answers.remote = { ok: true, snapshots: [] };
      const [a, b] = await Promise.all([findMyCopy(), findMyCopy()]);
      assert.deepEqual([a.ok, b.error], [true, 'busy']);
    """)


def test_the_password_rules_are_said_before_sending() -> None:
    """Il dialogo controlla da se' quel che non serve chiedere al gateway: la
    lunghezza, e scegliendo che le due siano uguali. Scrivendo quella che c'e'
    (telefono nuovo) la seconda non c'e', e conta solo la lunghezza."""
    _run(f"""
      const CLOUD_PASSWORD_MIN = 8;
      {function(DIALOGS_JS, "passwordProblem")}
      assert.equal(passwordProblem('choose', 'corta', 'corta'), i18n.t('cloudBackup.password.tooShort', {{ n: 8 }}));
      assert.equal(passwordProblem('choose', 'abbastanza', 'diversa!!'), i18n.t('cloudBackup.password.mismatch'));
      assert.equal(passwordProblem('change', 'abbastanza', 'abbastanza'), null);
      assert.equal(passwordProblem('type', 'abbastanza', undefined), null);
      assert.equal(passwordProblem('type', 'corta', undefined), i18n.t('cloudBackup.password.tooShort', {{ n: 8 }}));
    """)


def test_the_minimum_is_the_same_on_both_sides() -> None:
    from jenny_extensions.drive.cloud import PASSWORD_MIN_LEN

    js = int(re.search(r"export const CLOUD_PASSWORD_MIN = (\d+);", CLOUD_JS).group(1))
    assert js == PASSWORD_MIN_LEN
    # Anche le frasi che lo dicono a parole, in tutte e due le lingue.
    for lang in ("it", "en"):
        text = locale(lang)
        assert str(PASSWORD_MIN_LEN) in text["cloudBackup"]["errors"]["weak_password"]
