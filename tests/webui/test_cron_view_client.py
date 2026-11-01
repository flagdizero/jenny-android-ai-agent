"""Il modello di vista della programmazione, eseguito davvero sotto node.

``shared/cron-view.js`` e' puro di proposito — niente DOM, niente ``window``,
niente i18n — perche' i casi che deve azzeccare sono esattamente quelli che sul
telefono non si riescono a fabbricare: uno scheduler fermo, una scadenza nel
passato, un worker spento dalla config. Stesso idioma di
``test_launcher_rank_client.py``: il modulo si importa in node e le asserzioni
girano contro il codice vero.

Il caso che da' il nome al file, se dovesse averne uno, e' ``silenced``: un
monitor che tace **ha funzionato**, e un pannello che lo colora di rosso insegna
all'utente a ignorare i colori. E' la classe di difetto piu' silenziosa qui —
niente si rompe, l'informazione si limita a diventare inutile.
"""

from __future__ import annotations

import json
from pathlib import Path

from support.js_harness import requires_node, run_js

VIEW_JS = (
    Path(__file__).resolve().parents[2]
    / "jenny" / "templates" / "ui" / "assets" / "shared" / "cron-view.js"
)


pytestmark = requires_node

# ``tr`` finta: ritorna la chiave piu' i parametri, cosi' un'asserzione dice
# quale stringa e' stata scelta senza dipendere da una traduzione vera. Il
# modulo prende ``tr`` dal chiamante proprio per questo — importare i18n lo
# renderebbe non provabile qui.
_HARNESS = """
import assert from 'node:assert/strict';
const tr = (key, params) => (params ? key + ':' + JSON.stringify(params) : key);
const NOW = 1_700_000_000_000;
"""


def _run_js(script: str) -> str:
    source = VIEW_JS.read_text(encoding="utf-8") + _HARNESS + script
    return run_js(source, env={"TZ": "Europe/Rome", "PATH": "/usr/bin:/bin:/usr/local/bin"})


# ── il vocabolario dei cinque esiti ─────────────────────────────────────────

def test_a_silent_monitor_is_not_a_failure() -> None:
    out = _run_js("""
      console.log(JSON.stringify({
        ok: statusTone('ok'),
        silenced: statusTone('silenced'),
        skipped: statusTone('skipped'),
        could_not_check: statusTone('could_not_check'),
        error: statusTone('error'),
      }));
    """)
    tones = json.loads(out)

    assert tones["silenced"] == "neutral", "un monitor che tace ha guardato e non aveva nulla"
    assert tones["skipped"] == "neutral"
    assert tones["could_not_check"] == "warn", "il terzo stato non e' un errore"
    assert tones["error"] == "bad"
    assert tones["could_not_check"] != tones["error"]


def test_a_check_handed_to_a_helper_is_waiting_not_failing() -> None:
    """``delegated``: il monitor ha passato il controllo a un aiutante e ne aspetta
    il verdetto. Non e' finito e non e' fallito."""
    out = _run_js("console.log(statusTone('delegated'));")

    assert out.strip() == "neutral"


def test_an_outcome_this_version_does_not_know_is_neutral_not_broken() -> None:
    """Uno store scritto da una versione piu' nuova non deve sembrare un guasto."""
    out = _run_js("console.log(statusTone('qualcosa_di_domani'));")

    assert out.strip() == "neutral"


# ── §5.3: la scadenza persistita puo' essere nel passato ────────────────────

def test_a_next_run_in_the_past_is_reported_as_overdue() -> None:
    out = _run_js("""
      const past = formatWhen(NOW - 3 * 3_600_000, { nowMs: NOW, tr, locale: 'it' });
      const future = formatWhen(NOW + 30 * 60_000, { nowMs: NOW, tr, locale: 'it' });
      console.log(JSON.stringify({ past, future }));
    """)
    data = json.loads(out)

    assert data["past"]["overdue"] is True
    assert data["past"]["relative"].startswith("cron.when.ago")
    assert data["future"]["overdue"] is False
    assert data["future"]["relative"].startswith("cron.when.in")


