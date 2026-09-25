"""``time.sleep`` nel corpo di un test asincrono: sbagliato a prescindere dalla durata.

Un ``async def`` gira sul loop. Un ``time.sleep`` lì dentro non sospende la
coroutine: **ferma il loop**, quindi ferma anche tutto ciò che il test ha appena
avviato e che dovrebbe procedere durante l'attesa. Quando funziona lo stesso è
perché il lavoro sta su un thread e sul loop non c'era nient'altro — cioè
funziona per una proprietà che nessuno ha scritto e che il prossimo ``await``
aggiunto in quel test toglie.

C'erano due casi, ora corretti: ``test_subagent_send`` (2 ms, che spegneva ogni
task di sfondo mentre invecchiava la TTL) e ``test_exec_session`` (1,2 s con un
thread vivo dall'altra parte).

**Un ``time.sleep`` dentro una ``def`` sincrona annidata non è il bersaglio**, ed
è la ragione per cui questo test guarda l'albero invece di fare un grep. Sei
occorrenze stanno lì — bridge finti, probe SSH lente, snapshot serializzati — e
simulano una chiamata bloccante, che è esattamente ciò che devono fare: quel
codice gira su un thread, non sul loop. Un controllo che le colorasse di rosso
verrebbe spento entro una settimana, e si porterebbe dietro anche i due veri.
"""

from __future__ import annotations

import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parent


def _own_nodes(fn: ast.AsyncFunctionDef) -> set[int]:
    """I nodi che appartengono a *questa* coroutine.

    Si scende nell'albero fermandosi a ogni ``def``/``lambda``/``class``
    annidata: quello che c'è dentro gira altrove, tipicamente su un thread.
    """
    own: set[int] = set()

    def walk(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
            ):
                continue
            own.add(id(child))
            walk(child)

    walk(fn)
    return own


def _is_time_sleep(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "sleep"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "time"
    )


def _offenders() -> list[str]:
    found: list[str] = []
    for path in sorted(TESTS.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.AsyncFunctionDef):
                continue
            own = _own_nodes(fn)
            found.extend(
                f"{path.relative_to(TESTS.parent)}:{node.lineno} (async def {fn.name})"
                for node in ast.walk(fn)
                if _is_time_sleep(node) and id(node) in own
            )
    return found


def test_no_async_test_blocks_the_loop() -> None:
    offenders = _offenders()
    assert not offenders, (
        "``time.sleep`` nel corpo di una coroutine di test:\n  "
        + "\n  ".join(offenders)
        + "\n\nUsa ``await asyncio.sleep(...)``: stessa attesa, ma il loop resta "
        "libero di far girare quello che il test ha avviato. Se il ritardo deve "
        "essere bloccante di proposito — simulare una chiamata che blocca —, "
        "mettilo in una ``def`` sincrona: là dentro è giusto, e questo test non "
        "lo guarda."
    )
