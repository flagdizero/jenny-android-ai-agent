"""Workspace-scoped source preview payloads for the WebUI."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from jenny.security.workspace_access import WorkspaceScope
from jenny.security.workspace_policy import WorkspaceBoundaryError, resolve_allowed_path
from jenny.utils.wiki_paths import is_wiki_root

MAX_FILE_PREVIEW_BYTES = 384 * 1024


class WebUIFilePreviewError(ValueError):
    """Raised when a file cannot be previewed through the WebUI."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def file_preview_payload(
    raw_path: str | None,
    *,
    scope: WorkspaceScope,
    max_bytes: int = MAX_FILE_PREVIEW_BYTES,
    workspace_root: Path | None = None,
) -> dict[str, Any]:
    """Return a text preview for a file inside the session workspace.

    *workspace_root*, se passato, aggiunge ``workspace_path``: il percorso dal
    workspace, che e' quello con cui l'officina apre il file nell'editor.
    """

    path = _clean_preview_path(raw_path)
    if not path:
        raise WebUIFilePreviewError(400, "missing path")
    if len(path) > 4096:
        raise WebUIFilePreviewError(400, "path is too long")

    try:
        resolved = _resolve_preview_file(path, scope.project_path)
    except FileNotFoundError as e:
        raise WebUIFilePreviewError(404, "file not found") from e
    except WorkspaceBoundaryError as e:
        raise WebUIFilePreviewError(403, "file is outside the current workspace") from e
    except OSError as e:
        raise WebUIFilePreviewError(400, "invalid path") from e

    try:
        with open(resolved, "rb") as f:
            raw = f.read(max_bytes + 1)
    except OSError as e:
        raise WebUIFilePreviewError(500, "failed to read file") from e

    if b"\0" in raw[:4096]:
        raise WebUIFilePreviewError(415, "binary files cannot be previewed")

    truncated = len(raw) > max_bytes
    preview_bytes = raw[:max_bytes]
    try:
        content = preview_bytes.decode("utf-8")
    except UnicodeDecodeError:
        content = preview_bytes.decode("utf-8", errors="replace")

    display_path = _display_path(resolved, scope.project_path)
    payload: dict[str, Any] = {
        "path": str(resolved),
        "display_path": display_path,
        "project_path": str(scope.project_path),
        "language": _language_for_path(resolved),
        "content": content,
        "size": resolved.stat().st_size,
        "truncated": truncated,
    }
    if workspace_root is not None:
        try:
            payload["workspace_path"] = resolved.relative_to(workspace_root.resolve()).as_posix()
        except ValueError:
            pass
    return payload


def _resolve_preview_file(path: str, project: Path) -> Path:
    """Il file da mostrare, dentro *project*.

    **In un quaderno si riprova sotto ``wiki/``.** Le pagine stanno in
    ``wikis/<nome>/wiki/``, e nella chat del quaderno Jenny le nomina da li':
    ``entities/Pothos.md``, non ``wiki/entities/Pothos.md``. Cercato dalla
    radice del progetto quel percorso non esiste, e l'anteprima rispondeva 404
    («Failed to load» sul Titan 2, 26/09/2026). Un file che esiste davvero alla
    radice vince; il confine resta *project* in entrambi i tentativi.
    """
    try:
        found = resolve_allowed_path(path, workspace=project, allowed_root=project, strict=True)
    except FileNotFoundError:
        found = None
    if found is not None and found.is_file():
        return found
    if is_wiki_root(project):
        try:
            in_wiki = resolve_allowed_path(
                path, workspace=project / "wiki", allowed_root=project, strict=True
            )
        except FileNotFoundError:
            in_wiki = None
        if in_wiki is not None and in_wiki.is_file():
            return in_wiki
    raise FileNotFoundError(path)


def _clean_preview_path(raw_path: str | None) -> str:
    if raw_path is None:
        return ""
    value = raw_path.strip()
    if not value:
        return ""
    if value.startswith("file://"):
        parsed = urlparse(value)
        value = unquote(parsed.path)
        if re.match(r"^/[A-Za-z]:[\\/]", value):
            value = value[1:]
    else:
        value = unquote(value)
    value = value.split("?", 1)[0].split("#", 1)[0].strip()
    if not re.match(r"^[A-Za-z]:[\\/]", value):
        value = re.sub(r":\d+(?::\d+)?$", "", value)
    return value


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _language_for_path(path: Path) -> str:
    name = path.name.lower()
    ext = path.suffix.lower().lstrip(".")
    if name == "dockerfile":  # dockerfile syntax highlighting only; no Docker runtime available on Android
        return "dockerfile"
    return {
        "cjs": "javascript",
        "css": "css",
        "cts": "typescript",
        "html": "html",
        "js": "javascript",
        "json": "json",
        "jsonl": "json",
        "jsx": "jsx",
        "md": "markdown",
        "mdx": "markdown",
        "mjs": "javascript",
        "mts": "typescript",
        "py": "python",
        "pyi": "python",
        "scss": "scss",
        "sh": "text",
        "toml": "toml",
        "ts": "typescript",
        "tsx": "tsx",
        "yaml": "yaml",
        "yml": "yaml",
    }.get(ext, ext or "text")
