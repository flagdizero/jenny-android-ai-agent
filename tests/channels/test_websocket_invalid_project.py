"""Un progetto che non si può aprire viene rifiutato, non silenziosamente dirottato.

Il difetto: una cartella sotto `wikis/` il cui nome non passa
`is_valid_project_name` (`Ricerca ETF`, `università`, `project (2026)`) veniva
elencata da `/api/projects` e mostrata dal chip, ma
`WebSocketChannel._envelope_chat_id` ne riscriveva il `chat_id` sulla chat
personale. Tre guasti in un colpo, tutti muti:

- lo scope del turno diventava `default()`, cioè l'installazione intera scrivibile;
- `session_kind` diventava `personal`, quindi il contenuto alimentava `MEMORY.md`
  via Dream — la cosa precisa che le sessioni-progetto esistono per impedire;
- la schermata del progetto si vedeva servire la trascrizione personale.

E nessuno se ne accorgeva: l'eco `attached` il client non la guarda.

Qui si verifica il lato canale. Il lato route (`/api/projects` non offre quel che
non si apre) sta in `tests/webui/test_project_create.py`.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from jenny.channels.websocket import WebSocketChannel, WebSocketConfig
from jenny.webui.gateway_services import build_gateway_services

# Nomi che una cartella vera può avere e una sessione no. Non ipotetici: sono
# quelli che un utente italiano scrive per primo.
_UNOPENABLE = [
    "project:Ricerca ETF",
    "project:università",
    "project:perché",
    "project:progetto (2026)",
    "project:../fuori",
]


def _make_channel() -> WebSocketChannel:
    bus = MagicMock()
    bus.publish_inbound = AsyncMock()
    cfg = {"enabled": True, "allowFrom": ["*"], "websocketRequiresToken": False}
    parsed = WebSocketConfig.model_validate(cfg)
    gateway = build_gateway_services(
        config=parsed,
        bus=bus,
        session_manager=None,
        workspace_path=Path.cwd(),
        default_restrict_to_workspace=False,
        runtime_model_name=None,
    )
    channel = WebSocketChannel(cfg, bus, gateway=gateway)
    channel._handle_message = AsyncMock()  # type: ignore[method-assign]
    return channel


def _events(conn: AsyncMock) -> list[dict]:
    """Gli eventi di controllo mandati su quella connessione, decodificati."""
    out = []
    for call in conn.send.await_args_list:
        try:
            out.append(json.loads(call.args[0]))
        except (ValueError, IndexError):  # pragma: no cover - frame non JSON
            pass
    return out


class TestAnImpossibleProjectIsRejected:
    @pytest.mark.parametrize("chat_id", _UNOPENABLE)
    async def test_a_message_does_not_become_a_personal_turn(self, chat_id):
        """L'asserzione che conta: **nessun turno**. Prima ne partiva uno, e
        partiva nella chat personale."""
        channel = _make_channel()
        conn = AsyncMock()

        await channel._dispatch_envelope(
            conn, "client-1", {"type": "message", "chat_id": chat_id, "content": "ciao"}
        )

        channel._handle_message.assert_not_awaited()
        errors = [e for e in _events(conn) if e.get("event") == "error"]
        assert len(errors) == 1, _events(conn)
        assert errors[0]["reason"] == "invalid_project_name"
        # Il client mostra `detail` così com'è (`mobile-jenny.js`, `case 'error'`):
        # deve essere una frase, non un codice.
        assert "cannot be opened" in errors[0]["detail"]
        # Il nome arriva da un client e non torna indietro nel frame: la sua
        # lunghezza e il suo contenuto non sono nostri.
        assert chat_id.removeprefix("project:") not in errors[0]["detail"]

    @pytest.mark.parametrize("chat_id", _UNOPENABLE)
    async def test_an_attach_attaches_nothing(self, chat_id):
        """Agganciare la connessione a una chiave che nessun turno userà mai la
        iscriverebbe a un canale morto: il client resterebbe in attesa."""
        channel = _make_channel()
        conn = AsyncMock()

        await channel._dispatch_envelope(conn, "client-1", {"type": "attach", "chat_id": chat_id})

        assert channel._conn_chats.get(conn) is None
        assert channel._subs == {}
        events = _events(conn)
        assert [e.get("event") for e in events] == ["error"]

    async def test_media_are_not_even_decoded(self, tmp_path):
        """Il rifiuto arriva **prima** della scrittura su disco: un frame
        rifiutato non deve lasciare file."""
        channel = _make_channel()
        conn = AsyncMock()

        await channel._dispatch_envelope(
            conn,
            "client-1",
            {
                "type": "message",
                "chat_id": "project:Ricerca ETF",
                "content": "guarda",
                # Volutamente malformato: se venisse guardato, l'errore sarebbe
                # `image_rejected` e non il nostro.
                "media": "non-una-lista",
            },
        )

        errors = [e for e in _events(conn) if e.get("event") == "error"]
        assert len(errors) == 1
        assert errors[0]["reason"] == "invalid_project_name"


class TestWhatMustNotChange:
    """Il fallback silenzioso è la risposta **giusta** per la spazzatura, e resta."""

    @pytest.mark.parametrize("chat_id", ["default", "qualsiasi-cosa", "projectx", 12, None])
    async def test_an_unrecognized_form_stays_the_personal_chat(self, chat_id):
        channel = _make_channel()
        conn = AsyncMock()
        envelope: dict = {"type": "message", "content": "ciao"}
        if chat_id is not None:
            envelope["chat_id"] = chat_id

        await channel._dispatch_envelope(conn, "client-1", envelope)

        channel._handle_message.assert_awaited_once()
        assert channel._handle_message.call_args.kwargs["chat_id"] == "default"

    async def test_a_valid_project_opens_its_conversation(self):
        channel = _make_channel()
        conn = AsyncMock()

        await channel._dispatch_envelope(
            conn,
            "client-1",
            {"type": "message", "chat_id": "project:ricerca-etf", "content": "ciao"},
        )

        channel._handle_message.assert_awaited_once()
        assert channel._handle_message.call_args.kwargs["chat_id"] == "project:ricerca-etf"
        assert not [e for e in _events(conn) if e.get("event") == "error"]

    async def test_an_attach_to_a_valid_project_attaches_and_answers(self):
        channel = _make_channel()
        conn = AsyncMock()

        await channel._dispatch_envelope(
            conn, "client-1", {"type": "attach", "chat_id": "project:ricerca-etf"}
        )

        assert channel._conn_chats[conn] == {"project:ricerca-etf"}
        attached = [e for e in _events(conn) if e.get("event") == "attached"]
        assert attached and attached[0]["chat_id"] == "project:ricerca-etf"
