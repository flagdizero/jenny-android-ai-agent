"""Built-in slash command handlers."""

from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from typing import TYPE_CHECKING

from loguru import logger

from jenny import __version__
from jenny.bus.events import OutboundMessage
from jenny.command.router import CommandContext, CommandRouter
from jenny.runtime.user_text import say
from jenny.utils.helpers import build_status_content

if TYPE_CHECKING:
    from pathlib import Path

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
def _moved_to_settings(command: str, still: str) -> str:
    """«Quell'argomento non c'e' piu', la manopola e' in Impostazioni → Officina → Memoria».

    Le manopole di Dream e del giardiniere stanno nell'officina, nel cassetto
    Memoria (gruppi *Dream* e *Gardener*): la pagina Impostazioni della casa le
    raggiunge solo dalla riga Officina, quindi il percorso la nomina.
    """
    return say("cmd.moved_to_settings", command=command, still=still)


async def cmd_stop(ctx: CommandContext) -> OutboundMessage:
    """Ferma il turno in corso; gli aiutanti in background continuano.

    ``/stop`` ferma quel che Jenny sta facendo adesso nella chat (e il goal
    sostenuto, se c'e'), ma **non** i subagent lanciati prima: sono lavori che
    l'utente vede ciascuno nella propria bolla, e da li' li ferma uno per uno.
    Fino al 10/10/2026 li cancellava tutti, e un «basta» detto alla risposta
    corrente buttava via anche un lavoro lungo che non c'entrava. ``/stop all``
    (:func:`cmd_stop_all`) e' la forma di prima: ferma tutto.
    """
    return await _stop(ctx, spare_subagents=True)


async def cmd_stop_all(ctx: CommandContext) -> OutboundMessage:
    """Ferma tutto: il turno, il goal e ogni aiutante della sessione."""
    return await _stop(ctx, spare_subagents=False)


async def _stop(ctx: CommandContext, *, spare_subagents: bool) -> OutboundMessage:
    loop = ctx.loop
    msg = ctx.msg
    total = await loop._cancel_active_tasks(ctx.key, spare_subagents=spare_subagents)
    if total:
        # Il turno ripudiato salta il proprio restore/turn_end: li emette /stop,
        # in modo deterministico (la UI riceve sempre turn_end) — ma **dopo** la
        # propria risposta (v. `CommandContext.after_reply`).
        loop._restore_cancelled_turn(ctx.key)
        ctx.after_reply.append(lambda: loop._emit_stop_turn_end(msg, ctx.key))
    parts = [say("stop.done" if total else "stop.none")]
    if spare_subagents:
        helpers = 0
        with suppress(Exception):
            counted = loop.subagents.get_running_count_by_session(ctx.key)
            helpers = counted if isinstance(counted, int) else 0
        if helpers == 1:
            parts.append(say("stop.helper_running"))
        elif helpers > 1:
            parts.append(say("stop.helpers_running", count=helpers))
    return OutboundMessage(
        channel=msg.channel, chat_id=msg.chat_id, content=" ".join(parts),
        metadata=dict(msg.metadata or {})
    )


async def cmd_status(ctx: CommandContext) -> OutboundMessage:
    """Build an outbound status message for a session."""
    loop = ctx.loop
    session = ctx.session or loop.sessions.get_or_create(ctx.key)
    # Il numero vero dell'ultima chiamata, quando c'e': la stima e' un ripiego.
    # ``_last_usage`` invece somma tutte le chiamate di un turno, e non e' la
    # grandezza della conversazione.
    from jenny.utils.token_calibration import last_real

    ctx_est = last_real(ctx.key) or 0
    if ctx_est <= 0:
        with suppress(Exception):
            ctx_est, _ = loop.consolidator.estimate_session_prompt_tokens(session)

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
        ctx.after_reply.append(lambda: loop._emit_stop_turn_end(ctx.msg, ctx.key))
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
    # La vista del diario si ricongela: le sue righe «nuove da allora» e «non
    # piu' in sospeso» parlavano alla conversazione appena buttata. Il diario
    # resta (e' memoria, non conversazione): cambia solo da quando si conta.
    from jenny.agent.diary import DIARY_STATE_KEY

    session.metadata.pop(DIARY_STATE_KEY, None)
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
        content=say("cmd.new.done"),
        # `_session_boundary` dice ai client di rendere questa conferma come
        # separatore invece che come bolla. Il transcript NON viene toccato da
        # /new (si azzera il contesto del modello, non il registro visibile):
        # senza un confine esplicito la conversazione prosegue a schermo come
        # se il comando fosse stato ignorato.
        metadata={**dict(ctx.msg.metadata or {}), "_session_boundary": True},
    )