def test_the_now_window_does_not_call_a_due_job_late() -> None:
    """Entro tre quarti di minuto si dice «adesso»: un job che sta scattando non
    e' in ritardo, e non e' nemmeno «fra 0 minuti»."""
    out = _run_js("""
      console.log(JSON.stringify([
        formatWhen(NOW, { nowMs: NOW, tr }),
        formatWhen(NOW - 30_000, { nowMs: NOW, tr }),
        formatWhen(NOW - 60_000, { nowMs: NOW, tr }),
      ]));
    """)
    now, borderline, late = json.loads(out)

    assert now["relative"] == "cron.when.now"
    assert borderline["relative"] == "cron.when.now"
    assert borderline["overdue"] is False
    assert late["overdue"] is True


def test_a_missing_timestamp_is_null_not_a_fake_date() -> None:
    out = _run_js("""
      console.log(JSON.stringify([
        formatWhen(null, { nowMs: NOW, tr }),
        formatWhen(undefined, { nowMs: NOW, tr }),
      ]));
    """)

    assert json.loads(out) == [None, None]


# ── §5.4: il fuso si nomina solo quando dice qualcosa ───────────────────────

def test_the_time_zone_is_named_only_when_it_differs_from_the_device() -> None:
    """Il test gira con ``TZ=Europe/Rome``: Roma non va nominata, Tokyo sì."""
    out = _run_js("""
      console.log(JSON.stringify({
        same: timeZoneNote('Europe/Rome'),
        other: timeZoneNote('Asia/Tokyo'),
        none: timeZoneNote(null),
      }));
    """)
    notes = json.loads(out)

    assert notes["same"] is None
    assert notes["other"] == "Asia/Tokyo"
    assert notes["none"] is None


def test_a_time_zone_intl_rejects_does_not_take_the_row_down() -> None:
    """Uno store scritto a mano puo' portare un fuso che non esiste."""
    out = _run_js("""
      const when = formatWhen(NOW, { nowMs: NOW, tr, locale: 'it', timeZone: 'Marte/Olympus' });
      console.log(JSON.stringify(when.absolute.length > 0));
    """)

    assert json.loads(out) is True


# ── la schedulazione, composta qui e non dal server ─────────────────────────

def test_each_schedule_kind_gets_its_own_sentence() -> None:
    out = _run_js("""
      const say = (s) => describeSchedule(s, { tr, locale: 'it', timeZone: 'Europe/Rome' });
      console.log(JSON.stringify({
        every: say({ kind: 'every', every_ms: 1_800_000 }),
        hours: say({ kind: 'every', every_ms: 7_200_000 }),
        expr: say({ kind: 'cron', expr: '0 9 * * *' }),
        at: say({ kind: 'at', at_ms: NOW }),
        junk: say({ kind: 'every' }),
      }));
    """)
    said = json.loads(out)

    assert said["every"].startswith("cron.schedule.every")
    # La durata e' annidata dentro i parametri, quindi le virgolette sono
    # sfuggite una volta: si asserisce sulla forma vera, non su quella comoda.
    # ``n`` arriva formattato per la lingua, quindi e' una stringa: v.
    # ``test_the_decimal_separator_follows_the_locale``.
    assert "cron.unit.minutes" in said["every"] and '30' in said["every"]
    assert "cron.unit.hours" in said["hours"] and '2' in said["hours"]
    assert said["expr"] == 'cron.schedule.expr:{"expr":"0 9 * * *"}'
    assert said["at"].startswith("cron.schedule.at")
    assert said["junk"] == "cron.schedule.unknown", "una schedulazione incompleta non inventa"


def test_a_one_time_job_says_only_as_much_as_its_precision() -> None:
    """«Il 2 mar 2027» senza ora, «a marzo 2027» senza giorno: l'``at_ms`` c'e'
    sempre, ma mostrarlo intero direbbe una precisione che l'utente non ha chiesto."""
    out = _run_js("""
      const AT = Date.UTC(2027, 2, 2, 8, 0);  // 09:00 a Roma
      const say = (precision) => describeSchedule(
        { kind: 'at', at_ms: AT, precision }, { tr, locale: 'it', timeZone: 'Europe/Rome' });
      const next = (precision) => formatWhen(
        AT, { nowMs: AT - 86_400_000, tr, locale: 'it', timeZone: 'Europe/Rome', precision });
      console.log(JSON.stringify({
        exact: say(undefined), day: say('day'), month: say('month'),
        nextDay: next('day').absolute, nextMonth: next('month').absolute,
      }));
    """)
    said = json.loads(out)

    assert said["exact"].startswith("cron.schedule.at:") and "09:00" in said["exact"]
    assert said["day"].startswith("cron.schedule.at:")
    assert "2027" in said["day"] and "09:00" not in said["day"]
    assert said["month"] == 'cron.schedule.atMonth:{"when":"marzo 2027"}'
    assert "09:00" not in said["nextDay"]
    assert said["nextMonth"] == "marzo 2027"


