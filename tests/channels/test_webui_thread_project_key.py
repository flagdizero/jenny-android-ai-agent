"""La chat di un quaderno si ridisegna dalla sua trascrizione, non dalla sessione.

Il client chiede un progetto come ``project:<nome>`` (``shared/session-manager.js``),
ma la trascrizione la scrive il registratore sotto ``websocket:<chat_id>``, cioe'
``websocket:project:<nome>``. Fino al 26/09/2026 la route del thread cercava la
chiave del client, non trovava il file e ripiegava sulla storia ricostruita dalla
sessione. Misurato sul telefono su tutti gli 11 quaderni: niente tool, niente
ragionamento, e i rientri dei subagent registrati prima del marcatore
``injected_event`` (05/09) disegnati come messaggi dell'utente — «Summarize this
naturally for the user…» nella chat di viaggio-pazzo.

Nello stesso blocco ``run_started_at`` si leggeva sempre per ``default``: un
quaderno riaperto a turno in corso non lo sapeva, e uno aperto mentre girava la
chat personale credeva di averne uno.
"""

from __future__ import annotations

import json
import urllib.parse
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from support.gateway_http import make_handler, make_request

from jenny.session import webui_turns
from jenny.session.keys import webui_chat_id, webui_transcript_key
from jenny.webui.transcript_store import append_transcript_object

_ANNOUNCE = (
    "[Subagent 'wiki-fix' completed successfully]\n\n"
    "Task: spezza la pagina\n\nResult:\nfatto\n\n"
    "Summarize this naturally for the user. Keep it brief (1-2 sentences)."
)

# La sessione come la scriveva il loop prima del marcatore: il rientro del
# subagent e' una riga ``user`` qualunque.
_SESSION = [
    {"role": "user", "content": "sistema la pagina"},
    {"role": "assistant", "content": "Ci penso io, delego."},
    {"role": "user", "content": _ANNOUNCE},
    {"role": "assistant", "content": "Pagina spezzata in due."},
]


def _seed_transcript() -> None:
    key = "websocket:project:demo"
    chat = "project:demo"
    for ev in (
        {"event": "user", "chat_id": chat, "text": "sistema la pagina", "turn_id": "t1"},
        {"event": "reasoning_delta", "chat_id": chat, "text": "prima leggo", "turn_id": "t1"},
        {"event": "reasoning_end", "chat_id": chat, "turn_id": "t1"},
        {
            "event": "message",
            "chat_id": chat,
            "text": "",
            "kind": "progress",
            "tool_events": [{
                "phase": "end",
                "call_id": "c1",
                "name": "read_file",
                "arguments": {"path": "wiki/a.md"},
                "result": "ok",
            }],
            "turn_id": "t1",
        },
        {"event": "message", "chat_id": chat, "text": "Ci penso io, delego.", "turn_id": "t1"},
        {"event": "turn_end", "chat_id": chat, "turn_id": "t1"},
        # Il turno d'annuncio: nella trascrizione non ha una bolla utente.
        {"event": "message", "chat_id": chat, "text": "Pagina spezzata in due.", "turn_id": "t2"},
        {"event": "turn_end", "chat_id": chat, "turn_id": "t2"},
    ):
        append_transcript_object(key, ev)


@pytest.fixture
def handler(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)
    read_keys: list[str] = []

    def read_session_file(key: str) -> dict[str, Any]:
        read_keys.append(key)
        return {"messages": list(_SESSION)} if key == "project:demo" else {}

    h = make_handler(
        tmp_path,
        session_manager=SimpleNamespace(read_session_file=read_session_file),
        media=SimpleNamespace(
            augment_transcript_media=lambda paths: [],
            rewrite_local_markdown_images=lambda text, workspace_path=None: text,
        ),
        workspaces=SimpleNamespace(
            scope_for_session_key=lambda key: SimpleNamespace(
                project_path=None, payload=lambda: {"session_key": key}
            ),
        ),
    )
    h.read_keys = read_keys  # type: ignore[attr-defined]
    return h


def _get(handler, key: str) -> dict[str, Any]:
    quoted = urllib.parse.quote(key, safe="")
    response = handler._handle_webui_thread_get(
        make_request(f"/api/sessions/{quoted}/webui-thread"), quoted
    )
    assert response.status_code == 200, response.body
    return json.loads(response.body.decode("utf-8"))


def test_a_notebook_thread_is_drawn_from_its_transcript(handler) -> None:
    _seed_transcript()

    payload = _get(handler, "project:demo")
    msgs = payload["messages"]

    assert any(m.get("kind") == "trace" for m in msgs), msgs
    assert any(m.get("reasoning") == "prima leggo" for m in msgs), msgs
    # La sessione si legge ancora con la chiave del core: e' lei che da' lo scope.
    assert handler.read_keys == ["project:demo"]
    # Il contratto verso il client non cambia.
    assert payload["sessionKey"] == "project:demo"


def test_an_old_subagent_return_is_not_a_user_bubble(handler) -> None:
    """Nemmeno passando dal riempimento delle righe utente dalla sessione, che
    aggancia ai turni senza bolla la riga utente con la stessa risposta: il
    turno d'annuncio e' proprio un turno senza bolla."""
    _seed_transcript()

    msgs = _get(handler, "project:demo")["messages"]

    users = [m["content"] for m in msgs if m.get("role") == "user"]
    assert users == ["sistema la pagina"]
    assert not any("Summarize this naturally" in json.dumps(m) for m in msgs)
    assert msgs[-1]["content"] == "Pagina spezzata in due."


def test_run_started_at_belongs_to_the_open_conversation(
    handler, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_transcript()
    started = webui_turns._WEBSOCKET_TURN_WALL_STARTED_AT
    monkeypatch.setitem(started, "project:demo", 1234.5)

    assert _get(handler, "project:demo")["run_started_at"] == 1234.5

    monkeypatch.delitem(started, "project:demo")
    monkeypatch.setitem(started, "default", 99.0)

    assert "run_started_at" not in _get(handler, "project:demo")


@pytest.mark.parametrize(
    ("key", "chat_id", "transcript"),
    [
        ("websocket:default", "default", "websocket:default"),
        ("project:viaggio-pazzo", "project:viaggio-pazzo", "websocket:project:viaggio-pazzo"),
        ("websocket:project:x", "project:x", "websocket:project:x"),
    ],
)
def test_the_key_forms(key: str, chat_id: str, transcript: str) -> None:
    assert webui_chat_id(key) == chat_id
    assert webui_transcript_key(key) == transcript


def test_the_reconstructed_history_drops_old_subagent_returns_too(handler) -> None:
    """Un quaderno senza trascrizione si ridisegna dalla sessione: stesso filtro."""
    msgs = _get(handler, "project:demo")["messages"]

    assert [m["content"] for m in msgs if m.get("role") == "user"] == ["sistema la pagina"]
    assert msgs[-1]["content"] == "Pagina spezzata in due."


def test_a_user_quoting_a_subagent_line_keeps_the_bubble(tmp_path, monkeypatch) -> None:
    """Il riconoscimento guarda la testa del template, non una parola: chi cita
    una riga d'annuncio in mezzo a un messaggio scrive ancora lui."""
    from jenny.webui.transcript import build_webui_thread_response

    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)
    quoted = "perche' dice [Subagent 'x' failed]?\n\nTask: niente"
    out = build_webui_thread_response(
        "websocket:project:demo",
        session_messages=[
            {"role": "user", "content": quoted},
            {"role": "assistant", "content": "Perche' e' fallito."},
        ],
    )

    assert out is not None
    assert out["messages"][0]["content"] == quoted
