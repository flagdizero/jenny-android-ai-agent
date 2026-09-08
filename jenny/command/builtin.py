"""Built-in slash command handlers."""

from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from typing import TYPE_CHECKING

from jenny import __version__
from jenny.bus.events import OutboundMessage
from jenny.command.router import CommandContext, CommandRouter
from jenny.utils.helpers import build_status_content

if TYPE_CHECKING:
    from jenny.agent.dream_review import ReviewOutcome
    from jenny.agent.gardener import GardenerOutcome


# Quel che si risponde a chi digita una forma che non esiste piu'. **Da togliere
# dopo la 0.10.x** (scritto il 31/08/2026): e' una cortesia verso la memoria delle
# dita e verso la documentazione che gira nelle installazioni gia' aggiornate, non
# un ramo per sempre.
#
# Il prefisso resta registrato nel router *proprio* per arrivare qui: senza,
# `/dream budget` non sarebbe piu' un comando e finirebbe **al modello** come
# messaggio — che e' il modo peggiore di dire "non esiste piu'".
def _moved_to_settings(command: str, section: str, still: str) -> str:
    """«Quell'argomento non c'e' piu', la manopola e' in Impostazioni → <sezione>»."""
    return (
        f"`{command}` does not take arguments any more: those settings live in "
        f"**Settings → {section}**, where they sit next to the numbers they act on.\n\n"
        f"{still}"
    )


async def cmd_stop(ctx: CommandContext) -> OutboundMessage:
    """Cancel all active tasks and subagents for the session."""
    loop = ctx.loop
    msg = ctx.msg
    total = await loop._cancel_active_tasks(ctx.key)
    if total:
        # Il turno ripudiato salta il proprio restore/turn_end: li emette /stop,
        # in modo sincrono e deterministico (la UI riceve sempre turn_end).
        loop._restore_cancelled_turn(ctx.key)
        await loop._emit_stop_turn_end(msg, ctx.key)
    content = f"Stopped {total} task(s)." if total else "No active task to stop."
    return OutboundMessage(
        channel=msg.channel, chat_id=msg.chat_id, content=content,
        metadata=dict(msg.metadata or {})
    )


async def cmd_status(ctx: CommandContext) -> OutboundMessage:
    """Build an outbound status message for a session."""
    loop = ctx.loop
    session = ctx.session or loop.sessions.get_or_create(ctx.key)
    ctx_est = 0
    with suppress(Exception):
        ctx_est, _ = loop.consolidator.estimate_session_prompt_tokens(session)
    if ctx_est <= 0:
        ctx_est = loop._last_usage.get("prompt_tokens", 0)

    active_tasks = loop._active_tasks.get(ctx.key, [])
    task_count = sum(1 for t in active_tasks if not t.done())
    with suppress(Exception):
        task_count += loop.subagents.get_running_count_by_session(ctx.key)
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=build_status_content(
            version=__version__, model=loop.model,
            start_time=loop._start_time, last_usage=loop._last_usage,
            context_window_tokens=loop.context_window_tokens,
            session_msg_count=len(session.get_history(max_messages=0)),
            context_tokens_estimate=ctx_est,
            active_task_count=task_count,
            max_completion_tokens=getattr(
                getattr(loop.provider, "generation", None), "max_tokens", 8192
            ),
        ),
        metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
    )


async def cmd_new(ctx: CommandContext) -> OutboundMessage:
    """Stop active task and start a fresh session."""
    # Import locale: ``jenny.agent.memory`` passa da ``jenny.agent.__init__``,
    # che importa ``AgentLoop``, che importa questo modulo. E' lo stesso motivo
    # per cui gli altri handler qui dentro importano dove servono.
    from jenny.agent.memory import HISTORY_FLOOR_METADATA_KEY

    loop = ctx.loop
    cancelled = await loop._cancel_active_tasks(ctx.key)
    if cancelled:
        # Materializza il lavoro parziale del turno fermato PRIMA dello
        # snapshot, così finisce nell'archivio invece di andare perso.
        loop._restore_cancelled_turn(ctx.key)
        await loop._emit_stop_turn_end(ctx.msg, ctx.key)
    session = ctx.session or loop.sessions.get_or_create(ctx.key)
    snapshot = session.messages[session.last_consolidated:]
    session.clear()
    # Il diario non entra piu' nel prompt di questa sessione da qui in giu'.
    # Senza questa riga `/new` azzerava i messaggi ma lasciava nel system prompt
    # il blocco `# Recent History`, cioe' i riassunti di ogni auto-compattazione
    # della conversazione appena buttata: il modello ripartiva "pulito" con
    # davanti il sommario di quel che aveva smesso di ricordare. Il cursore di
    # Dream resta dov'e' — quelle voci devono ancora finire in MEMORY.md. Vedi
    # ``memory.HISTORY_FLOOR_METADATA_KEY``.
    session.metadata[HISTORY_FLOOR_METADATA_KEY] = loop.context.memory.current_history_cursor()
    loop.sessions.save(session)
    loop.sessions.invalidate(session.key)
    # La conversazione e' vuota, quindi non contiene piu' il contenuto di nessun
    # file: il dedup delle letture va dimenticato insieme ai messaggi, o la prima
    # lettura della sessione nuova torna «invariato dall'ultima lettura» a chi non
    # ha mai letto niente.
    loop.forget_file_reads(session.key)
    if snapshot:
        # L'altra meta' dello stesso confine, e non e' coperta dal pavimento: il
        # riassunto di questo snapshot lo scrive l'archiviazione **dopo**, quindi
        # con un cursore piu' alto. `prompt_visible=False` lo dichiara roba di
        # Dream e non di prompt, una volta e sulla voce, cosi' non serve
        # nessuna seconda scrittura del pavimento a task finito — che arriverebbe
        # a sessione ormai ricaricata.
        loop._schedule_background(
            loop.consolidator.archive(
                snapshot, session_key=ctx.key, prompt_visible=False,
            )
        )
    return OutboundMessage(
        channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
        content="New session started.",
        # `_session_boundary` dice ai client di rendere questa conferma come
        # separatore invece che come bolla. Il transcript NON viene toccato da
        # /new (si azzera il contesto del modello, non il registro visibile):
        # senza un confine esplicito la conversazione prosegue a schermo come
        # se il comando fosse stato ignorato.
        metadata={**dict(ctx.msg.metadata or {}), "_session_boundary": True},
    )