def test_a_condition_reads_in_front_of_the_schedule() -> None:
    """«umidita ≤ 15,5 · ogni 6 ore»: il filtro prima della cadenza."""
    out = _run_js("""
      const view = buildCronView({
        available: true, now_ms: NOW, default_timezone: 'Europe/Rome',
        jobs: [{
          id: 'b', name: 'basilico', kind: 'user', effective: 'active',
          schedule: { kind: 'every', every_ms: 21_600_000 },
          condition: { app: 'orto', params: {}, field: 'umidita', op: '<=', value: 15.5 },
          next_run_at_ms: NOW + 60_000, last_run_at_ms: null, runs: [],
        }],
      }, { nowMs: NOW, tr, locale: 'it' });
      console.log(JSON.stringify({ condition: view.rows[0].condition,
                                   schedule: view.rows[0].schedule }));
    """)
    row = json.loads(out)

    assert row["condition"] == "umidita ≤ 15,5"
    assert row["schedule"].startswith("umidita ≤ 15,5 · cron.schedule.every")


def test_a_common_cron_expression_is_said_in_words() -> None:
    """«orario: 30 18 * * 0» sul cassetto (20/10/2026): la forma la dà il server."""
    out = _run_js("""
      const say = (shape, expr = '0 0 * * *') => describeSchedule({ kind: 'cron', expr, shape },
        { tr, locale: 'it', timeZone: 'Europe/Rome' });
      console.log(JSON.stringify({
        weekly: say({ pattern: 'weekly', dow: [0], hour: 18, minute: 30 }),
        two: say({ pattern: 'weekly', dow: [1, 4], hour: 9, minute: 0 }),
        weekdays: say({ pattern: 'weekly', dow: [1, 2, 3, 4, 5], hour: 9, minute: 0 }),
        daily: say({ pattern: 'daily', hour: 7, minute: 5 }),
        monthly: say({ pattern: 'monthly', dom: [1, 15], hour: 9, minute: 0 }),
        hours: say({ pattern: 'hours', every: 3, minute: 15 }),
        raw: say(null, '0 9,21 * * *'),
      }));
    """)
    said = json.loads(out)
    assert said["weekly"] == 'cron.schedule.weekly:{"days":"domenica","time":"18:30"}'
    assert said["two"] == 'cron.schedule.weekly:{"days":"lunedì e giovedì","time":"09:00"}'
    assert said["weekdays"] == 'cron.schedule.weekdays:{"time":"09:00"}'
    assert said["daily"] == 'cron.schedule.daily:{"time":"07:05"}'
    assert said["monthly"] == 'cron.schedule.monthly:{"days":"1 e 15","time":"09:00"}'
    assert said["hours"].startswith("cron.schedule.every") and "cron.unit.hours" in said["hours"]
    # Senza una forma comune, l'espressione com'è: resta vera.
    assert said["raw"] == 'cron.schedule.expr:{"expr":"0 9,21 * * *"}'


def test_a_notebook_cron_says_its_notebook_and_its_card() -> None:
    out = _run_js("""
      console.log(JSON.stringify([
        placeText({ notebook: 'Piante', card: 'acero', cardTitle: 'Acero' }),
        placeText({ notebook: 'Piante', card: '', cardTitle: '' }),
        placeText(null),
      ]));
    """)
    assert json.loads(out) == ["Piante › Acero", "Piante", None]


def test_a_non_round_interval_keeps_one_decimal_instead_of_vanishing() -> None:
    """«ogni 2,5 ore» arrotondato a 2 sarebbe una schedulazione diversa."""
    out = _run_js("""
      console.log(humanizeDuration(9_000_000, { tr, locale: 'it' }));
    """)

    assert "2,5" in out


