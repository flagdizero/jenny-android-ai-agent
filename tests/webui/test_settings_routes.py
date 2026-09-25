"""Test del router HTTP di WebUISettingsRouter (dispatch, auth, mapping errori).

``tests/webui/test_settings_api.py`` copre già la logica pura in
``jenny/webui/settings_api.py``; qui si copre invece lo strato di route:
dispatch per path, 401 senza token, propagazione degli errori applicativi
(400/404) e mapping degli errori inattesi a 500.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest
from support.gateway_http import make_request
from websockets.http11 import Request as WsRequest

from jenny.channels.http_utils import check_api_secret, http_error, http_json_response, parse_query
from jenny.config.loader import load_config, save_config
from jenny.config.schema import Config, ProviderConfig
from jenny.providers.factory import provider_fingerprint
from jenny.runtime.context import get_runtime_context
from jenny.webui.settings_routes import WebUISettingsRouter

_SECRET = "s3cr3t-settings"


def _request(path: str, token: str | None = _SECRET) -> WsRequest:
    return make_request(path, token)


def _router(**overrides) -> WebUISettingsRouter:
    kwargs: dict = dict(
        bus=MagicMock(),
        logger=MagicMock(),
        check_api_token=lambda request: check_api_secret(request.headers, request.path, _SECRET),
        parse_query=parse_query,
        json_response=http_json_response,
        error_response=http_error,
    )
    kwargs.update(overrides)
    return WebUISettingsRouter(**kwargs)


@pytest.fixture()
def config_path(tmp_path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "config.json"
    save_config(Config(), path)
    monkeypatch.setattr(get_runtime_context(), "config_path", path)
    return path


def _json(response) -> dict:
    return json.loads(response.body.decode("utf-8"))


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


async def test_dispatch_returns_none_for_unrelated_path() -> None:
    router = _router()
    result = await router.dispatch(_request("/api/unrelated"), "/api/unrelated")
    assert result is None


# ---------------------------------------------------------------------------
# /api/settings
# ---------------------------------------------------------------------------


async def test_settings_requires_auth(config_path) -> None:
    router = _router()
    response = await router.dispatch(
        _request("/api/settings", token=None), "/api/settings"
    )
    assert response.status_code == 401


async def test_settings_returns_payload(config_path) -> None:
    router = _router()
    response = await router.dispatch(_request("/api/settings"), "/api/settings")
    assert response.status_code == 200
    body = _json(response)
    assert "providers" in body
    assert "agent" in body


# ---------------------------------------------------------------------------
# /api/settings/update
# ---------------------------------------------------------------------------


async def test_settings_update_requires_auth(config_path) -> None:
    router = _router()
    response = await router.dispatch(
        _request("/api/settings/update", token=None), "/api/settings/update"
    )
    assert response.status_code == 401


async def test_settings_update_invalid_value_maps_to_400(config_path) -> None:
    router = _router()
    response = await router.dispatch(
        _request("/api/settings/update?context_window_tokens=128000"),
        "/api/settings/update",
    )
    assert response.status_code == 400


async def test_settings_update_fires_on_settings_changed_for_model(config_path) -> None:
    on_changed = MagicMock()
    router = _router(on_settings_changed=on_changed)
    response = await router.dispatch(
        _request("/api/settings/update?model=gpt-x"), "/api/settings/update"
    )
    assert response.status_code == 200
    on_changed.assert_called_once()


async def test_settings_update_fires_for_an_unrelated_field_without_changing_the_provider(
    config_path,
) -> None:
    """Scatta, e non ricostruisce niente — ed e' un cambio di garanzia.

    Prima questo test pretendeva che la callback **non** partisse per un campo
    estraneo, perche' la rotta sceglieva da un elenco di nomi scritto a mano. La
    garanzia che l'utente puo' notare non e' pero' quella: e' che il provider
    non venga ricostruito senza motivo. Adesso a deciderlo e' il fingerprint,
    che e' completo sullo schema, e la rotta non prova piu' a indovinare.

    L'impronta identica prima e dopo e' la prova che la guardia esce dal ramo
    anticipato senza toccare il provider vivo.
    """
    on_changed = MagicMock()
    router = _router(on_settings_changed=on_changed)
    before = provider_fingerprint(load_config(config_path))

    response = await router.dispatch(
        _request("/api/settings/update?timezone=Asia/Tokyo"), "/api/settings/update"
    )

    assert response.status_code == 200
    on_changed.assert_called_once()
    assert provider_fingerprint(load_config(config_path)) == before


async def test_settings_update_fires_for_the_context_window(config_path) -> None:
    """Il campo che l'elenco a mano si era dimenticato.

    ``context_window_tokens`` la rotta lo accetta e lo scrive, ma non era in
    ``_GENERATION_KEYS``: il gancio non partiva, la risposta non dichiarava
    ``requires_restart``, e l'agente vivo continuava con la finestra vecchia —
    che e' in memoria su ``AgentLoop``, ``AgentRunner`` e ``Consolidator``, e la
    aggiorna solo ``_apply_provider_switch``.
    """
    on_changed = MagicMock()
    router = _router(on_settings_changed=on_changed)
    before = provider_fingerprint(load_config(config_path))

    response = await router.dispatch(
        _request("/api/settings/update?context_window_tokens=262144"),
        "/api/settings/update",
    )

    assert response.status_code == 200
    on_changed.assert_called_once()
    assert load_config(config_path).agents.defaults.context_window_tokens == 262144
    # E stavolta l'impronta *cambia*: la guardia ricostruira' davvero.
    assert provider_fingerprint(load_config(config_path)) != before


async def test_settings_update_swallows_on_settings_changed_exception(config_path) -> None:
    logger = MagicMock()
    boom = MagicMock(side_effect=RuntimeError("kaboom"))
    router = _router(logger=logger, on_settings_changed=boom)
    response = await router.dispatch(
        _request("/api/settings/update?model=gpt-y"), "/api/settings/update"
    )
    # Il callback fallisce ma la risposta resta 200: il fallimento è solo loggato.
    assert response.status_code == 200
    logger.exception.assert_called_once()


# ---------------------------------------------------------------------------
# /api/settings/provider/update
# ---------------------------------------------------------------------------


async def test_provider_update_requires_auth(config_path) -> None:
    router = _router()
    response = await router.dispatch(
        _request("/api/settings/provider/update", token=None),
        "/api/settings/provider/update",
    )
    assert response.status_code == 401


async def test_provider_update_requires_name(config_path) -> None:
    router = _router()
    response = await router.dispatch(
        _request("/api/settings/provider/update?format=openai_compat"),
        "/api/settings/provider/update",
    )
    assert response.status_code == 400


async def test_provider_update_success_fires_settings_changed(config_path) -> None:
    on_changed = MagicMock()
    router = _router(on_settings_changed=on_changed)
    response = await router.dispatch(
        _request("/api/settings/provider/update?name=my-provider&api_key=sk-test"),
        "/api/settings/provider/update",
    )
    assert response.status_code == 200
    body = _json(response)
    providers = {p["name"]: p for p in body["providers"]}
    assert "my-provider" in providers
    on_changed.assert_called_once()


async def test_provider_update_of_an_inactive_provider_changes_nothing(config_path) -> None:
    """La condizione «solo se e' il default» non serve piu': decide l'impronta.

    Era un'altra cosa da tenere allineata a mano. ``provider_fingerprint``
    riassume il solo provider **attivo**, quindi toccarne uno inattivo lascia
    l'impronta identica e la guardia ritorna presto da sola: la callback parte,
    e il provider vivo non viene ricostruito.
    """
    config = load_config(config_path)
    config.providers.providers.append(
        ProviderConfig(name="attivo", format="openai_compat", api_key="sk-attivo")
    )
    config.providers.providers.append(
        ProviderConfig(name="dormiente", format="openai_compat", api_key="sk-1")
    )
    config.providers.default = "attivo"
    save_config(config, config_path)
    before = provider_fingerprint(load_config(config_path))

    on_changed = MagicMock()
    router = _router(on_settings_changed=on_changed)
    response = await router.dispatch(
        _request("/api/settings/provider/update?name=dormiente&api_key=sk-2"),
        "/api/settings/provider/update",
    )

    assert response.status_code == 200
    on_changed.assert_called_once()
    saved = load_config(config_path)
    assert {p.name: p.api_key for p in saved.providers.providers}["dormiente"] == "sk-2"
    assert provider_fingerprint(saved) == before


# ---------------------------------------------------------------------------
# /api/settings/provider/delete
# ---------------------------------------------------------------------------


async def test_provider_delete_requires_auth(config_path) -> None:
    router = _router()
    response = await router.dispatch(
        _request("/api/settings/provider/delete?name=x", token=None),
        "/api/settings/provider/delete",
    )
    assert response.status_code == 401


async def test_provider_delete_success_fires_settings_changed(config_path) -> None:
    config = load_config(config_path)
    config.providers.providers.append(
        ProviderConfig(name="to-delete", format="openai_compat", api_key="sk-1")
    )
    save_config(config, config_path)

    on_changed = MagicMock()
    router = _router(on_settings_changed=on_changed)
    response = await router.dispatch(
        _request("/api/settings/provider/delete?name=to-delete"),
        "/api/settings/provider/delete",
    )
    assert response.status_code == 200
    on_changed.assert_called_once()
    saved = load_config(config_path)
    assert all(p.name != "to-delete" for p in saved.providers.providers)


# ---------------------------------------------------------------------------
# /api/settings/provider-models
# ---------------------------------------------------------------------------


async def test_provider_models_requires_auth(config_path) -> None:
    router = _router()
    response = await router.dispatch(
        _request("/api/settings/provider-models", token=None),
        "/api/settings/provider-models",
    )
    assert response.status_code == 401


async def test_provider_models_settings_error_maps_to_status(config_path, monkeypatch) -> None:
    from jenny.webui.settings_routes import WebUISettingsError

    def boom(query):
        raise WebUISettingsError("provider sconosciuto", status=404)

    monkeypatch.setattr("jenny.webui.settings_routes.provider_models_payload", boom)
    router = _router()
    response = await router.dispatch(
        _request("/api/settings/provider-models?provider=openai"),
        "/api/settings/provider-models",
    )
    assert response.status_code == 404


async def test_provider_models_unexpected_error_maps_to_500(config_path, monkeypatch) -> None:
    def boom(query):
        raise RuntimeError("guasto inatteso")

    monkeypatch.setattr("jenny.webui.settings_routes.provider_models_payload", boom)
    router = _router()
    response = await router.dispatch(
        _request("/api/settings/provider-models?provider=openai"),
        "/api/settings/provider-models",
    )
    assert response.status_code == 500
    assert b"guasto inatteso" not in response.body


# ---------------------------------------------------------------------------
# /api/settings/web-search/update
# ---------------------------------------------------------------------------


async def test_web_search_update_requires_auth(config_path) -> None:
    router = _router()
    response = await router.dispatch(
        _request("/api/settings/web-search/update", token=None),
        "/api/settings/web-search/update",
    )
    assert response.status_code == 401


async def test_web_search_update_noop_returns_payload(config_path) -> None:
    # Regressione: la route leggeva ``config.android_web`` (inesistente, il
    # blocco vive in ``config.tools.android_web``) e QUALSIASI chiamata
    # esplodeva con AttributeError non gestita.
    router = _router()
    response = await router.dispatch(
        _request("/api/settings/web-search/update"),
        "/api/settings/web-search/update",
    )
    assert response.status_code == 200
    assert "web_search" in _json(response)


async def test_web_search_update_persists_values(config_path) -> None:
    router = _router()
    response = await router.dispatch(
        _request(
            "/api/settings/web-search/update"
            "?search_engine=bing&max_results=7&timeout=45&fetch_max_chars=20000"
        ),
        "/api/settings/web-search/update",
    )
    assert response.status_code == 200
    body = _json(response)["web_search"]
    assert body["search_engine"] == "bing"
    assert body["max_results"] == 7
    assert body["timeout"] == 45
    assert body["fetch_max_chars"] == 20000

    saved = load_config(config_path)
    assert saved.tools.android_web.search.max_results == 7
    assert saved.tools.android_web.search.timeout == 45
    assert saved.tools.android_web.fetch.max_chars == 20000


async def test_web_search_update_invalid_engine_maps_to_400(config_path) -> None:
    router = _router()
    response = await router.dispatch(
        _request("/api/settings/web-search/update?search_engine=altavista"),
        "/api/settings/web-search/update",
    )
    assert response.status_code == 400


async def test_web_search_update_unexpected_error_maps_to_500(config_path, monkeypatch) -> None:
    def boom(query):
        raise RuntimeError("guasto inatteso")

    monkeypatch.setattr("jenny.webui.settings_routes.update_web_search_settings", boom)
    router = _router()
    response = await router.dispatch(
        _request("/api/settings/web-search/update?timeout=45"),
        "/api/settings/web-search/update",
    )
    assert response.status_code == 500
    assert b"guasto inatteso" not in response.body


# ---------------------------------------------------------------------------
# /api/onboarding/save
# ---------------------------------------------------------------------------


async def test_onboarding_save_requires_auth(config_path) -> None:
    router = _router()
    response = await router.dispatch(
        _request("/api/onboarding/save", token=None), "/api/onboarding/save"
    )
    assert response.status_code == 401


async def test_onboarding_save_settings_error_maps_to_400(config_path) -> None:
    router = _router()
    response = await router.dispatch(
        _request(
            "/api/onboarding/save?provider_name=openai&format=openai_compat&model=gpt-x"
        ),
        "/api/onboarding/save",
    )
    assert response.status_code == 400


async def test_onboarding_save_success(config_path) -> None:
    router = _router(onboarding_event=asyncio.Event())
    response = await router.dispatch(
        _request(
            "/api/onboarding/save"
            "?provider_name=openai&format=openai_compat&model=gpt-x&api_key=sk-test-123"
        ),
        "/api/onboarding/save",
    )
    assert response.status_code == 200
    body = _json(response)
    assert body["chat_id"] == "default"


async def test_onboarding_save_unexpected_error_maps_to_500(config_path, monkeypatch) -> None:
    async def boom(*args, **kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setattr("jenny.webui.settings_routes.save_onboarding", boom)
    router = _router()
    response = await router.dispatch(
        _request(
            "/api/onboarding/save"
            "?provider_name=openai&format=openai_compat&model=gpt-x&api_key=sk-test-123"
        ),
        "/api/onboarding/save",
    )
    assert response.status_code == 500
    assert b"kaboom" not in response.body


async def test_settings_update_fires_on_settings_changed_for_generation_params(
    config_path,
) -> None:
    """I parametri di generazione vivono in ``provider.generation``, costruito una
    volta in ``factory.make_provider``: senza rebuild resterebbero scritti nel
    config e inerti fino al riavvio, e la UI non mostra ``requires_restart``."""
    for field in ("max_tokens=16384", "temperature=0.5", "reasoning_effort=medium"):
        on_changed = MagicMock()
        router = _router(on_settings_changed=on_changed)
        response = await router.dispatch(
            _request(f"/api/settings/update?{field}"), "/api/settings/update"
        )
        assert response.status_code == 200, field
        on_changed.assert_called_once()


# ---------------------------------------------------------------------------
# Il tronco condiviso: nessuna rotta di scrittura fa trapelare un'eccezione
# ---------------------------------------------------------------------------

# Le cinque rotte che scrivevano passando da un try/except scritto a mano, senza
# l'``except Exception`` che la docstring del tronco SSH dice di non dimenticare.
# Tutte e cinque chiamano ``store.mutate()``, cioè il disco: una config corrotta
# o un errore di scrittura risaliva oltre ``dispatch`` — che ha solo un
# ``finally`` — fino all'hook di handshake di ``websockets``.
_WRITING_ROUTES = [
    ("/api/settings/update", "update_agent_settings"),
    ("/api/settings/memory/update", "update_memory_settings"),
    ("/api/settings/workers/update", "update_worker_settings"),
    ("/api/settings/provider/update?name=p", "update_provider"),
    ("/api/settings/provider/delete?name=p", "delete_provider"),
]


@pytest.mark.parametrize(("path", "target"), _WRITING_ROUTES)
async def test_writing_routes_turn_an_unexpected_error_into_a_mute_500(
    config_path, monkeypatch: pytest.MonkeyPatch, path: str, target: str,
) -> None:
    async def boom(*args, **kwargs):
        raise RuntimeError("kaboom: /Users/someone/workspace/config.json")

    monkeypatch.setattr(f"jenny.webui.settings_routes.{target}", boom)
    router = _router()

    response = await router.dispatch(_request(path), path.split("?", 1)[0])

    assert response.status_code == 500
    # Il messaggio non deve finire nel corpo: qui porterebbe un percorso del
    # filesystem, che è esattamente ciò che la docstring del tronco teme.
    assert b"kaboom" not in response.body
    assert b"config.json" not in response.body


@pytest.mark.parametrize(
    ("path", "target", "worker"),
    [
        ("/api/settings/memory/update?enabled=true", "update_memory_settings", "dream"),
        ("/api/settings/workers/update?gardener_enabled=true", "update_worker_settings", "gardener"),
    ],
)
async def test_a_failed_write_does_not_rearm_any_job(
    config_path, monkeypatch: pytest.MonkeyPatch, path: str, target: str, worker: str,
) -> None:
    """Il rearm segue il salvataggio, non la richiesta.

    Ri-armare un job su una scrittura fallita annuncerebbe una pianificazione
    che il config non contiene.
    """
    async def boom(*args, **kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(f"jenny.webui.settings_routes.{target}", boom)
    on_jobs_changed = MagicMock()
    router = _router(on_jobs_changed=on_jobs_changed)

    response = await router.dispatch(_request(path), path.split("?", 1)[0])

    assert response.status_code == 500
    on_jobs_changed.assert_not_called()


# --- toggle del canale Telegram --------------------------------------------


def _telegram_configured(config_path, *, enabled: bool) -> None:
    """Config con token e chat accoppiata, nello stato di partenza richiesto."""
    config = Config()
    config.telegram.enabled = enabled
    config.telegram.bot_token = "123456789:AAtestTOKENtestTOKENtestTOKEN"
    config.telegram.paired_chat_id = "21824351"
    save_config(config, config_path)


async def test_telegram_update_requires_auth(config_path) -> None:
    router = _router()
    response = await router.dispatch(
        _request("/api/telegram/update?enabled=false", token=None), "/api/telegram/update"
    )
    assert response.status_code == 401


async def test_telegram_update_off_then_on_round_trips(config_path) -> None:
    """Il giro completo che prima era una porta a senso unico.

    Lo spegnimento e la riaccensione passano dalla *stessa* rotta, e in mezzo il
    pairing non si muove: e' il punto di tutto il lavoro.
    """
    _telegram_configured(config_path, enabled=True)
    router = _router(on_telegram_changed=MagicMock())

    off = await router.dispatch(
        _request("/api/telegram/update?enabled=false"), "/api/telegram/update"
    )
    assert off.status_code == 200
    assert _json(off)["enabled"] is False
    assert load_config().telegram.enabled is False

    on = await router.dispatch(
        _request("/api/telegram/update?enabled=true"), "/api/telegram/update"
    )
    assert on.status_code == 200
    assert _json(on)["enabled"] is True
    config = load_config()
    assert config.telegram.enabled is True
    assert config.telegram.paired_chat_id == "21824351"


async def test_telegram_update_fires_reload(config_path) -> None:
    """Senza questo il toggle scrive il file e il canale resta come stava."""
    _telegram_configured(config_path, enabled=True)
    on_changed = MagicMock()
    router = _router(on_telegram_changed=on_changed)
    response = await router.dispatch(
        _request("/api/telegram/update?enabled=false"), "/api/telegram/update"
    )
    assert response.status_code == 200
    on_changed.assert_called_once()


async def test_telegram_update_enable_without_token_maps_to_400(config_path) -> None:
    router = _router(on_telegram_changed=MagicMock())
    response = await router.dispatch(
        _request("/api/telegram/update?enabled=true"), "/api/telegram/update"
    )
    assert response.status_code == 400
    assert load_config().telegram.enabled is False


async def test_telegram_update_missing_param_switches_off(config_path) -> None:
    """``parse_flag`` e' vero solo se dichiarato vero: l'ambiguo spegne.

    Fissa il verso prudente, cosi' una richiesta storta non accende un canale
    che l'utente non ha chiesto.
    """
    _telegram_configured(config_path, enabled=True)
    router = _router(on_telegram_changed=MagicMock())
    response = await router.dispatch(
        _request("/api/telegram/update"), "/api/telegram/update"
    )
    assert response.status_code == 200
    assert load_config().telegram.enabled is False