def _format_preset_names(names: list[str]) -> str:
    return ", ".join(f"`{name}`" for name in names) if names else "(none configured)"


def _model_preset_names(loop) -> list[str]:
    return sorted(loop.model_presets)


def _active_model_preset_name(loop) -> str:
    return loop.model_preset or "(none)"


def _command_error_message(exc: Exception) -> str:
    return str(exc.args[0]) if isinstance(exc, KeyError) and exc.args else str(exc)


def _model_command_status(loop) -> str:
    names = _model_preset_names(loop)
    active = _active_model_preset_name(loop)
    return "\n".join([
        "## Model",
        f"- Current model: `{loop.model}`",
        f"- Current preset: `{active}`",
        f"- Available presets: {_format_preset_names(names)}",
    ])


async def cmd_model(ctx: CommandContext) -> OutboundMessage:
    """Show or switch model presets."""
    loop = ctx.loop
    args = ctx.args.strip()
    metadata = {**dict(ctx.msg.metadata or {}), "render_as": "text"}

    if not args:
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content=_model_command_status(loop),
            metadata=metadata,
        )

    parts = args.split()
    if len(parts) != 1:
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content="Usage: `/model [preset]`",
            metadata=metadata,
        )

    name = parts[0]
    try:
        await loop.set_model_preset(name)
    except (KeyError, ValueError) as exc:
        names = _model_preset_names(loop)
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content=(
                f"Could not switch model preset: {_command_error_message(exc)}\n\n"
                f"Available presets: {_format_preset_names(names)}"
            ),
            metadata=metadata,
        )

    max_tokens = getattr(getattr(loop.provider, "generation", None), "max_tokens", None)
    lines = [
        f"Switched model preset to `{loop.model_preset}`.",
        f"- Model: `{loop.model}`",
        f"- Context window: {loop.context_window_tokens}",
    ]
    if max_tokens is not None:
        lines.append(f"- Max output tokens: {max_tokens}")
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content="\n".join(lines),
        metadata=metadata,
    )


def _int_or_zero(value: object) -> int:
    """Un contatore che arriva da un doppio può non essere un intero.

    Stava nel blocco di ``/dream budget``, che il 31/08/2026 è stato spostato in
    Impostazioni. Il suo chiamante però è il ramo che **lancia** Dream (v.
    ``refused=`` più sotto), quindi è risalita qui invece di andarsene con il
    resto: cancellarla insieme al blocco rompeva un percorso che quel lavoro non
    doveva toccare.
    """
    return value if isinstance(value, int) else 0


