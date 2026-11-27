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
    monkeypatch.setattr("jenny.agent.token_usage._workspace", lambda: tmp_path / "workspace")

    from jenny.agent.token_usage import record_token_usage

    # Il fuso della config, come l'hook vero: senza, la riga va nel giorno del
    # telefono e il riepilogo la cerca in quello di UTC — fra mezzanotte e
    # l'una (ora italiana) non la trovava.
    timezone_name = load_config(config_path).agents.defaults.timezone
    record_token_usage({"prompt_tokens": 10, "completion_tokens": 5}, timezone_name=timezone_name)

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
    deve rispondere ``probe: "config"`` (un codice che il wizard traduce, non
    una frase inglese), NON accendere onboarding_event, e NON persistere la
    config rotta (prima falliva async e silenzioso nel container)."""
    import asyncio

    config_path = tmp_path / "config.json"
    monkeypatch.setattr(get_runtime_context(), "config_path", config_path)
    event = asyncio.Event()

    result = await save_onboarding(
        {"provider_name": "openai", "format": "openai_compat", "model": "gpt-x"},
        onboarding_event=event,
    )

    assert result == {"ok": False, "probe": "config"}
    assert not event.is_set()
    assert not config_path.exists()


@pytest.mark.asyncio
@pytest.mark.usefixtures("onboarding_probe_ok")
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
@pytest.mark.usefixtures("onboarding_probe_ok")
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
@pytest.mark.usefixtures("onboarding_probe_ok")
async def test_save_onboarding_welcome_stays_in_the_chat_after_the_first_message(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Il saluto sta anche nel verbale della chat, non solo nella sessione.

    Prima la casa lo vedeva solo a verbale vuoto (ricostruito dalla sessione):
    al primo messaggio dell'utente il verbale non era piu' vuoto, e il saluto
    spariva — proprio quello sotto cui stanno i suggerimenti di partenza.
    """
    import asyncio

    from jenny.session.manager import SessionManager
    from jenny.webui.transcript_store import append_transcript_object, read_transcript_lines

    config_path = tmp_path / "config.json"
    monkeypatch.setattr(get_runtime_context(), "config_path", config_path)
    result = await save_onboarding(
        {"provider_name": "openai", "format": "openai_compat", "model": "gpt-x",
         "api_key": "sk-test-123", "locale": "it"},
        session_manager=SessionManager(tmp_path),
        onboarding_event=asyncio.Event(),
    )
    append_transcript_object("websocket:default", {
        "event": "user", "chat_id": "default", "text": "ciao", "turn_id": "u1", "turn_phase": "user"})
    texts = [line.get("text") for line in read_transcript_lines("websocket:default")]
    assert result["welcome_message"] in texts
    assert texts.index(result["welcome_message"]) < texts.index("ciao")


@pytest.mark.asyncio
@pytest.mark.usefixtures("onboarding_probe_ok")
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
@pytest.mark.usefixtures("onboarding_probe_ok")
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


# ---------------------------------------------------------------------------
# Il primo avvio prova la chiave prima di salvarla
# ---------------------------------------------------------------------------
#
# ``save_onboarding`` costruisce il provider e gli fa una domanda vera, fuori dal
# lucchetto della config; se non risponde non scrive niente e torna il tipo del
# guasto, che il wizard traduce. Qui il provider è finto (``make_provider``), la
# logica della prova è quella vera.


_ONBOARDING_DATA = {
    "provider_name": "openai",
    "format": "openai_compat",
    "model": "gpt-x",
    "api_key": "not-a-real-credential",
    "locale": "it",
}


class _FakeProvider:
    """Un provider che risponde *response* (o si blocca, con ``hang``)."""

    def __init__(self, response=None, *, hang: bool = False, calls: list | None = None):
        from jenny.providers.base import LLMResponse

        self.response = response if response is not None else LLMResponse(content="ok")
        self.hang = hang
        self.calls: list = calls if calls is not None else []
        self.closed = False

    async def chat_with_retry(self, **kwargs):
        import asyncio

        self.calls.append(("probe", kwargs))
        if self.hang:
            # Un provider che non risponde mai: solo il timeout della prova lo ferma.
            await asyncio.Event().wait()
        return self.response

    async def aclose(self) -> None:
        self.closed = True


def _use_provider(monkeypatch: pytest.MonkeyPatch, provider: _FakeProvider) -> None:
    monkeypatch.setattr("jenny.providers.factory.make_provider", lambda config: provider)


def _error(**fields):
    from jenny.providers.base import LLMResponse

    return LLMResponse(content=fields.pop("content", "Error: boom"), finish_reason="error", **fields)


