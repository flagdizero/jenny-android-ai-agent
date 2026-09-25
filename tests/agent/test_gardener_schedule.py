"""I tre orologi dell'innesco: chi viene giardinato a questo tick.

Passo **T4.3** di ``roadmap/taccuino-passi.md``. Ogni cancello è provato come
**unico impedimento** — con gli altri due aperti — perché una guardia che non può
scattare non è una guardia, ed è la stessa lezione già scritta in
``agent/autocompact.py`` (là togliere il filtro non faceva cadere nessun test,
perché la sola chiave che ci arrivava era comunque ammessa). Qui vale verbatim:
tre condizioni in ``and``, e un test che non isola non prova niente.

Il cancello che conta di più è il **fermo**, nella sua seconda metà: ``run_gardener``
gira su una chiave sua e non condivide il lock della conversazione del progetto,
quindi «turno in volo» è tutto ciò che tiene utente e giardiniere dallo scrivere
la mappa nello stesso momento.

Il terzo cancello ha **due** argomenti da T3.5 (delta non letto *oppure* mappa
oltre il tetto), e il blocco in fondo al file prova entrambi più il freno che
tiene la seconda ragione dall'essere un livelock. Là vale la stessa regola
dell'isolamento: il freno si prova **con la distanza già passata**, altrimenti si
sta misurando il timbro del tentativo.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from loguru import logger

from jenny.agent import gardener_schedule as mod
from jenny.agent.gardener import MAP_TARGET_CHARS
from jenny.agent.gardener_schedule import pick_project
from jenny.agent.gardener_state import GardenerState, write_state

_NOW = datetime(2026, 8, 23, 21, 0, 0)

# Un tick con i cancelli aperti: nessuna passata recente, nessuno che parla.
_OPEN = {"idle_min": 30, "min_hours_between_passes": 6}


def _project(workspace: Path, name: str, *, lines: int = 2) -> Path:
    root = workspace / "wikis" / name
    (root / "wiki").mkdir(parents=True)
    (root / "wiki" / "index.md").write_text(f"# {name}\n", encoding="utf-8")
    journal = root / "raw" / "journal"
    journal.mkdir(parents=True)
    if lines:
        (journal / "20260823.md").write_text(
            "# 2026-08-23\n\n" + "".join(f"- 09:0{i} — fatto {i}\n" for i in range(lines)),
            encoding="utf-8",
        )
    return root


class _Sessions:
    """``SessionManager`` ridotto a quel che la selezione chiede."""

    def __init__(self, updated: dict[str, datetime] | None = None) -> None:
        self._updated = updated or {}

    def read_session_metadata(self, key: str):
        stamp = self._updated.get(key)
        return {"updated_at": stamp.isoformat()} if stamp else None


def _pick(workspace: Path, sessions=None, **kw):
    return pick_project(
        workspace,
        sessions=sessions or _Sessions(),
        now=_NOW,
        **{**_OPEN, **kw},
    )


# ── Il caso normale ──────────────────────────────────────────────────────────


def test_a_project_with_new_lines_and_nobody_talking_is_picked(tmp_path):
    _project(tmp_path, "viaggio")

    pick = _pick(tmp_path)

    assert pick is not None
    assert pick.store.name == "viaggio" and pick.delta_lines == 2


def test_no_projects_means_nothing_to_do(tmp_path):
    (tmp_path / "wikis").mkdir()
    assert _pick(tmp_path) is None


def test_a_folder_that_is_not_a_project_is_not_considered(tmp_path):
    (tmp_path / "wikis" / "appunti").mkdir(parents=True)
    assert _pick(tmp_path) is None


# ── Cancello 1: il delta ─────────────────────────────────────────────────────


def test_no_new_lines_no_pass(tmp_path):
    _project(tmp_path, "viaggio", lines=0)
    assert _pick(tmp_path) is None


def test_a_journal_already_read_no_pass(tmp_path):
    """Il caso vero del secondo tick: le righe ci sono, ma sono già state lette.
    Se questo cancello cadesse, ogni mezz'ora ripartirebbe una passata sullo
    stesso materiale — cioè il degrado, a pagamento."""
    root = _project(tmp_path, "viaggio")
    write_state(root, GardenerState(cursor={"raw/journal/20260823.md": 4}))

    assert _pick(tmp_path) is None


# ── Cancello 2: il fermo ─────────────────────────────────────────────────────


def test_a_conversation_that_just_spoke_blocks_the_pass(tmp_path):
    _project(tmp_path, "viaggio")
    sessions = _Sessions({"project:viaggio": _NOW - timedelta(minutes=5)})

    assert _pick(tmp_path, sessions=sessions) is None


def test_the_same_conversation_gone_quiet_lets_it_through(tmp_path):
    _project(tmp_path, "viaggio")
    sessions = _Sessions({"project:viaggio": _NOW - timedelta(minutes=45)})

    assert _pick(tmp_path, sessions=sessions) is not None


def test_a_turn_in_flight_blocks_the_pass(tmp_path):
    """**Il cancello che conta.** Il giardiniere non condivide il lock della
    conversazione del progetto: senza questo, utente e giardiniere possono
    riscrivere la mappa nello stesso momento e l'ultimo che salva cancella
    l'altro. Non è una rifinitura del fermo — è il caso peggiore, e il fermo da
    solo non lo copre (una sessione ferma da un'ora può avere un turno lungo in
    corso in questo istante)."""
    _project(tmp_path, "viaggio")
    sessions = _Sessions({"project:viaggio": _NOW - timedelta(hours=2)})

    assert _pick(tmp_path, sessions=sessions,
                 active_session_keys=("project:viaggio",)) is None


def test_another_project_being_busy_does_not_block_this_one(tmp_path):
    _project(tmp_path, "viaggio")

    assert _pick(tmp_path, active_session_keys=("project:altro",)) is not None


def test_a_project_never_talked_to_counts_as_quiet(tmp_path):
    """Nessun metadato vuol dire che quella conversazione non è mai esistita:
    non c'è niente che stia parlando."""
    _project(tmp_path, "viaggio")

    assert _pick(tmp_path, sessions=_Sessions()) is not None


