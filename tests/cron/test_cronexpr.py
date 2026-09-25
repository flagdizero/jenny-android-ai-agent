"""``jenny.cron.cronexpr``: le espressioni cron senza ``croniter``.

``croniter`` è uscita da Jenny perché, appena importata, chiamava
``platform.architecture()``, che su Linux lancia ``file`` come sottoprocesso:
sotto Android il figlio nato con ``vfork`` resettava il gestore di SIGSEGV di
ART, e a ogni avvio del gateway il log si prendeva novanta righe d'errore
(``libsigchain: Setting SIGSEGV to SIG_DFL``). Il modulo che la sostituisce
deve dare gli stessi istanti, perché i job salvati sul telefono continuino a
scattare quando scattavano.

Come si è verificato (25/09/2026): 250.000 espressioni casuali, anche esotiche
e sbagliate, confrontate con croniter 6.2.4 su cinque esecuzioni successive:
nessuna differenza fuori dalle tre categorie volute qui sotto. Il file
``fixtures/cronexpr_croniter_samples.json`` ne tiene 1.500, coi risultati di
croniter, così il confronto resta anche senza croniter installata. Le regole ai
cambi d'ora sono state verificate a parte contro un riferimento indipendente
calcolato minuto per minuto (13.552 partenze in quattro fusi, nessuna
differenza); qui ne restano i casi che le raccontano.

Le tre differenze volute:
1. ai cambi d'ora la regola del cron di Vixie (croniter era incoerente: vedi
   il cappello di ``cronexpr``): un lavoro a orario fisso parte una volta, appena
   finito il salto se il suo orario non esiste, la prima volta se capita due;
   uno a ripetizione segue il tempo vero, e salta o ripete con l'orologio;
2. dove croniter si arrendeva con un errore perché uno dei due rami
   dell'"oppure" fra giorno del mese e giorno della settimana non trovava mai
   niente (``16 * fri#2``, ``15W * 0``), qui vale l'altro ramo;
3. nessun tetto al 2099 per le espressioni senza campo dell'anno (croniter
   stessa lo trattava così: il 2099 è il limite del settimo campo);
4. con il campo dell'anno la ricerca arriva al suo ultimo anno, non a
   cinquant'anni da oggi: ``0 9 1 1 * 0 2080`` scatta nel 2080, dove croniter
   (e la prima stesura di questo modulo) si arrendeva (seconda revisione,
   25/09/2026).
"""

from __future__ import annotations

import json
import subprocess
import sys
import zoneinfo
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from jenny.cron.cronexpr import next_after, parse

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = Path(__file__).parent / "fixtures" / "cronexpr_croniter_samples.json"
ROME = ZoneInfo("Europe/Rome")


def _tz(key: str):
    if key == "UTC":
        return timezone.utc
    if key[0] in "+-":
        sign = 1 if key[0] == "+" else -1
        hours, minutes = key[1:].split(":")
        return timezone(sign * timedelta(hours=int(hours), minutes=int(minutes)))
    return ZoneInfo(key)


def _chain(expr: str, base: datetime, n: int) -> list[datetime]:
    out: list[datetime] = []
    for _ in range(n):
        base = next_after(expr, base)
        out.append(base)
    return out


def _rome(*args) -> datetime:
    return datetime(*args, tzinfo=ROME)


# ── La stessa lingua di croniter ────────────────────────────────────────────


def _tz_data_version() -> str:
    """La versione IANA dei dati che ``ZoneInfo`` sta leggendo davvero.

    ``zoneinfo`` cerca prima nelle cartelle di sistema (``TZPATH``) e solo poi
    nel pacchetto ``tzdata``: sulla stessa macchina due interpreti possono
    leggere dati diversi, e la CI legge quelli della sua immagine.
    """
    for base in zoneinfo.TZPATH:
        root = Path(base)
        if not (root / "Europe" / "Rome").is_file():
            continue
        if (root / "+VERSION").is_file():  # macOS
            return (root / "+VERSION").read_text(encoding="utf-8").strip()
        if (root / "tzdata.zi").is_file():  # Linux: "# version 2026c"
            with (root / "tzdata.zi").open(encoding="utf-8", errors="replace") as fh:
                first = fh.readline()
            if first.startswith("# version "):
                return first.removeprefix("# version ").split("-")[0].strip()
        return f"sconosciuta ({root})"
    try:
        import tzdata
    except ImportError:
        return "sconosciuta"
    return tzdata.IANA_VERSION


