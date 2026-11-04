"""Test per ``jenny.channels.telegram_format`` (conversione md→HTML e chunking)."""

from __future__ import annotations

from jenny.channels.telegram_format import (
    TELEGRAM_MAX_LEN,
    markdown_to_telegram_html,
    split_message,
)

# --- markdown_to_telegram_html ------------------------------------------------


def test_bold_italic_and_code() -> None:
    out = markdown_to_telegram_html("**bold** e *ital* e `x<y`")
    assert "<b>bold</b>" in out
    assert "<i>ital</i>" in out
    assert "<code>x&lt;y</code>" in out


def test_html_special_chars_escaped() -> None:
    out = markdown_to_telegram_html("a < b & c > d")
    assert out == "a &lt; b &amp; c &gt; d"


def test_fenced_code_block_preserved_verbatim() -> None:
    out = markdown_to_telegram_html("```python\nif a < b:\n    pass\n```")
    assert out.startswith("<pre>")
    assert "if a &lt; b:" in out
    # Il contenuto del fence non subisce conversioni markdown.
    assert "<b>" not in out


def test_link_conversion() -> None:
    out = markdown_to_telegram_html("vedi [qui](https://example.com/a?b=1)")
    assert '<a href="https://example.com/a?b=1">qui</a>' in out


def test_heading_becomes_bold() -> None:
    out = markdown_to_telegram_html("## Titolo\ncorpo")
    assert "<b>Titolo</b>" in out


def test_underscores_inside_words_untouched() -> None:
    out = markdown_to_telegram_html("nome_file_lungo")
    assert out == "nome_file_lungo"


# --- split_message -------------------------------------------------------------


def test_short_message_single_chunk() -> None:
    assert split_message("ciao") == ["ciao"]


def test_empty_message_no_chunks() -> None:
    assert split_message("   ") == []


def test_split_prefers_paragraph_boundary() -> None:
    text = ("a" * 3000) + "\n\n" + ("b" * 3000)
    chunks = split_message(text, 4096)
    assert chunks == ["a" * 3000, "b" * 3000]


def test_split_hard_cut_without_boundaries() -> None:
    text = "x" * 9000
    chunks = split_message(text, 4096)
    assert all(len(c) <= 4096 for c in chunks)
    assert "".join(chunks) == text


def test_default_limit_is_telegram_max() -> None:
    chunks = split_message("y" * (TELEGRAM_MAX_LEN + 10))
    assert len(chunks) == 2


def test_wiki_links_become_words_as_in_notifications() -> None:
    """``[[pagina]]`` arrivava su Telegram con le parentesi: la regola è quella
    di ``utils.plain_text`` (etichetta, o nome della pagina a parole)."""
    out = markdown_to_telegram_html("Vedi [[piano-concordato]] e [[orto/semine|le semine]].")
    assert out == "Vedi piano concordato e le semine."
    assert markdown_to_telegram_html("`[[resta]]`") == "<code>[[resta]]</code>"