def test_an_unreadable_timestamp_does_not_freeze_a_project_forever(tmp_path):
    """Un ``updated_at`` corrotto non deve valere «sta parlando adesso»: sarebbe
    un progetto mai più giardinato per un guasto che non si vede."""
    _project(tmp_path, "viaggio")

    class _Broken(_Sessions):
        def read_session_metadata(self, key):
            return {"updated_at": "non-una-data"}

    assert _pick(tmp_path, sessions=_Broken()) is not None


def test_idle_zero_means_the_clock_is_off(tmp_path):
    _project(tmp_path, "viaggio")
    sessions = _Sessions({"project:viaggio": _NOW})

    assert _pick(tmp_path, sessions=sessions, idle_min=0) is not None


# ── Cancello 3: la distanza ──────────────────────────────────────────────────


def test_a_recent_pass_blocks_the_next_one(tmp_path):
    """La lezione del degrado del Dream come numero: un secondo giro ravvicinato
    sulla stessa materia è quello che rimpasta invece di aggiungere."""
    root = _project(tmp_path, "viaggio")
    write_state(root, GardenerState(
        cursor={}, last_run_at=(_NOW - timedelta(hours=2)).isoformat()
    ))

    assert _pick(tmp_path) is None


def test_an_old_pass_does_not(tmp_path):
    root = _project(tmp_path, "viaggio")
    write_state(root, GardenerState(
        cursor={}, last_run_at=(_NOW - timedelta(hours=7)).isoformat()
    ))

    assert _pick(tmp_path) is not None


def test_a_project_never_gardened_goes_first(tmp_path):
    """Il caso da servire per primo è il progetto nuovo, e un cursore perso non
    deve poterlo bloccare per sei ore."""
    _project(tmp_path, "nuovo")
    root = _project(tmp_path, "vecchio")
    write_state(root, GardenerState(
        cursor={}, last_run_at=(_NOW - timedelta(hours=7)).isoformat()
    ))

    pick = _pick(tmp_path)

    assert pick is not None and pick.store.name == "nuovo"