def _reproduce(cases: list[dict]) -> list[tuple]:
    wrong = []
    for case in cases:
        base = datetime.fromisoformat(case["base"]).astimezone(_tz(case["tz"]))
        if case["next"] == "error":
            try:
                next_after(case["expr"], base)
            except ValueError:
                continue
            wrong.append((case["expr"], "croniter lo rifiutava"))
            continue
        got = [int(d.timestamp()) for d in _chain(case["expr"], base, len(case["next"]))]
        if got != case["next"]:
            wrong.append((case["expr"], case["tz"], case["base"], case["next"], got))
    return wrong


def _is_fixed(tz: str) -> bool:
    return tz == "UTC" or tz[0] in "+-"


def test_every_recorded_croniter_answer_on_a_fixed_offset_is_reproduced() -> None:
    """UTC e fusi a scarto fisso: nessuna tabella di fusi in mezzo, vale ovunque."""
    data = json.loads(SAMPLES.read_text(encoding="utf-8"))
    assert len(data["cases"]) == 1500
    fixed = [c for c in data["cases"] if _is_fixed(c["tz"])]
    assert len(fixed) > 400, "i casi a scarto fisso sono spariti dai campioni"
    wrong = _reproduce(fixed)
    assert not wrong, wrong[:5]


def test_every_recorded_croniter_answer_in_a_named_zone_is_reproduced() -> None:
    """I fusi con un nome dipendono dalla versione dei dati IANA.

    I campioni sono stati verificati con le versioni in ``tz_data_verified``;
    ``America/Santiago`` cambia regole spesso, e un'altra versione può spostare
    un istante senza che ``cronexpr`` sbagli niente. Con una versione
    verificata la prova è stretta; con un'altra, una differenza **salta**
    dicendolo invece di fallire: i campioni non si rigenerano senza croniter,
    e un rosso che dipende dall'immagine della CI non dice niente del codice.
    """
    data = json.loads(SAMPLES.read_text(encoding="utf-8"))
    named = [c for c in data["cases"] if not _is_fixed(c["tz"])]
    wrong = _reproduce(named)
    if not wrong:
        return
    version = _tz_data_version()
    verified = data["tz_data_verified"]
    if version not in verified:
        pytest.skip(
            f"dati dei fusi {version}, campioni verificati con {verified}: "
            f"{len(wrong)} casi in fusi con nome danno altri istanti (es. {wrong[0]}). "
            "Può essere un cambio di regole IANA e non un difetto di cronexpr: "
            "confrontare con croniter su questa versione prima di toccare il codice."
        )
    assert not wrong, wrong[:5]


