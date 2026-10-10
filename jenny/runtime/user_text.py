"""Le frasi che il server dice all'utente, in italiano e in inglese, in un posto solo.

Prima erano stringhe inglesi sparse fra runner, loop, comandi e notifiche, e in
un'interfaccia italiana arrivavano come corpi estranei («Stopped 1 task(s).»,
«Error calling LLM: ...»). Qui ogni frase ha una chiave e due stesure; chi la
dice chiama :func:`say` con la chiave e i campi.

Solo testo **per l'utente**: quel che legge il modello (prompt, segnaposto nella
cronologia, risultati dei tool) resta in inglese dove sta, perché il modello
imita la lingua di quel che legge.

La lingua è ``agents.defaults.language``, letta da ``config.json`` al momento
della frase (come fa ``telegram_api``): un cambio vale subito, senza riavvio.
Una lingua che la tabella non conosce ripiega sull'inglese; una chiave che non
c'è solleva ``KeyError`` — è un errore di programmazione, e un test lo trova.
"""

from __future__ import annotations

from loguru import logger

_FALLBACK_LANGUAGE = "en"

_TEXT: dict[str, dict[str, str]] = {
    "en": {
        # /stop
        "stop.done": "I stopped.",
        "stop.none": "There was nothing to stop.",
        "stop.helper_running": (
            "One helper keeps working in the background: stop it from its own bubble, "
            "or with /stop all."
        ),
        "stop.helpers_running": (
            "{count} helpers keep working in the background: stop them from their own "
            "bubbles, or with /stop all."
        ),
        # Errori del provider, uno per tipo: ognuno dice cosa fare.
        "error.connection": (
            "I can't reach the model. Check your connection, or the provider in "
            "Settings → Who answers."
        ),
        "error.timeout": (
            "The model didn't answer in time. Try again in a moment; if it keeps "
            "happening, try another model in Settings → Who answers."
        ),
        "error.auth": (
            "The provider rejected the key. Check it in Settings → Who answers."
        ),
        "error.rate_limit": (
            "The provider says too many requests were sent. Wait a minute and try again."
        ),
        "error.server": (
            "The provider is having trouble on its side. Try again in a few minutes."
        ),
        "error.credit": (
            "The provider refused the request: the key is out of credit or the account "
            "needs paying. Top it up on the provider's website and try again."
        ),
        "error.generic": (
            "The model answered with an error. Try again; if it keeps happening, check "
            "the provider in Settings → Who answers."
        ),
        "error.internal": "Something went wrong on my side. Try again.",
        # Fine turno senza una risposta vera.
        "turn.empty": (
            "I finished the steps but couldn't write an answer. Try again, or ask me "
            "for something smaller."
        ),
        "turn.truncated": (
            "I ran out of room for the answer before getting to the point. Ask me for "
            "a smaller piece at a time."
        ),
        "turn.max_iterations": (
            "I took too many steps ({max_iterations}) without finishing. Try splitting "
            "the request into smaller pieces."
        ),
        "turn.images_dropped": (
            "⚠️ I couldn't look at the attached images: the active model can't read images."
        ),
        # Notifiche di sistema: il tipo nel titolo, e il corpo di ripiego.
        "notify.reminder": "⏰ Reminder",
        "notify.monitor": "Monitoring",
        "notify.update": "Update",
        "notify.new_message": "New message",
        # Allarmi di sistema: dicono dove guardare nell'interfaccia.
        "alarm.gardener_label": "Gardener · {name}",
        "alarm.gardener_stuck": (
            "The gardener has failed {failures} passes in a row on the notebook "
            "'{name}' ({status}): its journal is not becoming pages. Open the notebook "
            "and ask me what's wrong."
        ),
        "alarm.check_label": "{name} · check",
        "alarm.check_silent": (
            "'{name}' has not completed its check for {runs} runs in a row. Ask me in "
            "chat what's wrong."
        ),
        "alarm.checks_silent": (
            "{count} checks in '{name}' have not completed for up to {runs} runs in a "
            "row. Ask me in chat what's wrong."
        ),
        "alarm.dream_label": "Memory",
        "alarm.dream_stuck": (
            "My memory is stuck: for {stuck} runs in a row nothing could be saved, "
            "because the memory files are full. Workshop → Memory shows the sizes."
        ),
        "alarm.update_critical": (
            "Security update {version} is available. Find it in Settings → Updates."
        ),
    },
    "it": {
        "stop.done": "Mi sono fermata.",
        "stop.none": "Non stavo facendo niente da fermare.",
        "stop.helper_running": (
            "Un aiutante continua a lavorare in background: lo fermi dalla sua bolla, "
            "o con /stop all."
        ),
        "stop.helpers_running": (
            "{count} aiutanti continuano a lavorare in background: li fermi dalla loro "
            "bolla, o con /stop all."
        ),
        "error.connection": (
            "Non riesco a raggiungere il modello. Controlla la connessione, o il "
            "provider in Impostazioni → Chi risponde."
        ),
        "error.timeout": (
            "Il modello non ha risposto in tempo. Riprova fra poco; se succede spesso, "
            "prova un altro modello in Impostazioni → Chi risponde."
        ),
        "error.auth": (
            "Il provider ha rifiutato la chiave. Controllala in Impostazioni → Chi risponde."
        ),
        "error.rate_limit": (
            "Il provider dice che sono arrivate troppe richieste. Aspetta un minuto e riprova."
        ),
        "error.server": (
            "Il provider ha un problema dalla sua parte. Riprova fra qualche minuto."
        ),
        "error.credit": (
            "Il provider ha rifiutato la richiesta: la chiave ha finito il credito o il "
            "conto è da saldare. Ricaricala dal sito del provider e riprova."
        ),
        "error.generic": (
            "Il modello ha risposto con un errore. Riprova; se continua, controlla il "
            "provider in Impostazioni → Chi risponde."
        ),
        "error.internal": "Qualcosa è andato storto da parte mia. Riprova.",
        "turn.empty": (
            "Ho finito i passaggi ma non sono riuscita a scrivere una risposta. Riprova, "
            "o chiedimi una cosa più piccola."
        ),
        "turn.truncated": (
            "Ho finito lo spazio per la risposta prima di arrivare al punto. Chiedimi un "
            "pezzo più piccolo alla volta."
        ),
        "turn.max_iterations": (
            "Ho fatto troppi passaggi ({max_iterations}) senza finire. Prova a dividere "
            "la richiesta in pezzi più piccoli."
        ),
        "turn.images_dropped": (
            "⚠️ Non ho potuto guardare le immagini allegate: il modello attivo non legge "
            "immagini."
        ),
        "notify.reminder": "⏰ Promemoria",
        "notify.monitor": "Monitoraggio",
        "notify.update": "Aggiornamento",
        "notify.new_message": "Nuovo messaggio",
        "alarm.gardener_label": "Giardiniere · {name}",
        "alarm.gardener_stuck": (
            "Il giardiniere non riesce a sistemare il quaderno «{name}» da {failures} "
            "passate di fila ({status}): il suo diario non diventa pagine. Apri il "
            "quaderno e chiedimi cosa non va."
        ),
        "alarm.check_label": "{name} · controllo",
        "alarm.check_silent": (
            "«{name}» non riesce a fare il suo controllo da {runs} volte di fila. "
            "Chiedimi in chat cosa non va."
        ),
        "alarm.checks_silent": (
            "{count} controlli di «{name}» non riescono da fino a {runs} volte di fila. "
            "Chiedimi in chat cosa non va."
        ),
        "alarm.dream_label": "Memoria",
        "alarm.dream_stuck": (
            "La mia memoria è ferma: da {stuck} passate di fila non riesco a salvare "
            "niente, perché i file di memoria sono pieni. Officina → Memoria mostra le "
            "dimensioni."
        ),
        "alarm.update_critical": (
            "È disponibile l'aggiornamento di sicurezza {version}. Lo trovi in "
            "Impostazioni → Aggiornamenti."
        ),
    },
}