def test_distance_zero_means_the_clock_is_off(tmp_path):
    root = _project(tmp_path, "viaggio")
    write_state(root, GardenerState(cursor={}, last_run_at=_NOW.isoformat()))

    assert _pick(tmp_path, min_hours_between_passes=0) is not None


# ── Una per tick, la meno recente ────────────────────────────────────────────


def test_only_one_project_is_picked_per_tick(tmp_path):
    """Otto progetti con righe nuove farebbero otto turni LLM di fila su un
    telefono. Il tetto a uno rende il costo di un tick prevedibile per
    costruzione; gli altri aspettano mezz'ora, che su un lavoro con sei ore di
    distanza minima non è un ritardo."""
    for name in ("alfa", "beta", "gamma"):
        _project(tmp_path, name)

    pick = _pick(tmp_path)

    assert pick is not None
    assert pick.store.name in {"alfa", "beta", "gamma"}


def test_the_least_recently_gardened_wins(tmp_path):
    for name, hours in (("alfa", 7), ("beta", 30), ("gamma", 12)):
        root = _project(tmp_path, name)
        write_state(root, GardenerState(
            cursor={}, last_run_at=(_NOW - timedelta(hours=hours)).isoformat()
        ))

    pick = _pick(tmp_path)

    assert pick is not None and pick.store.name == "beta"


def test_the_choice_is_deterministic_on_a_tie(tmp_path):
    """A pari merito decide il nome: due tick sullo stesso stato devono scegliere
    lo stesso progetto, altrimenti un guasto non si riproduce."""
    for name in ("gamma", "alfa", "beta"):
        _project(tmp_path, name)

    assert _pick(tmp_path).store.name == "alfa"
    assert _pick(tmp_path).store.name == "alfa"


# ── L'ordine dei cancelli, che è il costo ────────────────────────────────────


def test_the_journal_is_not_even_opened_when_a_cheaper_gate_is_shut(tmp_path, monkeypatch):
    """L'ordine dei tre cancelli non è estetico: distanza e fermo si decidono con
    due letture piccole, il delta vuole aprire i diari. In un'installazione con
    otto progetti fermi un tick deve toccare pochi byte, e questo è il test che
    lo tiene vero quando qualcuno riordinerà i controlli."""
    root = _project(tmp_path, "viaggio")
    write_state(root, GardenerState(
        cursor={}, last_run_at=(_NOW - timedelta(minutes=10)).isoformat()
    ))
    opened: list[str] = []
    from jenny.agent import gardener_schedule as mod

    real = mod.GardenerStore.read_delta

    def _spy(self):
        opened.append(self.name)
        return real(self)

    monkeypatch.setattr(mod.GardenerStore, "read_delta", _spy)

    assert _pick(tmp_path) is None
    assert opened == [], "il diario è stato letto per un progetto già escluso"


@pytest.mark.parametrize("blocked", ["delta", "idle", "active", "distance"])
def test_each_gate_alone_is_enough_to_stop_a_pass(tmp_path, blocked):
    """Il test riassuntivo: ogni cancello, da solo, con gli altri aperti."""
    root = _project(tmp_path, "viaggio", lines=0 if blocked == "delta" else 2)
    sessions = _Sessions(
        {"project:viaggio": _NOW} if blocked == "idle" else {}
    )
    if blocked == "distance":
        write_state(root, GardenerState(
            cursor={}, last_run_at=(_NOW - timedelta(hours=1)).isoformat()
        ))
    active = ("project:viaggio",) if blocked == "active" else ()

    assert _pick(tmp_path, sessions=sessions, active_session_keys=active) is None


def test_with_every_gate_open_it_runs(tmp_path):
    """Il controllo del test sopra: senza di questo, un ``pick_project`` che
    ritorna sempre ``None`` passerebbe tutti e quattro."""
    _project(tmp_path, "viaggio")

    assert _pick(tmp_path) is not None