def test_the_decimal_separator_follows_the_locale() -> None:
    """Difetto visto sul telefono il 10/09/2026: «1.9 ore fa» in italiano.

    Il numero veniva interpolato grezzo. Cambia una virgola, e cambia su ogni
    riga che porta un'ora e mezza.
    """
    out = _run_js("""
      console.log(JSON.stringify({
        it: humanizeDuration(6_840_000, { tr, locale: 'it' }),
        en: humanizeDuration(6_840_000, { tr, locale: 'en' }),
        noLocale: humanizeDuration(6_840_000, { tr }),
      }));
    """)
    said = json.loads(out)

    assert "1,9" in said["it"] and "1.9" not in said["it"]
    assert "1.9" in said["en"]
    # Senza locale si delega al default dell'ambiente invece di sollevare.
    assert said["noLocale"]


def test_one_of_a_unit_asks_for_the_singular_key() -> None:
    """``i18n.t`` non pluralizza, e «1 minuti» si legge a ogni riga.

    La scelta della chiave sta nel modulo puro proprio per poterla provare qui.
    """
    out = _run_js("""
      console.log(JSON.stringify({
        one_minute: humanizeDuration(60_000, { tr }),
        two_minutes: humanizeDuration(120_000, { tr }),
        one_hour: humanizeDuration(3_600_000, { tr }),
        one_day: humanizeDuration(86_400_000, { tr }),
        one_second: humanizeDuration(900, { tr }),
      }));
    """)
    said = json.loads(out)

    assert said["one_minute"].startswith("cron.unit.minute:")
    assert said["two_minutes"].startswith("cron.unit.minutes:")
    assert said["one_hour"].startswith("cron.unit.hour:")
    assert said["one_day"].startswith("cron.unit.day:")
    assert said["one_second"].startswith("cron.unit.second:")


# ── ordinamento: prima quel che chiede attenzione ───────────────────────────

def test_broken_first_then_inert_then_by_next_run_and_disabled_last() -> None:
    out = _run_js("""
      const job = (over) => ({
        name: over.name, enabled: over.enabled ?? true,
        effective: over.effective ?? 'active',
        next_run_at_ms: over.next ?? null,
        health: { consecutive_could_not_check: over.cnc ?? 0 },
      });
      const jobs = [
        job({ name: 'spento', enabled: false, effective: 'disabled', next: NOW + 1 }),
        job({ name: 'tardi', next: NOW + 9_000_000 }),
        job({ name: 'presto', next: NOW + 1_000 }),
        job({ name: 'inerte', effective: 'inert', next: NOW + 2 }),
        job({ name: 'rotto', cnc: 3, next: NOW + 9_999_999 }),
        job({ name: 'senza-scadenza' }),
      ];
      console.log(JSON.stringify(jobs.sort(compareJobs).map((j) => j.name)));
    """)

    assert json.loads(out) == [
        "rotto", "inerte", "presto", "tardi", "senza-scadenza", "spento",
    ]


def test_health_puts_a_broken_worker_above_an_inert_one() -> None:
    out = _run_js("""
      console.log(JSON.stringify({
        broken: jobHealth({ effective: 'inert', health: { consecutive_could_not_check: 2 } }),
        inert: jobHealth({ effective: 'inert', health: { consecutive_could_not_check: 0 } }),
        off: jobHealth({ effective: 'disabled', health: {} }),
        ok: jobHealth({ effective: 'active', health: {} }),
      }));
    """)

    assert json.loads(out) == {
        "broken": "broken", "inert": "inert", "off": "off", "ok": "ok",
    }


# ── il banner: uno solo, il piu' utile ──────────────────────────────────────

def test_the_banner_prefers_the_explanation_that_covers_the_whole_list() -> None:
    """Uno scheduler fermo spiega da solo ogni «in ritardo» della lista: senza di
    lui la stessa spiegazione andrebbe ripetuta su ogni riga."""
    out = _run_js("""
      const inert = [{ name: 'dream', effective: 'inert' }];
      console.log(JSON.stringify({
        unavailable: pickBanner({ available: false }),
        recovered: pickBanner({ available: true, recovery: { restored_from: 'empty' } }),
        stopped: pickBanner({ available: true, service_running: false, jobs: inert }),
        inert: pickBanner({ available: true, service_running: true, jobs: inert }),
        fine: pickBanner({ available: true, service_running: true, jobs: [] }),
      }));
    """)
    banners = json.loads(out)

    assert banners["unavailable"] == {"kind": "unavailable"}
    assert banners["recovered"]["kind"] == "recovered"
    assert banners["recovered"]["restoredFrom"] == "empty"
    assert banners["stopped"] == {"kind": "stopped"}
    assert banners["inert"] == {"kind": "inert", "jobs": ["dream"]}
    assert banners["fine"] is None


