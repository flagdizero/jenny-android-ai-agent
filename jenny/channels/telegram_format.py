"""Conversione markdown-agente → HTML Telegram e chunking dei messaggi.

Funzioni pure, senza I/O. Telegram supporta un sottoinsieme di HTML
(``<b> <i> <code> <pre> <a>``); tutto il resto va escapato. La strategia è:
chunking sul testo grezzo (confini di paragrafo/riga), poi conversione di
ogni chunk. Un HTML malformato residuo è coperto dal fallback a plain text
del canale (retry senza ``parse_mode`` su errore 400).
"""

from __future__ import annotations

import html
import re

from jenny.utils.plain_text import wikilinks_as_text

# Limite hard di Telegram per il testo di un messaggio.
TELEGRAM_MAX_LEN = 4096

_FENCE_RE = re.compile(r"```[a-zA-Z0-9_+-]*\n(.*?)```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_ITALIC_RE = re.compile(r"(?<!\w)\*([^*\n]+)\*(?!\w)")
_ITALIC_UNDERSCORE_RE = re.compile(r"(?<!\w)_([^_\n]+)_(?!\w)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_HEADING_RE = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)
_PLACEHOLDER = "\x00JBLK{}\x00"


def markdown_to_telegram_html(text: str) -> str:
    """Converte il markdown dell'agente nel sottoinsieme HTML di Telegram."""
    blocks: list[str] = []

    def _stash_fence(m: re.Match[str]) -> str:
        blocks.append(f"<pre>{html.escape(m.group(1).rstrip())}</pre>")
        return _PLACEHOLDER.format(len(blocks) - 1)

    def _stash_inline(m: re.Match[str]) -> str:
        blocks.append(f"<code>{html.escape(m.group(1))}</code>")
        return _PLACEHOLDER.format(len(blocks) - 1)

    def _stash_link(m: re.Match[str]) -> str:
        label = html.escape(m.group(1))
        url = html.escape(m.group(2), quote=True)
        blocks.append(f'<a href="{url}">{label}</a>')
        return _PLACEHOLDER.format(len(blocks) - 1)

    # I blocchi verbatim vanno estratti PRIMA dell'escape globale.
    out = _FENCE_RE.sub(_stash_fence, text)
    out = _INLINE_CODE_RE.sub(_stash_inline, out)
    # Un link del wiki non porta da nessuna parte in Telegram: diventa parole,
    # con la stessa regola delle notifiche.
    out = wikilinks_as_text(out)
    out = _LINK_RE.sub(_stash_link, out)
    out = html.escape(out)
    out = _HEADING_RE.sub(lambda m: f"<b>{m.group(1)}</b>", out)
    out = _BOLD_RE.sub(lambda m: f"<b>{m.group(1)}</b>", out)
    out = _ITALIC_RE.sub(lambda m: f"<i>{m.group(1)}</i>", out)
    out = _ITALIC_UNDERSCORE_RE.sub(lambda m: f"<i>{m.group(1)}</i>", out)
    for i, block in enumerate(blocks):
        out = out.replace(_PLACEHOLDER.format(i), block)
    return out.strip()


def split_message(text: str, limit: int = TELEGRAM_MAX_LEN) -> list[str]:
    """Divide *text* in chunk ≤ *limit* preferendo confini di paragrafo/riga.

    Il limite è applicato al testo grezzo: la conversione HTML può allungare
    il risultato, quindi il chiamante deve usare un limite prudenziale.
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        # Preferenza: doppio newline (paragrafo), poi newline, poi spazio.
        cut = window.rfind("\n\n")
        if cut < limit // 4:
            cut = window.rfind("\n")
        if cut < limit // 4:
            cut = window.rfind(" ")
        if cut < limit // 4:
            cut = limit  # nessun confine utile: taglio hard
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return [c for c in chunks if c]
