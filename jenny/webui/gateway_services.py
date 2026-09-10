"""Composition helpers for the embedded WebUI gateway."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from loguru import logger as default_logger

from jenny.webui.commands import CommandContext
from jenny.webui.media_gateway import WebUIMediaGateway
from jenny.webui.transcript import WebUITranscriptRecorder
from jenny.webui.workspaces import WebUIWorkspaceController
from jenny.webui.ws_http import GatewayHTTPHandler


@dataclass(frozen=True)
class GatewayServices:
    """Explicit dependencies shared by WebSocket transport and HTTP routes."""

    http: GatewayHTTPHandler
    media: WebUIMediaGateway
    transcripts: WebUITranscriptRecorder
    workspaces: WebUIWorkspaceController
    # Dipendenze dei comandi con payload (scrittura file, note di audit). Le
    # serve il canale WebSocket, non le route: gli header HTTP del gateway non
    # possono trasportare contenuto (8 KB per riga, solo ISO-8859-1), un frame
    # WS sì. Vedi ``webui.commands`` e ``channels.ws_rpc``.
    commands: CommandContext
    session_manager: Any | None
    # Getter late-binding del ``SubagentManager``, lo stesso che ricevono le
    # route HTTP. Serve anche al canale WebSocket: il pump dell'attività dei
    # subagent legge ``manager.activity``, e passarglielo da qui evita sia un
    # global sia un secondo parametro nel costruttore del canale. Resta un
    # getter (non l'oggetto) perché durante l'onboarding l'agente non esiste
    # ancora e il gateway serve già la WebUI.
    get_subagent_manager: Callable[[], Any | None] | None = None


def _current_workspace_root() -> Path:
    """Radice del workspace corrente (import lazy come nelle route)."""
    from jenny.config.paths import get_workspace_path

    return get_workspace_path()


def build_gateway_services(
    *,
    config: Any,
    bus: Any,
    session_manager: Any | None,
    workspace_path: Path,
    default_restrict_to_workspace: bool,
    runtime_model_name: Any | None,
    disabled_skills: set[str] | None = None,
    snapshot_service: Any | None = None,
    # Getter late-binding del ``SubagentManager`` (attributo ``subagents``
    # dell'AgentLoop). Non un global: l'agente può essere creato dopo il
    # gateway (onboarding), quindi la route lo risolve a ogni chiamata.
    get_subagent_manager: Callable[[], Any | None] | None = None,
    # Getter late-binding del ``CronService``, come quello sopra: il pannello
    # della programmazione lo risolve a ogni chiamata.
    get_cron_service: Callable[[], Any | None] | None = None,
    logger: Any = default_logger,
    onboarding_event: Any | None = None,
    on_settings_changed: Callable[[], None] | None = None,
    on_telegram_changed: Callable[[], None] | None = None,
    on_jobs_changed: Callable[[str], None] | None = None,
) -> GatewayServices:
    media = WebUIMediaGateway(
        workspace_path=workspace_path,
        logger=logger,
    )
    transcripts = WebUITranscriptRecorder(log=logger)
    workspaces = WebUIWorkspaceController(
        session_manager=session_manager,
        default_workspace=workspace_path,
        default_restrict_to_workspace=default_restrict_to_workspace,
        # La stessa cartella che il picker elenca (``/api/projects``) e che il
        # turno risolve: se le tre divergono, il chip mostra progetti che lo
        # scope non trova.
        projects_subdir=getattr(getattr(config, "wiki", None), "wikis_dir", "wikis"),
    )
    http = GatewayHTTPHandler(
        config=config,
        session_manager=session_manager,
        runtime_model_name=runtime_model_name,
        bus=bus,
        media=media,
        workspaces=workspaces,
        skills_workspace_path=workspace_path,
        disabled_skills=disabled_skills,
        snapshot_service=snapshot_service,
        get_subagent_manager=get_subagent_manager,
        get_cron_service=get_cron_service,
        log=logger,
        onboarding_event=onboarding_event,
        on_settings_changed=on_settings_changed,
        on_telegram_changed=on_telegram_changed,
        on_jobs_changed=on_jobs_changed,
    )
    return GatewayServices(
        http=http,
        media=media,
        transcripts=transcripts,
        workspaces=workspaces,
        # Radice risolta a call-time come per le route del file manager: un
        # cambio di workspace a runtime non deve lasciare i comandi ancorati
        # alla vecchia directory.
        commands=CommandContext(
            get_workspace_root=_current_workspace_root,
            # ``project.delete`` toglie i file di una sessione: se quella restasse
            # in cache, il primo salvataggio la riscriverebbe sotto il nome
            # appena liberato. Risolto a call-time perche' il session manager e'
            # opzionale — senza di lui non c'e' nessuna cache da sgomberare, e
            # nemmeno nessuno che possa riscrivere il file.
            invalidate_session=lambda key: (
                session_manager.invalidate(key) if session_manager is not None else None
            ),
        ),
        session_manager=session_manager,
        get_subagent_manager=get_subagent_manager,
    )
