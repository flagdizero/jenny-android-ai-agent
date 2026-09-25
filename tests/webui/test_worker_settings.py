"""Le manopole dei due lavoratori periodici in Impostazioni.

Prima di questa superficie la copertura era a macchia di leopardo: Dream aveva i
tetti dentro ``/dream budget``, il giardiniere tutto dentro ``/gardener
settings``, e un terzo lavoratore, poi ritirato, niente — per spegnerlo si editava ``config.json`` a mano,
che è l'incidente da cui il blocco del giardiniere era nato (un ``sed -i`` che ha
rotto l'etichetta SELinux del file).

Cosa tengono chiuso questi test, oltre al giro leggi-scrivi:

- **i range vengono dallo schema**, non riscritti qui né nel modulo;
- **la schermata si apre comunque** se un file di memoria non è misurabile — è
  la schermata da cui si spengono i lavoratori, quindi non può dipendere dalla
  salute di ``SOUL.md``;
- **spegnere funziona da una config che lo schema di oggi boccerebbe**, che è la
  sola strada per cui la via d'uscita non deve mai fallire;
- il pavimento della cadenza di review resta lato server, dialogo o no.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from support.gateway_http import make_request
from websockets.http11 import Request as WsRequest

from jenny.channels.http_utils import check_api_secret, http_error, http_json_response, parse_query
from jenny.config.loader import load_config, save_config
from jenny.config.schema import Config, DreamConfig, GardenerConfig
from jenny.runtime.context import get_runtime_context
from jenny.webui import worker_settings
from jenny.webui.settings_api import WebUISettingsError, settings_payload
from jenny.webui.settings_routes import WebUISettingsRouter
from jenny.webui.worker_settings import (
    REVIEW_CADENCE_FLOOR,
    update_memory_settings,
    update_worker_settings,
)

_SECRET = "s3cr3t-workers"


@pytest.fixture
def config_path(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Config e **workspace** privati del test.

    Il workspace va isolato e non solo la config: ``config.workspace_path``
    risolve dal ``RuntimeContext``, non dal file, quindi senza questa riga le
    misure leggerebbero — e i test scriverebbero dentro — il workspace condiviso
    da tutta la suite (``tests/conftest.py``, che ci sincronizza i template:
    ``SOUL.md`` esiste già lì).
    """
    path = tmp_path / "config.json"
    save_config(Config(), path)
    monkeypatch.setattr(get_runtime_context(), "config_path", path)
    monkeypatch.setattr(get_runtime_context(), "workspace_dir", tmp_path / "workspace")
    return path


# -- lettura -----------------------------------------------------------------


def test_payload_carries_both_sections(config_path) -> None:
    payload = settings_payload()

    assert payload["memory"]["enabled"] is True
    assert payload["workers"]["gardener"]["enabled"] is True
    assert payload["workers"]["compact_projects_when_idle"] is False


def test_the_dead_runtime_fields_are_gone(config_path) -> None:
    """``runtime.dream`` (e il gemello poi ritirato) erano serviti e mai disegnati.

    Due verità sullo stesso oggetto sono la premessa di una divergenza: ora
    quelle informazioni stanno nelle due sezioni che la UI legge davvero.
    """
    runtime = settings_payload()["runtime"]

    assert "dream" not in runtime


@pytest.mark.parametrize(
    ("section", "field", "model", "attr"),
    [
        ("gardener", "interval_min", GardenerConfig, "interval_min"),
        ("gardener", "idle_min", GardenerConfig, "idle_min"),
        ("gardener", "min_hours_between_passes", GardenerConfig, "min_hours_between_passes"),
    ],
)
def test_every_number_carries_the_bounds_of_the_schema(
    config_path, section: str, field: str, model: type, attr: str
) -> None:
    """Il range non è riscritto da nessuna parte: si legge da ``model_fields``.

    Un range scritto due volte diventa due range appena uno dei due si muove, ed
    è precisamente il numero che si racconta all'utente nel rifiuto.
    """
    info = model.model_fields[attr]
    payload = settings_payload()["workers"][section][field]

    assert payload["min"] == (int(info.ge) if info.ge is not None else None)
    assert payload["max"] == (int(info.le) if info.le is not None else None)