def _format_preset_names(names: list[str]) -> str:
    return ", ".join(f"`{name}`" for name in names) if names else say("cmd.model.none_configured")


def _model_preset_names(loop) -> list[str]:
    return sorted(loop.model_presets)


def _active_model_preset_name(loop) -> str:
    return loop.model_preset or say("cmd.model.no_preset")


def _command_error_message(exc: Exception) -> str:
    return str(exc.args[0]) if isinstance(exc, KeyError) and exc.args else str(exc)


def _model_command_status(loop) -> str:
    return say(
        "cmd.model.status",
        model=loop.model,
        preset=_active_model_preset_name(loop),
        presets=_format_preset_names(_model_preset_names(loop)),
    )


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
            content=say("cmd.model.usage"),
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
            content=say(
                "cmd.model.switch_failed",
                error=_command_error_message(exc),
                presets=_format_preset_names(names),
            ),
            metadata=metadata,
        )

    max_tokens = getattr(getattr(loop.provider, "generation", None), "max_tokens", None)
    lines = [say(
        "cmd.model.switched",
        preset=loop.model_preset, model=loop.model, window=loop.context_window_tokens,
    )]
    if max_tokens is not None:
        lines.append(say("cmd.model.max_output", max_tokens=max_tokens))
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content="\n".join(lines),
        metadata=metadata,
    )


