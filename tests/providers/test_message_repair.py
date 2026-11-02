"""Test diretti per jenny/providers/message_repair.py.

``enforce_role_alternation`` è già coperta esaustivamente in
``tests/providers/test_enforce_role_alternation.py``: qui evitiamo di ripetere quei casi e ci
concentriamo su ``sanitize_empty_content``, ``strip_image_content`` e
``strip_image_content_inplace``, non coperte altrove, più un paio di casi
edge di ``enforce_role_alternation`` non toccati dalla suite esistente.
"""

from __future__ import annotations

from jenny.providers.message_repair import (
    enforce_role_alternation,
    sanitize_empty_content,
    strip_image_content,
    strip_image_content_inplace,
)

# ---------------------------------------------------------------------------
# sanitize_empty_content
# ---------------------------------------------------------------------------


def test_sanitize_empty_content_noop_on_healthy_messages() -> None:
    """Messaggi sani (content stringa non vuota) passano invariati."""
    msgs = [
        {"role": "user", "content": "ciao"},
        {"role": "assistant", "content": "risposta"},
    ]
    result = sanitize_empty_content(msgs)
    assert result == msgs
    assert result[0] is msgs[0]  # nessuna copia se non serve


def test_sanitize_empty_content_empty_list() -> None:
    assert sanitize_empty_content([]) == []


def test_sanitize_empty_content_empty_string_becomes_placeholder() -> None:
    msgs = [{"role": "user", "content": ""}]
    result = sanitize_empty_content(msgs)
    assert result[0]["content"] == "(empty)"


def test_sanitize_empty_content_empty_string_assistant_with_tool_calls_becomes_none() -> None:
    """Un assistant con tool_calls e content vuoto diventa None, non '(empty)'."""
    msgs = [
        {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
    ]
    result = sanitize_empty_content(msgs)
    assert result[0]["content"] is None
    assert result[0]["tool_calls"] == [{"id": "1"}]


def test_sanitize_empty_content_strips_empty_text_blocks_from_list() -> None:
    msgs = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "ciao"},
                {"type": "text", "text": ""},
                {"type": "input_text", "text": ""},
                {"type": "output_text", "text": ""},
            ],
        }
    ]
    result = sanitize_empty_content(msgs)
    assert result[0]["content"] == [{"type": "text", "text": "ciao"}]


def test_sanitize_empty_content_list_becomes_placeholder_when_all_blocks_removed() -> None:
    msgs = [{"role": "user", "content": [{"type": "text", "text": ""}]}]
    result = sanitize_empty_content(msgs)
    assert result[0]["content"] == "(empty)"


def test_sanitize_empty_content_list_becomes_none_when_assistant_tool_calls() -> None:
    msgs = [
        {
            "role": "assistant",
            "content": [{"type": "text", "text": ""}],
            "tool_calls": [{"id": "1"}],
        }
    ]
    result = sanitize_empty_content(msgs)
    assert result[0]["content"] is None


def test_sanitize_empty_content_strips_meta_field_from_blocks() -> None:
    msgs = [
        {
            "role": "user",
            "content": [{"type": "image_url", "image_url": {"url": "x"}, "_meta": {"path": "a"}}],
        }
    ]
    result = sanitize_empty_content(msgs)
    block = result[0]["content"][0]
    assert "_meta" not in block
    assert block["type"] == "image_url"


def test_sanitize_empty_content_wraps_dict_content_in_list() -> None:
    msgs = [{"role": "user", "content": {"type": "text", "text": "ciao"}}]
    result = sanitize_empty_content(msgs)
    assert result[0]["content"] == [{"type": "text", "text": "ciao"}]


def test_sanitize_empty_content_leaves_non_string_non_list_non_dict_content_untouched() -> None:
    """Content None (es. assistant con soli tool_calls) passa senza modifiche."""
    msgs = [{"role": "assistant", "content": None, "tool_calls": [{"id": "1"}]}]
    result = sanitize_empty_content(msgs)
    assert result[0] is msgs[0]


def test_sanitize_empty_content_does_not_mutate_original() -> None:
    msgs = [{"role": "user", "content": ""}]
    sanitize_empty_content(msgs)
    assert msgs[0]["content"] == ""


# ---------------------------------------------------------------------------
# enforce_role_alternation: solo edge case non coperti dalla suite esistente
# (accesso diretto al modulo, non tramite il wrapper di LLMProvider).
# ---------------------------------------------------------------------------


def test_enforce_role_alternation_empty_list_is_noop() -> None:
    assert enforce_role_alternation([]) == []


