"""A cosa serve ciascun job di sistema, in una riga che l'utente possa leggere.

Un job di sistema che compare in un elenco senza dire cosa fa è solo un motivo di
sospetto: questi girano da soli e spendono token o rete, quindi devono sapersi
presentare. La tabella nasce nel tool ``cron`` e vive qui da quando i lettori sono
due — il tool, che la mostra al modello, e la WebUI, che la mostra all'utente.

**Chi registra un lavoratore periodico in ``GatewayContainer.build`` gli deve
anche una riga qui.** Non è una raccomandazione: ``tests/cron/test_system_job_purposes.py``
confronta questa tabella con gli id che il container registra davvero, e senza la
riga è rosso. La regola valeva anche prima, scritta in un commento — ed è stata
persa lo stesso: il giardiniere è stato registrato il giorno che è nato e si è
presentato come «System-managed internal job.» finché quel test non è esistito.

I job **ritirati** (``_RETIRED_SYSTEM_JOBS``) non stanno qui: il loro job sparisce
dallo store al primo avvio della versione che li ritira, e nel frattempo cade sul
fallback, che è esattamente ciò che vogliamo dire di loro.
"""

from __future__ import annotations

# Chiave: l'``id`` del job (non il nome). Sono la stessa stringa per tutti e
# quattro, ma è l'id che il container passa a ``register_system_job`` ed è per id
# che ``retire_system_job`` toglie — il nome un utente può riusarlo per un suo
# promemoria, l'id no.
SYSTEM_JOB_PURPOSES: dict[str, str] = {
    "dream": "Dream memory consolidation for long-term memory.",
    "gardener": (
        "Gardener: turns what you told Jenny into pages under the project wikis, "
        "and promotes what it learns."
    ),
    "heartbeat": "Heartbeat: checks HEARTBEAT.md for tasks you left for Jenny.",
    "update_check": (
        "Update check: looks for a newer Jenny app release and tells you once per version."
    ),
}

# Cosa si dice di un job di sistema che la tabella non conosce. Capita per un job
# ritirato non ancora spazzato via dallo store, e per un aggiornamento che
# introduce un lavoratore senza la sua riga — nel secondo caso il test è rosso
# prima che il telefono lo veda, quindi questo fallback copre soltanto il primo.
UNKNOWN_SYSTEM_JOB_PURPOSE = "System-managed internal job."


def system_job_purpose(job_id: str) -> str:
    """La riga di presentazione di *job_id*, o il fallback se non la conosciamo."""
    return SYSTEM_JOB_PURPOSES.get(job_id, UNKNOWN_SYSTEM_JOB_PURPOSE)
