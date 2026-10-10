"""Un surrogato UTF-16 isolato non avvelena la sessione.

``json.loads`` accetta ``"\\ud83d"`` — metà di un'emoji, quel che resta di un frame
tagliato nel mezzo — e ne fa una stringa Python con un code point che in UTF-8 non
esiste. Finita in un messaggio, faceva fallire **ogni** ``SessionManager.save``
da lì in poi: anche i turni puliti rispondevano con l'errore generico, fino al
riavvio. Ora il testo si ripulisce all'ingresso (U+FFFD al posto del surrogato), e
``save`` regge comunque se un surrogato arriva da un'altra porta.
"""

from __future__ import annotations

import json

from jenny.bus.events import InboundMessage
from jenny.providers.base import LLMResponse
from jenny.runtime.user_text import say
from jenny.session.manager import SessionManager, scrub_lone_surrogates
from tests.support.agent import make_loop, make_provider

KEY = "unified:default"
GENERIC_ERROR = say("error.internal", "en")


async def test_the_turns_after_a_lone_surrogate_still_work(tmp_path):
    provider = make_provider()

    async def chat(**_kw):
        return LLMResponse(content="ok")

    provider.chat_with_retry = chat
    provider.chat_stream_with_retry = chat
    loop = make_loop(tmp_path, provider=provider)
    published: list[str] = []
    original = loop.bus.publish_outbound

    async def capture(message):
        published.append(message.content)
        await original(message)

    loop.bus.publish_outbound = capture
    cut = json.loads('"guarda \\ud83d"')  # un frame tagliato dentro un'emoji
    for content in (cut, "messaggio normale", "un altro"):
        await loop._dispatch(InboundMessage(channel="websocket", sender_id="u",
                                            chat_id="default", content=content))

    assert GENERIC_ERROR not in published
    reloaded = SessionManager(tmp_path).get_or_create(KEY)
    users = [m["content"] for m in reloaded.messages if m.get("role") == "user"]
    assert any("guarda �" in str(c) for c in users)
    assert any("un altro" in str(c) for c in users)


def _encoding_provider(seen: list[str], steps: list):
    """Un provider che codifica la richiesta come la codifica httpx.

    ``encode_json`` di httpx usa ``ensure_ascii=False`` e poi UTF-8: un
    surrogato isolato arrivato fin lì solleva ``UnicodeEncodeError``, cioè la
    chiamata al modello fallisce. Il salvataggio della sessione ha la sua rete,
    la richiesta al provider no: se il turno risponde, la pulizia all'ingresso
    ha fatto il suo lavoro.
    """
    from httpx._content import encode_json

    provider = make_provider()

    async def chat(**kwargs):
        encode_json({"messages": kwargs.get("messages")})
        for m in kwargs.get("messages") or []:
            if m.get("role") == "user":
                seen.append(json.dumps(m.get("content"), ensure_ascii=False))
        return steps.pop(0) if steps else LLMResponse(content="ok")

    provider.chat_with_retry = chat
    provider.chat_stream_with_retry = chat
    return provider


async def test_a_lone_surrogate_never_reaches_the_provider(tmp_path):
    seen: list[str] = []
    loop = make_loop(tmp_path, provider=_encoding_provider(seen, []))
    cut = json.loads('"guarda \\ud83d"')

    outcome = await loop._process_message(
        InboundMessage(channel="websocket", sender_id="u", chat_id="default", content=cut),
    )

    assert outcome.final_text == "ok"
    assert any("guarda �" in text for text in seen)


async def test_a_lone_surrogate_injected_mid_turn_never_reaches_the_provider(tmp_path):
    import asyncio

    from jenny.providers.base import ToolCallRequest

    (tmp_path / "a.txt").write_text("A", encoding="utf-8")
    seen: list[str] = []
    queue: asyncio.Queue = asyncio.Queue(maxsize=20)
    first = LLMResponse(content="passo", finish_reason="tool_calls",
                        tool_calls=[ToolCallRequest(id="c1", name="list_dir",
                                                    arguments={"path": "."})])
    provider = _encoding_provider(seen, [first])
    loop = make_loop(tmp_path, provider=provider)
    queue.put_nowait(InboundMessage(channel="websocket", sender_id="u", chat_id="default",
                                    content=json.loads('"anche \\udc00 questo"')))

    outcome = await loop._process_message(
        InboundMessage(channel="websocket", sender_id="u", chat_id="default", content="vai"),
        pending_queue=queue,
    )

    assert outcome.final_text == "ok"
    assert any("anche � questo" in text for text in seen)


def test_save_survives_a_surrogate_that_came_from_elsewhere(tmp_path):
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create(KEY)
    session.add_message("assistant", "mezza emoji \ud83d dal modello")
    session.metadata["nota"] = {"testo": "anche qui \udc00"}

    sessions.save(session)

    reloaded = SessionManager(tmp_path).get_or_create(KEY)
    assert reloaded.messages[0]["content"] == "mezza emoji � dal modello"
    assert reloaded.metadata["nota"] == {"testo": "anche qui �"}


def test_scrub_leaves_real_emoji_alone():
    assert scrub_lone_surrogates("ciao 😀 \ud83d") == "ciao 😀 �"
