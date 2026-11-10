"""Riprendere un aiutante: rilanciarlo, mandargli un messaggio, continuarne la storia.

Rilancia, manda, continua: tutto ciò che riapre un lavoro già cominciato.

Un pezzo di :class:`jenny.agent.subagent.SubagentManager` (mixin): il testo dei
metodi è quello di prima, spostato qui per responsabilità.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from loguru import logger

from jenny.agent.subagent_common import (
    MAX_AUTO_ATTEMPTS,
    MAX_BUDGET_RESUMES,
    SubagentSendError,
    _plan_metadata,
)
from jenny.agent.subagent_history import history_chars, light_resume_messages
from jenny.agent.subagent_records import CANCEL_REASON_SUPERSEDED, SubagentSpec
from jenny.security.subagent_policy import current_turn_taint

if TYPE_CHECKING:
    from jenny.agent.subagent_common import SubagentStatus, _Lineage
    from jenny.security.subagent_policy import SubagentRunPolicy


class SubagentRestartError(RuntimeError):
    """Sollevata da ``SubagentManager.restart`` quando il rilancio non e
    possibile (target sconosciuto, o tetto dei tentativi automatici raggiunto).
    Il messaggio e pensato per essere mostrato al chiamante."""


@dataclass(slots=True)
class SubagentSendResult:
    """Esito di ``SubagentManager.send``.

    ``mode`` e la sola informazione che cambia la mossa successiva
    dell'orchestratore — "iniettato in un subagent vivo" non e "ripartito da
    zero" — per questo viaggia strutturato e non solo dentro ``text``.
    """

    mode: str  # injected | resumed | restarted
    text: str
    # Il tentativo che portera la risposta: quello vivo per un'iniezione, quello
    # nuovo per una ripresa o un rilancio. La rotta «continua» della casa lo
    # rimanda al client, che segue il lavoro su quell'id.
    task_id: str | None = None


def _restamped(spec: SubagentSpec) -> SubagentSpec:
    """La spec di un rimando o di un rilancio con il passo del turno **corrente**.

    Un ``subagent_send`` nel passo 7 a un editor nato nel passo 6 e' lavoro del
    passo 7: col passo di nascita il driver scartava il suo resoconto
    (``note_step_report``: «un altro passo»), e il resoconto apriva un turno suo
    invece di aspettare i fratelli. Senza chiavi nel turno la spec resta com'e'.
    """
    plan_id, plan_step = _plan_metadata()
    if not (plan_id or plan_step):
        return spec
    if (plan_id, plan_step) == (spec.plan_id, spec.plan_step):
        return spec
    return replace(spec, plan_id=plan_id, plan_step=plan_step)


def _merge_taint(*groups: tuple[str, ...]) -> tuple[str, ...]:
    """Unisce fonti di taint senza doppioni, nel tetto di ``SubagentSpec``."""
    merged: list[str] = []
    for group in groups:
        for source in group:
            if source not in merged:
                merged.append(source)
    return tuple(merged[:8])


class ResumeMixin:
    """Rilancia, manda, continua: tutto ciò che riapre un lavoro già cominciato.

    Mixin di ``SubagentManager``.
    """

    if TYPE_CHECKING:
        # Contratto host↔mixin (solo per il type-checker; nessun effetto a
        # runtime): gli attributi di ``SubagentManager`` e i metodi degli
        # altri pezzi che questi metodi usano.
        CONTINUE_MAX_HISTORY_CHARS: int
        LIGHT_RESUME_MIN_HISTORY_CHARS: int
        _history: Any
        _lineages: dict[str, _Lineage]
        _pending_injections: dict[str, asyncio.Queue[str]]
        _records: Any
        _repudiated_task_ids: set[str]
        _run_policies: dict[str, SubagentRunPolicy]
        _running_tasks: dict[str, asyncio.Task[None]]
        _task_statuses: dict[str, SubagentStatus]

        def _enqueue_injection(self, task_id: str, message: str) -> None: ...

        async def _launch(self, spec: SubagentSpec, *, lineage_id: str | None=None, attempt: int=1, resume_messages: list[dict[str, Any]] | None=None, budget_resumes: int=0) -> tuple[str, str]: ...

        def _mark_cancelled(self, task_id: str, reason: str) -> SubagentStatus | None: ...

        async def _spawn_spec(self, spec: SubagentSpec, *, lineage_id: str | None=None, attempt: int=1) -> str: ...

    async def restart(
        self,
        target_id: str,
        *,
        extra_instructions: str | None = None,
        manual: bool = False,
        quick: bool | None = None,
        grace_s: float = 2.0,
    ) -> str:
        """Rilancia il lavoro identificato da ``target_id`` come attempt N+1.

        ``target_id`` puo essere un ``task_id`` (di un tentativo vivo o
        terminato) o un ``lineage_id``. Il tentativo precedente, se ancora vivo,
        viene cancellato e ripudiato prima di far partire il successivo.

        ``manual=True`` e il bottone premuto da un umano e non ha tetto;
        ``manual=False`` (orchestratore/codice) si fermano a
        ``MAX_AUTO_ATTEMPTS`` tentativi per lineage.
        """
        resolved = self._resolve_target(target_id)
        if resolved is None:
            raise SubagentRestartError(f"unknown subagent or lineage: {target_id}")
        lineage_id, spec, attempt = resolved
        next_attempt = attempt + 1
        if not manual and next_attempt > MAX_AUTO_ATTEMPTS:
            raise SubagentRestartError(
                f"automatic restart refused for lineage {lineage_id}: "
                f"{attempt}/{MAX_AUTO_ATTEMPTS} attempts already used "
                "(a manual relaunch is still possible)"
            )

        await self._supersede(lineage_id, grace_s=grace_s)

        new_spec = _restamped(spec.with_extra_instructions(extra_instructions))
        if extra_instructions and (turn := current_turn_taint()):
            # La nota correttiva l'ha scritta un turno con contenuto non fidato in
            # vista: il rilancio la porta, e con lei il taint.
            new_spec = replace(new_spec, taint_seed=_merge_taint(new_spec.taint_seed, turn))
        if quick is not None and quick != new_spec.quick:
            new_spec = replace(new_spec, quick=quick)
        logger.info(
            "Restarting lineage {} as attempt {} (manual={})",
            lineage_id, next_attempt, manual,
        )
        return await self._spawn_spec(new_spec, lineage_id=lineage_id, attempt=next_attempt)

    async def send(
        self,
        target_id: str,
        message: str,
        *,
        quick: bool | None = None,
        grace_s: float = 2.0,
        allow_restart: bool = True,
    ) -> SubagentSendResult:
        """Parla a un subagent, qualunque sia il suo stato.

        Il chiamante NON deve sapere se il subagent e vivo: dice "parla con
        questo subagent" e la scelta la fa il manager, in quest'ordine:

        1. tentativo **vivo e in corso** -> iniezione mid-run, senza fermarlo;
        2. **terminato bene**, o **fermato al budget**, con storia entro la
           finestra di retention ->
           *resume*: un run nuovo seminato con i messaggi salvati piu il
           follow-up — o, per un lavoro finito bene con una storia lunga, con
           la sola ossatura (brief, resoconto, file scritti: vedi
           ``_resume_lineage``);
        3. tutto il resto (stallato, fallito, storia scaduta) -> *rilancio*
           dalla spec, col messaggio come nota correttiva.

        Un resume occupa uno slot di concorrenza come uno spawn, ma **non**
        consuma il budget dei rilanci automatici: ``attempt`` non avanza, perche
        una continuazione diretta non e il ritentativo di un fallimento e
        ``MAX_AUTO_ATTEMPTS`` esiste per quello.

        Un lineage il cui ultimo tentativo si e fermato al budget non cade mai
        nel rilancio: si riprende, oppure ``SubagentSendError`` dice perche no
        (tetto ``MAX_BUDGET_RESUMES``, storia non piu riprendibile).
        ``allow_restart=False`` (il tasto «continua» della casa) rifiuta anche
        il terzo ramo: chi ha chiesto di continuare non ha chiesto di rifare.
        """
        text = (message or "").strip()
        if not text:
            raise SubagentSendError("the message is empty: there is nothing to send")

        # Prima il lineage, poi il tentativo vivo **del lineage**: chiesto col
        # task id di un tentativo finito, ``_live_task_for`` non vedeva la
        # ripresa gia in volo (il suo id e un altro), e il tasto «continua» piu
        # un ``subagent_send`` di Jenny facevano due tentativi vivi sullo stesso
        # lavoro.
        resolved = self._resolve_target(target_id)
        live_id = self._live_task_for(resolved[0] if resolved is not None else target_id)
        if live_id is not None:
            live = self._task_statuses.get(live_id)
            # La casella deve esistere: un tentativo che ha appena perso la
            # propria (terminazione in corso) non e piu iniettabile e deve
            # cadere sui rami resume/rilancio, non fallire.
            if (
                live is not None
                and live.state == "running"
                and live_id in self._pending_injections
            ):
                # Un messaggio scritto con contenuto non fidato in vista porta il
                # taint del turno dentro il lavoro vivo.
                if (turn := current_turn_taint()) and (policy := self._run_policies.get(live_id)):
                    for source in turn:
                        policy.mark_untrusted(source)
                self._enqueue_injection(live_id, text)
                return SubagentSendResult(
                    "injected",
                    f"Delivered to running subagent [{live_id}] ({live.label}); it will "
                    "pick the message up at its next step and keep working. Its result "
                    "will be announced to you as usual — do not poll for it.",
                    task_id=live_id,
                )

        if resolved is None:
            raise SubagentSendError(f"unknown subagent or lineage: {target_id}")
        lineage_id, spec, attempt = resolved
        last_state, budget_resumes = self._lineage_tail(lineage_id)
        if live_id is None and last_state == "budget":
            # Un aiutante fermato al budget si continua o non si tocca: un
            # rilancio da zero, in silenzio, butterebbe proprio il lavoro che la
            # ripresa esiste per salvare.
            if budget_resumes >= MAX_BUDGET_RESUMES:
                raise SubagentSendError(
                    f"this helper stopped at its budget {budget_resumes + 1} times: do not "
                    "continue it again. Split the remaining work into smaller helpers, "
                    "or ask the user how to proceed."
                )
            if not await asyncio.to_thread(self._history_continuable, lineage_id):
                raise SubagentSendError(
                    "this helper stopped at its budget, and its conversation can no longer "
                    "be resumed (too long, or expired). Start new, smaller helpers for what "
                    "remains, with a brief of what is already done."
                )

        # La storia si consulta solo se non c'e un tentativo vivo: un lineage
        # stallato puo avere il transcript di un tentativo precedente, e
        # riprenderlo mentre un altro tentativo e in volo sarebbe incoerente.
        history = (
            await asyncio.to_thread(self._history.load, lineage_id) if live_id is None else None
        )
        if history:
            return await self._resume_lineage(
                lineage_id, spec, attempt, history, text, quick=quick,
                budget_resumes=budget_resumes + (1 if last_state == "budget" else 0),
                finished=last_state == "done",
            )

        if not allow_restart:
            raise SubagentSendError(
                "no resumable conversation for this subagent, and continuing it would "
                "mean relaunching it from scratch"
            )
        if last_state == "done" and self._history.enabled:
            # Un lavoro finito bene la cui storia e' andata (scaduta, potata,
            # corrotta): rilanciarlo rifa' tutto il brief da zero sopra pagine
            # che esistono gia' (Titan, 26/10: due editor «relaunched from
            # scratch», ~5 minuti l'uno). Il rilancio resta per chi e' fallito o
            # si e' fermato, e per le costruzioni senza storia (``sessions=None``),
            # dove non c'era niente da perdere.
            raise SubagentSendError(
                "this helper finished its job and its conversation is no longer saved: "
                "relaunching it would redo the whole brief from scratch. Start a new helper "
                "with a brief of what is already written and what to change."
            )
        detail = await self.restart(
            target_id,
            extra_instructions=text,
            manual=False,
            quick=quick,
            grace_s=grace_s,
        )
        relaunched = self._lineages.get(lineage_id)
        return SubagentSendResult(
            "restarted",
            "No resumable conversation for this subagent (it failed, stalled, or its "
            f"history aged out), so the job was relaunched from scratch with your "
            f"message as a corrective note. {detail}",
            task_id=relaunched.task_id if relaunched is not None else None,
        )

    async def _resume_lineage(
        self,
        lineage_id: str,
        spec: SubagentSpec,
        attempt: int,
        history: list[dict[str, Any]],
        message: str,
        *,
        quick: bool | None,
        budget_resumes: int = 0,
        finished: bool = False,
    ) -> SubagentSendResult:
        """Rilancia un lineage terminato seminandolo con la storia salvata.

        ``finished`` dice che l'ultimo tentativo e' finito bene: solo allora, e
        solo oltre ``LIGHT_RESUME_MIN_HISTORY_CHARS``, la ripresa e' *leggera*
        (:func:`~jenny.agent.subagent_history.light_resume_messages`). Un aiutante
        fermato al budget riprende sempre con la storia intera: e' a meta' di un
        lavoro, e cio' che aveva letto gli serve per finirlo.
        """
        resume_spec = spec if quick is None or quick == spec.quick else replace(spec, quick=quick)
        resume_spec = _restamped(resume_spec)
        # La storia rimette in contesto ciò che il lavoro aveva letto: se era non
        # fidato, lo è anche la ripresa. Vale anche per quella leggera: il
        # resoconto e la nota vengono dalle stesse letture non fidate.
        taint = _merge_taint(self._history.taint_of(lineage_id), current_turn_taint())
        if taint:
            resume_spec = replace(resume_spec, taint_seed=taint)
        light: list[dict[str, Any]] | None = None
        # L'app-builder no: scrive con ``app draft/edit/set``, che la nota dei
        # file non vede, e il suo seguito vive del codice letto e dei risultati
        # di ``app run`` (l'instradamento di ``spawn`` gli promette proprio
        # questo: «conosce il codice e continua da lì»).
        light_ok = finished and spec.agent_type != "app-builder"
        size = history_chars(history) if light_ok else 0
        if size > self.LIGHT_RESUME_MIN_HISTORY_CHARS:
            light = light_resume_messages(history, message)
        if light is not None:
            messages = light
            logger.info(
                "Light resume of lineage {}: {} message(s), {} chars -> {} message(s), {} chars",
                lineage_id, len(history), size, len(light), history_chars(light),
            )
        else:
            messages = list(history) + [{"role": "user", "content": message}]
        # ``attempt`` invariato: vedi il contratto in ``send``. Il record del run
        # ripreso resta sullo stesso attempt, cosi ``can_restart`` nello snapshot
        # continua a significare "quanti rilanci automatici sono stati spesi".
        task_id, _ = await self._launch(
            resume_spec,
            lineage_id=lineage_id,
            attempt=attempt,
            resume_messages=messages,
            budget_resumes=budget_resumes,
        )
        logger.info(
            "Resumed lineage {} as [{}] from {} stored message(s)",
            lineage_id, task_id, len(history),
        )
        if light is not None:
            return SubagentSendResult(
                "resumed",
                f"Resumed subagent [{spec.label}] with a light context (new id: {task_id}, "
                f"lineage {lineage_id}): its saved conversation was long ({len(history)} "
                "messages), so it continues from its brief, its report and the list of "
                "files it wrote, not from everything it read. It will re-read the pages "
                "it changes. Its result will be announced to you as usual.",
                task_id=task_id,
            )
        return SubagentSendResult(
            "resumed",
            f"Resumed subagent [{spec.label}] from its saved conversation "
            f"(new id: {task_id}, lineage {lineage_id}, {len(history)} messages of "
            "context): it already knows what it did, you only sent the change. "
            "Its result will be announced to you as usual.",
            task_id=task_id,
        )

    def continuable(self, target_id: str) -> bool:
        """Vero se ``send`` a *target_id* continuerebbe il suo lavoro.

        Cioe' se c'e' un tentativo vivo che accetta un messaggio, o una storia
        da riprendere non troppo lunga. Il terzo ramo di ``send`` — il rilancio
        dalla spec, col messaggio come nota — qui non conta: rifarebbe il lavoro
        vecchio, non quello nuovo.
        """
        resolved = self._resolve_target(target_id)
        if resolved is None:
            return False
        # Il tentativo vivo si cerca sul lineage, come in ``send``.
        live_id = self._live_task_for(resolved[0])
        if live_id is not None:
            live = self._task_statuses.get(live_id)
            return (live is not None and live.state == "running"
                    and live_id in self._pending_injections)
        last_state, budget_resumes = self._lineage_tail(resolved[0])
        if last_state == "budget" and budget_resumes >= MAX_BUDGET_RESUMES:
            return False
        return self._history_continuable(resolved[0])

    def _lineage_tail(self, lineage_id: str) -> tuple[str | None, int]:
        """Stato terminale dell'ultimo tentativo e riprese dopo un budget del lineage.

        Dalla RAM se il lineage e tracciato e il suo ultimo tentativo e finito;
        altrimenti dal record piu recente su disco (dopo un riavvio).
        """
        tracked = self._lineages.get(lineage_id)
        if tracked is not None and tracked.last_state is not None:
            return tracked.last_state, tracked.budget_resumes
        if tracked is not None and tracked.task_id in self._task_statuses:
            return None, tracked.budget_resumes
        try:
            record = self._records.find(lineage_id)
        except Exception:  # noqa: BLE001 — record illeggibili: nessuno stato noto
            record = None
        if record is None or record.lineage_id != lineage_id:
            return None, tracked.budget_resumes if tracked is not None else 0
        return record.state, record.budget_resumes

    def _history_continuable(self, lineage_id: str) -> bool:
        """La storia salvata di *lineage_id* c'e ed e abbastanza corta da riprendere.

        La meta «storia» di :meth:`continuable`, senza la domanda sul tentativo
        vivo: l'annuncio di un budget e il record si chiedono questo mentre il
        tentativo sta ancora uscendo, e per loro vivo non vuol dire niente.
        """
        try:
            history = self._history.load(lineage_id)
        except Exception as e:  # noqa: BLE001 — una storia illeggibile non e riprendibile
            logger.warning("Subagent history unreadable for lineage {}: {}", lineage_id, e)
            return False
        if not history:
            return False
        return history_chars(history) <= self.CONTINUE_MAX_HISTORY_CHARS

    def _live_task_for(self, target_id: str) -> str | None:
        """Task id del tentativo VIVO che corrisponde a un task id o lineage id."""
        if not target_id:
            return None
        candidates: list[str] = []
        if target_id in self._task_statuses:
            candidates.append(target_id)
        candidates.extend(
            tid for tid, st in self._task_statuses.items()
            if tid != target_id and st.lineage_id == target_id
        )
        for tid in candidates:
            task = self._running_tasks.get(tid)
            if task is not None and not task.done():
                return tid
        return None

    def _resolve_target(self, target_id: str) -> tuple[str, SubagentSpec, int] | None:
        """Risolve un task id o lineage id in (lineage_id, spec, attempt corrente).

        L'attempt restituito e sempre il piu alto noto per il lineage: rilanciare
        partendo dall'id di un tentativo vecchio non deve far regredire la
        numerazione (ne rimettere in gioco il tetto automatico).
        """
        if not target_id:
            return None
        lineage_id: str | None = None
        spec: SubagentSpec | None = None
        attempt = 0

        if (live := self._task_statuses.get(target_id)) is not None:
            lineage_id = live.lineage_id
        elif target_id in self._lineages:
            lineage_id = target_id
        else:
            for lin in self._lineages.values():
                if lin.task_id == target_id:
                    lineage_id = lin.lineage_id
                    break
        if lineage_id is None:
            # Fallback durevole: dopo un riavvio del processo la memoria e vuota
            # ma i record Tier-1 su disco portano ancora spec, lineage e attempt.
            record = self._records.find(target_id)
            if record is None:
                return None
            lineage_id, spec, attempt = record.lineage_id, record.spec, record.attempt

        tracked = self._lineages.get(lineage_id)
        if tracked is not None and tracked.attempt >= attempt:
            spec, attempt = tracked.spec, tracked.attempt
        if spec is None:
            return None
        return lineage_id, spec, attempt

    async def _supersede(self, lineage_id: str, *, grace_s: float) -> None:
        """Cancella e ripudia il tentativo vivo di un lineage.

        Riusa ``_repudiated_task_ids``, lo stesso meccanismo di ``/stop``: un
        tentativo superato che riesce a finire piu tardi non deve iniettare il
        proprio risultato stale nella sessione.
        """
        lin = self._lineages.get(lineage_id)
        if lin is None:
            return
        task = self._running_tasks.get(lin.task_id)
        if task is None or task.done():
            return
        self._mark_cancelled(lin.task_id, CANCEL_REASON_SUPERSEDED)
        self._repudiated_task_ids.add(lin.task_id)
        task.cancel()
        await asyncio.wait([task], timeout=grace_s)
