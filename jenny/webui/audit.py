"""Audit shared logic — Python port of audit-shared TypeScript package.

Handles serialization, anchor computation, and ID generation
for markdown-based audit files with YAML frontmatter.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import datetime

# ── Constants ───────────────────────────────────────────────────────────────

_CONTEXT_CHARS = 80

_VALID_SOURCES: tuple[str, ...] = ("obsidian-plugin", "web-viewer", "manual")
_VALID_STATUSES: tuple[str, ...] = ("open", "resolved")

_ID_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")
_FRONTMATTER_RE = re.compile(r"^---\n([\s\S]*?)\n---\n?([\s\S]*)$")


# ── Schema ──────────────────────────────────────────────────────────────────

@dataclass
class _Anchor:
    target_lines: tuple[int, int]
    anchor_before: str
    anchor_text: str
    anchor_after: str


@dataclass
class AuditEntry:
    """Un riscontro umano ancorato a un punto di una pagina.

    **Niente ``severity``, e non e' una dimenticanza** (tolta il 22/09/2026).
    Erano quattro livelli — info/suggest/warn/error — che chi segnala doveva
    scegliere prima di scrivere: un campo nato per una coda di smistamento,
    cioe' per una squadra. Qui chi segnala, chi corregge e chi possiede il
    quaderno sono la stessa persona, e nessuno vuole dare un voto alla propria
    lamentela. Quel che resta e' l'ordine di arrivo, che e' la fila di una coda
    vera.
    """

    id: str
    target: str
    target_lines: tuple[int, int]
    anchor_before: str
    anchor_text: str
    anchor_after: str
    author: str
    source: str
    created: str
    status: str
    body: str = ""

    def validate(self) -> None:
        """Basic validation. Raises ValueError on issues."""
        if not _ID_RE.match(self.id):
            raise ValueError(f"invalid audit id: {self.id}")
        if not self.target:
            raise ValueError("target is required")
        if not self.anchor_text:
            raise ValueError("anchor_text is required")
        if self.source not in _VALID_SOURCES:
            raise ValueError(f"source must be one of {_VALID_SOURCES}")
        if self.status not in _VALID_STATUSES:
            raise ValueError(f"status must be one of {_VALID_STATUSES}")
        if not self.author:
            raise ValueError("author is required")


# ── Anchor computation ──────────────────────────────────────────────────────

def compute_anchor(
    file_text: str,
    sel_start: int,
    sel_end: int,
    context: int = _CONTEXT_CHARS,
) -> _Anchor:
    """Compute an anchor from a file's full text and a selection range."""
    if sel_start < 0 or sel_end > len(file_text) or sel_start >= sel_end:
        raise ValueError(
            f"compute_anchor: invalid range [{sel_start}, {sel_end}) "
            f"for text of length {len(file_text)}"
        )
    line_start, line_end = _offsets_to_lines(file_text, sel_start, sel_end)
    before_start = max(0, sel_start - context)
    after_end = min(len(file_text), sel_end + context)
    return _Anchor(
        target_lines=(line_start, line_end),
        anchor_before=file_text[before_start:sel_start],
        anchor_text=file_text[sel_start:sel_end],
        anchor_after=file_text[sel_end:after_end],
    )


def _offsets_to_lines(text: str, start: int, end: int) -> tuple[int, int]:
    """1-indexed line numbers for a half-open character range [start, end)."""
    line = 1
    line_start = 1
    line_end = 1
    seen_start = False
    seen_end = False
    for i, ch in enumerate(text):
        if not seen_start and i >= start:
            line_start = line
            seen_start = True
        if not seen_end and i >= end:
            line_end = line
            seen_end = True
            break
        if ch == "\n":
            line += 1
    if not seen_start:
        line_start = line
    if not seen_end:
        line_end = line
    if line_end < line_start:
        line_end = line_start
    return line_start, line_end


# ── Serialization ───────────────────────────────────────────────────────────

def to_markdown(entry: AuditEntry) -> str:
    """Render an AuditEntry as full markdown with YAML frontmatter."""
    entry.validate()
    front = {
        "id": entry.id,
        "target": entry.target,
        "target_lines": list(entry.target_lines),
        "anchor_before": entry.anchor_before,
        "anchor_text": entry.anchor_text,
        "anchor_after": entry.anchor_after,
        "author": entry.author,
        "source": entry.source,
        "created": entry.created,
        "status": entry.status,
    }
    import yaml

    yml = yaml.dump(
        front,
        width=0,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )
    body_text = entry.body.strip() if entry.body.strip() else _default_body()
    return f"---\n{yml}---\n\n{body_text}\n"


def from_markdown(text: str) -> AuditEntry:
    """Parse audit markdown back into AuditEntry."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise ValueError("audit file missing YAML frontmatter")
    import yaml

    try:
        front_raw = yaml.safe_load(m.group(1))
    except yaml.YAMLError as e:
        raise ValueError(f"invalid YAML in audit frontmatter: {e}") from e
    if not isinstance(front_raw, dict):
        raise ValueError("audit frontmatter must be a YAML mapping")
    body = m.group(2).lstrip("\n")
    target_lines_raw = front_raw.get("target_lines", [0, 0])
    if not isinstance(target_lines_raw, list) or len(target_lines_raw) != 2:
        raise ValueError("target_lines must be a list of 2 integers")
    target_lines = (int(target_lines_raw[0]), int(target_lines_raw[1]))
    entry = AuditEntry(
        id=front_raw.get("id", ""),
        target=front_raw.get("target", ""),
        target_lines=target_lines,
        anchor_before=front_raw.get("anchor_before", ""),
        anchor_text=front_raw.get("anchor_text", ""),
        anchor_after=front_raw.get("anchor_after", ""),
        author=front_raw.get("author", ""),
        source=front_raw.get("source", "web-viewer"),
        created=front_raw.get("created", datetime.now().isoformat()),
        status=front_raw.get("status", "open"),
        body=body,
    )
    entry.validate()
    return entry


def _default_body() -> str:
    return (
        "# Comment\n\n"
        "<!-- describe the feedback here -->\n\n"
        "# Resolution\n\n"
        "<!-- filled in when the audit is processed -->\n"
    )


# ── ID generation ───────────────────────────────────────────────────────────

def make_id() -> str:
    """Generate an audit ID: YYYYMMDD-HHMMSS-xxxx."""
    now = datetime.now()
    rand = secrets.token_hex(2)
    return now.strftime("%Y%m%d-%H%M%S-") + rand


def filename_for(audit_id: str, slug: str) -> str:
    """Build a filesystem-safe filename from id and slug."""
    safe_slug = re.sub(r"[^\w\-]", "_", slug.strip())[:40]
    return f"{audit_id}-{safe_slug or 'audit'}.md"