async def cmd_dream(ctx: CommandContext) -> OutboundMessage:
    """Manually trigger a Dream consolidation run, or read/tune the memory budgets."""
    loop = ctx.loop
    msg = ctx.msg

    args = ctx.args.strip()
    if args:
        # `/dream budget` viveva qui. Le manopole della memoria stanno in
        # Impostazioni dal 31/08/2026: v. ``_MOVED_TO_SETTINGS``.
        return _reply(msg, _moved_to_settings(
                "/dream",
                "Memory",
                "`/dream` on its own still runs a consolidation now.",
            ))

    from jenny.agent.dream_cycle import (
        DREAM_ALREADY_RUNNING,
        claim_dream_cycle,
        release_dream_cycle,
    )

    async def _dream_cycle():
        async def _silent(*_args, **_kwargs):
            pass

        # Prologo ed epilogo del ciclo sono gli stessi del job cron, e stanno in
        # un modulo solo: erano due copie della stessa sequenza, e ogni volta che
        # una cresceva l'altra restava indietro in silenzio — il guard del budget,
        # il gauge, i contatori del review sono arrivati qui tre commit dopo che
        # erano di là. Qui resta ciò che è davvero di questo percorso: il turno
        # incrementale e la frase da dire a chi ha lanciato il comando.
        from jenny.agent.dream_cycle import (
            NO_ENTRIES,
            batch_was_not_consolidated,
            begin_dream_cycle,
            finish_dream_cycle,
            take_dream_snapshot,
        )
        from jenny.agent.memory import MemoryStore
        from jenny.agent.memory_budget import render_gauge
        from jenny.config.loader import load_config

        dream_session_key = MemoryStore.dream_session_key
        prune_dream_sessions = MemoryStore.prune_dream_sessions

        store = loop.context.memory
        # Il checkpoint pre-Dream lo possiede il container, che lo appende al
        # loop accanto al dispatcher cron. ``getattr`` perché il loop può non
        # averlo — un test, un percorso che costruisce l'agente da sé — e la sua
        # assenza ha già una traduzione sola e giusta in ``take_dream_snapshot``:
        # ``snapshotted=False``, cioè "le tue cancellazioni sono definitive".
        #
        # Prima di ``04de3cc`` questo run faceva solo consolidamento
        # incrementale, e girare senza rete era già un buco. Ora può far partire
        # un review pass, che è esplicitamente autorizzato a ristrutturare e
        # cancellare (``agent/dream_review.md``): è un'altra cosa.
        snapshot_cb = getattr(loop, "snapshot_before_dream", None)
        content = ""
        review_note = ""
        resp = None
        t0 = time.monotonic()
        try:
            # Le due metà di `/dream` devono raccontare la stessa cosa, e per
            # tre commit non lo facevano. `/dream budget` legge lo stesso
            # ``budget_report`` e stampa all'utente dimensioni, tetti e
            # percentuali: chi li ha appena letti e poi lancia `/dream`
            # conclude — ragionevolmente — che quel tetto valga per il run che
            # sta avviando. Senza il guard montato qui non valeva per niente, e
            # non era "una feature che non gira": era un limite annunciato da
            # una metà del comando e ignorato dall'altra, cioè la porta di
            # servizio con cui si aggirava l'enforcement lanciando Dream a
            # mano. Il gauge segue la stessa logica: il modello non può
            # rispettare uno spazio di cui non gli si dice la misura.
            cfg = load_config().agents.defaults.dream
            prologue = await begin_dream_cycle(
                loop, store=store, cfg=cfg, take_snapshot=snapshot_cb,
            )
            if prologue.review is not None:
                review_note = _format_dream_review_note(prologue.review)

            # ``advanced=None`` vuol dire "il turno non ha mancato niente": è il
            # valore del ramo senza storia, e anche quello di un turno che è
            # crashato. Non ``False``, che incrementerebbe ``stuck`` e quindi
            # dichiarerebbe un livelock del budget dove c'è stata un'eccezione.
            advanced: bool | None = None
            try:
                result = store.build_dream_prompt(gauge=render_gauge(prologue.report))
                if result is None:
                    await loop.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel, chat_id=msg.chat_id,
                        content=_prefix_review_note(
                            review_note, _format_dream_no_input_message()
                        ),
                        metadata={"render_as": "text"},
                    ))
                    return
                prompt, last_cursor = result[0], result[1]
                # V. il gemello in ``runtime/cron_dispatch.py``: il tipo del batch
                # sceglie la cassetta, e un doppio che ritorna una coppia nuda
                # resta un batch personale.
                scope = getattr(result, "scope", "personal")
                if prologue.review is None:
                    # Un solo checkpoint per ciclo: se il review è appena girato
                    # lo snapshot è già stato preso e copre anche il turno
                    # incrementale che segue, mentre un secondo "pre_dream"
                    # archiviato dopo il review non sarebbe pre-niente.
                    await take_dream_snapshot(snapshot_cb)
                key = dream_session_key()
                dream_tools = store.build_dream_tools(
                    write_size_guard=prologue.guard, scope=scope,
                )
                # Legato prima del turno: il ``finally`` qui sotto lo legge, e un
                # ``process_direct`` che solleva lascerebbe altrimenti il nome non
                # definito — cioè un ``NameError`` dentro il ``finally``, che si
                # porterebbe via la chiusura del ciclo. È lo stesso guasto che
                # quel ``finally`` esiste per chiudere, reintrodotto un livello
                # più in basso.
                dream_file_states = getattr(dream_tools, "file_states", None)
                resp = await loop.process_direct(
                    prompt,
                    session_key=key,
                    ephemeral=True,
                    tools=dream_tools,
                    on_progress=_silent,
                )
                elapsed = time.monotonic() - t0
                advanced = MemoryStore.dream_should_advance_cursor(resp, dream_file_states)
                # Stessa domanda in più del percorso cron, e per la stessa
                # ragione: "ha scritto" non è "il batch è atterrato". Chi lancia
                # `/dream` a mano deve vedere lo stesso esito del job, o il
                # comando torna a essere la porta di servizio del meccanismo.
                # Il tool per voci del run appena concluso. ``getattr`` con un default
                # perché ``build_dream_tools`` è sostituito nei test da doppi che non lo
                # espongono: un run senza quel tool è un run con zero voci, non un errore.
                entries = getattr(dream_tools, "memory_entries", None) or NO_ENTRIES
                held_batch = advanced and batch_was_not_consolidated(
                    before=prologue.report,
                    history_text=MemoryStore.dream_prompt_history(prompt),
                    stuck=prologue.stuck + prologue.nothing_new,
                    # L'esito in voci del run, dal tool esposto sul registry: sono questi
                    # numeri a rendere la domanda una verifica invece di una stima.
                    added=entries.entries_added,
                    replaced=entries.entries_replaced,
                    already_present=entries.entries_already_present,
                    # Se non ha tentato nessuna scrittura non ha mancato niente:
                    # ha deciso che non c'era da scrivere. V. il docstring.
                    attempted=getattr(dream_file_states, "writes_attempted", 0),
                )
                if held_batch:
                    advanced = False
                if advanced:
                    store.set_last_dream_cursor(last_cursor)
                    content = f"Dream completed in {elapsed:.1f}s."
                elif held_batch:
                    content = (
                        f"Dream completed in {elapsed:.1f}s but consolidated nothing "
                        "from its batch (no memory file grew); memory cursor was not "
                        "advanced, so those entries come back next run."
                    )
                elif MemoryStore.dream_run_completed(resp):
                    content = (
                        f"Dream completed in {elapsed:.1f}s but wrote nothing "
                        "(attempts blocked/refused); memory cursor was not advanced."
                    )
                else:
                    content = (
                        f"Dream did not complete after {elapsed:.1f}s; "
                        "memory cursor was not advanced."
                    )
                content = _prefix_review_note(review_note, content)
            finally:
                # Anti-livelock, con la stessa regola del percorso cron perché è
                # la stessa funzione (la spiegazione sta lì). Contare anche da qui
                # è ciò che impedisce a un utente che lancia Dream a mano di
                # restare fuori dal meccanismo: un batch rifiutato dal budget
                # tornerebbe altrimenti identico a ogni `/dream`, per sempre.
                #
                # In un ``finally``, e vale per entrambi i percorsi: come ultima
                # istruzione del try, un turno che solleva la saltava del tutto —
                # quindi ``runs_since_review`` non avanzava e un Dream che
                # fallisce sempre non arrivava mai a un review pass.
                finish_dream_cycle(
                    store,
                    advanced=advanced,
                    runs_since_review=prologue.runs_since_review,
                    stuck=prologue.stuck,
                    nothing_new=prologue.nothing_new,
                    # La causa, che è ciò che decide quale dei due contatori sale:
                    # un rifiuto rimasto aperto significa "manca spazio" e un review
                    # può liberarlo; senza rifiuti non c'è niente da liberare.
                    refused=_int_or_zero(
                        getattr(dream_file_states, "unrecovered_refusals", 0)
                    ),
                )
        except Exception as e:
            elapsed = time.monotonic() - t0
            content = _prefix_review_note(
                review_note, f"Dream failed after {elapsed:.1f}s: {e}"
            )
        finally:
            # La contabilita' dei token **non passa da qui**: la fa
            # ``TokenUsageHook.after_iteration`` sul turno stesso, che e' l'unico
            # punto in cui l'``usage`` del provider esiste davvero. Qui c'era una
            # ``record_response_token_usage(resp, ...)``: ``resp`` e' un
            # ``OutboundMessage``, che un campo ``usage`` non ce l'ha e non l'ha mai
            # avuto, quindi quella riga non ha mai registrato niente in nessuno dei
            # cinque punti in cui era stata scritta. Toglierla non perde una misura
            # — ne toglie una finta.
            await asyncio.to_thread(store.compact_history)
            pruned_keys = prune_dream_sessions(loop.sessions.sessions_dir)
            if pruned_keys:
                loop.evict_pruned_sessions(pruned_keys)
        await loop.bus.publish_outbound(OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content=content,
        ))

    async def _run_dream():
        # Il ``finally`` sta **un livello fuori** dal lavoro, così non c'è un
        # cammino dentro ``_dream_cycle`` che possa saltarlo: ritorno, eccezione e
        # cancellazione passano tutti da qui. Una presa che resta presa spegnerebbe
        # Dream fino al riavvio del processo, e in silenzio.
        try:
            await _dream_cycle()
        finally:
            release_dream_cycle()

    # La presa si prende **qui**, sincrona, prima di ``create_task``: il corpo del
    # task non parte fino al primo punto di sospensione, quindi controllarla là
    # dentro lascerebbe passare due ``/dream`` di fila. E prendendola prima, la
    # risposta immediata al comando può dire la verità invece di promettere
    # "Dreaming..." a un ciclo che non partirà.
    if not claim_dream_cycle():
        return OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content=DREAM_ALREADY_RUNNING,
        )
    try:
        asyncio.create_task(_run_dream())
    except BaseException:
        # Se il task non arriva nemmeno a esistere, il suo ``finally`` non girerà:
        # la presa va restituita qui o resta appesa a un ciclo che non c'è.
        release_dream_cycle()
        raise
    return OutboundMessage(
        channel=msg.channel, chat_id=msg.chat_id, content="Dreaming...",
    )