def test_it_uses_the_configured_wikis_folder(tmp_path):
    root = tmp_path / "progetti" / "viaggio"
    (root / "wiki").mkdir(parents=True)
    (root / "raw" / "journal").mkdir(parents=True)
    (root / "raw" / "journal" / "20260823.md").write_text("- 09:00 — x\n", encoding="utf-8")

    pick = pick_project(
        tmp_path, sessions=_Sessions(), now=_NOW, wikis_dir_name="progetti", **_OPEN
    )

    assert pick is not None and pick.store.name == "viaggio"


def test_a_session_manager_that_raises_does_not_stop_the_tick(tmp_path):
    """Se leggere i metadati alza, il tick non deve morire: il giardiniere
    salterebbe per sempre su un guasto altrui."""
    _project(tmp_path, "viaggio")

    class _Angry:
        def read_session_metadata(self, key):
            raise RuntimeError("disco pieno")

    assert _pick(tmp_path, sessions=SimpleNamespace(
        read_session_metadata=_Angry().read_session_metadata
    )) is not None


# ── La distanza si misura sui tentativi, non sui successi ────────────────────


def test_a_pass_that_failed_is_not_retried_at_the_next_tick(tmp_path):
    """**Il test che conta di più di questo blocco.**

    Il cancello del fermo è aperto per costruzione — il giardiniere lavora
    *perché* il progetto è zitto — e il delta è ancora non letto, perché tenere il
    cursore fermo è la scelta giusta di una passata che non ha promosso tutto.
    Restava la distanza, e si misurava su ``last_run_at``, che solo un cursore che
    avanza scrive: una passata fallita lasciava il file di stato intatto e
    ripartiva identica ogni mezz'ora, per sempre. Misurate 48 volte in un giorno
    sullo stesso progetto, con un prompt intero ogni volta.
    """
    root = _project(tmp_path, "viaggio")
    write_state(root, GardenerState(
        last_attempt_at=(_NOW - timedelta(minutes=30)).isoformat(timespec="seconds"),
        failures=1,
    ))

    assert _pick(tmp_path) is None


def test_a_failed_pass_comes_back_once_the_distance_has_passed(tmp_path):
    """Il complemento: il tentativo **ritarda**, non esclude. Un progetto su cui
    la passata è andata storta va ritentato — dopo la distanza minima, come
    qualunque altro."""
    root = _project(tmp_path, "viaggio")
    write_state(root, GardenerState(
        last_attempt_at=(_NOW - timedelta(hours=7)).isoformat(timespec="seconds"),
        failures=3,
    ))

    assert _pick(tmp_path) is not None


def test_a_successful_pass_still_behaves_exactly_as_before(tmp_path):
    """Il controllo dell'altro verso: sul percorso riuscito non cambia niente.
    ``advanced`` scrive i due timbri insieme, quindi la distanza resta quella di
    prima — due ore bloccano, sette no."""
    for name, hours in (("fresco", 2), ("stantio", 7)):
        root = _project(tmp_path, name)
        stamp = (_NOW - timedelta(hours=hours)).isoformat(timespec="seconds")
        write_state(root, GardenerState(last_run_at=stamp, last_attempt_at=stamp))

    pick = _pick(tmp_path)

    assert pick is not None and pick.store.name == "stantio"


def test_a_project_that_keeps_failing_does_not_starve_the_others(tmp_path):
    """**L'altra metà del difetto, e non la copre il cancello della distanza.**

    Passata la distanza minima i due tornano entrambi candidati, e a quel punto
    decide l'ordine: ordinando per ``last_run_at`` il progetto che non ha mai
    *registrato* niente resta a ``None``, cioè primo a ogni giro, e quello sano
    fermo da tre giorni non arriva mai in cima. Con due progetti basta uno rotto
    per non giardinare più l'altro.
    """
    broken = _project(tmp_path, "rotto")
    write_state(broken, GardenerState(
        last_attempt_at=(_NOW - timedelta(hours=7)).isoformat(timespec="seconds"),
        failures=9,
    ))
    healthy = _project(tmp_path, "sano")
    stamp = (_NOW - timedelta(days=3)).isoformat(timespec="seconds")
    write_state(healthy, GardenerState(last_run_at=stamp, last_attempt_at=stamp))

    pick = _pick(tmp_path)

    assert pick is not None and pick.store.name == "sano"