def test_memory_numbers_carry_their_bounds_too(config_path) -> None:
    memory = settings_payload()["memory"]

    assert memory["review_every_runs"]["min"] == int(
        DreamConfig.model_fields["review_every_runs"].ge
    )
    # Nessun tetto sui budget: lo schema non ne impone uno, e inventarne uno qui
    # sarebbe il secondo range.
    assert memory["memory_budget_chars"]["max"] is None


def test_the_review_floor_travels_with_the_payload(config_path) -> None:
    """Il dialogo di conferma lo fa il client: senza il numero terrebbe una copia sua."""
    assert settings_payload()["memory"]["review_floor"] == REVIEW_CADENCE_FLOOR


def test_files_are_measured_in_render_order_and_say_which_exist(config_path) -> None:
    """``count_chars`` ritorna ``0`` sia per un file vuoto sia per uno assente.

    Senza ``exists`` accanto, "mai scritto" e "vuoto" si leggerebbero uguale.
    """
    workspace = Path(load_config(config_path).workspace_path)
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "USER.md").write_text("undici char", encoding="utf-8")

    files = settings_payload()["memory"]["files"]

    assert [entry["label"] for entry in files] == ["MEMORY.md", "USER.md", "SOUL.md"]
    user = next(entry for entry in files if entry["label"] == "USER.md")
    soul = next(entry for entry in files if entry["label"] == "SOUL.md")
    assert user["exists"] is True and user["chars"] == 11
    assert soul["exists"] is False and soul["chars"] == 0


def test_a_file_that_exists_but_cannot_be_opened_is_not_reported_as_empty(
    config_path,
) -> None:
    """Misurato sul telefono il 31/08/2026 con un ``chmod 000``.

    ``count_chars`` ingoia l'``OSError`` e ritorna ``0``, quindi il payload
    diceva «0 caratteri su 3.000» per un file da 2.407 byte: la schermata si
    apriva — che era il punto — ma quel numero era una bugia. ``readable`` la
    toglie, e la riga sotto la barra dice "non misurabile".
    """
    workspace = Path(load_config(config_path).workspace_path)
    workspace.mkdir(parents=True, exist_ok=True)
    soul = workspace / "SOUL.md"
    soul.write_text("x" * 500, encoding="utf-8")
    soul.chmod(0o000)
    try:
        files = settings_payload()["memory"]["files"]
    finally:
        soul.chmod(0o600)

    entry = next(f for f in files if f["label"] == "SOUL.md")
    assert entry["exists"] is True
    assert entry["readable"] is False


def test_a_file_that_cannot_be_measured_does_not_close_the_screen(
    config_path, monkeypatch
) -> None:
    """La misura salta, il resto no: qui si spengono i lavoratori."""
    import jenny.agent.memory_budget as memory_budget

    def _boom(*_args, **_kwargs):
        raise OSError("disco andato")

    monkeypatch.setattr(memory_budget, "budget_report", _boom)

    payload = settings_payload()

    assert payload["memory"]["files"] is None
    assert payload["memory"]["enabled"] is True
    assert payload["workers"]["gardener"]["enabled"] is True


# -- scrittura ---------------------------------------------------------------


async def test_a_gardener_number_is_persisted(config_path) -> None:
    payload = await update_worker_settings({"gardener_idle_min": ["45"]})

    assert payload["workers"]["gardener"]["idle_min"]["value"] == 45
    assert load_config(config_path).agents.defaults.gardener.idle_min == 45


async def test_camel_case_alias_is_accepted(config_path) -> None:
    await update_worker_settings({"gardenerIdleMin": ["45"]})

    assert load_config(config_path).agents.defaults.gardener.idle_min == 45


async def test_dream_can_be_turned_off_too(config_path) -> None:
    await update_memory_settings({"dream_enabled": ["false"]})

    assert load_config(config_path).agents.defaults.dream.enabled is False


async def test_a_budget_is_persisted(config_path) -> None:
    await update_memory_settings({"soul_budget_chars": ["4000"]})

    assert load_config(config_path).agents.defaults.dream.soul_budget_chars == 4000