# ── il modello completo ─────────────────────────────────────────────────────

def test_the_whole_view_comes_together_for_a_realistic_payload() -> None:
    out = _run_js("""
      const payload = {
        available: true, now_ms: NOW, service_running: true, next_wake_at_ms: NOW + 60_000,
        default_timezone: 'Europe/Rome', recovery: null,
        counts: { total: 2, enabled: 2, system: 1, user: 1, inert: 1, broken: 0 },
        jobs: [
          {
            id: 'dream', name: 'dream', kind: 'system', purpose: 'Dream: ...',
            protected: true, enabled: true, effective: 'inert', mode: 'reminder',
            one_shot: false, message: null, message_preview: null,
            display_timezone: 'Europe/Rome',
            schedule: { kind: 'every', every_ms: 1_800_000 },
            next_run_at_ms: NOW + 1_800_000, last_run_at_ms: NOW - 60_000,
            last_status: 'ok', last_error: null,
            health: { consecutive_could_not_check: 0, since_ms: null, escalated: false },
            runs: [{ run_at_ms: NOW - 60_000, status: 'ok', duration_ms: 900, error: null }],
          },
          {
            id: 'ab12', name: 'gocce', kind: 'user', purpose: null, protected: false,
            enabled: true, effective: 'active', mode: 'monitor', one_shot: false,
            message: 'testo intero', message_preview: 'testo intero',
            display_timezone: 'Asia/Tokyo',
            schedule: { kind: 'cron', expr: '0 9 * * *', tz: 'Asia/Tokyo' },
            next_run_at_ms: NOW + 600_000, last_run_at_ms: null,
            last_status: 'silenced', last_error: null,
            health: { consecutive_could_not_check: 0, since_ms: null, escalated: false },
            runs: [],
          },
        ],
      };
      const view = buildCronView(payload, { nowMs: NOW, tr, locale: 'it' });
      console.log(JSON.stringify({
        banner: view.banner,
        order: view.rows.map((r) => r.name),
        systemRow: {
          health: view.rows[0].health,
          tzNote: view.rows[0].next.timeZoneNote,
        },
        userRow: {
          monitor: view.rows[1].monitor,
          tone: view.rows[1].lastTone,
          tzNote: view.rows[1].next.timeZoneNote,
          last: view.rows[1].last,
        },
        asOf: view.asOf,
      }));
    """)
    view = json.loads(out)

    assert view["banner"] == {"kind": "inert", "jobs": ["dream"]}
    assert view["order"] == ["dream", "gocce"], "l'inerte viene prima del sano"
    assert view["systemRow"]["health"] == "inert"
    assert view["systemRow"]["tzNote"] is None, "Roma su un telefono a Roma e' rumore"
    assert view["userRow"]["monitor"] is True
    assert view["userRow"]["tone"] == "neutral", "silenced non e' un fallimento"
    assert view["userRow"]["tzNote"] == "Asia/Tokyo"
    assert view["userRow"]["last"] is None, "mai girato: non si inventa una data"
    assert view["asOf"] == 1_700_000_000_000


def test_an_unavailable_payload_still_produces_a_renderable_view() -> None:
    """Durante l'onboarding la sezione deve dire «non ancora avviato», non
    esplodere e non restare su «caricamento»."""
    out = _run_js("""
      const view = buildCronView({ available: false, now_ms: NOW }, { tr, locale: 'it' });
      console.log(JSON.stringify(view));
    """)
    view = json.loads(out)

    assert view["available"] is False
    assert view["banner"] == {"kind": "unavailable"}
    assert view["rows"] == []


# ── Il filtro: un elenco per cassetto ───────────────────────────────────────


def _payload(*, jobs: str = "", extra: str = "") -> str:
    return f"{{ available: true, now_ms: NOW, default_timezone: 'Europe/Rome', jobs: [{jobs}], counts: {{ system: 4, user: 2 }}{extra} }}"


def _job(id_: str, kind: str, *, effective: str = "active", next_ms: str = "NOW + 60_000") -> str:
    """Un lavoro come lo manda il gateway. `effective` e' il campo che
    `pickBanner` guarda per dire «fermo»: `enabled` da solo non basta."""
    return (f"{{ id: '{id_}', name: '{id_}', kind: '{kind}', effective: '{effective}', "
            f"schedule: {{ kind: 'every', every_ms: 1_800_000 }}, next_run_at_ms: {next_ms}, "
            f"last_run_at_ms: NOW - 600_000, runs: [] }}")