def test_a_corrupt_run_stamp_does_not_hide_a_fresh_attempt(tmp_path):
    """Due orologi, e vince il più giovane: un ``last_run_at`` illeggibile non
    deve poter mangiarsi un tentativo valido di mezz'ora fa. Altrimenti un
    timestamp corrotto riaprirebbe la ripetizione che questo cancello chiude."""
    root = _project(tmp_path, "viaggio")
    write_state(root, GardenerState(
        last_run_at="non-una-data",
        last_attempt_at=(_NOW - timedelta(minutes=30)).isoformat(timespec="seconds"),
    ))

    assert _pick(tmp_path) is None


# ── Un orologio che è corso avanti ───────────────────────────────────────────
#
# Il caso vero è un telefono: RTC ripartito avanti dopo una batteria a zero,
# prima che NTP lo rimetta a posto; o un salto di fuso. Un'età negativa i due
# cancelli la leggevano come «recentissimo» — cioè il contrario — e il progetto
# non veniva più giardinato *mai*, con una riga DEBUG per tutta la spiegazione.


def test_a_run_stamp_in_the_future_does_not_freeze_the_project(tmp_path):
    """**Il test di questo blocco.** Un anno nel futuro dava distanza negativa,
    che è ``< 6h``, che è «troppo presto»: e resta troppo presto anche
    trecento giorni dopo, perché la data non arriva mai. Ora un timbro
    impossibile vale «non lo so», e «non lo so» non blocca."""
    root = _project(tmp_path, "viaggio")
    write_state(root, GardenerState(
        cursor={}, last_run_at=(_NOW + timedelta(days=365)).isoformat(timespec="seconds")
    ))

    assert _pick(tmp_path) is not None


def test_the_freeze_of_a_future_stamp_did_not_thaw_with_time(tmp_path):
    """Il congelamento non è un caso di confine: lo stesso stato, trecento giorni
    dopo, dava ancora ``None``. Il tempo che passa non lo scioglie."""
    root = _project(tmp_path, "viaggio")
    write_state(root, GardenerState(
        cursor={}, last_run_at=(_NOW + timedelta(days=365)).isoformat(timespec="seconds")
    ))

    assert pick_project(
        tmp_path, sessions=_Sessions(), now=_NOW + timedelta(days=300), **_OPEN
    ) is not None


def test_a_session_stamp_in_the_future_does_not_block_the_pass(tmp_path):
    """La stessa falla nel cancello del fermo: un ``updated_at`` nel futuro
    valeva «ha appena parlato», e valeva per sempre."""
    _project(tmp_path, "viaggio")
    sessions = _Sessions({"project:viaggio": _NOW + timedelta(hours=3)})

    assert _pick(tmp_path, sessions=sessions) is not None


def test_a_future_stamp_is_told_at_warning(tmp_path):
    """Un orologio corso avanti va **detto**: l'unica traccia era una riga DEBUG
    e un file di stato che su un telefono l'utente non raggiunge."""
    root = _project(tmp_path, "viaggio")
    stamp = (_NOW + timedelta(days=365)).isoformat(timespec="seconds")
    write_state(root, GardenerState(cursor={}, last_run_at=stamp))
    mod._future_stamps_seen.clear()
    seen: list[str] = []
    handler = logger.add(lambda message: seen.append(str(message)), level="WARNING")
    try:
        _pick(tmp_path)
    finally:
        logger.remove(handler)

    assert any(stamp in line and "future" in line for line in seen), seen


