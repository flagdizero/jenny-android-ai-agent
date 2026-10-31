"""I comandi ``plan.*``: la scheda del piano a lista e le sue decisioni.

Come ``staged.*``, sono caricati per prefisso in ``dispatch_command`` e nessun
tool del modello li raggiunge: **Approva, Ferma e Torna indietro sono decisioni
dell'utente per costruzione**. Le letture vanno allo store (``plan.json`` è
l'unica fonte); tutto ciò che muove un piano passa dal driver
(``jenny/plan/driver.py``), che senza agente non c'è — e allora la risposta è
``unavailable``.

Il contratto (la WebUI ci scrive contro; ogni risposta riuscita ha ``ok: true``):

- ``plan.get {id} | {notebook}`` → ``{ok, plan}``. ``plan`` è ``plan.json`` più
  ``done`` (passi alle spalle: fatti e saltati), ``total`` (senza i tagliati),
  ``current_step_id``, ``can_rollback``, ``can_undo_rollback``. ``plan.json``
  porta ``coverage: [{id, item, have, missing, strategy, why}]`` (``id``:
  ``c1``…``cN`` per posizione), ``assumptions: [str]``, ``steps[].done_when``
  (vuoti per un piano di prima), ``steps[].covers: [id]`` (le voci che un
  passo ``research`` cerca; vuoto per gli altri) e
  ``blocked: {step, question, reason, at, kind: "question"|"drafts", drafts:
  [{id, target}]}``: con ``kind: "drafts"`` il passo aspetta che l'utente
  installi o scarti quelle bozze (``staged.*``), e si chiude da solo. Con
  ``{notebook}``: il piano aperto del quaderno, o il chiuso più recente, o
  ``plan: null``; e accanto ``drafting: {since, revise, reads} | null``, la
  pianificazione in corso (``reads``: le letture del quaderno dal suo inizio).
  Se un ritorno (o il suo annullamento) è partito e si è
  fermato a metà, ``plan.rollback_failed`` è ``{at, kind: "rollback"|"undo",
  error, applied?, total?}``: il quaderno è in parte tornato, e riprovare è
  sicuro. Un ritorno riuscito lo toglie.
- ``plan.list {}`` → ``{ok, plans: [{id, notebook, status, title, done, total,
  updated_at}]}``: i piani **aperti**, per il bollino della copertina.
- ``plan.approve {id, version, cut: [step id]}``, ``plan.discard {id}``,
  ``plan.pause {id}``, ``plan.resume {id}``, ``plan.stop {id, then:
  "keep"|"rollback"}``, ``plan.answer {id, text}`` → ``{ok: true}``
  (``plan.stop`` con ``then: "rollback"`` aggiunge ``report``, o
  ``rollback_error: {code, message}`` se il piano si è fermato ma il ritorno
  è stato rifiutato: si può chiedere dopo con ``plan.rollback``).
- ``plan.step_files {id, step}`` → ``{ok, added, changed, removed}``: la foto
  prima e quella dopo del passo.
- ``plan.changes {id}`` → ``{ok, added, changed, removed, groups: {path:
  group}}``: la foto A e la B (in corsa, l'ultima foto di un passo).
- ``plan.rollback_preview {id}`` → ``{ok, report, expect_end}``: il conto del
  ritorno senza toccare niente. ``report`` ha le liste ``restored``,
  ``removed``, ``already``, ``left``, ``kept``, ``excluded``, i loro
  ``counts``, e ``groups: {group: {esito: quanti}}``. ``expect_end`` è l'id
  della foto del quaderno **su cui è stato fatto questo conto** (non la foto B
  del piano, che dopo il Ferma non cambia più): il client lo rimanda tale e
  quale a ``plan.rollback``.
- ``plan.rollback {id, keep: [group], expect_end}``, ``plan.undo_rollback {id}``
  → ``{ok, report}`` (``plan.rollback`` anche ``discarded_drafts: [{id,
  target}]``: le bozze del piano ancora in attesa, buttate col ritorno). ``plan.rollback`` rifà la foto del quaderno: se non è
  ``expect_end``, il quaderno è cambiato dopo il conto e la risposta è
  ``conflict`` (si rilegge l'anteprima).
- ``plan.full {id}`` → ``{ok, title, coverage, assumptions, steps: [{id, title,
  detail, kind, status, tag, done_when, covers}]}``: il «piano completo», in sola
  lettura.
- ``plan.cancel_drafting {notebook}`` → ``{ok, cancelled}``: chiude la
  pianificazione in corso (il tasto spento, «Annulla il piano»); ``cancelled``
  è ``false`` se non ce n'era una. Emette ``plan_changed``.

Errori (``CommandError``): ``not_found``, ``conflict`` (una versione più nuova;
un quaderno cambiato dopo il conto del ritorno; Jenny al lavoro durante un
ritorno), ``busy`` (un turno o un aiutante in volo all'Approva o al Riprendi),
``bad_state`` (non da questo stato), ``plan_running`` (il ritorno o
l'annullamento di un piano vecchio mentre uno più nuovo tiene il quaderno),
``rollback_failed`` (il ritorno è partito e si è fermato a metà: v.
``plan.rollback_failed``), ``bad_request``, ``unavailable``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from jenny.plan.driver import PlanCommandError, PlanDriver, plan_view
from jenny.plan.model import (
    CLOSED_STATUSES,
    PlanError,
    drafting_expired,
    drafting_view,
    step_counts,
)
from jenny.plan.store import PlanNotFoundError, PlanStore, is_plan_id
from jenny.webui.commands import CommandContext, CommandError


def _store(ctx: CommandContext) -> PlanStore:
    driver = _driver_or_none(ctx)
    if driver is not None:
        return driver.store
    return PlanStore.for_workspace(ctx.get_workspace_root())


def _driver_or_none(ctx: CommandContext) -> PlanDriver | None:
    getter = getattr(ctx, "get_plan_driver", None)
    return getter() if getter is not None else None


def _driver(ctx: CommandContext) -> PlanDriver:
    driver = _driver_or_none(ctx)
    if driver is None:
        raise CommandError("unavailable", "plans are not available until Jenny is running")
    return driver


def _plan_id(params: Mapping[str, Any]) -> str:
    value = params.get("id")
    if not is_plan_id(value):
        raise CommandError("bad_request", "id required")
    return str(value)


def _error(exc: PlanCommandError) -> CommandError:
    return CommandError(exc.code, exc.message)


async def _call(coro: Any) -> Any:
    try:
        return await coro
    except PlanCommandError as exc:
        raise _error(exc) from exc


def _summary(plan: Any) -> dict[str, Any]:
    counts = step_counts(plan)
    return {
        "id": plan.id,
        "notebook": plan.notebook,
        "status": plan.status,
        "title": plan.title,
        "done": counts["finished"],
        "total": counts["total"],
        "updated_at": plan.updated_at,
    }


# ── letture ────────────────────────────────────────────────────────────────


async def plan_get(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    store = _store(ctx)
    if params.get("id") is not None:
        plan_id = _plan_id(params)
        try:
            plan = await _recheck(ctx, await asyncio.to_thread(store.load, plan_id))
        except PlanNotFoundError as exc:
            raise CommandError("not_found", "no such plan") from exc
        return {"ok": True, "plan": plan_view(plan)}
    notebook = params.get("notebook")
    if not isinstance(notebook, str) or not notebook.strip():
        raise CommandError("bad_request", "id or notebook required")

    def _pick() -> Any:
        plans = store.list_for_notebook(notebook.strip())
        for plan in plans:
            if plan.is_open:
                return plan
        # Il chiuso più recente: quello di cui la scheda mostra l'esito e il
        # ritorno. Un ``superseded`` o uno ``discarded`` non sono un esito.
        closed = [
            p for p in plans
            if p.status in CLOSED_STATUSES and p.status not in ("superseded", "discarded")
        ]
        closed.sort(key=lambda p: p.updated_at, reverse=True)
        return closed[0] if closed else None

    plan = await asyncio.to_thread(_pick)
    if plan is not None:
        plan = await _recheck(ctx, plan)
    return {
        "ok": True,
        "plan": plan_view(plan) if plan is not None else None,
        "drafting": await asyncio.to_thread(_drafting, store, notebook.strip()),
    }


async def _recheck(ctx: CommandContext, plan: Any) -> Any:
    """Un piano fermo sulle bozze del passo: le riguarda prima di mostrarlo.

    La rete di sicurezza contro un avviso delle bozze perso: la scheda rilegge
    ``plan.get`` a ogni ``plan_changed`` e a ogni apertura, e un passo che
    aspetta bozze già decise ripartirebbe solo qui.
    """
    driver = _driver_or_none(ctx)
    if driver is None or plan.status != "blocked" or (plan.blocked or {}).get("kind") != "drafts":
        return plan
    try:
        await driver.recheck_drafts(plan.id)
        return await asyncio.to_thread(driver.store.load, plan.id)
    except (PlanError, OSError):
        return plan


def _drafting(store: PlanStore, notebook: str) -> dict[str, Any] | None:
    """La pianificazione in corso dal file, come il piano: anche senza agente.

    Una scaduta è finita anche se il file non lo sa ancora (la toglie il
    driver al prossimo turno): la scheda la vede spenta subito.
    """
    entry = store.drafting_get(notebook)
    if entry is None or drafting_expired(entry):
        return None
    return drafting_view(entry)


async def plan_list(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    store = _store(ctx)
    plans = await asyncio.to_thread(store.list_all)
    return {"ok": True, "plans": [_summary(p) for p in plans if p.is_open]}


async def plan_full(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    plan_id = _plan_id(params)
    try:
        plan = await asyncio.to_thread(_store(ctx).load, plan_id)
    except PlanNotFoundError as exc:
        raise CommandError("not_found", "no such plan") from exc
    return {
        "ok": True,
        "title": plan.title,
        "coverage": [dict(c) for c in plan.coverage],
        "assumptions": list(plan.assumptions),
        "steps": [
            {
                "id": s.id,
                "title": s.title,
                "detail": s.detail,
                "kind": s.kind,
                "status": s.status,
                "tag": s.tag,
                "done_when": s.done_when,
                "covers": list(s.covers),
            }
            for s in plan.steps
        ],
    }


async def plan_step_files(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    driver = _driver(ctx)
    plan_id = _plan_id(params)
    try:
        diff = await asyncio.to_thread(driver.step_files, plan_id, params.get("step"))
    except PlanCommandError as exc:
        raise _error(exc) from exc
    except PlanError as exc:
        raise CommandError("bad_state", str(exc)) from exc
    return {"ok": True, **diff}


async def plan_changes(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    driver = _driver(ctx)
    plan_id = _plan_id(params)
    try:
        changes = await asyncio.to_thread(driver.changes, plan_id)
    except PlanCommandError as exc:
        raise _error(exc) from exc
    except PlanError as exc:
        raise CommandError("bad_state", str(exc)) from exc
    return {"ok": True, **changes}


async def plan_rollback_preview(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    result = await _call(_driver(ctx).rollback_preview(_plan_id(params)))
    return {"ok": True, **result}


# ── decisioni dell'utente ──────────────────────────────────────────────────


async def plan_approve(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    cut = params.get("cut") or []
    if not isinstance(cut, list):
        raise CommandError("bad_request", "cut must be a list of step ids")
    await _call(_driver(ctx).approve(_plan_id(params), params.get("version"), cut))
    return {"ok": True}


async def plan_discard(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    await _call(_driver(ctx).discard(_plan_id(params)))
    return {"ok": True}


async def plan_pause(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    await _call(_driver(ctx).pause(_plan_id(params)))
    return {"ok": True}


async def plan_resume(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    await _call(_driver(ctx).resume(_plan_id(params)))
    return {"ok": True}


async def plan_stop(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    then = params.get("then", "keep")
    if then not in ("keep", "rollback"):
        raise CommandError("bad_request", "then must be keep or rollback")
    result = await _call(_driver(ctx).stop(_plan_id(params), then))
    return {"ok": True, **(result or {})}


async def plan_answer(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    text = params.get("text")
    if not isinstance(text, str) or not text.strip():
        raise CommandError("bad_request", "text required")
    await _call(_driver(ctx).answer(_plan_id(params), text))
    return {"ok": True}


async def plan_cancel_drafting(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    notebook = params.get("notebook")
    if not isinstance(notebook, str) or not notebook.strip():
        raise CommandError("bad_request", "notebook required")
    cancelled = await _call(_driver(ctx).cancel_drafting(notebook.strip()))
    return {"ok": True, "cancelled": bool(cancelled)}


async def plan_rollback(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    keep = params.get("keep") or []
    if not isinstance(keep, list):
        raise CommandError("bad_request", "keep must be a list of groups")
    expect_end = params.get("expect_end")
    if not isinstance(expect_end, str) or not expect_end:
        raise CommandError("bad_request", "expect_end required")
    result = await _call(_driver(ctx).rollback(_plan_id(params), keep, expect_end))
    return {"ok": True, **result}


async def plan_undo_rollback(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    result = await _call(_driver(ctx).undo_rollback(_plan_id(params)))
    return {"ok": True, **result}


PLAN_COMMANDS = {
    "plan.get": plan_get,
    "plan.list": plan_list,
    "plan.full": plan_full,
    "plan.step_files": plan_step_files,
    "plan.changes": plan_changes,
    "plan.rollback_preview": plan_rollback_preview,
    "plan.approve": plan_approve,
    "plan.discard": plan_discard,
    "plan.pause": plan_pause,
    "plan.resume": plan_resume,
    "plan.stop": plan_stop,
    "plan.answer": plan_answer,
    "plan.cancel_drafting": plan_cancel_drafting,
    "plan.rollback": plan_rollback,
    "plan.undo_rollback": plan_undo_rollback,
}