async def test_a_request_without_a_field_leaves_it_alone(config_path) -> None:
    await update_worker_settings({"gardener_idle_min": ["45"]})
    await update_worker_settings({"gardener_interval_min": ["10"]})

    gardener = load_config(config_path).agents.defaults.gardener
    assert (gardener.idle_min, gardener.interval_min) == (45, 10)


async def test_a_value_that_did_not_change_does_not_rewrite_the_file(
    config_path, monkeypatch
) -> None:
    """Il ``.bak`` non deve ruotare per nulla: era il comportamento del comando."""
    from jenny.config import store

    writes: list[str] = []
    real_save = store.save_config

    def counting_save(*args, **kwargs):
        writes.append("save")
        return real_save(*args, **kwargs)

    monkeypatch.setattr(store, "save_config", counting_save)

    await update_worker_settings({"gardener_idle_min": ["45"]})
    assert writes == ["save"]

    await update_worker_settings({"gardener_idle_min": ["45"]})
    assert writes == ["save"]


async def test_compact_asks_for_a_restart_and_only_when_it_changes(config_path) -> None:
    """La legge l'agente quando parte: prima del riavvio non cambia niente."""
    assert (await update_worker_settings({"compact_projects_when_idle": ["1"]}))[
        "requires_restart"
    ] is True
    assert (await update_worker_settings({"compact_projects_when_idle": ["1"]}))[
        "requires_restart"
    ] is False


async def test_turning_the_gardener_off_works_from_an_out_of_range_config(config_path) -> None:
    """La via d'uscita non deve mai fallire.

    Un ``intervalMin`` fuori dai tetti scritto da una versione precedente non
    blocca lo spegnimento: ``GardenerConfig.clamp_raw`` riporta quei numeri
    dentro i bound al parse, e ``store.mutate`` rilegge il file dentro il proprio
    lock — quindi la clemenza vale anche per questa scrittura.
    """
    raw = json.loads(config_path.read_text("utf-8"))
    raw.setdefault("agents", {}).setdefault("defaults", {})["gardener"] = {
        "enabled": True,
        "intervalMin": 99999,
    }
    config_path.write_text(json.dumps(raw), encoding="utf-8")

    await update_worker_settings({"gardener_enabled": ["0"]})

    assert load_config(config_path).agents.defaults.gardener.enabled is False


# -- rifiuti -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value", "span"),
    [
        ("gardener_idle_min", "99999", "0–1440"),
        ("gardener_interval_min", "0", "1–1440"),
        ("gardener_min_hours_between_passes", "-1", "0–8760"),
    ],
)
async def test_out_of_range_is_refused_and_names_the_range(
    config_path, field: str, value: str, span: str
) -> None:
    """Il range nel messaggio è l'unico modo in cui chi ha sforato scopre il valore ammesso."""
    with pytest.raises(WebUISettingsError, match=span):
        await update_worker_settings({field: [value]})


async def test_a_refused_value_leaves_the_config_untouched(config_path) -> None:
    await update_worker_settings({"gardener_idle_min": ["45"]})
    with pytest.raises(WebUISettingsError):
        await update_worker_settings({"gardener_idle_min": ["99999"]})

    assert load_config(config_path).agents.defaults.gardener.idle_min == 45


@pytest.mark.parametrize("bad", ["", "molto", "12.5", "1e3"])
async def test_a_number_that_is_not_a_number_is_refused(config_path, bad: str) -> None:
    with pytest.raises(WebUISettingsError, match="whole number"):
        await update_memory_settings({"memory_budget_chars": [bad]})


@pytest.mark.parametrize("bad", ["", "forse", "2"])
async def test_a_boolean_that_is_not_a_boolean_is_refused(config_path, bad: str) -> None:
    with pytest.raises(WebUISettingsError, match="boolean"):
        await update_worker_settings({"gardener_enabled": [bad]})


async def test_the_review_floor_needs_an_explicit_confirmation(config_path) -> None:
    with pytest.raises(WebUISettingsError, match=str(REVIEW_CADENCE_FLOOR)):
        await update_memory_settings({"review_every_runs": ["1"]})

    assert load_config(config_path).agents.defaults.dream.review_every_runs == 12


