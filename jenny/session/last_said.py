"""Se l'utente ha risposto a un promemoria nella sua conversazione.

La bacheca di un quaderno lo chiede per i suoi promemoria: un cron che apre un
discorso (una seduta serale) ha avuto risposta? Il post-it lo dice in una riga
invece di ripetere il messaggio. Si legge la coda della sessione direttamente,
senza ``SessionManager``: la bacheca non deve mettere in cache una
conversazione per guardarne la fine.

**Per posizione, non per orario.** «Ha risposto» vuol dire: dopo la riga con cui
il cron è entrato nella chat c'è una riga scritta dall'utente. Il confronto fra
il ``timestamp`` dei messaggi (ora locale senza fuso) e l'istante dell'esito
sbagliava dopo un cambio di fuso, e Jenny il fuso lo segue quando l'utente
viaggia.
"""

from __future__ import annotations

from pathlib import Path

from jenny.session.history_meta import is_synthetic_history_row

# Quante righe si guardano dal fondo. Una risposta all'apertura sta in fondo; un
# turno lungo di strumenti dopo di lei la può allontanare di qualche decina di
# righe, non di centinaia. Un cron la cui ultima riga è più indietro resta senza
# risposta nota: il post-it dice solo che ha scritto.
TAIL_ROWS = 200


def said_by_user(message: dict) -> bool:
    """Vero per una riga che l'utente ha scritto: niente cron, rientri, comandi."""
    return (
        message.get("role") == "user"
        and not is_synthetic_history_row(message)
        and message.get("_command") is not True
    )


def cron_replies(workspace: Path, session_key: str) -> dict[str, bool]:
    """Per ogni cron entrato di recente in *session_key*: l'utente ha scritto dopo il suo ultimo giro?

    La chiave è l'id del job. Un job che non compare nella coda non c'è: non si sa.
    Un database che non si legge solleva: «non si sa» non è «non ha risposto».
    """
    from jenny.cron.session_turns import CRON_HISTORY_META
    from jenny.session import store

    rows = store.tail(store.session_database(workspace), session_key, TAIL_ROWS)
    replies: dict[str, bool] = {}
    user_after = False
    # Dal più nuovo: la prima riga di un job che s'incontra è il suo ultimo giro.
    for message in rows:
        if said_by_user(message):
            user_after = True
            continue
        job_id = message.get("cron_job_id")
        if message.get(CRON_HISTORY_META) is True and isinstance(job_id, str):
            replies.setdefault(job_id, user_after)
    return replies