# I tipi d'errore del provider che hanno una frase propria (``error.<tipo>``).
ERROR_KINDS: frozenset[str] = frozenset(
    key.split(".", 1)[1]
    for key in _TEXT[_FALLBACK_LANGUAGE]
    if key.startswith("error.") and key != "error.internal"
)


def current_language() -> str:
    """La lingua dell'agente adesso, da ``config.json``; ``en`` se non si legge."""
    return _language_from_config()


def _language_from_config() -> str:
    """La lettura vera di :func:`current_language` (i test fissano quella).

    ``load_config`` e non una copia in memoria: la lingua la scrive la WebUI
    (onboarding) passando da ``store.mutate``, e nessun oggetto vivo la segue.
    È la stessa lettura che altri moduli fanno più volte per turno; qui serve
    solo quando c'è una frase da dire.
    """
    try:
        from jenny.config.loader import load_config

        return (load_config().agents.defaults.language or "").strip().lower()
    except Exception:  # noqa: BLE001 — una frase non deve far fallire chi la dice
        logger.opt(exception=True).debug("user_text: language not readable, using English")
        return _FALLBACK_LANGUAGE


DEFAULT_BOT_NAME = "Jenny"


def current_bot_name() -> str:
    """Il nome che l'utente ha dato all'agente (``agents.defaults.bot_name``), adesso.

    Letto da ``config.json`` a ogni chiamata come :func:`current_language`: la
    WebUI lo cambia da Impostazioni e vale dal turno dopo, senza riavvio.
    ``Jenny`` se manca, è vuoto o non si legge.
    """
    try:
        from jenny.config.loader import load_config

        name = (load_config().agents.defaults.bot_name or "").strip()
    except Exception:  # noqa: BLE001 — il nome è una cortesia, non un requisito
        logger.opt(exception=True).debug("user_text: bot name not readable")
        return DEFAULT_BOT_NAME
    return name or DEFAULT_BOT_NAME


def say(key: str, language: str | None = None, **fields: object) -> str:
    """La frase *key* nella lingua *language* (``None`` = quella configurata).

    Lingua sconosciuta → inglese. Chiave sconosciuta → ``KeyError``, anche se la
    lingua ce l'avrebbe: le due tabelle devono avere le stesse chiavi (lo prova
    un test), e l'inglese è il riferimento.
    """
    english = _TEXT[_FALLBACK_LANGUAGE][key]
    lang = (language if language is not None else current_language()).strip().lower()
    template = _TEXT.get(lang, {}).get(key, english)
    return template.format(**fields) if fields else template


def languages() -> tuple[str, ...]:
    """Le lingue che la tabella conosce (per i test)."""
    return tuple(_TEXT)


def keys(language: str = _FALLBACK_LANGUAGE) -> frozenset[str]:
    """Le chiavi di *language* (per i test)."""
    return frozenset(_TEXT[language])
