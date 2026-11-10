"""La pianificazione in corso (``drafting``): nasce col tasto Piano, dura più turni, finisce.

La pianificazione di un posto: aprirla, tenerla viva, contarne le letture, chiuderla.

Un pezzo di :class:`jenny.plan.driver.PlanDriver` (mixin): il testo dei metodi è
quello di prima, spostato qui per responsabilità.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from loguru import logger

from jenny.plan.meta import PLAN_FOLLOWUP_META, PLAN_MODE_META, PLAN_REVISE_META
from jenny.plan.model import drafting_expired, drafting_view, now_iso
from jenny.plan.place import place_from_id
from jenny.plan.store import slot_id

if TYPE_CHECKING:
    from jenny.plan.driver_common import PlanHost, _Live
    from jenny.plan.store import PlanStore


# Il canale che ha il tasto Piano: solo i suoi turni sono di pianificazione
# (``_settle_drafting``).
_PLAN_BUTTON_CHANNEL = "websocket"


def _session_of_slot(slot: str) -> str:
    place = place_from_id(slot)
    return place.session_key if place is not None else ""


class DraftingMixin:
    """La pianificazione di un posto: aprirla, tenerla viva, contarne le letture, chiuderla.

    Mixin di ``PlanDriver``.
    """

    if TYPE_CHECKING:
        # Contratto host↔mixin (solo per il type-checker; nessun effetto a
        # runtime): gli attributi di ``PlanDriver.__init__`` e i metodi degli
        # altri pezzi che questi metodi usano.
        _cancelled_drafting: dict[str, float]
        _drafting: dict[str, dict[str, Any]]
        _drafting_lock: asyncio.Lock
        _index: dict[str, _Live]
        host: PlanHost
        store: PlanStore

        async def _publish_changed(self, slot: str, plan_id: str) -> None: ...
        def _slot(self, session_key: str | None) -> str | None: ...
        def held_status(self, session_key: str) -> str | None: ...

    def drafting_for(self, session_key: str | None) -> dict[str, Any] | None:
        """La pianificazione in corso della sessione, o ``None`` (scaduta = finita)."""
        slot = self._slot(session_key)
        if slot is None:
            return None
        entry = self._drafting.get(slot)
        if entry is None or drafting_expired(entry):
            return None
        return entry

    def drafting_info(self, slot: str) -> dict[str, Any] | None:
        """``{since, revise, reads}`` per ``plan.get``, o ``None``. *slot*: un
        posto, o il nome di un quaderno."""
        entry = self._drafting.get(slot_id(slot))
        if entry is None or drafting_expired(entry):
            return None
        return drafting_view(entry)

    async def start_drafting(
        self, session_key: str, revise: str | None = None, *, create: bool = True
    ) -> None:
        """Apre (o tiene viva) la pianificazione del quaderno della sessione.

        Una pianificazione già aperta tiene il suo inizio e le sue letture: il
        ``plan: true`` che il client rimanda a ogni messaggio non la azzera. Una
        revisione chiesta adesso vince su quella di prima. Con ``create=False``
        tocca solo una pianificazione che c'è: un turno che la continua non la
        fa rinascere se intanto l'utente l'ha annullata.
        """
        slot = self._slot(session_key)
        if slot is None:
            return
        if create:
            # Una pianificazione nuova chiude il conto dell'annullamento di prima.
            self._cancelled_drafting.pop(slot, None)
        stamp = now_iso()
        created: list[bool] = []

        def _apply(entry: dict[str, Any] | None) -> dict[str, Any] | None:
            if entry is None or drafting_expired(entry):
                if not create:
                    return None
                created.append(True)
                entry = {"since": stamp, "revise": None, "reads": 0, "helpers": []}
            if revise:
                entry["revise"] = revise
            entry["touched"] = stamp
            return entry

        await self._update_drafting(slot, _apply)
        if created:
            logger.info("Plan drafting started in slot {}", slot)
            await self._drafting_changed(slot)

    async def _update_drafting(
        self, slot: str, fn: Callable[[dict[str, Any] | None], dict[str, Any] | None]
    ) -> dict[str, Any] | None:
        """Scrive la voce di *slot* e ne allinea la copia in memoria.

        Sotto un lock solo per tutte le pianificazioni: la scrittura sul file e
        la copia in memoria vanno insieme, o un annullamento che arriva fra le
        due lascerebbe in memoria una pianificazione che sul file non c'è più.
        """
        async with self._drafting_lock:
            entry = await asyncio.to_thread(self.store.drafting_update, slot, fn)
            if entry is None:
                self._drafting.pop(slot, None)
            else:
                self._drafting[slot] = entry
            return entry

    async def note_read(self, session_key: str | None) -> None:
        """Una lettura del quaderno durante la pianificazione (il pavimento di A2)."""
        slot = self._slot(session_key)
        if slot is None or self.drafting_for(session_key) is None:
            return
        stamp = now_iso()

        def _apply(entry: dict[str, Any] | None) -> dict[str, Any] | None:
            if entry is None:
                return None
            reads = entry.get("reads")
            entry["reads"] = (reads if isinstance(reads, int) else 0) + 1
            entry["touched"] = stamp
            return entry

        await self._update_drafting(slot, _apply)

    async def note_helper(self, session_key: str | None, task_id: str) -> None:
        """Un aiutante lanciato durante la pianificazione: il suo risultato conterà."""
        slot = self._slot(session_key)
        if slot is None or not task_id or self.drafting_for(session_key) is None:
            return

        def _apply(entry: dict[str, Any] | None) -> dict[str, Any] | None:
            if entry is None:
                return None
            helpers = [h for h in entry.get("helpers") or () if isinstance(h, str)]
            if task_id not in helpers:
                helpers.append(task_id)
            entry["helpers"] = helpers[-20:]
            return entry

        await self._update_drafting(slot, _apply)

    async def note_helper_result(self, session_key: str | None, task_id: Any) -> bool:
        """Il risultato di un aiutante: è una lettura solo se l'ha lanciato la pianificazione.

        Un aiutante partito prima (magari un ``researcher`` di una chat di ieri)
        che rientra adesso non ha guardato il quaderno per questo piano: non
        conta, o basterebbe a superare il pavimento senza aver letto niente.
        """
        entry = self.drafting_for(session_key)
        if entry is None or not isinstance(task_id, str):
            return False
        if task_id not in (entry.get("helpers") or ()):
            return False
        await self.note_read(session_key)
        return True

    async def end_drafting(self, session_key: str | None, reason: str) -> bool:
        """Chiude la pianificazione della sessione. ``True`` se ce n'era una."""
        slot = self._slot(session_key)
        if slot is None:
            return False
        return await self._drop_drafting(slot, reason)

    async def cancel_drafting(self, slot: str) -> bool:
        """``plan.cancel_drafting``: l'utente ha spento il tasto o annullato.

        Ferma anche il lavoro della pianificazione: il turno in volo e gli
        aiutanti che ha lanciato. Il 25/10 sul telefono l'annullamento chiudeva
        solo lo stato: il turno andava avanti, ``plan_propose`` lo rifiutava, e
        Jenny («non è partita la modalità piano, così sono andata diretta»)
        lanciava un editor che riscriveva l'indice senza nessuna approvazione.
        *slot*: un posto, o il nome di un quaderno.
        """
        slot = slot_id(slot)
        entry = self._drafting.get(slot) or {}
        helpers = [h for h in entry.get("helpers") or () if isinstance(h, str)]
        dropped = await self._drop_drafting(slot, "cancelled")
        if dropped:
            self._cancelled_drafting[slot] = time.monotonic()
            await self._stop_drafting_work(slot, helpers)
        return dropped

    async def _stop_drafting_work(self, slot: str, helpers: list[str]) -> None:
        """Il turno di pianificazione in volo e gli aiutanti suoi, non gli altri.

        Gli altri aiutanti della chat (una ricerca chiesta prima del tasto Piano)
        non sono della pianificazione e restano al lavoro: per questo non è
        ``host.cancel``, che è ``/stop all``. Metodi facoltativi dell'ospite: un
        ospite che non li ha (i test vecchi) annulla solo lo stato, come prima.
        """
        session_key = _session_of_slot(slot)
        cancel_turn: Any = getattr(self.host, "cancel_turn", None)
        cancel_helpers: Any = getattr(self.host, "cancel_helpers", None)
        try:
            if cancel_turn is not None and self.host.turn_in_flight(session_key):
                await cancel_turn(session_key)
            running = self.host.running_helpers(session_key) or frozenset()
            mine = [h for h in helpers if h in running]
            if mine and cancel_helpers is not None:
                await cancel_helpers(mine)
        except Exception:  # noqa: BLE001 — lo stato è già annullato: il resto è un aiuto
            logger.opt(exception=True).warning(
                "Plan drafting in {}: work not stopped", slot
            )
        logger.info(
            "Plan drafting in {} cancelled: turn stopped, {} helper(s)",
            slot, len(helpers),
        )

    async def _drop_drafting(
        self, slot: str, reason: str, *, announce: bool = True
    ) -> bool:
        async with self._drafting_lock:
            self._drafting.pop(slot, None)
            removed = await asyncio.to_thread(self.store.drafting_remove, slot)
        if removed is None:
            return False
        # Una voce scaduta è già finita per tutti (``drafting_for`` la ignora):
        # toglierla è pulizia, ma la scheda la vedeva già spenta.
        live = not drafting_expired(removed)
        logger.info("Plan drafting in {} ended: {}", slot, reason)
        if announce:
            await self._drafting_changed(slot)
        return live

    async def _settle_drafting(
        self, session_key: str, metadata: dict[str, Any], *, channel: str | None = None,
    ) -> None:
        """Prima di un turno dell'utente: apre, tiene viva o chiude la pianificazione.

        Un messaggio col tasto Piano la apre, se non c'è. Ogni messaggio che
        arriva mentre c'è — col tasto o senza: il client lo rimanda finché il
        tasto è acceso — la continua: il turno diventa di pianificazione qui,
        nei metadati del messaggio, così il filtro dei tool, il blocco del
        prompt e la sola lettura dello scope lo vedono tutti allo stesso modo,
        e il blocco dice al modello di andare avanti, non di ricominciare. Un
        turno che la continua non la ricrea: se l'utente l'ha annullata nel
        frattempo, resta annullata.

        Solo dal canale che ha il tasto Piano (*channel*, la WebUI): la chat
        personale è una sessione sola per quattro canali, e una domanda fatta
        alla mascotte, da Telegram o da una notifica mentre in casa si prepara
        un piano resta una chat — senza, per dodici ore ogni «che tempo fa?»
        riceveva il blocco di pianificazione e la sola lettura, e un
        ``plan_propose`` metteva la scheda dove chi chiedeva non guardava.
        """
        slot = self._slot(session_key)
        if slot is None:
            return
        entry = self._drafting.get(slot)
        if entry is not None and drafting_expired(entry):
            await self._drop_drafting(slot, "expired")
            entry = None
        if self.held_status(session_key) is not None:
            # Un piano partito tiene il quaderno: non si pianifica sopra di lui
            # (``plan_propose`` lo rifiuterebbe), e una pianificazione rimasta
            # aperta non deve rendere «di pianificazione» la risposta a un blocco.
            if entry is not None:
                await self._drop_drafting(slot, "plan running")
            return
        asked = metadata.get(PLAN_REVISE_META)
        revise = asked if isinstance(asked, str) and asked else None
        if entry is None:
            if metadata.get(PLAN_MODE_META) is True:
                await self.start_drafting(session_key, revise)
            return
        if channel is not None and channel != _PLAN_BUTTON_CHANNEL:
            return
        from jenny.security.workspace_access import WORKSPACE_READONLY_METADATA_KEY

        metadata[PLAN_MODE_META] = True
        metadata[WORKSPACE_READONLY_METADATA_KEY] = True
        metadata[PLAN_FOLLOWUP_META] = True
        kept = entry.get("revise")
        if revise is None and isinstance(kept, str) and kept:
            metadata[PLAN_REVISE_META] = kept
        await self.start_drafting(session_key, revise, create=False)

    async def _drafting_changed(self, slot: str) -> None:
        """``plan_changed`` per l'inizio o la fine di una pianificazione.

        ``plan_id`` è il piano aperto del quaderno, se c'è (una revisione), o
        ``""``: il client rilegge comunque ``plan.get {slot}``.
        """
        live = self._index.get(_session_of_slot(slot))
        await self._publish_changed(slot, live.plan_id if live is not None else "")
