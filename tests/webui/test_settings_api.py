from __future__ import annotations

import httpx
import pytest

from jenny.config.loader import load_config, save_config
from jenny.config.schema import Config, ProviderConfig
from jenny.runtime.context import get_runtime_context
from jenny.webui.settings_api import (
    WebUISettingsError,
    normalize_api_base,
    provider_models_payload,
    save_onboarding,
    settings_payload,
    update_agent_settings,
    update_provider,
)

DYNAMIC_PROVIDER_NAME = "my-company-api"
DYNAMIC_PROVIDER_API_BASE = "https://example.test/v1"


def _add_provider(config: Config, name: str, format: str = "openai_compat", **kwargs) -> Config:
    config.providers.providers.append(ProviderConfig(name=name, format=format, **kwargs))
    return config


@pytest.mark.asyncio
async def test_update_provider_updates_dynamic_custom_provider(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config = _add_provider(Config(), DYNAMIC_PROVIDER_NAME, api_base="https://old.example/v1")
    save_config(config, config_path)
    monkeypatch.setattr(get_runtime_context(), "config_path", config_path)

    payload = await update_provider({
        "name": DYNAMIC_PROVIDER_NAME,
        "api_base": "https://new.example/v1",
        # Deliberatamente non somiglia a una chiave vera: un valore in stile
        # `sk-...` con code alto fa scattare i secret scanner su un repo
        # pubblico, e tre falsi positivi nei test si leggono come sciatteria.
        "api_key": "not-a-real-credential",
    })

    providers = {row["name"]: row for row in payload["providers"]}
    assert providers[DYNAMIC_PROVIDER_NAME]["api_base"] == "https://new.example/v1"
    # L'hint è `api_key[:4] + "..." + api_key[-4:]` (settings_api.py).
    assert providers[DYNAMIC_PROVIDER_NAME]["api_key_hint"] == "not-...tial"
    saved = load_config(config_path)
    provider_entry = next(p for p in saved.providers.providers if p.name == DYNAMIC_PROVIDER_NAME)
    assert provider_entry.api_base == "https://new.example/v1"
    assert provider_entry.api_key == "not-a-real-credential"


@pytest.mark.asyncio
@pytest.mark.parametrize("blank", ["", "   ", None])
async def test_update_provider_blank_api_key_keeps_stored_key(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    blank: str | None,
) -> None:
    """Campo chiave vuoto = "tieni quella salvata", non "cancellala"."""
    config_path = tmp_path / "config.json"
    config = _add_provider(
        Config(),
        DYNAMIC_PROVIDER_NAME,
        api_key="sk-real-secret-key-12345",
        api_base="https://old.example/v1",
    )
    save_config(config, config_path)
    monkeypatch.setattr(get_runtime_context(), "config_path", config_path)

    payload_in: dict[str, object] = {
        "name": DYNAMIC_PROVIDER_NAME,
        "api_base": "https://new.example/v1",
    }
    if blank is not None:
        payload_in["api_key"] = blank

    payload = await update_provider(payload_in)

    saved = next(
        p for p in load_config(config_path).providers.providers if p.name == DYNAMIC_PROVIDER_NAME
    )
    assert saved.api_key == "sk-real-secret-key-12345"
    assert saved.api_base == "https://new.example/v1"
    providers = {row["name"]: row for row in payload["providers"]}
    assert providers[DYNAMIC_PROVIDER_NAME]["api_key_hint"] == "sk-r...2345"


@pytest.mark.asyncio
async def test_update_provider_ignores_masked_api_key_hint(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Il suggerimento offuscato rimandato indietro non sovrascrive la chiave.

    Guardia contro un client vecchio che pre-compila il campo con l'hint
    (`sk-r...2345`) e lo rispedisce identico al salvataggio.
    """
    config_path = tmp_path / "config.json"
    config = _add_provider(
        Config(),
        DYNAMIC_PROVIDER_NAME,
        api_key="sk-real-secret-key-12345",
        api_base=DYNAMIC_PROVIDER_API_BASE,
    )
    save_config(config, config_path)
    monkeypatch.setattr(get_runtime_context(), "config_path", config_path)

    hint = settings_payload()["providers"][0]["api_key_hint"]
    assert hint == "sk-r...2345"

    await update_provider({"name": DYNAMIC_PROVIDER_NAME, "api_key": hint})

    saved = next(
        p for p in load_config(config_path).providers.providers if p.name == DYNAMIC_PROVIDER_NAME
    )
    assert saved.api_key == "sk-real-secret-key-12345"


@pytest.mark.asyncio
async def test_update_provider_still_replaces_key_when_retyped(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config = _add_provider(Config(), DYNAMIC_PROVIDER_NAME, api_key="sk-old-secret-key-0000")
    save_config(config, config_path)
    monkeypatch.setattr(get_runtime_context(), "config_path", config_path)

    await update_provider({"name": DYNAMIC_PROVIDER_NAME, "api_key": "sk-new-secret-key-9999"})

    saved = next(
        p for p in load_config(config_path).providers.providers if p.name == DYNAMIC_PROVIDER_NAME
    )
    assert saved.api_key == "sk-new-secret-key-9999"


@pytest.mark.asyncio
async def test_a_key_saved_without_a_format_keeps_the_provider_anthropic(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La casa salva la chiave con ``{name, api_key}`` e basta: il predefinito
    ``openai_compat`` finiva scritto sul provider, e un Anthropic smetteva di
    rispondere. Assente = invariato; il predefinito vale per un provider nuovo."""
    config_path = tmp_path / "config.json"
    config = _add_provider(Config(), "claude", format="anthropic", api_key="not-a-real-credential")
    save_config(config, config_path)
    monkeypatch.setattr(get_runtime_context(), "config_path", config_path)

    await update_provider({"name": "claude", "api_key": "another-fake-credential"})
    await update_provider({"name": "fresh", "api_key": "a-third-fake-credential"})

    saved = {p.name: p for p in load_config(config_path).providers.providers}
    assert saved["claude"].format == "anthropic"
    assert saved["claude"].api_key == "another-fake-credential"
    assert saved["fresh"].format == "openai_compat"


async def test_update_agent_settings_accepts_context_window_options(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config = Config()
    save_config(config, config_path)
    monkeypatch.setattr(get_runtime_context(), "config_path", config_path)

    payload = await update_agent_settings({"context_window_tokens": ["262144"]})

    assert payload["agent"]["context_window_tokens"] == 262144
    saved = load_config(config_path)
    assert saved.agents.defaults.context_window_tokens == 262144


async def test_update_context_window_rejects_unknown_values(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    save_config(Config(), config_path)
    monkeypatch.setattr(get_runtime_context(), "config_path", config_path)

    with pytest.raises(WebUISettingsError, match="context_window_tokens must be 65536 or 131072 or 262144"):
        await update_agent_settings({"context_window_tokens": ["128000"]})


def test_the_context_window_refusal_names_the_options_it_has(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jenny.webui import settings_api

    monkeypatch.setattr(settings_api, "_CONTEXT_WINDOW_TOKEN_OPTIONS", (65_536, 131_072, 262_144))
    with pytest.raises(WebUISettingsError, match="65536 or 131072 or 262144"):
        settings_api._parse_context_window_tokens("1000")


async def test_update_timezone_rejects_unknown_name(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regressione: la validazione era un no-op (safe_zoneinfo senza controllo).
    config_path = tmp_path / "config.json"
    save_config(Config(), config_path)
    monkeypatch.setattr(get_runtime_context(), "config_path", config_path)

    with pytest.raises(WebUISettingsError, match="unknown timezone 'Not/AZone'"):
        await update_agent_settings({"timezone": ["Not/AZone"]})


async def test_update_timezone_accepts_valid_name_and_requires_restart(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    save_config(Config(), config_path)
    monkeypatch.setattr(get_runtime_context(), "config_path", config_path)

    payload = await update_agent_settings({"timezone": ["Asia/Shanghai"]})

    assert payload["requires_restart"] is True
    saved = load_config(config_path)
    assert saved.agents.defaults.timezone == "Asia/Shanghai"


def test_settings_payload_includes_dynamic_custom_provider(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config = _add_provider(Config(), DYNAMIC_PROVIDER_NAME, api_base=DYNAMIC_PROVIDER_API_BASE)
    save_config(config, config_path)
    monkeypatch.setattr(get_runtime_context(), "config_path", config_path)

    payload = settings_payload()
    providers = {row["name"]: row for row in payload["providers"]}

    assert providers[DYNAMIC_PROVIDER_NAME]["configured"] is True
    assert providers[DYNAMIC_PROVIDER_NAME]["api_base"] == DYNAMIC_PROVIDER_API_BASE


def test_settings_payload_marks_dynamic_custom_provider_without_api_base_unconfigured(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config = _add_provider(Config(), DYNAMIC_PROVIDER_NAME, api_key="sk-test")
    save_config(config, config_path)
    monkeypatch.setattr(get_runtime_context(), "config_path", config_path)

    payload = settings_payload()
    providers = {row["name"]: row for row in payload["providers"]}

    assert providers[DYNAMIC_PROVIDER_NAME]["configured"] is True


def test_settings_payload_includes_token_usage_summary(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config = Config()
    save_config(config, config_path)
    monkeypatch.setattr(get_runtime_context(), "config_path", config_path)
    monkeypatch.setattr("jenny.agent.token_usage.get_webui_dir", lambda: tmp_path / "webui")

    from jenny.agent.token_usage import record_token_usage

    record_token_usage({"prompt_tokens": 10, "completion_tokens": 5})

    payload = settings_payload()

    assert payload["usage"]["total_tokens_30d"] == 15
    assert payload["usage"]["total_tokens"] == 15
    assert payload["usage"]["peak_day_tokens"] == 15
    assert payload["usage"]["current_streak_days"] == 1
    assert payload["usage"]["longest_streak_days"] == 1
    assert payload["usage"]["active_days_30d"] == 1
    assert payload["usage"]["requests_30d"] == 1


def test_provider_models_payload_fetches_openai_compatible_models(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config = _add_provider(Config(), "deepseek", api_key="sk-test", api_base="https://api.deepseek.com")
    save_config(config, config_path)
    monkeypatch.setattr(get_runtime_context(), "config_path", config_path)

    def fake_get(url: str, **kwargs):
        assert url == "https://api.deepseek.com/models"
        assert kwargs["headers"]["Authorization"] == "Bearer sk-test"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "deepseek-chat", "owned_by": "deepseek"},
                    {"id": "deepseek-reasoner", "context_window": 65536},
                ]
            },
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr("jenny.webui.settings_api.httpx.get", fake_get)

    payload = provider_models_payload({"provider": ["deepseek"]})

    assert payload["status"] == "available"
    assert payload["model_count"] == 2
    assert payload["models"][0]["id"] == "deepseek-chat"
    assert payload["models"][1]["context_window"] == 65536


def test_provider_models_payload_fetches_dynamic_custom_provider_models(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config = _add_provider(Config(), DYNAMIC_PROVIDER_NAME, api_base=DYNAMIC_PROVIDER_API_BASE, api_key="dp-test")
    save_config(config, config_path)
    monkeypatch.setattr(get_runtime_context(), "config_path", config_path)

    def fake_get(url: str, **kwargs):
        assert url == f"{DYNAMIC_PROVIDER_API_BASE}/models"
        assert kwargs["headers"]["Authorization"] == "Bearer dp-test"
        return httpx.Response(
            200,
            json={"data": [{"id": "custom-gpt", "owned_by": "example"}]},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr("jenny.webui.settings_api.httpx.get", fake_get)

    payload = provider_models_payload({"provider": [DYNAMIC_PROVIDER_NAME]})

    assert payload["provider"] == DYNAMIC_PROVIDER_NAME
    assert payload["status"] == "available"
    assert payload["models"][0]["id"] == "custom-gpt"


def test_provider_models_payload_requires_gateway_key(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config = _add_provider(Config(), "openrouter", api_base="https://openrouter.ai/api/v1")
    save_config(config, config_path)
    monkeypatch.setattr(get_runtime_context(), "config_path", config_path)

    payload = provider_models_payload({"provider": ["openrouter"]})

    assert payload["status"] == "not_configured"
    assert payload["models"] == []



# ---------------------------------------------------------------------------
# Fase 6.7 — onboarding valida il provider e propaga l'errore alla WebUI
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_onboarding_rejects_invalid_provider_and_does_not_signal(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Una config di onboarding senza api_key non produce un provider valido:
    deve sollevare WebUISettingsError, NON accendere onboarding_event, e NON
    persistere la config rotta (prima falliva async e silenzioso nel container)."""
    import asyncio

    config_path = tmp_path / "config.json"
    monkeypatch.setattr(get_runtime_context(), "config_path", config_path)
    event = asyncio.Event()

    with pytest.raises(WebUISettingsError, match="Provider configuration is invalid"):
        await save_onboarding(
            {"provider_name": "openai", "format": "openai_compat", "model": "gpt-x"},
            onboarding_event=event,
        )

    assert not event.is_set()
    assert not config_path.exists()


@pytest.mark.asyncio
async def test_save_onboarding_signals_when_provider_valid(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Con api_key valida il provider si costruisce: config salvata + evento acceso."""
    import asyncio

    config_path = tmp_path / "config.json"
    monkeypatch.setattr(get_runtime_context(), "config_path", config_path)
    event = asyncio.Event()

    result = await save_onboarding(
        {
            "provider_name": "openai",
            "format": "openai_compat",
            "model": "gpt-x",
            "api_key": "sk-test-123",
        },
        onboarding_event=event,
    )

    assert result["status"] == "ok"
    assert event.is_set()
    assert config_path.exists()


@pytest.mark.asyncio
async def test_save_onboarding_welcome_lands_in_unified_session(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Il saluto di benvenuto deve finire nell'unica sessione unificata —
    quella che la chat rilegge all'attach — con chat_id 'default'."""
    import asyncio

    from jenny.session.keys import UNIFIED_SESSION_KEY
    from jenny.session.manager import SessionManager

    config_path = tmp_path / "config.json"
    monkeypatch.setattr(get_runtime_context(), "config_path", config_path)
    sessions = SessionManager(tmp_path)

    result = await save_onboarding(
        {
            "provider_name": "openai",
            "format": "openai_compat",
            "model": "gpt-x",
            "api_key": "sk-test-123",
        },
        session_manager=sessions,
        onboarding_event=asyncio.Event(),
    )

    assert result["chat_id"] == "default"
    session = sessions.get_or_create(UNIFIED_SESSION_KEY)
    assert [m.get("role") for m in session.messages] == ["assistant"]
    assert result["welcome_message"] in session.messages[0]["content"]
    assert "Hi, I'm Jenny" in session.messages[0]["content"]
    # Senza locale si ripiega sull'inglese, come la WebUI, e lo si persiste.
    saved = load_config(config_path)
    assert saved.agents.defaults.language == "en"


@pytest.mark.asyncio
async def test_save_onboarding_welcome_is_localized_to_english(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Con locale='en' il saluto di benvenuto è in inglese."""
    import asyncio

    from jenny.session.keys import UNIFIED_SESSION_KEY
    from jenny.session.manager import SessionManager

    config_path = tmp_path / "config.json"
    monkeypatch.setattr(get_runtime_context(), "config_path", config_path)
    sessions = SessionManager(tmp_path)

    result = await save_onboarding(
        {
            "provider_name": "openai",
            "format": "openai_compat",
            "model": "gpt-x",
            "api_key": "sk-test-123",
            "locale": "en",
        },
        session_manager=sessions,
        onboarding_event=asyncio.Event(),
    )

    assert result["chat_id"] == "default"
    session = sessions.get_or_create(UNIFIED_SESSION_KEY)
    assert [m.get("role") for m in session.messages] == ["assistant"]
    assert "Hi, I'm Jenny" in session.messages[0]["content"]
    saved = load_config(config_path)
    assert saved.agents.defaults.language == "en"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("locale", "greeting", "saved_language"),
    [("it", "Ciao sono Jenny", "it"), ("fr", "Hi, I'm Jenny", "en")],
)
async def test_save_onboarding_welcome_follows_a_known_locale_only(
    tmp_path, monkeypatch: pytest.MonkeyPatch, locale: str, greeting: str, saved_language: str
) -> None:
    """L'italiano resta per chi l'ha scelto; una lingua che non c'è ripiega sull'inglese.

    Il saluto è il primo messaggio della cronologia: un ripiego italiano lo
    faceva leggere a un modello che per tutto il resto parla inglese.
    """
    import asyncio

    from jenny.session.keys import UNIFIED_SESSION_KEY
    from jenny.session.manager import SessionManager

    config_path = tmp_path / "config.json"
    monkeypatch.setattr(get_runtime_context(), "config_path", config_path)
    sessions = SessionManager(tmp_path)

    await save_onboarding(
        {
            "provider_name": "openai",
            "format": "openai_compat",
            "model": "gpt-x",
            "api_key": "sk-test-123",
            "locale": locale,
        },
        session_manager=sessions,
        onboarding_event=asyncio.Event(),
    )

    session = sessions.get_or_create(UNIFIED_SESSION_KEY)
    assert greeting in session.messages[0]["content"]
    assert load_config(config_path).agents.defaults.language == saved_language


async def test_short_api_key_is_shown_as_present(tmp_path, monkeypatch) -> None:
    """`EMPTY` è il segnaposto che i docs raccomandano per i server locali.

    La maschera restituiva stringa vuota per chiavi ≤ 8 caratteri, quindi la UI
    la annunciava come "(no key)": chi seguiva le istruzioni pensava di aver
    sbagliato qualcosa.
    """
    config_path = tmp_path / "config.json"
    save_config(Config(), config_path)
    monkeypatch.setattr(get_runtime_context(), "config_path", config_path)

    await update_provider({
        "name": "local-llama",
        "format": "openai_compat",
        "api_key": "EMPTY",
        "api_base": "http://127.0.0.1:8080/v1",
    })
    payload = settings_payload()

    provider = next(p for p in payload["providers"] if p["name"] == "local-llama")
    assert provider["api_key_hint"]
    assert "EMPTY" not in provider["api_key_hint"]
    assert provider["configured"] is True


def test_settings_payload_reports_a_recovered_cron_store(tmp_path) -> None:
    """Store cron illeggibile all'avvio: la UI deve poterlo dire.

    Il payload è l'unico canale: i job persi non lasciano traccia in nessuna
    schermata, quindi senza questo campo l'utente scopriva che i suoi
    promemoria non c'erano più solo quando non suonavano.
    """
    from jenny.runtime.context import get_runtime_context

    ctx = get_runtime_context()
    assert settings_payload()["cron_recovery"] is None

    broken = tmp_path / "jobs.json.corrupt-123"
    ctx.cron_recovered_from = "empty"
    ctx.cron_quarantine_path = broken
    try:
        recovery = settings_payload()["cron_recovery"]
        assert recovery == {"restored_from": "empty", "broken_file": str(broken)}
    finally:
        ctx.cron_recovered_from = None
        ctx.cron_quarantine_path = None


# ── L'indirizzo del provider si controlla prima di scriverlo (collaudo 27/09) ─


@pytest.mark.parametrize(
    ("typed", "saved"),
    [
        ("", None),
        ("   ", None),
        (None, None),
        ("Http://10.0.2.2:8765/v1", "http://10.0.2.2:8765/v1"),
        ("HTTPS://api.example.test/v1", "https://api.example.test/v1"),
        ("https://api.example.test", "https://api.example.test"),
        ("${PROVIDER_BASE}", "${PROVIDER_BASE}"),
    ],
)
def test_the_base_url_is_normalised(typed: str | None, saved: str | None) -> None:
    assert normalize_api_base(typed) == saved


@pytest.mark.parametrize(
    "typed", ["Http:/10.0.2.2:8765/v1", "ftp://files.example.test", "api.example.test/v1", "https://"]
)
def test_a_base_url_that_is_not_a_web_address_is_refused(typed: str) -> None:
    """«http://» diventato «Http:/» con l'autocorrezione era salvato così."""
    with pytest.raises(WebUISettingsError):
        normalize_api_base(typed)


@pytest.mark.asyncio
async def test_a_bad_base_url_does_not_reach_the_config(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config = _add_provider(Config(), DYNAMIC_PROVIDER_NAME, api_base=DYNAMIC_PROVIDER_API_BASE)
    save_config(config, config_path)
    monkeypatch.setattr(get_runtime_context(), "config_path", config_path)

    with pytest.raises(WebUISettingsError):
        await update_provider({"name": DYNAMIC_PROVIDER_NAME, "api_base": "Http:/broken"})
    await update_provider({"name": DYNAMIC_PROVIDER_NAME, "api_base": "Https://ok.example.test/v1"})

    saved = next(
        p for p in load_config(config_path).providers.providers if p.name == DYNAMIC_PROVIDER_NAME
    )
    assert saved.api_base == "https://ok.example.test/v1"


@pytest.mark.asyncio
async def test_the_onboarding_refuses_a_bad_base_url_too(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    save_config(Config(), config_path)
    monkeypatch.setattr(get_runtime_context(), "config_path", config_path)
    with pytest.raises(WebUISettingsError):
        await save_onboarding({
            "provider_name": "p", "format": "openai_compat", "api_key": "not-a-real-credential",
            "api_base": "Http:/10.0.2.2", "model": "m",
        })
    assert load_config(config_path).providers.providers == []
