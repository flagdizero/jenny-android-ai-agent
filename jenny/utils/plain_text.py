"""Markdown dell'agente → testo semplice, per le superfici che non lo rendono.

Una notifica di sistema mostra il testo così com'è: ``**Fatto!** Vedi
[il file](https://...)`` arrivava sul telefono con gli asterischi e l'URL, e
dentro i 200 caratteri del corpo il markup si mangiava la parte da leggere.

Stessa strategia di ``jenny/channels/telegram_format.py``: i blocchi verbatim
(codice) si mettono da parte **prima** di toccare il resto, così un ``*`` dentro
un pezzo di codice non diventa enfasi; poi si tolgono i segni e si rimettono i
blocchi, come testo.
"""

from __future__ import annotations

import re

_FENCE_RE = re.compile(r"```[^\n`]*\n?(.*?)```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)\s]*(?:\s+\"[^\"]*\")?\)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)\s]*(?:\s+\"[^\"]*\")?\)")
# Un link del wiki: ``[[pagina]]``, ``[[cartella/pagina#sezione]]``, ``[[pagina|testo]]``.
_WIKILINK_RE = re.compile(r"\[\[([^\[\]|#]+)(?:#[^\[\]|]*)?(?:\|([^\[\]]*))?\]\]")
_HEADING_RE = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+", re.MULTILINE)
_QUOTE_RE = re.compile(r"^[ \t]{0,3}>[ \t]?", re.MULTILINE)
_BULLET_RE = re.compile(r"^([ \t]*)(?:[-*+]|\d+[.)])[ \t]+", re.MULTILINE)
_RULE_RE = re.compile(r"^[ \t]{0,3}(?:[-*_][ \t]*){3,}$", re.MULTILINE)
_BOLD_RE = re.compile(r"(\*\*|__)(.+?)\1", re.DOTALL)
_ITALIC_STAR_RE = re.compile(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])")
_ITALIC_UNDERSCORE_RE = re.compile(r"(?<!\w)_([^_\n]+)_(?!\w)")
_STRIKE_RE = re.compile(r"~~(.+?)~~", re.DOTALL)
_PLACEHOLDER = "\x00PT{}\x00"


def plain_text(md: str) -> str:
    """*md* senza markup: il testo dei link, il codice come testo, niente segni.

    Tolti: titoli (``#``), enfasi (``**``, ``*``, ``_``, ``~~``), citazioni
    (``>``), punti elenco e numeri di lista, righe di separazione, le
    destinazioni dei link e delle immagini (resta il testo). Un link del wiki
    (``[[piano-concordato]]``) diventa il suo testo, o il nome della pagina a
    parole («piano concordato»). Il codice — a
    blocchi o in riga — resta come testo, senza i backtick. Non comprime gli
    spazi: è compito di chi impagina.
    """
    if not md:
        return ""
    blocks: list[str] = []

    def _stash(text: str) -> str:
        blocks.append(text)
        return _PLACEHOLDER.format(len(blocks) - 1)

    out = _FENCE_RE.sub(lambda m: _stash(m.group(1).strip("\n")), md)
    out = _INLINE_CODE_RE.sub(lambda m: _stash(m.group(1)), out)
    out = _IMAGE_RE.sub(lambda m: m.group(1), out)
    out = _LINK_RE.sub(lambda m: m.group(1), out)
    out = _WIKILINK_RE.sub(_wikilink_text, out)
    out = _RULE_RE.sub("", out)
    out = _HEADING_RE.sub("", out)
    out = _QUOTE_RE.sub("", out)
    out = _BULLET_RE.sub(lambda m: m.group(1), out)
    out = _BOLD_RE.sub(lambda m: m.group(2), out)
    out = _STRIKE_RE.sub(lambda m: m.group(1), out)
    out = _ITALIC_STAR_RE.sub(lambda m: m.group(1), out)
    out = _ITALIC_UNDERSCORE_RE.sub(lambda m: m.group(1), out)
    for i, block in enumerate(blocks):
        out = out.replace(_PLACEHOLDER.format(i), block)
    return out.strip()


def _wikilink_text(m: re.Match[str]) -> str:
    """Il testo di un ``[[…]]``: quello dopo ``|``, se c'è; se no il nome della
    pagina senza cartelle né ``.md``, coi trattini come spazi."""
    if m.group(2) and m.group(2).strip():
        return m.group(2).strip()
    name = m.group(1).strip().rsplit("/", 1)[-1].removesuffix(".md")
    return re.sub(r"[-_]+", " ", name).strip() or m.group(0)
