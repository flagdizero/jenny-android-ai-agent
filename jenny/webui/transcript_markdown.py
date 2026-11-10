"""Riscrittura dei media markdown per la WebUI (estratto da transcript.py).

Converte i path di media locali dentro il workspace, referenziati in markdown
(``![...](path)``), in URL firmati ``/api/media/...``. Gruppo self-contained
(regex + estensioni + funzione pura); dipende solo da stdlib + workspace policy.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from jenny.utils.path import safe_expanduser

MARKDOWN_LOCAL_IMAGE_RE = re.compile(
    r"!\[([^\]]*)\]\((<[^>]+>|[^)\s]+)(\s+(?:\"[^\"]*\"|'[^']*'))?\)"
)
INLINE_MARKDOWN_IMAGE_EXTS: frozenset[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}
)
INLINE_MARKDOWN_VIDEO_EXTS: frozenset[str] = frozenset({".mp4", ".mov", ".webm"})
INLINE_MARKDOWN_MEDIA_EXTS = INLINE_MARKDOWN_IMAGE_EXTS | INLINE_MARKDOWN_VIDEO_EXTS


def rewrite_local_markdown_images(
    text: str,
    *,
    workspace_path: Path,
    sign_path: Callable[[Path], Mapping[str, Any] | None],
) -> str:
    """Rewrite markdown media paths inside the workspace to signed WebUI media URLs."""
    if "![" not in text:
        return text

    def resolve_url(raw_url: str) -> str | None:
        url = raw_url.strip()
        if url.startswith("<") and url.endswith(">"):
            url = url[1:-1].strip()
        if not url or url.startswith(("/api/media/", "#")):
            return None
        parsed = urlparse(url)
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
            return None
        path_text = unquote(url)
        if Path(path_text).suffix.lower() not in INLINE_MARKDOWN_MEDIA_EXTS:
            return None
        try:
            candidate = safe_expanduser(path_text)
        except (RuntimeError, OSError):
            candidate = Path(path_text)
        if not candidate.is_absolute():
            candidate = workspace_path / candidate
        try:
            resolved = candidate.resolve(strict=False)
            resolved.relative_to(workspace_path)
        except (OSError, ValueError):
            return None
        if not resolved.is_file():
            return None
        signed = sign_path(resolved)
        return str(signed.get("url")) if signed and signed.get("url") else None

    def replace(match: re.Match[str]) -> str:
        signed_url = resolve_url(match.group(2))
        if not signed_url:
            return match.group(0)
        title = match.group(3) or ""
        return f"![{match.group(1)}]({signed_url}{title})"

    return MARKDOWN_LOCAL_IMAGE_RE.sub(replace, text)


def media_kind_from_name(name: str) -> str:
    ext = Path(name).suffix.lower()
    if ext in INLINE_MARKDOWN_IMAGE_EXTS:
        return "image"
    if ext in INLINE_MARKDOWN_VIDEO_EXTS:
        return "video"
    return "file"
