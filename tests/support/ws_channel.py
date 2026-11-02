"""Un ``WebSocketChannel`` vero coi suoi ``GatewayServices``, montato per i test.

Prima ogni file lo costruiva a mano: config validata, ``build_gateway_services``
con gli stessi sei argomenti, poi il canale. Qui la sequenza è una sola.

La config del canale resta quella passata: un dict il canale lo rivalida per
conto suo (un'istanza diversa da quella dei servizi, come facevano quasi tutti
i file); una ``WebSocketConfig`` già fatta la condividono canale e servizi.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from jenny.channels.websocket import WebSocketChannel, WebSocketConfig
from jenny.webui.gateway_services import build_gateway_services


def local_ws_cfg(**overrides: Any) -> dict[str, Any]:
    """La config minima di un canale locale senza token, con le chiavi in più."""
    cfg: dict[str, Any] = {"enabled": True, "allowFrom": ["*"], "websocketRequiresToken": False}
    cfg.update(overrides)
    return cfg


def make_bus() -> MagicMock:
    """Un bus finto il cui ``publish_inbound`` si può attendere."""
    bus = MagicMock()
    bus.publish_inbound = AsyncMock()
    return bus


def make_ws_channel(
    cfg: dict[str, Any] | WebSocketConfig | None = None,
    bus: Any | None = None,
    *,
    session_manager: Any | None = None,
    workspace_path: Path | None = None,
    runtime_model_name: Any | None = None,
    ui_query: Any | None = None,
    **gateway_kwargs: Any,
) -> WebSocketChannel:
    """Il canale su ``cfg`` (default ``local_ws_cfg()``) e ``bus`` (default ``make_bus()``).

    ``workspace_path`` vale ``Path.cwd()`` se assente; ``gateway_kwargs`` va a
    ``build_gateway_services`` così com'è (``get_subagent_manager``, ...).
    """
    if cfg is None:
        cfg = local_ws_cfg()
    if bus is None:
        bus = make_bus()
    parsed = cfg if isinstance(cfg, WebSocketConfig) else WebSocketConfig.model_validate(cfg)
    gateway = build_gateway_services(
        config=parsed,
        bus=bus,
        session_manager=session_manager,
        workspace_path=workspace_path or Path.cwd(),
        default_restrict_to_workspace=False,
        runtime_model_name=runtime_model_name,
        **gateway_kwargs,
    )
    return WebSocketChannel(cfg, bus, gateway=gateway, ui_query=ui_query)
