from __future__ import annotations

from datetime import datetime, timezone

import pytest

from jenny.agent.hook import AgentHookContext
from jenny.agent.token_usage import (
    TokenUsageHook,
    record_token_usage,
    token_usage_payload,
)


def test_record_token_usage_aggregates_by_local_day(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("jenny.agent.token_usage.get_webui_dir", lambda: tmp_path / "webui")

    record_token_usage(
        {"prompt_tokens": 100, "completion_tokens": 40, "cached_tokens": 20},
        timezone_name="Asia/Shanghai",
        now=datetime(2026, 6, 2, 18, 0, tzinfo=timezone.utc),
    )
    record_token_usage(
        {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        timezone_name="Asia/Shanghai",
        now=datetime(2026, 6, 2, 19, 0, tzinfo=timezone.utc),
    )

    payload = token_usage_payload(
        timezone_name="Asia/Shanghai",
        now=datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc),
    )

    assert payload["total_tokens_30d"] == 155
    assert payload["active_days_30d"] == 1
    assert payload["requests_30d"] == 2


def test_record_token_usage_skips_empty_usage(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("jenny.agent.token_usage.get_webui_dir", lambda: tmp_path / "webui")

    record_token_usage({"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})

    payload = token_usage_payload(now=datetime(2026, 6, 3, tzinfo=timezone.utc))
    assert payload["total_tokens_30d"] == 0
    assert payload["total_tokens"] == 0


def test_record_token_usage_keeps_estimated_split(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("jenny.agent.token_usage.get_webui_dir", lambda: tmp_path / "webui")

    record_token_usage(
        {"prompt_tokens": 100, "completion_tokens": 25, "estimated_tokens": 125},
        now=datetime(2026, 6, 3, tzinfo=timezone.utc),
    )

    payload = token_usage_payload(now=datetime(2026, 6, 3, tzinfo=timezone.utc))

    assert payload["total_tokens_30d"] == 125
    assert payload["total_tokens"] == 125


def test_record_token_usage_keeps_source_breakdown(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("jenny.agent.token_usage.get_webui_dir", lambda: tmp_path / "webui")

    record_token_usage(
        {"prompt_tokens": 100, "completion_tokens": 25},
        source="user",
        now=datetime(2026, 6, 3, tzinfo=timezone.utc),
    )
    record_token_usage(
        {"prompt_tokens": 20, "completion_tokens": 5},
        source="dream",
        now=datetime(2026, 6, 3, tzinfo=timezone.utc),
    )

    payload = token_usage_payload(now=datetime(2026, 6, 3, tzinfo=timezone.utc))

    assert payload["total_tokens_30d"] == 150
    assert payload["total_tokens"] == 150


@pytest.mark.asyncio
async def test_token_usage_hook_classifies_source_from_session_key(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("jenny.agent.token_usage.get_webui_dir", lambda: tmp_path / "webui")
    monkeypatch.setattr("jenny.agent.token_usage._local_day", lambda *_, **__: "2026-06-03")

    hook = TokenUsageHook()
    await hook.after_iteration(
        AgentHookContext(
            iteration=0,
            messages=[],
            session_key="cron:drink-water",
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )
    )

    payload = token_usage_payload(now=datetime(2026, 6, 3, tzinfo=timezone.utc))

    assert payload["total_tokens_30d"] == 15


from jenny.session.keys import HEARTBEAT_SESSION_KEY, UNIFIED_SESSION_KEY  # noqa: E402

# ── A chi si addebita il lavoro interno ─────────────────────────────────────
#
# Misurato sul dispositivo il 25/08, su 27 giorni di `token-usage.json`: il bucket
# `dream` non era comparso **una volta**, con Dream che gira ogni due
# ore e il giardiniere su otto wiki. La causa non era la mappa qui
# sotto ma il cancello degli hook (`AgentLoop`, `runs_when_ephemeral`); questa
# mappa è la seconda metà, e da sola avrebbe mandato il giardiniere nel posto
# peggiore possibile.


@pytest.mark.parametrize(
    ("session_key", "expected"),
    [
        ("dream:20260825-120537", "dream"),
        ("dream:review-20260825-060415", "dream"),
        ("gardener:viaggio-pazzo-20260824-195702", "gardener"),
        ("cron:update_check", "cron"),
        # Le due chiavi senza suffisso vengono dalle **costanti**, non da un
        # letterale: sono confronti per uguaglianza (v. ``_INTERNAL_KIND_BY_KEY``),
        # quindi una scritta a mano resterebbe verde il giorno in cui la costante
        # cambia, provando una cosa su una chiave che non esiste più. È anche la
        # regola che ``tests/session/test_internal_key_vocabulary.py`` fa
        # rispettare — e che questo file aveva violato.
        (HEARTBEAT_SESSION_KEY, "cron"),
        (UNIFIED_SESSION_KEY, "user"),
    ],
)
def test_internal_work_is_billed_to_itself(session_key, expected) -> None:
    """Le chiavi sono quelle **vere**, lette dai file di sessione del dispositivo.

    `gardener:` è la riga che questo test esiste per tenere ferma: senza la sua
    voce nella mappa cade nel fallthrough `"user"` — cioè la manutenzione viene
    addebitata alla chat dell'utente. È peggio di un secchio generico, perché è
    **plausibile**: un totale gonfiato che nessuno mette in dubbio.

    E un bucket suo invece di `cron`, benché sia il cron a farlo partire: il
    giardiniere gira una volta **per progetto**, quindi il suo costo cresce col
    numero di wiki e non col numero di job — dentro `cron` quella crescita non si
    vede.
    """
    from jenny.agent.token_usage import _source_from_session_key

    assert _source_from_session_key(session_key) == expected


def test_a_retired_bucket_keeps_its_label_in_the_ledger() -> None:
    """``atlas`` non spende piu' — nessun kind ci mappa — ma i giorni gia' scritti
    lo portano, e un registro non rietichetta la spesa passata: senza la chiave in
    `_SOURCE_KEYS` quel giorno passerebbe a `"system"` alla prima rilettura."""
    from jenny.agent.token_usage import (
        _INTERNAL_KIND_TO_SOURCE,
        _SOURCE_KEYS,
        normalize_token_usage_state,
    )

    assert "atlas" not in _INTERNAL_KIND_TO_SOURCE.values()
    assert "atlas" in _SOURCE_KEYS
    state = normalize_token_usage_state({
        "days": {
            "2026-09-01": {
                "prompt_tokens": 900, "completion_tokens": 100, "total_tokens": 1000,
                "requests": 1,
                "sources": {"atlas": {
                    "prompt_tokens": 900, "completion_tokens": 100, "total_tokens": 1000,
                    "requests": 1,
                }},
            }
        }
    })

    assert set(state["days"]["2026-09-01"]["sources"]) == {"atlas"}


def test_every_bucket_the_map_names_is_a_declared_source() -> None:
    """Un valore fuori da `_SOURCE_KEYS` viene riscritto in `"system"` **in
    silenzio** da `_clean_source`: una voce nuova nella mappa senza la sua chiave
    non darebbe un errore, darebbe un seppellimento."""
    from jenny.agent.token_usage import _INTERNAL_KIND_TO_SOURCE, _SOURCE_KEYS

    assert set(_INTERNAL_KIND_TO_SOURCE.values()) <= set(_SOURCE_KEYS)


def test_the_real_hook_declares_that_it_survives_an_ephemeral_turn() -> None:
    """Il contratto dell'oggetto **vero**, e senza questo si disfa in silenzio.

    Una mutazione che toglie `runs_when_ephemeral` da `TokenUsageHook` è
    sopravvissuta a tutto il resto del file: la mappa era provata, e il cancello
    era provato con uno *spy*. Nessuno chiedeva niente all'hook reale — cioè
    l'unico che, smettendo di dichiararsi, riporterebbe l'installazione a non
    misurare il lavoro interno, in silenzio. È il difetto che questa correzione
    chiude, riprodotto dentro la suite.

    **Cosa questo test non fa**, per non spacciarlo: non gira un turno vero con un
    `SessionManager` vero. Provato e scartato — in questo ambiente `SessionManager`
    è patchato, quindi la chiave che arriva all'hook è un `MagicMock` e
    `startswith("api:")` è *truthy*: l'asserzione sul bucket misurerebbe il mock.
    La catena hook→bucket è coperta da `test_internal_work_is_billed_to_itself`,
    il montaggio da `TestEphemeralHooks`, e questa riga è la giunzione fra le due.
    """
    assert TokenUsageHook().runs_when_ephemeral() is True


def test_a_plain_hook_does_not_survive_one() -> None:
    """Il default, che è quel che rende la riga sopra una *scelta*."""
    from jenny.agent.hook import AgentHook

    assert AgentHook().runs_when_ephemeral() is False