def test_a_future_stamp_is_told_once_not_at_every_tick(tmp_path):
    """Il tick batte ogni mezz'ora e l'orologio resta avanti fino a che qualcuno
    non lo sistema: una riga per tick smetterebbe di essere un avviso."""
    root = _project(tmp_path, "viaggio")
    write_state(root, GardenerState(
        cursor={}, last_run_at=(_NOW + timedelta(days=365)).isoformat(timespec="seconds")
    ))
    mod._future_stamps_seen.clear()
    seen: list[str] = []
    handler = logger.add(lambda message: seen.append(str(message)), level="WARNING")
    try:
        _pick(tmp_path)
        _pick(tmp_path)
        _pick(tmp_path)
    finally:
        logger.remove(handler)

    assert len([line for line in seen if "future" in line]) == 1, seen


def test_a_future_run_stamp_does_not_reopen_what_the_attempt_closed(tmp_path):
    """**L'interazione fra i due orologi**, che quando questo difetto è stato
    scritto non esisteva ancora. «Non lo so» non è «passa»: il timbro impossibile
    esce dal conto, e se l'altro orologio una data buona ce l'ha è quella a
    decidere. Senza questo, un RTC corso avanti trasformerebbe il congelamento a
    vita nel suo opposto — una passata ogni mezz'ora, che è il difetto che
    ``last_attempt_at`` ha appena chiuso."""
    root = _project(tmp_path, "viaggio")
    write_state(root, GardenerState(
        last_run_at=(_NOW + timedelta(days=365)).isoformat(timespec="seconds"),
        last_attempt_at=(_NOW - timedelta(minutes=30)).isoformat(timespec="seconds"),
    ))

    assert _pick(tmp_path) is None


def test_a_timezone_aware_stamp_is_unreadable_not_an_exception(tmp_path):
    """``fromisoformat`` un timbro col fuso lo legge benissimo; è la sottrazione
    da un ``now`` naïf che alza ``TypeError``, e ``except ValueError`` non lo
    prendeva — l'eccezione uscisse da ``pick_project`` fino al chiamante. Oggi
    niente scrive timbri con fuso: questo test tiene il file a mano perché il
    caso arriva da un file editato a mano."""
    root = _project(tmp_path, "viaggio")
    write_state(root, GardenerState(cursor={}, last_run_at="2026-08-23T20:30:00+02:00"))

    # Mezz'ora prima di ``_NOW`` in orologio da muro: se fosse letto bloccherebbe.
    assert _pick(tmp_path) is not None


# ── La mappa è la seconda ragione, e il suo freno ────────────────────────────


def _oversized_map(root: Path) -> int:
    """Una mappa oltre il tetto di iniezione, e restituisce la sua misura.

    Le proporzioni sono quelle vere: prosa in alto, elenco delle pagine in fondo
    (v. ``tests/agent/test_gardener.py::_oversized_map``, che lo argomenta). Qui
    serve solo che superi ``MAP_TARGET_CHARS``: quel che si prova in questo file è
    la *selezione*, non il ritaglio.
    """
    text = (
        "# progetto\n\n## Decided\n\n"
        + "Prosa che spetterebbe a una pagina. " * 70
        + "\n\n## Pages\n\n"
        + "\n".join(f"- [[pagina-{i:02d}]]" for i in range(20))
        + "\n"
    )
    (root / "wiki" / "index.md").write_text(text, encoding="utf-8")
    return len(text.strip())


def test_an_oversized_map_is_a_reason_to_garden_with_an_empty_journal(tmp_path):
    """**Il test di T3.5.** T3.4 ha insegnato alla passata a potare una mappa
    troppo grossa, e la misura del 23/08/2026 dice che quell'istruzione non
    sarebbe arrivata a nessun modello: le otto wiki vere hanno ``raw/journal/``
    **vuota** e sette mappe su otto oltre il tetto. Col solo delta come innesco,
    il produttore era riparato e l'artefatto no — e su un progetto che l'utente
    non sta usando «finché la cattura non scrive righe» vuol dire mai.
    """
    root = _project(tmp_path, "viaggio", lines=0)
    chars = _oversized_map(root)
    assert chars > MAP_TARGET_CHARS

    pick = _pick(tmp_path)

    assert pick is not None
    assert pick.store.name == "viaggio"
    assert pick.reason == "map" and pick.delta_lines == 0


