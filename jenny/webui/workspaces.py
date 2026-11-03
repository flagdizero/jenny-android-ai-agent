"""Persisted WebUI project workspace state."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jenny.config.paths import DEFAULT_WIKIS_DIR
from jenny.security.workspace_access import (
    WORKSPACE_SCOPE_METADATA_KEY,
    WorkspaceScope,
    WorkspaceScopeError,
    WorkspaceScopeResolver,
    default_workspace_scope,
    validate_workspace_scope_payload,
)
from jenny.session.keys import is_project_session_key


class WebUIWorkspaceController:
    """Own WebUI project scope persistence and validation."""

    def __init__(
        self,
        *,
        session_manager: Any | None,
        default_workspace: Path,
        default_restrict_to_workspace: bool,
        projects_subdir: str = DEFAULT_WIKIS_DIR,
    ) -> None:
        self._sessions = session_manager
        self._default_workspace = default_workspace
        self._default_restrict_to_workspace = default_restrict_to_workspace
        # Stesso risolutore del turno, cosi' quel che il chip mostra e quel che
        # l'agente riceve non possono divergere: una sola regola chiave->cartella.
        self._resolver = WorkspaceScopeResolver(
            default_workspace=default_workspace,
            default_restrict_to_workspace=default_restrict_to_workspace,
            projects_subdir=projects_subdir,
        )

    def default_scope(self) -> WorkspaceScope:
        return default_workspace_scope(
            self._default_workspace,
            self._default_restrict_to_workspace,
        )

    def scope_for_session_key(self, session_key: str) -> WorkspaceScope:
        # Per una sessione-progetto la cartella si deduce dalla chiave, non dai
        # metadati: e' la stessa regola del turno, e vale **anche prima del primo
        # messaggio**. Leggendo i metadati, aprire un progetto appena creato
        # avrebbe mostrato "sessione personale" sopra il composer finche' non ci
        # si scriveva dentro — cioe' la cosa che il chip esiste per non fare.
        if is_project_session_key(session_key):
            return self._resolver.for_project(session_key)
        if self._sessions is None:
            return self.default_scope()
        metadata_reader = getattr(self._sessions, "read_session_metadata", None)
        if callable(metadata_reader):
            data = metadata_reader(session_key)
        else:
            data = self._sessions.read_session_file(session_key)
        metadata = data.get("metadata", {}) if isinstance(data, dict) else {}
        if not isinstance(metadata, dict) or WORKSPACE_SCOPE_METADATA_KEY not in metadata:
            return self.default_scope()
        try:
            return validate_workspace_scope_payload(
                metadata.get(WORKSPACE_SCOPE_METADATA_KEY),
                default_workspace=self._default_workspace,
                default_restrict_to_workspace=self._default_restrict_to_workspace,
            )
        except WorkspaceScopeError:
            return self.default_scope()
