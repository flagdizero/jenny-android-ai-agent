"""Le regole comuni a ogni run interno: è finito bene, e può dichiararlo.

Dream e il giardiniere sono due mestieri diversi con la stessa forma: un
turno effimero che legge un input, scrive qualcosa, e poi **registra di averlo
digerito** — il cursore su ``history.jsonl``, il
delta del diario di un progetto. Il progresso è un'affermazione, e farla dopo un
run che non ha prodotto nulla significa perdere quell'input per sempre: non è una
somiglianza estetica fra i due, è la stessa invariante, e sta scritta una volta.

Vive in un modulo suo e non su ``MemoryStore`` perché quella classe è documentata
come «pure file I/O for memory files», e questa non è I/O sui file di memoria:
il giardiniere non ne apre nessuno. Prima erano ``@staticmethod``
lì, e ``gardener.py`` importava ``MemoryStore`` dentro la funzione
soltanto per raggiungerle — un import locale che non serviva a rompere un ciclo,
ma a mascherare una collocazione sbagliata.

``MemoryStore`` ri-esporta i tre nomi come alias, quindi
``MemoryStore.internal_run_should_commit`` continua a funzionare: sono in uso nei
test e in ``docs/internals/architecture.md``, e un rename di massa non è ciò che
questo spostamento vuole comprare.
"""

from __future__ import annotations

from typing import Any

from loguru import logger


async def run_internal_turn(
    agent: Any, prompt: str, *, session_key: str, tools: Any, reporter: Any = None
) -> Any:
    """Il turno effimero di una passata interna (Dream, la sua revisione, il
    giardiniere, la bacheca): con la cassetta della passata, e il progresso alla
    bolla di lavoro se qualcuno la guarda, a nessuno altrimenti."""
    from jenny.session.turn_visibility import silent_progress

    return await agent.process_direct(
        prompt,
        session_key=session_key,
        ephemeral=True,
        tools=tools,
        on_progress=reporter.on_progress if reporter is not None else silent_progress,
        on_retry_wait=reporter.on_retry_wait if reporter is not None else None,
    )


def internal_run_completed(resp: object | None) -> bool:
    """Return True only when an ephemeral internal agent turn completed cleanly."""
    metadata = getattr(resp, "metadata", None)
    return isinstance(metadata, dict) and metadata.get("_stop_reason") == "completed"