@pytest.mark.parametrize(
    ("expr", "base", "expected"),
    [
        # Le forme di tutti i giorni: giornaliero, serale, settimanale.
        ("0 8 * * *", (2026, 9, 25, 10, 17), [(2026, 9, 26, 8, 0), (2026, 9, 27, 8, 0)]),
        ("0 20 * * *", (2026, 9, 25, 10, 17), [(2026, 9, 25, 20, 0), (2026, 9, 26, 20, 0)]),
        ("0 7 * * *", (2026, 9, 25, 7, 0), [(2026, 9, 26, 7, 0), (2026, 9, 27, 7, 0)]),
        ("0 9 * * 1", (2026, 9, 25, 10, 17), [(2026, 9, 28, 9, 0), (2026, 10, 5, 9, 0)]),
        # Le forme meno ovvie, con quello che croniter rispondeva.
        ("59/4 9 * * *", (2026, 9, 25, 8, 0), [(2026, 9, 25, 9, 0), (2026, 9, 25, 9, 4)]),
        ("0 9 * * 5-5", (2026, 9, 25, 10, 0), [(2026, 9, 26, 9, 0), (2026, 9, 27, 9, 0)]),
        ("0 9 1-31 * mon", (2026, 9, 25, 10, 0), [(2026, 9, 26, 9, 0), (2026, 9, 27, 9, 0)]),
        ("0 9 L * *", (2026, 9, 25, 10, 0), [(2026, 9, 30, 9, 0), (2026, 10, 31, 9, 0)]),
        ("0 9 15W * *", (2026, 9, 25, 10, 0), [(2026, 10, 15, 9, 0), (2026, 11, 16, 9, 0)]),
        ("0 9 * * fri#2", (2026, 9, 25, 10, 0), [(2026, 10, 9, 9, 0), (2026, 11, 13, 9, 0)]),
        ("0 9 * * l5", (2026, 9, 25, 10, 0), [(2026, 10, 30, 9, 0), (2026, 11, 27, 9, 0)]),
        ("0 22-2 * * *", (2026, 9, 25, 21, 0), [(2026, 9, 25, 22, 0), (2026, 9, 25, 23, 0)]),
        ("@weekly", (2026, 9, 25, 10, 0), [(2026, 9, 27, 0, 0), (2026, 10, 4, 0, 0)]),
    ],
)
def test_known_expressions(expr: str, base: tuple, expected: list[tuple]) -> None:
    assert _chain(expr, _rome(*base), len(expected)) == [_rome(*e) for e in expected]


@pytest.mark.parametrize(
    "expr",
    ["", "0 9 * *", "0 9 * * * * * *", "0 25 * * *", "*/0 * * * *", "0 9 * * 8", "0 9 32 * *",
     "0 9 * * mon,fri#1", "0 9 L-3 * *", "0 9 * * 5L", "x * * * *", "0 9 1,15W * *", "0 0 31 2 *"],
)
def test_what_croniter_refused_is_refused(expr: str) -> None:
    with pytest.raises(ValueError):
        next_after(expr, _rome(2026, 9, 25, 10, 0))


def test_the_result_is_always_after_the_base_and_in_its_timezone() -> None:
    base = _rome(2026, 9, 25, 8, 0, 0)
    got = next_after("0 8 * * *", base)
    assert got > base and got.tzinfo is ROME
    with pytest.raises(ValueError):
        next_after("0 8 * * *", base.replace(tzinfo=None))


# ── Le tre differenze volute ────────────────────────────────────────────────


def test_a_time_that_does_not_exist_runs_once_when_the_jump_ends() -> None:
    """Roma, 29/03/2026: alle 02:00 si va alle 03:00. Un lavoro a orario fisso
    segue il calendario: il giornaliero delle 02:30 parte alle 03:00, una volta."""
    day_before = _rome(2026, 3, 28, 23, 0)
    assert _chain("30 2 * * *", day_before, 2) == [_rome(2026, 3, 29, 3, 0), _rome(2026, 3, 30, 2, 30)]


def test_a_time_that_happens_twice_runs_once_the_first_time() -> None:
    """Roma, 25/10/2026: alle 03:00 si torna alle 02:00. croniter faceva
    partire il giornaliero delle 02:30 due volte."""
    got = _chain("30 2 * * *", _rome(2026, 10, 24, 23, 0), 2)
    assert [d.isoformat() for d in got] == ["2026-10-25T02:30:00+02:00", "2026-10-26T02:30:00+01:00"]


def test_a_base_inside_the_repeated_hour_does_not_go_back_in_time() -> None:
    """Partendo dalla seconda passata delle 02:xx, le 02:31 della prima passata
    sono già trascorse: fra due datetime con lo stesso fuso Python confronta
    l'orario da parete, e un confronto ingenuo le darebbe ancora da venire."""
    second_pass = datetime(2026, 10, 25, 2, 30, 28, tzinfo=ROME, fold=1)
    got = next_after("31 2,9 * * *", second_pass)
    assert got.isoformat() == "2026-10-25T09:31:00+01:00"
    assert got.timestamp() > second_pass.timestamp()


