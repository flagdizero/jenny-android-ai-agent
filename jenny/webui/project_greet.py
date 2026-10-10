"""Il saluto di un quaderno appena nato: Jenny parla per prima.

Creato un quaderno, la casa e l'officina portano dritti nella sua chat, e la
chat vuota non dice niente. Qui Jenny scrive il primo messaggio — chi sei, se lo
sa, di cosa parlera' questo quaderno, e una domanda per cominciare — con una
chiamata breve al modello, **non** un turno dell'agente: niente strumenti,
niente memoria da consolidare, niente da promettere.

Le regole, nell'ordine in cui mordono:

1. **Solo su una conversazione vuota.** «Riprendila» alla creazione adotta una
   chat che ha gia' dei messaggi, e un saluto in fondo a quella sarebbe un
   «piacere di conoscerti» a meta' discorso. Vuota vuol dire sessione *e*
   trascrizione: la seconda resta anche quando la prima e' stata compattata.
2. **Mai sotto un turno.** Se l'utente ha gia' scritto mentre il modello
   pensava, il saluto arriverebbe dopo la sua domanda: si butta.
3. **Mai una chat vuota per colpa della rete.** Provider assente, errore o
   troppo tempo: il saluto diventa una frase fissa e tradotta. Chi ha appena
   creato un quaderno vede sempre qualcuno che gli parla.
4. **Una strada sola per il messaggio**, quella degli avvisi di Jenny: riga
   ``assistant`` nella sessione, frame sul bus — che il canale websocket
   registra anche nella trascrizione — e il ``turn_end`` che chiude il turno,
   senza il quale la mascotte resterebbe a pensare (v.
   ``ChannelDeliverer._close_webui_turn``).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.bus.events import OutboundMessage
from jenny.providers.opencode import conversation_scope
from jenny.session.keys import (
    WEBUI_CHANNEL,
    project_session_key,
    webui_chat_id,
    webui_transcript_key,
)
from jenny.utils.prompt_templates import render_template
from jenny.webui.metadata import WEBUI_TURN_METADATA_KEY
from jenny.webui.transcript_store import read_transcript_lines

# Quanto aspettare il modello prima della frase fissa. Il saluto e' la prima
# cosa che si legge nel quaderno: oltre questo tempo una frase fissa subito
# vale piu' di una frase bella dopo.
GREETING_TIMEOUT_S = 15.0

# Il saluto e' una o due frasi: piu' di cosi' il modello non ha seguito le
# istruzioni, e un muro di testo come primo messaggio e' peggio della riserva.
MAX_GREETING_CHARS = 600

# Quanto di ``USER.md`` arriva al modello: gli serve il nome, non la biografia.
MAX_USER_NOTES_CHARS = 1500

FALLBACK_GREETINGS: dict[str, str] = {
    "it": "Ciao! Questo è il nostro spazio per parlare di: {scope}. Cominciamo?",
    "en": "Hi! This is our space to talk about: {scope}. Shall we start?",
}

_LANGUAGE_NAMES = {"it": "Italian", "en": "English"}


def fallback_greeting(language: str, scope: str) -> str:
    """La frase fissa, nella lingua dell'interfaccia."""
    template = FALLBACK_GREETINGS.get(language, FALLBACK_GREETINGS["en"])
    return template.format(scope=scope.rstrip(" ."))


def _usable_scope(raw: str, name: str) -> str:
    """La riga di scope, o il nome se la riga non c'e'.

    ``read_wiki_scope`` torna un testo fra parentesi quando non trova niente
    (``(no scope set)``, ``(no AGENTS.md)``): e' un segnale per chi legge un
    registro, non qualcosa da far dire a Jenny.
    """
    text = " ".join((raw or "").split())
    if not text or text.startswith("("):
        return name
    return text


def _user_notes(workspace: Path) -> str:
    try:
        text = (workspace / "USER.md").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text.strip()[:MAX_USER_NOTES_CHARS]


def _clean(text: str | None) -> str:
    out = (text or "").strip().strip('"«»“”').strip()
    return out if len(out) <= MAX_GREETING_CHARS else ""


async def _generate(
    llm: tuple[Any, str] | None,
    *,
    session_key: str,
    prompt: str,
) -> str:
    """Il saluto del modello, o ``""`` se non e' arrivato."""
    if llm is None:
        return ""
    provider, model = llm
    try:
        with conversation_scope(session_key):
            response = await asyncio.wait_for(
                provider.chat_with_retry(
                    model=model,
                    messages=[
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": "Write the greeting now."},
                    ],
                    tools=None,
                    tool_choice=None,
                ),
                timeout=GREETING_TIMEOUT_S,
            )
    except Exception as exc:  # noqa: BLE001 - qualunque guasto vale la frase fissa
        logger.warning("notebook greeting: model call failed for {}: {}", session_key, exc)
        return ""
    if getattr(response, "finish_reason", None) == "error":
        logger.warning("notebook greeting: provider error for {}", session_key)
        return ""
    return _clean(getattr(response, "content", None))


def _is_empty(session_manager: Any, session_key: str) -> bool:
    session = session_manager.get_or_create(session_key)
    if session.messages:
        return False
    return not read_transcript_lines(webui_transcript_key(session_key))


async def greet_project(
    *,
    name: str,
    wiki_root: Path,
    workspace: Path,
    language: str,
    bot_name: str,
    session_manager: Any,
    bus: Any,
    llm: tuple[Any, str] | None,
    busy_session_keys: Callable[[], Any],
    read_scope: Callable[[Path], str],
) -> dict[str, Any]:
    """Scrive il saluto nella chat del quaderno *name*. Vedi la docstring del modulo.

    Ritorna ``greeted: False`` con il motivo quando non saluta: non e' un
    errore, e il client non deve mostrare niente.
    """
    session_key = project_session_key(name)
    if not _is_empty(session_manager, session_key):
        return {"greeted": False, "reason": "not_empty"}

    scope = _usable_scope(await asyncio.to_thread(read_scope, wiki_root), name)
    user_notes = await asyncio.to_thread(_user_notes, workspace)
    prompt = render_template(
        "agent/notebook_greeting.md",
        bot_name=bot_name,
        language=_LANGUAGE_NAMES.get(language, "English"),
        notebook=name,
        scope=scope,
        user_notes=user_notes,
    )
    text = await _generate(llm, session_key=session_key, prompt=prompt)
    fallback = not text
    if fallback:
        text = fallback_greeting(language, scope)

    # Il modello ha pensato per secondi: nel frattempo l'utente puo' aver scritto.
    if session_key in set(busy_session_keys()) or not _is_empty(session_manager, session_key):
        return {"greeted": False, "reason": "not_empty"}

    session = session_manager.get_or_create(session_key)
    session.add_message("assistant", text, _channel_delivery=True)
    session_manager.save(session)

    chat_id = webui_chat_id(session_key)
    turn_id = f"greeting:{uuid.uuid4().hex}"
    await bus.publish_outbound(OutboundMessage(
        channel=WEBUI_CHANNEL,
        chat_id=chat_id,
        content=text,
        metadata={WEBUI_TURN_METADATA_KEY: turn_id},
    ))
    await bus.publish_outbound(OutboundMessage(
        channel=WEBUI_CHANNEL,
        chat_id=chat_id,
        content="",
        metadata={WEBUI_TURN_METADATA_KEY: turn_id, "_turn_end": True},
    ))
    logger.info("notebook greeting written for {} (fallback={})", name, fallback)
    return {"greeted": True, "fallback": fallback, "text": text}