def _prefix_review_note(note: str, content: str) -> str:
    """Antepone la riga sul review pass alla risposta, se c'è stato un review."""
    return f"{note}\n\n{content}" if note else content


def _format_dream_review_note(outcome: "ReviewOutcome") -> str:
    """Riga sul review pass, da anteporre alla risposta di `/dream`.

    Il review è un turno LLM in più, partito dentro un comando che l'utente ha
    lanciato a mano e che gli ha risposto "Dreaming...": tacerlo gli farebbe
    pagare token e attesa senza dirgli per cosa, e un turno in più partito in
    silenzio è una sorpresa, non una feature. Il percorso cron lo scrive solo
    nel log perché lì non c'è nessuno in ascolto; qui c'è.

    ``freed`` è il delta dei **tre file misurati**, non del workspace: un review
    che sposta una task spec da USER.md a una ``skills/<name>/SKILL.md`` — cosa
    che il suo prompt chiede esplicitamente — la conta come liberata. La frase
    lo dice, perché quel numero serve a tarare quei tre tetti e non a stimare
    quanto è dimagrito il disco.

    E ``freed`` da solo **non basta a dire cosa è successo**: sono due domande
    diverse, "quanto spazio" e "quali fatti", e la seconda è quella che l'utente
    riconosce. Un review che sposta sei voci personali da ``USER.md``
    all'archivio — cosa che ``dream_review.md`` autorizza esplicitamente —
    poteva rispondere "nothing was freed" se nello stesso passaggio un altro
    file era cresciuto: il numero era vero e la frase era falsa. Le degradazioni
    hanno quindi una riga loro, sempre, con gli id da citare a ``recall``.

    Terza notizia, e la sola su cui l'utente possa fare qualcosa subito: i
    **rifiuti di budget rimasti aperti**. Una degradazione si ritrova con
    ``recall``; una scrittura rifiutata non è in nessun file, e resta rifiutata a
    ogni run finché il tetto non si alza o il file non si pota. Sta subito dopo la
    riga dei caratteri perché è la sua *spiegazione*: "nothing was freed" con un
    rifiuto aperto non è un run che non aveva niente da fare, è un run che non è
    riuscito a farlo.
    """
    from jenny.agent.dream_review import STATUS_FAILED

    files = "MEMORY.md, USER.md, SOUL.md"
    if outcome.status == STATUS_FAILED:
        note = (
            "A memory review pass ran first but did not complete cleanly; "
            f"{outcome.freed:,} chars freed across {files}."
        )
    elif outcome.freed > 0:
        note = (
            f"A memory review pass ran first and freed {outcome.freed:,} chars "
            f"across {files}."
        )
    else:
        # Zero o negativo. Un review che non trova niente da potare è un esito
        # valido — il suo prompt lo dice al modello — e il negativo è il caso in
        # cui ha ristrutturato spostando testo *fra* i tre file misurati.
        note = f"A memory review pass ran first; nothing was freed across {files}."
    extra = [_format_dream_refusals(outcome), _format_dream_demotions(outcome)]
    return " ".join([note, *(part for part in extra if part)])