def test_a_map_within_budget_and_an_empty_journal_is_still_nothing_to_do(tmp_path):
    """Il contro-limite, ed è quello che tiene il costo dov'era: senza di lui la
    seconda ragione diventa «gira sempre», cioè un turno LLM per progetto a ogni
    distanza minima su un'installazione che non ha niente da fare."""
    _project(tmp_path, "viaggio", lines=0)

    assert _pick(tmp_path) is None


def test_the_map_the_last_pass_left_is_not_a_reason_to_go_back(tmp_path):
    """**Il freno del livelock.** Una ragione che resta vera *dopo* la passata si
    ripresenta a ogni distanza minima per sempre, e nessuno dei due freni che
    c'erano la fermerebbe: il timbro del tentativo ritarda e non esclude (è il suo
    contratto), e ``failures`` non conta le potature a metà, perché quelle
    **committano** — cioè azzerano la serie. Lo ferma la misura che la passata
    lascia dietro: la mappa vale una passata solo se è più grossa di così.
    """
    root = _project(tmp_path, "viaggio", lines=0)
    chars = _oversized_map(root)
    write_state(root, GardenerState(map_left_at=chars))

    assert _pick(tmp_path) is None


def test_a_map_the_model_could_not_shrink_does_not_come_back_at_the_next_distance(tmp_path):
    """Lo stesso freno **con la distanza già passata**, che è il caso che conta.

    Sette ore dopo il tentativo il cancello della distanza è aperto, il fermo è
    aperto per costruzione e la mappa è ancora oltre il tetto: se il freno fosse
    solo il timbro, questo progetto tornerebbe adesso — e poi ogni sei ore, con lo
    stesso prompt e lo stesso esito, per sempre.
    """
    root = _project(tmp_path, "viaggio", lines=0)
    chars = _oversized_map(root)
    write_state(root, GardenerState(
        last_attempt_at=(_NOW - timedelta(hours=7)).isoformat(timespec="seconds"),
        failures=1,
        map_left_at=chars,
    ))

    assert _pick(tmp_path) is None


def test_a_map_that_grew_again_is_a_reason_again(tmp_path):
    """L'altro verso, senza il quale il freno sarebbe un blocco definitivo: una
    mappa che *ricresce* oltre quel che l'ultima passata ha lasciato è lavoro
    nuovo, non lo stesso lavoro, e va rifatta."""
    root = _project(tmp_path, "viaggio", lines=0)
    chars = _oversized_map(root)
    write_state(root, GardenerState(
        last_attempt_at=(_NOW - timedelta(hours=7)).isoformat(timespec="seconds"),
        map_left_at=chars - 1,
    ))

    pick = _pick(tmp_path)

    assert pick is not None and pick.reason == "map"


def test_the_map_is_not_even_measured_when_the_journal_has_work(tmp_path, monkeypatch):
    """L'ordine è il costo, anche per la ragione nuova: un progetto che ha già
    righe da promuovere è candidato, e misurargli la mappa sarebbe un file aperto
    per una domanda a cui la risposta non cambia niente."""
    _project(tmp_path, "viaggio")
    asked: list[str] = []

    def _spy(self, state=None):
        asked.append(self.name)
        return False

    monkeypatch.setattr(mod.GardenerStore, "map_needs_pruning", _spy)

    pick = _pick(tmp_path)

    assert pick is not None and pick.reason == "journal"
    assert asked == [], "la mappa è stata misurata per un progetto già candidato"


def test_a_shut_gate_still_stops_a_project_whose_map_is_oversized(tmp_path):
    """La ragione nuova non scavalca i cancelli: è un terzo argomento del terzo
    cancello, non una scorciatoia. Il fermo è quello che tiene utente e
    giardiniere lontani dalla stessa mappa — che è proprio il file che una passata
    per la mappa va a riscrivere."""
    root = _project(tmp_path, "viaggio", lines=0)
    _oversized_map(root)
    sessions = _Sessions({"project:viaggio": _NOW - timedelta(minutes=5)})

    assert _pick(tmp_path, sessions=sessions) is None
    assert _pick(tmp_path, active_session_keys=("project:viaggio",)) is None
