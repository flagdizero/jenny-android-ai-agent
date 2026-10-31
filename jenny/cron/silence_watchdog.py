"""L'allarme su un controllo morto che NON passa dal modello.

Ogni avviso su un controllo guasto, fin qui, finisce nel tool ``message``: la
soglia entra nel prompt (``should_escalate_could_not_check``,
``tasks_due_for_escalation``), il turno decide se chiamarlo, e lo stato registra
che l'avviso è uscito. È la struttura giusta — solo il turno sa se il controllo è
riuscito *adesso*, e solo il modello può scrivere una frase che significhi
qualcosa — ma ha un presupposto: che il modello faccia la sua parte.

Quando è quel presupposto a cadere non avvisa nessuno. E non è ipotetico: la
combinazione misurata il 2026-08-17 sul dispatcher vero fa 19 run consecutivi
con il controllo morto e zero avvisi, perché il timbro ``escalated`` viene dedotto
da ``spoke`` — un booleano di turno senza soggetto — e una volta messo il prompt
dice "non ripeterlo". La correzione di quel difetto sta altrove (il marcatore
``CHECK_WARNED``); questo modulo è ciò che rende quella classe di guasti non
fatale, adesso e per i difetti dello stesso genere che verranno.

La forma è quella che per Dream funziona già (``agent/dream_cycle.py::_alert_stuck``):
``notify_delivery``, cioè zero token, nessun turno LLM, nessuna dipendenza dal
modello, e no-op fuori da Android o senza event loop — quindi anche nei test.

**Cosa questo allarme non è.** Non è un messaggio in chat: ``post_alert`` posta
una notifica di sistema, e solo con l'app in background. Non lascia traccia nel
transcript e non va scritto come se la lasciasse. Serve a far *venire a
guardare*; il dettaglio — quale controllo, da quanti run, se e quando all'utente
è stato detto — si legge da ``/cron``, ed è il motivo per cui quella vista esiste.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from jenny.cron.could_not_check import ESCALATE_AFTER_FAILURES
from jenny.cron.types import CronJobState

# Quanti controlli mancati di fila prima che il silenzio diventi il problema.
# Il doppio della soglia di escalation, e il doppio è la scelta: sotto il doppio
# non si sa ancora distinguere "il modello non avvisa" da "il modello sta per
# avvisare al prossimo giro", e un allarme che parte insieme a quello del
# modello sarebbe soltanto il doppione che tutto questo lavoro esiste per
# togliere. A K=3 e mezz'ora di intervallo, sei run sono circa tre ore.
WATCHDOG_AFTER_FAILURES = ESCALATE_AFTER_FAILURES * 2

# Quanto silenzio si concede DOPO che all'utente si è parlato. Un timbro
# ``escalated`` dice che l'avviso è uscito una volta, non che il guasto sia stato
# capito: se il controllo è ancora morto un giorno dopo, la notizia è nuova.
# Ventiquattr'ore e non meno, perché sotto quella soglia questo allarme
# ricomincerebbe a fare il rumore da cui si è partiti.
WATCHDOG_QUIET_MS = 24 * 60 * 60 * 1000


def _is_silently_broken(
    *,
    consecutive: int,
    escalated: bool,
    escalated_at_ms: int | None,
    now_ms: int,
) -> bool:
    """Questo controllo è rotto da troppo tempo e l'utente non ne sa niente.

    Tre casi, e il terzo è quello che sembra un cavillo: una voce timbrata
    ``escalated`` **senza** ora (``escalated_at_ms is None``) è stata scritta
    prima che quel campo esistesse, e ``rearm_after_user_message`` la considera
    apposta non riarmabile. Vale come "gli si è parlato, non si sa quando": per
    il re-arm è la scelta prudente, qui è il contrario — un controllo morto da
    sei run con un timbro di data ignota è esattamente il caso che questo
    modulo esiste per rompere.
    """
    if consecutive < WATCHDOG_AFTER_FAILURES:
        return False
    if not escalated:
        return True
    if escalated_at_ms is None:
        return True
    return now_ms - escalated_at_ms >= WATCHDOG_QUIET_MS


def silently_broken_checks(state: CronJobState, *, now_ms: int) -> list[tuple[str, int]]:
    """``(etichetta, run mancati)`` dei controlli guasti di cui l'utente non sa.

    Due forme di stato, una funzione: l'heartbeat tiene una voce per task in
    ``task_checks``, un monitor ha un controllo solo e i suoi contatori stanno
    sul job. Quando la mappa per-task c'è è **l'unica** a essere guardata: i
    contatori del job sono il riassunto della stessa cosa ("almeno un task non è
    partito"), e sommare le due letture farebbe due allarmi per un guasto.
    """
    if state.task_checks:
        return [
            (entry.label or task_id, entry.consecutive_could_not_check)
            for task_id, entry in state.task_checks.items()
            if _is_silently_broken(
                consecutive=entry.consecutive_could_not_check,
                escalated=entry.escalated,
                escalated_at_ms=entry.escalated_at_ms,
                now_ms=now_ms,
            )
        ]
    if _is_silently_broken(
        consecutive=state.consecutive_could_not_check,
        escalated=state.could_not_check_escalated,
        # Il monitor non ha un'ora dell'avviso: il suo ``escalated`` è un
        # booleano solo. Vale quindi come timbro senza data (v.
        # ``_is_silently_broken``), e la finestra di quiete su questo ramo non si
        # applica: oltre soglia l'allarme vale a ogni run. Non è più rumoroso di
        # quanto suoni — il tag coalizza, quindi resta una notifica sola sempre
        # aggiornata — e l'alternativa sarebbe un campo nuovo nello store per
        # datare un avviso di cui il monitor non registra l'ora.
        escalated_at_ms=None,
        now_ms=now_ms,
    ):
        return [("", state.consecutive_could_not_check)]
    return []


def format_silence_alarm(job_name: str, checks: list[tuple[str, int]]) -> str:
    """La frase dell'allarme, nella lingua dell'utente. Una stesura sola.

    Nomina il controllo e dice da quanti run è fermo, perché sono le due cose che
    decidono se valga la pena alzarsi. Non dice il *motivo*: quello lo scrive il
    modello e sta in ``last_error``, e ripeterlo qui vorrebbe dire tenerne due
    copie che possono divergere. Per vederlo si chiede a Jenny in chat — è lei
    che legge ``last_error`` col tool ``cron`` — e non con un comando da
    digitare, che dalla tendina delle notifiche non si raggiunge.
    """
    from jenny.runtime.user_text import say

    if len(checks) == 1:
        label, runs = checks[0]
        return say("alarm.check_silent", name=label or job_name, runs=runs)
    worst = max(runs for _, runs in checks)
    return say("alarm.checks_silent", count=len(checks), name=job_name, runs=worst)


def alert_silently_broken_checks(
    job_name: str, state: CronJobState, *, now_ms: int
) -> list[tuple[str, int]]:
    """Manda l'alert di sistema se c'è un controllo morto di cui nessuno ha detto.

    Ritorna le voci per cui è partito, così il chiamante possa loggarle senza
    rifare il conto.

    Riparte a **ogni** run oltre soglia e non solo all'attraversamento, come
    ``_alert_stuck``: il tag deriva dall'etichetta, quindi il nuovo alert
    sostituisce il precedente invece di sommarsi, e chi ha scartato la notifica
    la rivede al giro dopo. Per un allarme che significa "questo controllo è
    morto e non te l'ha detto nessuno" è il comportamento voluto.

    L'etichetta porta un suffisso e non è il nome del job nudo: il tag
    (``cron:<label>``) è ciò che fa coalizzare gli alert, e con il nome nudo
    questa notifica sostituirebbe quella di un messaggio vero dello stesso job —
    che è l'unica notifica che l'utente non deve perdere.

    Import locali come in ``agent/dream_cycle.py::_alert_stuck``: nel grafo dei
    moduli ``jenny/cron`` non deve dipendere da ``jenny/runtime`` e da
    ``jenny/webui`` per una riga di allarme.
    """
    checks = silently_broken_checks(state, now_ms=now_ms)
    if not checks:
        return []

    from jenny.runtime.notifier import notify_delivery
    from jenny.runtime.user_text import say
    from jenny.webui.metadata import WEBUI_MESSAGE_SOURCE_METADATA_KEY

    metadata: dict[str, Any] = {
        WEBUI_MESSAGE_SOURCE_METADATA_KEY: {
            "kind": "cron",
            "label": say("alarm.check_label", name=job_name),
        }
    }
    notify_delivery(format_silence_alarm(job_name, checks), metadata)
    logger.error(
        "Cron: {} check(s) in job '{}' have been failing unreported: {}",
        len(checks),
        job_name,
        "; ".join(f"{label or job_name} ({runs} in a row)" for label, runs in checks),
    )
    return checks