def _format_dream_refusals(outcome: "ReviewOutcome") -> str:
    """Le scritture che il budget ha rifiutato e che non sono mai atterrate.

    ``unresolved_refusals`` conta **contenuto**, non tentativi: un file rifiutato
    e poi riscritto con dentro il fatto è stato recuperato e non arriva qui
    (v. ``FileStates.record_write_refused``). Quel che resta è un fatto che non è
    in nessun file, e nessuno degli altri numeri della nota lo dice — con lo
    status ``no-change`` è indistinguibile da "non c'era niente da potare".

    La frase dice il numero e la mossa, non la diagnosi: da qui non si sa *quale*
    dei tre tetti ha rifiutato (l'esito porta un conteggio, non i percorsi), e
    ``/dream budget`` è esattamente il comando che lo mostra. Non promette invece
    quel che non può sapere: se il fatto era in viaggio da un file all'altro,
    lo status è già ``failed`` e la riga sopra lo dice.
    """
    # ``getattr`` per la stessa ragione di ``memory_entries`` qui sopra: i doppi di
    # ``run_dream_review`` nei test costruiscono l'esito a mano e non espongono
    # tutti i campi. Il contratto vero non è affidato a questo default — lo fissa
    # un test che fa girare ``run_dream_review`` davvero e legge la nota che ne
    # esce, quindi un campo che sparisse dal dataclass farebbe rosso lì.
    refused = getattr(outcome, "unresolved_refusals", 0)
    if refused <= 0:
        return ""
    return (
        f"{refused} write(s) were refused by their size budget and never landed — "
        "**Settings → Memory** shows which file is full, as a gauge against its cap: "
        "raise it or prune the file, then run `/dream` again."
    )


# Quanti id di voci degradate si nominano nella risposta di `/dream`. Il tetto non
# serve al costo — otto caratteri per id — ma a impedire che una passata patologica
# trasformi la nota in un muro di hash. Oltre il tetto la via è comunque aperta:
# ``recall`` senza argomenti elenca l'archivio intero dalla voce più recente, e le
# voci di questa passata sono in testa a quell'elenco.
_DEMOTIONS_NAMED_IN_NOTE = 10


def _format_dream_demotions(outcome: "ReviewOutcome") -> str:
    """Cosa il review ha spostato in archivio, e come richiamarlo.

    Il numero da solo non è azionabile, e la reazione a "sei fatti in meno" senza
    un modo di guardarli è la stessa che a una cancellazione — cioè l'effetto che
    l'archivio esiste per non produrre. Gli id sono ciò che il tool ``recall``
    accetta, quindi la frase è già l'istruzione.
    """
    ids = outcome.demoted_ids
    if not ids:
        return ""
    shown = ids[:_DEMOTIONS_NAMED_IN_NOTE]
    more = len(ids) - len(shown)
    tail = f", and {more} more" if more else ""
    return (
        f"It also moved {len(ids)} fact(s) into `memory/archive/` instead of "
        f"deleting them — ask me to recall {', '.join(shown)}{tail} to read them back."
    )


def _format_dream_no_input_message() -> str:
    return "\n".join([
        "Dream has no conversation history to process yet.",
        "",
        "Dream reads new entries from `memory/history.jsonl` after the current Dream cursor.",
        (
            "Short chats only reach that file after token compaction or idle auto-compact, "
            "so a fresh or short WebUI chat may leave Dream with no input."
        ),
        "",
        "Next steps:",
        "- Enable `agents.defaults.idleCompactAfterMinutes` so completed chats become Dream input automatically.",
        "- Compact the current chat into memory once that manual action is available.",
        "- If you expected history to exist, check whether `memory/history.jsonl` has new entries after the Dream cursor.",
    ])


