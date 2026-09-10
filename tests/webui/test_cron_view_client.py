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
import shutil
import subprocess
from pathlib import Path

import pytest

VIEW_JS = (
    Path(__file__).resolve().parents[2]
    / "jenny" / "templates" / "ui" / "assets" / "shared" / "cron-view.js"
)

_NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(_NODE is None, reason="node non disponibile")

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
    proc = subprocess.run(
        [str(_NODE), "--input-type=module", "-e", source],
        capture_output=True,
        text=True,
        timeout=60,
        env={"TZ": "Europe/Rome", "PATH": "/usr/bin:/bin:/usr/local/bin"},
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return proc.stdout


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
      const inert = [{ name: 'heartbeat', effective: 'inert' }];
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
    assert banners["inert"] == {"kind": "inert", "jobs": ["heartbeat"]}
    assert banners["fine"] is None


# ── §5.8 / §4.2: l'heartbeat che non controlla niente ───────────────────────

def test_a_heartbeat_with_no_tasks_is_flagged_even_though_it_says_ok() -> None:
    out = _run_js("""
      const view = heartbeatView({
        file_present: false, file_readable: true, active_task_count: 0,
        tasks: [], orphan_checks: [],
      });
      console.log(JSON.stringify(view));
    """)
    view = json.loads(out)

    assert view["checkingNothing"] is True
    assert view["filePresent"] is False
    assert view["counts"] == {"total": 0, "broken": 0, "pending": 0}


def test_the_three_task_states_are_counted_apart_and_orphans_are_separate() -> None:
    out = _run_js("""
      const view = heartbeatView({
        file_present: true, file_readable: true, active_task_count: 3,
        tasks: [
          { id: 'a', state: 'ok' },
          { id: 'b', state: 'broken', consecutive: 4 },
          { id: 'c', state: 'pending' },
        ],
        orphan_checks: [{ id: 'z', label: 'tolto', consecutive: 9 }],
      });
      console.log(JSON.stringify({ counts: view.counts, orphans: view.orphans.length,
                                   checkingNothing: view.checkingNothing }));
    """)
    data = json.loads(out)

    assert data["counts"] == {"total": 3, "broken": 1, "pending": 1}
    assert data["orphans"] == 1
    assert data["checkingNothing"] is False


# ── il modello completo ─────────────────────────────────────────────────────

def test_the_whole_view_comes_together_for_a_realistic_payload() -> None:
    out = _run_js("""
      const payload = {
        available: true, now_ms: NOW, service_running: true, next_wake_at_ms: NOW + 60_000,
        default_timezone: 'Europe/Rome', recovery: null,
        counts: { total: 2, enabled: 2, system: 1, user: 1, inert: 1, broken: 0 },
        jobs: [
          {
            id: 'heartbeat', name: 'heartbeat', kind: 'system', purpose: 'Heartbeat: ...',
            protected: true, enabled: true, effective: 'inert', mode: 'reminder',
            one_shot: false, message: null, message_preview: null,
            display_timezone: 'Europe/Rome',
            schedule: { kind: 'every', every_ms: 1_800_000 },
            next_run_at_ms: NOW + 1_800_000, last_run_at_ms: NOW - 60_000,
            last_status: 'ok', last_error: null,
            health: { consecutive_could_not_check: 0, since_ms: null, escalated: false },
            runs: [{ run_at_ms: NOW - 60_000, status: 'ok', duration_ms: 900, error: null }],
            heartbeat: { file_present: true, file_readable: true, active_task_count: 0,
                         tasks: [], orphan_checks: [] },
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
            runs: [], heartbeat: null,
          },
        ],
      };
      const view = buildCronView(payload, { nowMs: NOW, tr, locale: 'it' });
      console.log(JSON.stringify({
        banner: view.banner,
        order: view.rows.map((r) => r.name),
        heartbeatRow: {
          health: view.rows[0].health,
          checkingNothing: view.rows[0].heartbeat.checkingNothing,
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

    assert view["banner"] == {"kind": "inert", "jobs": ["heartbeat"]}
    assert view["order"] == ["heartbeat", "gocce"], "l'inerte viene prima del sano"
    assert view["heartbeatRow"]["health"] == "inert"
    assert view["heartbeatRow"]["checkingNothing"] is True
    assert view["heartbeatRow"]["tzNote"] is None, "Roma su un telefono a Roma e' rumore"
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
