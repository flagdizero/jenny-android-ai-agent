"""La cache di un bridge Kotlin, scritta una volta sola.

Quattro moduli — ``runtime.notifier``, ``runtime.location``, ``runtime.power``,
``webui.android_apps_api`` — tenevano lo stesso blocco identico: una globale per
l'istanza, un lock, un double-check, e un ``reset_*`` che ricrea il lock. Copiato
quattro volte, con l'esito che la copia produce: ``android_apps_api`` è rimasta
**senza reset** per un po', cioè con la possibilità di un bridge stale dopo un
restart del gateway (v. il docstring di ``reset_installed_apps_state``).

Lo stesso per la chiamata (:meth:`BridgeCache.call`: su un thread, con un tetto,
un default e un log), che fino al 02/11/2026 era scritta a mano dieci volte — e
la posizione, l'unica senza tetto, poteva tenere fermo un turno.

``agent.tools.android_web`` **non** passa da qui, e non è una dimenticanza: il suo
bridge non si prende il lock per la sola costruzione ma per l'intera operazione,
perché la WebView nascosta condivide il processo renderer Chromium con quella
visibile e due chiamate sovrapposte le affamano il dispatch degli input. È una
semantica diversa, non un parametro.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger


class BridgeCache:
    """Un bridge Kotlin risolto e costruito una volta per processo.

    Il lock vive qui, in un attributo, e non in una globale di modulo. Non cambia
    la classe di bug: una ``asyncio.Lock`` si lega al loop su cui la si awaita la
    prima volta, il gateway riparte nello stesso processo, e un lock ereditato
    rifiuta ogni accodamento. Cambia solo dove va guardato — e
    ``tests/runtime/test_loop_bound_globals.py`` guarda anche dentro l'``__init__``
    di una classe di cui esiste un singleton di modulo, appunto per questo.
    """

    def __init__(self, java_class: str) -> None:
        self.java_class = java_class
        self.instance: Any = None
        self.lock = asyncio.Lock()

    @property
    def simple_name(self) -> str:
        """``…jenny.NotifierBridge`` -> ``NotifierBridge``, per i messaggi d'errore."""
        return self.java_class.rsplit(".", 1)[-1]

    def reset(self) -> None:
        """Butta istanza e lock a un nuovo start del gateway.

        Il lock si **ricrea**, non si rilascia: quello vecchio è legato a un loop
        morto e non è più utilizzabile da nessuno.
        """
        self.instance = None
        self.lock = asyncio.Lock()

    def resolve_class(self) -> Any:
        """Risolve la classe Kotlin via Chaquopy."""
        from java import jclass  # importabile solo sotto il runtime Chaquopy

        return jclass(self.java_class)

    async def get(self, context: Any, *, resolve: Callable[[], Any]) -> Any:
        """Costruisce o ritorna l'istanza cachata (thread-safe).

        ``resolve`` si passa a ogni chiamata invece di essere ``self.resolve_class``
        perché il punto in cui si entra in Chaquopy è il seam su cui i test
        montano il finto bridge: 35 di loro sostituiscono la
        ``_resolve_bridge_class`` **del modulo**. Legarla qui alla costruzione
        spegnerebbe quella sostituzione senza che nessun test lo dica — passerebbe
        tutto, contro un bridge vero che fuori dal telefono non esiste.
        """
        if self.instance is not None:
            return self.instance
        async with self.lock:
            # Riletto sotto lock: due chiamanti che partono insieme vedono
            # entrambi ``None`` prima di accodarsi, e senza questo il secondo
            # costruirebbe un secondo bridge.
            if self.instance is not None:
                return self.instance
            bridge_cls = resolve()
            try:
                self.instance = bridge_cls(context)
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(
                    f"Failed to construct {self.simple_name}: {exc}"
                ) from exc
            return self.instance

    async def call(
        self,
        get: Callable[[Any], Awaitable[Any]],
        context: Any,
        method: str,
        *args: Any,
        timeout: float,
        default: Any = None,
        level: str = "WARNING",
    ) -> Any:
        """Chiama ``method(*args)`` del bridge su un thread, con un tetto. Mai solleva.

        Torna *default* fuori da Android (*context* ``None``) e su qualunque
        guasto — il bridge che non si costruisce, un'eccezione Kotlin, il tetto
        superato — loggato a *level* con la traccia. Su un thread perché
        l'attraversamento Chaquopy è bloccante e siamo sul loop del gateway; col
        tetto perché può restare bloccato quanto il GIL resta preso.

        *get* è il ``_get_bridge`` del modulo chiamante, e non ``self.get``: è il
        seam su cui i test montano il finto bridge (v. :meth:`get`).
        """
        if context is None:
            return default
        try:
            bridge = await get(context)
            return await asyncio.wait_for(
                asyncio.to_thread(getattr(bridge, method), *args), timeout=timeout
            )
        except Exception:  # noqa: BLE001 — v. docstring
            logger.opt(exception=True).log(level, "{}.{} failed", self.simple_name, method)
            return default