async def cmd_gardener(ctx: CommandContext) -> OutboundMessage:
    """Run one gardener pass on a project, now.

    Il comando esiste per due ragioni, e la seconda è la vera. La prima: un
    utente che vuole vedere le pagine adesso invece di fra sei ore. La seconda:
    **senza di lui il passo non è collaudabile**. I tre orologi dell'innesco
    (delta, trenta minuti di fermo, sei ore di distanza) rendono la strada
    naturale impossibile da percorrere in una sessione di prova, ed è la stessa
    ragione per cui ``/dream`` esiste.

    **Non prende argomenti** (31/08/2026). Prendeva il nome di un progetto — il
    telecomando dalla chat personale — e sette parole riservate che invece di
    lanciare taravano la passata periodica. Il lavoro su un progetto si fa da
    dentro il progetto, come per ``journal_append``, e le manopole stanno in
    Impostazioni: restava solo un comando che significava tre cose.
    """
    from jenny.session.keys import PROJECT_SESSION_PREFIX, is_project_session_key

    loop = ctx.loop
    msg = ctx.msg
    if ctx.args.strip():
        return _reply(msg, _moved_to_settings(
            "/gardener",
            "Wiki and projects",
            "A pass runs on the project you are in: open it and send `/gardener` there.",
        ))
    if not is_project_session_key(ctx.key):
        # Un rifiuto che dice **dove**, non solo che qui non si può: la lezione
        # dei rifiuti del passo 6, e la stessa forma del rifiuto di
        # ``journal_append`` fuori da un progetto.
        return _reply(msg, _gardener_no_target())
    target = ctx.key[len(PROJECT_SESSION_PREFIX):]

    async def _run():
        from jenny.agent.gardener import GardenerStore, run_gardener
        from jenny.config.loader import load_config

        try:
            config = load_config()
            wikis_dir = getattr(getattr(config, "wiki", None), "wikis_dir", "wikis") or "wikis"
            store = GardenerStore.for_project(
                config.workspace_path, target, wikis_dir_name=wikis_dir
            )
            if store is None:
                content = (
                    f"`{target}` is not a project I can garden: I look for "
                    f"`{wikis_dir}/{target}/wiki/`, and there is nothing there."
                )
            else:
                content = _format_gardener_outcome(target, await run_gardener(loop, store))
        except Exception as e:
            content = f"The gardener failed: {e}"
        await loop.bus.publish_outbound(OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content=content,
            metadata={"render_as": "text"},
        ))

    asyncio.create_task(_run())
    return OutboundMessage(
        channel=msg.channel, chat_id=msg.chat_id,
        content=f"Gardening {target}...",
    )


def _reply(msg, content: str) -> OutboundMessage:
    return OutboundMessage(
        channel=msg.channel, chat_id=msg.chat_id, content=content,
        metadata={"render_as": "text"},
    )


def _gardener_no_target() -> str:
    """«Qui non c'è niente da curare», e dove invece c'è.

    **È il rifiuto canonico di scope, non una seconda frase.** Da quando il
    cancello sta nel router (:func:`_scope_refusal`) questa strada non la
    percorre più un messaggio dell'utente; resta come difesa dell'handler — che
    senza una chiave di progetto prenderebbe un bersaglio dal nulla — e chiama
    la stessa funzione, così le due porte non possono raccontare due storie.

    Non offre più di nominare un progetto: il lavoro su un progetto si fa da
    dentro il progetto (31/08/2026), come per ``journal_append``, che fuori da
    uno rifiuta e indica il chip sopra il composer senza avere un argomento con
    cui aggirarsi.
    """
    from jenny.command.scope import refusal, spec_for_line
    from jenny.session.keys import UNIFIED_SESSION_KEY

    spec = spec_for_line("/gardener")
    assert spec is not None  # è nella tabella: se non c'è, è un errore di programmazione
    return refusal(spec, UNIFIED_SESSION_KEY)


