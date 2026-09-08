"""Il sidecar dell'umore della mascotte.

Dopo ogni turno WebUI si chiede al modello, **fuori dal turno**, come si sente
Jenny per la risposta che ha appena dato: una lettera fra quattro, letta sulla
coda dell'ultimo scambio. Il risultato diventa il frame ``mascot_mood`` che il
client traduce in una faccia. Il piano e le ragioni stanno in
``.agent/mascot-mood-plan.md``, l'arte in ``.agent/mascot-faces-plan.md``; qui
si riassumono i vincoli che il codice tiene:

- **il prompt dell'agente principale non cambia**: questo modulo non legge
  SOUL.md, non tocca la cronologia, non aggiunge tool. Vede solo l'ultimo
  scambio, troncato, e produce una lettera;
- **una richiesta piccola per turno**: sistema sotto le sessanta parole, input
  a 300+600 caratteri, ``max_tokens`` a 3 — tre e non uno perché un provider
  può spendere il primo token in uno spazio o in un a-capo;
- **niente quando non serve**: risposte sotto ``MOOD_MIN_ASSISTANT_CHARS``,
  turni finiti in errore (l'ultima riga è dell'utente), turni-comando (il
  chiamante filtra ``runtime is None``) non costano nessuna richiesta.

Tutto ciò che qui non parla col provider è puro e si testa senza rete: la
selezione dell'input, la costruzione della richiesta, la lettura della lettera,
la scelta del modello.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from loguru import logger

from jenny.session.history_meta import is_synthetic_history_row
from jenny.utils.helpers import strip_think

if TYPE_CHECKING:
    from jenny.config.schema import Config
    from jenny.providers.base import LLMProvider, LLMResponse
    from jenny.session.manager import Session

# Le etichette che il client conosce (``MOOD_FACES`` in mobile-jenny.js); un
# contratto le tiene allineate. ``neutral`` non produce frame.
#
# Sono le espressioni che **esistono disegnate**: l'arte comanda, non il
# vocabolario. Fino all'08/09/2026 le lettere erano cinque e comprendevano
# ``worried`` e ``surprised``, che non hanno una faccia e se la prendevano in
# prestito da una posa; ``angry`` invece ce l'ha. V. mascot-faces-plan.md, F4.
MOODS: tuple[str, ...] = ("happy", "sad", "angry", "neutral")
NEUTRAL_MOOD = "neutral"

_LETTER_TO_MOOD: Mapping[str, str] = {
    "A": "happy",
    "B": "sad",
    "C": "angry",
    "D": NEUTRAL_MOOD,
}

MOOD_USER_MAX_CHARS = 300
MOOD_ASSISTANT_MAX_CHARS = 600
MOOD_MIN_ASSISTANT_CHARS = 40
MOOD_MAX_TOKENS = 3
MOOD_REASONING_EFFORT = "none"
MOOD_TEMPERATURE = 0.0
# Bucket di contabilita' in ``jenny.agent.token_usage``: il titolo che questo
# sidecar sostituisce non veniva contato; l'umore si'.
MOOD_TOKEN_USAGE_SOURCE = "mascot"


@dataclass(frozen=True)
class MoodInputs:
    """L'ultimo scambio, gia' troncato: la domanda dalla testa, la risposta dalla coda."""

    user: str
    assistant: str


def _text_of(message: Mapping[str, Any]) -> str:
    content = message.get("content")
    if not isinstance(content, str):
        return ""
    return strip_think(content).strip()


def _is_conversation_row(message: Mapping[str, Any]) -> bool:
    """Una riga scritta o letta dalla persona: niente comandi, niente sintetici.

    La regola e' quella che aveva la selezione del titolo, e va tenuta identica:
    un rientro di subagent o uno sprone a un goal non sono "l'utente ha detto".
    """
    if message.get("_command") is True:
        return False
    if is_synthetic_history_row(message):
        return False
    return message.get("role") in ("user", "assistant")


def mood_inputs(session: Session) -> MoodInputs | None:
    """L'ultimo scambio della sessione, o ``None`` se non c'e' niente da sentire.

    Si scorre dalla coda. La **prima** riga di conversazione deve essere della
    assistente: se e' dell'utente il turno e' finito in errore (o e' ancora
    aperto) e l'errore ha gia' la sua faccia, gratis, dal frame ``error``. Le
    righe strumentali (tool call, risultati) hanno contenuto non testuale e si
    saltano; una riga vuota pure.
    """
    assistant = ""
    user = ""
    for message in reversed(session.messages):
        if not isinstance(message, Mapping) or not _is_conversation_row(message):
            continue
        text = _text_of(message)
        if not text:
            continue
        role = message.get("role")
        if not assistant:
            if role != "assistant":
                return None
            assistant = text
            continue
        if role == "user":
            user = text
            break
    if not assistant or not user:
        return None
    if len(assistant) < MOOD_MIN_ASSISTANT_CHARS:
        return None
    if len(assistant) > MOOD_ASSISTANT_MAX_CHARS:
        # La coda: e' dove sta il tono ("...spero ti sia utile", "...non ci sono
        # riuscita"). L'inizio di una risposta lunga e' quasi sempre neutro.
        assistant = "…" + assistant[-(MOOD_ASSISTANT_MAX_CHARS - 1):].lstrip()
    if len(user) > MOOD_USER_MAX_CHARS:
        # La testa: la domanda si capisce dall'inizio.
        user = user[: MOOD_USER_MAX_CHARS - 1].rstrip() + "…"
    return MoodInputs(user=user, assistant=assistant)


def build_mood_request(inputs: MoodInputs, *, bot_name: str) -> list[dict[str, str]]:
    """I messaggi per il provider: la persona in due righe, poi lo scambio.

    In prima persona, perche' e' qui che vive il roleplay: non "classifica il
    sentiment di questo testo" ma "hai appena risposto cosi', come ti senti?".
    Nessun altro contesto — il tono della risposta e' gia' la persona.
    """
    name = (bot_name or "Jenny").strip() or "Jenny"
    system = (
        f"You are {name}, this person's personal assistant, and you have just sent "
        "them the reply below. Say how you feel about it with ONE letter and nothing "
        "else:\n"
        "A = happy or proud\n"
        "B = sad or sorry\n"
        "C = angry or annoyed\n"
        "D = nothing in particular"
    )
    user = f"They wrote:\n{inputs.user}\n\nYou replied:\n{inputs.assistant}\n\nLetter:"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def parse_mood(text: str | None) -> str:
    """La lettera in testa alla risposta, o ``neutral`` per tutto il resto.

    Accetta ``"B"``, ``" b"``, ``"B."``, ``"B)"``; rifiuta una parola che
    comincia con quella lettera (``"Bene"``): con ``max_tokens=3`` il modello
    puo' iniziare a divagare, e una divagazione non e' un umore. Una lettera
    fuori dall'alfabeto — la vecchia ``E``, o una inventata — vale ``neutral``.
    """
    if not isinstance(text, str):
        return NEUTRAL_MOOD
    cleaned = text.strip().lstrip("(*[\"'`")
    if not cleaned:
        return NEUTRAL_MOOD
    letter = cleaned[0].upper()
    if letter not in _LETTER_TO_MOOD:
        return NEUTRAL_MOOD
    if len(cleaned) > 1 and cleaned[1].isalpha():
        return NEUTRAL_MOOD
    return _LETTER_TO_MOOD[letter]


_WARNED_PRESETS: set[str] = set()


def resolve_mood_model(config: Config, runtime_model: str) -> str:
    """Il modello del sidecar: il preset configurato, altrimenti quello del turno.

    Di un preset (``config.model_presets``) si prende **solo** ``model``: i preset
    non cambiano provider a runtime (``loop_provider._apply_model_preset``) e
    l'umore non deve essere il primo a provarci. Un preset inesistente o senza
    modello ricade sul runtime, con un avviso nel log — una volta per processo.
    """
    name = getattr(config.agents.defaults, "mascot_mood_model_preset", None)
    if not name:
        return runtime_model
    presets = getattr(config, "model_presets", None) or {}
    preset = presets.get(name)
    model = preset.get("model") if isinstance(preset, Mapping) else getattr(preset, "model", None)
    if isinstance(model, str) and model.strip():
        return model.strip()
    if name not in _WARNED_PRESETS:
        _WARNED_PRESETS.add(name)
        logger.warning(
            "mascot mood: model preset {!r} is unknown or has no model; using the turn's {!r}",
            name,
            runtime_model,
        )
    return runtime_model


async def classify_mood(
    provider: LLMProvider,
    model: str,
    inputs: MoodInputs,
    *,
    bot_name: str,
) -> tuple[str, LLMResponse | None]:
    """Una richiesta al provider; ``(umore, risposta)``.

    La risposta torna al chiamante per la contabilita' dei token. Un errore del
    provider vale ``neutral`` e nessuna risposta: la mascotte resta ``idle``, che
    e' il comportamento di prima di questo modulo.
    """
    try:
        response = await provider.chat_with_retry(
            build_mood_request(inputs, bot_name=bot_name),
            tools=None,
            model=model,
            max_tokens=MOOD_MAX_TOKENS,
            temperature=MOOD_TEMPERATURE,
            reasoning_effort=MOOD_REASONING_EFFORT,
            retry_mode="standard",
        )
    except Exception:
        logger.debug("mascot mood: provider call failed", exc_info=True)
        return NEUTRAL_MOOD, None
    if response.finish_reason == "error":
        logger.debug("mascot mood: provider returned an error response")
        return NEUTRAL_MOOD, response
    content = (response.content or "").strip()
    if not content and response.finish_reason == "length":
        # Il budget e' finito prima della lettera: un modello che ragiona di
        # default ha speso i tre token a pensare. Sul telefono il DEBUG non si
        # vede, e questo e' l'unico sintomo di un sidecar che gira a vuoto.
        if model not in _WARNED_EMPTY_MODELS:
            _WARNED_EMPTY_MODELS.add(model)
            logger.warning(
                "mascot mood: {!r} spent the whole budget without answering "
                "(thinking on?); every verdict is neutral until a non-thinking "
                "model is set via mascotMoodModelPreset",
                model,
            )
        return NEUTRAL_MOOD, response
    mood = parse_mood(content)
    logger.info(
        "mascot mood: {} (model={}, raw={!r}, tokens={})",
        mood,
        model,
        content[:8],
        (response.usage or {}).get("total_tokens"),
    )
    return mood, response


_WARNED_EMPTY_MODELS: set[str] = set()