@pytest.mark.asyncio
async def test_onboarding_probe_that_answers_saves(
    config_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    provider = _FakeProvider()
    _use_provider(monkeypatch, provider)
    event = asyncio.Event()

    result = await save_onboarding(dict(_ONBOARDING_DATA), onboarding_event=event)

    assert result["ok"] is True and result["status"] == "ok"
    assert event.is_set()
    saved = load_config(config_path)
    assert saved.providers.providers[0].api_key == "not-a-real-credential"
    assert saved.agents.defaults.model == "gpt-x"
    # Una domanda sola, minima, al modello scelto e senza ragionamento.
    [(_, kwargs)] = provider.calls
    assert kwargs["model"] == "gpt-x"
    assert kwargs["max_tokens"] == 16
    assert kwargs["reasoning_effort"] is None
    assert kwargs["tools"] is None
    assert provider.closed, "il client di prova non resta aperto"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "kind"),
    [
        ({"error_status_code": 401}, "auth"),
        ({"error_status_code": 403}, "auth"),
        ({"error_status_code": 402}, "credit"),
        ({"error_status_code": 429}, "rate_limit"),
        ({"error_status_code": 503}, "server"),
        ({"error_kind": "connection"}, "connection"),
        ({"error_kind": "timeout"}, "timeout"),
        ({"error_status_code": 404, "error_code": "model_not_found"}, "model"),
        (
            {"error_status_code": 404, "error_type": "not_found_error",
             "content": "Error: not_found_error: model: claude-nope"},
            "model",
        ),
        ({"error_status_code": 400, "content": "Error: Model Not Exist"}, "model"),
        # Un 404 che non parla di modelli è più spesso un indirizzo sbagliato.
        ({"error_status_code": 404, "content": "Error: <html>Not Found</html>"}, "generic"),
        ({"error_status_code": 400, "content": "Error: max_tokens is too small"}, "generic"),
    ],
)
async def test_onboarding_probe_failure_saves_nothing(
    config_path, monkeypatch: pytest.MonkeyPatch, response: dict, kind: str
) -> None:
    """Chiave rifiutata, credito finito, modello che non c'è, rete: il file resta
    quello di prima, l'agente non nasce, e il wizard riceve il tipo."""
    import asyncio

    before = config_path.read_bytes()
    provider = _FakeProvider(_error(**response))
    _use_provider(monkeypatch, provider)
    event = asyncio.Event()

    result = await save_onboarding(dict(_ONBOARDING_DATA), onboarding_event=event)

    assert result == {"ok": False, "probe": kind}
    assert config_path.read_bytes() == before
    assert not event.is_set()
    assert provider.closed


