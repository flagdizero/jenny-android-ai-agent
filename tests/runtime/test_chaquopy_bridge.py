"""``BridgeCache``: la costruzione una volta sola, il reset, e il seam dei test.

Quattro moduli tenevano lo stesso blocco identico — istanza in una globale, lock,
double-check, ``reset_*`` che ricrea il lock. La copia ha già prodotto una
divergenza: ``android_apps_api`` è rimasta senza reset per un po', cioè con un
bridge stale possibile dopo un restart del gateway (lo dice il docstring di
``reset_installed_apps_state``).

Ora il blocco è uno. Questi test tengono ferme le tre cose che quel blocco
faceva e che una lettura distratta di ``BridgeCache`` non garantisce.
"""

from __future__ import annotations

import asyncio

import pytest

from jenny.runtime.chaquopy_bridge import BridgeCache

# I moduli che ci passano. ``agent.tools.android_web`` **non** c'è, e non è una
# dimenticanza: il suo bridge tiene il lock per l'intera operazione e non per la
# sola costruzione (WebView nascosta e visibile condividono il renderer
# Chromium). Semantica diversa, non un parametro.
BRIDGE_MODULES = [
    ("jenny.runtime.notifier", "NotifierBridge"),
    ("jenny.runtime.location", "LocationBridge"),
    ("jenny.runtime.power", "PowerBridge"),
    ("jenny.webui.android_apps_api", "InstalledAppsBridge"),
]


class _FakeBridgeClass:
    """Sta al posto della classe Kotlin: conta quante volte viene costruita."""

    def __init__(self) -> None:
        self.built = 0

    def __call__(self, context: object) -> object:
        self.built += 1
        return object()


async def test_concurrent_callers_build_one_bridge() -> None:
    """Il double-check sotto lock, che è la ragione per cui il lock c'è.

    Senza la rilettura dopo l'``await`` sul lock, due chiamanti che partono
    insieme costruiscono due bridge — su Chaquopy due oggetti Java, non due dict.

    Il lock si tiene **da fuori** prima di lanciare i chiamanti, e non è una
    finezza: ``Lock.acquire()`` su un lock libero non cede il controllo, quindi un
    semplice ``gather`` lascerebbe il primo arrivare fino in fondo senza mai
    sospendersi e gli altri troverebbero la cache già piena. Il test passerebbe
    anche togliendo il double-check, cioè misurerebbe niente. Tenendo il lock si
    accodano tutti e otto per davvero.
    """
    cache = BridgeCache("com.flagdizero.jenny.Whatever")
    cls = _FakeBridgeClass()

    await cache.lock.acquire()
    callers = [asyncio.create_task(cache.get(object(), resolve=lambda: cls)) for _ in range(8)]
    await asyncio.sleep(0)  # un giro basta: la coda dei pronti contiene tutti e otto

    # La precondizione si asserisce, non si spera: se qualcuno fosse già passato
    # il resto del test non misurerebbe più il double-check.
    assert cls.built == 0 and not any(c.done() for c in callers), (
        "i chiamanti non sono accodati sul lock: il test non sta misurando la corsa"
    )

    cache.lock.release()

    results = await asyncio.gather(*callers)

    assert cls.built == 1, f"costruito {cls.built} volte invece di una"
    assert len({id(r) for r in results}) == 1, "chiamanti diversi hanno avuto bridge diversi"
    assert results[0] is cache.instance


async def test_reset_frees_the_cache_and_rebinds_the_lock() -> None:
    """Il lock si **ricrea**, non si rilascia.

    Il gateway riparte nello stesso processo, quindi apre un secondo
    ``asyncio.run``: una ``asyncio.Lock`` legata al loop morto rifiuterebbe ogni
    accodamento. Riusare la stessa istanza dopo il reset è precisamente ciò che
    non deve funzionare per caso.
    """
    cache = BridgeCache("com.flagdizero.jenny.Whatever")
    cls = _FakeBridgeClass()
    first = await cache.get(object(), resolve=lambda: cls)

    lock_before = cache.lock
    cache.reset()

    assert cache.instance is None
    assert cache.lock is not lock_before, "il reset ha riusato il lock del loop precedente"

    second = await cache.get(object(), resolve=lambda: cls)
    assert second is not first
    assert cls.built == 2


async def test_a_construction_failure_names_the_bridge() -> None:
    """Il messaggio nomina la classe: è l'unico indizio in un log di logcat."""
    cache = BridgeCache("com.flagdizero.jenny.NotifierBridge")

    def explodes(context: object) -> object:
        raise ValueError("no Chaquopy here")

    with pytest.raises(RuntimeError, match="NotifierBridge"):
        await cache.get(object(), resolve=lambda: explodes)

    # E non resta mezza cache dietro: il prossimo tentativo riprova davvero.
    assert cache.instance is None


@pytest.mark.parametrize(("module_name", "bridge"), BRIDGE_MODULES)
async def test_the_module_seam_still_drives_the_shared_cache(
    module_name: str, bridge: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sostituire ``module._resolve_bridge_class`` deve ancora bastare.

    È l'unico punto in cui questi moduli entrano in Chaquopy, quindi è il seam su
    cui 35 test montano il finto bridge. ``BridgeCache.get`` riceve ``resolve``
    come parametro proprio per non scavalcarlo: legandolo a ``self.resolve_class``
    la sostituzione smetterebbe di avere effetto **in silenzio**, e la suite
    resterebbe verde girando contro un bridge che fuori dal telefono non esiste.
    """
    from importlib import import_module

    module = import_module(module_name)
    cls = _FakeBridgeClass()
    monkeypatch.setattr(module, "_resolve_bridge_class", lambda: cls)
    module._BRIDGE.reset()

    instance = await module._get_bridge(object())

    assert cls.built == 1, (
        f"{module_name}._get_bridge non è passato per _resolve_bridge_class del modulo: "
        "il seam dei test è scavalcato, e su Chaquopy si costruirebbe il bridge vero."
    )
    assert instance is module._BRIDGE.instance
    assert module._BRIDGE.java_class.endswith(bridge)
    module._BRIDGE.reset()