def _format_gardener_outcome(name: str, outcome: "GardenerOutcome") -> str:
    """Messaggio utente per una passata.

    Gli esiti "non ho fatto niente" hanno frasi distinte, come per Dream: un
    comando che risponde "fatto" senza aver fatto niente è peggio di uno che dice
    perché — e qui i modi di non fare niente sono tre, e vogliono dire cose molto
    diverse.

    Ai modi di non fare niente si aggiungono due modi di farlo **a metà**
    (``partial_write``, ``commit_failed``), e sono i due che vanno detti con più
    cura: le pagine sono su disco ma il diario è rimasto da rileggere. Senza un
    ramo proprio cadevano nel fondo, cioè si raccontavano come un fallimento —
    che è falso — e la frase di ``no_write`` («finished without writing») sarebbe
    falsa allo stesso modo, al contrario.

    Poi ci sono i due esiti che non parlano del lavoro ma di **chi altro c'era**.
    ``already_running`` è una passata che non è partita perché un'altra era in
    volo sullo stesso progetto; ``aborted_user_active`` è una passata che si è
    fermata perché l'utente è tornato su quel progetto mentre scriveva — e questa
    ha bisogno di una frase sua per la stessa ragione delle due a metà: può avere
    lasciato pagine su disco, quindi «finished without writing» sarebbe falso.

    Infine, **trasversale a quasi tutti**, la passata girata per la mappa
    (``outcome.map_pass``, T3.5): lì le righe di diario sono zero per costruzione, e
    ogni frase che le conta diventa falsa. Il ramo sta **prima** di tutti gli esiti
    che le nominano, e resta fuori solo per i due che parlano d'altro
    (``failed``/``incomplete``). V. ``_gardener_map_pass_line``.
    """
    elapsed = f"{outcome.elapsed:.1f}s"
    if outcome.status == "skipped_no_delta":
        return (
            f"Nothing new in {name}'s journal since the last pass, so there was nothing to "
            "promote — no tokens spent."
        )
    if outcome.map_pass and outcome.status not in ("failed", "incomplete"):
        return _gardener_map_pass_line(name, outcome, elapsed)
    if outcome.status == "written":
        return (
            f"The gardener read {outcome.lines} journal lines in {name} and wrote "
            f"{outcome.writes} times, in {elapsed}. See `wiki/index.md` and today's `log/`."
        )
    if outcome.status == "nothing_to_promote":
        return (
            f"The gardener read {outcome.lines} journal lines in {name} in {elapsed} and "
            "judged that none of them earned a page. The journal is marked as read."
        )
    if outcome.status == "partial_write":
        return (
            f"The gardener wrote {outcome.writes} pages in {name} in {elapsed}, but some writes "
            "were refused, so the journal was left unread — the next pass will see those lines "
            "again."
        )
    if outcome.status == "commit_failed":
        return (
            f"The gardener wrote {outcome.writes} pages in {name} in {elapsed}, but could not "
            f"record how far it had read: {outcome.detail}. The pages are on disk and the "
            "journal was left unread, so the next pass will see those lines again."
        )
    if outcome.status == "aborted_user_active":
        return (
            f"You came back to {name} while the gardener was working there, so it stood down "
            f"after {elapsed} rather than write over you ({outcome.writes} pages had already "
            "landed). The journal was left unread, so the next pass will see those lines again."
        )
    if outcome.status == "already_running":
        return (
            f"A pass on {name} is already running, so this one did not start — the gardener "
            "works on one project one pass at a time, or two passes overwrite each other's "
            "pages. Try again once it has finished."
        )
    if outcome.status == "no_write":
        return (
            f"The gardener finished in {elapsed} without writing (attempts blocked or refused); "
            "the journal was left unread, so the next pass will try again."
        )
    if outcome.status == "incomplete":
        return f"The gardener did not finish after {elapsed}; nothing was changed."
    return f"The gardener failed after {elapsed}: {outcome.detail}"


def _gardener_map_pass_line(name: str, outcome: "GardenerOutcome", elapsed: str) -> str:
    """Messaggio di una passata girata **per la mappa** e non per il diario.

    Serve un ramo suo perché tutte le frasi qui sopra contano righe di diario, e su
    una passata così sono zero: «read 0 journal lines and judged that none of them
    earned a page» è la risposta di un comando che ha smesso di dire la verità. Il
    numero che questa passata esiste per muovere è un altro, e va detto.

    E va detto anche il **freno**: la mappa che resta sopra il tetto non si
    ritenta, perché una ragione che resta vera dopo la passata è un livelock (v.
    ``GardenerState.map_left_at``). Un utente che rilancia il comando e non vede
    partire niente deve poter sapere perché da qui, non dai log di un telefono.
    """
    from jenny.agent.gardener import MAP_TARGET_CHARS

    lede = f"Nothing new in {name}'s journal, so the gardener went in for the map alone"
    if outcome.map_after < outcome.map_before:
        moved = (
            f"`wiki/index.md` went from {outcome.map_before} to {outcome.map_after} characters "
            f"in {elapsed}, against a ceiling of {MAP_TARGET_CHARS}"
        )
        if outcome.map_after <= MAP_TARGET_CHARS:
            return f"{lede}: {moved} — it fits now, so every turn sees all of it again."
        return (
            f"{lede}: {moved} — still over, and it will not be tried again until the map grows "
            "past that."
        )
    return (
        f"{lede}, and after {elapsed} it is still {outcome.map_after} characters against a "
        f"ceiling of {MAP_TARGET_CHARS}: nothing was moved out of it. It will not be tried "
        "again until the map grows past that."
    )


_HISTORY_DEFAULT_COUNT = 10
_HISTORY_MAX_COUNT = 50
_HISTORY_MAX_CONTENT_CHARS = 200


def _format_history_message(msg: dict) -> str | None:
    """Format a single history message for display. Returns None to skip."""
    role = msg.get("role")
    if role not in ("user", "assistant"):
        return None
    content = msg.get("content") or ""
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        content = " ".join(parts)
    content = str(content).strip()
    if not content:
        return None
    if len(content) > _HISTORY_MAX_CONTENT_CHARS:
        content = content[:_HISTORY_MAX_CONTENT_CHARS] + "…"
    label = "👤 You" if role == "user" else "🤖 Bot"
    return f"{label}: {content}"


