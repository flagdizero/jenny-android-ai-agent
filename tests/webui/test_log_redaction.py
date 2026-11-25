"""Regression tests for the log secret-redaction invariant (Intervento 4a).

Secrets travel in query strings (``?token=…``, ``?api_key=…``). Logging today is
already prudent, but nothing *guaranteed* it stayed that way. These tests lock in
the invariant: the redaction helper masks secret values, and a real onboarding
save never emits the raw ``api_key`` into the logs.
"""

from __future__ import annotations

import asyncio

import pytest
from loguru import logger as loguru_logger

from jenny.channels.http_utils import redact_query_secrets
from jenny.runtime.context import get_runtime_context
from jenny.session.manager import SessionManager
from jenny.webui.commands import CommandContext, dispatch_command

# ---------------------------------------------------------------------------
# Unit: redact_query_secrets
# ---------------------------------------------------------------------------


def test_redact_masks_token_value():
    assert (
        redact_query_secrets("/api/thing?token=super-secret-123")
        == "/api/thing?token=REDACTED"
    )


def test_redact_masks_api_key_value():
    assert (
        redact_query_secrets("/webui/bootstrap?api_key=sk-abc999")
        == "/webui/bootstrap?api_key=REDACTED"
    )


def test_redact_keeps_non_sensitive_params_intact():
    out = redact_query_secrets("/api/x?model=gpt-x&token=zzz&format=openai_compat")
    assert out == "/api/x?model=gpt-x&token=REDACTED&format=openai_compat"


def test_redact_no_query_is_unchanged():
    assert redact_query_secrets("/api/settings") == "/api/settings"
    assert redact_query_secrets("") == ""


def test_redact_empty_query_after_qmark():
    assert redact_query_secrets("/api/x?") == "/api/x?"


def test_redact_handles_empty_secret_value():
    assert redact_query_secrets("/api/x?token=") == "/api/x?token=REDACTED"


def test_redact_is_case_insensitive_on_key_names():
    assert redact_query_secrets("/api/x?ApiKey=nope") == "/api/x?ApiKey=REDACTED"
    assert redact_query_secrets("/api/x?ACCESS_TOKEN=nope") == "/api/x?ACCESS_TOKEN=REDACTED"
    assert redact_query_secrets("/api/x?Secret=nope") == "/api/x?Secret=REDACTED"


def test_redact_masks_multiple_secrets():
    out = redact_query_secrets("/api/x?token=aaa&client_id=me&api_key=bbb")
    assert out == "/api/x?token=REDACTED&client_id=me&api_key=REDACTED"


# ---------------------------------------------------------------------------
# Regression: onboarding must not leak the api_key value into logs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.usefixtures("onboarding_probe_ok")
async def test_onboarding_does_not_log_api_key_value(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "config.json"
    monkeypatch.setattr(get_runtime_context(), "config_path", config_path)

    secret = "sk-live-DO-NOT-LOG-me-4a2b7"
    ctx = CommandContext(
        get_workspace_root=lambda: tmp_path,
        invalidate_session=lambda _key: None,
        busy_session_keys=lambda: (),
        session_manager=SessionManager(tmp_path),
        onboarding_event=asyncio.Event(),
    )

    captured: list[str] = []
    sink_id = loguru_logger.add(lambda m: captured.append(str(m)), level="DEBUG")
    try:
        await dispatch_command(
            ctx,
            "onboarding.save",
            {
                "provider_name": "openai",
                "format": "openai_compat",
                "model": "gpt-x",
                "api_key": secret,
            },
        )
    finally:
        loguru_logger.remove(sink_id)

    joined = "".join(captured)
    assert joined, "expected the onboarding command to emit at least one log line"
    assert secret not in joined
