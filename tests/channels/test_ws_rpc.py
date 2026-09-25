"""Test dell'RPC client→server sul canale WebSocket (``rpc`` / ``rpc_result``).

Speculare a ``test_ui_query.py``. Copre le tre cose che il trasporto deve
garantire da solo, indipendentemente dai comandi: un envelope malformato non
diventa un'eccezione né una risposta silenziosa sbagliata, l'autorizzazione è
quella dell'handshake (non del frame), e nessun dettaglio interno esce nel
frame di errore.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny.channels import ws_rpc
from jenny.webui.commands import CommandContext, CommandError


def _ctx(tmp_path: Path) -> CommandContext:
    return CommandContext(get_workspace_root=lambda: tmp_path, invalidate_session=lambda _key: None, busy_session_keys=lambda: (), get_cron_service=lambda: None)


# ---------------------------------------------------------------------------
# parsing dell'envelope
# ---------------------------------------------------------------------------


def test_parse_accepts_a_well_formed_frame() -> None:
    rpc_id, method, params = ws_rpc.parse_rpc_frame(
        {"type": "rpc", "id": "rpc-abc123", "method": "workspace.write", "params": {"path": "a"}}
    )
    assert (rpc_id, method) == ("rpc-abc123", "workspace.write")
    assert params == {"path": "a"}


def test_parse_treats_missing_params_as_empty() -> None:
    _, _, params = ws_rpc.parse_rpc_frame({"id": "rpc-1", "method": "m"})
    assert params == {}
    _, _, params = ws_rpc.parse_rpc_frame({"id": "rpc-1", "method": "m", "params": None})
    assert params == {}


@pytest.mark.parametrize("bad_id", [None, "", 42, "has space", "colon:inside", "x" * 65])
def test_parse_rejects_a_bad_id_without_a_reply(bad_id: object) -> None:
    """Senza un id valido non c'è nulla a cui rispondere: il frame va scartato."""
    with pytest.raises(ws_rpc.RpcFrameError):
        ws_rpc.parse_rpc_frame({"id": bad_id, "method": "workspace.write"})


@pytest.mark.parametrize("bad_method", [None, "", "   ", 42, "m" * 65])
def test_parse_rejects_a_bad_method_but_keeps_the_id(bad_method: object) -> None:
    """Errore recapitabile: l'id c'è, quindi il client merita una risposta."""
    with pytest.raises(CommandError) as exc:
        ws_rpc.parse_rpc_frame({"id": "rpc-1", "method": bad_method})
    assert exc.value.code == "bad_request"


def test_parse_rejects_non_object_params() -> None:
    with pytest.raises(CommandError) as exc:
        ws_rpc.parse_rpc_frame({"id": "rpc-1", "method": "m", "params": ["a"]})
    assert exc.value.code == "bad_request"


# ---------------------------------------------------------------------------
# autorizzazione
# ---------------------------------------------------------------------------


def test_no_secret_configured_means_no_extra_gate() -> None:
    """Senza secret la WebUI locale non ha un token da presentare."""
    ws_rpc.authorize(secret="", connection_authenticated=False)
    ws_rpc.authorize(secret="   ", connection_authenticated=False)


def test_secret_configured_requires_an_authenticated_connection() -> None:
    with pytest.raises(CommandError) as exc:
        ws_rpc.authorize(secret="s3cr3t", connection_authenticated=False)
    assert exc.value.code == "forbidden"
    ws_rpc.authorize(secret="s3cr3t", connection_authenticated=True)


async def test_run_rpc_refuses_before_touching_the_filesystem(tmp_path: Path) -> None:
    """La scrittura non deve avvenire su una connessione non autenticata."""
    with pytest.raises(CommandError) as exc:
        await ws_rpc.run_rpc(
            _ctx(tmp_path),
            method="workspace.write",
            params={"path": "a.txt", "content": "x"},
            secret="s3cr3t",
            connection_authenticated=False,
        )
    assert exc.value.code == "forbidden"
    assert not (tmp_path / "a.txt").exists()


# ---------------------------------------------------------------------------
# frame di risposta
# ---------------------------------------------------------------------------


def test_result_frame_shape() -> None:
    assert ws_rpc.result_frame("rpc-1", {"path": "a"}) == {
        "id": "rpc-1", "ok": True, "result": {"path": "a"},
    }


def test_error_frame_carries_code_and_message_only() -> None:
    frame = ws_rpc.error_frame("rpc-1", CommandError("too_large", "file too large to save"))
    assert frame == {
        "id": "rpc-1",
        "ok": False,
        "error": {"code": "too_large", "message": "file too large to save"},
    }
    # Nessun campo in più: un traceback o un path interno non deve poter uscire.
    assert set(frame["error"]) == {"code", "message"}