# Un lavoro a ripetizione (il minuto o l'ora cominciano con ``*``) segue il tempo
# che passa davvero, come nel cron di Vixie: ai cambi d'ora il ritmo resta quello.


def _gaps_in_minutes(runs: list[datetime]) -> list[float]:
    return [(b.timestamp() - a.timestamp()) / 60 for a, b in zip(runs, runs[1:])]


def test_a_repeating_job_keeps_its_rhythm_through_the_repeated_hour() -> None:
    """Il difetto: ``*/15`` partito alle 02:50 della prima passata tornava alle
    03:00, un'ora e dieci dopo — un controllo ogni quindici minuti zitto per
    tutta la seconda passata."""
    first_pass = _rome(2026, 10, 25, 2, 50)
    runs = [first_pass, *_chain("*/15 * * * *", first_pass, 6)]
    assert _gaps_in_minutes(runs) == [10, 15, 15, 15, 15, 15]
    assert [r.isoformat() for r in runs[1:3]] == [
        "2026-10-25T02:00:00+01:00", "2026-10-25T02:15:00+01:00",
    ]


def test_an_hourly_job_runs_in_both_passes_of_the_repeated_hour() -> None:
    got = _chain("30 * * * *", _rome(2026, 10, 25, 1, 45), 3)
    assert [d.isoformat() for d in got] == [
        "2026-10-25T02:30:00+02:00", "2026-10-25T02:30:00+01:00", "2026-10-25T03:30:00+01:00",
    ]


def test_a_repeating_job_skips_the_times_that_do_not_exist() -> None:
    """Da 01:30 alle 03:30 del salto passa un'ora: l'orario ``30 *`` non ha niente
    da recuperare. (Com'era in croniter.) E ``*/15`` riprende alle 03:00."""
    assert _chain("30 * * * *", _rome(2026, 3, 29, 1, 50), 2) == [
        _rome(2026, 3, 29, 3, 30), _rome(2026, 3, 29, 4, 30),
    ]
    runs = [_rome(2026, 3, 29, 1, 45), *_chain("*/15 * * * *", _rome(2026, 3, 29, 1, 45), 3)]
    assert _gaps_in_minutes(runs) == [15, 15, 15]
    assert runs[1] == _rome(2026, 3, 29, 3, 0)


@pytest.mark.parametrize(
    ("spelled_out", "starred"),
    [
        ("0 0-23 * * *", "0 * * * *"),
        ("*/30 0-23 * * *", "*/30 * * * *"),
        ("0,15,30,45 2 * * *", "*/15 2 * * *"),
    ],
)
def test_a_star_spelled_out_behaves_like_a_star(spelled_out: str, starred: str) -> None:
    """``0 0-23`` e ``0 *`` sono lo stesso lavoro: le stesse partenze al cambio
    d'ora d'autunno e a quello di primavera."""
    assert parse(spelled_out).repeating
    for base in (_rome(2026, 10, 25, 0, 50), _rome(2026, 3, 29, 0, 50)):
        assert _chain(spelled_out, base, 8) == _chain(starred, base, 8)


def test_only_the_values_of_a_star_step_count_as_a_star() -> None:
    """La regola resta stretta: stessi valori di un ``*/n``, niente di più. Un
    orario d'ufficio, due orari al giorno, un passo sfasato: a orario fisso."""
    assert parse("0 0-22/2 * * *").repeating
    for fixed in ("0 9-17 * * *", "30 2 * * *", "0 9,21 * * *", "0 1-23/2 * * *"):
        assert not parse(fixed).repeating, fixed


def test_a_half_hour_shift_keeps_the_rhythm_too() -> None:
    """Lord Howe torna indietro di mezz'ora (alle 02:00 si torna alle 01:30)."""
    lord_howe = ZoneInfo("Australia/Lord_Howe")
    base = datetime(2026, 4, 5, 1, 20, tzinfo=lord_howe)
    runs = [base, *_chain("*/10 * * * *", base, 8)]
    assert set(_gaps_in_minutes(runs)) == {10}