async def cmd_history(ctx: CommandContext) -> OutboundMessage:
    """Show the last N messages of the current session (default 10, max 50).

    Usage: /history [count]
    """
    count = _HISTORY_DEFAULT_COUNT
    if ctx.args.strip():
        try:
            count = max(1, min(int(ctx.args.strip()), _HISTORY_MAX_COUNT))
        except ValueError:
            return OutboundMessage(
                channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
                content="Usage: /history [count] — e.g. /history 5 (default: 10, max: 50)",
                metadata=dict(ctx.msg.metadata or {}),
            )

    session = ctx.session or ctx.loop.sessions.get_or_create(ctx.key)
    history = session.get_history(max_messages=0)
    visible = [_format_history_message(m) for m in history]
    visible = [m for m in visible if m is not None]
    recent = visible[-count:]

    if not recent:
        return OutboundMessage(
            channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
            content="No conversation history yet.",
            metadata=dict(ctx.msg.metadata or {}),
        )

    header = f"Last {len(recent)} message(s):\n"
    return OutboundMessage(
        channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
        content=header + "\n".join(recent),
        metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
    )


_GOAL_PROMPT_TEMPLATE = """The user declared a sustained objective for this thread.

Inspect or clarify if needed, then call `long_task` with the refined objective (and optional short ui_summary). Work proceeds as normal assistant turns using your usual tools. When the objective is fully done and verified, call `complete_goal` with a brief recap. If the user later cancels or changes direction, still call `complete_goal` with an honest recap (then `long_task` again only after there is no active goal). Do not use `long_task` / `complete_goal` for trivial one-shot answers.

Goal:
{goal}
"""


async def cmd_goal(ctx: CommandContext) -> OutboundMessage | None:
    """Rewrite /goal into a normal agent turn that nudges long_task use."""
    goal = ctx.args.strip()
    if not goal:
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content="Usage: /goal <long-running task description>",
            metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
        )
    current_task = asyncio.current_task()
    active_tasks = ctx.loop._active_tasks.get(ctx.key, [])
    running = sum(1 for t in active_tasks if t is not current_task and not t.done())
    running += ctx.loop.subagents.get_running_count_by_session(ctx.key)
    if running > 0:
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content=(
                "A task is already running for this chat. "
                "Use `/stop` first, then send `/goal <long-running task description>` again."
            ),
            metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
        )

    ctx.msg.metadata = {
        **dict(ctx.msg.metadata or {}),
        "original_command": "/goal",
        "goal_started_at": time.time(),
    }
    ctx.msg.content = _GOAL_PROMPT_TEMPLATE.format(goal=goal)
    return None


async def cmd_skill(ctx: CommandContext) -> OutboundMessage:
    """List all enabled skills (name and description only)."""
    loop = ctx.loop
    skills = loop.context.skills.list_skills(filter_unavailable=False)
    if not skills:
        content = "No skills available."
    else:
        lines = [f"Available skills ({len(skills)}):", ""]
        for entry in skills:
            desc = loop.context.skills._get_skill_description(entry["name"])
            lines.append(f"- **{entry['name']}** — {desc}")
        content = "\n".join(lines)
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=content,
        metadata=dict(ctx.msg.metadata or {}),
    )

async def cmd_help(ctx: CommandContext) -> OutboundMessage:
    """Return available slash commands."""
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=build_help_text(ctx.key),
        metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
    )


def build_help_text(session_key: str | None = None) -> str:
    """Build canonical help text shared across channels.

    Elenca i comandi che **in questa conversazione** hanno un soggetto: dentro un
    progetto niente ``/dream`` (la memoria personale non passa da qui) e fuori
    niente ``/tidy``. ``None`` vuol dire "scope non noto" e mostra tutto.

    Prima filtrava solo la tendina della WebUI, e le due superfici non dicevano la
    stessa cosa: su Telegram — che e' **sempre** la sessione personale
    (``session_key_for_channel``) — ``/help`` pubblicizzava ``/tidy`` e ``/init``,
    che su quel canale non possono funzionare mai.
    """
    from jenny.command.scope import visible_specs

    lines = ["✿ jenny commands:"]
    for spec in visible_specs(session_key):
        command = spec.command
        if spec.arg_hint:
            command = f"{command} {spec.arg_hint}"
        lines.append(f"{command} — {spec.description}")
    return "\n".join(lines)


def _scope_refusal(ctx: CommandContext) -> OutboundMessage | None:
    """Il cancello di scope del router: un rifiuto, o ``None`` per procedere.

    Sta qui e non in ``router.py`` perche' e' l'unico punto in cui il router deve
    comporre un messaggio, e comporre messaggi e' di questo modulo. La decisione
    invece e' tutta in :mod:`jenny.command.scope`.
    """
    from jenny.command.scope import refusal_for_line

    text = refusal_for_line(ctx.raw, ctx.key)
    return None if text is None else _reply(ctx.msg, text)


def register_builtin_commands(router: CommandRouter) -> None:
    """Register the default set of slash commands."""
    # Il cancello prima delle voci: un router costruito e non registrato non ha
    # comandi, quindi non c'e' finestra in cui accetti senza controllare.
    router.availability = _scope_refusal
    router.priority("/stop", cmd_stop)
    router.priority("/status", cmd_status)
    router.exact("/new", cmd_new)
    router.exact("/model", cmd_model)
    router.prefix("/model ", cmd_model)
    router.exact("/history", cmd_history)
    router.prefix("/history ", cmd_history)
    router.exact("/goal", cmd_goal)
    router.prefix("/goal ", cmd_goal)
    router.exact("/dream", cmd_dream)
    router.prefix("/dream ", cmd_dream)
    router.exact("/gardener", cmd_gardener)
    router.prefix("/gardener ", cmd_gardener)
    router.exact("/skill", cmd_skill)
    router.exact("/help", cmd_help)