def test_a_drawer_shows_only_its_own_jobs() -> None:
    """«Programmazione» non era una famiglia: i quattro lavori di sistema
    finiscono in tre posti diversi. Il payload porta gia' `kind` per riga, e
    il filtro e' quel che rende la divisione un parametro invece di un giro di
    codice nuovo."""
    out = _run_js(f"""
      const all = buildCronView({_payload(jobs=', '.join([
          _job('dream', 'system'), _job('gardener', 'system'),
          _job('update_check', 'system'), _job('pioggia-oslo', 'user')]))}, {{ tr }});
      const hands = buildCronView({_payload(jobs=', '.join([
          _job('dream', 'system'), _job('gardener', 'system'),
          _job('update_check', 'system'), _job('pioggia-oslo', 'user')]))},
        {{ tr, keep: (j) => j.kind !== 'system' }});
      console.log(JSON.stringify({{
        all: all.rows.map((r) => r.id),
        hands: hands.rows.map((r) => r.id),
      }}));
    """)
    visto = json.loads(out)
    assert sorted(visto["all"]) == ["dream", "gardener", "pioggia-oslo", "update_check"]
    assert sorted(visto["hands"]) == ["pioggia-oslo"], (
        "il cassetto Mani mostra lavori che appartengono a un altro cassetto"
    )


def test_the_banner_talks_about_the_jobs_you_can_see() -> None:
    """Il banner nomina i lavori spenti. Calcolato sul payload intero e mostrato
    accanto a un elenco filtrato, direbbe di lavori che li' non ci sono — e chi
    legge cerca una riga che non esiste."""
    off = ', '.join([
        _job('dream', 'system', effective='inert', next_ms='null'),
        _job('gardener', 'system', effective='inert', next_ms='null'),
        _job('update_check', 'system'),
        _job('pioggia-oslo', 'user'),
    ])
    out = _run_js(f"""
      const all = buildCronView({_payload(jobs=off)}, {{ tr }});
      const hands = buildCronView({_payload(jobs=off)},
        {{ tr, keep: (j) => j.kind !== 'system' }});
      console.log(JSON.stringify({{
        all: all.banner, hands: hands.banner,
      }}));
    """)
    visto = json.loads(out)
    assert visto["all"] and visto["all"]["kind"] == "inert", visto["all"]
    assert "dream" in visto["all"]["jobs"], visto["all"]
    assert not visto["hands"] or "dream" not in (visto["hands"].get("jobs") or []), (
        f"il banner di Mani nomina un lavoro che Mani non mostra: {visto['hands']}"
    )


def test_the_count_describes_what_is_on_screen() -> None:
    """I conteggi vengono dal payload e descrivono **tutti** i lavori: un «4
    lavori» sopra due righe si legge come un guasto."""
    jobs = ', '.join([
        _job('dream', 'system'), _job('gardener', 'system'),
        _job('update_check', 'system'), _job('pioggia-oslo', 'user'),
    ])
    out = _run_js(f"""
      const all = buildCronView({_payload(jobs=jobs)}, {{ tr }});
      const hands = buildCronView({_payload(jobs=jobs)},
        {{ tr, keep: (j) => j.kind !== 'system' }});
      console.log(JSON.stringify({{ all: all.counts, hands: hands.counts }}));
    """)
    visto = json.loads(out)
    assert visto["all"] == {"system": 4, "user": 2}, "senza filtro i conti restano quelli del server"
    assert visto["hands"] == {"system": 0, "user": 1}, visto["hands"]


def test_without_a_filter_nothing_changes() -> None:
    """Il filtro e' un parametro, non un cambio di comportamento: chi non lo
    passa deve vedere esattamente quel che vedeva prima."""
    jobs = ', '.join([_job('dream', 'system'), _job('pioggia-oslo', 'user')])
    out = _run_js(f"""
      const a = buildCronView({_payload(jobs=jobs)}, {{ tr }});
      const b = buildCronView({_payload(jobs=jobs)}, {{ tr, keep: undefined }});
      console.log(JSON.stringify({{ a: a.rows.map((r) => r.id), b: b.rows.map((r) => r.id),
                                    tallies: a.counts }}));
    """)
    visto = json.loads(out)
    assert visto["a"] == visto["b"]
    assert visto["tallies"] == {"system": 4, "user": 2}