def test_a_two_hour_shift_keeps_the_rhythm_too() -> None:
    """``Antarctica/Troll`` torna indietro di **due** ore (alle 03:00 si torna
    alle 01:00): e' il caso che mette alla prova ``_SHIFT_MARGIN``, che li' e'
    esatto. Dall'ultimo istante della prima passata il prossimo e' l'inizio
    della seconda, due ore prima sul quadrante."""
    troll = ZoneInfo("Antarctica/Troll")
    base = datetime(2026, 10, 25, 2, 20, tzinfo=troll)
    runs = [base, *_chain("*/10 * * * *", base, 20)]
    assert set(_gaps_in_minutes(runs)) == {10}
    last = datetime(2026, 10, 25, 2, 59, 59, tzinfo=troll)
    following = next_after("*/30 * * * *", last)
    assert following.astimezone(timezone.utc) == datetime(2026, 10, 25, 1, 0, tzinfo=timezone.utc)
    assert (following.hour, following.minute, following.fold) == (1, 0, 1)


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        # Il 16 non è mai il secondo venerdì: croniter si arrendeva, qui valgono
        # i secondi venerdì (con ``x#n`` il giorno del mese non conta, come in
        # croniter: il 16 ottobre, terzo venerdì, non c'è).
        ("0 9 16 * fri#2", [(2026, 10, 9, 9, 0), (2026, 11, 13, 9, 0), (2026, 12, 11, 9, 0)]),
        # Il feriale più vicino non è mai domenica: qui vale il ``15W``.
        ("0 9 15W * 0", [(2026, 10, 15, 9, 0), (2026, 11, 16, 9, 0)]),
    ],
)
def test_where_croniter_gave_up_the_other_branch_holds(expr: str, expected: list[tuple]) -> None:
    assert _chain(expr, _rome(2026, 9, 25, 10, 0), len(expected)) == [_rome(*e) for e in expected]


def test_a_rare_date_is_found_past_2099() -> None:
    """Il quinto lunedì di febbraio capita ogni ventotto anni."""
    got = _chain("41 10 * 2 mon#5", _rome(2072, 3, 1, 0, 0), 2)
    # 2100 non è bisestile (secolo): il primo dopo il 2072 è il 2112.
    assert [d.date().isoformat() for d in got] == ["2112-02-29", "2140-02-29"]


def test_an_explicit_year_is_searched_where_it_is() -> None:
    """Il settimo campo arriva al 2099: cercarlo solo per cinquant'anni rifiutava
    come «mai» un'espressione valida."""
    base = _rome(2026, 9, 25, 10, 0)
    assert next_after("0 9 1 1 * 0 2080", base) == _rome(2080, 1, 1, 9, 0)
    assert next_after("0 9 1 1 * 0 2026-2099", base) == _rome(2027, 1, 1, 9, 0)
    # Anni tutti passati: e' davvero «mai».
    with pytest.raises(ValueError, match="no run"):
        next_after("0 9 1 1 * 0 2020,2021", base)


# ── Il motivo per cui esiste ────────────────────────────────────────────────


def test_computing_a_run_launches_no_process_and_imports_no_croniter() -> None:
    """In un interprete pulito, con un audit hook: calcolare la prossima
    esecuzione di un job non deve lanciare niente (croniter lanciava ``file``
    all'import) né portarsi dietro croniter."""
    script = (
        "import sys\n"
        "events = []\n"
        "sys.addaudithook(lambda ev, args: events.append(ev) if ev in "
        "('subprocess.Popen', 'os.posix_spawn', 'os.fork', 'os.exec', 'os.system') else None)\n"
        "from jenny.cron.service import _compute_next_run\n"
        "from jenny.cron.types import CronSchedule\n"
        "nxt = _compute_next_run(CronSchedule(kind='cron', expr='0 9 * * 1', tz='Europe/Rome'), "
        "1790000000000)\n"
        "assert nxt and nxt > 1790000000000, nxt\n"
        "assert not events, events\n"
        "assert 'croniter' not in sys.modules\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_parse_is_reusable() -> None:
    spec = parse("0 9 * * 1")
    assert next_after(spec, _rome(2026, 9, 25, 10, 0)) == _rome(2026, 9, 28, 9, 0)