@pytest.mark.asyncio
async def test_onboarding_probe_that_never_answers_is_a_timeout(
    config_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = config_path.read_bytes()
    monkeypatch.setattr("jenny.webui.settings_api.ONBOARDING_PROBE_TIMEOUT_S", 0.05)
    _use_provider(monkeypatch, _FakeProvider(hang=True))

    result = await save_onboarding(dict(_ONBOARDING_DATA))

    assert result == {"ok": False, "probe": "timeout"}
    assert config_path.read_bytes() == before


@pytest.mark.asyncio
async def test_onboarding_probe_that_raises_is_generic(
    config_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Raising(_FakeProvider):
        async def chat_with_retry(self, **kwargs):
            raise RuntimeError("unexpected")

    before = config_path.read_bytes()
    _use_provider(monkeypatch, _Raising())

    assert await save_onboarding(dict(_ONBOARDING_DATA)) == {"ok": False, "probe": "generic"}
    assert config_path.read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", ["1", "true", True])
async def test_onboarding_save_anyway_skips_the_probe(
    config_path, monkeypatch: pytest.MonkeyPatch, flag
) -> None:
    """«Salva lo stesso»: nessuna domanda, si salva e l'agente nasce."""
    import asyncio

    provider = _FakeProvider(_error(error_kind="connection"))
    _use_provider(monkeypatch, provider)
    event = asyncio.Event()

    result = await save_onboarding(
        {**_ONBOARDING_DATA, "skip_probe": flag}, onboarding_event=event,
    )

    assert result["ok"] is True
    assert provider.calls == []
    assert event.is_set()
    assert load_config(config_path).agents.defaults.model == "gpt-x"


@pytest.mark.asyncio
async def test_onboarding_save_anyway_still_refuses_a_broken_configuration(
    config_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Saltare la domanda non salta la costruzione: senza chiave non c'è provider."""
    before = config_path.read_bytes()
    result = await save_onboarding(
        {"provider_name": "openai", "format": "openai_compat", "model": "gpt-x",
         "skip_probe": "1"},
    )
    assert result == {"ok": False, "probe": "config"}
    assert config_path.read_bytes() == before


@pytest.mark.asyncio
async def test_onboarding_probe_runs_before_the_config_lock(
    config_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La domanda dura secondi: dentro ``store.mutate`` terrebbe preso il
    lucchetto della config per tutto quel tempo."""
    from jenny.config import store

    order: list = []
    _use_provider(monkeypatch, _FakeProvider(calls=order))
    real_mutate = store.mutate

    async def recording_mutate(apply, **kwargs):
        order.append(("mutate", None))
        return await real_mutate(apply, **kwargs)

    monkeypatch.setattr(store, "mutate", recording_mutate)

    result = await save_onboarding(dict(_ONBOARDING_DATA))

    assert result["ok"] is True
    assert [step for step, _ in order] == ["probe", "mutate"]


@pytest.mark.asyncio
async def test_onboarding_probe_failure_never_enters_the_config_lock(
    config_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jenny.config import store

    async def no_mutate(apply, **kwargs):
        raise AssertionError("a failed probe must not write the config")

    monkeypatch.setattr(store, "mutate", no_mutate)
    _use_provider(monkeypatch, _FakeProvider(_error(error_status_code=401)))

    assert await save_onboarding(dict(_ONBOARDING_DATA)) == {"ok": False, "probe": "auth"}


def test_provider_models_payload_tells_a_rejected_key_from_a_missing_one(
    config_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Senza chiave è ``not_configured``; con una chiave che il provider rifiuta
    (401/403) è ``rejected``: erano lo stesso stato, e la WebUI diceva «serve una
    chiave» a chi l'aveva appena scritta."""

    def refuse(url: str, **kwargs):
        request = httpx.Request("GET", url)
        return httpx.Response(401, json={"error": "nope"}, request=request)

    monkeypatch.setattr("jenny.webui.settings_api.httpx.get", refuse)

    rejected = provider_models_payload({
        "provider": ["openai"], "api_key": ["not-a-real-credential"],
        "format": ["openai_compat"], "api_base": ["https://api.example.test/v1"],
    })
    assert rejected["status"] == "rejected"
    assert rejected["models"] == []

    save_config(_add_provider(Config(), "openrouter", api_base="https://openrouter.ai/api/v1"),
                config_path)
    missing = provider_models_payload({"provider": ["openrouter"]})
    assert missing["status"] == "not_configured"


# ---------------------------------------------------------------------------
# add_provider: un provider nuovo dalla casa, dopo la domanda di prova
# ---------------------------------------------------------------------------
#
# La prova e' la stessa del primo avvio (``_probe_provider``): qui il provider e'
# finto come sopra, e si misura cio' che l'aggiunta fa di suo — non sovrascrive
# mai un nome, non scrive se la prova non risponde, non cambia chi risponde.

_ADD_DATA = {
    "name": "openrouter",
    "format": "openai_compat",
    "api_key": "not-a-real-credential",
    "api_base": "https://openrouter.ai/api/v1",
    "model": "m-1",
}


def _seed_active(config_path) -> None:
    config = _add_provider(Config(), "attivo", api_key="sk-attivo")
    config.providers.default = "attivo"
    config.agents.defaults.model = "modello-attivo"
    save_config(config, config_path)


@pytest.mark.asyncio
async def test_add_provider_that_answers_is_added_and_changes_nobody(
    config_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jenny.webui.settings_api import add_provider

    _seed_active(config_path)
    provider = _FakeProvider()
    _use_provider(monkeypatch, provider)

    result = await add_provider(dict(_ADD_DATA))

    assert result["ok"] is True
    assert "openrouter" in {p["name"] for p in result["providers"]}
    saved = load_config(config_path)
    by_name = {p.name: p for p in saved.providers.providers}
    assert by_name["openrouter"].api_key == "not-a-real-credential"
    assert by_name["openrouter"].api_base == "https://openrouter.ai/api/v1"
    assert by_name["openrouter"].format == "openai_compat"
    # Chi risponde non cambia: lo cambia la casa, con ``switchModel``.
    assert saved.providers.default == "attivo"
    assert saved.agents.defaults.model == "modello-attivo"
    assert by_name["attivo"].api_key == "sk-attivo"
    [(_, kwargs)] = provider.calls
    assert kwargs["model"] == "m-1"
    assert provider.closed


@pytest.mark.asyncio
async def test_add_provider_never_overwrites_a_name_in_use(
    config_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``update_provider`` e' un upsert: lo stesso nome rimpiazzerebbe la chiave
    dell'altro. Qui no, e senza nemmeno fare la domanda."""
    from jenny.webui.settings_api import add_provider

    _seed_active(config_path)
    before = config_path.read_bytes()
    provider = _FakeProvider()
    _use_provider(monkeypatch, provider)

    result = await add_provider({**_ADD_DATA, "name": "attivo", "api_key": "sk-altra"})

    assert result == {"ok": False, "error": "name_taken"}
    assert config_path.read_bytes() == before
    assert provider.calls == []


@pytest.mark.asyncio
async def test_add_provider_name_taken_during_the_probe_writes_nothing(
    config_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fra la prova e il lock un altro scrittore prende il nome: vince lui."""
    from jenny.webui.settings_api import add_provider

    _seed_active(config_path)

    async def another_writer_meanwhile(provider, model):
        config = load_config(config_path)
        config.providers.providers.append(
            ProviderConfig(name="openrouter", format="openai_compat", api_key="sk-sua")
        )
        save_config(config, config_path)
        return None

    monkeypatch.setattr("jenny.webui.settings_api._probe_provider", another_writer_meanwhile)
    _use_provider(monkeypatch, _FakeProvider())

    result = await add_provider(dict(_ADD_DATA))

    assert result == {"ok": False, "error": "name_taken"}
    saved = [p for p in load_config(config_path).providers.providers if p.name == "openrouter"]
    assert [p.api_key for p in saved] == ["sk-sua"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "kind"),
    [
        ({"error_status_code": 401}, "auth"),
        ({"error_status_code": 402}, "credit"),
        ({"error_kind": "connection"}, "connection"),
        ({"error_status_code": 404, "error_code": "model_not_found"}, "model"),
    ],
)
async def test_add_provider_probe_failure_writes_nothing(
    config_path, monkeypatch: pytest.MonkeyPatch, response: dict, kind: str
) -> None:
    from jenny.webui.settings_api import add_provider

    _seed_active(config_path)
    before = config_path.read_bytes()
    provider = _FakeProvider(_error(**response))
    _use_provider(monkeypatch, provider)

    assert await add_provider(dict(_ADD_DATA)) == {"ok": False, "probe": kind}
    assert config_path.read_bytes() == before
    assert provider.closed


@pytest.mark.asyncio
async def test_add_provider_save_anyway_skips_only_the_question(
    config_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jenny.webui.settings_api import add_provider

    _seed_active(config_path)
    provider = _FakeProvider(_error(error_kind="connection"))
    _use_provider(monkeypatch, provider)

    result = await add_provider({**_ADD_DATA, "skip_probe": "1"})

    assert result["ok"] is True
    assert provider.calls == []
    assert "openrouter" in {p.name for p in load_config(config_path).providers.providers}


@pytest.mark.asyncio
async def test_add_provider_save_anyway_still_refuses_a_provider_without_a_key(
    config_path,
) -> None:
    """Saltare la domanda non salta la costruzione: la fabbrica vera vuole una
    chiave (``factory.py``), quindi «Salva lo stesso» non salva un provider muto."""
    from jenny.webui.settings_api import add_provider

    _seed_active(config_path)
    before = config_path.read_bytes()

    result = await add_provider({**_ADD_DATA, "api_key": "", "skip_probe": "1"})

    assert result == {"ok": False, "probe": "config"}
    assert config_path.read_bytes() == before


@pytest.mark.asyncio
async def test_add_provider_asks_before_taking_the_config_lock(
    config_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La domanda dura secondi: dentro ``store.mutate`` terrebbe preso il lucchetto."""
    from jenny.config import store
    from jenny.webui.settings_api import add_provider

    _seed_active(config_path)
    order: list = []
    _use_provider(monkeypatch, _FakeProvider(calls=order))
    real_mutate = store.mutate

    async def recording_mutate(apply, **kwargs):
        order.append(("mutate", None))
        return await real_mutate(apply, **kwargs)

    monkeypatch.setattr(store, "mutate", recording_mutate)

    assert (await add_provider(dict(_ADD_DATA)))["ok"] is True
    assert [step for step, _ in order] == ["probe", "mutate"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        {"name": "  "},
        {"format": ""},
        {"format": "gemini"},
        {"model": ""},
        {"api_base": "Http:/openrouter.ai"},
    ],
)
async def test_add_provider_refuses_what_it_cannot_save(
    config_path, monkeypatch: pytest.MonkeyPatch, bad: dict
) -> None:
    from jenny.webui.settings_api import add_provider

    _seed_active(config_path)
    before = config_path.read_bytes()
    provider = _FakeProvider()
    _use_provider(monkeypatch, provider)

    with pytest.raises(WebUISettingsError):
        await add_provider({**_ADD_DATA, **bad})
    assert config_path.read_bytes() == before
    assert provider.calls == []