def _predicate_of_hands() -> str:
    """`HANDS_JOBS` preso dal sorgente, non riscritto qui.

    Ricopiarlo vorrebbe dire misurare la copia: il difetto che conta e' che
    *quel* predicato cambi, non che ne esista uno giusto da qualche parte.
    """
    import re

    src = (
        Path(__file__).resolve().parents[2]
        / "jenny" / "templates" / "ui" / "assets" / "mobile-settings.js"
    ).read_text(encoding="utf-8")
    m = re.search(r"^export const HANDS_JOBS = (.+);$", src, re.M)
    assert m, "HANDS_JOBS non si trova piu' in mobile-settings.js"
    return m.group(1)


def test_the_hands_drawer_keeps_what_she_does_for_you() -> None:
    """I quattro lavori di sistema vanno in tre posti diversi.

    In Mani resta cio' che Jenny fa **per te** quando non glielo stai
    chiedendo: i tuoi promemoria e i tuoi controlli. `dream` e `gardener` riempiono la memoria e
    stanno accanto a quel che riempiono; `update_check` e' dell'app, e il suo
    giro e' in casa.
    """
    out = _run_js(f"""
      const keep = {_predicate_of_hands()};
      const jobs = [
        {{ id: 'dream', kind: 'system' }},
        {{ id: 'gardener', kind: 'system' }},
        {{ id: 'update_check', kind: 'system' }},
        {{ id: 'reminders', kind: 'system' }},
        {{ id: 'pioggia-oslo', kind: 'user' }},
      ];
      console.log(JSON.stringify(jobs.filter(keep).map((j) => j.id)));
    """)
    assert sorted(json.loads(out)) == ["pioggia-oslo"], json.loads(out)


# ── un payload senza `jobs`, con un filtro attivo ───────────────────────────


def test_counting_survives_a_payload_without_jobs() -> None:
    """Un guardiano su due non e' un guardiano.

    ``visto`` nasce da ``keep && payload?.jobs ? {...} : payload``: quando
    ``jobs`` manca, ``visto`` **e'** ``payload``, cioe' un oggetto senza
    ``jobs``. Le due righe che ricontano i lavori ci facevano ``.filter``
    sopra — e con un filtro attivo, che Mani passa **sempre**
    (``HANDS_JOBS``), era un ``TypeError`` che portava via l'intero gruppo
    «quando agisce da sola».

    Trovato per caso il 21/09/2026 mentre si provava un altro passo, con una
    risposta finta priva di ``jobs``. La riga sopra si proteggeva gia'; questa
    no, e la distanza fra le due era di tre righe.
    """
    out = _run_js("""
      const view = buildCronView(
        { service_running: true },
        { nowMs: NOW, tr, locale: 'it', keep: (j) => j.kind !== 'system' },
      );
      console.log(JSON.stringify({ available: view.available, counts: view.counts }));
    """)
    data = json.loads(out)

    assert data["counts"] == {"system": 0, "user": 0}, (
        "senza lavori i conteggi sono zero, non un'eccezione"
    )


def test_the_row_carries_the_actions_the_server_allows_and_the_pause() -> None:
    """La lista dei gesti viene dal server e passa intatta: il client non la
    ricalcola. Un payload di una versione vecchia, senza ``actions``, non offre
    niente invece di rompersi."""
    paused = (_job("acqua", "user", effective="disabled", next_ms="null")[:-2]
              + ", actions: ['resume', 'remove'], paused_at_ms: NOW - 3_600_000 }")
    legacy = _job("vecchio", "user")
    out = _run_js(f"""
      const view = buildCronView({_payload(jobs=paused + ', ' + legacy)}, {{ tr }});
      const byId = Object.fromEntries(view.rows.map((r) => [r.id, r]));
      console.log(JSON.stringify({{
        acqua: [byId.acqua.actions, byId.acqua.pausedAtMs === NOW - 3_600_000],
        vecchio: [byId.vecchio.actions, byId.vecchio.pausedAtMs],
      }}));
    """)
    visto = json.loads(out)
    assert visto["acqua"] == [["resume", "remove"], True]
    assert visto["vecchio"] == [[], None]
