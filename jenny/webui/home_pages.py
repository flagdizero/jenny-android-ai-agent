"""Le pagine della casa in ``config.json``: chi le tocca oltre al foglio della casa.

Modulo neutro, senza trasporto: lo importano il comando RPC (``commands.py``, le
cancellazioni e il rinomino di un quaderno) e le rotte HTTP (``apps_routes.py``,
la cancellazione di un'app). Prima queste funzioni stavano in ``home_routes``, e
il modulo dei comandi — che di trasporti non sa niente — doveva importarle da un
modulo di rotte HTTP; l'involucro «stacca senza fallire» era scritto due volte.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from jenny.config.schema import Config, normalize_order


async def detach_pages_of(kind: str, ref: str) -> int:
    """Toglie da ``home.pages`` le pagine che puntano a (*kind*, *ref*).

    La chiamano le due cancellazioni — un'app, un quaderno — **dopo** aver
    cancellato: la cancellazione e' I/O lento e fuori dal lucchetto ci deve
    restare, qui dentro c'e' solo un filtro.

    **Non contraddice** la regola per cui il server non toglie una pagina da
    se' quando il suo contenuto sparisce (v. la testata di
    ``tests/webui/test_home_pages_routes.py``). Li' la cosa se ne va per
    altre strade e nessuno ha deciso niente sulla pagina; qui l'utente ha
    **cancellato la cosa** dalla sua scheda, e la pagina e' della cosa. Tenerla
    vorrebbe dire una pagina verso il nulla, lasciata li' apposta.

    Torna quante ne ha tolte. Se non ce n'erano il file non si tocca.
    """
    from jenny.config import store

    removed = 0

    def _apply(config: Config) -> bool:
        nonlocal removed
        before = list(config.home.pages)
        after = [s for s in before if not (s.kind == kind and s.ref == ref)]
        removed = len(before) - len(after)
        if not removed:
            return False
        config.home.pages = after
        # L'id staccato esce anche dall'ordine: lo farebbe la prossima lettura,
        # ma un file che dice il vero non deve aspettare quella.
        config.home.order = normalize_order(config.home.order, [s.id for s in after])
        return True

    await store.mutate(_apply)
    return removed


async def detach_pages_quietly(kind: str, ref: str, *, log: Any = logger) -> None:
    """:func:`detach_pages_of` dopo una cancellazione gia' riuscita, senza fallire.

    La cancellazione e' gia' avvenuta e non si disfa: un errore qui non deve
    diventare un 500 (o un ``internal``) su un'operazione riuscita. Resta una
    pagina verso il nulla, che il client disegna «non c'e' piu'» e l'utente
    toglie.

    Solo ``warning``: il log iniettato dalle rotte non e' per forza loguru, e un
    ``opt()`` che li' non esiste farebbe proprio l'errore da evitare.
    """
    try:
        await detach_pages_of(kind, ref)
    except Exception as exc:  # noqa: BLE001 — v. docstring
        log.warning("Page of deleted {} {} not removed: {}", kind, ref, exc)


async def rename_pages_of(kind: str, ref: str, new_ref: str) -> int:
    """Le pagine di (*kind*, *ref*) seguono la cosa sotto *new_ref*.

    Il gemello di :func:`detach_pages_of`, per il rinomino di un quaderno: la
    pagina salva ``project:<nome>``, e senza questo un rinomino la lascerebbe
    puntata a un nome che non esiste piu'. Stesso ordine: dopo, fuori dal
    lucchetto per tutto quel che e' lento. Torna quante ne ha spostate.
    """
    from jenny.config import store

    moved = 0

    def _apply(config: Config) -> bool:
        nonlocal moved
        moved = 0
        for s in config.home.pages:
            if s.kind == kind and s.ref == ref:
                s.ref = new_ref
                moved += 1
        return moved > 0

    await store.mutate(_apply)
    return moved