async def test_the_confirmation_lets_it_through(config_path) -> None:
    """Il percorso esiste perché misurare su un device vero è come si è scoperto il difetto."""
    await update_memory_settings(
        {"review_every_runs": ["1"], "confirm_back_to_back": ["1"]}
    )

    assert load_config(config_path).agents.defaults.dream.review_every_runs == 1


def test_the_floor_is_twelve_and_equals_the_shipped_default() -> None:
    """Pavimento e default devono restare lo stesso numero.

    Un pavimento sotto il default sarebbe una zona in cui la manopola scrive un
    valore che il piano della memoria non vuole; uno sopra renderebbe il default
    stesso irraggiungibile senza conferma.
    """
    assert REVIEW_CADENCE_FLOOR == 12
    assert DreamConfig().review_every_runs == REVIEW_CADENCE_FLOOR


def test_the_schema_still_accepts_a_restored_value_below_the_floor() -> None:
    """Il pavimento vive in questa superficie *perché* lo schema non deve alzarlo.

    Un ``ge=12`` renderebbe illeggibile un ``config.json`` con
    ``reviewEveryRuns: 1``: ``loader._load_with_recovery`` proverebbe il ``.bak``
    — stesso valore — e poi metterebbe il file in quarantena ripartendo dai
    default, provider e chiave API inclusi.
    """
    assert DreamConfig(review_every_runs=1).review_every_runs == 1
    assert DreamConfig.model_validate({"reviewEveryRuns": 1}).review_every_runs == 1


async def test_a_cadence_at_the_floor_needs_no_confirmation(config_path) -> None:
    await update_memory_settings({"review_every_runs": [str(REVIEW_CADENCE_FLOOR)]})

    assert (
        load_config(config_path).agents.defaults.dream.review_every_runs
        == REVIEW_CADENCE_FLOOR
    )


# -- funnel della config -----------------------------------------------------


def test_module_never_calls_save_config_directly() -> None:
    """Fuori da ``store.mutate`` una riscrittura intera cancella in silenzio quella di un altro."""
    source = Path(worker_settings.__file__).read_text("utf-8")

    assert "save_config" not in source


async def test_an_unknown_config_key_survives_the_write(config_path) -> None:
    """Il test che muore se qualcuno rimpiazza ``mutate()`` con ``save_config()``.

    ``save_config`` riscrive il file intero dal dump dello schema: senza
    ``preserve_unknown_from`` — che passa solo ``store.mutate`` — ogni chiave che
    questa versione non conosce viene cancellata. Sono le impostazioni di una
    versione più nuova, e su un telefono che ha aggiornato e poi è tornato
    indietro sparirebbero al primo tocco di una di queste manopole.

    Portato qui da ``tests/command/test_gardener_settings_command.py`` insieme
    alla manopola: la promessa è del funnel, non della superficie che lo usa.
    """
    raw = json.loads(config_path.read_text("utf-8"))
    raw["somethingFromANewerVersion"] = {"keep": "me"}
    config_path.write_text(json.dumps(raw, indent=2), encoding="utf-8")

    await update_worker_settings({"gardener_interval_min": ["120"]})

    saved = json.loads(config_path.read_text("utf-8"))
    assert saved["somethingFromANewerVersion"] == {"keep": "me"}
    assert saved["agents"]["defaults"]["gardener"]["intervalMin"] == 120


async def test_the_write_keeps_the_file_private(config_path) -> None:
    """Nel file c'è la chiave del provider: un ``config.json`` leggibile a tutti è
    una regressione di sicurezza silenziosa. Il ``chmod 600`` lo ripristina
    ``save_config`` in fondo al funnel — questo test copre la strada che un
    ``chmod`` fatto a mano fuori dal funnel romperebbe (è già successo: un
    ``sed -i`` sul telefono)."""
    config_path.chmod(0o600)

    await update_worker_settings({"gardener_interval_min": ["120"]})

    assert config_path.stat().st_mode & 0o777 == 0o600


async def test_a_read_only_request_rotates_no_backup(config_path) -> None:
    """Una richiesta che non cambia niente non deve far ruotare il ``.bak``: il
    backup è la rete di salvataggio di ``config.json``, e riempirlo con copie
    identiche vuol dire perdere la sola versione che serviva."""
    from jenny.config.loader import _backup_path

    before = config_path.read_bytes()

    await update_worker_settings({})

    assert config_path.read_bytes() == before
    assert not _backup_path(config_path).exists()