def internal_run_should_commit(
    resp: object | None,
    file_states: object | None,
) -> bool:
    """Return True quando un run interno può registrare il proprio progresso.

    Regola condivisa da Dream (avanzamento del cursore su ``history.jsonl``)
    e dal giardiniere
    (avanzamento del cursore sul diario di un progetto). In tutti i casi il
    progresso è un'affermazione — "questo input è stato digerito" — e farla dopo
    un run che non ha prodotto nulla per un blocco di policy significa perdere
    quell'input per sempre. Si registra quindi solo se il run:

    - è completato pulito (``internal_run_completed``), **e**
    - nessun rifiuto di budget è rimasto aperto
      (``unrecovered_refusals == 0``: un file rifiutato e poi riscritto
      accorciato non conta più), **e**
    - ha scritto almeno un file (``writes_ok > 0``), **oppure** non ha mai
      tentato una scrittura (``writes_attempted == 0``) — il caso legittimo
      "non c'era niente da cambiare".

    Se ha tentato scritture e nessuna è riuscita NON si registra: l'input va
    riprocessato al run seguente.

    Il rifiuto di budget va guardato a parte dai due contatori aggregati: un
    run che scrive con successo una skill e si vede rifiutare ``MEMORY.md`` ha
    comunque ``writes_ok > 0``, e su ``ok``/``attempted`` passerebbe per
    riuscito. Il fatto rifiutato non è su disco e, registrato il progresso, non
    tornerebbe in nessun batch successivo: perso.

    Ma il rifiuto che conta è quello **rimasto aperto**, non quello avvenuto —
    ed è una misura di *contenuto*, non un conteggio per run. Il messaggio di
    rifiuto chiede al modello di liberare spazio e riscrivere nello stesso
    turno; se obbedisce e il contenuto atterra, il run ha fatto il suo lavoro.
    Guardare il contatore cumulativo trattava quel successo come un
    fallimento: cursore fermo, stesso batch due ore dopo, ``stuck`` in salita e
    un allarme che annunciava scritture rifiutate che erano riuscite — con i
    tetti armati, lo stato normale e non un caso limite. Si legge quindi
    ``unrecovered_refusals``, che si chiude solo quando una scrittura riuscita
    su *quel* percorso fa atterrare almeno una delle righe rifiutate
    (v. ``FileStates.record_write_refused`` per il perché di "almeno una").

    Il livelock che resta — rifiuto che nessuno recupera — esce dal review
    forzato (v. ``agent/dream_cycle.py``), non da un commit più permissivo.

    **Cosa questa regola non copre.** ``writes_ok > 0`` risponde "qualcosa è
    atterrato", non "tutto è atterrato": un run con ``ok=2, attempted=3`` e
    nessun rifiuto di *budget* — una scrittura bloccata dalla policy, o
    fallita in I/O — passa. Per Dream è la semantica voluta; a chi
    vuole tenere il progresso anche su un fallimento parziale la condizione in
    più tocca **aggiungerla al proprio punto di chiamata**, come fa il
    giardiniere (``agent/gardener.py``) e come fa Dream per l'atterraggio del
    batch (``runtime/cron_dispatch.py``). Qui non si aggiunge un parametro:
    sarebbe una funzione con due contratti e un default a portata di mano del
    prossimo chiamante.

    ``file_states`` è tollerante a ``None`` / oggetti senza i contatori di
    scrittura (fallback conservativo: nessun avanzamento) per non far
    esplodere il chiamante se il registry non è quello costruito da
    ``MemoryStore.build_dream_tools``. Un registry senza
    ``unrecovered_refusals`` ripiega sul contatore cumulativo — comportamento
    di prima, che per chi non ha il gancio è identico — e in assenza di
    entrambi vale zero: chi non ha i contatori non ha nemmeno il gancio che li
    incrementa (``_FsTool._check_write_size``), quindi non può aver rifiutato
    nulla.
    """
    if not internal_run_completed(resp):
        return False
    writes_ok = getattr(file_states, "writes_ok", None)
    writes_attempted = getattr(file_states, "writes_attempted", None)
    if not isinstance(writes_ok, int) or not isinstance(writes_attempted, int):
        return False
    outstanding = getattr(file_states, "unrecovered_refusals", None)
    if not isinstance(outstanding, int):
        outstanding = getattr(file_states, "writes_refused_budget", 0)
    if isinstance(outstanding, int) and outstanding > 0:
        return False
    if writes_ok > 0:
        return True
    return writes_attempted == 0


def prune_internal_sessions(sessions: Any, prefix: str, *, keep: int = 10) -> list[str]:
    """Toglie le sessioni ``<prefix>:…`` meno recenti, tenendone *keep*.

    Solo le chiavi col prefisso; le sessioni di chiunque altro non si toccano.
    Ritorna le chiavi tolte, perché il chiamante tolga anche quel che tiene in
    memoria sotto la stessa chiave (cache del ``SessionManager``, task attivi,
    lock di sessione): togliere solo la sessione salvata lascerebbe quelle
    cache crescere per sempre.
    """
    list_keys = getattr(sessions, "list_keys", None)
    if not callable(list_keys):  # un doppio senza elenco: niente da potare
        return []
    keys = list_keys(f"{prefix}:")
    if len(keys) <= keep:
        return []
    removed_keys: list[str] = []
    for key in keys[: len(keys) - keep]:
        try:
            if sessions.delete(key):
                logger.debug("Pruned old {} session: {}", prefix, key)
                removed_keys.append(key)
        except Exception:  # noqa: BLE001 — una potatura mancata non ferma il lavoro
            logger.opt(exception=True).warning("Failed to prune {} session {}", prefix, key)
    return removed_keys
