import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from jenny.agent.loop import AgentLoop, TurnContext, TurnState
from jenny.bus.events import InboundMessage
from jenny.bus.queue import MessageBus
from jenny.providers.base import LLMResponse


def _make_loop(tmp_path: Path, extract_document_text: bool = True) -> AgentLoop:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="ok"))
    return AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        extract_document_text=extract_document_text,
    )


def test_should_extract_document_text_defaults_true(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    assert loop._should_extract_document_text() is True


def test_should_extract_document_text_respects_config_false(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path, extract_document_text=False)
    assert loop._should_extract_document_text() is False


def test_prepare_message_media_hybrid_inlines_small_docs(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path, extract_document_text=False)
    doc_path = tmp_path / "report.txt"
    doc_path.write_text("Quarterly revenue is $5M", encoding="utf-8")

    content, media = loop._prepare_message_media("summarize", [str(doc_path)])

    # Hybrid default: a small text/PDF document is inlined so the agent
    # considers it in the turn instead of ignoring a bare reference.
    assert "Quarterly revenue is $5M" in content
    assert f"[File: {doc_path.name}]" in content
    assert media == []


def test_prepare_message_media_hybrid_references_binary(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path, extract_document_text=False)
    blob_path = tmp_path / "archive.jbk"
    blob_path.write_bytes(b"\x00\x01\x02\x03binary-not-extractable")

    content, media = loop._prepare_message_media("what is this", [str(blob_path)])

    # Non-extractable (binary) files stay lightweight path references.
    assert f"[Attachment: {blob_path}]" in content
    assert "[File:" not in content
    assert media == []


def test_prepare_message_media_hybrid_references_oversized_doc(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path, extract_document_text=False)
    big_path = tmp_path / "huge.txt"
    big_path.write_text("x" * (600 * 1024), encoding="utf-8")

    content, media = loop._prepare_message_media("summarize", [str(big_path)])

    # Beyond the inline size cap: referenced, not inlined every turn.
    assert f"[Attachment: {big_path}]" in content
    assert "[File:" not in content
    assert media == []


@pytest.mark.asyncio
async def test_state_restore_extracts_documents_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = _make_loop(tmp_path)
    doc_path = tmp_path / "report.txt"
    doc_path.write_text("Quarterly revenue is $5M", encoding="utf-8")
    calls: list[tuple[str, list[str]]] = []

    def fake_extract_documents(content: str, media: list[str]) -> tuple[str, list[str]]:
        calls.append((content, media))
        return f"{content}\n\n[File: report.txt]\nQuarterly revenue is $5M", []

    monkeypatch.setattr("jenny.agent.turn_states.extract_documents", fake_extract_documents)

    ctx = TurnContext(
        msg=InboundMessage(
            channel="internal",
            sender_id="u",
            chat_id="c",
            content="summarize",
            media=[str(doc_path)],
        ),
        session_key="internal:c",
        state=TurnState.RESTORE,
        turn_id="turn-1",
    )

    assert await loop._state_restore(ctx) == "ok"

    assert calls == [("summarize", [str(doc_path)])]
    assert "Quarterly revenue" in ctx.msg.content
    assert ctx.msg.media == []


@pytest.mark.asyncio
async def test_pending_followup_extracts_documents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doc_path = tmp_path / "followup.txt"
    doc_path.write_text("Do not inject this file body", encoding="utf-8")
    captured_messages: list[list[dict]] = []
    call_count = {"n": 0}

    async def chat_with_retry(*, messages: list[dict], **kwargs: object) -> LLMResponse:
        call_count["n"] += 1
        captured_messages.append([dict(message) for message in messages])
        return LLMResponse(content=f"answer-{call_count['n']}", tool_calls=[], usage={})

    loop = _make_loop(tmp_path)
    loop.provider.chat_with_retry = chat_with_retry
    loop.tools.get_definitions = MagicMock(return_value=[])

    def tracking_extract_documents(content: str, media: list[str]) -> tuple[str, list[str]]:
        return f"{content}\n\n[File: followup.txt]\nDo not inject this file body", []

    monkeypatch.setattr("jenny.agent.turn_states.extract_documents", tracking_extract_documents)

    pending_queue: asyncio.Queue[InboundMessage] = asyncio.Queue()
    await pending_queue.put(
        InboundMessage(
            channel="internal",
            sender_id="u",
            chat_id="c",
            content="check this",
            media=[str(doc_path)],
        )
    )

    final_content, _, _, _, had_injections = await loop._run_agent_loop(
        [{"role": "user", "content": "hello"}],
        channel="internal",
        chat_id="c",
        pending_queue=pending_queue,
    )

    assert final_content == "answer-2"
    assert had_injections is True
    injected_user_content = [
        message["content"]
        for message in captured_messages[-1]
        if message.get("role") == "user" and isinstance(message.get("content"), str)
    ][-1]
    assert "check this" in injected_user_content
    assert "Do not inject this file body" in injected_user_content


def test_extract_documents_references_non_extractable_binary(tmp_path: Path) -> None:
    """I binari non estraibili (archivi, backup, …) non vanno scartati in
    silenzio: l'agente deve ricevere un riferimento [Attachment:] per leggerli
    on-demand (regressione: .jbk allegato risultava invisibile)."""
    from jenny.utils.document import extract_documents

    blob = tmp_path / "backup.jbk"
    blob.write_bytes(b"\x00\x01\x02binary-blob")
    doc = tmp_path / "notes.txt"
    doc.write_text("extract me", encoding="utf-8")

    content, media = extract_documents("cos'è?", [str(blob), str(doc)])

    assert media == []
    # Il testo estraibile è inlineato, il binario è referenziato.
    assert "extract me" in content
    assert f"[Attachment: {blob}]" in content


def test_extract_documents_references_oversized_file(tmp_path: Path) -> None:
    """File oltre soglia: riferimento invece di scarto silenzioso."""
    from jenny.utils.document import extract_documents

    big = tmp_path / "big.txt"
    big.write_text("x" * 2048, encoding="utf-8")

    content, media = extract_documents("look", [str(big)], max_file_size=1024)

    assert media == []
    assert f"[Attachment: {big}]" in content