async def cmd_dream(ctx: CommandContext) -> OutboundMessage:
    """Manually trigger a Dream consolidation run, or read/tune the memory budgets."""
    loop = ctx.loop
    msg = ctx.msg

    args = ctx.args.strip()
    if args:
        # `/dream budget` viveva qui. Le manopole della memoria stanno in
        # Impostazioni dal 31/08/2026: v. ``_MOVED_TO_SETTINGS``.
        return _reply(msg, _moved_to_settings("/dream", say("cmd.dream.moved")))

    from jenny.agent.dream_cycle import claim_dream_cycle, release_dream_cycle
    from jenny.bus.events import OUTBOUND_META_WORK_ID, OUTBOUND_META_WORK_START
    from jenny.runtime.work_status import WorkReporter

    # La bolla di lavoro della home, come per ``/gardener``: le fasi le decide
    # ``begin_dream_cycle``, che sola sa se tocca la revisione. Solo il ciclo
    # chiesto a mano ha la bolla: quello del cron non lo aspetta nessuno.
    reporter = WorkReporter(
        loop.bus, kind="dream", chat_id=msg.chat_id, channel=msg.channel,
        phases=(), manual=True,
    )
    work_meta = {OUTBOUND_META_WORK_ID: reporter.id}

    async def _dream_cycle():
        # Prologo ed epilogo del ciclo sono gli stessi del job cron, e stanno in
        # un modulo solo: erano due copie della stessa sequenza, e ogni volta che
        # una cresceva l'altra restava indietro in silenzio — il guard del budget,
        # il gauge, i contatori del review sono arrivati qui tre commit dopo che
        # erano di là. Dal 24/09/2026 anche il turno incrementale è condiviso
        # (``run_dream_turn``): qui resta la frase da dire a chi ha lanciato il
        # comando.
        from jenny.agent.dream_cycle import (
            DreamOutcome,
            begin_dream_cycle,
            finish_dream_cycle,
            run_dream_turn,
        )
        from jenny.agent.memory import MemoryStore
        from jenny.config.loader import load_config

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
                loop, store=store, cfg=cfg, take_snapshot=snapshot_cb, reporter=reporter,
            )
            if prologue.review is not None:
                review_note = _format_dream_review_note(prologue.review)

            # ``advanced=None`` vuol dire "il turno non ha mancato niente": è il
            # valore del ramo senza storia, e anche quello di un turno che è
            # crashato. Non ``False``, che incrementerebbe ``stuck`` e quindi
            # dichiarerebbe un livelock del budget dove c'è stata un'eccezione.
            # Legati **prima** del ``try``: il ``finally`` li legge su ogni
            # uscita, compresa quella del ramo senza storia (fino al 24/09/2026 un
            # nome nato più in basso faceva sollevare il ``finally``).
            advanced: bool | None = None
            refused = 0
            try:
                # Il turno è lo stesso del job cron (``run_dream_turn``): qui resta
                # la frase da dire a chi ha lanciato il comando.
                turn = await run_dream_turn(
                    loop, store, prologue, take_snapshot=snapshot_cb, reporter=reporter,
                )
                advanced, refused = turn.advanced, turn.refused
                elapsed = f"{time.monotonic() - t0:.1f}"
                # Non ha scritto (rifiuti, blocchi) o non si e' chiuso: per la
                # bolla e' un lavoro non riuscito. Tutto il resto — anche
                # «niente di nuovo» — e' un lavoro fatto.
                failed = turn.outcome in (DreamOutcome.BLOCKED, DreamOutcome.INCOMPLETE)
                reporter.finish(
                    "failed" if failed else "done",
                    status=turn.outcome.value,
                    reviewed=prologue.review is not None,
                    reason="model" if failed else None,
                )
                if turn.outcome is DreamOutcome.NO_INPUT:
                    await loop.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel, chat_id=msg.chat_id,
                        content=_prefix_review_note(
                            review_note, _format_dream_no_input_message()
                        ),
                        metadata={"render_as": "text", **work_meta},
                    ))
                    return
                if turn.outcome is DreamOutcome.ADVANCED:
                    content = say("cmd.dream.done", elapsed=elapsed)
                elif turn.outcome is DreamOutcome.HELD_BATCH:
                    content = say("cmd.dream.held", elapsed=elapsed)
                elif turn.outcome is DreamOutcome.BLOCKED:
                    content = say("cmd.dream.blocked", elapsed=elapsed)
                else:
                    content = say("cmd.dream.incomplete", elapsed=elapsed)
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
                    refused=refused,
                )
        except Exception as e:
            content = _prefix_review_note(
                review_note,
                say("cmd.dream.failed", elapsed=f"{time.monotonic() - t0:.1f}", error=e),
            )
            if reporter.state == "running":
                reporter.finish("failed", reason="error")
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
            channel=msg.channel, chat_id=msg.chat_id, content=content, metadata=work_meta,
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
        reporter.forget()
        return OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content=say("cmd.dream.already_running"),
        )
    try:
        # Tracciato dal loop, non ``create_task`` nudo: cosi' lo shutdown lo
        # aspetta (o lo cancella passando dal ``finally``) invece di lasciare
        # Dream a meta' riscrittura di ``MEMORY.md``, e asyncio non puo'
        # perderne il riferimento a meta' ciclo.
        loop._schedule_background(_run_dream())
    except BaseException:
        # Se il task non arriva nemmeno a esistere, il suo ``finally`` non girerà:
        # la presa va restituita qui o resta appesa a un ciclo che non c'è.
        release_dream_cycle()
        reporter.forget()
        raise
    reporter.start()
    return OutboundMessage(
        channel=msg.channel, chat_id=msg.chat_id, content=say("cmd.dream.started"),
        metadata={**work_meta, OUTBOUND_META_WORK_START: True},
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
        note = say("cmd.dream.review_failed", freed=f"{outcome.freed:,}", files=files)
    elif outcome.freed > 0:
        note = say("cmd.dream.review_freed", freed=f"{outcome.freed:,}", files=files)
    else:
        # Zero o negativo. Un review che non trova niente da potare è un esito
        # valido — il suo prompt lo dice al modello — e il negativo è il caso in
        # cui ha ristrutturato spostando testo *fra* i tre file misurati.
        note = say("cmd.dream.review_nothing", files=files)
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
    return say("cmd.dream.refused", count=refused)


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
    tail = say("cmd.dream.demoted_more", more=more) if more else ""
    return say("cmd.dream.demoted", count=len(ids), ids=", ".join(shown), more=tail)


def _format_dream_no_input_message() -> str:
    return say("cmd.dream.no_input")


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
        return _reply(msg, _moved_to_settings("/gardener", say("cmd.gardener.moved")))
    if not is_project_session_key(ctx.key):
        # Un rifiuto che dice **dove**, non solo che qui non si può: la lezione
        # dei rifiuti del passo 6, e la stessa forma del rifiuto di
        # ``journal_append`` fuori da un progetto.
        return _reply(msg, _gardener_no_target())
    target = ctx.key[len(PROJECT_SESSION_PREFIX):]
    # La bolla di lavoro della home: compare adesso, cresce mentre la passata
    # lavora, e alla fine diventa il resoconto (v. ``runtime/work_status.py``).
    # I due testi qui sotto restano per chi la bolla non ce l'ha — l'officina,
    # Telegram — e portano l'id del lavoro, cosi' la home non li disegna due
    # volte.
    from jenny.bus.events import OUTBOUND_META_WORK_ID, OUTBOUND_META_WORK_START
    from jenny.runtime.work_status import WorkReporter

    reporter = WorkReporter(
        loop.bus, kind="gardener", chat_id=msg.chat_id, channel=msg.channel,
        phases=("pages", "board"), project=target, manual=True,
    )
    reporter.start()

    async def _run():
        from jenny.agent.gardener import GardenerStore, report_gardener_outcome, run_gardener
        from jenny.config.loader import load_config

        try:
            config = load_config()
            wikis_dir = getattr(getattr(config, "wiki", None), "wikis_dir", "wikis") or "wikis"
            store = GardenerStore.for_project(
                config.workspace_path, target, wikis_dir_name=wikis_dir
            )
            if store is None:
                content = say(
                    "cmd.gardener.not_a_project",
                    name=target, path=f"{wikis_dir}/{target}/wiki/",
                )
                reporter.finish("failed", reason="not_a_project")
            else:
                # ``yield_to_user=False``: chi ha lanciato la passata puo'
                # scriverle mentre lavora senza fermarla (v. ``run_gardener``).
                outcome = await run_gardener(
                    loop, store, reporter=reporter, yield_to_user=False
                )
                absence = _gardener_board_absence(store.root)
                content = _format_gardener_outcome(target, outcome, absence=absence)
                report_gardener_outcome(reporter, outcome, store.root, absence=absence)
        except Exception as e:
            content = say("cmd.gardener.error", error=e)
            reporter.finish("failed", reason="error")
        await loop.bus.publish_outbound(OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content=content,
            metadata={"render_as": "text", OUTBOUND_META_WORK_ID: reporter.id},
        ))

    # Tracciato dal loop (v. ``cmd_dream``): una passata che sta scrivendo
    # pagine non va cancellata secca dal teardown al riavvio.
    loop._schedule_background(_run())
    return OutboundMessage(
        channel=msg.channel, chat_id=msg.chat_id,
        content=say("cmd.gardener.started", name=target),
        metadata={OUTBOUND_META_WORK_ID: reporter.id, OUTBOUND_META_WORK_START: True},
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

    spec = spec_for_line("/gardener")
    assert spec is not None  # è nella tabella: se non c'è, è un errore di programmazione
    return refusal(spec)


def _format_gardener_outcome(name: str, outcome: "GardenerOutcome", *, absence: str = "") -> str:
    """Messaggio utente per una passata: la wiki, poi la bacheca se e' girata.

    La passata della bacheca gira anche a diario vuoto (una prima bacheca da
    proporre, un cassetto stantio), quindi la frase «non c'era niente, nessun
    token speso» sarebbe falsa proprio quando la bacheca ha lavorato: in quel
    caso la wiki tace e parla solo la bacheca.

    *absence* e' il perche' il quaderno non ha bacheca, dopo la passata
    (``board_absence``). Se la bacheca non e' girata **e** il quaderno non ne ha
    una, si dice perche': il 05/10 un ``/gardener`` su un archivio di ricerca
    ha risposto solo «niente di nuovo nel diario», e la domanda successiva,
    «perche' la bacheca e' vuota?», e' finita in una causa inventata.
    """
    board = _gardener_board_line(name, outcome.board) or _gardener_no_board_line(name, absence)
    if not board:
        return _format_wiki_outcome(name, outcome)
    if outcome.status == "skipped_no_delta" and outcome.board:
        return say("cmd.gardener.no_delta_board", name=name, board=board)
    return f"{_format_wiki_outcome(name, outcome)} {board}"


def _gardener_board_absence(root: Path) -> str:
    """``board_absence`` per la risposta di ``/gardener``, o ``""`` se non si sa dire."""
    try:
        from jenny.board.context_block import board_absence

        return board_absence(root)
    except Exception as exc:  # noqa: BLE001 — la risposta esce comunque, senza il perche'
        logger.warning("gardener: cannot tell why {} has no board: {}", root.name, exc)
        return ""


# Gli esiti che hanno una frase propria in ``user_text`` (``cmd.gardener.*``).
_NO_BOARD_REASONS = frozenset({"research", "no_pages", "proposed", "declined", "later", "next_pass"})
_BOARD_PASS_STATUSES = frozenset({"done", "incomplete", "failed"})


def _gardener_no_board_line(name: str, absence: str) -> str:
    """Perche' *name* resta senza bacheca, o ``""`` se ce l'ha (o non si sa)."""
    if absence in _NO_BOARD_REASONS:
        return say(f"cmd.gardener.no_board.{absence}", name=name)
    return ""


def _gardener_board_line(name: str, status: str) -> str:
    """La frase della passata della bacheca, o ``""`` se non e' partita."""
    if status in _BOARD_PASS_STATUSES:
        return say(f"cmd.gardener.board.{status}", name=name)
    return ""


_WIKI_OUTCOME_STATUSES = frozenset({
    "written", "nothing_to_promote", "partial_write", "commit_failed",
    "aborted_user_active", "already_running", "no_write", "incomplete",
})


def _format_wiki_outcome(name: str, outcome: "GardenerOutcome") -> str:
    """Messaggio utente per la passata della wiki.

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
        return say("cmd.gardener.skipped_no_delta", name=name)
    if outcome.map_pass and outcome.status not in ("failed", "incomplete"):
        return _gardener_map_pass_line(name, outcome, elapsed)
    if outcome.status in _WIKI_OUTCOME_STATUSES:
        return say(
            f"cmd.gardener.{outcome.status}",
            name=name, elapsed=elapsed, lines=outcome.lines, writes=outcome.writes,
            detail=outcome.detail,
        )
    return say("cmd.gardener.failed", elapsed=elapsed, detail=outcome.detail)


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

    lede = say("cmd.gardener.map_lede", name=name)
    if outcome.map_after < outcome.map_before:
        moved = say(
            "cmd.gardener.map_moved",
            before=outcome.map_before, after=outcome.map_after, elapsed=elapsed,
            ceiling=MAP_TARGET_CHARS,
        )
        key = "map_fits" if outcome.map_after <= MAP_TARGET_CHARS else "map_still_over"
        return say(f"cmd.gardener.{key}", lede=lede, moved=moved)
    return say(
        "cmd.gardener.map_unchanged",
        lede=lede, elapsed=elapsed, after=outcome.map_after, ceiling=MAP_TARGET_CHARS,
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
    label = say("cmd.history.you") if role == "user" else say("cmd.history.bot")
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
                content=say("cmd.history.usage"),
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
            content=say("cmd.history.empty"),
            metadata=dict(ctx.msg.metadata or {}),
        )

    header = say("cmd.history.header", count=len(recent)) + "\n"
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
            content=say("cmd.goal.usage"),
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
            content=say("cmd.goal.busy"),
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
        content = say("cmd.skill.none")
    else:
        lines = [say("cmd.skill.header", count=len(skills)), ""]
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

    lines = [say("cmd.help.header")]
    for spec in visible_specs(session_key):
        command = spec.command
        if spec.arg_hint:
            command = f"{command} {spec.arg_hint}"
        lines.append(f"{command} — {say(spec.help_key)}")
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
    router.priority("/stop all", cmd_stop_all)
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