def test_enforce_role_alternation_importable_directly_from_module() -> None:
    """La funzione è chiamabile direttamente dal modulo: è l'unico ingresso,
    il wrapper ``LLMProvider._enforce_role_alternation`` non c'è più."""
    msgs = [{"role": "user", "content": "ciao"}]
    result = enforce_role_alternation(msgs)
    assert result == msgs


# ---------------------------------------------------------------------------
# strip_image_content (variante pura, ritorna None se non ci sono immagini)
# ---------------------------------------------------------------------------


def test_strip_image_content_returns_none_when_no_images() -> None:
    msgs = [{"role": "user", "content": "solo testo"}]
    assert strip_image_content(msgs) is None


def test_strip_image_content_empty_list_returns_none() -> None:
    assert strip_image_content([]) is None


def test_strip_image_content_replaces_image_block_with_placeholder() -> None:
    msgs = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "guarda"},
                {"type": "image_url", "image_url": {"url": "data:..."}, "_meta": {"path": "a.png"}},
            ],
        }
    ]
    result = strip_image_content(msgs)
    assert result is not None
    blocks = result[0]["content"]
    assert blocks[0] == {"type": "text", "text": "guarda"}
    assert blocks[1]["type"] == "text"
    assert "a.png" in blocks[1]["text"]


def test_strip_image_content_placeholder_empty_path() -> None:
    """Senza _meta.path, il placeholder spiega che il modello non vede immagini."""
    msgs = [{"role": "user", "content": [{"type": "image_url", "image_url": {}}]}]
    result = strip_image_content(msgs)
    assert result is not None
    text = result[0]["content"][0]["text"]
    assert "does not support image input" in text
    # Nessun path noto: il placeholder non include il percorso.
    assert text.startswith("[The user attached an image,")


def test_strip_image_content_does_not_mutate_original() -> None:
    original_block = {"type": "image_url", "image_url": {}, "_meta": {"path": "x"}}
    msgs = [{"role": "user", "content": [original_block]}]
    strip_image_content(msgs)
    assert msgs[0]["content"][0] is original_block
    assert msgs[0]["content"][0]["type"] == "image_url"


def test_strip_image_content_leaves_non_list_content_untouched() -> None:
    """Messaggi con content non-lista (es. stringa) sono ricopiati identici."""
    msgs = [{"role": "user", "content": "testo"}, {"role": "assistant", "content": None}]
    result = strip_image_content(msgs)
    # Nessuna immagine da nessuna parte -> None, anche con messaggi misti.
    assert result is None


def test_strip_image_content_mixed_messages_only_replaces_image_blocks() -> None:
    msgs = [
        {"role": "user", "content": "solo testo, invariato"},
        {"role": "user", "content": [{"type": "image_url", "image_url": {}, "_meta": {}}]},
    ]
    result = strip_image_content(msgs)
    assert result is not None
    assert result[0]["content"] == "solo testo, invariato"
    assert result[1]["content"][0]["type"] == "text"


# ---------------------------------------------------------------------------
# strip_image_content_inplace (variante mutante)
# ---------------------------------------------------------------------------


def test_strip_image_content_inplace_returns_false_when_no_images() -> None:
    msgs = [{"role": "user", "content": "solo testo"}]
    assert strip_image_content_inplace(msgs) is False
    assert msgs[0]["content"] == "solo testo"


def test_strip_image_content_inplace_empty_list_returns_false() -> None:
    assert strip_image_content_inplace([]) is False


def test_strip_image_content_inplace_mutates_content_list_in_place() -> None:
    content = [
        {"type": "text", "text": "guarda"},
        {"type": "image_url", "image_url": {}, "_meta": {"path": "b.jpg"}},
    ]
    msgs = [{"role": "user", "content": content}]
    found = strip_image_content_inplace(msgs)
    assert found is True
    # Lo stesso oggetto content è stato mutato: chi lo referenzia vede il cambio.
    assert content[1]["type"] == "text"
    assert "b.jpg" in content[1]["text"]
    assert msgs[0]["content"] is content


def test_strip_image_content_inplace_no_meta_path_uses_empty_fallback() -> None:
    content = [{"type": "image_url", "image_url": {}}]
    msgs = [{"role": "user", "content": content}]
    strip_image_content_inplace(msgs)
    assert "does not support image input" in content[0]["text"]


def test_strip_image_content_inplace_non_list_content_ignored() -> None:
    msgs = [{"role": "assistant", "content": None, "tool_calls": [{"id": "1"}]}]
    assert strip_image_content_inplace(msgs) is False