# -- rotte e ri-armo ---------------------------------------------------------


def _request(path: str, token: str | None = _SECRET) -> WsRequest:
    return make_request(path, token, always_append=True)


def _router(on_jobs_changed=None) -> WebUISettingsRouter:
    return WebUISettingsRouter(
        bus=MagicMock(),
        logger=MagicMock(),
        check_api_token=lambda request: check_api_secret(request.headers, request.path, _SECRET),
        parse_query=parse_query,
        json_response=http_json_response,
        error_response=http_error,
        on_jobs_changed=on_jobs_changed,
    )


@pytest.mark.parametrize(
    "path",
    ["/api/settings/memory/update", "/api/settings/workers/update"],
)
async def test_routes_require_auth(config_path, path: str) -> None:
    response = await _router().dispatch(_request(f"{path}?gardener_enabled=0", token=None), path)

    assert response.status_code == 401
    assert load_config(config_path).agents.defaults.gardener.enabled is True


async def test_route_persists_and_returns_the_payload(config_path) -> None:
    response = await _router().dispatch(
        _request("/api/settings/workers/update?gardener_idle_min=45"),
        "/api/settings/workers/update",
    )

    assert response.status_code == 200
    assert json.loads(response.body)["workers"]["gardener"]["idle_min"]["value"] == 45


async def test_a_refusal_becomes_a_400(config_path) -> None:
    response = await _router().dispatch(
        _request("/api/settings/workers/update?gardener_idle_min=99999"),
        "/api/settings/workers/update",
    )

    assert response.status_code == 400


@pytest.mark.parametrize(
    ("path", "query", "expected"),
    [
        ("/api/settings/memory/update", "dream_enabled=0", ["dream"]),
        ("/api/settings/memory/update", "dream_interval_h=4", ["dream"]),
        ("/api/settings/workers/update", "gardener_enabled=0", ["gardener"]),
        ("/api/settings/workers/update", "gardener_interval_min=10", ["gardener"]),
    ],
)
async def test_the_job_is_re_armed_for_what_lives_in_the_cron_store(
    config_path, path: str, query: str, expected: list[str]
) -> None:
    """Interruttore e intervallo non li rilegge nessun tick: stanno nello store del cron.

    E il caso peggiore è il lavoratore spento all'avvio, il cui job non è
    nemmeno registrato: riaccenderlo scriverebbe un ``enabled=True`` che nessuno
    va a leggere.
    """
    seen: list[str] = []

    await _router(on_jobs_changed=seen.append).dispatch(_request(f"{path}?{query}"), path)

    assert seen == expected


@pytest.mark.parametrize(
    ("path", "query"),
    [
        ("/api/settings/memory/update", "memory_budget_chars=4000"),
        ("/api/settings/memory/update", "review_every_runs=24"),
        ("/api/settings/workers/update", "gardener_idle_min=45"),
        ("/api/settings/workers/update", "gardener_min_hours_between_passes=12"),
        ("/api/settings/workers/update", "compact_projects_when_idle=1"),
    ],
)
async def test_what_every_tick_rereads_does_not_re_arm_anything(
    config_path, path: str, query: str
) -> None:
    """Ri-armare per un numero che il dispatch già rilegge sposterebbe la prossima
    scadenza a ogni tocco di una manopola che non c'entra."""
    seen: list[str] = []

    await _router(on_jobs_changed=seen.append).dispatch(_request(f"{path}?{query}"), path)

    assert seen == []


async def test_a_failing_hook_does_not_break_the_write(config_path) -> None:
    """Il valore è già scritto: il ri-armo è il contorno, non il piatto."""

    def _boom(_worker: str) -> None:
        raise RuntimeError("cron andato")

    response = await _router(on_jobs_changed=_boom).dispatch(
        _request("/api/settings/workers/update?gardener_enabled=0"),
        "/api/settings/workers/update",
    )

    assert response.status_code == 200
    assert load_config(config_path).agents.defaults.gardener.enabled is False
